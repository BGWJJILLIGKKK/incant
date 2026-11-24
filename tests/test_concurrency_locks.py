import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from incant.llm import TokenBudget, fcntl
from incant.stages.synthesize.kokoro.kokoro import KokoroSettings


def test_token_budget_cross_process_locking(tmp_path):
    lock_file = tmp_path / "test.lock"
    budget1 = TokenBudget(total=1000, lock_path=lock_file)

    # Budget 1 reserves 600 tokens
    acq1 = budget1.acquire(600)
    assert acq1 == 600
    assert lock_file.exists()

    data = json.loads(lock_file.read_text())
    assert len(data) == 1
    assert data[0]["tokens"] == 600

    # Budget 1 releases tokens
    budget1.release(600)
    data_after = json.loads(lock_file.read_text())
    assert len(data_after) == 0


@pytest.mark.skipif(fcntl is None, reason="flock is unavailable")
def test_threads_take_independent_reservation_file_locks(tmp_path, monkeypatch):
    budget = TokenBudget(1000, tmp_path / "threaded.lock")
    entered = threading.Event()
    waiting = threading.Event()
    proceed = threading.Event()
    read = budget._read_reservations

    def controlled_read(file):
        if not entered.is_set():
            entered.set()
            assert proceed.wait(3)
        else:
            waiting.set()
        return read(file)

    monkeypatch.setattr(budget, "_read_reservations", controlled_read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(budget.acquire, 400)
        assert entered.wait(3)
        started = threading.Event()

        def acquire_second():
            started.set()
            return budget.acquire(400)

        second = workers.submit(acquire_second)
        try:
            assert started.wait(3)
            assert not waiting.wait(0.1)
        finally:
            proceed.set()
        assert first.result(timeout=3) == second.result(timeout=3) == 400
        assert sum(entry["tokens"] for entry in json.loads(budget.lock_path.read_text())) == 800
    budget.release(400)
    budget.release(400)
    assert budget.free == 1000
    assert json.loads(budget.lock_path.read_text()) == []


@pytest.mark.skipif(fcntl is None, reason="flock is unavailable")
def test_reservation_file_recovers_from_extra_data_on_release(tmp_path):
    path = tmp_path / "old.lock"
    budget = TokenBudget(1000, path)
    reserved = budget.acquire(400)
    other = {"pid": os.getpid(), "tokens": 100, "time": time.time()}
    records = [*json.loads(path.read_text()), other]
    path.write_text(json.dumps(records) + "[]")
    budget.release(reserved)
    assert budget.free == 1000
    assert json.loads(path.read_text()) == [other]

    path.write_text("[broken")
    reserved = budget.acquire(400)
    budget.release(reserved)
    assert json.loads(path.read_text()) == []


def test_kokoro_bounded_threads_default():
    s = KokoroSettings()
    expected = max(1, min((os.cpu_count() or 1) // 2, 8))
    assert s.threads == expected
