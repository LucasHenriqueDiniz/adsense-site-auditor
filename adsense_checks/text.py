"""Visible text out of HTML, and how much of it is actual content.

Two of the original scripts extracted text with a hand-rolled HTMLParser, and
both extractors were broken in ways that made the check they fed always pass.

The blocker was `meta` and `link` sitting in check_duplicates' skip list. Both
are *void* elements: they have no end tag, so `handle_endtag` never fires for
them, the skip counter never comes back down, and every character of the
document is discarded. `get_text()` returned "" for any real page, so the
duplicate detector compared empty strings against empty strings, found nothing,
and printed a clean pass over a site of byte-identical pages. Void elements are
deliberately absent from `DROPPED_ELEMENTS` below: they cannot contain text, so
dropping them buys nothing and — with a stack — costs the whole document.

The counter was the other half of the same mistake. A `<nav>` that is never
closed leaves the counter stuck for the rest of the document (an 800-word
article measured as 0 words, reported thin); a stray `</footer>` with nothing
open drives the counter back to zero and pastes the menu into the word count. A
counter cannot tell nesting from noise. The tree built here can: an end tag
matching nothing open is ignored, and an unclosed tag is closed by its parent.

Third: joining text nodes with a separator splits "Hyper<em>text</em>" into two
words, and dropping nodes shorter than three characters deletes every price,
score and quiz result on the page. Text is therefore accumulated raw and a
separator is inserted only at the boundary of a non-inline element.

Two levels of extraction, because they answer different questions:

  * `extract_text` is what a reader sees — menus, buttons and captions
    included.
  * `main_content_text` is what an editor wrote. It is the level ADS-CONTENT-03
    asks about ("substantial main content, not only navigation"): the old skip
    list left `<title>`, `<header>` and `<aside>` in the count, so a page whose
    body said "Buy now." measured 360 words.

Two things this module refuses to guess, because guessing them is the same
mistake in a new place — a number reported for something nobody observed:

  * A body served as `text/html` with no charset reaches `measure_fetch`
    decoded as ISO-8859-1, requests' fallback. "coração" arrives as
    "coraÃ§Ã£o" and counts as three words, so a 240-word page measures 480 and
    clears a bar it should have missed. The bytes are recoverable and are
    re-decoded here; what cannot be decoded is ERROR, not a word count.
  * A page whose main content the heuristic cannot isolate is measured on the
    whole `<body>`, menu and footer included. That is a different question from
    the one ADS-CONTENT-03 asks, so `measure_depth` reports it as ERROR instead
    of grading the menu as prose. The `<body>` is only the obvious shape of
    that: `<div id="wrapper">` around the same page is a content container, so
    it wins the scoring and looks isolated. What gives it away is the furniture
    left inside the count — see `_furniture`, which also says why an
    `<article>`'s own `<header>` is not that.

What this module does not decide: the intro/body/conclusion structure that
ADS-COMPLETE-02 also requires. It supplies the word count and the main text; a
structure check belongs elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import unquote, urljoin, urlsplit

import requests

from adsense_checks.http import DEFAULT_TIMEOUT, Fetch, fetch
from adsense_checks.status import Status, escalate

# No end tag exists for these, so nothing is pushed on their behalf.
#
# What that buys, measured: the stack comes back empty after any document
# containing one, and the tree keeps the shape the markup describes. What it does
# NOT buy is the text — remove any of the fourteen and `extract_text` returns the
# same string, because `_collect` walks the whole tree and an element left open
# only becomes the parent of its following siblings, whose text is gathered
# either way. The old counter-based extractor is where an unclosed element was
# fatal, and this list outlived it.
#
# It stays for two reasons. The set must remain disjoint from `DROPPED_ELEMENTS`,
# and that IS load-bearing: a void element marked dropped would push a node that
# never pops and take the rest of the document with it. And a parser whose stack
# is honest is one whose next reader can reason about it — a guarantee is worth
# keeping before it is worth being observable in the output.
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Void elements that end a line. Stored as elements, not as separator text, so
# that reading a line of text can stop at them (see `_worded`).
LINE_BREAKS = frozenset({"br", "hr"})

# Elements whose subtree is not visible text. `head` and `title` are here so an
# implicit head (no <head> tag at all, which is legal) is still excluded.
DROPPED_ELEMENTS = frozenset(
    {"head", "iframe", "noscript", "script", "style", "svg", "template", "title"}
)

# Everything not listed here is treated as a boundary between words. The
# trade-off runs one way on purpose: a missing separator welds two words into a
# third that appears on no page, while a spurious one only splits a word in a
# custom element. `br`, `hr`, `img` and `input` are absent — they either are a
# break or occupy space like one. `wbr` is present because it marks a break
# opportunity *inside* a single word ("super<wbr>cali") and must not split it.
INLINE_ELEMENTS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "big",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    }
)

# Containers that can plausibly hold an article body. A text block credits
# only these, past any list structure between them (`LIST_STRUCTURE`); one
# inside anything else scores nothing, which is what keeps a menu from winning.
CONTENT_CONTAINERS = frozenset({"article", "body", "div", "main", "section", "td"})

# Prose that is not written as `<p>`: a list item, a term and its description,
# the question a `<details>` asks. Tool pages, FAQs and how-tos keep most of
# their text in these, and with `<p>` alone scoring, the paragraph scoring saw
# a fifth of it — measured on smallwebapps.com, 2026-10-18: with no `<h1>` to
# widen from, 28 tool pages read a median of 22% of what a reader counts.
TEXT_BLOCKS = frozenset({"dd", "dt", "li", "summary"})

# The structure those blocks hang from. None of it is a place an article lives,
# so a block's credit passes through it to the container that holds the list:
# a `<li>` credits the `<section>` around its `<ul>`, an answer `<p>` inside a
# `<details>` credits the FAQ around it. Before, both credited nothing.
LIST_STRUCTURE = frozenset({"dd", "details", "dl", "dt", "li", "ol", "summary", "ul"})

# A section of the page's outline is a heading and what follows it. The page's
# own `<h1>` heads the whole page, so it vouches for no section in particular.
SUBHEADINGS = frozenset({"h2", "h3", "h4", "h5", "h6"})

# Winning the main-content heuristic with one of these means it did not run:
# they are the page, not a part of it, so the "main content" they return is the
# menu and the footer as well. For the same reason they are the ceiling of
# `_titled_region`: a title and a block that only meet at one of these share
# the page, not a content region.
WHOLE_PAGE_ELEMENTS = frozenset({"#document", "body", "html"})

# Page furniture: the regions HTML marks as explicitly *not* the prose.
#
# `WHOLE_PAGE_ELEMENTS` only catches the wrapper when the wrapper is the body.
# `div` is a content container, so `<div id="wrapper">` around the entire page
# wins the scoring, is not in that set, and reports the menu and the footer as
# main content — the shape of nearly every CMS theme. These tags are how
# `measure_depth` notices that the winner is still holding the furniture.
#
# The tag alone is not the answer, though: see `_furniture` for whose region a
# given one of these turns out to be. An `<article>`'s own `<header>` carries
# the title and the byline, and that is the article, not the page.
BOILERPLATE_ELEMENTS = frozenset({"aside", "footer", "header", "nav"})

# A heading names a section of the page's outline. Inside a region widened to
# reach the title, a branch that holds one is a section of the page; a plain
# branch before the title or after the block could be content or page, and
# `_beside_the_content` does not pretend to know which.
HEADING_ELEMENTS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Opening one of these implicitly closes an open sibling of the same name:
# `<p>a<p>b` is two paragraphs, not a nested one. Only affects which container
# wins the main-content heuristic, never the extracted text.
IMPLIED_END = frozenset({"li", "p"})

# AdSense publishes no word count, so this is a review threshold, not a policy
# line. references/adsense-requirements.md:53 (ADS-CONTENT-03) says "substantial
# enough for users and crawlers" and leaves the number to the auditor. Output
# built from these constants must say "below the configured threshold", never
# "violates ADS-CONTENT-03".
DEFAULT_MIN_WORDS = 300
BORDERLINE_MULTIPLIER = 1.5

# This one *is* in the requirement: ADS-COMPLETE-02 (line 155) states 1200+
# words for each of the three guides/articles it demands.
MIN_ARTICLE_WORDS = 1200

# Below this, a page carrying an external script and an empty mount point is
# read as a client-rendered shell rather than as a thin page.
JS_SHELL_MAX_WORDS = 25
_MOUNT_IDS = frozenset({"__next", "__nuxt", "___gatsby", "app", "main-app", "root"})

_SEPARATOR = "\n"
_WHITESPACE = re.compile(r"\s+")
# A word is a run of letters (any alphabet, accents included) or a run of
# digits. `[^\W\d_]` is the Unicode-aware way to say "letter": a class like
# [a-zA-Z]+ would cut "coração" into "cora" and "o".
# A letter, in any alphabet. A count, a date or a price beside a link is a label
# of it, not the sentence around it: see `_worded`.
_LETTER = re.compile(r"[^\W\d_]")
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*|\d+(?:[.,]\d+)*")
# Invisible characters that sit *inside* a word and would otherwise split it:
# soft hyphen, zero-width space, BOM. Spelled as escapes on purpose — a literal
# here is unreadable in the source and unsearchable in a diff.
_INVISIBLE = str.maketrans({"\xad": "", "\u200b": "", "\ufeff": ""})

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


class _Node:
    """One element in the tolerant tree the parser builds."""

    __slots__ = ("attrs", "content", "depth", "dropped", "parent", "tag")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        parent: _Node | None = None,
        dropped: bool = False,
    ) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.depth = 0 if parent is None else parent.depth + 1
        # Text chunks and child nodes interleaved, so document order survives.
        self.content: list[str | _Node] = []
        self.dropped = dropped


class _Parser(HTMLParser):
    """HTMLParser that builds a tree instead of counting skip levels."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self.open: list[_Node] = [self.root]
        self.has_external_script = False
        self.parse_error: str | None = None

    @property
    def _current(self) -> _Node:
        return self.open[-1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "script" and any(k.lower() == "src" for k, _ in attrs):
            self.has_external_script = True
        if tag in VOID_ELEMENTS:
            # Return before touching the stack, so nothing is left open for an
            # element that has no end tag. The separator is the part that shows
            # in the output: a <br> or <hr> breaks a word boundary, and the
            # elements listed as inline — `wbr` alone, today — must not.
            if tag in LINE_BREAKS:
                # Kept as a childless element rather than the separator text it
                # used to be: `"\n"` from a <br> could not be told from a newline
                # in the source between two tags, and a look for the word beside
                # a link walked across it — "Menu:<br><a>Inicio</a>" read as one
                # sentence. Nothing is pushed, so the stack stays as it was.
                self._current.content.append(
                    _Node(tag, parent=self._current, dropped=self._current.dropped)
                )
            elif tag not in INLINE_ELEMENTS:
                self._current.content.append(_SEPARATOR)
            return
        if tag == "body":
            # An unclosed <head>, <title> or <template> must not swallow the
            # body. Anything still open above <html> is abandoned here.
            while len(self.open) > 1 and self._current.tag != "html":
                self.open.pop()
        if tag in IMPLIED_END and self._current.tag == tag:
            self.open.pop()
        node = _Node(
            tag,
            attrs={k.lower(): (v or "") for k, v in attrs},
            parent=self._current,
            dropped=self._current.dropped or tag in DROPPED_ELEMENTS,
        )
        self._current.content.append(node)
        self.open.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_ELEMENTS:
            # `<meta />` and `<br/>` reach this method through HTMLParser's own
            # handle_startendtag, which is start-then-end. Nothing was pushed
            # for them, so nothing may be popped: the guard is what makes an
            # override of handle_startendtag unnecessary.
            return
        # An end tag with nothing open to match is noise: ignoring it is what
        # stops a stray </footer> from re-enabling text that must stay dropped.
        if not any(node.tag == tag for node in self.open[1:]):
            return
        # Pop through anything left open inside it — malformed nesting closes
        # implicitly instead of jamming the parser. The `> 1` cannot bind: the
        # guard above has already proved a matching tag is open below the root,
        # so the loop always breaks first. It stays as the bound that makes that
        # local, rather than something the next reader has to re-derive.
        while len(self.open) > 1:
            if self.open.pop().tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._current.dropped and data:
            # Stored unstripped and unfiltered: stripping here is what turned
            # "dupli<b>cate</b>" into two words, and a length filter is what
            # deleted every "R$", "5" and "10" from the page.
            self._current.content.append(data)


def _parse(html: str) -> _Parser:
    parser = _Parser()
    if not html:
        return parser
    try:
        parser.feed(html)
        # close() flushes the parser's tail buffer. Without it, a document
        # ending in text that contains a bare '&' within the last 34 characters
        # ("...physics and R&D") never reaches handle_data at all.
        parser.close()
    except Exception as exc:  # recorded, never swallowed — see measure_depth
        parser.parse_error = f"{type(exc).__name__}: {exc}"
    return parser


def _collect(node: _Node, skip: frozenset[_Node] = frozenset()) -> str:
    """Concatenate the subtree's text, separating non-inline elements.

    `skip` drops whole subtrees, named by identity rather than by tag: which
    `<header>` is furniture depends on where it sits, and only the caller knows
    (see `_furniture`). It never drops `node` itself — the caller asked for
    this element's text, and "" for it would read as an empty page rather than
    as a refusal to answer — so a set that happens to hold `node` (every text
    block on the page, when the caller wants one block's own words) is safe.
    """
    out: list[str] = []
    # Iterative: 5000 unclosed <div>s in a malformed page would blow a
    # recursive walk's stack, and this module promises not to crash on those.
    stack: list[str | _Node] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        block = item.tag != "#document" and item.tag not in INLINE_ELEMENTS
        if item.dropped or (item in skip and item is not node):
            # The subtree goes; the boundary it drew stays. Leaving with the
            # separator unwritten is the welding this module exists to stop:
            # `abc<nav>x</nav>def` would come back as "abcdef", one word that
            # is on no page, and one word fewer than the two that are.
            if block:
                out.append(_SEPARATOR)
            continue
        if block:
            stack.append(_SEPARATOR)
        stack.extend(reversed(item.content))
        if block:
            stack.append(_SEPARATOR)
    return "".join(out)


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.translate(_INVISIBLE)).strip()


