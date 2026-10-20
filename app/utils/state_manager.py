import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import uuid
import asyncio
import threading
import copy
import glob
import time

from app.config import Config

logger = logging.getLogger(__name__)


class StateManager:
    """Manages download state persistence with atomic I/O, thread safety, and auto-healing."""

    def __init__(self):
        self.state_file = Config.STATE_FILE
        self._thread_lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        # Lazily bound on the active event loop on first save_state_async call.
        # Serializes in-flight async disk flushes so redundant force=False
        # flushes are dropped and temp files are never interleaved.
        self._async_write_lock: Optional[asyncio.Lock] = None
        # The event loop the async write lock is bound to. Re-bound when that
        # loop closes (e.g. tests spinning up fresh loops, uvicorn reloads).
        self._bound_loop: Optional[asyncio.AbstractEventLoop] = None
        # Strong references to fire-and-forget flush tasks so the event loop
        # cannot garbage-collect them mid-flight.
        self._background_tasks: set = set()
        self._last_save_time: float = 0.0
        self.download_status = self._initialize_status()

    def _initialize_status(self) -> Dict[str, Any]:
        """Initialize default download status with a full schema."""
        return {
            "active": False,
            "scanning": False,
            "scan_progress": 0,
            "progress": 0,
            "total": 0,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "concurrent_downloads": {},
            "completed_downloads": {},
            "cancelled_files": {},
            "cancelled": False,
            "session_id": str(uuid.uuid4()),
            "started_at": None,
            "channel": None,
            "queue": []
        }

    def _normalize_channel(self, channel: Any) -> str:
        """Normalize a channel identifier for safe comparison."""
        if channel is None:
            return ""
        return str(channel).lstrip("@").strip().lower()

    def _prune_backups(self, pattern: str, max_keep: int = 5):
        """Keep only the newest max_keep files matching pattern; remove older ones."""
        try:
            files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            for old_file in files[max_keep:]:
                try:
                    os.remove(old_file)
                    logger.debug(f"Pruned old backup file: {old_file}")
                except OSError as e:
                    logger.warning(f"Failed to prune old backup file {old_file}: {e}")
        except Exception as e:
            logger.warning(f"Error while pruning backups ({pattern}): {e}")

    def _create_snapshot(self) -> Dict[str, Any]:
        """
        Acquire the thread lock and return an immutable, validated deep-copy of
        the in-memory state.

        Isolating the snapshot under the lock guarantees that concurrent
        coroutines/threads cannot mutate the dictionaries mid-serialization
        (prevents 'dictionary changed size during iteration'). The returned dict
        is safe to hand to a background worker thread.
        """
        with self._thread_lock:
            return {
                "active": bool(self.download_status.get("active", False)),
                "scanning": bool(self.download_status.get("scanning", False)),
                "scan_progress": int(self.download_status.get("scan_progress", 0)),
                "progress": int(self.download_status.get("progress", 0)),
                "total": int(self.download_status.get("total", 0)),
                "current_file": str(self.download_status.get("current_file", "") or ""),
                "current_file_progress": int(self.download_status.get("current_file_progress", 0)),
                "current_file_size": int(self.download_status.get("current_file_size", 0)),
                "downloaded_bytes": int(self.download_status.get("downloaded_bytes", 0)),
                "concurrent_downloads": copy.deepcopy(self.download_status.get("concurrent_downloads", {})),
                "completed_downloads": copy.deepcopy(self.download_status.get("completed_downloads", {})),
                "cancelled_files": copy.deepcopy(self.download_status.get("cancelled_files", {})),
                "cancelled": bool(self.download_status.get("cancelled", False)),
                "session_id": self.download_status.get("session_id"),
                "started_at": self.download_status.get("started_at"),
                "channel": self.download_status.get("channel"),
                "queue": copy.deepcopy(self.download_status.get("queue", []))
            }

    def _sync_write_to_disk(self, state_to_save: Dict[str, Any], temp_file: str) -> None:
        """
        Write a pre-taken state snapshot to ``temp_file`` and atomically publish
        it as ``self.state_file``.

        This performs the genuinely blocking work (json.dump + flush + os.fsync
        + os.replace) and is designed to run on the default worker thread pool.
        A unique per-call temp file prevents parallel writers from colliding or
        truncating each other; it is cleaned up in a finally block on error.
        """
        os.makedirs(Config.SESSION_DIR, exist_ok=True)
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state_to_save, f, default=str, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic POSIX rename replaces state_file safely
            os.replace(temp_file, self.state_file)
        finally:
            # os.replace already moved the temp file on success; only clean up
            # the leftover temp file if something failed mid-write.
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def save_state(self, force: bool = True):
        """
        Save current download state to disk synchronously.

        Delegates to _create_snapshot + _sync_write_to_disk so that synchronous
        callers, CLI shutdown hooks, and synchronous unit tests retain 100%
        backward compatibility. Uses an isolated thread-safe snapshot, a unique
        temporary file, explicit fsync, and atomic rename (os.replace).
        Throttles high-frequency writes unless force=True.
        """
        now = time.time()
        # High-frequency disk write throttling (skip if not forced and saved recently)
        if not force and (now - self._last_save_time < 1.5):
            return

        try:
            state_to_save = self._create_snapshot()
            temp_file = f"{self.state_file}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
            self._sync_write_to_disk(state_to_save, temp_file)
            self._last_save_time = now
        except Exception as e:
            logger.error(f"Error saving state to {self.state_file}: {e}")

    async def save_state_async(self, force: bool = True) -> None:
        """
        Non-blocking variant of save_state for hot async call sites (e.g. the
        MTProto progress callback).

        The blocking snapshot + fsync work is offloaded to the default worker
        thread pool via asyncio.to_thread, so the event loop is never stalled
        by os.fsync(). Writes are serialized by an asyncio.Lock bound to the
        active loop; redundant force=False flushes issued while one is already
        in flight are dropped (debounced) instead of queuing up redundant
        fsyncs, and the 1.5s throttle window is preserved.
        """
        loop = asyncio.get_running_loop()
        # Lazily bind the write lock to the running loop so the manager can be
        # constructed outside of (or before) any loop. Re-bind whenever the
        # running loop differs from the bound one (fresh loops in tests or
        # after a uvicorn reload): a lock created under a closed loop must
        # never be touched again, including .locked().
        if self._async_write_lock is None or self._bound_loop is not loop:
            self._async_write_lock = asyncio.Lock()
            self._bound_loop = loop

        now = time.time()
        # Fast path: drop redundant flushes before paying the lock cost.
        if not force and (now - self._last_save_time < 1.5 or self._async_write_lock.locked()):
            return

        async with self._async_write_lock:
            # Re-check under the lock: a concurrent call may have written since
            # our fast path check.
            if not force and (time.time() - self._last_save_time < 1.5):
                return

            state_to_save = self._create_snapshot()
            temp_file = f"{self.state_file}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
            self._last_save_time = time.time()

            try:
                await asyncio.to_thread(self._sync_write_to_disk, state_to_save, temp_file)
            except Exception as e:
                logger.error(f"Error saving state (async) to {self.state_file}: {e}")

    def schedule_progress_save(self, force: bool = False) -> None:
        """
        Loop-safe, thread-safe entry point for hot progress callbacks.

        Progress callbacks (e.g. MTProto chunk streaming) are synchronous and may
        be invoked from any thread. This method:
          * on the event-loop thread: schedules the non-blocking async flush;
          * from a foreign thread: hops onto the bound loop via
            call_soon_threadsafe so the write still lands in the worker pool;
          * when no event loop is available (tests, CLI, shutdown): falls back
            to the fully synchronous path, which is thread-safe via the
            internal RLock.

        It never raises and never blocks the caller.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop on this thread: degrade to the synchronous writer.
            self.save_state(force=force)
            return

        bound = self._bound_loop
        if bound is None or bound is loop or bound.is_closed():
            # Unbound (will self-bind on first await), already on the owning
            # loop, or the bound loop is gone: schedule on this running loop.
            task = loop.create_task(self.save_state_async(force=force))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            # A live loop owns the write lock: hop onto it thread-safely so
            # the flush serializes with in-flight writes.
            self._schedule_save_on_loop(force)

    def _schedule_save_on_loop(self, force: bool) -> None:
        """Thread-safe hop onto the bound event loop to run an async flush."""
        loop = self._bound_loop
        if loop is None or loop.is_closed():
            self.save_state(force=force)
            return
        try:
            loop.call_soon_threadsafe(self._spawn_save_task, force)
        except RuntimeError:
            self.save_state(force=force)

    def _spawn_save_task(self, force: bool) -> None:
        """Runs ON the bound loop: spawns and tracks the async flush task."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save_state(force=force)
            return
        task = loop.create_task(self.save_state_async(force=force))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def load_state(self):
        """
        Load download state from file with automatic corruption detection
        and recovery from the latest valid backup.
        """
        with self._thread_lock:
            if not os.path.exists(self.state_file):
                logger.info("No state file found, using defaults")
                return

            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    saved_state = json.load(f)

                if isinstance(saved_state, dict) and saved_state.get("session_id"):
                    self.download_status.update(saved_state)
                    # Guarantee all keys and sub-structures exist
                    defaults = self._initialize_status()
                    for key, default_val in defaults.items():
                        if key not in self.download_status:
                            self.download_status[key] = copy.deepcopy(default_val)

                    completed_count = len(self.download_status.get("completed_downloads", {}))
                    queue_count = len(self.download_status.get("queue", []))
                    logger.info(
                        f"Loaded saved download state for session {self.download_status.get('session_id')}: "
                        f"{completed_count} completed files, {queue_count} queued files"
                    )
                else:
                    logger.warning("State file had invalid structure, re-initializing")

            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.error(f"Corrupted state file detected: {e}. Initiating auto-recovery.")
                self._backup_corrupted_state()
                self._attempt_restore_from_backup()
            except Exception as e:
                logger.error(f"Error loading state from {self.state_file}: {e}")

    def _backup_corrupted_state(self):
        """Backup corrupted state file and prune old corrupted backups."""
        try:
            if os.path.exists(self.state_file):
                backup_file = f"{self.state_file}.corrupted.{int(datetime.now().timestamp())}"
                os.replace(self.state_file, backup_file)
                logger.info(f"Preserved corrupted state as {backup_file}")
                self._prune_backups(f"{self.state_file}.corrupted.*", max_keep=5)
        except Exception as e:
            logger.error(f"Failed to backup corrupted state: {e}")

    def _attempt_restore_from_backup(self) -> bool:
        """Search for the most recent valid backup file and restore state."""
        backup_files = sorted(glob.glob(f"{self.state_file}.backup.*"), key=os.path.getmtime, reverse=True)
        for backup_path in backup_files:
            try:
                with open(backup_path, 'r', encoding='utf-8') as f:
                    candidate = json.load(f)
                if isinstance(candidate, dict) and candidate.get("session_id"):
                    logger.info(f"Successfully recovered state from backup: {backup_path}")
                    self.download_status.update(candidate)
                    defaults = self._initialize_status()
                    for key, default_val in defaults.items():
                        if key not in self.download_status:
                            self.download_status[key] = copy.deepcopy(default_val)
                    self.save_state(force=True)
                    return True
            except Exception as e:
                logger.warning(f"Backup file {backup_path} was also unreadable: {e}")
        logger.info("No valid backup found to restore from. Resetting to defaults.")
        return False

    def clear_state(self):
        """Backup existing state and completely reset to defaults."""
        with self._thread_lock:
            try:
                if os.path.exists(self.state_file):
                    backup_file = f"{self.state_file}.backup.{int(datetime.now().timestamp())}"
                    try:
                        import shutil
                        shutil.copy2(self.state_file, backup_file)
                        logger.info(f"Backed up state to {backup_file} before clearing")
                    except Exception as e:
                        logger.error(f"Failed to backup state before clearing: {e}")

                self._prune_backups(f"{self.state_file}.backup.*", max_keep=5)
                self.download_status = self._initialize_status()
                self.save_state(force=True)
            except Exception as e:
                logger.error(f"Error clearing state: {e}")

    def clear_completed(self):
        """Clear completed downloads without destroying pending queue or active session."""
        with self._thread_lock:
            self.download_status["completed_downloads"] = {}
            self.download_status["cancelled_files"] = {}
            self.download_status["progress"] = 0

            # If nothing is active and queue is empty, reset session counters cleanly
            has_queue = len(self.download_status.get("queue", [])) > 0
            if not self.download_status.get("active") and not has_queue:
                self.download_status["total"] = 0
                self.download_status["started_at"] = None

            self.save_state(force=True)
            logger.info("Completed downloads cleared from state")

    def remove_completed(self, file_id: str) -> bool:
        """Remove an individual file from completed history and recalculate session progress."""
        with self._thread_lock:
            removed = False
            completed = self.download_status.get("completed_downloads", {})
            cancelled = self.download_status.get("cancelled_files", {})

            if file_id in completed:
                del completed[file_id]
                removed = True

            if file_id in cancelled:
                del cancelled[file_id]
                removed = True

            if removed:
                # Recalculate progress for current session
                session_id = self.download_status.get("session_id")
                current_count = 0
                for fid, data in completed.items():
                    if isinstance(data, dict) and data.get("session_id") == session_id:
                        current_count += 1
                    elif session_id and session_id in fid:
                        current_count += 1

                self.download_status["progress"] = current_count
                self.save_state(force=True)
                logger.info(f"Removed record {file_id} from completed history. Progress now: {current_count}")

            return removed

    def cleanup_state(self, force: bool = False) -> List[str]:
        """
        Clean up corrupted or incomplete state on startup or recovery.
        Moves interrupted concurrent downloads back to queue and ensures state consistency.
        """
        with self._thread_lock:
            if os.path.exists(self.state_file):
                backup_file = f"{self.state_file}.backup.{int(datetime.now().timestamp())}"
                try:
                    import shutil
                    shutil.copy2(self.state_file, backup_file)
                except Exception as e:
                    logger.warning(f"Failed to backup state during cleanup: {e}")

            self._prune_backups(f"{self.state_file}.backup.*", max_keep=5)

            cleaned_items: List[str] = []
            is_active = self.download_status.get("active", False)
            is_cancelled = self.download_status.get("cancelled", False)

            if force or not is_active:
                concurrent = self.download_status.get("concurrent_downloads", {})
                if concurrent:
                    if "queue" not in self.download_status:
                        self.download_status["queue"] = []

                    completed = self.download_status.get("completed_downloads", {})
                    existing_queue_ids = {item.get("id") for item in self.download_status["queue"]}

                    for file_id, data in list(concurrent.items()):
                        if file_id not in completed and file_id not in existing_queue_ids:
                            logger.info(f"Re-queueing interrupted download {file_id}")
                            item_data = copy.deepcopy(data)
                            item_data["status"] = "queued"
                            if "id" not in item_data:
                                item_data["id"] = file_id
                            if "concurrent_downloads" in item_data:
                                del item_data["concurrent_downloads"]
                            self.download_status["queue"].append(item_data)
                            cleaned_items.append(file_id)

                    self.download_status["concurrent_downloads"] = {}

            # Reset ephemeral download metrics
            self.download_status["current_file"] = ""
            self.download_status["current_file_progress"] = 0
            self.download_status["current_file_size"] = 0
            self.download_status["downloaded_bytes"] = 0

            # State reconciliation: Never mark active=True if the user previously cancelled!
            has_queue = len(self.download_status.get("queue", [])) > 0
            if is_cancelled:
                self.download_status["active"] = False
            elif force:
                self.download_status["active"] = has_queue
            elif not has_queue:
                self.download_status["active"] = False

            self.save_state(force=True)
            return cleaned_items

    def get_status(self) -> Dict[str, Any]:
        """Get current download status."""
        return self.download_status

    def update_status(self, updates: Dict[str, Any]):
        """Update download status and persist state."""
        with self._thread_lock:
            for k, v in updates.items():
                old_v = self.download_status.get(k)
                if k in ("active", "cancelled") and old_v != v:
                    logger.info(f"[STATE] {k.capitalize()} changed: {old_v} -> {v}")
                self.download_status[k] = v
            self.save_state(force=True)

    def is_file_active_or_queued(self, channel: Any, message_id: Any) -> bool:
        """Check if a file is already in queue (status='queued') or currently downloading."""
        target_channel = self._normalize_channel(channel)
        try:
            target_msg_id = int(message_id)
        except (ValueError, TypeError):
            return False

        with self._thread_lock:
            # Check queue
            for item in self.download_status.get("queue", []):
                item_chan = self._normalize_channel(item.get("channel"))
                try:
                    item_msg_id = int(item.get("message_id", -1))
                except (ValueError, TypeError):
                    continue

                if item_chan == target_channel and item_msg_id == target_msg_id:
                    if item.get("status") == "queued":
                        return True

            # Check concurrent downloads
            for fid, data in self.download_status.get("concurrent_downloads", {}).items():
                data_chan = self._normalize_channel(data.get("channel"))
                try:
                    data_msg_id = int(data.get("message_id", -1))
                except (ValueError, TypeError):
                    continue

                if data_chan == target_channel and data_msg_id == target_msg_id:
                    return True

        return False

    async def mark_file_completed(self, file_id: str, file_data: Dict[str, Any]):
        """Thread-safe and async-safe method to mark a file as completed and update progress."""
        async with self._async_lock:
            with self._thread_lock:
                # Add to completed downloads
                if "completed_downloads" not in self.download_status:
                    self.download_status["completed_downloads"] = {}
                self.download_status["completed_downloads"][file_id] = file_data

                # Remove from concurrent downloads if present
                if file_id in self.download_status.get("concurrent_downloads", {}):
                    del self.download_status["concurrent_downloads"][file_id]

                # Update progress - Count files belonging to the CURRENT session
                session_id = self.download_status.get("session_id")
                current_count = 0
                for fid, data in self.download_status["completed_downloads"].items():
                    data_session = data.get("session_id") if isinstance(data, dict) else None
                    if session_id and (data_session == session_id or session_id in fid):
                        current_count += 1

                # If no session-specific matches were found, fallback to total completed count
                if current_count == 0 and not session_id:
                    current_count = len(self.download_status["completed_downloads"])

                self.download_status["progress"] = current_count

                # Ensure total is at least progress
                total = self.download_status.get("total", 0)
                if total > 0 and current_count > total:
                    self.download_status["total"] = current_count

                self.save_state(force=True)
                logger.info(
                    f"File {file_id} marked as completed. "
                    f"Session Progress: {self.download_status['progress']}/{self.download_status.get('total', 0)}"
                )

    # Queue Management Methods

    def get_queue(self) -> List[Dict[str, Any]]:
        """Get current download queue."""
        with self._thread_lock:
            return list(self.download_status.get("queue", []))

    def add_to_queue(self, items: List[Dict[str, Any]]):
        """Add items to the download queue atomically."""
        with self._thread_lock:
            if "queue" not in self.download_status:
                self.download_status["queue"] = []

            existing_ids = {item.get("id") for item in self.download_status["queue"]}
            added_count = 0

            for item in items:
                item_id = item.get("id")
                if not item_id or item_id in existing_ids:
                    continue

                item_copy = copy.deepcopy(item)
                if "added_at" not in item_copy:
                    item_copy["added_at"] = datetime.now().isoformat()
                if "status" not in item_copy:
                    item_copy["status"] = "queued"
                if "priority" not in item_copy:
                    item_copy["priority"] = 0
                if "retry_count" not in item_copy:
                    item_copy["retry_count"] = 0

                self.download_status["queue"].append(item_copy)
                existing_ids.add(item_id)
                added_count += 1

            if added_count > 0:
                self.save_state(force=True)
                logger.info(f"Added {added_count} items to queue. Total in queue: {len(self.download_status['queue'])}")

    def remove_from_queue(self, queue_id: str):
        """Remove an item from the queue atomically."""
        with self._thread_lock:
            if "queue" in self.download_status:
                original_len = len(self.download_status["queue"])
                self.download_status["queue"] = [
                    item for item in self.download_status["queue"]
                    if item.get("id") != queue_id
                ]
                if len(self.download_status["queue"]) < original_len:
                    self.save_state(force=True)
                    logger.info(f"Removed item {queue_id} from queue")

    def reorder_queue(self, queue_ids: List[str]):
        """Reorder queue based on ordered list of IDs."""
        with self._thread_lock:
            if "queue" not in self.download_status:
                return

            current_map = {item["id"]: item for item in self.download_status["queue"] if "id" in item}
            new_queue = []

            for q_id in queue_ids:
                if q_id in current_map:
                    new_queue.append(current_map.pop(q_id))

            # Append any remaining items not specified in queue_ids
            for remaining_item in current_map.values():
                new_queue.append(remaining_item)

            self.download_status["queue"] = new_queue
            self.save_state(force=True)
            logger.info("Queue reordered")

    def get_next_queued_item(self) -> Optional[Dict[str, Any]]:
        """Get the next item to download based on priority and submission time."""
        with self._thread_lock:
            queue = self.download_status.get("queue", [])
            queued_items = [
                item for item in queue
                if item.get("status") == "queued"
            ]

            if not queued_items:
                return None

            # Sort by priority (descending) then added_at (ascending)
            queued_items.sort(key=lambda x: (-x.get("priority", 0), x.get("added_at", "")))
            next_item = copy.deepcopy(queued_items[0])
            logger.info(f"Picking next item from queue: {next_item.get('name')} (ID: {next_item.get('id')})")
            return next_item

    def update_queue_item_status(self, queue_id: str, status: str):
        """Update status of a queue item atomically."""
        with self._thread_lock:
            updated = False
            for item in self.download_status.get("queue", []):
                if item.get("id") == queue_id:
                    item["status"] = status
                    updated = True
                    break
            if updated:
                self.save_state(force=True)

    def clear_queue(self):
        """Clear all items from the download queue atomically."""
        with self._thread_lock:
            self.download_status["queue"] = []
            self.save_state(force=True)
            logger.info("Cleared all items from queue")
