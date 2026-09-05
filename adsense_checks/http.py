"""One fetch path for every check, with the redirect handling done once.

Four separate defects in the original scripts were all the same mistake made in
different places — asking a question of the wrong response:

  * `session.head(url)` was used for security headers. requests defaults HEAD to
    `allow_redirects=False`, so on any site redirecting apex to www the headers
    inspected were the 301's, and all four security headers were reported
    missing. Most sites redirect, so most sites got four false positives.
  * A server answering 405 to HEAD — ordinary behind a WAF — was reported as a
    critical redirect failure, in the same report where the uptime check said
    the site returned 200 to GET.
  * HTTPS was judged by `url.startswith("https://")`, the string the caller
    passed in, not the scheme actually served. Passing the http:// form of an
    HTTPS-only site reported "does not use HTTPS"; a site redirecting from
    https to http reported OK.
  * Redirect health and uptime each did their own request, so the two could
    disagree inside one report.

`fetch` answers all of them from a single GET that follows redirects, and keeps
the hop chain so callers can inspect it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import SplitResult, urldefrag, urljoin, urlsplit

import requests

from adsense_checks.status import Status

# Carrying the `Mediapartners-Google` token is the point: this audit asks what
# the AdSense crawler would be served, and sites do serve that token differently.
#
# The tool names itself alongside it, and the `+` URL points here rather than at
# Google's bot page. The string used to be a byte-for-byte copy of Google's own,
# which was arguable while this crawler still stopped wherever `*` said stop —
# and stopped being arguable once it began walking through a `Disallow: /` on the
# strength of being Mediapartners-Google. A site owner reading their log can see
# who actually called.
ADSENSE_UA = (
    "Mozilla/5.0 (compatible; Mediapartners-Google/2.1; adsense-site-auditor/0.1; "
    "+https://github.com/LucasHenriqueDiniz/adsense-site-auditor)"
)

# Seconds, and a fractional one is fine. Every `timeout` in the package is
# annotated `float` because that is what requests accepts; the annotation said
# `int` while a test was already passing 0.1 to bound a deliberately slow route.
DEFAULT_TIMEOUT = 15

_UNPARSEABLE = SplitResult("", "", "", "", "")


def split_url(url: str) -> SplitResult:
    """`urlsplit` that never raises. What it cannot parse comes back empty.

    `urlsplit("http://[::1:99999]/")` raises ValueError, and ValueError is not a
    RequestException, so it walked straight out of `fetch` and out of every URL
    helper in the package: four of the five CLIs died on it. Two of them needed
    nothing more than ONE malformed href on the audited page — the operator's own
    URL was fine.

    An unparseable URL comes back as an all-empty result, which every caller here
    already handles: no scheme and no netloc reads as "not a URL I can use", which
    is exactly what it is.
    """
    try:
        return urlsplit(url)
    except ValueError:
        return _UNPARSEABLE


def join_url(base: str, ref: str) -> str:
    """`urljoin` that never raises. Returns "" when either side is unparseable.

    Note `urljoin` short-circuits an empty base and hands `ref` straight back
    WITHOUT parsing it, so a "" return here does not mean `ref` is safe to parse.
    """
    try:
        return urljoin(base, ref)
    except ValueError:
        return ""


def defrag_url(url: str) -> str:
    """`urldefrag` that never raises. Returns "" when the URL cannot be parsed.

    `urldefrag` parses, so it raises the same bracketed-host ValueError as the
    rest — and it was the crash that survived the first sweep, twice: once in
    `normalize_url`, the single identity function, and once in `_resolve_links`,
    which reaches it with a raw href whenever a `<base href>` failed to join.
    """
    try:
        return urldefrag(url)[0]
    except ValueError:
        return ""


@dataclass
class Fetch:
    """The result of one request, after redirects."""

    url: str
    final_url: str = ""
    status_code: int | None = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    redirect_chain: list[tuple[int, str]] = field(default_factory=list)
    # Wall time of the WHOLE chain, redirect hops included. `resp.elapsed` alone
    # times the final request only, so on a site sending apex to www — most of
    # the web — a two-hop answer was reported as the duration of its last hop.
    elapsed_ms: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 400

    @property
    def final_scheme(self) -> str:
        return split_url(self.final_url or self.url).scheme

    @property
    def is_https(self) -> bool:
        """Whether the response actually arrived over TLS, regardless of input."""
        return self.final_scheme == "https"

    @property
    def downgraded_to_http(self) -> bool:
        """True when the chain started on https and ended on http."""
        if not self.redirect_chain:
            return False
        started_secure = split_url(self.url).scheme == "https"
        return started_secure and not self.is_https

    @property
    def status(self) -> Status:
        if self.error is not None:
            return Status.ERROR
        if self.status_code is None:
            return Status.ERROR
        # `>= 500` and `> 500` decide the same thing: 500 falls through to the
        # `>= 400` below and comes back FAIL either way. Kept for the reader,
        # who should not have to trace two branches to learn that a 500 fails.
        if self.status_code >= 500:
            return Status.FAIL
        if self.status_code == 404:
            return Status.MISSING
        if self.status_code >= 400:
            return Status.FAIL
        return Status.OK


def fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    user_agent: str = ADSENSE_UA,
    method: str = "GET",
) -> Fetch:
    """GET a URL following redirects. Never raises; failures come back as ERROR.

    HEAD is available via method="HEAD" but falls back to GET when the server
    answers 4xx/5xx to it, because a server refusing HEAD says nothing about the
    page.
    """
    if not split_url(url).netloc:
        # The bracketed-host ValueError, caught here instead of around the whole
        # request: `except ValueError` also swallowed `timeout=0` and a bad
        # timeout tuple, so a caller's bug came back as "the site could not be
        # reached" about a site that was up the whole time.
        result = Fetch(url=url)
        result.error = f"InvalidURL: {url!r} has no host this client can parse"
        return result
    sess = session or requests.Session()
    # HTTP methods are case-sensitive on the wire but the caller's spelling is not
    # a verdict: `method="head"` used to miss the fallback below and return the
    # server's 405 as the answer.
    method = method.upper()
    result = Fetch(url=url)
    try:
        resp = sess.request(
            method,
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        # Which method actually produced `resp`. Tracking it is the point: the
        # body guard below used to test the method REQUESTED, so the GET issued
        # here had its body thrown away and a WAF answering 405 to HEAD came back
        # as 200 with an empty document — indistinguishable from an empty page.
        answered_with = method
        # The refused HEAD cost real time; dropping it understated the chain.
        refused_head = None
        if method == "HEAD" and resp.status_code >= 400:
            refused_head = resp.elapsed
            resp = sess.request(
                "GET",
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": user_agent},
            )
            answered_with = "GET"
        result.final_url = resp.url
        result.status_code = resp.status_code
        result.headers = {k.lower(): v for k, v in resp.headers.items()}
        result.redirect_chain = [(r.status_code, r.url) for r in resp.history]
        # Every hop, guarded the same way. Guarding the history and not the final
        # response left an AttributeError path that is not a RequestException, so
        # it would escape `fetch` and break the "never raises" contract above.
        timings = [r.elapsed for r in (*resp.history, resp)] + [refused_head]
        result.elapsed_ms = sum(t.total_seconds() for t in timings if t is not None) * 1000
        # A real HEAD response has no body; guard so callers never see a stale
        # one. A GET that replaced a refused HEAD does have one, and keeping it
        # is the whole reason for the fallback.
        result.text = "" if answered_with == "HEAD" else resp.text
    except requests.RequestException as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result
