---
name: adsense-site-auditor
description: Audit websites for Google AdSense application readiness and ad-serving compliance. Use when checking whether a site is likely to satisfy AdSense eligibility, site ownership, content quality, navigation, crawler access, ads.txt, privacy disclosure, Google Publisher Policies, AdSense Program policies, or when the user asks if a site can apply for AdSense, pass AdSense review, show ads, or fix AdSense rejection/site-not-ready issues.
---

# AdSense Site Auditor

## Core Rule

Use Google AdSense and Google Publisher official documentation as the source of truth. This skill converts those docs into an actionable website audit, but it cannot guarantee approval.

Before a serious audit, refresh the official docs when internet access is available because AdSense policies can change. If live docs conflict with this skill, the live Google docs win.

Every audit must explicitly evaluate every checklist item in `references/adsense-requirements.md`. Do not sample, summarize, or check only likely problem areas. For each requirement ID, assign exactly one status: `Pass`, `Fail`, `Unknown`, or `N/A`. Use `N/A` only when the requirement truly does not apply to the site type or monetization mode, and state why.

**Site Type Detection**: Before auditing, identify whether the site is a:
- **Tool/App site** (utility, calculator, converter, generator) — use `references/tool-site-rubric.md` for content quality checks
- **Quiz/Entertainment site** (quizzes, polls, interactive content) — use `references/quiz-site-rubric.md` for uniqueness and value checks
- **Blog/News site** — standard ADS requirements apply
- **Directory/Listing site** — check for scraped listings and thin pages
- **Hybrid** — apply rubrics to each section

## Required Reference

Read `references/adsense-requirements.md` before auditing. It contains the full checklist, severity mapping, and source URLs.

For user-facing invocation examples and reusable prompts, read `references/usage-prompts.md` when the user asks how to use this skill, asks for prompt templates, or needs an audit request template.

## Pre-Flight Completeness Gate

**Before running the full requirement audit**, work these four items. This
section is the gate; `README.md` summarises it and defers here. They are ordered
first because they are cheap and commonly decisive, not because of any measured
share of rejections — Google publishes none, and the figure that stood here had
no source.

1. **Site Completeness** (ADS-COMPLETE-01) — four conditions, scored together.
   Count how many fail; no single one is a blocker on its own.
   - Missing About page?
   - Contact page is placeholder ("will be added here")?
   - Tool/feature pages say "Coming Soon" or "Not yet"?
   - Broken nav links (404s) on homepage?
   - Score: 0-1 fails = Pass → proceed to full audit
   - Score: 2-3 fails = High Risk → flag before full audit
   - Score: 4+ fails = Blocker → STOP, recommend major structural fixes

2. **Publisher Identity** (ADS-AUTHOR-01)
   - About page missing? ❌ Blocker
   - No real name (just pseudonym or anonymous)? ❌ Blocker
   - No contact method (email, form, or social)? ❌ Blocker
   - Score: 0 fails = Pass → proceed to full audit
   - Score: 1+ fails = Blocker → STOP, require identity verification before applying

3. **Minimum Content** (ADS-COMPLETE-02)
   - Counts and thresholds live in `references/adsense-requirements.md` under
     that ID, and nowhere else. This file used to say 1000 words while the
     reference said 1200, which is what happens when a number is written twice.
   - Below the threshold is High Risk, a soft gate: flag it and continue.

4. **Bulk page sample** (ADS-CONTENT-03) — **the gate's blind spot**
   - Sample at least 10 pages from the largest section of the site, not the
     trust pages and not the guides. Measure each with
     `scripts/analyze_text_depth.py`.
   - If the median falls below the ADS-CONTENT-03 threshold: ❌ Blocker.
   - Why this step exists: smallwebapps.com passed items 1 through 3 — About and
     Contact both real and substantial, twelve guides with eight above 1200
     words, no placeholders, no broken navigation — and Google rejected the
     application. Its 48 tool pages have a median of 220 words and 45 of them sit
     below 300, which items 1-3 never look at. On a catalogue site the trust
     pages and the guides are the best-written part; the catalogue is the site.
     A gate that samples only the former passes a site whose latter half is the
     problem. The measurements are in Part 2 of `EXAMPLES.md`; they were taken on
     2026-08-30 and no command reproduces them.

**Decision at pre-flight:**
- **0 Blockers**: Proceed to the full requirement audit
- **1-2 Blockers**: Output "Not ready — fix structural issues first" + specific list
- **3+ Blockers**: Output "Not ready — site appears unfinished"

---

## Audit Modes

The skill supports these audit contexts. Identify which mode applies before starting:

1. **Pre-Application Audit** — Site owner wants to know if ready to apply. Runs the pre-flight gate first, then every requirement ID in `references/adsense-requirements.md` if no blockers.
2. **Post-Rejection Diagnosis** — Site was rejected; map rejection reason to ADS-* IDs and provide priority fix list.
3. **Post-Fix Verification** — Owner claims fixes are done; re-audit to confirm and assess readiness to resubmit.
4. **Task Generation** — Convert audit findings into actionable work items (prioritized, with file/page specifics).
5. **Repo + Live URL Audit** — Inspect source code (Vite/React/static HTML), routes, templates, and live rendering together.
6. **Health Check** — Quick scan for new risks on a already-approved site (post-approval monitoring).

## Audit Workflow

1. Identify the target and mode:
   - Live URL/domain, repo path (if available), or both.
   - Audit context (pre-application, post-rejection, verification, task output, health check).
   - Site type: tool/app, quiz, blog, directory, ecommerce, or hybrid.

