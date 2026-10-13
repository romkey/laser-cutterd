import logging
import socket

import pytest
from fakes import encode, fake_controller, memory, reply

from laser_cutterd import ruida
from laser_cutterd.ruida import (
    ACK,
    ENQ,
    ERR,
    NAK,
    RuidaClient,
    RuidaError,
    RuidaTimeoutError,
    Scrambler,
)


def test_scrambling_matches_captured_traffic() -> None:
    # jnweiger/ruida-laser doc/protocol.md: captured from a real controller,
    # ACK arrives as 0xC6 and an error as 0x46 (magic 0x88).
    scrambler = Scrambler()
    assert scrambler.scramble(bytes([ACK, ERR])) == bytes([0xC6, 0x46])


@pytest.mark.parametrize("magic", [0x88, 0x11, 0x38])
def test_scrambling_round_trips(magic: int) -> None:
    scrambler = Scrambler(magic)
    data = bytes(range(256))
    assert scrambler.unscramble(scrambler.scramble(data)) == data


def test_rejects_bad_magic() -> None:
    with pytest.raises(ValueError, match="magic"):
        Scrambler(256)


def test_packet_has_checksum_of_scrambled_bytes() -> None:
    scrambler = Scrambler()
    datagram = ruida.packet(b"\xda\x00\x04\x00", scrambler)
    body = datagram[2:]
    assert scrambler.unscramble(body) == b"\xda\x00\x04\x00"
    assert int.from_bytes(datagram[:2], "big") == sum(body)


def test_decodes_numbers() -> None:
    assert ruida.decode_unsigned(encode(123_456_789)) == 123_456_789
    assert ruida.decode_signed32(encode(-1500)) == -1500
    assert ruida.decode_signed32(encode(1500)) == 1500


def test_parses_only_the_matching_reply() -> None:
    message = reply(0x0400, b"\x00\x00\x00\x00\x01")
    assert ruida.parse_memory_reply(message, 0x0400) == b"\x00\x00\x00\x00\x01"
    assert ruida.parse_memory_reply(message, 0x0421) is None


def client(port: int) -> RuidaClient:
    return RuidaClient("127.0.0.1", port=port, local_port=0, timeout=0.3, retries=0)


def test_reads_memory() -> None:
    values = {0x0421: encode(-12_345), 0x0403: encode(42), 0x057F: b"V1.2\x00"}
    with fake_controller(memory(values)) as port, client(port) as c:
        assert c.read_signed(0x0421) == -12_345
        assert c.read_unsigned(0x0403) == 42
        assert c.read(0x057F) == b"V1.2\x00"


def test_skips_keepalives_and_stale_replies() -> None:
    def respond(command: bytes) -> list[bytes]:
        return [bytes([ENQ]), reply(0x0999, encode(1)), reply(0x0400, encode(7))]

    with fake_controller(respond) as port, client(port) as c:
        assert c.read_unsigned(0x0400) == 7


def test_resends_after_nak() -> None:
    sent: list[bytes] = []

    def respond(command: bytes) -> list[bytes]:
        sent.append(command)
        if len(sent) == 1:
            return [bytes([NAK])]
        return [bytes([ACK]), reply(0x0400, encode(3))]

    with fake_controller(respond) as port, client(port) as c:
        assert c.read_unsigned(0x0400) == 3
    assert len(sent) == 2


def test_error_reply_raises() -> None:
    with (
        fake_controller(lambda command: [bytes([ERR])]) as port,
        client(port) as c,
        pytest.raises(RuidaError, match="refused"),
    ):
        c.read(0x0400)


def test_short_number_raises() -> None:
    with (
        fake_controller(memory({0x0400: b"\x01"})) as port,
        client(port) as c,
        pytest.raises(RuidaError, match="short reply"),
    ):
        c.read_unsigned(0x0400)


def test_times_out_after_retries() -> None:
    attempts: list[bytes] = []

    def respond(command: bytes) -> list[bytes]:
        attempts.append(command)
        return [bytes([ACK])]

    with fake_controller(respond) as port:
        c = RuidaClient("127.0.0.1", port=port, local_port=0, timeout=0.1, retries=1)
        with c, pytest.raises(RuidaTimeoutError, match="didn't answer"):
            c.read(0x0400)
    assert len(attempts) == 2


def test_closed_client_reopens_and_close_is_idempotent() -> None:
    with fake_controller(memory({0x0400: encode(0)})) as port:
        c = client(port)
        assert c.read_unsigned(0x0400) == 0
        c.close()
        c.close()
        with pytest.raises(RuidaError, match="closed"):
            c._socket()


def test_open_is_idempotent_and_bind_failure_closes_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("", 0))
        port = taken.getsockname()[1]
        c = RuidaClient("127.0.0.1", local_port=port)
        with pytest.raises(OSError, match=r"."):
            c.open()
        assert c._sock is None

    c = RuidaClient("127.0.0.1", local_port=0)
    c.open()
    sock = c._sock
    c.open()
    assert c._sock is sock
    c.close()


def test_trace_logs_every_datagram(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="laser_cutterd.ruida.io")
    sent = 0

    def respond(command: bytes) -> list[bytes]:
        nonlocal sent
        sent += 1
        if sent == 1:  # the first attempt goes unanswered
            return []
        return [bytes([ACK]), reply(0x0400, encode(5)), bytes([ENQ])]

    with fake_controller(respond) as port:
        c = RuidaClient("127.0.0.1", port=port, local_port=0, timeout=0.1, retries=1)
        with c:
            assert c.read_unsigned(0x0400) == 5
            # The trailing ENQ arrives late; the next read drains it.
            assert c.read_unsigned(0x0400) == 5

    lines = [r.getMessage() for r in caplog.records if r.name.endswith(".io")]
    assert lines[0].startswith(f"-> 127.0.0.1:{port} da 00 04 00 [read 0x0400] (wire ")
    assert "-- no reply to read 0x0400 within 0.1 s" in lines
    assert any("<- 127.0.0.1" in line and "[ACK]" in line for line in lines)
    assert any("[0x0400 = 5]" in line for line in lines)
    assert any("[ENQ; late, dropped]" in line for line in lines)


def test_trace_is_silent_when_off(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="laser_cutterd.ruida.io")
    with fake_controller(memory({0x0400: encode(1)})) as port, client(port) as c:
        c.read(0x0400)
    assert not [r for r in caplog.records if r.name.endswith(".io")]


@pytest.mark.parametrize(
    ("message", "label"),
    [
        (bytes([NAK]), "NAK (checksum error)"),
        (bytes([ERR]), "ERR"),
        (b"\xda\x00\x05\x7e", "read 0x057E"),
        (b"\xda\x01\x04\x21" + encode(1500), "0x0421 = 1500"),
        (b"\xa5\x50", "unknown"),
    ],
)
def test_describe(message: bytes, label: str) -> None:
    assert ruida.describe(message) == label
