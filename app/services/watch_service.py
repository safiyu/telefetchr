import os
import re
import json
import uuid
import logging
import asyncio
from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional, Any

from telethon import events
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio

from app.config import Config
from app.services.telegram_service import TelegramService
from app.services.download_service import DownloadService

logger = logging.getLogger(__name__)


class WatchService:
    """Service for managing automated channel watch rules and auto-downloading media."""

    def __init__(self, telegram_service: TelegramService, download_service: DownloadService):
        self.telegram_service = telegram_service
        self.download_service = download_service
        self.rules_file = Config.WATCH_RULES_FILE
        self._lock = Lock()
        self.rules: Dict[str, Dict[str, Any]] = {}
        self._handler_attached = False
        self.load_rules()

    def load_rules(self) -> None:
        """Load watch rules from disk."""
        with self._lock:
            if not os.path.exists(self.rules_file):
                self.rules = {}
                return
            try:
                with open(self.rules_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.rules = {r["id"]: r for r in data if isinstance(r, dict) and "id" in r}
                logger.info(f"Loaded {len(self.rules)} watch rule(s)")
            except Exception as e:
                logger.error(f"Error loading watch rules: {e}")
                self.rules = {}

    def save_rules(self) -> None:
        """Save watch rules to disk atomically."""
        with self._lock:
            os.makedirs(os.path.dirname(self.rules_file), exist_ok=True)
            tmp = f"{self.rules_file}.tmp.{os.getpid()}"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(list(self.rules.values()), f, indent=2)
                os.replace(tmp, self.rules_file)
            except Exception as e:
                logger.error(f"Error saving watch rules: {e}")
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    def get_rules(self) -> List[Dict[str, Any]]:
        """Return list of all watch rules."""
        with self._lock:
            return list(self.rules.values())

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a single rule by ID."""
        with self._lock:
            return self.rules.get(rule_id)

    def add_rule(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create and persist a new watch rule."""
        from app.utils.path_utils import sanitize_subfolder
        raw_sub = data.get("subfolder")
        if raw_sub:
            sanitize_subfolder(raw_sub) # raises ValueError if path traversal detected

        rule_id = f"rule_{uuid.uuid4().hex[:8]}"
        rule = {
            "id": rule_id,
            "channel": str(data.get("channel", "")).strip(),
            "channel_name": data.get("channel_name") or data.get("channel", ""),
            "keywords": data.get("keywords") or "",
            "file_extensions": data.get("file_extensions") or "",
            "min_size_mb": float(data.get("min_size_mb")) if data.get("min_size_mb") is not None else None,
            "max_size_mb": float(data.get("max_size_mb")) if data.get("max_size_mb") is not None else None,
            "subfolder": data.get("subfolder") or "",
            "organize_by": data.get("organize_by") or None,
            "enabled": bool(data.get("enabled", True)),
            "created_at": datetime.now().isoformat(),
            "last_matched_at": None,
            "match_count": 0
        }
        with self._lock:
            self.rules[rule_id] = rule
        self.save_rules()
        logger.info(f"Added watch rule {rule_id} for channel {rule['channel']}")
        self.attach_event_handler_if_needed()
        return rule

    def update_rule(self, rule_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing watch rule."""
        from app.utils.path_utils import sanitize_subfolder
        if "subfolder" in data and data["subfolder"]:
            sanitize_subfolder(data["subfolder"])

        with self._lock:
            if rule_id not in self.rules:
                return None
            rule = self.rules[rule_id]
            for key in ["channel", "channel_name", "keywords", "file_extensions", "subfolder", "organize_by", "enabled"]:
                if key in data and data[key] is not None:
                    rule[key] = data[key]
            for key in ["min_size_mb", "max_size_mb"]:
                if key in data:
                    rule[key] = float(data[key]) if data[key] is not None else None
        self.save_rules()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a watch rule."""
        with self._lock:
            if rule_id in self.rules:
                del self.rules[rule_id]
                removed = True
            else:
                removed = False
        if removed:
            self.save_rules()
            logger.info(f"Deleted watch rule {rule_id}")
        return removed

    def attach_event_handler_if_needed(self) -> None:
        """Attach NewMessage event handler to the TelegramClient if active."""
        if self._handler_attached:
            return
        client = getattr(self.telegram_service, "client", None)
        if not client or not client.is_connected():
            return

        try:
            client.add_event_handler(self._on_new_message, events.NewMessage())
            self._handler_attached = True
            logger.info("WatchService event handler successfully registered on Telegram client")
        except Exception as e:
            logger.warning(f"Failed to register WatchService event handler: {e}")

    def detach_event_handler(self) -> None:
        """Detach NewMessage event handler from the TelegramClient if active."""
        if not self._handler_attached:
            return
        client = getattr(self.telegram_service, "client", None)
        if client:
            try:
                client.remove_event_handler(self._on_new_message, events.NewMessage())
            except Exception as e:
                logger.warning(f"Failed to remove WatchService event handler: {e}")
        self._handler_attached = False

    async def _on_new_message(self, event) -> None:
        """Handle real-time incoming messages and match against active watch rules."""
        message = getattr(event, "message", None)
        if not message or not getattr(message, "media", None):
            return

        chat_id = event.chat_id
        chat = None
        username = None
        try:
            chat = await event.get_chat()
            username = getattr(chat, "username", None)
        except Exception:
            pass

        active_rules = [r for r in self.get_rules() if r.get("enabled")]
        if not active_rules:
            return

        for rule in active_rules:
            rule_chan = str(rule.get("channel", "")).strip().lstrip("@")
            # Match channel by peer ID, numeric ID, or username
            is_match = False
            if str(chat_id) == rule_chan or str(chat_id).replace("-100", "") == rule_chan:
                is_match = True
            elif username and username.lower() == rule_chan.lower():
                is_match = True

            if not is_match:
                continue

            # Test message against filters
            if self._matches_filter(message, rule):
                logger.info(f"[WATCHER] Rule {rule['id']} matched message {message.id} in {chat_id} (@{username})")
                await self._trigger_rule_download(rule, message, chat_id, username)

    def _matches_filter(self, message, rule: Dict[str, Any]) -> bool:
        """Check if message satisfies rule criteria."""
        if not message or not getattr(message, "media", None):
            return False

        file_name = self.download_service._get_file_name(message)
        file_size = 0
        if isinstance(message.media, MessageMediaDocument):
            file_size = getattr(getattr(message.media, "document", None), "size", 0)
        elif isinstance(message.media, MessageMediaPhoto):
            photo = getattr(message.media, "photo", None)
            sizes = getattr(photo, "sizes", []) if photo else []
            sizes_with_bytes = [getattr(s, "size", 0) for s in sizes if hasattr(s, "size")]
            file_size = max(sizes_with_bytes) if sizes_with_bytes else 0

        # Extension check
        ext_filter = (rule.get("file_extensions") or "").strip()
        if ext_filter:
            allowed_exts = [e.strip().lower().lstrip(".") for e in ext_filter.split(",") if e.strip()]
            file_ext = file_name.split(".")[-1].lower() if "." in file_name else ""
            if file_ext not in allowed_exts:
                return False

        # Keywords / Regex check with ReDoS protection and bounded search
        kw_filter = (rule.get("keywords") or "").strip()
        if kw_filter:
            message_text = getattr(message, "message", "") or ""
            # Bound search space to prevent catastrophic backtracking on oversized captions
            search_blob = f"{file_name} {message_text}"[:4096]
            keywords = [k.strip() for k in kw_filter.split(",") if k.strip()]
            matched_any = False
            for kw in keywords:
                # Cap regex pattern length
                pat = kw[:128]
                try:
                    if re.search(pat, search_blob, re.IGNORECASE):
                        matched_any = True
                        break
                except (re.error, TimeoutError, OverflowError):
                    if pat.lower() in search_blob.lower():
                        matched_any = True
                        break
            if not matched_any:
                return False

        # Size check
        if file_size > 0:
            size_mb = file_size / (1024 * 1024)
            min_mb = rule.get("min_size_mb")
            if min_mb is not None and min_mb > 0 and size_mb < min_mb:
                return False
            max_mb = rule.get("max_size_mb")
            if max_mb is not None and max_mb > 0 and size_mb > max_mb:
                return False
        elif not isinstance(message.media, MessageMediaPhoto):
            min_mb = rule.get("min_size_mb")
            if min_mb is not None and min_mb > 0:
                return False

        return True

    async def _trigger_rule_download(self, rule: Dict[str, Any], message, chat_id, username) -> None:
        """Queue matched message for download and update rule stats."""
        channel_id = f"@{username}" if username else str(chat_id)
        subfolder = rule.get("subfolder") or ""
        organize_by = rule.get("organize_by") or None

        await self.download_service.download_single(
            channel_id,
            message.id,
            subfolder=subfolder,
            organize_by=organize_by
        )

        with self._lock:
            rule["last_matched_at"] = datetime.now().isoformat()
            rule["match_count"] = rule.get("match_count", 0) + 1
        self.save_rules()

    async def scan_rule(self, rule_id: str, limit: int = 50) -> Dict[str, Any]:
        """Retroactively scan recent messages in rule channel and queue matching files."""
        rule = self.get_rule(rule_id)
        if not rule:
            return {"status": "error", "message": "Rule not found"}

        channel = rule.get("channel")
        queued_count = 0
        try:
            async for message in await self.telegram_service.iter_messages(channel, limit=limit):
                if self._matches_filter(message, rule):
                    if not self.download_service.state_manager.is_file_active_or_queued(channel, message.id):
                        await self._trigger_rule_download(rule, message, channel, None)
                        queued_count += 1
            return {
                "status": "success",
                "message": f"Scan complete. Queued {queued_count} matching file(s).",
                "queued_count": queued_count
            }
        except Exception as e:
            logger.error(f"Error scanning rule {rule_id}: {e}")
            return {"status": "error", "message": str(e)}
