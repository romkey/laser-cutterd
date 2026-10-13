"""Monitor a Ruida laser controller and publish its status over MQTT.

Run with no arguments to start the daemon. Configuration comes from
environment variables; see docs/configuration.md.
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from types import FrameType

from laser_cutterd import __version__
from laser_cutterd.config import ConfigError, Settings
from laser_cutterd.metrics import METRICS
from laser_cutterd.monitor import Monitor, Snapshot
from laser_cutterd.mqtt import Publisher
from laser_cutterd.ruida import RuidaClient, RuidaError
from laser_cutterd.server import make_server

logger = logging.getLogger("laser_cutterd")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laser-cutterd", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="read every known value from the controller once, print it as "
        "JSON with the raw bytes, and exit (to check units against the panel)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="log every datagram sent to and received from the controller "
        "(same as RUIDA_TRACE=true)",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigError as e:
        parser.exit(2, f"laser-cutterd: {e}\n")

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # The I/O trace is separate from LOG_LEVEL=DEBUG, which it would flood.
    trace = args.trace or settings.ruida_trace
    logging.getLogger("laser_cutterd.ruida.io").setLevel(
        logging.DEBUG if trace else logging.INFO
    )
    client = make_client(settings)
    try:
        client.open()
    except OSError as e:
        logger.error(
            "can't listen on UDP %d for the controller's replies: %s",
            settings.ruida_local_port,
            e,
        )
        return 1
    with client:
        if args.dump:
            return dump(client)
        return run(settings, client)


def make_client(settings: Settings) -> RuidaClient:
    return RuidaClient(
        settings.ruida_host,
        port=settings.ruida_port,
        local_port=settings.ruida_local_port,
        magic=settings.ruida_magic,
        timeout=settings.ruida_timeout,
        retries=settings.ruida_retries,
    )


def dump(client: RuidaClient) -> int:
    results = []
    for metric in METRICS:
        entry: dict[str, object] = {
            "key": metric.key,
            "address": f"0x{metric.address:04X}",
            "unit": metric.unit,
            "verified": metric.verified,
        }
        try:
            data = client.read(metric.address)
        except RuidaError as e:
            entry["error"] = str(e)
        else:
            entry["raw"] = data.hex()
            entry["value"] = metric.decode(data)
        results.append(entry)
    sys.stdout.write(json.dumps(results, indent=2) + "\n")
    return 0 if any("raw" in r for r in results) else 1


def run(settings: Settings, client: RuidaClient) -> int:
    stop = threading.Event()

    # `docker stop` sends SIGTERM. Finish the current poll and shut down.
    def on_signal(signum: int, frame: FrameType | None) -> None:
        logger.info("received signal %d, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    monitor = Monitor(client)
    server = make_server(settings.host, settings.port, lambda: monitor.latest.as_json())
    threading.Thread(target=server.serve_forever, daemon=True).start()

    publisher = Publisher(settings) if settings.mqtt_host else None
    if publisher is None:
        logger.warning("MQTT_HOST isn't set; only serving /status over HTTP")
    else:
        publisher.start()

    logger.info(
        "laser-cutterd %s watching %s:%d (replies to UDP %d); HTTP on %s:%d",
        __version__,
        settings.ruida_host,
        settings.ruida_port,
        settings.ruida_local_port,
        settings.host,
        server.server_port,
    )
    try:
        poll_loop(
            monitor,
            publisher.publish if publisher else lambda snapshot: None,
            poll_interval=settings.poll_interval,
            slow_poll_interval=settings.slow_poll_interval,
            stop=stop,
        )
    finally:
        if publisher is not None:
            publisher.stop()
        server.shutdown()
        server.server_close()
    logger.info("stopped")
    return 0


def poll_loop(
    monitor: Monitor,
    publish: Callable[[Snapshot], None],
    *,
    poll_interval: float,
    slow_poll_interval: float,
    stop: threading.Event,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll until `stop` is set; slow metrics every slow_poll_interval."""
    next_slow = clock()
    while not stop.is_set():
        started = clock()
        include_slow = started >= next_slow
        snapshot = monitor.poll(include_slow=include_slow)
        # Offline: try the slow ones again as soon as it's back.
        if include_slow and snapshot.online:
            next_slow = started + slow_poll_interval
        publish(snapshot)
        stop.wait(max(0.0, poll_interval - (clock() - started)))


if __name__ == "__main__":
    sys.exit(main())
