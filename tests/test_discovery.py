from laser_cutterd import __version__
from laser_cutterd.config import Settings
from laser_cutterd.discovery import Topics, discovery_config
from laser_cutterd.metrics import METRICS
from laser_cutterd.monitor import DERIVED_KEYS

SETTINGS = Settings(ruida_host="laser.local", device_id="x700", device_name="Laser")


def test_topics() -> None:
    topics = Topics.for_settings(SETTINGS)
    assert topics.state == "laser-cutterd/x700/state"
    assert topics.availability == "laser-cutterd/x700/availability"
    assert topics.controller == "laser-cutterd/x700/controller"
    assert topics.discovery == "homeassistant/device/x700/config"
    assert topics.ha_status == "homeassistant/status"


def test_device_and_origin() -> None:
    config = discovery_config(SETTINGS)
    assert config["device"] == {
        "identifiers": ["laser_cutterd_x700"],
        "name": "Laser",
        "manufacturer": "Redsail",
        "model": "X700",
    }
    assert config["origin"]["name"] == "laser-cutterd"
    assert config["origin"]["sw_version"] == __version__


def test_every_value_has_an_entity() -> None:
    components = discovery_config(SETTINGS)["components"]
    keys = {m.key for m in METRICS} | set(DERIVED_KEYS) | {"updated", "controller"}
    assert set(components) == keys
    for key, component in components.items():
        assert component["platform"] in {"sensor", "binary_sensor"}
        assert component["unique_id"] == f"x700_{key}"
        assert component["default_entity_id"].endswith(f".x700_{key}")


def test_metric_entities() -> None:
    components = discovery_config(SETTINGS)["components"]
    x = components["x_position"]
    assert x["value_template"] == "{{ value_json.x_position }}"
    assert x["unit_of_measurement"] == "mm"
    assert x["device_class"] == "distance"
    assert x["state_topic"] == "laser-cutterd/x700/state"
    assert "entity_category" not in x
    assert "enabled_by_default" not in x
    assert components["status_raw"]["enabled_by_default"] is False
    assert components["card_id"]["entity_category"] == "diagnostic"


def test_controller_connectivity_is_available_whenever_the_daemon_is() -> None:
    controller = discovery_config(SETTINGS)["components"]["controller"]
    assert controller["state_topic"] == "laser-cutterd/x700/controller"
    assert "value_template" not in controller
    assert "availability_mode" not in controller
    assert controller["availability"] == [{"topic": "laser-cutterd/x700/availability"}]


def test_other_entities_need_daemon_and_controller() -> None:
    state = discovery_config(SETTINGS)["components"]["state"]
    assert state["availability_mode"] == "all"
    assert len(state["availability"]) == 2
    assert state["options"] == ["idle", "running", "paused"]
    running = discovery_config(SETTINGS)["components"]["job_running"]
    assert (running["payload_on"], running["payload_off"]) == ("True", "False")
