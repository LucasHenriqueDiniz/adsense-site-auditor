"""Unfinished-site detection and the trust pages AdSense expects to find.

Covers ADS-COMPLETE-01 (unfinished markers plus navigation links leading
nowhere), ADS-UX-05 (the About/Contact pages answer 200 and are not stubs) and
part of ADS-AUTHOR-02 (at least one contact channel exists in the HTML).

"Leading nowhere" is not the same question as "answers 4xx". A CMS serving its
"page not found" template with HTTP 200 — the soft 404 — used to make a menu of
dead links report `broken 0`, and `crawl._load_robots` already refuses to read
an HTML body as robots rules for exactly this reason. `_probe_not_found` asks
the host once what it serves for a URL that cannot exist, and the answer decides
what a 200 from that host is worth at all.

What the probe is allowed to decide is deliberately narrow. On a host that
answers a success code for a URL that cannot exist, no 200 is evidence about the
page behind it — in either direction — so every such link is reported as
unverified, never as broken. Recognising the not-found page in a link's response
sharpens the sentence the report prints; it does not turn the verdict into a
failure. The reason is that "this response is byte-identical to the page served
for nothing" is a true description of a dead link AND of several live ones: a
WordPress empty category, empty tag and out-of-range page number all render the
same `content-none.php` partial the 404 template renders, and on a catch-all
that answers with the home page, `/index.php` is a real route. `_is_home_again`
uses that same equality to reject a candidate and keep looking — an exclusion.
Reusing an exclusion rule as an accusation is what produced `FAIL, count 3` on
working sites.

Two defects of the previous implementation are made structurally impossible
here rather than patched in place:

  * The WARNING it produced for a home page reading "Coming soon" reached
    neither sum in the summary, so the run ended with "Completeness checks
    passed" printed directly under the problem it had just listed. No report in
    this module stores a status. `status` is derived from the findings that were
    actually recorded, so a recorded finding cannot fail to move the verdict,
    and there is no second list of statuses to keep in sync.
  * The `else` branch for /about and /contact wrote `"ERROR (503)"` into details
    and appended nothing to issues, so a page behind a 500, a 403 or a timeout
    was indistinguishable from a page that passed. Every branch here that failed
    to observe a page records Status.ERROR, which is the most severe status
    there is. A check that could not look never reports a pass.

Deliberately not decided here, because none of it is decidable from the HTML:

  * whether the About page is truthful, or the person named on it exists;
  * whether a contact channel actually reaches anyone — ADS-AUTHOR-02 also asks
    that the mailto address, the form action and the social profile resolve, and
    only the social profile could be probed without sending mail or POSTing;
  * whether a placeholder phrase is the page's own state or its subject matter.
    See `find_placeholders` for exactly how far the block heuristic goes.

On the parser: `adsense_checks.text` already extracts text, and this module does
not use it for that, because the questions here are structural — which block a
phrase sits in, which region a link comes from, whether a form has fields a
human could type into — and the depth extractor deliberately flattens all three
away. What is reused is its `looks_javascript_rendered`, so that a client-
rendered shell is called what it is instead of being guessed at twice.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlunsplit

import requests

from adsense_checks.crawl import (
    keep_first_base_href,
    looks_like_document,
    normalize_url,
    resolve_base,
    same_site,
    strip_userinfo,
)
from adsense_checks.http import (
    DEFAULT_TIMEOUT,
    Fetch,
    defrag_url,
    fetch,
    join_url,
    split_url,
)
from adsense_checks.status import Status, escalate, worst
from adsense_checks.text import looks_javascript_rendered

# --------------------------------------------------------------------------
# Tunables. Every threshold in this module is named and stated here rather than
# inlined, because a deterministic check whose limit is invisible is not
# reproducible by the person reading its output.
# --------------------------------------------------------------------------

# A block at or under this many words is treated as "short": a heading, a card,
# a button, a list item. The distinction carries the whole placeholder
# heuristic, so it is deliberately generous.
SHORT_BLOCK_WORDS = 25

# Markers that are also ordinary words are only accepted when little else shares
# their block ("TODO: write this" qualifies, "todo mundo sabe" does not).
ALONE_SLACK_WORDS = 3

# ADS-UX-05 wants a word count above a minimum. It is low on purpose: a real
# contact page is often a form, an address and one sentence.
MIN_TRUST_PAGE_WORDS = 25

# How many of the home page's own linked PAGES may be probed per trust page —
# identities, not hrefs, so the worst case is this many times
# `MAX_SPELLINGS_PER_IDENTITY` requests. The
# conventional paths (`ABOUT_PATHS`, `CONTACT_PATHS`) are always tried in full
# and are never counted against this budget: a path this module declares and
# then never requests turns "No About page found" into a claim about a URL
# nobody looked at. A single total cap did exactly that — it cut the list at six
# and `pages/about`, the Shopify convention, was the seventh.
MAX_LINKED_CANDIDATES = 6

# How many spellings of ONE page may be requested. `MAX_LINKED_CANDIDATES` is
# charged per identity, so `/about` beside `/about/` costs one slot and both
# still go on the wire — a host serving only the second answers 404 to the
# first. That discount has to be bounded or it is not a budget: `_canonical`
# folds the trailing slash with `rstrip`, so `/about//`, `/about///` and two
# hundred more are all one identity, and a footer holding them made the auditor
# fire 216 requests, 200 of them at the same page. Two is the whole discount,
# because two is how many spellings of a path a site actually writes.
MAX_SPELLINGS_PER_IDENTITY = 2

DEFAULT_NAV_LINK_LIMIT = 25

# One broken link in the navigation is a defect; three reads as an abandoned
# site. The number is arbitrary but fixed, which is what makes it reproducible —
# and only observed 4xx/5xx are counted against it. Nothing inferred from a
# host's not-found page reaches this threshold, because an inference that can
# cross it turns a WordPress site with an empty category, an empty tag and a
# page 7 of 6 into "abandoned".
BROKEN_NAV_FAIL_THRESHOLD = 3

# The paths the soft-404 probe asks for, joined onto the audited base so a
# subdirectory install is probed inside its own install rather than at the apex.
# Fixed rather than random, so the operator can curl the same URL and check the
# claim the report makes about their host. They differ in length and in shape on
# purpose: a not-found template that echoes the requested address answers them
# with different text, and telling those two hosts apart is the whole job of
# `_probe_not_found`. The second is requested only against a host whose answer
# to the first already proved it serves 200 for pages it does not have.
NOT_FOUND_PROBE_PATHS = (
    "adsense-auditor-probe-no-such-page",
    "adsense-auditor-probe-nn-9x7",
)

_SNIPPET_CHARS = 140


# --------------------------------------------------------------------------
# Text folding
# --------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lowercase and strip accents, so 'em construção' and 'em construcao' match.

    Sites misspell their own placeholder text constantly, and the audit is not a
    spelling test. Folding is applied to the matching copy only; every snippet
    reported back to the caller keeps the original characters.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _words(text: str) -> int:
    return len(text.split())


def _snippet(text: str) -> str:
    return text if len(text) <= _SNIPPET_CHARS else text[: _SNIPPET_CHARS - 3] + "..."


# --------------------------------------------------------------------------
# HTML parsing
# --------------------------------------------------------------------------

# Their content is not page text. `head` is NOT in this set on purpose: html.parser
# is not a tree builder, so a document that omits `</head>` — or omits `<body>`,
# which is legal — would suppress the entire document. That is the exact shape of
# the old defect, where text was only collected between a literal <body> and
# </body> and a valid page without those tags extracted to the empty string.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "title", "svg"})

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hr", "legend", "li", "main",
        "nav", "ol", "p", "pre", "section", "summary", "table", "td", "th", "tr", "ul",
    }
)

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "summary", "legend"})

# Containers that can define which region of the page a link sits in.
_REGION_TAGS = frozenset({"footer", "nav", "header", "div", "section", "aside", "main", "ul", "ol"})

Region = Literal["footer", "nav", "body"]


@dataclass(frozen=True)
class Block:
    """One run of text delimited by block-level tags."""

    tag: str
    text: str

    @property
    def words(self) -> int:
        return _words(self.text)

    @property
    def is_short(self) -> bool:
        """Whether the block is small enough that a marker dominates it."""
        return self.tag in _HEADING_TAGS or self.words <= SHORT_BLOCK_WORDS


@dataclass(frozen=True)
class Link:
    href: str
    text: str
    region: Region


# Words that make a form say, in its own action/id/class, that it is for writing
# to the publisher. Label-anchored, like every other pattern in this module.
_CONTACT_FORM_RE = re.compile(
    r"(?<!\w)(contact|contato|fale[\s_-]*conosco|mensagem|message)(?!\w)"
)


@dataclass
class Form:
    action: str = ""
    fields: int = 0
    has_textarea: bool = False
    looks_like_search: bool = False
    looks_like_contact: bool = False

    @property
    def is_contact_shaped(self) -> bool:
        """A form a human could write a message into, as opposed to a search box.

        The message box is the signal, and `has_textarea` is why it is collected:
        "one field and a submit button" is what a newsletter subscription, a
        login and a site search all look like, so accepting any non-search form
        made a login box proof of a contact channel and a contact page offering
        nothing at all passed as OK.

        A form without a textarea therefore only counts when it names itself a
        contact form. The cost is stated rather than hidden: a real contact form
        built from plain inputs posting to a neutral action ("/enviar") is not
        recognised, and the page is reported as offering no channel. That is the
        safe direction — a false MISSING asks a human to look, a false OK is the
        defect this module exists to make impossible.
        """
        if self.looks_like_search or self.fields == 0:
            return False
        return self.has_textarea or self.looks_like_contact


@dataclass(frozen=True)
class Document:
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)
    # As written in the markup, unresolved. Every href above is relative to it,
    # and `crawl.resolve_base` is the one place that turns the pair into a base:
    # storing an already-resolved base here would need the document's URL, which
    # `parse_document` does not have and `find_placeholders` never will.
    base_href: str | None = None
    # Set when parsing raised. Callers must degrade rather than report a pass on
    # a document they only half read.
    parse_error: str | None = None

    @property
    def text(self) -> str:
        return " ".join(b.text for b in self.blocks)

    @property
    def words(self) -> int:
        return _words(self.text)


@dataclass
class _PendingAnchor:
    href: str
    parts: list[str]
    region: Region


class _DocumentParser(HTMLParser):
    """Collects text blocks, links and forms in one pass over the markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.links: list[Link] = []
        self.forms: list[Form] = []
        self.base_href: str | None = None
        self._skip = 0
        self._buffer: list[str] = []
        self._tag = "body"
        self._stack: list[tuple[str, Region]] = []
        self._anchor: _PendingAnchor | None = None
        self._form: Form | None = None

    # -- regions ----------------------------------------------------------

    def _region(self) -> Region:
        return self._stack[-1][1] if self._stack else "body"

    def _region_for(self, tag: str, attrs: dict[str, str]) -> Region:
        if tag == "footer":
            return "footer"
        if tag in ("nav", "header"):
            return "nav"
        ident = fold(f"{attrs.get('class', '')} {attrs.get('id', '')} {attrs.get('role', '')}")
        if "footer" in ident or "rodape" in ident:
            return "footer"
        if "nav" in ident or "menu" in ident:
            return "nav"
        return self._region()

    def _pop_region(self, tag: str) -> None:
        # Pop by tag name, not by depth: unclosed inline markup is the norm, and
        # depth counting would leave every later link mislabelled after one
        # stray <div>.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    # -- buffers ----------------------------------------------------------

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer.clear()
        if text:
            self.blocks.append(Block(tag=self._tag, text=text))

    def _flush_anchor(self) -> None:
        if self._anchor is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._anchor.parts)).strip()
        self.links.append(Link(href=self._anchor.href, text=text, region=self._anchor.region))
        self._anchor = None

    # -- HTMLParser hooks -------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base":
            # Ahead of the skip counter, and taken even inside a skipped subtree,
            # because `crawl.PageParser` reads it that way: this module already
            # disagreed with the crawler about one document's links once, and
            # skipping a `<base>` the crawler keeps would be that same defect
            # rebuilt out of a difference nobody would think to look for. Which
            # `<base>` wins is `keep_first_base_href`'s call, not a second
            # opinion written here.
            href = {k.lower(): (v or "") for k, v in attrs}.get("href", "")
            self.base_href = keep_first_base_href(self.base_href, href)
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag in _REGION_TAGS:
            self._stack.append((tag, self._region_for(tag, values)))
        if tag in _BLOCK_TAGS:
            self._flush()
            self._tag = tag
        if tag == "a":
            href = values.get("href", "").strip()
            if href:
                self._flush_anchor()  # an unterminated previous <a> still counts
                self._anchor = _PendingAnchor(href=href, parts=[], region=self._region())
        elif tag == "form":
            ident = fold(
                f"{values.get('action', '')} {values.get('id', '')} {values.get('class', '')}"
            )
            self._form = Form(
                action=values.get("action", "").strip(),
                looks_like_search=any(w in ident for w in ("search", "busca", "pesquisa")),
                looks_like_contact=_CONTACT_FORM_RE.search(ident) is not None,
            )
            self.forms.append(self._form)
        elif tag in ("input", "textarea", "select"):
            self._record_field(tag, values)

    def _record_field(self, tag: str, values: dict[str, str]) -> None:
        if self._form is None:
            return
        kind = fold(values.get("type", "text"))
        ident = fold(f"{values.get('name', '')} {values.get('id', '')}")
        if tag == "textarea":
            self._form.has_textarea = True
        if kind in ("hidden", "submit", "button", "image", "reset"):
            return
        if kind == "search" or "search" in ident or "busca" in ident:
            self._form.looks_like_search = True
            return
        self._form.fields += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag == "a":
            self._flush_anchor()
        elif tag == "form":
            self._form = None
        if tag in _BLOCK_TAGS:
            self._flush()
            self._tag = "body"
        if tag in _REGION_TAGS:
            self._pop_region(tag)

    def handle_data(self, data: str) -> None:
        # The old extractor gated on `in_body` and let CDATA through, so a Next.js
        # bundle contributed both its word count and its JSON keys ("todo",
        # "placeholder") to the visible text. Here the gate is the skip counter.
        if self._skip:
            return
        self._buffer.append(data)
        if self._anchor is not None:
            self._anchor.parts.append(data)

    def close(self) -> None:
        # HTMLParser holds back trailing text it cannot yet prove is not an
        # incomplete character reference. feed() alone loses it: a body ending in
        # "Contato: joao&" extracted to the empty string, which then read as a
        # stub page with no name and no contact.
        super().close()
        self._flush()
        self._flush_anchor()


