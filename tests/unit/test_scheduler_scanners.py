import time
from datetime import UTC, datetime
from threading import Event

from commercevision_scheduler.runtime import (
    IndependentScannerOrchestrator,
    ScannerDefinition,
)


def settle(orchestrator: IndependentScannerOrchestrator) -> None:
    for _ in range(50):
        orchestrator.run_due()
        if all(not status.in_progress for status in orchestrator.statuses.values()):
            return
        time.sleep(0.002)
    raise AssertionError("scanner did not settle")


def test_scanner_exception_does_not_stop_other_scanners() -> None:
    calls: list[str] = []

    def failing() -> int:
        calls.append("failing")
        raise RuntimeError("scanner failed")

    def healthy() -> int:
        calls.append("healthy")
        return 7

    wall_time = datetime(2026, 7, 23, 13, 0, tzinfo=UTC)
    orchestrator = IndependentScannerOrchestrator(
        scanners=(
            ScannerDefinition("failing", 10, failing),
            ScannerDefinition("healthy", 10, healthy),
        ),
        monotonic_clock=lambda: 100.0,
        wall_clock=lambda: wall_time,
    )

    orchestrator.run_due()
    settle(orchestrator)

    assert set(calls) == {"failing", "healthy"}
    assert orchestrator.statuses["failing"].last_error == "RuntimeError: scanner failed"
    assert orchestrator.statuses["healthy"].last_success_at == wall_time
    assert orchestrator.statuses["healthy"].last_count == 7
    assert orchestrator.statuses["healthy"].total_count == 7


def test_successful_scanner_run_clears_only_its_own_error() -> None:
    current_tick = 100.0
    should_fail = True

    def scanner() -> int:
        if should_fail:
            raise RuntimeError("temporary failure")
        return 2

    orchestrator = IndependentScannerOrchestrator(
        scanners=(ScannerDefinition("recovery", 10, scanner),),
        monotonic_clock=lambda: current_tick,
        wall_clock=lambda: datetime(2026, 7, 23, 13, 0, tzinfo=UTC),
    )
    orchestrator.run_due()
    settle(orchestrator)
    assert orchestrator.statuses["recovery"].last_error

    should_fail = False
    current_tick = 110.0
    orchestrator.run_due()
    settle(orchestrator)

    assert orchestrator.statuses["recovery"].last_error is None
    assert orchestrator.statuses["recovery"].last_count == 2


def test_hung_scanner_times_out_without_blocking_other_due_scanners() -> None:
    release = Event()
    healthy_ran = Event()
    healthy_calls = 0
    current_tick = 100.0

    def hung() -> int:
        release.wait(timeout=5)
        return 1

    def healthy() -> int:
        nonlocal healthy_calls
        healthy_calls += 1
        healthy_ran.set()
        return 4

    orchestrator = IndependentScannerOrchestrator(
        scanners=(
            ScannerDefinition("hung", 10, hung),
            ScannerDefinition("healthy", 10, healthy),
        ),
        timeout_seconds=0.05,
        monotonic_clock=lambda: current_tick,
        wall_clock=lambda: datetime(2026, 7, 23, 13, 30, tzinfo=UTC),
    )

    started = time.perf_counter()
    orchestrator.run_due()
    assert healthy_ran.wait(timeout=0.1)
    time.sleep(0.06)
    orchestrator.run_due()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    assert orchestrator.statuses["healthy"].last_count == 4
    assert orchestrator.statuses["healthy"].last_success_at is not None
    assert orchestrator.statuses["hung"].last_error == (
        "TimeoutError: scanner exceeded 0.050 seconds"
    )
    assert orchestrator.statuses["hung"].timeout_count == 1
    assert orchestrator.statuses["hung"].in_progress is True
    current_tick = 110.0
    healthy_ran.clear()
    orchestrator.run_due()
    assert healthy_ran.wait(timeout=0.1)
    for _ in range(50):
        orchestrator.run_due()
        if orchestrator.statuses["healthy"].total_count == 8:
            break
        time.sleep(0.002)
    assert healthy_calls == 2
    assert orchestrator.statuses["healthy"].total_count == 8
    release.set()
