# amcx/mirror.py
# Mirror system embedded inside the .amcx — inspired by WinRAR.
#
# Final file structure:
#   [ HEADER ] [ INDEX ] [ CHUNKS... ] [ XOR RECOVERY BLOCK ] [ SHA-1 MIRROR BLOCK ]
#
# Both blocks are optional and are detected by their magic bytes at the end of the file.
# The reader finds them with rfind() without affecting normal chunk reading.

import hashlib
import struct
import time
import ctypes
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .exceptions import AMCXError, AMCXCorruptError


# ─── Optional native accelerators (C++ add-ons) ───────────────────────────────
# Loaded only if a path is passed explicitly. Falls back silently to the
# pure-Python implementation (hashlib.sha1 / manual XOR) if loading fails.

_sha1_accel_cache: dict[str, "ctypes.CDLL"] = {}
_xor_accel_cache:  dict[str, "ctypes.CDLL"] = {}


def _load_sha1_accelerator(path: Optional[str]):
    if not path:
        return None
    if path in _sha1_accel_cache:
        return _sha1_accel_cache[path]
    try:
        lib = ctypes.CDLL(path)
        lib.amcx_accelerator_version.restype = ctypes.c_int32
        lib.amcx_sha1.argtypes = [ctypes.c_char_p, ctypes.c_int32, ctypes.c_char_p]
        lib.amcx_sha1.restype = None
        _ = lib.amcx_accelerator_version()
        _sha1_accel_cache[path] = lib
        return lib
    except Exception:
        return None


def _load_xor_accelerator(path: Optional[str]):
    if not path:
        return None
    if path in _xor_accel_cache:
        return _xor_accel_cache[path]
    try:
        lib = ctypes.CDLL(path)
        lib.amcx_accelerator_version.restype = ctypes.c_int32
        lib.amcx_xor_parity.argtypes = [
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int32), ctypes.c_int32,
            ctypes.c_char_p, ctypes.c_int32,
        ]
        lib.amcx_xor_parity.restype = ctypes.c_int32
        lib.amcx_xor_recover.argtypes = [
            ctypes.c_char_p, ctypes.c_int32,
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int32), ctypes.c_int32,
            ctypes.c_char_p, ctypes.c_int32,
        ]
        lib.amcx_xor_recover.restype = ctypes.c_int32
        _ = lib.amcx_accelerator_version()
        _xor_accel_cache[path] = lib
        return lib
    except Exception:
        return None


def _sha1_digest(data: bytes, accelerator_path: Optional[str] = None) -> bytes:
    lib = _load_sha1_accelerator(accelerator_path)
    if lib is not None:
        try:
            out = ctypes.create_string_buffer(20)
            lib.amcx_sha1(data, len(data), out)
            return out.raw
        except Exception:
            pass
    return hashlib.sha1(data).digest()


def _xor_parity(buffers: list[bytes], accelerator_path: Optional[str] = None) -> bytes:
    lib = _load_xor_accelerator(accelerator_path)
    max_len = max(len(b) for b in buffers)
    if lib is not None:
        try:
            padded = [b + b'\x00' * (max_len - len(b)) for b in buffers]
            ptrs = (ctypes.c_char_p * len(padded))(*padded)
            lens = (ctypes.c_int32 * len(padded))(*[len(b) for b in padded])
            out = ctypes.create_string_buffer(max_len)
            ret = lib.amcx_xor_parity(ptrs, lens, len(padded), out, max_len)
            if ret == 0:
                return out.raw
        except Exception:
            pass
    padded = [b + b'\x00' * (max_len - len(b)) for b in buffers]
    parity = bytearray(padded[0])
    for extra in padded[1:]:
        for i, b in enumerate(extra):
            parity[i] ^= b
    return bytes(parity)


def _xor_recover(parity: bytes, healthy: list[bytes], accelerator_path: Optional[str] = None) -> bytes:
    lib = _load_xor_accelerator(accelerator_path)
    max_len = max(len(parity), *(len(h) for h in healthy)) if healthy else len(parity)
    if lib is not None:
        try:
            padded = [h + b'\x00' * (max_len - len(h)) for h in healthy]
            ptrs = (ctypes.c_char_p * len(padded))(*padded)
            lens = (ctypes.c_int32 * len(padded))(*[len(h) for h in padded])
            out = ctypes.create_string_buffer(max_len)
            ret = lib.amcx_xor_recover(parity, len(parity), ptrs, lens, len(padded), out, max_len)
            if ret == 0:
                return out.raw
        except Exception:
            pass
    result = bytearray(parity + b'\x00' * (max_len - len(parity)))
    for h in healthy:
        padded = h + b'\x00' * (max_len - len(h))
        for i, b in enumerate(padded):
            result[i] ^= b
    return bytes(result)


