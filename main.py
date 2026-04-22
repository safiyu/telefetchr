"""Main entry point for the Telefetchr application."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import Config
from app.services.telegram_service import TelegramService
from app.services.download_service import DownloadService
from app.services.auth_service import AuthService
from app.utils.state_manager import StateManager
from app.utils.auth_dependencies import set_auth_service
from app.api.routes import router, set_services

# Configure logging
log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
formatter = logging.Formatter(log_format)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
root_logger.addHandler(console_handler)

# File handler
log_dir = os.path.abspath('sessions')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'app.log')

file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

# Silence noisy third-party loggers
logging.getLogger("telethon").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Global service instances
telegram_service: TelegramService = None
download_service: DownloadService = None
state_manager: StateManager = None
auth_service: AuthService = None


def cleanup_old_logs(log_file_path: str, max_age_days: int = 30):
    """Remove log lines older than max_age_days from the active log file."""
    if not os.path.exists(log_file_path):
        return
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=max_age_days)
    kept_lines = []
    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    timestamp_str = line.split(' | ')[0].strip()
                    line_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
                    if line_time >= cutoff:
                        kept_lines.append(line)
                except (ValueError, IndexError):
                    kept_lines.append(line)
        with open(log_file_path, 'w', encoding='utf-8') as f:
            f.writelines(kept_lines)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    global telegram_service, download_service, state_manager, auth_service

    # Startup
    try:
        # Ensure required directories exist
        Config.ensure_directories()
        
        # Cleanup old logs
        cleanup_old_logs(os.path.join(os.path.abspath('sessions'), 'app.log'))

        # Initialize authentication service
        auth_service = AuthService()
        set_auth_service(auth_service)
        logger.info("Authentication service initialized")

        # Initialize state manager and load saved state
        state_manager = StateManager()
        state_manager.load_state()
        
        # Cleanup incomplete/interrupted downloads from previous run
        state_manager.cleanup_state(force=True)

        # Initialize Telegram service
        telegram_service = TelegramService()
        await telegram_service.connect()

        # Initialize download service
        download_service = DownloadService(telegram_service, state_manager)

        # Inject services into routes
        set_services(telegram_service, download_service, state_manager, auth_service)

        # Log saved state information
        # Auto-resume if active and has a session ID
        status = state_manager.get_status()
        if status.get("session_id") and status.get("active") and not status.get("cancelled"):
            logger.info("Auto-resuming active download session found at startup")
            download_service.start_queue_processor()
        
    except Exception as e:
        logger.error(f"Startup error: {str(e)}")

    yield

    # Shutdown - save state one final time
    if state_manager:
        state_manager.save_state()

    # Cancel any active download tasks
    if download_service:
        download_service.cleanup_tasks()

    # Disconnect Telegram client
    if telegram_service:
        await telegram_service.disconnect()


# Create FastAPI app
app = FastAPI(title="Telefetchr", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include API routes
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, 
        host=Config.HOST, 
        port=Config.PORT,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
