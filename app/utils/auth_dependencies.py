from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.auth_service import AuthService

# HTTP Bearer security scheme
security = HTTPBearer()

# Global auth service instance (will be injected)
auth_service: AuthService = None


def set_auth_service(service: AuthService):
    """Set the global auth service instance"""
    global auth_service
    auth_service = service


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Dependency to get current authenticated user"""
    return await auth_service.get_current_user(credentials)
