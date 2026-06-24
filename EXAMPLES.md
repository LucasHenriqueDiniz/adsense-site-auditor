# AdSense Site Auditor — Real-World Examples

Complete, step-by-step examples for auditing smallwebapps.com and funsona.com.

---

## Example 1: Tool Site Pre-Application Audit (smallwebapps.com)

### Scenario

You've built smallwebapps.com with 50 utility tools (converters, calculators, generators). You want to apply for AdSense but aren't sure if your pages meet quality standards.

### Step 1: Run Helper Scripts

```bash
# From the adsense-site-auditor directory:

# Crawl the site and list all tools
python scripts/crawl_site.py https://smallwebapps.com --depth 2 --output crawl.json

# Analyze word count on all pages (target: 300+ words)
python scripts/analyze_text_depth.py crawl.json --min-words 300 --output depth_analysis.txt

# Check for boilerplate and template reuse
python scripts/check_duplicates.py crawl.json --threshold 0.8 --output duplicate_analysis.txt

# Verify technical requirements (robots.txt, sitemap, HTTPS, etc.)
python scripts/check_technical.py https://smallwebapps.com --output technical_check.txt
```

**Sample script output:**

```
# depth_analysis.txt
URL: https://smallwebapps.com/tools/hex-converter
  Words: 285 | Density: 45 words/KB
  Risk: BORDERLINE

URL: https://smallwebapps.com/tools/json-formatter
  Words: 420 | Density: 62 words/KB
  Risk: OK

⚠️  Risk: 15 pages may trigger ADS-CONTENT-03 or ADS-CONTENT-04

# duplicate_analysis.txt
Source: https://smallwebapps.com/tools/hex-converter
  ↔ 73% similar | https://smallwebapps.com/tools/decimal-converter
  ↔ 68% similar | https://smallwebapps.com/tools/binary-converter

⚠️  High duplication risk: 23 of 50 pages have >70% similar content
```

### Step 2: Invoke the Skill

```text
/adsense-site-auditor

URL: https://smallwebapps.com
Mode: Pre-application audit
Site type: Tool site

Analysis data:
- Total tools: 50 across 8 categories
- Crawl results: See crawl.json (sample: 48/50 pages accessible, status 200)
- Word count analysis: 35 pages OK (300+), 15 pages BORDERLINE (200-300)
- Duplicate analysis: 23 pages have 70%+ boilerplate text overlap with related tools
- Technical: robots.txt OK, sitemap.xml found (50 URLs), HTTPS OK, response time <2s

Key findings from manual review:
- Hex-to-decimal converter page: Title="Hex Converter", 85 words, no original guidance
- Unit converter: 420 words with good examples, links to related converters
- Most tools lack "Why would you use this?" section

Request: Full ADS-* checklist with priorities. Assume I'll need to add original guidance to 15-20 thin pages.
```

### Step 3: Skill Output (Excerpt)

```markdown
# AdSense Audit: smallwebapps.com

**Decision**: Ready after fixes

**Blockers**: None

**High Risks**

- **ADS-CONTENT-03**: 15 tool pages are under target depth (200-300 words vs. 300+ recommended)
  
  Evidence: 
  - `/tools/hex-converter` (85 words) — "Convert hex values online"
  - `/tools/time-zone-converter` (120 words) — only 4 sentences
  - Full list in analysis: `depth_analysis.txt`
  
  Fix:
  1. Review each page in `depth_analysis.txt` with status "BORDERLINE" or "THIN"
  2. For each tool, add 200-300 words of original guidance covering:
     - Why would someone use this tool?
     - How to use it (step-by-step)
     - Common use cases (e.g., "Programmers use this to debug color codes")
     - Limitations or edge cases
  3. Re-run `analyze_text_depth.py` to verify all pages are 300+ words
  
  Effort: 2-3 hours (15 pages × 10-15 min each)

- **ADS-CONTENT-02**: 23 tool pages have 70%+ template boilerplate text
  
  Evidence:
  - `/tools/hex-converter` (73% similar to `/tools/decimal-converter`)
  - `/tools/binary-converter` (68% similar to `/tools/hex-converter`)
  - Template reuse across converters
  
  Fix:
  1. Review `templates/[category].html` to identify shared boilerplate
  2. Make category-specific boilerplate: generic intro for "Number Converters", unique description for each tool
  3. Example: Currently all converters have "This tool converts format A to format B." Make this specific: "Hex-to-Decimal converts hexadecimal notation used in programming to standard decimal numbers."
  4. Run `check_duplicates.py --threshold 0.8` again to verify overlap <40%
  
  Effort: 1-2 hours (refactor template + customize 50 tool descriptions)

**Medium Risks**

- **ADS-UX-02**: Internal navigation between related tools is unclear
  
  Evidence: Homepage lists tools alphabetically; no "Related tools" links on detail pages
  
  Fix:
  - Add "See also" section linking to 2-3 related tools
  - Group tools by category on homepage
  
  Effort: 1 hour

**Exhaustive Checklist** (73 items)

| ID | Status | Evidence | Next Action |
| --- | --- | --- | --- |
| ADS-ELIG-01 | Pass | Owner confirmed eligible | Ready |
| ADS-ELIG-02 | Unknown | Owner has existing AdSense? | Ask owner |
| ADS-ELIG-03 | Fail | See Blockers/High/Medium above | Implement fixes |
| ... | ... | ... | ... |

**Summary**

Your site is **Ready after fixes**. Estimated effort: 4-6 hours.

**Priority fix order**:
1. Expand thin tool pages to 300+ words (2-3h) → High impact
2. Reduce template boilerplate (1-2h) → High impact
3. Add internal tool linking (1h) → Medium impact

After fixes, re-audit with:
```
/adsense-site-auditor https://smallwebapps.com
Mode: Post-fix verification
Fixed items:
- Expanded 15 thin pages to 300+ words
- Customized tool descriptions to reduce boilerplate
- Added internal tool linking
```
```

