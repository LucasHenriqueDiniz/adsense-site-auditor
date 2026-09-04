"""Near-duplicate detection between pages, by word shingles and Jaccard.

Two defects in the original `check_duplicates.py` were not bugs in the coding
sense — the code did exactly what it said — but the thing it said was the wrong
question:

  * The risk test compared the number of duplicate GROUPS with the number of
    PAGES: `len(duplicates) > len(urls) * 0.3`. A group of 40 identical pages
    counts as one, so the worse the duplication the smaller the indicator. Ten
    byte-identical pages produced `1 > 3.0` — False — and the script printed
    "Duplication risk appears acceptable" for a site that was 100% duplicated,
    while four pages in two pairs (half the problem) tripped the alarm. Here the
    denominator and the numerator are both pages: `duplicate_page_count /
    len(analyzed)`. A group is still reported, but it never decides a verdict.

  * Similarity came from `difflib.SequenceMatcher` over raw characters. That is
    quadratic in the length of the text, and its `autojunk` heuristic silently
    discards any character occupying more than 1% of positions once a sequence
    passes 200 elements — which, in running prose, is most of the alphabet, so
    the ratio was under-reported by an unpredictable margin. Worse, character
    edit similarity is not what "content overlap" means in the rubric.

    Shingling (Broder) is the standard answer for near-duplicate detection and
    is what this module uses: normalize to words, take every window of
    SHINGLE_SIZE consecutive words as one token, and compare the two sets with
    Jaccard. Building the sets is linear in the text; comparing two of them is
    linear in the smaller set. Nothing is discarded for being "popular", so
    boilerplate shared by both pages counts as the overlap it is. Comparing N
    pages is still N*(N-1)/2 pairs, but each pair is a set intersection over
    precomputed sets rather than a fresh alignment over full documents. For
    crawls beyond a few thousand pages the next step is MinHash/LSH; this module
    does not implement it and does not pretend the pair loop is sublinear.

On honesty of capability: ADS-CONTENT-OVERLAP asks for similarity against the
top 5 SERP results. This module performs NO search. `compare_against` takes
competitor texts supplied by the caller; obtaining them is external work. Given
none, the answer is MISSING, never OK, and given fewer than SERP_SAMPLE_SIZE it
says how many of the five it actually saw.

Requirements: ADS-CONTENT-02 (partial), ADS-CONTENT-OVERLAP (partial).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import requests

from adsense_checks.crawl import normalize_url
from adsense_checks.http import DEFAULT_TIMEOUT, fetch
from adsense_checks.status import Status, escalate

# Five words per shingle. Below ~3 the windows are common enough in any prose of
# the same language to report overlap that is grammar, not content; above ~8 a
# rewritten connective breaks every window that touches it and genuine copies
# read as distinct. Five is the usual working point for page-level duplicate
# detection and is exposed as a parameter everywhere it is used.
SHINGLE_SIZE = 5

# references/adsense-requirements.md (ADS-CONTENT-OVERLAP) puts the high-risk
# band at >60% overlap. The old default of 0.8 sat above the entire band the
# rubric calls High Risk, so every page pair the rubric wanted flagged went
# unreported and the audit concluded the requirement passed.
OVERLAP_MONITOR = 0.4
OVERLAP_HIGH_RISK = 0.6
DEFAULT_SIMILARITY_THRESHOLD = OVERLAP_HIGH_RISK

# Share of analyzed pages that may be involved in duplication before the site
# itself is the finding rather than a page pair.
DEFAULT_DUPLICATE_PAGE_RATIO = 0.3

# The count named by ADS-CONTENT-OVERLAP. Used only to report how much of the
# requested evidence the caller actually supplied — never to fetch anything.
SERP_SAMPLE_SIZE = 5

HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Digits are words: prices and scores are content."""
    return _WORD_RE.findall(text.casefold())