def _iter_elements(root: _Node):
    """Pre-order walk of the live elements, never entering a dropped subtree."""
    stack = [root]
    while stack:
        node = stack.pop()
        for child in reversed(node.content):
            if isinstance(child, _Node) and not child.dropped:
                stack.append(child)
        if node is not root:
            yield node


def _find_body(doc: _Parser) -> _Node:
    for node in _iter_elements(doc.root):
        if node.tag == "body":
            return node
    return doc.root


def _ancestors(node: _Node | None):
    """`node` and every element above it, up to the document."""
    while node is not None:
        yield node
        node = node.parent


def _is_link(node: _Node) -> bool:
    """An `<a>` that goes somewhere. `<a name="faq">` marks a place; it is not a
    link, and a heading wrapped in one is still a heading."""
    return node.tag == "a" and "href" in node.attrs


# Per element: (has words inside links, has words outside them).
_LinkWords = dict[_Node, tuple[bool, bool]]


def _link_words(root: _Node) -> _LinkWords:
    """For `root` and every live element under it: whether it holds words inside
    links, and whether it holds words outside them.

    One walk down records which text sits inside a link; one walk back up folds
    the two answers per subtree. Built once per document, because asking it per
    paragraph walked every paragraph's subtree again: 2000 unclosed `<div><p>`
    took 4.3 s that way, against 1.0 s before the question existed. Iterative,
    for the same malformed pages `_collect` is.
    """
    order: list[tuple[_Node, bool]] = []
    stack = [(root, any(_is_link(above) for above in _ancestors(root)))]
    while stack:
        node, linked = stack.pop()
        order.append((node, linked))
        stack.extend(
            (child, linked or _is_link(child))
            for child in node.content
            if isinstance(child, _Node) and not child.dropped
        )
    words: _LinkWords = {}
    # Reversed pre-order visits every child before its parent.
    for node, linked in reversed(order):
        worded = any(isinstance(item, str) and _WORD.search(item) for item in node.content)
        inside = linked and worded
        outside = not linked and worded
        for child in node.content:
            if isinstance(child, _Node) and child in words:
                child_inside, child_outside = words[child]
                inside, outside = inside or child_inside, outside or child_outside
        words[node] = (inside, outside)
    return words


