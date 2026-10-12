import pytest

from laser_cutterd.config import ConfigError, Settings

BASE = {"RUIDA_HOST": "192.0.2.10"}


def test_defaults() -> None:
    settings = Settings.from_env(BASE)
    assert settings == Settings(ruida_host="192.0.2.10")
    assert settings.ruida_port == 50200
    assert settings.ruida_local_port == 40200
    assert settings.ruida_magic == 0x88
    assert settings.mqtt_host is None
    assert settings.client_id == "laser-cutterd-laser_cutter"


def test_requires_ruida_host() -> None:
    with pytest.raises(ConfigError, match="RUIDA_HOST"):
        Settings.from_env({"RUIDA_HOST": "  "})


def test_reads_environment() -> None:
    env = {
        **BASE,
        "RUIDA_PORT": "50201",
        "RUIDA_LOCAL_PORT": "40201",
        "RUIDA_MAGIC": "0x11",
        "RUIDA_TIMEOUT": "2.5",
        "RUIDA_RETRIES": "0",
        "POLL_INTERVAL": "5",
        "SLOW_POLL_INTERVAL": "60",
        "MQTT_HOST": "broker",
        "MQTT_PORT": "8883",
        "MQTT_USERNAME": "laser",
        "MQTT_PASSWORD": "dummy",  # not-a-secret
        "MQTT_TLS": "yes",
        "MQTT_CLIENT_ID": "my-client",
        "MQTT_TOPIC_PREFIX": "shop/lasers",
        "HA_DISCOVERY": "off",
        "HA_DISCOVERY_PREFIX": "ha",
        "DEVICE_ID": "x700",
        "DEVICE_NAME": "Big Laser",
        "DEVICE_MANUFACTURER": "Acme",
        "DEVICE_MODEL": "Z9",
        "HOST": "0.0.0.0",  # noqa: S104 - a test value
        "PORT": "9000",
        "LOG_LEVEL": "debug",
    }
    s = Settings.from_env(env)
    assert (s.ruida_port, s.ruida_local_port, s.ruida_magic) == (50201, 40201, 0x11)
    assert (s.ruida_timeout, s.ruida_retries) == (2.5, 0)
    assert (s.poll_interval, s.slow_poll_interval) == (5, 60)
    assert (s.mqtt_host, s.mqtt_port, s.mqtt_tls) == ("broker", 8883, True)
    assert (s.mqtt_username, s.mqtt_password) == ("laser", "dummy")
    assert s.client_id == "my-client"
    assert (s.mqtt_topic_prefix, s.ha_discovery, s.ha_discovery_prefix) == (
        "shop/lasers",
        False,
        "ha",
    )
    assert (s.device_id, s.device_name, s.device_manufacturer, s.device_model) == (
        "x700",
        "Big Laser",
        "Acme",
        "Z9",
    )
    assert (s.port, s.log_level) == (9000, "DEBUG")


def test_password_is_not_in_repr() -> None:
    env = {**BASE, "MQTT_USERNAME": "u", "MQTT_PASSWORD": "hunter2"}  # not-a-secret
    assert "hunter2" not in repr(Settings.from_env(env))


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PORT", "eighty", "PORT must be a whole number"),
        ("PORT", "0", "PORT must be 1-65535"),
        ("RUIDA_PORT", "65536", "RUIDA_PORT must be 1-65535"),
        ("RUIDA_MAGIC", "0x100", "RUIDA_MAGIC must be 0-255"),
        ("RUIDA_TIMEOUT", "soon", "RUIDA_TIMEOUT must be a number"),
        ("RUIDA_TIMEOUT", "0", "RUIDA_TIMEOUT must be at least 0.1"),
        ("POLL_INTERVAL", "nan", "POLL_INTERVAL must be at least"),
        ("SLOW_POLL_INTERVAL", "1", "SLOW_POLL_INTERVAL must be at least 2"),
        ("MQTT_TLS", "maybe", "MQTT_TLS must be true or false"),
        ("DEVICE_ID", "Laser Cutter", "DEVICE_ID"),
        ("MQTT_TOPIC_PREFIX", "a/#", "MQTT_TOPIC_PREFIX"),
        ("LOG_LEVEL", "loud", "LOG_LEVEL"),
        ("MQTT_PASSWORD", "x", "MQTT_USERNAME"),
    ],
)
def test_rejects_bad_values(name: str, value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({**BASE, name: value})


@pytest.mark.parametrize("value", ["false", "0", "no"])
def test_flag_false(value: str) -> None:
    assert Settings.from_env({**BASE, "MQTT_TLS": value}).mqtt_tls is False
