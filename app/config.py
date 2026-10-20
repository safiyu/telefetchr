import os
import secrets
import logging
from pathlib import Path
from typing import List

_config_logger = logging.getLogger(__name__)

# Known-weak values that must never be accepted. The first is the old
# placeholder; the second was shipped in docker-compose.yaml and is public.
_WEAK_SECRET_KEYS = {
    "your-secret-key-change-this-in-production",
    "telefetchr-secure-dev-key-12345",
}

# Default admin passwords that are brute-forceable or publicly documented and
# must be replaced. The last entry is the placeholder shipped in .env.example:
# a verbatim `cp .env.example .env` must not be able to boot the app with a
# password that is readable in this repository.
_WEAK_ADMIN_PASSWORDS = {
    "",
    "admin123",
    "admin",
    "password",
    "changeme",
    "telefetchr",
    "change-me-generate-a-strong-password",
}


def _load_or_create_secret_key() -> str:
    """Load the persisted JWT key, generating one on first run if needed.

    The key is stored in the sessions volume (sessions/.secret_key, mode 0600)
    so container restarts keep previously issued tokens valid, while the image
    and compose file never contain a real secret.
    """
    key_file = os.path.join(os.path.abspath("sessions"), ".secret_key")
    try:
        if os.path.exists(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                os.chmod(key_file, 0o600)
                return key
        key = secrets.token_urlsafe(48)
        Path(os.path.dirname(key_file)).mkdir(parents=True, exist_ok=True)
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        _config_logger.warning(
            "SECRET_KEY not set - generated a random key and persisted it to %s. "
            "Set SECRET_KEY explicitly to control this value.", key_file
        )
        return key
    except OSError as e:
        # Falling back to an ephemeral key means tokens die on restart, but
        # we must never silently reuse a weak key.
        _config_logger.error(
            f"Could not persist generated SECRET_KEY ({e}); using an ephemeral "
            "key - all sessions will be invalidated on restart. Set SECRET_KEY "
            "explicitly to avoid this."
        )
        return secrets.token_urlsafe(48)


class Config:
    """Application configuration"""
    VERSION = "2.0.1"
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

    # Authentication Configuration.
    # A missing SECRET_KEY is no longer fatal: a cryptographically random key
    # is generated and persisted to sessions/.secret_key (see
    # _load_or_create_secret_key). Known-weak values (including the one that
    # was previously shipped in docker-compose.yaml) are rejected outright,
    # because a public JWT secret lets anyone forge an admin token.
    _secret_key = os.getenv("SECRET_KEY")
    if _secret_key and _secret_key.strip() in _WEAK_SECRET_KEYS:
        raise ValueError(
            "SECRET_KEY is set to a known-insecure default. Generate a random "
            "key instead, e.g. python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\", or unset SECRET_KEY to let "
            "the app generate and persist one automatically."
        )
    SECRET_KEY = _secret_key.strip() if _secret_key and _secret_key.strip() else _load_or_create_secret_key()
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours

    # Admin credentials. The password MUST be provided via environment; the
    # previous hardcoded fallback (admin123) is rejected so a default deploy
    # can never boot with credentials that are publicly documented.
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    if not ADMIN_PASSWORD or ADMIN_PASSWORD in _WEAK_ADMIN_PASSWORDS:
        raise ValueError(
            "ADMIN_PASSWORD must be set to a strong, non-default value "
            "(e.g. python -c \"import secrets; print(secrets.token_urlsafe(16))\")."
        )
    
    # Trusted Subnets for Auth Bypass (comma-separated CIDRs)
    # Example: "192.168.1.0/24,10.0.0.0/8"
    TRUSTED_SUBNETS = [s.strip() for s in os.getenv("TRUSTED_SUBNETS", "").split(",") if s.strip()]

    # Safe reverse-proxy peers (comma-separated IPs/CIDRs). X-Forwarded-For is
    # only honored when the direct socket peer matches one of these, which
    # prevents subnet auth-bypass via spoofed proxy headers.
    #
    # Keep this list minimal: it should contain ONLY the reverse proxy that
    # sits directly in front of the app (e.g. a local nginx). Broad private
    # ranges (172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16) must NOT be trusted
    # by default, because Docker bridge / NAT peers fall in those ranges and
    # would let any client on the host spoof X-Forwarded-For. Add your exact
    # proxy IP/CIDR here (or via the TRUSTED_PROXIES env var) if you run
    # behind a non-localhost proxy.
    TRUSTED_PROXIES: List[str] = [s.strip() for s in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if s.strip()]

    @classmethod
    def ensure_directories(cls) -> None:
        """Ensure required directories exist and session material is private."""
        os.makedirs(cls.SESSION_DIR, exist_ok=True)
        os.makedirs(cls.SAVE_PATH, exist_ok=True)
        os.makedirs(cls.INCOMPLETE_PATH, exist_ok=True)
        cls.harden_session_permissions()

    @classmethod
    def harden_session_permissions(cls) -> None:
        """Restrict permissions on the sessions directory and Telegram session files.

        The Telegram .session file grants full access to the linked account, so
        it must never be world/group-readable. On a host-mounted volume the
        owner is controlled by the host user, so failures are logged, not fatal.
        """
        try:
            os.chmod(cls.SESSION_DIR, 0o700)
        except OSError as e:
            _config_logger.warning(
                f"Could not set sessions dir {cls.SESSION_DIR} to 0700: {e} - "
                "ensure the host directory is not group/world accessible."
            )
        for name in (
            os.path.basename(cls.SESSION_FILE) + ".session",
            os.path.basename(cls.SESSION_FILE) + ".session-journal",
        ):
            path = os.path.join(cls.SESSION_DIR, name)
            if os.path.exists(path):
                try:
                    os.chmod(path, 0o600)
                except OSError as e:
                    _config_logger.warning(f"Could not chmod {path} to 0600: {e}")
