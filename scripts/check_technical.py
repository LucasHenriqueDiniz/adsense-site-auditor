#!/usr/bin/env python3
"""robots.txt, sitemap, availability and reachability for one site.

Serves ADS-CRAWL-01, ADS-CRAWL-02, ADS-CRAWL-06 and ADS-CRAWL-07. ADS-CRAWL-06
names four things and this script observes three of them — DNS, TLS and response
time; see `_availability` for why the fourth is reported as a gap rather than
decided. Security headers are not checked by anything in this repo.

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

from adsense_checks.http import (  # noqa: E402
    DEFAULT_TIMEOUT,
    MAX_WAIT_SECONDS,
    Fetch,
    fetch,
    join_url,
    split_url,
)
from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.robots import (  # noqa: E402
    ADSBOT_CRAWLER,
    ADSENSE_CRAWLER,
    INDEX_CRAWLER,
    Robots,
    blocks_everything,
    parse_robots,
)
from adsense_checks.sitemap import check_sitemap, verify_sample_urls  # noqa: E402
from adsense_checks.status import Status, escalate  # noqa: E402

# A review threshold, not a Google policy line: AdSense publishes no response-time
# limit. Stated here rather than inlined so the reader of a WARNING can see what
# it was measured against. `Fetch.elapsed_ms` covers the whole redirect chain.
SLOW_RESPONSE_MS = 2500

_REQ_ROBOTS = "ADS-CRAWL-02"


def _robots(base: str, timeout: int) -> tuple[Line, Robots]:
    """ADS-CRAWL-02 — read robots.txt and say whether it shuts a crawler out.

    Every return carries the requirement ID. Only the success path used to, so a
    503 or a refused connection printed with an empty requirement column, and
    SKILL.md's completeness gate — which reconciles the IDs a report prints
    against the reference — read a failed ADS-CRAWL-02 as one never checked.

    What arrives is classified before it is parsed, the way crawl._load_robots
    already does it. Anything that is not a 200 carrying a non-HTML body used to
    fall straight into `parse_robots`, and an HTML error page contains no
    `user-agent:` line, so a 403 and a SPA catch-all both came out as "everything
    is allowed" — an assertion about permissions read off a document that was not
    robots.txt, with the HTTP status printed nowhere.
    """
    url = join_url(base, "/robots.txt") or f"{base} (unresolvable)"
    # robots.txt is only ever read from the origin root. Appending to the URL as
    # typed asked a subdirectory install for `/blog/robots.txt`, took the 404 as
    # "absent: everything is crawlable", and reported a site as fully open
    # without having fetched the file that governs it.
    response = fetch(url, timeout=timeout)
    code = response.status_code

    def unreadable(status: Status, note: str) -> tuple[Line, Robots]:
        return Line("robots.txt", status, [note], {"url": url}, _REQ_ROBOTS), Robots(missing=True)

    if response.error is not None:
        return unreadable(Status.ERROR, f"{url} could not be fetched: {response.error}")
    if code == 429 or (code is not None and code >= 500):
        # Google reads 429 and 5xx as a temporary error and stops crawling the
        # whole site rather than assuming permission. Worse than a 404.
        return unreadable(Status.FAIL, f"{url} returned HTTP {code}: crawling stops site-wide")
    if code is not None and code >= 400:
        # Every other 4xx, 404 included: Google crawls as if the file did not
        # exist. Not a failure, but the code is named — "absent" and "we were
        # refused" are different facts and the report used to print neither.
        detail = "absent" if code == 404 else f"unreadable (HTTP {code})"
        return unreadable(Status.INFO, f"{url} {detail}: Google crawls as if unrestricted")
    if "html" in response.headers.get("content-type", "").lower():
        # A catch-all route answering 200 with the app shell. An HTML document
        # holds no `user-agent:` line, so parsing it read as "allow everything"
        # for the wrong reason.
        return unreadable(Status.INFO, f"{url} served as HTML (soft 404): no rules were read")

    robots = parse_robots(response.text)
    findings: list[str] = []
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
        "robots.txt",
        status,
        findings,
        {"url": url, "sitemaps": robots.sitemaps, "groups": len(robots.groups)},
        _REQ_ROBOTS,
    ), robots


def _availability(home: Fetch) -> Line:
    """ADS-CRAWL-06 — DNS, TLS and response time, from the one request made.

    The requirement names four things: DNS, TLS, uptime and server response
    times. Three are decidable from a single response and are decided here.
    Uptime is not: reliability over time needs sampling over time, and an audit
    run makes one request. That quarter is reported as INFO instead of being left
    implicit, because what stood here before was a scheme test carrying the whole
    requirement ID and printing PASS — three quarters of a requirement asserted
    without having been looked at, which is the defect class this package exists
    to remove.

    INFO and not MISSING on purpose: MISSING is not a pass and would make this
    check impossible to ever satisfy, so the script could never exit 0 and would
    stop being usable as a gate. The gap is named in the output either way.
    """
    status = Status.OK
    findings: list[str] = []

    # The scheme of the response, not of the string the caller typed.
    if home.downgraded_to_http:
        status = escalate(status, Status.FAIL)
        findings.append(f"TLS: redirect chain ends on http: {home.final_url}")
    elif not home.is_https:
        status = escalate(status, Status.WARNING)
        findings.append(f"TLS: final URL is not HTTPS: {home.final_url}")
    else:
        findings.append("TLS: served over HTTPS")

    # DNS resolved, or `fetch` would have come back with an error and this
    # function would not have been called. Worth stating: it is one of the four.
    findings.append(f"DNS: host {split_url(home.final_url or home.url).netloc} resolved")

    if home.status_code is not None and home.status_code >= 400:
        # The timing of an error page is not the site's response time. This
        # function runs in the `else` of `home.error is None`, which is as true
        # for a 500 as for a 200, so it was answering three quarters of
        # ADS-CRAWL-06 about a response the same report was failing.
        status = escalate(status, Status.MISSING)
        findings.append(
            f"response time: not measured — the home page answered HTTP"
            f" {home.status_code}, so there is no served page to time"
        )
    elif home.elapsed_ms is None:
        status = escalate(status, Status.ERROR)
        findings.append("response time: not measured")
    elif home.elapsed_ms > SLOW_RESPONSE_MS:
        status = escalate(status, Status.WARNING)
        findings.append(
            f"response time: {home.elapsed_ms:.0f}ms for the whole chain, over the"
            f" {SLOW_RESPONSE_MS}ms review threshold"
        )
    else:
        findings.append(f"response time: {home.elapsed_ms:.0f}ms for the whole chain")

    status = escalate(status, Status.INFO)
    findings.append(
        "uptime: not observed — one request cannot establish reliability over time."
        " This quarter of ADS-CRAWL-06 needs monitoring, not an audit run"
    )
    return Line("availability", status, findings, requirement="ADS-CRAWL-06")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
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

    home = fetch(args.url, timeout=args.timeout)
    lines: list[Line] = []

    if home.error is not None:
        lines.append(Line("reachable", Status.ERROR, [home.error], requirement="ADS-CRAWL-01"))
        # The availability line used to live only in the `else`, so on every
        # error path ADS-CRAWL-06 was not reported as ERROR — it was not reported
        # at all, and a gate reconciling printed IDs against the reference read it
        # as a requirement nobody checked. Exactly the defect `_robots` was fixed
        # for, left standing one function over.
        lines.append(
            Line(
                "availability",
                Status.ERROR,
                [f"nothing answered, so DNS, TLS and response time are unobserved: {home.error}"],
                requirement="ADS-CRAWL-06",
            )
        )
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
        lines.append(_availability(home))

    # One origin for every check below, resolved once: the one that actually
    # answered. robots.txt and the sitemap both live at the root of the origin
    # SERVING the site, and on a host sending apex to www the typed URL names a
    # different one. Deriving the base twice is what let the two halves of a
    # single report ask two different hosts. Same rule as crawl._load_robots;
    # the typed URL is the fallback for when nothing answered at all.
    base = home.final_url or args.url

    robots_line, robots = _robots(base, args.timeout)
    lines.append(robots_line)

    sitemap = check_sitemap(base, robots=robots, timeout=args.timeout)
    # ADS-CRAWL-07 asks that the advertised URLs answer 200. `verify_sample_urls`
    # existed for it and had no caller outside the tests, so the check printed
    # PASS having never fetched one of them — the very thing `_availability`
    # refuses to do for ADS-CRAWL-06, one function over.
    sample = verify_sample_urls(sitemap, timeout=args.timeout)
    # `reasons` is empty when nothing went wrong, and a bare `[PASS] sitemap` line
    # leaves the reader unable to tell "observed and correct" from "never ran" —
    # the guard crawl_site.py and check_completeness.py both install, and the one
    # script that lacked it.
    if sample.checked:
        bad = [f"{u} -> HTTP {c}" for u, st, c in sample.checked if st is not Status.OK]
        sitemap.status = escalate(sitemap.status, sample.status)
        sitemap.note(
            f"{sample.ok_count} of {len(sample.checked)} sampled URL(s) answered 200"
            + (f"; {', '.join(bad[:3])}" if bad else "")
        )
    sitemap_findings = list(sitemap.reasons) or [
        f"{sitemap.kind} at {sitemap.sitemap_url}, found via {sitemap.discovered_via}:"
        f" {sitemap.url_count} URL(s)"
        + (" (lower bound, truncated)" if sitemap.url_count_is_lower_bound else "")
    ]
    lines.append(
        Line(
            "sitemap",
            sitemap.status,
            sitemap_findings,
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
