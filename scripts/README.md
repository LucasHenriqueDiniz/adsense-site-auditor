# AdSense Site Auditor — Helper Scripts

These Python scripts automate data collection for AdSense website audits. They work as standalone CLI tools or can be invoked from the Claude Code skill.

## Quick Start

All scripts require Python 3.7+ and `requests`:

```bash
pip install requests
```

## Scripts

### 1. crawl_site.py — Website Crawler

Crawls a website and collects URLs, titles, meta descriptions, and H1s.

**Usage:**
```bash
python crawl_site.py <URL> [--depth N] [--output FILE.json]
```

**Examples:**
```bash
python crawl_site.py https://example.com --depth 2
python crawl_site.py https://example.com --depth 1 --output crawl_report.json
```

**Output:**
- List of URLs with HTTP status, title, meta description, content length
- JSON file (if `--output`) containing crawl results for use by other scripts

**Useful for:**
- ADS-CRAWL-01: Verify site is live and accessible
- ADS-CRAWL-02: Check which pages are crawlable
- ADS-SITE-01: Get list of pages to audit

---

### 2. analyze_text_depth.py — Text Depth & Thin Content Detector

Analyzes pages for word count, text density, and thin-content risk.

**Usage:**
```bash
python analyze_text_depth.py <URL_or_FILE> [--min-words N] [--output FILE]
```

**Examples:**
```bash
# Analyze single URL
python analyze_text_depth.py https://example.com/article --min-words 300

# Analyze all URLs from crawl report
python analyze_text_depth.py crawl_report.json --min-words 500 --output depth_report.txt
```

**Output:**
- Risk classification: OK, BORDERLINE, THIN, ERROR
- Word count and text density for each page
- Summary of high-risk pages

**Useful for:**
- ADS-CONTENT-01: Verify pages have useful, original content
- ADS-CONTENT-03: Check if pages have substantial content (not just UI)
- ADS-CONTENT-04: Detect under-construction or empty pages

---

### 3. check_duplicates.py — Duplicate & Boilerplate Detector

Detects near-duplicate pages and high boilerplate reuse using text similarity.

**Usage:**
```bash
python check_duplicates.py <URL_or_FILE> [--threshold N] [--output FILE]
```

**Examples:**
```bash
# Check single URL for duplicates (loads similar pages automatically)
python check_duplicates.py https://example.com/quiz1 --threshold 0.8

# Analyze all URLs from crawl report
python check_duplicates.py crawl_report.json --threshold 0.75 --output dup_report.txt
```

**Output:**
- Groups of similar pages with similarity percentage
- Average similarity per page group
- Risk assessment (high duplication = flag)

**Useful for:**
- ADS-CONTENT-02: Detect copied or near-duplicate content
- ADS-CONTENT-08: Find template boilerplate reuse
- Quiz/entertainment sites: Identify auto-generated or templated quiz pages

---

### 4. check_technical.py — Technical Checks

Verifies robots.txt, sitemap.xml, redirects, security headers, HTTPS, DNS, and uptime.

**Usage:**
```bash
python check_technical.py <URL> [--output FILE]
```

**Examples:**
```bash
python check_technical.py https://example.com
python check_technical.py https://example.com --output technical_report.txt
```

**Output:**
- robots.txt: Is it blocking Googlebot or Mediapartners-Google?
- sitemap.xml: Does it exist and contain URLs?
- Redirects: Excessive redirect chains?
- Security headers: CSP, X-Frame-Options, HSTS
- HTTPS: Site uses secure connection?
- DNS/uptime: Can site be resolved and reached?

**Useful for:**
- ADS-CRAWL-01: Verify site is live
- ADS-CRAWL-02: Check robots.txt doesn't block crawlers
- ADS-CRAWL-06: DNS and hosting reliability
- ADS-CRAWL-07: Sitemap for crawlability

---

## Workflow Example

### Pre-Application Audit for smallwebapps.com

