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
from urllib.parse import urlparse

import requests

from adsense_checks.status import Status

# Identifying as the AdSense crawler is the point: this audit asks what
# Mediapartners-Google would see, and sites do serve it differently.
ADSENSE_UA = "Mozilla/5.0 (compatible; Mediapartners-Google/2.1; +http://www.google.com/bot.html)"

DEFAULT_TIMEOUT = 15


@dataclass
class Fetch:
    """The result of one request, after redirects."""

    url: str
    final_url: str = ""
    status_code: int | None = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    redirect_chain: list[tuple[int, str]] = field(default_factory=list)
    elapsed_ms: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 400

    @property
    def final_scheme(self) -> str:
        return urlparse(self.final_url or self.url).scheme

    @property
    def is_https(self) -> bool:
        """Whether the response actually arrived over TLS, regardless of input."""
        return self.final_scheme == "https"

    @property
    def downgraded_to_http(self) -> bool:
        """True when the chain started on https and ended on http."""
        if not self.redirect_chain:
            return False
        started_secure = urlparse(self.url).scheme == "https"
        return started_secure and not self.is_https

    @property
    def status(self) -> Status:
        if self.error is not None:
            return Status.ERROR
        if self.status_code is None:
            return Status.ERROR
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
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    user_agent: str = ADSENSE_UA,
    method: str = "GET",
) -> Fetch:
    """GET a URL following redirects. Never raises; failures come back as ERROR.

    HEAD is available via method="HEAD" but falls back to GET when the server
    answers 4xx/5xx to it, because a server refusing HEAD says nothing about the
    page.
    """
    sess = session or requests.Session()
    result = Fetch(url=url)
    try:
        resp = sess.request(
            method,
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        if method == "HEAD" and resp.status_code >= 400:
            resp = sess.request(
                "GET",
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": user_agent},
            )
        result.final_url = resp.url
        result.status_code = resp.status_code
        result.headers = {k.lower(): v for k, v in resp.headers.items()}
        result.redirect_chain = [(r.status_code, r.url) for r in resp.history]
        result.elapsed_ms = resp.elapsed.total_seconds() * 1000
        # HEAD responses have no body; guard so callers never see a stale one.
        result.text = resp.text if method != "HEAD" else ""
    except requests.RequestException as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result
