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

# Containers that can plausibly hold an article body. A `<p>` inside anything
# else scores nothing, which is what keeps a menu of links from winning.
CONTENT_CONTAINERS = frozenset({"article", "body", "div", "main", "section", "td"})

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
            if tag not in INLINE_ELEMENTS:
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
    (see `_furniture`). It never holds `node` itself — the caller asked for
    this element's text, and "" for it would read as an empty page rather than
    as a refusal to answer.
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
        if item.dropped or item in skip:
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


def _link_only(roots: list[_Node]) -> frozenset[_Node]:
    """The largest subtrees under `roots` whose every word is link text.

    Link text is navigation by another name. A menu, a widget list, a grid of
    related-tool cards and a logo linked to the home page are text a reader
    clicks rather than reads, and none of them needs a `<nav>` around it to be
    that. The rule is all or nothing on purpose: a share of link text —
    readability's link density — needs a cutoff, and any cutoff here would be
    invented, while "every word" needs none. "Hyper<a>text</a>" has a word
    outside the link, so its subtree is not link-only.

    One walk down records which text sits inside an `<a>`; one walk back up
    says, per subtree, whether it has words inside links and words outside
    them. Both iterative, for the same malformed pages `_collect` is.
    """
    order: list[tuple[_Node, bool]] = []
    stack = [(root, any(above.tag == "a" for above in _ancestors(root))) for root in roots]
    while stack:
        node, linked = stack.pop()
        order.append((node, linked))
        stack.extend(
            (child, linked or child.tag == "a")
            for child in node.content
            if isinstance(child, _Node) and not child.dropped
        )
    # Reversed pre-order visits every child before its parent.
    in_links: dict[_Node, bool] = {}
    outside_links: dict[_Node, bool] = {}
    for node, linked in reversed(order):
        worded = any(isinstance(item, str) and _WORD.search(item) for item in node.content)
        children = [c for c in node.content if isinstance(c, _Node) and c in in_links]
        in_links[node] = (linked and worded) or any(in_links[c] for c in children)
        outside_links[node] = (not linked and worded) or any(outside_links[c] for c in children)

    found: set[_Node] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if in_links[node] and not outside_links[node]:
            found.add(node)
            continue
        pending.extend(c for c in node.content if isinstance(c, _Node) and c in in_links)
    return frozenset(found)


def _page_title(doc: _Parser) -> _Node | None:
    """The page's own `<h1>`: the first one with words that is neither furniture
    nor a link.

    A logo wrapped in `<h1>` names the site, not the page. Anchoring on it
    stretches the region up to whatever wraps the header, which is the page
    wrapper this module keeps refusing to measure. Inside a `<header>` the
    furniture says so. Inside `<div id="header">` nothing does, except that a
    logo is a link home: `<h1><a href="/">Brand</a></h1>` is link-only, and a
    page's title is not.
    """
    body = _find_body(doc)
    furniture = _furniture(body)
    for node in _iter_elements(body):
        if node.tag != "h1" or word_count(_collect(node)) == 0:
            continue
        if any(above in furniture for above in _ancestors(node)):
            continue
        if node in _link_only([node]):
            continue
        return node
    return None


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
    region: _Node, title: _Node, block: _Node
) -> tuple[frozenset[_Node], frozenset[_Node]]:
    """What a widened region holds besides its title, its block and the path
    between them: what to leave out, and what nobody can say is content.

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
    """
    paths = set(_ancestors(title)) | set(_ancestors(block))
    # The region is in the order too: a title left unclosed swallows the rest of
    # the page, and is then the region itself.
    order = {node: i for i, node in enumerate([region, *_iter_elements(region)])}
    first, last = sorted((order[title], order[block]))
    branches: list[_Node] = []
    stack = [region]
    while stack:
        for child in stack.pop().content:
            if not isinstance(child, _Node) or child.dropped or child is title or child is block:
                continue
            (stack if child in paths else branches).append(child)
    furniture = _furniture(region)
    left_out = furniture | _link_only([b for b in branches if b not in furniture])
    undecided = frozenset(
        branch
        for branch in branches
        if branch not in left_out
        and not first < order[branch] < last
        and not _has_heading(branch, left_out)
    )
    return left_out, undecided


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
        region, so the block comes back alone. Counting the body would be the
        menu-and-footer count `measure_depth` refuses everywhere else.

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
    # `_titled_region` widened — see `_beside_the_content`.
    outside: frozenset[_Node] = frozenset()
    # Subtrees inside `node` that are counted but could as well be the page.
    undecided: frozenset[_Node] = frozenset()