# ─── Magic bytes for each block ───────────────────────────────────────────────
RECOVERY_MAGIC = b'AMCXR\x00'   # XOR recovery block
MIRROR_MAGIC   = b'AMCXM\x00'   # SHA-1 mirror block


# ─── Configuration ─────────────────────────────────────────────────────────────

class MirrorMode(Enum):
    NONE   = auto()   # no embedded mirror
    MANUAL = auto()   # only when you call writer.embed_mirror()
    AUTO   = auto()   # automatic every time writer.save() is called


class ChunkStatus(Enum):
    OK             = "ok"
    MODIFIED       = "modified"       # chunk SHA-1 changed
    MISSING_ORIG   = "missing_orig"   # in the mirror but not in the chunks
    MISSING_MIRROR = "missing_mirror" # in the chunks but not in the mirror
    OUTDATED       = "outdated"       # mirror is older than the file


@dataclass
class ChunkReport:
    chunk_id:      int
    summary:       str
    status:        ChunkStatus
    sha1_original: Optional[str] = None
    sha1_mirror:   Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == ChunkStatus.OK


@dataclass
class MirrorStatus:
    mirror_exists: bool
    chunks:        list[ChunkReport] = field(default_factory=list)
    mirror_ts:     Optional[int] = None
    file_ts:       Optional[int] = None

    @property
    def all_ok(self) -> bool:
        return self.mirror_exists and all(c.ok for c in self.chunks)

    @property
    def problems(self) -> list[ChunkReport]:
        return [c for c in self.chunks if not c.ok]

    def report(self) -> str:
        lines = [
            f"Embedded mirror: {'✓ exists' if self.mirror_exists else '✗ does not exist'}",
        ]
        if self.mirror_ts and self.file_ts:
            if self.mirror_ts < self.file_ts:
                lines.append("⚠  The mirror is older than the file")
            else:
                lines.append("✓  Mirror up to date")
        lines.append("")
        if self.chunks:
            lines.append(f"{'ID':>4}  {'Status':<16}  Summary")
            lines.append("-" * 52)
            for c in self.chunks:
                icon = "✓" if c.ok else "✗"
                lines.append(f"{c.chunk_id:>4}  {icon} {c.status.value:<14}  {c.summary}")
        lines.append("")
        if self.all_ok:
            lines.append("✓ Everything is in order.")
        else:
            lines.append(f"✗ {len(self.problems)} problem(s) found.")
        return "\n".join(lines)


# ─── AMCXMirror — embedded SHA-1 block ───────────────────────────────────────

