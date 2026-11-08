"""Tests for Features 1, 2, and 3:
1. Auto-Watch & Auto-Download Rules (Watched Channels)
2. Smart Subfolder Routing & Media Server Organization
3. Direct Telegram Message Link / t.me URL Paste
"""
import os
import sys
import json
from datetime import datetime
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "telefetchr-test-key-12345")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash")
os.environ.setdefault("ADMIN_PASSWORD", "telefetchr-test-password-xyz")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Config
from app.utils.path_utils import sanitize_subfolder, resolve_destination_dir
from app.utils.link_parser import parse_single_telegram_link, parse_telegram_links
from app.services.watch_service import WatchService
from app.services.download_service import DownloadService
from app.utils.state_manager import StateManager


# ==========================================
# FEATURE 2: Path Jail & Organization Tests
# ==========================================

def test_sanitize_subfolder():
    """Verify sanitize_subfolder strips leading/trailing slashes, resolves .. and blocks jailbreak."""
    assert sanitize_subfolder("") == ""
    assert sanitize_subfolder(None) == ""
    assert sanitize_subfolder("   ") == ""
    assert sanitize_subfolder("/movies/action/") == "movies/action"
    assert sanitize_subfolder("series/season 1") == "series/season 1"
    assert sanitize_subfolder(r"series\season 1") == "series/season 1"

    # Directory traversal attempts must be neutralised or raise ValueError
    with pytest.raises(ValueError):
        sanitize_subfolder("../../../etc/passwd")

    with pytest.raises(ValueError):
        sanitize_subfolder("movies/../../secret")


def test_resolve_destination_dir(tmp_path):
    """Verify resolve_destination_dir strictly keeps paths jailed inside root save dir."""
    save_root = str(tmp_path / "downloads")
    os.makedirs(save_root, exist_ok=True)

    # Simple root
    dest = resolve_destination_dir(base_path=save_root)
    assert dest == os.path.realpath(save_root)

    # Subfolder
    dest_sub = resolve_destination_dir(subfolder="Anime/Subbed", base_path=save_root)
    assert dest_sub == os.path.realpath(os.path.join(save_root, "Anime/Subbed"))
    assert os.path.isdir(dest_sub)

    # Organize by channel
    dest_chan = resolve_destination_dir(organize_by="channel", channel_name="AnimeChannel", base_path=save_root)
    assert dest_chan == os.path.realpath(os.path.join(save_root, "AnimeChannel"))

    # Organize by media type
    dest_type = resolve_destination_dir(organize_by="media_type", media_type="video", base_path=save_root)
    assert dest_type == os.path.realpath(os.path.join(save_root, "video"))

    # Combined subfolder and organize_by
    dest_combo = resolve_destination_dir(
        subfolder="Media",
        organize_by="media_type",
        media_type="audio",
        base_path=save_root
    )
    assert dest_combo == os.path.realpath(os.path.join(save_root, "Media", "audio"))

    # Path traversal attack must raise ValueError
    with pytest.raises(ValueError):
        resolve_destination_dir(subfolder="../../../../tmp", base_path=save_root)


# ==========================================
# FEATURE 3: Link Parsing Tests
# ==========================================

def test_parse_single_telegram_link():
    """Verify link parser correctly extracts channel identifier and message ID."""
    # Standard public t.me link
    res = parse_single_telegram_link("https://t.me/durov/123")
    assert res == ("durov", 123)

    # Public t.me link without https
    res = parse_single_telegram_link("t.me/telegram/999")
    assert res == ("telegram", 999)

    # Private t.me/c/ link -> converted to -100 prefix
    res = parse_single_telegram_link("https://t.me/c/1234567890/456")
    assert res == (-1001234567890, 456)

    # Custom tg://resolve link
    res = parse_single_telegram_link("tg://resolve?domain=mychannel&post=789")
    assert res == ("mychannel", 789)

    # Invalid links
    assert parse_single_telegram_link("https://google.com/123") is None
    assert parse_single_telegram_link("https://t.me/not_a_message") is None
    assert parse_single_telegram_link("random text") is None


def test_parse_telegram_links_bulk():
    """Verify multi-line and comma-separated link parsing."""
    raw_text = """
    https://t.me/chan1/10
    Some random chatter
    https://t.me/c/9876543210/20
    tg://resolve?domain=chan2&post=30
    """
    items = parse_telegram_links(raw_text)
    assert len(items) == 3
    assert items[0] == ("chan1", 10)
    assert items[1] == (-1009876543210, 20)
    assert items[2] == ("chan2", 30)


