import json
from dataclasses import replace

import paho.mqtt.client as paho
import pytest
from paho.mqtt.enums import CallbackAPIVersion, MQTTErrorCode
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from laser_cutterd.config import Settings
from laser_cutterd.monitor import Snapshot
from laser_cutterd.mqtt import Publisher

SETTINGS = Settings(
    ruida_host="laser.local",
    mqtt_host="broker.local",
    mqtt_username="user",
    mqtt_password="pw",  # noqa: S106 - a test value
    device_id="x700",
)
SUCCESS = ReasonCode(PacketTypes.CONNACK, identifier=0)
NOT_AUTHORIZED = ReasonCode(PacketTypes.CONNACK, identifier=135)


class FakeClient(paho.Client):
    """A paho client that records what it would have sent."""

    def __init__(self) -> None:
        super().__init__(CallbackAPIVersion.VERSION2, client_id="test")
        self.published: list[tuple[str, str, bool]] = []
        self.subscribed: list[str] = []
        self.connected = False
        self.connect_args: tuple[str, int] | None = None
        self.loop_running = False
        self.tls = False

    def publish(  # type: ignore[override]  # only the arguments we use
        self, topic: str, payload: str, qos: int = 0, retain: bool = False
    ) -> paho.MQTTMessageInfo:
        self.published.append((topic, payload, retain))
        info = paho.MQTTMessageInfo(len(self.published))
        info._set_as_published()
        return info

    def subscribe(self, topic: str, qos: int = 0) -> tuple[MQTTErrorCode, int]:  # type: ignore[override]  # only the arguments we use
        self.subscribed.append(topic)
        return MQTTErrorCode.MQTT_ERR_SUCCESS, 1

    def tls_set(self, *args: object, **kwargs: object) -> None:  # don't load CA certs
        self.tls = True

    def connect_async(self, host: str, port: int = 1883, *args: object) -> None:
        self.connect_args = (host, port)

    def loop_start(self) -> MQTTErrorCode:
        self.loop_running = True
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def loop_stop(self) -> MQTTErrorCode:
        self.loop_running = False
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def disconnect(self, *args: object, **kwargs: object) -> MQTTErrorCode:
        self.connected = False
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def is_connected(self) -> bool:
        return self.connected

    def topics(self) -> list[str]:
        return [topic for topic, _, _ in self.published]


def connect(publisher: Publisher, reason: ReasonCode = SUCCESS) -> FakeClient:
    """Simulate paho connecting: calls the publisher's on_connect handler."""
    client = publisher.client
    assert isinstance(client, FakeClient)
    client.connected = not reason.is_failure
    publisher._on_connect(client, None, paho.ConnectFlags(False), reason, None)
    return client


def message(topic: str, payload: bytes) -> paho.MQTTMessage:
    msg = paho.MQTTMessage(topic=topic.encode())
    msg.payload = payload
    return msg


SNAPSHOT = Snapshot(online=True, values={"state": "idle", "x_position": 1.5})


def test_requires_a_broker() -> None:
    with pytest.raises(ValueError, match="MQTT_HOST"):
        Publisher(replace(SETTINGS, mqtt_host=None), FakeClient())


def test_start_connects_in_the_background() -> None:
    client = FakeClient()
    Publisher(replace(SETTINGS, mqtt_tls=True), client).start()
    assert client.connect_args == ("broker.local", 1883)
    assert client.loop_running
    assert client.tls


def test_connect_announces_and_sends_discovery_and_state() -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    publisher.publish(SNAPSHOT)  # before connecting: remembered
    assert client.published == []

    connect(publisher)
    assert client.published[0] == ("laser-cutterd/x700/availability", "online", True)
    assert client.subscribed == ["homeassistant/status"]
    assert client.topics()[1:] == [
        "homeassistant/device/x700/config",
        "laser-cutterd/x700/controller",
        "laser-cutterd/x700/state",
    ]
    discovery = json.loads(client.published[1][1])
    assert "x_position" in discovery["components"]
    state = json.loads(client.published[3][1])
    assert state["online"] is True
    assert state["x_position"] == 1.5


def test_publish_while_connected() -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    connect(publisher)
    client.published.clear()
    publisher.publish(Snapshot(online=False))
    assert client.published[0] == ("laser-cutterd/x700/controller", "offline", True)
    assert json.loads(client.published[1][1])["online"] is False


def test_no_discovery_when_disabled() -> None:
    client = FakeClient()
    publisher = Publisher(replace(SETTINGS, ha_discovery=False), client)
    connect(publisher)
    assert client.subscribed == []
    assert client.topics() == ["laser-cutterd/x700/availability"]


def test_refused_connection_publishes_nothing() -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    connect(publisher, NOT_AUTHORIZED)
    assert client.published == []


def test_resends_when_home_assistant_restarts() -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    connect(publisher)
    publisher.publish(SNAPSHOT)
    client.published.clear()
    publisher._on_message(client, None, message("homeassistant/status", b"offline"))
    assert client.published == []
    publisher._on_message(client, None, message("homeassistant/status", b"online"))
    assert client.topics() == [
        "homeassistant/device/x700/config",
        "laser-cutterd/x700/controller",
        "laser-cutterd/x700/state",
    ]


def test_stop_says_offline() -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    publisher.start()
    connect(publisher)
    publisher.stop()
    assert client.published[-1] == ("laser-cutterd/x700/availability", "offline", True)
    assert not client.loop_running
    assert not client.connected


def test_disconnect_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient()
    publisher = Publisher(SETTINGS, client)
    reason = ReasonCode(PacketTypes.DISCONNECT, identifier=0x80)
    publisher._on_disconnect(client, None, paho.DisconnectFlags(False), reason, None)
    assert "reconnecting" in caplog.text
