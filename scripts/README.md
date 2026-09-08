# AdSense Site Auditor — Helper Scripts

Five CLIs over `adsense_checks/`, where every decision lives and is unit-tested.
Each script is a thin wrapper: it parses arguments, calls the module, and renders.
Nothing here decides anything on its own.

This file used to document a different set of scripts — flags, JSON hand-off
files and checks that no longer exist — so treat the table below, and
`--help`, as the only description of the current surface.

## The one rule

**A check that could not observe its condition never reports a pass.** Every
script exits non-zero unless every check it ran observed its condition and that
condition held. A site the script could not read is `ERROR`, not "0 problems";
a sample that never grew past the homepage is `MISSING`, not "all pages fine".

| Status | Meaning | Exit |
| --- | --- | --- |
| `PASS` | Observed, and the condition held | 0 |
| `note` | An observation the report does not decide on | 0 |
| `MISS` | The thing checked for is absent, or could not be observed | 1 |
| `WARN` | Observed, and below the configured bar | 1 |
| `FAIL` | Observed, and broken | 1 |
| `ERR ` | The check itself could not run | 1 |

## Quick start

```bash
uv pip install -e .          # requests, and urllib3 that comes with it
python scripts/check_completeness.py https://example.com
```

Every script takes URLs and writes to stdout. There is **no `--output` flag** and
**no JSON hand-off file**: redirect if you want a file.

## Scripts

| Script | Arguments | Requirements | What it decides |
| --- | --- | --- | --- |
| `check_completeness.py` | `URL` `[--nav-limit N] [--timeout S]` | ADS-COMPLETE-01, ADS-UX-05, ADS-AUTHOR-02 (part) | Unfinished markers on the home page, About/Contact present and not stubs, a contact channel in the HTML, navigation links that 4xx/5xx. Links answering 200 on a host that also answers 200 for URLs it does not have are reported `MISSING` — unverified, not broken: they are left out of the broken count and cannot reach the FAIL threshold |
| `check_technical.py` | `URL` `[--timeout S]` | ADS-CRAWL-01, -02, -06, -07 | Reachability, robots.txt for the three Google crawlers, sitemap discovery, parsing and a five-URL sample fetched to see whether what it advertises answers 200, DNS/TLS/response time |
| `crawl_site.py` | `URL` `[--depth N] [--max-pages N] [--delay S] [--timeout S] [--verify-stateless] [--verify-canonical]` | ADS-CRAWL-01, -04, -05 | Breadth-first crawl; pages answer 2xx publicly, redirect chains are short and stateless, URLs carry no session ids |
| `analyze_text_depth.py` | `URL [URL ...]` `[--min-words N]` | ADS-CONTENT-03 | Main-content word count per page, chrome excluded where detectable. It does **not** decide ADS-COMPLETE-02: that requirement counts three articles over 1200 words, and this measures one page at a time |
| `check_duplicates.py` | `URL [URL ...]` `[--threshold F]` | ADS-CONTENT-02 (part), ADS-CONTENT-OVERLAP | Near-duplicate groups among the URLs you name, by word shingles and Jaccard. ADS-CONTENT-OVERLAP is reported `MISSING` on every run: no search is performed |

All five take `-v/--verbose`, which prints the per-check details dictionary.
`crawl_site.py -v` additionally dumps per-page evidence: title, H1 count, meta
description, visible word count, and the links it chose not to follow.

### Defaults worth knowing

