import os
from pathlib import Path

class Config:
    """Application configuration"""
    VERSION = "1.3.0"
    # Telegram API Configuration
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = f'+{os.getenv("PHONE_NUMBER")}'

    # Download Configuration
    MAX_CONCURRENT_DOWNLOADS = 1
    SAVE_PATH = os.path.abspath('downloads')

    # Telethon Download Configuration
    DOWNLOAD_CHUNK_SIZE = int(os.getenv("DOWNLOAD_CHUNK_SIZE", "4194304"))  # 4MB chunks (faster)
    DOWNLOAD_REQUEST_DELAY = float(os.getenv("DOWNLOAD_REQUEST_DELAY", "0.01"))  # 10ms delay between chunk requests
    DOWNLOAD_WORKERS = int(os.getenv("DOWNLOAD_WORKERS", "8"))  # Number of parallel workers for downloading single file

    # Session Configuration
    SESSION_DIR = os.path.abspath('sessions')
    SESSION_FILE = os.path.join(SESSION_DIR, 'telegram_session')
    STATE_FILE = os.path.join(SESSION_DIR, 'download_state.json')

    # Server Configuration
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "9868"))

    # Authentication Configuration
    _secret_key = os.getenv("SECRET_KEY")
    if not _secret_key or _secret_key == "your-secret-key-change-this-in-production":
        raise ValueError("SECRET_KEY environment variable must be set to a secure value!")
    SECRET_KEY = _secret_key
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours

    # Default admin credentials (change these!)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # Will be hashed
    
    # Trusted Subnets for Auth Bypass (comma-separated CIDRs)
    # Example: "192.168.1.0/24,10.0.0.0/8"
    TRUSTED_SUBNETS = [s.strip() for s in os.getenv("TRUSTED_SUBNETS", "").split(",") if s.strip()]

    @classmethod
    def ensure_directories(cls):
        """Ensure required directories exist"""
        os.makedirs(cls.SESSION_DIR, exist_ok=True)
        os.makedirs(cls.SAVE_PATH, exist_ok=True)
