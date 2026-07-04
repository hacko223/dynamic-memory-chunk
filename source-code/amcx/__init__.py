# amcx/__init__.py — versión dmc (sin detection.py)

from .format import (
    COMPRESS_NONE, COMPRESS_ZLIB, COMPRESS_LZMA,
    CHUNK_LORE, CHUNK_CHARACTER, CHUNK_EVENT, CHUNK_ACTIVE, CHUNK_GENERIC,
)
from .reader import AMCXReader, IndexEntry, AMCXHeader
from .writer import AMCXWriter, ChunkEntry
from .mirror import AMCXMirror, AMCXRecovery, MirrorMode, MirrorStatus, ChunkStatus
from .exceptions import (
    AMCXError, AMCXInvalidFileError, AMCXVersionError,
    AMCXCompressionError, AMCXChunkNotFoundError, AMCXCorruptError,
    AMCXReadOnlyError, AMCXSecurityError,
)
from .smart import SmartMemory

__version__ = "0.3.4"
__author__  = "hacko223"

__all__ = [
    "AMCXReader", "AMCXWriter",
    "AMCXMirror", "AMCXRecovery", "MirrorMode", "MirrorStatus", "ChunkStatus",
    "ChunkEntry", "IndexEntry", "AMCXHeader",
    "COMPRESS_NONE", "COMPRESS_ZLIB", "COMPRESS_LZMA",
    "CHUNK_LORE", "CHUNK_CHARACTER", "CHUNK_EVENT", "CHUNK_ACTIVE", "CHUNK_GENERIC",
    "AMCXError", "AMCXInvalidFileError", "AMCXVersionError",
    "AMCXCompressionError", "AMCXChunkNotFoundError", "AMCXCorruptError",
    "AMCXReadOnlyError", "AMCXSecurityError",
    "SmartMemory",
]
