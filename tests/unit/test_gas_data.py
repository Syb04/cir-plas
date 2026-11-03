"""Gas data: tabulated rates, Maxwellian integration of cross sections, gas files given by path."""

import json
import math

import numpy as np
import pytest

from cirplas.core.constants import E_CHARGE, M_E
from cirplas.core.materials import TE_TABLE, gas_db, maxwellian_rate
from cirplas.core.plasma import d_eff, te_particle_balance


def v_mean(te):
    return math.sqrt(8 * E_CHARGE * te / (math.pi * M_E))


@pytest.mark.parametrize("te", [1.0, 3.0, 8.0])
def test_maxwellian_rate_analytic(te):
    e = np.array([0.0, 1000.0])
    # constant cross section: K = sigma v_mean
    assert maxwellian_rate(e, [1e-19, 1e-19], te) == pytest.approx(1e-19 * v_mean(te), rel=1e-3)
    # step at threshold E0: K = sigma0 v_mean (1 + E0/Te) exp(-E0/Te)
    e0 = 15.0
    es = np.array([e0, e0 + 1e-9, 1000.0])
    k = maxwellian_rate(es, [0.0, 2e-20, 2e-20], te)
    assert k == pytest.approx(2e-20 * v_mean(te) * (1 + e0 / te) * math.exp(-e0 / te), rel=2e-3)


def _write_gas(tmp_path, rates, **extra):
    d = {"name": "test", "mass_amu": 39.948, "sigma_i_m2": 1e-18, "E_iz_eV": 15.76, "E_ex_eV": 12.14, "rates": rates, **extra}
    path = tmp_path / "test.gas.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    return str(path)


def test_table_reproduces_bundled_argon(tmp_path):
    ar = gas_db("ar")
    rates = {key: {"type": "table", "te": list(TE_TABLE), "k": [getattr(ar, key)(t) for t in TE_TABLE]} for key in ("K_iz", "K_ex", "K_el")}
    tab = gas_db(_write_gas(tmp_path, rates))
    for te in (0.7, 2.5, 6.0):
        assert tab.K_iz(te) == pytest.approx(ar.K_iz(te), rel=1e-3)
        assert tab.eps_c(te) == pytest.approx(ar.eps_c(te), rel=1e-3)
    n_g, de = 1.2e21, d_eff(0.15, 0.025, 0.2, 0.1)
    assert te_particle_balance(tab, n_g, de) == pytest.approx(te_particle_balance(ar, n_g, de), rel=1e-3)


def test_cross_section_file(tmp_path):
    e = np.concatenate([[15.76], np.linspace(16.0, 500.0, 200)])
    s = 3e-20 * np.log(e / 15.76) / (e / 15.76)  # Lotz-like shape
    np.savetxt(tmp_path / "iz.csv", np.column_stack([e, s]), delimiter=",", header="E_eV,sigma_m2")
    rates = {"K_iz": {"type": "cross_section", "file": "iz.csv"},
             "K_ex": {"type": "arrhenius_power", "A": 2.48e-14, "b": 0.33, "E": 12.78},
             "K_el": {"type": "cross_section", "energy_eV": [0.0, 1000.0], "sigma_m2": [1e-19, 1e-19]}}
    g = gas_db(_write_gas(tmp_path, rates))
    assert g.K_el(3.0) == pytest.approx(1e-19 * v_mean(3.0), rel=1e-2)
    assert g.K_iz(3.0) == pytest.approx(maxwellian_rate(e, s, 3.0), rel=1e-2)
    assert g.K_iz(1.0) < g.K_iz(3.0) < g.K_iz(10.0)


def test_electronegative_rejected_and_unknown(tmp_path):
    path = _write_gas(tmp_path, {}, electronegative=True)
    with pytest.raises(ValueError):
        gas_db(path)
    with pytest.raises(KeyError):
        gas_db("unobtainium")


_LXCAT = """LXCat, www.lxcat.net
DATABASE:         Synthetic test set
xxxxxxxx
{blocks}
"""


def _block(kind, param, e, s, species="Tg"):
    head = f"{kind}\n{species}\n" + (f" {param}\n" if param is not None else "")
    rows = "\n".join(f" {a:.6e}\t{b:.6e}" for a, b in zip(e, s))
    return f"{head}PROCESS: E + {species}, {kind}\nCOLUMNS: Energy (eV) | Cross section (m2)\n-----------------------------\n{rows}\n-----------------------------\n"


def _lxcat_gas(tmp_path, blocks, **extra):
    (tmp_path / "set.txt").write_text(_LXCAT.format(blocks="\n".join(blocks)), encoding="utf-8")
    d = {"name": "tg", "sigma_i_m2": 1e-18, "lxcat": {"file": "set.txt", "species": "Tg"}, **extra}
    path = tmp_path / "tg.gas.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    return str(path)


def test_lxcat_multi_level_excitation(tmp_path):
    from cirplas.core.materials import parse_lxcat

    e = np.array([0.0, 1000.0])
    blocks = [_block("ELASTIC", 4.2e-6, e, [1e-19, 1e-19]),
              _block("EXCITATION", 8.0, [8.0, 8.0001, 1000.0], [0.0, 1e-20, 1e-20]),
              _block("EXCITATION", 10.0, [10.0, 10.0001, 1000.0], [0.0, 2e-20, 2e-20]),
              _block("IONIZATION", 12.0, [12.0, 12.0001, 1000.0], [0.0, 3e-20, 3e-20])]
    path = _lxcat_gas(tmp_path, blocks)
    procs = parse_lxcat(tmp_path / "set.txt")
    assert [p.kind for p in procs] == ["ELASTIC", "EXCITATION", "EXCITATION", "IONIZATION"] and procs[0].database == "Synthetic test set"
    g = gas_db(path)
    assert g.E_iz == 12.0 and [lvl for _, lvl in g.excitations] == [8.0, 10.0]
    assert g.mass_amu == pytest.approx(M_E / 4.2e-6 / 1.66053906660e-27, rel=1e-3)  # from m/M when mass_amu is absent
    te = 3.0
    step = lambda s0, e0: s0 * v_mean(te) * (1 + e0 / te) * math.exp(-e0 / te)  # noqa: E731
    expect = (12.0 * step(3e-20, 12.0) + 8.0 * step(1e-20, 8.0) + 10.0 * step(2e-20, 10.0)
              + 1e-19 * v_mean(te) * 3 * (M_E / g.mass_kg) * te) / step(3e-20, 12.0)
    assert g.eps_c(te) == pytest.approx(expect, rel=1e-2)


def test_lxcat_effective_and_attachment(tmp_path):
    e = np.array([0.0, 12.0, 12.0001, 1000.0])
    eff = _block("EFFECTIVE", 4.2e-6, e, [1e-19, 1e-19, 1.3e-19, 1.3e-19])  # elastic 1e-19 + ionization 3e-20 above 12 eV
    iz = _block("IONIZATION", 12.0, [12.0, 12.0001, 1000.0], [0.0, 3e-20, 3e-20])
    g = gas_db(_lxcat_gas(tmp_path, [eff, iz], mass_amu=40.0))
    assert g.K_el(2.0) == pytest.approx(1e-19 * v_mean(2.0), rel=2e-2)  # inelastic part removed from the effective set
    att = _block("ATTACHMENT", None, [0.0, 10.0], [1e-21, 1e-21])
    sub = tmp_path / "neg"
    sub.mkdir()
    with pytest.raises(ValueError, match="attachment"):
        gas_db(_lxcat_gas(sub, [eff, iz, att], mass_amu=40.0))
