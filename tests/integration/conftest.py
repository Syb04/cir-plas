import pytest


@pytest.fixture(scope="session")
def ngspice():
    from cirplas.core.ngspice.shared import NgspiceError, NgspiceShared

    try:
        return NgspiceShared.get()
    except NgspiceError as exc:  # pragma: no cover
        pytest.skip(f"ngspice shared library not available: {exc}")