def _subtrees(roots: frozenset[_Node]) -> frozenset[_Node]:
    """Every live element at or below `roots`. One walk for all of them, so a
    membership test costs nothing per element asked about."""
    found: set[_Node] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.dropped or node in found:
            continue
        found.add(node)
        stack.extend(child for child in node.content if isinstance(child, _Node))
    return frozenset(found)


def _link_only(roots: list[_Node], words: _LinkWords | None = None) -> frozenset[_Node]:
    """The largest subtrees under `roots` whose every word is link text.

    Link text is navigation by another name. A menu, a widget list, a grid of
    related-tool cards and a logo linked to the home page are text a reader
    clicks rather than reads, and none of them needs a `<nav>` around it to be
    that. The rule is all or nothing on purpose: a share of link text —
    readability's link density — needs a cutoff, and any cutoff here would be
    invented, while "every word" needs none. "Hyper<a>text</a>" has a word
    outside the link, so its subtree is not link-only.

    A link inside running text is not one of them: "Leia <a>este guia</a> e
    volte" is a sentence with a link in it, and its words are the sentence's.
    Cutting them took 100 words out of a 220-word paragraph of a section beside
    the block, and a reader's 475 came out 375. So an inline element with a word
    right beside it stays, whatever it holds, and so does a run of links that a
    sentence goes on after (see `_in_running_text`). Only inline elements can be
    in running text, so a heading asked about as itself (`_page_title`) is
    always judged whole.

    Nor is a heading on its own: `<h2><a href="#parte">` is the heading of its
    section, linked to its own anchor. Inside a link-only card it still leaves,
    because there the card is the subtree found.

    `words` is `_link_words` of a tree holding every root; without it, each
    root gets its own walk.
    """
    if words is None:
        words = {}
        for root in roots:
            words.update(_link_words(root))
    found: set[_Node] = set()
    # Each element travels with where it sits in its block's flow — the block,
    # and the index there of its outermost inline ancestor — so a tag cloud of
    # ten thousand links is not searched once per link.
    pending: list[tuple[_Node, tuple[_Node, int] | None]] = [(root, None) for root in roots]
    runs: dict[tuple[int, int], bool] = {}
    while pending:
        node, at = pending.pop()
        inside, outside = words[node]
        if inside and not outside:
            # A heading wrapped in a link to its own anchor — `<h2><a
            # href="#parte">` in many blog and docs themes — is still the
            # heading of its section. Cut as navigation, its section lost the
            # heading that vouched for it, became doubt, and a 357-word page
            # read ERROR. A heading inside a link-only card still goes with
            # the card: there the card is the subtree found, not the heading.
            if node.tag not in HEADING_ELEMENTS and not _in_running_text(node, words, at, runs):
                found.add(node)
            continue
        for i, child in enumerate(node.content):
            if isinstance(child, _Node) and child in words:
                inherits = node.tag in INLINE_ELEMENTS and at is not None
                pending.append((child, at if inherits else (node, i)))
    return frozenset(found)


def _in_running_text(
    node: _Node,
    words: _LinkWords,
    at: tuple[_Node, int] | None = None,
    runs: dict[tuple[int, int], bool] | None = None,
) -> bool:
    """Whether `node` is inline with a word right beside it.

    Right beside means the nearest thing on either side in the block's flow,
    past spaces and punctuation: text with a word in it, or an inline element
    with words outside links. "Leia <a>o guia</a> e volte" has one on each
    side; "veja o <a>guia</a>." has one on the left. A block ends the flow, so
    `<div><a>Home</a><p>Texto</p></div>` is a link on a line of its own.

    Beside, not merely in the same block: any word in the flow kept a labelled
    list whole — "Tags: <a>pdf</a>, <a>ferramentas</a>, …" counted 160 linked
    words for one word of label. Now the label keeps the link it touches, and
    the rest, with only commas and other links beside them, leave.

    `at` is the block and the index of `node`'s outermost inline ancestor
    there, when the caller knows it; otherwise it is found by walking up.
    """
    if node.tag not in INLINE_ELEMENTS:
        return False
    if at is None:
        item = node
        while item.parent is not None and item.parent.tag in INLINE_ELEMENTS:
            item = item.parent
        if item.parent is None:
            return False
        block = item.parent
        at = (block, next(i for i, c in enumerate(block.content) if c is item))
    block, index = at
    flow = block.content
    return (
        _worded(flow, index, -1, words)
        or _worded(flow, index, 1, words)
        or _sentence_goes_on(flow, index, words, runs)
    )


def _worded(flow: list[str | _Node], index: int, step: int, words: _LinkWords) -> bool:
    """Whether the nearest thing to `flow[index]`, going `step`, carries a word.

    A word here has letters. "<a>Notícias</a> (15)" is the shape of every
    category and archive widget, and a count beside a link made it running
    text: 60 such items counted their 120 link words. Spaces, punctuation and
    bare numbers are looked past, written as text or wrapped in an inline
    element of their own (`<span>:</span>`). A block, a line break or a link
    stops the look.
    """
    j = index + step
    while 0 <= j < len(flow):
        item = flow[j]
        if isinstance(item, str):
            if _LETTER.search(item):
                return True
        elif not item.dropped:
            if item.tag not in INLINE_ELEMENTS:
                return False
            inside, outside = words.get(item, (False, False))
            if outside and _letters_outside_links(item):
                return True
            if inside and not outside:
                return False
        j += step
    return False


def _letters_outside_links(node: _Node) -> bool:
    """Whether `node` holds a letter that is not inside a link."""
    stack: list[str | _Node] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _LETTER.search(item):
                return True
        elif not item.dropped and not _is_link(item):
            stack.extend(item.content)
    return False


def _sentence_goes_on(
    flow: list[str | _Node],
    index: int,
    words: _LinkWords,
    runs: dict[tuple[int, int], bool] | None = None,
) -> bool:
    """Whether `flow[index]` opens a run of links, joined by spaces alone, that
    the sentence goes on right after: "<a>Foo</a> <a>Bar</a> é uma biblioteca".

    `_worded` stops at a link, so the first of two links opening a sentence had
    only a link beside it and was cut. Looking along the run on either side
    would undo what `_worded` stops: "Tags: <a>pdf</a> <a>guias</a>" is a run
    with a word beside it too. The difference is the sentence going on after
    the run — a label introduces a list and ends there, a sentence continues. So
    only the right side counts, past spaces only, and it must start with a
    letter: "… <a>guias</a>. Mais texto" ends at the period. A run joined by
    commas, and one ending its sentence, are left as `_worded` judges them.

    Every link of a run has the same answer, so `runs` keeps it for each one
    the walk passed: asked link by link, a run of 4,000 links joined by spaces
    took 0.96 s to read, against 0.07 s before this rule.
    """
    key = (id(flow), index)
    if runs is not None and key in runs:
        return runs[key]
    passed = [index]
    answer = False
    j = index + 1
    while j < len(flow):
        item = flow[j]
        if isinstance(item, str):
            if item.strip():
                answer = bool(_LETTER.match(item.lstrip()))
                break
        elif not item.dropped:
            if item.tag not in INLINE_ELEMENTS:
                break
            inside, outside = words.get(item, (False, False))
            if outside:
                answer = _letters_outside_links(item)
                break
            if not inside:
                break
            if runs is not None and (id(flow), j) in runs:
                answer = runs[(id(flow), j)]
                break
            passed.append(j)
        j += 1
    if runs is not None:
        for k in passed:
            runs[(id(flow), k)] = answer
    return answer


