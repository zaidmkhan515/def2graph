"""def2graph - convert DEF/LEF/Liberty into a port-bounded logic-cone graph.

Pipeline:
    LEF  -> macro geometry, pin directions, CLASS (macro/block detection)
    LIB  -> cell area, pin directions, clock pins, ff/latch (flop) & memory groups
    DEF  -> design ports (PINS), instances (COMPONENTS), connectivity (NETS)

A directed signal-flow graph is built (ports -> nets -> combinational cells -> ...).
From each top-level design port we trace the combinational cone until we hit a
sequential element (flip-flop / latch) or a memory/macro, which are recorded as
terminal endpoints.

Public entry point: def2graph.build.build_from_files(...)
"""

from .model import CellLibrary, CellInfo
from .conegraph import ConeGraph

__all__ = ["CellLibrary", "CellInfo", "ConeGraph"]
__version__ = "0.1.0"
