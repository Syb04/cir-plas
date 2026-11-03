"""cirplas-report: write a self-contained HTML report of a result file.

usage: cirplas-report run.h5 [-o report.html] [--title NAME]   (also: cirplas-solve report ...)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.report import write_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cirplas-report", description="CirPlas HTML report")
    ap.add_argument("result", help="HDF5 result file")
    ap.add_argument("-o", "--output", default=None, help="output HTML (default: <result>.html)")
    ap.add_argument("--title", default=None)
    args = ap.parse_args(argv)
    out = Path(args.output) if args.output else Path(args.result).with_suffix(".html")
    try:
        write_report(args.result, out, args.title)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
