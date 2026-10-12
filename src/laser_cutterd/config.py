"""Settings, read once at startup from environment variables.

Every variable is documented in docs/configuration.md and listed, with a
safe example value, in .env.example.
"""

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from laser_cutterd import ruida


class ConfigError(ValueError):
    """An environment variable is set to something unusable."""


# Home Assistant object ids and MQTT topic levels: keep them plain.
_ID = re.compile(r"[a-z0-9_]+")
_TOPIC = re.compile(r"[^#+\s]+")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    ruida_host: str
    ruida_port: int = ruida.DEFAULT_PORT
    ruida_local_port: int = ruida.DEFAULT_LOCAL_PORT
    ruida_magic: int = ruida.DEFAULT_MAGIC
    ruida_timeout: float = 1.0
    ruida_retries: int = 2
    poll_interval: float = 2.0
    slow_poll_interval: float = 300.0

    mqtt_host: str | None = None
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = field(default=None, repr=False)
    mqtt_tls: bool = False
    mqtt_client_id: str | None = None
    mqtt_topic_prefix: str = "laser-cutterd"
    ha_discovery: bool = True
    ha_discovery_prefix: str = "homeassistant"

    device_id: str = "laser_cutter"
    device_name: str = "Laser Cutter"
    device_manufacturer: str = "Redsail"
    device_model: str = "X700"

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def client_id(self) -> str:
        return self.mqtt_client_id or f"laser-cutterd-{self.device_id}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        ruida_host = _text(env.get("RUIDA_HOST"))
        if not ruida_host:
            raise ConfigError("RUIDA_HOST must be set to the controller's address")

        username = _text(env.get("MQTT_USERNAME"))
        password = _text(env.get("MQTT_PASSWORD"))
        if password and not username:
            raise ConfigError("MQTT_PASSWORD is set but MQTT_USERNAME isn't")

        poll_interval = _number(
            "POLL_INTERVAL", env.get("POLL_INTERVAL"), cls.poll_interval, 0.5
        )
        slow_poll_interval = _number(
            "SLOW_POLL_INTERVAL",
            env.get("SLOW_POLL_INTERVAL"),
            cls.slow_poll_interval,
            poll_interval,
        )

        log_level = _text(env.get("LOG_LEVEL"), cls.log_level).upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ConfigError(f"LOG_LEVEL {log_level!r} is not a logging level")

        return cls(
            ruida_host=ruida_host,
            ruida_port=_port("RUIDA_PORT", env.get("RUIDA_PORT"), cls.ruida_port),
            ruida_local_port=_port(
                "RUIDA_LOCAL_PORT", env.get("RUIDA_LOCAL_PORT"), cls.ruida_local_port
            ),
            ruida_magic=_integer(
                "RUIDA_MAGIC", env.get("RUIDA_MAGIC"), cls.ruida_magic, 0, 0xFF
            ),
            ruida_timeout=_number(
                "RUIDA_TIMEOUT", env.get("RUIDA_TIMEOUT"), cls.ruida_timeout, 0.1
            ),
            ruida_retries=_integer(
                "RUIDA_RETRIES", env.get("RUIDA_RETRIES"), cls.ruida_retries, 0, 10
            ),
            poll_interval=poll_interval,
            slow_poll_interval=slow_poll_interval,
            mqtt_host=_text(env.get("MQTT_HOST")) or None,
            mqtt_port=_port("MQTT_PORT", env.get("MQTT_PORT"), cls.mqtt_port),
            mqtt_username=username or None,
            mqtt_password=password or None,
            mqtt_tls=_flag("MQTT_TLS", env.get("MQTT_TLS"), cls.mqtt_tls),
            mqtt_client_id=_text(env.get("MQTT_CLIENT_ID")) or None,
            mqtt_topic_prefix=_matching(
                "MQTT_TOPIC_PREFIX",
                env.get("MQTT_TOPIC_PREFIX"),
                cls.mqtt_topic_prefix,
                _TOPIC,
            ),
            ha_discovery=_flag(
                "HA_DISCOVERY", env.get("HA_DISCOVERY"), cls.ha_discovery
            ),
            ha_discovery_prefix=_matching(
                "HA_DISCOVERY_PREFIX",
                env.get("HA_DISCOVERY_PREFIX"),
                cls.ha_discovery_prefix,
                _TOPIC,
            ),
            device_id=_matching("DEVICE_ID", env.get("DEVICE_ID"), cls.device_id, _ID),
            device_name=_text(env.get("DEVICE_NAME"), cls.device_name),
            device_manufacturer=_text(
                env.get("DEVICE_MANUFACTURER"), cls.device_manufacturer
            ),
            device_model=_text(env.get("DEVICE_MODEL"), cls.device_model),
            host=_text(env.get("HOST"), cls.host),
            port=_port("PORT", env.get("PORT"), cls.port),
            log_level=log_level,
        )


def _text(value: str | None, default: str = "") -> str:
    return (value or "").strip() or default


def _integer(name: str, value: str | None, default: int, low: int, high: int) -> int:
    text = _text(value)
    if not text:
        return default
    try:
        number = int(text, 0)  # base 0 accepts 0x88 as well as 136
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {text!r}") from None
    if not low <= number <= high:
        raise ConfigError(f"{name} must be {low}-{high}, not {number}")
    return number


def _port(name: str, value: str | None, default: int) -> int:
    return _integer(name, value, default, 1, 65535)


def _number(name: str, value: str | None, default: float, low: float) -> float:
    text = _text(value)
    if not text:
        return default
    try:
        number = float(text)
    except ValueError:
        raise ConfigError(f"{name} must be a number, not {text!r}") from None
    if not number >= low:  # also rejects nan
        raise ConfigError(f"{name} must be at least {low:g}, not {text}")
    return number


def _flag(name: str, value: str | None, default: bool) -> bool:
    text = _text(value).lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, not {text!r}")


def _matching(
    name: str, value: str | None, default: str, pattern: re.Pattern[str]
) -> str:
    text = _text(value, default)
    if not pattern.fullmatch(text):
        raise ConfigError(f"{name} {text!r} has characters it can't contain")
    return text
