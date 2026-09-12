#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.text import DEFAULT_MIN_WORDS, measure_url  # noqa: E402

__doc__ = """Measure content depth of one or more pages.

Serves ADS-CONTENT-03. A page that could not be read reports ERROR — never a
word count, and never a pass.

It does NOT decide ADS-COMPLETE-02, which the docstring used to claim. That
requirement asks for at least three published guides of 1200+ words; this script
measures one page at a time against `--min-words` (300 by default, a review
threshold and not a policy line) and counts no articles. Feed it the guides with
`--min-words 1200` and the count is still yours to make.

    python scripts/analyze_text_depth.py URL [URL ...] [--min-words 300]
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--min-words", type=int, default=DEFAULT_MIN_WORDS)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.min_words < 1:
        # `--min-words 0` printed PASS over a ten-word page.
        parser.error("--min-words must be at least 1")

    lines = []
    for url in args.urls:
        depth = measure_url(url, min_words=args.min_words)
        lines.append(
            Line(
                depth.url,
                depth.status,
                [depth.reason] if depth.reason else [f"{depth.words} words in main content"],
                {
                    "words": depth.words,
                    "total_words": depth.total_words,
                    "main_ratio": round(depth.main_ratio, 3),
                },
                "ADS-CONTENT-03",
            )
        )

    text, overall = render(
        f"Content depth (min {args.min_words} words)", lines, verbose=args.verbose
    )
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
