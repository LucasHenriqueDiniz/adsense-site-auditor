#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.duplicates import check_urls  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.status import Status  # noqa: E402

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
    if not 0 < args.threshold <= 1:
        # The grouping test is `jaccard(...) >= threshold`, and jaccard is in
        # [0, 1] by construction. Outside that range the comparison stops being a
        # measurement and becomes a constant: `--threshold 5` can never be met,
        # so the report printed "no near-duplicate groups" over any input at all,
        # and `--threshold 0` or below is met by every pair, so two pages sharing
        # no words were reported as "2 pages at similarity >= 0.00" and failed
        # the run. Both ends fabricate a verdict, which is why 0 is excluded
        # while 1 — "group only byte-identical extractions" — is kept.
        parser.error("--threshold must be greater than 0 and at most 1")

    result = check_urls(args.urls, threshold=args.threshold)
    findings = list(result.reasons)
    for group in result.groups:
        # The threshold as typed, not a rounding of it. `:.2f` printed every
        # legal threshold below 0.005 as "similarity >= 0.00" — the exact string
        # the guard above cites as the symptom of a threshold that grouped
        # everything — so the evidence line for a real verdict could not be told
        # apart from the fabricated one. There is no arithmetic between argparse
        # and here: the value is `float(what the operator typed)`, and the
        # default `str` of a float is the shortest text that reads back as the
        # same float, so it cannot print a threshold other than the one applied.
        findings.append(f"{len(group.urls)} pages at similarity >= {args.threshold}:")
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
            # "in part", and the line says so: ADS-CONTENT-02 is `judgement` in
            # the reference and this measures one half of it — overlap between
            # the URLs you named. A clean run here is evidence, not a verdict.
            "ADS-CONTENT-02 (part)",
        )
    ]
    lines.append(
        Line(
            "compared against the web",
            Status.MISSING,
            [
                "not measured: ADS-CONTENT-OVERLAP asks for similarity against the top 5"
                " search results and this script performs no search"
            ],
            requirement="ADS-CONTENT-OVERLAP",
        )
    )
    text, overall = render("Duplicate content", lines, verbose=args.verbose)
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
