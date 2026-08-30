#!/usr/bin/env python3
"""robots.txt, sitemap, HTTPS and reachability for one site.

Serves ADS-CRAWL-01, ADS-CRAWL-02, ADS-CRAWL-06 and ADS-CRAWL-07.

Thin wrapper: every decision lives in adsense_checks/, where it is unit-tested.
Exits non-zero whenever a check did not observe its condition — a site this
script could not read must not be reported as a site that passed.

    python scripts/check_technical.py https://example.com [-v]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.http import fetch  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.robots import (  # noqa: E402
    ADSBOT_CRAWLER,
    ADSENSE_CRAWLER,
    INDEX_CRAWLER,
    Robots,
    blocks_everything,
    parse_robots,
)
from adsense_checks.sitemap import check_sitemap  # noqa: E402
from adsense_checks.status import Status, escalate  # noqa: E402


def _robots(base: str, timeout: int) -> tuple[Line, Robots]:
    response = fetch(base.rstrip("/") + "/robots.txt", timeout=timeout)
    findings: list[str] = []

    if response.error is not None:
        return Line(
            "robots.txt", Status.ERROR, [f"could not be fetched: {response.error}"]
        ), Robots(missing=True)
    if response.status_code == 404:
        # Google treats an absent robots.txt as "crawl everything", so this is
        # not a failure. It is still worth reporting.
        return Line("robots.txt", Status.INFO, ["absent: everything is crawlable"]), Robots(
            missing=True
        )
    if response.status_code and response.status_code >= 500:
        # A 5xx here is worse than a 404: Google stops crawling the whole site
        # rather than assuming permission.
        return (
            Line(
                "robots.txt",
                Status.FAIL,
                [f"HTTP {response.status_code}: crawling stops site-wide"],
            ),
            Robots(missing=True),
        )

    robots = parse_robots(response.text)
    status = Status.OK
    for crawler, label in (
        (ADSENSE_CRAWLER, "the AdSense crawler"),
        (INDEX_CRAWLER, "Googlebot"),
        (ADSBOT_CRAWLER, "AdsBot"),
    ):
        if blocks_everything(robots, crawler):
            status = escalate(status, Status.FAIL)
            findings.append(f"{crawler} is disallowed at /: {label} cannot read this site")
    if not findings:
        findings.append("Mediapartners-Google, Googlebot and AdsBot are all allowed at /")
    return Line(
        "robots.txt", status, findings, {"sitemaps": robots.sitemaps}, "ADS-CRAWL-02"
    ), robots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    home = fetch(args.url, timeout=args.timeout)
    lines: list[Line] = []

    if home.error is not None:
        lines.append(Line("reachable", Status.ERROR, [home.error], requirement="ADS-CRAWL-01"))
    else:
        lines.append(
            Line(
                "reachable",
                home.status,
                [f"HTTP {home.status_code} in {home.elapsed_ms:.0f}ms"],
                {"final_url": home.final_url, "redirects": len(home.redirect_chain)},
                "ADS-CRAWL-01",
            )
        )
        # The scheme of the response, not of the string the caller typed.
        https = Status.OK if home.is_https else Status.WARNING
        note = "served over HTTPS" if home.is_https else f"final URL is not HTTPS: {home.final_url}"
        if home.downgraded_to_http:
            https, note = Status.FAIL, f"redirect chain ends on http: {home.final_url}"
        lines.append(Line("https", https, [note], requirement="ADS-CRAWL-06"))

    robots_line, robots = _robots(args.url, args.timeout)
    lines.append(robots_line)

    sitemap = check_sitemap(args.url, robots=robots)
    lines.append(
        Line(
            "sitemap",
            sitemap.status,
            list(sitemap.reasons),
            {
                "sitemap_url": sitemap.sitemap_url,
                "discovered_via": sitemap.discovered_via,
                "kind": sitemap.kind,
                "url_count": sitemap.url_count,
                "child_sitemaps": sitemap.child_sitemap_count,
            },
            "ADS-CRAWL-07",
        )
    )

    text, overall = render(f"Technical checks — {args.url}", lines, verbose=args.verbose)
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
