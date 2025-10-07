from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, Channel, User
from contextlib import asynccontextmanager
import os
import asyncio
from typing import Optional, List
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = f'+{os.getenv("PHONE_NUMBER")}'
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

# Session file - always use sessions directory
SAVE_PATH = 'downloads'
SESSION_DIR = 'sessions'
SESSION_FILE = os.path.join(SESSION_DIR, 'telegram_session')

# Global client instance
client: Optional[TelegramClient] = None
DOWNLOAD_DIR = SAVE_PATH
download_status = {
    "active": False, 
    "progress": 0, 
    "total": 0, 
    "current_file": "",
    "current_file_progress": 0,
    "current_file_size": 0,
    "downloaded_bytes": 0,
    "concurrent_downloads": {},
    "cancelled": False
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    global client
    # Startup
    try:
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info(f"Already logged in as: {me.first_name} (@{me.username})")
        else:
            logger.info("Not authorized. User needs to login.")
    except Exception as e:
        logger.error(f"Startup error: {str(e)}")
    
    yield
    
    # Shutdown
    if client and client.is_connected():
        await client.disconnect()


app = FastAPI(title="Telegram File Downloader", lifespan=lifespan)

# Serve static files (JS, CSS, etc.) from the current directory
app.mount("/static", StaticFiles(directory=".", html=True), name="static")


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CodeRequest(BaseModel):
    code: str


class PasswordRequest(BaseModel):
    password: str


class DownloadRequest(BaseModel):
    channel_username: str
    limit: Optional[int] = 10
    filter_type: Optional[str] = None


class FileInfo(BaseModel):
    file_id: int
    file_name: str
    file_size: int
    file_type: str
    date: str


@app.get("/", response_class=HTMLResponse)
async def get_ui():
    """Serve the modern web UI"""
    with open('view.html', 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read())


@app.post("/login/request-code")
async def request_code():
    """Request verification code using credentials from config"""
    global client
    
    try:
        if not client:
            client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
            await client.connect()
        
        await client.send_code_request(PHONE_NUMBER)
        
        return {
            "status": "success",
            "message": f"Verification code sent to {PHONE_NUMBER}"
        }
    except Exception as e:
        logger.error(f"Request code error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/login/verify")
async def verify_login(request: CodeRequest):
    """Verify the login code"""
    global client
    
    if not client:
        raise HTTPException(status_code=400, detail="Login not started. Call /login/request-code first.")
    
    try:
        me = await client.sign_in(PHONE_NUMBER, request.code)
        
        return {
            "status": "success",
            "message": "Logged in successfully",
            "user": {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name
            }
        }
    except Exception as e:
        error_str = str(e)
        logger.error(f"Login verify error: {error_str}")
        
        if "password" in error_str.lower() or "2fa" in error_str.lower():
            raise HTTPException(status_code=400, detail="2FA password required. Use /login/password endpoint.")
        
        raise HTTPException(status_code=400, detail=error_str)


@app.post("/login/password")
async def verify_password(request: PasswordRequest):
    """Verify 2FA password"""
    global client
    
    if not client:
        raise HTTPException(status_code=400, detail="Login not started.")
    
    try:
        me = await client.sign_in(password=request.password)
        
        return {
            "status": "success",
            "message": "Logged in successfully",
            "user": {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name
            }
        }
    except Exception as e:
        logger.error(f"Password verify error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/status")
async def check_status():
    """Check if the client is connected and authenticated"""
    global client
    
    if not client:
        return {"status": "not_initialized"}
    
    if not client.is_connected():
        return {"status": "disconnected"}
    
    try:
        if not await client.is_user_authorized():
            return {"status": "not_authenticated"}
        
        me = await client.get_me()
        return {
            "status": "connected",
            "user": {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name
            }
        }
    except:
        return {"status": "not_authenticated"}

@app.get("/config/channels")
async def get_channels():
    """Get the list of Telegram channels with @usernames"""
    bots = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, User) and entity.bot:
            bots.append({
                "name": entity.first_name,
                "id": entity.id,
                "username": f"@{entity.username}" if entity.username else None
            })
    channels = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        # Include only public channels with usernames
        if isinstance(entity, Channel) and entity.username:
            channels.append({
                "name": dialog.name,
                "id": entity.id,
                "username": f"@{entity.username}"
            })

    return {
        "channels": bots + channels,
        "save_path": SAVE_PATH  # if still needed
    }

