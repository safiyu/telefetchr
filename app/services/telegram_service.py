import logging
from datetime import datetime
from typing import Optional, List
import math
import os
import asyncio
import aiofiles
from telethon import TelegramClient, utils
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, Channel, User
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

from app.config import Config
from app.models.schemas import FileInfo
from app.utils.text_utils import tidy_display_name

logger = logging.getLogger(__name__)


class TelegramService:
    """Service for managing Telegram client operations"""

    def __init__(self):
        self.client: Optional[TelegramClient] = None

    async def connect(self):
        """Connect to Telegram with optimized settings"""
        try:
            self.client = TelegramClient(
                Config.SESSION_FILE,
                Config.API_ID,
                Config.API_HASH,
                # Optimizations for better reliability
                timeout=60,  # Increased timeout for requests (was 30s)
                request_retries=10,  # More retries for failed requests (was 5)
                connection_retries=10,  # More connection retries (was 5)
                retry_delay=3,  # Delay between retries (was 2s)
                auto_reconnect=True,  # Auto-reconnect on disconnect
                sequential_updates=True  # Process updates sequentially
            )
            await self.client.connect()

            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                logger.info(f"Already logged in as: {me.first_name} (@{me.username})")
            else:
                logger.info("Not authorized. User needs to login.")

        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            raise

    async def disconnect(self):
        """Disconnect from Telegram"""
        if self.client and self.client.is_connected():
            await self.client.disconnect()

    async def request_code(self):
        """Request verification code"""
        if not self.client:
            self.client = TelegramClient(
                Config.SESSION_FILE,
                Config.API_ID,
                Config.API_HASH,
                timeout=60,
                request_retries=10,
                connection_retries=10,
                retry_delay=3,
                auto_reconnect=True,
                sequential_updates=True
            )
            await self.client.connect()

        await self.client.send_code_request(Config.PHONE_NUMBER)
        return f"Verification code sent to {Config.PHONE_NUMBER}"

    async def verify_code(self, code: str):
        """Verify login code"""
        if not self.client:
            raise ValueError("Login not started. Call request_code first.")

        me = await self.client.sign_in(Config.PHONE_NUMBER, code)
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name
        }

    async def verify_password(self, password: str):
        """Verify 2FA password"""
        if not self.client:
            raise ValueError("Login not started.")

        me = await self.client.sign_in(password=password)
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name
        }

    async def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.client is not None and self.client.is_connected()

    async def is_authorized(self) -> bool:
        """Check if user is authorized"""
        if not self.client:
            return False
        return await self.client.is_user_authorized()

    async def get_me(self):
        """Get current user info"""
        if not self.client:
            return None
        return await self.client.get_me()

    async def get_channels(self):
        """Get list of channels and bots"""
        bots = []
        channels = []

        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, User) and entity.bot:
                bots.append({
                    "name": tidy_display_name(entity.first_name),
                    "id": entity.id,
                    "username": f"@{entity.username}" if entity.username else None
                })
            elif isinstance(entity, Channel) and entity.username:
                channels.append({
                    "name": tidy_display_name(dialog.name),
                    "id": entity.id,
                    "username": f"@{entity.username}"
                })

        return bots + channels

    async def list_files(
        self,
        channel_username: str,
        limit: int = 10,
        filter_type: Optional[str] = None,
        search_query: Optional[str] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        file_extension: Optional[str] = None
    ) -> List[FileInfo]:
        """List files from a channel with search and filter options"""
        files = []

        date_from_dt = None
        date_to_dt = None
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except:
                logger.warning(f"Invalid date_from format: {date_from}")

        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except:
                logger.warning(f"Invalid date_to format: {date_to}")

        async for message in self.client.iter_messages(channel_username, limit=limit * 2):
            if message.media:
                file_info = None

                if isinstance(message.media, MessageMediaDocument):
                    doc = message.media.document
                    file_name = next((attr.file_name for attr in doc.attributes
                                      if hasattr(attr, 'file_name')), f"document_{message.id}")

                    if not filter_type or filter_type == 'document':
                        file_info = FileInfo(
                            file_id=message.id,
                            file_name=file_name,
                            file_size=doc.size,
                            file_type="document",
                            date=str(message.date)
                        )

                elif isinstance(message.media, MessageMediaPhoto):
                    if not filter_type or filter_type == 'photo':
                        file_info = FileInfo(
                            file_id=message.id,
                            file_name=f"photo_{message.id}.jpg",
                            file_size=0,
                            file_type="photo",
                            date=str(message.date)
                        )

                if file_info:
                    if search_query and search_query.lower() not in file_info.file_name.lower():
                        continue

                    if min_size is not None and file_info.file_size < min_size:
                        continue

                    if max_size is not None and file_info.file_size > max_size:
                        continue

                    if date_from_dt and message.date.replace(tzinfo=None) < date_from_dt.replace(tzinfo=None):
                        continue

                    if date_to_dt and message.date.replace(tzinfo=None) > date_to_dt.replace(tzinfo=None):
                        continue

                    if file_extension:
                        ext = file_info.file_name.split('.')[-1].lower() if '.' in file_info.file_name else ''
                        if ext != file_extension.lower().lstrip('.'):
                            continue

                    files.append(file_info)

                    if len(files) >= limit:
                        break

        return files

    async def get_messages(self, channel_username: str, message_ids: List[int]):
        """Get specific messages by IDs in bulk"""
        try:
            # Telethon natively supports fetching a list of IDs
            messages = await self.client.get_messages(channel_username, ids=message_ids)
            
            # Message collection can return None for individual IDs if they don't exist
            # Filter out None and those without media
            valid_messages = [m for m in messages if m and m.media]
            
            if len(valid_messages) < len(message_ids):
                logger.warning(f"Only {len(valid_messages)}/{len(message_ids)} valid media messages found")
                
            return valid_messages
        except Exception as e:
            logger.error(f"Error fetching bulk messages: {e}")
            return []

    async def get_message(self, channel_username: str, message_id: int):
        """Get a single message"""
        return await self.client.get_messages(channel_username, ids=message_id)

    async def iter_messages(self, channel_username: str, limit: int):
        """Iterate through channel messages"""
        return self.client.iter_messages(channel_username, limit=limit)

    async def download_media(self, message, file_path: str, progress_callback=None):
        """Download media from message with optimized settings"""
        import asyncio

        # Wrap progress callback to add small delay between updates
        last_callback_time = [datetime.now()]

        def throttled_progress_callback(current, total):
            if progress_callback:
                # Only call callback if enough time has passed (prevents flooding)
                now = datetime.now()
                elapsed = (now - last_callback_time[0]).total_seconds()
                if elapsed >= Config.DOWNLOAD_REQUEST_DELAY or current == total:
                    progress_callback(current, total)
                    last_callback_time[0] = now

        # Download with progress callback only (chunk size is handled by Telethon internally)
        return await self.client.download_media(
            message,
            file=file_path,
            progress_callback=throttled_progress_callback if progress_callback else None
        )

    async def download_media_parallel(self, message, file_path: str, progress_callback=None, workers: int = 4):
        """Download media using parallel workers (segmented download)"""
        # Determine file size
        file_size = 0
        if isinstance(message.media, MessageMediaDocument):
            file_size = message.media.document.size
        elif isinstance(message.media, MessageMediaPhoto):
            # Photos are usually small, use standard download
            return await self.download_media(message, file_path, progress_callback)
        
        if file_size == 0:
             return await self.download_media(message, file_path, progress_callback)

        chunk_size = math.ceil(file_size / workers)
        downloaded_bytes = 0
        lock = asyncio.Lock()
        
        last_callback_time = [datetime.now()]
        
        async def worker(worker_id: int, start_byte: int, end_byte: int):
            nonlocal downloaded_bytes
            
            # Using temporary part file for this worker
            part_file_path = f"{file_path}.part{worker_id}"
            current_offset = start_byte
            
            try:
                # Use configured chunk size (default 4MB) for faster downloads
                request_chunk_size = Config.DOWNLOAD_CHUNK_SIZE

                async for chunk in self.client.iter_download(
                    message.media,
                    offset=start_byte,
                    limit=end_byte - start_byte,
                    chunk_size=None,
                    request_size=request_chunk_size
                ):
                    if not chunk:
                        break
                        
                    # Write sequentially to part file
                    async with aiofiles.open(part_file_path, 'ab') as f:
                        await f.write(chunk)
                    
                    chunk_len = len(chunk)
                    current_offset += chunk_len
                    
                    async with lock:
                        downloaded_bytes += chunk_len
                        # Progress callback
                        if progress_callback:
                            now = datetime.now()
                            elapsed = (now - last_callback_time[0]).total_seconds()
                            if elapsed >= Config.DOWNLOAD_REQUEST_DELAY or downloaded_bytes == file_size:
                                progress_callback(downloaded_bytes, file_size)
                                last_callback_time[0] = now
                                
                    if current_offset >= end_byte:
                        break

            except Exception as e:
                logger.error(f"Worker {worker_id} failed: {e}")
                # Cleanup part file on failure
                if os.path.exists(part_file_path):
                    try:
                        os.remove(part_file_path)
                    except:
                        pass
                raise

        tasks = []
        for i in range(workers):
            start = i * chunk_size
            end = min((i + 1) * chunk_size, file_size)
            if start >= end:
                break
            # Clear any existing part file first
            part_path = f"{file_path}.part{i}"
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except:
                    pass
            tasks.append(worker(i, start, end))

        try:
            await asyncio.gather(*tasks)
            # Merge parts after successful download
            await self.merge_parts(file_path, len(tasks))
            
        except Exception as e:
            logger.error(f"Parallel download failed: {e}")
            # Cleanup all parts
            for i in range(workers):
                part_path = f"{file_path}.part{i}"
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except:
                        pass
            # Cleanup target file if it exists (incomplete merge)
            if os.path.exists(file_path):
                os.remove(file_path)
            raise

        return file_path

    async def merge_parts(self, file_path: str, num_parts: int):
        """Merge download parts into final file"""
        logger.info(f"Merging {num_parts} parts into {file_path}")
        
        # Use aiofiles for merging to allow event loop to run (though it's IO bound)
        async with aiofiles.open(file_path, 'wb') as outfile:
            for i in range(num_parts):
                part_path = f"{file_path}.part{i}"
                if os.path.exists(part_path):
                    async with aiofiles.open(part_path, 'rb') as infile:
                        while True:
                            chunk = await infile.read(1024 * 1024)  # 1MB copy buffer
                            if not chunk:
                                break
                            await outfile.write(chunk)
                    
                    # Remove part file after merging
                    try:
                        os.remove(part_path)
                    except Exception as e:
                        logger.warning(f"Failed to remove part file {part_path}: {e}")
                else:
                    logger.error(f"Missing part file: {part_path}")
                    raise FileNotFoundError(f"Part file missing: {part_path}")
