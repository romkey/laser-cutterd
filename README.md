# laser-cutterd

[![CI](https://github.com/romkey/laser-cutterd/actions/workflows/ci.yaml/badge.svg)](https://github.com/romkey/laser-cutterd/actions/workflows/ci.yaml)
[![Lint](https://github.com/romkey/laser-cutterd/actions/workflows/lint.yaml/badge.svg)](https://github.com/romkey/laser-cutterd/actions/workflows/lint.yaml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A daemon that watches a laser cutter with a Ruida controller over the
network and reports everything it can read from it (whether a job is running
or paused, head position, job progress and timing, lifetime counters, flash
space and machine settings) to MQTT, with Home Assistant discovery so the
cutter shows up as a device with no YAML. Built for a Redsail X700 with an
RDC6442G controller; other Ruida RDC644x boards should work too. It only
reads: it never moves the head, fires the laser or changes a setting.

> **AI disclosure:** This project was written by AI under human oversight,
> following [these rules](https://github.com/romkey/python-ai-rules/blob/main/RULES.md).

## What's here

| Path | What it is |
|------|------------|
| `src/laser_cutterd/ruida.py` | Read-only client for the Ruida UDP protocol: byte scrambling, checksums, memory reads |
| `src/laser_cutterd/metrics.py` | Every value read from the controller: its address, decoding, units and Home Assistant metadata |
| `src/laser_cutterd/monitor.py` | Polls the controller, works out the machine state and times jobs |
| `src/laser_cutterd/mqtt.py`, `discovery.py` | MQTT publishing and Home Assistant device discovery |
| `src/laser_cutterd/server.py` | HTTP: `GET /healthz`, `GET /status` |
| `src/laser_cutterd/config.py` | Settings from environment variables |
| `src/laser_cutterd/__main__.py` | The `laser-cutterd` command: daemon, or `--dump` |
| `tests/` | pytest tests, with a fake UDP controller |
| `docs/configuration.md` | Every environment variable |

## Technical details

### Talking to the controller

Ruida never published its protocol, and there's no maintained Python
library for it. `ruida.py` implements the small read-only part needed here,
following the reverse-engineering in
[MeerK40t's Ruida driver](https://github.com/meerk40t/meerk40t/tree/main/meerk40t/ruida),
the [Ruida wiki](https://edutechwiki.unige.ch/en/Ruida),
[jnweiger/ruida-laser](https://github.com/jnweiger/ruida-laser) and
[ruida_bridge](https://github.com/MrkyWtrs/ruida_bridge):

- The daemon sends UDP datagrams to port 50200 on the controller, which
  replies to port **40200** on the daemon's host. Only one program per host
  can listen there, so don't run LightBurn or RDWorks on the same machine.
  Running them on another machine is fine.
- Each datagram is a 16-bit checksum, then the command with every byte
  scrambled (`RUIDA_MAGIC`, 0x88 for the RDC6442G). The controller ACKs each
  one, then replies.
- Reading memory address `HHLL` is `DA 00 HH LL`; the reply is
  `DA 01 HH LL` and a value in five 7-bit bytes.

### What it reports

Read from the controller, with the address it comes from. "Verified" means
a project tested on real hardware decodes it this way; the rest are labels
from MeerK40t's emulator or the wiki whose units nobody has confirmed, so
compare them with your controller's panel (or LightBurn's Machine Settings)
using `laser-cutterd --dump`, which prints each raw reply. Slow values are
read every `SLOW_POLL_INTERVAL` (5 minutes).

| Key | Name | Address | Unit | Read | Verified |
|-----|------|---------|------|------|----------|
| `status_raw` | Status bits | `0x0400` |  | every poll | yes |
| `x_position` | X position | `0x0421` | mm | every poll | yes |
| `y_position` | Y position | `0x0431` | mm | every poll | yes |
| `z_position` | Z position | `0x0441` | mm | every poll | yes |
| `u_position` | U position | `0x0451` | mm | every poll | no |
| `job_length` | Job length | `0x0620` |  | every poll | no |
| `job_length_done` | Job length done | `0x0621` |  | every poll | no |
| `total_on_time` | Total power-on time | `0x0401` | s | slow | no |
| `total_work_time` | Total processing time | `0x0402` | s | slow | no |
| `total_laser_on_time` | Total laser-on time | `0x0411` | s | slow | no |
| `total_jobs` | Total jobs | `0x0403` |  | slow | no |
| `previous_job_time` | Previous job time (controller) | `0x0408` | s | slow | no |
| `saved_files` | Saved files | `0x0405` |  | slow | yes |
| `flash_total` | Flash size | `0x0406` | B | slow | no |
| `flash_free` | Flash free | `0x0407` | B | slow | no |
| `x_total_travel` | X total travel | `0x0423` | m | slow | no |
| `y_total_travel` | Y total travel | `0x0433` | m | slow | no |
| `z_total_travel` | Z total travel | `0x0443` | m | slow | no |
| `u_total_travel` | U total travel | `0x0453` | m | slow | no |
| `card_id` | Controller card ID | `0x057E` |  | slow | yes |
| `mainboard_version` | Mainboard version | `0x057F` |  | slow | no |
| `x_range` | X travel range | `0x0026` | mm | slow | yes |
| `y_range` | Y travel range | `0x0036` | mm | slow | yes |
| `z_range` | Z travel range | `0x0046` | mm | slow | yes |
| `x_max_speed` | X max speed | `0x0023` | mm/s | slow | yes |
| `y_max_speed` | Y max speed | `0x0033` | mm/s | slow | yes |
| `z_max_speed` | Z max speed | `0x0043` | mm/s | slow | yes |
| `laser_1_frequency` | Laser 1 PWM frequency | `0x0011` | Hz | slow | yes |
| `laser_1_min_power` | Laser 1 minimum power | `0x0012` | % | slow | yes |
| `laser_1_max_power` | Laser 1 maximum power | `0x0013` | % | slow | yes |
| `laser_2_frequency` | Laser 2 PWM frequency | `0x0017` | Hz | slow | yes |
| `laser_2_min_power` | Laser 2 minimum power | `0x0018` | % | slow | yes |
| `laser_2_max_power` | Laser 2 maximum power | `0x0019` | % | slow | yes |

Worked out by the daemon:

| Key | Meaning |
|-----|---------|
| `state` | `idle`, `running` or `paused`, from status bit 0 (job active) and bit 1 (with bit 0: paused) |
| `job_running`, `paused`, `moving` | Booleans from the status bits (moving is bit 24, per MeerK40t) |
| `job_progress` | % done, from `job_length_done` / `job_length` (unverified) |
| `job_started`, `job_elapsed` | When the current job started, and seconds since, timed by the daemon |
| `last_job_duration`, `last_job_finished` | The last job's length in seconds and when it ended, timed by the daemon |
| `jobs_since_start` | Jobs finished since the daemon started |
| `controller_model` | The board, from its card ID (e.g. `RDC6442G` for `0x65006500`) |
| `online`, `updated` | Whether the controller is answering, and when it last did |

A value the controller refuses to give is logged once and not asked for
again; it reads as unknown.

### MQTT

With the default `MQTT_TOPIC_PREFIX` and `DEVICE_ID`:

| Topic | Payload | Retained |
|-------|---------|----------|
| `laser-cutterd/laser_cutter/state` | JSON with every key above (`null` if unknown) | yes |
| `laser-cutterd/laser_cutter/controller` | `online` / `offline`: is the controller answering | yes |
| `laser-cutterd/laser_cutter/availability` | `online` / `offline`: is the daemon running (MQTT last will) | yes |
| `homeassistant/device/laser_cutter/config` | Home Assistant device discovery | yes |

### Home Assistant

The daemon publishes one [device discovery](https://www.home-assistant.io/integrations/mqtt/#device-discovery-payload)
message, so the MQTT integration creates a device named `DEVICE_NAME` with
a sensor or binary sensor for each value. Settings and identifiers are
under Diagnostic; a few rarely useful ones (raw status bits, U axis, laser 2)
start disabled. Entities go unavailable when the daemon or the controller
is offline, except **Controller** (connectivity), which says which. The
daemon resends discovery and state whenever Home Assistant restarts.

Things to automate on: `binary_sensor.laser_cutter_job_running` turning
off ("job done"), `sensor.laser_cutter_state` becoming `paused`, or
`binary_sensor.laser_cutter_controller` going off.

### Command line and HTTP

- `laser-cutterd` runs the daemon until SIGTERM or Ctrl-C.
- `laser-cutterd --dump` reads every value once and prints JSON with the
  address, raw bytes and decoded value (needs only `RUIDA_HOST`).
- `GET /` lists the endpoints.
- `GET /healthz` returns `{"status": "ok", "version": ...}`;
  `GET /status` returns the latest state JSON.

### Troubleshooting

Once it reaches the controller the log says `controller is online`; until
then it warns `controller isn't answering` (at startup, then every 10
minutes), and `/status` shows `"online": false`. The usual cause in Docker
is the controller's replies to UDP 40200 not reaching the container: run
it with `network_mode: host`, as `docker-compose.prod.yaml` does (a bridge
network with a `ports:` mapping isn't enough). Then check `RUIDA_HOST`, that
the laser is on, and that nothing else on the host (LightBurn, RDWorks,
another copy of this) is using UDP 40200. `laser-cutterd --dump` with
`LOG_LEVEL=DEBUG` shows each read.

### Caveats

- The protocol is reverse-engineered; firmware may differ. Not yet tried
  against a real RDC6442G (see the Verified column).
- The daemon polls while LightBurn sends jobs from another machine. The
  controller handles one datagram at a time and MeerK40t and ruida_bridge
  do the same, but if uploads ever fail, raise `POLL_INTERVAL`.

## Running it

Requires Docker. Configuration comes from environment variables, kept in
`.env` (see [docs/configuration.md](docs/configuration.md)):

```bash
cp .env.example .env   # then edit it
docker compose -f docker-compose.prod.yaml up -d
```

That runs the latest release from `ghcr.io/romkey/laser-cutterd`; set
`APP_VERSION=X.Y.Z` to pin one. Set at least `RUIDA_HOST` and `MQTT_HOST`
in `.env`. It uses host networking so the controller's replies reach it
(on Linux; Docker Desktop needs host networking turned on), and serves
http://localhost:8000/status.

Check that it can see the controller, and compare values with the panel:

```bash
docker compose -f docker-compose.prod.yaml run --rm app laser-cutterd --dump
```

### With pip

Needs Python 3.14 or newer. Install into a virtual environment, from a
release's wheel (or `pip install laser-cutterd` if it's published on PyPI):

```bash
python3 -m venv ~/.venvs/laser-cutterd
~/.venvs/laser-cutterd/bin/pip install https://github.com/romkey/laser-cutterd/releases/download/vX.Y.Z/laser_cutterd-X.Y.Z-py3-none-any.whl
~/.venvs/laser-cutterd/bin/laser-cutterd --version
```

Set its environment variables yourself (see
[docs/configuration.md](docs/configuration.md)); it doesn't read `.env`.

## Development

With Docker, nothing else to install:

```bash
docker compose up --build                                  # run from the working tree
docker compose -f docker-compose.test.yaml run --rm test   # tests with coverage
docker compose -f docker-compose.lint.yaml run --rm lint   # every lint check, as CI runs them
```

With [uv](https://docs.astral.sh/uv/), which installs the exact versions
CI tests (`uv.lock`):

```bash
uv sync
uv run pytest
uv run mypy
uv run laser-cutterd
```

With pip (25.1 or newer, for `--group`). pip doesn't read `uv.lock`, so it
may pick newer dependency versions than CI tested:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e . --group dev
pytest
mypy
laser-cutterd
```

Either way, `uvx pre-commit install` (or `pip install pre-commit &&
pre-commit install`) runs the lint checks on every commit.

## License

[MIT](LICENSE)
