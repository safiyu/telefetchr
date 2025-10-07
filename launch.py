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
from typing import Optional, List, Dict, Any
import logging
import json
from datetime import datetime
import uuid

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
STATE_FILE = os.path.join(SESSION_DIR, 'download_state.json')

# Global client instance
client: Optional[TelegramClient] = None
DOWNLOAD_DIR = SAVE_PATH

# Enhanced download status with persistence
download_status = {
    "active": False, 
    "progress": 0, 
    "total": 0, 
    "current_file": "",
    "current_file_progress": 0,
    "current_file_size": 0,
    "downloaded_bytes": 0,
    "concurrent_downloads": {},
    "completed_downloads": {},  # Track completed downloads
    "cancelled": False,
    "session_id": str(uuid.uuid4()),  # Unique session ID
    "started_at": None,
    "channel": None
}

# Active download tasks tracking
active_download_tasks = {}

def save_state():
    """Save current download state to file"""
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        
        # Create a copy to save (avoid modifying the global state during serialization)
        state_to_save = {
            "active": download_status.get("active", False),
            "progress": download_status.get("progress", 0),
            "total": download_status.get("total", 0),
            "current_file": download_status.get("current_file", ""),
            "current_file_progress": download_status.get("current_file_progress", 0),
            "current_file_size": download_status.get("current_file_size", 0),
            "downloaded_bytes": download_status.get("downloaded_bytes", 0),
            "completed_downloads": download_status.get("completed_downloads", {}),
            "cancelled": download_status.get("cancelled", False),
            "session_id": download_status.get("session_id"),
            "started_at": download_status.get("started_at"),
            "channel": download_status.get("channel")
        }
        
        # Write to temp file first, then rename (atomic operation)
        temp_file = STATE_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(state_to_save, f, default=str, indent=2)
        
        # Atomic rename
        os.replace(temp_file, STATE_FILE)
        
    except Exception as e:
        logger.error(f"Error saving state: {e}")


def load_state():
    """Load download state from file"""
    global download_status
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                saved_state = json.load(f)
                
                # Only restore if the state is meaningful
                if saved_state.get("session_id") and saved_state.get("channel"):
                    # If there was an active download, mark it as not active
                    # (since we can't resume the actual task after restart)
                    if saved_state.get("active"):
                        saved_state["active"] = False
                        saved_state["cancelled"] = True
                        logger.info("Found interrupted download session - marked for resume")
                    
                    # Merge with current status
                    download_status.update(saved_state)
                    
                    # Clear concurrent downloads (they're not valid after restart)
                    download_status["concurrent_downloads"] = {}
                    
                    logger.info(f"Loaded saved download state: {len(saved_state.get('completed_downloads', {}))} completed files")
                else:
                    logger.info("No valid saved state found")
                    
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted state file: {e}")
        # Backup corrupted file
        try:
            if os.path.exists(STATE_FILE):
                backup_file = STATE_FILE + '.corrupted.' + str(int(datetime.now().timestamp()))
                os.rename(STATE_FILE, backup_file)
                logger.info(f"Backed up corrupted state to {backup_file}")
        except:
            pass
    except Exception as e:
        logger.error(f"Error loading state: {e}")


