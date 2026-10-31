"""Offline regression checks; never contact the real subscription provider."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from app import Application, fetch_upstream, load_settings, parse_profile, render_common, load_mixin, merge_profile, load_common, DEFAULT_USER_AGENT

COMMON = Path(__file__).resolve().parent.parent / "MySubscription-Common.yaml"
PRIVATE = COMMON.with_name("Private-Nodes-Mixin.yaml")
SUBSCRIPTION = COMMON.with_name("Subscription-Mixin.yaml")
ROUTER = COMMON.with_name("Nikki-Mixin.yaml")
UPSTREAM = b"# Original provider profile\nproxies:\n- {name: Airport-test, type: ss, server: example.com, port: 443, cipher: aes-128-gcm, password: test}\nproxy-groups:\n- {name: Default, type: select, proxies: [Airport-test]}\nrules: ['MATCH,Default']\n"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.calls = 0
        self.denied = False
        self.settings = {
            "private_nodes_mixin": str(PRIVATE),
            "subscription_mixin": str(SUBSCRIPTION), "router_mixin": str(ROUTER),
            "common": COMMON, "upstream_url": "https://provider.invalid/sub",
            "upstream_user_agent": DEFAULT_USER_AGENT, "timeout": 15,
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
        common = merge_profile(load_common(self.settings), load_mixin(SUBSCRIPTION))
        for key in ("rules", "proxy-groups", "proxies", "dns", "tun", "rule-providers"):
            self.assertEqual(actual[key], common[key])
        provider = actual["proxy-providers"]["Airport"]
        self.assertEqual(provider["type"], "inline")
        self.assertEqual(provider["payload"], parse_profile(UPSTREAM)["proxies"])
        self.assertNotIn("url", provider)
        self.assertEqual(provider["health-check"], common["proxy-providers"]["Airport"]["health-check"])

    def test_private_nodes_are_separate_and_references_resolve(self):
        self.assertNotIn("proxies", load_mixin(COMMON))
        for device in ("d", "r"):
            profile = yaml.safe_load(self.request(device)[2])
            self.assertEqual(profile["proxies"], load_mixin(PRIVATE)["proxies"])
            names = {node["name"] for node in profile["proxies"]}
            names.update(group["name"] for group in profile["proxy-groups"])
            names.update(("DIRECT", "REJECT"))
            for group in profile["proxy-groups"]:
                self.assertTrue(set(group.get("proxies", [])) <= names)

    def test_mobile_is_byte_for_byte_upstream(self):
        status, headers, body = self.request("m")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, UPSTREAM)
        self.assertIn("subscription-userinfo", headers)

    def test_router_automatically_merges_nikki(self):
        desktop = yaml.safe_load(self.request("d")[2])
        status, _, body = self.request("r")
        self.assertEqual(status, "200 OK")
        router = yaml.safe_load(body)
        self.assertEqual(router, merge_profile(desktop, load_mixin(ROUTER)))
        self.assertFalse(router["tun"]["enable"])
        self.assertEqual(router["tun"]["dns-hijack"], [])
        self.assertEqual(router["dns"]["enhanced-mode"], "redir-host")
        self.assertEqual(router["dns"]["nameserver-policy"], desktop["dns"]["nameserver-policy"])
        self.assertEqual(router["tproxy-port"], 7893)
        self.assertTrue(desktop["tun"]["enable"])
        self.assertEqual(desktop["dns"]["enhanced-mode"], "fake-ip")
        for key in ("rules", "proxies", "proxy-groups", "proxy-providers"):
            self.assertEqual(router[key], desktop[key])

    def test_merge_replaces_lists_and_does_not_mutate_inputs(self):
        base = {"dns": {"keep": True, "list": [1, 2]}, "tun": True}
        overlay = {"dns": {"list": []}, "tun": False}
        original = copy.deepcopy((base, overlay))
        self.assertEqual(merge_profile(base, overlay), {"dns": {"keep": True, "list": []}, "tun": False})
        self.assertEqual((base, overlay), original)

    def test_broken_router_mixin_does_not_affect_desktop_or_mobile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "router.yaml"
            path.write_text("[]")
            self.settings["router_mixin"] = path
            self.assertEqual(self.request("r")[0], "503 Service Unavailable")
            self.assertEqual(self.request("d")[0], "200 OK")
            self.assertEqual(self.request("m")[2], UPSTREAM)

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
        common = load_common(self.settings)
        original = copy.deepcopy(common)
        render_common(common, parse_profile(UPSTREAM), load_mixin(SUBSCRIPTION), load_mixin(ROUTER))
        self.assertEqual(common, original)

    def test_settings_reject_placeholder_duplicate_and_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            settings = copy.deepcopy(self.settings)
            settings["common"] = str(COMMON)
            path.write_text(yaml.safe_dump(settings))
            self.assertEqual(load_settings(path)["common"], COMMON)
            settings["upstream_url"] = ""
            path.write_text(yaml.safe_dump(settings))
            self.assertEqual(load_settings(path)["upstream_url"], load_mixin(SUBSCRIPTION)["proxy-providers"]["Airport"]["url"])
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

    def test_default_and_legacy_settings_use_clash_verge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            settings = {**self.settings, "common": str(COMMON)}
            for value in (None, "", "clash.meta"):
                settings["upstream_user_agent"] = value
                path.write_text(yaml.safe_dump(settings))
                self.assertEqual(load_settings(path)["upstream_user_agent"], DEFAULT_USER_AGENT)
            del settings["upstream_user_agent"]
            path.write_text(yaml.safe_dump(settings))
            self.assertEqual(load_settings(path)["upstream_user_agent"], DEFAULT_USER_AGENT)
        provider = load_mixin(SUBSCRIPTION)["proxy-providers"]["Airport"]
        self.assertEqual(provider["header"]["User-Agent"], [DEFAULT_USER_AGENT])

    @patch("app.urlopen")
    def test_fetch_sets_explicit_user_agent_and_filters_headers(self, open_mock):
        response = open_mock.return_value.__enter__.return_value
        response.status = 200
        response.url = "https://provider.invalid/sub"
        response.read.return_value = UPSTREAM
        response.headers = {"subscription-userinfo": "upload=1", "Set-Cookie": "private"}
        body, headers = fetch_upstream(response.url, DEFAULT_USER_AGENT, 15)
        request = open_mock.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), DEFAULT_USER_AGENT)
        self.assertEqual(body, UPSTREAM)
        self.assertEqual(headers, {"subscription-userinfo": "upload=1"})


if __name__ == "__main__":
    unittest.main()
