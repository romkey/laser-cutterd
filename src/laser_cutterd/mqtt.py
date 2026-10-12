"""Publishes snapshots to MQTT, and Home Assistant discovery.

Uses paho-mqtt 2 (https://eclipse.dev/paho/files/paho.mqtt.python/html/):
its network thread reconnects by itself; on every (re)connect we announce
ourselves and resend discovery and the latest state.
"""

import json
import logging
import ssl
import threading
from typing import Any

import paho.mqtt.client as paho
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from laser_cutterd.config import Settings
from laser_cutterd.discovery import Topics, discovery_config
from laser_cutterd.monitor import Snapshot

logger = logging.getLogger(__name__)

ONLINE = "online"
OFFLINE = "offline"


class Publisher:
    def __init__(self, settings: Settings, client: paho.Client | None = None) -> None:
        if not settings.mqtt_host:
            raise ValueError("MQTT_HOST isn't set")
        self.settings = settings
        self.host: str = settings.mqtt_host
        self.topics = Topics.for_settings(settings)
        self.client = client or paho.Client(
            CallbackAPIVersion.VERSION2, client_id=settings.client_id
        )
        self._lock = threading.Lock()
        self._last: Snapshot | None = None

        self.client.will_set(self.topics.availability, OFFLINE, qos=1, retain=True)
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        host, port = self.host, self.settings.mqtt_port
        logger.info("connecting to MQTT broker %s:%d", host, port)
        # connect_async + loop_start: the network thread keeps retrying, so a
        # broker that's down at startup doesn't stop the daemon.
        self.client.connect_async(host, port)
        self.client.loop_start()

    def stop(self) -> None:
        if self.client.is_connected():
            self.client.publish(
                self.topics.availability, OFFLINE, qos=1, retain=True
            ).wait_for_publish(timeout=2)
            self.client.disconnect()
        self.client.loop_stop()

    def publish(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._last = snapshot
        if self.client.is_connected():
            self._publish_state(snapshot)

    def _publish_state(self, snapshot: Snapshot) -> None:
        self.client.publish(
            self.topics.controller,
            ONLINE if snapshot.online else OFFLINE,
            qos=1,
            retain=True,
        )
        self.client.publish(
            self.topics.state, json.dumps(snapshot.as_json()), qos=1, retain=True
        )

    def _publish_discovery(self) -> None:
        if not self.settings.ha_discovery:
            return
        logger.info("publishing Home Assistant discovery to %s", self.topics.discovery)
        self.client.publish(
            self.topics.discovery,
            json.dumps(discovery_config(self.settings)),
            qos=1,
            retain=True,
        )

    # paho callbacks run on its network thread. The unused arguments are
    # the callback signatures paho 2 (VERSION2) requires; `userdata` is
    # whatever was passed to Client(), hence Any.

    def _on_connect(
        self,
        client: paho.Client,
        userdata: Any,
        flags: paho.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.error("MQTT broker refused the connection: %s", reason_code)
            return
        logger.info("connected to MQTT broker")
        client.publish(self.topics.availability, ONLINE, qos=1, retain=True)
        if self.settings.ha_discovery:
            client.subscribe(self.topics.ha_status, qos=1)
        self._publish_discovery()
        with self._lock:
            last = self._last
        if last is not None:
            self._publish_state(last)

    def _on_disconnect(
        self,
        client: paho.Client,
        userdata: Any,
        flags: paho.DisconnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.warning("lost the MQTT broker (%s); reconnecting", reason_code)

    def _on_message(
        self, client: paho.Client, userdata: Any, message: paho.MQTTMessage
    ) -> None:
        # Home Assistant announces itself when it starts; it may have lost
        # discovery and state, so send them again.
        if message.topic == self.topics.ha_status and message.payload == b"online":
            logger.info("Home Assistant restarted; resending discovery")
            self._publish_discovery()
            with self._lock:
                last = self._last
            if last is not None:
                self._publish_state(last)
