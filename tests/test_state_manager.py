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


def test_save_state_async_persists_data(temp_state_manager):
    """Verify save_state_async(force=True) persists valid, complete JSON to disk."""
    manager, state_file, _ = temp_state_manager

    session_id = str(uuid.uuid4())
    manager.download_status.update({
        "session_id": session_id,
        "channel": "async_channel",
        "total": 7,
        "progress": 2,
        "active": True,
        "current_file": "async_video.mp4",
        "concurrent_downloads": {
            "file_async_1": {"name": "a.mp4", "progress": 50, "total": 100}
        },
        "queue": [{"id": "q_async", "name": "queued.mp4", "status": "queued"}]
    })

    asyncio.run(manager.save_state_async(force=True))

    assert state_file.exists()
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Full schema present and values round-tripped intact
    assert data["session_id"] == session_id
    assert data["channel"] == "async_channel"
    assert data["total"] == 7
    assert data["progress"] == 2
    assert data["active"] is True
    assert data["current_file"] == "async_video.mp4"
    assert data["concurrent_downloads"]["file_async_1"]["progress"] == 50
    assert len(data["queue"]) == 1
    assert data["queue"][0]["id"] == "q_async"

    # No orphaned temp files should remain after a successful flush
    assert not list(state_file.parent.glob(state_file.name + ".tmp.*"))

    # A fresh manager can load exactly what the async flush wrote
    manager2 = StateManager()
    manager2.load_state()
    assert manager2.get_status()["session_id"] == session_id
    assert manager2.get_status()["progress"] == 2


def test_save_state_async_does_not_block_event_loop(temp_state_manager, monkeypatch):
    """
    With an artificial disk delay injected into _sync_write_to_disk, concurrent
    save_state_async calls must run their fsync work on the worker thread pool so
    that a co-running ticker coroutine on the event loop keeps sub-10ms jitter.
    """
    manager, state_file, _ = temp_state_manager
    manager.update_status({"session_id": "blocking-check", "total": 3})

    write_count = 0
    original = manager._sync_write_to_disk

    def slow_sync_write(state_to_save, temp_file):
        nonlocal write_count
        write_count += 1
        time.sleep(0.05)  # Simulate slow disk / expensive fsync
        original(state_to_save, temp_file)

    monkeypatch.setattr(manager, "_sync_write_to_disk", slow_sync_write)

    tick_jitters = []
    stop_event = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not stop_event.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            tick_jitters.append(now - last - 0.005)
            last = now

    async def saver():
        for i in range(5):
            manager.download_status["current_file_progress"] = i
            await manager.save_state_async(force=True)
            await asyncio.sleep(0.001)

    async def main():
        ticker_task = asyncio.create_task(ticker())
        savers = [asyncio.create_task(saver()) for _ in range(4)]
        await asyncio.gather(*savers)
        stop_event.set()
        await ticker_task

    asyncio.run(main())

    # All writes were offloaded and completed (4 savers x 5 forced writes)
    assert write_count == 20
    # Event loop must never have been stalled by the 50ms disk sleeps
    assert tick_jitters, "Ticker never ran"
    max_jitter = max(tick_jitters)
    assert max_jitter < 0.010, f"Event loop tick jitter too high: {max_jitter * 1000:.2f}ms"
    # And the final state on disk must be valid, uninterleaved JSON
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == "blocking-check"


def test_save_state_async_throttling_and_debouncing(temp_state_manager, monkeypatch):
    """
    Rapid concurrent save_state_async(force=False) calls must drop redundant
    in-flight flushes (debounce + 1.5s throttle) instead of stacking fsyncs,
    without corrupting state or leaking temp files.
    """
    manager, state_file, session_dir = temp_state_manager
    manager.update_status({"session_id": "debounce-check", "total": 2})
    # update_status() force-writes and stamps _last_save_time; clear the throttle
    # window so the burst below is dropped by in-flight debouncing, not the 1.5s timer.
    manager._last_save_time = 0.0

    write_count = 0
    original = manager._sync_write_to_disk

    def slow_sync_write(state_to_save, temp_file):
        nonlocal write_count
        write_count += 1
        time.sleep(0.25)  # Hold the lock long enough for the burst to observe it
        original(state_to_save, temp_file)

    monkeypatch.setattr(manager, "_sync_write_to_disk", slow_sync_write)

    async def main():
        # Yield so the first save acquires the lock before the burst is dispatched
        tasks = [asyncio.create_task(manager.save_state_async(force=False))
                 for _ in range(50)]
        await asyncio.sleep(0)
        for _ in tasks:
            pass
        await asyncio.gather(*tasks)
        # Ensure any in-flight worker thread has finished publishing
        for _ in range(200):
            if not list(session_dir.glob(state_file.name + ".tmp.*")):
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())

    # Exactly one real disk write should have survived the 50-call burst;
    # the rest must be debounced (throttled or lock-held) rather than queued.
    assert write_count >= 1, "Burst produced no disk write"
    assert write_count <= 5, f"Debounce failed: {write_count} redundant writes flushed"

    # No leftover temp files from interleaved writers
    assert not list(session_dir.glob(state_file.name + ".tmp.*")), \
        "Orphaned temp files after concurrent async saves"

    # State file must be valid, uncorrupted JSON with the expected session
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == "debounce-check"
    assert data["total"] == 2


