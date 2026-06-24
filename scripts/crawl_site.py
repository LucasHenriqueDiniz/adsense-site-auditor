#!/usr/bin/env python3
"""
AdSense Site Auditor: Web Crawler

Crawls a website to collect URLs, HTTP status, page titles, meta descriptions,
and basic content for AdSense audit analysis.

Usage:
    python crawl_site.py <URL> [--depth N] [--output FILE]

Example:
    python crawl_site.py https://example.com --depth 2 --output crawl_report.txt
"""

import sys
import requests
from urllib.parse import urljoin, urlparse
from collections import deque
import json
from datetime import datetime
from html.parser import HTMLParser

class MetaExtractor(HTMLParser):
    """Extract title, meta description, H1 from HTML."""
    def __init__(self):
        super().__init__()
        self.title = None
        self.meta_desc = None
        self.h1 = None
        self.in_head = False
        self.in_title = False
        self.in_h1 = False
        self.title_text = []
        self.h1_text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'head':
            self.in_head = True
        elif tag == 'title':
            self.in_title = True
        elif tag == 'h1':
            self.in_h1 = True
        elif tag == 'meta' and self.in_head:
            attrs_dict = dict(attrs)
            if attrs_dict.get('name', '').lower() == 'description':
                self.meta_desc = attrs_dict.get('content', '').strip()

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        elif tag == 'title':
            self.in_title = False
            self.title = ''.join(self.title_text).strip()
        elif tag == 'h1':
            self.in_h1 = False
            self.h1 = ''.join(self.h1_text).strip()

    def handle_data(self, data):
        if self.in_title:
            self.title_text.append(data)
        elif self.in_h1:
            self.h1_text.append(data)

def crawl_site(start_url, max_depth=2, timeout=10):
    """
    Crawl a website starting from start_url up to max_depth.

    Returns a list of crawl results: [{"url": url, "status": status, "title": title, ...}, ...]
    """
    parsed = urlparse(start_url)
    base_domain = parsed.netloc

    visited = set()
    queue = deque([(start_url, 0)])
    results = []
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    while queue:
        url, depth = queue.popleft()

        if url in visited or depth > max_depth:
            continue

        visited.add(url)
        parsed_url = urlparse(url)

        # Only crawl same domain
        if parsed_url.netloc != base_domain:
            continue

        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            status = resp.status_code

            # Extract metadata
            meta_extractor = MetaExtractor()
            try:
                meta_extractor.feed(resp.text[:5000])  # Only parse first 5KB for speed
            except Exception:
                pass

            result = {
                "url": resp.url,
                "status": status,
                "title": meta_extractor.title or "[no title]",
                "meta_description": meta_extractor.meta_desc or "[none]",
                "h1": meta_extractor.h1 or "[no h1]",
                "content_length": len(resp.text),
                "depth": depth
            }
            results.append(result)

            # Extract links for next crawl
            if depth < max_depth:
                meta_extractor = MetaExtractor()
                try:
                    meta_extractor.feed(resp.text)
                except Exception:
                    pass

                # Simple link extraction
                import re
                for match in re.finditer(r'href=["\']([^"\']+)["\']', resp.text):
                    link = match.group(1)
                    if link.startswith('#'):
                        continue
                    next_url = urljoin(resp.url, link)
                    if next_url not in visited:
                        queue.append((next_url, depth + 1))

        except Exception as e:
            result = {
                "url": url,
                "status": "error",
                "error": str(e),
                "depth": depth
            }
            results.append(result)

    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: python crawl_site.py <URL> [--depth N] [--output FILE]")
        sys.exit(1)

    url = sys.argv[1]
    depth = 2
    output = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--depth' and i + 1 < len(sys.argv) - 2:
            depth = int(sys.argv[i + 3])
        elif arg == '--output' and i + 1 < len(sys.argv) - 2:
            output = sys.argv[i + 3]

    print(f"Crawling {url} (depth={depth})...")
    results = crawl_site(url, max_depth=depth)

    # Print summary
    print(f"\nCrawled {len(results)} URLs:")
    for result in results:
        status_str = f"{result.get('status', 'error')}"
        title = result.get('title', '[no title]')[:60]
        print(f"  {status_str:>3} | {result['url'][:70]:70} | {title}")

    # Save to JSON if output specified
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            json.dump({
                "crawl_date": datetime.now().isoformat(),
                "start_url": url,
                "total_urls": len(results),
                "results": results
            }, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to {output}")

    return results

if __name__ == '__main__':
    main()
