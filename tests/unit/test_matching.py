import cmath
import math

import pytest

from cirplas.core.analysis.matching import design_l_match


@pytest.mark.parametrize("z_load", [complex(0.9, -8.1), complex(5.0, -20.0), complex(120.0, -30.0), complex(300.0, 10.0)])
def test_l_match_transforms_to_generator_impedance(z_load):
    f, r_gen = 60e6, 50.0
    m = design_l_match(z_load, f, r_gen)
    w = 2 * math.pi * f
    if m.topology == "shunt_c_generator":
        z1 = z_load + 1j * w * m.l
        z_in = 1.0 / (1.0 / z1 + 1j * w * m.c)
    else:
        y = 1.0 / z_load + 1j * w * m.c
        z_in = 1.0 / y + 1j * w * m.l
    assert abs(z_in - r_gen) < 1e-6 * r_gen
    assert m.l > 0 and m.c > 0
