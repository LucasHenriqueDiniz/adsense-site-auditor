#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.crawl import (  # noqa: E402
    check_pages_reachable,
    check_redirect_chain,
    check_session_urls,
    crawl,
)
from adsense_checks.report import Line, exit_code, render  # noqa: E402

__doc__ = """Crawl a site and check reachability, redirects and URL stability.

Serves ADS-CRAWL-01, ADS-CRAWL-04 and ADS-CRAWL-05.

    python scripts/crawl_site.py https://example.com [--depth 2] [--max-pages 50] [-v]
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument(
        "--verify-stateless",
        action="store_true",
        help="re-request redirecting pages without cookies (one extra request each)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    result = crawl(args.url, max_depth=args.depth, max_pages=args.max_pages, delay=args.delay)
    checks = [
        check_pages_reachable(result),
        check_redirect_chain(result, verify_stateless=args.verify_stateless),
        check_session_urls(result),
    ]
    # Nomes legíveis; o ID vai na coluna do requisito e não se repete.
    rotulos = {
        "ADS-CRAWL-01": "pages reachable",
        "ADS-CRAWL-04": "redirect chains",
        "ADS-CRAWL-05": "stable URLs",
    }
    lines = []
    for c in checks:
        achados = list(c.findings)
        if not achados:
            # Um PASS sem evidência não deixa distinguir "observado e correto" de
            # "nunca rodou", que é o que o relatório antigo escondia.
            achados = [
                ", ".join(f"{k}={v}" for k, v in sorted(c.details.items()))
                or "checked, nothing to report"
            ]
        lines.append(
            Line(
                rotulos.get(c.requirement, c.requirement),
                c.status,
                achados,
                dict(c.details),
                c.requirement,
            )
        )
    lines.insert(
        0,
        Line(
            "crawl",
            result.status,
            [f"{len(result.pages)} pages fetched, {len(result.html_pages)} readable HTML"]
            + ([result.stopped_reason] if result.stopped_reason else []),
        ),
    )

    text, overall = render(f"Crawl — {args.url}", lines, verbose=args.verbose)
    print(text)
    if args.verbose:
        print()
        for page in result.pages:
            print(f"  {page.status_code} {page.final_url}")
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
