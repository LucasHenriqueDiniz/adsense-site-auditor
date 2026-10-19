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

It also decodes the body once, for every caller (see `_decode`). That was done
in `text.py` alone, and the other readers of `Fetch.text` got requests'
ISO-8859-1 fallback: Portuguese placeholders and robots.txt rules turned to
mojibake and stopped matching anything.
"""

from __future__ import annotations

import codecs
import http.cookiejar
import re
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urldefrag, urljoin, urlsplit

import requests
from urllib3.util import Timeout as Urllib3Timeout

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

# Google's crawlers and fetchers read the first 15 MB of a file, uncompressed,
# and ignore the rest: "Overview of Google crawlers and fetchers", checked on
# 2026-10-19. Reading further cannot change what the AdSense crawler is served,
# and reading with no ceiling let one link exhaust the auditor: a crawl whose
# home page linked an extensionless 300 MB download read all of it and peaked at
# 1,382 MB of memory, measured the same day. 15 MB is taken as 15 MiB.
#
# It is the default, not a law of every fetch. A sitemap may be 50 MB under its
# own protocol, and cut at 15 MiB a valid one became "XML parse error: no
# element found" — the site blamed for a cut this client made — so a caller
# whose document has its own documented limit passes it (see `sitemap.py`).
MAX_BODY_BYTES = 15 * 1024 * 1024


def cookieless_session() -> requests.Session:
    """A session that reuses connections and keeps no cookies.

    One session per run saves a handshake per request, but a session is also a
    cookie jar: a cookie the home page set went out on robots.txt, on every
    sitemap and on every sampled URL. A crawler arrives without one. Measured on
    2026-10-19: a site whose sitemap URLs answer 403 to a visitor without the
    home page's consent cookie read `0 of 2 sampled URL(s) answered 200` with a
    session per request, and `2 of 2`, PASS, once the session was shared.
    """
    session = requests.Session()
    session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    return session


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
    # The body went past the limit `fetch` was given — `MAX_BODY_BYTES` unless
    # the caller said otherwise — and `text` holds only the part up to it.
    truncated: bool = False
    # Set when the header or the document names an encoding the bytes do not
    # honour. `text` is then what a browser would show, replacement characters
    # and all. Whether that costs a check anything depends on where the bad
    # bytes were (see `decoding_loss`).
    charset_error: str | None = None

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


# The longest wait this machine can represent. urllib3 hands a timeout to
# `socket.settimeout`, and CPython converts seconds to a signed 64-bit nanosecond
# count, so anything at or above 2**63 nanoseconds raises OverflowError rather
# than waiting. `time.sleep` shares the ceiling, which is why the crawler's delay
# is measured against it too. Bisected, not assumed: 9223372036.854774 is
# accepted and 9223372036.854776 is not.
MAX_WAIT_SECONDS = 2**63 / 1e9


def _reject_invalid_timeout(timeout: object) -> None:
    """Raise on a `timeout` no HTTP client here can use. Silent when it is fine.

    This exists so that `fetch` can catch ValueError around the whole request
    without swallowing the caller's own bug. urllib3 validates `timeout` deep
    inside the request — a plain `ValueError` from `timeout=0`, a non-number, or
    a tuple of the wrong length — and that is indistinguishable, at the `except`,
    from the ValueError a malformed URL raises. Checking first separates them by
    WHEN they happen instead of by type: anything left for the try block to catch
    is about a URL, because the argument was already proven usable.

    It runs urllib3's own validator rather than a hand-rolled `<= 0`, on the same
    shapes requests accepts, so "usable here" cannot drift from "usable there".
    urllib3 only refuses timeouts at or below zero, so the ceiling is checked
    here as well: `float("inf")` passed its validator and then raised
    OverflowError out of `socket.settimeout`, which is neither a ValueError this
    function can be said to have allowed through nor an error about a URL.
    """
    if isinstance(timeout, Urllib3Timeout):
        # Already urllib3's own object, so its constructor ran — but that only
        # refuses values at or below zero, and the ceiling and `nan` are ours to
        # catch. Reading the members back is the only way to reach them.
        _reject_unwaitable(timeout.connect_timeout)
        _reject_unwaitable(timeout.read_timeout)
        return
    if isinstance(timeout, tuple):
        try:
            connect, read = timeout
        except ValueError:
            raise ValueError(
                f"Invalid timeout {timeout!r}. Pass a (connect, read) timeout tuple, "
                "or a single float to set both timeouts to the same value."
            ) from None
        Urllib3Timeout(connect=connect, read=read)
        _reject_unwaitable(connect)
        _reject_unwaitable(read)
        return
    Urllib3Timeout(connect=timeout, read=timeout)
    _reject_unwaitable(timeout)


def _reject_unwaitable(seconds: object) -> None:
    """Raise on a duration `socket.settimeout` cannot represent.

    Spelled as `not 0 < x < ceiling` rather than `x >= ceiling`, because `nan`
    answers False to every comparison and so satisfies neither this bound nor
    urllib3's own `<= 0`. It reached `socket.settimeout`, which refuses it as a
    plain ValueError, and the clause around the request then reported a live
    site as a URL this client cannot parse — the wrong thing to send an operator
    to fix, and the exact failure the try block's ordering exists to prevent.
    """
    if seconds is None:
        return
    if not isinstance(seconds, (int, float)) or not 0 < seconds < MAX_WAIT_SECONDS:
        raise ValueError(
            f"Attempted to set a timeout of {seconds!r} seconds, but the timeout "
            f"must be a number above 0 and below {MAX_WAIT_SECONDS}."
        )


# HTML5 requires a document's encoding declaration inside the first 1024 bytes.
# Searching further would start matching prose — an article *about* encodings
# says "charset=utf-8" in its body without declaring anything.
_DECLARATION_WINDOW = 1024

# Both spellings HTML5 allows: the short <meta charset="utf-8"> and the legacy
# <meta http-equiv="Content-Type" content="text/html; charset=utf-8">. The value
# may be quoted with either quote character or left bare.
_META_CHARSET = re.compile(
    r"""<meta[^>]*?charset\s*=\s*["']?([A-Za-z0-9_:.+-]+)""",
    re.IGNORECASE,
)