def parse_document(html: str) -> Document:
    """Parse markup into text blocks, links and forms. Never raises."""
    parser = _DocumentParser()
    error: str | None = None
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # html.parser can still raise on pathological input
        error = f"{type(exc).__name__}: {exc}"
    return Document(
        blocks=list(parser.blocks),
        links=list(parser.links),
        forms=list(parser.forms),
        base_href=parser.base_href,
        parse_error=error,
    )


def visible_text(html: str) -> str:
    """Visible text as this module sees it, whitespace-normalised.

    Named apart from `adsense_checks.text.extract_text` on purpose: that one
    answers "how much did the author write", this one is the text the block,
    link and form scanner below actually looked at. Asserting on the depth
    module's output would test the wrong parser.
    """
    return parse_document(html).text


# --------------------------------------------------------------------------
# Placeholder markers
# --------------------------------------------------------------------------

Confidence = Literal["strong", "weak"]


@dataclass(frozen=True)
class _Marker:
    phrase: str
    pattern: re.Pattern[str]
    # Unambiguous statements that something is unfinished.
    strong: bool
    # True for markers that are also ordinary words in one of the two languages
    # scanned: "todo" is Portuguese for "every", "draft" is a kind of beer,
    # "placeholder" is a form attribute. These need the block to be almost
    # nothing but the marker.
    alone_only: bool = False


def _compile(phrase: str) -> re.Pattern[str]:
    # Word-anchored, and tolerant of the whitespace collapse the parser already
    # did. The old check used `in`, so "Mastodon" contained "todo" and every
    # sentence with "in this section" was an unfinished page.
    inner = r"\s+".join(re.escape(word) for word in phrase.split())
    return re.compile(rf"(?<!\w){inner}(?!\w)")


_MARKER_SOURCE: tuple[tuple[str, bool, bool], ...] = (
    # phrase (folded), strong, alone_only
    ("coming soon", True, False),
    ("em breve", True, False),
    ("under construction", True, False),
    ("em construcao", True, False),
    ("will be added", True, False),
    ("sera adicionado", True, False),
    ("nao implementado", True, False),
    ("not implemented", True, False),
    ("lorem ipsum", True, False),
    ("work in progress", True, False),
    ("not yet", False, False),
    ("ainda nao", False, False),
    ("tbd", False, False),
    ("todo", False, True),
    ("draft", False, True),
    ("rascunho", False, True),
    ("placeholder", False, True),
)

