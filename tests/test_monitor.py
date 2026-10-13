import logging
from datetime import UTC, datetime

import pytest
from fakes import encode

from laser_cutterd.metrics import STATUS_JOB, STATUS_MOVING, STATUS_PAUSED, Metric
from laser_cutterd.monitor import DERIVED_KEYS, Monitor
from laser_cutterd.ruida import RuidaError, RuidaTimeoutError

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class FakeReader:
    """A controller's memory: address -> bytes, or an exception to raise."""

    def __init__(self, memory: dict[int, bytes | Exception]) -> None:
        self.memory = memory
        self.reads: list[int] = []

    def read(self, address: int) -> bytes:
        self.reads.append(address)
        value = self.memory.get(address, RuidaTimeoutError("no answer"))
        if isinstance(value, Exception):
            raise value
        return value


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(memory: dict[int, bytes | Exception]) -> tuple[Monitor, FakeReader, FakeClock]:
    reader = FakeReader(memory)
    clock = FakeClock()
    return Monitor(reader, clock=clock, now=lambda: NOW), reader, clock


def idle_memory() -> dict[int, bytes | Exception]:
    return {
        0x0400: encode(0),
        0x0421: encode(150_000),
        0x0431: encode(-2_500),
        0x0441: encode(0),
        0x0451: encode(0),
        0x0620: encode(0),
        0x0621: encode(0),
        0x057E: encode(0x65006500),
        0x0403: encode(1234),
    }


def test_starts_with_every_key_unknown() -> None:
    monitor, _, _ = make({})
    data = monitor.latest.as_json()
    assert data["online"] is False
    assert data["updated"] is None
    assert set(DERIVED_KEYS) <= set(data)
    assert data["x_position"] is None
    assert data["jobs_since_start"] == 0


def test_reads_fast_metrics_only_unless_asked() -> None:
    monitor, reader, _ = make(idle_memory())
    snapshot = monitor.poll(include_slow=False)
    assert snapshot.online
    assert snapshot.values["x_position"] == 150.0
    assert snapshot.values["y_position"] == -2.5
    assert snapshot.values["state"] == "idle"
    assert snapshot.values["job_running"] is False
    assert snapshot.values["total_jobs"] is None
    assert 0x0403 not in reader.reads
    assert snapshot.as_json()["updated"] == NOW.isoformat()

    snapshot = monitor.poll(include_slow=True)
    assert snapshot.values["total_jobs"] == 1234
    assert snapshot.values["card_id"] == "0x65006500"
    assert snapshot.values["controller_model"] == "RDC6442G"


def test_offline_when_first_read_times_out() -> None:
    monitor, reader, _ = make({})
    assert monitor.poll(include_slow=True).online is False
    assert reader.reads == [0x0400]  # gave up straight away


def test_goes_offline_and_back() -> None:
    memory = idle_memory()
    monitor, _, _ = make(memory)
    assert monitor.poll(include_slow=False).online
    memory[0x0400] = OSError("network unreachable")
    assert monitor.poll(include_slow=False).online is False
    assert monitor.latest.online is False
    memory[0x0400] = encode(0)
    assert monitor.poll(include_slow=False).online


def test_unreadable_metric_is_skipped_from_then_on() -> None:
    memory = idle_memory()
    memory[0x0421] = RuidaError("refused")
    monitor, reader, _ = make(memory)
    monitor.poll(include_slow=False)
    reader.reads.clear()
    monitor.poll(include_slow=False)
    assert 0x0421 not in reader.reads
    assert "x_position" in monitor.unsupported


def test_timeout_after_first_read_leaves_value_alone() -> None:
    memory = idle_memory()
    del memory[0x0431]
    monitor, _, _ = make(memory)
    snapshot = monitor.poll(include_slow=False)
    assert snapshot.online
    assert snapshot.values["y_position"] is None


def test_undecodable_value_becomes_unknown() -> None:
    class Broken(Metric):
        def decode(self, data: bytes) -> int:
            raise ValueError("nope")

    monitor = Monitor(FakeReader({0x0001: b"\xff"}), metrics=(Broken("bad", "Bad", 1),))
    assert monitor.poll(include_slow=True).values["bad"] is None


def test_all_reads_unsupported_means_offline() -> None:
    monitor, _, _ = make({0x0400: RuidaError("refused")})
    monitor.metrics = monitor.metrics[:1]
    assert monitor.poll(include_slow=False).online is False


def test_tracks_a_job() -> None:
    memory = idle_memory()
    monitor, _, clock = make(memory)
    monitor.poll(include_slow=False)

    memory[0x0400] = encode(STATUS_JOB | STATUS_MOVING)
    memory[0x0620] = encode(2000)
    memory[0x0621] = encode(500)
    clock.t += 2
    values = monitor.poll(include_slow=False).values
    assert values["state"] == "running"
    assert values["job_running"] is True
    assert values["moving"] is True
    assert values["job_started"] == NOW.isoformat()
    assert values["job_elapsed"] == 0
    assert values["job_progress"] == 25.0

    memory[0x0400] = encode(STATUS_JOB | STATUS_PAUSED)
    clock.t += 30
    values = monitor.poll(include_slow=False).values
    assert values["state"] == "paused"
    assert values["paused"] is True
    assert values["job_elapsed"] == 30

    memory[0x0400] = encode(0)
    clock.t += 15
    values = monitor.poll(include_slow=False).values
    assert values["state"] == "idle"
    assert values["last_job_duration"] == 45
    assert values["last_job_finished"] == NOW.isoformat()
    assert values["jobs_since_start"] == 1
    assert values["job_elapsed"] is None
    assert values["job_progress"] is None


def test_job_already_running_at_startup_has_no_start_time() -> None:
    memory = idle_memory()
    memory[0x0400] = encode(STATUS_JOB)
    monitor, _, _ = make(memory)
    values = monitor.poll(include_slow=False).values
    assert values["state"] == "running"
    assert values["job_started"] is None
    assert values["job_elapsed"] is None

    memory[0x0400] = encode(0)
    values = monitor.poll(include_slow=False).values
    assert values["last_job_duration"] is None
    assert values["jobs_since_start"] == 1


@pytest.mark.parametrize("unreadable", [False, True])
def test_no_progress_without_a_length(unreadable: bool) -> None:
    memory = idle_memory()
    memory[0x0400] = encode(STATUS_JOB)
    memory[0x0621] = encode(5)
    if unreadable:
        memory[0x0620] = RuidaError("refused")
    monitor, _, _ = make(memory)
    assert monitor.poll(include_slow=False).values["job_progress"] is None


def test_warns_when_controller_never_answers(caplog: pytest.LogCaptureFixture) -> None:
    monitor, _, clock = make({})
    monitor.poll(include_slow=False)
    assert caplog.text.count("isn't answering") == 1

    clock.t += 60
    monitor.poll(include_slow=False)
    assert caplog.text.count("isn't answering") == 1  # not every poll

    clock.t += 600
    monitor.poll(include_slow=False)
    assert caplog.text.count("isn't answering") == 2  # a reminder


def test_warns_once_when_controller_stops_answering(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    memory = idle_memory()
    monitor, _, clock = make(memory)
    monitor.poll(include_slow=False)
    assert "controller is online" in caplog.text

    del memory[0x0400]
    monitor.poll(include_slow=False)
    clock.t += 60
    monitor.poll(include_slow=False)
    assert caplog.text.count("stopped answering") == 1
    assert "isn't answering" not in caplog.text
