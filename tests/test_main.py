import json
import signal
import socket
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any, ClassVar

import pytest
from fakes import encode, fake_controller, memory

from laser_cutterd import __main__ as cli
from laser_cutterd import __version__
from laser_cutterd.config import Settings
from laser_cutterd.monitor import Monitor, Snapshot
from laser_cutterd.ruida import RuidaClient


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"laser-cutterd {__version__}\n"


def test_bad_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RUIDA_HOST", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
    assert "RUIDA_HOST" in capsys.readouterr().err


def test_port_in_use_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("", 0))
        monkeypatch.setenv("RUIDA_HOST", "127.0.0.1")
        monkeypatch.setenv("RUIDA_LOCAL_PORT", str(taken.getsockname()[1]))
        assert cli.main([]) == 1


def test_dump(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    values = {0x0400: encode(1), 0x057F: b"V9\x00"}
    with (
        fake_controller(memory(values)) as port,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as free,
    ):
        free.bind(("", 0))
        local_port = free.getsockname()[1]
        free.close()
        for name, value in {
            "RUIDA_HOST": "127.0.0.1",
            "RUIDA_PORT": str(port),
            "RUIDA_LOCAL_PORT": str(local_port),
            "RUIDA_TIMEOUT": "0.1",
            "RUIDA_RETRIES": "0",
        }.items():
            monkeypatch.setenv(name, value)
        assert cli.main(["--dump"]) == 0
    dumped = {entry["key"]: entry for entry in json.loads(capsys.readouterr().out)}
    assert dumped["status_raw"]["value"] == 1
    assert dumped["status_raw"]["address"] == "0x0400"
    assert dumped["mainboard_version"]["value"] == "V9"
    assert "error" in dumped["x_position"]


def test_dump_with_no_answers_fails(capsys: pytest.CaptureFixture[str]) -> None:
    with fake_controller(memory({})) as port:
        client = RuidaClient(
            "127.0.0.1", port=port, local_port=0, timeout=0.01, retries=0
        )
        with client:
            assert cli.dump(client) == 1


class FakeMonitor(Monitor):
    def __init__(self, online: list[bool]) -> None:
        super().__init__(RuidaClient("127.0.0.1"))
        self.online_sequence = online
        self.calls: list[bool] = []

    def poll(self, *, include_slow: bool) -> Snapshot:
        self.calls.append(include_slow)
        return Snapshot(online=self.online_sequence[len(self.calls) - 1])


def test_poll_loop_reads_slow_metrics_on_schedule() -> None:
    now = [0.0]
    stop = threading.Event()
    published: list[Snapshot] = []
    # online, offline (slow retried next time), online, then 3 fast polls
    monitor = FakeMonitor([True, False, True, True, True, True])

    def publish(snapshot: Snapshot) -> None:
        published.append(snapshot)
        now[0] += 2  # each poll "takes" a poll interval
        if len(published) == 6:
            stop.set()

    cli.poll_loop(
        monitor,
        publish,
        poll_interval=2,
        slow_poll_interval=5,
        stop=stop,
        clock=lambda: now[0],
    )
    # t=0 slow (next at 5); t=2 fast; t=4 fast; t=6 slow... with the
    # offline poll at t=2 not counting since only slow polls reschedule.
    assert monitor.calls == [True, False, False, True, False, False]
    assert len(published) == 6


class FakePublisher:
    instances: ClassVar[list[FakePublisher]] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started = False
        self.stopped = False
        self.published: list[Snapshot] = []
        FakePublisher.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def publish(self, snapshot: Snapshot) -> None:
        self.published.append(snapshot)


@pytest.mark.parametrize("mqtt_host", ["broker.local", None])
def test_run_wires_everything_together(
    monkeypatch: pytest.MonkeyPatch, mqtt_host: str | None
) -> None:
    handlers: dict[int, Callable[..., Any]] = {}
    monkeypatch.setattr(signal, "signal", handlers.__setitem__)
    monkeypatch.setattr(cli, "Publisher", FakePublisher)
    FakePublisher.instances.clear()

    def fake_loop(
        monitor: Monitor, publish: Callable[[Snapshot], None], **kwargs: Any
    ) -> None:
        publish(Snapshot(online=True))
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert kwargs["stop"].is_set()

    monkeypatch.setattr(cli, "poll_loop", fake_loop)
    settings = replace(Settings(ruida_host="127.0.0.1"), mqtt_host=mqtt_host, port=0)
    with RuidaClient("127.0.0.1", local_port=0) as client:
        assert cli.run(settings, client) == 0

    if mqtt_host:
        (publisher,) = FakePublisher.instances
        assert publisher.started
        assert publisher.stopped
        assert len(publisher.published) == 1
    else:
        assert FakePublisher.instances == []
