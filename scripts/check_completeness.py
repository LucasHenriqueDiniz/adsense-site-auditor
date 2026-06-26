#!/usr/bin/env python3
"""
AdSense Site Auditor: Completeness & Identity Checker

Detects "site unfinished" patterns and verifies publisher identity.
Useful for ADS-COMPLETE-* and ADS-AUTHOR-* requirement checks.

Usage:
    python check_completeness.py <URL> [--repo PATH] [--output FILE]

Example:
    python check_completeness.py https://example.com --output completeness.txt
    python check_completeness.py https://example.com --repo /path/to/repo
"""

import sys
import requests
from urllib.parse import urljoin
from html.parser import HTMLParser
import json
from datetime import datetime

class TextExtractor(HTMLParser):
    """Extract text from HTML, focus on body content."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.in_body = True

    def handle_endtag(self, tag):
        if tag == 'body':
            self.in_body = False

    def handle_data(self, data):
        if self.in_body:
            text = data.strip()
            if text:
                self.text.append(text)

    def get_text(self):
        return ' '.join(self.text)

class CompletenessChecker:
    def __init__(self, url, timeout=10):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; AdSenseAuditor/1.0)'
        })

    def fetch_page(self, path):
        """Fetch a page and return status, text."""
        try:
            url = urljoin(self.url, path)
            resp = self.session.get(url, timeout=self.timeout)
            return resp.status_code, resp.text
        except Exception as e:
            return None, str(e)

    def extract_text(self, html):
        """Extract body text from HTML."""
        extractor = TextExtractor()
        try:
            extractor.feed(html)
        except:
            pass
        return extractor.get_text()

    def check_placeholder_text(self, text):
        """Check for common placeholder/unfinished phrases."""
        placeholders = [
            'will be added', 'coming soon', 'not yet', 'planned',
            'under construction', 'draft', 'todo', 'tbd',
            'work in progress', 'in development', 'placeholder',
            'this section'
        ]

        text_lower = text.lower()
        found = []
        for phrase in placeholders:
            if phrase in text_lower:
                found.append(phrase)
        return found

    def check_site_complete(self):
        """Check if site appears complete (not under construction)."""
        result = {
            "check": "site_completeness",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        # Check Homepage
        status, html = self.fetch_page('/')
        if status != 200:
            result["status"] = "FAIL"
            result["issues"].append(f"Homepage returns {status}")
            return result

        text = self.extract_text(html)

        # Check for placeholder text on homepage
        placeholders = self.check_placeholder_text(text)
        if placeholders:
            result["issues"].append(f"Homepage contains placeholder phrases: {', '.join(placeholders)}")
            result["status"] = "WARNING"

        # Check About page
        about_status, about_html = self.fetch_page('/about')
        if about_status == 404:
            result["issues"].append("About page missing (404)")
            result["details"]["about_page"] = "MISSING"
        elif about_status == 200:
            about_text = self.extract_text(about_html)
            if len(about_text.split()) < 100:
                result["issues"].append("About page is a stub (<100 words)")
                result["details"]["about_page"] = "STUB"
            else:
                result["details"]["about_page"] = "OK"
                # Check for placeholder on About
                about_placeholders = self.check_placeholder_text(about_text)
                if about_placeholders:
                    result["issues"].append(f"About page contains placeholder: {', '.join(about_placeholders)}")
        else:
            result["details"]["about_page"] = f"ERROR ({about_status})"

        # Check Contact page
        contact_status, contact_html = self.fetch_page('/contact')
        if contact_status == 404:
            result["issues"].append("Contact page missing (404)")
            result["details"]["contact_page"] = "MISSING"
        elif contact_status == 200:
            contact_text = self.extract_text(contact_html)
            contact_placeholders = self.check_placeholder_text(contact_text)
            if contact_placeholders:
                result["issues"].append(f"Contact page placeholder: {', '.join(contact_placeholders)}")
                result["details"]["contact_page"] = "PLACEHOLDER"
            elif 'email' not in contact_text.lower() and '@' not in contact_text:
                result["issues"].append("Contact page missing email/form")
                result["details"]["contact_page"] = "NO_EMAIL"
            else:
                result["details"]["contact_page"] = "OK"
        else:
            result["details"]["contact_page"] = f"ERROR ({contact_status})"

        # Severity: count issues
        if len(result["issues"]) >= 4:
            result["status"] = "FAIL"
        elif len(result["issues"]) >= 2:
            result["status"] = "HIGH_RISK"

        return result

    def check_publisher_identity(self):
        """Check if publisher identity is verifiable."""
        result = {
            "check": "publisher_identity",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        # Fetch About page
        status, html = self.fetch_page('/about')
        if status == 404:
            result["status"] = "FAIL"
            result["issues"].append("About page missing")
            return result

        if status != 200:
            result["status"] = "ERROR"
            result["issues"].append(f"Cannot access About page ({status})")
            return result

        text = self.extract_text(html)

        # Check for real name (heuristic: look for capital letter + space + capital letter pattern)
        has_likely_name = False
        words = text.split()
        for i in range(len(words) - 1):
            if words[i][0].isupper() and words[i+1][0].isupper() and len(words[i]) > 2 and len(words[i+1]) > 2:
                has_likely_name = True
                break

        if not has_likely_name:
            result["issues"].append("No clear real name found (not First Last format)")
            result["status"] = "HIGH_RISK"
        else:
            result["details"]["has_name"] = True

        # Check for contact info
        has_contact = False
        contact_signals = ['email@', 'contact', 'reach us', 'get in touch', 'linkedin', 'twitter', 'github']
        text_lower = text.lower()
        for signal in contact_signals:
            if signal in text_lower:
                has_contact = True
                break

        if not has_contact:
            result["issues"].append("No contact method visible (email, form, or social)")
            result["status"] = "HIGH_RISK"
        else:
            result["details"]["has_contact"] = True

        # Check for credentials/expertise
        credential_signals = ['years', 'experience', 'expert', 'professional', 'founder', 'ceo', 'engineer', 'developer', 'consultant']
        has_credentials = any(signal in text_lower for signal in credential_signals)
        result["details"]["has_credentials"] = has_credentials
        if not has_credentials:
            result["issues"].append("No visible credentials or expertise claimed")

        return result

    def run_all_checks(self):
        """Run all completeness checks."""
        return [
            self.check_site_complete(),
            self.check_publisher_identity(),
        ]

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_completeness.py <URL> [--output FILE]")
        sys.exit(1)

    url = sys.argv[1]
    output = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--output' and i + 1 < len(sys.argv) - 2:
            output = sys.argv[i + 3]

    print(f"Checking completeness for {url}...\n")

    checker = CompletenessChecker(url)
    results = checker.run_all_checks()

    # Print results
    for check in results:
        status_symbol = {"OK": "✓", "HIGH_RISK": "⚠️ ", "FAIL": "❌", "ERROR": "❓", "WARNING": "⚠️ "}.get(check["status"], "?")
        print(f"{status_symbol} {check['check']:25} | {check['status']:10}")

        for issue in check.get('issues', []):
            print(f"   → {issue}")

        for key, val in check.get('details', {}).items():
            print(f"   • {key}: {val}")

        print()

    # Risk summary
    failures = sum(1 for c in results if c['status'] in ['FAIL', 'ERROR'])
    high_risks = sum(1 for c in results if c['status'] == 'HIGH_RISK')

    if failures > 0:
        print(f"❌ {failures} FAIL checks — site appears unfinished or identity unverifiable")
    elif high_risks > 0:
        print(f"⚠️  {high_risks} HIGH_RISK checks — likely AdSense rejection")
    else:
        print("✓ Completeness checks passed")

    # Save to file
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            f.write(f"AdSense Completeness Check Report\n")
            f.write(f"URL: {url}\n")
            f.write(f"Date: {datetime.now().isoformat()}\n\n")

            for check in results:
                f.write(f"{check['check']}: {check['status']}\n")
                for issue in check.get('issues', []):
                    f.write(f"  • {issue}\n")
                for key, val in check.get('details', {}).items():
                    f.write(f"  {key}: {val}\n")
                f.write("\n")

        print(f"\nReport saved to {output}")

    return results

if __name__ == '__main__':
    main()
