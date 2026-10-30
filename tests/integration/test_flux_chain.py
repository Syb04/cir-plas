"""V4: flux-chain realization of the mutual inductance matrix, and charge conservation of the
sheath capacitor realization (spec 05 section V4)."""

import math

import numpy as np

from cirplas.core.constants import MU0


def _chain_netlist(zetas, currents, f, dr, r_b):
    c = MU0 * dr / (2 * math.pi * r_b)
    lines = ["* flux chain unit test"]
    for j, (z, i_amp) in enumerate(zip(zetas, currents), start=1):
        lines += [
            f"I{j} 0 n{j}a AC {i_amp} 0",
            f"E{j} n{j}a n{j}b A{j} 0 1",
            f"V{j} n{j}b 0 0",
            f"F{j} 0 A{j} V{j} 1",
        ]
    prev = 0.0
    for k, z in enumerate(zetas, start=1):
        lines.append(f"Lch{k} {'0' if k == 1 else 'A'+str(k-1)} A{k} {c*(z-prev):.9e}")
        prev = z
    lines += [f".ac lin 1 {f} {f}", ".save all", ".end"]
    return "\n".join(lines) + "\n", c


def test_flux_chain_matches_mutual_inductance_matrix(ngspice):
    zetas = [1.39e-3, 5.0e-3, 1.69e-2, 2.6e-2, 3.18e-2]
    currents = [1.0, 0.3, 2.0, -0.7, 0.5]
    f, dr, r_b = 60e6, 5e-3, 0.12
    net, c = _chain_netlist(zetas, currents, f, dr, r_b)
    res = ngspice.run(net)
    assert not res.errors, res.errors
    w = 2 * math.pi * f
    z = np.array(zetas)
    L = c * np.minimum.outer(z, z)
    expected = 1j * w * L @ np.array(currents)
    for j in range(len(zetas)):
        got = complex(res.get(f"v(n{j+1}a)")[0])
        assert abs(got - expected[j]) <= 1e-6 * abs(expected[j]), (j, got, expected[j])


def test_sheath_capacitor_realizations_conserve_charge(ngspice):
    """Drive the three realizations of Q(V) with a sinusoidal voltage and check that the
    integrated current equals the change of Q(V) at every time."""
    from cirplas.core.netlist.builder import sheath_subckt
    from cirplas.core.project import SheathSpec
    from cirplas.core.sheath_models import make_sheath_params
    from cirplas.core.constants import AMU

    sp = make_sheath_params(1e-2, 2e15, 2.5, 40 * AMU, SheathSpec())
    for form in ("ecf", "charge"):
        sub = sheath_subckt("diode", form)
        # isolate the capacitor: huge Ii/Ie0 removal by setting them tiny
        net = "\n".join([
            "* charge conservation test", sub,
            f"X1 p 0 sheath A={sp.area} ns={sp.ns} Te={sp.te} beta={sp.beta} s1={sp.s1} Vmin={sp.vmin} eps={sp.eps} Rst=1e-9 Ii=1e-30 Ie0=1e-30 nd={sp.nd}",
            "Vsrc p 0 DC 80 SIN(80 60 1e6)",
            ".options rshunt=1e12 method=gear maxord=2",
            ".tran 1e-9 3e-6 0 1e-9",
            ".save all", ".end",
        ]) + "\n"
        res = ngspice.run(net)
        assert not res.errors, (form, res.errors)
        t = res.scale
        v = res.get("v(p)")
        i_c = res.get("v.x1.vsc#branch")  # current N->W through the sense source = capacitive current into the wall node
        q_int = np.concatenate([[0.0], np.cumsum(0.5 * (i_c[1:] + i_c[:-1]) * np.diff(t))])
        dq = sp.charge(v) - sp.charge(v[0])
        err = np.max(np.abs(q_int - dq)) / (dq.max() - dq.min())
        assert err < 2e-3, (form, err)
