# AdSense Site Auditor Skill — Usage Guide

This skill audits websites for Google AdSense application readiness and ad-serving compliance.

## Quick Start

### Pre-Application Audit

```text
/adsense-site-auditor

URL: https://yoursite.com
Mode: Pre-application audit
Site type: Tool site (or: Quiz site, Blog, Directory, Hybrid)
```

The skill will check all 70+ AdSense requirements and output a readiness decision: **Not ready**, **Ready after fixes**, or **Ready**.

---

## Audit Modes

### 1. Pre-Application Audit

**Use when:** Site owner wants to know if ready to apply for AdSense.

```text
/adsense-site-auditor

URL: https://example.com
Mode: Pre-application audit

Requirements:
- Full exhaustive checklist (all ADS-* IDs)
- Every item marked Pass/Fail/Unknown/N/A with evidence
- Blocker/High/Medium severity priorities
```

**Expected output:**
- Executive decision: Not ready / Ready after fixes / Ready
- Blocker findings (must fix)
- High-risk findings (should fix)
- Medium findings (nice to fix)
- Complete checklist table with 70+ requirement IDs

---

### 2. Post-Rejection Diagnosis

**Use when:** Site was rejected from AdSense; you have the rejection reason.

```text
/adsense-site-auditor

URL: https://example.com
Mode: Post-rejection diagnosis

Rejection reason: [Paste the text from AdSense dashboard]

Requirements:
- Map rejection to ADS-* IDs
- Check all related requirements
- Prioritize fixes by impact
```

**Expected output:**
- Root cause analysis
- Related ADS-* IDs that may be affected
- Priority fix list (Blocker first)
- Verification checklist

---

### 3. Post-Fix Verification

**Use when:** You've fixed issues and want to confirm readiness to resubmit.

```text
/adsense-site-auditor

URL: https://example.com
Mode: Post-fix verification

Fixed items:
- Rewrote all article pages with original commentary
- Added privacy policy with cookie disclosures
- Verified robots.txt doesn't block AdSense crawler

Requirements:
- Re-audit all ADS-* items (not just fixed ones)
- Confirm no regressions
```

**Expected output:**
- Pass/Fail/Unknown for every item
- Confirmation that previous Blockers are now Pass
- New risks introduced (if any)
- Final readiness decision

---

### 4. Task Generation

**Use when:** You want to convert audit findings into actionable tasks.

```text
/adsense-site-auditor

URL: https://example.com
Mode: Task generation

Output format: Todoist | GitHub | Plain list

Requirements:
- One task per Blocker/High finding
- Include file path, specific issue, steps to fix
- Effort estimate (hours)
- Acceptance criteria (how to verify the fix)
```

**Expected output:**
- Prioritized task list (Blockers first)
- Each task includes URL, issue, steps, verification
- Effort estimates for project planning

---

### 5. Repo + Live URL Audit

**Use when:** Site is built with Vite/React/Next and deployed live. Auditor will check source code and rendered output.

```text
/adsense-site-auditor

Site URL: https://example.com
Repo path: /path/to/local/repo

Mode: Repo + live URL audit

Requirements:
- Inspect templates, routes, content sources (from repo)
- Crawl rendered pages (from live URL)
- Check for template boilerplate, auto-generated content
- Verify all ADS-* requirements
```

**Expected output:**
- Findings from both source and live rendering
- Specific file paths with issues (from repo)
- Specific URLs with issues (from live site)
- Content quality issues (duplicated templates, thin pages, etc.)

---

### 6. Health Check

**Use when:** Site is already approved by AdSense; you want to monitor for new risks.

```text
/adsense-site-auditor

URL: https://example.com
Mode: Health check

Requirements:
- Quick scan for changes that might trigger rejection
- Focus on content quality and policy compliance
- Flag anything new that risks ad suspension
```

**Expected output:**
- Summary of any new issues
- Risks introduced since last audit
- Actions recommended to maintain approval

---

## Site-Specific Guidance

### Tool Sites (e.g., smallwebapps.com)

Provide this context:

```text
/adsense-site-auditor

URL: https://smallwebapps.com
Site type: Tool site

Requirements:
- Audit 5-10 representative tools from different categories
- Check: tool explanation, word count, original guidance, value without tool
- Reference: references/tool-site-rubric.md
- Use: analyze_text_depth.py to check word counts
```

**Focus areas:**
- ADS-CONTENT-01: Each tool page has original explanation + 300+ words
- ADS-CONTENT-02: Content is not copied from other converters/generators
- ADS-CONTENT-03: Substantial content, not just tool UI
- ADS-UX-02: Tools are organized into clear categories with internal links

