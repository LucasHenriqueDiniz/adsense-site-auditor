"""Sitemap discovery and parsing: namespace-agnostic, index-aware, honest counts.

Three defects in check_technical.py were the same refusal to look at what the
server actually sent:

  * URL counting matched the literal `sitemaps.org/0.9` namespace. A sitemap from
    a home-grown generator (no namespace at all) or one still on the 0.84 URI was
    reported as "found but contains no URLs". The namespace of a sitemap is
    decoration; the element names are the contract.
  * A `<sitemapindex>` had its `<loc>` elements counted as page URLs, so a site
    with 300,000 URLs behind two child sitemaps reported `url_count: 2`. Nothing
    in the output distinguished an index from a urlset, so the number was
    believed.
  * Discovery was hard-coded to /sitemap.xml and ignored the `Sitemap:` directive
    in the robots.txt the same script had already downloaded. WordPress with
    Yoast — a large share of the web — serves 404 at /sitemap.xml and declares
    /sitemap_index.xml in robots.txt, so the audit reported "no sitemap" for a
    sitemap that exists and is advertised by the site itself.

Two rules hold everywhere below. A document we could not read is ERROR, never a
count of zero: "malformed XML" and "empty sitemap" are different findings and
only one of them is the site's fault. And `found` is never inferred from HTTP
200 — a single-page-app catch-all answers 200 with index.html for every path,
including /sitemap.xml, and the old check called that a sitemap.

Requirement: ADS-CRAWL-07.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests

from adsense_checks.http import Fetch, fetch
from adsense_checks.robots import Robots, parse_robots
from adsense_checks.status import Status, escalate

# Tried in order, after anything robots.txt declares. /sitemap.xml first because
# it is the convention; the rest are the defaults shipped by the generators that
# do not use it — wp-sitemap.xml is WordPress core 5.5+, sitemap_index.xml is
# Yoast, and sitemap-index.xml is the hyphenated spelling several plugins emit.
CONVENTIONAL_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap/sitemap.xml",
)

# How many levels of nested <sitemapindex> to follow. The entry document is
# level 0, so 2 means index -> children -> grandchildren and no further.
MAX_INDEX_DEPTH = 2
# How many child documents to fetch in total while resolving an index. Large
# sites publish hundreds; an audit does not need to download all of them, but it
# does need to say so, which is what SitemapResult.truncated is for.
MAX_CHILD_SITEMAPS = 20
# Sample kept for the report. url_count is the real total; this is evidence.
MAX_SAMPLE_URLS = 50

_HTML_START = re.compile(r"^\s*(<!doctype\s+html|<html[\s>])", re.IGNORECASE)
_DOCTYPE = re.compile(r"<!doctype", re.IGNORECASE)


@dataclass
class SitemapDoc:
    """One sitemap document, after parsing."""

    url: str = ""
    # "urlset", "sitemapindex", or "unknown" when the body was not either.
    kind: str = "unknown"
    urls: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    status: Status = Status.OK
    reason: str = ""

    @property
    def is_sitemap(self) -> bool:
        return self.kind in ("urlset", "sitemapindex")


@dataclass
class SitemapResult:
    """The outcome of ADS-CRAWL-07's discovery and parsing half."""

    base_url: str
    status: Status = Status.OK
    sitemap_url: str | None = None
    # "robots.txt", "conventional path", or "none".
    discovered_via: str = "none"
    kind: str = "none"
    # Total page URLs found. A lower bound when `truncated` is set — read
    # url_count_is_lower_bound before quoting this number anywhere.
    url_count: int = 0
    # Child sitemaps declared by every index seen, whether or not we fetched them.
    child_sitemap_count: int = 0
    # Sitemap documents actually requested, including the candidates that came
    # back 404 or unreadable. robots.txt is not one of them and is not counted:
    # the caller may have fetched it. A .gz candidate is refused without a
    # request, so it does not count either. A field named "fetched" that reports
    # 1 after four requests understates the budget the audit spent.
    documents_fetched: int = 0
    truncated: bool = False
    sample_urls: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    # (url, status, reason) for every candidate that did not yield a sitemap.
    attempts: list[tuple[str, Status, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        """Whether a sitemap document was parsed. Never inferred from HTTP 200."""
        return self.kind in ("urlset", "sitemapindex")

    @property
    def url_count_is_lower_bound(self) -> bool:
        return self.truncated

    def note(self, reason: str) -> None:
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


def _local(tag: object) -> str:
    """Local element name, namespace stripped, lowercased.

    `{http://www.sitemaps.org/schemas/sitemap/0.9}loc`, `{...0.84}loc` and a bare
    `loc` all collapse to the same string here, which is the whole point: the
    previous check compared the fully qualified tag and therefore only ever saw
    one of the three.
    """
    if not isinstance(tag, str):
        # Comments and processing instructions carry a callable as their tag.
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _loc_values(root: ElementTree.Element, entry_name: str) -> list[str]:
    """`<loc>` text of each direct `<entry_name>` child of root.

    Deliberately structural rather than `root.iter()`. The image extension puts
    an `<image:loc>` inside every `<url>`, and a flat search by local name counts
    it as a page: a 100-URL sitemap with one image each would report 200. The
    grandchild is excluded here because it is not a direct child of `<url>`.
    """
    values: list[str] = []
    for entry in root:
        if _local(entry.tag) != entry_name:
            continue
        for child in entry:
            if _local(child.tag) != "loc":
                continue
            text = (child.text or "").strip()
            if text:
                values.append(text)
            break
    return values


def parse_sitemap(text: str, *, url: str = "", content_type: str = "") -> SitemapDoc:
    """Parse a sitemap body. Never raises; unreadable bodies come back as ERROR."""
    doc = SitemapDoc(url=url)
    body = (text or "").strip()

    if not body:
        doc.status = Status.MISSING
        doc.reason = "empty body"
        return doc

    media_type = content_type.split(";", 1)[0].strip().lower()
    if _HTML_START.match(body):
        # The SPA catch-all rewrite ("/* -> /index.html") answers 200 with the app
        # shell for /sitemap.xml. Calling that "found" was how a site with no
        # sitemap at all passed. It is absent, so say MISSING.
        #
        # The body decides, not the header. A sitemap generated by a script that
        # never calls header('Content-Type: application/xml') is served as
        # text/html — PHP's default — and condemning it on the header alone would
        # be declaring absence without having looked at what arrived, which is
        # the defect class this module exists to remove.
        doc.status = Status.MISSING
        doc.reason = f"served HTML, not a sitemap (content-type {media_type or 'unset'})"
        return doc

    if _DOCTYPE.search(body[:2048]):
        # Sitemaps carry no DOCTYPE. Refusing one costs nothing and is also the
        # cheapest guard against entity-expansion blowups, which
        # xml.etree.ElementTree does not defend against on untrusted input.
        doc.status = Status.ERROR
        doc.reason = "document declares a DOCTYPE; refused without parsing"
        return doc

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        if "html" in media_type:
            # HTML that does not open with <!doctype or <html: a theme error page,
            # a template fragment. The header is believed only here, after the body
            # itself has refused to be XML — it classifies a failure we already
            # observed instead of pre-empting the parse.
            doc.status = Status.MISSING
            doc.reason = f"served HTML, not a sitemap (content-type {media_type})"
            return doc
        # The distinction the old check collapsed: we could not read the file, so
        # we do not know how many URLs it has. Zero would be a claim we cannot make.
        doc.status = Status.ERROR
        doc.reason = f"XML parse error: {exc}"
        return doc

    name = _local(root.tag)
    if name == "urlset":
        doc.kind = "urlset"
        doc.urls = _loc_values(root, "url")
        if not doc.urls:
            doc.status = Status.WARNING
            doc.reason = "valid <urlset> listing zero URLs"
    elif name == "sitemapindex":
        doc.kind = "sitemapindex"
        doc.children = _loc_values(root, "sitemap")
        if not doc.children:
            doc.status = Status.WARNING
            doc.reason = "valid <sitemapindex> listing zero child sitemaps"
    else:
        doc.status = Status.MISSING
        doc.reason = f"root element is <{name}>, not <urlset> or <sitemapindex>"

    return doc


def discover_sitemap_urls(base_url: str, robots: Robots | None = None) -> list[str]:
    """Candidate sitemap URLs, most authoritative first, deduplicated.

    robots.txt comes first because it is the site's own declaration of where its
    sitemap lives. Hard-coding /sitemap.xml and ignoring the directive is what
    made the audit report "no sitemap" for every Yoast install on the web.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    if robots is not None and not robots.missing:
        for declared in robots.sitemaps:
            # The directive is specified as absolute, but relative values appear
            # in the wild; urljoin leaves an absolute URL untouched either way.
            add(urljoin(base_url, declared.strip()))

    for path in CONVENTIONAL_PATHS:
        add(urljoin(base_url, path))

    return candidates


def _fetch_doc(url: str, session: requests.Session | None) -> tuple[SitemapDoc, Fetch]:
    """Fetch and parse one sitemap URL through the shared fetch path."""
    if urlparse(url).path.lower().endswith(".gz"):
        # requests transparently decodes Content-Encoding, not a .gz payload, so
        # Fetch.text would be mojibake. Saying so is honest; parsing it and
        # reporting zero URLs would not be.
        doc = SitemapDoc(url=url, status=Status.ERROR, reason="gzipped sitemap; not readable here")
        return doc, Fetch(url=url)

    got = fetch(url, session=session)
    if not got.ok:
        reason = got.error or f"HTTP {got.status_code}"
        return SitemapDoc(url=url, status=got.status, reason=reason), got

    doc = parse_sitemap(
        got.text,
        url=got.final_url or url,
        content_type=got.headers.get("content-type", ""),
    )
    return doc, got


def _requested(got: Fetch) -> bool:
    """Whether a request actually went out for this candidate.

    The .gz short-circuit above returns a Fetch that was never sent; counting it
    as a document fetched would overstate the budget in the other direction.
    """
    return got.status_code is not None or got.error is not None


def _fold_failed_candidate(
    result: SitemapResult, candidate: str, doc: SitemapDoc, declared: set[str]
) -> None:
    """Carry a candidate that yielded no sitemap into the visible result.

    A later candidate that works does not unobserve this one. Who chose the URL
    decides how loudly it is reported. robots.txt is the site's own declaration:
    a sitemap it advertises and then does not serve is a finding at that URL's own
    severity, even when a conventional path saves the audit. A conventional path
    is our guess, so a 404 there says nothing about the site and stays silent —
    but a 5xx, or a body we could not read, is something we did observe, and
    dropping it into `attempts` alone summarises an observed failure as approval.
    """
    if candidate in declared:
        result.status = escalate(result.status, doc.status)
        result.note(f"sitemap {candidate} declared in robots.txt is unusable: {doc.reason}")
    elif doc.status in (Status.FAIL, Status.ERROR):
        result.status = escalate(result.status, Status.INFO)
        result.note(f"candidate {candidate} was not readable: {doc.reason}")


def _load_robots(base_url: str, session: requests.Session | None) -> tuple[Robots, str]:
    """Fetch and parse robots.txt. An unreachable file is 'allow all', per Google."""
    got = fetch(urljoin(base_url, "/robots.txt"), session=session)
    if not got.ok:
        return Robots(missing=True), f"robots.txt not read ({got.error or got.status_code})"
    return parse_robots(got.text), ""


def check_sitemap(
    base_url: str,
    *,
    robots: Robots | None = None,
    session: requests.Session | None = None,
    max_depth: int = MAX_INDEX_DEPTH,
    max_child_sitemaps: int = MAX_CHILD_SITEMAPS,
) -> SitemapResult:
    """Discover, fetch and parse the site's sitemap.

    Pass `robots` when the caller has already fetched robots.txt — the original
    defect was a script that had the file in hand and still ignored it. When it
    is None this fetches robots.txt itself rather than skipping discovery.
    """
    result = SitemapResult(base_url=base_url)

    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in ("http", "https") or not parsed_base.netloc:
        # A bare domain used to produce a base of "://" and a report full of
        # invented findings about ":///sitemap.xml". Refuse instead of inventing.
        result.status = Status.ERROR
        result.note(f"invalid base URL {base_url!r}: expected http(s) scheme and host")
        return result

    if robots is None:
        robots, note = _load_robots(base_url, session)
        result.note(note)

    declared: set[str] = set()
    if not robots.missing:
        declared = {urljoin(base_url, s.strip()) for s in robots.sitemaps}
    result.candidates = discover_sitemap_urls(base_url, robots)

    for candidate in result.candidates:
        doc, got = _fetch_doc(candidate, session)
        if _requested(got):
            result.documents_fetched += 1
        if not doc.is_sitemap:
            # Keep going: a 404 at /sitemap.xml says nothing about /sitemap_index.xml.
            # Going on is not the same as forgetting, which is what _fold does.
            result.attempts.append((candidate, doc.status, doc.reason))
            _fold_failed_candidate(result, candidate, doc, declared)
            continue

        result.sitemap_url = candidate
        result.discovered_via = "robots.txt" if candidate in declared else "conventional path"
        result.kind = doc.kind
        result.status = escalate(result.status, doc.status)
        result.note(doc.reason)

        host = urlparse(candidate).netloc
        if host != parsed_base.netloc:
            # Legal, but Google only honours a cross-host sitemap for a verified
            # property, so the reader needs to know before trusting the count.
            result.status = escalate(result.status, Status.INFO)
            result.note(f"sitemap is hosted on {host}, not {parsed_base.netloc}")

        if doc.kind == "urlset":
            _absorb_urls(result, doc.urls)
        else:
            _resolve_index(result, doc, session, max_depth, max_child_sitemaps)
        break

    if not result.found:
        # Nothing was parseable. Escalate over what the candidates actually said
        # so a 500 or a malformed body is not summarised as a plain absence.
        for _url, status, _reason in result.attempts:
            result.status = escalate(result.status, status)
        result.status = escalate(result.status, Status.MISSING)
        result.note(f"no sitemap found; tried {len(result.candidates)} candidate URLs")

    return result


def _absorb_urls(result: SitemapResult, urls: list[str]) -> None:
    result.url_count += len(urls)
    room = MAX_SAMPLE_URLS - len(result.sample_urls)
    if room > 0:
        result.sample_urls.extend(urls[:room])


def _resolve_index(
    result: SitemapResult,
    entry: SitemapDoc,
    session: requests.Session | None,
    max_depth: int,
    max_child_sitemaps: int,
) -> None:
    """Follow a `<sitemapindex>` into its children, within explicit limits.

    The defect this replaces counted the index's own `<loc>` elements as pages,
    so 300,000 URLs behind two child sitemaps were reported as `url_count: 2`.
    Following them costs requests, so both limits are caller-visible and hitting
    either one sets `truncated` — a partial count that does not announce itself
    is the same lie in a new place.
    """
    result.child_sitemap_count += len(entry.children)
    visited = {entry.url}
    queue: deque[tuple[str, int]] = deque((child, 1) for child in entry.children)
    children_fetched = 0

    while queue:
        url, depth = queue.popleft()
        if url in visited:
            # A sitemapindex listing itself, or two indexes citing each other, is
            # something real generators emit; without this the walk never ends.
            continue
        visited.add(url)

        if children_fetched >= max_child_sitemaps:
            # break, not return: the tail of this function still has to mark the
            # count partial, which is the only thing that makes it safe to read.
            result.truncated = True
            result.note(f"stopped after {max_child_sitemaps} child sitemaps fetched")
            break

        doc, got = _fetch_doc(url, session)
        children_fetched += 1
        if _requested(got):
            result.documents_fetched += 1

        if not doc.is_sitemap:
            # An index that points at a missing file is a real finding: the site
            # is advertising URLs no crawler can reach.
            result.status = escalate(result.status, doc.status)
            result.note(f"child sitemap {url} unusable: {doc.reason}")
            continue

        result.status = escalate(result.status, doc.status)
        if doc.reason:
            # A child that is readable but empty raises the status; naming it is
            # what makes that status readable. A WARNING nobody can explain sends
            # the reader back to the site with nothing to look at.
            result.note(f"child sitemap {url}: {doc.reason}")
        if doc.kind == "urlset":
            _absorb_urls(result, doc.urls)
            continue

        result.child_sitemap_count += len(doc.children)
        if depth >= max_depth:
            result.truncated = True
            result.note(f"nested index at depth {depth} not followed (max_depth={max_depth})")
            continue
        queue.extend((grandchild, depth + 1) for grandchild in doc.children)

    if result.url_count == 0 and not result.truncated:
        result.status = escalate(result.status, Status.WARNING)
        result.note("sitemap index resolved to zero page URLs")

    if result.truncated:
        # Not a site defect, but the number is incomplete and must not read as final.
        result.status = escalate(result.status, Status.INFO)


@dataclass
class SampleCheck:
    """Result of fetching a sample of the URLs a sitemap advertises."""

    checked: list[tuple[str, Status, int | None]] = field(default_factory=list)
    status: Status = Status.OK

    @property
    def ok_count(self) -> int:
        return sum(1 for _url, status, _code in self.checked if status is Status.OK)


def verify_sample_urls(
    result: SitemapResult,
    *,
    sample_size: int = 5,
    session: requests.Session | None = None,
) -> SampleCheck:
    """Fetch the first `sample_size` advertised URLs and report what they answer.

    ADS-CRAWL-07 asks that the listed URLs return 200. Kept out of check_sitemap
    so that discovery stays one concern and the request budget stays the caller's
    decision.
    """
    check = SampleCheck()
    if not result.sample_urls:
        check.status = Status.MISSING
        return check

    for url in result.sample_urls[:sample_size]:
        got = fetch(url, session=session)
        check.checked.append((url, got.status, got.status_code))
        check.status = escalate(check.status, got.status)
    return check