# The only bodies a `<meta>` can declare anything for. A robots.txt or a sitemap
# that happens to contain the string is not declaring an encoding.
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# The byte-order marks the WHATWG Encoding Standard recognises. A BOM decides
# the encoding before the header or the document is asked: a page re-saved as
# UTF-8 with a BOM while still declaring windows-1252 is UTF-8 to a browser.
_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le"),
)

# Encoding names of the WHATWG standard that Python has no alias for.
_LABEL_ALIASES = {
    "iso-8859-8-i": "iso-8859-8",
    "x-mac-cyrillic": "mac-cyrillic",
    "windows-874": "cp874",
    "windows-31j": "cp932",
    "windows-949": "cp949",
}

# The encodings a web page can be in, keyed by Python's canonical name for a
# label, with the codec each is read as. The keys are the WHATWG Encoding
# Standard's encodings; anything else Python can decode is not a web encoding
# and a label naming it is ignored, as a browser ignores it. `undefined` is the
# reason this is an allowlist: a template rendering an unset variable writes
# `<meta charset="undefined">`, Python has a codec by that name that raises a
# bare UnicodeError on every byte, and it walked out of `_decode` into the
# URL-error clause of `fetch` — the page reported unreachable.
#
# The values carry the standard's own remapping: `iso-8859-1`, `latin1` and
# `ascii` are read as windows-1252, `iso-8859-9` as windows-1254, `tis-620` and
# `iso-8859-11` as windows-874, `gb2312` as GBK, `big5` as Big5-HKSCS,
# `shift_jis` as windows-31J and `euc-kr` as windows-949. Two encodings of the
# standard are left out: x-user-defined, which Python has no codec for, and
# `replacement`, which decodes nothing.
_WEB_ENCODINGS = {
    codecs.lookup(label).name: codec
    for label, codec in (
        ("utf-8", "utf-8"),
        ("ibm866", "cp866"),
        ("iso-8859-1", "cp1252"),
        ("ascii", "cp1252"),
        ("iso-8859-2", "iso8859-2"),
        ("iso-8859-3", "iso8859-3"),
        ("iso-8859-4", "iso8859-4"),
        ("iso-8859-5", "iso8859-5"),
        ("iso-8859-6", "iso8859-6"),
        ("iso-8859-7", "iso8859-7"),
        ("iso-8859-8", "iso8859-8"),
        ("iso-8859-9", "cp1254"),
        ("iso-8859-10", "iso8859-10"),
        ("iso-8859-11", "cp874"),
        ("tis-620", "cp874"),
        ("iso-8859-13", "iso8859-13"),
        ("iso-8859-14", "iso8859-14"),
        ("iso-8859-15", "iso8859-15"),
        ("iso-8859-16", "iso8859-16"),
        ("koi8-r", "koi8-r"),
        ("koi8-u", "koi8-u"),
        ("macintosh", "mac-roman"),
        ("mac-cyrillic", "mac-cyrillic"),
        ("cp874", "cp874"),
        ("windows-1250", "cp1250"),
        ("windows-1251", "cp1251"),
        ("windows-1252", "cp1252"),
        ("windows-1253", "cp1253"),
        ("windows-1254", "cp1254"),
        ("windows-1255", "cp1255"),
        ("windows-1256", "cp1256"),
        ("windows-1257", "cp1257"),
        ("windows-1258", "cp1258"),
        ("gbk", "gbk"),
        ("gb2312", "gbk"),
        ("gb18030", "gb18030"),
        ("big5", "big5hkscs"),
        ("big5-hkscs", "big5hkscs"),
        ("euc-jp", "euc_jp"),
        ("iso-2022-jp", "iso2022_jp"),
        ("shift_jis", "cp932"),
        ("cp932", "cp932"),
        ("euc-kr", "cp949"),
        ("cp949", "cp949"),
        ("utf-16", "utf-16-le"),
        ("utf-16le", "utf-16-le"),
        ("utf-16be", "utf-16-be"),
    )
}


