# amc/exceptions.py
# Custom errors for the .amcx library


class AMCXError(Exception):
    """Base class for all AMC errors."""


class AMCXInvalidFileError(AMCXError):
    """The file is not a valid .amcx (incorrect magic bytes, corrupt header, etc.)."""


class AMCXVersionError(AMCXError):
    """The file version is not compatible with this library."""


class AMCXCompressionError(AMCXError):
    """Error during compression or decompression of a chunk."""


class AMCXChunkNotFoundError(AMCXError):
    """A chunk was requested that does not exist in the index."""


class AMCXCorruptError(AMCXError):
    """CRC32 does not match — the file is corrupt."""


class AMCXReadOnlyError(AMCXError):
    """Attempted to write to a file marked as read-only."""


class AMCXSecurityError(AMCXError):
    """A bypass or security threat was detected."""
