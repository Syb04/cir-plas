"""Physical constants (SI) used throughout CirPlas."""

from __future__ import annotations

import math

E_CHARGE = 1.602176634e-19  # C
M_E = 9.1093837015e-31  # kg
EPS0 = 8.8541878128e-12  # F/m
MU0 = 1.25663706212e-6  # H/m
K_B = 1.380649e-23  # J/K
C_LIGHT = 299792458.0  # m/s
AMU = 1.66053906660e-27  # kg
TORR_TO_PA = 133.322368
MTORR_TO_PA = 0.133322368
TWO_PI = 2.0 * math.pi

# Thermal voltage at the ngspice default temperature (27 degC), used to map the
# electron Boltzmann current onto a SPICE diode (emission coefficient N = Te/VT).
NGSPICE_DEFAULT_TEMP_C = 27.0
VT_27C = K_B * (273.15 + NGSPICE_DEFAULT_TEMP_C) / E_CHARGE  # ~0.025852 V
