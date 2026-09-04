"""Rendering and exit codes, shared by every CLI.

The old scripts each decided their own summary line from a hand-written list of
statuses, and each list had drifted from the statuses its checks actually
produced. check_technical emitted MISSING and counted only FAIL and ERROR, so a
site with no robots.txt and no sitemap printed "All technical checks passed".
check_completeness emitted WARNING and counted neither, so it printed "✓
Completeness checks passed" directly under the problems it had just listed.

The verdict here is derived from the statuses that were actually emitted, so it
cannot drift. Nothing needs updating when a check learns a new status.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from adsense_checks.status import Status, worst

# One glyph per status, so a reader can scan a column rather than read prose.
_GLYPH = {
    Status.OK: "PASS",
    Status.INFO: "note",
    Status.MISSING: "MISS",
    Status.WARNING: "WARN",
    Status.FAIL: "FAIL",
    Status.ERROR: "ERR ",
}


@dataclass
class Line:
    """One reported check."""

    name: str
    status: Status
    findings: Sequence[str] = field(default_factory=tuple)
    details: dict | None = None
    requirement: str = ""


def render(title: str, lines: Iterable[Line], *, verbose: bool = False) -> tuple[str, Status]:
    """The report text and its overall status."""
    lines = list(lines)
    # MISSING for an empty report: `worst()` of nothing is OK, so zero checks
    # announced success. The verdict line said nothing was observed while the
    # exit code said everything held — the only place the two disagreed.
    overall = worst(*(line.status for line in lines)) if lines else Status.MISSING

    out = [f"{title}", "=" * len(title), ""]
    for line in lines:
        label = f"{line.requirement} " if line.requirement else ""
        out.append(f"[{_GLYPH[line.status]}] {label}{line.name}")
        for finding in line.findings:
            out.append(f"         - {finding}")
        if verbose and line.details:
            for key, value in sorted(line.details.items()):
                out.append(f"           {key}: {value}")
    out.append("")

    counts: dict[Status, int] = {}
    for line in lines:
        counts[line.status] = counts.get(line.status, 0) + 1
    tally = ", ".join(f"{_GLYPH[s].strip()}={counts[s]}" for s in sorted(counts))
    noun = "check" if len(lines) == 1 else "checks"
    out.append(f"{len(lines)} {noun}: {tally}")

    # The verdict names the worst status rather than asserting a pass. "No check
    # failed" and "every check passed" are different claims, and only the first
    # is true when something could not be observed.
    if not lines:
        # `worst()` of nothing is OK, so an empty report used to announce that
        # every check passed. Zero checks passed because zero ran.
        out.append("Verdict: no checks ran. Nothing was observed and nothing is claimed.")
    elif overall is Status.OK:
        out.append("Verdict: every check observed its condition and passed.")
    elif overall is Status.INFO:
        # INFO is this package's "recorded, not decided". Printing the OK
        # sentence over it put "every check observed its condition" four lines
        # under a note saying a condition had not been observed.
        out.append(
            "Verdict: nothing failed, and some observations were recorded without "
            "being decided — see the note lines."
        )
    elif overall is Status.MISSING:
        out.append(
            "Verdict: nothing failed, but something could not be observed. "
            "Not a pass — see the MISS lines."
        )
    else:
        out.append(f"Verdict: worst status is {overall.name}. Not ready.")
    return "\n".join(out), overall


def exit_code(status: Status) -> int:
    """0 only when everything was observed and held.

    Anything from MISSING up is non-zero, so a script cannot report success for a
    site it never managed to read — which is what every one of the original five
    did in at least one path.
    """
    return 0 if status <= Status.INFO else 1