| Flag | Default | Why |
| --- | --- | --- |
| `--depth` | 2 | |
| `--max-pages` | 50 | |
| `--delay` | 0.5s | Politeness. A crawler with no ceiling and no pause is a load generator. |
| `--min-words` | 300 | A **review threshold**, not a Google policy line — AdSense publishes no word count. ADS-COMPLETE-02's 1200 is in the requirement itself. |
| `--threshold` | 0.6 | ADS-CONTENT-OVERLAP puts high risk above 60%. The 0.8 this file used to document sat above that entire band, so every pair the rubric wants flagged went unreported. |
| `--nav-limit` | 25 | When the limit bites, the broken-link count is a lower bound and the report says so. |
| `--verify-stateless` | off | Re-requests each redirecting page without cookies, one extra request each. Any page that redirects holds ADS-CRAWL-04 at `MISSING` until it runs. |
| `--verify-canonical` | off | Re-requests every page twice from fresh sessions to compare the canonical. ADS-CRAWL-05 reports `MISSING` until it runs, so on a site that declares canonicals this flag is the only route to exit 0. |

`check_completeness.py` also asks the audited host, for a handful of paths that
nothing can route — `adsense-auditor-probe-no-such-page` and a second one. That
is how it tells a working page from a "page not found" template served with
HTTP 200: where the server answers 404 or 410 — routed, and there is nothing here
— a 200 means the page is there. Where it does not, no 200 from it is evidence
either way and the links are reported as unverified rather than as broken. A
401, 403, 429 or 5xx counts as "does not": those refuse the request instead of
routing it, and a WAF answering 403 to a path shaped like a scan says nothing
about what the site does with a page it lacks.

The answer is a fact about ONE DIRECTORY, never about the host. A WordPress
install under `/blog/` and a static apex above it answer that question
differently, so the question is asked **per directory**: every directory a
response actually came back from is asked about itself, and no directory's
answer is ever allowed to classify another's. Reading one off the other is what
wrecked four attempts at this in a row — the last of them by treating
"contained in the probed directory" as "measured by the probe", which is a no-op
when the probed directory is the root.

The directory a URL belongs to is what `urljoin(url, ".")` says, so a
document-shaped address — `/index.php`, from an apex that redirects there —
belongs to the directory containing it, and a trailing slash makes an address
its own directory. That second part is what costs: on a modern menu nearly every
link is a directory of its own.

| | |
|---|---|
| ceiling | **8 distinct directories per run** (`MAX_PROBED_DIRECTORIES`) |
| worst case on the wire | 8 invented requests, or 16 where every directory soft-404s |
| measured, documented fixture | 6 directories, 15 requests in total against 10 before |
| measured, 25-link menu of 25 directories | 8 probes against 43 requests to real addresses |
| conventional About/Contact paths | one slot between all seventeen, asked at the base they are invented from |
| past the ceiling | `MISSING` — unverified, and the report names the first five directories and counts the rest |
| coverage it costs | a 25-link menu spread over 25 directories has 18 links reported `MISSING` on a host that answers 404 honestly |

That last row is the price and it is not small: on a site whose menu is spread
wider than the ceiling, most links come back unverified even though the host is
honest and every one of them works. The run says so — `MISSING` is exit 1, and
the sentence names five directories and then says how many more it did not name,
so a truncated list cannot be mistaken for a complete one. But a reader who
expects a clean pass from a healthy site should know why they did not get one.
There is no flag for it: raising the ceiling means editing
`MAX_PROBED_DIRECTORIES` in `adsense_checks/completeness.py`, and it buys the
coverage at the cost of more requests for addresses nobody routes. The value is
a design decision about someone else's error log, which is why it is a constant
with the reasoning written on it rather than a knob.

The ceiling is its own constant and **not** shared with `--nav-limit`. The two
bound different things: `--nav-limit` bounds how much of the site's own
published navigation is read — addresses that exist, which a browser fetches
too — while this bounds requests for addresses nobody routes, which only this
tool sends and which land in someone's error log. Sharing them would make the
invented load grow with the size of the menu, so raising `--nav-limit` to see a
big menu would silently double the footprint on a third party. Eight is the
smallest value that covers the documented fixture's six with room for the two
additions that are ordinary rather than exotic: a `<base href>` install
directory, and a footer trust link in a directory of its own.

