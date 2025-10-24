import logging
from typing import Optional, List
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, Channel, User

from app.config import Config
from app.models.schemas import FileInfo

logger = logging.getLogger(__name__)


class TelegramService:
    """Service for managing Telegram client operations"""

    def __init__(self):
        self.client: Optional[TelegramClient] = None

    async def connect(self):
        """Connect to Telegram"""
        try:
            self.client = TelegramClient(
                Config.SESSION_FILE,
                Config.API_ID,
                Config.API_HASH
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
                Config.API_HASH
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
                    "name": entity.first_name,
                    "id": entity.id,
                    "username": f"@{entity.username}" if entity.username else None
                })
            elif isinstance(entity, Channel) and entity.username:
                channels.append({
                    "name": dialog.name,
                    "id": entity.id,
                    "username": f"@{entity.username}"
                })

        return bots + channels

    async def list_files(self, channel_username: str, limit: int = 10, filter_type: Optional[str] = None) -> List[FileInfo]:
        """List files from a channel"""
        files = []

        async for message in self.client.iter_messages(channel_username, limit=limit):
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
                    files.append(file_info)

        return files

    async def get_messages(self, channel_username: str, message_ids: List[int]):
        """Get specific messages by IDs"""
        messages_to_download = []

        for message_id in message_ids:
            message = await self.client.get_messages(channel_username, ids=message_id)
            if message and message.media:
                messages_to_download.append(message)
            else:
                logger.warning(f"Message {message_id} not found or has no media")

        return messages_to_download

    async def get_message(self, channel_username: str, message_id: int):
        """Get a single message"""
        return await self.client.get_messages(channel_username, ids=message_id)

    async def iter_messages(self, channel_username: str, limit: int):
        """Iterate through channel messages"""
        return self.client.iter_messages(channel_username, limit=limit)

    async def download_media(self, message, file_path: str, progress_callback=None):
        """Download media from message"""
        return await self.client.download_media(
            message,
            file=file_path,
            progress_callback=progress_callback
        )