def _links_home(href: str) -> bool:
    """Whether `href` points at the site root: `/`, or a host with no path.

    Relative to nothing on purpose. `#top`, `?page=2` and `post.html` resolve
    against the page they sit on, and none of them is the root unless the page
    is; without the page's URL, only a root written as a root counts.
    """
    parts = urlsplit(href.strip())
    return parts.path == "/" or (bool(parts.netloc) and parts.path == "")


def _page_title(
    body: _Node, furniture: frozenset[_Node], link_words: _LinkWords, url: str = ""
) -> _Node | None:
    """The page's own `<h1>`: the first one with words that is neither furniture
    nor a logo.

    A logo wrapped in `<h1>` names the site, not the page. Anchoring on it
    stretches the region up to whatever wraps the header, which is the page
    wrapper this module keeps refusing to measure. Inside a `<header>` the
    furniture says so. Inside `<div id="header">` nothing does, except what a
    logo is: link text, every word of it, and every link pointing home. A post
    title linked to its own permalink — some themes print
    `<h1><a href="/post">` on the post itself — is link text too, but it does
    not lead home, and it is the page's title.

    `furniture` is `_furniture(body)` and `link_words` is `_link_words` of the
    document, both of which the caller already holds; `url` is the page's, when
    known (see `_is_logo`).
    """
    for node in _iter_elements(body):
        if node.tag != "h1" or word_count(_collect(node)) == 0:
            continue
        if any(above in furniture for above in _ancestors(node)):
            continue
        inside, outside = link_words[node]
        if inside and not outside and _is_logo(node, url):
            continue
        return node
    return None


def _is_logo(heading: _Node, url: str = "") -> bool:
    """Whether a link-only heading is a logo rather than a title linked to itself.

    With the page's URL the question has an answer: a title links to the page
    it titles, a logo links somewhere else — the root, `/pt-br/`, `/blog/`,
    wherever the site keeps its home. On the root page itself the logo links to
    the page, and is still the logo. Without the URL, only a link to a root
    written as a root can be told apart (`_links_home`), and a logo pointing at
    `/pt-br/` passes for a title: measured on 2026-10-18, a 300-word promo in a
    `div` header then counted as content, 425 words, INFO, around a 120-word
    post. `measure_url` always passes the URL.
    """
    links = [above for above in _ancestors(heading) if _is_link(above)]
    links += [node for node in _iter_elements(heading) if _is_link(node)]
    if not url:
        return all(_links_home(link.attrs["href"]) for link in links)
    page = _page_path(url)
    if page == "":
        return True
    return not all(_page_path(urljoin(url, link.attrs["href"])) == page for link in links)


# A directory's index document, which `/post/` and `/post/index.html` both name.
_INDEX_DOCUMENT = re.compile(r"/index\.[a-z0-9]+$")


def _page_path(url: str) -> str:
    """The path `url` names, spelled one way for every spelling of one page.

    `/post`, `/post/`, `/Post/`, `/post/index.html` and a percent-encoded `/ca%C3%A7a`
    against a literal `/caça` are the same page as far as a link to it goes; a
    literal comparison took a title linked to its own page for a logo and kept
    the undercount, 100 words where a reader counts 562. These are spelling
    conventions of URLs and web servers, not a measure of anything. The root
    comes back empty.
    """
    path = unquote(urlsplit(url.strip()).path).lower()
    return _INDEX_DOCUMENT.sub("", path).rstrip("/")


def _has_heading(node: _Node, skip: frozenset[_Node]) -> bool:
    """Whether `node`, or anything in it outside `skip`, is a heading with words."""
    stack = [node]
    while stack:
        item = stack.pop()
        if item.dropped or item in skip:
            continue
        if item.tag in HEADING_ELEMENTS and word_count(_collect(item, skip)) > 0:
            return True
        stack.extend(child for child in item.content if isinstance(child, _Node))
    return False


def _beside_the_content(
    region: _Node,
    title: _Node | None,
    block: _Node,
    furniture: frozenset[_Node],
    link_words: _LinkWords,
    prose: frozenset[_Node] = frozenset(),
) -> tuple[frozenset[_Node], frozenset[_Node], int]:
    """What a widened region holds besides its title, its block and the path
    between them: what to leave out, what nobody can say is content, and how
    many words of that sit loose, outside any element.

    The climb from the block to the title collects every branch hanging off the
    two paths. Some are the point — the sibling sections the scoring cannot
    see. Others are the page, spelled in `<div>`s the furniture tags never name.
    Measured on 2026-10-18 against the code before this function existed: a
    Twenty-Ten-style theme (logo `<h1><a>` in `div#header`, a `div` menu and
    sidebar) measured 489 words, OK, around a 120-word post, and an `<h1>` hung
    straight in the wrapper beside a plain-text `div` measured 521, OK. Both
    read 120, WARNING, before the widening existed.

    Left out, like furniture:
      * the region's furniture (`_furniture`);
      * every link-only subtree in those branches (`_link_only`): menus, widget
        lists, related-tool grids, however they are tagged.

    Undecided: a branch with no heading that sits before the title or after the
    block. A heading makes a branch a section of the page's outline, and the
    stretch between the title and the block is what the climb was taken to
    collect. Outside both, a `<div>` of plain text is a sidebar or a list of use
    cases, and structure cannot say which. It stays in the count;
    `measure_depth` refuses the count when the verdict depends on it.

    Text sitting loose in the region or in a container on the paths — outside
    every element — is a branch too, with no heading and no link. Before the
    title or after the block it is undecided; between them it counts. Measured
    on 2026-10-18: 400 loose words after a 120-word post read 521, OK, because
    only elements were sorted. A text chunk cannot be named in the skip set the
    way a subtree can, so its words come back as a number instead.

    A region `_prose_region` widened has no title, so nothing lies between: every
    branch is before or after the block. There the climb was taken for prose
    rather than for a title, so a branch that holds prose (`prose`, the elements
    with prose below them) is vouched for the way a heading vouches for one.

    `furniture` is `_furniture(region)`, which the caller already holds.
    """
    paths = set(_ancestors(block)) | (set(_ancestors(title)) if title is not None else set())
    # Document order over what the paths hold: the branches, the loose text,
    # and where the title and the block start. Nothing below a branch, the
    # title or the block is numbered, and the relative order is unaffected.
    position = 0
    marks: dict[_Node, int] = {}
    branches: list[tuple[int, _Node]] = []
    loose: list[tuple[int, str]] = []
    stack: list[_Node | str] = [region]
    while stack:
        item = stack.pop()
        position += 1
        if isinstance(item, str):
            loose.append((position, item))
            continue
        if item.dropped:
            continue
        if item is title or item is block:
            marks[item] = position
            # A title left unclosed swallows the rest of the page and is then
            # the region itself; only then is there anything inside it to sort.
            if item is not region:
                continue
        elif item not in paths:
            branches.append((position, item))
            continue
        elif item in furniture:
            # The block won the scoring from inside the page's furniture — a
            # `<footer>` holding the only `<p>` on the page. The whole subtree
            # is out of the count, so nothing in it is content, doubt or a
            # branch; sorting its loose text subtracted 200 words that were
            # never counted from a count of 402, and reported ERROR.
            marks[block] = position
            continue
        stack.extend(reversed(item.content))
    first, last = sorted((marks[title] if title is not None else marks[block], marks[block]))

    left_out = furniture | _link_only([b for _, b in branches if b not in furniture], link_words)
    undecided = frozenset(
        branch
        for at, branch in branches
        if branch not in left_out
        and not first < at < last
        and not _has_heading(branch, left_out)
        and not (title is None and branch in prose)
    )
    # Joined with a separator so two chunks cannot weld into one word that is
    # on no page; the same reason `_collect` writes one at element boundaries.
    loose_words = word_count(_SEPARATOR.join(text for at, text in loose if not first < at < last))
    return left_out, undecided, loose_words


# Per element: (words below it, words of prose, words that weigh nothing).
_WordSums = dict[_Node, tuple[int, int, int]]