No delay is inserted between probes, and there is no flag for one.
`check_completeness.py` spaces none of its ~20 requests, so slowing only the
invented ones would make them politer than the site's own pages while leaving
the run's shape unchanged; the lever here is the NUMBER of invented requests,
which is what a server's log counts. `--delay` belongs to `crawl_site.py`, which
walks a whole site.

Two things cost nothing. A link observed to answer 4xx/5xx is broken on the
evidence and needs no probe at all — the fixture's `/pricing/` adds nothing to
the count. And a site that keeps everything in one directory costs exactly one
probe, which is what a single-probe run cost before.

What each answer does, per directory:

- **4xx to the probe.** That directory spends a real status code on a missing
  page, so a 200 from it is a page and a 4xx/5xx is a broken link. One request.
- **200 to the probe.** No 200 from that directory is evidence, so every one of
  them is `MISSING` — `same_as_not_found` where the response is exactly the page
  that directory serves for nothing, `unverified` otherwise. A second request
  goes out here, and only here, to find out whether that page is stable enough
  to recognise the others by. It is asked per directory rather than once per
  host because in `check_trust_pages` it changes a verdict: the fingerprint is
  what excludes a not-found template from passing as an About page on the
  strength of its prose, and a linked About at `/loja/quem-eu-sou` lands in its
  own directory.
- **Never asked.** The ceiling was spent, or the response came from another
  origin, which this script does not send invented URLs to. `MISSING` again, and
  the report prints `refused_directories` under `-v` so the operator can point a
  second run at the subdirectory.

Accepting a candidate and excluding one are not the same question, and they do
not take the same evidence. **Excluding** never sends a request: an answer
already measured for that directory is free, and so is the base's, because an
address this script invented is a guess that a directory exists — if it does
not, the base's router is what answered. That is what keeps the seventeen
conventional About/Contact paths to one slot between them. **Accepting** a
candidate as the About or Contact page requires an answer for the directory its
response actually came from, and asks that directory when none is in hand.
Otherwise `/about/` mounted as its own router that soft-404s is accepted on the
apex's answer — a router its response never touched — and on a site with no
About page the `FAIL` saying neither page was found drops out of the report.
Only a candidate that already answered 200 and survived every free exclusion
reaches the ask, and accepting stops the loop, so the added cost is at most one
directory per page kind: nothing on an honest host, nothing on a single
catch-all, two extra requests where a guessed directory turns out to be a real
one with its own soft-404 template.

A trust page found in a directory that answered 200 for a URL it does not
have — or in one the ceiling refused — is still reported at the status its own
content earns, because that check has its own discriminators (word count,
unfinished markers) and does not rest on the status code alone. Turning it into
`MISSING` would make a ceiling say "this site has no About page", and paired
with Contact that is a `FAIL` on a healthy site. What the report adds is a
`MISSING` finding beside it saying the page was judged on its content rather
than on its status code — the same weight the navigation half gives the same
observation, and enough that the run does not exit 0 with the note unread. This
is the residual hole, stated: such a page passes, and the sentence is what sends
a human to look.

The directory the script invents URLs in — its own probe paths and the
conventional About/Contact paths it guesses — never leaves the site you named.
An off-site or non-HTTP `<base href>` is honoured for the links the page
actually wrote, since they do point elsewhere and are skipped as off-site, but
the invented addresses fall back to the audited host, the way `crawl_site.py`
refuses a seed that is not on the site under audit. A third-party host receives
zero requests, probes included. When that happens the report says so, naming
both the base it refused and the links it dropped because of it.

Either probe path can appear in a 404 log. The second is sent only into a
directory that already answered below 400 to the first, which usually means it
answers below 400 to everything too — but not always: a catch-all matching only
alphabetic slugs lets the second path through to a real 404, because that one
carries digits. The script has a branch for exactly that answer and prints it,
since two different status codes for two URLs that both do not exist is itself
the finding.

The crawler identifies itself as `Mediapartners-Google`, because the question
this audit asks is what the AdSense crawler is served — sites do serve it
differently.

## What these scripts do NOT do

