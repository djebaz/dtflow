"""Bounded PNG fixture encoding and validation without provider libraries."""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def fixture_png(width: int, height: int, seed: int) -> bytes:
    """Encode a solid fixture swatch, explicitly unrelated to model inference."""
    color = bytes((seed % 256, (seed // 256) % 256, 192))
    row = b"\0" + color * width
    compressor = zlib.compressobj()
    compressed = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def encode_png(pixels: bytes, width: int, height: int, channels: int) -> bytes:
    """Encode raw 8-bit pixels as an RGB/RGBA PNG; one-channel input is expanded to RGB."""
    if channels not in (1, 3, 4) or width <= 0 or height <= 0 or len(pixels) != width * height * channels:
        raise ValueError("Pixel data does not match the declared dimensions.")
    if channels == 1:
        expanded = bytearray(len(pixels) * 3)
        expanded[0::3] = pixels
        expanded[1::3] = pixels
        expanded[2::3] = pixels
        pixels, channels = bytes(expanded), 3
    stride = width * channels
    compressor = zlib.compressobj()
    compressed = (
        b"".join(compressor.compress(b"\0" + pixels[row * stride : (row + 1) * stride]) for row in range(height))
        + compressor.flush()
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2 if channels == 3 else 6, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def validate_png(data: bytes, width: int, height: int, *, decoded_limit: int) -> None:
    """Check complete non-interlaced RGB/RGBA PNGs with bounded inflation."""
    if type(data) is not bytes or not data.startswith(SIGNATURE):
        raise ValueError("Invalid PNG signature.")
    offset = len(SIGNATURE)
    compressed = bytearray()
    seen_header = False
    seen_data = False
    seen_end = False
    channels = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk.")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + length + 12
        if end > len(data):
            raise ValueError("Truncated PNG payload.")
        payload = data[offset + 8 : end - 4]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) != checksum:
            raise ValueError("Invalid PNG checksum.")
        if not seen_header:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Missing PNG header.")
            w, h, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (
                (w, h) != (width, height)
                or depth != 8
                or color not in (2, 6)
                or (compression, filtering, interlace) != (0, 0, 0)
            ):
                raise ValueError("Unsupported PNG encoding or dimensions.")
            channels = 3 if color == 2 else 4
            seen_header = True
        elif kind == b"IHDR":
            raise ValueError("Duplicate PNG header.")
        elif kind == b"IDAT":
            compressed.extend(payload)
            seen_data = True
        elif kind == b"IEND":
            if length or end != len(data):
                raise ValueError("Invalid PNG ending.")
            seen_end = True
        elif kind[0] & 32 == 0:
            raise ValueError("Unsupported critical PNG chunk.")
        offset = end
    expected = height * (1 + width * channels)
    if not seen_end or not seen_data or expected > decoded_limit:
        raise ValueError("Incomplete PNG or decoded limit exceeded.")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Incomplete or oversized PNG data.")
    stride = 1 + width * channels
    if any(raw[i] > 4 for i in range(0, expected, stride)):
        raise ValueError("Invalid PNG filter.")
