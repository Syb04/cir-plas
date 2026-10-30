"""Structured view of the user's extra netlist (``circuit.extra_netlist``).

The GUI schematic editor edits two-terminal passive elements and DC sources graphically. Lines of the
forms below are parsed into :class:`UserElement`; every other line (comments, models, sources with
waveforms, ...) is kept verbatim as :class:`RawLine` so that round-tripping never loses user content.

    Rxxx n1 n2 value [tail]
    Lxxx n1 n2 value [tail]
    Cxxx n1 n2 value [tail]
    Vxxx n1 n2 [DC] value        (value 0 = short / current probe)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

RESERVED_NODES = ("W", "F", "T", "0")
USER_NODE_PREFIX = "u_"
KINDS = {"R": "抵抗", "L": "インダクタ", "C": "コンデンサ", "V": "DC 電圧源"}
UNITS = {"R": "Ω", "L": "H", "C": "F", "V": "V"}

_SUFFIX = [("meg", 1e6), ("t", 1e12), ("g", 1e9), ("k", 1e3), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)]
_NUM = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)$")


def parse_spice_value(token: str) -> Optional[float]:
    """Parse a SPICE number with scale suffix (``10u``, ``1meg``, ``2.2nF``); None if not a number."""
    m = _NUM.match(token.strip())
    if not m:
        return None
    mant, suffix = float(m.group(1)), m.group(2).lower()
    if not suffix:
        return mant
    for s, scale in _SUFFIX:
        if suffix.startswith(s):
            return mant * scale
    return mant  # unknown letters are units (e.g. "10V")


def format_spice_value(value: float) -> str:
    """Unambiguous SPICE literal (no scale letters: 'M' means milli in SPICE but mega to most users)."""
    if value == 0:
        return "0"
    return f"{value:.6g}"


_PREFIXES = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]


def format_eng(value: Optional[float], unit: str = "") -> str:
    """Human readable engineering notation: 2e-9, 'F' -> '2 nF'."""
    if value is None:
        return "—"
    if value == 0:
        return f"0 {unit}".strip()
    a = abs(value)
    for scale, prefix in _PREFIXES:
        if a >= scale * 0.9999:
            return f"{value / scale:.4g} {prefix}{unit}".strip()
    return f"{value:.3g} {unit}".strip()


@dataclass
class UserElement:
    kind: str  # R / L / C / V
    name: str  # full SPICE name (e.g. Ru1)
    n1: str
    n2: str
    value: float
    tail: str = ""  # extra tokens kept verbatim
    raw: Optional[str] = None  # original text (None once edited)

    @property
    def unit(self) -> str:
        return UNITS[self.kind]

    def to_line(self) -> str:
        if self.raw is not None:
            return self.raw
        dc = "DC " if self.kind == "V" else ""
        line = f"{self.name} {self.n1} {self.n2} {dc}{format_spice_value(self.value)}"
        return f"{line} {self.tail}".rstrip()

    def nodes(self) -> tuple[str, str]:
        return self.n1, self.n2


@dataclass
class RawLine:
    text: str

    def to_line(self) -> str:
        return self.text


Line = Union[UserElement, RawLine]


def parse_line(text: str) -> Line:
    s = text.strip()
    if not s or s.startswith("*") or s.startswith(".") or s.startswith("+"):
        return RawLine(text)
    tok = s.split()
    kind = tok[0][0].upper()
    if kind not in KINDS or len(tok) < 4:
        return RawLine(text)
    name, n1, n2 = tok[0], tok[1], tok[2]
    rest = tok[3:]
    if kind == "V":
        if rest[0].lower() == "dc":
            rest = rest[1:]
        if len(rest) != 1:
            return RawLine(text)  # AC / SIN / PULSE sources stay textual
        v = parse_spice_value(rest[0])
        return UserElement("V", name, n1, n2, v, "", text) if v is not None else RawLine(text)
    v = parse_spice_value(rest[0])
    if v is None:
        return RawLine(text)
    tail = " ".join(rest[1:])
    if tail.startswith(";") or tail.startswith("$"):
        tail = ""
    return UserElement(kind, name, n1, n2, v, tail, text)


@dataclass
class UserNetlist:
    lines: list[Line] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "UserNetlist":
        return cls([parse_line(ln) for ln in text.splitlines()])

    def to_text(self) -> str:
        return "\n".join(ln.to_line() for ln in self.lines)

    @property
    def elements(self) -> list[UserElement]:
        return [ln for ln in self.lines if isinstance(ln, UserElement)]

    @property
    def raw_lines(self) -> list[RawLine]:
        return [ln for ln in self.lines if isinstance(ln, RawLine) and ln.text.strip()]

    def names(self) -> set[str]:
        return {e.name.lower() for e in self.elements}

    def user_nodes(self) -> list[str]:
        """``u_`` nodes in order of first appearance."""
        seen: list[str] = []
        for e in self.elements:
            for n in e.nodes():
                if n.lower().startswith(USER_NODE_PREFIX) and n not in seen:
                    seen.append(n)
        return seen

    def all_nodes(self) -> list[str]:
        seen: list[str] = []
        for e in self.elements:
            for n in e.nodes():
                if n not in seen:
                    seen.append(n)
        return seen

    def next_name(self, kind: str) -> str:
        names = self.names()
        k = 1
        while f"{kind}u{k}".lower() in names:
            k += 1
        return f"{kind}u{k}"

    def next_node(self) -> str:
        used = {n.lower() for n in self.user_nodes()}
        k = 1
        while f"{USER_NODE_PREFIX}{k}" in used:
            k += 1
        return f"{USER_NODE_PREFIX}{k}"

    def add(self, kind: str, n1: str, n2: str, value: float, name: Optional[str] = None) -> UserElement:
        kind = kind.upper()
        if kind not in KINDS:
            raise ValueError(f"unsupported element kind {kind!r}")
        el = UserElement(kind, name or self.next_name(kind), n1, n2, float(value))
        self.lines.append(el)
        return el

    def remove(self, element: UserElement) -> None:
        self.lines = [ln for ln in self.lines if ln is not element]

    def index_of(self, element: UserElement) -> int:
        return self.elements.index(element)

    def touch(self, element: UserElement) -> None:
        """Mark an element as edited so that it is re-serialized from its fields."""
        element.raw = None

    def problems(self, available_nodes: set[str]) -> list[str]:
        """Warnings for the schematic (undefined reserved nodes, dangling user nodes, duplicate names)."""
        out: list[str] = []
        seen: dict[str, int] = {}
        for e in self.elements:
            seen[e.name.lower()] = seen.get(e.name.lower(), 0) + 1
            for n in e.nodes():
                if n.upper() in RESERVED_NODES and n.upper() not in available_nodes and n != "0":
                    out.append(f"{e.name}: ノード {n} はこの構成では存在しません")
        for name, count in seen.items():
            if count > 1:
                out.append(f"素子名 {name} が重複しています")
        counts: dict[str, int] = {}
        for e in self.elements:
            for n in e.nodes():
                if n.lower().startswith(USER_NODE_PREFIX):
                    counts[n] = counts.get(n, 0) + 1
        for n, c in counts.items():
            if c < 2:
                out.append(f"ノード {n} には素子が 1 つしか接続されていません（浮いています）")
        return out
