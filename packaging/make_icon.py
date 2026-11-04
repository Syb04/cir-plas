"""Build the application icons from packaging/icon_source.png (needs Pillow).

Usage (from the repository root):  python packaging/make_icon.py
Writes packaging/cirplas.ico (exe, installer, file type) and cirplas/data/cirplas_icon.png (window icon at run time).
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

src = Image.open(ROOT / "packaging" / "icon_source.png").convert("RGBA")
src.save(ROOT / "packaging" / "cirplas.ico", sizes=[(s, s) for s in ICO_SIZES])
src.resize((256, 256), Image.LANCZOS).save(ROOT / "cirplas" / "data" / "cirplas_icon.png", optimize=True)
