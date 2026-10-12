"""A read-only client for Ruida laser controllers (RDC644x and kin) over UDP.

Ruida never published the protocol; everything here comes from
reverse-engineering by others:

- MeerK40t's Ruida driver, the most complete Python implementation:
  https://github.com/meerk40t/meerk40t/tree/main/meerk40t/ruida
  (rdjob.py: byte scrambling and 7-bit number encoding; udp_connection.py:
  ports, ACK/NAK handling; emulator.py: memory address meanings)
- https://edutechwiki.unige.ch/en/Ruida: ports, checksum, memory addresses,
  scrambling "magic" per model
- https://github.com/jnweiger/ruida-laser/blob/master/doc/protocol.md

In short: the controller listens on UDP 50200 and answers to UDP 40200 on
the sender's host. Each datagram is a 2-byte big-endian checksum (the sum of
the scrambled bytes) followed by scrambled command bytes. The controller
acknowledges each datagram with a single scrambled byte (0xCC ACK, 0xCF
checksum error, so resend), then sends the reply, if the command has one.

Reading memory: send ``DA 00 <hi> <lo>``; the reply is
``DA 01 <hi> <lo> <value>``, where the value is usually a number in five
7-bit bytes, most significant first. The address is the two bytes as sent
on the wire, e.g. 0x0400 for machine status (the wiki also writes these as
14-bit numbers: 0x0400 on the wire is 0x200 there).

This client only reads. It never sends anything that moves the machine,
fires the laser or changes a setting.
"""

import logging
import socket
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_PORT = 50200
DEFAULT_LOCAL_PORT = 40200
# The scrambling key used by RDC644x, 654x, 633x and 320 boards. The 634XG
# uses 0x11; some other cards differ (see the wiki).
DEFAULT_MAGIC = 0x88

ACK = 0xCC
NAK = 0xCF  # checksum mismatch; resend
ERR = 0xCD
ENQ = 0xCE  # keep-alive

READ_MEMORY = 0xDA
MEMORY_READ_REQUEST = 0x00
MEMORY_READ_REPLY = 0x01


class RuidaError(Exception):
    """The controller couldn't be read."""


class RuidaTimeoutError(RuidaError):
    """The controller didn't answer."""


def scramble_byte(b: int, magic: int) -> int:
    """MeerK40t's swizzle_byte: swap bits 0 and 7, XOR with magic, add 1."""
    b ^= (b >> 7) & 0xFF
    b ^= (b << 7) & 0xFF
    b ^= (b >> 7) & 0xFF
    b ^= magic
    return (b + 1) & 0xFF


def unscramble_byte(b: int, magic: int) -> int:
    b = (b - 1) & 0xFF
    b ^= magic
    b ^= (b >> 7) & 0xFF
    b ^= (b << 7) & 0xFF
    b ^= (b >> 7) & 0xFF
    return b


@dataclass(frozen=True)
class Scrambler:
    magic: int = DEFAULT_MAGIC
    _forward: bytes = field(init=False, repr=False, compare=False)
    _reverse: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not 0 <= self.magic <= 0xFF:
            raise ValueError(f"magic must be 0-255, not {self.magic}")
        forward = bytes(scramble_byte(b, self.magic) for b in range(256))
        reverse = bytes(unscramble_byte(b, self.magic) for b in range(256))
        object.__setattr__(self, "_forward", forward)
        object.__setattr__(self, "_reverse", reverse)

    def scramble(self, data: bytes) -> bytes:
        return data.translate(self._forward)

    def unscramble(self, data: bytes) -> bytes:
        return data.translate(self._reverse)


def packet(command: bytes, scrambler: Scrambler) -> bytes:
    """A UDP datagram for one command: checksum, then the scrambled bytes."""
    body = scrambler.scramble(command)
    return (sum(body) & 0xFFFF).to_bytes(2, "big") + body


def decode_unsigned(data: bytes) -> int:
    """A number in 7-bit bytes, most significant first (usually 5 bytes)."""
    value = 0
    for b in data:
        value = (value << 7) | (b & 0x7F)
    return value


