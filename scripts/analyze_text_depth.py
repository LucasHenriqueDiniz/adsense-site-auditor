#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.text import DEFAULT_MIN_WORDS, measure_url  # noqa: E402

__doc__ = """Measure content depth of one or more pages.

Serves ADS-CONTENT-03 and ADS-COMPLETE-02. A page that could not be read reports
ERROR — never a word count, and never a pass.

    python scripts/analyze_text_depth.py URL [URL ...] [--min-words 300]
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--min-words", type=int, default=DEFAULT_MIN_WORDS)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

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
