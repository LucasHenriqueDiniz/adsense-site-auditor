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

# How many DISTINCT DIRECTORIES one run may ask the not-found question in.
#
# The answer to that question is a fact about ONE directory's router, so the only
# rule that is right in both directions is to ask each directory about itself —
# and `urljoin("/about/", ".")` is `/about/`, not `/`, so a URL with a trailing
# slash IS its own directory and on the modern web nearly every navigation link
# is one. Measured on the fixture EXAMPLES.md documents: six navigation links,
# six distinct directories, and the run grows from 10 requests to 15. On a
# 25-link menu where every link is its own directory it would be 26.
#
# So the ceiling is the design decision. Its own constant and NOT shared with
# `DEFAULT_NAV_LINK_LIMIT`, because the two bound different things: that one
# bounds how much of the site's OWN PUBLISHED navigation is read — addresses
# that exist, which a browser fetches too — while this one bounds requests for
# addresses NOBODY ROUTES, which only this tool ever sends and which land in the
# host's error log. Sharing them would make the invented load scale with menu
# size, so an operator raising `--nav-limit` to see a big menu would silently
# double their footprint on someone else's host. `crawl.py` calls an uncapped
# crawler "a load generator" and this is the same debt one check over.
#
# Eight is the smallest value that covers the documented fixture's six with room
# for the two additions that are common rather than exotic — a `<base href>`
# install directory, and a footer trust link in a directory of its own. That
# slack is real only because the CONVENTIONAL trust paths no longer take a slot
# each: `about/`, `sobre/`, `contact/`, `contato/` and `pages/` carry trailing
# slashes, so asked per address they claimed six of the eight before a single
# navigation link was judged, and on a soft-404 host the ceiling then ran out
# before `/sobre/` — where the error template answered and passed as an About
# page, taking the headline "neither an About nor a Contact page was found" out
# of the report with it. They are invented addresses, so they are asked at the
# base they were invented from, which costs one slot between all seventeen. The
# cost
# it admits to, measured against a 25-link audit's 43 requests to real
# addresses: 8 invented requests (19%) on an honest host, 16 (37%) on one that
# soft-404s in every directory, because a directory proven to soft-404 is asked
# the second path too. Either way the addresses this tool invents stay a
# minority of the run. Past the cap nothing is guessed: a link whose directory
# was refused a probe is reported MISSING — unverified, neither working nor
# broken — which is a bounded, honest false MISSING and is stated as such in
# `count_broken_nav_links`.
#
# No delay is inserted between probes. `completeness.py` spaces none of its ~20
# requests, so slowing only the invented ones would make them politer than the
# site's own pages while leaving the run's footprint the same shape; the lever
# chosen here is the NUMBER of invented requests, which is what a host's log
# counts. `DEFAULT_DELAY` belongs to `crawl.py`, which walks a whole site.
MAX_PROBED_DIRECTORIES = 8

# The paths the soft-404 probe asks for, joined onto the directory being probed.
# One such directory is `_invented_base` — where the URLs this audit MAKES UP go,
# so a subdirectory install is probed inside its own install rather than at the
# apex, whether the subdirectory came from the typed URL or from the document's
# `<base href>`. It is not the only one: a link the document wrote goes where
# `urljoin` puts it and an absolute one goes where it says, so `_NotFoundProbes`
# asks each directory a response actually came from about itself, and
# `_probe_covers` refuses to let one directory's answer classify another's.
# Fixed rather than random, so the operator can curl the same URL and check the
# claim the report makes about their host. They differ in length and in shape on
# purpose: a not-found template that echoes the requested address answers them
# with different text, and telling those two hosts apart is the whole job of
# `_probe_not_found`. The second is requested only into a DIRECTORY whose answer
# to the first already proved it serves 200 for pages it does not have — which
# USUALLY means neither reaches a 404 log, but not always, and the docs used to
# claim always: a catch-all routing only alphabetic slugs answers the first and
# 404s the second, because that one carries digits. `_probe_not_found` has the
# branch for it and the report prints the pair, so the claim to make is that the
# second is never sent to a directory that 404s the first.
#
# It is asked per directory rather than once per host, and that was measured
# rather than assumed. In `count_broken_nav_links` it changes nothing: both
# non-honest regimes mean "HTTP 200 proves nothing here", carry the same weight
# and never reach `count`, so only the printed sentence differs. In
# `check_trust_pages` it changes a VERDICT — the fingerprint is what
# `_is_not_found_page` excludes a candidate by, and without it a not-found
# template carrying prose is long enough for `_judge_page` to approve as the
# About page. A linked About at `/loja/quem-eu-sou` lands in its own directory,
# which is the ordinary shape and not an exotic one, so restricting the second
# path to one directory per run would leave every other directory's soft-404
# template able to pass as a trust page. That is why the cap is stated in
# directories AND in requests: eight directories, at most sixteen requests.
# The status codes that mean "routed, and there is nothing here". Anything else
# at or above 400 refuses the request instead of answering it — 401 and 403 say
# who you are is the problem, 429 says not now, 5xx says the server broke — and
# none of them describes what this directory does with a path nothing routes.
# Reading them as honesty is how a WAF's 403 on a scan-shaped path turned a menu
# of dead links into a pass. 405 and 451 are refusals of the same kind and stay
# out; 410 is in, because Gone is the server routing the path and saying so.
_ROUTED_TO_NOTHING = frozenset({404, 410})

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
    # Links that answered HTTP 200 from a directory this run never measured:
    # either the per-run ceiling on probed directories was already spent, or the
    # response came from another origin, which this audit does not send invented
    # URLs to. Their own third list because the fact is about the request and not
    # about the host — other directories may well have answered honestly, and
    # that answer does not reach where these landed. Same weight as the two above.
    unmeasured: list[NavLink] = field(default_factory=list)
    unresolved: list[NavLink] = field(default_factory=list)
    used_all_links: bool = False
    # What the ANCHOR directory — the one this audit invents URLs in — does with
    # a URL that does not exist: "honest", "fingerprint", "opaque", or "" when no
    # probe was sent. An observation about one directory, not a verdict, and
    # deliberately not a claim about the host: the other directories a run
    # touches are measured separately and can disagree with this one.
    not_found_regime: str = ""
    # Directories this run declined to measure because `MAX_PROBED_DIRECTORIES`
    # was already spent. Named so the report can say which addresses went
    # unjudged and why, rather than leaving the reader to infer a ceiling from a
    # count. Not every entry is a directory a NAVIGATION link came from: a trust
    # candidate or the anchor asks first and can spend the last slot, so a reader
    # pointing a second run at one of these may find nothing there to re-check.
    # The list is what the ceiling refused, not a list of suspect addresses.
    refused_directories: list[str] = field(default_factory=list)
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

        Three lists because the sentences differ, one property because the weight
        does not: nothing in here was observed to work and nothing in here was
        observed to be broken.
        """
        return self.same_as_not_found + self.unverified + self.unmeasured

    @property
    def unverified_reason(self) -> str:
        """Why the FIRST unverified link could not be read as a page.

        Derived rather than stored, so there is no second copy to drift from the
        links it explains. It is one directory's observation and not the host's:
        with several directories measured the rest of the list may carry a
        different sentence, which is why each `NavLink` keeps its own `reason`
        and the group line that quotes this one says "answered HTTP 200 but
        could not be shown to lead anywhere" rather than attributing the cause
        to every link in it.
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


