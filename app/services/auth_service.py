from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import Config
from app.models.schemas import TokenData

# HTTP Bearer token security
security = HTTPBearer()


class AuthService:
    """Service for handling authentication"""

    def __init__(self):
        # Hash the admin password on initialization
        self.admin_password_hash = self.get_password_hash(Config.ADMIN_PASSWORD)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its bcrypt hash"""
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8")
            )
        except (ValueError, TypeError):
            return False

    def get_password_hash(self, password: str) -> str:
        """Hash a password using native bcrypt"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def authenticate_user(self, username: str, password: str) -> bool:
        """Authenticate a user"""
        if username != Config.ADMIN_USERNAME:
            return False
        if not self.verify_password(password, self.admin_password_hash):
            return False
        return True

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, Config.SECRET_KEY, algorithm=Config.ALGORITHM)
        return encoded_jwt

    def verify_token(self, token: str) -> TokenData:
        """Verify a JWT token and return token data"""
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

        try:
            payload = jwt.decode(token, Config.SECRET_KEY, algorithms=[Config.ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception
            token_data = TokenData(username=username)
        except JWTError:
            raise credentials_exception

        return token_data

    async def get_current_user(self, credentials: HTTPAuthorizationCredentials) -> str:
        """Get current user from token"""
        token = credentials.credentials
        token_data = self.verify_token(token)

        if token_data.username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials"
            )

        return token_data.username
