# Configuration

The daemon reads these environment variables at startup. Put them in `.env`
(copy `.env.example`); compose passes it to the container. An invalid
value stops the daemon with a message naming the variable.

## The laser controller

| Variable | Default | Meaning |
|----------|---------|---------|
| `RUIDA_HOST` | required | The Ruida controller's IP address or host name |
| `RUIDA_PORT` | `50200` | UDP port the controller listens on |
| `RUIDA_LOCAL_PORT` | `40200` | UDP port on this host the controller replies to. Only one program per host can use it, so LightBurn or RDWorks can't run on the same machine |
| `RUIDA_MAGIC` | `0x88` | Byte-scrambling key, decimal or `0x` hex, 0-255. `0x88` for RDC644x (including the RDC6442G), 654x, 633x and 320 boards; `0x11` for the 634XG |
| `RUIDA_TIMEOUT` | `1.0` | Seconds to wait for each reply (at least 0.1) |
| `RUIDA_RETRIES` | `2` | How many times to resend an unanswered read, 0-10 |
| `POLL_INTERVAL` | `2` | Seconds between reads of status, position and job progress (at least 0.5) |
| `SLOW_POLL_INTERVAL` | `300` | Seconds between reads of the lifetime counters, flash space and settings (at least `POLL_INTERVAL`) |

## MQTT

| Variable | Default | Meaning |
|----------|---------|---------|
| `MQTT_HOST` | unset | The broker. Unset: no MQTT, only HTTP `/status` |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` | unset | Username, if the broker needs one |
| `MQTT_PASSWORD` | unset | Password; needs `MQTT_USERNAME` |
| `MQTT_TLS` | `false` | `true` to connect with TLS, checking the broker's certificate against the system CAs |
| `MQTT_CLIENT_ID` | `laser-cutterd-<DEVICE_ID>` | MQTT client id; must be unique on the broker |
| `MQTT_TOPIC_PREFIX` | `laser-cutterd` | Topics are `<prefix>/<DEVICE_ID>/...`; no `#`, `+` or spaces |

## Home Assistant

| Variable | Default | Meaning |
|----------|---------|---------|
| `HA_DISCOVERY` | `true` | Publish MQTT discovery so Home Assistant creates the device and its entities |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Must match the MQTT integration's discovery prefix |
| `DEVICE_ID` | `laser_cutter` | Lowercase letters, digits and `_`. Used in topics, unique ids and entity ids; changing it makes a new device |
| `DEVICE_NAME` | `Laser Cutter` | Device name in Home Assistant |
| `DEVICE_MANUFACTURER` | `Redsail` | Shown on the device page |
| `DEVICE_MODEL` | `X700` | Shown on the device page |

## The daemon

| Variable | Default | Meaning |
|----------|---------|---------|
| `HOST` | `127.0.0.1` (`0.0.0.0` in the image) | Address for the HTTP server |
| `PORT` | `8000` | Port for `GET /healthz` and `GET /status`. With the production compose file's host networking, change its health check too |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. `DEBUG` logs each UDP exchange problem |
