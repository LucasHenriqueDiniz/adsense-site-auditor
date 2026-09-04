"""robots.txt parsing for the AdSense-relevant crawlers.

Why this exists as a module rather than a substring test: the previous check asked
whether the file contained "*" and "disallow: /" anywhere, which is true of almost
every healthy robots.txt — `User-agent: *` supplies the star and `Disallow: /admin/`
supplies the prefix. Sites that block nothing were reported as blocking everything.

The rules implemented here follow Google's own robots.txt specification:

  * A leading UTF-8 BOM is ignored, and only the first 500 KiB of the file is
    parsed — both are what Google documents.
  * Records are grouped by consecutive `User-agent` lines. A group applies to an
    agent when one of its user-agent tokens matches.
  * Matching is case-insensitive, and all non-matching text after the product
    token in the file is ignored: `Googlebot/1.2` and `Googlebot*` both mean
    `Googlebot`. A value that STARTS with `*` is the wildcard group, whatever
    follows it.
  * The token in the file must be the crawler's product token or a prefix of it
    ending on a `-` boundary: `Googlebot` in the file governs the crawler
    `Googlebot-Image`, and never the reverse, and `Google` governs neither.
  * When several groups match with DIFFERENT tokens, the one with the LONGEST
    token wins — it is the most specific thing the site said about this crawler.
  * Groups that match with the SAME token are merged into one, in file order.
  * If ANY group names an agent specifically, that agent ignores the `*` group
    entirely. Those two are never merged.
  * A handful of crawlers ignore the `*` group even when no group names them —
    see `_IGNORES_WILDCARD`.
  * Rule and path are percent-normalized before being compared, so `/~user` and
    `/%7Euser` are the same path and `/%2F` is still not `/`.
  * Within the applicable rules, the rule with the longest path pattern wins.
    On equal length, `Allow` beats `Disallow`.
  * `Disallow:` with an empty value allows everything; `Allow:` with an empty
    value is ignored.
  * `*` wildcards and a trailing `$` anchor are supported in paths.

Crawlers that matter for AdSense:

  * `Mediapartners-Google` fetches pages to choose ads. Blocking it does not stop
    indexing, but it does stop ad serving — the failure this audit exists to catch.
    Google documents it as ignoring the `*` group, so only a group that names it
    can exclude it.
  * `AdsBot-Google` checks landing-page quality and ignores `*` for the same
    reason: it must be named explicitly to be excluded.
  * `Googlebot` handles ordinary indexing, and follows `*` like anyone else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ADSENSE_CRAWLER = "Mediapartners-Google"
ADSBOT_CRAWLER = "AdsBot-Google"
INDEX_CRAWLER = "Googlebot"

# The crawlers Google documents as ignoring the wildcard group: they must be named
# to be excluded, which is why a site can block "*" and still be crawled by them.
# Mediapartners-Google belongs here — leaving it out made this library report a
# site as ad-blocked whenever it blocked "*", the single most common robots.txt
# there is, when Google in fact keeps fetching those pages to choose ads.
_IGNORES_WILDCARD = (ADSENSE_CRAWLER.lower(), ADSBOT_CRAWLER.lower())

# A user-agent value is a product token followed by text Google throws away, so
# "googlebot/1.2" and "googlebot*" both mean "googlebot". Comparing the raw value
# meant a file saying `User-agent: Googlebot*` matched no group at all and, with
# no "*" group to fall back to, silently allowed everything it meant to block.
#
# No digits, per RFC 9309's `identifier = 1*(%x2D / %x41-5A / %x5F / %x61-7A)` and
# Google's own extractor, which stops at the first character outside [a-zA-Z_-].
# Admitting digits looked harmless and was not: `User-agent: Mediapartners-Google2`
# yielded the token `mediapartners-google2`, which governs no crawler, so the group
# was discarded, the wildcard exemption applied, and a file that blocks ad serving
# came back "allowed" — a false pass on the one check this module exists for.
# Google reads that line as `Mediapartners-Google` and blocks. The cost is that a
# third-party agent whose name really carries a digit is cut at it
# (`w3c-checklink` becomes `w`); that group then governs nobody, which is what
# Google does with it too.
_PRODUCT_TOKEN = re.compile(r"[a-z_-]+")

# Google enforces a 500 KiB limit on robots.txt and ignores everything after it.
# Without a cap an 8.7 MB file parsed to 400_001 rules and a single is_allowed()
# call took 3.56s — the whole crawl budget spent on one hostile file.
_MAX_BYTES = 500 * 1024

# Octets that keep their meaning only while encoded, so their escapes are left
# alone during normalization. `%2F` must not become `/` (it is a literal slash
# inside a segment, not a separator) and `%2A` must not become `*` (it is a
# literal asterisk, not the wildcard). `%` itself is in here so `%25` and a bare
# `%` cannot end up as two different spellings of the same path.
# RFC 9309 §2.2.2: a percent-encoded octet is decoded before comparison only when
# it stands for an `unreserved` character. Everything else — reserved delimiters
# and the punctuation that is neither — stays encoded, so `%2F` never becomes a
# path separator and `%2A` never becomes the wildcard.
_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")


# The BOM as it arrives when the decoding went wrong. A robots.txt served as
# `text/plain` with no charset — nginx's default for a .txt, and the commoner of
# the two cases — leaves requests on its ISO-8859-1 fallback, and the three UTF-8
# BOM bytes surface as these three characters instead of one.
_BOM_SPELLINGS = ("\ufeff", "\u00ef\u00bb\u00bf")


def _strip_bom(text: str) -> str:
    """Drop a leading byte-order mark, however it survived decoding.

    Google: *"Google ignores invalid lines in robots.txt files, including the
    Unicode Byte Order Mark (BOM) at the beginning."* Without this the first
    field name is `\ufeffuser-agent`, the line is dropped as unknown, and every
    rule beneath it lands in the "no group yet" branch and is dropped too — so a
    file that disallows every crawler parses to nothing and reads as allow-all.
    """
    for bom in _BOM_SPELLINGS:
        if text.startswith(bom):
            return text[len(bom) :]
    return text


def _product_token(value: str) -> str:
    """The product token of a user-agent value, lowercased, with the junk dropped.

    Returns "" for a value with no token at all, which then matches no crawler.
    """
    value = value.strip().lower()
    if value == "*" or (value[:1] == "*" and value[1:2].isspace()):
        # A `*` alone, or a `*` followed by junk, is the global group. Requiring
        # the value to be exactly "*" made `User-agent: * Googlebot` parse to the
        # empty token — neither wildcard nor specific — so a file whose only group
        # was that one plus `Disallow: /` read as allow-all.
        #
        # A star GLUED to more text is not the global group, though: Google's
        # reference implementation treats `*` as global only standing alone or
        # followed by whitespace. Accepting any leading star made `User-agent:
        # *bot` block Googlebot, which Google would let through — over-blocking
        # rather than over-crawling, but a false alarm on the headline check.
        return "*"
    match = _PRODUCT_TOKEN.match(value)
    return match.group(0) if match else ""


def _token_governs(token: str, ua: str) -> bool:
    """Whether a file token applies to a crawler token. Both already lowercased.

    The match must end on a `-` boundary. `ua.startswith(token)` was broader than
    what RFC 9309 and Google's reference implementation do — they compare against
    the crawler's declared token list — and a bogus short token silently voided the
    wildcard group: `User-agent: Google` captured Googlebot, and because a specific
    group suppresses `*` entirely, the `Disallow: /` under `User-agent: *` vanished.

    Residual not fixed here: `User-agent: Mediapartners` still governs
    `Mediapartners-Google`, which Google would not do, because `Mediapartners` is
    not one of that crawler's declared tokens. Getting that right needs a table of
    declared tokens per crawler, which is out of scope for this module.
    """
    return bool(token) and (ua == token or ua.startswith(token + "-"))


def _ignores_wildcard(ua: str) -> bool:
    """Whether this crawler is exempt from the `*` group.

    Matched on the token family, the same direction as the group matching: an exact
    string test left `AdsBot-Google-Mobile`, documented as exempt too, governed by a
    `*` group that Google never applies to it.
    """
    return any(_token_governs(token, ua) for token in _IGNORES_WILDCARD)


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


def _truncate(text: str) -> str:
    """The first 500 KiB of the file, cut back to a whole line. Google's limit.

    Cut back to a whole line where there is one: half of
    `Disallow: /very/long/path` is a shorter and therefore BROADER rule than the
    site wrote, so trimming to the newline keeps a truncated file from blocking
    more than the real one.

    Where there is NO newline inside the cap, the bytes are kept as they are.
    `rfind` returning -1 used to empty the whole file, and any large file without a
    newline — one long line of anything — became allow-all, which is the direction
    that hides a real block. A partial rule over-blocks; no rule at all under-blocks,
    and Google truncates at the byte limit and parses whatever is left either way.
    """
    raw = text.encode("utf-8", "surrogatepass")
    if len(raw) <= _MAX_BYTES:
        return text
    cut = raw[:_MAX_BYTES]
    newline = cut.rfind(b"\n")
    if newline != -1:
        cut = cut[: newline + 1]
    return cut.decode("utf-8", "ignore")


def parse_robots(text: str) -> Robots:
    """Parse robots.txt content into groups. Never raises on malformed input."""
    robots = Robots()
    current: Group | None = None
    # A group is a run of user-agent lines followed by rules. A new user-agent line
    # after a rule line starts a new group.
    last_was_agent = False

    # The BOM is Cf, not whitespace, so strip() kept it: the first field parsed as
    # "<BOM>user-agent", was skipped as unknown, and every rule under it was then
    # dropped for having no group — a BOM'd `Disallow: /` for Mediapartners-Google
    # was reported as "allowed". Google documents the BOM as an ignored line.
    text = _truncate(_strip_bom(text))

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
            current.agents.append(_product_token(value))
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


def _rules_for(robots: Robots, user_agent: str) -> list[tuple[bool, str]]:
    """Every rule that governs this agent, in file order. Empty when none applies."""
    ua = user_agent.strip().lower()
    specific: list[tuple[bool, str]] = []
    # Length of the token that won, so a later, more specific group can take over.
    specific_len = -1
    wildcard: list[tuple[bool, str]] = []

    for group in robots.groups:
        # The longest token in THIS group that names the agent, so a group listing
        # both `*` and `Googlebot` counts as the specific one it also is.
        matched_len = -1
        names_wildcard = False
        for token in group.agents:
            if token == "*":
                names_wildcard = True
                continue
            # Prefix on a `-` boundary, not substring, and only in this direction.
            # Matching both ways let `User-agent: Googlebot-Image` capture the
            # crawler `Googlebot` and, because a specific group suppresses `*`, hid
            # the `Disallow: /` that actually governed it. See `_token_governs`.
            if not _token_governs(token, ua):
                continue
            matched_len = max(matched_len, len(token))

        if names_wildcard:
            # Every `*` group counts, not just the first. Two WordPress plugins each
            # appending their own `User-agent: *` block is the ordinary shape of this:
            # with `Disallow: /wp-admin/` written before `Disallow: /`, a site that
            # blocks everything was reported as open, and reported as blocked once the
            # two blocks were swapped.
            wildcard.extend(group.rules)
        if matched_len > specific_len:
            # A more specific token than anything seen so far replaces it: this is
            # the most precise thing the site said about this crawler.
            specific, specific_len = list(group.rules), matched_len
        elif matched_len == specific_len >= 0:
            # Same token, second group: Google combines them, so `Disallow: /a/` and
            # `Disallow: /b/` under two `User-agent: Googlebot` headers both apply.
            specific.extend(group.rules)

    if specific_len >= 0:
        return specific
    if _ignores_wildcard(ua):
        return []
    return wildcard


def _normalize(value: str) -> str:
    """One canonical percent-encoding for a rule pattern or a path.

    RFC 9309 requires both sides to be brought to the same form before comparison:
    non-ASCII octets percent-encoded, and percent-encoded ASCII unencoded unless it
    is reserved. Without it three real cases all failed OPEN — `Disallow: /~user`
    missed `/%7Euser`, and a Cyrillic `Disallow:` missed its own encoded URL and
    vice versa — so the crawler walked into paths the site had excluded.

    Only the RFC's `unreserved` set is decoded — ALPHA / DIGIT / `-` / `.` / `_` /
    `~`. Everything else stays encoded and uppercased, which is what keeps `%2F`
    distinct from `/` and stops `%2A` from turning into the `*` wildcard. Deciding
    it the other way round — decode unless reserved — also decoded `%3C`, `%7B` and
    the rest of the non-reserved-but-not-unreserved punctuation, which Google never
    decodes.
    """
    raw = value.encode("utf-8", "surrogatepass")
    out: list[str] = []
    i = 0
    while i < len(raw):
        byte = raw[i]
        if (
            byte == 0x25
            and i + 2 < len(raw)
            and raw[i + 1] in _HEX_DIGITS
            and raw[i + 2] in _HEX_DIGITS
        ):
            decoded = int(raw[i + 1 : i + 3], 16)
            out.append(chr(decoded) if decoded in _UNRESERVED else f"%{decoded:02X}")
            i += 3
            continue
        # Everything outside printable ASCII is encoded, so a literal `п` and a
        # `%D0%BF` written by hand end up as the same string. A stray `%` that is
        # not a valid escape is encoded too, so it cannot collide with `%25`.
        out.append(chr(byte) if 0x21 <= byte <= 0x7E and byte != 0x25 else f"%{byte:02X}")
        i += 1
    return "".join(out)


def _matches(pattern: str, path: str) -> bool:
    """Glob match from the start of the path: `*` is any run, a trailing `$` anchors.

    Two pointers with a backtrack anchor, the algorithm Google's own reference
    implementation uses, because the regex this replaced was attacker-controlled:
    every `*` became an unbounded `.*`, and a short `Disallow` line from a
    stranger's robots.txt hung a live crawl. Worst case is the NON-match, where the
    backtracking has to exhaust every combination: `/a*a*…*a*b` against
    `"/" + "a" * 40` took 0.26s at 8 stars, 3.57s at 10 and 35.6s at 12, and
    `requests`' timeout does not reach inside `re.match`.
    """
    anchored_end = pattern.endswith("$")
    # An unanchored pattern matches a prefix of the path, which is the same thing as
    # an anchored pattern with a trailing `*`.
    body = pattern[:-1] if anchored_end else pattern + "*"

    p = s = 0
    star = -1
    star_s = 0
    while s < len(path):
        if p < len(body) and body[p] == "*":
            star, star_s = p, s
            p += 1
        elif p < len(body) and body[p] == path[s]:
            p += 1
            s += 1
        elif star >= 0:
            # The last `*` swallows one more character and we retry from there. No
            # position is ever revisited, so the cost stays bounded by the input.
            star_s += 1
            p, s = star + 1, star_s
        else:
            return False
    while p < len(body) and body[p] == "*":
        p += 1
    return p == len(body)


def is_allowed(robots: Robots, user_agent: str, path: str = "/") -> bool:
    """Whether user_agent may fetch path. Unreachable or ruleless files allow all."""
    if robots.missing:
        return True

    target = _normalize(path)
    best_len = -1
    best_allow = True
    for is_allow, raw_pattern in _rules_for(robots, user_agent):
        if not raw_pattern:
            # "Disallow:" with no value means allow everything; "Allow:" empty is a no-op.
            continue
        pattern = _normalize(raw_pattern)
        if not _matches(pattern, target):
            continue
        # Longest pattern wins; Allow breaks a tie. Measured on the RAW pattern,
        # because that is what Google counts: its MaybeEscapePattern only uppercases
        # escapes and encodes high octets, and never decodes. Measuring the
        # normalized form inverted the outcome whenever an encoded rule met a
        # wildcard one — `Allow: /*x` and `Disallow: /%7E*` against `/%7Ex` came out
        # as a 3-3 tie that Allow won, where Google scores 3 against 5 and disallows.
        length = len(raw_pattern)
        if length > best_len or (length == best_len and is_allow and not best_allow):
            best_len = length
            best_allow = is_allow

    return best_allow


def blocks_everything(robots: Robots, user_agent: str) -> bool:
    """True when the agent is denied the site root — the case worth alarming about."""
    return not is_allowed(robots, user_agent, "/")