### Step 4: Fix & Re-Audit

After implementing fixes:

```bash
# Verify fixes worked
python scripts/analyze_text_depth.py crawl.json --min-words 300 --output depth_analysis_v2.txt
python scripts/check_duplicates.py crawl.json --threshold 0.8 --output duplicate_analysis_v2.txt
```

Then:

```text
/adsense-site-auditor

URL: https://smallwebapps.com
Mode: Post-fix verification

Fixed items:
- Expanded all 15 thin pages to 300+ words with original use-case guidance
- Customized tool descriptions; boilerplate overlap reduced to 22%
- Added "Related Tools" sections on all detail pages

Verification: All pages now show "OK" in depth_analysis_v2.txt; duplicate overlap 22% (down from 70%)
```

---

## Example 2: Quiz Site Post-Rejection Diagnosis (funsona.com)

### Scenario

funsona.com had 200 quizzes approved for AdSense, but Google suspended the account citing "Low-value content" and "Mass-generated pages."

### Step 1: Gather Data & Rejection Context

```bash
python scripts/crawl_site.py https://funsona.com --depth 1 --output funsona_crawl.json
python scripts/analyze_text_depth.py funsona_crawl.json --min-words 200 --output funsona_depth.txt
python scripts/check_duplicates.py funsona_crawl.json --threshold 0.75 --output funsona_dupes.txt
```

### Step 2: Invoke for Post-Rejection Diagnosis

```text
/adsense-site-auditor

URL: https://funsona.com
Mode: Post-rejection diagnosis

Rejection message:
"Your site has been suspended due to low-value content and mass-generated pages. 
Content must provide substantial value, not just serve as ad inventory. 
Review our policies on thin content and auto-generated material."

Site context:
- 200 quizzes across 10 categories
- Quizzes launched in last 6 months (rapid growth)
- Many quizzes use template structure

Analysis data:
- Text depth: 85 result pages under 200 words; 40 with just "You got Result X" (5 words)
- Duplicates: 82% text overlap across quizzes in same category
- Sample: quiz_1 and quiz_2 both have identical intro ("Take this fun quiz to find out!") and result template

Request:
1. Map rejection message to specific ADS-* requirements
2. Identify root causes
3. Priority fix plan
4. Verification checklist
```

### Step 3: Skill Output (Excerpt)