2. Gather evidence:
   - Crawl the homepage and 5–10 representative content pages (or use `scripts/crawl_site.py` for automated list).
   - Check `robots.txt`, `sitemap.xml`, canonical URLs, redirects, HTTP status, login walls, WAF/geoblocking, and POST-only page gating.
   - Inspect privacy policy, about/contact/ownership, navigation flow, content depth, ad/affiliate density, copied content signals, and prohibited content risks.
   - If repo access exists, inspect templates, routes, content sources, and generated pages rather than only rendered output.
   - **For tool/quiz sites**: Run site-type-specific rubrics (see `references/tool-site-rubric.md` and `references/quiz-site-rubric.md`).
   - **For thin content risk**: Use `scripts/analyze_text_depth.py` to detect pages under the target word count. It measures length and the main-content share, not originality — that is `check_duplicates.py`, and only against URLs you name.
   - **For duplication risk**: Use `scripts/check_duplicates.py` to find template boilerplate reuse and near-duplicate pages.

3. Classify findings:
   - `Blocker`: likely to prevent application approval or violate a hard policy.
   - `High`: meaningful approval or ad-serving risk.
   - `Medium`: quality, crawlability, UX, disclosure, or evidence gap that should be fixed before applying.
   - `Pass`: checked with evidence.
   - `Unknown`: cannot verify from available access; state exactly what is needed.
   - `N/A`: not applicable; state the site condition that makes it irrelevant.

4. Produce an audit report:
   - Executive decision: `Not ready`, `Ready after fixes`, or `Ready`.
   - Findings first, ordered by severity and requirement ID.
   - For each finding: requirement ID, issue, evidence (URL + quote or observation), official Google basis, exact fix.
   - Include a final exhaustive checklist table covering every requirement ID, with `Pass`/`Fail`/`Unknown`/`N/A`, evidence, and next action.
   - If mode is "Task Generation": convert Blockers and High findings into prioritized work items using `templates/task-output-template.md`.

## Implementation Guidance

### Concrete Over Generic

Prefer specific, verifiable checks over vague assessments. Examples:

- ❌ "crawler issue" → ✅ "robots.txt blocks Mediapartners-Google on /articles/* paths"
- ❌ "thin content" → ✅ "42 of 73 tool pages are under 300 words with no original guidance"
- ❌ "too many ads" → ✅ "above-the-fold contains 3 ad slots and 180 words of content (ad density 1.7:1)"
- ❌ "privacy policy incomplete" → ✅ "privacy policy does not disclose Google Analytics 4 or Google Ads tracking"
- ❌ "duplicate pages" → ✅ "quiz result pages use identical template; 91% text overlap across 28 results"

### Content Quality Specifics

For **tool/app sites**: Check that each tool page includes original guidance, examples, use-case explanation, and limitations—not just the interactive element. See `references/tool-site-rubric.md`.

For **quiz/entertainment sites**: Check that quizzes have distinct questions and results, not boilerplate or mass-generated variants. See `references/quiz-site-rubric.md`.

### Automation Support

Use helper scripts when available:
- `scripts/check_completeness.py URL` — items 1 and 2 of the gate above, in part:
  placeholder text (ADS-COMPLETE-01), the About and Contact pages (ADS-UX-05),
  a contact channel in the HTML (ADS-AUTHOR-02) and navigation links that 4xx/5xx
  (ADS-COMPLETE-01). It does not decide ADS-AUTHOR-01 — whether a real name or
  registered company stands behind the site is `judgement`, and no script here
  touches it. Item 4 of the gate is `analyze_text_depth.py`, run over a sample
  you choose
- `scripts/crawl_site.py URL [--depth N]` — crawl homepage and internal pages;
  reachability, redirect chains, URL stability. It does not fetch the sitemap —
  that is `check_technical.py`'s ADS-CRAWL-07 line
- `scripts/analyze_text_depth.py URL [URL ...] [--min-words 300]` — detect thin pages
- `scripts/check_duplicates.py URL [URL ...] [--threshold 0.6]` — find near-duplicate
  pages. The threshold defaults to 0.6 because `ADS-CONTENT-OVERLAP` puts high risk
  above 60%; the 0.8 documented here before sat above that entire band, so every
  pair the rubric wants flagged went unreported.
- `scripts/check_technical.py URL` — robots.txt, sitemap, reachability, and
  three of `ADS-CRAWL-06`'s four parts: DNS, TLS and response time. Uptime it
  reports as an explicit gap — one request cannot establish reliability over
  time, so that quarter of the requirement needs monitoring, not an audit run.
  Security headers are checked by nothing in this repo.

### Readiness Decision

Do not advise applying until:
- ✅ All Blockers are resolved
- ✅ High risks are either fixed or explicitly accepted by the owner with documented reasoning
- ✅ Medium risks have a remediation plan with a timeline

## Completeness Gate

Before finishing, list the requirement IDs in `references/adsense-requirements.md`
and compare them with the IDs in the final checklist. If any ID is missing, the
audit is incomplete. Add the missing rows before giving a final readiness decision.

Take the IDs from the requirement table rows in sections A through L:

```bash
awk '/^## M\./{exit} /^\| ADS-/{print $2}' references/adsense-requirements.md | sort -u
```

That prints 81 IDs today. Two ways of counting give the wrong answer, and both
have been used here before:

- Section M is an **output-format example**, not requirements. Its sample table
  repeats `ADS-ELIG-01` and `ADS-ELIG-02` as rows, so counting `| ADS-` rows over
  the whole file returns 83. The `exit` above stops before section M.
- Three IDs have three parts — `ADS-CONTENT-ORIGINAL`, `ADS-CONTENT-ADDED-VALUE`,
  `ADS-CONTENT-OVERLAP`. A regex like `ADS-[A-Z]+-[0-9]+` does not match them and
  returns 78.

Recount rather than trusting 81: the number lives in the reference, and every
other file that names it is quoting.
