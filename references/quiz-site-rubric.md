# Quiz/Entertainment Site Quality Rubric

Use this rubric when auditing quiz, poll, interactive personality test, or entertainment-content websites.

## Why Quiz Sites Are High Risk for AdSense Rejection

Google flags quiz sites for:
- **Thin content**: quiz pages have only questions and a generic result with no added value
- **Mass-generated pages**: 100+ quizzes use the same template with minimal text variation
- **Low-value entertainment**: quizzes are not original, derivative of trending formats, or created only to display ads
- **Unoriginal result pages**: results are auto-generated or trivial without meaningful content
- **Misleading UX**: fake personality assessments or low-quality "Are You" quizzes

## Quiz Page Quality Checklist

For **each quiz** (or a representative sample of 5-10 quizzes), evaluate:

### Quiz Distinctiveness and Content (Blocker/High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Quiz uniqueness** | Quiz questions and topic are distinct; not a reused template with different category labels | Quiz is a generic template (e.g., "Guess the X", "Are you a Y") applied to 50+ topics with minimal variation |
| **Question originality** | 80%+ of questions are specific and unique to the quiz topic, not generic boilerplate | Questions are generic or copy-pasted across quizzes (e.g., "What's your name?" on every quiz) |
| **Question count and depth** | Quiz has 10+ substantive questions that require thought, not just trivial picks | Quiz is 3-5 yes/no or single-click questions; too short to provide value |
| **Result pages unique** | Each result page has unique, meaningful text (200+ words) explaining the result and its context | Result pages are auto-generated (e.g., "You got Result X" with no explanation) or copy-pasted |
| **Result page value** | Results are interesting and add value beyond entertainment; if personality test, results include insights or advice | Results are trivial (e.g., "You are Pizza") with no substance |
| **Intro/description** | Quiz opening page explains what the quiz is, what result pages might reveal, and why it's worth taking | No intro or intro is just "Take this quiz" |

### Site-Level Content and Structure (High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Homepage value** | Homepage has original content, category descriptions, or curated quiz selections explaining the site | Homepage is just a list of quiz links with no editorial content |
| **Quiz categories** | Quizzes are organized into meaningful categories (e.g., "Personality", "Knowledge", "Entertainment") with category pages that describe the category | No categories; quizzes are listed randomly or by ID |
| **Category page content** | Category pages have 200+ words of original text explaining what kind of quizzes are in that category | Category pages are empty or auto-generated titles only |
| **Editorial/static content** | Site includes at least one editorial page (blog post, guide, FAQ, "best quizzes") showing non-quiz content | Site is 100% quiz interface; no static editorial content |

### Ad Placement and UX (High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Above-the-fold ratio** | Quiz intro and first question visible above the fold; ads are 30% or less of above-the-fold | Ads dominate above-the-fold; first question pushed below the fold |
| **Quiz flow unobstructed** | Progress through quiz is smooth; no ads between questions (or max 1 non-intrusive ad per 5 questions) | Ads between every question or intrusive pop-ups during quiz |
| **Result ad density** | Result page is 60% content, 40% ads max | Result page is mostly ads with minimal text |
| **Ad labels** | All ads clearly labeled "Sponsored", "Ads", or "Advertisement" | Ads unlabeled or designed to look like quiz content |

### Crawlability and SEO (High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Result page canonical** | Result pages have stable canonical tags pointing to a template URL (e.g., `/quiz/results/quiz-slug?result_id=X`) | Result URLs are completely unique per-user (session ID, timestamp); not reliably indexable |
| **Meta tags** | Quiz and result pages have descriptive meta titles and descriptions | Missing meta or generic meta ("Quiz Results") |
| **Robots.txt and indexing** | Robots.txt allows Googlebot to crawl quiz and result pages | Robots.txt blocks quiz pages or result pages from indexing |

