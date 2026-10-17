import logging
from datetime import datetime
from typing import Optional, List, Callable
import math
import os
import asyncio
import aiofiles
from telethon import TelegramClient, utils
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, Channel, User, InputMessagesFilterVideo, InputMessagesFilterDocument, InputMessagesFilterPhotos, InputMessagesFilterMusic
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

        api_filter = None
        if filter_type:
            if filter_type.lower() == 'video':
                api_filter = InputMessagesFilterVideo
            elif filter_type.lower() == 'document':
                api_filter = InputMessagesFilterDocument
            elif filter_type.lower() == 'photo':
                api_filter = InputMessagesFilterPhotos
            elif filter_type.lower() == 'audio':
                api_filter = InputMessagesFilterMusic

        async for message in self.client.iter_messages(channel_username, limit=limit * 2, filter=api_filter):
            if message.media:
                file_info = None

                if isinstance(message.media, MessageMediaDocument):
                    doc = message.media.document
                    file_name = next((attr.file_name for attr in doc.attributes
                                      if hasattr(attr, 'file_name')), f"document_{message.id}")

                    file_info = FileInfo(
                        file_id=message.id,
                        file_name=file_name,
                        file_size=doc.size,
                        file_type="document",
                        date=str(message.date)
                    )

                elif isinstance(message.media, MessageMediaPhoto):
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

    async def download_media_parallel(
        self, 
        message, 
        file_path: str, 
        progress_callback: Optional[Callable[[int, int], None]] = None, 
        workers: int = 4
    ) -> str:
        """Download Telegram media using sparse file pre-allocation and direct os.pwrite range requests."""
        import os
        import math
        import asyncio
        from app.config import Config

        # Determine file size
        file_size = 0
        if isinstance(message.media, MessageMediaDocument):
            file_size = message.media.document.size
        elif isinstance(message.media, MessageMediaPhoto):
            return await self.download_media(message, file_path, progress_callback)
        
        if file_size == 0:
            return await self.download_media(message, file_path, progress_callback)

        # Dynamic worker scaling based on file size thresholds
        if file_size < 20 * 1024 * 1024:
            effective_workers = 1
        elif file_size < 100 * 1024 * 1024:
            effective_workers = min(2, workers)
        elif file_size < 500 * 1024 * 1024:
            effective_workers = min(4, workers)
        else:
            effective_workers = min(workers, Config.DOWNLOAD_WORKERS)

        request_chunk_size = 1024 * 1024  # 1MB aligned chunks
        part_size = math.ceil(file_size / effective_workers)
        # Align part_size to 1MB chunk boundaries
        aligned_part_size = math.ceil(part_size / request_chunk_size) * request_chunk_size

        # Pre-allocate sparse target file using truncate
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        fd = os.open(file_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.ftruncate(fd, file_size)
        except Exception:
            os.close(fd)
            if os.path.exists(file_path):
                os.remove(file_path)
            raise

        downloaded_bytes = 0
        lock = asyncio.Lock()
        loop = asyncio.get_running_loop()
        last_callback_time = [datetime.now()]

        async def worker(worker_id: int, start_byte: int, end_byte: int) -> None:
            nonlocal downloaded_bytes
            current_offset = start_byte

            async for chunk in self.client.iter_download(
                message.media,
                offset=start_byte,
                limit=end_byte - start_byte,
                request_size=request_chunk_size
            ):
                if not chunk:
                    break
                chunk_len = len(chunk)
                
                # Direct seek-free write at current offset using os.pwrite
                await loop.run_in_executor(None, os.pwrite, fd, chunk, current_offset)
                current_offset += chunk_len

                async with lock:
                    downloaded_bytes += chunk_len
                    if progress_callback:
                        now = datetime.now()
                        elapsed = (now - last_callback_time[0]).total_seconds()
                        if elapsed >= Config.DOWNLOAD_REQUEST_DELAY or downloaded_bytes == file_size:
                            progress_callback(downloaded_bytes, file_size)
                            last_callback_time[0] = now

        tasks = []
        for i in range(effective_workers):
            start = i * aligned_part_size
            end = min(start + aligned_part_size, file_size)
            if start >= file_size:
                break
            tasks.append(worker(i, start, end))

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Parallel download failed for {file_path}: {e}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            raise
        finally:
            os.close(fd)

        return file_path