_MARKERS: tuple[_Marker, ...] = tuple(
    _Marker(phrase=phrase, pattern=_compile(phrase), strong=strong, alone_only=alone)
    for phrase, strong, alone in _MARKER_SOURCE
)


@dataclass(frozen=True)
class Placeholder:
    """One unfinished-work marker, with the context that qualifies it."""

    phrase: str
    tag: str
    snippet: str
    words_in_block: int
    confidence: Confidence


def find_placeholders(html: str) -> list[Placeholder]:
    """Find unfinished-work markers in Portuguese and English (ADS-COMPLETE-01).

    Every hit carries a `confidence`, and the difference between the two values
    is the only defence this function has against its own false positives:

      * `strong` — an unambiguous marker ("coming soon", "em breve", "under
        construction", "lorem ipsum") that dominates a short block: a heading, a
        card, a button. This is the product card that was never filled in.
      * `weak` — the same marker buried in long-form prose, or a generic marker
        ("todo", "draft", "not yet") in a short block.

    The limitation is real and is not papered over: **a marker is a string, and
    this function cannot tell a page's state from its subject.** A blog post
    titled "Em breve, o que muda no Pix" is a finished article about something
    forthcoming, and it produces the same substring as a product card that was
    never written. The block heuristic only asks "is this phrase most of what
    the block says?", which correlates with the distinction without deciding it.
    Callers that need precision must treat `weak` as a prompt to look, never as
    a finding, and even a `strong` hit inside an article listing can be wrong.

    Generic markers are dropped entirely from long blocks, which is why "We
    serve draft beer" and "I have not yet published the sequel" produce nothing.
    """
    return placeholders_in(parse_document(html).blocks)


def placeholders_in(blocks: list[Block]) -> list[Placeholder]:
    """`find_placeholders` over an already-parsed document."""
    found: list[Placeholder] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        folded = fold(block.text)
        for marker in _MARKERS:
            match = marker.pattern.search(folded)
            if match is None:
                continue
            if marker.alone_only and _words(folded) - _words(match.group()) > ALONE_SLACK_WORDS:
                continue
            if not marker.strong and not block.is_short:
                continue
            key = (marker.phrase, block.text)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Placeholder(
                    phrase=marker.phrase,
                    tag=block.tag,
                    snippet=_snippet(block.text),
                    words_in_block=block.words,
                    confidence="strong" if marker.strong and block.is_short else "weak",
                )
            )
    return found


def strong_placeholders(placeholders: list[Placeholder]) -> list[Placeholder]:
    return [p for p in placeholders if p.confidence == "strong"]


# --------------------------------------------------------------------------
# Contact channels (ADS-AUTHOR-02, partial)
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.IGNORECASE)

# "contato [arroba] meusite.com.br" — an address written to survive scrapers,
# which a human still reads as an address. Both halves are required. The
# separator token on its own is not a channel, and in Portuguese it is not even
# rare: an arroba is 15 kg of cattle, so matching the bare word would turn a
# livestock price into proof that the publisher can be reached. Bare English
# "at" is not accepted either, because "write at length" is prose.
_OBF_AT = r"(?:\[\s*(?:at|arroba)\s*\]|\(\s*(?:at|arroba)\s*\)|\s+arroba\s+)"
_OBF_DOT = r"(?:\s*\[\s*(?:dot|ponto)\s*\]\s*|\s*\(\s*(?:dot|ponto)\s*\)\s*|\.)"
_OBFUSCATED_RE = re.compile(
    rf"[\w.+-]+\s*{_OBF_AT}\s*[\w-]+(?:{_OBF_DOT}[\w-]+)*{_OBF_DOT}[a-z]{{2,}}(?!\w)",
    re.IGNORECASE,
)

_SOCIAL_HOSTS = (
    "linkedin.com", "github.com", "twitter.com", "x.com", "instagram.com",
    "facebook.com", "youtube.com", "bsky.app", "t.me", "wa.me",
    "telegram.me", "threads.net",
)

# Mastodon is federated: the profile lives on the instance's own domain, so
# there is no registrable domain to list. A host label is all that is left to
# match on — and it is still a label, never a substring, so "mastodonte.com.br"
# is not a profile.
_SOCIAL_HOST_LABELS = frozenset({"mastodon"})


def _is_social_host(host: str) -> bool:
    """Whether `host` is a known profile host, matched on label boundaries.

    Substring matching is what this exists to avoid, and the damage was not
    hypothetical: "x.com" sits inside netflix.com and phoenix.com, and "t.me"
    inside payment.mercadopago.com.br. Any outgoing link then counted as a
    social profile, a social profile is proof of a contact channel, and a
    contact page offering no way to reach anyone came back OK. It is the same
    missing-word-boundary defect `_compile` was written to kill, made once more
    in the host list instead of in the markers.
    """
    host = host.rpartition("@")[2].split(":")[0]  # drop userinfo and port
    if not host:
        return False
    if any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_HOSTS):
        return True
    return not _SOCIAL_HOST_LABELS.isdisjoint(host.split("."))


@dataclass(frozen=True)
class ContactChannels:
    """What the HTML actually offers as a way to reach the publisher.

    A single '@' in the text is not on this list. It was the old proof of a
    contact channel, and "Siga @meublog no Twitter" satisfied it while a page
    whose only channel was a working form did not.
    """

    mailto: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    obfuscated: list[str] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)

    @property
    def any_found(self) -> bool:
        # `obfuscated` counts. It is an address a human can read and use, and
        # leaving it out was the other half of the same defect: an observation
        # was recorded, shown in `describe()`, and moved no verdict — so a page
        # whose only channel was written as "contato [arroba] meusite.com.br"
        # was reported as offering no contact channel at all.
        return bool(self.mailto or self.emails or self.obfuscated or self.forms or self.socials)

    def describe(self) -> str:
        parts = []
        if self.mailto:
            parts.append(f"mailto:{self.mailto[0]}")
        if self.emails:
            parts.append(f"email {self.emails[0]}")
        if self.obfuscated:
            parts.append(f"obfuscated address ({self.obfuscated[0]})")
        if self.forms:
            parts.append("contact form")
        if self.socials:
            parts.append(f"profile {self.socials[0]}")
        return ", ".join(parts) if parts else "none"


def find_contact_channels(html: str) -> ContactChannels:
    """Detect mailto links, contact-shaped forms, plain-text and social channels.

    Not covered, and not claimed: ADS-AUTHOR-02 also requires that each channel
    resolve — that the form action answers and the profile URL returns 200.
    Verifying the form would mean POSTing to a stranger's endpoint, so this
    function reports presence only, and the caller must not read presence as
    proof of reachability.
    """
    return channels_in(parse_document(html))


def channels_in(doc: Document) -> ContactChannels:
    """`find_contact_channels` over an already-parsed document."""
    mailto: list[str] = []
    socials: list[str] = []
    for link in doc.links:
        href = link.href.strip()
        lowered = fold(href)
        if lowered.startswith("mailto:"):
            address = href[len("mailto:") :].split("?")[0].strip()
            if _EMAIL_RE.fullmatch(address):
                mailto.append(address)
            continue
        if _is_social_host(fold(split_url(href).netloc)):
            socials.append(href)
    text = doc.text
    emails = [m.group() for m in _EMAIL_RE.finditer(text)]
    obfuscated = [m.group() for m in _OBFUSCATED_RE.finditer(text)]
    forms = [f for f in doc.forms if f.is_contact_shaped]
    return ContactChannels(
        mailto=_dedupe(mailto),
        emails=_dedupe(emails),
        obfuscated=_dedupe(obfuscated),
        forms=forms,
        socials=_dedupe(socials),
    )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


# --------------------------------------------------------------------------
# Reports. Status is always derived, never assigned.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    status: Status
    message: str


class _Verdict:
    """Mixin: the verdict is a function of the findings and of nothing else.

    The defect this replaces was a summary counting a hand-written list of
    statuses that had drifted from the statuses the checks emitted, so WARNING
    was printed and then not counted. Here there is no place to put a status
    that is not backed by a finding, and no second list to forget to update.
    """

    findings: list[Finding]

    def add(self, status: Status, message: str) -> None:
        """The only way to record a problem — and it always moves the verdict."""
        self.findings.append(Finding(status=status, message=message))

    @property
    def status(self) -> Status:
        return worst(*(f.status for f in self.findings))

    @property
    def issues(self) -> list[str]:
        return [f.message for f in self.findings]

    @property
    def passed(self) -> bool:
        """Only a clean OK is a pass. MISSING, INFO and ERROR are not."""
        return self.status is Status.OK

    @property
    def blocks_readiness(self) -> bool:
        return self.status.blocks_readiness


