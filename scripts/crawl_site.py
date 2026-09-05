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
from adsense_checks.http import DEFAULT_TIMEOUT, MAX_WAIT_SECONDS  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402

__doc__ = """Crawl a site and check reachability, redirects and URL stability.

Serves ADS-CRAWL-01, ADS-CRAWL-04 and ADS-CRAWL-05.

    python scripts/crawl_site.py https://example.com [--depth 2] [--max-pages 50] [-v]
"""

def _dump_pages(result) -> None:
    """Per-page evidence, under -v.

    The crawler collects a title, a meta description, an H1 count, a visible
    word count and the links it chose not to follow, and until this function
    existed nothing read any of it: the three checks above look only at status
    codes, links and canonicals, and the `--output crawl.json` that used to
    carry the rest was removed. Every page was parsed for a report nobody could
    see. This is the audit's evidence — a thin page, a missing title, a duplicate
    H1 are all things the requirement list asks a human to judge — so it is
    printed rather than the parsing being deleted.
    """
    print()
    # Written by `crawl()` and read by nothing until now. Without the robots note
    # a crawl that ran under an unreadable robots.txt prints `blocked_by_robots:
    # 0` and reads exactly like a crawl of a site that permits everything.
    origin = result.base_url or "no response"
    print(f"Site identity: {result.base_host or '(unknown)'} (from {origin})")
    if not result.base_url:
        # Nothing was fetched at all, so robots.txt was never even located. The
        # `or` fallback used to fire here and print "read, 0 group(s)", which is
        # indistinguishable from a real 200 carrying no rules.
        note = "never requested: the first fetch did not complete"
    else:
        note = result.robots_note or f"read, {len(result.robots.groups)} group(s)"
    print(f"robots.txt: {note}")
    if result.sitemaps:
        declared = ", ".join(result.sitemaps)
        print(f"Sitemaps declared in robots.txt ({len(result.sitemaps)}): {declared}")
    print(f"Pages ({len(result.pages)}):")
    for page in result.pages:
        print(f"  [{page.status_code}] d{page.depth} {page.final_url}")
        if page.error or page.parse_error:
            print(f"        ! {page.error or page.parse_error}")
            continue
        if page.elapsed_ms is not None or page.redirect_chain:
            hops = f", {len(page.redirect_chain)} redirect hop(s)" if page.redirect_chain else ""
            print(f"        {page.content_type or '(no content-type)'}"
                  f" in {page.elapsed_ms or 0:.0f}ms{hops}")
        if page.auth_challenged:
            print("        ! authentication challenged: not publicly readable")
        if not page.is_html:
            # The title/h1/description block below describes a document that was
            # never parsed; printing it made a JSON body read like an HTML page
            # missing all its tags.
            print("        not HTML: no page metadata was extracted")
            continue
        print(f"        title: {page.title or '(none)'}")
        extra = f" (+{page.h1_count - 1} more)" if page.h1_count > 1 else ""
        print(f"        h1: {page.h1 or '(none)'}{extra}")
        print(f"        description: {page.meta_description or '(none)'}")
        print(
            f"        {page.word_count} visible words in {page.html_chars} chars of markup"
            f"; {len(page.links)} links, {len(page.nofollow_links)} nofollow"
        )
        if page.canonical:
            print(f"        canonical: {page.canonical}")

    for label, urls in (
        ("Off-site links", result.off_site),
        ("Assets skipped", result.skipped_assets),
        ("Blocked by robots.txt", result.blocked_by_robots),
    ):
        if urls:
            tail = " ..." if len(urls) > 5 else ""
            print(f"{label} ({len(urls)}): " + ", ".join(urls[:5]) + tail)
    non_http = sorted({link for page in result.pages for link in page.non_http_links})
    if non_http:
        # mailto:/tel: are what ADS-AUTHOR-02 asks about, which is why the
        # crawler keeps them instead of discarding non-http hrefs.
        tail = " ..." if len(non_http) > 5 else ""
        print(f"Non-http links ({len(non_http)}): " + ", ".join(non_http[:5]) + tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--verify-stateless",
        action="store_true",
        help="re-request redirecting pages without cookies (one extra request each)",
    )
    parser.add_argument(
        "--verify-canonical",
        action="store_true",
        help="re-request pages twice from fresh sessions to compare their canonical."
        " ADS-CRAWL-05 stays MISSING until this runs, so it is the only way to reach"
        " exit 0 on a site that declares canonicals",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.depth < 0:
        # 0 is a real request — `_queue_links` stops at `page.depth >=
        # max_depth`, so depth 0 crawls the seed and follows nothing — and that
        # is why the floor is 0 and not 1. A negative hits the same guard and
        # behaves exactly like 0, so `--depth -1` was silently reinterpreted
        # rather than refused.
        parser.error("--depth must be 0 or more")
    if args.max_pages < 1:
        # The seed is fetched before the budget is consulted, so `--max-pages 0`
        # reported "1 pages fetched ... stopped at max_pages=0" — a budget of
        # zero the crawl cannot honour, printed as though it had. One page is the
        # smallest crawl that exists.
        parser.error("--max-pages must be at least 1")
    if not 0 <= args.delay < MAX_WAIT_SECONDS:
        # This is the argument that reaches outside, so it is the one where a
        # bad value costs somebody else. Below zero `crawl` skips the sleep
        # altogether — its guard is `delay > 0` — so `--delay -1`, a plausible
        # slip for `--delay 1`, crawled flat out against a stranger's host while
        # appearing to ask for the opposite. `--delay nan` does the same thing
        # for the same reason, and reads even more like a request for courtesy.
        # At the other end `--delay inf` does not fail early either: `crawl`
        # fetches the seed and robots.txt before the first sleep, so the
        # OverflowError out of `time.sleep` landed after two requests had
        # already gone out. All three fall out of one chained comparison —
        # `nan` satisfies neither side of it. 0 stays valid: it is what the test
        # suite passes to skip the courtesy wait. The two int arguments above
        # need no such care, because `type=int` refuses "nan" and "inf" before
        # the value gets here.
        parser.error(f"--delay must be 0 or more and less than {MAX_WAIT_SECONDS}")
    if not 0 < args.timeout < MAX_WAIT_SECONDS:
        # Both ends crashed the run with a bare traceback out of the socket
        # layer. `--timeout 0` came back as urllib3's ValueError; `--timeout
        # inf` — the plausible spelling of "no timeout", and what `Infinity` and
        # `1e400` also become under `type=float` — came back as OverflowError
        # from `socket.settimeout`. `fetch` forwards both on purpose (see
        # adsense_checks/http.py) because they report a caller's bug and not an
        # unreachable site, so the CLI is the layer that has to refuse them.
        # Neither bound is rounded off: the floor is exclusive zero because a
        # fraction of a second is a legitimate ask against a fast host, and the
        # ceiling is the exact value the socket stops accepting. One chained
        # comparison covers `--timeout nan` as well, which satisfies no
        # comparison at all and so fails this one.
        parser.error(f"--timeout must be greater than 0 and less than {MAX_WAIT_SECONDS}")

    result = crawl(
        args.url,
        max_depth=args.depth,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
    )
    checks = [
        check_pages_reachable(result),
        check_redirect_chain(
            result, verify_stateless=args.verify_stateless, timeout=args.timeout
        ),
        check_session_urls(
            result, verify_two_sessions=args.verify_canonical, timeout=args.timeout
        ),
    ]
    # Readable names; the ID goes in the requirement column and is not repeated.
    labels = {
        "ADS-CRAWL-01": "pages reachable",
        "ADS-CRAWL-04": "redirect chains",
        "ADS-CRAWL-05": "stable URLs",
    }
    lines = []
    for check in checks:
        findings = list(check.findings)
        if not findings and not args.verbose:
            # A PASS with no evidence leaves the reader unable to tell "observed
            # and correct" from "never ran", which is what the old report hid.
            # Under -v the renderer prints the same details itself, so
            # synthesizing them here only said everything twice.
            findings = [
                ", ".join(f"{k}={v}" for k, v in sorted(check.details.items()))
                or "checked, nothing to report"
            ]
        lines.append(
            Line(
                labels.get(check.requirement, check.requirement),
                check.status,
                findings,
                dict(check.details),
                check.requirement,
            )
        )
    crawl_evidence = [
        f"{len(result.pages)} pages fetched, {len(result.html_pages)} readable HTML"
    ]
    if result.blocked_by_robots:
        # Lived only in a details dict that renders when a check has NO findings,
        # so one unrelated 404 silenced it: half a site's internal links could be
        # skipped and the default report said "robots" zero times.
        blocked = ", ".join(result.blocked_by_robots[:3])
        tail = " ..." if len(result.blocked_by_robots) > 3 else ""
        crawl_evidence.append(
            f"{len(result.blocked_by_robots)} URL(s) not fetched, disallowed by "
            f"robots.txt: {blocked}{tail}"
        )
    if result.stopped_reason:
        crawl_evidence.append(result.stopped_reason)
    lines.insert(0, Line("crawl", result.status, crawl_evidence))

    text, overall = render(f"Crawl — {args.url}", lines, verbose=args.verbose)
    print(text)
    if args.verbose:
        _dump_pages(result)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
