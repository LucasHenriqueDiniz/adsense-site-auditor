"""Status values, ordered, with a combinator that can only escalate.

Every one of the five original scripts shared the same defect class: a check
assigned `result["status"] = ...` unconditionally, so a later, milder condition
overwrote an earlier severe one. A homepage returning 503 in 6.2 seconds was
reported as WARNING ("slow response") because the slowness test ran after the
5xx test and simply replaced the verdict.

The other half of the class was the summary. Each script decided its final line
from a hand-written list of statuses — `failures = FAIL, ERROR` — that had
drifted from the statuses the checks actually produced. MISSING was emitted and
never counted, so a site with no robots.txt and no sitemap printed "All
technical checks passed".

Both disappear if severity is a total order, combination only ever escalates,
and the verdict is derived from the statuses that were emitted rather than from
a list someone remembered to update.
"""

from __future__ import annotations

from enum import IntEnum


class Status(IntEnum):
    """Ordered by severity. Higher always wins a combination."""

    OK = 0
    INFO = 1
    # The thing checked for is absent. Distinct from FAIL because absence is
    # sometimes allowed (no robots.txt means "crawl everything") and sometimes
    # not (no privacy policy), and only the caller knows which.
    MISSING = 2
    WARNING = 3
    FAIL = 4
    # The check itself could not run — network error, timeout, unparseable
    # response. Never report ERROR as a pass: an unanswered question is not a
    # satisfied requirement, which is the mistake analyze_text_depth made by
    # counting failed fetches in its OK bucket.
    ERROR = 5

    @property
    def is_bad(self) -> bool:
        return self >= Status.MISSING

    @property
    def blocks_readiness(self) -> bool:
        """Whether this status alone should stop a 'Ready' verdict."""
        return self >= Status.WARNING


def worst(*statuses: Status) -> Status:
    """The most severe of the given statuses. OK when none are given."""
    return max(statuses, default=Status.OK)


def escalate(current: Status, candidate: Status) -> Status:
    """Raise `current` to `candidate` when candidate is worse. Never lowers.

    Use this instead of assignment inside a check that tests several conditions.
    """
    return worst(current, candidate)
