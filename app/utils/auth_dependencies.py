import ipaddress
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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


def _matches_proxy_list(ip_str: str, candidates) -> bool:
    """Return True if ip_str matches any entry in candidates (IP or CIDR)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            if "/" in candidate:
                if ip in ipaddress.ip_network(candidate, strict=False):
                    return True
            elif ipaddress.ip_address(candidate) == ip:
                return True
        except ValueError:
            logger.warning(f"Invalid trusted proxy configuration: {candidate}")
            continue
    return False


def is_trusted_ip(ip_str: str) -> bool:
    """Check if client IP matches configured TRUSTED_SUBNETS."""
    if not Config.TRUSTED_SUBNETS:
        return False

    try:
        # Handle cases where IP might be ::1 or 127.0.0.1 mapped
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.warning(f"Invalid client IP address: {ip_str}")
        return False

    for subnet in Config.TRUSTED_SUBNETS:
        try:
            if ip in ipaddress.ip_network(subnet, strict=False):
                return True
        except ValueError:
            logger.warning(f"Invalid trusted subnet configuration: {subnet}")
            continue

    return False


def _is_trusted_proxy_peer(ip_str: str) -> bool:
    """Check whether a direct socket peer is a trusted reverse proxy."""
    if not ip_str:
        return False
    if not Config.TRUSTED_PROXIES:
        return False
    return _matches_proxy_list(ip_str, Config.TRUSTED_PROXIES)


def get_client_ip(request: Request) -> str:
    """Determine client IP by validating whether direct socket peer is in TRUSTED_PROXIES.

    - If the direct socket peer is a trusted proxy, the client address is read
      from the X-Forwarded-For chain (right-most untrusted hop) or X-Real-IP.
    - If the peer is NOT a trusted proxy, all forwarding headers are ignored
      and the peer IP is used as-is (anti-spoofing).
    """
    client = request.scope.get("client")
    if client:
        peer_ip = client[0]
    elif request.client:
        peer_ip = getattr(request.client, "host", None)
    else:
        peer_ip = None
    if not peer_ip:
        return "unknown"

    if _is_trusted_proxy_peer(peer_ip):
        # Direct peer is a known proxy: trust the forwarded chain.
        # Walk X-Forwarded-For right-to-left and pick the first untrusted hop
        # (the real client), which defeats proxies chaining their own IPs.
        forwarded = request.headers.get("X-Forwarded-For", "")
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for hop in reversed(hops):
            if not _matches_proxy_list(hop, Config.TRUSTED_PROXIES):
                return hop
        if hops:
            return hops[-1]

        # No forwarded chain: fall back to X-Real-IP, then peer.
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
        return peer_ip

    # Untrusted peer: ignore all proxy headers entirely.
    return peer_ip


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> str:
    """Authenticate user via trusted subnet bypass or Bearer JWT token."""
    # Check for trusted IP bypass (spoofing-safe client resolution)
    client_ip = get_client_ip(request)
    if is_trusted_ip(client_ip):
        return Config.ADMIN_USERNAME

    # Standard token validation
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await auth_service.get_current_user(credentials)
