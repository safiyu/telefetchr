import os
import logging
from datetime import timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from fastapi.responses import FileResponse, HTMLResponse

from app.models.schemas import (
    LoginRequest,
    Token,
    CodeRequest,
    PasswordRequest,
    DownloadRequest,
    DownloadSelectedRequest
)
from app.services.telegram_service import TelegramService
from app.services.download_service import DownloadService
from app.services.auth_service import AuthService
from app.utils.state_manager import StateManager
from app.utils.auth_dependencies import get_current_user
from app.config import Config

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# These will be injected during app startup
telegram_service: TelegramService = None
download_service: DownloadService = None
state_manager: StateManager = None
auth_service: AuthService = None


def set_services(tg_service: TelegramService, dl_service: DownloadService, st_manager: StateManager, a_service: AuthService):
    """Set service instances"""
    global telegram_service, download_service, state_manager, auth_service
    telegram_service = tg_service
    download_service = dl_service
    state_manager = st_manager
    auth_service = a_service


@router.get("/", response_class=HTMLResponse)
async def get_ui():
    """Serve the login page"""
    html_path = os.path.join('app', 'static', 'login.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read())


@router.post("/auth/login", response_model=Token)
async def login(login_request: LoginRequest):
    """Login endpoint - returns JWT token"""
    if not auth_service.authenticate_user(login_request.username, login_request.password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password"
        )

    access_token_expires = timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_service.create_access_token(
        data={"sub": login_request.username}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/app", response_class=HTMLResponse)
async def get_app():
    """Serve the main application (authentication by frontend)"""
    html_path = os.path.join('app', 'static', 'view.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read())


@router.get("/status")
async def check_status(current_user: str = Depends(get_current_user)):
    """Check if the client is connected and authenticated"""
    if not await telegram_service.is_connected():
        return {"status": "disconnected"}

    try:
        if not await telegram_service.is_authorized():
            return {"status": "not_authenticated"}

        me = await telegram_service.get_me()
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


@router.post("/login/request-code")
async def request_code(current_user: str = Depends(get_current_user)):
    """Request verification code"""
    try:
        message = await telegram_service.request_code()
        return {"status": "success", "message": message}
    except Exception as e:
        logger.error(f"Request code error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login/verify")
async def verify_login(request: CodeRequest, current_user: str = Depends(get_current_user)):
    """Verify the login code"""
    try:
        user = await telegram_service.verify_code(request.code)
        return {
            "status": "success",
            "message": "Logged in successfully",
            "user": user
        }
    except Exception as e:
        error_str = str(e)
        logger.error(f"Login verify error: {error_str}")

        if "password" in error_str.lower() or "2fa" in error_str.lower():
            raise HTTPException(status_code=400, detail="2FA password required. Use /login/password endpoint.")

        raise HTTPException(status_code=400, detail=error_str)


@router.post("/login/password")
async def verify_password(request: PasswordRequest, current_user: str = Depends(get_current_user)):
    """Verify 2FA password"""
    try:
        user = await telegram_service.verify_password(request.password)
        return {
            "status": "success",
            "message": "Logged in successfully",
            "user": user
        }
    except Exception as e:
        logger.error(f"Password verify error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config/channels")
async def get_channels(current_user: str = Depends(get_current_user)):
    """Get the list of Telegram channels"""
    channels = await telegram_service.get_channels()
    return {
        "channels": channels,
        "save_path": Config.SAVE_PATH
    }


@router.post("/files/list")
async def list_files(request: DownloadRequest, current_user: str = Depends(get_current_user)):
    """List files from a channel with search and filter options"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")

    try:
        files = await telegram_service.list_files(
            request.channel_username,
            request.limit,
            request.filter_type,
            request.search_query,
            request.min_size,
            request.max_size,
            request.date_from,
            request.date_to,
            request.file_extension
        )
        return {"status": "success", "files": files, "count": len(files)}
    except Exception as e:
        logger.error(f"List files error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/files/download-selected")
async def download_selected_files(request: DownloadSelectedRequest, background_tasks: BackgroundTasks, current_user: str = Depends(get_current_user)):
    """Download selected files"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")

    try:
        session_id = await download_service.download_selected_files(
            request.channel_username,
            request.message_ids
        )
        return {
            "status": "started",
            "message": f"Downloading {len(request.message_ids)} selected files with {Config.MAX_CONCURRENT_DOWNLOADS} parallel downloads.",
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/files/download-all")
async def download_all_files(request: DownloadRequest, background_tasks: BackgroundTasks, current_user: str = Depends(get_current_user)):
    """Download all files from a channel"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")

    try:
        session_id = await download_service.download_all_files(
            request.channel_username,
            request.limit,
            request.filter_type
        )
        return {
            "status": "started",
            "message": f"Download started in background with {Config.MAX_CONCURRENT_DOWNLOADS} parallel downloads.",
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/files/download/{message_id}")
async def download_file(message_id: int, channel_username: str, current_user: str = Depends(get_current_user)):
    """Download a specific file by message ID"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")

    try:
        result = await download_service.download_single(channel_username, message_id)
        return {
            "status": "success",
            "message": "File downloaded successfully",
            "file_path": result["file_path"]
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Download error: {error_msg}")
        if "cancelled" in error_msg.lower():
            raise HTTPException(status_code=400, detail="Download cancelled")
        raise HTTPException(status_code=400, detail=error_msg)


@router.get("/download-progress")
async def get_download_progress(current_user: str = Depends(get_current_user)):
    """Get the current download progress"""
    return state_manager.get_status()


@router.post("/download/cancel")
async def cancel_download(current_user: str = Depends(get_current_user)):
    """Cancel the current download operation"""
    result = await download_service.cancel_download()
    return result


@router.post("/download/resume")
async def resume_download(current_user: str = Depends(get_current_user)):
    """Resume a previously interrupted download session"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")

    result = await download_service.resume_download()
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.get("/download/state")
async def get_download_state(current_user: str = Depends(get_current_user)):
    """Get the current saved download state"""
    status = state_manager.get_status()
    return {
        "has_saved_state": bool(status.get("session_id")),
        "active": status.get("active", False),
        "session_id": status.get("session_id"),
        "channel": status.get("channel"),
        "started_at": status.get("started_at"),
        "progress": status.get("progress", 0),
        "total": status.get("total", 0),
        "completed_count": len(status.get("completed_downloads", {})),
        "concurrent_count": len(status.get("concurrent_downloads", {}))
    }


@router.post("/download/clear-completed")
async def clear_completed_downloads(current_user: str = Depends(get_current_user)):
    """Clear completed downloads from state"""
    status = state_manager.get_status()
    status["completed_downloads"] = {}
    if not status.get("active"):
        status["session_id"] = None
        status["channel"] = None
        status["started_at"] = None
        status["total"] = 0
        status["progress"] = 0
        status["cancelled"] = False
    state_manager.save_state()
    return {
        "status": "success",
        "message": "Completed downloads cleared"
    }


@router.get("/files/downloaded")
async def list_downloaded_files(current_user: str = Depends(get_current_user)):
    """List all downloaded files"""
    try:
        files = []
        for filename in os.listdir(Config.SAVE_PATH):
            file_path = os.path.join(Config.SAVE_PATH, filename)
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


@router.get("/files/serve/{filename}")
async def serve_file(filename: str, current_user: str = Depends(get_current_user)):
    """Serve a downloaded file"""
    file_path = os.path.join(Config.SAVE_PATH, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path)


@router.get("/debug/state")
async def debug_state(current_user: str = Depends(get_current_user)):
    """Debug endpoint to see full state information"""
    status = state_manager.get_status()

    state_file_exists = os.path.exists(Config.STATE_FILE)
    state_file_size = os.path.getsize(Config.STATE_FILE) if state_file_exists else 0

    raw_state = None
    if state_file_exists:
        try:
            with open(Config.STATE_FILE, 'r') as f:
                raw_state = f.read()
        except:
            raw_state = "Error reading file"

    return {
        "memory_state": {
            "active": status.get("active"),
            "progress": status.get("progress"),
            "total": status.get("total"),
            "session_id": status.get("session_id"),
            "channel": status.get("channel"),
            "started_at": status.get("started_at"),
            "completed_count": len(status.get("completed_downloads", {})),
            "concurrent_count": len(status.get("concurrent_downloads", {})),
            "cancelled": status.get("cancelled")
        },
        "file_state": {
            "exists": state_file_exists,
            "size_bytes": state_file_size,
            "path": Config.STATE_FILE,
            "content": raw_state[:500] if raw_state else None
        },
        "completed_downloads": {
            file_id: {
                "name": data.get("name"),
                "size": data.get("size"),
                "completed_at": data.get("completed_at")
            }
            for file_id, data in status.get("completed_downloads", {}).items()
        },
        "active_tasks": list(download_service.active_download_tasks.keys())
    }


@router.post("/debug/cleanup-state")
async def cleanup_state(current_user: str = Depends(get_current_user)):
    """Clean up corrupted or incomplete state"""
    cleaned_items = state_manager.cleanup_state()
    status = state_manager.get_status()

    return {
        "status": "success",
        "message": "State cleaned up",
        "cleaned_concurrent": cleaned_items,
        "current_state": {
            "active": status.get("active"),
            "session_id": status.get("session_id"),
            "channel": status.get("channel"),
            "completed_count": len(status.get("completed_downloads", {}))
        }
    }


@router.post("/debug/reset-state")
async def reset_state(current_user: str = Depends(get_current_user)):
    """Completely reset the download state"""
    import shutil
    from datetime import datetime

    # Backup current state first
    if os.path.exists(Config.STATE_FILE):
        backup_file = Config.STATE_FILE + '.backup.' + str(int(datetime.now().timestamp()))
        try:
            shutil.copy2(Config.STATE_FILE, backup_file)
            logger.info(f"Backed up state to {backup_file}")
        except Exception as e:
            logger.error(f"Failed to backup state: {e}")

    state_manager.clear_state()

    return {
        "status": "success",
        "message": "State completely reset. Backup saved."
    }