@app.post("/files/list")
async def list_files(request: DownloadRequest):
    """List files from a channel"""
    global client
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    try:
        files = []
        async for message in client.iter_messages(request.channel_username, limit=request.limit):
            if message.media:
                file_info = None
                
                if isinstance(message.media, MessageMediaDocument):
                    doc = message.media.document
                    file_name = next((attr.file_name for attr in doc.attributes 
                                    if hasattr(attr, 'file_name')), f"document_{message.id}")
                    
                    if not request.filter_type or request.filter_type == 'document':
                        file_info = FileInfo(
                            file_id=message.id,
                            file_name=file_name,
                            file_size=doc.size,
                            file_type="document",
                            date=str(message.date)
                        )
                
                elif isinstance(message.media, MessageMediaPhoto):
                    if not request.filter_type or request.filter_type == 'photo':
                        file_info = FileInfo(
                            file_id=message.id,
                            file_name=f"photo_{message.id}.jpg",
                            file_size=0,
                            file_type="photo",
                            date=str(message.date)
                        )
                
                if file_info:
                    files.append(file_info)
        
        return {"status": "success", "files": files, "count": len(files)}
    
    except Exception as e:
        logger.error(f"List files error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


class DownloadSelectedRequest(BaseModel):
    channel_username: str
    message_ids: List[int]


@app.post("/files/download-selected")
async def download_selected_files(request: DownloadSelectedRequest, background_tasks: BackgroundTasks):
    """Download selected files from a channel with parallel downloads"""
    global client, download_status
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    logger.info(f"Starting download of {len(request.message_ids)} selected files")
    
    # Initialize status immediately
    download_status = {
        "active": True, 
        "progress": 0, 
        "total": len(request.message_ids), 
        "current_file": "",
        "current_file_progress": 0,
        "current_file_size": 0,
        "downloaded_bytes": 0,
        "concurrent_downloads": {},
        "cancelled": False
    }
    
    async def download_single_file(message, target_dir, file_id):
        """Download a single file with progress tracking"""
        try:
            file_name = "unknown"
            if isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                file_name = next((attr.file_name for attr in doc.attributes 
                                if hasattr(attr, 'file_name')), f"document_{message.id}")
            elif isinstance(message.media, MessageMediaPhoto):
                file_name = f"photo_{message.id}.jpg"
            
            logger.info(f"Starting download: {file_name}")
            
            download_status["concurrent_downloads"][file_id] = {
                "name": file_name,
                "progress": 0,
                "total": 0,
                "percentage": 0
            }
            
            def progress_callback(current, total):
                if download_status["cancelled"]:
                    raise Exception("Download cancelled by user")
                download_status["concurrent_downloads"][file_id]["progress"] = current
                download_status["concurrent_downloads"][file_id]["total"] = total
                download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
            
            file_path = await client.download_media(
                message, 
                file=target_dir,
                progress_callback=progress_callback
            )
            
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            
            logger.info(f"Completed download: {file_name}")
            return file_path
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            if "cancelled" in error_msg.lower():
                return None
            return None
    
    async def download_task():
        try:
            target_dir = DOWNLOAD_DIR
            
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                logger.warning(f"Could not create directory {target_dir}: {str(e)}")
            
            logger.info(f"Fetching {len(request.message_ids)} messages from {request.channel_username}")
            
            # Get only the selected messages
            messages_to_download = []
            for message_id in request.message_ids:
                message = await client.get_messages(request.channel_username, ids=message_id)
                if message and message.media:
                    messages_to_download.append(message)
                else:
                    logger.warning(f"Message {message_id} not found or has no media")
            
            total_files = len(messages_to_download)
            download_status["total"] = total_files
            logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={MAX_CONCURRENT_DOWNLOADS}")
            
            if total_files == 0:
                logger.warning("No files to download!")
                download_status["active"] = False
                return
            
            downloaded = []
            
            for i in range(0, len(messages_to_download), MAX_CONCURRENT_DOWNLOADS):
                if download_status["cancelled"]:
                    logger.info("Download cancelled by user")
                    break
                    
                batch = messages_to_download[i:i + MAX_CONCURRENT_DOWNLOADS]
                logger.info(f"Processing batch {i // MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")
                
                tasks = []
                for idx, message in enumerate(batch):
                    file_id = f"file_{i + idx}"
                    tasks.append(download_single_file(message, target_dir, file_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        downloaded.append(result)
                        download_status["progress"] = len(downloaded)
                        logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")
            
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            logger.info(f"Download completed. Total files: {len(downloaded)}")
        
        except Exception as e:
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            logger.error(f"Background download error: {str(e)}", exc_info=True)
    
    background_tasks.add_task(download_task)
    
    return {
        "status": "started",
        "message": f"Downloading {len(request.message_ids)} selected files with {MAX_CONCURRENT_DOWNLOADS} parallel downloads."
    }


@app.post("/files/download/{message_id}")
async def download_file(message_id: int, channel_username: str):
    """Download a specific file by message ID"""
    global client, download_status
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    try:
        target_dir = DOWNLOAD_DIR
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create directory {target_dir}: {str(e)}")

        message = await client.get_messages(channel_username, ids=message_id)
        if not message or not message.media:
            raise HTTPException(status_code=404, detail="File not found")

        file_name = "unknown"
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            file_name = next((attr.file_name for attr in doc.attributes if hasattr(attr, 'file_name')), f"document_{message.id}")
        elif isinstance(message.media, MessageMediaPhoto):
            file_name = f"photo_{message.id}.jpg"

        # Initialize download status (single file fields)
        download_status["current_file"] = file_name
        download_status["current_file_progress"] = 0
        download_status["current_file_size"] = 0
        download_status["downloaded_bytes"] = 0
        download_status["cancelled"] = False

        # Also add to concurrent_downloads for unified frontend progress
        file_id = f"single_{message_id}"
        download_status["concurrent_downloads"][file_id] = {
            "name": file_name,
            "progress": 0,
            "total": 0,
            "percentage": 0
        }

        def progress_callback(current, total):
            if download_status["cancelled"]:
                raise Exception("Download cancelled by user")
            download_status["current_file_progress"] = current
            download_status["current_file_size"] = total
            download_status["downloaded_bytes"] = current
            # Update concurrent_downloads entry
            download_status["concurrent_downloads"][file_id]["progress"] = current
            download_status["concurrent_downloads"][file_id]["total"] = total
            download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
            logger.info(f"Download progress: {current}/{total} bytes ({file_name})")

        file_path = await client.download_media(
            message,
            file=target_dir,
            progress_callback=progress_callback
        )

        # Remove from concurrent_downloads after done
        if file_id in download_status["concurrent_downloads"]:
            del download_status["concurrent_downloads"][file_id]

        # Reset progress after download
        download_status["current_file_progress"] = 0
        download_status["current_file_size"] = 0
        download_status["downloaded_bytes"] = 0

        if not file_path:
            raise HTTPException(status_code=500, detail="Download failed")

        return {
            "status": "success",
            "message": "File downloaded successfully",
            "file_path": file_path
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Download error: {error_msg}")
        download_status["current_file_progress"] = 0
        download_status["current_file_size"] = 0
        download_status["downloaded_bytes"] = 0
        # Remove from concurrent_downloads on error
        file_id = f"single_{message_id}"
        if file_id in download_status["concurrent_downloads"]:
            del download_status["concurrent_downloads"][file_id]
        if "cancelled" in error_msg.lower():
            raise HTTPException(status_code=400, detail="Download cancelled")
        raise HTTPException(status_code=400, detail=error_msg)


@app.post("/files/download-all")
async def download_all_files(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Download all files from a channel with parallel downloads"""
    global client, download_status
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    logger.info(f"Starting download-all from {request.channel_username}, limit={request.limit}")
    
    # Initialize status immediately
    download_status = {
        "active": True, 
        "progress": 0, 
        "total": 0,
        "current_file": "",
        "current_file_progress": 0,
        "current_file_size": 0,
        "downloaded_bytes": 0,
        "concurrent_downloads": {},
        "cancelled": False
    }
    
    async def download_single_file(message, target_dir, file_id):
        """Download a single file with progress tracking"""
        try:
            file_name = "unknown"
            if isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                file_name = next((attr.file_name for attr in doc.attributes 
                                if hasattr(attr, 'file_name')), f"document_{message.id}")
            elif isinstance(message.media, MessageMediaPhoto):
                file_name = f"photo_{message.id}.jpg"
            
            logger.info(f"Starting download: {file_name}")
            
            download_status["concurrent_downloads"][file_id] = {
                "name": file_name,
                "progress": 0,
                "total": 0,
                "percentage": 0
            }
            
            def progress_callback(current, total):
                if download_status["cancelled"]:
                    raise Exception("Download cancelled by user")
                download_status["concurrent_downloads"][file_id]["progress"] = current
                download_status["concurrent_downloads"][file_id]["total"] = total
                download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
            
            file_path = await client.download_media(
                message, 
                file=target_dir,
                progress_callback=progress_callback
            )
            
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            
            logger.info(f"Completed download: {file_name}")
            return file_path
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            if "cancelled" in error_msg.lower():
                return None
            return None
    
    async def download_task():
        try:
            target_dir = DOWNLOAD_DIR
            
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                logger.warning(f"Could not create directory {target_dir}: {str(e)}")
            
            logger.info("Fetching messages from channel...")
            messages_to_download = []
            async for message in client.iter_messages(request.channel_username, limit=request.limit):
                if message.media:
                    should_download = False
                    
                    if isinstance(message.media, MessageMediaDocument):
                        if not request.filter_type or request.filter_type == 'document':
                            should_download = True
                    elif isinstance(message.media, MessageMediaPhoto):
                        if not request.filter_type or request.filter_type == 'photo':
                            should_download = True
                    
                    if should_download:
                        messages_to_download.append(message)
            
            total_files = len(messages_to_download)
            download_status["total"] = total_files
            logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={MAX_CONCURRENT_DOWNLOADS}")
            
            if total_files == 0:
                logger.warning("No files to download!")
                download_status["active"] = False
                return
            
            downloaded = []
            
            for i in range(0, len(messages_to_download), MAX_CONCURRENT_DOWNLOADS):
                if download_status["cancelled"]:
                    logger.info("Download cancelled by user")
                    break
                    
                batch = messages_to_download[i:i + MAX_CONCURRENT_DOWNLOADS]
                logger.info(f"Processing batch {i // MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")
                
                tasks = []
                for idx, message in enumerate(batch):
                    file_id = f"file_{i + idx}"
                    tasks.append(download_single_file(message, target_dir, file_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        downloaded.append(result)
                        download_status["progress"] = len(downloaded)
                        logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")
            
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            logger.info(f"Download completed. Total files: {len(downloaded)}")
        
        except Exception as e:
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            logger.error(f"Background download error: {str(e)}", exc_info=True)
    
    background_tasks.add_task(download_task)
    
    return {
        "status": "started",
        "message": f"Download started in background with {MAX_CONCURRENT_DOWNLOADS} parallel downloads."
    }


@app.get("/download-progress")
async def get_download_progress():
    """Get the current download progress"""
    global download_status
    return download_status


@app.post("/download/cancel")
async def cancel_download():
    """Cancel the current download operation"""
    global download_status
    
    if download_status["active"] or download_status["current_file_progress"] > 0:
        # Initialize status immediately
        download_status = {
            "active": False, 
            "progress": 0, 
            "total": 0,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "concurrent_downloads": {},
            "cancelled": True
        }
        logger.info("Download cancellation requested")
        return {
            "status": "success",
            "message": "Download cancellation requested"
        }
    else:
        return {
            "status": "info",
            "message": "No active download to cancel"
        }


@app.get("/files/downloaded")
async def list_downloaded_files():
    """List all downloaded files"""
    try:
        files = []
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                files.append({
                    "name": filename,
                    "size": os.path.getsize(file_path),
                    "path": file_path
                })
        
        return {"status": "success", "files": files, "count": len(files)}
    
    except Exception as e:
        logger.error(f"List downloaded files error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/files/serve/{filename}")
async def serve_file(filename: str):
    """Serve a downloaded file"""
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
