"""Breadth-first site crawl, plus the three requirements decided from its output.

Two defects in the original crawler were not "bugs in an edge case": each one
silently emptied the audit on the most ordinary site there is.

  * The base domain was taken from the URL the user typed, while every link was
    resolved against `resp.url` — the URL *after* redirects. On a site that
    redirects apex to www (the dominant setup on the web) every resolved link
    landed on a netloc different from the base, and the crawl stopped after one
    page. It reported "Crawled 1 URLs" and no warning, so every downstream check
    ran over a single page and passed. Here the base host comes from the FINAL
    response of the first request, and `www.` is not part of a host's identity.
  * Metadata was parsed from `resp.text[:5000]`, while title/meta/h1 are only
    committed on the closing tag. A `<head>` holding inline critical CSS — a
    normal build output — pushed `<title>` past 5000 characters, and the page was
    reported as having no title, no description and no H1. The parser now reads
    the whole document, in a single pass that also collects the links.

The rest of the module follows from the same rule the package is built on: a
check that cannot observe its condition returns ERROR or MISSING with a reason,
never OK. A crawl that could not reach the site is ERROR, not "0 problems"; a
sample that never grew past the homepage is MISSING, not "all pages fine" —
that MISSING is the alarm that would have caught the redirect defect on day one.

Requirements decided here:

  * ADS-CRAWL-01 — home and a sample of internal URLs answer 2xx, publicly,
    without authentication.
  * ADS-CRAWL-04 — redirect chains are short, and do not depend on session state.
  * ADS-CRAWL-05 — no session identifiers in URLs, and `<link rel="canonical">`
    is stable across independent sessions.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests

from adsense_checks.http import ADSENSE_UA, DEFAULT_TIMEOUT, Fetch, fetch
from adsense_checks.robots import ADSENSE_CRAWLER, Robots, is_allowed, parse_robots
from adsense_checks.status import Status, escalate, worst

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_PAGES = 100
# A crawler with no ceiling and no pause is a load generator: the old one could
# fire ~10^4 sequential requests at a third party from `--depth 2`. Politeness is
# the default and has to be switched off explicitly (tests pass delay=0).
DEFAULT_DELAY = 0.5
# Identify honestly. The old crawler forged a Chrome User-Agent, which not only
# denies the site any chance to rate-limit the bot, it also asks the wrong
# question: this audit wants to know what Mediapartners-Google is served.
DEFAULT_USER_AGENT = ADSENSE_UA

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# Extensions that are never an HTML document. Filtering before the request is the
# point: the old crawler enqueued whatever matched an `href=` regex and then
# downloaded it whole into memory — a linked .mp4 was a 500MB read, and a PDF was
# parsed as HTML and reported as a page with "[no title]".
_ASSET_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".csv",
    ".zip", ".gz", ".tgz", ".tar", ".rar", ".7z", ".dmg", ".exe", ".apk", ".pkg",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".mp3", ".mp4", ".m4a", ".avi", ".mov", ".wmv", ".webm", ".ogg", ".wav",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom", ".txt",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
)

# Text inside these never counts as page words, and no <title> found under <svg>
# is the document title.
_NON_TEXT_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head", "title"})

_DEFAULT_PORTS = {"http": "80", "https": "443"}

# ADS-CRAWL-05: identifiers that make a URL specific to one visitor. The leading
# delimiter class is what keeps `?asid=` and `/inside=` from matching `sid=`.
_SESSION_ID_RE = re.compile(
    r"(?:^|[?&;/])"
    r"(phpsessid|jsessionid|aspsessionid[a-z0-9]*|sessionid|session_id|sessid"
    r"|sid|uid|userid|user_id|zenid|cfid|cftoken|oscsid)"
    r"=",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# URL identity
# --------------------------------------------------------------------------- #


def normalize_url(url: str) -> str:
    """The identity of a URL for deduplication purposes.

    Drops the fragment, lowercases scheme and host, removes a default port and
    makes the trailing slash consistent. The old crawler only skipped links
    starting with "#", so `/about`, `/about/`, `/about#team` and `/about#jobs`
    were four GETs and four identical report lines for one document — and the
    downstream analysis, iterating a list rather than a set, counted that page
    four times when computing the share of thin pages.
    """
    parsed = urlparse(urldefrag(url)[0])
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    default_port = _DEFAULT_PORTS.get(scheme)
    if default_port and netloc.endswith(":" + default_port):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def site_host(url: str) -> str:
    """Host without `www.`, lowercased — the identity of a *site*, not of a name.

    `ex.com` and `www.ex.com` are one site for every purpose this audit has, and
    treating them as two is precisely what stopped the old crawl dead.
    """
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    """Whether two URLs belong to the same site (www-insensitive)."""
    host_a = site_host(a)
    return bool(host_a) and host_a == site_host(b)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _path_with_query(url: str) -> str:
    """What robots.txt rules are matched against: path plus query string."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _looks_like_asset(url: str) -> bool:
    return urlparse(url).path.lower().endswith(_ASSET_SUFFIXES)