Stated because each one used to be claimed somewhere:

- **No SERP search.** ADS-CONTENT-OVERLAP asks for similarity against the top 5
  search results. `check_duplicates.py` compares the URLs you give it against
  each other and nothing else. `adsense_checks.duplicates.compare_against` takes
  competitor texts a caller supplies; obtaining them is external work.
- **No security headers, and no uptime.** CSP, X-Frame-Options and HSTS are
  checked by nothing in this repo. `check_technical.py` reports three of
  ADS-CRAWL-06's four parts — DNS, TLS, response time — and names uptime as an
  explicit gap: one request cannot establish reliability over time.
- **No JavaScript.** A client-rendered shell is reported as `ERROR`, not as a
  thin page: it is a page this audit cannot read, which is a different finding.
- **Nowhere near every requirement.** These scripts stamp **12 requirement IDs**.
  Eleven of them are among the 35 marked `auto` in
  `references/adsense-requirements.md`; the twelfth, `ADS-CONTENT-02`, is marked
  `judgement`, so the scripts contribute evidence toward it and do not settle
  it. That leaves 24 `auto` requirements with no implementation at all — everything under `ADS-PRIV`, `ADS-TXT`, `ADS-PROG` and
  all three `auto` rows under `ADS-PUB`. Those four prefixes are 14 of the 24;
  the other ten are `ADS-ELIG-04`, `ADS-OWN-03`, `ADS-CONTENT-04/-05/-06`,
  `ADS-UX-04`, `ADS-CRAWL-03`, `ADS-REST-08`, `ADS-COMPLETE-02` and
  `ADS-AUTHOR-03`. Treat a requirement these scripts never stamp as unaudited,
  not as passing.

  Re-derive the 12 rather than trusting it. Match the quoted literal, not any
  mention: several IDs appear in comments precisely to say they are *not*
  decided here, and a looser grep counts those too.

  ```bash
  grep -rhoE '"ADS-[A-Z0-9-]+( \(part\))?"' scripts/ adsense_checks/ --include='*.py' | sort -u
  ```

  Counting broken navigation links is `ADS-COMPLETE-01`'s "the site looks
  abandoned", not `ADS-UX-01` — that one is `judgement`, about readability,
  alignment and dropdowns, and nothing here decides it.

## Workflow

```bash
# 1. Pre-flight. If this fails, fix the structure before going further.
python scripts/check_completeness.py https://example.com

# 2. Crawlability and technical availability.
python scripts/check_technical.py https://example.com
python scripts/crawl_site.py https://example.com --depth 2 -v

# 3. Content. Sample the largest SECTION of the site, not the trust pages:
#    a catalogue site's best-written pages are exactly the ones a gate looks at.
python scripts/analyze_text_depth.py https://example.com/tool-a https://example.com/tool-b
python scripts/check_duplicates.py https://example.com/tool-a https://example.com/tool-b
```

Then paste the output into the skill invocation, which walks every requirement ID
and returns `Not ready`, `Ready after fixes` or `Ready` with a checklist.

## Tests

```bash
uv pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
```

The suite runs against a real local HTTP server rather than a mocked `requests`:
most of the defects these modules were written for were protocol behaviour — a
redirect followed silently, a 403 confused with a 404, `HEAD` refused by a WAF —
and a mock reproduces the wrong assumption instead of the protocol.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `MissingSchema: Invalid URL 'crawl.json'` | You used an old command. These scripts take URLs, not files. |
| `unrecognized arguments: --output` | Same: the flag no longer exists. Redirect stdout. |
| Everything `ERR` with a connection error | The site refused `Mediapartners-Google`, which is itself the finding for ADS-CRAWL-02. |
| `MISS` on a check that looks fine | Read the line. `MISSING` means the condition was not observed, which is deliberately not a pass. |
| Exit 1 with no `FAIL` | Something was `MISS` or `WARN`. Only `PASS`/`note` exit 0. |

## License

See `LICENSE` at the repository root.
