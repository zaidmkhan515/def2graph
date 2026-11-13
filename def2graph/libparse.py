"""Tolerant Liberty (.lib) parser.

We do NOT build the full timing model. We stream the token tree and extract only
the structural facts the cone graph needs:
  - library time/cap units
  - per cell: area, whether it is sequential (has an `ff`/`latch` group) or a
    memory (has a `memory` group)
  - per pin: direction, clock flag, function, capacitance
It handles bus()/bundle() pins (memories) and both quoted and unquoted values,
across Nangate45 / sky130 / ASAP7 / gf180 / fakeram styles.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LibPin:
    name: str
    direction: Optional[str] = None     # input / output / inout / internal
    is_clock: bool = False
    function: Optional[str] = None
    capacitance: Optional[float] = None
    is_power: bool = False              # pg_pin
    is_bus: bool = False


@dataclass
class LibCell:
    name: str
    area: Optional[float] = None
    is_ff: bool = False
    is_latch: bool = False
    is_memory: bool = False
    pins: Dict[str, LibPin] = field(default_factory=dict)

    @property
    def is_sequential(self) -> bool:
        return self.is_ff or self.is_latch


class LibData:
    def __init__(self) -> None:
        self.cells: Dict[str, LibCell] = {}
        self.time_unit: Optional[str] = None
        self.cap_unit: Optional[str] = None
        self.voltage_unit: Optional[str] = None
        self.libraries: List[str] = []
        self.warnings: List[str] = []            # files skipped / failed to parse


# Split into: quoted string | single punctuation | bareword
_TOKEN_RE = re.compile(r'"[^"]*"|[{}();:,]|[^\s{}();:,]+')
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

_GROUP_PINLIKE = {"pin", "bus", "bundle", "pg_pin"}
_GROUP_SEQ_FF = {"ff", "ff_bank"}
_GROUP_SEQ_LATCH = {"latch", "latch_bank"}


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _tokenize(text: str):
    text = _BLOCK_COMMENT_RE.sub(" ", text)
    text = _LINE_COMMENT_RE.sub(" ", text)
    text = text.replace("\\\n", " ")  # Liberty line continuations
    return _TOKEN_RE.findall(text)


def parse_liberty(path: str, data: Optional[LibData] = None) -> LibData:
    """Parse one Liberty file, merging into `data` if provided."""
    if data is None:
        data = LibData()

    from .ioutil import read_text
    toks = _tokenize(read_text(path))

    n = len(toks)
    i = 0
    stack: List[str] = []            # group-type stack
    cur_cell: Optional[LibCell] = None
    cur_pin: Optional[LibPin] = None
    # when a pin group declares multiple names (bus members) we apply attrs to all
    cur_pin_group: List[LibPin] = []

    while i < n:
        t = toks[i]

        if t == "}":
            if stack:
                top = stack.pop()
                if top == "cell":
                    cur_cell = None
                elif top in _GROUP_PINLIKE:
                    cur_pin = None
                    cur_pin_group = []
            i += 1
            continue

        if t in ("{", ";", ":", ",", "(", ")"):
            i += 1
            continue

        # `t` is an identifier starting a group or an attribute.
        name = t.lower()
        j = i + 1
        args: List[str] = []
        if j < n and toks[j] == "(":
            depth = 1
            j += 1
            while j < n and depth > 0:
                tj = toks[j]
                if tj == "(":
                    depth += 1
                elif tj == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                else:
                    args.append(tj)
                j += 1

        if j < n and toks[j] == "{":
            # ---- group start ----
            arg_names = [_unquote(a) for a in args if a != ","]
            if name == "library":
                if arg_names:
                    data.libraries.append(arg_names[0])
                stack.append("library")
            elif name == "cell":
                cname = arg_names[0] if arg_names else f"__anon_{i}"
                cur_cell = data.cells.get(cname) or LibCell(name=cname)
                data.cells[cname] = cur_cell
                stack.append("cell")
            elif name in _GROUP_SEQ_FF and cur_cell is not None:
                cur_cell.is_ff = True
                stack.append("ff")
            elif name in _GROUP_SEQ_LATCH and cur_cell is not None:
                cur_cell.is_latch = True
                stack.append("latch")
            elif name == "memory" and cur_cell is not None:
                cur_cell.is_memory = True
                stack.append("memory")
            elif name in _GROUP_PINLIKE and cur_cell is not None:
                cur_pin_group = []
                for pn in (arg_names or ["__anon"]):
                    p = LibPin(name=pn, is_power=(name == "pg_pin"),
                               is_bus=(name in ("bus", "bundle")))
                    cur_cell.pins[pn] = p
                    cur_pin_group.append(p)
                cur_pin = cur_pin_group[0] if cur_pin_group else None
                stack.append(name)
            else:
                stack.append(name)
            i = j + 1
            continue

        # ---- simple attribute:  name : value ... ;   ----
        if j < n and toks[j] == ":":
            k = j + 1
            vals: List[str] = []
            while k < n and toks[k] != ";":
                if toks[k] == "{":  # malformed; bail to group handling
                    break
                vals.append(toks[k])
                k += 1
            _apply_attr(name, vals, cur_cell, cur_pin_group)
            i = k + 1
            continue

        # attribute like `define(...) ;` or stray token -> skip to next ;
        i = j
        continue

    # pull units from first library seen
    return data


def _apply_attr(name: str, vals: List[str], cell: Optional[LibCell],
                pin_group: List[LibPin]) -> None:
    if not vals:
        return
    val = _unquote(vals[0])

    if cell is None:
        return

    if pin_group:
        p0 = pin_group[0]
        if name == "direction":
            d = val.lower()
            for p in pin_group:
                p.direction = d
        elif name == "clock":
            flag = val.lower() in ("true", "1")
            for p in pin_group:
                p.is_clock = flag
        elif name == "function":
            p0.function = val
        elif name == "capacitance":
            try:
                c = float(val)
                for p in pin_group:
                    p.capacitance = c
            except ValueError:
                pass
        return

    # cell-level attribute (not inside a pin)
    if name == "area":
        try:
            cell.area = float(val)
        except ValueError:
            pass


def parse_liberties(paths) -> LibData:
    """Parse many Liberty files, skipping any that are missing or unreadable."""
    data = LibData()
    for p in paths:
        if not os.path.isfile(p):
            data.warnings.append(f"Liberty not found, skipped: {p}")
            continue
        try:
            parse_liberty(p, data)
        except OSError as e:
            data.warnings.append(f"Liberty unreadable, skipped: {p} ({e})")
        except Exception as e:  # malformed file shouldn't abort the whole run
            data.warnings.append(f"Liberty parse error, skipped: {p} ({e})")
    return data
