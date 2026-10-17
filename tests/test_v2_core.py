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

    async def iter_download(self, media, offset: int, limit: int,
                            request_size: int = 65536):
        if offset not in self.start_offsets:
            self.start_offsets.append(offset)
        end = offset + limit
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
    # Sanity: default trusted proxy list is safe (loopback + RFC1918 only)
    assert "127.0.0.1" in Config.TRUSTED_PROXIES
    assert "172.16.0.0/12" in Config.TRUSTED_PROXIES
    assert "10.0.0.0/8" in Config.TRUSTED_PROXIES
    assert "192.168.0.0/16" in Config.TRUSTED_PROXIES

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
    chained = _make_request("172.16.5.10", xff="203.0.113.99, 10.0.0.9")
    assert get_client_ip(chained) == "203.0.113.99"

    # 5. Trusted proxy with no headers -> falls back to the peer IP.
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
