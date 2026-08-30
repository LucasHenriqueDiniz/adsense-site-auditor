#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.completeness import check_completeness  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.status import Status  # noqa: E402

__doc__ = """The pre-flight gate: unfinished site, trust pages, broken navigation.

Serves ADS-COMPLETE-01, ADS-UX-05 and ADS-AUTHOR-02 in part.

    python scripts/check_completeness.py https://example.com [-v]
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--nav-limit", type=int, default=25)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    report = check_completeness(args.url, nav_link_limit=args.nav_limit)
    lines: list[Line] = []

    # Placeholders on the home page.
    home = [p.phrase for p in report.home_placeholders]
    lines.append(
        Line(
            "home page is finished",
            report.home_placeholders[0].status if report.home_placeholders else Status.OK,
            [f"unfinished marker: {phrase!r}" for phrase in home]
            or ["no unfinished markers found"],
            requirement="ADS-COMPLETE-01",
        )
    )

    # Trust pages, one line each, always carrying the evidence for the verdict.
    # A Pass with nothing shown is what the reference forbids: the reader cannot
    # tell an observed pass from a check that never ran.
    for kind in ("about", "contact"):
        outcome = report.trust.pages.get(kind)
        if outcome is None:
            lines.append(
                Line(f"{kind} page", Status.MISSING, ["not found"], requirement="ADS-UX-05")
            )
            continue
        evidence = [f"{outcome.url} ({outcome.words} words)"] if outcome.url else []
        if outcome.reason:
            evidence.append(outcome.reason)
        channels = outcome.channels
        found = (
            list(channels.emails)
            + list(channels.socials)
            + [f"{len(channels.forms)} form(s)" for _ in (1,) if channels.forms]
        )
        if found:
            evidence.append("contact: " + ", ".join(found))
        lines.append(Line(f"{kind} page", outcome.status, evidence, requirement="ADS-UX-05"))

    # Navigation.
    nav = report.nav
    if nav is None:
        lines.append(Line("navigation", Status.MISSING, ["not checked"], requirement="ADS-UX-01"))
    else:
        broken = [f"{link.url} -> HTTP {link.status_code}" for link in nav.broken]
        lines.append(
            Line(
                "navigation",
                nav.status,
                broken or [f"{nav.checked} links followed, none broken"],
                {"checked": nav.checked, "broken": len(nav.broken)},
                "ADS-UX-01",
            )
        )

    for finding in report.findings:
        lines.append(
            Line(
                str(getattr(finding, "message", finding)),
                getattr(finding, "status", Status.WARNING),
            )
        )

    text, overall = render(f"Completeness — {args.url}", lines, verbose=args.verbose)
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
