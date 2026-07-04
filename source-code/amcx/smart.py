# amcx/smart.py
# High-level API — the developer only uses this, everything else is automatic

import os
import time
import struct
import zlib
from typing import Optional, List
from datetime import datetime, timedelta

from .writer import AMCXWriter, ChunkEntry
from .reader import AMCXReader
from .mirror import MirrorMode, AMCXMirror, AMCXRecovery
from .format import CHUNK_GENERIC, CHUNK_ACTIVE, COMPRESS_ZLIB, COMPRESS_LZMA
from .exceptions import AMCXError, AMCXCorruptError


class SmartMemory:
    """
    Intelligent high-level API for AI memory.
    
    The developer only does:
        memory = SmartMemory("chat.amcx", use_mirror=False)
        memory.append("user: hello")
        memory.append("ai: hello, how are you?")
        results = memory.search("hello")
    
    Everything else is automatic:
    - Smart compression (zlib for recent chunks, lzma for old ones)
    - Splitting into chunks when they grow too large
    - Selective reading (only loads necessary chunks)
    - Automatic verification and recovery on corruption
    - No temporary files
    
    The end user NEVER sees any of this.
    """
    
    def __init__(
        self,
        path: str,
        use_mirror: bool = False,
        use_recovery: bool = False,
        auto_chunk_size: int = 2000,      # characters per chunk before splitting
        old_chunk_days: int = 7,          # days before a chunk is considered "old" → lzma
    ):
        """
        Args:
            path:            path to the .amcx file
            use_mirror:      if True, enables SHA-1 mirror (more space, more security)
            use_recovery:    if True, enables XOR recovery blocks
            auto_chunk_size: maximum size in characters before creating a new chunk
            old_chunk_days:  how many days before compressing with lzma instead of zlib
        """
        self.path = path
        self.use_mirror = use_mirror
        self.use_recovery = use_recovery
        self.auto_chunk_size = auto_chunk_size
        self.old_chunk_days = old_chunk_days
        
        # In-RAM cache of loaded chunks (to avoid reading disk repeatedly)
        self._cache: dict[int, str] = {}
        
        # Metadata loaded from the index (without loading content)
        self._index_cache: Optional[list] = None
        
        # New chunks that have not been saved yet
        self._pending: list[tuple[str, int]] = []  # [(text, timestamp)]
        
        # Load index if the file exists
        if os.path.exists(self.path):
            self._load_index()
    
    # ─── Public API ───────────────────────────────────────────────────────────
    
    def append(self, text: str) -> None:
        """
        Adds a message to memory.
        Saved automatically when enough content has accumulated.
        """
        self._pending.append((text, int(time.time())))
        
        # Auto-save when there is enough pending content
        total_pending = sum(len(t) for t, _ in self._pending)
        if total_pending >= self.auto_chunk_size:
            self.flush()
    
    def search(self, query: str, max_results: int = 5) -> List[str]:
        """
        Searches for relevant messages in memory.
        Only decompresses the necessary chunks.
        
        Args:
            query:       text to search for
            max_results: maximum number of results
        
        Returns:
            list of messages that contain the query
        """
        results = []
        query_lower = query.lower()
        
        if not os.path.exists(self.path):
            return results
        
        # Search first in the index (summaries) to optimize, but if summary doesn't match, search anyway
        try:
            with AMCXReader(self.path) as reader:
                for entry in reader.list_chunks():
                    try:
                        text = self._load_chunk(entry.chunk_id)
                        if query_lower in text.lower():
                            results.append(text)
                            if len(results) >= max_results:
                                break
                    except AMCXCorruptError:
                        # Corrupt chunk, try to recover
                        recovered = self._try_recover(entry.chunk_id)
                        if recovered and query_lower in recovered.lower():
                            results.append(recovered)
                            if len(results) >= max_results:
                                break
        except Exception:
            pass
        
        # Also search in pending (not yet saved)
        for text, _ in self._pending:
            if query_lower in text.lower():
                results.append(text)
                if len(results) >= max_results:
                    break
        
        return results
    
    def get_recent(self, n: int = 10) -> List[str]:
        """
        Gets the N most recent messages.
        Useful for loading conversational context.
        """
        messages = []
        
        # First the pending ones (most recent)
        for text, _ in reversed(self._pending):
            messages.append(text)
            if len(messages) >= n:
                return messages
        
        # Then from the file
        if os.path.exists(self.path):
            try:
                with AMCXReader(self.path) as reader:
                    entries = sorted(reader.list_chunks(), key=lambda e: e.timestamp, reverse=True)
                    for entry in entries:
                        if len(messages) >= n:
                            break
                        try:
                            text = self._load_chunk(entry.chunk_id)
                            messages.append(text)
                        except AMCXCorruptError:
                            recovered = self._try_recover(entry.chunk_id)
                            if recovered:
                                messages.append(recovered)
            except Exception:
                pass
        
        return messages
    
    def flush(self) -> None:
        """
        Saves all pending messages to the .amcx file.
        Called automatically, but can also be called manually.
        """
        if not self._pending:
            return
        
        # Load existing chunks — copy raw compressed bytes as-is, no decompress/recompress
        existing_chunks = []
        next_id = 0
        
        if os.path.exists(self.path):
            try:
                with AMCXReader(self.path) as reader:
                    for entry in reader.list_chunks():
                        reader._file.seek(entry.offset)
                        size_field = struct.unpack('>I', reader._file.read(4))[0]
                        compressed_bytes = reader._file.read(size_field)

                        actual_crc = zlib.crc32(compressed_bytes) & 0xFFFFFFFF
                        if actual_crc != entry.crc32:
                            # corrupt — try to recover the decompressed text and
                            # let it be recompressed fresh below instead of copied raw
                            recovered = self._try_recover(entry.chunk_id)
                            if recovered is None:
                                raise AMCXCorruptError(f"Chunk {entry.chunk_id} is corrupt and unrecoverable")
                            existing_chunks.append(ChunkEntry(
                                chunk_id=entry.chunk_id,
                                chunk_type=entry.chunk_type,
                                summary=entry.summary,
                                content=recovered.encode("utf-8"),
                                algorithm=entry.algorithm,
                                timestamp=entry.timestamp,
                            ))
                        else:
                            existing_chunks.append(ChunkEntry(
                                chunk_id=entry.chunk_id,
                                chunk_type=entry.chunk_type,
                                summary=entry.summary,
                                content=compressed_bytes,
                                algorithm=entry.algorithm,
                                timestamp=entry.timestamp,
                                pre_compressed=True,
                                crc32=entry.crc32,
                                size_original=entry.size_original,
                            ))
                        next_id = max(next_id, entry.chunk_id + 1)
            except Exception:
                pass
        
        # Create new chunks from pending
        now = time.time()
        cutoff = now - (self.old_chunk_days * 86400)
        
        for text, ts in self._pending:
            # Decide compression based on age
            algo = COMPRESS_LZMA if ts < cutoff else COMPRESS_ZLIB
            
            # Summary = first 60 characters
            summary = text[:60].replace("\n", " ")
            
            existing_chunks.append(ChunkEntry(
                chunk_id=next_id,
                chunk_type=CHUNK_ACTIVE if ts == self._pending[-1][1] else CHUNK_GENERIC,
                summary=summary,
                content=text.encode("utf-8"),
                algorithm=algo,
                timestamp=ts,
            ))
            next_id += 1
        
        # Write everything
        mirror_mode = MirrorMode.AUTO if self.use_mirror else MirrorMode.NONE
        writer = AMCXWriter(mirror=mirror_mode, recovery=self.use_recovery)
        
        for chunk in existing_chunks:
            writer.add_chunk(chunk)
        
        writer.save(self.path)
        
        # Clear pending and cache
        self._pending.clear()
        self._cache.clear()
        self._index_cache = None
    
    def verify_integrity(self) -> bool:
        """
        Verifies the integrity of the file.
        Returns True if everything is fine, False if there are problems.
        """
        if not os.path.exists(self.path):
            return True  # empty file = no problems
        
        if not self.use_mirror:
            # Without mirror, only verify CRC32 of each chunk
            try:
                with AMCXReader(self.path) as reader:
                    for entry in reader.list_chunks():
                        try:
                            reader.read_chunk(entry.chunk_id)
                        except AMCXCorruptError:
                            return False
                return True
            except Exception:
                return False
        else:
            # With mirror, use full verification
            status = AMCXMirror.verify(self.path)
            return status.all_ok
    
    def repair(self) -> bool:
        """
        Attempts to repair corrupt chunks using mirror or XOR recovery.
        Returns True if something was repaired, False if there was nothing to repair or it failed.
        """
        if not os.path.exists(self.path):
            return False
        
        repaired = False
        chunks_to_repair = []
        
        try:
            with AMCXReader(self.path) as reader:
                for entry in reader.list_chunks():
                    try:
                        reader.read_chunk(entry.chunk_id)
                    except AMCXCorruptError:
                        chunks_to_repair.append(entry.chunk_id)
        except Exception:
            return False
        
        if not chunks_to_repair:
            return False
        
        # Attempt to recover each damaged chunk
        for cid in chunks_to_repair:
            recovered = self._try_recover(cid)
            if recovered:
                repaired = True
        
        return repaired
    
    def size_on_disk(self) -> int:
        """Returns the file size in bytes."""
        if not os.path.exists(self.path):
            return 0
        return os.path.getsize(self.path)
    
    def count_messages(self) -> int:
        """Returns the total number of saved + pending messages."""
        count = len(self._pending)
        if os.path.exists(self.path):
            try:
                with AMCXReader(self.path) as reader:
                    count += reader.header.num_chunks
            except Exception:
                pass
        return count
    
    # ─── Internals ────────────────────────────────────────────────────────────
    
    def _load_index(self):
        """Loads only the index (without chunk content)."""
        if not os.path.exists(self.path):
            return
        try:
            with AMCXReader(self.path) as reader:
                self._index_cache = reader.list_chunks()
        except Exception:
            self._index_cache = None
    
    def _load_chunk(self, chunk_id: int) -> str:
        """Loads and caches a chunk in RAM."""
        if chunk_id in self._cache:
            return self._cache[chunk_id]
        
        with AMCXReader(self.path) as reader:
            text = reader.read_chunk_text(chunk_id)
            self._cache[chunk_id] = text
            return text
    
    def _try_recover(self, chunk_id: int) -> Optional[str]:
        """Attempts to recover a corrupt chunk."""
        if self.use_recovery and AMCXRecovery.can_recover(self.path, chunk_id):
            try:
                recovered = AMCXRecovery.recover_chunk(self.path, chunk_id)
                text = recovered.decode("utf-8", errors="replace")
                self._cache[chunk_id] = text
                return text
            except Exception:
                pass
        return None
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.flush()
    
    def __del__(self):
        # Auto-save when the object is destroyed
        if hasattr(self, '_pending') and self._pending:
            try:
                self.flush()
            except Exception:
                pass
