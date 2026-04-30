"""Tests for S6: NetworkInterceptor Fix.

Covers: domain matching, private IP blocking, credential stripping,
SSRF prevention in api_discovery and post_dispatcher.
"""
import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from security import NetworkInterceptor


class TestDomainMatching(unittest.TestCase):
    """Verify allowlist domain matching logic."""

    def setUp(self):
        self.interceptor = NetworkInterceptor()

    @patch("security.socket.gethostbyname", return_value="142.250.80.46")
    def test_allowed_domain_passes(self, mock_dns):
        self.assertTrue(self.interceptor.check_url("https://googleapis.com/api"))

    @patch("security.socket.gethostbyname", return_value="142.250.80.46")
    def test_allowed_subdomain_passes(self, mock_dns):
        self.assertTrue(self.interceptor.check_url("https://storage.googleapis.com/bucket"))

    @patch("security.socket.gethostbyname", return_value="93.184.216.34")
    def test_disallowed_domain_blocked(self, mock_dns):
        self.assertFalse(self.interceptor.check_url("https://evil.com/steal"))

    def test_empty_url_blocked(self):
        self.assertFalse(self.interceptor.check_url(""))

    def test_no_scheme_blocked(self):
        self.assertFalse(self.interceptor.check_url("just-a-string"))

    @patch("security.socket.gethostbyname", return_value="142.250.80.46")
    def test_domain_with_port_passes(self, mock_dns):
        self.assertTrue(self.interceptor.check_url("https://googleapis.com:8443/api"))


class TestPrivateIPBlocking(unittest.TestCase):
    """Verify SSRF protection blocks private/internal IP ranges."""

    def setUp(self):
        self.interceptor = NetworkInterceptor()

    @patch("security.socket.gethostbyname", return_value="10.0.0.1")
    def test_rfc1918_10_blocked(self, mock_dns):
        self.assertFalse(self.interceptor.check_url("https://internal.googleapis.com/api"))

    @patch("security.socket.gethostbyname", return_value="172.16.5.5")
    def test_rfc1918_172_blocked(self, mock_dns):
        self.assertFalse(self.interceptor.check_url("https://internal.googleapis.com/api"))

    @patch("security.socket.gethostbyname", return_value="192.168.1.1")
    def test_rfc1918_192_blocked(self, mock_dns):
        self.assertFalse(self.interceptor.check_url("https://internal.googleapis.com/api"))

    @patch("security.socket.gethostbyname", return_value="127.0.0.1")
    def test_loopback_blocked(self, mock_dns):
        self.assertFalse(self.interceptor.check_url("https://loopback.googleapis.com/api"))

    @patch("security.socket.gethostbyname", return_value="169.254.169.254")
    def test_link_local_metadata_blocked(self, mock_dns):
        """Cloud metadata endpoint (169.254.169.254) must be blocked."""
        self.assertFalse(self.interceptor.check_url("https://metadata.googleapis.com/"))

    @patch("security.socket.gethostbyname", side_effect=Exception("DNS failure"))
    def test_dns_failure_blocked(self, mock_dns):
        """DNS resolution failure should fail-closed (block)."""
        self.assertFalse(self.interceptor.check_url("https://unknown.googleapis.com/api"))


class TestCredentialStripping(unittest.TestCase):
    """Verify _strip_credentials removes user:pass@ from URLs."""

    def test_credentials_stripped(self):
        url = "https://user:pass@googleapis.com/api"
        result = NetworkInterceptor._strip_credentials(url)
        self.assertNotIn("user", result)
        self.assertNotIn("pass@", result)
        self.assertIn("googleapis.com", result)

    def test_no_credentials_unchanged(self):
        url = "https://googleapis.com/api"
        result = NetworkInterceptor._strip_credentials(url)
        self.assertEqual(result, url)


class TestSSRFInAPIDiscovery(unittest.TestCase):
    """Verify api_discovery.py checks URLs through NetworkInterceptor."""

    @patch("security.socket.gethostbyname", return_value="10.0.0.1")
    def test_private_url_blocked_in_scrape(self, mock_dns):
        from api_discovery import APIDiscoveryEngine
        engine = APIDiscoveryEngine()
        result = engine.scrape_docs("http://internal-server.com/docs")
        self.assertIn("blocked by security policy", result)

    def test_raw_text_passes_through(self):
        from api_discovery import APIDiscoveryEngine
        engine = APIDiscoveryEngine()
        result = engine.scrape_docs("This is raw documentation text")
        self.assertEqual(result, "This is raw documentation text")


class TestSSRFInPostDispatcher(unittest.TestCase):
    """Verify post_dispatcher.py checks URLs through NetworkInterceptor."""

    @patch("security.socket.gethostbyname", return_value="192.168.1.100")
    def test_private_endpoint_blocked_in_dispatch(self, mock_dns):
        from post_dispatcher import PostDispatcher
        dispatcher = PostDispatcher()
        dispatcher.register_platform("evil", endpoint="http://192.168.1.100/hook", mode="http")
        result = dispatcher.dispatch("test content", "evil", mode="http")
        self.assertEqual(result["status"], "error")
        self.assertIn("blocked by security policy", result["response"])


if __name__ == "__main__":
    unittest.main()