def has_session_id(url: str) -> bool:
    """Whether the URL carries a per-visitor session identifier (ADS-CRAWL-05)."""
    return _SESSION_ID_RE.search(url) is not None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


class PageParser(HTMLParser):
    """One pass over a whole document: metadata, links and visible-word count.

    Every field here corresponds to something the previous extractor got wrong:

      * the title and H1 buffers were never cleared, so each closing tag
        re-assigned the concatenation of everything seen so far — a page with a
        logo H1 and an article H1 reported `AcmeHow to bake bread`, and an inline
        SVG icon turned the title into `Home | AcmeMenu icon`;
      * the meta description was only read while an `in_head` flag was set, but
        HTMLParser is a tokenizer and does not synthesise the implicit `<head>`
        that HTML5 allows a document to omit, so valid minified pages reported no
        description at all;
      * attribute lookups used `.get("name", "")`, which returns None — not "" —
        for a valueless attribute like `<meta name="description" content>`, and
        the AttributeError it raised was swallowed by a bare `except: pass`,
        losing the rest of the document with no trace;
      * links came from a regex over the raw HTML, which matched hrefs inside
        `<link rel=stylesheet>`, HTML comments and JavaScript strings while
        missing unquoted and space-padded ones. Collecting them in the tokenizer
        makes all six cases right by construction, and gives the attribute value
        with entities already resolved, so `&amp;` no longer reaches the wire.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta_description: str | None = None
        self.canonical: str | None = None
        self.base_href: str | None = None
        self.meta_robots: str | None = None
        self.h1s: list[str] = []
        self.links: list[str] = []
        self.nofollow_links: list[str] = []
        self._text: list[str] = []
        self._title_buf: list[str] | None = None
        self._h1_buf: list[str] | None = None
        self._svg_depth = 0
        self._non_text_depth = 0

    @property
    def text(self) -> str:
        return " ".join(self._text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def h1(self) -> str | None:
        return self.h1s[0] if self.h1s else None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `or ""` and not a default: dict.get's default never fires for a key that
        # exists with a None value, which is exactly what `content` alone parses to.
        attr = {name.lower(): (value or "") for name, value in attrs}

        if tag == "svg":
            self._svg_depth += 1
        if tag in _NON_TEXT_TAGS:
            self._non_text_depth += 1

        if tag == "title":
            # An <svg><title> is an accessibility label, not the document title.
            if self._svg_depth == 0 and self.title is None:
                self._title_buf = []
        elif tag == "h1":
            self._h1_buf = []
        elif tag == "base":
            if self.base_href is None and attr.get("href", "").strip():
                self.base_href = attr["href"].strip()
        elif tag == "meta":
            name = attr.get("name", "").lower()
            content = attr.get("content", "").strip()
            # No `in_head` condition: a meta description counts wherever it
            # appears, and the flag only ever produced false "no description".
            if name == "description" and self.meta_description is None:
                self.meta_description = content
            elif name == "robots" and self.meta_robots is None:
                self.meta_robots = content.lower()
        elif tag == "link":
            rels = attr.get("rel", "").lower().split()
            if "canonical" in rels and self.canonical is None:
                self.canonical = attr.get("href", "").strip() or None
        elif tag == "a":
            href = attr.get("href", "").strip()
            if href:
                rels = attr.get("rel", "").lower().split()
                target = self.nofollow_links if "nofollow" in rels else self.links
                target.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg":
            self._svg_depth = max(0, self._svg_depth - 1)
        if tag in _NON_TEXT_TAGS:
            # Clamped: a stray closing tag must not make the counter negative and
            # start counting script bodies as page text.
            self._non_text_depth = max(0, self._non_text_depth - 1)

        if tag == "title" and self._title_buf is not None:
            self.title = "".join(self._title_buf).strip() or None
            self._title_buf = None
        elif tag == "h1" and self._h1_buf is not None:
            text = "".join(self._h1_buf).strip()
            if text:
                self.h1s.append(text)
            self._h1_buf = None

    def handle_data(self, data: str) -> None:
        if self._title_buf is not None:
            self._title_buf.append(data)
        if self._h1_buf is not None:
            self._h1_buf.append(data)
        if self._non_text_depth == 0:
            self._text.append(data)


def parse_html(html: str) -> PageParser:
    """Parse a document. Raises nothing that `feed` would not raise itself."""
    parser = PageParser()
    parser.feed(html)
    parser.close()
    return parser


# --------------------------------------------------------------------------- #
# Crawl results
# --------------------------------------------------------------------------- #


@dataclass
class Page:
    """One fetched URL. Both the requested and the final URL are kept.

    The old result had a single `url` field holding `resp.url` while `visited`
    held the requested one, so the fact that a redirect happened at all was
    thrown away — and with it any chance of reporting the 301/302 the README
    promised.
    """

    requested_url: str
    final_url: str
    depth: int
    status_code: int | None = None
    # Combines the HTTP outcome with whether the document could be parsed: a page
    # whose parse failed has unobservable metadata, which is ERROR, not "no tags".
    status: Status = Status.OK
    error: str | None = None
    parse_error: str | None = None
    content_type: str = ""
    is_html: bool = True
    auth_challenged: bool = False
    redirect_chain: list[tuple[int, str]] = field(default_factory=list)
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    h1_count: int = 0
    canonical: str | None = None
    # Words of visible text, and the size of the markup. The old field was called
    # `content_length` but held len(resp.text) — characters of HTML including
    # inline CSS and JS — so a 120-word page behind 80KB of script outranked a
    # 2000-word one on the very metric used to judge thin content.
    word_count: int = 0
    html_chars: int = 0
    links: list[str] = field(default_factory=list)
    nofollow_links: list[str] = field(default_factory=list)
    non_http_links: list[str] = field(default_factory=list)
    elapsed_ms: float | None = None

    @property
    def redirect_hops(self) -> int:
        return len(self.redirect_chain)

    @property
    def redirected(self) -> bool:
        """Whether the response came from somewhere other than the URL requested.

        The hop chain is consulted first, because comparing normalized URLs alone
        answers "no" for a real `/blog` -> `/blog/` redirect: the trailing slash
        is precisely what normalization erases, so the one hop that did happen
        would be reported as no redirect at all.
        """
        if self.redirect_chain:
            return True
        return normalize_url(self.requested_url) != normalize_url(self.final_url)

    @property
    def downgraded_to_http(self) -> bool:
        """Chain started on https and ended on http."""
        return (
            urlparse(self.requested_url).scheme == "https"
            and urlparse(self.final_url).scheme == "http"
        )


@dataclass
class CrawlResult:
    start_url: str
    # Origin and host taken from the FINAL response to the first request, never
    # from `start_url`. This single line is the redirect blocker's fix.
    base_url: str = ""
    base_host: str = ""
    max_depth: int = DEFAULT_MAX_DEPTH
    max_pages: int = DEFAULT_MAX_PAGES
    pages: list[Page] = field(default_factory=list)
    robots: Robots = field(default_factory=lambda: Robots(missing=True))
    robots_note: str = ""
    sitemaps: list[str] = field(default_factory=list)
    blocked_by_robots: list[str] = field(default_factory=list)
    skipped_assets: list[str] = field(default_factory=list)
    off_site: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    # Whether the crawl itself could be performed — NOT a verdict on the site.
    # The site's verdict is what the check_* functions below return.
    status: Status = Status.OK

    @property
    def html_pages(self) -> list[Page]:
        return [p for p in self.pages if p.is_html and p.status_code == 200]

    @property
    def worst_page_status(self) -> Status:
        return worst(*[p.status for p in self.pages])

    def by_url(self, url: str) -> Page | None:
        target = normalize_url(url)
        for page in self.pages:
            known = {normalize_url(page.final_url), normalize_url(page.requested_url)}
            if target in known:
                return page
        return None


# --------------------------------------------------------------------------- #
# The crawl
# --------------------------------------------------------------------------- #


def _load_robots(
    base_url: str,
    *,
    session: requests.Session,
    timeout: int,
    user_agent: str,
) -> tuple[Robots, str]:
    """Fetch robots.txt from the origin actually served. Unreachable means allow-all."""
    result = fetch(
        base_url + "/robots.txt", timeout=timeout, session=session, user_agent=user_agent
    )
    if result.error is not None:
        return Robots(missing=True), f"robots.txt unreachable ({result.error})"
    if result.status_code != 200:
        return Robots(missing=True), f"robots.txt returned {result.status_code}"
    # A soft 404 serving the HTML error page would otherwise be parsed as rules,
    # and an HTML document contains no `user-agent:` line, so it would silently
    # read as "allow everything" anyway — but for the wrong reason.
    if "html" in result.headers.get("content-type", "").lower():
        return Robots(missing=True), "robots.txt served as HTML (soft 404)"
    return parse_robots(result.text), ""


def _record(result: CrawlResult, response: Fetch, depth: int) -> Page:
    """Turn one Fetch into a Page and append it, resolving its links."""
    page = Page(
        requested_url=response.url,
        final_url=response.final_url or response.url,
        depth=depth,
        status_code=response.status_code,
        error=response.error,
        redirect_chain=list(response.redirect_chain),
        elapsed_ms=response.elapsed_ms,
        html_chars=len(response.text),
    )
    page.content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    # An absent Content-Type is treated as HTML: refusing to parse would lose real
    # pages, and the extension filter has already kept the obvious binaries out.
    page.is_html = page.content_type in _HTML_TYPES or page.content_type == ""
    page.auth_challenged = (
        response.status_code in (401, 407) or "www-authenticate" in response.headers
    )

    status = response.status
    if response.error is None and page.is_html and response.text:
        try:
            parsed = parse_html(response.text)
        except Exception as exc:  # a malformed document is a fact worth reporting
            # The original swallowed this with `except Exception: pass`, so a parse
            # failure was indistinguishable from a page that genuinely has no H1.
            page.parse_error = f"{type(exc).__name__}: {exc}"
            status = escalate(status, Status.ERROR)
        else:
            page.title = parsed.title
            page.meta_description = parsed.meta_description or None
            page.h1 = parsed.h1
            page.h1_count = len(parsed.h1s)
            page.word_count = parsed.word_count
            base = urljoin(page.final_url, parsed.base_href) if parsed.base_href else page.final_url
            page.canonical = urljoin(base, parsed.canonical) if parsed.canonical else None
            page.links = _resolve_links(base, parsed.links, page.non_http_links)
            page.nofollow_links = _resolve_links(base, parsed.nofollow_links, page.non_http_links)

    page.status = status
    result.pages.append(page)
    for link in page.links:
        if not same_site(link, result.base_url) and link not in result.off_site:
            result.off_site.append(link)
    return page


def _resolve_links(base: str, hrefs: Sequence[str], non_http: list[str]) -> list[str]:
    """Absolute, de-fragmented, de-duplicated, in document order.

    The URL kept is the one the page published, only made absolute: it is what
    the crawler will request. `normalize_url` decides *identity* here and nothing
    else, because the trailing slash it drops is not decoration on the wire — a
    host that only serves `/blog/` answers 404 to `/blog`, and the audit would
    report a broken link over a link that works.
    """
    resolved: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if href.startswith("#"):
            continue
        scheme = urlparse(href).scheme.lower()
        if scheme and scheme not in ("http", "https"):
            # mailto:, tel:, javascript:, data: — kept, because ADS-AUTHOR-02 needs
            # the mailto: links and re-parsing every page to find them would be waste.
            if href not in non_http:
                non_http.append(href)
            continue
        # The fragment is dropped from the URL itself: it never travels, and
        # keeping it would send the same document to the queue once per anchor.
        absolute = urldefrag(urljoin(base, href))[0]
        if urlparse(absolute).scheme.lower() not in ("http", "https"):
            continue
        identity = normalize_url(absolute)
        if identity not in seen:
            seen.add(identity)
            resolved.append(absolute)
    return resolved


def _enqueue(
    result: CrawlResult,
    page: Page,
    queue: list[tuple[str, int]],
    seen: set[str],
) -> None:
    """Queue a page's same-site links. The only depth guard in the crawl lives here.

    The original had two guards with different semantics — `depth > max_depth` in
    the loop and `depth < max_depth` at the append — and the first was
    unreachable, which made the depth limit look enforced in a place where it was
    not. One guard, one meaning.

    `seen` holds normalized identities; the queue holds the URL as published, so
    four spellings of one document still cost one GET, and that GET asks for the
    spelling the site actually serves.
    """
    if page.depth >= result.max_depth:
        return
    for link in page.links:
        if not same_site(link, result.base_url):
            continue
        if _looks_like_asset(link):
            if link not in result.skipped_assets:
                result.skipped_assets.append(link)
            continue
        identity = normalize_url(link)
        if identity in seen:
            continue
        # Recorded as seen at enqueue time, not at visit time: the old crawler
        # only tested `visited`, so one URL linked from fifty pages sat in the
        # queue fifty times.
        seen.add(identity)
        queue.append((link, page.depth + 1))


def crawl(
    start_url: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    respect_robots: bool = True,
    extra_seeds: Sequence[str] = (),
) -> CrawlResult:
    """Crawl a site breadth-first and collect what the audit needs per page.

    Network failures come back as ERROR inside the result; programming errors are
    left to propagate, because the original's blanket `except Exception` turned a
    site that was entirely offline into a successful run with one "page".
    """
    sess = session or requests.Session()
    result = CrawlResult(start_url=start_url, max_depth=max_depth, max_pages=max_pages)

    # One request before robots.txt is unavoidable: the origin that robots.txt
    # must be read from is only known after the redirects of the first request.
    root = fetch(start_url, timeout=timeout, session=sess, user_agent=user_agent)
    if root.error is not None or not root.final_url:
        result.status = Status.ERROR
        result.stopped_reason = f"could not reach {start_url}: {root.error or 'no response'}"
        _record(result, root, 0)
        return result

    result.base_url = _origin(root.final_url)
    result.base_host = site_host(root.final_url)

    if respect_robots:
        result.robots, result.robots_note = _load_robots(
            result.base_url, session=sess, timeout=timeout, user_agent=user_agent
        )
    else:
        result.robots_note = "robots.txt not consulted (respect_robots=False)"
    result.sitemaps = list(result.robots.sitemaps)

    root_page = _record(result, root, 0)
    if respect_robots and not is_allowed(
        result.robots, ADSENSE_CRAWLER, _path_with_query(root.final_url)
    ):
        # The page is kept: it was fetched before robots.txt could be located, and
        # discarding evidence already on hand would leave the audit blind about
        # the very site it is auditing. Nothing further is requested.
        result.blocked_by_robots.append(root.final_url)
        result.stopped_reason = "robots.txt disallows the AdSense crawler at the site root"
        return result

    seen = {normalize_url(start_url), normalize_url(root.final_url)}
    queue: list[tuple[str, int]] = []
    _enqueue(result, root_page, queue, seen)
    for seed in extra_seeds:
        # The seed is requested exactly as given, for the same reason a link is:
        # the normalized form is an identity, not an address.
        identity = normalize_url(seed)
        if identity not in seen:
            seen.add(identity)
            queue.append((seed, 0))

    cursor = 0
    while cursor < len(queue):
        if len(result.pages) >= max_pages:
            result.stopped_reason = f"stopped at max_pages={max_pages}"
            break
        url, depth = queue[cursor]
        cursor += 1

        if respect_robots and not is_allowed(result.robots, ADSENSE_CRAWLER, _path_with_query(url)):
            result.blocked_by_robots.append(url)
            continue

        if delay > 0:
            time.sleep(delay)
        response = fetch(url, timeout=timeout, session=sess, user_agent=user_agent)
        page = _record(result, response, depth)
        # The redirect target counts as visited too, so a second spelling of the
        # same document does not cost another request.
        seen.add(normalize_url(page.final_url))
        _enqueue(result, page, queue, seen)

    return result


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    requirement: str
    status: Status
    findings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """OK and INFO pass. MISSING and ERROR never do — an unanswered question
        is not a satisfied requirement."""
        return not self.status.is_bad


def check_pages_reachable(
    result: CrawlResult, *, min_sample: int = 2, requirement: str = "ADS-CRAWL-01"
) -> CheckResult:
    """ADS-CRAWL-01 — home and a sample of internal URLs answer 2xx, publicly.

    The `min_sample` clause is the guard against the defect that motivated this
    module: when the crawl reaches nothing but the homepage there is no sample,
    so the requirement is unverified (MISSING) rather than satisfied. The old
    script printed "Crawled 1 URLs" and let every later check pass on that.
    """
    check = CheckResult(requirement, Status.OK)
    if result.status is Status.ERROR:
        check.status = Status.ERROR
        check.findings.append(result.stopped_reason or "crawl could not run")
        return check
    if not result.pages:
        check.status = Status.ERROR
        check.findings.append("no pages were fetched")
        return check

    status = Status.OK
    for page in result.pages:
        if page.error is not None:
            status = escalate(status, Status.ERROR)
            check.findings.append(f"{page.requested_url}: request failed ({page.error})")
        elif page.auth_challenged or page.status_code == 403:
            status = escalate(status, Status.FAIL)
            check.findings.append(
                f"{page.final_url}: {page.status_code} — not publicly readable without credentials"
            )
        elif page.status_code == 404:
            status = escalate(status, Status.MISSING)
            check.findings.append(f"{page.final_url}: 404 on a link found on the site")
        elif page.status_code is not None and page.status_code >= 400:
            status = escalate(status, Status.FAIL)
            check.findings.append(f"{page.final_url}: HTTP {page.status_code}")

    if len(result.pages) < min_sample:
        status = escalate(status, Status.MISSING)
        check.findings.append(
            f"only {len(result.pages)} page(s) reached: there is no internal sample to judge"
            f" (links found: {sum(len(p.links) for p in result.pages)})"
        )

    check.status = status
    check.details = {
        "pages": len(result.pages),
        "ok": sum(1 for p in result.pages if p.status_code == 200),
        "blocked_by_robots": len(result.blocked_by_robots),
        "stopped_reason": result.stopped_reason,
    }
    return check


def check_redirect_chain(
    result: CrawlResult,
    *,
    max_hops: int = 2,
    verify_stateless: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    requirement: str = "ADS-CRAWL-04",
) -> CheckResult:
    """ADS-CRAWL-04 — redirect chains stay short and do not depend on session state.

    `verify_stateless` re-requests every page that redirected using a brand-new
    `requests.Session`, so no cookie set earlier in the crawl is sent. A landing
    page that only resolves for a visitor who already has a session is a page
    Google cannot crawl.

    That second half costs one request per redirecting page, which is why it is
    off by default — but a requirement whose second half was never looked at
    comes back MISSING, not OK. Only a site with no redirect at all can pass
    without it: there the question has an observed answer, namely that there is
    no chain to depend on anything.

    That last sentence needs a working site behind it. When no HTML page was
    readable, "no redirect chain" is vacuous rather than observed, so the answer
    is MISSING as well.
    """
    check = CheckResult(requirement, Status.OK)
    if result.status is Status.ERROR:
        check.status = Status.ERROR
        check.findings.append(result.stopped_reason or "crawl could not run")
        return check
    if not result.pages:
        check.status = Status.ERROR
        check.findings.append("no pages were fetched")
        return check

    status = Status.OK
    if not result.html_pages:
        # Same rule as check_session_urls, and the same trap: `result.pages` is
        # non-empty here because the failed responses are pages. Nothing was
        # readable, so "no redirect chain" is vacuous rather than observed — a
        # site answering 500 everywhere was passing this requirement.
        check.status = Status.MISSING
        check.findings.append(
            "no readable HTML page was reached: whether redirects are short and"
            " stateless could not be observed"
        )
        check.details = {"redirected_pages": 0, "verified_stateless": verify_stateless}
        return check

    redirected = [p for p in result.pages if p.redirect_hops > 0]
    for page in redirected:
        if page.redirect_hops > max_hops:
            status = escalate(status, Status.WARNING)
            check.findings.append(
                f"{page.requested_url}: {page.redirect_hops} redirect hops (limit {max_hops})"
            )
        if page.downgraded_to_http:
            status = escalate(status, Status.FAIL)
            check.findings.append(f"{page.requested_url}: redirect ends on http")

    if verify_stateless:
        for page in redirected:
            # session=None makes `fetch` build a fresh Session: no cookies at all.
            again = fetch(page.requested_url, timeout=timeout, user_agent=DEFAULT_USER_AGENT)
            if again.error is not None:
                status = escalate(status, Status.ERROR)
                check.findings.append(
                    f"{page.requested_url}: cookie-less re-request failed ({again.error})"
                )
            elif normalize_url(again.final_url) != normalize_url(page.final_url):
                status = escalate(status, Status.WARNING)
                check.findings.append(
                    f"{page.requested_url}: redirect depends on session state "
                    f"(with cookies -> {page.final_url}; without -> {again.final_url})"
                )
    elif redirected:
        status = escalate(status, Status.MISSING)
        check.findings.append(
            f"{len(redirected)} page(s) redirect and the cookie-less re-request was not"
            " made (verify_stateless=False): dependence on session state is unverified"
        )

    check.status = status
    check.details = {
        "redirected_pages": len(redirected),
        "max_hops_seen": max((p.redirect_hops for p in result.pages), default=0),
        "verified_stateless": verify_stateless,
    }
    return check


def check_session_urls(
    result: CrawlResult,
    *,
    verify_two_sessions: bool = False,
    sample: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
    requirement: str = "ADS-CRAWL-05",
) -> CheckResult:
    """ADS-CRAWL-05 — no session identifiers in URLs, canonical stable per session.

    Returns MISSING whenever the canonical half could not be observed, and there
    are three ways for that to happen: no HTML page was readable at all, no page
    declares a `<link rel="canonical">`, or the two-session comparison was not
    requested. Reporting OK for a comparison that was never made is exactly the
    failure mode this package exists to remove.
    """
    check = CheckResult(requirement, Status.OK)
    if result.status is Status.ERROR:
        check.status = Status.ERROR
        check.findings.append(result.stopped_reason or "crawl could not run")
        return check
    if not result.pages:
        check.status = Status.ERROR
        check.findings.append("no pages were fetched")
        return check

    status = Status.OK
    flagged: set[str] = set()
    for page in result.pages:
        # Only this site's own URLs. A `uid=` in an outbound link is the other
        # site's URL scheme, and failing the audited domain for it would be a
        # finding nobody can act on.
        candidates = [page.requested_url, page.final_url]
        candidates.extend(link for link in page.links if same_site(link, result.base_url))
        if page.canonical:
            candidates.append(page.canonical)
        for url in candidates:
            if has_session_id(url) and url not in flagged:
                flagged.add(url)
                status = escalate(status, Status.FAIL)
                check.findings.append(f"session identifier in URL: {url}")

    html_pages = result.html_pages
    with_canonical = [p for p in html_pages if p.canonical]
    if not html_pages:
        # Zero readable HTML pages is LESS observation than zero canonicals, not
        # more: gating the MISSING on `html_pages` non-empty made a site answering
        # 500 everywhere — or serving no HTML at all — pass this requirement
        # without a single comparison having been made.
        status = escalate(status, Status.MISSING)
        check.findings.append(
            "no readable HTML page was reached: nothing about this requirement "
            "could be observed"
        )
    elif not with_canonical:
        status = escalate(status, Status.MISSING)
        check.findings.append(
            "no page declares <link rel=\"canonical\">: the canonical/requested "
            "comparison this requirement asks for cannot be made"
        )
    for page in with_canonical:
        if normalize_url(page.canonical or "") != normalize_url(page.final_url):
            # Not a failure by itself — a canonical pointing elsewhere is normal
            # for filtered or tracked URLs. It is reported so a human can look.
            status = escalate(status, Status.INFO)
            check.findings.append(
                f"{page.final_url}: canonical points to {page.canonical}"
            )

    if verify_two_sessions:
        for page in with_canonical[:sample]:
            seen_canonicals = set()
            for _ in range(2):
                # A fresh Session per pass: two genuinely independent visitors.
                again = fetch(page.final_url, timeout=timeout, user_agent=DEFAULT_USER_AGENT)
                if again.error is not None or not again.text:
                    status = escalate(status, Status.ERROR)
                    check.findings.append(
                        f"{page.final_url}: re-request for canonical comparison failed"
                        f" ({again.error or 'empty body'})"
                    )
                    break
                parsed = parse_html(again.text)
                seen_canonicals.add(
                    normalize_url(urljoin(again.final_url, parsed.canonical))
                    if parsed.canonical
                    else ""
                )
            if len(seen_canonicals) > 1:
                status = escalate(status, Status.FAIL)
                check.findings.append(
                    f"{page.final_url}: canonical differs between two sessions "
                    f"({sorted(seen_canonicals)})"
                )
    elif with_canonical:
        # Same rule as the branch above: the network cost justifies the default,
        # it does not justify calling the unmeasured half satisfied.
        status = escalate(status, Status.MISSING)
        check.findings.append(
            f"{len(with_canonical)} page(s) declare a canonical and the two-session"
            " comparison was not made (verify_two_sessions=False): canonical"
            " stability across independent sessions is unverified"
        )

    check.status = status
    check.details = {
        "pages": len(result.pages),
        "with_canonical": len(with_canonical),
        "session_urls": sorted(flagged),
        "verified_two_sessions": verify_two_sessions,
    }
    return check
