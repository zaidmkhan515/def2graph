"""Shared file reading that transparently handles gzip-compressed inputs.

PD flows frequently emit compressed DEF/LEF/LIB (`*.def.gz`, `*.lib.gz`).
Detection is by magic bytes, so a gzipped file works even without a `.gz`
extension. Decoding is lenient (errors="replace") to tolerate stray bytes.
"""
from __future__ import annotations
import gzip

_GZIP_MAGIC = b"\x1f\x8b"


def read_text(path: str) -> str:
    """Return the full text of `path`, decompressing gzip automatically."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    opener = gzip.open if magic == _GZIP_MAGIC else open
    with opener(path, "rt", errors="replace") as fh:
        return fh.read()
