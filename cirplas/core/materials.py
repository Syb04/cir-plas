"""Material and gas databases (bundled JSON under cirplas/data)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Union

from .constants import AMU
from .project import MaterialProps, MaterialRef


def _data_text(relpath: str) -> str:
    base = resources.files("cirplas").joinpath("data")
    return base.joinpath(relpath).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def material_db() -> dict[str, MaterialProps]:
    raw = json.loads(_data_text("materials.json"))
    return {k: MaterialProps(**v) for k, v in raw.items() if not k.startswith("_")}


def resolve_material(ref: MaterialRef) -> MaterialProps:
    if isinstance(ref, MaterialProps):
        return ref
    db = material_db()
    if ref not in db:
        raise KeyError(f"unknown material '{ref}' (known: {', '.join(sorted(db))})")
    return db[ref]


@dataclass(frozen=True)
class RateCoefficient:
    """K(Te) [m^3/s] for Maxwellian electrons, Te in eV."""

    type: str
    A: float
    b: float
    E: float = 0.0
    c2: float = 0.0
    c3: float = 0.0

    def __call__(self, te: float) -> float:
        if te <= 0:
            raise ValueError("Te must be positive")
        if self.type == "arrhenius_power":
            return self.A * te**self.b * math.exp(-self.E / te)
        if self.type == "ll_argon_elastic":
            lt = math.log(te)
            return self.A * te**self.b * math.exp(self.c2 * lt**2 + self.c3 * lt**3)
        raise ValueError(f"unknown rate coefficient type '{self.type}'")


@dataclass(frozen=True)
class GasData:
    name: str
    mass_amu: float
    sigma_i: float  # ion-neutral cross section [m^2] for lambda_i = 1/(n_g sigma_i)
    E_iz: float  # eV
    E_ex: float  # eV
    K_iz: RateCoefficient
    K_ex: RateCoefficient
    K_el: RateCoefficient

    @property
    def mass_kg(self) -> float:
        return self.mass_amu * AMU

    def eps_c(self, te: float) -> float:
        """Collisional energy loss per electron-ion pair created [eV] (L&L eq. 3.5.8)."""
        kiz = self.K_iz(te)
        from .constants import M_E

        return (kiz * self.E_iz + self.K_ex(te) * self.E_ex + self.K_el(te) * 3.0 * (M_E / self.mass_kg) * te) / kiz

    def nu_m(self, te: float, n_g: float) -> float:
        """Electron momentum-transfer collision frequency [1/s] (elastic rate x gas density)."""
        return n_g * self.K_el(te)


@lru_cache(maxsize=None)
def gas_db(name: str) -> GasData:
    try:
        raw = json.loads(_data_text(f"gases/{name.lower()}.json"))
    except FileNotFoundError as exc:
        raise KeyError(f"unknown gas '{name}'") from exc
    rates = raw["rates"]

    def rc(key: str) -> RateCoefficient:
        d = dict(rates[key])
        return RateCoefficient(type=d.pop("type"), **d)

    return GasData(
        name=raw["name"],
        mass_amu=raw["mass_amu"],
        sigma_i=raw["sigma_i_m2"],
        E_iz=raw["E_iz_eV"],
        E_ex=raw["E_ex_eV"],
        K_iz=rc("K_iz"),
        K_ex=rc("K_ex"),
        K_el=rc("K_el"),
    )


Material = Union[str, MaterialProps]