class AMCXMirror:
    """
    Reads and writes the SHA-1 mirror block inside the .amcx.

    Block format:
      AMCXM\\x00          magic (6 bytes)
      uint32 version
      uint64 timestamp
      uint32 num_entries
      [ for each chunk: ]
        uint32 chunk_id
        uint32 size_original
        20 bytes SHA-1 (raw)
        uint8  summary_len
        N bytes summary UTF-8

    The block is appended at the end of the file after the chunks
    and after the XOR recovery block if it exists.
    """

    @staticmethod
    def build_block(chunk_data: dict[int, tuple[bytes, str]], accelerator_path: Optional[str] = None) -> bytes:
        """
        Builds the mirror block in bytes.

        Args:
            chunk_data: {chunk_id: (original_content, summary)}
        """
        buf = bytearray()
        buf += MIRROR_MAGIC
        buf += struct.pack('>II', 1, len(chunk_data))           # version=1, num_entries
        buf += struct.pack('>Q', int(time.time()))              # timestamp

        for chunk_id, (data, summary) in sorted(chunk_data.items()):
            sha1          = _sha1_digest(data, accelerator_path)    # 20 raw bytes
            summary_bytes = summary.encode("utf-8")[:255]
            buf += struct.pack('>II', chunk_id, len(data))
            buf += sha1
            buf += struct.pack('>B', len(summary_bytes))
            buf += summary_bytes

        return bytes(buf)

    @staticmethod
    def read_block(amcx_path: str) -> Optional[dict]:
        """
        Reads the mirror block from the file if it exists.

        Returns:
            dict with 'timestamp' and 'chunks': {chunk_id: {'sha1': hex, 'summary': str}}
            or None if there is no mirror block.
        """
        with open(amcx_path, "rb") as f:
            data = f.read()

        pos = data.rfind(MIRROR_MAGIC)
        if pos == -1:
            return None

        pos += len(MIRROR_MAGIC)
        version, num_entries = struct.unpack_from('>II', data, pos); pos += 8
        timestamp,           = struct.unpack_from('>Q',  data, pos); pos += 8

        chunks = {}
        for _ in range(num_entries):
            chunk_id, size_orig = struct.unpack_from('>II', data, pos); pos += 8
            sha1_raw            = data[pos:pos+20]; pos += 20
            summary_len,        = struct.unpack_from('>B', data, pos); pos += 1
            summary             = data[pos:pos+summary_len].decode("utf-8", errors="replace")
            pos += summary_len
            chunks[chunk_id] = {
                "sha1":    sha1_raw.hex(),
                "summary": summary,
                "size":    size_orig,
            }

        return {"timestamp": timestamp, "chunks": chunks}

    @staticmethod
    def verify(amcx_path: str, accelerator_path: Optional[str] = None) -> MirrorStatus:
        """
        Compares the mirror block with the current chunks in the file.
        """
        import os
        from .reader import AMCXReader

        mirror_data = AMCXMirror.read_block(amcx_path)
        status = MirrorStatus(
            mirror_exists=mirror_data is not None,
            file_ts=int(os.path.getmtime(amcx_path)),
            mirror_ts=mirror_data["timestamp"] if mirror_data else None,
        )

        if not mirror_data:
            return status

        mirror_chunks = mirror_data["chunks"]

        with AMCXReader(amcx_path) as reader:
            orig_ids = {e.chunk_id for e in reader.list_chunks()}

            for entry in reader.list_chunks():
                cid = entry.chunk_id

                if cid not in mirror_chunks:
                    status.chunks.append(ChunkReport(
                        chunk_id=cid,
                        summary=entry.summary,
                        status=ChunkStatus.MISSING_MIRROR,
                    ))
                    continue

                try:
                    raw  = reader.read_chunk(cid)
                    sha1 = _sha1_digest(raw, accelerator_path).hex()
                except AMCXCorruptError:
                    sha1 = None

                mirror_sha1 = mirror_chunks[cid]["sha1"]

                if sha1 is None or sha1 != mirror_sha1:
                    chunk_status = ChunkStatus.MODIFIED
                elif status.mirror_ts and status.file_ts and status.mirror_ts < status.file_ts:
                    chunk_status = ChunkStatus.OUTDATED
                else:
                    chunk_status = ChunkStatus.OK

                status.chunks.append(ChunkReport(
                    chunk_id=cid,
                    summary=entry.summary,
                    status=chunk_status,
                    sha1_original=sha1,
                    sha1_mirror=mirror_sha1,
                ))

            # Chunks in the mirror that are no longer in the original
            for cid, info in mirror_chunks.items():
                if cid not in orig_ids:
                    status.chunks.append(ChunkReport(
                        chunk_id=cid,
                        summary=info["summary"],
                        status=ChunkStatus.MISSING_ORIG,
                        sha1_mirror=info["sha1"],
                    ))

        status.chunks.sort(key=lambda c: c.chunk_id)
        return status

    @staticmethod
    def embed(amcx_path: str, chunk_data: dict[int, tuple[bytes, str]], accelerator_path: Optional[str] = None) -> None:
        """Adds or replaces the mirror block at the end of the file."""
        with open(amcx_path, "rb") as f:
            data = f.read()

        # If there is already a mirror block, remove it before adding the new one
        pos = data.rfind(MIRROR_MAGIC)
        if pos != -1:
            data = data[:pos]

        with open(amcx_path, "wb") as f:
            f.write(data)
            f.write(AMCXMirror.build_block(chunk_data, accelerator_path))

    @staticmethod
    def update(amcx_path: str, accelerator_path: Optional[str] = None) -> None:
        """Regenerates the mirror block by reading the current state of the chunks."""
        from .reader import AMCXReader
        chunk_data = {}
        with AMCXReader(amcx_path) as reader:
            for entry in reader.list_chunks():
                raw = reader.read_chunk(entry.chunk_id)
                chunk_data[entry.chunk_id] = (raw, entry.summary)
        AMCXMirror.embed(amcx_path, chunk_data, accelerator_path)


