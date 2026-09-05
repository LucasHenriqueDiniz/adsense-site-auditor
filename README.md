# AdSense Site Auditor

A [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills) that audits websites against Google's official AdSense eligibility requirements, with automated detection for thin content and duplicate pages, and dedicated rubrics for tool/utility sites and quiz/entertainment sites.

Originally based on [yantoumu/adsense-site-auditor-skill](https://github.com/yantoumu/adsense-site-auditor-skill) (a Codex skill); rewritten for Claude Code with automation scripts, site-type rubrics, and additional audit modes.

## What it does

- Checks **81 requirement IDs**. `references/adsense-requirements.md` is the
  authoritative list; this number is a copy of it and `SKILL.md` says how to
  recount it. Sections A-I map to AdSense Help, the AdSense Program Policies,
  the Google Publisher Policies and the Publisher Restrictions, with source URLs
  in the reference. Sections J-L are this repo's reading of those same policies
  rather than separate documented rules, and each of the three says so in its
  own preamble.
- Forces every item to a `Pass` / `Fail` / `Unknown` / `N/A` verdict with evidence — no vague summaries
- Classifies findings by severity: `Blocker`, `High`, `Medium`
- Ships Python scripts to crawl a site, measure word count, detect near-duplicate pages, and check crawlability — so findings cite real numbers instead of impressions
- Includes dedicated rubrics for **tool/utility sites** and **quiz/entertainment sites**, the two patterns most likely to get flagged as low-value content
- Supports six audit modes: pre-application, post-rejection diagnosis, post-fix verification, repo + live URL, task generation, and health check

## Install

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/LucasHenriqueDiniz/adsense-site-auditor.git ~/.claude/skills/adsense-site-auditor
```

Restart Claude Code. Verify with:

```
/adsense-site-auditor
```

The helper scripts are optional. `requests` is the only package to install;
`adsense_checks.http` also imports `urllib3` by name, which arrives with it and
is declared in `pyproject.toml` so a resolver cannot pick a tree without it:

```bash
pip install requests
```

## Usage

Basic pre-application audit:

```
/adsense-site-auditor

URL: https://yoursite.com
Site type: Tool site
```

The skill reads `references/adsense-requirements.md`, walks every requirement ID, and returns a decision of `Not ready`, `Ready after fixes`, or `Ready`, with a full checklist table.

For richer evidence, run the scripts first and paste their output into the prompt:

```bash
python scripts/check_completeness.py https://yoursite.com
python scripts/check_technical.py https://yoursite.com
python scripts/crawl_site.py https://yoursite.com --depth 2
python scripts/analyze_text_depth.py https://yoursite.com/page-a https://yoursite.com/page-b
python scripts/check_duplicates.py https://yoursite.com/page-a https://yoursite.com/page-b
```

Each script takes URLs and prints to stdout. There is no `--output` flag and no
intermediate `crawl.json`: the block above used to pass one script's output file
to the next, and neither the flag nor the file mode exists. Redirect if you want
a file. Exit status is 0 only when every check observed its condition and passed —
anything unobserved is non-zero, so these are usable in CI.

See [USAGE.md](USAGE.md) for all six audit modes. [EXAMPLES.md](EXAMPLES.md) shows
what each script prints, against a local fixture you can rebuild in one command,
and records the single site whose real AdSense verdict is known.

## Audit modes

| Mode | When to use |
|---|---|
| Pre-application | Before applying for AdSense (runs pre-flight gate, then full audit) |
| Post-rejection diagnosis | Map a rejection message to specific requirement IDs |
| Post-fix verification | Confirm fixes resolved prior findings before resubmitting |
| Repo + live URL | Site has source code available (Vite/React/static) — inspect templates and rendered output together |
| Task generation | Convert findings into a prioritized task list (Todoist/GitHub format) |
| Health check | Spot-check an already-approved site for new risk |

## Pre-Flight Gate

Before running the full requirement audit, the skill checks four gate items that
are common causes of rejection. `SKILL.md` holds the gate itself — the sub-checks,
the scoring and the stop rule — and this list is a summary of it. The ordering is a
judgement call, not a measurement: this repo has no data on how AdSense rejections
are actually distributed.

1. **Site Completeness** (ADS-COMPLETE-01) — Missing About, Contact placeholder, "Coming Soon" pages, broken navigation
2. **Publisher Identity** (ADS-AUTHOR-01) — No verifiable real name, no contact method
3. **Minimum Content** (ADS-COMPLETE-02) — thresholds defined with the requirement, not here
4. **Bulk page sample** (ADS-CONTENT-03) — measure at least ten pages from the
   largest section of the site, not the trust pages and not the guides

Item 4 exists because of the one real outcome this repo has: a site that cleared
items 1-3 and was rejected anyway, with the answer sitting in the catalogue pages
the gate never sampled. See `TESTING.md` and Part 2 of `EXAMPLES.md`.

**If the gate reports a blocker, the skill stops and recommends structural fixes before proceeding.**

The skill has no measured accuracy, and does not claim one. See `TESTING.md` for
what would be needed to measure it.

## Repository structure

```
.
├── README.md                         This file
├── SKILL.md                          Skill entry point read by Claude Code
├── USAGE.md                          All audit modes with example prompts
├── EXAMPLES.md                       Fixture runs, and the one real outcome on record
├── TESTING.md                        What is tested, what is not, how to measure it
├── LICENSE                           MIT
├── pyproject.toml                    Package metadata, pytest and ruff config
│
├── references/
│   ├── adsense-requirements.md       The requirement list: IDs, decidability, severity, sources
│   ├── tool-site-rubric.md           Quality checks for utility/calculator sites
│   ├── quiz-site-rubric.md           Quality checks for quiz/entertainment sites
│   └── usage-prompts.md              Copy-paste prompt templates
│
├── templates/
│   └── task-output-template.md       Findings → Todoist/GitHub task format
│
├── adsense_checks/                   The only Python package. Every decision lives here.
│   ├── status.py                     Ordered statuses; combination can only escalate
│   ├── report.py                     Rendering, the tally, the verdict, the exit code
│   ├── http.py                       Fetching as Mediapartners-Google, redirects, timing
│   ├── crawl.py                      Breadth-first crawl; reachability, redirects, URL stability
│   ├── robots.py                     robots.txt parsing and per-crawler rules
│   ├── sitemap.py                    Sitemap discovery and parsing
│   ├── completeness.py               Placeholders, trust pages, contact channels, navigation
│   ├── text.py                       Main-content extraction and word counts
│   └── duplicates.py                 Shingles and Jaccard similarity
│
├── scripts/                          Thin CLIs over the package; they decide nothing
│   ├── README.md                     Script usage guide
│   ├── crawl_site.py                 Crawl; reachability, redirect chains, URL stability
│   ├── analyze_text_depth.py         Main-content word count per page
│   ├── check_duplicates.py           Near-duplicates among the URLs you name
│   ├── check_technical.py            robots.txt, sitemap, reachability, DNS/TLS/response time
│   └── check_completeness.py         The pre-flight gate: placeholders, trust pages,
│                                     contact channel, broken navigation
│
└── tests/                            Runs against a real local HTTP server, not a mock
    └── ... one module per checked module, plus one per CLI
```

## Why site-type rubrics

Generic "thin content" advice doesn't transfer well to two common archetypes:

- **Tool sites** (converters, calculators, generators) tend to be rejected for having a working widget and almost no surrounding text. [`tool-site-rubric.md`](references/tool-site-rubric.md) checks for an explanation, word count, original guidance, and internal linking per tool.
- **Quiz/entertainment sites** tend to be rejected for mass-templated quizzes with auto-generated result pages. [`quiz-site-rubric.md`](references/quiz-site-rubric.md) checks question originality, result-page uniqueness, and template text overlap (measurable with `check_duplicates.py`).

## Disclaimer

This skill cannot guarantee AdSense approval. It encodes Google's published policies as of the date noted in `references/adsense-requirements.md`; policies change, and the live Google documentation always takes precedence over this checklist.

## License

[MIT](LICENSE)
