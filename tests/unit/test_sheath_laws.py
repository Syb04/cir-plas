"""Sheath laws: each Q(V) model reproduces its Child current at the Bohm flux (spec 01 section 3.2)."""

import math

import pytest

from cirplas.core.constants import E_CHARGE, EPS0
from cirplas.core.plasma import bohm_speed
from cirplas.core.project import SheathSpec
from cirplas.core.sheath_models import make_sheath_params

M_AR = 39.948 * 1.66053907e-27


@pytest.mark.parametrize("te,ns", [(3.0, 1e16), (2.0, 3e17)])
def test_child_law_width(te, ns):
    sp = make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="child"))
    for v in (30 * te, 100 * te):
        s = float(sp.width(v))
        j_child = 4.0 / 9.0 * EPS0 * math.sqrt(2 * E_CHARGE / M_AR) * (v + sp.vmin) ** 1.5 / s**2
        assert abs(j_child / (E_CHARGE * ns * bohm_speed(te, M_AR)) - 1) < 1e-3  # softplus guard near V = 0


@pytest.mark.parametrize("lambda_i", [1e-4, 1e-3])
def test_collisional_child_law_width(lambda_i):
    te, ns = 3.0, 1e16
    sp = make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="collisional"), lambda_i=lambda_i)
    assert sp.beta == 0.6
    for v in (30 * te, 100 * te):
        s = float(sp.width(v))
        j_coll = 1.68 * EPS0 * math.sqrt(2 * E_CHARGE * lambda_i / (math.pi * M_AR)) * (v + sp.vmin) ** 1.5 / s**2.5
        assert abs(j_coll / (E_CHARGE * ns * bohm_speed(te, M_AR)) - 1) < 1e-3  # softplus guard near V = 0
    # shorter mean free path -> thinner sheath at the same voltage (s ~ lambda_i^(1/5))
    thin = make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="collisional"), lambda_i=lambda_i / 32)
    assert abs(thin.s1 / sp.s1 - 0.5) < 1e-9


def test_auto_selects_by_mean_free_path():
    te, ns = 3.0, 1e16
    assert make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="auto"), lambda_i=1e-1).model == "child"
    assert make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="auto"), lambda_i=1e-5).model == "collisional"
    with pytest.raises(ValueError):
        make_sheath_params(1.0, ns, te, M_AR, SheathSpec(model="collisional"))
