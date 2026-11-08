import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "telefetchr-test-key-12345")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash")
os.environ.setdefault("ADMIN_PASSWORD", "telefetchr-test-password-xyz")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request, HTTPException
from fastapi.testclient import TestClient

from app.config import Config
from app.services.telegram_service import TelegramService
from app.services.download_service import DownloadService
from app.services.auth_service import AuthService
from app.utils.state_manager import StateManager
from app.utils.auth_dependencies import get_current_user
from telethon.tl.types import MessageMediaDocument, Document, DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio


def test_phone_number_formatting():
    """Verify PHONE_NUMBER does not duplicate leading + and handles unset properly."""
    from importlib import reload
    import app.config

    base_env = {
        "SECRET_KEY": "telefetchr-test-key-12345",
        "API_ID": "12345",
        "API_HASH": "test_hash",
        "ADMIN_PASSWORD": "telefetchr-test-password-xyz"
    }

    with patch.dict(os.environ, {**base_env, "PHONE_NUMBER": "+1234567890"}):
        reload(app.config)
        assert app.config.Config.PHONE_NUMBER == "+1234567890"

    with patch.dict(os.environ, {**base_env, "PHONE_NUMBER": "1234567890"}):
        reload(app.config)
        assert app.config.Config.PHONE_NUMBER == "+1234567890"

    with patch.dict(os.environ, {**base_env, "PHONE_NUMBER": ""}):
        reload(app.config)
        assert app.config.Config.PHONE_NUMBER is None


def test_resolve_peer():
    """Verify TelegramService._resolve_peer handles usernames and numeric IDs."""
    assert TelegramService._resolve_peer("-1001234567890") == -1001234567890
    assert TelegramService._resolve_peer("1234567890") == 1234567890
    assert TelegramService._resolve_peer("telefetchr_channel") == "telefetchr_channel"
    assert TelegramService._resolve_peer(-1001234567890) == -1001234567890


@pytest.mark.asyncio
async def test_create_parallel_senders_cleanup_on_error():
    """Verify that _create_parallel_senders disconnects any connected senders on error
    and restores _init_request.query."""
    tg = TelegramService()
    mock_client = MagicMock()
    mock_session = MagicMock()
    mock_session.dc_id = 2
    mock_session.auth_key = b"auth_key"
    mock_client.session = mock_session
    mock_client._get_dc = AsyncMock(return_value=MagicMock(ip_address="127.0.0.1", port=443, id=2))
    mock_client._connection = MagicMock()
    mock_client._log = MagicMock()
    mock_client._proxy = None
    mock_init_req = MagicMock()
    mock_init_req.query = "original_query"
    mock_client._init_request = mock_init_req
    tg.client = mock_client

    mock_sender1 = MagicMock()
    mock_sender1.connect = AsyncMock()
    mock_sender1.disconnect = AsyncMock()

    mock_sender2 = MagicMock()
    mock_sender2.connect = AsyncMock(side_effect=RuntimeError("Connection timeout on sender 2"))
    mock_sender2.disconnect = AsyncMock()

    with patch("telethon.network.MTProtoSender", side_effect=[mock_sender1, mock_sender2]):
        result = await tg._create_parallel_senders(dc_id=2, count=2)
        assert result is None
        # Sender 1 must have been disconnected cleanly
        mock_sender1.disconnect.assert_awaited_once()
        # Initial request query must have been preserved
        assert mock_client._init_request.query == "original_query"


@pytest.mark.asyncio
async def test_auth_cookie_and_scoped_query_token():
    """Verify get_current_user accepts HttpOnly cookie and restricts query token to /files/serve/."""
    auth = AuthService()
    valid_token = auth.create_access_token(data={"sub": "admin"})

    # 1. Cookie authentication succeeds for any path
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/disk",
        "headers": [(b"cookie", f"access_token={valid_token}".encode())],
        "query_string": b"",
    }
    req = Request(scope)
    user = await get_current_user(request=req)
    assert user == "admin"

    # 2. Query token is rejected for non-file-serve path
    scope_bad_query = {
        "type": "http",
        "method": "GET",
        "path": "/api/disk",
        "headers": [],
        "query_string": f"token={valid_token}".encode(),
    }
    req_bad = Request(scope_bad_query)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=req_bad)
    assert exc_info.value.status_code == 401

    # 3. Query token is accepted for /files/serve/
    scope_serve = {
        "type": "http",
        "method": "GET",
        "path": "/files/serve/movie.mp4",
        "headers": [],
        "query_string": f"token={valid_token}".encode(),
    }
    req_serve = Request(scope_serve)
    user = await get_current_user(request=req_serve)
    assert user == "admin"


@pytest.mark.asyncio
async def test_download_all_video_filter():
    """Verify download_all_files recognizes video files with DocumentAttributeVideo."""
    state = StateManager()
    state.download_status = state._initialize_status()

    # Build a video message
    doc = Document(
        id=123,
        access_hash=b"\x00" * 8,
        file_reference=b"",
        size=1048576,
        date="2026-01-01",
        mime_type="video/mp4",
        attributes=[DocumentAttributeFilename(file_name="movie.mp4"), DocumentAttributeVideo(duration=10, w=1920, h=1080)],
        dc_id=2
    )
    video_msg = MagicMock(id=1, media=MessageMediaDocument(document=doc))

    class MockTgService(TelegramService):
        async def iter_messages(self, channel_username, limit, filter_type=None):
            async def _gen():
                yield video_msg
            return _gen()

    service = DownloadService(MockTgService(), state)
    await service.download_all_files("test_channel", limit=10, filter_type="video")
    if service.collection_tasks:
        await asyncio.gather(*list(service.collection_tasks))

    queue = state.get_queue()
    assert len(queue) == 1
    assert queue[0]["message_id"] == 1
    service._stop_queue = True


@pytest.mark.asyncio
async def test_debug_endpoint_gated_in_production():
    """Verify debug_state raises 403 when debug routes are disabled."""
    from app.api.routes import debug_state
    with patch.object(Config, "ENABLE_DEBUG_ROUTES", False), \
         patch.object(Config, "LOG_LEVEL", "INFO"):
        with pytest.raises(HTTPException) as exc_info:
            await debug_state(current_user="admin")
        assert exc_info.value.status_code == 403
        assert "disabled in production" in exc_info.value.detail
