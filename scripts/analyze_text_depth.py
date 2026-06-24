#!/usr/bin/env python3
"""
AdSense Site Auditor: Text Depth Analyzer

Analyzes pages for word count, text density, and thin-content risk.
Useful for detecting pages that may violate ADS-CONTENT-03 and ADS-CONTENT-04.

Usage:
    python analyze_text_depth.py <URL_or_FILE> [--min-words 300] [--output FILE]

If URL_or_FILE is a file (*.json), reads crawl results from crawl_site.py.
If URL_or_FILE is a URL, analyzes that single page.

Example:
    python analyze_text_depth.py crawl_report.json --min-words 300 --output depth_report.txt
    python analyze_text_depth.py https://example.com/article --min-words 500
"""

import sys
import json
import requests
from html.parser import HTMLParser
from urllib.parse import urlparse

class TextExtractor(HTMLParser):
    """Extract visible text from HTML, excluding script, style, and nav elements."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {'script', 'style', 'nav', 'footer', 'noscript'}
        self.skip_level = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.skip_level += 1

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_level > 0:
            self.skip_level -= 1

    def handle_data(self, data):
        if self.skip_level == 0:
            text = data.strip()
            if text:
                self.text.append(text)

    def get_text(self):
        return ' '.join(self.text)

def analyze_page(url, min_words=300, timeout=10):
    """
    Fetch a page and analyze its text depth.

    Returns a dict with word count, text density, and risk assessment.
    """
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()

        extractor = TextExtractor()
        try:
            extractor.feed(resp.text)
        except Exception:
            pass

        text = extractor.get_text()
        words = text.split()
        word_count = len(words)

        # Estimate text density (words per KB)
        content_length_kb = len(resp.text) / 1024
        text_density = word_count / content_length_kb if content_length_kb > 0 else 0

        # Risk assessment
        risk = "OK"
        if word_count < min_words:
            risk = "THIN"
        elif word_count < min_words * 1.5:
            risk = "BORDERLINE"

        return {
            "url": resp.url,
            "status": resp.status_code,
            "word_count": word_count,
            "text_density": round(text_density, 2),
            "content_length_kb": round(content_length_kb, 2),
            "risk": risk,
            "sample_text": text[:200] + "..." if len(text) > 200 else text
        }

    except Exception as e:
        return {
            "url": url,
            "status": "error",
            "error": str(e),
            "risk": "ERROR"
        }

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_text_depth.py <URL_or_FILE> [--min-words N] [--output FILE]")
        sys.exit(1)

    target = sys.argv[1]
    min_words = 300
    output = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--min-words' and i + 1 < len(sys.argv) - 2:
            min_words = int(sys.argv[i + 3])
        elif arg == '--output' and i + 1 < len(sys.argv) - 2:
            output = sys.argv[i + 3]

    results = []

    # Check if target is a file or URL
    if target.endswith('.json'):
        print(f"Loading crawl results from {target}...")
        with open(target, 'r', encoding='utf-8') as f:
            crawl_data = json.load(f)
            urls = [r['url'] for r in crawl_data.get('results', []) if r.get('status') == 200]
    else:
        urls = [target]

    print(f"Analyzing text depth for {len(urls)} URLs (min_words={min_words})...\n")

    for url in urls:
        result = analyze_page(url, min_words=min_words)
        results.append(result)

        risk_marker = "⚠️ " if result.get('risk') in ['THIN', 'BORDERLINE'] else "✓ "
        print(f"{risk_marker} {result.get('risk', '?'):12} | {result.get('word_count', 0):>5} words | {url[:70]}")

    # Summary
    thin_count = sum(1 for r in results if r.get('risk') == 'THIN')
    borderline_count = sum(1 for r in results if r.get('risk') == 'BORDERLINE')

    print(f"\n--- Summary ---")
    print(f"Total: {len(results)} pages")
    print(f"THIN (<{min_words} words): {thin_count} pages")
    print(f"BORDERLINE ({min_words}-{int(min_words*1.5)} words): {borderline_count} pages")
    print(f"OK (>{int(min_words*1.5)} words): {len(results) - thin_count - borderline_count} pages")

    if thin_count + borderline_count > 0:
        print(f"\n⚠️  Risk: {thin_count + borderline_count} pages may trigger ADS-CONTENT-03 or ADS-CONTENT-04")

    # Save to file if output specified
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            f.write("AdSense Text Depth Analysis Report\n")
            f.write(f"Min word count: {min_words}\n")
            f.write(f"Total pages analyzed: {len(results)}\n\n")

            for r in results:
                f.write(f"{r.get('url')}\n")
                f.write(f"  Status: {r.get('status')}\n")
                f.write(f"  Words: {r.get('word_count')} | Density: {r.get('text_density')} words/KB\n")
                f.write(f"  Risk: {r.get('risk')}\n")
                if r.get('error'):
                    f.write(f"  Error: {r.get('error')}\n")
                f.write("\n")

        print(f"\nResults saved to {output}")

    return results

if __name__ == '__main__':
    main()