@dataclass
class PageOutcome:
    """What happened to one trust page.

    `status` is the three-way distinction the audit needs: MISSING (no such
    page), WARNING (it exists but is unfinished or a stub) and ERROR (it could
    not be read, so nothing about it is known).
    """

    kind: str
    status: Status
    url: str | None = None
    reason: str = ""
    words: int = 0
    placeholders: list[Placeholder] = field(default_factory=list)
    channels: ContactChannels | None = None
    attempts: list[tuple[str, str]] = field(default_factory=list)
    # URLs the home page itself offers as this page and that could not be read,
    # in the case where some other URL did answer. Non-empty only alongside a
    # page that was found: when nothing answered, the outcome is ERROR and its
    # reason already names them.
    declared_unreadable: list[str] = field(default_factory=list)


@dataclass
class TrustPagesReport(_Verdict):
    base_url: str
    pages: dict[str, PageOutcome] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class NavLink:
    url: str
    text: str
    status_code: int | None = None
    reason: str = ""


@dataclass
class NavLinkReport(_Verdict):
    base_url: str
    found: int = 0
    checked: int = 0
    broken: list[NavLink] = field(default_factory=list)
    # Links that answered HTTP 200 with exactly the page this host serves for a
    # URL that does not exist. Named for what was seen, not for the conclusion
    # somebody would like to draw from it: the same observation describes a dead
    # link and a live route that renders the site's "nothing here" partial. Kept
    # apart from `unverified` only so the report can print the sharper sentence;
    # both carry the same weight in the verdict, and neither is `count`.
    same_as_not_found: list[NavLink] = field(default_factory=list)
    # Links that answered HTTP 200 on a host where 200 was not shown to mean
    # anything. Neither working nor broken, and never folded into either.
    unverified: list[NavLink] = field(default_factory=list)
    unresolved: list[NavLink] = field(default_factory=list)
    used_all_links: bool = False
    # What the host does with a URL that does not exist: "honest", "fingerprint",
    # "opaque", or "" when no probe was sent. An observation, not a verdict.
    not_found_regime: str = ""
    findings: list[Finding] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Links OBSERVED to lead nowhere: the 4xx/5xx, and only those.

        A lower bound whenever `truncated` is true. Everything the not-found
        probe merely inferred is deliberately outside this number, in both
        directions. Counted as working, `unverified` is the soft-404 hole this
        module exists to close; counted as broken, `same_as_not_found` crosses
        BROKEN_NAV_FAIL_THRESHOLD on a site whose menu works — three links is
        one empty category, one empty tag and one page past the last, all of
        which render the partial the 404 template renders. This property feeds
        the threshold, so what it must never contain is a guess.
        """
        return len(self.broken)

    @property
    def unclassified(self) -> list[NavLink]:
        """Every 200 that could not be read as a page, whichever way it looked.

        Two lists because the sentences differ, one property because the weight
        does not: nothing in here was observed to work and nothing in here was
        observed to be broken.
        """
        return self.same_as_not_found + self.unverified

    @property
    def unverified_reason(self) -> str:
        """Why the unverified links could not be read as pages.

        Derived rather than stored: it is one observation about the host, so
        there is no second copy to drift from the links it explains.
        """
        return self.unverified[0].reason if self.unverified else ""

    @property
    def truncated(self) -> bool:
        return self.checked < self.found


@dataclass
class CompletenessReport(_Verdict):
    base_url: str
    home_placeholders: list[Placeholder] = field(default_factory=list)
    trust: TrustPagesReport | None = None
    nav: NavLinkReport | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> Status:
        parts = [f.status for f in self.findings]
        if self.trust is not None:
            parts.append(self.trust.status)
        if self.nav is not None:
            parts.append(self.nav.status)
        return worst(*parts)

    @property
    def issues(self) -> list[str]:
        out = [f.message for f in self.findings]
        if self.trust is not None:
            out.extend(self.trust.issues)
        if self.nav is not None:
            out.extend(self.nav.issues)
        return out


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------


def as_base(url: str) -> str:
    """Normalise an audit target into a base URL safe for relative joins.

    `urljoin(url, '/about')` throws away any subdirectory: for
    'https://user.github.io/meusite/' it asks 'https://user.github.io/about',
    which is a different site. Project pages and '/blog/' installs are the
    common case, and the old checker reported their existing pages as 404.
    """
    url = url.strip()
    if url.startswith("//"):
        # Protocol-relative. Prefixing the whole scheme gave "https:////ex.com".
        url = "https:" + url
    elif "://" not in url:
        url = "https://" + url
    parts = split_url(url)
    if not parts.netloc:
        # Unparseable, or no host at all. Returning a bare "/" made the report
        # blame the site — "Home page could not be read: Invalid URL '/'" — for
        # what the operator typed. Handing the input back keeps the error naming
        # the actual argument.
        return url
    path = parts.path or "/"
    # The slash goes on a DIRECTORY only. Appending it unconditionally turned a
    # target naming a document — `https://ex.com/index.html` — into
    # `…/index.html/`, which 404s, and the whole audit came back "Home page could
    # not be read: HTTP 404". For a document the right base is its containing
    # directory, which is what urljoin already resolves to. The dot is a
    # heuristic and it is wrong for a directory called `v1.0`; being wrong there
    # costs a subdirectory prefix, being wrong the other way costs the audit.
    if not looks_like_document(path):
        path = path.rstrip("/") + "/"
    # A query or fragment in a base is not part of any path it is joined with.
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _join(base: str, path: str) -> str:
    # lstrip is the whole point: a leading slash would escape the subdirectory.
    return join_url(base, path.lstrip("/"))


def _resolve_target(base: str, href: str) -> str:
    """The absolute URL a href points at, with any `user:pass@` taken off it.

    Both loops that turn a home-page href into a request go through here, so the
    two cannot drift from each other, and the stripping itself is
    `crawl.strip_userinfo` so neither can drift from the crawler. `crawl` has
    dropped userinfo from every link it resolves since ADS-CRAWL-01 was written:
    credentials scraped off the page and sent back to it mean the 401 never
    happens and "readable publicly, without authentication" passes on a page no
    anonymous visitor can open. This module reads the SAME document and asks the
    same question of the same pages — and it also PRINTS the URL, so a link with
    credentials on it ends up in the report the operator pastes into a ticket.
    """
    return strip_userinfo(join_url(base, href))


def _canonical(url: str) -> str:
    """The identity of a URL, shared with the crawler so the two cannot disagree.

    This used to compare `netloc` verbatim while `_same_site` folded `www.`, so a
    nav link written in the `www.` form survived deduplication as a second target:
    one broken URL was counted twice, and three counted broken links is the line
    between WARNING and FAIL.
    """
    return normalize_url(url)


def _same_site(url: str, home_url: str) -> bool:
    """Whether both URLs belong to the same site, `www.` not being part of one.

    Comparing netloc exactly is the identity mistake `crawl.site_host` exists to
    remove: on a home page reached at the apex, an absolute footer link to the
    `www.` form read as another site, so the About page the site declares was
    never requested and came back MISSING, and nav links there went unfollowed.
    """
    return same_site(url, home_url)


# --------------------------------------------------------------------------
# Trust pages
# --------------------------------------------------------------------------

ABOUT_PATHS = ("about", "about/", "about-us", "about-me", "sobre", "sobre/", "sobre-mim",
               "quem-somos", "pages/about")
CONTACT_PATHS = ("contact", "contact/", "contact-us", "contato", "contato/", "contatos",
                 "fale-conosco", "pages/contact")

_ABOUT_HINT = re.compile(r"(?<!\w)(about|sobre|quem\s*somos|who\s*we\s*are|bio)(?!\w)")
_CONTACT_HINT = re.compile(
    r"(?<!\w)(contact|contacto|contato|fale\s*conosco|get\s*in\s*touch)(?!\w)"
)

_TRUST_KINDS: dict[str, tuple[str, tuple[str, ...], re.Pattern[str]]] = {
    "about": ("About", ABOUT_PATHS, _ABOUT_HINT),
    "contact": ("Contact", CONTACT_PATHS, _CONTACT_HINT),
}


def check_trust_pages(
    base_url: str,
    *,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    home_page: Fetch | None = None,
    max_linked_candidates: int = MAX_LINKED_CANDIDATES,
    not_found: _NotFoundProbe | None = None,
) -> TrustPagesReport:
    """Look for About/Sobre and Contact/Contato (ADS-UX-05, ADS-AUTHOR-02 part).

    Candidates come from the home page's own footer and navigation links first —
    a site that links to /pages/quem-eu-sou has an About page, and only reading
    its links can find it — then from the conventional paths, joined relative to
    `base_url` so a subdirectory install is not silently swapped for the domain
    root. Only the linked candidates are capped; every conventional path is
    tried, so "no candidate answered" is never said about a URL that was never
    requested.

    Each page ends in exactly one of four states, and three of them are not a
    pass: OK, MISSING (nothing answered), WARNING (a stub or a placeholder) and
    ERROR (a 403, a 5xx, a timeout — the page may well exist and we cannot say).
    An access failure is never an approval.

    `not_found` is what the host answers for a URL that cannot exist, obtained
    once by the caller. Given it, a candidate answering 200 with exactly that
    page is skipped the way `_is_home_again` skips the catch-all, and the report
    records how the pages below were judged. Without it this check runs as
    before — and used to print PASS for `/about` in the same report whose
    navigation line said that very page is what the host serves for nothing.
    """
    base = as_base(base_url)
    sess = session or requests.Session()
    report = TrustPagesReport(base_url=base)

    home = home_page if home_page is not None else fetch(base, session=sess, timeout=timeout)
    if not home.ok:
        # No home page means no link discovery and, in practice, no site. Saying
        # "About page missing" here would be an assertion about a document that
        # was never read.
        reason = home.error or f"HTTP {home.status_code}"
        report.add(Status.ERROR, f"Home page could not be read ({reason}); trust pages not checked")
        return report

    home_doc = parse_document(home.text)
    if home_doc.parse_error:
        report.add(Status.ERROR, f"Home page HTML could not be parsed: {home_doc.parse_error}")
    home_text = home_doc.text

    for kind, (label, paths, hint) in _TRUST_KINDS.items():
        candidates = _candidates(home, home_doc, base, paths, hint, max_linked_candidates)
        outcome = _resolve_trust_page(
            kind=kind,
            candidates=candidates,
            home=home,
            home_text=home_text,
            session=sess,
            timeout=timeout,
            not_found=not_found,
        )
        report.pages[kind] = outcome
        _record_page(report, label, outcome, home_doc)

    if not_found is not None and not not_found.trustworthy:
        # One line for the whole check, not one per page: it is a single fact
        # about the host. Recorded because without it this report contradicted
        # itself — [PASS] about page, and four lines down "this host answers 200
        # for URLs it does not have" — and the reader had no way to know that
        # the pass rests on the words on the page rather than on the 200.
        report.add(
            Status.INFO,
            f"This host answered HTTP 200 for {not_found.url}, which does not exist, so the "
            "trust pages above were judged on the content they served and not on their status "
            "code",
        )

    missing = [k for k, p in report.pages.items() if p.status is Status.MISSING]
    if len(missing) == len(_TRUST_KINDS):
        # Neither page is a rejection on its own at Google, so it outranks the
        # per-page severities instead of merely repeating them.
        report.add(
            Status.FAIL,
            "Neither an About nor a Contact page was found — AdSense rejects sites "
            "with no way to identify or reach the publisher",
        )
    return report


@dataclass(frozen=True)
class _Candidate:
    url: str
    # True when the home page links here as this kind of page. A declared URL
    # that cannot be read is a different fact from a guessed one that 404s.
    declared: bool


def _candidates(
    home: Fetch,
    home_doc: Document,
    base: str,
    paths: tuple[str, ...],
    hint: re.Pattern[str],
    max_linked: int,
) -> list[_Candidate]:
    """Linked candidates first (footer, then nav, then body), conventions after.

    The cap applies to the linked half only. Sharing one budget let a handful of
    matching footer links push the conventions out of the list entirely, and cut
    the conventions themselves mid-tuple.
    """
    home_url = home.final_url or home.url
    # The same base `_nav_targets` and the crawler resolve against. A home
    # carrying `<base href="/app/">` declares its About page at `/app/sobre`;
    # joining against `home_url` alone requested `/sobre`, got a 404, and the
    # report then said "No About page found" — the one claim this check's
    # docstring forbids, made about a URL nobody ever asked for.
    link_base = resolve_base(home_url, home_doc.base_href)
    by_region: dict[str, list[str]] = {"footer": [], "nav": [], "body": []}
    for link in home_doc.links:
        href = link.href.strip()
        if not href or href.startswith("#"):
            continue
        if fold(href).startswith(("mailto:", "tel:", "javascript:")):
            continue
        target = _resolve_target(link_base, href)
        if split_url(target).scheme not in ("http", "https"):
            # The guard `_nav_targets` has always had and this loop had not. A
            # scheme this client cannot speak is not a missing page: `<base
            # href="ftp://ex.com/">` handed `fetch` an `ftp://` URL, which came
            # back ERROR ("no connection adapters") and the About page was
            # reported as a fault of the site. Before the base was honoured only
            # an explicit `<a href="ftp:…">` could reach here; now every relative
            # href in the document can, so the two loops agree about it.
            continue
        if not _same_site(target, home_url):
            continue
        # Either the visible label or the path may carry the word; a footer link
        # reading "Sobre" pointing at /pages/quem-eu-sou is found by the label.
        if hint.search(fold(link.text)) or hint.search(fold(split_url(target).path)):
            by_region[link.region].append(target)
    linked = by_region["footer"] + by_region["nav"] + by_region["body"]
    # Deduplicated by the address, not by its identity. `_canonical` folds the
    # trailing slash — that is what makes it an identity — and folding it here
    # silently dropped `/about/`, so a host that serves only that form and
    # answers 404 to `/about` was reported as having no About page. The candidate
    # list is a list of ADDRESSES to request, and on the wire the two are
    # different addresses; the docstring above already promises every
    # conventional path is tried.
    #
    # The home page is the one comparison where identity is the right notion: it
    # answered already, and asking for another spelling of it learns nothing.
    casa = _canonical(home_url)
    # Keyed by the ADDRESS THE REQUEST WILL CARRY, which is the href minus its
    # fragment: a fragment is never sent, so `/sobre#equipe` and `/sobre#historia`
    # are one GET. Keyed by the href as written, this set stopped counting
    # requests at all — a footer with 200 anchors into `/sobre` fired 200
    # byte-identical GETs at it, one per link, with no ceiling anywhere.
    seen: set[str] = set()
    # Identity -> how many of its spellings are already on the list. The budget
    # is spent on IDENTITIES while the list requests ADDRESSES: `/sobre` beside
    # `/sobre/` is one page on any server that folds the trailing slash, and
    # charging each of them a slot let the two eat two of the six, so a seventh
    # link went unrequested — the crowding-out this cap exists to prevent,
    # recreated one level down. Both spellings still go on the wire; what changes
    # is only the price. `MAX_SPELLINGS_PER_IDENTITY` is what keeps the discount
    # from being a hole the same size as the one it closes.
    charged: dict[str, int] = {}
    out: list[_Candidate] = []
    for href in linked:
        url = defrag_url(href) or href
        identity = _canonical(url)
        if url in seen or identity == casa:
            continue
        spellings = charged.get(identity, 0)
        if spellings == 0 and len(charged) >= max_linked:
            continue
        if spellings >= MAX_SPELLINGS_PER_IDENTITY:
            continue
        seen.add(url)
        charged[identity] = spellings + 1
        out.append(_Candidate(url=url, declared=True))
    for path in paths:
        url = _join(base, path)
        if url in seen or _canonical(url) == casa:
            continue
        seen.add(url)
        out.append(_Candidate(url=url, declared=False))
    return out


def _resolve_trust_page(
    *,
    kind: str,
    candidates: list[_Candidate],
    home: Fetch,
    home_text: str,
    session: requests.Session,
    timeout: float,
    not_found: _NotFoundProbe | None = None,
) -> PageOutcome:
    attempts: list[tuple[str, str]] = []
    # Tracks the worst thing that stopped us from reading a candidate. It only
    # ever escalates, so one 404 among the guesses cannot mask a 503.
    access = Status.OK
    blocked: list[str] = []
    # The subset of `blocked` the home page itself points at. Kept separately
    # because it survives a later candidate answering 200: the site declares
    # that URL to be its About page, and a 503 there is a fact about the page
    # this check was asked about, not about a guess that missed.
    declared_blocked: list[str] = []
    for candidate in candidates:
        url = candidate.url
        response = fetch(url, session=session, timeout=timeout)
        if response.error is not None:
            attempts.append((url, f"unreachable: {response.error}"))
            blocked.append(f"{url} unreachable: {response.error}")
            if candidate.declared:
                declared_blocked.append(f"{url} unreachable: {response.error}")
            access = escalate(access, Status.ERROR)
            continue
        if response.status_code in (404, 410):
            attempts.append((url, f"HTTP {response.status_code}"))
            continue
        if not response.ok:
            # 401/403/405/5xx: the page may exist and be perfectly fine. The old
            # code wrote this into details and left the verdict at OK.
            attempts.append((url, f"HTTP {response.status_code}"))
            blocked.append(f"{url} returned HTTP {response.status_code}")
            if candidate.declared:
                declared_blocked.append(f"{url} returned HTTP {response.status_code}")
            access = escalate(access, Status.ERROR)
            continue
        doc = parse_document(response.text)
        if _is_home_again(response, home, doc, home_text):
            # A catch-all route answering 200 with the home page is not a page.
            # Following redirects made this the old checker's silent false OK.
            attempts.append((response.final_url, "served the home page"))
            continue
        if _is_not_found_page(not_found, response):
            # The same move one line up, against the other page a catch-all
            # answers with. Used HERE, as an exclusion, this equality is sound:
            # the worst it can do is keep looking at the next candidate and end
            # at MISSING, which is the honest answer when the only thing found
            # was the not-found page. Used as an accusation it is not sound,
            # which is why count_broken_nav_links refuses to fail a link on it.
            attempts.append(
                (
                    response.final_url,
                    f"served the page this host answers with for {not_found.url}, "
                    "which does not exist",
                )
            )
            continue
        return _judge_page(kind, response, doc, attempts, declared_blocked)
    if access is Status.ERROR:
        return PageOutcome(
            kind=kind,
            status=Status.ERROR,
            reason="could not read any candidate, so the page may well exist: "
            + "; ".join(blocked[:3]),
            attempts=attempts,
        )
    return PageOutcome(
        kind=kind,
        status=Status.MISSING,
        reason=f"no candidate answered 200 ({len(attempts)} tried)",
        attempts=attempts,
    )


def _is_home_again(response: Fetch, home: Fetch, doc: Document, home_text: str) -> bool:
    home_url = home.final_url or home.url
    if _canonical(response.final_url or response.url) == _canonical(home_url):
        return True
    # Same bytes of visible text at a different URL is the SPA catch-all: the
    # router answered 200 for a route it does not have.
    return bool(home_text) and doc.text == home_text


def _is_not_found_page(probe: _NotFoundProbe | None, response: Fetch) -> bool:
    """Whether this response IS the page the host serves for a URL that does not exist.

    Only ever an answer on a host whose not-found page was pinned down, and only
    ever used to stop treating a response as the page that was asked for. Never
    used to declare one broken — see `count_broken_nav_links`.
    """
    if probe is None or probe.regime != "fingerprint":
        return False
    return _readable_text(response) == probe.fingerprint


def _judge_page(
    kind: str,
    response: Fetch,
    doc: Document,
    attempts: list[tuple[str, str]],
    declared_blocked: list[str] | None = None,
) -> PageOutcome:
    url = response.final_url or response.url
    found = placeholders_in(doc.blocks)
    channels = channels_in(doc)

    def outcome(status: Status, reason: str) -> PageOutcome:
        return PageOutcome(
            kind=kind, status=status, url=url, reason=reason, words=doc.words,
            placeholders=found, channels=channels, attempts=attempts,
            declared_unreadable=list(declared_blocked or []),
        )

    if doc.parse_error:
        return outcome(Status.ERROR, f"HTML could not be parsed: {doc.parse_error}")
    strong = strong_placeholders(found)
    if strong:
        phrases = ", ".join(sorted({p.phrase for p in strong}))
        return outcome(Status.WARNING, f"unfinished markers: {phrases}")
    if doc.words < MIN_TRUST_PAGE_WORDS:
        if looks_javascript_rendered(response.text):
            # Reusing the depth module's shell detector rather than guessing
            # again: an empty <div id="root"> is not a stub, it is a page this
            # audit cannot see. Calling it a stub would be an assertion about
            # content that was never rendered — ERROR is the honest answer.
            return outcome(
                Status.ERROR,
                "client-rendered shell: the served HTML carries no content and this audit "
                "does not execute JavaScript",
            )
        return outcome(
            Status.WARNING,
            f"stub: {doc.words} words, below the {MIN_TRUST_PAGE_WORDS} word minimum",
        )
    return outcome(Status.OK, "")


def _record_page(
    report: TrustPagesReport, label: str, outcome: PageOutcome, home_doc: Document
) -> None:
    if outcome.declared_unreadable and outcome.status is not Status.ERROR:
        # The page was found somewhere else, so the verdict is not ERROR — but
        # the URL the site sends its own readers to could not be read, and
        # dropping that on the floor is how a report comes out clean about
        # something it never managed to look at.
        report.add(
            Status.INFO,
            f"{label} page reported from {outcome.url}, but the URL the home page offers "
            f"for it could not be read: {'; '.join(outcome.declared_unreadable[:3])}",
        )
    if outcome.status is Status.OK:
        no_channel = outcome.channels is not None and not outcome.channels.any_found
        if outcome.kind == "contact" and no_channel:
            report.add(
                Status.MISSING,
                f"{label} page at {outcome.url} offers no contact channel: no mailto: address, "
                "no address written out in the text, no contact-shaped form and no social "
                "profile link (ADS-AUTHOR-02)",
            )
        return
    if outcome.status is Status.ERROR:
        report.add(Status.ERROR, f"{label} page could not be checked — {outcome.reason}")
        return
    if outcome.status is Status.WARNING:
        report.add(
            Status.WARNING, f"{label} page at {outcome.url} looks unfinished — {outcome.reason}"
        )
        return
    # MISSING. A missing About stays MISSING rather than WARNING because the
    # identity can legitimately live on the home page, which this check does not
    # judge. A missing Contact is only that mild when the home page really does
    # expose a channel; otherwise there is nothing anywhere and it escalates.
    if outcome.kind == "contact" and not channels_in(home_doc).any_found:
        report.add(
            Status.WARNING,
            f"No {label} page found and the home page exposes no contact channel either "
            f"({outcome.reason})",
        )
        return
    report.add(Status.MISSING, f"No {label} page found — {outcome.reason}")


# --------------------------------------------------------------------------
# What HTTP 200 is worth on this host
# --------------------------------------------------------------------------

# How the host answers for a URL that does not exist, which is what decides
# whether its status codes carry any information at all.
ProbeRegime = Literal["honest", "fingerprint", "opaque"]


@dataclass(frozen=True)
class _NotFoundProbe:
    regime: ProbeRegime
    # The visible text this host serves for a URL that does not exist. Set in
    # the "fingerprint" regime only, and never empty — an empty document matches
    # every content-free response, which is evidence of nothing.
    fingerprint: str = ""
    url: str = ""
    # Why a 200 from this host says nothing about the page behind it. Set in
    # BOTH non-honest regimes, because in both of them that is the fact the
    # report has to print: "fingerprint" narrows down what the not-found page
    # looks like, it does not restore the meaning of the status code.
    reason: str = ""

    @property
    def trustworthy(self) -> bool:
        """Whether HTTP 200 from this host is evidence that a page is there."""
        return self.regime == "honest"


def _readable_text(response: Fetch) -> str | None:
    """The response's visible text, or None when it cannot serve as evidence.

    None for a document that would not parse and for one with no visible text:
    accepting the empty string as a fingerprint would match a nav link pointing
    at a PDF, an image or a genuinely empty page, and report it as a soft 404 on
    the strength of both sides being blank.
    """
    doc = parse_document(response.text)
    if doc.parse_error:
        return None
    return doc.text.strip() or None


def _probe_not_found(
    base: str, *, session: requests.Session, timeout: float
) -> _NotFoundProbe:
    """Ask the host what it serves for a URL that does not exist.

    `count_broken_nav_links` decided on `status_code >= 400` alone, and a CMS
    answering 200 with its "page not found" template — the soft 404, and it is
    common — reported `broken 0` over a menu whose every link goes nowhere. A
    status code is only evidence when the host is willing to spend one on a page
    it does not have, and the only way to learn whether this host does is to ask
    it for a page it cannot have. The same reasoning already runs for robots.txt
    in `crawl._load_robots`, which refuses to parse an HTML body as rules.

    Three answers, and only the first of them restores the meaning of a 200:

      * `honest` — the probe got 4xx/5xx. This host spends a status code on a
        missing page, so a nav link answering 200 is a page. **One request**, and
        it is the common case: the second probe is never sent.
      * `fingerprint` — both probes got the same status and the same visible
        text. That text is what this host serves for nothing, so a response
        carrying it is worth naming. It is NOT worth failing a link over: the
        same equality holds for a real route rendering the site's "nothing
        here" partial, and it is the equality `_is_home_again` uses to skip a
        candidate rather than to condemn one.
      * `opaque` — the host answers 200 for a URL that does not exist but not
        with a recognisable page (the template echoes the requested address, or
        carries no text), or the probe could not be made at all.

    The regime is a fact about the HOST, and `fingerprint` is a weaker fact than
    it looks: it says two samples matched, not that the template is stable. On a
    host whose error page carries a timestamp or a request id the two probes
    match or not depending on where a second boundary fell, so anything the
    caller decides differently between `fingerprint` and `opaque` is a verdict
    decided by a coin. Both therefore mean the same thing to the caller — HTTP
    200 proves nothing here — and only the sentence printed differs.
    """
    first_url = _join(base, NOT_FOUND_PROBE_PATHS[0])

    def opaque(reason: str) -> _NotFoundProbe:
        return _NotFoundProbe("opaque", url=first_url, reason=reason)

    first = fetch(first_url, session=session, timeout=timeout)
    if first.error is not None:
        return opaque(
            f"{first_url} could not be fetched ({first.error}), so what this host answers "
            "for a URL that does not exist is unknown"
        )
    if first.status_code is None:
        return opaque(f"{first_url} answered without a status code")
    if first.status_code >= 400:
        return _NotFoundProbe("honest", url=first_url)

    # Past here the host is a proven soft-404 host: it answered a success code
    # for a path nothing routes.
    served = f"{first_url} does not exist and this host answered HTTP {first.status_code} for it"
    first_text = _readable_text(first)
    if first_text is None:
        # No second request: whatever it came back with, a blank page can never
        # be the thing other links are recognised by.
        return opaque(
            f"{served} carrying no readable text, and a blank page matches every blank "
            "response, so it cannot be used to recognise the other links"
        )

    # The second request is spent only on a host that has already misbehaved,
    # and it buys exactly one thing — whether the page served for nothing is the
    # same page every time, and so usable to recognise the other links by.
    second_url = _join(base, NOT_FOUND_PROBE_PATHS[1])
    second = fetch(second_url, session=session, timeout=timeout)
    if second.error is not None:
        return opaque(
            f"{served}; the second probe {second_url} could not be fetched ({second.error}), "
            "so its not-found page could not be identified"
        )
    if second.status_code != first.status_code:
        return opaque(
            f"{served}, and HTTP {second.status_code} for {second_url}, which does not exist "
            "either: this host's status codes do not say whether a page is there"
        )
    # Reported apart from the blank case above rather than as one either/or,
    # because the report's job is to say what was seen: "the page it serves for
    # nothing is blank" and "it serves a different page every time" send the
    # reader to different places.
    if first_text != _readable_text(second):
        return opaque(
            f"{served}, and it serves a DIFFERENT page for each such URL — the template echoes "
            f"the address, as {second_url} showed — so there is no one page to recognise the "
            "other links by"
        )
    return _NotFoundProbe(
        "fingerprint",
        fingerprint=first_text,
        url=first_url,
        reason=f"{served}, so HTTP 200 from this host does not show that a page is there",
    )


# --------------------------------------------------------------------------
# Broken navigation links
# --------------------------------------------------------------------------


def count_broken_nav_links(
    base_url: str,
    *,
    limit: int = DEFAULT_NAV_LINK_LIMIT,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    home_page: Fetch | None = None,
    not_found: _NotFoundProbe | None = None,
) -> NavLinkReport:
    """Follow the home page's navigation links and count the ones leading nowhere.

    Part of ADS-COMPLETE-01: a menu pointing at pages that do not exist is what
    an abandoned site looks like from the crawler's side.

    Only same-site http(s) links are followed, at most `limit` of them. When the
    limit bites, `truncated` is true and `count` is a lower bound — the report
    says so as a MISSING finding rather than leaving a partial number looking
    complete. Links that fail at the transport layer are `unresolved`, not
    "fine": they raise the status to ERROR, because a link nobody could resolve
    has not been shown to work.

    A link answering HTTP 200 is only a working page on a host that would have
    said 404 otherwise, so `_probe_not_found` asks the host that question once —
    not once per link — and the answer decides what the 200s are worth. Pass
    `not_found` to reuse an answer already obtained for this host; without it
    the probe is sent here, and only when there is a menu to sort.

      * The host is `honest`: it 4xx'd a URL that cannot exist, so a 200 is a
        page and a 4xx/5xx is a broken link. This is the only regime in which
        this function reports anything as working.
      * The host answered a success code for a URL that cannot exist: then no
        200 from it is evidence about the page behind it, and every one of them
        is recorded as unverified — `same_as_not_found` when the response is
        exactly the page the probe drew, `unverified` otherwise. Both are
        MISSING, neither reaches `count`, and neither can reach
        `BROKEN_NAV_FAIL_THRESHOLD`.

    That last rule is the whole design and it is deliberately blunt. The sharper
    version — fail the links matching the not-found page, pass the rest — was
    tried and is wrong twice over. It fails working sites: an empty WordPress
    category, an empty tag and a page past the last one render the same
    `content-none.php` the 404 template renders, which is three links and
    exactly the threshold; on a catch-all serving the home page, `/index.php`
    and `/pt/` are real routes. And it is not reproducible: whether the two
    probes matched decides whether the comparison happens at all, so a host
    whose error page carries a clock returned OK, WARNING or MISSING for the
    same dead menu depending on the run.

    A 4xx/5xx is untouched by any of this and still feeds the threshold on every
    host: that one was observed, not inferred. Links that fail at the transport
    layer stay `unresolved` and ERROR.

    What it still misses, stated rather than hidden: a host that 404s the probe
    path but soft-404s inside one subtree (a CMS mounted under `/blog/`) is
    treated as honest throughout. The probe path is fixed and published in
    `NOT_FOUND_PROBE_PATHS`, so a host could also special-case it; nothing here
    defends against a site that lies on purpose.
    """
    base = as_base(base_url)
    sess = session or requests.Session()
    report = NavLinkReport(base_url=base)

    home = home_page if home_page is not None else fetch(base, session=sess, timeout=timeout)
    if not home.ok:
        reason = home.error or f"HTTP {home.status_code}"
        report.add(Status.ERROR, f"Home page could not be read ({reason}); no links followed")
        return report

    doc = parse_document(home.text)
    if doc.parse_error:
        report.add(Status.ERROR, f"Home page HTML could not be parsed: {doc.parse_error}")

    home_url = home.final_url or home.url
    targets = _nav_targets(doc, home_url)
    if not targets:
        # No <nav>/<footer> markup at all is common on hand-written pages; fall
        # back to every internal link and say so, rather than reporting a clean
        # zero for links that were never looked at.
        targets = _nav_targets(doc, home_url, regions=None)
        report.used_all_links = bool(targets)

    report.found = len(targets)

    # Asked once for the whole menu, and only when there is something to sort:
    # on a menu of nothing the answer would decide nothing and the request would
    # be spent against someone else's host for free.
    probe = _NotFoundProbe("honest")
    if not_found is not None:
        probe = not_found
        report.not_found_regime = probe.regime
    elif targets:
        # Probed where the LINKS are, which is `resolve_base` and not the URL the
        # operator typed. Two things separate them, and both make the answer
        # useless if the probe is asked at the wrong one: a site redirecting apex
        # to www serves different hosts, and a `<base href>` moves every link
        # into a subdirectory that may answer a missing page quite differently
        # from the root. Asking the root what a missing page looks like and then
        # judging `/app/` by it reported OK over a menu of dead links.
        probe = _probe_not_found(
            as_base(resolve_base(home_url, doc.base_href)), session=sess, timeout=timeout
        )
        report.not_found_regime = probe.regime

    for url, text in targets[:limit]:
        report.checked += 1
        response = fetch(url, session=sess, timeout=timeout)
        if response.error is not None:
            report.unresolved.append(NavLink(url=url, text=text, reason=response.error))
            continue
        code = response.status_code
        if code is not None and code >= 400:
            # Observed off the wire, on any host. This is the only branch that
            # is allowed to call a link broken.
            report.broken.append(
                NavLink(url=url, text=text, status_code=code, reason=f"HTTP {code}")
            )
            continue
        if probe.trustworthy:
            continue
        # The host answers a success code for a URL that cannot exist, so this
        # 200 shows nothing about the page behind it. Which of the two lists it
        # lands in changes the sentence and not the weight.
        if probe.regime == "fingerprint" and _readable_text(response) == probe.fingerprint:
            report.same_as_not_found.append(
                NavLink(
                    url=url, text=text, status_code=code,
                    reason=f"HTTP {code} serving exactly the page {probe.url} serves, and that "
                           "URL does not exist",
                )
            )
        else:
            report.unverified.append(
                NavLink(url=url, text=text, status_code=code, reason=probe.reason)
            )

    severity = Status.FAIL if report.count >= BROKEN_NAV_FAIL_THRESHOLD else Status.WARNING
    if report.broken:
        listed = ", ".join(f"{link.url} ({link.status_code})" for link in report.broken[:5])
        report.add(severity, f"{len(report.broken)} navigation link(s) return 4xx/5xx: {listed}")
    if report.same_as_not_found:
        listed = ", ".join(link.url for link in report.same_as_not_found[:5])
        report.add(
            # MISSING, not FAIL, and this is the decision the whole probe exists
            # to support. Serving the not-found page is what a dead link looks
            # like AND what a live route rendering the site's "nothing here"
            # partial looks like, and the two are indistinguishable from here.
            # Reported at the severity of "not observed", which is what it is.
            Status.MISSING,
            f"{len(report.same_as_not_found)} navigation link(s) answered HTTP 200 with exactly "
            f"the page this host serves for {probe.url}, which does not exist. That is what a "
            "dead link looks like here — and also what a real page sharing the site's "
            "\"nothing here\" template looks like, so it is recorded as unverified rather than "
            f"broken and a human has to open one: {listed}",
        )
    if report.unverified:
        listed = ", ".join(link.url for link in report.unverified[:5])
        report.add(
            # MISSING for the same reason truncation is: the condition was not
            # observed. Not OK, because a 200 on a host that answers 200 for
            # everything demonstrates nothing; not WARNING or FAIL, because
            # nothing here was shown to be broken and inventing three failures
            # out of a host quirk would print "abandoned" over a working site.
            Status.MISSING,
            f"{len(report.unverified)} navigation link(s) answered HTTP 200 but could not be "
            f"shown to lead anywhere: {report.unverified_reason}. Counted as neither working "
            f"nor broken: {listed}",
        )
    if report.unresolved:
        listed = ", ".join(f"{link.url} ({link.reason})" for link in report.unresolved[:5])
        report.add(
            Status.ERROR,
            f"{len(report.unresolved)} navigation link(s) could not be resolved: {listed}",
        )
    if report.truncated:
        report.add(
            # MISSING for the same reason the crawl's page ceiling is: the links
            # past the limit were not looked at. As INFO a 32-link menu with
            # three 404s past `--nav-limit 25` exited 0.
            Status.MISSING,
            f"Followed {report.checked} of {report.found} navigation links (limit {limit}); "
            "the broken-link count is a lower bound",
        )
    return report


def _nav_targets(
    doc: Document,
    home_url: str,
    regions: tuple[str, ...] | None = ("nav", "footer"),
) -> list[tuple[str, str]]:
    # Relative hrefs point wherever `<base href>` says, which is why this is not
    # `home_url`: a menu of three live links under `<base href="/app/">` was
    # requested at the document root instead, 404ed three times, and three broken
    # nav links is the FAIL threshold. `home_url` stays the yardstick for the two
    # questions that are about the DOCUMENT rather than its links — which target
    # is the home page again, and which targets are off-site — because a `<base>`
    # elsewhere does not move the page that declared it.
    base = resolve_base(home_url, doc.base_href)
    out: list[tuple[str, str]] = []
    seen = {_canonical(home_url)}
    for link in doc.links:
        if regions is not None and link.region not in regions:
            continue
        href = link.href.strip()
        if not href or href.startswith("#"):
            continue
        if fold(href).startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        target = _resolve_target(base, href)
        if split_url(target).scheme not in ("http", "https"):
            continue
        if not _same_site(target, home_url):
            continue  # external links are somebody else's uptime
        key = _canonical(target)
        if key in seen:
            continue
        seen.add(key)
        out.append((target, link.text))
    return out


# --------------------------------------------------------------------------
# The whole check
# --------------------------------------------------------------------------


def _why_unreadable(home: Fetch) -> str | None:
    """Why the home page cannot be judged, or None when it can be.

    Every branch here is a case where the checks below would have run over an
    empty document and reported that they found nothing wrong with it.
    """
    if not home.ok:
        return home.error or f"HTTP {home.status_code}"
    media = home.headers.get("content-type", "").split(";")[0].strip().lower()
    if media and media not in ("text/html", "application/xhtml+xml"):
        return f"Content-Type {media!r} is not HTML"
    if not home.text.strip():
        return f"HTTP {home.status_code} with an empty body"
    if looks_javascript_rendered(home.text):
        return (
            "client-rendered shell: the served HTML carries no content and this "
            "audit does not execute JavaScript"
        )
    if not parse_document(home.text).text.strip():
        return f"HTTP {home.status_code} carrying markup but no visible text"
    return None


def check_completeness(
    base_url: str,
    *,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    nav_link_limit: int = DEFAULT_NAV_LINK_LIMIT,
) -> CompletenessReport:
    """Home page markers + trust pages + broken navigation, as one verdict.

    The home page is fetched once and handed to both sub-checks. The verdict is
    `worst()` over everything recorded, so nothing that was written down can be
    absent from it — which is precisely what "Completeness checks passed"
    printed under a list of problems used to mean.
    """
    base = as_base(base_url)
    sess = session or requests.Session()
    report = CompletenessReport(base_url=base)

    home = fetch(base, session=sess, timeout=timeout)
    unreadable = _why_unreadable(home)
    if unreadable is not None:
        # An answer is not a document. A 200 carrying an empty body, a JSON API
        # response, or a client-rendered shell all used to sail through: the home
        # placeholder scan found no markers in no text and the navigation scan
        # found no broken links among no links, so BOTH printed PASS and the run
        # exited 0 over a page this audit never read. `looks_javascript_rendered`
        # was already imported and already applied to the trust pages; the home
        # was the one document it was not asked about.
        report.add(Status.ERROR, f"Home page could not be read: {unreadable}")
        return report

    doc = parse_document(home.text)
    if doc.parse_error:
        report.add(Status.ERROR, f"Home page HTML could not be parsed: {doc.parse_error}")
    report.home_placeholders = placeholders_in(doc.blocks)
    strong = strong_placeholders(report.home_placeholders)
    if strong:
        phrases = ", ".join(sorted({p.phrase for p in strong}))
        report.add(Status.WARNING, f"Home page shows unfinished markers: {phrases}")
    weak = [p for p in report.home_placeholders if p.confidence == "weak"]
    if weak:
        phrases = ", ".join(sorted({p.phrase for p in weak}))
        # INFO, not WARNING: in long-form prose these are as likely to be the
        # subject as the state. Recorded because an unreported observation is how
        # the previous version lost its warnings.
        report.add(
            Status.INFO,
            f"Home page mentions, in prose: {phrases} (review — may be about the topic)",
        )

    # What HTTP 200 is worth on this host, asked once and handed to both
    # sub-checks. It used to be asked inside count_broken_nav_links, which runs
    # second, so check_trust_pages printed [PASS] for /about in the very report
    # whose navigation line said that page is what this host serves for a URL it
    # does not have. Costs the same one or two requests wherever it is asked;
    # asked here it is also spent on a site whose menu is empty, and the trust
    # pages are requested on every site there is.
    # Asked where the links are — `<base href>` moves them, and the probe has to
    # move with them or it answers about a directory nothing is fetched from.
    not_found = _probe_not_found(
        as_base(resolve_base(home.final_url or home.url, doc.base_href)),
        session=sess,
        timeout=timeout,
    )

    report.trust = check_trust_pages(
        base, session=sess, timeout=timeout, home_page=home, not_found=not_found
    )
    report.nav = count_broken_nav_links(base, limit=nav_link_limit, session=sess, timeout=timeout,
                                        home_page=home, not_found=not_found)
    return report
