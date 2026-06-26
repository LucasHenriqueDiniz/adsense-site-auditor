# AdSense Skill Accuracy Testing

Goal: Validate that the skill correctly predicts AdSense approval/rejection with 75-80% accuracy.

---

## Test Cases

### Part A: Sites Rejected by Google (Should Be Flagged as "Not Ready")

These are actual patterns from AdSense community reports and official rejections.

#### Rejected Case #1: Unfinished Site
**URL**: (synthetic test case)
**Issue**: Site under construction

- About page: Missing (404)
- Contact page: "Contact form will be added here" (placeholder)
- Tools: "CSV Cleaner (coming soon)" / "AI Image Checker (not yet)"
- Footer: "© 2026 Small Web Apps" (no author name)
- Guides: 1 guide only (too few)

**Expected Skill Output**: 
- ADS-COMPLETE-01: **Blocker** (4+ fails: missing About, placeholder Contact, Coming Soon tools, no author)
- ADS-AUTHOR-01: **Blocker** (anonymous footer)
- Decision: **Not ready — site appears under construction**

**Actual Result** (after audit):
- ☐ Flagged correctly as Blocker
- ☐ Specific issues identified
- ☐ Actionable fixes suggested

---

#### Rejected Case #2: Anonymous Publisher
**URL**: (synthetic test case)
**Issue**: No verifiable identity

- About page: Missing entirely
- Contact: Only generic contact form, no email
- Author: No name, no bio, no credentials visible
- Social links: None

**Expected Skill Output**:
- ADS-AUTHOR-01: **Blocker** (no verifiable identity)
- Decision: **Not ready — publisher identity cannot be verified**

**Actual Result**:
- ☐ Flagged correctly as Blocker
- ☐ Specific issues identified

---

#### Rejected Case #3: Thin Content + Mass-Generated
**URL**: (synthetic test case)
**Issue**: 50 similar tool pages with <200 words each

- Word count: 85-120 words per tool page
- Template overlap: 82% across pages
- Guides: 0 published guides
- Unique value prop: None (generic converters)

**Expected Skill Output**:
- ADS-COMPLETE-02: **High Risk** (0 guides)
- ADS-CONTENT-03: **High Risk** (pages <300 words)
- ADS-CONTENT-ORIGINAL: **High Risk** (no unique value prop)

**Actual Result**:
- ☐ Flagged correctly as High/Blocker
- ☐ Specific page examples cited

---

#### Rejected Case #4: Scraped Content
**URL**: (synthetic test case)
**Issue**: Pages are copy-pasted from competitors

- Text analysis: 90%+ overlap with top SERP results
- No original data or examples
- No added value

**Expected Skill Output**:
- ADS-CONTENT-OVERLAP: **High Risk** (>80% overlap)
- ADS-CONTENT-02: **Blocker** (plagiarized)

**Actual Result**:
- ☐ Detected high overlap
- ☐ Flagged as plagiarism risk

---

#### Rejected Case #5: Ad-Heavy Design
**URL**: (synthetic test case)
**Issue**: Ads dominate above-the-fold

- Above-fold: 4 ad slots, 100 words content (ad density 80%)
- Content: Pushed below fold
- CTAs: Aggressive ("Click here!", "Learn more!")

**Expected Skill Output**:
- ADS-CONTENT-AD-DENSITY: **High Risk** (ads >50% above fold)
- ADS-CONTENT-05: **High Risk** (ads dominate)

**Actual Result**:
- ☐ Detected excessive ads
- ☐ Flagged ad density

---

#### Rejected Case #6: Incomplete Privacy Policy
**URL**: (synthetic test case)
**Issue**: Privacy policy missing or vague

- Privacy page missing
- OR: Privacy mentions cookies but not Google-specific
- OR: No opt-out links

**Expected Skill Output**:
- ADS-PRIV-01 or ADS-PRIV-02: **Blocker/High** (incomplete disclosure)

