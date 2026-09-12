#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adsense_checks.completeness import (  # noqa: E402
    CompletenessReport,
    Finding,
    check_completeness,
    strong_placeholders,
)
from adsense_checks.http import DEFAULT_TIMEOUT, MAX_WAIT_SECONDS  # noqa: E402
from adsense_checks.report import Line, exit_code, render  # noqa: E402
from adsense_checks.status import Status, worst  # noqa: E402

__doc__ = """The pre-flight gate: unfinished site, trust pages, broken navigation.

Serves ADS-COMPLETE-01, ADS-UX-05 and ADS-AUTHOR-02 in part.

    python scripts/check_completeness.py https://example.com [-v]

Requirement IDs stamped here: ADS-COMPLETE-01 (unfinished markers, broken
navigation), ADS-UX-05 (the trust pages exist and are not stubs) and
ADS-AUTHOR-02 (a contact channel exists in the HTML). ADS-AUTHOR-01 — a
verifiable real name behind the site — is `judgement` and no script decides it.
"""


def _all_findings(report: CompletenessReport) -> list[Finding]:
    """Every finding recorded anywhere in the report.

    `CompletenessReport.findings` holds only what the top-level check wrote. The
    trust-page and navigation verdicts live on their own sub-reports, so
    iterating the top-level list alone lost them — including the aggregate FAIL
    that check_trust_pages raises when neither trust page exists. The report then
    printed a verdict milder than `CompletenessReport.status`, which is this
    package's own defect class one layer further out.
    """
    found = list(report.findings)
    for sub in (report.trust, report.nav):
        if sub is not None:
            found.extend(sub.findings)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--nav-limit", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.nav_limit < 1:
        # `--nav-limit 0` followed no link at all and still exited 0.
        parser.error("--nav-limit must be at least 1")
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

    report = check_completeness(args.url, nav_link_limit=args.nav_limit, timeout=args.timeout)
    lines: list[Line] = []

    # check_completeness returns before building either sub-report when the home
    # page could not be read, and that is the only path that leaves them unset.
    # Every line below has to say so instead of reporting on a document nobody
    # read: "no unfinished markers found" over an unfetched page is a pass
    # printed for a condition nobody observed.
    home_unreadable = report.trust is None
    unread = "not checked: the home page could not be read"

    # Placeholders on the home page. A Placeholder carries no status of its own —
    # it is an observation, and the severity comes from its confidence, exactly as
    # check_completeness derives it: a strong marker is a WARNING, a weak one an
    # INFO that asks a human to look.
    home = report.home_placeholders
    if home_unreadable:
        home_status = Status.ERROR
    elif strong_placeholders(home):
        home_status = Status.WARNING
    elif home:
        home_status = Status.INFO
    else:
        home_status = Status.OK
    lines.append(
        Line(
            "home page is finished",
            home_status,
            [f"unfinished marker ({p.confidence}): {p.phrase!r}" for p in home]
            or [unread if home_unreadable else "no unfinished markers found"],
            requirement="ADS-COMPLETE-01",
        )
    )

    # Trust pages, one line each, always carrying the evidence for the verdict.
    # A Pass with nothing shown is what the reference forbids: the reader cannot
    # tell an observed pass from a check that never ran.
    for kind in ("about", "contact"):
        # Saying "not found" about a page nobody looked for would be the false
        # MISSING this package exists to prevent.
        outcome = None if home_unreadable else report.trust.pages.get(kind)
        if outcome is None:
            lines.append(Line(f"{kind} page", Status.ERROR, [unread], requirement="ADS-UX-05"))
            continue
        evidence = [f"{outcome.url} ({outcome.words} words)"] if outcome.url else []
        if outcome.reason:
            evidence.append(outcome.reason)
        # None for every MISSING and ERROR outcome: nothing was parsed, so there
        # are no channels to list. `describe()` is the module's own rendering and
        # covers mailto: and obfuscated addresses, which this line used to drop.
        channels = outcome.channels
        if channels is not None and channels.any_found:
            evidence.append("contact: " + channels.describe())
        lines.append(Line(f"{kind} page", outcome.status, evidence, requirement="ADS-UX-05"))

    # ADS-AUTHOR-02 gets its own line. The docstring claimed it while every Line
    # was stamped ADS-UX-05, so `grep ADS-AUTHOR-02` over a whole run returned
    # nothing and SKILL.md's completeness gate read the requirement as unchecked.
    contact = None if home_unreadable else report.trust.pages.get("contact")
    channels = contact.channels if contact is not None else None
    if home_unreadable:
        lines.append(Line("contact channel", Status.ERROR, [unread], requirement="ADS-AUTHOR-02"))
    elif channels is None:
        lines.append(
            Line(
                "contact channel",
                Status.MISSING,
                [
                    "no contact page was read, so the HTML offers nothing to inspect"
                    f" ({contact.reason if contact else 'not checked'})"
                ],
                requirement="ADS-AUTHOR-02",
            )
        )
    else:
        found = channels.any_found
        lines.append(
            Line(
                "contact channel",
                Status.OK if found else Status.MISSING,
                [channels.describe() if found else "no mailto:, address, form or profile link"]
                # Presence is all the HTML can show. The requirement also asks
                # that the channel RESOLVE, and probing it would mean sending
                # mail or POSTing to a stranger, so that half stays unmeasured.
                + ["reachability not tested: presence in the HTML is not delivery"],
                requirement="ADS-AUTHOR-02",
            )
        )

    # Navigation.
    nav = report.nav
    if nav is None:
        lines.append(Line("navigation", Status.ERROR, [unread], requirement="ADS-COMPLETE-01"))
    else:
        evidence = [f"{link.url} -> HTTP {link.status_code}" for link in nav.broken]
        # Printed per link and not as a group, unlike the line below, because
        # what was compared differs from link to link and the reader is entitled
        # to disbelieve "HTTP 200, and yet" without being shown the comparison.
        # It says "unverified", never "broken": the same equality holds for a
        # real page that renders the site's "nothing here" template.
        evidence += [
            f"{link.url} -> HTTP {link.status_code}, unverified: {link.reason}"
            for link in nav.same_as_not_found
        ]
        # Unresolved links raise the status to ERROR and used to contribute no
        # evidence, so the fallback fired and an [ERR] line read "none broken".
        evidence += [f"{link.url} -> unresolved ({link.reason})" for link in nav.unresolved]
        # Printed per link, like same_as_not_found and unlike the group line
        # below, because the directory a link landed in differs from link to
        # link — it is a fact about the request, not about the host.
        evidence += [
            f"{link.url} -> HTTP {link.status_code}, unverified: {link.reason}"
            for link in nav.unmeasured
        ]
        if nav.unverified:
            # One line for the group: the reason is a single observation about
            # the host, not a fact about each link. Printing it per link would
            # bury the URLs under the same sentence repeated.
            evidence.append(
                f"{len(nav.unverified)} link(s) answered HTTP 200 but were shown to be neither "
                f"working nor broken — {nav.unverified_reason} — and are counted as neither: "
                + ", ".join(link.url for link in nav.unverified[:5])
            )
        if nav.truncated:
            # "25 links followed, none broken" over a 32-link menu with three
            # 404s past the limit. The count is a lower bound and has to say so
            # on the line, not only in a finding further down.
            evidence.append(
                f"{nav.checked} of {nav.found} links followed (limit {args.nav_limit});"
                " the rest were not checked, so the broken count is a lower bound"
            )
        elif not evidence:
            # A pass has to name what makes it a pass. "None broken" over a host
            # that answers 200 for every URL it does not have is the soft-404
            # hole, so the line states which of the two things was observed.
            # "honest" is the only regime with an entry, and that is the point.
            # It used to also claim, for "fingerprint", that "none of these
            # links served the page it answers with" — a proof the code cannot
            # give, since a host with two not-found templates (a CMS mounted at
            # /blog/) matches neither and exits 0 under that sentence. On any
            # host that answers 200 for a URL it does not have, every 200 is now
            # recorded as unverified, so this branch is unreachable there:
            # `evidence` is never empty. Nothing left to word carefully.
            proof = {
                "honest": "; every directory they came from answers 404 or 410 for a URL that does "
                          "not exist, so HTTP 200 from it means the page is there",
            }.get(nav.not_found_regime, "")
            evidence = [f"all {nav.checked} navigation links followed, none broken{proof}"]
        lines.append(
            Line(
                "navigation",
                nav.status,
                evidence,
                # `lead_nowhere` used to sit here beside `http_4xx_5xx` carrying
                # a different number, the difference being the links this check
                # had merely inferred were dead. It no longer infers any, so the
                # two would now be the same column printed twice.
                {"found": nav.found, "checked": nav.checked,
                 "http_4xx_5xx": nav.count,
                 "same_as_not_found": len(nav.same_as_not_found),
                 "unverified": len(nav.unverified),
                 # Its own column rather than folded into `unverified`: the two
                 # are the same weight but not the same fact, and a run where
                 # this one is non-zero is telling the operator that part of
                 # their menu answered from a directory this run never measured.
                 "unmeasured": len(nav.unmeasured),
                 # The directories the per-run ceiling refused, spelled out. A
                 # count alone cannot tell the operator where to point a second
                 # run, and it cannot separate a ceiling that bit from a menu
                 # that redirected off-origin, which needs no second run at all.
                 "refused_directories": nav.refused_directories,
                 # The regime of the ANCHOR directory only — the one this audit
                 # invents URLs in. Named per directory in the evidence above,
                 # because the other directories a run measures can disagree
                 # with this one and usually do on a subdirectory install.
                 "anchor_not_found_regime": nav.not_found_regime or "not probed"},
                # Counting links that 404 is ADS-COMPLETE-01's "site looks
                # abandoned", which is what count_broken_nav_links' own docstring
                # says. ADS-UX-01 is a `judgement` requirement about readability,
                # alignment and dropdowns — stamping it here printed a PASS on a
                # requirement three quarters of which nobody looked at.
                "ADS-COMPLETE-01",
            )
        )

    # `getattr(finding, "message", finding)` used to stand here, defending
    # against a shape `Finding` cannot have. What it actually hid was the
    # sub-reports being skipped entirely.
    #
    # One line for all of them, not one line each: `render` counts its lines as
    # checks, so a finding per line made four checks tally as five, or as seven
    # when both trust pages were missing. Every message is still shown and the
    # worst status still reaches the verdict — this changes what is counted,
    # never what is decided.
    findings = _all_findings(report)
    if findings:
        lines.append(
            Line(
                "recorded findings",
                worst(*(f.status for f in findings)),
                [f"[{f.status.name}] {f.message}" for f in findings],
            )
        )

    text, overall = render(f"Completeness — {args.url}", lines, verbose=args.verbose)
    print(text)
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
