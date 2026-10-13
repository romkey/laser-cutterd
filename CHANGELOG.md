# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `GET /` lists the HTTP endpoints instead of returning 404.

### Fixed

- Warn when the controller doesn't answer, at startup and every 10 minutes
  while it stays silent; before, an unreachable controller logged nothing.
- The startup message names the UDP port the controller's replies come to.

## [0.1.1] - 2026-10-12

### Fixed

- Releases no longer fail to publish the image when GitHub's build cache
  refuses a write ("failed to reserve cache"); the cache is now optional.

## [0.1.0] - 2026-10-12

### Added

- Read-only Ruida UDP client (`ruida.py`): scrambling, checksums, memory reads.
- Polls machine status, X/Y/Z/U position, job progress, lifetime counters,
  flash space, card ID, firmware version, axis ranges and speeds, and laser
  power and frequency settings.
- Derived state (idle/running/paused), job start, elapsed and duration timing.
- MQTT publishing with a last will, and Home Assistant device discovery.
- HTTP `GET /healthz` and `GET /status`.
- `laser-cutterd --dump` to print every raw value for checking units.
