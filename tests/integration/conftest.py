import pytest


@pytest.fixture(scope="session")
def ngspice():
    """ngspice shared library, or the batch backend (console executable) when only that is installed."""
    from cirplas.core.ngspice.backend import get_backend
    from cirplas.core.ngspice.shared import NgspiceError
    from cirplas.core.project import NgspiceSpec

    try:
        return get_backend(NgspiceSpec())
    except NgspiceError as exc:  # pragma: no cover
        pytest.skip(f"ngspice not available: {exc}")
