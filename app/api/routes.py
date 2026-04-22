import os
import logging
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

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
from app.utils.auth_dependencies import get_current_user, is_trusted_ip
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
async def get_ui(request: Request):
    """Serve the login page or redirect if trusted"""
    client_ip = request.client.host
    if is_trusted_ip(client_ip):
        return RedirectResponse(url="/app")
        
    html_path = os.path.join('app', 'static', 'login.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read(), headers=NO_CACHE_HEADERS)


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


@router.get("/auth/is-trusted")
async def check_trusted_ip(request: Request):
    """Check if the current request is from a trusted IP"""
    client_ip = request.client.host
    is_trusted = is_trusted_ip(client_ip)
    return {
        "is_trusted": is_trusted,
        "client_ip": client_ip
    }


NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@router.get("/app", response_class=HTMLResponse)
async def get_app():
    """Serve the main application (authentication by frontend)"""
    html_path = os.path.join('app', 'static', 'view.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read(), headers=NO_CACHE_HEADERS)


@router.get("/logs", response_class=HTMLResponse)
async def get_logs_page():
    """Serve the logs viewer page (auth handled client-side)"""
    html_path = os.path.join('app', 'static', 'logs.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(content=f.read(), headers=NO_CACHE_HEADERS)


@router.get("/api/logs")
async def get_logs(
    level: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    search: Optional[str] = Query(None),
    current_user: str = Depends(get_current_user)
):
    """Get parsed log entries"""
    log_file = os.path.join(os.path.abspath('sessions'), 'app.log')

    if not os.path.exists(log_file):
        return {"logs": [], "total": 0}

    entries = []
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(' | ', 3)
                if len(parts) < 4:
                    continue
                entry = {
                    "timestamp": parts[0],
                    "level": parts[1],
                    "module": parts[2],
                    "message": parts[3]
                }
                if level and entry["level"] != level:
                    continue
                if search and search.lower() not in entry["message"].lower():
                    continue
                entries.append(entry)
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return {"logs": [], "total": 0}

    entries.reverse()
    total = len(entries)
    entries = entries[:limit]

    return {"logs": entries, "total": total}


@router.post("/api/logs/clear")
async def clear_logs(current_user: str = Depends(get_current_user)):
    """Clear the log file"""
    log_file = os.path.join(os.path.abspath('sessions'), 'app.log')
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.truncate(0)
        logger.info("Logs cleared by user")
        return {"status": "success", "message": "Logs cleared"}
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear logs")


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
            "message": f"Downloading {len(request.message_ids)} selected files via FIFO queue.",
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
            "message": f"Download started. Files added to queue.",
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/files/download/{message_id}")
async def download_file(
    message_id: int, 
    background_tasks: BackgroundTasks,
    channel_username: str = Query(..., description="Channel username or ID"),
    current_user: str = Depends(get_current_user)
):
    """Download a specific file by message ID - starts download in background"""
    if not await telegram_service.is_connected():
        raise HTTPException(status_code=400, detail="Not connected. Login first.")
    
    try:
        file_id = f"single_{message_id}"
        
        # Add to background tasks properly
        background_tasks.add_task(download_service.download_single, channel_username, message_id)
        
        return {
            "status": "started",
            "message": "Download added to queue.",
            "file_id": file_id
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Download error: {error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)


@router.get("/download-progress")
async def get_download_progress(current_user: str = Depends(get_current_user)):
    """Get the current download progress"""
    # Use download_service to get status as it includes live transitioning items
    status = download_service.get_status_as_dict()
    return status


@router.post("/download/cancel")
async def cancel_download(current_user: str = Depends(get_current_user)):
    """Cancel the current download operation"""
    result = await download_service.cancel_download()
    return result


@router.post("/download/cancel/{file_id}")
async def cancel_individual_download(file_id: str, current_user: str = Depends(get_current_user)):
    """Cancel a specific download by its ID"""
    result = await download_service.cancel_individual_download(file_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))
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
    # Only consider it a "saved state" if there's meaningful data to resume.
    # We EXCLUDE cancelled sessions from triggering the resume alert to avoid nagging the user.
    has_meaningful_state = bool(
        status.get("session_id") and 
        not status.get("cancelled") and # Don't alert if cancelled
        status.get("total", 0) > 0 and 
        status.get("total", 0) > status.get("progress", 0) # Only alert if something is left
    )

    return {
        "has_saved_state": has_meaningful_state,
        "active": status.get("active", False),
        "cancelled": status.get("cancelled", False),
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

    # Check if there's an active download
    if status.get("active"):
        # Only clear completed downloads, keep the active session
        status["completed_downloads"] = {}
        status["progress"] = 0  # Reset progress since we're clearing completed
        state_manager.save_state()
    else:
        # No active download, clear everything
        state_manager.clear_state()
    
    return {
        "status": "success",
        "message": "Completed downloads cleared"
    }

@router.delete("/download/completed/{file_id}")
async def clear_individual_download(file_id: str, current_user: str = Depends(get_current_user)):
    """Clear a single completed download from state"""
    status = state_manager.get_status()
    found = False

    if file_id in status.get("completed_downloads", {}):
        del status["completed_downloads"][file_id]
        # Update progress counter
        status["progress"] = len(status["completed_downloads"])
        found = True
    
    if not found and file_id in status.get("cancelled_files", {}):
        del status["cancelled_files"][file_id]
        found = True

    if found:
        state_manager.save_state()
        return {
            "status": "success",
            "message": f"Record for {file_id} cleared"
        }
    
    raise HTTPException(status_code=404, detail=f"Download {file_id} not found in history")


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
        "message": "State cleared."
    }

@router.post("/logout-session")
async def logout_session(current_user: str = Depends(get_current_user)):
    """Logout and delete Telegram session"""
    try:
        # Disconnect the Telegram client
        await telegram_service.disconnect()

        # Delete session file
        session_file = Config.SESSION_FILE + '.session'
        if os.path.exists(session_file):
            os.remove(session_file)
            logger.info(f"Deleted session file: {session_file}")

        # Also remove any other session-related files
        for ext in ['', '.session-journal']:
            file_path = Config.SESSION_FILE + ext
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted file: {file_path}")

        # Clear download state
        state_manager.clear_state()

        await telegram_service.connect()  # Reconnect to allow fresh login next time

        return {
            "status": "success",
            "message": "Telegram session cleared"
        }
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# Queue Management Endpoints

from app.models.schemas import QueueReorderRequest

@router.get("/queue")
async def get_queue(current_user: str = Depends(get_current_user)):
    """Get the current download queue"""
    return {
        "status": "success",
        "queue": state_manager.get_queue()
    }

@router.post("/queue/reorder")
async def reorder_queue(request: QueueReorderRequest, current_user: str = Depends(get_current_user)):
    """Reorder the download queue"""
    state_manager.reorder_queue(request.queue_ids)
    return {
        "status": "success", 
        "message": "Queue reordered",
        "queue": state_manager.get_queue()
    }

@router.delete("/queue/{item_id}")
async def remove_from_queue(item_id: str, current_user: str = Depends(get_current_user)):
    """Remove an item from the queue"""
    result = await download_service.cancel_individual_download(item_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))
        
    return {
        "status": "success",
        "message": result.get("message"),
        "queue": state_manager.get_queue()
    }

@router.post("/queue/clear")
async def clear_queue(current_user: str = Depends(get_current_user)):
    """Clear all items from the queue"""
    queue = state_manager.get_queue()
    # Iterate over a copy to be safe
    for item in list(queue):
        file_id = item.get("id")
        if file_id:
            await download_service.cancel_individual_download(file_id)
        else:
            # If no ID, just force remove it from the list if possible
            # (Fallback for corrupted items)
            state_manager.download_status["queue"] = [i for i in state_manager.download_status.get("queue", []) if i != item]
            state_manager.save_state()
    return {
        "status": "success",
        "message": "Queue cleared"
    }
@router.get("/about")
async def get_about(current_user: str = Depends(get_current_user)):
    """Get information about the application including version and changelog"""
    changelog_content = ""
    changelog_path = "CHANGELOG.md"
    
    if os.path.exists(changelog_path):
        try:
            with open(changelog_path, 'r', encoding='utf-8') as f:
                full_changelog = f.read()
                
            # Parse and extract only the last 10 releases
            lines = full_changelog.split('\n')
            filtered_lines = []
            release_count = 0
            max_releases = 5
            
            for line in lines:
                # Check if this is a release header (e.g., "## [1.2.5] - 2024-12-24")
                if line.startswith('## [') and '] -' in line:
                    release_count += 1
                    if release_count > max_releases:
                        break
                
                filtered_lines.append(line)
            
            changelog_content = '\n'.join(filtered_lines)
            
        except Exception as e:
            logger.error(f"Error reading changelog: {e}")
            changelog_content = "Error loading changelog."
    else:
        changelog_content = "Changelog not found."

    return {
        "version": Config.VERSION,
        "changelog": changelog_content
    }
