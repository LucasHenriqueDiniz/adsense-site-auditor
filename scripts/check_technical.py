#!/usr/bin/env python3
"""
AdSense Site Auditor: Technical Checks

Verifies robots.txt, sitemap.xml, redirects, security headers, and crawler accessibility.
Useful for ADS-CRAWL-* and ADS-SITE-* requirements.

Usage:
    python check_technical.py <URL> [--output FILE]

Example:
    python check_technical.py https://example.com --output technical_report.txt
"""

import sys
import requests
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

class TechnicalChecker:
    def __init__(self, url, timeout=10):
        self.url = url
        self.domain = urlparse(url).netloc
        self.base_url = f"{urlparse(url).scheme}://{self.domain}"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'
        })

    def check_robots_txt(self):
        """Check if robots.txt exists and if Googlebot is allowed."""
        result = {
            "check": "robots.txt",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        url = f"{self.base_url}/robots.txt"
        try:
            resp = self.session.get(url, timeout=self.timeout)

            result["details"]["http_status"] = resp.status_code

            if resp.status_code == 200:
                content = resp.text.lower()

                # Check for Googlebot blocks
                if 'user-agent:' in content:
                    if '*' in content and 'disallow: /' in content:
                        result["status"] = "FAIL"
                        result["issues"].append("robots.txt blocks all crawlers with Disallow: /")

                    if 'mediapartners-google' in content:
                        if 'mediapartners-google' in content.split('user-agent:')[-1]:
                            if 'disallow:' in content:
                                result["issues"].append("robots.txt may block Mediapartners-Google (AdSense crawler)")
                                result["status"] = "WARNING"

                result["details"]["found"] = True
                result["details"]["snippet"] = content[:300]
            else:
                result["status"] = "MISSING"
                result["issues"].append(f"robots.txt returns {resp.status_code}")

        except Exception as e:
            result["status"] = "ERROR"
            result["issues"].append(str(e))

        return result

    def check_sitemap(self):
        """Check if sitemap.xml exists and is valid."""
        result = {
            "check": "sitemap.xml",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        url = f"{self.base_url}/sitemap.xml"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            result["details"]["http_status"] = resp.status_code

            if resp.status_code == 200:
                try:
                    root = ET.fromstring(resp.text)
                    # Count URLs in sitemap
                    urls = root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    result["details"]["url_count"] = len(urls)
                    result["details"]["found"] = True

                    if len(urls) == 0:
                        result["status"] = "WARNING"
                        result["issues"].append("sitemap.xml found but contains no URLs")
                    else:
                        result["details"]["first_urls"] = [u.text for u in urls[:3]]

                except ET.ParseError:
                    result["status"] = "WARNING"
                    result["issues"].append("sitemap.xml is not valid XML")

            else:
                result["status"] = "MISSING"
                result["issues"].append(f"sitemap.xml returns {resp.status_code}")

        except Exception as e:
            result["status"] = "ERROR"
            result["issues"].append(str(e))

        return result

    def check_redirects(self):
        """Check if homepage redirects are minimal."""
        result = {
            "check": "redirects",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        try:
            resp = self.session.head(self.url, timeout=self.timeout, allow_redirects=True)
            history_count = len(resp.history)
            result["details"]["redirect_count"] = history_count
            result["details"]["final_url"] = resp.url

            if history_count > 2:
                result["status"] = "WARNING"
                result["issues"].append(f"Excessive redirects: {history_count} hops")

            if resp.status_code >= 400:
                result["status"] = "FAIL"
                result["issues"].append(f"Final response is {resp.status_code}")

        except Exception as e:
            result["status"] = "ERROR"
            result["issues"].append(str(e))

        return result

    def check_security_headers(self):
        """Check for security headers (informational)."""
        result = {
            "check": "security_headers",
            "status": "OK",
            "issues": [],
            "details": {}
        }

        try:
            resp = self.session.head(self.url, timeout=self.timeout)

            headers_to_check = {
                'Content-Security-Policy': 'CSP',
                'X-Frame-Options': 'Clickjacking protection',
                'X-Content-Type-Options': 'MIME-sniffing protection',
                'Strict-Transport-Security': 'HTTPS enforcement',
            }

            for header, name in headers_to_check.items():
                if header in resp.headers:
                    result["details"][name] = resp.headers[header][:50] + "..."
                else:
                    result["issues"].append(f"Missing {name} header ({header})")

            if len(result["issues"]) > 0:
                result["status"] = "WARNING"

        except Exception as e:
            result["status"] = "ERROR"
            result["issues"].append(str(e))

        return result

    def check_https(self):
        """Check if site uses HTTPS."""
        result = {
            "check": "https",
            "status": "OK",
            "issues": []
        }

        if not self.url.startswith("https://"):
            result["status"] = "WARNING"
            result["issues"].append("Site does not use HTTPS")
        else:
            try:
                resp = self.session.get(self.url, timeout=self.timeout, verify=True)
                result["details"] = {"ssl_verified": True}
            except requests.exceptions.SSLError:
                result["status"] = "FAIL"
                result["issues"].append("SSL certificate verification failed")
            except requests.exceptions.RequestException as e:
                result["status"] = "ERROR"
                result["issues"].append(str(e))

        return result

    def check_dns_and_uptime(self):
        """Quick check for DNS resolution and basic connectivity."""
        result = {
            "check": "dns_and_uptime",
            "status": "OK",
            "issues": []
        }

        try:
            resp = self.session.get(self.url, timeout=self.timeout)
            result["details"] = {
                "http_status": resp.status_code,
                "response_time_ms": round(resp.elapsed.total_seconds() * 1000, 2)
            }

            if resp.status_code >= 500:
                result["status"] = "FAIL"
                result["issues"].append(f"Server returned {resp.status_code}")

            if result["details"]["response_time_ms"] > 5000:
                result["status"] = "WARNING"
                result["issues"].append(f"Slow response: {result['details']['response_time_ms']}ms")

        except requests.exceptions.ConnectionError:
            result["status"] = "FAIL"
            result["issues"].append("DNS or connection error")
        except Exception as e:
            result["status"] = "ERROR"
            result["issues"].append(str(e))

        return result

    def run_all_checks(self):
        """Run all technical checks."""
        checks = [
            self.check_dns_and_uptime(),
            self.check_https(),
            self.check_robots_txt(),
            self.check_sitemap(),
            self.check_redirects(),
            self.check_security_headers(),
        ]
        return checks

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_technical.py <URL> [--output FILE]")
        sys.exit(1)

    url = sys.argv[1]
    output = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--output' and i + 1 < len(sys.argv) - 2:
            output = sys.argv[i + 3]

    print(f"Running technical checks on {url}...\n")

    checker = TechnicalChecker(url)
    results = checker.run_all_checks()

    # Print results
    for check in results:
        status_symbol = {"OK": "✓", "WARNING": "⚠️ ", "FAIL": "❌", "ERROR": "❓", "MISSING": "⚠️ "}.get(check["status"], "?")
        print(f"{status_symbol} {check['check']:20} | {check['status']:10}")

        for issue in check.get('issues', []):
            print(f"   → {issue}")

        for key, val in check.get('details', {}).items():
            if isinstance(val, str) and len(val) > 60:
                val = val[:60] + "..."
            print(f"   • {key}: {val}")

        print()

    # Risk summary
    failures = sum(1 for c in results if c['status'] in ['FAIL', 'ERROR'])
    warnings = sum(1 for c in results if c['status'] == 'WARNING')

    if failures > 0:
        print(f"⚠️  {failures} critical issues found — may impact crawlability")
    elif warnings > 0:
        print(f"⚠️  {warnings} warnings — review recommended")
    else:
        print("✓ All technical checks passed")

    # Save to file
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            f.write(f"AdSense Technical Check Report\n")
            f.write(f"URL: {url}\n\n")

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
