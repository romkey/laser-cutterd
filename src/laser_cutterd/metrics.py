"""What the daemon reads from the controller, and how to decode it.

Addresses are as sent on the wire (see ruida.py). Meanings come from
MeerK40t's driver and emulator, ruida_bridge
(https://github.com/MrkyWtrs/ruida_bridge), and the Ruida wiki. ``verified``
marks values those sources read from real hardware; the rest are labels
from the emulator or wiki whose units nobody has confirmed, so check them
against your controller's panel with ``laser-cutterd --dump``.
"""

from dataclasses import dataclass
from enum import Enum

from laser_cutterd.ruida import decode_signed32, decode_unsigned


class Kind(Enum):
    UNSIGNED = "unsigned"
    SIGNED = "signed"  # positions: 32-bit two's complement
    TEXT = "text"  # NUL-terminated ASCII
    HEX = "hex"  # an identifier, shown as 0x12345678


Value = int | float | str | bool | None


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    address: int
    kind: Kind = Kind.UNSIGNED
    scale: float = 1.0  # value = raw * scale
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    precision: int | None = None
    diagnostic: bool = False
    enabled: bool = True  # enabled by default in Home Assistant
    slow: bool = False  # read every SLOW_POLL_INTERVAL instead of every poll
    verified: bool = False

    def decode(self, data: bytes) -> Value:
        match self.kind:
            case Kind.TEXT:
                return data.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            case Kind.HEX:
                return f"0x{decode_unsigned(data[:5]):08X}"
            case Kind.SIGNED:
                raw: int = decode_signed32(data[:5])
            case Kind.UNSIGNED:
                raw = decode_unsigned(data[:5])
        if self.scale == 1:
            return raw
        value = raw * self.scale
        return round(value, self.precision) if self.precision is not None else value


MM = 0.001  # µm to mm

STATUS = Metric(
    "status_raw",
    "Status bits",
    0x0400,
    icon="mdi:numeric",
    diagnostic=True,
    enabled=False,
    verified=True,
)

