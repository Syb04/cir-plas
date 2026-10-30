"""PyInstaller entry point for the solver worker."""
import sys

from cirplas.worker.cli import main

if __name__ == "__main__":
    sys.exit(main())