def decode_signed32(data: bytes) -> int:
    """A 5-byte value holding a 32-bit two's complement number (positions)."""
    value = decode_unsigned(data) & 0xFFFFFFFF
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def read_memory_command(address: int) -> bytes:
    return bytes([READ_MEMORY, MEMORY_READ_REQUEST, *address.to_bytes(2, "big")])


def parse_memory_reply(reply: bytes, address: int) -> bytes | None:
    """The value bytes of a reply to reading `address`, or None if it isn't one."""
    header = bytes([READ_MEMORY, MEMORY_READ_REPLY, *address.to_bytes(2, "big")])
    if reply.startswith(header):
        return reply[len(header) :]
    return None


class RuidaClient:
    """Reads controller memory over UDP, one request at a time.

    Not thread-safe; use it from one thread. The controller answers on a
    fixed port (40200), so only one client per host can talk to it.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        local_port: int = DEFAULT_LOCAL_PORT,
        magic: int = DEFAULT_MAGIC,
        timeout: float = 1.0,
        retries: int = 2,
    ) -> None:
        self.host = host
        self.port = port
        self.local_port = local_port
        self.timeout = timeout
        self.retries = retries
        self.scrambler = Scrambler(magic)
        self._sock: socket.socket | None = None
        self._address: str | None = None

    def open(self) -> None:
        if self._sock is not None:
            return
        # Resolve once so replies can be matched against the sender's address.
        self._address = socket.gethostbyname(self.host)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No SO_REUSEADDR: a second daemon on this host would steal replies.
            sock.bind(("", self.local_port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(self.timeout)
        self._sock = sock
        logger.debug(
            "bound UDP %d for %s:%d", self.local_port, self._address, self.port
        )

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> RuidaClient:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read(self, address: int) -> bytes:
        """The raw value bytes stored at `address` (e.g. 0x0400)."""
        self.open()
        datagram = packet(read_memory_command(address), self.scrambler)
        self._drain()
        for attempt in range(self.retries + 1):
            self._send(datagram)
            try:
                return self._await_reply(address, datagram)
            except RuidaTimeoutError:
                logger.debug(
                    "no reply reading 0x%04X (attempt %d)", address, attempt + 1
                )
        raise RuidaTimeoutError(
            f"{self.host}:{self.port} didn't answer a read of 0x{address:04X}"
        )

    def read_unsigned(self, address: int) -> int:
        return decode_unsigned(self._number(address))

    def read_signed(self, address: int) -> int:
        return decode_signed32(self._number(address))

    def _number(self, address: int) -> bytes:
        value = self.read(address)
        if len(value) < 5:
            raise RuidaError(f"short reply reading 0x{address:04X}: {value.hex()}")
        return value[:5]

    def _await_reply(self, address: int, datagram: bytes) -> bytes:
        sock = self._socket()
        deadline = time.monotonic() + self.timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, (sender, _port) = sock.recvfrom(2048)
            except TimeoutError:
                break
            if sender != self._address:
                logger.debug("ignoring datagram from %s", sender)
                continue
            message = self.scrambler.unscramble(data)
            if message == bytes([ACK]) or message == bytes([ENQ]):
                continue
            if message == bytes([NAK]):
                logger.debug("controller NAKed; resending")
                self._send(datagram)
                continue
            if message == bytes([ERR]):
                raise RuidaError(f"controller refused a read of 0x{address:04X}")
            value = parse_memory_reply(message, address)
            if value is not None:
                return value
            logger.debug("ignoring unexpected reply %s", message.hex())
        raise RuidaTimeoutError(f"timed out reading 0x{address:04X}")

    def _drain(self) -> None:
        """Discard late replies to earlier requests so they can't be mismatched."""
        sock = self._socket()
        sock.setblocking(False)
        try:
            while True:
                sock.recvfrom(2048)
        except BlockingIOError:
            pass
        finally:
            sock.settimeout(self.timeout)

    def _send(self, datagram: bytes) -> None:
        self._socket().sendto(datagram, (self._address, self.port))

    def _socket(self) -> socket.socket:
        if self._sock is None:
            raise RuidaError("client is closed")
        return self._sock
