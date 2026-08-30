# Testing and accuracy

## Current state: accuracy has never been measured

This skill has no measured accuracy. Nothing here reports one, and any number
you find elsewhere in the repo describing how often the skill is right should be
treated as removed rather than merely stale.

The previous version of this file presented a confusion matrix — 8 true
positives, 9 true negatives, 1 false positive, 1 false negative, "Overall
Accuracy 85%", "Target: 75-80% ✓ ACHIEVED" — directly above thirty unticked
checkboxes and ten cases whose URL field read `(synthetic test case)`. No run
had happened, against no real site. The arithmetic did not close either: those
four counts sum to 19 and were divided by 20.

That mattered more than an ordinary documentation error. A readiness audit whose
whole value is telling you whether to trust its verdict cannot fabricate the
number that says how much to trust it.

## What can be tested today

The two halves of this skill need completely different treatment, and conflating
them is what made the old claim possible.

### Deterministic checks — unit tests, running now

Anything decidable from the page or the HTTP response: robots.txt directives,
status codes, presence of ads.txt, word counts, template overlap, "coming soon"
strings, broken navigation links. A script either finds these or does not, and
the answer does not depend on judgement.

```bash
uv venv && uv pip install requests pytest ruff
.venv/bin/python -m pytest tests/ -q
```

These tests are the ground truth for the deterministic layer. Every fixed
parsing defect should arrive with the input that exposed it — `tests/test_robots.py`
carries the real robots.txt of a live site that the previous implementation
misreported, for exactly that reason.

### Judgement checks — not unit-testable, and not yet measured

"Useful, original content", "unique value proposition", "misleading
representation". No script settles these, and a model's opinion about them is
not ground truth. Their accuracy can only be measured against sites whose real
AdSense outcome is known.

## Measuring the judgement layer

`claude plugin eval` is the eventual mechanism: it runs versioned cases against
the skill and scores them, with a no-plugin baseline arm so the score delta shows
what the skill contributes rather than what the model already knew.

```bash
claude plugin eval --eval-dir evals --ablation with-without
```

It is gated behind early access and returns `plugin eval is currently in early
access` on accounts without it, so do not plan around it being available. The
fallback needs no special tooling and measures the same thing: run the audit
against each site in a list, record the verdict, and compare it to the outcome
Google actually gave. One row per site, `Ready` or `Not ready` against `approved`
or `rejected`, is enough to fill the table below.

Either way, what this repo does not yet have is **ground truth**: sites
whose AdSense application actually succeeded or actually failed, with the
rejection reason where one was given. Synthetic cases cannot supply this. A
scenario written to describe an unfinished site will always be caught by a check
written to find unfinished sites; that measures nothing but the author's
consistency.

There is now exactly one labelled outcome, recorded below, and the skill got
it wrong. One case is not a measurement, so this skill still describes itself
as unmeasured — but it is no longer describing itself as untested.

## The one real outcome on record

**smallwebapps.com — application rejected by Google.** Audited 2026-08-30 with
the checks in this repo. The pre-flight gate returned "every check observed its
condition and passed", so the skill would have advised submitting the
application that Google refused.

That is one false negative out of one labelled site. It is far too small a
sample to be an accuracy figure and is not offered as one — this section exists
because the honest count of real outcomes was zero before it, and is one now.

What the run showed, in full detail in `EXAMPLES.md`:

| | |
| --- | --- |
| Passed the gate on | About (742 words), Contact (820 words), 12 guides with 8 over 1200 words, no placeholders, 25 navigation links with none broken |
| Missed | 48 tool pages with a median of 220 words; 45 of them below 300 |
| Ruled out by measurement | duplication — 276 pairs compared, median similarity 0.134, maximum 0.327, none above the rubric's 0.40 safe band |

The gate reads the home page, the trust pages and the guide count. On a
catalogue site those are the best-written part and the catalogue is the site, so
the sample was pointed away from the problem. `SKILL.md` now carries a fourth
pre-flight item that samples the largest section instead.

The lesson generalises past this one site: a gate that inspects only the pages a
publisher wrote by hand will pass a site whose generated bulk is the reason for
rejection.

## Scenarios worth turning into cases

These came from the previous file. They are plausible descriptions of failure
modes and useful as a checklist of what to collect, but none of them is evidence:
none corresponds to a site that was submitted to Google.

**Expected to fail review**

1. Unfinished site — missing About, placeholder Contact, "coming soon" tools
2. Anonymous publisher — no name, no bio, no contact method
3. Thin, mass-generated pages — 50 tool pages under 200 words, 82% template overlap
4. Scraped content republished without commentary
5. Ad-heavy layout — paid placement dominating above the fold
6. Privacy policy that omits third-party cookies and ad personalisation
7. Deceptive navigation — fake download buttons, links to nothing
8. Crawler blocked — login wall, geoblock, or robots.txt exclusion
9. Low-authority site with no original data or analysis
10. Prohibited content under Google Publisher Policies

**Expected to pass review**

11. Well-built technical blog with named author and original writing
12. Small business site with real contact details and service pages
13. Educational site with substantive lessons

To make any of these a real case, replace the description with a URL and the
outcome Google actually returned.

## Metric definitions

Fix one convention and hold it: the **positive class is "site will be rejected
by Google"**, because that is the event the audit exists to predict.

| | Google rejected | Google approved |
|---|---|---|
| **Skill said Not ready** | True positive | False positive |
| **Skill said Ready** | False negative | True negative |

```
Accuracy  = (TP + TN) / total
Precision = TP / (TP + FP)   of the sites we called Not ready, how many really were
Recall    = TP / (TP + FN)   of the sites Google rejected, how many we caught
```

The previous file swapped the false-positive and false-negative definitions
against its own stated positive class, so its precision and recall formulas
computed neither quantity. Worth stating because the direction of the error has
a cost: a false negative here is a site we told someone to submit that Google
then rejected, which is the failure this skill is supposed to prevent.

Set targets only once there is a denominator to put them over.

## Known limitations

- No measured accuracy, as above.
- Google's review is partly manual and its criteria are not published in full.
  A perfect implementation of the documented rules still cannot guarantee a
  verdict, and this skill should never claim otherwise.
- Policies change. The reference snapshot is dated; live Google documentation
  wins over anything stored here.
- Hosted platforms (Blogger, YouTube) run separate approval flows not covered.
- International and sector-specific rules (medical, legal, finance) may add
  requirements beyond the general policy set.
