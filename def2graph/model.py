"""Unified cell model: merge LEF geometry + Liberty structure, classify boundaries.

A cell is a boundary (cone-terminating) if it is:
  * sequential  - Liberty cell has an ff/latch group, or
  * a memory    - Liberty cell has a memory group, or
  * a hard macro - LEF CLASS is BLOCK (and not a known std cell)

Pin direction is taken from Liberty when available (most reliable, gives clock
flags too) and falls back to LEF. Unknown cells are treated as pass-through with
bidirectional pins so connectivity is never silently dropped.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional

from .lefparse import LefData
from .libparse import LibData

# LEF CLASS values that denote a hard block / boundary macro
_MACRO_CLASSES = {"BLOCK", "RING"}

# pin-direction normalisation
_IN = "INPUT"
_OUT = "OUTPUT"
_INOUT = "INOUT"


@dataclass
class PinInfo:
    name: str
    direction: str = _INOUT       # INPUT / OUTPUT / INOUT
    is_clock: bool = False
    is_power: bool = False
    function: Optional[str] = None


@dataclass
class CellInfo:
    name: str
    kind: str = "comb"            # comb / flop / memory / unknown
    area: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    cls: Optional[str] = None     # LEF CLASS
    pins: Dict[str, PinInfo] = field(default_factory=dict)

    @property
    def is_boundary(self) -> bool:
        return self.kind in ("flop", "memory")

    def pin_dir(self, pin: str) -> str:
        p = self.pins.get(pin)
        return p.direction if p else _INOUT


def _norm_dir(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    d = d.upper()
    if d in ("INPUT", "IN"):
        return _IN
    if d in ("OUTPUT", "OUT"):
        return _OUT
    if d in ("INOUT", "BIDIRECTIONAL", "FEEDTHRU", "TRISTATE"):
        return _INOUT
    if d == "INTERNAL":
        return "INTERNAL"
    return _INOUT


class CellLibrary:
    def __init__(self) -> None:
        self.cells: Dict[str, CellInfo] = {}
        self.stats = {"flop": 0, "memory": 0, "comb": 0, "unknown": 0,
                      "from_lef_only": 0, "from_lib_only": 0, "merged": 0}

    @classmethod
    def from_parsed(cls, lef: LefData, lib: LibData) -> "CellLibrary":
        self = cls()
        names = set(lef.macros) | set(lib.cells)
        for name in names:
            lm = lef.macros.get(name)
            lc = lib.cells.get(name)
            ci = CellInfo(name=name)

            if lm is not None:
                ci.cls = lm.cls
                if lm.size:
                    ci.width, ci.height = lm.size
                for pn, lp in lm.pins.items():
                    d = _norm_dir(lp.direction) or _INOUT
                    is_pwr = (lp.use in ("POWER", "GROUND"))
                    ci.pins[pn] = PinInfo(name=pn, direction=d, is_power=is_pwr)

            if lc is not None:
                ci.area = lc.area
                for pn, lp in lc.pins.items():
                    d = _norm_dir(lp.direction)
                    existing = ci.pins.get(pn)
                    if existing is None:
                        ci.pins[pn] = PinInfo(
                            name=pn, direction=d or _INOUT,
                            is_clock=lp.is_clock, is_power=lp.is_power,
                            function=lp.function)
                    else:
                        if d:                     # Liberty direction wins
                            existing.direction = d
                        existing.is_clock = existing.is_clock or lp.is_clock
                        existing.is_power = existing.is_power or lp.is_power
                        if lp.function:
                            existing.function = lp.function

            # ---- classify ----
            is_block = lm is not None and (lm.cls in _MACRO_CLASSES)
            is_seq = lc is not None and lc.is_sequential
            if lc is not None and lc.is_memory:
                ci.kind = "memory"
            elif is_block and not is_seq:
                # A hard macro (LEF CLASS BLOCK/RING) that is not a flip-flop is
                # a memory/macro boundary -- trust LEF even if the Liberty cell
                # says otherwise (or has no memory() group).
                ci.kind = "memory"
            elif is_seq:
                ci.kind = "flop"
            elif ci.pins:
                ci.kind = "comb"
            else:
                ci.kind = "unknown"

            self.cells[name] = ci
            self.stats[ci.kind] = self.stats.get(ci.kind, 0) + 1
            if lm is not None and lc is not None:
                self.stats["merged"] += 1
            elif lm is not None:
                self.stats["from_lef_only"] += 1
            else:
                self.stats["from_lib_only"] += 1
        return self

    def get(self, name: str) -> Optional[CellInfo]:
        return self.cells.get(name)
