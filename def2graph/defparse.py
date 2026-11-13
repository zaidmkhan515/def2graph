"""Tolerant DEF parser.

Extracts the design-level information the cone graph needs:
  - DESIGN name, UNITS (dbu per micron), DIEAREA
  - PINS   -> top-level design ports (name, direction, use, net, location)
  - COMPONENTS -> instances (name, macro/cell, placement x/y, orientation)
  - NETS   -> connectivity: netname + list of (instance, pin) / (PIN, port)

SPECIALNETS (power/ground routing) are skipped. Routing geometry in NETS is
skipped: only the connection tuples before the first `+` attribute are read.
Works across OpenROAD / sky130 / Nangate45 DEF styles and any DBU scale.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class DefPort:
    name: str
    direction: Optional[str] = None   # INPUT / OUTPUT / INOUT
    use: Optional[str] = None         # SIGNAL / POWER / GROUND / CLOCK
    net: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    layer: Optional[str] = None


@dataclass
class DefComponent:
    name: str
    cell: str
    x: Optional[int] = None
    y: Optional[int] = None
    orient: Optional[str] = None
    status: Optional[str] = None       # PLACED / FIXED / UNPLACED / COVER


@dataclass
class DefNet:
    name: str
    use: Optional[str] = None
    # connections: (instance_name, pin_name) ; top ports appear as ("PIN", port)
    conns: List[Tuple[str, str]] = field(default_factory=list)


class DefData:
    def __init__(self) -> None:
        self.design: Optional[str] = None
        self.dbu_microns: Optional[int] = None
        self.diearea: Optional[List[Tuple[int, int]]] = None
        self.ports: Dict[str, DefPort] = {}
        self.components: Dict[str, DefComponent] = {}
        self.nets: Dict[str, DefNet] = {}


_CONN_RE = re.compile(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)")
_PLACE_RE = re.compile(r"\b(PLACED|FIXED|COVER)\b\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*(\w+)?")

_SECTIONS = ("COMPONENTS", "PINS", "NETS", "SPECIALNETS")


# Start of a net's physical wiring ( + ROUTED / + FIXED / + COVER ). Everything
# from here to the ';' is routing geometry we do not read.
_ROUTING_RE = re.compile(r"\+\s*(?:ROUTED|FIXED|COVER)\b", re.I)


def parse_def(path: str) -> DefData:
    """Stream a DEF line by line (gzip-aware). Net routing geometry is skipped;
    only connectivity (instance/pin tuples) and USE are read from NETS."""
    from .ioutil import read_lines
    data = DefData()

    section: Optional[str] = None
    buf: List[str] = []
    skip_routing = False   # inside a NET entry, past its connectivity

    def flush_entry() -> None:
        if not buf:
            return
        text = " ".join(buf).strip()
        buf.clear()
        if not text.startswith("-"):
            return
        if section == "COMPONENTS":
            _parse_component(text, data)
        elif section == "PINS":
            _parse_pin(text, data)
        elif section == "NETS":
            _parse_net(text, data)
        # SPECIALNETS ignored entirely

    for raw in read_lines(path):
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            continue

        if section is None:
            head = stripped.split()
            kw = head[0].upper()
            if kw == "DESIGN":
                data.design = head[1].rstrip(";") if len(head) > 1 else None
            elif kw == "UNITS" and len(head) >= 4 and head[1].upper() == "DISTANCE":
                try:
                    data.dbu_microns = int(float(head[3]))
                except ValueError:
                    pass
            elif kw == "DIEAREA":
                nums = re.findall(r"-?\d+", stripped)
                if len(nums) >= 4:
                    data.diearea = [(int(nums[k]), int(nums[k + 1]))
                                    for k in range(0, len(nums) - 1, 2)]
            elif kw in _SECTIONS:
                section = kw
                buf.clear()
                skip_routing = False
            continue

        # ---- inside a section ----
        upper = stripped.upper()
        if upper.startswith("END " + section):
            flush_entry()
            section = None
            skip_routing = False
            continue

        # SPECIALNETS: power/ground routing, not used -> skip without buffering
        if section == "SPECIALNETS":
            continue

        if section == "NETS":
            if skip_routing:
                if ";" in stripped:      # entry ends -> ready for the next one
                    skip_routing = False
                continue
            if stripped.startswith("-") and buf:
                flush_entry()            # previous entry had no routing/terminator
            m = _ROUTING_RE.search(stripped)
            if m:
                buf.append(stripped[:m.start()])
                flush_entry()            # connectivity + USE are complete here
                if ";" not in stripped[m.start():]:
                    skip_routing = True   # swallow the rest of the routing
                continue
            # connectivity line (may wrap across several lines before the first '+')
            if not stripped.startswith("-") and not buf:
                continue                 # section header line, e.g. "NETS 7680 ;"
            buf.append(stripped)
            if ";" in stripped:
                flush_entry()
            continue

        # ---- COMPONENTS / PINS ----
        if not stripped.startswith("-") and not buf:
            continue                     # section header line
        buf.append(stripped)
        if ";" in stripped:
            flush_entry()

    return data


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


def _parse_component(text: str, data: DefData) -> None:
    toks = text.split()
    # - <instName> <cellName> + ...
    if len(toks) < 3:
        return
    inst, cell = toks[1], toks[2]
    comp = DefComponent(name=inst, cell=cell)
    m = _PLACE_RE.search(text)
    if m:
        comp.status = m.group(1)
        comp.x = int(m.group(2))
        comp.y = int(m.group(3))
        comp.orient = m.group(4)
    elif "UNPLACED" in text:
        comp.status = "UNPLACED"
    data.components[inst] = comp


def _parse_pin(text: str, data: DefData) -> None:
    toks = text.split()
    if len(toks) < 2:
        return
    name = toks[1]
    port = DefPort(name=name)
    # scan for + NET / + DIRECTION / + USE / + LAYER / + PLACED|FIXED ( x y )
    t = toks
    for i, tok in enumerate(t):
        up = tok.upper()
        if up == "NET" and i + 1 < len(t):
            port.net = t[i + 1]
        elif up == "DIRECTION" and i + 1 < len(t):
            port.direction = t[i + 1].upper()
        elif up == "USE" and i + 1 < len(t):
            port.use = t[i + 1].upper()
        elif up == "LAYER" and i + 1 < len(t) and port.layer is None:
            port.layer = t[i + 1]
    m = _PLACE_RE.search(text)
    if m:
        port.x = int(m.group(2))
        port.y = int(m.group(3))
    data.ports[name] = port


def _parse_net(text: str, data: DefData) -> None:
    toks = text.split()
    if len(toks) < 2:
        return
    name = toks[1]
    net = DefNet(name=name)
    # connections appear before the first '+' attribute
    before_plus = text.split("+", 1)[0]
    # drop the "- name" prefix
    after_name = before_plus.split(None, 2)
    conn_region = after_name[2] if len(after_name) > 2 else ""
    for a, b in _CONN_RE.findall(conn_region):
        net.conns.append((a, b))
    # USE attribute
    m = re.search(r"\bUSE\s+(\w+)", text)
    if m:
        net.use = m.group(1).upper()
    data.nets[name] = net