# ─── AMCXRecovery — embedded XOR blocks ──────────────────────────────────────

class AMCXRecovery:
    """
    XOR recovery blocks embedded in the .amcx, inspired by WinRAR.

    Each group of N chunks has a parity block (XOR of all of them).
    If a chunk is damaged, it can be reconstructed with the others + the parity.

    Block format:
      AMCXR\\x00          magic (6 bytes)
      uint32 num_groups
      [ for each group: ]
        uint32 group_id
        uint32 num_chunk_ids
        [uint32 chunk_id ...]
        uint32 parity_size
        [bytes parity]
    """

    @staticmethod
    def append(amcx_path: str, group_size: int = 3, accelerator_path: Optional[str] = None) -> None:
        """Appends XOR recovery blocks to the file."""
        from .reader import AMCXReader

        with AMCXReader(amcx_path) as reader:
            entries = reader.list_chunks()
            groups  = [entries[i:i+group_size] for i in range(0, len(entries), group_size)]

            recovery_blocks = []
            for gidx, group in enumerate(groups):
                chunk_ids = [e.chunk_id for e in group]
                chunks    = [reader.read_chunk(cid) for cid in chunk_ids]
                parity    = _xor_parity(chunks, accelerator_path)
                recovery_blocks.append((gidx, chunk_ids, parity))

        with open(amcx_path, "ab") as f:
            f.write(RECOVERY_MAGIC)
            f.write(struct.pack('>I', len(recovery_blocks)))
            for gidx, chunk_ids, parity in recovery_blocks:
                f.write(struct.pack('>II', gidx, len(chunk_ids)))
                for cid in chunk_ids:
                    f.write(struct.pack('>I', cid))
                f.write(struct.pack('>I', len(parity)))
                f.write(parity)

    @staticmethod
    def can_recover(amcx_path: str, damaged_chunk_id: int) -> bool:
        blocks = AMCXRecovery._read_blocks(amcx_path)
        return any(damaged_chunk_id in ids for _, ids, _ in blocks)

    @staticmethod
    def recover_chunk(amcx_path: str, damaged_chunk_id: int, accelerator_path: Optional[str] = None) -> bytes:
        """Reconstructs a damaged chunk using XOR parity."""
        from .reader import AMCXReader

        for _, chunk_ids, parity in AMCXRecovery._read_blocks(amcx_path):
            if damaged_chunk_id not in chunk_ids:
                continue

            with AMCXReader(amcx_path) as reader:
                healthy = []
                for cid in chunk_ids:
                    if cid == damaged_chunk_id:
                        continue
                    try:
                        healthy.append(reader.read_chunk(cid))
                    except AMCXCorruptError:
                        raise AMCXError(
                            f"Cannot recover chunk {damaged_chunk_id}: "
                            f"chunk {cid} in the same group is also damaged."
                        )

            result = _xor_recover(parity, healthy, accelerator_path)
            return result.rstrip(b'\x00')

        raise AMCXError(f"No recovery block found for chunk {damaged_chunk_id}.")

    @staticmethod
    def _read_blocks(amcx_path: str) -> list[tuple[int, list[int], bytes]]:
        with open(amcx_path, "rb") as f:
            data = f.read()

        pos = data.rfind(RECOVERY_MAGIC)
        if pos == -1:
            return []

        pos   += len(RECOVERY_MAGIC)
        num_g, = struct.unpack_from('>I', data, pos); pos += 4
        blocks = []

        for _ in range(num_g):
            gidx, num_ids = struct.unpack_from('>II', data, pos); pos += 8
            chunk_ids     = list(struct.unpack_from(f'>{num_ids}I', data, pos)); pos += 4 * num_ids
            parity_size,  = struct.unpack_from('>I', data, pos); pos += 4
            parity        = data[pos:pos + parity_size]; pos += parity_size
            blocks.append((gidx, chunk_ids, parity))

        return blocks
