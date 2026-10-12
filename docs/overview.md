# laser-cutterd overview

**What:** a daemon that polls a Ruida laser controller (built for a Redsail
X700 with an RDC6442G) over UDP and publishes its status to MQTT, with Home
Assistant discovery.
**Runs as:** a long-running daemon; HTTP `/healthz` and `/status` on port 8000.
**Talks to:** the controller (UDP 50200 out, replies on UDP 40200), an MQTT
broker.

## Files

- `src/laser_cutterd/__main__.py`: command line (`--dump`), poll loop, signals.
- `src/laser_cutterd/config.py`: `Settings` from environment variables.
- `src/laser_cutterd/ruida.py`: the UDP protocol: scrambling, checksums,
  memory reads (`RuidaClient`). Read-only.
- `src/laser_cutterd/metrics.py`: every memory address read, its decoding
  and Home Assistant metadata; status bits and `machine_state()`.
- `src/laser_cutterd/monitor.py`: `Monitor.poll()` reads metrics into a
  `Snapshot`, derives state and times jobs.
- `src/laser_cutterd/discovery.py`: topics and the device-discovery payload.
- `src/laser_cutterd/mqtt.py`: `Publisher` (paho-mqtt): last will, retained
  state, resends discovery when Home Assistant restarts.
- `src/laser_cutterd/server.py`: HTTP handler.
- `tests/`: pytest; `fakes.py` has a fake UDP controller.
- `Dockerfile`: `dev` and `production` stages.
- `docker-compose.yaml` (dev), `.test`, `.lint`, `.prod` (host networking).

## Conventions

- All configuration from environment variables; see docs/configuration.md.
- Protocol facts come from cited sources; unverified units are flagged
  `verified=False` in `metrics.py`.
- Never send the controller anything but memory reads.
- Rules: https://github.com/romkey/python-ai-rules/blob/main/RULES.md
