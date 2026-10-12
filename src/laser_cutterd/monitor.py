"""Polls the controller and turns what it reads into a snapshot to publish."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from laser_cutterd.metrics import (
    CARD_MODELS,
    METRICS,
    STATUS,
    STATUS_MOVING,
    Metric,
    State,
    Value,
    machine_state,
)
from laser_cutterd.ruida import RuidaError, RuidaTimeoutError

logger = logging.getLogger(__name__)


class Reader(Protocol):
    def read(self, address: int) -> bytes: ...


# Keys the monitor works out itself rather than reading.
DERIVED_KEYS = (
    "state",
    "job_running",
    "paused",
    "moving",
    "job_progress",
    "job_started",
    "job_elapsed",
    "last_job_duration",
    "last_job_finished",
    "jobs_since_start",
    "controller_model",
)


@dataclass
class Snapshot:
    online: bool
    values: dict[str, Value] = field(default_factory=dict)
    updated: datetime | None = None

    def as_json(self) -> dict[str, Value]:
        """Every key, with None for unknown, so templates never miss one."""
        return {
            "online": self.online,
            "updated": self.updated.isoformat() if self.updated else None,
            **self.values,
        }


class Monitor:
    def __init__(
        self,
        reader: Reader,
        *,
        metrics: tuple[Metric, ...] = METRICS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.reader = reader
        self.metrics = metrics
        self.clock = clock
        self.now = now
        self.values: dict[str, Value] = dict.fromkeys(
            [m.key for m in metrics] + list(DERIVED_KEYS)
        )
        self.values["jobs_since_start"] = 0
        self.online = False
        self.unsupported: set[str] = set()
        self._job_started_at: float | None = None
        self._updated: datetime | None = None
        # The latest snapshot, for other threads (the HTTP server) to read.
        self.latest = self.snapshot()

    def poll(self, *, include_slow: bool) -> Snapshot:
        """Read the controller once. Slow metrics only when asked."""
        wanted = [
            m
            for m in self.metrics
            if (include_slow or not m.slow) and m.key not in self.unsupported
        ]
        read_any = False
        for metric in wanted:
            try:
                data = self.reader.read(metric.address)
            except RuidaTimeoutError:
                if not read_any:
                    return self._offline()
                logger.warning("no answer reading %s", metric.key)
                continue
            except RuidaError as e:
                logger.info("%s isn't readable on this controller: %s", metric.key, e)
                self.unsupported.add(metric.key)
                continue
            except OSError as e:
                logger.warning("network error talking to the controller: %s", e)
                return self._offline()
            read_any = True
            try:
                self.values[metric.key] = metric.decode(data)
            except (ValueError, IndexError) as e:
                logger.warning(
                    "couldn't decode %s from %s: %s", metric.key, data.hex(), e
                )
                self.values[metric.key] = None

        if not read_any:
            return self._offline()
        if not self.online:
            logger.info("controller is online")
        self.online = True
        self._updated = self.now()
        self._derive()
        self.latest = self.snapshot()
        return self.latest

    def snapshot(self) -> Snapshot:
        return Snapshot(self.online, dict(self.values), self._updated)

    def _offline(self) -> Snapshot:
        if self.online:
            logger.warning("controller stopped answering")
        self.online = False
        self.latest = self.snapshot()
        return self.latest

    def _derive(self) -> None:
        values = self.values
        card = values.get("card_id")
        if isinstance(card, str):
            values["controller_model"] = CARD_MODELS.get(card)

        status = values.get(STATUS.key)
        if not isinstance(status, int):
            return
        state = machine_state(status)
        previous = values["state"]
        values["state"] = state.value
        values["job_running"] = state is not State.IDLE
        values["paused"] = state is State.PAUSED
        values["moving"] = bool(status & STATUS_MOVING)

        if state is not State.IDLE and previous == State.IDLE.value:
            self._job_started_at = self.clock()
            values["job_started"] = self.now().isoformat()
            logger.info("job started")
        elif state is not State.IDLE and previous is None:
            # Started up mid-job: it's running, but we don't know since when.
            logger.info("a job is already running")
        elif state is State.IDLE and previous not in (None, State.IDLE.value):
            if self._job_started_at is not None:
                duration = round(self.clock() - self._job_started_at, 1)
                values["last_job_duration"] = duration
                logger.info("job finished after %.1f s", duration)
            else:
                logger.info("job finished")
            values["last_job_finished"] = self.now().isoformat()
            jobs = values["jobs_since_start"]
            values["jobs_since_start"] = (jobs if isinstance(jobs, int) else 0) + 1
            self._job_started_at = None

        if state is State.IDLE:
            values["job_elapsed"] = None
            values["job_progress"] = None
            return
        if self._job_started_at is not None:
            values["job_elapsed"] = round(self.clock() - self._job_started_at, 1)
        values["job_progress"] = _progress(
            values.get("job_length_done"), values.get("job_length")
        )


def _progress(done: Value, total: Value) -> float | None:
    if not isinstance(done, int) or not isinstance(total, int) or total <= 0:
        return None
    return round(min(done / total, 1.0) * 100, 1)