def clear_state():
    """Clear saved state file"""
    try:
        if os.path.exists(STATE_FILE):
            # Backup before clearing
            backup_file = STATE_FILE + '.backup.' + str(int(datetime.now().timestamp()))
            os.rename(STATE_FILE, backup_file)
            logger.info(f"Backed up state to {backup_file} before clearing")
        
        # Reset global state
        global download_status
        download_status.update({
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
            "session_id": None,
            "started_at": None,
            "channel": None
        })
        
    except Exception as e:
        logger.error(f"Error clearing state: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    global client
    # Startup
    try:
        # Load any saved state FIRST
        load_state()
        
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info(f"Already logged in as: {me.first_name} (@{me.username})")
            
            # If there's a saved state with downloads, log it
            if download_status.get("session_id") and download_status.get("completed_downloads"):
                completed_count = len(download_status.get("completed_downloads", {}))
                total_count = download_status.get("total", 0)
                logger.info(f"Resumable session found: {completed_count}/{total_count} files completed")
        else:
            logger.info("Not authorized. User needs to login.")
    except Exception as e:
        logger.error(f"Startup error: {str(e)}")
    
    yield
    
    # Shutdown - save state one final time
    save_state()
    
    # Cancel any active download tasks
    for task_id, task in list(active_download_tasks.items()):
        if not task.done():
            task.cancel()
            logger.info(f"Cancelled task {task_id} during shutdown")
    
    if client and client.is_connected():
        await client.disconnect()

app = FastAPI(title="Telefetchr", lifespan=lifespan)

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

@app.get("/debug/state")
async def debug_state():
    """Debug endpoint to see full state information"""
    global download_status
    
    state_file_exists = os.path.exists(STATE_FILE)
    state_file_size = os.path.getsize(STATE_FILE) if state_file_exists else 0
    
    # Read raw file content
    raw_state = None
    if state_file_exists:
        try:
            with open(STATE_FILE, 'r') as f:
                raw_state = f.read()
        except:
            raw_state = "Error reading file"
    
    return {
        "memory_state": {
            "active": download_status.get("active"),
            "progress": download_status.get("progress"),
            "total": download_status.get("total"),
            "session_id": download_status.get("session_id"),
            "channel": download_status.get("channel"),
            "started_at": download_status.get("started_at"),
            "completed_count": len(download_status.get("completed_downloads", {})),
            "concurrent_count": len(download_status.get("concurrent_downloads", {})),
            "cancelled": download_status.get("cancelled")
        },
        "file_state": {
            "exists": state_file_exists,
            "size_bytes": state_file_size,
            "path": STATE_FILE,
            "content": raw_state[:500] if raw_state else None  # First 500 chars
        },
        "completed_downloads": {
            file_id: {
                "name": data.get("name"),
                "size": data.get("size"),
                "completed_at": data.get("completed_at")
            }
            for file_id, data in download_status.get("completed_downloads", {}).items()
        },
        "active_tasks": list(active_download_tasks.keys())
    }


# Also add an endpoint to manually trigger state save
@app.post("/debug/save-state")
async def debug_save_state():
    """Manually trigger state save"""
    save_state()
    return {"status": "success", "message": "State saved"}


# Add an endpoint to reload state from file
@app.post("/debug/reload-state")
async def debug_reload_state():
    """Reload state from file"""
    load_state()
    return {
        "status": "success", 
        "message": "State reloaded",
        "completed_count": len(download_status.get("completed_downloads", {})),
        "session_id": download_status.get("session_id")
    }




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
        "save_path": SAVE_PATH
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
    global client, download_status, active_download_tasks
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    logger.info(f"Starting download of {len(request.message_ids)} selected files")
    
    # IMPORTANT: Clear old session but keep structure
    # Don't create a new dict - just update the existing one
    download_status.update({
        "active": True, 
        "progress": 0, 
        "total": len(request.message_ids), 
        "current_file": "",
        "current_file_progress": 0,
        "current_file_size": 0,
        "downloaded_bytes": 0,
        "concurrent_downloads": {},
        "completed_downloads": {},  # Clear for new session
        "cancelled": False,
        "session_id": str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(),
        "channel": request.channel_username
    })
    save_state()
    
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
            save_state()
            
            def progress_callback(current, total):
                if download_status["cancelled"]:
                    raise Exception("Download cancelled by user")
                download_status["concurrent_downloads"][file_id]["progress"] = current
                download_status["concurrent_downloads"][file_id]["total"] = total
                download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
                save_state()
            
            file_path = await client.download_media(
                message, 
                file=target_dir,
                progress_callback=progress_callback
            )
            
            if file_path:
                # Move to completed downloads
                download_status["completed_downloads"][file_id] = {
                    "name": file_name,
                    "path": file_path,
                    "size": download_status["concurrent_downloads"][file_id]["total"],
                    "completed_at": datetime.now().isoformat()
                }
            
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            save_state()
            
            logger.info(f"Completed download: {file_name}")
            return file_path
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            save_state()
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
            save_state()
            logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={MAX_CONCURRENT_DOWNLOADS}")
            
            if total_files == 0:
                logger.warning("No files to download!")
                download_status["active"] = False
                save_state()
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
                    file_id = f"file_{i + idx}_{message.id}"
                    tasks.append(download_single_file(message, target_dir, file_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        downloaded.append(result)
                        download_status["progress"] = len(downloaded)
                        save_state()
                        logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")
            
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            save_state()
            logger.info(f"Download completed. Total files: {len(downloaded)}")
        
        except Exception as e:
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            save_state()
            logger.error(f"Background download error: {str(e)}", exc_info=True)
    
    # Create task and track it
    task_id = download_status["session_id"]
    task = asyncio.create_task(download_task())
    active_download_tasks[task_id] = task
    
    return {
        "status": "started",
        "message": f"Downloading {len(request.message_ids)} selected files with {MAX_CONCURRENT_DOWNLOADS} parallel downloads.",
        "session_id": task_id
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

        # Initialize download status for single file (KEEP CHANNEL INFO!)
        file_id = f"single_{message_id}"
        
        # Update state but preserve structure
        download_status.update({
            "active": True,  # Mark as active during download
            "current_file": file_name,
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "cancelled": False,
            "channel": channel_username,  # SAVE THE CHANNEL!
            "session_id": download_status.get("session_id") or str(uuid.uuid4()),
            "started_at": download_status.get("started_at") or datetime.now().isoformat()
        })

        # Also add to concurrent_downloads for unified frontend progress
        download_status["concurrent_downloads"][file_id] = {
            "name": file_name,
            "progress": 0,
            "total": 0,
            "percentage": 0
        }
        save_state()

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
            save_state()

        file_path = await client.download_media(
            message,
            file=target_dir,
            progress_callback=progress_callback
        )

        if file_path:
            # Add to completed downloads
            download_status["completed_downloads"][file_id] = {
                "name": file_name,
                "path": file_path,
                "size": download_status["concurrent_downloads"][file_id]["total"],
                "completed_at": datetime.now().isoformat()
            }
            download_status["progress"] = len(download_status["completed_downloads"])

        # Remove from concurrent_downloads after done
        if file_id in download_status["concurrent_downloads"]:
            del download_status["concurrent_downloads"][file_id]

        # Reset progress after download but KEEP session info
        download_status.update({
            "active": False,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0
        })
        save_state()

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
        
        # Clean up on error
        download_status.update({
            "active": False,
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0
        })
        
        # Remove from concurrent_downloads on error
        file_id = f"single_{message_id}"
        if file_id in download_status["concurrent_downloads"]:
            del download_status["concurrent_downloads"][file_id]
        save_state()
        
        if "cancelled" in error_msg.lower():
            raise HTTPException(status_code=400, detail="Download cancelled")
        raise HTTPException(status_code=400, detail=error_msg)

@app.post("/debug/cleanup-state")
async def cleanup_state():
    """Clean up corrupted or incomplete state"""
    global download_status
    
    # Backup current state
    if os.path.exists(STATE_FILE):
        backup_file = STATE_FILE + '.backup.' + str(int(datetime.now().timestamp()))
        try:
            import shutil
            shutil.copy2(STATE_FILE, backup_file)
            logger.info(f"Backed up state to {backup_file}")
        except Exception as e:
            logger.error(f"Failed to backup state: {e}")
    
    # Clean up incomplete downloads (concurrent_downloads that aren't active)
    cleaned_items = []
    if not download_status.get("active") and download_status.get("concurrent_downloads"):
        cleaned_items = list(download_status["concurrent_downloads"].keys())
        download_status["concurrent_downloads"] = {}
        logger.info(f"Cleaned up {len(cleaned_items)} incomplete downloads")
    
    # Reset fields that don't make sense when not active
    if not download_status.get("active"):
        download_status["current_file"] = ""
        download_status["current_file_progress"] = 0
        download_status["current_file_size"] = 0
        download_status["downloaded_bytes"] = 0
    
    # If there are no completed downloads and no channel, reset everything
    if not download_status.get("completed_downloads") and not download_status.get("channel"):
        download_status.update({
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
            "session_id": None,
            "started_at": None,
            "channel": None
        })
        logger.info("Reset state completely (no valid session data)")
    
    save_state()
    
    return {
        "status": "success",
        "message": "State cleaned up",
        "cleaned_concurrent": cleaned_items,
        "current_state": {
            "active": download_status.get("active"),
            "session_id": download_status.get("session_id"),
            "channel": download_status.get("channel"),
            "completed_count": len(download_status.get("completed_downloads", {}))
        }
    }


@app.post("/debug/reset-state")
async def reset_state():
    """Completely reset the download state (use with caution!)"""
    global download_status
    
    # Backup current state first
    if os.path.exists(STATE_FILE):
        backup_file = STATE_FILE + '.backup.' + str(int(datetime.now().timestamp()))
        try:
            import shutil
            shutil.copy2(STATE_FILE, backup_file)
            logger.info(f"Backed up state to {backup_file}")
        except Exception as e:
            logger.error(f"Failed to backup state: {e}")
    
    # Completely reset
    download_status.clear()
    download_status.update({
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
        "session_id": None,
        "started_at": None,
        "channel": None
    })
    
    save_state()
    
    return {
        "status": "success",
        "message": "State completely reset. Backup saved."
    }

@app.post("/files/download-all")
async def download_all_files(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Download all files from a channel with parallel downloads"""
    global client, download_status, active_download_tasks
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    logger.info(f"Starting download-all from {request.channel_username}, limit={request.limit}")
    
    # IMPORTANT: Clear old session but keep structure
    # Don't create a new dict - just update the existing one
    download_status.update({
        "active": True, 
        "progress": 0, 
        "total": 0,
        "current_file": "",
        "current_file_progress": 0,
        "current_file_size": 0,
        "downloaded_bytes": 0,
        "concurrent_downloads": {},
        "completed_downloads": {},  # Clear for new session
        "cancelled": False,
        "session_id": str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(),
        "channel": request.channel_username
    })
    save_state()
    
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
            save_state()
            
            def progress_callback(current, total):
                if download_status["cancelled"]:
                    raise Exception("Download cancelled by user")
                download_status["concurrent_downloads"][file_id]["progress"] = current
                download_status["concurrent_downloads"][file_id]["total"] = total
                download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
                save_state()
            
            file_path = await client.download_media(
                message, 
                file=target_dir,
                progress_callback=progress_callback
            )
            
            if file_path:
                # Move to completed downloads
                download_status["completed_downloads"][file_id] = {
                    "name": file_name,
                    "path": file_path,
                    "size": download_status["concurrent_downloads"][file_id]["total"],
                    "completed_at": datetime.now().isoformat()
                }
            
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            save_state()
            
            logger.info(f"Completed download: {file_name}")
            return file_path
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            save_state()
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
            save_state()
            logger.info(f"Found {total_files} files to download. MAX_CONCURRENT_DOWNLOADS={MAX_CONCURRENT_DOWNLOADS}")
            
            if total_files == 0:
                logger.warning("No files to download!")
                download_status["active"] = False
                save_state()
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
                    file_id = f"file_{i + idx}_{message.id}"
                    tasks.append(download_single_file(message, target_dir, file_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        downloaded.append(result)
                        download_status["progress"] = len(downloaded)
                        save_state()
                        logger.info(f"Downloaded ({len(downloaded)}/{total_files}): {result}")
            
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            save_state()
            logger.info(f"Download completed. Total files: {len(downloaded)}")
        
        except Exception as e:
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            save_state()
            logger.error(f"Background download error: {str(e)}", exc_info=True)
    
    # Create task and track it
    task_id = download_status["session_id"]
    task = asyncio.create_task(download_task())
    active_download_tasks[task_id] = task
    
    return {
        "status": "started",
        "message": f"Download started in background with {MAX_CONCURRENT_DOWNLOADS} parallel downloads.",
        "session_id": task_id
    }


@app.get("/download-progress")
async def get_download_progress():
    """Get the current download progress"""
    global download_status
    return download_status


@app.post("/download/cancel")
async def cancel_download():
    """Cancel the current download operation"""
    global download_status, active_download_tasks
    
    if download_status["active"] or download_status["current_file_progress"] > 0:
        download_status["cancelled"] = True
        save_state()
        
        # Cancel active tasks
        session_id = download_status.get("session_id")
        if session_id and session_id in active_download_tasks:
            task = active_download_tasks[session_id]
            if not task.done():
                task.cancel()
            del active_download_tasks[session_id]
        
        # Reset status but KEEP completed downloads for resume functionality
        download_status.update({
            "active": False, 
            "progress": len(download_status.get("completed_downloads", {})),
            "current_file": "",
            "current_file_progress": 0,
            "current_file_size": 0,
            "downloaded_bytes": 0,
            "concurrent_downloads": {},
            # KEEP: completed_downloads, session_id, channel, total, started_at
            "cancelled": True
        })
        save_state()
        
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


@app.post("/download/clear-completed")
async def clear_completed_downloads():
    """Clear completed downloads from state"""
    global download_status
    
    download_status["completed_downloads"] = {}
    save_state()
    
    return {
        "status": "success",
        "message": "Completed downloads cleared"
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


@app.post("/download/resume")
async def resume_download():
    """Resume a previously interrupted download session"""
    global download_status, client, active_download_tasks
    
    if not client or not client.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    # Check if there's a saved state to resume
    if not download_status.get("channel"):
        return {
            "status": "error",
            "message": "No saved download session to resume"
        }
    
    # Check if already active
    if download_status.get("active"):
        return {
            "status": "info",
            "message": "Download already in progress"
        }
    
    # Get channel info from saved state
    channel = download_status.get("channel")
    total = download_status.get("total", 0)
    
    # Get completed file IDs to skip them
    completed_ids = set()
    for file_id, data in download_status.get("completed_downloads", {}).items():
        # Extract message ID from file_id format (file_INDEX_MESSAGEID)
        if "_" in file_id:
            parts = file_id.split("_")
            if len(parts) >= 3:
                try:
                    completed_ids.add(int(parts[-1]))
                except:
                    pass
    
    logger.info(f"Resuming download session {download_status.get('session_id')}")
    logger.info(f"Channel: {channel}, Total: {total}, Completed: {len(completed_ids)}")
    
    # Reactivate the download
    download_status["active"] = True
    download_status["cancelled"] = False
    save_state()
    
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
            save_state()
            
            def progress_callback(current, total):
                if download_status["cancelled"]:
                    raise Exception("Download cancelled by user")
                download_status["concurrent_downloads"][file_id]["progress"] = current
                download_status["concurrent_downloads"][file_id]["total"] = total
                download_status["concurrent_downloads"][file_id]["percentage"] = int((current / total * 100)) if total > 0 else 0
                save_state()
            
            file_path = await client.download_media(
                message, 
                file=target_dir,
                progress_callback=progress_callback
            )
            
            if file_path:
                # Move to completed downloads
                download_status["completed_downloads"][file_id] = {
                    "name": file_name,
                    "path": file_path,
                    "size": download_status["concurrent_downloads"][file_id]["total"],
                    "completed_at": datetime.now().isoformat()
                }
            
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            
            # Update overall progress
            download_status["progress"] = len(download_status.get("completed_downloads", {}))
            save_state()
            
            logger.info(f"Completed download: {file_name}")
            return file_path
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error downloading {file_name}: {error_msg}")
            if file_id in download_status["concurrent_downloads"]:
                del download_status["concurrent_downloads"][file_id]
            save_state()
            if "cancelled" in error_msg.lower():
                return None
            return None
    
    async def resume_task():
        try:
            target_dir = DOWNLOAD_DIR
            os.makedirs(target_dir, exist_ok=True)
            
            logger.info(f"Fetching messages from {channel} to resume download...")
            
            # Fetch all messages again and filter out completed ones
            messages_to_download = []
            async for message in client.iter_messages(channel, limit=total):
                if message.media and message.id not in completed_ids:
                    messages_to_download.append(message)
            
            remaining_files = len(messages_to_download)
            logger.info(f"Found {remaining_files} remaining files to download out of {total} total")
            
            if remaining_files == 0:
                logger.info("All files already downloaded!")
                download_status["active"] = False
                download_status["progress"] = download_status["total"]
                save_state()
                return
            
            downloaded = []
            
            # Process remaining files in batches
            for i in range(0, len(messages_to_download), MAX_CONCURRENT_DOWNLOADS):
                if download_status["cancelled"]:
                    logger.info("Download cancelled by user")
                    break
                    
                batch = messages_to_download[i:i + MAX_CONCURRENT_DOWNLOADS]
                logger.info(f"Processing batch {i // MAX_CONCURRENT_DOWNLOADS + 1}, {len(batch)} files")
                
                tasks = []
                for idx, message in enumerate(batch):
                    file_id = f"file_{len(completed_ids) + i + idx}_{message.id}"
                    tasks.append(download_single_file(message, target_dir, file_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        downloaded.append(result)
                        logger.info(f"Downloaded ({len(completed_ids) + len(downloaded)}/{total}): {result}")
            
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            download_status["progress"] = len(download_status.get("completed_downloads", {}))
            save_state()
            logger.info(f"Resume completed. Total files now: {download_status['progress']}/{total}")
        
        except Exception as e:
            download_status["active"] = False
            download_status["concurrent_downloads"] = {}
            save_state()
            logger.error(f"Resume download error: {str(e)}", exc_info=True)
    
    # Create task and track it
    task_id = download_status.get("session_id") or str(uuid.uuid4())
    download_status["session_id"] = task_id
    task = asyncio.create_task(resume_task())
    active_download_tasks[task_id] = task
    
    return {
        "status": "resumed",
        "message": f"Resumed download with {len(completed_ids)} files already completed",
        "session_id": task_id,
        "completed": len(completed_ids),
        "remaining": total - len(completed_ids),
        "total": total
    }


@app.get("/download/state")
async def get_download_state():
    """Get the current saved download state"""
    global download_status
    
    return {
        "has_saved_state": bool(download_status.get("session_id")),
        "active": download_status.get("active", False),
        "session_id": download_status.get("session_id"),
        "channel": download_status.get("channel"),
        "started_at": download_status.get("started_at"),
        "progress": download_status.get("progress", 0),
        "total": download_status.get("total", 0),
        "completed_count": len(download_status.get("completed_downloads", {})),
        "concurrent_count": len(download_status.get("concurrent_downloads", {}))
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)