**Actual Result**:
- ☐ Detected missing privacy details
- ☐ Flagged as non-compliant

---

#### Rejected Case #7: Deceptive Navigation
**URL**: (synthetic test case)
**Issue**: Fake buttons, misleading links

- "Download" button actually redirects to ads
- "Menu" links go to external sites
- Misleading CTAs

**Expected Skill Output**:
- ADS-UX-03: **Blocker** (deceptive navigation)

**Actual Result**:
- ☐ Detected deceptive UX
- ☐ Flagged as policy violation

---

#### Rejected Case #8: Crawler Access Issues
**URL**: (synthetic test case)
**Issue**: Blocked by robots.txt or login wall

- robots.txt: `Disallow: /` (blocks all crawlers)
- OR: Pages require login
- OR: WAF blocks automated requests

**Expected Skill Output**:
- ADS-CRAWL-02: **Blocker** (crawler blocked)

**Actual Result**:
- ☐ Detected crawler block
- ☐ Flagged as inaccessible

---

#### Rejected Case #9: Low-Authority Site
**URL**: (synthetic test case)
**Issue**: No trust signals, brand new domain

- Domain age: <1 month
- No social links, no about
- No contact, no credibility
- Content: Generic advice

**Expected Skill Output**:
- ADS-AUTHOR-01: **High Risk** (low trust)
- ADS-CONTENT-ORIGINAL: **High Risk** (generic)

**Actual Result**:
- ☐ Detected new domain + weak signals
- ☐ Flagged as low-trust

---

#### Rejected Case #10: Prohibited Content
**URL**: (synthetic test case)
**Issue**: Content violates policies

- Adult content
- OR: Instructions for hacking
- OR: Hate speech
- OR: Dangerous products

**Expected Skill Output**:
- ADS-PUB-*: **Blocker** (policy violation)

**Actual Result**:
- ☐ Detected prohibited content
- ☐ Flagged as unmonetizable

---

### Part B: Sites Approved by Google (Should Pass Audit)

#### Approved Case #1: Tech Blog (Well-Built)
**URL**: (example pattern)
**Signals**:
- About page: Detailed author bio with 10+ years experience
- Contact: Email + contact form visible
- Guides: 8 published articles (1500+ words each)
- Content: Original analysis, unique data, no plagiarism
- Privacy: Comprehensive, mentions Google services
- Ad density: Reasonable (40/60 ratio)
- Trust: Author has Twitter (5k followers), LinkedIn (public)

**Expected Skill Output**:
- Decision: **Ready for application**
- All critical checks: Pass
- Minor flags: None or very few

**Actual Result**:
- ☐ Passed all pre-flight checks
- ☐ Full audit shows 0 Blockers
- ☐ Recommendation: Ready

---

#### Approved Case #2: Small Business Site
**URL**: (example pattern)
**Signals**:
- About page: Company info, team members, 5+ years in business
- Contact: Phone + email visible
- NAP: Consistent (Name, Address, Phone)
- Content: 5 published service pages (1200+ words each)
- Privacy: Standard, clear
- Trust: Business registration visible, Google My Business linked
- Pages: Complete (no Coming Soon)

**Expected Skill Output**:
- Decision: **Ready for application**
- All checks: Pass

**Actual Result**:
- ☐ Passed pre-flight
- ☐ 0 Blockers, minimal High risks
- ☐ Recommendation: Ready

---

#### Approved Case #3: Educational Site
**URL**: (example pattern)
**Signals**:
- About: Real instructor, credentials (university affiliation)
- Content: Original course guides, examples, case studies
- Guides: 10+ published lessons (2000+ words each)
- Uniqueness: Custom lessons, proprietary exercises
- Privacy: Clear data usage policy
- Navigation: Clean, complete

**Expected Skill Output**:
- Decision: **Ready for application**

**Actual Result**:
- ☐ Passed pre-flight
- ☐ Content depth verified
- ☐ Recommendation: Ready

