"""Test doubles shared by the tests."""

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from laser_cutterd.ruida import ACK, ERR, Scrambler


def encode(value: int) -> bytes:
    """A number as the controller sends it: five 7-bit bytes."""
    value &= 0x7_FFFF_FFFF
    return bytes((value >> shift) & 0x7F for shift in (28, 21, 14, 7, 0))


def reply(address: int, value: bytes) -> bytes:
    return bytes([0xDA, 0x01, *address.to_bytes(2, "big")]) + value


# Given the unscrambled command, the unscrambled datagrams to answer with.
Responder = Callable[[bytes], list[bytes]]


def memory(values: dict[int, bytes]) -> Responder:
    """Answers memory reads like a controller: ACK, then the value.

    Unknown addresses get an error at once, so tests needn't wait out a timeout.
    """

    def respond(command: bytes) -> list[bytes]:
        address = int.from_bytes(command[2:4], "big")
        if address not in values:
            return [bytes([ERR])]
        return [bytes([ACK]), reply(address, values[address])]

    return respond


@contextmanager
def fake_controller(respond: Responder) -> Iterator[int]:
    """A UDP 'controller' on localhost; yields its port."""
    scrambler = Scrambler()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.05)
    done = threading.Event()

    def serve() -> None:
        while not done.is_set():
            try:
                data, sender = sock.recvfrom(2048)
            except TimeoutError:
                continue
            checksum, body = int.from_bytes(data[:2], "big"), data[2:]
            assert checksum == sum(body) & 0xFFFF
            for message in respond(scrambler.unscramble(body)):
                sock.sendto(scrambler.scramble(message), sender)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        done.set()
        thread.join()
        sock.close()
