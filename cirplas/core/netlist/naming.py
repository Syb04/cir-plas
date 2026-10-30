"""Node / element naming conventions (spec 02 section 2.1). All names are lowercase-safe."""

from __future__ import annotations

GND = "0"


def conductor_node(conductor: str) -> str:
    return {"W": "W", "F": "F", "G": GND}[conductor]


def sb(i: int) -> str:  # bottom surface node (plasma side of the bottom stack)
    return f"Sb_{i}"


def s_layer(k: int, i: int) -> str:  # node above bottom layer k (1-based), k < m
    return f"S{k}_{i}"


def bulk(i: int, j: int) -> str:  # bulk node j = 1..Nz+1
    return f"B_{i}_{j}"


def bulk_mid(i: int, j: int) -> str:  # mid node of bulk layer j = 1..Nz
    return f"Bc_{i}_{j}"


def st(i: int) -> str:  # top surface node (plasma side of the top stack)
    return f"St_{i}"


def u_layer(k: int, i: int) -> str:  # node above top layer k (1-based, bottom-up), k < m
    return f"U{k}_{i}"


def top(i: int) -> str:
    return f"T_{i}"


def chain(i: int, k: int) -> str:  # flux chain node, boundary i, level k (k>=1); k=0 -> GND
    return GND if k == 0 else f"A_{i}_{k}"


def vec_node(name: str) -> str:
    """ngspice vector name of a node voltage."""
    return f"v({name.lower()})"


def vec_branch(element: str) -> str:
    """ngspice vector name of a branch current (voltage sources, inductors)."""
    return f"{element.lower()}#branch"


def vec_subckt_branch(instance: str, element: str) -> str:
    """Branch current of an element inside a subcircuit instance (e.g. v.xshb_12.vsc#branch)."""
    letter = element[0].lower()
    return f"{letter}.{instance.lower()}.{element.lower()}#branch"