def _directory_of(url: str) -> str:
    """The directory `url` belongs to, resolved the way `_join` resolves.

    The unit the not-found question has an answer about. Resolved through
    `urljoin` rather than by slicing the path, because `_join` sends the probe
    through `urljoin` and the two have to agree: `urljoin` drops a
    document-shaped last segment, so `/index.php` belongs to `/` and slicing
    instead made the gate believe it had measured `/index.php` as a directory —
    every link beside the probe, in the very directory the probe answered from,
    came back "outside the only directory this run measured".

    A trailing slash makes a URL its OWN directory: `urljoin("/about/", ".")` is
    `/about/`, not `/`. That is not a quirk to work around, it is what the
    servers do, and it is why one probe per run could never classify a modern
    menu — nearly every link in one is a directory of its own.
    """
    if not url:
        return ""
    return split_url(join_url(url, ".")).path or "/"


@dataclass(frozen=True)
class _InventedBase:
    """Where this audit puts the URLs it makes up, and the base it would not use."""

    url: str
    # The `<base href>` the document declared and this audit declined to invent
    # URLs under, verbatim. Empty when the document declared none or when the
    # declared one was usable. Carried rather than recomputed because the
    # refusal is silent otherwise, and a silent refusal is how a report came out
    # `[PASS] all 0 navigation links followed, none broken` over a menu of three.
    refused: str = ""


def _invented_base(document_url: str, base_href: str | None) -> _InventedBase:
    """The DIRECTORY the URLs this audit makes up are joined onto.

    Invented URLs are the conventional trust paths (`ABOUT_PATHS`,
    `CONTACT_PATHS`) and the not-found probe that anchors the run. A URL the
    document actually WROTE is not one of them: it is resolved by
    `_resolve_target` against `resolve_base`, and the two questions have
    different answers that no single expression covers.

        as_base(url)                 which DIRECTORY the install lives in
        resolve_base(url, base_href) what urljoin resolves a RELATIVE href against

    At `final_url = http://h/app` those are `http://h/app/` and `http://h/`,
    because `urljoin` treats `/app` as a file and replaces it. Four commits ran
    aground on the belief that one directory — any one directory — could carry
    the not-found answer for a whole run:

      * `as_base(resolve_base(...))` is right here and wrong for the links. It
        put `<base href="/app">`'s links at `/sobre` and its probe at `/app/…`,
        and then let the probe's answer classify the links anyway.
      * `resolve_base(...)` unwrapped is right for the links and wrong here. A
        Next.js `trailingSlash:false` install at `/app` — `/app/` 308s to `/app`,
        `/app/` soft-404s, the root answers honestly — put the probe at the root,
        which said "this host spends a 404 on a missing page", and three dead
        absolute links under `/app/` came back `[PASS]`, exit 0.
      * an invented base gated by CONTAINMENT passed 40 of 40 apex-document
        shapes crossed with ordinary subdirectories, because when the probed
        directory is `/` containment is the whole site and the per-request gate
        is a no-op. `nav=MISSING` refusing five links became `nav=OK` refusing
        none, three of them serving the not-found template.

    None of them is a fix, because the defect is not the expression, it is the
    inference: "contained in the probed directory" is not "measured by the
    probe". So this function answers only the question it is named for — where do
    the INVENTED URLs go, and therefore which directory anchors the run — while
    `_NotFoundProbes` asks EVERY directory a response came from about itself and
    `_probe_covers` refuses to let one directory's answer classify another's. A
    navigation link in a directory the ceiling refused is MISSING, which is what
    this package already does with a condition it could not observe.

    The cost of `as_base` here is stated rather than hidden: on a base with no
    trailing slash the guesses go one segment deeper than a browser resolves a
    relative href — `<base href="/a/b/c">` guesses `/a/b/c/about` where a link
    `about` lands at `/a/b/about`. That direction is deliberate: it is what puts
    the conventional trust paths inside the install rather than at the apex. It
    no longer decides anything about the links, because the links are judged by
    the directories they themselves landed in.

    Clamped to the audited site, which `resolve_base` deliberately does not do —
    an off-site `<base href>` is a TRUE statement about where the links point,
    and refusing to follow them is the same-site filter's job at each caller. An
    invented URL is different: it is not something the document wrote, and
    `crawl` already refuses an off-site seed rather than fetch a host the
    operator did not name under a robots.txt that was never read for it. Auditing
    a staging host whose template still carries `<base href="https://www.example
    .com/">` sent every probe to the production host and let a third party's
    regime decide staging's verdict, with zero probes reaching the host under
    audit.

    The clamp runs BEFORE `as_base`, and that order is load-bearing. `as_base`
    prefixes `https://` to anything without `://`, so `<base
    href="mailto:contato@127.0.0.1:61081">` became a live connection to
    `https://mailto:contato@127.0.0.1:61081/` — an address assembled out of the
    markup — which the report then printed as the URL the audited host answered.

    The scheme is checked as well as the host, which is the same guard both link
    loops already apply to a resolved href and for the same reason: `same_site`
    compares hosts and ports and deliberately ignores the scheme, so
    `<base href="ftp://the-audited-host/">` passes it. A transport this client
    cannot speak is not a missing page — every conventional path came back
    "No connection adapters were found" and the report blamed the site for URLs
    it could never have been asked over HTTP.
    """
    resolved = resolve_base(document_url, base_href)
    if split_url(resolved).scheme in ("http", "https") and same_site(resolved, document_url):
        return _InventedBase(as_base(resolved))
    # `resolve_base` with no href is the document's own URL, credentials off —
    # the same fallback a document declaring no base gets.
    return _InventedBase(
        as_base(resolve_base(document_url, None)), refused=(base_href or "").strip()
    )


