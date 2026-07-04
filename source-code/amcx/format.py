# amcx/format.py
# Binary format definition for .amcx
# AMCX = Adaptive Memory Chunks X

import struct

# ─── Magic & Version ───────────────────────────────────────────────────────────
MAGIC = b'AMC\x00'          # 4 bytes that identify the file
VERSION_MAJOR = 0
VERSION_MINOR = 2            # version 0.2 — adds per-chunk checksum

# ─── Compression ───────────────────────────────────────────────────────────────
COMPRESS_NONE = 0x00         # no compression
COMPRESS_ZLIB = 0x01         # zlib  → active chunks (fast)
COMPRESS_LZMA = 0x02         # lzma  → old chunks (maximum compression)

# ─── Chunk types ───────────────────────────────────────────────────────────────
CHUNK_LORE      = 0x00       # lore / world / rules
CHUNK_CHARACTER = 0x01       # character
CHUNK_EVENT     = 0x02       # narrative event
CHUNK_ACTIVE    = 0x03       # active chunk (the most recent)
CHUNK_GENERIC   = 0x04       # generic content

# ─── Header flags (2-byte bitfield) ───────────────────────────────────────────
FLAG_COMPRESSED  = 1 << 0    # at least one chunk has compression
FLAG_ENCRYPTED   = 1 << 1    # reserved for future encryption
FLAG_READONLY    = 1 << 2    # read-only file
FLAG_HAS_ACTIVE  = 1 << 3    # there is a chunk marked as active
FLAG_HAS_ASSETS  = 1 << 4    # contains assets/images (future)

# ─── Fixed sizes in bytes ──────────────────────────────────────────────────────
HEADER_SIZE      = 32        # total size of the header
SUMMARY_SIZE     = 64        # bytes reserved for the summary in the index

# ─── Header layout ─────────────────────────────────────────────────────────────
# Offset  Size    Field
# 0x00    4       Magic "AMC\0"
# 0x04    1       Version major
# 0x05    1       Version minor
# 0x06    4       Number of chunks
# 0x0A    8       Creation timestamp (unix, big-endian)
# 0x12    4       Offset where the index starts
# 0x16    4       Size of the index block
# 0x1A    2       Flags
# 0x1C    4       CRC32 of the header (first 28 bytes)
HEADER_STRUCT = struct.Struct('>4sBBIQIIHI')
# Fields: magic, v_major, v_minor, num_chunks, timestamp, index_offset, index_size, flags, crc32

# ─── Layout of each index entry ───────────────────────────────────────────────
# Offset  Size    Field
# 0x00    4       Chunk ID
# 0x04    4       Chunk offset in the file
# 0x08    4       Compressed size
# 0x0C    4       Original size (before compression)
# 0x10    2       Chunk type
# 0x12    1       Compression algorithm
# 0x13    1       Reserved
# 0x14    8       Chunk timestamp
# 0x1C    4       CRC32 of the chunk (over compressed bytes)  ← NEW
# 0x20    64      Summary in UTF-8 (null-padded)
INDEX_ENTRY_STRUCT = struct.Struct('>IIIIHBBQI64s')
# Fields: chunk_id, offset, size_c, size_o, ctype, algo, reserved, ts, crc32, summary

INDEX_ENTRY_SIZE = INDEX_ENTRY_STRUCT.size  # automatically calculated from the struct