```markdown
# AdSense Post-Rejection Diagnosis: funsona.com

**Root Cause Analysis**

The suspension message "low-value content" and "mass-generated" maps to:

1. **ADS-CONTENT-01 (Blocker)**: Site must have useful, original, visitor-relevant content
   - Finding: 40 result pages are auto-generated with minimal text ("You got: [Result_Name]")
   - Example: Quiz "Are You a Pizza?" result page = 5 words + ads
   - This is a **hard blocker** — auto-generated results are explicitly prohibited

2. **ADS-CONTENT-02 (Blocker)**: Do not rely on auto-generated or copied content without original value
   - Finding: 82% text overlap across quizzes in same category indicates template reuse
   - Example: All "Personality" quizzes share identical intro and result template structure
   - This is a **hard blocker** — mass-generated content without original analysis

3. **ADS-CONTENT-08 (High)**: Content should not use doorway pages or pages made mainly for search engines
   - Finding: Quiz titles are auto-generated ("Are You a [Word]?" applied to 50+ topics)
   - Evidence: No meaningful differentiation between quizzes; pure template variations

**Why This Triggers Suspension**

Google sees: Rapid launch (200 quizzes in 6 months) + template reuse (82% overlap) + thin results (5 words) = **Clearly an ad farm, not genuine content**.

To fix:

1. **Rewrite result pages** (Critical)
   - Current: "You got: Pizza Lover" (5 words)
   - Required: 200+ words of original analysis, e.g.:
     ```
     "Pizza Lover: You appreciate quality food and social gatherings. 
     Pizza lovers are often outgoing and enjoy sharing meals with friends. 
     Your perfect weekend? Trying a new pizzeria with a group of friends. 
     [Additional personality insights, tips, related quizzes...]"
     ```

2. **Reduce template boilerplate** (Critical)
   - Current overlap: 82% across category
   - Target: <40% (only structural template shared, not content)
   - Action: Customize intro, questions, and results per quiz

3. **Slow down content production** (For future)
   - Current: 200 quizzes in 6 months = 1/day
   - After approval: max 2-3 quizzes/week, with editorial review

**Detailed Fixes**

| Requirement | Issue | Fix | Effort | Verification |
| --- | --- | --- | --- | --- |
| ADS-CONTENT-01 | 40 auto-generated result pages | Rewrite 200+ words per result with original analysis | 12-16h | All results 200+words; no "You got X" filler |
| ADS-CONTENT-02 | 82% template overlap across quizzes | Customize intro/questions/results; target <40% overlap | 8-10h | check_duplicates.py shows <40% |
| ADS-CONTENT-08 | Doorway-like auto-generated titles | Review quiz titles; ensure each is unique and meaningful | 2h | Each quiz title describes actual content, not template |
| ADS-UX-02 | No editorial/category pages | Add category pages with 300+ words describing quiz types | 3h | Each category has unique description and featured quiz |

**Timeline to Resubmit**

1. Week 1: Rewrite result pages (40-50 high-priority)
2. Week 2: Customize quiz intro/questions
3. Week 3: Add category pages and editorial content
4. Week 4: Re-audit and resubmit

**Resubmission Checklist**

Before asking Google to review:

- [ ] All result pages are 200+words with original analysis (not "You got X")
- [ ] Text overlap across quizzes in same category is <40%
- [ ] Homepage and category pages have 300+word editorial content
- [ ] Each quiz title and questions are unique (not auto-generated)
- [ ] No new quizzes during the fix period (pause launches)

**Re-Audit Command** (after fixes)

```
/adsense-site-auditor https://funsona.com
Mode: Post-fix verification

Fixed items:
- Rewrote 200 result pages (200+ words each) with original personality insights
- Customized quiz descriptions; boilerplate overlap <40%
- Added 10 category pages with editorial content
- Reviewed all quiz titles for uniqueness

Results: verify with check_duplicates.py and depth analysis
```
```

---

## Example 3: Repo + Live URL Audit (Vite + Cloudflare Pages)

### Scenario

You have a React/Vite tool site deployed on Cloudflare Pages. Want to audit both the source code and live rendering.

### Step 1: Run Scripts

```bash
# Crawl live site
python scripts/crawl_site.py https://tools.example.com --depth 2 --output crawl_live.json

# Check technical setup
python scripts/check_technical.py https://tools.example.com --output tech_live.txt

# Analyze live pages for thin content
python scripts/analyze_text_depth.py crawl_live.json --min-words 300 --output depth_live.txt
```

### Step 2: Inspect Local Repo

```bash
# Get repo info (assuming you have the repo checked out locally)
find /path/to/repo -name "*.tsx" -o -name "*.jsx" | head -20
grep -r "generatedPages\|Template\|boilerplate" /path/to/repo --include="*.tsx"
```

### Step 3: Invoke Skill

```text
/adsense-site-auditor

Site URL: https://tools.example.com
Repo path: /Users/me/code/tools-site

Mode: Repo + live URL audit
Site type: Tool site

Source code findings:
- Main tool pages generated from route pattern: src/pages/tools/[toolSlug].tsx
- Template reuse: 95% of tool pages use identical React component structure
- Content source: Tool metadata from JSON config; description is 2-3 sentences, not expandable

Live rendering findings:
- 48/50 tools crawlable
- 25 tools under 200 words
- Technical: HTTPS OK, sitemap.xml OK, robots.txt OK

Request:
1. Identify where boilerplate is coming from (source code level)
2. What's the SEO risk of generated routes?
3. How to add original content per tool without duplicating code?
4. Feasibility of canonical URLs for generated pages?
```

### Step 4: Skill Output (Excerpt)

```markdown
# Repo + Live URL Audit: tools.example.com

**Structure Analysis**

Source:
```
src/pages/tools/[toolSlug].tsx — renders all 50 tools