def _probe_covers(probe: _NotFoundProbe, url: str) -> bool:
    """Whether THIS probe's answer describes the response that came back from `url`.

    A probe asked one path in one directory, so what it learned is a fact about
    that directory's router and not about the host. Letting it classify a
    response from anywhere else is the conflation `_invented_base` describes,
    and it is what no choice of a single base can fix: an ABSOLUTE link lands
    where it says, under neither base, and a directory nobody probed can
    soft-404 while a probed one is honest. That shape reported `[PASS]` over dead
    links at every commit before this one.

    EQUALITY of directory, not containment, and that is the whole correction.
    Containment reads "contained in the probed directory" as "measured by the
    probe", which is an inference and not an observation — and when the probed
    directory is `/`, containment is the entire site and this gate becomes a
    no-op. Measured: 8 apex-document spellings crossed with 5 ordinary
    subdirectories gave a false PASS in 40 of 40, `nav=MISSING` refusing five
    links turning into `nav=OK` refusing none while three of them served the
    not-found template.

    Equality would be far too strict with ONE probe per run — every `/blog/post`
    on an ordinary site unverified — which is why it arrives together with
    `_NotFoundProbes`: each directory a response came from is asked about
    itself, up to `MAX_PROBED_DIRECTORIES`. Narrowing to the probe's segment
    DEPTH instead was considered and is wrong in the other direction: on the
    fixture EXAMPLES.md documents the home is `/` and every navigation link sits
    at depth 2 or 3, so that rule reports MISSING on an ordinary site.

    `url` must be where the response CAME FROM — `final_url`, not what was asked
    — because a link that redirects into another directory was answered by a
    router this probe never asked.

    Two shapes fall on the wrong side of the comparison, both toward MISSING and
    neither toward a pass. A directory whose name is non-ASCII AND comes from the
    markup rather than from the typed URL: `probe.base` keeps the raw `<base
    href>` text while `final_url` comes back percent-encoded, so they do not
    match. That is a decoding fault older than this gate — the parser turns `ç`
    into `Ã§` here at every commit on this branch — and normalizing the strings
    here would hide it rather than fix it. A directory typed as the target is
    unaffected, because both sides are already encoded. And paths differing only
    in case: RFC 3986 says they are different paths, so this follows it, which
    costs a false MISSING on a case-insensitive server.
    """
    # `same_site` is what refuses a probe nobody sent: `site_host("")` is empty
    # and it returns False for it, so the sentinel `_NotFoundProbe("honest")`
    # covers nothing. A separate `if not probe.base` above this said the same
    # thing twice, and the copy was unkillable — no input could reach it with the
    # first check in place, so loosening it changed nothing and broke no test.
    if not same_site(url, probe.base):
        return False
    return _directory_of(url) == probe.directory