def _windows_1252_table() -> str:
    """Python's cp1252 decoding table, with each byte it leaves undefined mapped
    to the code point of the same number, which is what WHATWG specifies."""
    chars = []
    for byte in range(256):
        try:
            chars.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            chars.append(chr(byte))
    return "".join(chars)


# WHATWG's windows-1252. Python's cp1252 leaves 0x81, 0x8D, 0x8F, 0x90 and 0x9D
# undefined, so strict decoding failed on bytes every browser reads. A UTF-8 "Á"
# is C3 81: a UTF-8 page served as iso-8859-1 was refused when it said "Área"
# and read as mojibake when it did not, measured on 2026-10-19 — the verdict
# depended on which letters the page happened to contain.
_WINDOWS_1252 = _windows_1252_table()


def _decode_as(raw: bytes, codec: str, errors: str = "strict") -> str:
    """`raw.decode(codec, errors)`, with windows-1252 read as the standard reads it."""
    if codec == "cp1252":
        return codecs.charmap_decode(raw, errors, _WINDOWS_1252)[0]
    return raw.decode(codec, errors)


def _web_encoding(label: str) -> str:
    """The codec a web page labelled `label` is read with, or "" for none.

    "" for an empty label, one Python does not know, and one Python knows that
    is not an encoding a page can be in.
    """
    label = label.strip().lower()
    if not label:
        return ""
    try:
        name = codecs.lookup(_LABEL_ALIASES.get(label, label)).name
    except LookupError:
        return ""
    return _WEB_ENCODINGS.get(name, "")


def _header_charset(content_type: str) -> str:
    """The charset parameter of a Content-Type, lowercased. Empty when absent."""
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip("\"'").lower()
    return ""


def _declared_charset(text: str) -> str:
    """The encoding an HTML document declares about itself, lowercased.

    Matches `<meta charset=...>` and the `charset=` inside a `<meta
    http-equiv="Content-Type">`, which is where HTML5 says to look when the
    transport does not say. Only the head is searched: HTML5 requires the
    declaration in the first 1024 bytes, and a `charset=` further down is more
    likely to be prose about encodings than a declaration.
    """
    match = _META_CHARSET.search(text[:_DECLARATION_WINDOW])
    return match.group(1).lower() if match else ""


def _read_body(resp: requests.Response, max_bytes: int) -> tuple[bytes, bool]:
    """The body, uncompressed, up to `max_bytes`; and whether it went past.

    Read in chunks off a streamed response, so the bytes past the ceiling are
    never downloaded, let alone decoded.
    """
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            break
    body = b"".join(chunks)
    return body[:max_bytes], size > max_bytes


def _drop_redirect_body(
    resp: requests.Response, *args: object, **kwargs: object
) -> requests.Response:
    """Close a redirect without reading its body. A `response` hook of `fetch`.

    With `allow_redirects=True`, requests reads the whole body of every 3xx
    before following it — `Session.resolve_redirects` touches `resp.content` to
    release the connection — so the ceiling `_read_body` puts on the final
    response never reached a hop. Measured on 2026-10-19: a 301 carrying 200 MB
    before a small page peaked at 609 MB. Nothing here reads a redirect's body,
    so the hook marks it read and empty and closes the connection, and the bytes
    stay on the server.

    `_content` and `_content_consumed` are requests' own attributes: setting
    them is how `resp.content` is made to return without touching the socket.
    The test that pins this measures the memory, so a requests release that
    reads the body some other way fails it rather than going unnoticed.
    """
    if resp.is_redirect:
        # Close first. `Response.close` closes the socket only while the body is
        # unread, and otherwise hands the connection back to the pool — with the
        # rest of the 3xx body still arriving on it, for the next hop to read as
        # its response.
        resp.close()
        resp._content = b""
        resp._content_consumed = True
    return resp


