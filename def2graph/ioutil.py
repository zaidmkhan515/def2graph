"""Shared file reading that transparently handles gzip-compressed inputs.

PD flows frequently emit compressed DEF/LEF/LIB (`*.def.gz`, `*.lib.gz`).
Detection is by magic bytes, so a gzipped file works even without a `.gz`
extension. Decoding is lenient (errors="replace") to tolerate stray bytes.
"""
from __future__ import annotations
import gzip

_GZIP_MAGIC = b"\x1f\x8b"


def _opener(path):
    with open(path, "rb") as fh:
        magic = fh.read(2)
    return gzip.open if magic == _GZIP_MAGIC else open


def read_text(path: str) -> str:
    """Return the full text of `path`, decompressing gzip automatically."""
    opener = _opener(path)
    with opener(path, "rt", errors="replace") as fh:
        return fh.read()


def read_lines(path: str):
    """Yield lines from `path` one at a time (gzip-aware, streaming).

    Used by the DEF/LEF parsers so a large routed DEF is never held in memory
    all at once.
    """
    opener = _opener(path)
    with opener(path, "rt", errors="replace") as fh:
        for line in fh:
            yield line