def _record_refused_base(
    report: _Verdict, invented: _InventedBase, moved_by_base: list[str]
) -> None:
    """Say that a declared `<base href>` was not used, and what it cost.

    Without this the refusal is invisible. A home declaring
    `<base href="https://www.example.com/">` over a menu of three relative links
    printed `[PASS] … all 0 navigation links followed, none broken`: the base
    carried all three off-site, the same-site filter dropped them, the guessed
    paths quietly went somewhere else, and the report named none of it. A reader
    cannot tell that from a page with no menu at all, which is the difference
    between "nothing to check" and "everything was skipped".

    The severity follows what was actually lost rather than being fixed, because
    the same refusal costs the two callers different things. Links the base
    moved out of reach were declared and never followed — a condition this run
    did not observe, which is MISSING. With no such links there is nothing
    unobserved and the refusal is only worth printing, which is INFO.
    """
    if not invented.refused:
        return
    if moved_by_base:
        report.add(
            Status.MISSING,
            f'The home page declares <base href="{invented.refused}">, which is not an '
            f"http(s) address on the audited site. {len(moved_by_base)} relative link(s) "
            f"resolve through it and were not followed ({', '.join(moved_by_base[:5])}); the "
            f"paths this check guesses were asked at {invented.url} instead",
        )
        return
    report.add(
        Status.INFO,
        f'The home page declares <base href="{invented.refused}">, which is not an http(s) '
        f"address on the audited site, so the paths this check guesses were asked at "
        f"{invented.url} instead",
    )


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
    not_found: _NotFoundProbes | None = None,
) -> TrustPagesReport:
    """Look for About/Sobre and Contact/Contato (ADS-UX-05, ADS-AUTHOR-02 part).

    Candidates come from the home page's own footer and navigation links first —
    a site that links to /pages/quem-eu-sou has an About page, and only reading
    its links can find it — then from the conventional paths, joined onto
    `_invented_base` so a subdirectory install is not silently swapped for the
    domain root. Only the linked candidates are capped; every conventional path
    is tried, so "no candidate answered" is never said about a URL that was never
    requested.

    Each page ends in exactly one of four states, and three of them are not a
    pass: OK, MISSING (nothing answered), WARNING (a stub or a placeholder) and
    ERROR (a 403, a 5xx, a timeout — the page may well exist and we cannot say).
    An access failure is never an approval.

    `not_found` is the run's set of per-directory answers to "what does a URL
    that cannot exist look like here", shared with the navigation check so both
    halves of a report agree and spend one ceiling between them. Given it, a
    candidate answering 200 with exactly the page ITS OWN directory serves for
    nothing is skipped the way `_is_home_again` skips the catch-all. Without it
    this check runs blind — and used to print PASS for `/about` in the same
    report whose navigation line said that very page is what the host serves for
    nothing.

    A page that IS found keeps the status its own content earns, because this
    check does not rest on the status code alone — word count and unfinished
    markers are its own discriminators — so a directory that was not measured
    narrows the evidence without emptying it. Making the page MISSING instead
    would let a ceiling say "this site has no About page", and paired with
    Contact that is the FAIL two lines below: a false FAIL on a healthy site.

    What is recorded beside it is a MISSING FINDING saying the page was judged
    on its content rather than on its status code — the same weight
    `count_broken_nav_links` gives the identical observation, because giving one
    observation two weights in one report is how the two halves of this module
    came to contradict each other. At INFO, which is what this note used to be,
    `exit_code` returns 0 and the run succeeded with the note unread.

    The annotation is per PAGE and keyed to the page's own directory, which is
    the correction the per-directory design forces. Under one probe gated by
    containment it was emitted only for a page "outside" the probed directory,
    and with the probe anchored at `/` no page was ever outside: `[PASS] about
    page` printed clean and silent while resting on a not-found template with
    prose. The residual hole is stated rather than closed: such a page still
    passes, and the sentence plus the non-zero exit is what sends a human to
    look at it.
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

    moved_by_base: list[str] = []
    for kind, (label, paths, hint) in _TRUST_KINDS.items():
        candidates = _candidates(
            home, home_doc, paths, hint, max_linked_candidates, moved_by_base
        )
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

    _record_refused_base(
        report,
        _invented_base(home.final_url or home.url, home_doc.base_href),
        _dedupe(moved_by_base),
    )

    # One line PER PAGE, keyed to the page's own directory, and no host-level
    # line at all — because there is no host-level fact to state. A run measures
    # several directories and they disagree: `/app/` honest while `/loja/`
    # soft-404s is the ordinary shape, not an exotic one. The single host line
    # this replaces said "the trust pages above were judged on the content they
    # served" over pages that had in fact been judged on a measured 200, and
    # said nothing at all about the one page whose directory was never measured.
    for kind, outcome in report.pages.items():
        if outcome.url is None or not_found is None:
            continue
        # `measured`, never `for_url`: this pass reports what the run already
        # consulted. Sending a probe here would spend a request to print a
        # sentence about a directory no classification ever used, and would let
        # the ceiling be crossed by the report rather than by the check.
        probe = not_found.measured(outcome.url)
        if probe is not None and probe.trustworthy:
            # Measured, and it spends a real status code on a missing page. The
            # 200 behind this page IS evidence, so there is nothing to warn
            # about and the report stays quiet.
            continue
        # Otherwise the page was judged on its words alone. The hole is real
        # either way: a linked candidate at an absolute `/loja/sobre` served by
        # a soft-404 template is long enough to read as prose and comes back
        # [PASS], so the sentence is the only thing that sends a human to look.
        label = _TRUST_KINDS[kind][0]
        # MISSING, not INFO, and that is a decision this design makes rather
        # than inherits. It is the SAME observation `count_broken_nav_links`
        # records — a 200 that was not shown to mean anything — and giving one
        # observation two different weights in one report is how the two halves
        # of this module came to contradict each other in the first place. At
        # INFO `exit_code` returns 0, so a run whose About page rests on a
        # not-found template with prose exited successfully with a note nobody
        # had to read. It does NOT reach the page's own status, so it cannot
        # manufacture the "Neither an About nor a Contact page was found" FAIL
        # out of a ceiling: the page was read and judged, and what is missing is
        # the evidence that its status code meant anything.
        if probe is None:
            report.add(
                Status.MISSING,
                f"{label} page at {outcome.url} answered HTTP 200 from a directory this run "
                f"did not measure (at most {MAX_PROBED_DIRECTORIES} are), so what a missing "
                "page looks like there is unknown and this page was judged on the content it "
                "served rather than on its status code",
            )
            continue
        report.add(
            Status.MISSING,
            f"{label} page at {outcome.url} answered HTTP 200 from a directory that also "
            f"answers HTTP 200 for {probe.url}, which does not exist — so it was judged on "
            "the content it served rather than on its status code",
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
    paths: tuple[str, ...],
    hint: re.Pattern[str],
    max_linked: int,
    moved_by_base: list[str] | None = None,
) -> list[_Candidate]:
    """Linked candidates first (footer, then nav, then body), conventions after.

    The cap applies to the linked half only. Sharing one budget let a handful of
    matching footer links push the conventions out of the list entirely, and cut
    the conventions themselves mid-tuple.

    The two halves are joined onto DIFFERENT bases, and that is the point rather
    than an oversight: a linked candidate is an address the document wrote, so
    `urljoin` decides where it lands, while a conventional path is one this
    module made up, so it goes in the install's directory. Making them agree by
    picking one expression for both is what broke three commits in a row — see
    `_invented_base`. What makes the difference safe is that neither half is
    judged by the probe unless `_probe_covers` says the probe measured where it
    landed.
    """
    home_url = home.final_url or home.url
    # The same base `_nav_targets` and the crawler resolve against. A home
    # carrying `<base href="/app/">` declares its About page at `/app/sobre`;
    # joining against `home_url` alone requested `/sobre`, got a 404, and the
    # report then said "No About page found" — the one claim this check's
    # docstring forbids, made about a URL nobody ever asked for.
    #
    # Unclamped on purpose, unlike the conventional paths below: an off-site
    # `<base href>` really does move the document's own links off-site, and the
    # `_same_site` guard in the loop is what declines to follow them. Clamping
    # here would invent an on-site address for a link that points elsewhere and
    # then report the audited host's answer as that link's.
    link_base = resolve_base(home_url, home_doc.base_href)
    # The conventional paths are guesses this module writes, not links the
    # document wrote, so they go where every other invented URL goes.
    guess_base = _invented_base(home_url, home_doc.base_href).url
    by_region: dict[str, list[str]] = {"footer": [], "nav": [], "body": []}
    for link in home_doc.links:
        href = link.href.strip()
        if not href or href.startswith("#"):
            continue
        if fold(href).startswith(("mailto:", "tel:", "javascript:")):
            continue
        target = _resolve_target(link_base, href)
        relative = not split_url(href).scheme and not href.startswith("//")
        if split_url(target).scheme not in ("http", "https"):
            # The guard `_nav_targets` has always had and this loop had not. A
            # scheme this client cannot speak is not a missing page: `<base
            # href="ftp://ex.com/">` handed `fetch` an `ftp://` URL, which came
            # back ERROR ("no connection adapters") and the About page was
            # reported as a fault of the site. Before the base was honoured only
            # an explicit `<a href="ftp:…">` could reach here; now every relative
            # href in the document can, so the two loops agree about it.
            if moved_by_base is not None and relative:
                moved_by_base.append(href)
            continue
        if not _same_site(target, home_url):
            # Relative only, for the reason `_nav_targets.dropped` states: a
            # link written out in full to another host is off-site whatever the
            # base says, and only the base can carry a relative one off-site.
            if moved_by_base is not None and relative:
                moved_by_base.append(href)
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
        url = _join(guess_base, path)
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
    not_found: _NotFoundProbes | None = None,
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
    # The probe URLs that excluded a candidate for BEING the not-found page.
    # Collected so the MISSING reason can say so: "no candidate answered 200"
    # is false about a run where every candidate answered 200 and was thrown
    # out, and this check's own docstring forbids saying nothing answered about
    # a URL that did. It also carries the only mention of the probe left in a
    # report where no trust page was found, now that the host-level line is gone.
    served_not_found: list[str] = []
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
        # The answer for the directory THIS candidate came back from. Asked here
        # and not once for the whole check, because a linked `/loja/quem-eu-sou`
        # is served by a different router than the conventional `/sobre` beside
        # the home page, and a fingerprint taken in one of them recognises
        # nothing in the other. Only reached on a candidate that answered 200:
        # the 404/410 and the not-ok branches above return first, so a guess
        # that misses costs no invented request.
        landed = response.final_url or response.url
        # A candidate the DOCUMENT wrote is asked about where it landed; one this
        # audit INVENTED is asked about the base it was invented from. `/about/`
        # is a guess that a directory exists, so probing inside it presumes the
        # thing under test — and if it does not exist, what answered was the
        # base's router, which the base's probe already describes. Measured: on a
        # soft-404 host the answer taken at `/` recognises the responses to
        # `/about/`, `/sobre/`, `/contact/` and `/pages/about` alike, because one
        # catch-all serves them all.
        #
        # It is also what keeps the guesses from starving the site's own links.
        # The conventional tuples carry four trailing-slash spellings and
        # `pages/`, so asked per address they claimed SIX of the eight slots
        # before a single navigation link was judged — the audit spending its
        # ceiling on its own guesses, which are worse evidence than what the
        # site publishes.
        probe = None
        if not_found is not None:
            probe = (
                not_found.for_url(landed) if candidate.declared else not_found.anchor_probe()
            )
        if probe is not None and _is_not_found_page(probe, response):
            # The same move one line up, against the other page a catch-all
            # answers with. Used HERE, as an exclusion, this equality is sound:
            # the worst it can do is keep looking at the next candidate and end
            # at MISSING, which is the honest answer when the only thing found
            # was the not-found page. Used as an accusation it is not sound,
            # which is why count_broken_nav_links refuses to fail a link on it.
            attempts.append(
                (
                    response.final_url,
                    f"served the page its directory answers with for {probe.url}, "
                    "which does not exist",
                )
            )
            served_not_found.append(probe.url)
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
    if served_not_found:
        return PageOutcome(
            kind=kind,
            status=Status.MISSING,
            reason=f"no candidate was a page ({len(attempts)} tried); "
            f"{len(served_not_found)} answered HTTP 200 with exactly the page their own "
            f"directory serves for a URL that does not exist, such as {served_not_found[0]}",
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
    """Whether this response IS the page its own directory serves for a missing URL.

    `probe` must be the answer for the directory the response CAME FROM. A
    fingerprint taken elsewhere recognises nothing here, and comparing against
    one would be the same inference this module dropped everywhere else — the
    direction is safe (it only ever excludes a candidate) but the sentence it
    writes into `attempts` would name a URL that never described this page.

    Only ever an answer where the not-found page was pinned down, and only ever
    used to stop treating a response as the page that was asked for. Never used
    to declare one broken — see `count_broken_nav_links`.
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
# What HTTP 200 is worth in one directory of this host
# --------------------------------------------------------------------------