### Uniqueness Analysis (Blocker/High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Template text overlap** | Across 10 random quizzes, no more than 30% of body text is shared boilerplate | 50%+ text overlap across different quizzes indicates heavy reuse |
| **Result page overlap** | Result pages have <40% text overlap (excluding quiz intro/metadata); mostly unique content per result | Result pages are mostly auto-generated or copy-pasted |
| **Category distinctiveness** | Categories are meaningfully different (e.g., "Movies" vs. "Books" vs. "TV"); not variations of the same category | 50+ categories that are near-duplicates of each other |

## Red Flags for Quiz Sites

- ❌ 100+ quizzes created in the last 3 months with identical boilerplate
- ❌ Quiz titles are auto-generated from a template (e.g., "Are You a [Noun]?", "Guess the [Category]")
- ❌ Result pages are auto-generated with no unique text (e.g., "Result: You got [result_name]")
- ❌ Homepage has no original content; only a list of quiz links
- ❌ More than 50% text overlap across quizzes in different categories
- ❌ Quizzes have no introduction or context; users don't know what to expect
- ❌ Results are trivial and provide no real value (e.g., "You are red" with no explanation)

## Mapping to AdSense Requirements

| AdSense Requirement | How Quiz Rubric Applies |
|---|---|
| ADS-CONTENT-01 | Check "Quiz uniqueness", "Result page value", "Homepage value" |
| ADS-CONTENT-02 | Check "Question originality", "Result pages unique", and text-overlap analysis |
| ADS-CONTENT-03 | Check "Above-the-fold ratio" and ensure substantial content, not just UI |
| ADS-CONTENT-04 | Check for under-construction quizzes or empty categories |
| ADS-CONTENT-08 | Check "Template text overlap" and "Category distinctiveness" for doorway/thin patterns |
| ADS-UX-02 | Check site has clear navigation and category structure |
| ADS-CRAWL-05 | Check "Result page canonical" and URL stability |

## Sample Audit Output for Quiz Sites

### Pass Example

**Quiz Site: Personality Quizzes**

✅ **ADS-CONTENT-01** (Distinctiveness): Audited 8 quizzes across 4 categories (personality, career, hobby, entertainment). Each quiz has 12-18 unique questions. No quiz is a reused template.

✅ **ADS-CONTENT-02** (Originality): Text overlap across different quizzes averages 18% (metadata/navigation only). Each result page has 200-300 words of unique, original analysis.

✅ **ADS-CONTENT-03** (Substance): Result pages explain the result, provide insights, and suggest related quizzes. Minimum 200 words per result.

✅ **ADS-UX-02** (Structure): Homepage has 400 words describing the site, 6 category pages with curated quiz lists and descriptions, and a "How to interpret results" guide.

### Fail Example

**Quiz Site: Mass-Generated Quizzes**

❌ **ADS-CONTENT-01**: 127 quizzes created in 6 months. Template analysis shows 80 quizzes use identical question structure: "Pick a/an [noun]", "Choose a [color]", "Select a [animal]". Titles are auto-generated: "Are You a [Word]?".

❌ **ADS-CONTENT-02**: Text overlap across categories averages 62%. Result pages are template-generated: "You are [Result_Name]. [Auto-generated description from result metadata]". No unique writing.

❌ **ADS-CONTENT-08**: Homepage is just a grid of quiz links. No category descriptions, no editorial content, no "why take this quiz" messaging.

❌ **ADS-CRAWL-05**: Result URLs are unique per user (`/quiz/result?quiz_id=123&user_id=456&timestamp=789`). No canonical tag. Not reliably crawlable.

## Recommendation for Quiz Sites

**For funsona-style sites**: 
1. Audit a sample of 10 quizzes across your main categories using this rubric.
2. Calculate template text overlap using `scripts/check_duplicates.py`.
3. If 70%+ of quizzes pass the rubric and overlap is <40%, mark ADS-CONTENT-01, ADS-CONTENT-02 as Pass with evidence.
4. If <50% pass, flag ADS-CONTENT-01 as Fail and recommend adding unique result pages (200+ words) and question originality review.
5. Always include homepage/category page analysis in ADS-UX-02 evaluation.
