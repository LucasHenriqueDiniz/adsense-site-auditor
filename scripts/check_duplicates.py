#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.duplicates import check_urls  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402

__doc__ = """Find near-duplicate pages within one site.

Serves ADS-CONTENT-02 in part.

This compares the URLs you give it against each other. It does NOT search the
web: ADS-CONTENT-OVERLAP asks for comparison against the top search results, and
fetching those is outside what this script does. Saying so is the point — the
previous version was cited in the reference as if it could.

    python scripts/check_duplicates.py URL [URL ...] [--threshold 0.6]
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    result = check_urls(args.urls, threshold=args.threshold)
    findings = list(result.reasons)
    for group in result.groups:
        findings.append(f"{len(group.urls)} pages at similarity >= {args.threshold:.2f}:")
        findings.extend(f"    {u}" for u in group.urls)

    lines = [
        Line(
            f"{len(args.urls)} URLs",
            result.status,
            findings or ["no near-duplicate groups"],
            {
                "analyzed": len(result.analyzed),
                "unanalyzable": len(result.unanalyzable),
                "groups": len(result.groups),
            },
            "ADS-CONTENT-02",
        )
    ]
    text, overall = render("Duplicate content", lines, verbose=args.verbose)
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
