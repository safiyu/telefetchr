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

    def save_state(self):
        """Save current download state to file"""
        try:
            os.makedirs(Config.SESSION_DIR, exist_ok=True)

            # Create a copy to save
            state_to_save = {
                "active": self.download_status.get("active", False),
                "progress": self.download_status.get("progress", 0),
                "total": self.download_status.get("total", 0),
                "current_file": self.download_status.get("current_file", ""),
                "current_file_progress": self.download_status.get("current_file_progress", 0),
                "current_file_size": self.download_status.get("current_file_size", 0),
                "downloaded_bytes": self.download_status.get("downloaded_bytes", 0),
                "completed_downloads": self.download_status.get("completed_downloads", {}),
                "cancelled_files": self.download_status.get("cancelled_files", {}),
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

                    # Only restore if the state is meaningful
                    if saved_state.get("session_id") and saved_state.get("channel"):
                        # If there was an active download, mark it as not active
                        if saved_state.get("active"):
                            saved_state["active"] = False
                            saved_state["cancelled"] = True
                            logger.info("Found interrupted download session - marked for resume")

                        # Merge with current status
                        self.download_status.update(saved_state)

                        # Clear concurrent downloads (they're not valid after restart)
                        self.download_status["concurrent_downloads"] = {}

                        logger.info(f"Loaded saved download state: {len(saved_state.get('completed_downloads', {}))} completed files")
                    else:
                        logger.info("No valid saved state found")

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

        except Exception as e:
            logger.error(f"Error clearing state: {e}")

    def cleanup_state(self):
        """Clean up corrupted or incomplete state"""
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
        if not self.download_status.get("active") and self.download_status.get("concurrent_downloads"):
            cleaned_items = list(self.download_status["concurrent_downloads"].keys())
            self.download_status["concurrent_downloads"] = {}
            logger.info(f"Cleaned up {len(cleaned_items)} incomplete downloads")

        # Reset fields that don't make sense when not active
        if not self.download_status.get("active"):
            self.download_status["current_file"] = ""
            self.download_status["current_file_progress"] = 0
            self.download_status["current_file_size"] = 0
            self.download_status["downloaded_bytes"] = 0

        # If there are no completed downloads and no channel, reset everything
        if not self.download_status.get("completed_downloads") and not self.download_status.get("channel"):
            self.download_status.update(self._initialize_status())
            logger.info("Reset state completely (no valid session data)")

        self.save_state()
        return cleaned_items

    def get_status(self) -> Dict[str, Any]:
        """Get current download status"""
        return self.download_status

    def update_status(self, updates: Dict[str, Any]):
        """Update download status"""
        self.download_status.update(updates)
        self.save_state()

    async def mark_file_completed(self, file_id: str, file_data: Dict[str, Any]):
        """Thread-safe method to mark a file as completed and update progress"""
        async with self._lock:
            # Add to completed downloads
            self.download_status["completed_downloads"][file_id] = file_data

            # Remove from concurrent downloads if present
            if file_id in self.download_status.get("concurrent_downloads", {}):
                del self.download_status["concurrent_downloads"][file_id]

            # Update progress based on current completed count
            self.download_status["progress"] = len(self.download_status["completed_downloads"])

            # Save the state
            self.save_state()

            self.save_state()

            logger.info(f"File {file_id} marked as completed. Progress: {self.download_status['progress']}/{self.download_status.get('total', 0)}")

    # Queue Management Methods

    def get_queue(self) -> List[Dict[str, Any]]:
        """Get current download queue"""
        return self.download_status.get("queue", [])

    def add_to_queue(self, items: List[Dict[str, Any]]):
        """Add items to the download queue"""
        if "queue" not in self.download_status:
            self.download_status["queue"] = []
        
        # Add new items with default status
        for item in items:
            item["added_at"] = datetime.now().isoformat()
            item["status"] = "queued"
            if "priority" not in item:
                item["priority"] = 0
            self.download_status["queue"].append(item)
        
        self.save_state()
        logger.info(f"Added {len(items)} items to queue. Total in queue: {len(self.download_status['queue'])}")

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
            return None
            
        # Sort by priority (desc) then added_at (asc)
        # Priority 10 comes before Priority 0
        queued_items.sort(key=lambda x: (-x.get("priority", 0), x.get("added_at", "")))
        
        return queued_items[0]

    def update_queue_item_status(self, queue_id: str, status: str):
        """Update status of a queue item"""
        if "queue" in self.download_status:
            for item in self.download_status["queue"]:
                if item.get("id") == queue_id:
                    item["status"] = status
                    self.save_state()
                    break