# ==========================================
# FEATURE 1: Watch Service & Filtering Tests
# ==========================================

@pytest.fixture
def watch_service(tmp_path):
    """Fixture to create WatchService in isolated temporary directory."""
    rules_path = str(tmp_path / "watch_rules.json")
    with patch.object(Config, "WATCH_RULES_FILE", rules_path):
        tg_mock = MagicMock()
        tg_mock.is_connected = MagicMock(return_value=True)
        tg_mock.client = MagicMock()
        dl_mock = MagicMock()
        ws = WatchService(tg_mock, dl_mock)
        yield ws


def test_watch_service_crud(watch_service):
    """Verify adding, updating, and deleting watch rules."""
    rule_data = {
        "channel": "@TestChannel",
        "subfolder": "Watched",
        "file_extensions": "mp4, mkv",
        "keywords": "1080p, Bluray",
        "min_size_mb": 10.0,
        "max_size_mb": 500.0,
        "organize_by": "media_type"
    }

    created = watch_service.add_rule(rule_data)
    rule_id = created["id"]
    assert rule_id.startswith("rule_")
    assert created["channel"] == "@TestChannel"
    assert created["enabled"] is True

    # Get rules
    rules = watch_service.get_rules()
    assert len(rules) == 1
    assert watch_service.get_rule(rule_id)["channel"] == "@TestChannel"

    # Update rule
    updated = watch_service.update_rule(rule_id, {"enabled": False, "subfolder": "Archived"})
    assert updated["enabled"] is False
    assert updated["subfolder"] == "Archived"

    # Verify persistence
    watch_service.load_rules()
    assert watch_service.get_rule(rule_id)["enabled"] is False

    # Delete rule
    deleted = watch_service.delete_rule(rule_id)
    assert deleted is True
    assert len(watch_service.get_rules()) == 0


def test_watch_service_matches_filter(watch_service):
    """Verify rule filtering on extensions, keywords, and min/max sizes."""
    rule = {
        "channel": "@test",
        "file_extensions": "mp4, mkv",
        "keywords": "1080p, x265",
        "min_size_mb": 50.0,
        "max_size_mb": 1000.0
    }

    # Helper mock message
    def make_msg(name, size_bytes, text=""):
        msg = MagicMock()
        msg.message = text
        msg.media = MagicMock()
        # Mock document
        from telethon.tl.types import MessageMediaDocument
        msg.media = MagicMock(spec=MessageMediaDocument)
        doc = MagicMock()
        doc.size = size_bytes
        msg.media.document = doc
        watch_service.download_service._get_file_name.return_value = name
        return msg

    # Match: .mp4, 100MB, contains "1080p"
    msg_valid = make_msg("Movie.1080p.mp4", 100 * 1024 * 1024, "New release")
    assert watch_service._matches_filter(msg_valid, rule) is True

    # Fail extension (.pdf)
    msg_pdf = make_msg("Doc.1080p.pdf", 100 * 1024 * 1024, "")
    assert watch_service._matches_filter(msg_pdf, rule) is False

    # Fail keyword (no 1080p or x265)
    msg_no_kw = make_msg("Movie.720p.mp4", 100 * 1024 * 1024, "standard def")
    assert watch_service._matches_filter(msg_no_kw, rule) is False

    # Fail min size (too small: 20MB < 50MB)
    msg_small = make_msg("Sample.1080p.mp4", 20 * 1024 * 1024, "")
    assert watch_service._matches_filter(msg_small, rule) is False

    # Fail max size (too large: 2000MB > 1000MB)
    msg_large = make_msg("Huge.1080p.mp4", 2000 * 1024 * 1024, "")
    assert watch_service._matches_filter(msg_large, rule) is False


