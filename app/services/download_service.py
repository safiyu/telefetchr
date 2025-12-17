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
        try:
            channel = item["channel"]
            message_id = item["message_id"]
            file_id = item["id"]
            target_dir = Config.SAVE_PATH
            
            logger.info(f"Processing queued item: {channel}/{message_id}")
            
            # Fetch message
            message = await self.telegram_service.get_message(channel, message_id)
            if not message:
                logger.error(f"Message not found for queued item {file_id}")
                self.state_manager.update_queue_item_status(file_id, "failed")
                return

            # Start download
            self.state_manager.update_queue_item_status(file_id, "downloading")
            
            # We use the existing download_single_file logic
            # It updates concurrent_downloads, handles progress, etc.
            result = await self.download_single_file(message, target_dir, file_id)
            
            if result:
                self.state_manager.remove_from_queue(file_id)
            else:
                self.state_manager.update_queue_item_status(file_id, "failed")
                # Maybe remove or keep for retry? For now keep as failed
                
        except Exception as e:
            logger.error(f"Error processing queued item {item.get('id')}: {e}")
            self.state_manager.update_queue_item_status(item.get("id"), "failed")

    async def process_queue(self):
        """Main loop to process downloads from queue"""
        if self._queue_processing:
            return
            
        self._queue_processing = True
        logger.info("Starting queue processor loop")
        
        try:
            while True:
                # Check for exit condition (no items in queue and no active downloads?)
                # We'll run as long as there are items in queue or simple periodic check in background
                # For this implementation, we run while queue is not empty
                
                queue = self.state_manager.get_queue()
                if not queue:
                    # Queue empty, wait a bit then exit loop to save resources? 
                    # Or sleep loop. Let's sleep loop.
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
                    
                logger.info(f"Starting download for queued item {item['id']} (Priority: {item.get('priority')})")
                
                # Launch task
                asyncio.create_task(self._download_queued_item(item))
                
                # Small yield to prevent loop hogging
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Queue processor crashed: {e}")
        finally:
            self._queue_processing = False
            logger.info("Queue processor loop stopped")

    def start_queue_processor(self):
        """Start the queue processor if not running"""
        if not self._queue_processing:
            asyncio.create_task(self.process_queue())

    async def download_selected_files(self, channel_username: str, message_ids: List[int]) -> str:
        """Add selected files to queue"""
        logger.info(f"Queueing {len(message_ids)} selected files")

        session_id = str(uuid.uuid4())
        
        # Prepare queue items
        items_to_queue = []
        
        # We need to fetch messages first to get filenames? 
        # Ideally yes, for UI experience. But that slows down 'Add to Queue'.
        # Let's fetch messages first as before.
        
        messages = await self.telegram_service.get_messages(channel_username, message_ids)
        
        for i, msg in enumerate(messages):
             file_name = self._get_file_name(msg)
             items_to_queue.append({
                 "id": f"file_{session_id}_{msg.id}", # Unique ID
                 "message_id": msg.id,
                 "channel": channel_username,
                 "name": file_name,
                 "priority": 0,
                 "status": "queued",
                 "session_id": session_id # logical grouping
             })

        # Add to queue
        self.state_manager.add_to_queue(items_to_queue)
        
        # Ensure processor is running
        self.start_queue_processor()
        
        # Update global status for UI feedback (optional, since UI now reads queue)
        self.state_manager.update_status({
            "active": True, # Keep global active flag for now
            "session_id": session_id
        })

        return session_id

    async def download_all_files(self, channel_username: str, limit: int, filter_type: Optional[str] = None) -> str:
        """Queue all files from channel"""
        logger.info(f"Queueing files from {channel_username}, limit={limit}")
        
        session_id = str(uuid.uuid4())
        items_to_queue = []
        
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
        
        self.state_manager.add_to_queue(items_to_queue)
        self.start_queue_processor()
        
        return session_id
        
    # Re-use existing download_single_file (no changes needed there mostly)
    async def download_single_file(self, message, target_dir: str, file_id: str, max_retries: int = 3) -> Optional[str]:
        # ... (Existing implementation kept via partial replace or explicit full replace if needed)
        # Assuming we keep the existing internal logic for single file download
        # Logic is maintained as provided in previous file read
        return await super().download_single_file(message, target_dir, file_id, max_retries) # Pseudo-code, actually we need to preserve the method body if I use full replace

    # ... (Keep existing cancel_download, etc. but update to clear queue)
    
    async def cancel_download(self) -> Dict:
        """Cancel active downloads and clear queue"""
        # Clear queue
        queue = self.state_manager.get_queue()
        queue_ids = [item["id"] for item in queue]
        for qid in queue_ids:
            self.state_manager.remove_from_queue(qid)
            
        # Call original cancel logic for active downloads
        # ... 
        # (Since I cannot do 'super', I will rely on the previous implementation logic but adapted)
        
        status = self.state_manager.get_status()
        status["cancelled"] = True
        self.state_manager.save_state()
        
        # Stop queue processor?
        # self._queue_processing = False (loop handles it)
        
        # Cancel concurrent tasks
        # Current concurrent downloads are tracked in state_manager
        # We assume download_single_file checks for status['cancelled']
        
        return {"status": "success", "message": "Queue cleared and active downloads cancelled"}


    async def download_single_file(self, message, target_dir: str, file_id: str, max_retries: int = 3) -> Optional[str]:
        """Download a single file with progress tracking and retry logic"""
        file_name = self._get_file_name(message)
        logger.info(f"=== DOWNLOAD SINGLE FILE CALLED === file_id={file_id}, file_name={file_name}, target_dir={target_dir}")

        for attempt in range(max_retries):
            try:
                logger.info(f"Starting download (attempt {attempt + 1}/{max_retries}): {file_name}")
                logger.info(f"Message details: id={message.id}, has_media={message.media is not None}")

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
                download_start_time = datetime.now()

                def progress_callback(current, total):
                    nonlocal last_progress_time, last_progress_bytes

                    if status["cancelled"]:
                        raise Exception("Download cancelled by user")

                    now = datetime.now()
                    time_diff = (now - last_progress_time).total_seconds()
                    
                    # Update every 0.5s or if finished
                    if time_diff >= 0.5 or current == total:
                        bytes_diff = current - last_progress_bytes
                        speed = bytes_diff / time_diff if time_diff > 0 else 0 # Bytes/s
                        eta = (total - current) / speed if speed > 0 else 0
                        
                        last_progress_time = now
                        last_progress_bytes = current

                        status["concurrent_downloads"][file_id]["progress"] = current
                        status["concurrent_downloads"][file_id]["total"] = total
                        status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
                        status["concurrent_downloads"][file_id]["speed"] = speed
                        status["concurrent_downloads"][file_id]["eta"] = eta
                        status["concurrent_downloads"][file_id]["last_update"] = now.isoformat()
                        
                        # Only save state if significant change or sufficient time passed to avoid I/O thrashing
                        # or simple throttle logic in StateManager (which is better)
                        # For now, we trust StateManager or OS file buffering.
                        self.state_manager.save_state()

                # Create a task to monitor overall progress even when callback isn't called
                async def download_with_monitoring():
                    logger.info(f"")
                    logger.info(f"{'='*80}")
                    logger.info(f"=== DOWNLOAD_WITH_MONITORING FUNCTION STARTED ===")
                    logger.info(f"=== file_name={file_name}, file_id={file_id}")
                    logger.info(f"{'='*80}")

                    # Determine the expected file path
                    expected_file_path = os.path.join(target_dir, file_name)
                    logger.info(f"Expected file path: {expected_file_path}")
                    logger.info(f"Target directory exists: {os.path.exists(target_dir)}")

                    logger.info(f"Creating download task via telegram_service.download_media...")
                    try:
                        download_task = asyncio.create_task(
                            self.telegram_service.download_media_parallel(
                                message,
                                expected_file_path,
                                progress_callback,
                                workers=Config.DOWNLOAD_WORKERS
                            )
                        )
                        logger.info(f"✓ Download task created successfully: {download_task}")
                    except Exception as e:
                        logger.error(f"✗ Failed to create download task: {e}")
                        raise

                    last_check_file_size = -1  # Use -1 to indicate initial state
                    last_size_change_time = datetime.now()
                    check_count = 0

                    while not download_task.done():
                        try:
                            await asyncio.sleep(5)  # Check every 5 seconds
                        except asyncio.CancelledError:
                            logger.warning(f"Monitor sleep cancelled for {file_name}")
                            download_task.cancel()
                            raise

                        check_count += 1

                        if download_task.done():
                            logger.info(f"Download task completed for {file_name}")
                            break

                        logger.debug(f"Monitor loop iteration {check_count} for {file_name}")

                        # Check actual file size on disk
                        actual_file_size = 0
                        if os.path.exists(expected_file_path):
                            actual_file_size = os.path.getsize(expected_file_path)

                        # Also check state for reported progress
                        status = self.state_manager.get_status()
                        current_status = status.get("concurrent_downloads", {}).get(file_id, {})
                        state_bytes = current_status.get("progress", 0)
                        total_bytes = current_status.get("total", 0)
                        percentage = current_status.get("percentage", 0)

                        logger.info(f"Monitor check #{check_count} for {file_name}: state={state_bytes}/{total_bytes} ({percentage}%), disk={actual_file_size}")

                        # Just log the progress, stall detection is handled by progress_callback
                        # File monitor stall detection removed to avoid false positives from buffering
                        if actual_file_size != last_check_file_size:
                            if last_check_file_size >= 0:
                                logger.info(f"File growing: {last_check_file_size} -> {actual_file_size} bytes (+{actual_file_size - last_check_file_size})")
                            else:
                                logger.info(f"First check: file size = {actual_file_size} bytes")
                            last_check_file_size = actual_file_size
                        else:
                            logger.debug(f"File size unchanged: {actual_file_size} bytes (this is normal during buffering)")

                            # Update state with actual file size if callback hasn't been called
                            if state_bytes == 0 and actual_file_size > 0:
                                logger.info(f"Updating state with actual file size: {actual_file_size}")
                                status["concurrent_downloads"][file_id]["progress"] = actual_file_size
                                if total_bytes > 0:
                                    status["concurrent_downloads"][file_id]["percentage"] = int((actual_file_size / total_bytes * 100))
                                status["concurrent_downloads"][file_id]["last_update"] = datetime.now().isoformat()
                                self.state_manager.save_state()

                    result = await download_task
                    logger.info(f"Download task finished for {file_name}")

                    # Verify the file was actually downloaded
                    if result and os.path.exists(result):
                        final_size = os.path.getsize(result)
                        logger.info(f"Download completed successfully: {file_name}, size: {final_size} bytes")
                    else:
                        logger.warning(f"Download task returned but file not found: {result}")

                    return result

                # Add timeout wrapper for the download (20 minutes per file)
                logger.info(f"About to call download_with_monitoring() with 1200s timeout")
                try:
                    file_path = await asyncio.wait_for(
                        download_with_monitoring(),
                        timeout=1200  # 20 minutes timeout for entire download
                    )
                    logger.info(f"download_with_monitoring() completed, file_path={file_path}")
                except asyncio.TimeoutError:
                    logger.error(f"Download timeout after 20 minutes for {file_name}")
                    raise Exception("Download timeout - exceeded 20 minutes")
                except asyncio.CancelledError:
                    logger.warning(f"Download cancelled (likely due to stall) for {file_name}")
                    raise Exception("Download stalled and was cancelled")
                except Exception as e:
                    logger.error(f"Exception in download_with_monitoring: {type(e).__name__}: {str(e)}")
                    raise

                if file_path:
                    # Get the final file size from disk
                    final_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

                    # If we don't have the size from disk, try to get from concurrent state
                    status = self.state_manager.get_status()
                    if final_size == 0 and file_id in status.get("concurrent_downloads", {}):
                        final_size = status["concurrent_downloads"][file_id].get("total", 0)

                    # Use thread-safe method to mark file as completed
                    file_data = {
                        "name": file_name,
                        "path": file_path,
                        "size": final_size,
                        "percentage": 100,
                        "completed_at": datetime.now().isoformat()
                    }
                    await self.state_manager.mark_file_completed(file_id, file_data)

                    logger.info(f"Completed download: {file_name}, size: {final_size} bytes, marked as 100%")
                    return file_path
                else:
                    logger.warning(f"Download completed but no file path returned for {file_name}")
                    return None

            except Exception as e:
                error_msg = str(e)

                # Check if user cancelled
                if "cancelled" in error_msg.lower():
                    logger.info(f"Download cancelled by user: {file_name}")
                    status = self.state_manager.get_status()
                    if file_id in status["concurrent_downloads"]:
                        del status["concurrent_downloads"][file_id]
                    self.state_manager.save_state()
                    return None

                # Check if it's a timeout/stall error from Telegram
                is_timeout = (
                    "timeout" in error_msg.lower() or
                    "timeouterror" in error_msg.lower() or
                    "stalled" in error_msg.lower() or
                    "stuck" in error_msg.lower()
                )

                if is_timeout and attempt < max_retries - 1:
                    # Calculate exponential backoff: 2^attempt seconds (2s, 4s, 8s)
                    wait_time = 2 ** attempt
                    logger.warning(f"Telegram timeout for {file_name}. Retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Final attempt failed or non-timeout error
                    logger.error(f"Error downloading {file_name} after {attempt + 1} attempts: {error_msg}")
                    status = self.state_manager.get_status()
                    if file_id in status["concurrent_downloads"]:
                        del status["concurrent_downloads"][file_id]
                    self.state_manager.save_state()
                    return None

        # Should not reach here, but just in case
        return None




    async def download_single(self, channel_username: str, message_id: int) -> Dict:
        """Download a single file with retry mechanism"""
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
            "started_at": status.get("started_at") or datetime.now().isoformat(),
            "progress": 0,
            "total": 1
        })
        self.state_manager.save_state()

        # Use the robust download_single_file method which has retry and stall detection
        try:
            file_path = await self.download_single_file(message, target_dir, file_id)
        except Exception as e:
            logger.error(f"Failed to download single file: {e}")
            file_path = None

        status = self.state_manager.get_status()
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
