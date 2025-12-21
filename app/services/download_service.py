import os
import logging
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
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
        self._queue_processing = False
        self._stop_queue = False 

    def _get_file_name(self, message) -> str:
        """Extract file name from message"""
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            return next((attr.file_name for attr in doc.attributes
                        if hasattr(attr, 'file_name')), f"document_{message.id}")
        elif isinstance(message.media, MessageMediaPhoto):
            return f"photo_{message.id}.jpg"
        return "unknown"

    async def _download_queued_item(self, item: Dict[str, Any]):
        """Download a single item from the queue"""
        file_id = item.get("id")
        session_id = item.get("session_id")
        try:
            channel = item["channel"]
            message_id = item["message_id"]
            target_dir = Config.SAVE_PATH
            
            logger.info(f"Processing queued item: {channel}/{message_id} ({file_id})")
            
            # Fetch message
            message = await self.telegram_service.get_message(channel, message_id)
            if not message:
                logger.error(f"Message not found for queued item {file_id}")
                self.state_manager.update_queue_item_status(file_id, "failed")
                return

            # Download using the robust method - passing session_id
            result = await self.download_single_file(message, target_dir, file_id, session_id=session_id)
            
            if result:
                self.state_manager.remove_from_queue(file_id)
            else:
                self.state_manager.update_queue_item_status(file_id, "failed")
                
        except Exception as e:
            if "cancelled" not in str(e).lower():
                logger.error(f"Error processing queued item {file_id}: {e}")
            if file_id:
                self.state_manager.update_queue_item_status(file_id, "failed")
        finally:
            # Task cleanup is handled in the spawning point or here
            if file_id in self.active_download_tasks:
                del self.active_download_tasks[file_id]

    async def process_queue(self):
        """Main loop to process downloads from queue"""
        if self._queue_processing:
            return
            
        self._queue_processing = True
        logger.info("Starting queue processor loop")
        
        try:
            while True:
                # Check if we should stop
                if self._stop_queue:
                    logger.info("Queue processor received stop signal")
                    break
                    
                # Check for cancelled state
                status = self.state_manager.get_status()
                if status.get("cancelled"):
                    logger.info("Queue processor detected cancelled state, stopping")
                    break
                
                queue = self.state_manager.get_queue()
                if not queue:
                    # Queue empty, wait a bit then check again
                    await asyncio.sleep(2)
                    continue

                # Check concurrency limit
                status = self.state_manager.get_status()
                current_concurrent = len(status.get("concurrent_downloads", {}))
                
                if current_concurrent >= Config.MAX_CONCURRENT_DOWNLOADS:
                    await asyncio.sleep(1)
                    continue
                
                # Get next item
                item = self.state_manager.get_next_queued_item()
                if not item:
                    await asyncio.sleep(1)
                    continue
                    
                logger.info(f"Starting download for queued item {item['id']}")
                
                # Mark as downloading BEFORE launching task to prevent race condition
                self.state_manager.update_queue_item_status(item['id'], "downloading")
                
                # Launch task and track it
                task = asyncio.create_task(self._download_queued_item(item))
                self.active_download_tasks[item['id']] = task
                
                # Small yield
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Queue processor crashed: {e}")
        finally:
            self._queue_processing = False
            logger.info("Queue processor loop stopped")

    def start_queue_processor(self):
        """Start the queue processor if not running"""
        if not self._queue_processing:
            self._stop_queue = False
            asyncio.create_task(self.process_queue())

    def _reset_session_state(self, channel_username: str, total_count: int, session_id: str):
        """Initialize state for a new download session"""
        self.state_manager.update_status({
            "active": True,
            "cancelled": False,
            "session_id": session_id,
            "channel": channel_username,
            "started_at": datetime.now().isoformat(),
            "total": total_count,
            "progress": 0, # Reset progress counter
            "concurrent_downloads": {},
            "cancelled_files": {}
        })

    async def download_selected_files(self, channel_username: str, message_ids: List[int]) -> str:
        """Add selected files to queue in background"""
        logger.info(f"Preparing to queue {len(message_ids)} selected files")
        session_id = str(uuid.uuid4())
        
        # Initialize session state immediately with total known (but metadata not yet fetched)
        self._reset_session_state(channel_username, len(message_ids), session_id)
        
        async def collection_task():
            try:
                # Use bulk fetch for efficiency
                message_list = await self.telegram_service.get_messages(channel_username, message_ids)
                items_to_queue = []
                
                for msg in message_list:
                    if self.state_manager.is_file_active_or_queued(channel_username, msg.id):
                        logger.info(f"Skipping duplicate file: {self._get_file_name(msg)} (ID: {msg.id})")
                        continue

                    file_name = self._get_file_name(msg)
                    items_to_queue.append({
                        "id": f"file_{session_id}_{msg.id}",
                        "message_id": msg.id,
                        "channel": channel_username,
                        "name": file_name,
                        "priority": 0,
                        "status": "queued",
                        "session_id": session_id
                    })

                self.state_manager.add_to_queue(items_to_queue)
                
                # Update total in case some messages weren't valid (though usually they are)
                if len(items_to_queue) != len(message_ids):
                    self.state_manager.update_status({"total": len(items_to_queue)})
                    
                self.start_queue_processor()
                logger.info(f"Successfully queued {len(items_to_queue)} selected files in background")
            except Exception as e:
                logger.error(f"Background collection task for selected files failed: {e}")

        # Start background task
        asyncio.create_task(collection_task())
        
        return session_id

    async def download_all_files(self, channel_username: str, limit: int, filter_type: Optional[str] = None) -> str:
        """Queue all files from channel in background"""
        logger.info(f"Preparing to queue files from {channel_username}, limit={limit}")
        session_id = str(uuid.uuid4())
        
        # Initialize state with 0 total (will be updated during collection)
        self._reset_session_state(channel_username, 0, session_id)
        
        async def collection_task():
            try:
                items_to_queue = []
                count = 0
                
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
                            if self.state_manager.is_file_active_or_queued(channel_username, message.id):
                                logger.info(f"Skipping duplicate file in scan: {self._get_file_name(message)}")
                                continue

                            file_name = self._get_file_name(message)
                            items_to_queue.append({
                                 "id": f"file_{session_id}_{message.id}",
                                 "message_id": message.id,
                                 "channel": channel_username,
                                 "name": file_name,
                                 "priority": 0,
                                 "status": "queued",
                                 "session_id": session_id
                            })
                            count += 1
                
                if items_to_queue:
                    self.state_manager.add_to_queue(items_to_queue)
                    self.state_manager.update_status({"total": count})
                    self.start_queue_processor()
                    logger.info(f"Successfully queued {count} files from {channel_username} in background")
                else:
                    logger.info(f"No matchable files found in {channel_username}")
                    self.state_manager.update_status({"active": False})
                    
            except Exception as e:
                logger.error(f"Background collection task for all files failed: {e}")
                self.state_manager.update_status({"active": False})

        # Start background task
        asyncio.create_task(collection_task())
        
        return session_id

    async def download_single_file(self, message, target_dir: str, file_id: str, max_retries: int = 3, session_id: Optional[str] = None) -> Optional[str]:
        """Download a single file with progress tracking and retry logic"""
        file_name = self._get_file_name(message)
        logger.info(f"Starting download: {file_name} (ID: {file_id})")

        for attempt in range(max_retries):
            try:
                status = self.state_manager.get_status()
                status["concurrent_downloads"][file_id] = {
                    "name": file_name,
                    "progress": 0,
                    "total": 0,
                    "percentage": 0,
                    "retry_attempt": attempt + 1 if attempt > 0 else None
                }
                self.state_manager.save_state()

                last_progress_time = datetime.now()
                last_progress_bytes = 0

                def progress_callback(current, total):
                    nonlocal last_progress_time, last_progress_bytes
                    
                    # Check for cancellation within callback
                    status = self.state_manager.get_status()
                    
                    # Abandon if session changed or stopped
                    if self._stop_queue:
                        raise Exception("Queue stopped")
                    if status.get("cancelled"):
                        raise Exception("Download cancelled")
                    if session_id and status.get("session_id") != session_id:
                        raise Exception(f"Session changed: task session {session_id} != current {status.get('session_id')}")

                    now = datetime.now()
                    time_diff = (now - last_progress_time).total_seconds()
                    
                    if time_diff >= 0.5 or current == total:
                        bytes_diff = current - last_progress_bytes
                        speed = bytes_diff / time_diff if time_diff > 0 else 0
                        eta = (total - current) / speed if speed > 0 else 0
                        
                        last_progress_time = now
                        last_progress_bytes = current

                        if file_id in status["concurrent_downloads"]:
                            item_status = status["concurrent_downloads"][file_id]
                            item_status["progress"] = current
                            item_status["total"] = total
                            item_status["percentage"] = int((current / total * 100)) if total > 0 else 0
                            item_status["speed"] = speed
                            item_status["eta"] = eta
                            item_status["last_update"] = now.isoformat()
                            self.state_manager.save_state()

                expected_file_path = os.path.join(target_dir, file_name)
                
                try:
                    # Use the parallel downloader for speed
                    file_path = await self.telegram_service.download_media_parallel(
                        message,
                        expected_file_path,
                        progress_callback,
                        workers=Config.DOWNLOAD_WORKERS
                    )
                except Exception as e:
                    if "cancelled" in str(e).lower():
                        raise
                    logger.error(f"Download attempt {attempt+1} failed for {file_name}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise

                if file_path and os.path.exists(file_path):
                    final_size = os.path.getsize(file_path)
                    file_data = {
                        "name": file_name,
                        "path": file_path,
                        "size": final_size,
                        "percentage": 100,
                        "completed_at": datetime.now().isoformat()
                    }
                    await self.state_manager.mark_file_completed(file_id, file_data)
                    
                    # Cleanup from concurrent downloads
                    status = self.state_manager.get_status()
                    if file_id in status.get("concurrent_downloads", {}):
                        del status["concurrent_downloads"][file_id]
                    self.state_manager.save_state()
                    
                    return file_path
                return None

            except Exception as e:
                error_msg = str(e)
                if "cancelled" in error_msg.lower():
                    logger.info(f"Download cancelled: {file_name}")
                    # Cleanup
                    status = self.state_manager.get_status()
                    if file_id in status.get("concurrent_downloads", {}):
                        del status["concurrent_downloads"][file_id]
                    self.state_manager.save_state()
                    return None
                
                if attempt == max_retries - 1:
                    logger.error(f"Final attempt failed for {file_name}: {error_msg}")
                    status = self.state_manager.get_status()
                    if file_id in status.get("concurrent_downloads", {}):
                        del status["concurrent_downloads"][file_id]
                    self.state_manager.save_state()
                    return None

        return None

    async def cancel_download(self) -> Dict:
        """Cancel active downloads and clear queue"""
        logger.info("Cancel download requested")
        
        # Signal queue processor to stop
        self._stop_queue = True
        
        # Cancel all active asyncio tasks
        for fid, task in list(self.active_download_tasks.items()):
            logger.info(f"Cancelling task for {fid}")
            task.cancel()
        self.active_download_tasks.clear()
        
        # Clear queue contents
        queue = self.state_manager.get_queue()
        queue_ids = [item["id"] for item in queue]
        for qid in queue_ids:
            self.state_manager.remove_from_queue(qid)
        
        # Get current status to move active items to cancelled_files
        status = self.state_manager.get_status()
        concurrent = status.get("concurrent_downloads", {})
        
        for fid, data in list(concurrent.items()):
            status["cancelled_files"][fid] = {
                "name": data.get("name"),
                "progress": data.get("progress"),
                "total": data.get("total"),
                "timestamp": datetime.now().isoformat()
            }
        
        status["cancelled"] = True
        status["active"] = False
        status["concurrent_downloads"] = {}
        self.state_manager.save_state()
        
        # Brief delay to allow tasks to catch the cancellation
        await asyncio.sleep(0.5)
        self._stop_queue = False
        
        return {"status": "success", "message": f"Download cancelled. Stopped {len(queue_ids)} items."}

    async def resume_download(self) -> Dict:
        """Resume interrupted download by re-queueing missing files"""
        status = self.state_manager.get_status()

        if not status.get("channel"):
            return {"status": "error", "message": "No session to resume"}

        if status.get("active") and not status.get("cancelled"):
            return {"status": "info", "message": "Download already active"}

        channel = status.get("channel")
        total = status.get("total", 0)
        session_id = status.get("session_id") or str(uuid.uuid4())

        # Reset status for resume
        status["active"] = True
        status["cancelled"] = False
        status["concurrent_downloads"] = {}
        self.state_manager.save_state()

        async def resume_task():
            try:
                completed_ids = set()
                # Extract message IDs from completed files
                for fid in status.get("completed_downloads", {}):
                    try:
                        completed_ids.add(int(fid.split("_")[-1]))
                    except: pass

                logger.info(f"Resuming session {session_id}. Channel: {channel}")
                
                messages_to_queue = []
                async for message in await self.telegram_service.iter_messages(channel, total):
                    if message.media and message.id not in completed_ids:
                        messages_to_queue.append({
                            "id": f"file_{session_id}_{message.id}",
                            "message_id": message.id,
                            "channel": channel,
                            "name": self._get_file_name(message),
                            "priority": 1, # Higher priority for resumed items
                            "status": "queued",
                            "session_id": session_id
                        })

                if not messages_to_queue:
                    logger.info("Nothing to resume")
                    status["active"] = False
                    self.state_manager.save_state()
                    return

                self.state_manager.add_to_queue(messages_to_queue)
                self.start_queue_processor()

            except Exception as e:
                logger.error(f"Resume failed: {e}")
                status["active"] = False
                self.state_manager.save_state()

        asyncio.create_task(resume_task())
        
        return {"status": "success", "message": "Resume task started"}

    async def download_single(self, channel_username: str, message_id: int):
        """Add a single file to the queue and start processor"""
        logger.info(f"Adding single file {message_id} from {channel_username} to queue")
        
        # Verify message exists and get metadata
        message = await self.telegram_service.get_message(channel_username, message_id)
        if not message:
            logger.error(f"Message {message_id} not found")
            return
            
        file_name = self._get_file_name(message)
        
        # Get current status to see if there's an active session
        status = self.state_manager.get_status()
        session_id = status.get("session_id") or str(uuid.uuid4())
        file_id = f"single_{message_id}"
        
        # If not already downloading or queued
        if not self.state_manager.is_file_active_or_queued(channel_username, message_id):
            # If not active, start a "session"
            if not status.get("active"):
                self._reset_session_state(channel_username, 1, session_id)
            else:
                # If active, just increment total
                current_total = status.get("total", 0)
                self.state_manager.update_status({"total": current_total + 1})
                
            item = {
                "id": file_id,
                "message_id": message_id,
                "channel": channel_username,
                "name": file_name,
                "priority": 2, # Higher priority than "download all" (0) or "selected" (0)
                "status": "queued",
                "session_id": session_id
            }
            
            self.state_manager.add_to_queue([item])
            self.start_queue_processor()
        else:
            logger.info(f"File {message_id} is already active or in queue")

    def cleanup_tasks(self):
        """Cleanup active download tasks (not fully needed with queue but good for shutdown)"""
        self._stop_queue = True
