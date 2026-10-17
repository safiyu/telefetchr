import os
from pathlib import Path
from typing import List

class Config:
    """Application configuration"""
    VERSION = "2.0.0"
    # Telegram API Configuration
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = f'+{os.getenv("PHONE_NUMBER")}'

    # Download Configuration
    MAX_CONCURRENT_DOWNLOADS = 1
    SAVE_PATH = os.path.abspath('downloads')
    # Staging directory for in-progress (sparse) downloads. Media servers
    # (Jellyfin, Plex, Tdarr) never see files until they are atomically
    # promoted out of this directory.
    INCOMPLETE_PATH: str = os.path.join(SAVE_PATH, '.incomplete')

    # Telethon Download Configuration
    DOWNLOAD_CHUNK_SIZE = int(os.getenv("DOWNLOAD_CHUNK_SIZE", "4194304"))  # 4MB chunks (faster)
    DOWNLOAD_REQUEST_DELAY = float(os.getenv("DOWNLOAD_REQUEST_DELAY", "0.01"))  # 10ms delay between chunk requests
    DOWNLOAD_WORKERS = int(os.getenv("DOWNLOAD_WORKERS", "8"))  # Number of parallel workers for downloading single file

    # Duplicate filename resolution strategy when the destination exists:
    #   skip      - skip download if destination exists with matching size
    #   overwrite - replace the existing destination file
    #   rename    - append an incremental counter: name (1).ext, name (2).ext, ...
    DUPLICATE_STRATEGY: str = os.getenv("DUPLICATE_STRATEGY", "skip").lower()

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

    # Safe reverse-proxy peers (comma-separated IPs/CIDRs). X-Forwarded-For is
    # only honored when the direct socket peer matches one of these, which
    # prevents subnet auth-bypass via spoofed proxy headers.
    TRUSTED_PROXIES: List[str] = [s.strip() for s in os.getenv("TRUSTED_PROXIES", "127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16").split(",") if s.strip()]

    @classmethod
    def ensure_directories(cls) -> None:
        """Ensure required directories exist"""
        os.makedirs(cls.SESSION_DIR, exist_ok=True)
        os.makedirs(cls.SAVE_PATH, exist_ok=True)
        os.makedirs(cls.INCOMPLETE_PATH, exist_ok=True)
