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
        logger.info(f"DownloadService initialized with StateManager ID: {id(state_manager)}")
        self.active_download_tasks = {}
        self.collection_tasks = set()
        self._queue_processing = False
        self._stop_queue = False 
        self._queue_event = asyncio.Event()

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

            # Download using the robust method - passing session_id and channel
            result = await self.download_single_file(message, target_dir, file_id, session_id=session_id, channel_identifier=channel)
            
            if result:
                # Already removed from queue, just log success
                logger.debug(f"Queued item {file_id} finished successfully")
            else:
                # If it failed, check retry count
                retry_count = item.get("retry_count", 0)
                if retry_count < 3: # MAX_RETRIES = 3
                    logger.warning(f"Download failed for {file_id}. Retrying ({retry_count + 1}/3)...")
                    item["status"] = "queued"
                    item["retry_count"] = retry_count + 1
                    self.state_manager.add_to_queue([item])
                    self.start_queue_processor()
                else:
                    logger.error(f"Download failed for {file_id} after {retry_count} retries.")
                    item["status"] = "failed"
                    self.state_manager.add_to_queue([item])
                    self.start_queue_processor()
                
        except Exception as e:
            # Check for cancellation first - don't retry if manually cancelled
            if isinstance(e, asyncio.CancelledError) or "cancelled" in str(e).lower():
                logger.info(f"Task {file_id} was cancelled, skipping retry")
                return

            logger.error(f"Error processing queued item {file_id}: {e}")
            if file_id:
                # If it crashed, retry if possible
                retry_count = item.get("retry_count", 0)
                if retry_count < 3:
                    item["status"] = "queued"
                    item["retry_count"] = retry_count + 1
                    self.state_manager.add_to_queue([item])
                    self.start_queue_processor()
                else:
                    item["status"] = "failed"
                    item["retry_count"] = retry_count
                    self.state_manager.add_to_queue([item])
                    self.start_queue_processor()
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
                    # Check if we are truly done (no queue, no active tasks)
                    if not self.active_download_tasks and status.get("active"):
                         # Verify consistency with state manager
                         if not status.get("concurrent_downloads") and not status.get("queue"):
                             logger.info("Queue empty and no active downloads. Session complete.")
                             self.state_manager.update_status({"active": False, "started_at": None})
                             break # Stop the processor as the session is done

                    # Queue empty, wait for event or timeout
                    try:
                        await asyncio.wait_for(self._queue_event.wait(), timeout=10)
                        self._queue_event.clear()
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Check concurrency limit using IN-MEMORY tasks for accuracy and to avoid race conditions
                if len(self.active_download_tasks) >= Config.MAX_CONCURRENT_DOWNLOADS:
                    await asyncio.sleep(1)
                    continue

                # Get next item
                item = self.state_manager.get_next_queued_item()
                if not item:
                    # Clear event if no more items to process right now
                    self._queue_event.clear()
                    await asyncio.sleep(1)
                    continue
                    
                logger.info(f"Starting download for queued item {item['id']} ({item.get('name')})")
                
                # Remove from queue immediately as it's been picked up
                self.state_manager.remove_from_queue(item['id'])
                
                # Track task BEFORE any await to prevent race condition
                file_id = item['id']
                task = asyncio.create_task(self._download_queued_item(item))
                self.active_download_tasks[file_id] = task
                
                # Small yield
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Queue processor crashed: {e}")
        finally:
            self._queue_processing = False
            
            # Check if we are truly done (no queue, no active tasks)
            # AND ensure we don't race with a new batch being added
            if not self.active_download_tasks and status.get("active"):
                # Verify consistency with state manager
                current_status = self.state_manager.get_status()
                if not current_status.get("concurrent_downloads") and not current_status.get("queue"):
                    # Add a double-check delay
                    await asyncio.sleep(0.5)
                    final_check = self.state_manager.get_status()
                    if not final_check.get("queue") and not final_check.get("concurrent_downloads"):
                        logger.info("Queue empty and no active downloads for >0.5s. Session complete.")
                        self.state_manager.update_status({"active": False, "started_at": None})
            
            logger.info("Queue processor loop stopped")

    def start_queue_processor(self):
        """Start the queue processor if not running"""
        self._queue_event.set()
        if not self._queue_processing:
            self._stop_queue = False
            asyncio.create_task(self.process_queue())

    def _reset_session_state(self, channel_username: str, total_count: int, session_id: str):
        """Initialize state for a new download session"""
        # Cancel any existing active tasks BEFORE resetting state
        # This prevents the "Session changed" error when old tasks try to update progress
        for fid, task in list(self.active_download_tasks.items()):
            logger.info(f"Cancelling orphaned task {fid} from previous session")
            task.cancel()
        self.active_download_tasks.clear()
        
        # Clear ANY existing queue items when starting a new session
        # This prevents orphaned items from previous sessions/errors from blocking or joining the new session
        self.state_manager.download_status["queue"] = []
        
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
        
        # Check if there's an active session we can add to
        status = self.state_manager.get_status()
        existing_active = status.get("active") and not status.get("cancelled")
        
        if existing_active:
            # Reuse existing session - just add to the queue
            session_id = status.get("session_id")
            current_total = status.get("total", 0)
            self.state_manager.update_status({
                "total": current_total + len(message_ids),
                "active": True  # FORCE active=True when adding new files
            })
            logger.info(f"Adding {len(message_ids)} files to existing session {session_id}")
        else:
            # Start new session
            session_id = str(uuid.uuid4())
            self._reset_session_state(channel_username, len(message_ids), session_id)
        
        # Start processor immediately so UI shows "active" or "initializing" state
        self.start_queue_processor()
        
        async def collection_task():
            try:
                # Use bulk fetch for efficiency
                message_list = await self.telegram_service.get_messages(channel_username, message_ids)
                items_to_queue = []
                skipped_count = 0
                
                for msg in message_list:
                    if self.state_manager.is_file_active_or_queued(channel_username, msg.id):
                        logger.info(f"Skipping duplicate file: {self._get_file_name(msg)} (ID: {msg.id})")
                        skipped_count += 1
                        continue

                    file_name = self._get_file_name(msg)
                    items_to_queue.append({
                        "id": f"file_{session_id}_{msg.id}",
                        "message_id": msg.id,
                        "channel": channel_username,
                        "name": file_name,
                        "priority": 0,
                        "status": "queued",
                        "session_id": session_id,
                        "retry_count": 0
                    })

                self.state_manager.add_to_queue(items_to_queue)
                
                # Adjust total if some were skipped (duplicates)
                if skipped_count > 0:
                    current_total = self.state_manager.get_status().get("total", 0)
                    self.state_manager.update_status({"total": current_total - skipped_count})
                    
                self.start_queue_processor()
                logger.info(f"Successfully queued {len(items_to_queue)} selected files (skipped {skipped_count} duplicates)")
            except Exception as e:
                logger.error(f"Background collection task for selected files failed: {e}")

        # Start background task and track it
        task = asyncio.create_task(collection_task())
        self.collection_tasks.add(task)
        task.add_done_callback(self.collection_tasks.discard)
        
        return session_id

    async def download_all_files(self, channel_username: str, limit: int, filter_type: Optional[str] = None) -> str:
        """Queue all files from channel in background"""
        logger.info(f"Preparing to queue files from {channel_username}, limit={limit}")
        session_id = str(uuid.uuid4())
        
        # Initialize state with 0 total (will be updated during collection)
        self._reset_session_state(channel_username, 0, session_id)
        
        # Start processor immediately so UI shows "active" state while scanning
        self.start_queue_processor()
        
        async def collection_task():
            try:
                count = 0
                chunk_size = 5 # Start with very small chunk for immediate feedback
                current_chunk = []
                
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
                                logger.debug(f"Skipping duplicate file in scan: {self._get_file_name(message)}")
                                continue

                            file_name = self._get_file_name(message)
                            current_chunk.append({
                                 "id": f"file_{session_id}_{message.id}",
                                 "message_id": message.id,
                                 "channel": channel_username,
                                 "name": file_name,
                                 "priority": 0,
                                 "status": "queued",
                                 "session_id": session_id,
                                 "retry_count": 0
                            })
                            count += 1
                            
                            if len(current_chunk) >= chunk_size:
                                logger.info(f"Queuing next {len(current_chunk)} items from {channel_username} scan...")
                                self.state_manager.add_to_queue(current_chunk)
                                current_chunk = []
                                # Update total incrementally
                                self.state_manager.update_status({"total": count})
                                self.start_queue_processor()
                                
                                # Ramp up chunk size after first immediate batch
                                if chunk_size < 50:
                                    chunk_size = 50
                
                # Add final chunk
                if current_chunk:
                    logger.info(f"Queuing final {len(current_chunk)} items from {channel_username} scan.")
                    self.state_manager.add_to_queue(current_chunk)
                    self.state_manager.update_status({"total": count})
                    self.start_queue_processor()
                
                if count == 0:
                    logger.info(f"No matchable files found in {channel_username}")
                    self.state_manager.update_status({"active": False})
                else:
                    logger.info(f"Scan complete for {channel_username}. Total queued: {count}")
                    
            except asyncio.CancelledError:
                logger.info(f"Collection task for {channel_username} was cancelled")
            except Exception as e:
                logger.error(f"Background collection task for all files failed: {e}")
                self.state_manager.update_status({"active": False})

        # Start background task and track it
        task = asyncio.create_task(collection_task())
        self.collection_tasks.add(task)
        task.add_done_callback(self.collection_tasks.discard)
        
        return session_id

    async def download_single_file(self, message, target_dir: str, file_id: str, max_retries: int = 3, session_id: Optional[str] = None, channel_identifier: Optional[str] = None) -> Optional[str]:
        """Download a single file with progress tracking and retry logic"""
        file_name = self._get_file_name(message)
        logger.info(f"Starting download: {file_name} (ID: {file_id})")

        for attempt in range(max_retries):
            try:
                status = self.state_manager.get_status()
                logger.info(f"Pre-update concurrent keys: {list(status.get('concurrent_downloads', {}).keys())}")
                status["concurrent_downloads"][file_id] = {
                    "id": file_id,
                    "name": file_name,
                    "channel": channel_identifier or (message.peer_id.channel_id if hasattr(message.peer_id, 'channel_id') else str(message.peer_id)),
                    "message_id": message.id,
                    "progress": 0,
                    "total": 0,
                    "percentage": 0,
                    "retry_attempt": attempt + 1 if attempt > 0 else None
                }
                logger.info(f"Updated concurrent_downloads for {file_id}. StateManager ID: {id(self.state_manager)}")
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
                
                # File overwrite protection: append (1), (2), etc. if file exists
                if os.path.exists(expected_file_path):
                    base, ext = os.path.splitext(file_name)
                    counter = 1
                    while os.path.exists(os.path.join(target_dir, f"{base} ({counter}){ext}")):
                        counter += 1
                    file_name = f"{base} ({counter}){ext}"
                    expected_file_path = os.path.join(target_dir, file_name)
                    logger.info(f"File already exists, renaming to: {file_name}")
                
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
                        "completed_at": datetime.now().isoformat(),
                        "session_id": session_id,
                        "message_id": message.id,
                        "channel": channel_identifier or (message.peer_id.channel_id if hasattr(message.peer_id, 'channel_id') else str(message.peer_id))
                    }
                    # mark_file_completed handles adding to history, removing from concurrent, 
                    # updating progress counter, and saving state.
                    await self.state_manager.mark_file_completed(file_id, file_data)
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

        # Cancel all background collection tasks (scans)
        if self.collection_tasks:
            logger.info(f"Cancelling {len(self.collection_tasks)} background collection tasks")
            for task in list(self.collection_tasks):
                task.cancel()
            self.collection_tasks.clear()
        
        # Clear queue contents
        queue = self.state_manager.get_queue()
        queue_ids = [item["id"] for item in queue]
        for qid in queue_ids:
            self.state_manager.remove_from_queue(qid)
        
        # Get current status to move active items to cancelled_files
        status = self.state_manager.get_status()
        concurrent = status.get("concurrent_downloads", {})
        
        for fid, data in list(concurrent.items()):
            # Just remove them, don't store in cancelled_files
            pass
        
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

        if status.get("active") and not status.get("cancelled") and self._queue_processing:
            return {"status": "info", "message": "Download already active"}

        progress = status.get("progress", 0)
        total = status.get("total", 0)

        if total > 0 and progress >= total:
            logger.info(f"Session {status.get('session_id')} already finished ({progress}/{total}). Marking as inactive.")
            self.state_manager.update_status({"active": False})
            return {"status": "info", "message": "Download session already completed"}

        channel = status.get("channel")
        total = status.get("total", 0)
        # Reuse existing session_id if possible when resuming
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
                # We also check if the file ID itself contains the message ID
                history = status.get("completed_downloads", {})
                for fid, info in history.items():
                    try:
                        # Extract from fid: single_MESSAGEID or file_SESSIONID_MESSAGEID
                        mid = int(fid.split("_")[-1])
                        completed_ids.add(mid)
                    except: 
                        # Fallback: check if stored in metadata
                        if "message_id" in info:
                            completed_ids.add(info["message_id"])

                logger.info(f"Resuming session {session_id}. Channel: {channel}. Found {len(completed_ids)} completed items in history.")
                
                messages_to_queue = []
                # Fetch more than 'total' just in case some were added
                fetch_limit = max(total, 50) 
                
                logger.info(f"Scanning channel for up to {fetch_limit} messages to find {total - len(completed_ids)} remaining files...")
                
                async for message in await self.telegram_service.iter_messages(channel, fetch_limit):
                    if message.media:
                        if message.id in completed_ids:
                            logger.debug(f"Skipping already completed message {message.id}")
                            continue
                            
                        messages_to_queue.append({
                            "id": f"file_{session_id}_{message.id}",
                            "message_id": message.id,
                            "channel": channel,
                            "name": self._get_file_name(message),
                            "priority": 1, # Higher priority for resumed items
                            "status": "queued",
                            "session_id": session_id,
                            "retry_count": 0
                        })
                        
                        # Stop if we found enough remaining files to satisfy the original 'total'
                        # (But usually we want to resume whatever is missing from the range)
                        if len(messages_to_queue) >= (total - len(completed_ids)) and len(messages_to_queue) > 0:
                            if total > 0: # Only cap if total was specifically set
                                break

                if not messages_to_queue:
                    logger.info("Nothing left to resume")
                    status["active"] = False
                    self.state_manager.save_state()
                    return

                logger.info(f"Re-queueing {len(messages_to_queue)} missing files for resumption")
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
        
        # PROACTIVE RESET: Clear cancelled flag immediately so UI updates
        # This must happen before any awaits to ensure immediate state persistence
        status = self.state_manager.get_status()
        
        # Use existing session if it's active and not cancelled
        is_new_session = not status.get("active") or status.get("cancelled")
        # Ensure we always have a session_id
        session_id = (status.get("session_id") if not is_new_session else None) or str(uuid.uuid4())
        
        if is_new_session:
            # Start fresh session or override cancelled one
            self._reset_session_state(channel_username, 1, session_id)
        else:
            # Adding to existing active session -> just increment total
            if status.get("cancelled"):
                 self.state_manager.update_status({"cancelled": False})
            
            # Update status with current session if somehow missing
            if not status.get("session_id"):
                 self.state_manager.update_status({"session_id": session_id})
        
        self.state_manager.save_state()

        # IMMEDIATE QUEUEING: Add placeholder to queue so UI updates instantly
        file_id = f"single_{message_id}"
        
        if not self.state_manager.is_file_active_or_queued(channel_username, message_id):
            if not is_new_session:
                current_total = self.state_manager.get_status().get("total", 0)
                self.state_manager.update_status({"total": current_total + 1})
            
            # Use placeholder name initially
            item = {
                "id": file_id,
                "message_id": message_id,
                "channel": channel_username,
                "name": f"Fetching metadata for message {message_id}...",
                "priority": 2, 
                "status": "queued",
                "session_id": session_id,
                "retry_count": 0
            }
            
            self.state_manager.add_to_queue([item])
            self._queue_event.set()
            self.start_queue_processor()
            
            # Now fetch real metadata in background
            # We don't await this so the response returns to user instantly
            asyncio.create_task(self._update_placeholder_metadata(channel_username, message_id, file_id))
        else:
            logger.info(f"File {message_id} is already active or in queue")

    async def _update_placeholder_metadata(self, channel_username: str, message_id: int, file_id: str):
        """Fetch real metadata for a placeholder item in queue"""
        try:
            message = await self.telegram_service.get_message(channel_username, message_id)
            if not message:
                logger.error(f"Message {message_id} not found during metadata update")
                self.state_manager.update_queue_item_status(file_id, "failed")
                return
                
            file_name = self._get_file_name(message)
            
            # Update item in queue
            status = self.state_manager.get_status()
            queue = status.get("queue", [])
            updated = False
            for item in queue:
                if item.get("id") == file_id:
                    item["name"] = file_name
                    updated = True
                    break
            
            if updated:
                self.state_manager.save_state()
                logger.info(f"Updated metadata for {file_id}: {file_name}")
                
        except Exception as e:
            logger.error(f"Error updating placeholder metadata: {e}")

    async def cancel_individual_download(self, file_id: str) -> Dict:
        """Cancel an individual active download"""
        logger.info(f"Cancel individual download requested for: {file_id}")
        
        # Check if task is active
        if file_id in self.active_download_tasks:
            logger.info(f"Cancelling active task for {file_id}")
            self.active_download_tasks[file_id].cancel()
            # Task cleanup in _download_queued_item will remove it from self.active_download_tasks
        
        # Move from concurrent_downloads to cancelled_files
        status = self.state_manager.get_status()
        concurrent = status.get("concurrent_downloads", {})
        
        if file_id in concurrent:
            data = concurrent[file_id]
            # Just remove it
            del status["concurrent_downloads"][file_id]
            
            # Decrement session total so UI doesn't wait for "phantom" files
            if status.get("total", 0) > 0:
                status["total"] -= 1
            
            # Update state
            self.state_manager.save_state()
            
            # Check if session is now complete
            if not self.active_download_tasks and not self.state_manager.get_queue():
                logger.info("Last active task cancelled and queue empty. Marking session inactive.")
                self.state_manager.update_status({"active": False, "started_at": None})
                
            return {"status": "success", "message": f"Download for {data.get('name')} cancelled."}
        
        # If it was just in the queue (not yet downloading)
        queue = self.state_manager.get_queue()
        for item in queue:
            if item.get("id") == file_id:
                self.state_manager.remove_from_queue(file_id)
                
                if status.get("total", 0) > 0:
                    status["total"] -= 1
                    self.state_manager.save_state()
                
                # Check if session is now complete
                if not self.active_download_tasks and not self.state_manager.get_queue():
                    logger.info("Last queued item removed and no active tasks. Marking session inactive.")
                    self.state_manager.update_status({"active": False, "started_at": None})
                    
                return {"status": "success", "message": f"Item {item.get('name')} removed from queue."}
        
        return {"status": "error", "message": "Download not found or already finished."}

    def cleanup_tasks(self):
        """Cleanup active download tasks (not fully needed with queue but good for shutdown)"""
        self._stop_queue = True
