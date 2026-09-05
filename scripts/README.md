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

`check_completeness.py` also asks the audited host, once per run, for one or
two paths that nothing can route — `adsense-auditor-probe-no-such-page` and a
second one. That is how it tells a working page from a "page not found"
template served with HTTP 200: where the host answers 4xx, a 200 means the page
is there; where it does not, no 200 from it is evidence either way and the
links are reported as unverified rather than as broken.

The probe is asked at the same base every other URL of the run is asked at —
the navigation links, the About/Contact pages the home page links to, and the
conventional About/Contact paths this script guesses. That base is the home
page's own `<base href>` when it declares one, and the URL that answered
otherwise, so on a site declaring `<base href="/app/">` all of it happens
inside `/app/`. The answer is a fact about a directory and not about a host, so
splitting it — probing one place and requesting another — reports a site as
whole over dead links, which is what it used to do.

That base never leaves the site you named. An off-site or non-HTTP
`<base href>` is honoured for the links the page actually wrote — they do point
elsewhere, and they are skipped as off-site — but the probe and the guessed
paths fall back to the audited host, the way `crawl_site.py` refuses a seed
that is not on the site under audit.

Only the FIRST probe path can appear in a 404 log. The second is sent only to a
host that already answered below 400 to the first, so it never 404s; and on a
host that soft-404s, neither path appears in the log at all.

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
