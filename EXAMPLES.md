# Worked examples

Every command and every output below was run against the live site on the date
given. Nothing here is illustrative or reconstructed. The previous version of
this file was written by hand and contradicted itself — it showed
`check_duplicates.py --threshold 0.8` printing pairs at 73% and 68%, which that
script cannot do because it only prints pairs at or above the threshold, and it
concluded "Blockers: None" in one example while another converted the same audit
of the same site into "BLOCKER-01".

To regenerate: run the commands shown and paste what you get.

---

## Example 1 — smallwebapps.com, rejected by Google

**Outcome: the AdSense application for this site was rejected.** This is the only
example here with a known verdict from Google, which makes it the one worth
reading carefully: it is the single data point available on whether this skill
predicts the real decision.

Run on 2026-08-30.

### The pre-flight gate

```
$ python scripts/check_completeness.py https://smallwebapps.com

[PASS] ADS-COMPLETE-01 home page is finished
         - no unfinished markers found
[PASS] ADS-UX-05 about page
         - https://smallwebapps.com/about/ (742 words)
         - contact: https://github.com/LucasHenriqueDiniz/smallwebapps
[PASS] ADS-UX-05 contact page
         - https://smallwebapps.com/contact/ (820 words)
         - contact: lucas.hdo@hotmail.com, https://github.com/LucasHenriqueDiniz/smallwebapps/issues
[note] ADS-UX-01 navigation
         - 25 links followed, none broken

4 checks: PASS=3, note=1
Verdict: every check observed its condition and passed.
```

**The gate passes a site Google rejected.** That is a false negative, and it is
the failure mode this skill exists to prevent: it would have told the owner to
apply.

### Technical

```
$ python scripts/check_technical.py https://smallwebapps.com

[PASS] ADS-CRAWL-01 reachable          - HTTP 200 in 88ms
[PASS] ADS-CRAWL-06 https              - served over HTTPS
[PASS] ADS-CRAWL-02 robots.txt         - Mediapartners-Google, Googlebot and AdsBot are all allowed at /
[PASS] ADS-CRAWL-07 sitemap

4 checks: PASS=4
Verdict: every check observed its condition and passed.
```

Nothing wrong here, and nothing here was the reason.

### Content depth — where the answer actually is

The crawl finds 48 tool pages under `/apps/`. Measuring all of them:

```
distribuição: {'WARNING': 45, 'INFO': 2, 'OK': 1}
mediana de palavras: 220
mínimo: 92   máximo: 3281

as 8 mais rasas:
     92 palavras  /apps/compress-pdf-to-1mb/
    114 palavras  /apps/pdf-rotate/
    119 palavras  /apps/pdf-split/
    120 palavras  /apps/youtube-description-cleaner/
    134 palavras  /apps/pdf-to-image/
    136 palavras  /apps/image-to-pdf/
    136 palavras  /apps/pdf-metadata/
    137 palavras  /apps/pdf-watermark/
```

