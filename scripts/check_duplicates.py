#!/usr/bin/env python3
"""
AdSense Site Auditor: Duplicate Content Detector

Detects near-duplicate pages and high boilerplate reuse.
Useful for flagging ADS-CONTENT-02 and ADS-CONTENT-08 violations.

Usage:
    python check_duplicates.py <URL_or_FILE> [--threshold 0.8] [--output FILE]

If URL_or_FILE is a file (*.json), reads crawl results from crawl_site.py.

Example:
    python check_duplicates.py crawl_report.json --threshold 0.8 --output dup_report.txt
"""

import sys
import json
import requests
from html.parser import HTMLParser
import difflib

class TextExtractor(HTMLParser):
    """Extract main text from HTML."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {'script', 'style', 'nav', 'meta', 'link', 'noscript', 'footer'}
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
            if text and len(text) > 2:
                self.text.append(text)

    def get_text(self):
        return '\n'.join(self.text)

def fetch_text(url, timeout=10):
    """Fetch a page and extract main text."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()

        extractor = TextExtractor()
        try:
            extractor.feed(resp.text)
        except Exception:
            pass

        return extractor.get_text()
    except Exception:
        return None

def similarity_ratio(text1, text2):
    """Calculate text similarity using SequenceMatcher (0.0 to 1.0)."""
    if not text1 or not text2:
        return 0.0

    matcher = difflib.SequenceMatcher(None, text1, text2)
    return matcher.ratio()

def analyze_duplicates(urls, threshold=0.8, timeout=10):
    """
    Analyze text similarity across URLs.

    Returns a list of duplicate groups where similarity >= threshold.
    """
    print(f"Fetching content from {len(urls)} URLs...\n")

    texts = {}
    for url in urls:
        text = fetch_text(url, timeout=timeout)
        if text:
            texts[url] = text
            print(f"  ✓ {url[:70]}")
        else:
            print(f"  ✗ {url[:70]}")

    print(f"\nAnalyzing {len(texts)} pages for duplicates (threshold={threshold})...\n")

    duplicates = []
    processed = set()

    for url1 in texts:
        if url1 in processed:
            continue

        group = [url1]
        for url2 in texts:
            if url1 == url2 or url2 in processed:
                continue

            sim = similarity_ratio(texts[url1], texts[url2])
            if sim >= threshold:
                group.append((url2, sim))

        if len(group) > 1:
            duplicates.append({
                "source": url1,
                "similar_pages": group[1:],
                "avg_similarity": sum(sim for _, sim in group[1:]) / len(group[1:]) if group[1:] else 0
            })

        processed.add(url1)
        for url2, _ in group[1:]:
            processed.add(url2)

    return duplicates, texts

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_duplicates.py <URL_or_FILE> [--threshold N] [--output FILE]")
        sys.exit(1)

    target = sys.argv[1]
    threshold = 0.8
    output = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--threshold' and i + 1 < len(sys.argv) - 2:
            threshold = float(sys.argv[i + 3])
        elif arg == '--output' and i + 1 < len(sys.argv) - 2:
            output = sys.argv[i + 3]

    # Load URLs from file or use single URL
    if target.endswith('.json'):
        print(f"Loading crawl results from {target}...")
        with open(target, 'r', encoding='utf-8') as f:
            crawl_data = json.load(f)
            urls = [r['url'] for r in crawl_data.get('results', []) if r.get('status') == 200]
    else:
        urls = [target]

    duplicates, texts = analyze_duplicates(urls, threshold=threshold)

    # Report
    if duplicates:
        print(f"⚠️  Found {len(duplicates)} pages with similar content:\n")
        for dup in duplicates:
            print(f"Source: {dup['source'][:70]}")
            for url, sim in dup['similar_pages']:
                print(f"  ↔ {sim:.0%} similar | {url[:60]}")
            print()
    else:
        print(f"✓ No significant duplicates found (threshold={threshold})")

    # Risk assessment
    if len(duplicates) > len(urls) * 0.3:
        print(f"⚠️  High duplication risk: {len(duplicates)} of {len(urls)} pages have >80% similar content")
        print("    This may trigger ADS-CONTENT-02 or ADS-CONTENT-08 flags\n")
    else:
        print(f"✓ Duplication risk appears acceptable\n")

    # Save to file
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            f.write("AdSense Duplicate Content Report\n")
            f.write(f"Threshold: {threshold}\n")
            f.write(f"Pages analyzed: {len(texts)}\n")
            f.write(f"Duplicate groups found: {len(duplicates)}\n\n")

            for dup in duplicates:
                f.write(f"Source: {dup['source']}\n")
                f.write(f"Average similarity: {dup['avg_similarity']:.0%}\n")
                for url, sim in dup['similar_pages']:
                    f.write(f"  {sim:.0%} | {url}\n")
                f.write("\n")

        print(f"Report saved to {output}")

    return duplicates

if __name__ == '__main__':
    main()