def _decode(raw: bytes, content_type: str) -> tuple[str, str | None]:
    """The body as text, and why it could not be read as declared, when it could not.

    requests reads a `text/*` body with no charset parameter as ISO-8859-1:
    RFC 2616's default, which HTML5 replaced with "read the document's own
    declaration". The damage is not cosmetic, and it is not confined to word
    counts:

      * "coração" arrives as "coraÃ§Ã£o", which `text._WORD` reads as three
        words, so a 240-word page measured 480 and cleared a bar it should
        have missed;
      * "em construção" arrives as "em construÃ§Ã£o", and the completeness
        check's placeholder markers stopped matching: measured on 2026-10-19, a
        home page saying "em construção" and "será adicionado" read WARN with
        `charset=utf-8` in the header and PASS without it, exit 0;
      * robots.txt is served as `text/plain` with no charset by nginx's default,
        and `Disallow: /promoção/` normalized to a path no URL has, so the
        AdSense crawler was allowed into a directory the site had closed to it.

    So the bytes are decoded here, once, for every caller, in the order of the
    HTML5 encoding sniffing algorithm:

      1. a byte-order mark, which beats everything after it;
      2. the header's charset, when it names an encoding a page can be in;
      3. for an HTML document, its own `<meta>` declaration in the first 1024
         bytes, with a UTF-16 label read as UTF-8, as HTML5 says to;
      4. UTF-8, when the body decodes strictly as UTF-8 — a multi-byte sequence
         that decodes strictly is UTF-8 in every document not built to fool
         this check;
      5. otherwise windows-1252, the WHATWG default: a genuine legacy page
         declaring nothing, where every Latin-1 letter is still that letter.

    Step 5 used to be requests' own reading, which is a different thing from
    one call to the next: ISO-8859-1 for a `text/*` body, a statistical guess
    for any other, and UTF-8 with replacement characters when the header named
    a label `_web_encoding` had just skipped — so a Latin-1 page under
    `charset=utf8mb4` read "construção" as "constru\ufffd\ufffdo".

    A label that names no web encoding is skipped, as a browser skips it (see
    `_WEB_ENCODINGS`). An encoding that is declared, in the header or in the
    `<meta>`, and that the bytes do not honour is read as a browser reads it,
    with replacement characters, and reported in the second value: the text is
    what a visitor sees, not what the page was written to say. A caller that
    turns text into a verdict refuses the part it reads when that part lost a
    character (see `decoding_loss`). That is the commonest mislabel
    there is — a legacy Latin-1 page under a server-wide `charset=utf-8` — and
    it lost "em construção" to "em constru\ufffd\ufffdo" without a word, where
    the same failure declared in a `<meta>` was refused. A BOM is the encoding
    rather than a declaration of it, and is decoded with replacement silently.
    """
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            return raw[len(bom) :].decode(codec, "replace"), None
    label = _header_charset(content_type)
    declared = _web_encoding(label)
    source = "header"
    if not declared and content_type.split(";")[0].strip().lower() in _HTML_MEDIA_TYPES:
        label = _declared_charset(raw[:_DECLARATION_WINDOW].decode("iso-8859-1"))
        declared = _web_encoding(label)
        source = "declared"
        if declared in ("utf-16-le", "utf-16-be"):
            # A page that reached a `<meta>` prescan is ASCII-compatible, so
            # a UTF-16 label in it is a mistake, and HTML5 reads it as UTF-8.
            declared = "utf-8"
    if declared:
        try:
            return _decode_as(raw, declared), None
        except UnicodeDecodeError:
            return (
                _decode_as(raw, declared, "replace"),
                f"{source} charset {label!r} does not decode the body",
            )
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return _decode_as(raw, "cp1252"), None


def decoding_loss(response: Fetch, text: str) -> str | None:
    """`response.charset_error` when it cost `text` a character, else None.

    `text` is what one check reads off the response: the visible text, the main
    content, the extracted page. A declared encoding that does not decode the
    bytes leaves replacement characters where the bad bytes were, and only a
    check whose own text carries one has lost anything. Deciding on the bytes
    refused too much: one Latin-1 byte inside an inline `<script>`, under
    `charset=utf-8`, made check_completeness report a home page unreadable whose
    visible text decoded cleanly and said "em construção", measured on
    2026-10-19.
    """
    if response.charset_error is not None and "\ufffd" in text:
        return response.charset_error
    return None


def fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    user_agent: str = ADSENSE_UA,
    method: str = "GET",
    max_bytes: int = MAX_BODY_BYTES,
) -> Fetch:
    """GET a URL following redirects. Never raises; failures come back as ERROR.

    "Never raises" is about the network and about URLs — the things a stranger
    controls. A `timeout` this client cannot use is the caller's own bug and is
    still raised, at the top, before any request goes out.

    HEAD is available via method="HEAD" but falls back to GET when the server
    answers 4xx/5xx to it, because a server refusing HEAD says nothing about the
    page.
    """
    if not split_url(url).netloc:
        # Answered here rather than by the try block below, because here we can
        # name the offender: at this point `url` IS the URL that failed to parse.
        # Once the request is in flight that stops being true — see the clause at
        # the bottom, which is reached by redirect targets and proxy hosts too.
        result = Fetch(url=url)
        result.error = f"InvalidURL: {url!r} has no host this client can parse"
        return result
    _reject_invalid_timeout(timeout)
    sess = session or requests.Session()
    # HTTP methods are case-sensitive on the wire but the caller's spelling is not
    # a verdict: `method="head"` used to miss the fallback below and return the
    # server's 405 as the answer.
    method = method.upper()
    result = Fetch(url=url)
    try:
        # Streamed, so the body is read by `_read_body` and only up to the
        # ceiling a Google crawler reads.
        resp = sess.request(
            method,
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": user_agent},
            stream=True,
            hooks={"response": _drop_redirect_body},
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
            resp.close()
            resp = sess.request(
                "GET",
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": user_agent},
                stream=True,
                hooks={"response": _drop_redirect_body},
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
        try:
            if answered_with != "HEAD":
                raw, result.truncated = _read_body(resp, max_bytes)
                result.text, result.charset_error = _decode(
                    raw, resp.headers.get("content-type", "")
                )
        finally:
            # A streamed response holds its connection until it is closed, and
            # one cut at the ceiling still has bytes on the wire.
            resp.close()
    # Order matters, and this clause has to stay second: requests' own InvalidURL,
    # MissingSchema and InvalidSchema are BOTH a RequestException and a ValueError,
    # and they already carry a precise message of their own.
    except requests.RequestException as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    except ValueError as exc:
        # The bad-URL defect of the netloc guard above, one layer lower: a URL
        # that got past `urlsplit` and past requests' own prepare step, and blew
        # up in flight. Two known ways in, neither of them a RequestException,
        # so both used to walk straight out of `fetch` and break the never-raises
        # contract:
        #
        #   * A DNS label of 64 characters or more, or an empty one as in
        #     `a..example`. urllib3's `create_connection` runs `host.encode("idna")`
        #     at connect time and raises LocationParseError. It reaches here from
        #     the caller's URL, from a redirect target, and from a host in
        #     HTTP_PROXY.
        #   * A redirect whose `Location` names a bracketed host that is not a
        #     valid IPv6 literal — `http://[foo]/`, `http://[]/`, `http://[1.2.3.4]/`.
        #     requests' `resolve_redirects` calls `urlparse` on the redirect target
        #     with no guard, and CPython's bracketed-host check raises a PLAIN
        #     ValueError. That is stdlib, so no dependency pin fences it off.
        #
        # Hence ValueError rather than either exception class: the second one has
        # no class of its own to catch, and enumerating stdlib parse failures is a
        # game this file has already lost three times — see `split_url`,
        # `join_url` and `defrag_url`, which are the same sweep made in the same
        # file for the same reason.
        #
        # A blanket `except ValueError` here USED to be a bug of its own: it also
        # swallowed the plain ValueError urllib3 raises for `timeout=0` or a bad
        # timeout tuple, handing a caller's bug back as "the site could not be
        # reached" about a site that was up the whole time. That timeout is now
        # validated before the try, so nothing this clause can catch is about the
        # arguments any more.
        #
        # Reported as InvalidURL and not as a network failure: the host is not
        # unreachable, it is one this client cannot pronounce, and that is a
        # finding about a URL. But NOT in the netloc guard's words — the URL that
        # failed to parse is often not `url`. On a redirect it is hop two, and
        # saying `'http://site/mapa.xml' has no host this client can parse` about
        # a URL that parses perfectly sends the operator to fix the wrong thing.
        # The offender is named by `{exc}`; `url` is named as what was requested.
        result.error = (
            f"InvalidURL: fetching {url!r} reached a URL this client cannot parse ({exc})"
        )
    return result
