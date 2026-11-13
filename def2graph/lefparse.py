"""Tolerant LEF parser.

We only extract what the graph needs:
  - MACRO name, CLASS, SIZE (width BY height), ORIGIN, SYMMETRY
  - PIN name, DIRECTION, USE
Geometry (PORT/OBS/LAYER/RECT/POLYGON) is intentionally skipped.

Works across vendor styles (Nangate45, ASAP7, sky130, gf180, fakeram) because it
is driven by keywords and a small block stack rather than fixed column layouts.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class LefPin:
    name: str
    direction: Optional[str] = None   # INPUT / OUTPUT / INOUT / FEEDTHRU
    use: Optional[str] = None         # SIGNAL / POWER / GROUND / CLOCK / ANALOG


@dataclass
class LefMacro:
    name: str
    cls: Optional[str] = None         # CORE / BLOCK / PAD / RING / COVER / ...
    subclass: Optional[str] = None
    size: Optional[Tuple[float, float]] = None   # (width, height) in microns
    origin: Optional[Tuple[float, float]] = None
    symmetry: Optional[str] = None
    site: Optional[str] = None
    pins: Dict[str, LefPin] = field(default_factory=dict)


class LefData:
    def __init__(self) -> None:
        self.macros: Dict[str, LefMacro] = {}
        self.dbu_microns: Optional[int] = None   # LEF database units per micron
        self.manufacturing_grid: Optional[float] = None
        self.warnings: List[str] = []            # files skipped / failed to parse


_DBU_RE = re.compile(r"DATABASE\s+MICRONS\s+([0-9.]+)")
_MFG_RE = re.compile(r"MANUFACTURINGGRID\s+([0-9.]+)")


def parse_lef(path: str, data: Optional[LefData] = None) -> LefData:
    """Parse one LEF file, merging into `data` if provided (for multi-LEF PDKs)."""
    if data is None:
        data = LefData()

    with open(path, "r", errors="replace") as fh:
        text = fh.read()

    m = _DBU_RE.search(text)
    if m and data.dbu_microns is None:
        data.dbu_microns = int(float(m.group(1)))
    m = _MFG_RE.search(text)
    if m and data.manufacturing_grid is None:
        data.manufacturing_grid = float(m.group(1))

    macro: Optional[LefMacro] = None
    pin: Optional[LefPin] = None
    skip_depth = 0  # inside PORT / OBS geometry block(s)

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        toks = line.split()
        kw = toks[0].upper()

        # Inside a geometry block we only care about finding its END.
        if skip_depth:
            if kw == "PORT" or kw == "OBS":
                skip_depth += 1
            elif kw == "END":
                skip_depth -= 1
            continue

        if kw == "MACRO":
            macro = LefMacro(name=toks[1])
            data.macros[macro.name] = macro
            pin = None
        elif macro is None:
            # outside any macro -> header statements we don't need
            continue
        elif kw == "CLASS":
            vals = [t for t in toks[1:] if t != ";"]
            macro.cls = vals[0].upper() if vals else None
            macro.subclass = vals[1].upper() if len(vals) > 1 else None
        elif kw == "SIZE":
            # SIZE <w> BY <h> ;
            try:
                w = float(toks[1]); h = float(toks[3])
                macro.size = (w, h)
            except (IndexError, ValueError):
                pass
        elif kw == "ORIGIN":
            try:
                macro.origin = (float(toks[1]), float(toks[2]))
            except (IndexError, ValueError):
                pass
        elif kw == "SYMMETRY":
            macro.symmetry = " ".join(t for t in toks[1:] if t != ";")
        elif kw == "SITE" and pin is None:
            macro.site = toks[1] if len(toks) > 1 else None
        elif kw == "PIN":
            pin = LefPin(name=toks[1])
            macro.pins[pin.name] = pin
        elif kw == "DIRECTION" and pin is not None:
            pin.direction = toks[1].upper().rstrip(";")
        elif kw == "USE" and pin is not None:
            pin.use = toks[1].upper().rstrip(";")
        elif kw == "PORT" or kw == "OBS":
            skip_depth = 1
        elif kw == "END":
            name = toks[1] if len(toks) > 1 else None
            if pin is not None and name == pin.name:
                pin = None
            elif name == macro.name:
                macro = None
                pin = None
            # a bare "END" or mismatched END is tolerated
    return data


def parse_lefs(paths) -> LefData:
    """Parse many LEF files, skipping any that are missing or unreadable."""
    data = LefData()
    for p in paths:
        if not os.path.isfile(p):
            data.warnings.append(f"LEF not found, skipped: {p}")
            continue
        try:
            parse_lef(p, data)
        except OSError as e:
            data.warnings.append(f"LEF unreadable, skipped: {p} ({e})")
        except Exception as e:  # malformed file shouldn't abort the whole run
            data.warnings.append(f"LEF parse error, skipped: {p} ({e})")
    return data
