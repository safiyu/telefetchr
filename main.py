"""Main entry point for the Telefetchr application."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import Config
from app.services.telegram_service import TelegramService
from app.services.download_service import DownloadService
from app.services.auth_service import AuthService
from app.utils.state_manager import StateManager
from app.utils.auth_dependencies import set_auth_service
from app.api.routes import router, set_services

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global service instances
telegram_service: TelegramService = None
download_service: DownloadService = None
state_manager: StateManager = None
auth_service: AuthService = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    global telegram_service, download_service, state_manager, auth_service

    # Startup
    try:
        # Ensure required directories exist
        Config.ensure_directories()

        # Initialize authentication service
        auth_service = AuthService()
        set_auth_service(auth_service)
        logger.info("Authentication service initialized")

        # Initialize state manager and load saved state
        state_manager = StateManager()
        state_manager.load_state()

        # Initialize Telegram service
        telegram_service = TelegramService()
        await telegram_service.connect()

        # Initialize download service
        download_service = DownloadService(telegram_service, state_manager)

        # Inject services into routes
        set_services(telegram_service, download_service, state_manager, auth_service)

        # Log saved state information
        if state_manager.get_status().get(
            "session_id"
        ) and state_manager.get_status().get("completed_downloads"):
            completed_count = len(
                state_manager.get_status().get("completed_downloads", {})
            )
            total_count = state_manager.get_status().get("total", 0)
            logger.info(
                f"Resumable session found: {completed_count}/{total_count} files completed"
            )

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

    uvicorn.run(app, host=Config.HOST, port=Config.PORT)
