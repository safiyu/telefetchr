import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import uuid
import asyncio

from app.config import Config

logger = logging.getLogger(__name__)


class StateManager:
    """Manages download state persistence"""

    def __init__(self):
        self.state_file = Config.STATE_FILE
        self.download_status = self._initialize_status()
        self._lock = asyncio.Lock()

    def _initialize_status(self) -> Dict[str, Any]:
        """Initialize default download status"""
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
            "cancelled": False,
            "session_id": str(uuid.uuid4()),
            "started_at": None,
            "channel": None,
            "queue": []
        }

    def save_state(self):
        """Save current download state to file"""
        try:
            os.makedirs(Config.SESSION_DIR, exist_ok=True)

            # Create a copy to save
            state_to_save = {
                "active": self.download_status.get("active", False),
                "scanning": self.download_status.get("scanning", False),
                "scan_progress": self.download_status.get("scan_progress", 0),
                "progress": self.download_status.get("progress", 0),
                "total": self.download_status.get("total", 0),
                "current_file": self.download_status.get("current_file", ""),
                "current_file_progress": self.download_status.get("current_file_progress", 0),
                "current_file_size": self.download_status.get("current_file_size", 0),
                "downloaded_bytes": self.download_status.get("downloaded_bytes", 0),
                "concurrent_downloads": self.download_status.get("concurrent_downloads", {}),
                "completed_downloads": self.download_status.get("completed_downloads", {}),
                "cancelled": self.download_status.get("cancelled", False),
                "session_id": self.download_status.get("session_id"),
                "started_at": self.download_status.get("started_at"),
                "channel": self.download_status.get("channel"),
                "queue": self.download_status.get("queue", [])
            }

            # Write to temp file first, then rename (atomic operation)
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(state_to_save, f, default=str, indent=2)

            # Atomic rename
            os.replace(temp_file, self.state_file)

        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def load_state(self):
        """Load download state from file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    saved_state = json.load(f)

                    # Only restore if basic structure is there
                    if saved_state.get("session_id"):
                        # If there was an active download session, we should keep its status 
                        # but the processor loop itself is dead, so it needs to be restarted by DownloadService.
                        # We don't force 'active' to False yet; DownloadService will decide if it can resume.
                        
                        # Merge with current status
                        self.download_status.update(saved_state)

                        # Re-ensure some defaults if missing from file
                        if "queue" not in self.download_status:
                            self.download_status["queue"] = []
                        if "concurrent_downloads" not in self.download_status:
                            self.download_status["concurrent_downloads"] = {}

                        logger.info(f"Loaded saved download state session {self.download_status.get('session_id')}: {len(self.download_status.get('completed_downloads', {}))} completed files")
                    else:
                        logger.info("Loaded empty or invalid state file")
            else:
                logger.info("No state file found, using defaults")

        except json.JSONDecodeError as e:
            logger.error(f"Corrupted state file: {e}")
            self._backup_corrupted_state()
        except Exception as e:
            logger.error(f"Error loading state: {e}")

    def _backup_corrupted_state(self):
        """Backup corrupted state file"""
        try:
            if os.path.exists(self.state_file):
                backup_file = self.state_file + '.corrupted.' + str(int(datetime.now().timestamp()))
                os.rename(self.state_file, backup_file)
                logger.info(f"Backed up corrupted state to {backup_file}")
        except Exception as e:
            logger.error(f"Failed to backup corrupted state: {e}")

    def clear_state(self):
        """Clear saved state file"""
        try:
            if os.path.exists(self.state_file):
                # Backup before clearing
                backup_file = self.state_file + '.backup.' + str(int(datetime.now().timestamp()))
                os.rename(self.state_file, backup_file)
                logger.info(f"Backed up state to {backup_file} before clearing")

            # Reset global state
            self.download_status.update(self._initialize_status())
            self.save_state()

        except Exception as e:
            logger.error(f"Error clearing state: {e}")

    def cleanup_state(self, force: bool = False):
        """Clean up corrupted or incomplete state. Move concurrent downloads back to queue."""
        # Backup current state
        if os.path.exists(self.state_file):
            backup_file = self.state_file + '.backup.' + str(int(datetime.now().timestamp()))
            try:
                import shutil
                shutil.copy2(self.state_file, backup_file)
                logger.info(f"Backed up state to {backup_file}")
            except Exception as e:
                logger.error(f"Failed to backup state: {e}")

        # Clean up incomplete downloads
        cleaned_items = []
        
        # We clean up if explicitly forced (startup) OR if session is not supposed to be active
        if force or not self.download_status.get("active"):
            concurrent = self.download_status.get("concurrent_downloads", {})
            if concurrent:
                logger.info(f"Cleaning up {len(concurrent)} concurrent downloads...")
                if "queue" not in self.download_status:
                    self.download_status["queue"] = []
                
                for file_id, data in list(concurrent.items()):
                    # Add back to queue if not already there and not already completed
                    is_completed = file_id in self.download_status.get("completed_downloads", {})
                    already_queued = any(item.get("id") == file_id for item in self.download_status["queue"])
                    
                    if not already_queued and not is_completed:
                        logger.info(f"Moving stalled file {file_id} back to queue")
                        data["status"] = "queued"
                        # Ensure ID is present even if it was missing from metadata
                        if "id" not in data:
                            data["id"] = file_id
                        # Ensure we don't have nested status or other junk
                        if "concurrent_downloads" in data: del data["concurrent_downloads"]
                        self.download_status["queue"].append(data)
                        cleaned_items.append(file_id)
                    else:
                        logger.info(f"Skipping cleanup for {file_id} (already queued or completed)")
                
                self.download_status["concurrent_downloads"] = {}
                self.download_status["active"] = True # Set active to True so auto-resume kicks in
                logger.info(f"Cleared concurrent_downloads. Re-queued {len(cleaned_items)} interrupted downloads")

        # Reset session metrics if not active or if forced (new run)
        if force or not self.download_status.get("active"):
            self.download_status["current_file"] = ""
            self.download_status["current_file_progress"] = 0
            self.download_status["current_file_size"] = 0
            self.download_status["downloaded_bytes"] = 0
            # If forced cleanup at startup, ensure consistency
            if force:
                 # Check if we should actually be 'active'
                 has_queue = len(self.download_status.get("queue", [])) > 0
                 # We keep active status if there's a queue so auto-resume works
                 if not has_queue:
                     self.download_status["active"] = False

        self.save_state()
        return cleaned_items

    def get_status(self) -> Dict[str, Any]:
        """Get current download status"""
        return self.download_status

    def update_status(self, updates: Dict[str, Any]):
        """Update download status"""
        if "active" in updates and updates["active"] != self.download_status.get("active"):
            logger.info(f"[STATE] Active changed: {self.download_status.get('active')} -> {updates['active']}")
        if "cancelled" in updates and updates["cancelled"] != self.download_status.get("cancelled"):
            logger.info(f"[STATE] Cancelled changed: {self.download_status.get('cancelled')} -> {updates['cancelled']}")
            
        self.download_status.update(updates)
        self.save_state()

    def is_file_active_or_queued(self, channel: str, message_id: int) -> bool:
        """Check if a file is already in queue or being downloaded"""
        # Check queue
        queue = self.download_status.get("queue", [])
        for item in queue:
            if item.get("channel") == channel and item.get("message_id") == message_id:
                # ONLY return True if it's actually waiting
                if item.get("status") == "queued":
                    return True
        
        # Check concurrent downloads
        concurrent = self.download_status.get("concurrent_downloads", {})
        for fid, data in concurrent.items():
            # Support both string and int channel identifiers
            if str(data.get("channel")) == str(channel) and data.get("message_id") == message_id:
                return True
                
        return False

    async def mark_file_completed(self, file_id: str, file_data: Dict[str, Any]):
        """Thread-safe method to mark a file as completed and update progress"""
        async with self._lock:
            # Add to completed downloads
            self.download_status["completed_downloads"][file_id] = file_data

            # Remove from concurrent downloads if present
            if file_id in self.download_status.get("concurrent_downloads", {}):
                del self.download_status["concurrent_downloads"][file_id]

            # Update progress - Count only files from the CURRENT session
            session_id = self.download_status.get("session_id")
            current_count = 0
            for fid in self.download_status.get("completed_downloads", {}):
                if session_id and session_id in fid:
                    current_count += 1
                elif fid.startswith("single_"):
                    # For single downloads, they effectively ARE their own session 
                    # but if we started a new bulk session, they shouldn't count towards its progress
                    pass
            
            # Fallback: if we can't match session_id, use global count as before 
            # (but usually we'll have a session_id)
            if current_count == 0 and session_id:
                # If everything in history is old, progress for this new session is just 0
                self.download_status["progress"] = 0
            else:
                self.download_status["progress"] = current_count or len(self.download_status["completed_downloads"])

            self.save_state()
            logger.info(f"File {file_id} marked as completed. Session Progress: {self.download_status['progress']}/{self.download_status.get('total', 0)}")

    # Queue Management Methods

    def get_queue(self) -> List[Dict[str, Any]]:
        """Get current download queue"""
        return self.download_status.get("queue", [])

    def add_to_queue(self, items: List[Dict[str, Any]]):
        """Add items to the download queue"""
        if "queue" not in self.download_status:
            self.download_status["queue"] = []
        
        # Add new items with default status
        existing_ids = {item.get("id") for item in self.download_status["queue"]}
        added_count = 0
        
        for item in items:
            if item.get("id") in existing_ids:
                continue
                
            item["added_at"] = datetime.now().isoformat()
            if "status" not in item:
                item["status"] = "queued"
            if "priority" not in item:
                item["priority"] = 0
            self.download_status["queue"].append(item)
            existing_ids.add(item.get("id"))
            added_count += 1
        
        if added_count > 0:
            self.save_state()
            logger.info(f"Added {added_count} items to queue. Total in queue: {len(self.download_status['queue'])}")

    def remove_from_queue(self, queue_id: str):
        """Remove an item from the queue"""
        if "queue" in self.download_status:
            original_len = len(self.download_status["queue"])
            self.download_status["queue"] = [
                item for item in self.download_status["queue"] 
                if item.get("id") != queue_id
            ]
            if len(self.download_status["queue"]) < original_len:
                self.save_state()
                logger.info(f"Removed item {queue_id} from queue")

    def reorder_queue(self, queue_ids: List[str]):
        """Reorder queue based on list of IDs"""
        if "queue" not in self.download_status:
            return
            
        current_queue = {item["id"]: item for item in self.download_status["queue"]}
        new_queue = []
        
        # Add items in the new order
        for q_id in queue_ids:
            if q_id in current_queue:
                new_queue.append(current_queue[q_id])
                del current_queue[q_id] # Remove processed
        
        # Append any remaining items (that weren't in the reorder list)
        for item in current_queue.values():
            new_queue.append(item)
            
        self.download_status["queue"] = new_queue
        self.save_state()
        logger.info("Queue reordered")

    def get_next_queued_item(self) -> Optional[Dict[str, Any]]:
        """Get the next item to download based on priority and time"""
        if "queue" not in self.download_status or not self.download_status["queue"]:
            return None
            
        # Filter for 'queued' items only
        queued_items = [
            item for item in self.download_status["queue"] 
            if item.get("status") == "queued"
        ]
        
        if not queued_items:
            # logger.debug("Refusing to pick next item: No items with status='queued'")
            return None
            
        # Sort by priority (desc) then added_at (asc)
        # Priority 10 comes before Priority 0
        queued_items.sort(key=lambda x: (-x.get("priority", 0), x.get("added_at", "")))
        
        next_item = queued_items[0]
        logger.info(f"Picking next item from queue: {next_item.get('name')} (ID: {next_item.get('id')})")
        return next_item

    def update_queue_item_status(self, queue_id: str, status: str):
        """Update status of a queue item"""
        if "queue" in self.download_status:
            for item in self.download_status["queue"]:
                if item.get("id") == queue_id:
                    item["status"] = status
                    self.save_state()
                    break

