import pytest
from fakes import encode

from laser_cutterd.metrics import (
    METRICS,
    STATUS_JOB,
    STATUS_MOVING,
    STATUS_PAUSED,
    Kind,
    Metric,
    State,
    machine_state,
)


def test_keys_and_addresses_are_unique() -> None:
    keys = [m.key for m in METRICS]
    addresses = [m.address for m in METRICS]
    assert len(set(keys)) == len(keys)
    assert len(set(addresses)) == len(addresses)


@pytest.mark.parametrize(
    ("metric", "data", "value"),
    [
        (Metric("a", "A", 1), encode(42), 42),
        (Metric("a", "A", 1, Kind.SIGNED, 0.001, precision=2), encode(-1234), -1.23),
        (Metric("a", "A", 1, scale=0.5), encode(3), 1.5),
        (Metric("a", "A", 1, Kind.HEX), encode(0x65006500), "0x65006500"),
        (Metric("a", "A", 1, Kind.TEXT), b"RDC V9 \x00junk", "RDC V9"),
    ],
)
def test_decodes(metric: Metric, data: bytes, value: object) -> None:
    assert metric.decode(data) == value


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (0, State.IDLE),
        (STATUS_PAUSED, State.IDLE),
        (STATUS_MOVING, State.IDLE),
        (STATUS_JOB, State.RUNNING),
        (STATUS_JOB | STATUS_MOVING, State.RUNNING),
        (STATUS_JOB | STATUS_PAUSED, State.PAUSED),
        # MeerK40t's emulator: 22 idle, 21 running, 23 paused.
        (22, State.IDLE),
        (21, State.RUNNING),
        (23, State.PAUSED),
    ],
)
def test_machine_state(status: int, state: State) -> None:
    assert machine_state(status) is state
