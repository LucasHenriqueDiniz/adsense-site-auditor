"""robots.txt parsing for the AdSense-relevant crawlers.

Why this exists as a module rather than a substring test: the previous check asked
whether the file contained "*" and "disallow: /" anywhere, which is true of almost
every healthy robots.txt — `User-agent: *` supplies the star and `Disallow: /admin/`
supplies the prefix. Sites that block nothing were reported as blocking everything.

The rules implemented here follow Google's own robots.txt specification:

  * Records are grouped by consecutive `User-agent` lines. A group applies to an
    agent when one of its user-agent tokens matches.
  * Matching is case-insensitive and by substring of the product token, so
    `Googlebot` in the file matches the crawler `Googlebot-Image`.
  * If ANY group names an agent specifically, that agent ignores the `*` group
    entirely. It does not merge them.
  * Within the applicable group, the rule with the longest path pattern wins.
    On equal length, `Allow` beats `Disallow`.
  * `Disallow:` with an empty value allows everything; `Allow:` with an empty
    value is ignored.
  * `*` wildcards and a trailing `$` anchor are supported in paths.

Crawlers that matter for AdSense:

  * `Mediapartners-Google` fetches pages to choose ads. Blocking it does not stop
    indexing, but it does stop ad serving — the failure this audit exists to catch.
  * `AdsBot-Google` checks landing-page quality and, per Google, ignores the `*`
    group: it must be named explicitly to be excluded.
  * `Googlebot` handles ordinary indexing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ADSENSE_CRAWLER = "Mediapartners-Google"
ADSBOT_CRAWLER = "AdsBot-Google"
INDEX_CRAWLER = "Googlebot"

# AdsBot deliberately does not follow the wildcard group. Google documents this:
# it must be named to be excluded, which is why a site can block "*" and still be
# crawled by it.
_IGNORES_WILDCARD = {ADSBOT_CRAWLER.lower()}


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    # (is_allow, raw_path) in file order.
    rules: list[tuple[bool, str]] = field(default_factory=list)


@dataclass
class Robots:
    groups: list[Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    # True when the file could not be fetched or returned a non-200 status. Google
    # treats an unreachable robots.txt as "allow all", and so does this.
    missing: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.groups


def parse_robots(text: str) -> Robots:
    """Parse robots.txt content into groups. Never raises on malformed input."""
    robots = Robots()
    current: Group | None = None
    # A group is a run of user-agent lines followed by rules. A new user-agent line
    # after a rule line starts a new group.
    last_was_agent = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            if current is None or not last_was_agent:
                current = Group()
                robots.groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
        elif field_name in ("allow", "disallow"):
            if current is None:
                # Rules before any user-agent line belong to no group; Google ignores
                # them, and so do we.
                continue
            current.rules.append((field_name == "allow", value))
            last_was_agent = False
        elif field_name == "sitemap" and value:
            robots.sitemaps.append(value)

    return robots


def _group_for(robots: Robots, user_agent: str) -> Group | None:
    """The single group that governs this agent, or None when nothing applies."""
    ua = user_agent.lower()
    specific: Group | None = None
    wildcard: Group | None = None

    for group in robots.groups:
        for token in group.agents:
            if token == "*":
                if wildcard is None:
                    wildcard = group
            elif token and (token in ua or ua in token):
                # Longest matching token wins when several groups name the agent.
                if specific is None:
                    specific = group
                break

    if specific is not None:
        return specific
    if ua in _IGNORES_WILDCARD:
        return None
    return wildcard


def _pattern_to_regex(path: str) -> re.Pattern[str]:
    anchored_end = path.endswith("$")
    body = path[:-1] if anchored_end else path
    escaped = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile("^" + escaped + ("$" if anchored_end else ""))


def is_allowed(robots: Robots, user_agent: str, path: str = "/") -> bool:
    """Whether user_agent may fetch path. Unreachable or ruleless files allow all."""
    if robots.missing:
        return True

    group = _group_for(robots, user_agent)
    if group is None:
        return True

    best_len = -1
    best_allow = True
    for is_allow, pattern in group.rules:
        if not pattern:
            # "Disallow:" with no value means allow everything; "Allow:" empty is a no-op.
            continue
        if not _pattern_to_regex(pattern).match(path):
            continue
        # Longest pattern wins; Allow breaks a tie.
        length = len(pattern)
        if length > best_len or (length == best_len and is_allow and not best_allow):
            best_len = length
            best_allow = is_allow

    return best_allow


def blocks_everything(robots: Robots, user_agent: str) -> bool:
    """True when the agent is denied the site root — the case worth alarming about."""
    return not is_allowed(robots, user_agent, "/")
