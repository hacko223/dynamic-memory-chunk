# amcx/writer.py
# Writing .amcx files

import io
import time
import zlib
import struct
from dataclasses import dataclass, field
from typing import Optional

from .format import (
    MAGIC, VERSION_MAJOR, VERSION_MINOR,
    COMPRESS_NONE, COMPRESS_ZLIB, COMPRESS_LZMA,
    CHUNK_ACTIVE, FLAG_COMPRESSED, FLAG_HAS_ACTIVE,
    HEADER_SIZE, INDEX_ENTRY_SIZE, SUMMARY_SIZE,
    HEADER_STRUCT, INDEX_ENTRY_STRUCT,
)
from .compression import compress, decompress
from .exceptions import AMCXReadOnlyError
from .mirror import AMCXMirror, AMCXRecovery, MirrorMode, MirrorStatus, ChunkStatus


@dataclass
class ChunkEntry:
    """Represents a chunk before writing it to the file."""
    chunk_id:       int
    chunk_type:     int
    summary:        str
    content:        bytes
    algorithm:      int = COMPRESS_ZLIB
    timestamp:      int = field(default_factory=lambda: int(time.time()))
    pre_compressed: bool = False           # if True, `content` is already compressed — skip compress()
    crc32:          Optional[int] = None   # required when pre_compressed=True (CRC32 of the compressed bytes)
    size_original:  Optional[int] = None   # required when pre_compressed=True (size before compression)