# How a DIRECTORY answers for a URL that does not exist, which is what decides
# whether the status codes coming out of it carry any information at all. Not a
# fact about the host: a WordPress install under `/blog/` and a static apex
# answer this question differently, and reading one off the other is the
# inference four commits in a row were wrecked by.
ProbeRegime = Literal["honest", "fingerprint", "opaque"]


@dataclass(frozen=True)
class _NotFoundProbe:
    regime: ProbeRegime
    # The visible text this directory serves for a URL that does not exist. Set
    # in the "fingerprint" regime only, and never empty — an empty document
    # matches every content-free response, which is evidence of nothing.
    fingerprint: str = ""
    url: str = ""
    # The address this answer was measured through, and the only place it
    # describes. Carried on the probe rather than recomputed by each consumer
    # because the probe travels: `check_completeness` measures a directory once
    # and both sub-checks read the same object, and a consumer deriving the base
    # a second time is how the two halves of this module came to disagree about
    # one document in the first place. Empty on a probe nobody sent, which covers
    # nothing.
    base: str = ""
    # Why a 200 from this directory says nothing about the page behind it. Set in
    # BOTH non-honest regimes, because in both of them that is the fact the
    # report has to print: "fingerprint" narrows down what the not-found page
    # looks like, it does not restore the meaning of the status code.
    reason: str = ""

    @property
    def trustworthy(self) -> bool:
        """Whether HTTP 200 from this directory is evidence that a page is there."""
        return self.regime == "honest"

    @property
    def directory(self) -> str:
        """The one directory this answer describes.

        Derived from `base` rather than stored beside it, so the two cannot
        drift. `df55766` stored the pair and they disagreed: the probe was SENT
        through `urljoin`, which drops a document-shaped last segment, while the
        gate sliced the path — so a base of `/index.php` sent the probe into `/`
        and then judged coverage as though `/index.php` were a directory.
        """
        return _directory_of(self.base)


