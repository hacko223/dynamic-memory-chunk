# amc/compression.py
# Compression abstraction — the rest of the library only calls compress/decompress

import zlib
import lzma
from .format import COMPRESS_NONE, COMPRESS_ZLIB, COMPRESS_LZMA
from .exceptions import AMCXCompressionError


def compress(data: bytes, algorithm: int) -> bytes:
    """
    Compresses data using the specified algorithm.

    Args:
        data:      bytes to compress
        algorithm: COMPRESS_NONE | COMPRESS_ZLIB | COMPRESS_LZMA

    Returns:
        compressed bytes (or the same if algorithm=COMPRESS_NONE)
    """
    if algorithm == COMPRESS_NONE:
        return data

    if algorithm == COMPRESS_ZLIB:
        try:
            return zlib.compress(data, level=6)   # level 6: speed/size balance
        except zlib.error as e:
            raise AMCXCompressionError(f"Error compressing with zlib: {e}") from e

    if algorithm == COMPRESS_LZMA:
        try:
            return lzma.compress(data, preset=6)  # preset 6: good balance for old chunks
        except lzma.LZMAError as e:
            raise AMCXCompressionError(f"Error compressing with lzma: {e}") from e

    raise AMCXCompressionError(f"Unknown compression algorithm: {algorithm:#04x}")


def decompress(data: bytes, algorithm: int) -> bytes:
    """
    Decompresses data using the specified algorithm.

    Args:
        data:      compressed bytes
        algorithm: COMPRESS_NONE | COMPRESS_ZLIB | COMPRESS_LZMA

    Returns:
        original bytes
    """
    if algorithm == COMPRESS_NONE:
        return data

    if algorithm == COMPRESS_ZLIB:
        try:
            return zlib.decompress(data)
        except zlib.error as e:
            raise AMCXCompressionError(f"Error decompressing with zlib: {e}") from e

    if algorithm == COMPRESS_LZMA:
        try:
            return lzma.decompress(data)
        except lzma.LZMAError as e:
            raise AMCXCompressionError(f"Error decompressing with lzma: {e}") from e

    raise AMCXCompressionError(f"Unknown compression algorithm: {algorithm:#04x}")


def algorithm_name(algorithm: int) -> str:
    """Returns the human-readable name of the algorithm."""
    names = {
        COMPRESS_NONE: "none",
        COMPRESS_ZLIB: "zlib",
        COMPRESS_LZMA: "lzma",
    }
    return names.get(algorithm, f"unknown({algorithm:#04x})")
