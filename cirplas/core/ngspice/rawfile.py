"""Reader for ngspice binary/ASCII rawfiles (batch-mode fallback)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .shared import RawResult


def read_rawfile(path: str | Path) -> list[RawResult]:
    data = Path(path).read_bytes()
    results: list[RawResult] = []
    pos = 0
    while pos < len(data):
        # header is ASCII up to "Binary:\n" or "Values:\n"
        m = re.search(rb"(Binary:|Values:)\r?\n", data[pos:])
        if not m:
            break
        header = data[pos:pos + m.start()].decode("latin-1")
        kind = m.group(1).decode()
        body_start = pos + m.end()
        fields = {}
        var_lines = []
        in_vars = False
        for line in header.splitlines():
            if line.startswith("Variables:"):
                in_vars = True
                continue
            if in_vars:
                var_lines.append(line.strip())
            elif ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
        nvar = int(fields["No. Variables"])
        npts = int(fields["No. Points"])
        is_complex = "complex" in fields.get("Flags", "")
        names = []
        for vl in var_lines[:nvar]:
            parts = vl.split()
            names.append(parts[1].lower())
        if kind == "Binary:":
            per_point = nvar * (16 if is_complex else 8)
            raw = np.frombuffer(data, dtype=np.float64, count=npts * nvar * (2 if is_complex else 1), offset=body_start)
            pos = body_start + npts * per_point
            if is_complex:
                raw = raw.reshape(npts, nvar, 2)
                arr = raw[:, :, 0] + 1j * raw[:, :, 1]
            else:
                arr = raw.reshape(npts, nvar)
        else:
            # ASCII values: "index v0\n v1\n ..." repeated
            tail = data[body_start:].decode("latin-1")
            tokens = tail.split()
            vals = []
            k = 0
            for _ in range(npts):
                k += 1  # index
                row = []
                for _ in range(nvar):
                    t = tokens[k]
                    k += 1
                    if "," in t:
                        a, b = t.split(",")
                        row.append(complex(float(a), float(b)))
                    else:
                        row.append(float(t))
                vals.append(row)
            arr = np.array(vals)
            pos = len(data)
        vectors = {names[j]: arr[:, j] for j in range(nvar)}
        scale = names[0]
        if is_complex and scale == "frequency":
            vectors["frequency"] = vectors["frequency"].real
        ptype = "ac" if "AC" in fields.get("Plotname", "") else ("tran" if "Transient" in fields.get("Plotname", "") else fields.get("Plotname", ""))
        results.append(RawResult(plot_name=fields.get("Plotname", ""), plot_type=ptype, scale_name=scale, vectors=vectors))
    return results
