import os
import logging
import asyncio
from datetime import datetime
from typing import List, Optional, Dict
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto
import uuid

from app.config import Config
from app.services.telegram_service import TelegramService
from app.utils.state_manager import StateManager

logger = logging.getLogger(__name__)


class DownloadService:
    """Service for managing file downloads"""

    def __init__(self, telegram_service: TelegramService, state_manager: StateManager):
        self.telegram_service = telegram_service
        self.state_manager = state_manager
        self.active_download_tasks = {}

    def _get_file_name(self, message) -> str:
        """Extract file name from message"""
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            return next((attr.file_name for attr in doc.attributes
                        if hasattr(attr, 'file_name')), f"document_{message.id}")
        elif isinstance(message.media, MessageMediaPhoto):
            return f"photo_{message.id}.jpg"
        return "unknown"

    async def download_single_file(self, message, target_dir: str, file_id: str) -> Optional[str]:
        """Download a single file with progress tracking"""
        try:
            file_name = self._get_file_name(message)
            logger.info(f"Starting download: {file_name}")

            status = self.state_manager.get_status()
            status["concurrent_downloads"][file_id] = {
                "name": file_name,
                "progress": 0,
                "total": 0,
                "percentage": 0
            }
            self.state_manager.save_state()

            def progress_callback(current, total):
                if status["cancelled"]:
                    raise Exception("Download cancelled by user")
                status["concurrent_downloads"][file_id]["progress"] = current
                status["concurrent_downloads"][file_id]["total"] = total
                status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
                self.state_manager.save_state()

            file_path = await self.telegram_service.download_media(
                message,
                target_dir,
                progress_callback
            )

            if file_path:
                # Move to completed downloads
                status["completed_downloads"][file_id] = {
                    "name": file_name,
                    "path": file_path,
                    "size": status["concurrent_downloads"][file_id]["total"],
                    "completed_at": datetime.now().isoformat()
                }

            if file_id in status["concurrent_downloads"]:
                del status["concurrent_downloads"][file_id]
            self.state_manager.save_state()

            logger.info(f"Completed download: {file_name}")
            return file_path

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            status = self.state_manager.get_status()
            if file_id in status["concurrent_downloads"]:
                del status["concurrent_downloads"][file_id]
            self.state_manager.save_state()
            if "cancelled" in error_msg.lower():
                return None
            return None

    async def download_selected_files(self, channel_username: str, message_ids: List[int]) -> str:
        """Download selected files with parallel processing"""
        logger.info(f"Starting download of {len(message_ids)} selected files")

        # Initialize new session
        session_id = str(uuid.uuid4())
        self.state_manager.update_status({
            "active": True,
            "progress": 0,
            "total": len(message_ids),
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "concurrent_downloads": {},
            "completed_downloads": {},
            "cancelled": False,
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "channel": channel_username
        })

        async def download_task():
            try:
                target_dir = Config.SAVE_PATH
                os.makedirs(target_dir, exist_ok=True)

                logger.info(f"Fetching {len(message_ids)} messages from {channel_username}")
                messages_to_download = await self.telegram_service.get_messages(channel_username, message_ids)

                total_files = len(messages_to_download)
                status = self.state_manager.get_status()
                status["total"] = total_files
                self.state_manager.save_state()

                logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={Config.MAX_CONCURRENT_DOWNLOADS}")

                if total_files == 0:
                    logger.warning("No files to download!")
                    status["active"] = False
                    self.state_manager.save_state()
                    return

                downloaded = []

                for i in range(0, len(messages_to_download), Config.MAX_CONCURRENT_DOWNLOADS):
                    if status["cancelled"]:
                        logger.info("Download cancelled by user")
                        break

                    batch = messages_to_download[i:i + Config.MAX_CONCURRENT_DOWNLOADS]
                    logger.info(f"Processing batch {i // Config.MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")

                    tasks = []
                    for idx, message in enumerate(batch):
                        file_id = f"file_{i + idx}_{message.id}"
                        tasks.append(self.download_single_file(message, target_dir, file_id))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if result and not isinstance(result, Exception):
                            downloaded.append(result)
                            status["progress"] = len(downloaded)
                            self.state_manager.save_state()
                            logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")

                status["active"] = False
                status["concurrent_downloads"] = {}
                self.state_manager.save_state()
                logger.info(f"Download completed. Total files: {len(downloaded)}")

            except Exception as e:
                status = self.state_manager.get_status()
                status["active"] = False
                status["concurrent_downloads"] = {}
                self.state_manager.save_state()
                logger.error(f"Background download error: {str(e)}", exc_info=True)

        # Create task and track it
        task = asyncio.create_task(download_task())
        self.active_download_tasks[session_id] = task

        return session_id

    async def download_all_files(self, channel_username: str, limit: int, filter_type: Optional[str] = None) -> str:
        """Download all files from channel"""
        logger.info(f"Starting download-all from {channel_username}, limit={limit}")

        # Initialize new session
        session_id = str(uuid.uuid4())
        self.state_manager.update_status({
            "active": True,
            "progress": 0,
            "total": 0,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "concurrent_downloads": {},
            "completed_downloads": {},
            "cancelled": False,
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "channel": channel_username
        })

        async def download_task():
            try:
                target_dir = Config.SAVE_PATH
                os.makedirs(target_dir, exist_ok=True)

                logger.info("Fetching messages from channel...")
                messages_to_download = []

                async for message in await self.telegram_service.iter_messages(channel_username, limit):
                    if message.media:
                        should_download = False

                        if isinstance(message.media, MessageMediaDocument):
                            if not filter_type or filter_type == 'document':
                                should_download = True
                        elif isinstance(message.media, MessageMediaPhoto):
                            if not filter_type or filter_type == 'photo':
                                should_download = True

                        if should_download:
                            messages_to_download.append(message)

                total_files = len(messages_to_download)
                status = self.state_manager.get_status()
                status["total"] = total_files
                self.state_manager.save_state()

                logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={Config.MAX_CONCURRENT_DOWNLOADS}")

                if total_files == 0:
                    logger.warning("No files to download!")
                    status["active"] = False
                    self.state_manager.save_state()
                    return

                downloaded = []

                for i in range(0, len(messages_to_download), Config.MAX_CONCURRENT_DOWNLOADS):
                    if status["cancelled"]:
                        logger.info("Download cancelled by user")
                        break

                    batch = messages_to_download[i:i + Config.MAX_CONCURRENT_DOWNLOADS]
                    logger.info(f"Processing batch {i // Config.MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")

                    tasks = []
                    for idx, message in enumerate(batch):
                        file_id = f"file_{i + idx}_{message.id}"
                        tasks.append(self.download_single_file(message, target_dir, file_id))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if result and not isinstance(result, Exception):
                            downloaded.append(result)
                            status["progress"] = len(downloaded)
                            self.state_manager.save_state()
                            logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")

                status["active"] = False
                status["concurrent_downloads"] = {}
                self.state_manager.save_state()
                logger.info(f"Download completed. Total files: {len(downloaded)}")

            except Exception as e:
                status = self.state_manager.get_status()
                status["active"] = False
                status["concurrent_downloads"] = {}
                self.state_manager.save_state()
                logger.error(f"Background download error: {str(e)}", exc_info=True)

        # Create task and track it
        task = asyncio.create_task(download_task())
        self.active_download_tasks[session_id] = task

        return session_id

    async def download_single(self, channel_username: str, message_id: int) -> Dict:
        """Download a single file"""
        target_dir = Config.SAVE_PATH
        os.makedirs(target_dir, exist_ok=True)

        message = await self.telegram_service.get_message(channel_username, message_id)
        if not message or not message.media:
            raise ValueError("File not found")

        file_name = self._get_file_name(message)
        file_id = f"single_{message_id}"

        status = self.state_manager.get_status()
        status.update({
            "active": True,
            "current_file": file_name,
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "cancelled": False,
            "channel": channel_username,
            "session_id": status.get("session_id") or str(uuid.uuid4()),
            "started_at": status.get("started_at") or datetime.now().isoformat()
        })

        status["concurrent_downloads"][file_id] = {
            "name": file_name,
            "progress": 0,
            "total": 0,
            "percentage": 0
        }
        self.state_manager.save_state()

        def progress_callback(current, total):
            if status["cancelled"]:
                raise Exception("Download cancelled by user")
            status["current_file_progress"] = current
            status["current_file_size"] = total
            status["downloaded_bytes"] = current
            status["concurrent_downloads"][file_id]["progress"] = current
            status["concurrent_downloads"][file_id]["total"] = total
            status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
            self.state_manager.save_state()

        file_path = await self.telegram_service.download_media(
            message,
            target_dir,
            progress_callback
        )

        if file_path:
            status["completed_downloads"][file_id] = {
                "name": file_name,
                "path": file_path,
                "size": status["concurrent_downloads"][file_id]["total"],
                "completed_at": datetime.now().isoformat()
            }
            status["progress"] = len(status["completed_downloads"])

        if file_id in status["concurrent_downloads"]:
            del status["concurrent_downloads"][file_id]

        status.update({
            "active": False,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0
        })
        self.state_manager.save_state()

        if not file_path:
            raise ValueError("Download failed")

        return {"file_path": file_path, "file_name": file_name}

    async def cancel_download(self) -> Dict:
        """Cancel active download"""
        status = self.state_manager.get_status()

        if status["active"] or status["current_file_progress"] > 0:
            status["cancelled"] = True
            self.state_manager.save_state()

            # Cancel active tasks
            session_id = status.get("session_id")
            if session_id and session_id in self.active_download_tasks:
                task = self.active_download_tasks[session_id]
                if not task.done():
                    task.cancel()
                del self.active_download_tasks[session_id]

            status.update({
                "active": False,
                "progress": len(status.get("completed_downloads", {})),
                "current_file": "",
                "current_file_progress": 0,
                "current_file_size": 0,
                "downloaded_bytes": 0,
                "concurrent_downloads": {},
                "cancelled": True
            })
            self.state_manager.save_state()

            logger.info("Download cancellation requested")
            return {
                "status": "success",
                "message": "Download cancelled. You can resume later."
            }
        else:
            return {
                "status": "info",
                "message": "No active download to cancel"
            }

    async def resume_download(self) -> Dict:
        """Resume interrupted download"""
        status = self.state_manager.get_status()

        if not status.get("channel"):
            return {
                "status": "error",
                "message": "No saved download session to resume"
            }

        if status.get("active"):
            return {
                "status": "info",
                "message": "Download already in progress"
            }

        channel = status.get("channel")
        total = status.get("total", 0)

        # Get completed file IDs to skip them
        completed_ids = set()
        for file_id, data in status.get("completed_downloads", {}).items():
            if "_" in file_id:
                parts = file_id.split("_")
                if len(parts) >= 3:
                    try:
                        completed_ids.add(int(parts[-1]))
                    except:
                        pass

        logger.info(f"Resuming download session {status.get('session_id')}")
        logger.info(f"Channel: {channel}, Total: {total}, Completed: {len(completed_ids)}")

        status["active"] = True
        status["cancelled"] = False
        self.state_manager.save_state()

        async def resume_task():
            try:
                target_dir = Config.SAVE_PATH
                os.makedirs(target_dir, exist_ok=True)

                logger.info(f"Fetching messages from {channel} to resume download...")

                # Fetch all messages again and filter out completed ones
                messages_to_download = []
                async for message in await self.telegram_service.iter_messages(channel, total):
                    if message.media and message.id not in completed_ids:
                        messages_to_download.append(message)

                remaining_files = len(messages_to_download)
                logger.info(f"Found {remaining_files} remaining files to download out of {total} total")

                if remaining_files == 0:
                    logger.info("All files already downloaded!")
                    status = self.state_manager.get_status()
                    status["active"] = False
                    status["progress"] = status["total"]
                    self.state_manager.save_state()
                    return

                downloaded = []

                # Process remaining files in batches
                for i in range(0, len(messages_to_download), Config.MAX_CONCURRENT_DOWNLOADS):
                    status = self.state_manager.get_status()
                    if status["cancelled"]:
                        logger.info("Download cancelled by user")
                        break

                    batch = messages_to_download[i:i + Config.MAX_CONCURRENT_DOWNLOADS]
                    logger.info(f"Processing batch {i // Config.MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")

                    tasks = []
                    for idx, message in enumerate(batch):
                        file_id = f"file_{len(completed_ids) + i + idx}_{message.id}"
                        tasks.append(self.download_single_file(message, target_dir, file_id))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if result and not isinstance(result, Exception):
                            downloaded.append(result)
                            logger.info(f"Downloaded ({len(completed_ids) + len(downloaded)}/{total}): {result}")

                status = self.state_manager.get_status()
                status["active"] = False
                status["concurrent_downloads"] = {}
                status["progress"] = len(status.get("completed_downloads", {}))
                self.state_manager.save_state()
                logger.info(f"Resume completed. Total files now: {status['progress']}/{total}")

            except Exception as e:
                status = self.state_manager.get_status()
                status["active"] = False
                status["concurrent_downloads"] = {}
                self.state_manager.save_state()
                logger.error(f"Resume download error: {str(e)}", exc_info=True)

        # Create task and track it
        task_id = status.get("session_id") or str(uuid.uuid4())
        status["session_id"] = task_id
        task = asyncio.create_task(resume_task())
        self.active_download_tasks[task_id] = task

        return {
            "status": "resumed",
            "message": f"Resumed download with {len(completed_ids)} files already completed",
            "session_id": task_id,
            "completed": len(completed_ids),
            "remaining": total - len(completed_ids),
            "total": total
        }

    def cleanup_tasks(self):
        """Cleanup active download tasks"""
        for task_id, task in list(self.active_download_tasks.items()):
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled task {task_id} during cleanup")
