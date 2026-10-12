"""Home Assistant MQTT discovery: one device-discovery message for everything.

Format: https://www.home-assistant.io/integrations/mqtt/#device-discovery-payload
(``<prefix>/device/<id>/config`` with ``device``, ``origin`` and
``components``, each component naming its ``platform``).
"""

from dataclasses import dataclass
from typing import Any

from laser_cutterd import __version__
from laser_cutterd.config import Settings
from laser_cutterd.metrics import METRICS, Metric, State

SUPPORT_URL = "https://github.com/romkey/laser-cutterd"

# A discovery payload is JSON; its values are strings, numbers, booleans,
# lists and nested objects, which typing can't usefully narrow further.
Config = dict[str, Any]


@dataclass(frozen=True)
class Topics:
    base: str
    availability: str  # this daemon: online / offline (last will)
    controller: str  # the controller: online / offline
    state: str  # JSON snapshot
    discovery: str
    ha_status: str  # Home Assistant's birth message

    @classmethod
    def for_settings(cls, settings: Settings) -> Topics:
        base = f"{settings.mqtt_topic_prefix}/{settings.device_id}"
        return cls(
            base=base,
            availability=f"{base}/availability",
            controller=f"{base}/controller",
            state=f"{base}/state",
            discovery=f"{settings.ha_discovery_prefix}/device/{settings.device_id}/config",
            ha_status=f"{settings.ha_discovery_prefix}/status",
        )


class _Components:
    """Builds component configs that share topics and availability."""

    def __init__(self, settings: Settings, topics: Topics) -> None:
        self.device_id = settings.device_id
        self.topics = topics
        self.daemon_only = [{"topic": topics.availability}]
        self.both = [{"topic": topics.availability}, {"topic": topics.controller}]

    def make(self, platform: str, key: str, name: str, **options: object) -> Config:
        """A component reading `key` from the state JSON; None drops an option."""
        config: Config = {
            "platform": platform,
            "name": name,
            "unique_id": f"{self.device_id}_{key}",
            "default_entity_id": f"{platform}.{self.device_id}_{key}",
            "state_topic": self.topics.state,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "availability": self.both,
            "availability_mode": "all",
        }
        for option, value in options.items():
            if value is None:
                config.pop(option, None)
            else:
                config[option] = value
        return config

    def sensor(self, key: str, name: str, **options: object) -> Config:
        return self.make("sensor", key, name, **options)

    def flag(self, key: str, name: str, **options: object) -> Config:
        # The template renders Python's True or False (None: unknown).
        return self.make(
            "binary_sensor",
            key,
            name,
            payload_on="True",
            payload_off="False",
            **options,
        )

    def metric(self, metric: Metric) -> Config:
        return self.sensor(
            metric.key,
            metric.name,
            unit_of_measurement=metric.unit,
            device_class=metric.device_class,
            state_class=metric.state_class,
            icon=metric.icon,
            suggested_display_precision=metric.precision,
            entity_category="diagnostic" if metric.diagnostic else None,
            enabled_by_default=None if metric.enabled else False,
        )


def discovery_config(settings: Settings) -> Config:
    topics = Topics.for_settings(settings)
    c = _Components(settings, topics)
    components: dict[str, Config] = {
        "controller": c.make(
            "binary_sensor",
            "controller",
            "Controller",
            state_topic=topics.controller,
            value_template=None,
            payload_on="online",
            payload_off="offline",
            device_class="connectivity",
            entity_category="diagnostic",
            availability=c.daemon_only,
            availability_mode=None,
        ),
        "state": c.sensor(
            "state",
            "State",
            device_class="enum",
            options=[s.value for s in State],
            icon="mdi:state-machine",
        ),
        "job_running": c.flag("job_running", "Job running", device_class="running"),
        "paused": c.flag("paused", "Paused", icon="mdi:pause-circle"),
        "moving": c.flag("moving", "Moving", device_class="moving"),
        "job_progress": c.sensor(
            "job_progress",
            "Job progress",
            unit_of_measurement="%",
            state_class="measurement",
            suggested_display_precision=0,
            icon="mdi:progress-helper",
        ),
        "job_elapsed": c.sensor(
            "job_elapsed",
            "Job elapsed",
            unit_of_measurement="s",
            device_class="duration",
            icon="mdi:timer-play",
        ),
        "job_started": c.sensor("job_started", "Job started", device_class="timestamp"),
        "last_job_duration": c.sensor(
            "last_job_duration",
            "Last job duration",
            unit_of_measurement="s",
            device_class="duration",
            icon="mdi:timer-check",
        ),
        "last_job_finished": c.sensor(
            "last_job_finished", "Last job finished", device_class="timestamp"
        ),
        "jobs_since_start": c.sensor(
            "jobs_since_start",
            "Jobs since daemon start",
            state_class="total_increasing",
            icon="mdi:counter",
            entity_category="diagnostic",
        ),
        "controller_model": c.sensor(
            "controller_model",
            "Controller model",
            icon="mdi:chip",
            entity_category="diagnostic",
        ),
        "updated": c.sensor(
            "updated",
            "Last read",
            device_class="timestamp",
            entity_category="diagnostic",
            enabled_by_default=False,
        ),
    }
    components.update({m.key: c.metric(m) for m in METRICS})

    return {
        "device": {
            "identifiers": [f"laser_cutterd_{settings.device_id}"],
            "name": settings.device_name,
            "manufacturer": settings.device_manufacturer,
            "model": settings.device_model,
        },
        "origin": {
            "name": "laser-cutterd",
            "sw_version": __version__,
            "support_url": SUPPORT_URL,
        },
        "components": components,
        "qos": 1,
    }
