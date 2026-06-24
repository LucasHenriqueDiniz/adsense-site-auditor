# AdSense Audit Task Output Template

Use this template when the audit mode is "Task Generation" to convert findings into prioritized, actionable work items.

## Format

```markdown
# AdSense Audit: [SITE_NAME] — Task Breakdown

**Audit Date**: YYYY-MM-DD
**Site URL**: [URL]
**Audit Mode**: Pre-application | Post-rejection | Post-fix verification
**Overall Decision**: Not ready / Ready after fixes / Ready

---

## Blocker Tasks (Must Fix Before Application)

### BLOCKER-01: [Requirement ID] — [Issue Title]

**Requirement**: [Full requirement text from adsense-requirements.md]

**Evidence**: 
- [Specific finding with URL/page/screenshot]
- [Additional evidence]

**Files/Pages Affected**:
- [ ] `/path/or/page-name` — [specific issue]

**Fix Action**:
1. [Specific step 1]
2. [Specific step 2]

**Acceptance Criteria**:
- [ ] [Concrete verifiable check]
- [ ] [Concrete verifiable check]

**Priority**: Blocker — blocks application

**Effort Estimate**: X hours

---

[Repeat for each Blocker task]

## High Risk Tasks (Should Fix Before Application)

### HIGH-01: [Requirement ID] — [Issue Title]

[Same structure as Blocker above]

**Priority**: High — meaningful approval risk

---

[Repeat for each High task]

## Medium Tasks (Recommended Before Application)

### MEDIUM-01: [Requirement ID] — [Issue Title]

[Same structure]

**Priority**: Medium — quality/compliance improvement

---

[Repeat for each Medium task]

---

## Summary

| Priority | Count | Files | Est. Hours |
|----------|-------|-------|-----------|
| Blocker | X | [list] | X |
| High | X | [list] | X |
| Medium | X | [list] | X |

**Total Estimated Effort**: X hours

**Recommended Timeline**: [e.g., "1 week for Blockers, 2 weeks for High"]

---

## Verification Checklist

After completing fixes, re-audit against these items:

- [ ] All Blockers marked Pass
- [ ] All High risks marked Pass or explicitly accepted
- [ ] Medium tasks reviewed and timeline confirmed
- [ ] No new issues introduced

---

## Example: Completed Task

### BLOCKER-02: ADS-CONTENT-02 — Scraped Article Content Without Original Commentary

**Requirement**: "Do not rely on copied articles, embedded videos, syndicated content, or affiliate feeds without original commentary, curation, review, data, tools, or analysis."

**Evidence**:
- URL: `https://example.com/article-123`
- Screenshot: [article body is 95% copied from techcrunch.com; only 1 sentence added]
- Diff: [tool output showing >90% text match with source]

**Files/Pages Affected**:
- [ ] `/articles/ai-trends-2024` — 95% copied from TechCrunch article
- [ ] `/articles/web-dev-2024` — 88% copied from MDN docs
- [ ] `/articles/ux-patterns` — 82% copied from Nielsen Norman

**Fix Action**:
1. Review `/articles/ai-trends-2024` source material
2. Rewrite to include original analysis: company examples, contrasting viewpoints, proprietary data
3. Add at least 300 words of original commentary per article
4. Cite external sources with proper attribution
5. Run duplicate detection again to confirm <40% overlap

**Acceptance Criteria**:
- [ ] Text overlap with sources is <40%
- [ ] Article has 200+ words of original analysis before first external link
- [ ] Meta description reflects original angle, not source title
- [ ] On-page SEO shows original H1 (not source headline)

**Priority**: Blocker

**Effort Estimate**: 3 hours

---
```

## Task Output Mode Invocation

When the user requests task generation, use this template to convert audit findings into Todoist or GitHub issues.

### Example User Prompt

```text
/adsense-site-auditor
URL: https://example.com
Mode: Task Generation

Convert all Blockers and High findings into actionable tasks with file paths, acceptance criteria, and effort estimates. Format for Todoist.
```

### Todoist-Compatible Output

When converting to Todoist, format each task as:

```
[SITE] BLOCKER-01: [Title] (p1, 3h, @adsense)
• Requirement: ADS-CONTENT-02
• Evidence: [URL] — [specific issue]
• Fix: [step 1; step 2; step 3]
• Verify: [acceptance criteria]
```

**Priority Mapping**:
- Blocker → p1 (highest)
- High → p2
- Medium → p3

**Tags**:
- @adsense — AdSense audit task
- @[site-name] — site-specific
- @blocker, @high, @medium — severity

### GitHub Issues-Compatible Output

```markdown
## [SITE] BLOCKER-01: [Title]

**Requirement**: ADS-CONTENT-02

**Evidence**:
- [URL]
- [Finding]

**Fix**:
- [ ] Step 1
- [ ] Step 2

**Labels**: blocker, adsense, [site-name]
```

## Guidance

- **One task per finding**: Do not combine Blockers or High items into one task.
- **Specificity**: Include file paths, page URLs, and code locations.
- **Verifiability**: Each acceptance criterion must be checkable (not "improve navigation").
- **Effort honesty**: Estimate conservatively; include testing and re-audit time.
- **Prioritization**: Blockers first, then High, then Medium. Within priority tier, order by effort/risk ratio.
