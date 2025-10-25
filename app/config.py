import os
from pathlib import Path

class Config:
    """Application configuration"""

    # Telegram API Configuration
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = f'+{os.getenv("PHONE_NUMBER")}'

    # Download Configuration
    MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
    SAVE_PATH = os.path.abspath('downloads')

    # Session Configuration
    SESSION_DIR = os.path.abspath('sessions')
    SESSION_FILE = os.path.join(SESSION_DIR, 'telegram_session')
    STATE_FILE = os.path.join(SESSION_DIR, 'download_state.json')

    # Server Configuration
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))

    # Authentication Configuration
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours

    # Default admin credentials (change these!)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # Will be hashed

    @classmethod
    def ensure_directories(cls):
        """Ensure required directories exist"""
        os.makedirs(cls.SESSION_DIR, exist_ok=True)
        os.makedirs(cls.SAVE_PATH, exist_ok=True)