def _main_container(doc: _Parser) -> _MainContent:
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
    # itself to the container above. A nav or a sidebar has links and list
    # items but no <p>, so it scores zero and cannot win.
    scores: dict[int, float] = {}
    nodes: dict[int, _Node] = {}

    def credit(node: _Node | None, weight: float) -> None:
        if node is None or node.tag not in CONTENT_CONTAINERS:
            return
        key = id(node)
        nodes[key] = node
        scores[key] = scores.get(key, 0.0) + weight

    for node in _iter_elements(doc.root):
        if node.tag != "p":
            continue
        weight = float(len(_normalize(_collect(node))))
        if weight <= 0:
            continue
        if node in _link_only([node]):
            # A card's description inside `<a class="related-card">` is a
            # paragraph nobody reads in place. Six of them out-scored the
            # article on smallwebapps.com/apps/csv-to-json/ as built on
            # 2026-08-30, and the grid of links won as the main content.
            continue
        credit(node.parent, weight)
        credit(node.parent.parent if node.parent else None, weight / 2)

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
        title = _page_title(doc)
        region = _titled_region(title, winner)
        if region is winner:
            # The title is inside the block, or there is none to widen to. The
            # block's own furniture stays in the count, where `measure_depth`'s
            # control can still see it.
            return _MainContent(winner, True)
        return _MainContent(region, True, *_beside_the_content(region, title, winner))

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


def main_content_text(html: str) -> str:
    """Text of the page's main content, chrome excluded where detectable.

    The heuristic, in order:

      1. `<main>`, if one carries text (the richest, if a page has several).
      2. otherwise the `<article>` with the most words — the richest single
         one, not all of them concatenated, so a blog index does not add up its
         teasers into a substantial-looking page.
      3. otherwise the container scoring highest on paragraph text: each `<p>`
         credits its parent container fully and its grandparent by half —
         widened to the smallest element that also holds the page's `<h1>`,
         with that element's own nav/header/footer/aside left out, and never
         widened as far as the `<body>` (see `_titled_region`).
      4. otherwise the whole `<body>`.

    Limitation: this reads structure, not layout. A page whose paragraphs hang
    directly off `<body>`, or that builds paragraphs from `<div>`s, falls
    through to step 4 and gets its menu counted with the article. Returning the
    body anyway is deliberate here — a caller asking for text wants the text it
    can get — but it is not a measurement, and `measure_depth` reports it as
    ERROR rather than grading the menu as prose. It also only sees server HTML:
    for a client-rendered page there is nothing to find, which `measure_depth`
    likewise reports as ERROR rather than as a thin page.
    """
    found = _main_container(_parse(html))
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
    """Measure main-content depth in an HTML string."""
    doc = _parse(html)
    container, isolated, outside, undecided = _main_container(doc)
    main = _normalize(_collect(container, skip=outside))
    words = word_count(main)
    total = word_count(_visible_text(doc))
    # The same container with its page furniture removed. Never reported as
    # the count — it is only the control that says whether `words` is a
    # measurement of the article or of the menu wrapped around it. For a
    # region `_titled_region` widened, `outside` already holds that furniture,
    # so the two counts agree and the control has nothing left to catch.
    editorial_words = word_count(
        _normalize(_collect(container, skip=outside | _furniture(container)))
    )
    # The second control, for a widened region only: the count without the
    # branches `_beside_the_content` could not call content or page.
    decided_words = (
        word_count(_normalize(_collect(container, skip=outside | undecided)))
        if undecided
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