```bash
# Step 1: Crawl the site
python crawl_site.py https://smallwebapps.com --depth 2 --output crawl.json

# Step 2: Check technical requirements
python check_technical.py https://smallwebapps.com --output technical.txt

# Step 3: Analyze text depth on all pages
python analyze_text_depth.py crawl.json --min-words 300 --output depth.txt

# Step 4: Check for duplicate content
python check_duplicates.py crawl.json --threshold 0.8 --output duplicates.txt

# Now feed these reports to Claude Code skill:
# /adsense-site-auditor https://smallwebapps.com
# [Provide crawl.json, technical.txt, depth.txt, duplicates.txt as context]
```

---

## Integration with Claude Code Skill

When invoking the skill, you can reference script outputs:

```text
/adsense-site-auditor

URL: https://smallwebapps.com
Crawl report: [results from crawl_site.py]
Text depth analysis: [results from analyze_text_depth.py]
Duplicate analysis: [results from check_duplicates.py]
Technical checks: [results from check_technical.py]

Mode: Pre-application audit
Site type: Tool site
```

The skill will use this data to populate findings with concrete evidence for each ADS-* requirement.

---

## Notes

- **Timeout**: Default 10 seconds per request. Adjust with `--timeout` if needed (not currently exposed but can edit scripts).
- **Rate limiting**: Scripts use standard delays. For large crawls, consider adding `--delay` between requests.
- **Content extraction**: Text extraction ignores `<script>`, `<style>`, `<nav>`, `<footer>` to focus on main content.
- **Similarity threshold**: Default 0.8 (80%). Lower for strict duplication, higher for boilerplate tolerance.

---

### 5. check_completeness.py — Site Completeness & Publisher Identity

Detects "site unfinished" patterns and verifies publisher identity verification. Accounts for 25-40% of AdSense rejections.

**Usage:**
```bash
python check_completeness.py <URL> [--output FILE]
```

**Examples:**
```bash
python check_completeness.py https://example.com
python check_completeness.py https://example.com --output completeness_report.txt
```

**Output:**
- Site completeness score (About page, Contact page, placeholder text, navigation)
- Publisher identity verification (real name, contact method, credentials)
- Risk classification: OK, HIGH_RISK, FAIL

**Useful for:**
- ADS-COMPLETE-01: Detect "site unfinished" pattern
- ADS-COMPLETE-02: Verify 3+ published guides exist
- ADS-AUTHOR-01, ADS-AUTHOR-02: Verify publisher identity is real and contactable
- Pre-flight gate: Run this FIRST before 73-item full audit

**Key red flags detected:**
- About page missing or is stub
- Contact page has placeholder ("will be added here")
- Tool pages say "Coming Soon" or "Not yet"
- No real name in About (anonymous site)
- No contact method (email, form, or social)
- Homepage contains "under construction" or similar

---

## Workflow Example: Pre-Flight Audit

```bash
# Step 1: Run pre-flight checks FIRST
python check_completeness.py https://yoursite.com --output completeness.txt
python check_technical.py https://yoursite.com --output technical.txt

# If completeness shows HIGH_RISK or FAIL, stop here and fix before continuing

# Step 2: If pre-flight passes, run detailed checks
python crawl_site.py https://yoursite.com --depth 2 --output crawl.json
python analyze_text_depth.py crawl.json --min-words 300 --output depth.txt
python check_duplicates.py crawl.json --threshold 0.8 --output duplicates.txt

# Step 3: Feed all reports to Claude Code skill
/adsense-site-auditor
URL: https://yoursite.com
Mode: Pre-application audit
[Paste outputs from all scripts above]
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'requests'`**
```bash
pip install requests
```

**Scripts hang or timeout**
- Check network connectivity
- Site may be slow or blocking bots
- Reduce `--depth` for crawl_site.py
- Increase timeout (edit script)

**Empty or error results**
- Verify URL is correct and accessible
- Check if site requires authentication
- Site may block automated requests; add delays or adjust User-Agent

---

## License

Part of AdSense Site Auditor Skill. Use for AdSense audit purposes.