@pytest.mark.asyncio
async def test_watch_service_on_new_message(watch_service):
    """Verify WatchService triggers download when incoming message matches an active rule."""
    rule = watch_service.add_rule({
        "channel": "@auto_channel",
        "file_extensions": "mp4",
        "enabled": True
    })

    event = MagicMock()
    event.chat_id = 12345
    chat_mock = MagicMock()
    chat_mock.username = "auto_channel"
    event.get_chat = AsyncMock(return_value=chat_mock)

    message_mock = MagicMock()
    message_mock.id = 555
    media_mock = MagicMock()
    media_mock.document.size = 10 * 1024 * 1024
    message_mock.media = media_mock
    event.message = message_mock

    watch_service.download_service._get_file_name.return_value = "video.mp4"
    watch_service.download_service.download_single = AsyncMock()

    await watch_service._on_new_message(event)

    watch_service.download_service.download_single.assert_awaited_once_with(
        "@auto_channel",
        555,
        subfolder="",
        organize_by=None
    )


# ==========================================
# DownloadService & Webhook Tests
# ==========================================

@pytest.mark.asyncio
async def test_download_service_quick_fetch_links(tmp_path):
    """Verify quick_fetch_links parses links and schedules downloads."""
    tg_mock = MagicMock()
    state_mock = MagicMock(spec=StateManager)
    state_mock.is_file_active_or_queued.return_value = False

    dl_service = DownloadService(tg_mock, state_mock)
    dl_service.download_single = AsyncMock()

    links_text = "https://t.me/testchannel/101\nhttps://t.me/c/1122334455/202"
    result = await dl_service.quick_fetch_links(links_text, subfolder="Quick", organize_by="channel")

    assert result["status"] == "success"
    assert len(result["queued"]) == 2
    assert dl_service.download_single.await_count == 2


@pytest.mark.asyncio
async def test_completion_webhook_notification():
    """Verify completion webhook is dispatched when COMPLETION_WEBHOOK_URL is set."""
    with patch.object(Config, "COMPLETION_WEBHOOK_URL", "https://mock-webhook.local/test"):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            tg_mock = MagicMock()
            state_mock = MagicMock(spec=StateManager)
            dl_service = DownloadService(tg_mock, state_mock)

            await dl_service._send_completion_webhook({
                "event": "file_completed",
                "file_name": "movie.mkv",
                "file_path": "/downloads/movie.mkv",
                "file_size": 1048576,
                "channel": "@testchan",
                "message_id": 42
            })

            mock_post.assert_awaited_once()
            called_url = mock_post.await_args[0][0]
            assert called_url == "https://mock-webhook.local/test"
            payload = mock_post.await_args[1]["json"]
            assert payload["event"] == "file_completed"
            assert payload["file_name"] == "movie.mkv"
            assert payload["channel"] == "@testchan"


# ==========================================
# API Endpoint Integration Tests
# ==========================================

import httpx
from fastapi import FastAPI
from app.api.routes import router, set_services
from app.utils.auth_dependencies import get_current_user


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    return app


@pytest.mark.asyncio
async def test_watch_rules_api_endpoints(test_app, tmp_path):
    """Verify REST API endpoints for Watch Rules."""
    tg_mock = MagicMock()
    tg_mock.is_connected = AsyncMock(return_value=True)
    dl_mock = MagicMock()
    state_mock = MagicMock()
    auth_mock = MagicMock()
    rules_path = str(tmp_path / "api_watch_rules.json")

    with patch.object(Config, "WATCH_RULES_FILE", rules_path):
        ws = WatchService(tg_mock, dl_mock)
        set_services(tg_mock, dl_mock, state_mock, auth_mock, ws)
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # GET initially empty
            res = await client.get("/api/watch-rules")
            assert res.status_code == 200
            assert res.json() == {"rules": []}

            # POST create rule
            create_payload = {
                "channel": "@TestChan",
                "subfolder": "Movies",
                "file_extensions": "mkv, mp4",
                "keywords": "1080p",
                "min_size_mb": 50,
                "max_size_mb": 5000,
                "organize_by": "channel",
                "enabled": True
            }
            res = await client.post("/api/watch-rules", json=create_payload)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            rule_id = data["rule"]["id"]

            # GET rules has 1 rule
            res = await client.get("/api/watch-rules")
            assert len(res.json()["rules"]) == 1

            # PUT update rule
            res = await client.put(f"/api/watch-rules/{rule_id}", json={"enabled": False})
            assert res.status_code == 200
            assert res.json()["rule"]["enabled"] is False

            # POST scan rule
            ws.scan_rule = AsyncMock(return_value={"status": "success", "message": "Scanned 10 items", "queued_count": 2})
            res = await client.post(f"/api/watch-rules/{rule_id}/scan?limit=25")
            assert res.status_code == 200
            assert res.json()["queued_count"] == 2

            # DELETE rule
            res = await client.delete(f"/api/watch-rules/{rule_id}")
            assert res.status_code == 200
            assert res.json()["status"] == "success"

            # Verify deletion
            res = await client.get("/api/watch-rules")
            assert len(res.json()["rules"]) == 0


