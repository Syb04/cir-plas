"""PyInstaller entry point for the GUI."""
import sys

from cirplas.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
