import json
import os
import logging
from datetime import datetime
from typing import Dict, Any
import uuid

from app.config import Config

logger = logging.getLogger(__name__)


class StateManager:
    """Manages download state persistence"""

    def __init__(self):
        self.state_file = Config.STATE_FILE
        self.download_status = self._initialize_status()

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
            "cancelled": False,
            "session_id": str(uuid.uuid4()),
            "started_at": None,
            "channel": None
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
                "cancelled": self.download_status.get("cancelled", False),
                "session_id": self.download_status.get("session_id"),
                "started_at": self.download_status.get("started_at"),
                "channel": self.download_status.get("channel")
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