@pytest.mark.asyncio
async def test_quick_fetch_api_endpoint(test_app):
    """Verify POST /files/quick-fetch endpoint."""
    tg_mock = MagicMock()
    tg_mock.is_connected = AsyncMock(return_value=True)
    dl_mock = MagicMock()
    dl_mock.quick_fetch_links = AsyncMock(return_value={
        "status": "success",
        "message": "Queued 1 link(s)",
        "queued_count": 1,
        "skipped_count": 0,
        "queued": ["@test/123"],
        "failed": []
    })
    state_mock = MagicMock()
    auth_mock = MagicMock()

    set_services(tg_mock, dl_mock, state_mock, auth_mock)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/files/quick-fetch", json={
            "links": "https://t.me/test/123",
            "subfolder": "Direct",
            "organize_by": "media_type"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["queued_count"] == 1
        dl_mock.quick_fetch_links.assert_awaited_once_with(
            "https://t.me/test/123",
            subfolder="Direct",
            organize_by="media_type"
        )


@pytest.mark.asyncio
async def test_serve_file_subfolder_and_jail(test_app, tmp_path):
    """Verify serve_file successfully serves files from subfolders and blocks traversal."""
    save_dir = tmp_path / "downloads"
    sub_dir = save_dir / "Movies" / "SciFi"
    sub_dir.mkdir(parents=True, exist_ok=True)
    file_path = sub_dir / "test_movie.mp4"
    file_path.write_bytes(b"sample video bytes")

    with patch.object(Config, "SAVE_PATH", str(save_dir)):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Serving from subfolder succeeds
            res = await client.get("/files/serve/Movies/SciFi/test_movie.mp4")
            assert res.status_code == 200
            assert res.content == b"sample video bytes"
            assert "test_movie.mp4" in res.headers.get("content-disposition", "")

            # Traversal attempts return 403 or 404, never contents
            res_bad = await client.get("/files/serve/Movies/SciFi/..%2f..%2f..%2fetc/passwd")
            assert res_bad.status_code in (403, 404)
            assert b"root:" not in res_bad.content

            # Symlink escape inside subfolder returns 403
            outside_file = tmp_path / "outside.txt"
            outside_file.write_bytes(b"sensitive host data")
            symlink_path = sub_dir / "symlink_escape"
            symlink_path.symlink_to(outside_file)
            res_sym = await client.get("/files/serve/Movies/SciFi/symlink_escape")
            assert res_sym.status_code == 403

            # Missing file returns 404
            res_missing = await client.get("/files/serve/Movies/SciFi/nonexistent.mp4")
            assert res_missing.status_code == 404


@pytest.mark.asyncio
async def test_retry_preserves_subfolder_and_organize():
    """Verify retrying a cancelled/partial download preserves subfolder and organize_by."""
    tg_mock = MagicMock()
    state = StateManager()
    state.download_status["cancelled_files"]["single_777"] = {
        "id": "single_777",
        "message_id": 777,
        "channel": "@TestChan",
        "name": "sample.mp4",
        "progress": 500,
        "total": 1000,
        "percentage": 50,
        "subfolder": "Movies/2026",
        "organize_by": "channel"
    }

    dl_service = DownloadService(tg_mock, state)
    dl_service.start_queue_processor = MagicMock()

    res = await dl_service.retry_download("single_777")
    assert res["status"] == "success"

    queue = state.get_queue()
    assert len(queue) == 1
    item = queue[0]
    assert item["id"] == "single_777"
    assert item["subfolder"] == "Movies/2026"
    assert item["organize_by"] == "channel"


@pytest.mark.asyncio
async def test_quick_fetch_link_limit():
    """Verify quick_fetch_links caps max links at 100."""
    tg_mock = MagicMock()
    state_mock = MagicMock(spec=StateManager)
    dl_service = DownloadService(tg_mock, state_mock)

    many_links = [f"https://t.me/channel/{i}" for i in range(101)]
    res = await dl_service.quick_fetch_links(many_links)
    assert res["status"] == "error"
    assert "Too many links" in res["message"]


