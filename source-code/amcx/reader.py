# amcx/reader.py
# Reading .amcx files — loads only the index until you request a specific chunk

import zlib
import struct
from dataclasses import dataclass
from typing import Optional

from .format import (
    MAGIC, VERSION_MAJOR,
    HEADER_SIZE, INDEX_ENTRY_SIZE, SUMMARY_SIZE,
    HEADER_STRUCT, INDEX_ENTRY_STRUCT,
    FLAG_HAS_ACTIVE, FLAG_READONLY,
)
from .compression import decompress, algorithm_name
from .exceptions import (
    AMCXInvalidFileError, AMCXVersionError,
    AMCXChunkNotFoundError, AMCXCorruptError,
)


@dataclass
class IndexEntry:
    """Index entry — metadata of a chunk without loading its content."""
    chunk_id:        int
    offset:          int
    size_compressed: int
    size_original:   int
    chunk_type:      int
    algorithm:       int
    timestamp:       int
    crc32:           int      # expected CRC32 of the chunk
    summary:         str

    @property
    def algorithm_name(self) -> str:
        return algorithm_name(self.algorithm)


@dataclass
class AMCXHeader:
    """Parsed header of the file."""
    version_major: int
    version_minor: int
    num_chunks:    int
    created_at:    int
    index_offset:  int
    index_size:    int
    flags:         int

    @property
    def has_active_chunk(self) -> bool:
        return bool(self.flags & FLAG_HAS_ACTIVE)

    @property
    def is_readonly(self) -> bool:
        return bool(self.flags & FLAG_READONLY)

    @property
    def version_str(self) -> str:
        return f"{self.version_major}.{self.version_minor}"


class AMCXReader:
    """
    Reader for .amcx files.
    Loads the full index on open, but chunks only when requested.
    Verifies the CRC32 of each chunk when reading it.

    Basic usage:
        reader = AMCXReader("memory.amcx")
        print(reader.list_chunks())
        content = reader.read_chunk(0)
        reader.close()

    As a context manager:
        with AMCXReader("memory.amcx") as r:
            content = r.read_chunk(0)
    """

    def __init__(self, path: str):
        self._path = path
        self._file = open(path, "rb")
        self.header: AMCXHeader = self._read_header()
        self.index:  list[IndexEntry] = self._read_index()

    # ─── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()

    # ─── Public API ────────────────────────────────────────────────────────────

    def list_chunks(self) -> list[IndexEntry]:
        """Returns all index entries without loading content."""
        return list(self.index)

    def get_index_entry(self, chunk_id: int) -> IndexEntry:
        """Returns the index entry for a given chunk_id."""
        for entry in self.index:
            if entry.chunk_id == chunk_id:
                return entry
        raise AMCXChunkNotFoundError(f"Chunk {chunk_id} not found in the index.")

    def read_chunk(self, chunk_id: int) -> bytes:
        """
        Reads, verifies CRC32 and decompresses the content of a chunk.
        Raises AMCXCorruptError if the CRC32 does not match.
        """
        entry = self.get_index_entry(chunk_id)
        self._file.seek(entry.offset)

        size_field     = struct.unpack('>I', self._file.read(4))[0]
        compressed_data = self._file.read(size_field)

        # ── Verify chunk CRC32 ─────────────────────────────────────────────────
        actual_crc = zlib.crc32(compressed_data) & 0xFFFFFFFF
        if actual_crc != entry.crc32:
            raise AMCXCorruptError(
                f"Corrupt chunk {chunk_id}: "
                f"expected CRC32={entry.crc32:#010x}, "
                f"calculated={actual_crc:#010x}"
            )

        return decompress(compressed_data, entry.algorithm)

    def read_chunk_text(self, chunk_id: int, encoding: str = "utf-8") -> str:
        """Shortcut for reading a text chunk."""
        return self.read_chunk(chunk_id).decode(encoding)

    def read_active_chunk(self) -> Optional[bytes]:
        """Reads the active chunk if it exists."""
        if not self.header.has_active_chunk:
            return None
        from .format import CHUNK_ACTIVE
        for entry in self.index:
            if entry.chunk_type == CHUNK_ACTIVE:
                return self.read_chunk(entry.chunk_id)
        return None

    def summary(self) -> str:
        """Human-readable summary of the file for debugging."""
        lines = [
            f"AMCX file v{self.header.version_str}",
            f"Chunks: {self.header.num_chunks}",
            f"Active chunk: {'yes' if self.header.has_active_chunk else 'no'}",
            "",
            f"{'ID':>4}  {'Type':>4}  {'Compression':>10}  {'Original':>8}  {'CRC32':>10}  Summary",
            "-" * 72,
        ]
        for e in self.index:
            lines.append(
                f"{e.chunk_id:>4}  {e.chunk_type:>4}  {e.algorithm_name:>10}  "
                f"{e.size_original:>6}b  {e.crc32:#010x}  {e.summary}"
            )
        return "\n".join(lines)

    # ─── Internals ─────────────────────────────────────────────────────────────

    def _read_header(self) -> AMCXHeader:
        self._file.seek(0)
        raw = self._file.read(HEADER_SIZE)

        if len(raw) < HEADER_SIZE:
            raise AMCXInvalidFileError("File too small to be a valid .amcx.")

        if raw[:4] != MAGIC:
            raise AMCXInvalidFileError(
                f"Incorrect magic bytes: {raw[:4]!r} (expected {MAGIC!r})"
            )

        stored_crc   = struct.unpack('>I', raw[28:32])[0]
        computed_crc = zlib.crc32(raw[:28]) & 0xFFFFFFFF
        if stored_crc != computed_crc:
            raise AMCXCorruptError(
                f"Header CRC32 does not match: "
                f"stored={stored_crc:#010x}, calculated={computed_crc:#010x}"
            )

        _, v_major, v_minor, num_chunks, created_at, idx_offset, idx_size, flags, _ = \
            HEADER_STRUCT.unpack(raw)

        if v_major != VERSION_MAJOR:
            raise AMCXVersionError(
                f"Incompatible version: {v_major}.{v_minor} "
                f"(this library supports {VERSION_MAJOR}.x)"
            )

        return AMCXHeader(
            version_major=v_major,
            version_minor=v_minor,
            num_chunks=num_chunks,
            created_at=created_at,
            index_offset=idx_offset,
            index_size=idx_size,
            flags=flags,
        )

    def _read_index(self) -> list[IndexEntry]:
        self._file.seek(self.header.index_offset)
        entries = []
        for _ in range(self.header.num_chunks):
            raw = self._file.read(INDEX_ENTRY_SIZE)
            if len(raw) < INDEX_ENTRY_SIZE:
                break
            chunk_id, offset, size_c, size_o, ctype, algo, _reserved, ts, crc32, summary_bytes = \
                INDEX_ENTRY_STRUCT.unpack(raw)
            summary = summary_bytes.rstrip(b'\x00').decode("utf-8", errors="replace")
            entries.append(IndexEntry(
                chunk_id=chunk_id,
                offset=offset,
                size_compressed=size_c,
                size_original=size_o,
                chunk_type=ctype,
                algorithm=algo,
                timestamp=ts,
                crc32=crc32,
                summary=summary,
            ))
        return entries
