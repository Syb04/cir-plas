"""L-type matching network design from the AC port impedance (spec 01 section 5.1, Phase 2).

The port chain (feed inductance, blocking capacitor, stray capacitance, plasma) presents Z_L = R + jX at the
fundamental. An L network transforms it to the generator impedance r_gen:

* R < r_gen: series element on the load side, shunt capacitor on the generator side
  ("shunt_c_generator"): series reactance X_s = +-sqrt(R (r_gen - R)) - X, shunt B = +-sqrt((r_gen - R)/R)/r_gen.
* R > r_gen: shunt capacitor on the load side, series inductor on the generator side ("shunt_c_load").

Only the inductor/capacitor solution (series L, shunt C) is used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class LMatch:
    topology: str
    l: float  # series inductance [H]
    c: float  # shunt capacitance [F]
    z_load: complex
    f: float


def design_l_match(z_load: complex, f: float, r_gen: float = 50.0, topology: str = "auto") -> LMatch:
    w = 2 * math.pi * f
    R, X = z_load.real, z_load.imag
    if R <= 0:
        raise ValueError("load resistance must be positive for matching")
    if topology == "auto":
        topology = "shunt_c_generator" if R < r_gen else "shunt_c_load"
    if topology == "shunt_c_generator":
        if R >= r_gen:
            raise ValueError("shunt_c_generator topology needs R < r_gen")
        q = math.sqrt(r_gen / R - 1.0)
        x_series = q * R - X  # total series reactance needed (inductive if positive)
        if x_series <= 0:
            raise ValueError("load too inductive for a series-L / shunt-C match at this frequency")
        l = x_series / w
        b_shunt = q / r_gen
        c = b_shunt / w
        return LMatch(topology, l, c, z_load, f)
    # shunt capacitor across the load first, then series inductor to the generator
    if R <= r_gen:
        raise ValueError("shunt_c_load topology needs R > r_gen")
    y = 1.0 / z_load
    g, b = y.real, y.imag
    # shunt susceptance B_c such that Re(1/(g + j(b + B_c))) = r_gen:  g/(g^2 + B_t^2) = r_gen  ->  B_t^2 = g/r_gen - g^2
    bt2 = g / r_gen - g * g
    if bt2 < 0:
        raise ValueError("no L-match solution")
    bt = math.sqrt(bt2)  # B_t > 0 gives a capacitive remainder that the series inductor cancels
    b_c = bt - b
    if b_c <= 0:
        raise ValueError("load too capacitive for a shunt-C / series-L match")
    z2 = 1.0 / complex(g, bt)
    x_series = -z2.imag
    if x_series <= 0:
        raise ValueError("no inductive series solution")
    return LMatch(topology, x_series / w, b_c / w, z_load, f)