**45 of 48 tool pages are below 300 words, with a median of 220.** This is the
shape of the site: a large catalogue of interactive tools, each wrapped in a
short description. It matches `ADS-CONTENT-03` ("main content should be
substantial enough for users and crawlers") and the tool-site rubric's demand
that a tool page carry original guidance, not just the widget.

### What is *not* wrong, and should not be guessed at

The obvious second hypothesis for a tool site is template duplication. It does
not hold here. Comparing 24 tool pages pairwise:

```
276 pares comparados
  mediana: 0.134
  máximo:  0.327
  >= 0.40: 0 pares
  >= 0.60: 0 pares

pares mais parecidos:
  0.327  /apps/dockerignore-generator  vs  /apps/gitignore-generator
  0.283  /apps/image-crop              vs  /apps/image-resize
```

Every pair sits below the rubric's 0.40 "safe" band. The pages are short, but
they are genuinely different from one another.

The guides are also real, and substantial:

```
  1349 palavras  /guides/youtube-watch-history-json-format
  1326 palavras  /guides/resize-compress-images-locally
  1317 palavras  /guides/privacy-first-file-tools-local-processing
  ... 12 guides total, 8 of them at or above 1200 words
```

`ADS-COMPLETE-02` asks for three substantive guides. There are eight.

### What this example teaches about the skill

The signal was present — 45 thin pages, plainly measurable — and the gate that
produces the go/no-go answer never looked at it. The pre-flight gate reads the
home page, the About page, the Contact page and the guide count. It does not
read the pages that make up the bulk of the site.

For a catalogue site, that is the wrong sample. The three trust pages and the
guides are the best-written part; the 48 tool pages are the site. A gate that
inspects only the former will pass a site whose latter half is the problem.

There is also a counting trap worth knowing. A naive "count pages over 1200
words" run against the crawl returns seven — but six of them are the same page,
`/apps/` reached through six different query strings, each declaring
`rel="canonical"` back to `/apps`. The crawl reports this itself:

```
[MISS] ADS-CRAWL-05 stable URLs
         - https://smallwebapps.com/apps/?cat=PDF+Tools: canonical points to https://smallwebapps.com/apps
         - https://smallwebapps.com/apps/?cat=Developer+Tools: canonical points to https://smallwebapps.com/apps
```

Counting those as six substantive pages would have turned a real gap into a
comfortable pass.

---

## Example 2 — calculebrasil.com, outcome not known

Run on 2026-08-30. No AdSense verdict is recorded for this site, so nothing here
is validation — it is an illustration of the output shape on a site that is in
better condition.

```
$ python scripts/check_technical.py https://calculebrasil.com -v

[PASS] ADS-CRAWL-01 reachable
         - HTTP 200 in 55ms
           final_url: https://calculebrasil.com/
[PASS] ADS-CRAWL-06 https              - served over HTTPS
[PASS] ADS-CRAWL-02 robots.txt         - Mediapartners-Google, Googlebot and AdsBot are all allowed at /
[PASS] ADS-CRAWL-07 sitemap
           discovered_via: robots.txt
           kind: urlset
           url_count: 49

4 checks: PASS=4
Verdict: every check observed its condition and passed.
```

Note `discovered_via: robots.txt`. The sitemap is at the conventional path here,
but the check reads the `Sitemap:` directive first, which is what makes it work
on the many sites that serve 404 at `/sitemap.xml` and declare the real location
in robots.txt.

```
$ python scripts/analyze_text_depth.py https://calculebrasil.com https://calculebrasil.com/sobre

[note] ADS-CONTENT-03 https://calculebrasil.com/
         - 315 words: borderline (300-449 words)
[WARN] ADS-CONTENT-03 https://calculebrasil.com/sobre/
         - 251 words: below the configured threshold (300 words)

2 checks: note=1, WARN=1
Verdict: worst status is WARNING. Not ready.
```

---

## Reading an unmeasured half

Both sites produce this, and it is the reports' most important habit:

```
$ python scripts/crawl_site.py https://smallwebapps.com --max-pages 25

[PASS] ADS-CRAWL-01 pages reachable
         - blocked_by_robots=0, ok=25, pages=25
[MISS] ADS-CRAWL-04 redirect chains
         - 24 page(s) redirect and the cookie-less re-request was not made
           (verify_stateless=False): dependence on session state is unverified
[MISS] ADS-CRAWL-05 stable URLs
         - 25 page(s) declare a canonical and the two-session comparison was not
           made (verify_two_sessions=False): canonical stability is unverified

4 checks: PASS=2, MISS=2
Verdict: nothing failed, but something could not be observed. Not a pass.
```

`MISS` is not a failure and not a pass: the check has a second half that costs
extra requests and is off by default. Pass `--verify-stateless` to measure it.

The distinction exists because the previous implementation had no way to express
it, and resolved every unmeasured condition to "passed". Exit codes follow the
same rule: `MISS` and worse exit non-zero, so a site the tooling could not read
cannot be reported as a site that passed.
