"""Device-specific Mihomo subscriptions, served behind an HTTPS reverse proxy."""

import copy
import hmac
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

MAX_BODY = 5 * 1024 * 1024
LOG = logging.getLogger(__name__)


def parse_profile(body):
    profile = yaml.safe_load(body)
    if not isinstance(profile, dict):
        raise ValueError("Expected a YAML profile")
    proxies = profile.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Expected a non-empty proxies list")
    names = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            raise ValueError("Invalid proxy")
        name = proxy.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Missing or duplicate proxy name")
        if not isinstance(proxy.get("type"), str):
            raise ValueError("Missing proxy type")
        names.add(name)
    if not isinstance(profile.get("proxy-groups"), list) or not isinstance(
        profile.get("rules"), list
    ):
        raise ValueError("Expected a complete Clash subscription")
    return profile


def load_mixin(path):
    mixin = yaml.safe_load(Path(path).read_bytes())
    if not isinstance(mixin, dict):
        raise ValueError("Expected a YAML mixin mapping")
    return mixin


def merge_profile(base, overlay):
    """Recursively merge mappings; replace lists and scalar values."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_profile(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def render_common(common, upstream, subscription, router=None):
    """Compose local policy and mixins, consuming only upstream nodes."""
    result = merge_profile(common, subscription)
    if router is not None:
        result = merge_profile(result, router)
    provider = result["proxy-providers"]["Airport"]
    for key in ("url", "path", "interval", "proxy", "header", "size-limit"):
        provider.pop(key, None)
    provider["type"] = "inline"
    provider["payload"] = copy.deepcopy(upstream["proxies"])
    # YAML helpers are already expanded by safe_load.
    for key in list(result):
        if isinstance(key, str) and key.startswith("x-"):
            del result[key]
    return yaml.safe_dump(result, allow_unicode=True, sort_keys=False).encode("utf-8")


def fetch_upstream(url, user_agent, timeout):
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/yaml"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200 or urlsplit(response.url).scheme != "https":
            raise ValueError("Unexpected upstream response")
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("Upstream response too large")
        # Preserve quota metadata, but do not forward arbitrary upstream headers.
        headers = {}
        value = response.headers.get("subscription-userinfo")
        if value and value.isascii() and not any(ord(c) < 32 or ord(c) == 127 for c in value):
            headers["subscription-userinfo"] = value
        return body, headers


class SubscriptionCache:
    def __init__(self, settings, fetch=fetch_upstream, clock=time.monotonic):
        self.settings = settings
        self.fetch = fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.value = None
        self.updated = 0
        self.next_attempt = 0

    def get(self):
        with self.lock:
            now = self.clock()
            if now >= self.next_attempt:
                try:
                    body, headers = self.fetch(
                        self.settings["upstream_url"],
                        self.settings["upstream_user_agent"],
                        self.settings["timeout"],
                    )
                    parsed = parse_profile(body)
                    self.value = (body, headers, parsed)
                    self.updated = self.clock()
                    self.next_attempt = self.updated + self.settings["cache_ttl"]
                except Exception:
                    # Exception messages may contain the private subscription URL.
                    LOG.warning("Upstream subscription unavailable; retry delayed")
                    self.next_attempt = self.clock() + self.settings["retry_interval"]
            age = self.clock() - self.updated
            if self.value is None or age > self.settings["cache_ttl"] + self.settings["max_stale"]:
                raise RuntimeError("Subscription unavailable")
            return self.value, age >= self.settings["cache_ttl"]


def load_settings(path):
    path = Path(path).resolve()
    settings = yaml.safe_load(path.read_bytes())
    common_path = (path.parent / settings.get("common", "../MySubscription-Common.yaml")).resolve()
    settings["common"] = common_path
    parse_profile(common_path.read_bytes())
    for key, filename in (("subscription_mixin", "Subscription-Mixin.yaml"),
                          ("router_mixin", "Nikki-Mixin.yaml")):
        settings[key] = (path.parent / settings.get(key, f"../{filename}")).resolve()
        load_mixin(settings[key])
    subscription = load_mixin(settings["subscription_mixin"])
    url = settings.get("upstream_url") or subscription["proxy-providers"]["Airport"]["url"]
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username:
        raise ValueError("upstream_url must be an HTTPS URL without userinfo")
    settings["upstream_url"] = url
    settings.setdefault("upstream_user_agent", "clash.meta")
    for key, default in (("cache_ttl", 300), ("max_stale", 86400), ("retry_interval", 60), ("timeout", 15)):
        settings.setdefault(key, default)
        if type(settings[key]) is not int or settings[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    devices = settings.get("devices")
    if not isinstance(devices, list) or not devices:
        raise ValueError("At least one device is required")
    tokens = set()
    for device in devices:
        token = device.get("token", "")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) or token.startswith("REPLACE_"):
            raise ValueError("Each device needs a random URL-safe token of 32–128 characters")
        if token in tokens or device.get("type") not in {"desktop", "mobile", "router"}:
            raise ValueError("Duplicate token or invalid device type")
        tokens.add(token)
    return settings


class Application:
    def __init__(self, settings, fetch=fetch_upstream, clock=time.monotonic):
        self.settings = settings
        self.cache = SubscriptionCache(settings, fetch, clock)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        headers = [("Cache-Control", "private, no-store"), ("X-Content-Type-Options", "nosniff")]
        status, body = "404 Not Found", b"Not found\n"
        match = re.fullmatch(r"/sub/([A-Za-z0-9_-]{32,128})/config\.yaml", environ.get("PATH_INFO", ""))
        device_type = None
        if match:
            for device in self.settings["devices"]:
                if hmac.compare_digest(match[1], device["token"]):
                    device_type = device["type"]
        if device_type:
            if method not in {"GET", "HEAD"}:
                status, body = "405 Method Not Allowed", b"Method not allowed\n"
                headers.append(("Allow", "GET, HEAD"))
            else:
                try:
                    (raw, upstream_headers, profile), stale = self.cache.get()
                    if device_type == "mobile":
                        body = raw
                    else:
                        common = parse_profile(self.settings["common"].read_bytes())
                        subscription = load_mixin(self.settings["subscription_mixin"])
                        router = load_mixin(self.settings["router_mixin"]) if device_type == "router" else None
                        body = render_common(common, profile, subscription, router)
                    status = "200 OK"
                    headers.extend(upstream_headers.items())
                    headers.extend([
                        ("Content-Type", "application/yaml; charset=utf-8"),
                        ("Content-Disposition", 'attachment; filename="config.yaml"'),
                        ("X-Subscription-Cache", "stale" if stale else "fresh"),
                    ])
                except Exception:
                    LOG.warning("Configuration unavailable")
                    status, body = "503 Service Unavailable", b"Configuration temporarily unavailable\n"
                    headers.append(("Retry-After", str(self.settings["retry_interval"])))
        if status != "200 OK":
            headers.append(("Content-Type", "text/plain; charset=utf-8"))
        headers.append(("Content-Length", str(len(body))))
        start_response(status, headers)
        return [b"" if method == "HEAD" else body]


def create_app():
    return Application(load_settings(os.environ.get("CLASH_SETTINGS", "settings.yaml")))
