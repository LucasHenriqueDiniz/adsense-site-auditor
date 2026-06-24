# Tool/App Site Quality Rubric

Use this rubric when auditing utility, calculator, converter, generator, or interactive-tool websites.

## Why Tool Sites Are High Risk for AdSense Rejection

Google flags tool sites for:
- **Thin content**: tool page is just the interactive component with no explanatory text
- **Low-value content**: tool is common (e.g., generic unit converter) with no differentiation
- **Ad-heavy design**: ads dominate the space around the tool
- **No original guidance**: tool documentation is copied or generic

## Tool Page Quality Checklist

For **each representative tool page**, evaluate:

### Content and Value (Blocker/High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Tool explanation** | Page clearly explains what the tool does in 1-2 sentences before or near the tool | No explanation; tool appears without context |
| **Minimum word count** | Page has 300+ words of indexable text (excluding tool UI, ads, navigation) | Page is <300 words or mostly tool UI |
| **Original guidance** | Page includes specific examples, use cases, tips, or limitations written in the site's voice | Guidance is copy-pasted from other sites or generic boilerplate |
| **User value without tool** | Page is useful to a reader *before* they interact with the tool (teaches the concept, explains why it matters) | Page has no value if the tool is hidden; pure tool interface |
| **Functionality independent** | Tool works without login, payment, or third-party dependencies for basic use | Tool requires sign-up, payment, or external account to function |
| **Link to related tools** | Page links to 1-3 related tools on the same site; internal linking shows a tool category or section | No internal links; tool is isolated with no category structure |
| **FAQ or help content** | Page includes a FAQ section or help docs specific to this tool | No FAQ; no help content; tool has no support documentation |

### Design and Ad Placement (High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Above-the-fold ratio** | Above-the-fold content is 70% page content, 30% ads/navigation max | Above-the-fold is mostly ads or navigation; tool/content pushed down |
| **Ad labels and placement** | Ads are clearly labeled "Ads" or "Sponsored"; not confused with tool or content | Ads are unlabeled or designed to look like content or tool controls |
| **Tool vs. ad proximity** | At least 2-3 lines of content separate the tool from any ad blocks | Ads immediately adjacent to or overlapping the tool interface |

### Crawlability and SEO (High)

| Check | Pass Criteria | Fail Signal |
|-------|---------------|-------------|
| **Canonical URL** | Page has a stable, simple canonical URL; results page (if applicable) uses a canonical to a template | Page URL includes session IDs, user tokens, or per-result unique IDs that make indexing unreliable |
| **Meta description** | Meta description is 100-160 chars and describes the tool's value, not just "Tool" | Missing meta; generic meta ("online calculator") |
| **H1 and title** | Page title and H1 are descriptive and relate to tool purpose, e.g., "Hex to Decimal Converter — Convert hex values online" | Title is generic ("Tool") or keyword-stuffed |

## Red Flags for Tool Sites

- ❌ 20+ tool pages with nearly identical structure and <200 words each
- ❌ Tool pages that redirect or require click-through to access tool
- ❌ Tool description is only auto-generated from metadata (e.g., "Tool: [tool-name]")
- ❌ Most tool pages are scraped tool descriptions from other sites
- ❌ No visible tool category, navigation, or homepage listing
- ❌ Tool results are unsaved/not indexable; every user sees a unique URL

## Mapping to AdSense Requirements

| AdSense Requirement | How Tool Rubric Applies |
|---|---|
| ADS-CONTENT-01 | Check "Tool explanation" + "Minimum word count" + "Original guidance" |
| ADS-CONTENT-02 | Check "Tool explanation" and ensure not copy-pasted |
| ADS-CONTENT-03 | Check "Minimum word count" and "User value without tool" |
| ADS-CONTENT-04 | Check page is live and functional; not under construction |
| ADS-UX-01, ADS-UX-02 | Check tool categories and internal linking |
| ADS-CRAWL-05 | Check "Canonical URL" for session/user ID leakage |

## Sample Audit Output for Tool Sites

### Pass Example

**Tool Page: Hex to Decimal Converter**

✅ **ADS-CONTENT-01**: Page opens with "Convert hexadecimal values to decimal and back. Useful for debugging color codes, Unicode values, and programming." (28 words)

✅ **ADS-CONTENT-02**: Includes 400 words explaining hex systems, use cases (web colors, character codes, memory addresses), and limitations ("valid for 0x0 to 0xFFFFFFFF")

✅ **ADS-CONTENT-03**: Main content is 420 words; ad block below the fold is ~80 words

❌ **ADS-CRAWL-05**: URL has canonical tag pointing to stable `/tools/hex-converter` (pass)

### Fail Example

**Tool Page: Generic Unit Converter**

❌ **ADS-CONTENT-01**: No explanation; page loads with a dropdown selector labeled "Select units" and no introductory text

❌ **ADS-CONTENT-03**: Page is 85 words (input labels, button text, ads); tool is 70% of above-the-fold

❌ **ADS-CONTENT-02**: "Converter description" is copy-pasted from Wikipedia

❌ **ADS-UX-01**: No navigation to related tools; no category structure visible

## Recommendation

**For smallwebapps-style sites**: Audit 5-10 representative tool pages across different categories. If 80%+ pass the rubric, mark ADS-CONTENT-01 and ADS-CONTENT-02 as Pass with evidence. If <50% pass, mark as Fail and recommend adding 200+ words of original guidance to each thin page.