class _NotFoundProbes:
    """One not-found answer per directory, asked on demand, bounded per run.

    The unit of the answer is a directory's router, and an audit requests URLs
    in several directories: the ones it invents in `_invented_base`, the ones a
    relative href resolves into, and wherever an absolute link or a redirect
    says. So this asks each directory about ITSELF and hands out that directory's
    answer only — never a neighbour's.

    On demand rather than up front, for two reasons that are both about cost. A
    directory only needs an answer when a response from it actually needs
    classifying, and an observed 4xx/5xx never does: on the documented fixture
    `/pricing/` is a dead link and costs no probe at all. And the directory that
    matters is where the response CAME FROM, which a redirect can change, so a
    list built from the hrefs before the requests go out would probe the wrong
    places and still miss the right ones.

    The anchor — `_invented_base`, where the conventional trust paths and the
    run's own probe paths go — is the exception and is asked eagerly by the
    callers that have a menu to sort. It is the one directory the report must be
    able to speak about whatever the links did, and it is where a soft-404
    template has to be pinned down for `_is_not_found_page` to exclude it as a
    trust-page candidate.

    `MAX_PROBED_DIRECTORIES` is the ceiling and the reasoning for the value is
    on the constant. Past it nothing is inferred: `for_url` returns None, the
    directory is recorded in `refused`, and the caller reports the response as
    unmeasured — MISSING, neither working nor broken. Another ORIGIN is refused
    too and never counted against the ceiling, because sending an invented URL
    to a host the operator did not name is what the clamp in `_invented_base`
    exists to prevent; a link redirecting to a third party is unmeasured, not
    probed.

    The answers are held as a LIST and matched with `_probe_covers`, not looked
    up by a directory string this class computes for itself. That is deliberate
    and it is what keeps one predicate in charge: `_probe_covers` decides both
    which measured answer applies to a response AND, by finding none, that a
    directory still has to be asked. A dictionary keyed on the directory would
    make the gate ornamental — the key would already guarantee the match, so
    loosening the predicate to `startswith` would change no behaviour and no
    test, which is exactly how a gate rots into a comment. With the scan, a
    loosened predicate hands one directory's answer to a response from another
    AND skips the probe that would have caught it. Loosening to `startswith` is
    the case to hold in mind, and `/` is what makes it bite: every directory is
    below it, so one honest apex would answer for the whole site — the false
    pass this design exists to close. A sibling pair like `/blog/` and
    `/blog-antigo/` does NOT separate them, because `_directory_of` always emits
    the trailing slash and `/blog-antigo/` does not start with `/blog/`.
    """

    def __init__(
        self,
        anchor: str,
        *,
        session: requests.Session,
        timeout: float,
        limit: int = MAX_PROBED_DIRECTORIES,
    ) -> None:
        self.anchor = anchor
        self._session = session
        self._timeout = timeout
        self._limit = limit
        self._measured: list[_NotFoundProbe] = []
        # Directories a response came from that the ceiling refused. A list and
        # not a count, because "these addresses went unjudged" is only actionable
        # if the report can name where.
        self.refused: list[str] = []

    def anchor_probe(self) -> _NotFoundProbe | None:
        """The answer for the directory this audit invents URLs in, asked once.

        None when the ceiling was already spent elsewhere. Returning a sentinel
        `_NotFoundProbe("opaque")` here instead would put a REGIME on the report
        for a directory nobody measured — a claim about the host invented by the
        type signature, which is the shape of defect this module keeps finding.
        """
        return self.for_url(self.anchor)

    def measured(self, url: str) -> _NotFoundProbe | None:
        """The answer that covers `url` IF one was already measured. Never asks.

        For the second pass over results, where asking would spend a request to
        learn what is already in hand — and, worse, would let a report line
        exist for a directory the run's own classification never consulted, or
        push the ceiling over on behalf of the report rather than the check.
        """
        for probe in self._measured:
            if _probe_covers(probe, url):
                return probe
        return None

    def for_url(self, url: str) -> _NotFoundProbe | None:
        """The answer that covers `url`, asking its directory if none does yet.

        None means this run did not measure there and will not guess: the origin
        differs, or the ceiling was already spent.
        """
        covering = self.measured(url)
        if covering is not None:
            return covering
        # Only after no measured answer covers it, so the same-origin refusal
        # cannot hide an answer that was already paid for.
        if not same_site(url, self.anchor):
            return None
        directory = _directory_of(url)
        if not directory:
            return None
        if len(self._measured) >= self._limit:
            if directory not in self.refused:
                self.refused.append(directory)
            return None
        # The anchor keeps the address `_invented_base` produced, so the report
        # names the base the operator can check and a document-shaped base is
        # resolved by `_join` exactly as `_directory_of` resolved it. Every other
        # directory is probed through the directory itself.
        base = self.anchor if directory == _directory_of(self.anchor) else join_url(url, ".")
        probe = _probe_not_found(base, session=self._session, timeout=self._timeout)
        self._measured.append(probe)
        return probe


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
    """Ask ONE DIRECTORY what it serves for a URL that does not exist.

    `count_broken_nav_links` decided on `status_code >= 400` alone, and a CMS
    answering 200 with its "page not found" template — the soft 404, and it is
    common — reported `broken 0` over a menu whose every link goes nowhere. A
    status code is only evidence when the server is willing to spend one on a
    page it does not have, and the only way to learn whether it does is to ask
    for a page it cannot have. The same reasoning already runs for robots.txt in
    `crawl._load_robots`, which refuses to parse an HTML body as rules.

    Three answers, and only the first of them restores the meaning of a 200:

      * `honest` — the probe got 4xx/5xx. This directory spends a status code on
        a missing page, so a link answering 200 from it is a page. **One
        request**, and it is the common case: the second probe is never sent.
      * `fingerprint` — both probes got the same status and the same visible
        text. That text is what this directory serves for nothing, so a response
        carrying it is worth naming. It is NOT worth failing a link over: the
        same equality holds for a real route rendering the site's "nothing
        here" partial, and it is the equality `_is_home_again` uses to skip a
        candidate rather than to condemn one.
      * `opaque` — the directory answers 200 for a URL that does not exist but
        not with a recognisable page (the template echoes the requested address,
        or carries no text), or the probe could not be made at all.

    The regime describes the DIRECTORY `base` resolves into and nothing wider.
    Reading it as a fact about the host is the whole defect `_NotFoundProbes`
    exists to prevent, and it is not a hypothetical: a Next.js install at `/app`
    soft-404s while the apex above it answers honestly, and every commit that
    read one off the other printed `[PASS]` over a dead menu.

    `fingerprint` is also a weaker fact than it looks: it says two samples
    matched, not that the template is stable. Where the error page carries a
    timestamp or a request id the two probes match or not depending on where a
    second boundary fell, so anything a caller decides differently between
    `fingerprint` and `opaque` is a verdict decided by a coin. In
    `count_broken_nav_links` nothing does — both mean "HTTP 200 proves nothing
    here", carry the same weight and never reach `count`, so only the printed
    sentence differs. `check_trust_pages` does treat them differently, through
    `_is_not_found_page`, and that is why the second path is asked in every
    directory that earns it rather than once per run: see `NOT_FOUND_PROBE_PATHS`.
    """
    first_url = _join(base, NOT_FOUND_PROBE_PATHS[0])

    def opaque(reason: str) -> _NotFoundProbe:
        return _NotFoundProbe("opaque", url=first_url, reason=reason, base=base)

    first = fetch(first_url, session=session, timeout=timeout)
    if first.error is not None:
        return opaque(
            f"{first_url} could not be fetched ({first.error}), so what this directory "
            "answers for a URL that does not exist is unknown"
        )
    if first.status_code is None:
        return opaque(f"{first_url} answered without a status code")
    if first.status_code in _ROUTED_TO_NOTHING:
        return _NotFoundProbe("honest", url=first_url, base=base)
    if first.status_code >= 400:
        # A refusal is not an answer about routing. `>= 400` used to be enough,
        # and a WAF is exactly the thing that 403s a path shaped like a scan —
        # which is what the probe path looks like. With every other URL soft-404
        # ing, one 403 here read as "this directory spends a status code on a
        # missing page" and a menu of five dead links came back OK, About and
        # Contact passing over the error template. 401, 429 and 5xx say the same
        # nothing: the request never reached the router whose behaviour is the
        # question.
        return opaque(
            f"{first_url} does not exist and this host answered HTTP "
            f"{first.status_code} for it, which refuses the request rather than "
            "routing it: what this directory does with a missing page is unknown"
        )

    # Past here this DIRECTORY is a proven soft-404 directory: it answered a
    # success code for a path nothing routes. Not the host — a neighbour may well
    # spend a real 404, and assuming otherwise is the inference this design drops.
    served = f"{first_url} does not exist and this host answered HTTP {first.status_code} for it"
    first_text = _readable_text(first)
    if first_text is None:
        # No second request: whatever it came back with, a blank page can never
        # be the thing other links are recognised by.
        return opaque(
            f"{served} carrying no readable text, and a blank page matches every blank "
            "response, so it cannot be used to recognise the other links"
        )

    # The second request is spent only in a directory that has already
    # misbehaved, and it buys exactly one thing — whether the page served for
    # nothing is the same page every time, and so usable to recognise the other
    # responses from here by. `check_trust_pages` turns that into a verdict
    # through `_is_not_found_page`, which is why it is asked per directory.
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
            "either: this directory's status codes do not say whether a page is there"
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
        base=base,
        reason=f"{served}, so HTTP 200 from this directory does not show that a page is there",
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
    not_found: _NotFoundProbes | None = None,
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

    A link answering HTTP 200 is only a working page where a missing one would
    have got a 404, and that is a fact about the DIRECTORY the response came
    from, not about the host. So `_NotFoundProbes` asks each directory the menu
    actually lands in about itself, and each 200 is judged by the answer for its
    own directory. Pass `not_found` to share one run's answers and one run's
    ceiling with the other sub-check; without it the probes are sent here, and
    only when there is a menu to sort.

      * The directory is `honest`: it 4xx'd a URL that cannot exist, so a 200
        from it is a page and a 4xx/5xx is a broken link. This is the only
        regime in which this function reports anything as working.
      * The directory answered a success code for a URL that cannot exist: then
        no 200 from it is evidence about the page behind it, and every one of
        them is recorded as unverified — `same_as_not_found` when the response is
        exactly the page that directory's probe drew, `unverified` otherwise.
      * The directory was never measured, because `MAX_PROBED_DIRECTORIES` was
        already spent or because the response came from another origin, which
        this audit does not send invented URLs to. Recorded as `unmeasured`.

    All three are MISSING, none reaches `count`, and none can reach
    `BROKEN_NAV_FAIL_THRESHOLD`. The third is a false MISSING on a healthy site
    and it is chosen deliberately: refusing to guess costs a human a look, while
    guessing costs the `[PASS]` over a dead menu that this module exists to make
    impossible. It is also bounded — a run measures up to
    `MAX_PROBED_DIRECTORIES` directories and the report names the ones it
    refused, so the operator can see exactly how much went unjudged and re-run
    against a subdirectory to measure it.

    That rule is deliberately blunt. The sharper version — fail the links
    matching the not-found page, pass the rest — was tried and is wrong twice
    over. It fails working sites: an empty WordPress category, an empty tag and
    a page past the last one render the same `content-none.php` the 404 template
    renders, which is three links and exactly the threshold; on a catch-all
    serving the home page, `/index.php` and `/pt/` are real routes. And it is not
    reproducible: whether the two probes matched decides whether the comparison
    happens at all, so a host whose error page carries a clock returned OK,
    WARNING or MISSING for the same dead menu depending on the run.

    A 4xx/5xx is untouched by any of this and still feeds the threshold in every
    directory: that one was observed, not inferred. It also costs no probe —
    a dead link needs no oracle — which is why the documented fixture's
    `/pricing/` adds nothing to the invented-request count. Links that fail at
    the transport layer stay `unresolved` and ERROR.

    What it still misses, stated rather than hidden: a directory that 404s the
    probe path but soft-404s DEEPER inside itself — `/blog/` honest, `/blog/x/y`
    not — is read as honest for the responses that came from `/blog/` itself.
    The unit is the directory, so a router that disagrees with itself within one
    is invisible here. The probe paths are fixed and published in
    `NOT_FOUND_PROBE_PATHS`, so a host could special-case them; nothing here
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
    invented = _invented_base(home_url, doc.base_href)
    moved_by_base: list[str] = []
    targets = _nav_targets(doc, home_url, moved_by_base=moved_by_base)
    if not targets:
        # No <nav>/<footer> markup at all is common on hand-written pages; fall
        # back to every internal link and say so, rather than reporting a clean
        # zero for links that were never looked at.
        moved_by_base.clear()
        targets = _nav_targets(doc, home_url, regions=None, moved_by_base=moved_by_base)
        report.used_all_links = bool(targets)

    report.found = len(targets)
    _record_refused_base(report, invented, moved_by_base)

    # Shared with the other sub-check when the caller has one, so both halves of
    # a report read the same answers and spend one ceiling between them.
    probes = not_found
    if probes is None:
        probes = _NotFoundProbes(invented.url, session=sess, timeout=timeout)
    # Reported from the cache when the caller already asked, so a run with no
    # menu at all still says what the anchor answered — `check_completeness`
    # asks it for the trust paths regardless, and dropping the line here made a
    # report go silent about a host it had in fact measured.
    anchor = probes.measured(probes.anchor)
    if anchor is None and targets:
        # Asked eagerly, and only when there is something to sort: on a menu of
        # nothing the answer would decide nothing and the request would be spent
        # against someone else's host for free. It is measured against the host
        # that actually ANSWERED, not the one the operator typed: apex -> www is
        # two hosts, and asking the first what it serves for a missing page and
        # then judging the second's pages by it is the same category error one
        # origin up.
        anchor = probes.anchor_probe()
    if anchor is not None:
        report.not_found_regime = anchor.regime

    for url, text in targets[:limit]:
        report.checked += 1
        response = fetch(url, session=sess, timeout=timeout)
        if response.error is not None:
            report.unresolved.append(NavLink(url=url, text=text, reason=response.error))
            continue
        code = response.status_code
        if code is not None and code >= 400:
            # Observed off the wire, in any directory. This is the only branch
            # that is allowed to call a link broken — and it runs BEFORE any
            # probe is asked for, so a dead link costs no invented request.
            report.broken.append(
                NavLink(url=url, text=text, status_code=code, reason=f"HTTP {code}")
            )
            continue
        # Where the response CAME FROM decides which directory answers for it. A
        # link that redirects into another directory was answered by a router
        # nobody asked about yet, and asking about the requested URL instead let
        # `/app/velho -> /loja/novo` be judged by `/app/`'s honest answer.
        landed = response.final_url or response.url
        # `for_url` hands back an answer only when `_probe_covers` accepted it
        # for THIS url, so there is no second check to make here: re-testing the
        # same predicate would be a branch no input can take, and a branch no
        # input can take is a comment that looks like a guard.
        probe = probes.for_url(landed)
        if probe is None:
            # Nothing was measured where this came from: the per-run ceiling on
            # probed directories was already spent, or the response came from
            # another origin, which this audit does not send invented URLs to.
            # Refusing to guess is the point — an absolute link into a sibling
            # install is the shape that matters, and letting one directory's
            # honest answer speak for another printed `[PASS]` over three dead
            # links at every commit before this one.
            report.unmeasured.append(
                NavLink(
                    url=url, text=text, status_code=code,
                    reason=f"HTTP {code} answered from {landed}, in a directory this run did "
                           f"not measure (at most {MAX_PROBED_DIRECTORIES} are), so what a "
                           "missing page looks like there is unknown",
                )
            )
            continue
        if probe.trustworthy:
            continue
        # This directory answers a success code for a URL that cannot exist, so
        # this 200 shows nothing about the page behind it. Which of the two lists
        # it lands in changes the sentence and not the weight.
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

    # Copied out of the shared probe set, so the report carries the ceiling's
    # own record rather than a number reconstructed from the link lists — which
    # cannot distinguish "the ceiling bit" from "the link went off-origin".
    report.refused_directories = list(probes.refused)

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
            "the page the directory they came from serves for a URL that does not exist. That "
            "is what a dead link looks like there — and also what a real page sharing the "
            "site's \"nothing here\" template looks like, so it is recorded as unverified "
            f"rather than broken and a human has to open one: {listed}",
        )
    if report.unverified:
        listed = ", ".join(link.url for link in report.unverified[:5])
        report.add(
            # MISSING for the same reason truncation is: the condition was not
            # observed. Not OK, because a 200 out of a directory that answers 200
            # for everything demonstrates nothing; not WARNING or FAIL, because
            # nothing here was shown to be broken and inventing three failures
            # out of a routing quirk would print "abandoned" over a working site.
            Status.MISSING,
            f"{len(report.unverified)} navigation link(s) answered HTTP 200 but could not be "
            f"shown to lead anywhere: {report.unverified_reason}. Counted as neither working "
            f"nor broken: {listed}",
        )
    if report.unmeasured:
        listed = ", ".join(link.url for link in report.unmeasured[:5])
        refused = ", ".join(report.refused_directories[:5])
        # Named rather than counted: without the directories the reader cannot
        # tell a ceiling that bit from a menu that wandered off-origin, and the
        # remedy differs — re-run against the subdirectory, or nothing to do.
        where = (
            f" The ceiling of {MAX_PROBED_DIRECTORIES} probed directories was reached and "
            f"these went unmeasured: {refused}."
            if refused
            else " None of them came from a directory this audit may probe."
        )
        report.add(
            # MISSING for the same reason the two lists above are: the condition
            # was not observed. Other directories were measured, these answered
            # from one that was not, and a soft-404 template one directory over
            # answers 200 exactly like a page. Calling them working is the
            # `[PASS]` over three dead links this gate exists to stop; calling
            # them broken would invent failures out of links nobody showed to be
            # dead.
            Status.MISSING,
            f"{len(report.unmeasured)} navigation link(s) answered HTTP 200 from a directory "
            "this run did not measure. What a server serves for a URL it does not have is a "
            f"fact about one directory, so these are counted as neither working nor broken: "
            f"{listed}.{where}",
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
    moved_by_base: list[str] | None = None,
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

    def dropped(href: str) -> None:
        """Note a RELATIVE href the base carried somewhere unfetchable.

        Relative only, because those are the ones the base moved: a footer link
        written out in full to twitter.com is off-site whatever the base says,
        and counting it would make the sentence below fire on every ordinary
        site. `//host/x` is not relative for this purpose — it names its own
        host and only borrows the scheme.
        """
        if moved_by_base is not None and not split_url(href).scheme and not href.startswith("//"):
            moved_by_base.append(href)

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
            dropped(href)
            continue
        if not _same_site(target, home_url):
            dropped(href)
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

    # What HTTP 200 is worth, per directory, built once and shared by both
    # sub-checks. Sharing is not an optimisation, it is the invariant: the probes
    # used to be sent inside count_broken_nav_links, which runs second, so
    # check_trust_pages printed [PASS] for /about in the very report whose
    # navigation line said that page is what this host serves for a URL it does
    # not have. One object also means ONE ceiling — the run cannot spend
    # `MAX_PROBED_DIRECTORIES` on trust pages and then another set on the menu.
    #
    # Anchored on the directory this audit invents URLs in, which is where the
    # conventional trust paths go and which is therefore the one directory both
    # halves always have an answer for. It is deliberately NOT claimed to be
    # where every URL of the run lands: the navigation links and the linked trust
    # candidates go where `urljoin` and their own absolute addresses put them,
    # so each of those directories is asked about itself. The anchor is measured
    # against the host that ANSWERED, not the one typed: apex -> www is two hosts
    # and the second one's pages are the ones being judged.
    not_found = _NotFoundProbes(
        _invented_base(home.final_url or home.url, doc.base_href).url,
        session=sess,
        timeout=timeout,
    )
    # The anchor is NOT claimed here with an eager probe. It arrives on demand,
    # like every other directory, and it cannot be crowded out: the conventional
    # trust paths always resolve into it, and the linked candidates that could
    # take slots ahead of them are capped at `MAX_LINKED_CANDIDATES` per kind,
    # which is smaller than `MAX_PROBED_DIRECTORIES`. A reservation here would
    # have been a line no input could exercise — it survived every mutation of
    # itself — and an unexercised line is a claim nobody checks.

    report.trust = check_trust_pages(
        base, session=sess, timeout=timeout, home_page=home, not_found=not_found
    )
    report.nav = count_broken_nav_links(base, limit=nav_link_limit, session=sess, timeout=timeout,
                                        home_page=home, not_found=not_found)
    return report
