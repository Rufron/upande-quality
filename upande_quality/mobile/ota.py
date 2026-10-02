"""OTA update manifest proxy for the mobile app (expo-updates).

Why this exists
---------------
The quality app ships its JavaScript bundles as expo-updates assets published to GitHub
Pages. The expo-updates Android client, however, REQUIRES the manifest response
to carry an ``expo-protocol-version: 1`` header: ``UpdateFactory.kt`` throws
"Legacy manifests are no longer supported" when it is missing, and the client
falls back to the bundle baked into the APK. GitHub Pages serves static files
only and cannot set response headers.

So the Frappe site serves the manifest. `manifest` fetches the published
``<base>/<platform>/<runtime>/manifest.json`` (cached for 60 s per platform +
runtime) and returns it verbatim with the protocol headers the client demands.
The bundle and asset URLs inside the manifest still point at GitHub Pages, so
the site only ever proxies the small manifest document.

Site config: ``quality_ota_base_url`` overrides the published root (default: the
GitHub Pages ``ota`` folder).

Failure policy: a fleet of phones polls this on every launch, so it never
answers 500. Upstream 404 (nothing published for that runtime) is the
protocol's "no update" — a 204 with the protocol header. Any other upstream
failure is also a 204, logged once per minute per runtime.
"""

import re

import frappe
from werkzeug.wrappers import Response

DEFAULT_BASE_URL = "https://mark-judah.github.io/upande-quality/ota"
CACHE_SECONDS = 60
FETCH_TIMEOUT = 10

# An expo-updates manifest is a few kB of JSON. Anything larger is not one, and
# caching it would put an arbitrary upstream document into this site's redis.
MAX_MANIFEST_BYTES = 512 * 1024

RUNTIME_RE = re.compile(r"^\d+\.\d+$")
PLATFORMS = ("android", "ios")

PROTOCOL_HEADERS = {
	"expo-protocol-version": "1",
	"expo-sfv-version": "0",
	"cache-control": "private, max-age=0, no-store",
}


def _base_url():
	return (frappe.conf.get("quality_ota_base_url") or DEFAULT_BASE_URL).rstrip("/")


def _cache():
	# frappe.cache is a callable on v15 and the wrapper instance on v16+.
	cache = frappe.cache
	return cache() if callable(cache) else cache


def _header(name, fallback=None):
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) if request is not None else None
	value = None
	if headers is not None:
		try:
			value = headers.get(name)
		except Exception:
			value = None
	return (value or fallback or "").strip()


def fetch_upstream(url):
	"""(status_code, body_text) for the published manifest. Split out so tests
	can replace the network call."""
	import requests

	response = requests.get(url, timeout=FETCH_TIMEOUT, headers={"Accept": "application/json"})
	return response.status_code, response.text


def _no_update():
	response = Response(status=204)
	for key, value in PROTOCOL_HEADERS.items():
		response.headers[key] = value
	return response


def _log_once(key, title, message):
	"""One Error Log per minute per runtime, not one per phone."""
	try:
		cache = _cache()
		flag = f"quality_ota_error:{key}"
		if cache.get_value(flag):
			return
		cache.set_value(flag, 1, expires_in_sec=CACHE_SECONDS)
		frappe.log_error(title=title, message=message)
		# This runs inside a guest GET, and Frappe rolls a GET back unless told
		# otherwise — so the redis flag would land while the Error Log did not,
		# and a day-long Pages outage would leave no trace at all.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit, whitelisted-side-effect-on-get -- keeps the Error Log of a guest GET
	except Exception:
		# Logging the failure must not become the failure.
		pass


@frappe.whitelist(allow_guest=True, methods=["GET"])
def manifest(runtime: str | None = None, platform: str | None = None):
	"""Serve the expo-updates manifest for the caller's runtime version.

	Reads ``expo-runtime-version`` and ``expo-platform`` from the request
	headers (as the expo-updates client sends them); ``runtime`` / ``platform``
	query parameters are accepted as fallbacks for manual checks.

	Guest is allowed on purpose and on this method only: expo-updates fetches
	the manifest before the app has a session, and the response is a public
	build artefact.

	The whole body is guarded: this is the one endpoint a fleet of phones calls
	unattended on every launch, and anything it could raise — a cache that is
	down, a manifest that is not text — has to come back as "no update", not a
	500 the client reports as a broken app.
	"""
	try:
		return _manifest(runtime, platform)
	except Exception:
		_log_once("manifest", "Quality OTA manifest failed", frappe.get_traceback())
		return _no_update()


def _manifest(runtime, platform):
	runtime = _header("expo-runtime-version", runtime)
	platform = _header("expo-platform", platform).lower() or "android"
	if not RUNTIME_RE.match(runtime) or platform not in PLATFORMS:
		return _no_update()

	key = f"{platform}:{runtime}"
	cache = _cache()
	cache_key = f"quality_ota_manifest:{key}"
	cached = cache.get_value(cache_key)
	if not isinstance(cached, dict):
		url = f"{_base_url()}/{platform}/{runtime}/manifest.json"
		try:
			status, body = fetch_upstream(url)
		except Exception:
			_log_once(key, "Quality OTA manifest fetch failed", frappe.get_traceback() + f"\n\nURL: {url}")
			return _no_update()
		if status == 200 and len(body or "") > MAX_MANIFEST_BYTES:
			_log_once(key, "Quality OTA manifest too large", f"{len(body)} bytes from {url}")
			return _no_update()
		if status == 200:
			cached = {"status": 200, "body": body}
		elif status == 404:
			cached = {"status": 404, "body": ""}
		else:
			_log_once(
				key, "Quality OTA manifest upstream error", f"HTTP {status} from {url}\n\n{body[:2000]}"
			)
			return _no_update()
		cache.set_value(cache_key, cached, expires_in_sec=CACHE_SECONDS)

	if cached["status"] != 200:
		return _no_update()

	response = Response(cached["body"], status=200, content_type="application/json; charset=utf-8")
	for name, value in PROTOCOL_HEADERS.items():
		response.headers[name] = value
	return response