class AMCXWriter:
    """
    Builds an .amcx file in memory and flushes it to disk.

    Args:
        mirror:         MirrorMode.NONE | MANUAL | AUTO
        recovery:       if True, adds XOR recovery blocks when saving
        recovery_group: parity group size (default 3)

    Basic usage:
        writer = AMCXWriter()
        writer.add_text_chunk(0, CHUNK_LORE, "The world", "content...")
        writer.save("memory.amcx")

    With automatic mirror and recovery:
        writer = AMCXWriter(mirror=MirrorMode.AUTO, recovery=True)
        writer.save("memory.amcx")
        # → generates memory.amcx + memory.amcx.mirror (with SHA-1 per chunk)
        #   and XOR recovery blocks at the end of the .amcx
    """

    def __init__(
        self,
        mirror:         MirrorMode = MirrorMode.NONE,
        recovery:       bool       = False,
        recovery_group: int        = 3,
    ):
        self._chunks:         list[ChunkEntry] = []
        self._active_chunk_id: Optional[int]  = None
        self._flags:          int              = 0
        self._created_at:     int              = int(time.time())
        self.mirror           = mirror
        self.recovery         = recovery
        self.recovery_group   = recovery_group

    # ─── Public API ────────────────────────────────────────────────────────────

    def add_chunk(self, entry: ChunkEntry) -> None:
        """Adds a chunk. If its type is CHUNK_ACTIVE, marks it as active."""
        self._chunks.append(entry)
        if entry.chunk_type == CHUNK_ACTIVE:
            self._active_chunk_id = entry.chunk_id
            self._flags |= FLAG_HAS_ACTIVE
        if entry.algorithm != COMPRESS_NONE:
            self._flags |= FLAG_COMPRESSED

    def add_text_chunk(
        self,
        chunk_id:   int,
        chunk_type: int,
        summary:    str,
        text:       str,
        algorithm:  int = COMPRESS_ZLIB,
    ) -> None:
        """Shortcut for adding a text chunk (automatically encodes to UTF-8)."""
        self.add_chunk(ChunkEntry(
            chunk_id=chunk_id,
            chunk_type=chunk_type,
            summary=summary,
            content=text.encode("utf-8"),
            algorithm=algorithm,
        ))

    def save(self, path: str, accelerator_path: Optional[str] = None) -> None:
        """
        Serializes and writes the .amcx file.
        If mirror=AUTO, also generates the .amcx.mirror.
        If recovery=True, also appends XOR blocks at the end.

        accelerator_path: optional path to a native .so/.dll (amcx_sha1 /
        amcx_xor) used to speed up the mirror/recovery blocks. Falls back
        silently to pure Python if not given or if loading fails.
        """
        with open(path, "wb") as f:
            f.write(self._build())

        if self.recovery:
            AMCXRecovery.append(path, group_size=self.recovery_group, accelerator_path=accelerator_path)

        if self.mirror == MirrorMode.AUTO:
            self.embed_mirror(path, accelerator_path=accelerator_path)

    def embed_mirror(self, path: str, accelerator_path: Optional[str] = None) -> None:
        """
        Embeds the SHA-1 mirror block inside the .amcx manually.
        Useful when mirror=MANUAL.

        The mirror must always hash the *original* (decompressed) content of
        each chunk, since that's what AMCXMirror.verify() compares against
        (it reads chunks back via AMCXReader.read_chunk(), which decompresses
        them). For chunks added through add_chunk()/add_text_chunk(), `content`
        already holds that original data. For chunks copied over with
        pre_compressed=True (e.g. SmartMemory.flush() re-saving existing
        chunks without recompressing them), `content` holds the *compressed*
        bytes instead, so we decompress them first to get a matching hash.
        """
        chunk_data = {}
        for e in self._chunks:
            original = decompress(e.content, e.algorithm) if e.pre_compressed else e.content
            chunk_data[e.chunk_id] = (original, e.summary)
        AMCXMirror.embed(path, chunk_data, accelerator_path=accelerator_path)

    def to_bytes(self) -> bytes:
        """Returns the .amcx file as bytes (useful for tests or sending over the network)."""
        return self._build()

    # ─── Internals ─────────────────────────────────────────────────────────────

    def _build(self) -> bytes:
        buf = io.BytesIO()

        # 1. Reserve space for the header
        buf.write(b'\x00' * HEADER_SIZE)

        # 2. Reserve space for the index
        index_offset = HEADER_SIZE
        index_size   = INDEX_ENTRY_SIZE * len(self._chunks)
        buf.write(b'\x00' * index_size)

        # 3. Compress, calculate CRC32 and write each chunk
        index_entries = []
        for entry in self._chunks:
            if entry.pre_compressed:
                compressed  = entry.content
                chunk_crc32 = entry.crc32
                if chunk_crc32 is None:
                    chunk_crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
            else:
                compressed  = compress(entry.content, entry.algorithm)
                chunk_crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
            chunk_offset = buf.tell()

            buf.write(struct.pack('>I', len(compressed)))
            buf.write(compressed)

            index_entries.append({
                "chunk_id":        entry.chunk_id,
                "offset":          chunk_offset,
                "size_compressed": len(compressed),
                "size_original":   entry.size_original if entry.pre_compressed else len(entry.content),
                "chunk_type":      entry.chunk_type,
                "algorithm":       entry.algorithm,
                "timestamp":       entry.timestamp,
                "crc32":           chunk_crc32,
                "summary":         entry.summary,
            })

        # 4. Write the index
        buf.seek(index_offset)
        for e in index_entries:
            summary_bytes = e["summary"].encode("utf-8")[:SUMMARY_SIZE].ljust(SUMMARY_SIZE, b'\x00')
            buf.write(INDEX_ENTRY_STRUCT.pack(
                e["chunk_id"],
                e["offset"],
                e["size_compressed"],
                e["size_original"],
                e["chunk_type"],
                e["algorithm"],
                0,
                e["timestamp"],
                e["crc32"],
                summary_bytes,
            ))

        # 5. Write the header with CRC32
        header_without_crc = struct.pack(
            '>4sBBIQIIH',
            MAGIC,
            VERSION_MAJOR,
            VERSION_MINOR,
            len(self._chunks),
            self._created_at,
            index_offset,
            index_size,
            self._flags,
        )
        header_crc = zlib.crc32(header_without_crc) & 0xFFFFFFFF
        buf.seek(0)
        buf.write(header_without_crc + struct.pack('>I', header_crc))

        return buf.getvalue()