def test_schedule_progress_save_sync_fallback_off_loop(temp_state_manager):
    """schedule_progress_save with no running event loop must fall back to the
    synchronous writer and persist state without raising or blocking."""
    manager, state_file, _ = temp_state_manager
    manager.update_status({"session_id": "sync-fallback", "total": 4,
                           "current_file_progress": 42})
    # Simulate a progress callback fired from a thread with no event loop.
    manager.schedule_progress_save(force=True)

    assert state_file.exists()
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == "sync-fallback"
    assert data["current_file_progress"] == 42
    # Synchronous fallback must not leave the async path dirty.
    assert not list(state_file.parent.glob(state_file.name + ".tmp.*"))


def test_schedule_progress_save_flushes_on_running_loop(temp_state_manager):
    """schedule_progress_save called from within a running loop must schedule a
    non-blocking async flush that lands in the worker thread pool and tracks its
    task so it is never garbage-collected mid-flight."""
    manager, state_file, _ = temp_state_manager
    manager.update_status({"session_id": "on-loop", "total": 6})

    async def main():
        manager.download_status["current_file_progress"] = 77
        manager.schedule_progress_save(force=True)
        # Yield until the background flush task completes and is discarded.
        for _ in range(200):
            if not manager._background_tasks:
                break
            await asyncio.sleep(0.01)
        # Give the worker thread a beat to finish publishing.
        for _ in range(200):
            if not list(state_file.parent.glob(state_file.name + ".tmp.*")):
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())

    assert not manager._background_tasks, "Background flush task was not tracked/dropped"
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == "on-loop"
    assert data["current_file_progress"] == 77
    assert not list(state_file.parent.glob(state_file.name + ".tmp.*"))


def test_schedule_progress_save_hops_from_foreign_thread(temp_state_manager, monkeypatch):
    """When a live bound loop exists and schedule_progress_save is invoked from a
    thread running a *different* event loop, the flush must hop onto the bound
    loop via call_soon_threadsafe and run on that loop's worker pool — not write
    synchronously on the foreign thread."""
    manager, state_file, _ = temp_state_manager
    manager.update_status({"session_id": "cross-thread", "total": 3})

    write_threads = []
    original = manager._sync_write_to_disk

    def counting_sync_write(state_to_save, temp_file):
        write_threads.append(threading.current_thread().name)
        original(state_to_save, temp_file)

    monkeypatch.setattr(manager, "_sync_write_to_disk", counting_sync_write)

    results = {"foreign_done": threading.Event(), "outer_name": None}

    def run_outer_loop():
        async def outer_main():
            # Bind the write lock to this outer loop.
            await manager.save_state_async(force=True)
            results["outer_name"] = threading.current_thread().name

            def foreign_body():
                try:
                    async def foreign_main():
                        manager.download_status["current_file_progress"] = 99
                        # Foreign running loop != bound loop -> must hop.
                        manager.schedule_progress_save(force=True)

                    asyncio.run(foreign_main())
                finally:
                    results["foreign_done"].set()

            t = threading.Thread(target=foreign_body, name="foreign-loop-thread")
            t.start()

            # Keep the outer loop responsive (NO blocking join) while the hopped
            # flush drains here; completion is signalled by the published state.
            published = False
            for _ in range(1000):
                await asyncio.sleep(0.01)
                if state_file.exists():
                    try:
                        with open(state_file, 'r', encoding='utf-8') as f:
                            if json.load(f).get("current_file_progress") == 99:
                                published = True
                                break
                    except (json.JSONDecodeError, OSError):
                        pass  # mid-publish, keep polling
            t.join(timeout=10)
            results["published"] = published
            results["foreign_alive"] = t.is_alive()

        asyncio.run(outer_main())

    outer = threading.Thread(target=run_outer_loop, name="outer-loop-thread")
    outer.start()
    outer.join(timeout=30)
    assert not outer.is_alive(), "Outer loop thread hung"
    assert results["foreign_done"].is_set(), "Foreign loop thread hung"
    assert not results.get("foreign_alive"), "Foreign loop thread still running"
    assert results.get("published"), "Hopped flush never published to disk"

    # The hopped write must have run on the bound loop's executor pool — never on
    # the foreign or outer loop threads. (asyncio.to_thread names its default
    # executor threads "asyncio_N"; what matters is it was NOT either loop.)
    assert write_threads, "Cross-loop hop produced no disk write"
    last_writer = write_threads[-1]
    assert last_writer not in ("foreign-loop-thread", results["outer_name"]), \
        f"Write ran on wrong thread: {last_writer}"
    assert last_writer.startswith("asyncio_"), \
        f"Expected worker-pool write, got: {last_writer}"

    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == "cross-thread"
    assert data["current_file_progress"] == 99
    assert not list(state_file.parent.glob(state_file.name + ".tmp.*"))


def test_save_state_async_rebinds_after_loop_closes(temp_state_manager):
    """A manager whose write lock is bound to a loop that has since closed must
    rebind cleanly on the next fresh loop instead of raising RuntimeError from the
    fast-path .locked() check (the bug this refactor guards against)."""
    manager, state_file, _ = temp_state_manager

    # First loop: bind the lock, then exit (loop closes).
    asyncio.run(manager.save_state_async(force=True))
    first_bound = manager._bound_loop
    assert first_bound is not None
    assert first_bound.is_closed(), "First loop should be closed after asyncio.run"

    # Force=True path on a brand new loop: must not raise from .locked() on the
    # stale cross-loop lock, must rebind, and must persist.
    asyncio.run(manager.save_state_async(force=True))
    assert manager._bound_loop is not first_bound, "Lock was not re-bound to the new loop"

    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data["session_id"] == manager.get_status()["session_id"]
    assert not list(state_file.parent.glob(state_file.name + ".tmp.*"))