---

### Quiz Sites (e.g., funsona.com)

Provide this context:

```text
/adsense-site-auditor

URL: https://funsona.com
Site type: Quiz site

Requirements:
- Audit 10 representative quizzes across different categories
- Check: quiz originality, result page uniqueness, homepage value
- Reference: references/quiz-site-rubric.md
- Use: check_duplicates.py to measure template reuse

Template text overlap acceptable threshold: 30%
```

**Focus areas:**
- ADS-CONTENT-01: Quizzes are original, not templated; results have value
- ADS-CONTENT-02: Result pages are unique, not auto-generated
- ADS-CONTENT-08: Template reuse is <40%; no doorway patterns
- ADS-UX-02: Homepage and categories have editorial content

---

## Using Helper Scripts

Before invoking the skill, run these to gather data:

```bash
# Crawl the site
python scripts/crawl_site.py https://example.com --depth 2 --output crawl.json

# Check technical requirements
python scripts/check_technical.py https://example.com --output technical.txt

# Analyze text depth
python scripts/analyze_text_depth.py crawl.json --min-words 300 --output depth.txt

# Check for duplicates
python scripts/check_duplicates.py crawl.json --threshold 0.8 --output duplicates.txt
```

Then include the outputs in your skill invocation:

```text
/adsense-site-auditor

URL: https://example.com
Mode: Pre-application audit

[Include outputs from scripts above for concrete evidence]
```

---

## Common Rejection Reasons & What to Check

| Rejection Reason | Check These ADS-* IDs | Skill Mode |
|---|---|---|
| "Low-value content" | ADS-CONTENT-01, ADS-CONTENT-03, ADS-CONTENT-04 | Post-rejection diagnosis |
| "Insufficient original content" | ADS-CONTENT-02, ADS-CONTENT-08 | Post-rejection diagnosis |
| "Thin pages / thin sites" | ADS-CONTENT-03 with analyze_text_depth.py | Post-rejection diagnosis |
| "Crawler cannot access" | ADS-CRAWL-01, ADS-CRAWL-02 with check_technical.py | Post-rejection diagnosis |
| "Incomplete privacy policy" | ADS-PRIV-01, ADS-PRIV-02 | Pre-application audit |
| "Ad placement violations" | ADS-PROG-06, ADS-PUB-10 | Pre-application audit |
| "Policy violations (adult/weapons/etc.)" | ADS-PUB-01 through ADS-PUB-16 | Pre-application audit |

---

## Output Format

The skill produces an exhaustive report:

```markdown
# AdSense Audit Report: [SITE_NAME]

**Decision**: Not ready / Ready after fixes / Ready

**Blockers** (must fix before applying)
- ADS-CONTENT-01: [issue]. Evidence: [URL/quote]. Fix: [action].

**High Risks** (should fix before applying)
- ADS-CRAWL-02: [issue]. Evidence: [URL]. Fix: [action].

**Medium Findings** (recommend fixing)
- ADS-UX-05: [issue]. Evidence: [page]. Fix: [action].

**Exhaustive Checklist**
| ID | Status | Evidence | Next Action |
| --- | --- | --- | --- |
| ADS-ELIG-01 | Pass | Owner confirmed eligible | Ready to apply |
| ADS-CONTENT-02 | Fail | [URL] is 95% copied | Rewrite with original analysis |
| ... | ... | ... | ... |

**Completeness Check**
- Total ADS-* IDs in reference: 73
- IDs in report: 73
- Missing: none ✓
```

---

## Tips

1. **Be specific in invocation**: Mention site type, mode, and what you've already tried.
2. **Provide context**: If you have crawl reports or rejection reasons, include them.
3. **Use helper scripts first**: Run scripts to gather data before the audit.
4. **Read the rubrics**: For tool/quiz sites, review `tool-site-rubric.md` or `quiz-site-rubric.md` before auditing.
5. **Iterate**: First audit finds Blockers → fix → re-audit → finds new High risks → fix → ready to apply.

---

## Need Help?

- **How to use this skill?** → Ask: `/adsense-site-auditor --help` (or read this file)
- **What does a requirement mean?** → Check `references/adsense-requirements.md` for official Google source
- **Is my site a tool/quiz site?** → Review `references/tool-site-rubric.md` or `references/quiz-site-rubric.md`
- **How do I fix [finding]?** → Use Task Generation mode to convert audit results into actionable tasks