// Current:
export default function ToolPage({ slug }) {
  const tool = toolConfig[slug];
  return <div>
    <h1>{tool.name}</h1>
    <p>{tool.shortDesc}</p>  {/* 2-3 sentences from JSON */}
    <ToolComponent slug={slug} />
    </div>
}

// toolConfig.json: { "hex-converter": { "name": "Hex Converter", "shortDesc": "Convert hex to decimal" } }
```

Live rendering:
- URL: `/tools/hex-converter`
- Title: "Hex Converter"
- Content: 85 words (2-3 from JSON + tool UI)
- Boilerplate: ~75 words (header, footer, nav — same on all pages)

**Findings**

- **ADS-CONTENT-03 (High)**: Main content on generated pages is minimal
  - Root cause: Tool description in JSON is intentionally short
  - Fix: Expand toolConfig to include `fullDescription` (500+ words)

- **ADS-CONTENT-02 (High)**: 50 generated routes create duplication risk
  - Issue: toolConfig pattern means any changes apply to all pages
  - Risk: If one page violates policy, all 50 similar pages may be flagged
  - Recommendation: Customize each tool's page with unique guidance

- **ADS-CRAWL-05 (Medium)**: Dynamic routes need stable canonicals
  - Check: Do generated pages set canonical?
  - Recommendation: Add `<link rel="canonical" href={`/tools/${slug}`} />`

**Actionable Changes**

1. **Update toolConfig schema**:
```json
{
  "hex-converter": {
    "name": "Hex Converter",
    "shortDesc": "Convert hex to decimal",
    "fullDescription": "Converting hexadecimal to decimal is essential for programmers working with color codes, memory addresses, and low-level data representations. This tool simplifies the process... [300+ original words]",
    "useCases": ["Debugging", "Color codes", "Unicode"],
    "examples": [...]
  }
}
```

2. **Update component**:
```tsx
export default function ToolPage({ slug }) {
  const tool = toolConfig[slug];
  return <article>
    <h1>{tool.name}</h1>
    <p>{tool.shortDesc}</p>
    <section className="description">
      {tool.fullDescription}  {/* 300+ words */}
    </section>
    <section className="use-cases">
      <h2>When to use this</h2>
      {tool.useCases.map(uc => <p>{uc}</p>)}
    </section>
    <ToolComponent slug={slug} />
    </article>
}
```

3. **Add canonical (if not already present)**:
```tsx
<link rel="canonical" href={`https://tools.example.com/tools/${slug}`} />
```

4. **Verify re-renders**:
- Expected: All 50 pages now 400-500 words each
- No content duplication between routes (each fullDescription is unique)
- Canonical tags consistent

**Re-Audit After Changes**

```
/adsense-site-auditor https://tools.example.com
Mode: Post-fix verification

Changes:
- Added fullDescription (300+ words) to all 50 tools in toolConfig
- Each description is original and tool-specific
- Added canonical links to all generated pages

Verification: Re-run depth analysis to confirm all pages 300+words
```
```

---

## Example 4: Task Generation (Convert Audit to Todoist)

After any audit, convert findings to tasks:

```text
/adsense-site-auditor

URL: https://smallwebapps.com
Mode: Task generation
Output format: Todoist

[Provide previous audit findings]
```

**Output:**

```markdown
# AdSense Audit Tasks: smallwebapps.com

## Blocker Tasks (p1 — must complete before applying)

### BLOCKER-01: ADS-CONTENT-03 — Expand thin tool pages
**Project**: @smallwebapps  
**Priority**: p1  
**Effort**: 3h  
**Tags**: @adsense @blocker

- [ ] Review depth_analysis.txt; identify 15 pages under 300 words
- [ ] For each tool, add original guidance section (why use it, how, examples)
- [ ] Target: 300+ words per page
- [ ] Verification: Re-run `analyze_text_depth.py`; all pages show "OK"

---

### BLOCKER-02: ADS-CONTENT-02 — Reduce boilerplate in tool descriptions
**Priority**: p1  
**Effort**: 2h  
**Tags**: @adsense @blocker

- [ ] Identify template file: `templates/tool-page.html`
- [ ] Move shared boilerplate to single template
- [ ] Customize each tool description (currently "Convert X to Y")
- [ ] Verification: `check_duplicates.py` shows <40% overlap

---

## High Tasks (p2)

### HIGH-01: ADS-UX-02 — Add internal tool navigation
... [etc.]
```

---

## Key Takeaways

1. **Run scripts first** — Gather concrete data before auditing
2. **Use site-specific rubrics** — Tool and quiz sites have different requirements
3. **Be specific** — Always cite URLs, word counts, and evidence
4. **Fix in priority order** — Blockers → High → Medium
5. **Re-audit after fixes** — Verify improvements and watch for regressions