@pytest.mark.asyncio
async def test_subfolder_traversal_endpoint_validation(test_app):
    """Verify upfront path traversal in subfolder yields 400 across endpoints."""
    tg_mock = MagicMock()
    tg_mock.is_connected = AsyncMock(return_value=True)
    dl_mock = MagicMock()
    state_mock = MagicMock()
    auth_mock = MagicMock()
    ws_mock = MagicMock()

    set_services(tg_mock, dl_mock, state_mock, auth_mock, ws_mock)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # download-selected
        res = await client.post("/files/download-selected", json={
            "channel_username": "@test",
            "message_ids": [1],
            "subfolder": "../../etc"
        })
        assert res.status_code == 400

        # download-all
        res = await client.post("/files/download-all", json={
            "channel_username": "@test",
            "limit": 10,
            "subfolder": "folder/../../../etc"
        })
        assert res.status_code == 400

        # download single
        res = await client.post("/files/download/123?channel_username=@test&subfolder=../traversal")
        assert res.status_code == 400

        # quick-fetch
        res = await client.post("/files/quick-fetch", json={
            "links": "https://t.me/test/1",
            "subfolder": "../../escape"
        })
        assert res.status_code == 400

        # watch-rules
        res = await client.post("/api/watch-rules", json={
            "channel": "@test",
            "subfolder": "../../escape"
        })
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_folders_api_list_and_create(test_app, tmp_path, monkeypatch):
    """Test GET /api/folders and POST /api/folders."""
    from app.config import Config
    resolved_tmp = tmp_path.resolve()
    monkeypatch.setattr(Config, "SAVE_PATH", str(resolved_tmp))
    monkeypatch.setattr("app.api.routes.Config.SAVE_PATH", str(resolved_tmp))

    tg_mock = MagicMock()
    dl_mock = MagicMock()
    state_mock = MagicMock()
    auth_mock = MagicMock()
    ws_mock = MagicMock()

    set_services(tg_mock, dl_mock, state_mock, auth_mock, ws_mock)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Initially empty
        res = await client.get("/api/folders")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["folders"] == []

        # Create valid subfolder
        res = await client.post("/api/folders", json={"name": "Series/Season 1"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["folder"] == "Series/Season 1"
        assert (resolved_tmp / "Series" / "Season 1").is_dir()

        # List folders should now return Series and Series/Season 1
        res = await client.get("/api/folders")
        assert res.status_code == 200
        folders = res.json()["folders"]
        assert "Series" in folders
        assert "Series/Season 1" in folders

        # Reject empty name
        res = await client.post("/api/folders", json={"name": "   "})
        assert res.status_code == 400

        # Reject path traversal
        res = await client.post("/api/folders", json={"name": "../escaped_dir"})
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_telegram_service_multi_extension_filter():
    """Verify telegram_service multi-extension filtering matches comma-separated extensions."""
    from app.services.telegram_service import TelegramService
    from telethon.tl.types import MessageMediaDocument, Document, DocumentAttributeFilename
    from unittest.mock import MagicMock

    tg = TelegramService()
    tg.is_connected = MagicMock(return_value=True)

    def make_doc_msg(msg_id, filename):
        msg = MagicMock()
        msg.id = msg_id
        msg.date = datetime.now()
        attr = DocumentAttributeFilename(file_name=filename)
        doc = MagicMock(spec=Document)
        doc.size = 1024
        doc.attributes = [attr]
        media = MessageMediaDocument(document=doc, nopremium=False, spoiler=False, ttl_seconds=None)
        msg.media = media
        return msg

    msg_mp4 = make_doc_msg(1, "video1.mp4")
    msg_mkv = make_doc_msg(2, "video2.mkv")
    msg_pdf = make_doc_msg(3, "doc.pdf")

    async def mock_iter(channel, limit=None, **kwargs):
        for m in [msg_mp4, msg_mkv, msg_pdf]:
            yield m

    tg.client = MagicMock()
    tg.client.iter_messages = mock_iter

    # Test filtering video files with comma-separated list
    files = await tg.list_files("@test", limit=10, file_extension="mp4, mkv")
    names = [f.file_name for f in files]
    assert "video1.mp4" in names
    assert "video2.mkv" in names
    assert "doc.pdf" not in names



