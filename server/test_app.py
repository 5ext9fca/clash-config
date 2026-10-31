"""Offline regression checks; never contact the real subscription provider."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from app import Application, fetch_upstream, load_settings, parse_profile, render_common

COMMON = Path(__file__).resolve().parent.parent / "MySubscription-Common.yaml"
UPSTREAM = b"# Original provider profile\nproxies:\n- {name: Airport-test, type: ss, server: example.com, port: 443, cipher: aes-128-gcm, password: test}\nproxy-groups:\n- {name: Default, type: select, proxies: [Airport-test]}\nrules: ['MATCH,Default']\n"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.calls = 0
        self.denied = False
        self.settings = {
            "common": COMMON, "upstream_url": "https://provider.invalid/sub",
            "upstream_user_agent": "clash.meta", "timeout": 15,
            "cache_ttl": 300, "max_stale": 600, "retry_interval": 60,
            "devices": [
                {"type": kind, "token": letter * 40}
                for kind, letter in (("desktop", "d"), ("mobile", "m"), ("router", "r"))
            ],
        }
        self.app = Application(self.settings, self.fetch, lambda: self.now)

    def fetch(self, url, user_agent, timeout):
        self.calls += 1
        if self.denied:
            raise PermissionError("403: private subscription URL")
        return UPSTREAM, {"subscription-userinfo": "upload=1; download=2; total=100"}

    def request(self, letter="d", method="GET", path=None):
        response = {}

        def start(status, headers):
            response.update(status=status, headers=dict(headers))

        body = b"".join(self.app({
            "REQUEST_METHOD": method,
            "PATH_INFO": path or f"/sub/{letter * 40}/config.yaml",
        }, start))
        return response["status"], response["headers"], body

    def test_desktop_preserves_common_policies_and_embeds_nodes(self):
        status, _, body = self.request()
        self.assertEqual(status, "200 OK")
        actual = yaml.safe_load(body)
        common = yaml.safe_load(COMMON.read_bytes())
        for key in ("rules", "proxy-groups", "proxies", "dns", "tun", "rule-providers"):
            self.assertEqual(actual[key], common[key])
        provider = actual["proxy-providers"]["Airport"]
        self.assertEqual(provider["type"], "inline")
        self.assertEqual(provider["payload"], parse_profile(UPSTREAM)["proxies"])
        self.assertNotIn("url", provider)
        self.assertEqual(provider["health-check"], common["proxy-providers"]["Airport"]["health-check"])

    def test_mobile_is_byte_for_byte_upstream(self):
        status, headers, body = self.request("m")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, UPSTREAM)
        self.assertIn("subscription-userinfo", headers)

    def test_router_has_common_without_mixin(self):
        self.assertEqual(self.request("d")[2], self.request("r")[2])
        self.assertTrue(yaml.safe_load(self.request("r")[2])["tun"]["enable"])

    def test_invalid_token_path_and_method_do_not_fetch(self):
        for path in ("/", "/sub/bad/config.yaml", f"/sub/{'z' * 40}/config.yaml", "/../settings.yaml"):
            self.assertEqual(self.request(path=path)[0], "404 Not Found")
        self.assertEqual(self.request(method="POST")[0], "405 Method Not Allowed")
        self.assertEqual(self.calls, 0)

    def test_head_has_no_body(self):
        status, headers, body = self.request("m", "HEAD")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(UPSTREAM))

    def test_shared_cache_refreshes_on_expiry(self):
        self.request("m")
        self.request("d")
        self.assertEqual(self.calls, 1)
        self.now = 300
        self.request("m")
        self.assertEqual(self.calls, 2)

    def test_denial_serves_bounded_stale_cache_and_throttles_retries(self):
        self.request("m")
        self.now, self.denied = 300, True
        status, headers, body = self.request("m")
        self.assertEqual((status, body), ("200 OK", UPSTREAM))
        self.assertEqual(headers["X-Subscription-Cache"], "stale")
        self.request("m")
        self.assertEqual(self.calls, 2)
        self.now = 901
        self.assertEqual(self.request("m")[0], "503 Service Unavailable")

    def test_initial_denial_returns_503_without_private_details(self):
        self.denied = True
        status, _, body = self.request()
        self.assertEqual(status, "503 Service Unavailable")
        self.assertNotIn(b"private", body)

    def test_bad_upstream_does_not_replace_successful_cache(self):
        self.request("m")
        self.now = 300
        self.app.cache.fetch = lambda *args: (b"<html>Access denied</html>", {})
        self.assertEqual(self.request("m")[2], UPSTREAM)

    def test_invalid_yaml_and_duplicate_nodes_are_rejected(self):
        for body in (b"proxies: []", b"message: Forbidden", b"!!python/object:foo {}"):
            with self.assertRaises((ValueError, yaml.YAMLError)):
                parse_profile(body)
        profile = parse_profile(UPSTREAM)
        profile["proxies"] *= 2
        with self.assertRaises(ValueError):
            parse_profile(yaml.safe_dump(profile))

    def test_template_is_not_mutated(self):
        common = parse_profile(COMMON.read_bytes())
        original = copy.deepcopy(common)
        render_common(common, parse_profile(UPSTREAM))
        self.assertEqual(common, original)

    def test_settings_reject_placeholder_duplicate_and_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            settings = copy.deepcopy(self.settings)
            settings["common"] = str(COMMON)
            path.write_text(yaml.safe_dump(settings))
            self.assertEqual(load_settings(path)["common"], COMMON)
            for key, value in (("upstream_url", "http://provider.invalid/sub"), ("cache_ttl", 0)):
                changed = {**settings, key: value}
                path.write_text(yaml.safe_dump(changed))
                with self.assertRaises(ValueError):
                    load_settings(path)
            settings["devices"][0]["token"] = "REPLACE_WITH_A_RANDOM_DESKTOP_TOKEN"
            path.write_text(yaml.safe_dump(settings))
            with self.assertRaises(ValueError):
                load_settings(path)
            settings["devices"][0]["token"] = settings["devices"][1]["token"]
            path.write_text(yaml.safe_dump(settings))
            with self.assertRaises(ValueError):
                load_settings(path)

    @patch("app.urlopen")
    def test_fetch_sets_explicit_user_agent_and_filters_headers(self, open_mock):
        response = open_mock.return_value.__enter__.return_value
        response.status = 200
        response.url = "https://provider.invalid/sub"
        response.read.return_value = UPSTREAM
        response.headers = {"subscription-userinfo": "upload=1", "Set-Cookie": "private"}
        body, headers = fetch_upstream(response.url, "provider-approved-ua", 15)
        request = open_mock.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "provider-approved-ua")
        self.assertEqual(body, UPSTREAM)
        self.assertEqual(headers, {"subscription-userinfo": "upload=1"})


if __name__ == "__main__":
    unittest.main()