def _word_sums(root: _Node, prose_words: dict[_Node, int], left_out: frozenset[_Node]) -> _WordSums:
    """How the words under each element split into prose, words that weigh
    nothing, and the rest.

    Weightless: headings, which label a section without being its text; the
    page furniture; and link-only text. The last two never reach a widened
    count anyway — `_beside_the_content` leaves them out — so holding them
    against an element would punish it for words it will not count. Measured on
    2026-10-18: weighed as the rest, a 130-word related-tools grid kept 29 of
    the 142 smallwebapps.com tool pages built on 2026-08-30 at their FAQ alone.

    `prose_words` holds each prose block's own words; `left_out` is the page
    furniture and the link-only subtrees that leave a count (`_link_only`). A
    link in running text is not among them, so a paragraph's linked words are
    prose once and weightless never: weighed as both, 40 linked words in a
    50-word paragraph carried a climb over 80 words of chrome. Totals are
    summed chunk by chunk, so a word an inline tag splits counts twice; that
    only nudges a comparison, and no count the report prints is taken from here.
    """
    order: list[_Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(c for c in node.content if isinstance(c, _Node) and not c.dropped)
    sums: _WordSums = {}
    for node in reversed(order):
        total = sum(word_count(item) for item in node.content if isinstance(item, str))
        prose = prose_words.get(node, 0)
        heading = 0
        for child in node.content:
            if isinstance(child, _Node) and child in sums:
                child_total, child_prose, child_heading = sums[child]
                total, prose, heading = (
                    total + child_total,
                    prose + child_prose,
                    heading + child_heading,
                )
        if node.tag in HEADING_ELEMENTS or node in left_out:
            heading = total - prose
        sums[node] = (total, prose, heading)
    return sums


def _prose_region(block: _Node, sums: _WordSums) -> _Node:
    """The element at or above `block`, below the `<body>`, whose prose most
    outweighs everything else in it.

    For a page the title cannot widen — no `<h1>`, an `<h1>` inside the block,
    or one that meets it only at the `<body>`. The paragraph scoring credits two
    levels up, so on a page of sibling sections it picks one, and nothing above
    it got a vote: with no title to climb to, the 28 smallwebapps.com tool pages
    read a median of a third of what a reader counts, even with list items
    scoring.

    Each element on the way up is weighed as its prose minus the other words it
    would count — tool chrome, plain `<div>` text, a list with no heading over
    it. Headings, furniture and link-only text weigh nothing either way (see
    `_word_sums`). A word counts once on each side, so there is no share of the
    page and no weight to tune. A sidebar does not get counted because it is
    near the article: it makes the element holding both worth less than the
    article alone, unless the prose it sits beside outweighs it. A tie keeps the
    deeper element.
    """

    def weight(node: _Node) -> int:
        total, prose, heading = sums[node]
        return prose - (total - prose - heading)

    best, best_weight = block, weight(block)
    for above in _ancestors(block.parent):
        if above.tag in WHOLE_PAGE_ELEMENTS:
            break
        if weight(above) > best_weight:
            best, best_weight = above, weight(above)
    return best


def _titled_region(title: _Node | None, block: _Node) -> _Node:
    """The smallest element holding both `block` and the page's `<h1>`.

    The paragraph scoring finds one block — the densest run of `<p>` — and on a
    page built from sibling `<section>`s that is one section. Measured on
    2026-10-18: smallwebapps.com/apps/pdf-rotate/ has no `<main>`, its text sits
    in lists, FAQ `<details>` and one `div` of three paragraphs, and the scoring
    returned that `div` — 114 words of a page a reader counts at 589 from the
    `<h1>` to its related-links heading. Every tool page on the site measured
    the same way, and the median fell below the threshold the pre-flight gate
    treats as a Blocker.

    The title is where the editor's content starts, so the region that holds
    the title and the block holds the sections between and around them. Two
    limits keep it from turning into the page:

      * no title outside the page furniture (see `_page_title`), no widening;
      * a title and a block that only meet at the `<body>` share the page, not a
        region, so the title cannot widen it; `_prose_region` climbs from the
        block instead. Counting the body would be the menu-and-footer count
        `measure_depth` refuses everywhere else.

    What the region holds besides the title and the block is sorted by
    `_beside_the_content`. What it still gets wrong: sections the block and the
    title do not share a wrapper with stay outside; a title inside the block
    itself — a hero whose description is the page's densest paragraph — widens
    nothing; and plain text that is not content but carries a heading, or sits
    between the title and the block, counts.
    """
    if title is None:
        return block
    holds_block = set(_ancestors(block))
    # Always found: both walks end at the same `#document`.
    region = next(node for node in _ancestors(title) if node in holds_block)
    return block if region.tag in WHOLE_PAGE_ELEMENTS else region


def _subheaded(root: _Node) -> frozenset[_Node]:
    """Every live element under `root` that holds an `<h2>`-`<h6>` somewhere below
    it. One walk down and one back up, like `_link_words`, so asking it per
    list item costs nothing."""
    order: list[_Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(c for c in node.content if isinstance(c, _Node) and not c.dropped)
    holds: set[_Node] = set()
    for node in reversed(order):
        if any(isinstance(c, _Node) and (c in holds or c.tag in SUBHEADINGS) for c in node.content):
            holds.add(node)
    return frozenset(holds)


def _past_lists(node: _Node | None) -> _Node | None:
    """`node`, or the first element above it that is not list structure."""
    while node is not None and node.tag in LIST_STRUCTURE:
        node = node.parent
    return node


def _richest(nodes: list[_Node]) -> _Node | None:
    """The node with the most words, or None when they are all empty."""
    best: _Node | None = None
    best_words = 0
    for node in nodes:
        words = word_count(_collect(node))
        if words > best_words:
            best, best_words = node, words
    return best


class _MainContent(NamedTuple):
    """Where `_main_container` found the main content, and how far to trust it."""

    node: _Node
    # False: the heuristic separated nothing, and `node` is the whole page.
    isolated: bool
    # Subtrees inside `node` left out of the count. Empty except for a region
    # widened from the block, by `_titled_region` or by `_prose_region` — see
    # `_beside_the_content`. Non-empty does not mean a title was found.
    outside: frozenset[_Node] = frozenset()
    # Subtrees inside `node` that are counted but could as well be the page.
    undecided: frozenset[_Node] = frozenset()
    # Words of loose text in the same position: counted, and as doubtful.
    undecided_loose_words: int = 0
    # `_furniture(node)`, when `_main_container` already computed it.
    furniture: frozenset[_Node] | None = None


def _main_container(doc: _Parser, url: str = "") -> _MainContent:
    """The element holding the main content, and how far it was isolated.

    `isolated` False means the heuristic separated nothing: what comes back is
    the whole page, chrome included. `measure_depth` turns that into ERROR,
    because a word count over the menu and the footer is not the number
    ADS-CONTENT-03 asks for and must not be reported as a pass.
    """
    for tag in ("main", "article"):
        found = _richest([n for n in _iter_elements(doc.root) if n.tag == tag])
        if found is not None:
            return _MainContent(found, True)

    # Readability-style scoring: a paragraph credits its container, and half of
    # itself to the container above. A list item, a term, a description and a
    # `<details>` question score the same way (`TEXT_BLOCKS`), and the list
    # structure between any block and its container is passed through
    # (`LIST_STRUCTURE`). A menu still cannot win: its items are link text, and
    # its `<nav>` is furniture — the new blocks score only outside both.
    scores: dict[int, float] = {}
    nodes: dict[int, _Node] = {}

    def credit(node: _Node | None, weight: float) -> None:
        if node is None or node.tag not in CONTENT_CONTAINERS:
            return
        key = id(node)
        nodes[key] = node
        scores[key] = scores.get(key, 0.0) + weight

    link_words = _link_words(doc.root)
    body = _find_body(doc)
    # Computed once: the scoring, the title search and the region all read it.
    page_furniture = _furniture(body)
    text_paragraphs: list[tuple[_Node, float]] = []
    link_paragraphs: list[tuple[_Node, float]] = []
    for node in _iter_elements(doc.root):
        if node.tag != "p":
            continue
        weight = float(len(_normalize(_collect(node))))
        if weight <= 0:
            continue
        inside, outside = link_words[node]
        (link_paragraphs if inside and not outside else text_paragraphs).append((node, weight))
    # A card's description inside `<a class="related-card">` is a paragraph
    # nobody reads in place: six of them out-scored the article on
    # smallwebapps.com/apps/csv-to-json/ as built on 2026-08-30, and the grid of
    # links won as the main content. So link paragraphs stand aside when a
    # paragraph of text competes — one outside the page furniture. On a
    # directory whose every `<p>` is a link, the list is the page, and the
    # copyright `<p>` in its footer is no competitor: counting it as one sent
    # the page to the body and reported a thin listing as unmeasurable.
    in_furniture = _subtrees(page_furniture)
    # Built on the first question: a page with no list anywhere never walks it.
    subheaded_cache: list[frozenset[_Node]] = []

    def subheaded() -> frozenset[_Node]:
        if not subheaded_cache:
            subheaded_cache.append(_subheaded(doc.root))
        return subheaded_cache[0]

    def headed_section(block: _Node) -> tuple[_Node | None, _Node | None] | None:
        """Where a block's credit lands once it passes through list structure —
        the container around the list and the one above — when either of them
        heads a section. A plain list in a `<div>`, with no heading near it, is
        a sidebar as easily as a list of use cases, and scoring it let a 400-word
        sidebar out-score a 120-word post and be counted whole as the article.
        """
        holder = _past_lists(block.parent)
        above = _past_lists(holder.parent) if holder is not None else None
        for candidate in (holder, above):
            if (
                candidate is not None
                and candidate.tag not in WHOLE_PAGE_ELEMENTS
                and candidate in subheaded()
            ):
                return holder, above
        return None

    # In document order, so that a tie keeps resolving to the first container.
    in_order = [n for n in _iter_elements(doc.root) if n.tag == "p" or n.tag in TEXT_BLOCKS]
    every_block = frozenset(in_order)
    targets: dict[_Node, tuple[_Node | None, _Node | None]] = {}
    text_blocks: list[tuple[_Node, float]] = []
    # Each text block's own words, kept for `_prose_region` instead of collected twice.
    own_words: dict[_Node, int] = {}
    for node in in_order:
        if node.tag == "p":
            # A paragraph inside a list or a `<details>` passes through it into
            # a headed section; anywhere else it credits its parent and
            # grandparent exactly as it always has.
            parent = node.parent
            section = (
                headed_section(node)
                if parent is not None and parent.tag in LIST_STRUCTURE
                else None
            )
            targets[node] = section or (parent, parent.parent if parent is not None else None)
            continue
        if node in in_furniture:
            continue
        inside, outside = link_words[node]
        if inside and not outside:
            continue
        section = headed_section(node)
        if section is None:
            continue
        # Each by its own words: a `<li>` holding a nested list is credited for
        # its label, and each nested item for itself.
        own = _normalize(_collect(node, skip=every_block))
        if own:
            targets[node] = section
            text_blocks.append((node, float(len(own))))
            own_words[node] = word_count(own)
    competes = bool(text_blocks) or any(node not in in_furniture for node, _ in text_paragraphs)
    allowed = {n for n, _ in text_paragraphs} | {
        n for n, _ in (text_blocks if competes else link_paragraphs)
    }
    weights = dict(text_paragraphs + link_paragraphs + text_blocks)
    for node in in_order:
        if node in allowed:
            container, above = targets[node]
            credit(container, weights[node])
            credit(above, weights[node] / 2)

    if scores:
        # Deepest wins a tie, so a chain of wrapper divs resolves to the
        # innermost one that still holds all the paragraphs.
        key = max(scores, key=lambda k: (scores[k], nodes[k].depth))
        winner = nodes[key]
        # `body` is a content container — a page may hang its paragraphs
        # straight off it — so it can win the scoring, and a single <p> in the
        # footer is enough for it to: the grandparent credit lands on the body
        # whenever the parent (<footer>, <nav>) is not a container itself. When
        # the body wins, nothing was separated from anything.
        if winner.tag in WHOLE_PAGE_ELEMENTS:
            return _MainContent(winner, False)
        title = _page_title(body, page_furniture, link_words, url)
        region = _titled_region(title, winner)
        prose_blocks: frozenset[_Node] = frozenset()
        if region is winner and winner in in_furniture:
            # The scoring elected a block inside the page's furniture — the only
            # `<p>` on the page sitting in a `<header>`. Climbing from there
            # stopped on the header itself, weightless and so worth more than
            # the block, and counted its `<nav>` as content: 60 words where the
            # block alone is 40. A block in the furniture is not grown.
            return _MainContent(winner, True)
        if region is winner:
            # No title to widen to, or none outside the block: weigh the prose
            # instead (see `_prose_region`).
            prose_words = dict(own_words)
            for n, _ in text_paragraphs:
                if n not in in_furniture:
                    prose_words[n] = word_count(_collect(n, skip=every_block))
            sums = _word_sums(
                doc.root, prose_words, page_furniture | _link_only([doc.root], link_words)
            )
            region = _prose_region(winner, sums)
            if region is winner:
                # Nothing above the block is worth more than the block. Its own
                # furniture stays in the count, where `measure_depth`'s control
                # can still see it.
                return _MainContent(winner, True)
            title = None
            prose_blocks = frozenset(n for n, (_, prose, _) in sums.items() if prose > 0)
        # The region's furniture is the body's, below the region. No `<article>`
        # with words sits above the region, or it would have won above, so
        # nothing between the body and the region claims a piece differently;
        # and the region is never in it, holding a title proved outside all.
        region_furniture = frozenset(f for f in page_furniture if region in _ancestors(f.parent))
        left_out, undecided, loose_words = _beside_the_content(
            region, title, winner, region_furniture, link_words, prose_blocks
        )
        return _MainContent(region, True, left_out, undecided, loose_words, region_furniture)

    return _MainContent(_find_body(doc), False)


def _furniture(container: _Node) -> frozenset[_Node]:
    """The subtrees inside `container` that belong to the page, not to an article.

    A `nav`, `header`, `footer` or `aside` says "this region is not the prose".
    It does not say whose region it is, and that is the whole question. The
    canonical `<article>` in the HTML spec opens with a `<header>` holding the
    heading and the publication date and closes with a `<footer>`; WordPress
    ships that shape as `entry-header`/`entry-footer` on every single-post
    theme, title and byline and category line inside the `<article>`. Skipping
    those by tag name reports "main content not separated from page furniture"
    about a page whose main content was separated perfectly — and ERROR in this
    package means the check could not run, not that the page is odd.

    So an `<article>` claims the furniture tags beneath it, and `container`
    counts as one when it is an article itself: a page that is a single
    `<article>` with no `<main>` around it wins the heuristic *as* the article,
    and its own header is still its own. What no article claims belongs to the
    page, which is what a wrapper is full of.

    Two shapes this gets wrong, in the two directions:

      * a `<header>` sitting straight in a `<main>` with no `<article>` in
        between is read as furniture even when it holds the page's own title.
        The window is narrow — such a header is a heading and a date, not a
        menu — and it is the price of still being able to catch a `<main>`
        wrapped around the site navigation.
      * an `<article>` used as the whole-page wrapper claims the menu inside
        it, so that page's count stays too generous. That is the same class of
        miss the band gate already accepts, and `<article>` around a whole site
        is a far rarer mistake than `<div id="wrapper">` around one.
    """
    found: set[_Node] = set()
    # Each entry carries whether an <article> at or below `container` already
    # claims this node. Seeded from the container's children, so the container
    # is never furniture itself — nor can it be: `_main_container` returns a
    # content container, `<main>`, `<article>`, the body, or a `_titled_region`,
    # which holds a title `_page_title` already proved is outside all furniture.
    claimed_by_article = container.tag == "article"
    stack: list[tuple[_Node, bool]] = [
        (child, claimed_by_article) for child in container.content if isinstance(child, _Node)
    ]
    while stack:
        node, claimed = stack.pop()
        if node.dropped:
            continue
        if not claimed and node.tag in BOILERPLATE_ELEMENTS:
            # The whole subtree goes with it, so there is nothing below to look
            # at: an <article> nested inside a menu is not a page's article.
            found.add(node)
            continue
        claimed = claimed or node.tag == "article"
        stack.extend((c, claimed) for c in node.content if isinstance(c, _Node))
    return frozenset(found)


def _visible_text(doc: _Parser) -> str:
    return _normalize(_collect(doc.root))


def _js_shell(doc: _Parser) -> bool:
    if not doc.has_external_script:
        return False
    if word_count(_visible_text(doc)) >= JS_SHELL_MAX_WORDS:
        return False
    for node in _iter_elements(doc.root):
        if node.attrs.get("id", "").lower() in _MOUNT_IDS and not _collect(node).strip():
            return True
    return False


def looks_javascript_rendered(html: str) -> bool:
    """Whether this is a client-rendered shell rather than a page with content.

    Three signals together, because any one of them alone is common on ordinary
    pages: an external script, an empty mount point with a framework's id, and
    almost no visible text. A server-rendered page keeps the same mount id but
    fills it, so it does not match.
    """
    return _js_shell(_parse(html))


def extract_text(html: str) -> str:
    """Visible text of the document, whitespace normalized.

    Best effort by design: a document the parser could not finish still returns
    the text it did reach. Use `measure_depth` when the caller needs to know
    whether the parse completed — it turns a partial parse into ERROR instead
    of reporting a word count that looks like a measurement.
    """
    return _visible_text(_parse(html))


def main_content_text(html: str, *, url: str = "") -> str:
    """Text of the page's main content, chrome excluded where detectable.

    The heuristic, in order:

      1. `<main>`, if one carries text (the richest, if a page has several).
      2. otherwise the `<article>` with the most words — the richest single
         one, not all of them concatenated, so a blog index does not add up its
         teasers into a substantial-looking page.
      3. otherwise the container scoring highest on paragraph text: each `<p>`
         credits its parent container fully and its grandparent by half, and a
         `<p>` that is all link text scores only when no other does. List
         items, terms, descriptions and `<details>` questions score too, by
         their own words, when they sit in a section with a heading and
         outside the furniture; their credit, and that of a `<p>` inside a
         list, passes through the list to the container around it. The
         winner is widened to the smallest element that also holds the page's
         `<h1>`, never as far as the `<body>` (see `_titled_region`); when no
         title can widen it, it climbs as far as its prose outweighs the rest
         it would count (see `_prose_region`). What the
         widening collects beside the title and the block loses its
         nav/header/footer/aside and every subtree that is all link text, but
         for a link with a word right beside it (see `_in_running_text`); the
         block and the title themselves are taken whole. Text the widening
         cannot place — no heading, before the title or after the block — is
         returned like the rest: only `measure_depth` says it is in doubt
         (see `_beside_the_content`).
      4. otherwise the whole `<body>`.

    Limitation: this reads structure, not layout. A page whose paragraphs hang
    directly off `<body>`, or that builds paragraphs from `<div>`s, falls
    through to step 4 and gets its menu counted with the article. Returning the
    body anyway is deliberate here — a caller asking for text wants the text it
    can get — but it is not a measurement, and `measure_depth` reports it as
    ERROR rather than grading the menu as prose. It also only sees server HTML:
    for a client-rendered page there is nothing to find, which `measure_depth`
    likewise reports as ERROR rather than as a thin page.

    `url` is the page's own, and means what it means to `measure_depth`: with
    it, a title linked to this very page is told apart from a logo linked
    home (see `_is_logo`). Pass the same one and the text is the text measured.
    """
    found = _main_container(_parse(html), url)
    return _normalize(_collect(found.node, skip=found.outside))


def word_count(text: str) -> int:
    """Words in `text`: runs of letters, and runs of digits.

    Alphabet-agnostic, so "coração" and "intuição" are one word each rather
    than two. "guarda-chuva" and "d'água" count once; "R$ 5,00" counts twice.
    """
    return len(_WORD.findall(text))


def borderline_ceiling(min_words: int = DEFAULT_MIN_WORDS) -> int:
    """First word count that is no longer borderline."""
    return int(min_words * BORDERLINE_MULTIPLIER)


def classify_depth(words: int, *, min_words: int = DEFAULT_MIN_WORDS) -> Status:
    """Status for a main-content word count against a threshold.

    A page with no main content at all is FAIL, not OK: ADS-CONTENT-04 counts
    "pages with 0 words of main content" as a finding in its own right. A page
    the checker could not read is neither — that is ERROR, and only
    `measure_depth` can tell the two apart.
    """
    if words <= 0:
        return Status.FAIL
    if words < min_words:
        return Status.WARNING
    if words < borderline_ceiling(min_words):
        return Status.INFO
    return Status.OK


def describe_depth(words: int, *, min_words: int = DEFAULT_MIN_WORDS) -> str:
    """Human-readable band for `words`, derived from the same constants.

    The old summary hard-coded its labels ("BORDERLINE (300-450)", "OK (>450)")
    while the code classified 450 as OK — a page could land in a band the
    report declared impossible. Both now come from `borderline_ceiling`.
    """
    ceiling = borderline_ceiling(min_words)
    if words <= 0:
        return "no main content found (0 words)"
    if words < min_words:
        return f"{words} words: below the configured threshold ({min_words} words)"
    if words < ceiling:
        return f"{words} words: borderline ({min_words}-{ceiling - 1} words)"
    return f"{words} words: at or above the configured bar (>= {ceiling} words)"


@dataclass
class TextDepth:
    """What one page's main content measures, and whether that is acceptable."""

    url: str = ""
    text: str = ""
    words: int = 0
    total_words: int = 0
    # Share of the visible words that live in the main content — the
    # content/boilerplate ratio ADS-CONTENT-03 asks for. Reported, never used
    # to decide: no defensible cutoff exists, and inventing one would repeat
    # the "words per KB" number that the old report printed and nobody read.
    main_ratio: float = 0.0
    # ERROR by default. A TextDepth nobody managed to fill in is an unanswered
    # question, and an unanswered question is not a pass.
    status: Status = Status.ERROR
    reason: str = ""


def measure_depth(html: str, *, min_words: int = DEFAULT_MIN_WORDS, url: str = "") -> TextDepth:
    """Measure main-content depth in an HTML string.

    `url` is the address the HTML was served from, after redirects, and it is
    not only a label: on a page with no `<main>` or `<article>`, an `<h1>` whose
    every word is link text is the page's title when it links to this URL and
    a logo otherwise (see `_is_logo`), and that decides how far the count
    reaches. Without it, only a link to the site root is known to be a logo.
    `measure_fetch` passes the final URL; a caller holding only the requested
    one should expect a different count when the two differ.
    """
    doc = _parse(html)
    found = _main_container(doc, url)
    container, isolated, outside, undecided = (
        found.node,
        found.isolated,
        found.outside,
        found.undecided,
    )
    undecided_loose = found.undecided_loose_words
    main = _normalize(_collect(container, skip=outside))
    words = word_count(main)
    total = word_count(_visible_text(doc))
    # The same container with its page furniture removed. Never reported as
    # the count — it is only the control that says whether `words` is a
    # measurement of the article or of the menu wrapped around it. Furniture
    # already in `outside` was never counted, so when all of it is, the
    # recount would only reproduce `words` and is skipped. Decided by the sets
    # themselves, not by which path filled `outside`: a path that one day
    # leaves something out without the furniture still gets its control.
    furniture = found.furniture if found.furniture is not None else _furniture(container)
    editorial_words = (
        words
        if furniture <= outside
        else word_count(_normalize(_collect(container, skip=outside | furniture)))
    )
    # The second control, for a widened region only: the count without what
    # `_beside_the_content` could not call content or page — the subtrees,
    # skipped, and the loose text, subtracted.
    decided_words = (
        word_count(_normalize(_collect(container, skip=outside | undecided))) - undecided_loose
        if undecided or undecided_loose
        else words
    )

    status = Status.OK
    notes: list[str] = []
    if doc.parse_error is not None:
        # The old code did `except Exception: pass` and returned the partial
        # text, so a 900-word page that failed after its first paragraph was
        # reported as a 45-word thin page with no sign anything went wrong.
        status = escalate(status, Status.ERROR)
        notes.append(f"parse did not complete ({doc.parse_error}); count is partial")
    if _js_shell(doc):
        # Refusing to classify beats reporting THIN: the shell of a React app
        # is not a thin page, it is a page this checker cannot read.
        status = escalate(status, Status.ERROR)
        notes.append("client-rendered shell: server HTML carries no main content")
    if words > 0 and not isolated:
        # The count below is the whole <body>: navigation, sidebar and footer
        # inside it. Reporting OK for it is this module's own version of the
        # blocker — a pass printed for a condition nobody observed — and it is
        # worse than the old script's, because the old one at least called the
        # same page BORDERLINE. `main_ratio == 1.0` is the fingerprint, and a
        # number the report never decides on is not a warning to anybody.
        # A body with no words at all is exempt: nothing was mixed in, and an
        # empty page is an observation (FAIL) rather than a failed reading.
        status = escalate(status, Status.ERROR)
        notes.append(
            "main content not isolated: no <main>, <article> or paragraph block stood "
            "out, so this counts the whole <body>, navigation and footer included"
        )
    elif classify_depth(editorial_words, min_words=min_words) is not classify_depth(
        words, min_words=min_words
    ):
        # A container that is not the <body> can still be the whole page: a
        # `<div id="wrapper">` holds the paragraphs, so it wins the scoring,
        # and `WHOLE_PAGE_ELEMENTS` waves it through with the menu and the
        # footer inside the count. `main_ratio == 1.0` is the fingerprint of
        # that, but it cannot be the rule on its own: a bare article with no
        # chrome also measures 1.0, and it is a page this module read
        # correctly. The ratio says nothing was separated; it does not say
        # whether there was anything to separate.
        #
        # The furniture answers that. `_furniture` picks out the <nav>,
        # <header>, <footer> and <aside> that no <article> claims — the page's
        # own, not an article's title and byline — and this fires only when
        # discarding them lands the page in a different band, which is
        # precisely when the verdict being reported is a verdict about the
        # menu. A 2000-word article with a four-word copyright line moves no
        # band and stays silent; a wrapper holding an 80-item menu around one
        # six-word <p> moves from "at or above the bar" to "below the
        # threshold" and must not read as a pass.
        status = escalate(status, Status.ERROR)
        notes.append(
            "main content not separated from page furniture: the container measured "
            f"still holds {words - editorial_words} words of <nav>/<header>/<footer>/"
            "<aside>, and without them this page is "
            f"{describe_depth(editorial_words, min_words=min_words)}"
        )
    elif classify_depth(decided_words, min_words=min_words) is not classify_depth(
        words, min_words=min_words
    ):
        # The same rule as the furniture control, applied to what the climb to
        # the title collected without a heading to vouch for it. A `<div>` of
        # plain text after the block is a sidebar or a list of use cases; when
        # the band depends on which, the band is not a measurement.
        status = escalate(status, Status.ERROR)
        notes.append(
            "main content not separated from the page around it: "
            f"{words - decided_words} words of the region measured sit before its <h1> "
            "or after its main block under no heading, and without them this page is "
            f"{describe_depth(decided_words, min_words=min_words)}"
        )
    status = escalate(status, classify_depth(words, min_words=min_words))
    notes.append(describe_depth(words, min_words=min_words))

    return TextDepth(
        url=url,
        text=main,
        words=words,
        total_words=total,
        main_ratio=(words / total if total else 0.0),
        status=status,
        reason="; ".join(notes),
    )


def _header_charset(content_type: str) -> str:
    """The charset parameter of a Content-Type, lowercased. Empty when absent."""
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip("\"'").lower()
    return ""


def _declared_charset(text: str) -> str:
    """The encoding the document declares about itself, lowercased.

    Matches `<meta charset=...>` and the `charset=` inside a `<meta
    http-equiv="Content-Type">`, which is where HTML5 says to look when the
    transport does not say. Only the head is searched: HTML5 requires the
    declaration in the first 1024 bytes, and a `charset=` further down is more
    likely to be prose about encodings than a declaration.
    """
    match = _META_CHARSET.search(text[:_DECLARATION_WINDOW])
    return match.group(1).lower() if match else ""


def _decode_body(response: Fetch, content_type: str) -> tuple[str, str | None]:
    """The body as text, and the reason it could not be read, when it could not.

    `Fetch.text` is whatever requests decoded, and requests reads a `text/*`
    body with no charset parameter as ISO-8859-1: RFC 2616's default, which
    HTML5 replaced with "read the document's own declaration". The damage is
    not cosmetic. "coração" arrives as "coraÃ§Ã£o", which `_WORD` reads as three
    words, so a 240-word page measures 480 and clears a 450-word bar it should
    have missed — a thin page reported as a clean pass, which is the defect
    this whole module exists to stop.

    ISO-8859-1 maps every byte to its own code point, so the fallback is
    reversible and the original bytes can be decoded again here. The right home
    for the repair is the fetch layer, which still holds the response object;
    until it moves there, this is the last place before a number gets printed.
    """
    text = response.text
    if _header_charset(content_type):
        # The server said what it sent and the fetch layer decoded with it.
        return text, None
    try:
        raw = text.encode("iso-8859-1")
    except UnicodeEncodeError:
        # Code points above U+00FF: this text did not come from the fallback,
        # so re-decoding it would corrupt what somebody already got right.
        return text, None

    declared = _declared_charset(text)
    if declared:
        try:
            return raw.decode(declared), None
        except (LookupError, UnicodeDecodeError):
            # The page names an encoding its own bytes do not honour. Any text
            # taken from here is a guess, and a word count over a guess is the
            # pass nobody observed.
            return text, f"declared charset {declared!r} does not decode the body"

    try:
        # UTF-8 validates itself: a multi-byte sequence that decodes strictly
        # is UTF-8 in every document that is not built to fool this check.
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        # A genuine legacy single-byte page, declaring nothing. ISO-8859-1 is
        # the historical default and its letters are letters, so the count
        # stands rather than being refused.
        return text, None


def measure_fetch(response: Fetch, *, min_words: int = DEFAULT_MIN_WORDS) -> TextDepth:
    """Measure depth from an already-performed fetch."""
    url = response.final_url or response.url
    if not response.ok:
        # The blocker in analyze_text_depth: pages whose fetch failed were
        # counted in the OK bucket, so an audit where nothing was read printed
        # "OK (>450 words): 3 pages". `Fetch.status` supplies the distinction
        # the fetch already made (404 MISSING, 5xx/403 FAIL, network ERROR);
        # the escalate is this function's own floor on top of it, so a fetch
        # that did not succeed cannot come back as a pass even if that mapping
        # is ever widened.
        return TextDepth(
            url=url,
            status=escalate(response.status, Status.MISSING),
            reason=response.error or f"HTTP {response.status_code}",
        )

    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in ("text/html", "application/xhtml+xml"):
        # A PDF or an image served with 200 used to be fed straight into
        # HTMLParser and compared as if it were a page.
        detail = f"Content-Type {media_type!r}" if media_type else "no Content-Type"
        return TextDepth(
            url=url,
            status=Status.ERROR,
            reason=f"{detail}: body not parsed as HTML",
        )

    html, undecodable = _decode_body(response, content_type)
    if undecodable is not None:
        return TextDepth(url=url, status=Status.ERROR, reason=f"{undecodable}: not measured")

    return measure_depth(html, min_words=min_words, url=url)


def measure_url(
    url: str,
    *,
    min_words: int = DEFAULT_MIN_WORDS,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> TextDepth:
    """Fetch a page and measure its main-content depth.

    Goes through `adsense_checks.http.fetch`, which sends the AdSense crawler
    User-Agent. The old script used the requests default, so the same URLs that
    a browser-UA crawl had just recorded as 200 came back 403 here.
    """
    return measure_fetch(fetch(url, session=session, timeout=timeout), min_words=min_words)