def shingles(text: str, size: int = SHINGLE_SIZE) -> frozenset[tuple[str, ...]]:
    """Every window of `size` consecutive words, as a set.

    A text shorter than one window is represented by a single shingle holding
    all of its words. That keeps two short identical pages comparable, at the
    cost of never matching a short text inside a long one — a page with fewer
    words than the window is thin content (ADS-CONTENT-03's question), not a
    case this metric is meant to settle.
    """
    words = tokenize(text)
    if not words:
        return frozenset()
    if len(words) < size:
        return frozenset({tuple(words)})
    return frozenset(tuple(words[i : i + size]) for i in range(len(words) - size + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    """|A n B| / |A u B|. Symmetric; 0.0 when either side is empty.

    Two empty texts are deliberately NOT 1.0. An empty extraction means the page
    could not be read, and calling two unread pages "100% duplicate" would
    manufacture a finding out of absent evidence. Callers upstream route empty
    texts to `unanalyzable` instead, so this branch is a floor, not a path.
    """
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / (len(a) + len(b) - intersection)


def containment(a: frozenset, b: frozenset) -> float:
    """Share of A's shingles that also occur in B. Asymmetric on purpose.

    This is the right metric for "how much of MY page exists elsewhere", which
    is what ADS-CONTENT-OVERLAP asks. Jaccard would answer a different question:
    a competitor page ten times longer that contains our text in full scores
    ~0.09 by Jaccard and 1.0 by containment, and the second number is the one
    that describes the risk.
    """
    if not a:
        return 0.0
    return len(a & b) / len(a)


def similarity(text_a: str, text_b: str, *, shingle_size: int = SHINGLE_SIZE) -> float:
    """Symmetric near-duplicate similarity of two texts, in [0, 1]."""
    return jaccard(shingles(text_a, shingle_size), shingles(text_b, shingle_size))


def overlap_band(value: float) -> str:
    """The rubric's three bands, so a report never has to re-derive them."""
    if value >= OVERLAP_HIGH_RISK:
        return "high-risk"
    if value >= OVERLAP_MONITOR:
        return "monitor"
    return "safe"


@dataclass(frozen=True)
class DuplicateGroup:
    """A connected component of the "similar to" relation.

    Membership means "similar to at least one other member", not "similar to all
    of them" — similarity above a threshold is not transitive. `pairs` carries
    the links that actually met the threshold so a reader can see which, rather
    than trusting a group label to mean more than it does.
    """

    urls: tuple[str, ...]
    pairs: tuple[tuple[str, str, float], ...]

    @property
    def max_similarity(self) -> float:
        return max(sim for _, _, sim in self.pairs)

    @property
    def mean_similarity(self) -> float:
        # No `... if pairs else 0` guard: a group exists only because at least
        # one pair met the threshold, so the empty case is unreachable. The
        # original carried exactly that dead branch, which invented a case the
        # code could not produce.
        return sum(sim for _, _, sim in self.pairs) / len(self.pairs)


@dataclass(frozen=True)
class DuplicationResult:
    """Verdict for internal duplication across a set of pages."""

    status: Status
    threshold: float
    page_ratio_threshold: float
    analyzed: tuple[str, ...]
    groups: tuple[DuplicateGroup, ...]
    unanalyzable: dict[str, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    @property
    def duplicate_pages(self) -> frozenset[str]:
        """Every page in some group — the population the verdict is about."""
        return frozenset(url for group in self.groups for url in group.urls)

    @property
    def duplicate_page_count(self) -> int:
        return len(self.duplicate_pages)

    @property
    def duplicate_ratio(self) -> float:
        """Duplicated pages over analyzed pages. Both sides are pages.

        The defect this replaces divided a group count by a page count, so the
        indicator fell as duplication rose. Denominator is `analyzed`, not the
        URLs handed in: pages that could not be read are not evidence of
        anything and inflating the denominator with them would dilute the ratio.
        """
        if not self.analyzed:
            return 0.0
        return self.duplicate_page_count / len(self.analyzed)

    def summary(self) -> str:
        if len(self.analyzed) < 2:
            return (
                f"duplication is undecidable: {len(self.analyzed)} analyzable page(s), "
                f"{len(self.unanalyzable)} unreadable"
            )
        line = (
            f"{self.duplicate_page_count} of {len(self.analyzed)} analyzed pages share "
            # The effective threshold, interpolated. The original hard-coded
            # ">80%" into this sentence while honouring --threshold elsewhere,
            # so a run at 0.5 reported pairs of 56% as being over 80%.
            f">={self.threshold:.0%} of their content "
            f"({len(self.groups)} duplicate group(s))"
        )
        if self.unanalyzable:
            line += f"; {len(self.unanalyzable)} page(s) could not be analyzed"
        return line


@dataclass(frozen=True)
class SourceOverlap:
    """How much of our page was found in one caller-supplied source."""

    label: str
    overlap: float
    jaccard: float

    @property
    def band(self) -> str:
        return overlap_band(self.overlap)


@dataclass(frozen=True)
class OverlapResult:
    """Verdict for overlap against external texts the CALLER provided."""

    status: Status
    sources: tuple[SourceOverlap, ...]
    expected_sources: int = SERP_SAMPLE_SIZE
    reasons: tuple[str, ...] = ()

    @property
    def overlap(self) -> float | None:
        """Worst-case overlap. None when there was nothing to compare against."""
        if not self.sources:
            return None
        return max(source.overlap for source in self.sources)

    @property
    def band(self) -> str | None:
        value = self.overlap
        return None if value is None else overlap_band(value)

    def summary(self) -> str:
        if not self.sources:
            return "no comparison texts supplied; content overlap was not measured"
        return (
            f"{self.overlap:.0%} of this page's content also appears in "
            f"{len(self.sources)} of {self.expected_sources} supplied source(s) "
            f"[{self.band}]"
        )


def _components(
    nodes: Sequence[str], edges: Sequence[tuple[str, str, float]]
) -> list[list[str]]:
    """Union-find over the similarity edges.

    Grouping by walking a dict and marking pages "processed" — the original
    approach — made the pair scan non-exhaustive: a page absorbed into an early
    group was skipped by every later inner loop, so the single most similar pair
    in a set could go unreported because one of its members had already been
    filed elsewhere. Every pair is scored here, and grouping happens afterwards
    over the complete edge list, which also removes the dependence on dict
    insertion order.
    """
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for left, right, _ in edges:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    grouped: dict[str, list[str]] = {}
    for node in nodes:
        grouped.setdefault(find(node), []).append(node)
    return list(grouped.values())


def find_duplicate_groups(
    texts: Mapping[str, str],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    shingle_size: int = SHINGLE_SIZE,
) -> tuple[DuplicateGroup, ...]:
    """Group pages linked by a similarity of at least `threshold`.

    Shingle sets are built once per page and reused across every pair, which is
    what makes the metric change a performance change as well as a correctness
    one: the old code re-aligned two full documents on every comparison.
    """
    urls = sorted(texts)
    sets = {url: shingles(texts[url], shingle_size) for url in urls}

    edges: list[tuple[str, str, float]] = []
    for index, left in enumerate(urls):
        for right in urls[index + 1 :]:
            score = jaccard(sets[left], sets[right])
            if score >= threshold:
                edges.append((left, right, score))

    # Each edge is filed under its left endpoint only. Both endpoints of an edge
    # are in the same component by construction, so collecting a component's
    # edges from its members needs no membership test on the right endpoint.
    edges_by_member: dict[str, list[tuple[str, str, float]]] = {}
    for edge in edges:
        edges_by_member.setdefault(edge[0], []).append(edge)

    groups: list[DuplicateGroup] = []
    for members in _components(urls, edges):
        if len(members) < 2:
            continue
        pairs = tuple(
            sorted(
                (edge for member in members for edge in edges_by_member.get(member, [])),
                # Strongest link first, then by URL: same crawl, same report.
                key=lambda edge: (-edge[2], edge[0], edge[1]),
            )
        )
        groups.append(DuplicateGroup(urls=tuple(sorted(members)), pairs=pairs))

    # Biggest group first; ties broken by URL so two runs over the same crawl
    # produce byte-identical reports.
    return tuple(sorted(groups, key=lambda group: (-len(group.urls), group.urls[0])))


def check_duplication(
    texts: Mapping[str, str],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    page_ratio_threshold: float = DEFAULT_DUPLICATE_PAGE_RATIO,
    unavailable: Mapping[str, str] | None = None,
    shingle_size: int = SHINGLE_SIZE,
) -> DuplicationResult:
    """Decide ADS-CONTENT-02's duplication question over a set of page texts.

    `unavailable` carries pages that could not be turned into text, with the
    reason for each. They are not dropped: any unreadable page escalates the
    result to ERROR, because the page we could not read may be the duplicate,
    and a partial answer to "is this site duplicated?" is not a pass. The
    original swallowed every fetch failure into `return None` and then announced
    "No significant duplicates found" over a crawl in which nothing had been
    fetched at all.
    """
    unanalyzable = dict(unavailable or {})
    usable: dict[str, str] = {}
    for url in sorted(texts):
        if tokenize(texts[url]):
            usable[url] = texts[url]
        else:
            # Empty text is an extraction failure, not a page with no words.
            unanalyzable.setdefault(url, "no extractable text")

    groups = find_duplicate_groups(usable, threshold=threshold, shingle_size=shingle_size)
    duplicate_pages = {url for group in groups for url in group.urls}

    status = Status.OK
    reasons: list[str] = []

    if unanalyzable:
        status = escalate(status, Status.ERROR)
        listed = ", ".join(f"{url} ({reason})" for url, reason in sorted(unanalyzable.items()))
        reasons.append(f"{len(unanalyzable)} page(s) could not be analyzed: {listed}")

    if len(usable) < 2:
        status = escalate(status, Status.ERROR)
        reasons.append(
            f"duplication requires at least 2 analyzable pages, got {len(usable)}; "
            "a single page cannot demonstrate the absence of duplicates"
        )
    else:
        ratio = len(duplicate_pages) / len(usable)
        if ratio > page_ratio_threshold:
            status = escalate(status, Status.FAIL)
            reasons.append(
                f"{len(duplicate_pages)} of {len(usable)} analyzed pages "
                f"({ratio:.0%}) are involved in duplication, over the "
                f"{page_ratio_threshold:.0%} allowance"
            )
        elif groups:
            status = escalate(status, Status.WARNING)
            reasons.append(
                f"{len(duplicate_pages)} of {len(usable)} analyzed pages "
                f"share >={threshold:.0%} of their content"
            )

    return DuplicationResult(
        status=status,
        threshold=threshold,
        page_ratio_threshold=page_ratio_threshold,
        analyzed=tuple(sorted(usable)),
        groups=groups,
        unanalyzable=unanalyzable,
        reasons=tuple(reasons),
    )


def compare_against(
    text: str,
    sources: Mapping[str, str] | Sequence[str] | str | None,
    *,
    shingle_size: int = SHINGLE_SIZE,
    expected_sources: int = SERP_SAMPLE_SIZE,
) -> OverlapResult:
    """Measure ADS-CONTENT-OVERLAP against texts the CALLER supplies.

    This function does not search anything. ADS-CONTENT-OVERLAP names the top 5
    SERP results for the page's topic; retrieving them is external work (a
    search API, a manual paste, a separate crawler) and belongs to whoever calls
    this. Two consequences are deliberate:

      * with no sources the status is MISSING, never OK — an unmeasured overlap
        is not a low overlap;
      * with fewer than `expected_sources` the status escalates to MISSING too,
        with a reason naming the shortfall, so a report cannot present a
        two-source comparison as if the requirement had been satisfied.

    `sources` may be a mapping of label -> text (a URL is the useful label), a
    plain sequence of texts, which get positional labels, or a single text.
    """
    if isinstance(sources, Mapping):
        labelled = list(sources.items())
    elif isinstance(sources, str):
        # One competitor text is the most natural call there is, and `str` is a
        # `Sequence[str]`, so a type checker approves it. Without this branch the
        # loop below iterates the CHARACTERS: every letter becomes a "source"
        # with no shingle in common with our page, and a verbatim copy comes back
        # as 0% overlap in the "safe" band, from "193 of 5 supplied source(s)".
        labelled = [("source-1", sources)]
    else:
        labelled = [(f"source-{i}", value) for i, value in enumerate(sources or [], start=1)]

    status = Status.OK
    reasons: list[str] = []

    own = shingles(text, shingle_size)
    if not own:
        # Nothing to measure from our side; saying "0% overlap" here would be a
        # clean bill of health issued over an empty file.
        return OverlapResult(
            status=Status.ERROR,
            sources=(),
            expected_sources=expected_sources,
            reasons=("page text is empty; overlap could not be measured",),
        )

    measured: list[SourceOverlap] = []
    for label, source_text in labelled:
        other = shingles(source_text, shingle_size)
        if not other:
            status = escalate(status, Status.MISSING)
            reasons.append(f"comparison source {label!r} has no text; it was not compared")
            continue
        measured.append(
            SourceOverlap(
                label=label,
                overlap=containment(own, other),
                jaccard=jaccard(own, other),
            )
        )

    if not measured:
        status = escalate(status, Status.MISSING)
        reasons.append(
            "no comparison texts supplied; this module performs no SERP search, so "
            "ADS-CONTENT-OVERLAP stays unmeasured until the caller provides the "
            f"top {expected_sources} results"
        )
    else:
        if len(measured) < expected_sources:
            status = escalate(status, Status.MISSING)
            reasons.append(
                f"compared against {len(measured)} of the {expected_sources} sources the "
                "requirement names; the remainder was not measured"
            )
        worst_source = max(measured, key=lambda source: source.overlap)
        if worst_source.overlap >= OVERLAP_HIGH_RISK:
            status = escalate(status, Status.FAIL)
        elif worst_source.overlap >= OVERLAP_MONITOR:
            status = escalate(status, Status.WARNING)
        reasons.append(
            f"{worst_source.overlap:.0%} overlap with {worst_source.label!r} "
            f"[{worst_source.band}]"
        )

    return OverlapResult(
        status=status,
        sources=tuple(sorted(measured, key=lambda source: (-source.overlap, source.label))),
        expected_sources=expected_sources,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class Corpus:
    """Texts that could be read, and the reason for each one that could not.

    `texts` is keyed by the identity of the page that answered — the normalized
    FINAL url — not by the url that was asked for. `aliases` maps every other
    requested url onto the entry that turned out to be the same page, so the
    caller can still see where each of its inputs went.
    """

    texts: dict[str, str] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)


def fetch_page_texts(
    urls: Iterable[str],
    *,
    extract: Callable[[str], str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Corpus:
    """Fetch each URL and extract its text, recording why any page is missing.

    `extract` defaults to `adsense_checks.text.extract_text`. It is a parameter
    because the similarity core has nothing to do with HTML parsing, and because
    ADS-CONTENT-03 wants main-content extraction (readability-style) rather than
    whole-body text; swapping the extractor should not mean forking this.

    Every URL ends up in exactly one of `texts`, `aliases` or `unavailable`.
    Nothing is dropped silently — the original returned None for any failure, so
    a crawl where every fetch 403'd was reported as a site with no duplicate
    content.

    A page is identified by `normalize_url(final_url)`, the url that actually
    answered, so several names for one page collapse into one entry instead of
    becoming N pages of identical text. Keying the corpus by the REQUESTED url
    turned `/quiz`, `/QUIZ` and `/quiz?utm_source=x` — all three 301 to `/quiz/`
    — into four pages sharing 100% of their content, and a healthy site failed
    ADS-CONTENT-02 with "4 of 5 analyzed pages (80%)". `crawl.normalize_url`
    already carries this identity, and its docstring records the same inflated
    count as the defect it was written for. Only a page that was read registers
    an identity: an unreadable url is reported under the name the caller used,
    because what that entry is for is telling the caller which input failed.

    Limitation worth stating: decoding is whatever `fetch` returned. A server
    that omits the charset leaves requests on its ISO-8859-1 fallback, and the
    resulting mojibake changes the tokens. That belongs to the fetch layer.
    """
    if extract is None:
        from adsense_checks.text import extract_text

        extract = extract_text

    corpus = Corpus()
    # Identity of the requested url -> key it resolved to in `texts`. Consulting
    # it before fetching also spares the server a request for a name already
    # known to be a page it has served.
    resolved: dict[str, str] = {}

    def record(requested: str, canonical: str) -> None:
        resolved[normalize_url(requested)] = canonical
        if requested != canonical:
            corpus.aliases[requested] = canonical

    for url in urls:
        known = resolved.get(normalize_url(url))
        if known is not None:
            record(url, known)
            continue

        result = fetch(url, timeout=timeout, session=session)
        if result.error is not None:
            corpus.unavailable[url] = result.error
            continue
        if not result.ok:
            corpus.unavailable[url] = f"HTTP {result.status_code}"
            continue

        canonical = normalize_url(result.final_url or url)
        if canonical in corpus.texts:
            record(url, canonical)
            continue

        content_type = result.headers.get("content-type", "").split(";")[0].strip().lower()
        if not content_type:
            # Without the header we cannot tell HTML from a PDF, and feeding a
            # PDF to an HTML parser yields binary noise that then competes as a
            # "page" in the similarity matrix.
            corpus.unavailable[url] = "missing Content-Type; cannot confirm HTML"
            continue
        if content_type not in HTML_CONTENT_TYPES:
            corpus.unavailable[url] = f"not HTML (Content-Type: {content_type})"
            continue

        try:
            text = extract(result.text)
        except Exception as exc:  # noqa: BLE001 - reason is recorded, not swallowed
            # The original wrapped the parse in `except Exception: pass` and then
            # returned whatever text had accumulated before the failure, so a
            # half-parsed page was compared against whole ones as if complete.
            corpus.unavailable[url] = f"extraction failed: {type(exc).__name__}: {exc}"
            continue

        if not tokenize(text):
            corpus.unavailable[url] = "no extractable text"
            continue

        corpus.texts[canonical] = text
        record(url, canonical)
    return corpus


def check_urls(
    urls: Iterable[str],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    page_ratio_threshold: float = DEFAULT_DUPLICATE_PAGE_RATIO,
    extract: Callable[[str], str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    shingle_size: int = SHINGLE_SIZE,
) -> DuplicationResult:
    """Fetch, extract and judge in one call, keeping the failures attached.

    URLs that turn out to be one page — a redirect, a trailing slash, a case
    variant — are collapsed into a single analyzed page here, which is why
    `analyzed` can be shorter than `urls` without anything having failed. Call
    `fetch_page_texts` directly to see which name resolved to which page.
    """
    corpus = fetch_page_texts(urls, extract=extract, timeout=timeout, session=session)
    return check_duplication(
        corpus.texts,
        threshold=threshold,
        page_ratio_threshold=page_ratio_threshold,
        unavailable=corpus.unavailable,
        shingle_size=shingle_size,
    )
