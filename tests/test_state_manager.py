"""
Tests for StateManager and state stability in Telefetchr.
"""

import os
import sys
import json
import time
import uuid
import asyncio
import threading
from pathlib import Path
from typing import Dict, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SECRET_KEY", "telefetchr-test-key-12345")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("PHONE_NUMBER", "1234567890")
# Config now refuses to boot without a non-default admin password.
os.environ.setdefault("ADMIN_PASSWORD", "telefetchr-test-password-xyz")

import pytest
from app.config import Config
from app.utils.state_manager import StateManager
from app.services.download_service import DownloadService


@pytest.fixture
def temp_state_manager(tmp_path, monkeypatch):
    """Fixture providing a StateManager configured with a temporary state file."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    state_file = session_dir / "download_state.json"
    
    monkeypatch.setattr(Config, "SESSION_DIR", str(session_dir))
    monkeypatch.setattr(Config, "STATE_FILE", str(state_file))
    
    manager = StateManager()
    return manager, state_file, session_dir


def test_state_initialization(temp_state_manager):
    """Verify all state schema fields are present with correct defaults."""
    manager, state_file, _ = temp_state_manager
    status = manager.get_status()

    expected_keys = [
        "active", "scanning", "scan_progress", "progress", "total",
        "current_file", "current_file_progress", "current_file_size",
        "downloaded_bytes", "concurrent_downloads", "completed_downloads",
        "cancelled_files", "cancelled", "session_id", "started_at",
        "channel", "queue"
    ]
    for key in expected_keys:
        assert key in status, f"Missing key in state: {key}"

    assert status["active"] is False
    assert status["cancelled"] is False
    assert status["progress"] == 0
    assert status["total"] == 0
    assert isinstance(status["queue"], list)
    assert isinstance(status["concurrent_downloads"], dict)
    assert isinstance(status["completed_downloads"], dict)


def test_atomic_save_and_load(temp_state_manager):
    """Verify save_state writes atomically and load_state restores all data."""
    manager, state_file, _ = temp_state_manager

    session_id = str(uuid.uuid4())
    manager.update_status({
        "session_id": session_id,
        "channel": "test_channel",
        "total": 5,
        "active": True
    })

    manager.add_to_queue([{
        "id": "item_1",
        "message_id": 101,
        "channel": "test_channel",
        "name": "video1.mp4",
        "priority": 1
    }])

    assert state_file.exists()
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    assert data["session_id"] == session_id
    assert data["channel"] == "test_channel"
    assert data["total"] == 5
    assert len(data["queue"]) == 1
    assert data["queue"][0]["id"] == "item_1"

    # New manager instance loads the same file
    manager2 = StateManager()
    manager2.load_state()
    loaded_status = manager2.get_status()

    assert loaded_status["session_id"] == session_id
    assert loaded_status["channel"] == "test_channel"
    assert loaded_status["total"] == 5
    assert len(loaded_status["queue"]) == 1


def test_concurrent_mutations_during_save(temp_state_manager):
    """
    Verify save_state does not raise 'dictionary changed size during iteration'
    when concurrent threads are actively modifying the status dict.
    """
    manager, _, _ = temp_state_manager
    stop_event = threading.Event()
    errors = []

    def mutator():
        counter = 0
        while not stop_event.is_set():
            counter += 1
            fid = f"file_{counter}"
            # Mutate concurrent downloads
            manager.download_status["concurrent_downloads"][fid] = {
                "name": f"test_{counter}.mp4",
                "progress": counter * 10
            }
            if counter > 5 and f"file_{counter - 5}" in manager.download_status["concurrent_downloads"]:
                del manager.download_status["concurrent_downloads"][f"file_{counter - 5}"]

            # Mutate queue
            if counter % 2 == 0:
                manager.download_status["queue"].append({"id": f"q_{counter}", "status": "queued"})
            elif manager.download_status["queue"]:
                manager.download_status["queue"].pop(0)

            time.sleep(0.001)

    threads = [threading.Thread(target=mutator) for _ in range(3)]
    for t in threads:
        t.start()

    try:
        # Perform frequent saves from main thread
        for _ in range(50):
            try:
                manager.save_state(force=True)
            except Exception as e:
                errors.append(e)
            time.sleep(0.005)
    finally:
        stop_event.set()
        for t in threads:
            t.join()

    assert not errors, f"Concurrent save errors: {errors}"


def test_high_frequency_throttling(temp_state_manager):
    """Verify save_state(force=False) throttles high-frequency writes."""
    manager, state_file, _ = temp_state_manager

    # Force initial write
    manager.save_state(force=True)
    initial_mtime = state_file.stat().st_mtime_ns

    # Call save_state(force=False) repeatedly immediately after
    for i in range(10):
        manager.download_status["current_file_progress"] = i * 100
        manager.save_state(force=False)

    # st_mtime should be unchanged because throttled
    second_mtime = state_file.stat().st_mtime_ns
    assert second_mtime == initial_mtime

    # But in-memory status must be updated immediately
    assert manager.get_status()["current_file_progress"] == 900


def test_corrupted_state_recovery_from_backup(temp_state_manager):
    """Verify load_state auto-recovers from the newest valid backup when state_file is corrupt."""
    manager, state_file, session_dir = temp_state_manager

    valid_session_id = "valid-session-123"
    valid_state = {
        "session_id": valid_session_id,
        "active": False,
        "channel": "recovered_channel",
        "queue": [{"id": "saved_1", "status": "queued"}]
    }

    # Create a valid backup file
    backup_file = session_dir / f"download_state.json.backup.{int(time.time()) - 10}"
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(valid_state, f)

    # Write corrupt data to the main state file
    with open(state_file, 'w', encoding='utf-8') as f:
        f.write("{corrupt json truncated string...")

    # load_state should detect corruption and recover from backup
    manager.load_state()

    assert manager.get_status()["session_id"] == valid_session_id
    assert manager.get_status()["channel"] == "recovered_channel"
    assert len(manager.get_status()["queue"]) == 1

    # Main state file must have been re-written cleanly
    with open(state_file, 'r', encoding='utf-8') as f:
        recovered = json.load(f)
    assert recovered["session_id"] == valid_session_id


def test_backup_pruning(temp_state_manager):
    """Verify backup files are pruned to keep at most 5 files."""
    manager, state_file, session_dir = temp_state_manager

    # Create 8 dummy backup files with varying mtimes
    base_time = time.time()
    for i in range(8):
        bf = session_dir / f"download_state.json.backup.{int(base_time) + i}"
        bf.write_text('{"session_id": "test"}')
        os.utime(bf, (base_time + i, base_time + i))

    assert len(list(session_dir.glob("download_state.json.backup.*"))) == 8

    # Trigger clear_state, which creates another backup and prunes
    manager.clear_state()

    remaining_backups = list(session_dir.glob("download_state.json.backup.*"))
    assert len(remaining_backups) <= 5


def test_session_progress_single_and_batch_downloads(temp_state_manager):
    """
    Verify mark_file_completed accurately tracks session progress for both
    batch downloads and single downloads with session_id metadata.
    """
    manager, _, _ = temp_state_manager
    session_id = "session-abc-123"

    manager.update_status({
        "session_id": session_id,
        "total": 3,
        "progress": 0
    })

    # 1. Batch download with session_id embedded in ID
    asyncio.run(manager.mark_file_completed(
        f"file_{session_id}_101",
        {"name": "file1.mp4", "size": 1000, "session_id": session_id}
    ))
    assert manager.get_status()["progress"] == 1

    # 2. Single download with single_ ID but session_id in file_data
    asyncio.run(manager.mark_file_completed(
        "single_202",
        {"name": "single_file.mp4", "size": 2000, "session_id": session_id}
    ))
    assert manager.get_status()["progress"] == 2

    # 3. Old download from a different session should NOT increment current session progress
    asyncio.run(manager.mark_file_completed(
        "file_old-session-xyz_303",
        {"name": "old.mp4", "size": 3000, "session_id": "old-session-xyz"}
    ))
    assert manager.get_status()["progress"] == 2


def test_channel_normalization_and_duplicate_check(temp_state_manager):
    """Verify is_file_active_or_queued normalizes channel usernames and message IDs."""
    manager, _, _ = temp_state_manager

    manager.add_to_queue([{
        "id": "q1",
        "channel": "@MyChannel",
        "message_id": "42",
        "status": "queued"
    }])

    # Match with variations: lowercase, no @, int message_id
    assert manager.is_file_active_or_queued("mychannel", 42) is True
    assert manager.is_file_active_or_queued("@mychannel", 42) is True
    assert manager.is_file_active_or_queued("MyChannel", "42") is True

    # Non-matching message id or channel
    assert manager.is_file_active_or_queued("mychannel", 99) is False
    assert manager.is_file_active_or_queued("otherchannel", 42) is False


def test_clear_completed_preserves_queue(temp_state_manager):
    """Verify clear_completed only removes completed history and does not wipe the queue."""
    manager, _, _ = temp_state_manager

    manager.update_status({
        "session_id": "sess-1",
        "active": False,
        "progress": 2,
        "total": 4,
        "completed_downloads": {
            "f1": {"name": "done1.mp4"},
            "f2": {"name": "done2.mp4"}
        }
    })

    manager.add_to_queue([
        {"id": "q1", "name": "pending1.mp4", "status": "queued"},
        {"id": "q2", "name": "pending2.mp4", "status": "queued"}
    ])

    manager.clear_completed()

    status = manager.get_status()
    assert len(status["completed_downloads"]) == 0
    assert status["progress"] == 0
    # Queue must be preserved!
    assert len(status["queue"]) == 2
    assert status["queue"][0]["id"] == "q1"


def test_cleanup_state_respects_cancelled(temp_state_manager):
    """Verify cleanup_state does not mark a session active if it was cancelled."""
    manager, _, _ = temp_state_manager

    manager.update_status({
        "cancelled": True,
        "active": False,
        "concurrent_downloads": {
            "interrupted_1": {"name": "interrupted.mp4", "channel": "ch1", "message_id": 50}
        }
    })

    cleaned = manager.cleanup_state(force=True)

    assert "interrupted_1" in cleaned
    status = manager.get_status()
    assert len(status["concurrent_downloads"]) == 0
    assert len(status["queue"]) == 1
    # Because cancelled is True, active MUST be False!
    assert status["active"] is False


def test_zombie_active_reconciliation(temp_state_manager):
    """Verify get_status_as_dict auto-heals a zombie active session when no tasks/queue exist."""
    manager, _, _ = temp_state_manager

    manager.update_status({
        "active": True,
        "started_at": "2026-10-19T10:00:00",
        "queue": [],
        "concurrent_downloads": {},
        "scanning": False
    })

    service = DownloadService(telegram_service=None, state_manager=manager)
    # No active tasks, no transitioning items, processor stopped
    service._queue_processing = False

    status = service.get_status_as_dict()

    assert status["active"] is False
    assert status["started_at"] is None
    # State manager itself must have been updated to False
    assert manager.get_status()["active"] is False
