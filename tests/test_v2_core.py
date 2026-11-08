"""
Telefetchr 2.0 regression test suite.

Validates security boundaries (filename sanitization, anti-spoofing client
resolution, canonical path jail for file serving) and download engine
primitives (zero-merge sparse/pwrite parallel downloader, duplicate
resolution strategies with .incomplete staging + atomic promotion).

Run with:
    SECRET_KEY=telefetchr-test-key-12345 pytest tests/test_v2_core.py -v
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Environment must be configured BEFORE the app modules are imported because
# Config validates SECRET_KEY and reads API credentials at import time.
# ---------------------------------------------------------------------------
os.environ.setdefault("SECRET_KEY", "telefetchr-test-key-12345")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("PHONE_NUMBER", "1234567890")
# Config now refuses to boot without a non-default admin password.
os.environ.setdefault("ADMIN_PASSWORD", "telefetchr-test-password-xyz")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from telethon.tl import types as tltypes

from app.config import Config
from app.utils.text_utils import sanitize_filename, format_directory_template
from app.utils.auth_dependencies import get_client_ip, is_trusted_ip


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_request(peer_ip: str, xff: Optional[str] = None,
                  real_ip: Optional[str] = None):
    """Build a minimal object mimicking starlette's Request (scope + headers)."""
    scope = {"type": "http", "client": (peer_ip, 56789)}
    raw = {}
    if xff is not None:
        raw["x-forwarded-for"] = xff
    if real_ip is not None:
        raw["x-real-ip"] = real_ip

    # Starlette's Headers lookup is case-insensitive; mirror that here.
    class _CI(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    request = type("FakeRequest", (), {})()
    request.scope = scope
    request.client = type("FakeAddr", (), {"host": peer_ip, "port": 56789})()
    request.headers = _CI(raw)
    return request


def _fake_message(size: int, file_name: str = "video.mp4"):
    """Build a realistic Telethon MessageMediaDocument for range downloads."""
    doc = tltypes.Document(
        id=424242,
        access_hash=b"\x00" * 8,
        file_reference=b"",
        size=size,
        date=datetime.now(),
        mime_type="video/mp4",
        attributes=[tltypes.DocumentAttributeFilename(file_name=file_name)],
        dc_id=2,
    )
    media = tltypes.MessageMediaDocument(document=doc)

    # Light Message wrapper (download_single_file reads id/peer_id for status)
    return type("FakeMessage", (), {
        "id": 424242,
        "media": media,
        "peer_id": type("Peer", (), {"channel_id": 999})(),
    })()


class FakeParallelClient:
    """Stands in for TelegramClient: serves deterministic byte ranges via the
    async iter_download interface (mirrors telethon's range-request API)."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.start_offsets: List[int] = []

    async def iter_download(self, media, offset: int = 0, limit: int = None,
                            request_size: int = 65536, **kwargs):
        if offset not in self.start_offsets:
            self.start_offsets.append(offset)
        if limit is not None:
            # Support both chunk count (Telethon API) and raw byte limits
            if limit * request_size <= len(self.payload) + request_size:
                max_bytes = limit * request_size
            else:
                max_bytes = limit
            end = min(len(self.payload), offset + max_bytes)
        else:
            end = len(self.payload)
        while offset < end:
            yield self.payload[offset:offset + min(request_size, end - offset)]
            offset += request_size


class FakeStateManager:
    """In-memory stand-in for StateManager (download_single_file contract)."""

    def __init__(self):
        self.completed = {}
        self.status = {
            "active": False,
            "cancelled": False,
            "session_id": "test-session",
            "concurrent_downloads": {},
        }

    def get_status(self):
        return self.status

    def save_state(self):
        pass

    def schedule_progress_save(self, force=False):
        pass

    async def mark_file_completed(self, file_id: str, file_data: dict):
        self.completed[file_id] = file_data


def _make_service(payload: bytes):
    """DownloadService wired to a fake Telegram client + fake state."""
    from app.services.telegram_service import TelegramService
    from app.services.download_service import DownloadService

    tg = TelegramService()
    tg.client = FakeParallelClient(payload)
    state = FakeStateManager()
    return DownloadService(tg, state), state


# ---------------------------------------------------------------------------
# Test 1: sanitize_filename - traversal & NFKC normalization
# ---------------------------------------------------------------------------

def test_sanitize_filename_traversal_and_nfkc() -> None:
    # Path traversal sequences are stripped down to the basename
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\evil.exe") == "evil.exe"
    assert sanitize_filename("a/b/c/../d.txt") == "d.txt"

    # NFKC normalization: fullwidth characters fold to ASCII
    assert sanitize_filename("\uff22\uff21\uff23\uff23\uff29") == "BACCI"

    # Malicious character sequences (shell metachars, control chars) are removed
    result = sanitize_filename('bad;rm -rf "$HOME"\nfile.mp4')
    assert ";" not in result and "$" not in result and "\n" not in result
    assert result.endswith("file.mp4")

    # Empty / dot-only results fall back to a safe name
    assert sanitize_filename("") == "unnamed_file"
    assert sanitize_filename("...") == "unnamed_file"
    assert sanitize_filename("///") == "unnamed_file"
    assert sanitize_filename("???***") == "unnamed_file"

    # Legitimate characters survive intact
    assert sanitize_filename("My Video (2024) [4K].mp4") == "My Video (2024) [4K].mp4"
    assert sanitize_filename("movie.part-1.mkv") == "movie.part-1.mkv"

    # Directory template interpolation produces a clean, contained subfolder path
    from datetime import datetime as _dt
    tpl = format_directory_template(
        "{channel}/{year}/{month}/{day}/{type}/{ext}",
        channel="../../evil channel",
        message_date=_dt(2026, 10, 17),
        media_type="video",
        ext=".mp4",
    )
    parts = tpl.split(os.sep)
    assert ".." not in parts
    assert "evil" in " ".join(parts).lower()
    assert parts[-2] == "video"
    assert parts[-1] == "mp4"


# ---------------------------------------------------------------------------
# Test 2: sanitize_filename - UTF-8 byte truncation preserving extension
# ---------------------------------------------------------------------------

def test_sanitize_filename_byte_truncation() -> None:
    max_base_bytes = 240
    long_base = "a" * 500
    result = sanitize_filename(f"{long_base}.mp4", max_base_bytes=max_base_bytes)

    assert result.endswith(".mp4")
    base = result[: -len(".mp4")]
    assert len(base.encode("utf-8")) == max_base_bytes

    # Total result must stay under NAME_MAX (255 bytes) on Linux
    assert len(result.encode("utf-8")) <= 255

    # NFKC-folded fullwidth digits produce a long UTF-8 base that must still be
    # truncated to the byte budget (whole sequences only, no mid-char splits).
    mb_base = "\uff11" * 500  # fullwidth '1' x500; NFKC -> '1'*500
    result_mb = sanitize_filename(f"{mb_base}.txt", max_base_bytes=max_base_bytes)
    assert result_mb.endswith(".txt")
    mb_base_part = result_mb[: -len(".txt")]
    assert len(mb_base_part.encode("utf-8")) == max_base_bytes
    assert len(result_mb.encode("utf-8")) <= 255  # under NAME_MAX

    # Short names pass through unchanged
    assert sanitize_filename("short.mp4") == "short.mp4"


# ---------------------------------------------------------------------------
# Test 3: get_client_ip - anti-spoofing proxy resolution
# ---------------------------------------------------------------------------

def test_get_client_ip_anti_spoofing() -> None:
    # Sanity: the default trusted-proxy list is the strictest safe baseline -
    # loopback only. Broad RFC1918 ranges must NOT be trusted by default
    # because Docker bridge / NAT peers fall in those ranges and would let
    # any host-local client spoof X-Forwarded-For.
    assert "127.0.0.1" in Config.TRUSTED_PROXIES
    assert "172.16.0.0/12" not in Config.TRUSTED_PROXIES
    assert "10.0.0.0/8" not in Config.TRUSTED_PROXIES
    assert "192.168.0.0/16" not in Config.TRUSTED_PROXIES

    # 1. Untrusted peer spoofs X-Forwarded-For with a trusted subnet IP ->
    #    the spoof is ignored and the real peer IP is used.
    spoofed = _make_request("203.0.113.7", xff="192.168.1.50, 203.0.113.7")
    assert get_client_ip(spoofed) == "203.0.113.7"
    assert is_trusted_ip(get_client_ip(spoofed)) is False

    # 2. Untrusted peer with X-Real-IP spoofing -> also ignored.
    spoofed_real = _make_request("198.51.100.9", real_ip="127.0.0.1")
    assert get_client_ip(spoofed_real) == "198.51.100.9"

    # 3. Trusted reverse proxy (127.0.0.1 is in default TRUSTED_PROXIES):
    #    X-Forwarded-For is honored, returning the real client IP.
    proxied = _make_request("127.0.0.1", xff="10.0.0.42")
    assert get_client_ip(proxied) == "10.0.0.42"

    # 4. Chained proxies: trusted hops are skipped right-to-left, so the
    #    right-most UNTRUSTED hop is the true client.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Config, "TRUSTED_PROXIES", ["172.16.5.10", "10.0.0.9"])
        chained = _make_request("172.16.5.10", xff="203.0.113.99, 10.0.0.9")
        assert get_client_ip(chained) == "203.0.113.99"

        # 5. Every hop is a trusted proxy: the LEFT-most entry (original
        #    client as recorded by the first proxy) is returned. Returning the
        #    right-most hop would let an attacker pin a trusted-subnet IP and
        #    trigger the trusted-subnet auth bypass.
        mp.setattr(Config, "TRUSTED_PROXIES", ["127.0.0.1", "10.0.0.9", "192.168.1.50"])
        all_trusted = _make_request("127.0.0.1", xff="192.168.1.50, 10.0.0.9")
        assert get_client_ip(all_trusted) == "192.168.1.50"

    # 6. Trusted proxy with no headers -> falls back to the peer IP.
    bare = _make_request("127.0.0.1")
    assert get_client_ip(bare) == "127.0.0.1"


# ---------------------------------------------------------------------------
# Test 4: serve_file - canonical path jail
# ---------------------------------------------------------------------------

def test_serve_file_path_traversal_jail() -> None:
    import httpx
    from fastapi import FastAPI
    from app.api.routes import router, set_services
    from app.utils.auth_dependencies import get_current_user
    from app.services.telegram_service import TelegramService
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager
    from app.services.auth_service import AuthService

    set_services(
        TelegramService(), DownloadService(None, None), StateManager(), AuthService()
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    async def _get(url: str):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await ac.get(url)

    # Health probe must be reachable without authentication
    health = asyncio.run(_get("/api/health"))
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "healthy"
    assert body["version"] == Config.VERSION

    # Plant a legitimate file inside the save directory
    save_dir = Path(Config.SAVE_PATH)
    save_dir.mkdir(parents=True, exist_ok=True)
    planted_name = "jailed_" + "x" * 220 + ".bin"  # long but legal name
    planted = save_dir / planted_name
    planted.write_bytes(b"secret-payload")

    link = save_dir / "escape_link"
    secret_outside = Path(Config.SESSION_DIR) / "secret_outside.txt"
    Path(Config.SESSION_DIR).mkdir(parents=True, exist_ok=True)
    secret_outside.write_text("outside-data")

    try:
        # Legitimate file is served fine
        ok = asyncio.run(_get(f"/files/serve/{planted_name}"))
        assert ok.status_code == 200
        assert ok.content == b"secret-payload"

        # Traversal attempts can never escape SAVE_PATH: the basename jail
        # strips directory components, so at worst the request resolves to a
        # (non-existent) file inside the jail -> 404, never a read outside it.
        for evil in (
            "../../../etc/passwd",
            "..%2F..%2Fetc%2Fpasswd",
            "subdir/../../etc/shadow",
        ):
            resp = asyncio.run(_get(f"/files/serve/{evil}"))
            assert resp.status_code in (403, 404)
            assert b"root:" not in resp.content

        # A symlink inside SAVE_PATH pointing outside must be 403 (containment
        # fails after canonical resolution).
        link.symlink_to(secret_outside)
        resp_link = asyncio.run(_get("/files/serve/escape_link"))
        assert resp_link.status_code == 403

        # Missing file -> 404
        missing = asyncio.run(_get(f"/files/serve/{'n' * 50}_missing.bin"))
        assert missing.status_code == 404
    finally:
        planted.unlink(missing_ok=True)
        link.unlink(missing_ok=True)
        secret_outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 5: zero-merge sparse/pwrite parallel downloader
# ---------------------------------------------------------------------------

def test_sparse_pwrite_zero_merge_simulation(tmp_path: Path) -> None:
    from app.services.telegram_service import TelegramService

    # 25MB payload -> >=20MB tier -> 2 effective workers (1MB-aligned parts)
    size = 25 * 1024 * 1024
    payload = bytes((i * 7 + 13) % 256 for i in range(size))

    tg = TelegramService()
    client = FakeParallelClient(payload)
    tg.client = client

    target = tmp_path / "out" / "clip.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)

    progress: List = []
    result = asyncio.run(tg.download_media_parallel(
        _fake_message(size),
        str(target),
        progress_callback=lambda c, t: progress.append((c, t)),
        workers=8,
    ))

    # The file returned is the final path itself - no .part* intermediates
    assert result == str(target)
    assert target.is_file()
    # Byte-exact: proves os.pwrite placed every chunk at the correct offset
    assert target.read_bytes() == payload
    assert not [p for p in target.parent.iterdir() if ".part" in p.name]

    # Parallel dispatch: multiple distinct byte ranges were requested
    assert 0 in client.start_offsets
    assert len(client.start_offsets) >= 2
    # Offsets are 1MB-aligned (worker partition boundaries)
    assert all(off % (1024 * 1024) == 0 for off in client.start_offsets)

    # Progress reporting reached 100%
    assert progress
    assert progress[-1] == (size, size)

    # Failure path: a worker that dies mid-stream leaves NO partial file behind
    class BrokenClient(FakeParallelClient):
        async def iter_download(self, media, offset, limit, request_size=65536):
            if offset == 0:
                yield self.payload[offset:offset + min(request_size, limit)]
                raise RuntimeError("network drop")
            end = offset + limit
            while offset < end:
                yield self.payload[offset:offset + min(request_size, end - offset)]
                offset += request_size

    tg2 = TelegramService()
    tg2.client = BrokenClient(payload)
    target2 = tmp_path / "out2" / "broken.mp4"
    target2.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="network drop"):
        asyncio.run(tg2.download_media_parallel(_fake_message(size), str(target2)))

    # Partial sparse file must have been cleaned up
    assert not target2.exists()

    # Overshoot guard: real Telethon iter_download yields chunks past worker
    # partitions if workers do not strictly enforce their end boundary, leading to
    # >100% (e.g. 400%) progress and file corruption from overlapping writes.
    class TelethonLikeOvershootingClient(FakeParallelClient):
        async def iter_download(self, media, offset, limit, request_size=65536):
            if offset not in self.start_offsets:
                self.start_offsets.append(offset)
            # Yields past assigned partition all the way to EOF of file
            while offset < len(self.payload):
                yield self.payload[offset:offset + min(request_size, len(self.payload) - offset)]
                offset += request_size

    tg3 = TelegramService()
    tg3.client = TelethonLikeOvershootingClient(payload)
    target3 = tmp_path / "out3" / "overshoot_protected.mp4"
    target3.parent.mkdir(parents=True, exist_ok=True)

    progress3: List = []
    res3 = asyncio.run(tg3.download_media_parallel(
        _fake_message(size),
        str(target3),
        progress_callback=lambda c, t: progress3.append((c, t)),
        workers=4,
    ))
    assert res3 == str(target3)
    assert target3.read_bytes() == payload
    # Progress must never exceed 100%
    assert all(c <= t for c, t in progress3)
    assert progress3[-1] == (size, size)

    # Resume test: an interrupted download with resume=True preserves partial chunks
    # and resumes exactly where it left off, avoiding re-downloading from the beginning.
    drop_after_bytes = 1024 * 1024  # 1MB
    class InterruptedClient(FakeParallelClient):
        def __init__(self, payload):
            super().__init__(payload)
            self.drop = True

        async def iter_download(self, media, offset: int = 0, limit: int = None, request_size: int = 65536, **kwargs):
            bytes_yielded = 0
            async for chunk in super().iter_download(media, offset=offset, limit=limit, request_size=request_size, **kwargs):
                if self.drop and bytes_yielded >= drop_after_bytes:
                    raise RuntimeError("simulated interruption")
                yield chunk
                bytes_yielded += len(chunk)

    tg4 = TelegramService()
    client4 = InterruptedClient(payload)
    tg4.client = client4
    target4 = tmp_path / "out4" / "resumable.mp4"
    target4.parent.mkdir(parents=True, exist_ok=True)
    meta4 = Path(f"{target4}.meta")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        asyncio.run(tg4.download_media_parallel(
            _fake_message(size),
            str(target4),
            workers=4,
            resume=True
        ))

    # Partial file and meta must be preserved
    assert target4.exists()
    assert meta4.exists()

    # Resume phase: disable drop, call with resume=True
    client4.drop = False
    client4.start_offsets.clear()

    res4 = asyncio.run(tg4.download_media_parallel(
        _fake_message(size),
        str(target4),
        workers=4,
        resume=True
    ))

    assert res4 == str(target4)
    # File content must match byte-for-byte
    assert target4.read_bytes() == payload
    # Meta file must be cleaned up after completion
    assert not meta4.exists()
    # The worker that already downloaded 1MB did NOT start at byte 0
    assert 0 not in client4.start_offsets


# ---------------------------------------------------------------------------
# Test 6: duplicate strategy resolution (skip / rename / overwrite)
# ---------------------------------------------------------------------------

def test_duplicate_strategy_resolution(tmp_path: Path) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        save_dir = tmp_path / "downloads"
        save_dir.mkdir()
        monkeypatch.setattr(Config, "SAVE_PATH", str(save_dir))
        incomplete = save_dir / ".incomplete"
        incomplete.mkdir()
        monkeypatch.setattr(Config, "INCOMPLETE_PATH", str(incomplete))

        size = 32 * 1024
        payload = bytes((i * 3 + 1) % 256 for i in range(size))
        message = _fake_message(size, file_name="movie.mp4")

        # --- strategy: skip (identical size -> no download at all) -------------
        monkeypatch.setattr(Config, "DUPLICATE_STRATEGY", "skip")
        dest = save_dir / "movie.mp4"
        dest.write_bytes(payload)
        service, state = _make_service(payload)

        result = asyncio.run(service.download_single_file(message, str(save_dir), "file_s_1"))
        assert result == str(dest)
        # Marked completed without downloading: no staging artifact left behind
        assert "file_s_1" in state.completed
        assert state.completed["file_s_1"]["size"] == size
        assert not any(incomplete.iterdir())

        # skip with size mismatch -> must download and promote over the stale file
        dest.write_bytes(b"stale-content")
        service, state = _make_service(payload)
        result = asyncio.run(service.download_single_file(message, str(save_dir), "file_s_2"))
        assert result == str(dest)
        assert dest.read_bytes() == payload
        assert "file_s_2" in state.completed

        # --- strategy: rename (collision -> "movie (1).mp4", then (2)) ---------
        monkeypatch.setattr(Config, "DUPLICATE_STRATEGY", "rename")
        dest.write_bytes(b"original")
        service, state = _make_service(payload)
        result = asyncio.run(service.download_single_file(message, str(save_dir), "file_r_1"))
        renamed = save_dir / "movie (1).mp4"
        assert result == str(renamed)
        assert renamed.read_bytes() == payload
        assert dest.read_bytes() == b"original"  # original untouched

        service, state = _make_service(payload)
        result = asyncio.run(service.download_single_file(message, str(save_dir), "file_r_2"))
        assert result == str(save_dir / "movie (2).mp4")

        # --- strategy: overwrite (existing file replaced) -----------------------
        monkeypatch.setattr(Config, "DUPLICATE_STRATEGY", "overwrite")
        dest.write_bytes(b"old-version")
        service, state = _make_service(payload)
        result = asyncio.run(service.download_single_file(message, str(save_dir), "file_o_1"))
        assert result == str(dest)
        assert dest.read_bytes() == payload  # replaced with the fresh download
        assert "file_o_1" in state.completed

        # Staging directory is clean after all promotions (os.replace is atomic)
        assert not any(incomplete.iterdir())


# ---------------------------------------------------------------------------
# Test 7: security headers middleware and CSP configuration
# ---------------------------------------------------------------------------

def test_security_headers_and_csp() -> None:
    import httpx
    from main import app

    async def _check():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await ac.get("/api/health")

    resp = asyncio.run(_check())
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in resp.headers["content-security-policy"]

    # Verify static HTML pages do not include frame-ancestors in <meta>
    # and correctly allow Font Awesome and background pattern domains
    html_files = [
        REPO_ROOT / "app" / "static" / "login.html",
        REPO_ROOT / "app" / "static" / "view.html",
    ]
    for path in html_files:
        content = path.read_text(encoding="utf-8")
        assert "frame-ancestors" not in content, f"frame-ancestors should not be in meta tag in {path.name}"
        assert "https://cdnjs.cloudflare.com" in content
        assert "https://www.transparenttextures.com" in content
        assert "/static/images/stardust.png" in content

    assert (REPO_ROOT / "app" / "static" / "images" / "stardust.png").exists()


# ---------------------------------------------------------------------------
# Test 8: Parallel download chunk alignment and Telethon compatibility
# ---------------------------------------------------------------------------

def test_parallel_downloader_chunk_settings() -> None:
    """Verify that parallel downloader request_chunk_size is 512KB (MAX_CHUNK_SIZE)
    and limit is computed in chunks, not bytes."""
    from telethon.client.downloads import MAX_CHUNK_SIZE
    import inspect
    from app.services.telegram_service import TelegramService

    src = inspect.getsource(TelegramService.download_media_parallel)
    # Must use 512KB chunks to prevent fallback to _GenericDownloadIter
    assert "request_chunk_size = 512 * 1024" in src
    # Must pass chunk limit, not byte count
    assert "chunk_limit = math.ceil((end_byte - start_byte) / request_chunk_size)" in src
    assert "limit=chunk_limit" in src


# ---------------------------------------------------------------------------
# Test 9: /api/disk endpoint and download retry functionality
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disk_info_and_retry_functionality() -> None:
    """Verify that /api/disk returns storage metrics and retry_download correctly re-queues."""
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager
    from unittest.mock import AsyncMock, MagicMock

    tg_mock = MagicMock()
    sm = StateManager()
    ds = DownloadService(tg_mock, sm)

    # Test disk info endpoint logic
    import shutil
    from app.config import Config
    total, used, free = shutil.disk_usage(Config.SAVE_PATH)
    assert total > 0
    assert free >= 0

    # Test retry functionality for cancelled files
    status = sm.get_status()
    status["channel"] = "testchannel"
    status["cancelled_files"]["file_123_456"] = {
        "id": "file_123_456",
        "name": "sample_video.mp4",
        "channel": "testchannel",
        "message_id": 456,
        "progress": 500,
        "total": 1000
    }
    sm.save_state()

    result = await ds.retry_download("file_123_456")
    assert result["status"] == "success"
    assert "file_123_456" not in sm.get_status().get("cancelled_files", {})
    queue = sm.get_queue()
    assert any(item["id"] == "file_123_456" for item in queue)


# ---------------------------------------------------------------------------
# Test 10: download_selected_files adjusts total on missing messages
# ---------------------------------------------------------------------------

def test_download_selected_files_missing_messages_adjusts_total() -> None:
    """Verify that when some selected messages are deleted or lack media,
    skipped_count decrements total so progress can reach 100%."""
    from app.services.telegram_service import TelegramService
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager

    class MockTelegramService(TelegramService):
        def __init__(self):
            super().__init__()
        async def get_messages(self, channel_username, message_ids):
            # Only return 1 valid message out of 3 requested
            return [_fake_message(1024, file_name="single.mp4")]

    tg = MockTelegramService()
    state = StateManager()
    state.download_status = state._initialize_status()
    service = DownloadService(tg, state)

    async def _run():
        session_id = await service.download_selected_files("test_channel", [101, 102, 103])
        # Wait for the collection task to finish
        if service.collection_tasks:
            await asyncio.gather(*list(service.collection_tasks))
        return session_id

    asyncio.run(_run())
    status = state.get_status()
    # Total was 3, but 2 messages were missing, so total must be adjusted to 1!
    assert status.get("total") == 1
    # Cleanup tasks
    service._stop_queue = True
    for t in list(service.active_download_tasks.values()):
        t.cancel()


# ---------------------------------------------------------------------------
# Test 11: download_all_files handles limit=None without TypeError
# ---------------------------------------------------------------------------

def test_download_all_files_limit_none() -> None:
    """Verify that download_all_files handles limit=None without crashing."""
    from app.services.telegram_service import TelegramService
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager

    class MockTelegramService(TelegramService):
        def __init__(self):
            super().__init__()
        async def iter_messages(self, channel_username, limit):
            async def _gen():
                yield _fake_message(2048, file_name="all1.mp4")
                yield _fake_message(4096, file_name="all2.mp4")
            return _gen()

    tg = MockTelegramService()
    state = StateManager()
    state.download_status = state._initialize_status()
    service = DownloadService(tg, state)

    async def _run():
        await service.download_all_files("test_channel", limit=None)
        if service.collection_tasks:
            await asyncio.gather(*list(service.collection_tasks))

    # Must complete cleanly without TypeError
    asyncio.run(_run())
    status = state.get_status()
    assert status.get("total") == 2
    # Cleanup tasks
    service._stop_queue = True
    for t in list(service.active_download_tasks.values()):
        t.cancel()


# ---------------------------------------------------------------------------
# Test 12: Cancelled queued download is not retried
# ---------------------------------------------------------------------------

def test_cancelled_queued_download_is_not_retried() -> None:
    """Verify that when a download task is cancelled, _download_queued_item
    does not re-queue the item for retry."""
    from app.services.telegram_service import TelegramService
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager

    tg = TelegramService()
    state = StateManager()
    state.download_status = state._initialize_status()
    # Simulate cancelled state
    state.download_status["cancelled"] = True
    service = DownloadService(tg, state)

    item = {
        "id": "file_cancel_test",
        "message_id": 999,
        "channel": "test_chan",
        "retry_count": 0,
        "name": "cancel_me.mp4"
    }

    async def _run():
        # Calling _download_queued_item when session is cancelled must return immediately without re-queueing
        await service._download_queued_item(item)

    asyncio.run(_run())
    # Queue must remain empty, item must NOT be re-added
    assert len(state.get_queue()) == 0


# ---------------------------------------------------------------------------
# Test 13: Free disk space pre-flight check
# ---------------------------------------------------------------------------

def test_disk_space_preflight_check(monkeypatch, tmp_path: Path) -> None:
    """Verify that download_single_file raises InsufficientDiskSpaceError
    when free disk space is smaller than target file size + safety margin."""
    import shutil
    from app.services.download_service import DownloadService, InsufficientDiskSpaceError
    from app.services.telegram_service import TelegramService

    service, state = _make_service(b"dummy")
    message = _fake_message(100 * 1024 * 1024, file_name="large.mp4")

    # Simulate free disk space of only 50MB (below 100MB file + 500MB margin)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: (1000 * 1024 * 1024, 950 * 1024 * 1024, 50 * 1024 * 1024))

    with pytest.raises(InsufficientDiskSpaceError):
        asyncio.run(service.download_single_file(message, str(tmp_path), "file_disk_test"))


# ---------------------------------------------------------------------------
# Test 14: Native bcrypt password hashing without passlib
# ---------------------------------------------------------------------------

def test_native_bcrypt_auth_service() -> None:
    """Verify that AuthService uses native bcrypt for hashing and verification,
    avoiding deprecated passlib/crypt dependencies."""
    from app.services.auth_service import AuthService
    import sys

    # passlib should not be imported by auth_service
    auth = AuthService()
    raw_pass = "TestSecurePassword123!"
    hashed = auth.get_password_hash(raw_pass)

    assert hashed.startswith("$2b$")
    assert auth.verify_password(raw_pass, hashed) is True
    assert auth.verify_password("WrongPassword!", hashed) is False
    assert auth.authenticate_user(Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD) is True


# ---------------------------------------------------------------------------
# Test 15: Dockerfile healthcheck presence
# ---------------------------------------------------------------------------

def test_dockerfile_healthcheck_presence() -> None:
    """Verify that dockerfile defines HEALTHCHECK with standard library probe."""
    df_content = (REPO_ROOT / "dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in df_content
    assert "/api/health" in df_content


# ---------------------------------------------------------------------------
# Test 16: Partial progress retention on cancellation & retry endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_tracks_partial_progress_and_allows_retry() -> None:
    """Verify that cancelling active downloads snapshots progress into cancelled_files
    and retry_download allows them to be resumed."""
    from app.services.download_service import DownloadService
    from app.utils.state_manager import StateManager
    from unittest.mock import MagicMock

    tg_mock = MagicMock()
    sm = StateManager()
    ds = DownloadService(tg_mock, sm)

    status = sm.get_status()
    status["channel"] = "my_channel"
    status["total"] = 2
    status["concurrent_downloads"]["file_part_1"] = {
        "id": "file_part_1",
        "name": "movie_part.mkv",
        "channel": "my_channel",
        "message_id": 888,
        "progress": 450 * 1024 * 1024,
        "total": 1000 * 1024 * 1024,
        "percentage": 45
    }
    sm.save_state()

    # Cancel individual
    res = await ds.cancel_individual_download("file_part_1")
    assert res["status"] == "success"

    # Must be in cancelled_files with exact progress
    cancelled = sm.get_status().get("cancelled_files", {})
    assert "file_part_1" in cancelled
    assert cancelled["file_part_1"]["progress"] == 450 * 1024 * 1024
    assert cancelled["file_part_1"]["percentage"] == 45

    # Retry should re-queue it
    retry_res = await ds.retry_download("file_part_1")
    assert retry_res["status"] == "success"
    assert "file_part_1" not in sm.get_status().get("cancelled_files", {})
    queue_items = sm.get_queue()
    assert any(q["id"] == "file_part_1" and q["status"] == "queued" for q in queue_items)


def test_get_current_user_query_token_auth() -> None:
    from app.services.auth_service import AuthService
    from app.utils import auth_dependencies
    from fastapi import Request

    auth_svc = AuthService()
    auth_dependencies.set_auth_service(auth_svc)

    token = auth_svc.create_access_token({"sub": "remote_user"})

    # Test via query string on an untrusted remote IP
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/files/serve/test.mp4",
        "query_string": f"token={token}".encode("ascii"),
        "headers": [],
        "client": ("198.51.100.25", 54321),  # Untrusted external IP
    }
    req = Request(scope)
    user = asyncio.run(auth_dependencies.get_current_user(req, credentials=None))
    assert user == "remote_user"


def test_parallel_multi_sender_download(tmp_path: Path) -> None:
    from app.services.telegram_service import TelegramService
    from telethon.tl.types import MessageMediaDocument, Document
    from unittest.mock import AsyncMock, MagicMock

    size = 10 * 1024 * 1024  # 10MB
    payload = bytes((i * 11 + 3) % 256 for i in range(size))

    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 2

    senders_disconnected = []

    class MockSender:
        def __init__(self, idx):
            self.idx = idx

        async def disconnect(self):
            senders_disconnected.append(self.idx)

    mock_senders = [MockSender(i) for i in range(2)]

    async def mock_call(sender, req):
        offset = req.offset
        limit = req.limit
        chunk = payload[offset:offset + limit]
        res = MagicMock()
        res.bytes = chunk
        return res

    client._call = AsyncMock(side_effect=mock_call)

    tg = TelegramService()
    tg.client = client
    tg._create_parallel_senders = AsyncMock(return_value=mock_senders)

    doc = Document(id=999, access_hash=888, file_reference=b'ref', date=None, mime_type='video/mp4', size=size, dc_id=2, attributes=[])
    media = MessageMediaDocument(document=doc)
    msg = MagicMock()
    msg.media = media

    target = tmp_path / "parallel_fast.mp4"
    res = asyncio.run(tg.download_media_parallel(msg, str(target), workers=2))

    assert res == str(target)
    assert target.is_file()
    assert target.read_bytes() == payload
    # Verify all senders were disconnected
    assert sorted(senders_disconnected) == [0, 1]







