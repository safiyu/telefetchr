from fastapi import Depends, Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import ipaddress
import logging

from app.config import Config
from app.services.auth_service import AuthService

# HTTP Bearer security scheme - allow optional for bypass
security = HTTPBearer(auto_error=False)

# Global auth service instance (will be injected)
auth_service: AuthService = None
logger = logging.getLogger(__name__)


def set_auth_service(service: AuthService):
    """Set the global auth service instance"""
    global auth_service
    auth_service = service


def is_trusted_ip(ip_str: str) -> bool:
    """Check if an IP address belongs to trusted subnets"""
    if not Config.TRUSTED_SUBNETS:
        return False
        
    try:
        # Handle cases where IP might be ::1 or 127.0.0.1 mapped
        ip = ipaddress.ip_address(ip_str)
        
        for subnet in Config.TRUSTED_SUBNETS:
            try:
                if ip in ipaddress.ip_network(subnet, strict=False):
                    return True
            except ValueError:
                logger.warning(f"Invalid trusted subnet configuration: {subnet}")
                continue
                
        return False
    except ValueError:
        logger.warning(f"Invalid client IP address: {ip_str}")
        return False


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """Dependency to get current authenticated user or bypass if local"""
    # Check for trusted IP bypass
    client_ip = request.client.host
    if is_trusted_ip(client_ip):
        logger.info(f"Auth bypass - TRUSTED IP: {client_ip}") 
        return Config.ADMIN_USERNAME
    
    logger.info(f"Auth required - UNTRUSTED IP: {client_ip}")

    # Standard token validation
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    return await auth_service.get_current_user(credentials)
