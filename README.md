# AdSense Site Auditor

A [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills) that audits websites against Google's official AdSense eligibility requirements, with automated detection for thin content and duplicate pages, and dedicated rubrics for tool/utility sites and quiz/entertainment sites.

Originally based on [yantoumu/adsense-site-auditor-skill](https://github.com/yantoumu/adsense-site-auditor-skill) (a Codex skill); rewritten for Claude Code with automation scripts, site-type rubrics, and additional audit modes.

## What it does

- Checks **73 official requirement IDs** sourced from AdSense Help, AdSense Program Policies, Google Publisher Policies, and Google Publisher Restrictions
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

The helper scripts are optional and only need `requests`:

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
python scripts/crawl_site.py https://yoursite.com --depth 2 --output crawl.json
python scripts/analyze_text_depth.py crawl.json --min-words 300
python scripts/check_duplicates.py crawl.json --threshold 0.8
python scripts/check_technical.py https://yoursite.com
```

See [USAGE.md](USAGE.md) for all six audit modes and [EXAMPLES.md](EXAMPLES.md) for full worked audits (tool site, quiz site, post-rejection, and a Vite/React repo + live audit).

## Audit modes

| Mode | When to use |
|---|---|
| Pre-application | Before applying for AdSense |
| Post-rejection diagnosis | Map a rejection message to specific requirement IDs |
| Post-fix verification | Confirm fixes resolved prior findings before resubmitting |
| Repo + live URL | Site has source code available (Vite/React/static) — inspect templates and rendered output together |
| Task generation | Convert findings into a prioritized task list (Todoist/GitHub format) |
| Health check | Spot-check an already-approved site for new risk |

## Repository structure

```
.
├── SKILL.md                          Skill entry point read by Claude Code
├── USAGE.md                          All audit modes with example prompts
├── EXAMPLES.md                       Full worked audits
├── references/
│   ├── adsense-requirements.md       73 requirement IDs, severity, source URLs
│   ├── tool-site-rubric.md           Quality checks for utility/calculator sites
│   ├── quiz-site-rubric.md           Quality checks for quiz/entertainment sites
│   └── usage-prompts.md              Copy-paste prompt templates
├── templates/
│   └── task-output-template.md      Findings → Todoist/GitHub task format
└── scripts/
    ├── crawl_site.py                 Crawl a site, collect URLs/titles/meta
    ├── analyze_text_depth.py         Flag pages under a word-count threshold
    ├── check_duplicates.py           Measure text similarity across pages
    └── check_technical.py            robots.txt, sitemap.xml, HTTPS, redirects
```

## Why site-type rubrics

Generic "thin content" advice doesn't transfer well to two common archetypes:

- **Tool sites** (converters, calculators, generators) tend to be rejected for having a working widget and almost no surrounding text. [`tool-site-rubric.md`](references/tool-site-rubric.md) checks for an explanation, word count, original guidance, and internal linking per tool.
- **Quiz/entertainment sites** tend to be rejected for mass-templated quizzes with auto-generated result pages. [`quiz-site-rubric.md`](references/quiz-site-rubric.md) checks question originality, result-page uniqueness, and template text overlap (measurable with `check_duplicates.py`).

## Disclaimer

This skill cannot guarantee AdSense approval. It encodes Google's published policies as of the date noted in `references/adsense-requirements.md`; policies change, and the live Google documentation always takes precedence over this checklist.

## License

[MIT](LICENSE)