METRICS: tuple[Metric, ...] = (
    STATUS,
    *(
        Metric(
            f"{axis}_position",
            f"{axis.upper()} position",
            address,
            Kind.SIGNED,
            MM,
            "mm",
            "distance",
            "measurement",
            f"mdi:axis-{axis}-arrow" if axis != "u" else "mdi:rotate-3d-variant",
            precision=2,
            enabled=axis != "u",
            verified=axis != "u",
        )
        for axis, address in (
            ("x", 0x0421),
            ("y", 0x0431),
            ("z", 0x0441),
            ("u", 0x0451),
        )
    ),
    Metric(
        "job_length",
        "Job length",
        0x0620,
        icon="mdi:file-chart",
        diagnostic=True,
        enabled=False,
    ),
    Metric(
        "job_length_done",
        "Job length done",
        0x0621,
        icon="mdi:file-chart",
        diagnostic=True,
        enabled=False,
    ),
    # Counters and settings change slowly.
    Metric(
        "total_on_time",
        "Total power-on time",
        0x0401,
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        icon="mdi:power",
        slow=True,
    ),
    Metric(
        "total_work_time",
        "Total processing time",
        0x0402,
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        icon="mdi:timer-cog",
        slow=True,
    ),
    Metric(
        "total_laser_on_time",
        "Total laser-on time",
        0x0411,
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        icon="mdi:laser-pointer",
        slow=True,
    ),
    Metric(
        "total_jobs",
        "Total jobs",
        0x0403,
        state_class="total_increasing",
        icon="mdi:counter",
        slow=True,
    ),
    Metric(
        "previous_job_time",
        "Previous job time (controller)",
        0x0408,
        scale=0.001,
        unit="s",
        device_class="duration",
        icon="mdi:timer-check",
        precision=1,
        slow=True,
    ),
    Metric(
        "saved_files",
        "Saved files",
        0x0405,
        state_class="measurement",
        icon="mdi:file-multiple",
        slow=True,
        verified=True,
    ),
    Metric(
        "flash_total",
        "Flash size",
        0x0406,
        unit="B",
        device_class="data_size",
        icon="mdi:sd",
        diagnostic=True,
        enabled=False,
        slow=True,
    ),
    Metric(
        "flash_free",
        "Flash free",
        0x0407,
        unit="B",
        device_class="data_size",
        state_class="measurement",
        icon="mdi:sd",
        diagnostic=True,
        slow=True,
    ),
    *(
        Metric(
            f"{axis}_total_travel",
            f"{axis.upper()} total travel",
            address,
            unit="m",
            device_class="distance",
            state_class="total_increasing",
            icon="mdi:map-marker-distance",
            enabled=axis in "xy",
            slow=True,
        )
        for axis, address in (
            ("x", 0x0423),
            ("y", 0x0433),
            ("z", 0x0443),
            ("u", 0x0453),
        )
    ),
    Metric(
        "card_id",
        "Controller card ID",
        0x057E,
        Kind.HEX,
        icon="mdi:chip",
        diagnostic=True,
        slow=True,
        verified=True,
    ),
    Metric(
        "mainboard_version",
        "Mainboard version",
        0x057F,
        Kind.TEXT,
        icon="mdi:chip",
        diagnostic=True,
        slow=True,
    ),
    *(
        Metric(
            f"{axis}_range",
            f"{axis.upper()} travel range",
            address,
            scale=MM,
            unit="mm",
            device_class="distance",
            icon="mdi:arrow-expand-horizontal",
            precision=1,
            diagnostic=True,
            slow=True,
            verified=True,
        )
        for axis, address in (("x", 0x0026), ("y", 0x0036), ("z", 0x0046))
    ),
    *(
        Metric(
            f"{axis}_max_speed",
            f"{axis.upper()} max speed",
            address,
            scale=MM,
            unit="mm/s",
            device_class="speed",
            icon="mdi:speedometer",
            precision=1,
            diagnostic=True,
            enabled=axis in "xy",
            slow=True,
            verified=True,
        )
        for axis, address in (("x", 0x0023), ("y", 0x0033), ("z", 0x0043))
    ),
    *(
        metric
        for laser, (freq, low, high) in (
            (1, (0x0011, 0x0012, 0x0013)),
            (2, (0x0017, 0x0018, 0x0019)),
        )
        for metric in (
            Metric(
                f"laser_{laser}_frequency",
                f"Laser {laser} PWM frequency",
                freq,
                scale=0.001,
                unit="Hz",
                device_class="frequency",
                icon="mdi:sine-wave",
                precision=0,
                diagnostic=True,
                enabled=laser == 1,
                slow=True,
                verified=True,
            ),
            Metric(
                f"laser_{laser}_min_power",
                f"Laser {laser} minimum power",
                low,
                unit="%",
                icon="mdi:laser-pointer",
                diagnostic=True,
                enabled=laser == 1,
                slow=True,
                verified=True,
            ),
            Metric(
                f"laser_{laser}_max_power",
                f"Laser {laser} maximum power",
                high,
                unit="%",
                icon="mdi:laser-pointer",
                diagnostic=True,
                enabled=laser == 1,
                slow=True,
                verified=True,
            ),
        )
    ),
)

# Card IDs seen in the wild; the wiki and MeerK40t.
CARD_MODELS = {
    "0x65006500": "RDC6442G",
    "0x65106510": "RDC6442S",
}


# Machine status (0x0400) bits. All three sources agree that bit 0 is set
# while a job is loaded and running, and that bits 0 and 1 together mean
# paused (MeerK40t calls bit 1 "part end"; ruida_bridge and MeerK40t's
# emulator treat it as paused). MeerK40t reads bit 24 as "moving".
STATUS_JOB = 0x00000001
STATUS_PAUSED = 0x00000002
STATUS_MOVING = 0x01000000


class State(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


def machine_state(status: int) -> State:
    if not status & STATUS_JOB:
        return State.IDLE
    if status & STATUS_PAUSED:
        return State.PAUSED
    return State.RUNNING
