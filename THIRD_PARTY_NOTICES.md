# Third-party notices

CirPlas bundles or depends on the following software. Their licenses apply to the respective components.

| Component | License | Notes |
|---|---|---|
| ngspice (shared library `libngspice`) | Modified BSD (3-clause) | Circuit simulation engine. https://ngspice.sourceforge.io/ |
| Qt 6 / PySide6 | LGPL v3 | GUI toolkit, dynamically linked and shipped as separate files so that it can be replaced. https://www.qt.io/ |
| pyqtgraph | MIT | Scientific plotting. |
| NumPy, SciPy | BSD 3-clause | Numerical routines. |
| h5py / HDF5 | BSD 3-clause | Result files. |
| pydantic | MIT | Project schema validation. |
| PyInstaller (build only) | GPL with bootloader exception | Used to build the Windows bundle; not linked into the application. |
| Inno Setup (build only) | Inno Setup License | Installer builder. |
| MSYS2 mingw-w64 runtime DLLs | Various (GCC runtime exception etc.) | Runtime dependencies of `libngspice-0.dll` on Windows. |

The Argon rate coefficients bundled in `cirplas/data/gases/ar.json` follow Lieberman & Lichtenberg,
*Principles of Plasma Discharges and Materials Processing*, 2nd ed., Table 3.3.
