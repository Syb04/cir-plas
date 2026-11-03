"""Material and gas databases (bundled JSON under cirplas/data)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Union

import numpy as np

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
    """K(Te) [m^3/s] for Maxwellian electrons, Te in eV.

    Types: ``arrhenius_power`` (A Te^b exp(-E/Te)), ``ll_argon_elastic`` (L&L fit), ``table`` (tabulated K(Te),
    log-log interpolation, constant-slope extrapolation) and ``cross_section`` (converted to a table at load time by
    Maxwellian integration, see `maxwellian_rate`)."""

    type: str
    A: float = 0.0
    b: float = 0.0
    E: float = 0.0
    c2: float = 0.0
    c3: float = 0.0
    te: tuple = ()  # table: Te grid [eV]
    k: tuple = ()  # table: K values [m^3/s]

    def __call__(self, te: float) -> float:
        if te <= 0:
            raise ValueError("Te must be positive")
        if self.type == "arrhenius_power":
            return self.A * te**self.b * math.exp(-self.E / te)
        if self.type == "ll_argon_elastic":
            lt = math.log(te)
            return self.A * te**self.b * math.exp(self.c2 * lt**2 + self.c3 * lt**3)
        if self.type == "table":
            x = np.log(np.asarray(self.te, dtype=float))
            y = np.log(np.maximum(np.asarray(self.k, dtype=float), 1e-300))
            lt = math.log(te)
            if lt <= x[0]:
                j = 0
            elif lt >= x[-1]:
                j = len(x) - 2
            else:
                return float(math.exp(np.interp(lt, x, y)))
            slope = (y[j + 1] - y[j]) / (x[j + 1] - x[j])
            return float(math.exp(y[j] + slope * (lt - x[j])))
        raise ValueError(f"unknown rate coefficient type '{self.type}'")


def maxwellian_rate(energy_ev, sigma_m2, te_ev: float) -> float:
    """K(Te) = <sigma v> for a Maxwellian EEDF: (2/sqrt(pi)) Te^(-3/2) sqrt(2e/m_e) Int sigma(E) E exp(-E/Te) dE [m^3/s].

    The cross section is interpolated linearly in the table and taken as zero outside it (tables should start at
    the threshold, or at 0 eV for elastic processes, and extend to well above 10 Te)."""
    from .constants import E_CHARGE, M_E

    e_tab = np.asarray(energy_ev, dtype=float)
    s_tab = np.asarray(sigma_m2, dtype=float)
    e_hi = min(float(e_tab[-1]), 60.0 * te_ev)
    e_lo = max(float(e_tab[0]), 1e-4)
    if e_hi <= e_lo:
        return 0.0
    e = np.unique(np.concatenate([np.geomspace(e_lo, e_hi, 4000), e_tab[(e_tab >= e_lo) & (e_tab <= e_hi)]]))
    s = np.interp(e, e_tab, s_tab, left=0.0, right=0.0)
    integrand = s * e * np.exp(-e / te_ev)
    integral = float(np.sum(0.5 * (integrand[1:] + integrand[:-1]) * np.diff(e)))
    return 2.0 / math.sqrt(math.pi) * te_ev**-1.5 * math.sqrt(2.0 * E_CHARGE / M_E) * integral


TE_TABLE = tuple(float(v) for v in np.geomspace(0.2, 50.0, 120))


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


def bundled_gases() -> list[str]:
    base = resources.files("cirplas").joinpath("data", "gases")
    return sorted(p.name[:-5] for p in base.iterdir() if p.name.endswith(".json"))


def _load_cross_section(d: dict, base_dir: Path | None) -> tuple[np.ndarray, np.ndarray]:
    if "file" in d:
        path = Path(d["file"])
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        data = np.loadtxt(path, delimiter=d.get("delimiter", ","), comments="#")
        e, s = data[:, 0], data[:, 1] * float(d.get("sigma_scale", 1.0))
    else:
        e, s = np.asarray(d["energy_eV"], dtype=float), np.asarray(d["sigma_m2"], dtype=float)
    if np.any(np.diff(e) <= 0):
        raise ValueError("cross-section energies must be strictly increasing")
    return e, s


@lru_cache(maxsize=None)
def gas_db(name: str) -> GasData:
    """Bundled gas (``ar``) or a gas JSON file given by path (same schema; cross-section files are relative to it).

    The plasma model is electropositive (single positive ion, no attachment): data for electronegative gases
    cannot be used as is."""
    base_dir = None
    if name.lower().endswith(".json") or Path(name).suffix:
        path = Path(name)
        if not path.exists():
            raise KeyError(f"gas data file not found: {name}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        base_dir = path.parent
    else:
        try:
            raw = json.loads(_data_text(f"gases/{name.lower()}.json"))
        except FileNotFoundError as exc:
            raise KeyError(f"unknown gas '{name}' (bundled: {', '.join(bundled_gases())}; or give a gas JSON file path)") from exc
    if raw.get("electronegative") or "K_att" in raw.get("rates", {}):
        raise ValueError(f"gas '{raw.get('name', name)}': electronegative chemistry is not supported by the plasma model")
    rates = raw["rates"]

    def rc(key: str) -> RateCoefficient:
        d = dict(rates[key])
        kind = d.pop("type")
        if kind == "cross_section":
            e, s = _load_cross_section(d, base_dir)
            return RateCoefficient(type="table", te=TE_TABLE, k=tuple(maxwellian_rate(e, s, t) for t in TE_TABLE))
        if kind == "table":
            return RateCoefficient(type="table", te=tuple(float(v) for v in d["te"]), k=tuple(float(v) for v in d["k"]))
        return RateCoefficient(type=kind, **d)

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