---

#### Approved Case #4-10: Variants
(Pattern repeats: complete site, verifiable identity, 3-5+ guides, clear content value, no red flags)

---

## How to Run Tests

### Quick Test (Single Site)

```bash
# Run pre-flight check
python scripts/check_completeness.py https://rejected-case-1.example.com

# If pre-flight passes (no Blockers), run full audit
/adsense-site-auditor
URL: https://rejected-case-1.example.com
Mode: Pre-application audit

# Check if skill output matches expected (Blocker detected)
```

### Batch Test (All 20 Cases)

```bash
# Create test list
cat > test_urls.txt << EOF
https://rejected-1.example.com
https://rejected-2.example.com
...
https://approved-1.example.com
...
EOF

# Run script
python scripts/test_accuracy.py test_urls.txt --output accuracy_report.txt
```

### Expected Output Format

```
Test Results: AdSense Skill Accuracy
====================================

Rejected Cases (Should flag as NOT READY):
✓ Case #1: Correctly flagged as Blocker (unfinished site)
✓ Case #2: Correctly flagged as Blocker (no identity)
✗ Case #3: MISSED - should have detected thin content
...
Rejected accuracy: 8/10 (80%)

Approved Cases (Should pass audit):
✓ Case #1: Correctly passed all checks
✓ Case #2: Correctly passed all checks
...
Approved accuracy: 9/10 (90%)

Overall Accuracy: (8 + 9) / 20 = 85%

True Positives: 8 (correctly rejected)
True Negatives: 9 (correctly approved)
False Positives: 1 (incorrectly passed approved case)
False Negatives: 1 (incorrectly approved rejected case)

Target: 75-80% ✓ ACHIEVED
```

---

## Accuracy Metrics

### Definition

- **True Positive (TP)**: Skill flagged rejected site as "Not Ready" ✓
- **True Negative (TN)**: Skill passed approved site as "Ready" ✓
- **False Positive (FP)**: Skill passed rejected site as "Ready" ✗
- **False Negative (FN)**: Skill flagged approved site as "Not Ready" ✗

### Formulas

```
Accuracy = (TP + TN) / Total
Precision = TP / (TP + FP)  [of "Ready" verdicts, how many were actually approved]
Recall = TP / (TP + FN)     [of rejected sites, how many did we catch]

Target: Accuracy ≥ 75%, Recall ≥ 70%
(Better to be overly cautious than approve something Google will reject)
```

---

## Validation Checklist

After running all 20 test cases, check:

- [ ] Accuracy ≥ 75%
- [ ] Recall ≥ 70% (catches most rejected sites)
- [ ] All Blockers identified with specific evidence
- [ ] High risks explained with actionable fixes
- [ ] No false negatives on obvious rejections (cases 1, 2, 5, 7, 8)
- [ ] Approved cases pass without unnecessary flags

---

## How to Add Your Own Test Cases

1. Document the site (URL or synthetic case description)
2. Run scripts:
   ```bash
   python scripts/check_completeness.py URL
   python scripts/check_technical.py URL
   python scripts/analyze_text_depth.py URL
   ```
3. Note the skill's output
4. Add row to test results table
5. Update accuracy metrics

---

## Known Limitations

1. **Synthetic test cases** may not capture all edge cases (use real rejected sites if possible)
2. **Domain-specific content** (legal sites, medical sites) may need custom checks
3. **International sites** may have different compliance rules
4. **YouTube channels, Blogger blogs** have separate approval flows (not covered)

---

## Continuous Improvement

After each audit season (monthly/quarterly):
- Collect rejection feedback from users
- Test skill against new rejection patterns
- Update checks if patterns change
- Document false positives/negatives
- Propose skill improvements

---

**Last updated**: 2026-06-23  
**Accuracy target**: 75-80%  
**Target audience**: Sites ready for real AdSense application
