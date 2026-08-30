"""Deterministic checks behind the AdSense readiness audit.

Only requirements decidable from the page or the HTTP response live here. The
81 requirement IDs in references/adsense-requirements.md split three ways:

  * 35 are deterministic and belong in this package
  * 34 are judgement calls that no script settles
  * 12 can only be answered by the account owner

A check in here must be able to fail. If a condition cannot be observed, it
returns ERROR or MISSING and says so — never OK.
"""

from adsense_checks.robots import is_allowed, parse_robots
from adsense_checks.status import Status, escalate, worst

__all__ = ["Status", "escalate", "worst", "parse_robots", "is_allowed"]
