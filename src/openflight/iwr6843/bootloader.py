"""TI IWR6843 ROM UART bootloader protocol.

This implements the host side of TI application note SWRA627. It intentionally
contains no board-specific SOP or reset GPIO handling; the current LEVM uses
its physical boot-mode switch and reset button.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from typing import Protocol

SYNC = 0xAA
MAX_CHUNK_SIZE = 240

OP_PING = 0x20
OP_OPEN = 0x21
OP_CLOSE = 0x22
OP_GET_STATUS = 0x23
OP_WRITE_FLASH = 0x24
OP_ERASE = 0x28

STORAGE_SFLASH = 2
FILE_TYPE_META_IMAGE1 = 4

ACK = b"\x00\xcc"
NACK = b"\x00\x33"
ACK_FRAME = b"\x00\x04\xcc\x00\xcc"
NACK_FRAME = b"\x00\x04\x33\x00\x33"
STATUS_INITIAL = 0x00
STATUS_SUCCESS = 0x40
STATUS_ACCESS_IN_PROGRESS = 0x4B


class SerialTransport(Protocol):
    """Subset of pyserial used by the bootloader client."""

    break_condition: bool

    def read(self, size: int) -> bytes:
        """Read up to ``size`` bytes."""

    def write(self, data: bytes) -> int:
        """Write bytes and return the count accepted."""

    def send_break(self, duration: float = 0.25) -> None:
        """Assert the UART break condition."""

    def reset_input_buffer(self) -> None:
        """Discard unread input."""


class BootloaderError(RuntimeError):
    """The radar rejected or did not complete a bootloader operation."""


def encode_command(opcode: int, data: bytes = b"") -> bytes:
    """Encode one SWRA627 host command."""
    payload = bytes([opcode]) + data
    length = len(payload) + 2
    if length > 0xF3:
        raise ValueError(f"bootloader payload too large: {len(data)} bytes")
    checksum = sum(payload) & 0xFF
    return bytes([SYNC]) + struct.pack(">HB", length, checksum) + payload


def _read_exact(serial: SerialTransport, size: int, empty_read_limit: int = 2) -> bytes:
    data = bytearray()
    empty_reads = 0
    while len(data) < size:
        chunk = serial.read(size - len(data))
        if not chunk:
            empty_reads += 1
            if empty_reads >= empty_read_limit:
                raise BootloaderError(
                    f"timed out waiting for bootloader response ({len(data)}/{size} bytes)"
                )
            continue
        data.extend(chunk)
    return bytes(data)


def read_response(
    serial: SerialTransport,
    *,
    require_ack: bool = False,
    empty_read_limit: int = 2,
) -> bytes:
    """Read and validate one response from the device."""
    length = struct.unpack(">H", _read_exact(serial, 2, empty_read_limit))[0]
    if length < 2 or length > 0x100:
        raise BootloaderError(f"invalid bootloader response length: {length}")
    checksum = _read_exact(serial, 1, empty_read_limit)[0]
    payload = _read_exact(serial, length - 2, empty_read_limit)
    expected = sum(payload) & 0xFF
    if checksum != expected:
        raise BootloaderError(
            f"bootloader response checksum mismatch: got 0x{checksum:02x}, "
            f"expected 0x{expected:02x}"
        )
    if require_ack and payload == NACK:
        raise BootloaderError("IWR6843 bootloader returned NACK")
    if require_ack and payload != ACK:
        raise BootloaderError(f"expected bootloader ACK, got {payload.hex()}")
    return payload


def read_handshake_ack(serial: SerialTransport, max_bytes: int = 64) -> None:
    """Find the bootloader ACK while tolerating BREAK-generated zero bytes."""
    window = bytearray()
    for _ in range(max_bytes):
        byte = serial.read(1)
        if not byte:
            raise BootloaderError("timed out waiting for IWR6843 bootloader handshake ACK")
        window.extend(byte)
        if bytes(window).endswith(ACK_FRAME):
            return
        if bytes(window).endswith(NACK_FRAME):
            raise BootloaderError("IWR6843 bootloader returned NACK during handshake")
        if len(window) > len(ACK_FRAME):
            del window[: -len(ACK_FRAME)]
    raise BootloaderError("IWR6843 bootloader handshake ACK not found")


class IWR6843Bootloader:
    """Program an IWR6843 meta image over its ROM UART bootloader."""

    def __init__(self, serial: SerialTransport):
        self.serial = serial
        self._handshake_active = False

    def _send(
        self,
        opcode: int,
        data: bytes = b"",
        *,
        require_ack: bool = True,
        empty_read_limit: int = 2,
    ) -> bytes:
        packet = encode_command(opcode, data)
        written = self.serial.write(packet)
        if written != len(packet):
            raise BootloaderError(f"short serial write: {written}/{len(packet)} bytes")
        return read_response(
            self.serial,
            require_ack=require_ack,
            empty_read_limit=empty_read_limit,
        )

    def start_handshake(self) -> None:
        """Assert BREAK before the radar is reset, matching TI UniFlash."""
        if self._handshake_active:
            raise BootloaderError("IWR6843 bootloader handshake is already active")
        self.serial.reset_input_buffer()
        self.serial.break_condition = True
        self._handshake_active = True
        time.sleep(0.1)

    def cancel_handshake(self) -> None:
        """Release an armed BREAK without exchanging bootloader packets."""
        if self._handshake_active:
            self.serial.break_condition = False
            self._handshake_active = False

    def finish_handshake(self) -> None:
        """Release BREAK, receive its ACK, and verify PING."""
        if not self._handshake_active:
            raise BootloaderError("IWR6843 bootloader handshake was not started")
        self.serial.break_condition = False
        self._handshake_active = False
        read_handshake_ack(self.serial)
        self._send(OP_PING)

    def handshake(self) -> None:
        """Handshake without an externally coordinated reset."""
        self.start_handshake()
        self.finish_handshake()

    def get_status(self) -> int:
        """Return the status of the most recent actionable command."""
        payload = self._send(OP_GET_STATUS, require_ack=False)
        if len(payload) != 1:
            raise BootloaderError(f"invalid status response: {payload.hex()}")
        return payload[0]

    def wait_for_success(self, timeout_s: float = 20.0) -> None:
        """Poll until the previous operation succeeds or fails."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_status()
            if status == STATUS_SUCCESS:
                return
            if status != STATUS_ACCESS_IN_PROGRESS:
                raise BootloaderError(f"IWR6843 bootloader status 0x{status:02x}")
            if time.monotonic() >= deadline:
                raise BootloaderError("timed out waiting for IWR6843 flash operation")
            time.sleep(0.1)

    def flash(
        self,
        image: bytes,
        *,
        erase: bool = True,
        progress: Callable[[int, int], None] | None = None,
        handshake: bool = True,
        stage: Callable[[str], None] | None = None,
    ) -> None:
        """Flash one META IMG1 image and require final CRC/status success."""
        if not image.startswith(b"MSTR"):
            raise ValueError("IWR6843 firmware must be a TI MSTR meta image")
        if len(image) > 0xFFFFFFFF:
            raise ValueError("IWR6843 firmware image is too large")

        if handshake:
            self.handshake()
        if erase:
            if stage is not None:
                stage("Erasing existing SFLASH")
            erase_fields = struct.pack(">III", STORAGE_SFLASH, 0, 0)
            self._send(OP_ERASE, erase_fields, empty_read_limit=12)

        if stage is not None:
            stage("Opening firmware image")
        open_fields = struct.pack(
            ">IIII",
            len(image),
            STORAGE_SFLASH,
            FILE_TYPE_META_IMAGE1,
            0,
        )
        self._send(OP_OPEN, open_fields)

        if stage is not None:
            stage("Writing firmware")
        sent = 0
        for offset in range(0, len(image), MAX_CHUNK_SIZE):
            chunk = image[offset : offset + MAX_CHUNK_SIZE]
            self._send(OP_WRITE_FLASH, chunk)
            sent += len(chunk)
            if progress is not None:
                progress(sent, len(image))

        if stage is not None:
            stage("Closing and verifying firmware")
        self._send(OP_CLOSE, struct.pack(">I", STORAGE_SFLASH))
        self.wait_for_success()
