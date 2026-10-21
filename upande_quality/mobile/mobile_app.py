# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Device / app-version register shared by every Upande mobile app.
# Ported from upande_sensors' install register, with:
#
#   * one register for all apps; each app is a `Mobile App` record (adding an
#     app is a new record, no code change), and `Mobile App Install.app` links
#     to it;
#   * the LATEST published version per app, refreshed hourly by
#     `refresh_latest` from the app's OTA manifest on GitHub Pages and its
#     GitHub Releases (APK), stored on the `Mobile App` record;
#   * a status per install: current / behind_ota / needs_apk / ahead /
#     dormant / unknown, plus `update_to`.
#
# Status is always the device's LATEST report compared against the app's
# CURRENT latest published release. It is never derived from history (old
# rows, the fleet's highest version): when the latest is not known, or the
# app is not a known, enabled Mobile App, the status is `unknown`.

import re

import frappe
import requests
from frappe import _
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	convert_utc_to_system_timezone,
	cstr,
	get_datetime,
	now,
	now_datetime,
	today,
)

MOBILE_APP = "Mobile App"
INSTALL = "Mobile App Install"
INSTALL_USER = "Mobile App Install User"

# Release line 2 (scripts/version.mjs in both apps): runtime = (100 + major).minor.
RUNTIME_MAJOR_BASE = 100
# Where runtime discovery starts when nothing has been found yet: release
# line 2 first (every current app), then a plain 1.0.
START_RUNTIMES = ("101.0", "1.0")
MAX_RUNTIME_PROBES = 10

STATUSES = ("current", "behind_ota", "needs_apk", "ahead", "dormant", "unknown")
# Report order: who must act first.
STATUS_ORDER = {"needs_apk": 0, "behind_ota": 1, "unknown": 2, "ahead": 3, "current": 4, "dormant": 5}
DEFAULT_DORMANT_DAYS = 7

INSTALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
APP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,63}$")
INSTALL_PLATFORMS = ("android", "ios", "web")
# A same-account launch inside this window only refreshes last_seen — unless
# the version changed, which is always recorded.
INSTALL_THROTTLE_MINUTES = 5
MAX_DATA_LENGTH = 140
MAX_PAGE_LENGTH = 500
HTTP_TIMEOUT = 15
HTTP_HEADERS = {"User-Agent": "upande-packhouse-version-check", "Accept": "application/json"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _text(value, default=""):
	if value is None:
		return default
	value = cstr(value).strip()
	return value or default


def _clip(value, length=MAX_DATA_LENGTH):
	return _text(value)[:length]


def _flag(value):
	"""A checkbox from a client value; "true"/"false" arrive as strings on a form body."""
	text = cstr(value).strip().lower()
	if text in ("true", "false"):
		return 1 if text == "true" else 0
	return 1 if cint(value) else 0


def _ts(value):
	return str(get_datetime(value).replace(microsecond=0)) if value else ""


def _observed_ip():
	"""The caller's address as the server saw it — never one from the request body."""
	ip = _text(getattr(frappe.local, "request_ip", None))
	if ip:
		return ip[:MAX_DATA_LENGTH]
	request = getattr(frappe.local, "request", None)
	forwarded = ""
	if request is not None:
		try:
			forwarded = _text(request.headers.get("X-Forwarded-For"))
		except Exception:
			forwarded = ""
	return forwarded.split(",")[0].strip()[:MAX_DATA_LENGTH]


def _require_manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("The app install register is visible to System Managers only."), frappe.PermissionError
		)


def parse_version(value):
	"""(major, minor, patch) from "1.2.3" / "v1.2.3-rc1"; None when unparseable."""
	text = _text(value).lstrip("vV").split("-")[0].split("+")[0]
	parts = text.split(".")
	if not parts or not parts[0].isdigit():
		return None
	nums = []
	for part in parts[:3]:
		if not part.isdigit():
			return None
		nums.append(int(part))
	while len(nums) < 3:
		nums.append(0)
	return tuple(nums)


def parse_runtime(value):
	"""(major, minor) from a runtime like "101.1"; None when unparseable."""
	parts = _text(value).split(".")
	if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
		return None
	return (int(parts[0]), int(parts[1]))


def runtime_of(version):
	"""The runtime a release-line-2 version belongs to: 1.1.20 -> "101.1"."""
	parsed = parse_version(version)
	if not parsed:
		return ""
	return f"{RUNTIME_MAJOR_BASE + parsed[0]}.{parsed[1]}"


def _cmp(a, b):
	return (a > b) - (a < b)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


APP_FIELDS = (
	"name",
	"enabled",
	"dormant_days",
	"latest_version",
	"latest_runtime",
	"latest_apk_version",
	"latest_apk_url",
	"checked_at",
	"published_at",
)


def apps_by_name():
	"""{app name: Mobile App row} for every ENABLED app."""
	rows = frappe.get_all(MOBILE_APP, filters={"enabled": 1}, fields=list(APP_FIELDS))
	return {r.name: r for r in rows}


def _context(apps, app):
	"""(the app's row or None, its dormant days) — None makes the status unknown."""
	row = apps.get(app)
	if not row:
		return None, DEFAULT_DORMANT_DAYS
	days = DEFAULT_DORMANT_DAYS if row.dormant_days is None else cint(row.dormant_days)
	return row, days


def latest_by_app():
	"""{app: row} for enabled apps whose latest published version is known."""
	return {name: r for name, r in apps_by_name().items() if parse_version(r.latest_version)}


MAX_UNKNOWN_APPS = 20


def _ensure_app(app):
	"""The Mobile App for a name a client sent; an unknown name is created DISABLED.

	So an install of an app nobody has set up yet is still recorded (status
	unknown) and shows up on the desk, where enabling it and filling in its
	repo / OTA URL starts version checks — no code change.
	"""
	stored = frappe.db.get_value(MOBILE_APP, app, "name")
	if stored:
		return stored  # the record's own spelling: names match case-insensitively
	# A cap on what any signed-in account can create: past it an unknown name is refused.
	if frappe.db.count(MOBILE_APP, {"enabled": 0}) >= MAX_UNKNOWN_APPS:
		return None
	frappe.get_doc({"doctype": MOBILE_APP, "app_name": app, "enabled": 0}).insert(ignore_permissions=True)
	return app


def install_status(app_version, runtime_version, last_seen, latest, dormant_days, now_dt=None):
	"""(status, update_to) for one device's latest report against the app's current latest.

	`latest` is that app's Mobile App row (or None). Nothing about
	the device's history or the rest of the fleet goes into this.
	"""
	now_dt = now_dt or now_datetime()
	if dormant_days > 0 and last_seen and get_datetime(last_seen) < add_days(now_dt, -dormant_days):
		return "dormant", ""

	installed = parse_version(app_version)
	latest_version = parse_version(latest.get("latest_version")) if latest else None
	if not installed or not latest_version:
		return "unknown", ""

	latest_str = _text(latest.get("latest_version"))
	installed_rt = parse_runtime(runtime_version) or parse_runtime(runtime_of(app_version))
	latest_rt_str = _text(latest.get("latest_runtime")) or runtime_of(latest_str)
	latest_rt = parse_runtime(latest_rt_str)
	if not installed_rt or not latest_rt:
		return "unknown", ""

	runtime_cmp = _cmp(installed_rt, latest_rt)
	if runtime_cmp < 0:
		apk = _text(latest.get("latest_apk_version"))
		if apk and parse_runtime(runtime_of(apk)) == latest_rt:
			if parse_version(apk) < latest_version:
				return "needs_apk", f"APK {apk}, then {latest_str} over the air"
			return "needs_apk", f"APK {apk}"
		return "needs_apk", f"APK for runtime {latest_rt_str} (not published)"
	if runtime_cmp > 0:
		return "ahead", ""

	version_cmp = _cmp(installed, latest_version)
	if version_cmp < 0:
		return "behind_ota", f"{latest_str} (reopen the app)"
	if version_cmp > 0:
		return "ahead", ""
	return "current", ""


# --------------------------------------------------------------------------- #
# register_install
# --------------------------------------------------------------------------- #


def _install_user_row(install_id, user):
	return frappe.db.get_value(
		INSTALL_USER,
		{"parent": install_id, "parenttype": INSTALL, "user": user},
		["name", "app_version", "runtime_version", "logins", "launches"],
		as_dict=True,
	)


def _touch_install_user(install_id, app_version, runtime_version, platform, now_str, is_login, counted):
	"""Upsert the (install, account) pairing; returns that account's login count."""
	user = frappe.session.user
	row = _install_user_row(install_id, user)
	if row:
		values = {"last_seen": now_str, "platform": platform, "runtime_version": runtime_version}
		stored = _text(row.get("app_version"))
		if app_version and app_version != stored:
			values["app_version"] = app_version
			if stored:
				values["previous_app_version"] = stored
		logins = cint(row.get("logins"))
		if is_login:
			logins += 1
			values["logins"] = logins
		if counted:
			values["launches"] = cint(row.get("launches")) + 1
		frappe.db.set_value(INSTALL_USER, row["name"], values, update_modified=True)
		return logins

	idx = cint(
		frappe.db.sql(
			"SELECT MAX(idx) FROM `tabMobile App Install User` WHERE parent = %s AND parenttype = %s",
			(install_id, INSTALL),
		)[0][0]
	)
	child = frappe.get_doc(
		{
			"doctype": INSTALL_USER,
			"parent": install_id,
			"parenttype": INSTALL,
			"parentfield": "users",
			"idx": idx + 1,
			"user": user,
			"app_version": app_version,
			"runtime_version": runtime_version,
			"platform": platform,
			"first_seen": now_str,
			"last_seen": now_str,
			"logins": 1 if is_login else 0,
			"launches": 1,
		}
	)
	child.insert(ignore_permissions=True)
	return cint(child.logins)


@frappe.whitelist(methods=["POST"])
def register_install(
	install_id: str | None = None,
	app: str | None = None,
	platform: str | None = None,
	device_brand: str | None = None,
	device_model: str | None = None,
	device_name: str | None = None,
	os_version: str | None = None,
	app_version: str | None = None,
	runtime_version: str | None = None,
	is_physical_device: str | int | bool | None = None,
	reason: str | None = None,
	login: str | int | bool | None = None,
):
	"""Record this device in the register, or refresh its row.

	`install_id` is a random id the app keeps in its own storage; it is the
	docname. The parent row is the device and its LATEST report (who reported
	last, on which version); the `users` child keeps one row per account that
	has used it.

	A launch by the same account inside `INSTALL_THROTTLE_MINUTES` only moves
	`last_seen` — except when the app or runtime version changed, which is
	always written, so a version applied over the air shows at once. A login,
	or a different account, is never throttled.

	`reason` is ``login``, ``launch`` (default) or ``foreground``. A foreground
	report (the app returning to the screen) only keeps `last_seen` fresh on
	scanners that stay open all day; it counts as a launch only when the
	version or the account changed.

	Upserted with ignore_permissions: any signed-in account may report its own
	device, and the row is filed under the session's user, never one the client
	claims.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required."), frappe.AuthenticationError)

	install_id = _text(install_id)
	if not INSTALL_ID_PATTERN.match(install_id):
		frappe.throw(_("A valid install id is required (8-64 characters of A-Z, a-z, 0-9, _ or -)."))
	app = _text(app)
	if not APP_NAME_PATTERN.match(app):
		frappe.throw(_("A valid app name is required."))
	app = _ensure_app(app)
	if not app:
		frappe.throw(_("Unknown app."))

	platform = _text(platform, "android").lower()
	if platform not in INSTALL_PLATFORMS:
		platform = "android"
	app_version = _clip(app_version)
	runtime_version = _clip(runtime_version) or runtime_of(app_version)
	is_login = _flag(login) == 1 or _text(reason).lower() == "login"
	is_foreground = not is_login and _text(reason).lower() == "foreground"
	user = frappe.session.user
	now_str = now()

	latest, dormant_days = _context(apps_by_name(), app)
	status, update_to = install_status(app_version, runtime_version, now_str, latest, dormant_days)

	existing = frappe.db.get_value(
		INSTALL,
		install_id,
		[
			"name",
			"app",
			"user",
			"app_version",
			"runtime_version",
			"first_seen",
			"last_seen",
			"launches",
			"upgrades",
		],
		as_dict=True,
	)

	if existing:
		stored_version = _text(existing.get("app_version"))
		version_changed = bool(app_version) and app_version != stored_version
		runtime_changed = bool(runtime_version) and runtime_version != _text(existing.get("runtime_version"))
		last_seen = existing.get("last_seen")
		throttled = (
			not is_login
			and not version_changed
			and not runtime_changed
			and _text(existing.get("user")) == user
			and (
				is_foreground
				or (
					last_seen
					and get_datetime(last_seen)
					> add_to_date(now_datetime(), minutes=-INSTALL_THROTTLE_MINUTES)
				)
			)
		)
		if throttled:
			frappe.db.set_value(
				INSTALL,
				existing["name"],
				{"last_seen": now_str, "status": status, "update_to": update_to},
				update_modified=False,
			)
			pairing = _install_user_row(install_id, user) or {}
			return {
				"install_id": install_id,
				"user": user,
				"status": status,
				"update_to": update_to,
				"launches": cint(existing.get("launches")),
				"logins": cint(pairing.get("logins")),
				"tracked": True,
			}

		launches = cint(existing.get("launches")) + 1
		values = {
			"app": app,
			"user": user,
			"platform": platform,
			"device_brand": _clip(device_brand),
			"device_model": _clip(device_model),
			"device_name": _clip(device_name),
			"os_version": _clip(os_version),
			"app_version": app_version,
			"runtime_version": runtime_version,
			"is_physical_device": _flag(is_physical_device),
			"ip_address": _observed_ip(),
			"last_seen": now_str,
			"launches": launches,
			"status": status,
			"update_to": update_to,
		}
		if version_changed and stored_version:
			values["previous_app_version"] = stored_version
			values["version_changed_at"] = now_str
			values["upgrades"] = cint(existing.get("upgrades")) + 1
		frappe.db.set_value(INSTALL, existing["name"], values, update_modified=True)
		logins = _touch_install_user(
			install_id, app_version, runtime_version, platform, now_str, is_login, counted=True
		)
		return {
			"install_id": install_id,
			"user": user,
			"status": status,
			"update_to": update_to,
			"launches": launches,
			"logins": logins,
			"tracked": True,
		}

	ip = _observed_ip()
	frappe.get_doc(
		{
			"doctype": INSTALL,
			"install_id": install_id,
			"app": app,
			"user": user,
			"platform": platform,
			"device_brand": _clip(device_brand),
			"device_model": _clip(device_model),
			"device_name": _clip(device_name),
			"os_version": _clip(os_version),
			"app_version": app_version,
			"runtime_version": runtime_version,
			"is_physical_device": _flag(is_physical_device),
			"ip_address": ip,
			"first_ip": ip,
			"first_seen": now_str,
			"last_seen": now_str,
			"launches": 1,
			"status": status,
			"update_to": update_to,
		}
	).insert(ignore_permissions=True)
	logins = _touch_install_user(
		install_id, app_version, runtime_version, platform, now_str, is_login, counted=False
	)
	return {
		"install_id": install_id,
		"user": user,
		"status": status,
		"update_to": update_to,
		"launches": 1,
		"logins": logins,
		"tracked": True,
	}


# --------------------------------------------------------------------------- #
# installs listing (shared with the Mobile App Installs report)
# --------------------------------------------------------------------------- #


def install_rows(app=None, status=None, user=None, since_days=None, include_dormant=False):
	"""One row per device: its latest report, its status against the current latest.

	Sorted with who must act first (needs_apk, behind_ota) on top, then newest
	last_seen. Dormant rows are left out unless asked for (or filtered on).
	"""
	conditions, params = ["1 = 1"], []
	if app:
		conditions.append("i.app = %s")
		params.append(app)
	if user:
		conditions.append("i.user = %s")
		params.append(user)
	since_days = cint(since_days)
	if since_days > 0:
		conditions.append("i.last_seen >= %s")
		params.append(add_days(today(), -since_days) + " 00:00:00")

	rows = frappe.db.sql(
		"""
		SELECT i.install_id, i.app, i.user, u.full_name, i.platform, i.device_brand, i.device_model,
			i.device_name, i.os_version, i.app_version, i.runtime_version, i.previous_app_version,
			i.version_changed_at, i.is_physical_device, i.ip_address, i.first_seen, i.last_seen,
			i.launches, COALESCE(p.logins, 0) AS logins
		FROM `tabMobile App Install` i
		LEFT JOIN `tabUser` u ON u.name = i.user
		LEFT JOIN `tabMobile App Install User` p
			ON p.parent = i.name AND p.parenttype = 'Mobile App Install' AND p.user = i.user
		WHERE """
		+ " AND ".join(conditions),
		params,
		as_dict=True,
	)

	apps = apps_by_name()
	now_dt = now_datetime()
	out = []
	for row in rows:
		latest, dormant_days = _context(apps, row.app)
		row_status, update_to = install_status(
			row.app_version, row.runtime_version, row.last_seen, latest, dormant_days, now_dt
		)
		if status and row_status != status:
			continue
		if row_status == "dormant" and not include_dormant and status != "dormant":
			continue
		row["status"] = row_status
		row["update_to"] = update_to
		row["full_name"] = row.get("full_name") or ""
		row["launches"] = cint(row.get("launches"))
		row["logins"] = cint(row.get("logins"))
		row["is_physical_device"] = bool(cint(row.get("is_physical_device")))
		out.append(row)

	out.sort(key=lambda r: r["install_id"])
	out.sort(
		key=lambda r: get_datetime(r["last_seen"]) if r.get("last_seen") else get_datetime("1900-01-01"),
		reverse=True,
	)
	out.sort(key=lambda r: STATUS_ORDER.get(r["status"], 9))
	return out


@frappe.whitelist(methods=["GET"])
def installs(
	app: str | None = None,
	status: str | None = None,
	user: str | None = None,
	since_days: int | str | None = None,
	include_dormant: int | str | bool | None = None,
	start: int | str | None = None,
	page_length: int | str | None = None,
):
	"""The register for System Managers: each device's latest report, status and update_to."""
	_require_manager()
	start = max(cint(start), 0)
	page_length = min(cint(page_length) or 100, MAX_PAGE_LENGTH)
	rows = install_rows(
		app=_text(app) or None,
		status=_text(status) or None,
		user=_text(user) or None,
		since_days=since_days,
		include_dormant=bool(_flag(include_dormant)),
	)
	by_status = {}
	for row in rows:
		by_status[row["status"]] = by_status.get(row["status"], 0) + 1
	latest = latest_by_app()
	return {
		"rows": [
			{
				**row,
				"version_changed_at": _ts(row.get("version_changed_at")),
				"first_seen": _ts(row.get("first_seen")),
				"last_seen": _ts(row.get("last_seen")),
			}
			for row in rows[start : start + page_length]
		],
		"total": len(rows),
		"start": start,
		"page_length": page_length,
		"by_status": by_status,
		"latest": {
			app_name: {
				"latest_version": r.latest_version,
				"latest_runtime": r.latest_runtime,
				"latest_apk_version": r.latest_apk_version or "",
				"checked_at": _ts(r.checked_at),
			}
			for app_name, r in latest.items()
		},
		"server_time": now(),
	}


# --------------------------------------------------------------------------- #
# latest published versions (hourly)
# --------------------------------------------------------------------------- #


def _get_json(url):
	response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
	if response.status_code == 404:
		return None
	response.raise_for_status()
	return response.json()


def _manifest_url(source, runtime):
	base = _text(source.get("ota_base_url")).rstrip("/")
	platform = _text(source.get("platform"), "android")
	return f"{base}/{platform}/{runtime}/manifest.json"


def _fetch_manifest(source, runtime):
	"""The published OTA manifest for one runtime, or None when that runtime has none."""
	data = _get_json(_manifest_url(source, runtime))
	if not isinstance(data, dict):
		return None
	extra = data.get("extra") or {}
	version = _text((extra.get("expoClient") or {}).get("version")) or _text(extra.get("appVersion"))
	if not parse_version(version):
		return None
	return {
		"version": version,
		"runtime": _text(data.get("runtimeVersion"), runtime),
		"created_at": _text(data.get("createdAt")),
	}


def _next_runtimes(runtime):
	major, minor = parse_runtime(runtime)
	return [f"{major}.{minor + 1}", f"{major + 1}.0"]


def latest_ota(source, start_runtime):
	"""Newest published OTA: walk forward from `start_runtime` while a newer runtime has a manifest.

	Only GitHub Pages is read (no API rate limit). A runtime moves with a new
	minor/major release, so the newer candidates of x.y are x.(y+1) and (x+1).0.
	"""
	found = None
	starts = [start_runtime] if parse_runtime(start_runtime) else []
	for runtime in starts + [r for r in START_RUNTIMES if r != start_runtime]:
		found = _fetch_manifest(source, runtime)
		if found:
			break
	if not found:
		return None
	for _probe in range(MAX_RUNTIME_PROBES):
		newer = None
		for candidate in _next_runtimes(found["runtime"]):
			newer = _fetch_manifest(source, candidate)
			if newer:
				break
		if not newer:
			break
		found = newer
	return found


def latest_apk(source):
	"""(version, download_url) of the newest non-legacy GitHub Release with an .apk, or (None, None)."""
	repo = _text(source.get("github_repo"))
	if not repo:
		return None, None
	headers = dict(HTTP_HEADERS, Accept="application/vnd.github+json")
	token = frappe.conf.get("mobile_app_github_token")
	if token:
		headers["Authorization"] = f"Bearer {token}"
	response = requests.get(
		f"https://api.github.com/repos/{repo}/releases?per_page=100", headers=headers, timeout=HTTP_TIMEOUT
	)
	response.raise_for_status()
	best = None
	for release in response.json() or []:
		tag = _text(release.get("tag_name"))
		if release.get("draft") or release.get("prerelease") or tag.lower().startswith("legacy"):
			continue
		version = parse_version(tag)
		if not version:
			continue
		apks = [a for a in release.get("assets") or [] if _text(a.get("name")).lower().endswith(".apk")]
		if not apks:
			continue
		# Prefer the asset named for its version (upande_quality_v1.1.0.apk) over an alias.
		apks.sort(key=lambda a: tag.lstrip("vV") not in _text(a.get("name")))
		if best is None or version > best[0]:
			best = (version, tag.lstrip("vV"), _text(apks[0].get("browser_download_url")))
	if not best:
		return None, None
	return best[1], best[2]


def _iso_to_system(value):
	if not value:
		return None
	try:
		dt = get_datetime(value.replace("Z", "+00:00"))
		if dt.tzinfo is not None:
			dt = convert_utc_to_system_timezone(dt).replace(tzinfo=None)
		return dt.replace(microsecond=0)
	except Exception:
		return None


def _refresh_app(doc):
	"""Fetch one app's latest onto its Mobile App record. Errors are kept on the record, never raised.

	When no OTA manifest can be read, the stored latest is cleared, so a stale
	value can't keep being reported as the latest: status falls back to `unknown`.
	"""
	errors = []
	source = doc.as_dict()
	try:
		ota = latest_ota(source, _text(doc.latest_runtime)) if _text(doc.ota_base_url) else None
		if not ota:
			errors.append(
				"OTA manifest: none found under " + (_text(doc.ota_base_url) or "(no OTA Base URL)")
			)
	except Exception as exc:
		ota = None
		errors.append(f"OTA manifest: {exc}")
	if ota:
		doc.latest_version = ota["version"]
		doc.latest_runtime = ota["runtime"]
		doc.published_at = _iso_to_system(ota["created_at"])
	else:
		doc.latest_version = None
		doc.latest_runtime = None
		doc.published_at = None
	try:
		apk_version, apk_url = latest_apk(source)
		doc.latest_apk_version = apk_version
		doc.latest_apk_url = apk_url
	except Exception as exc:
		# The APK only names what to install; keep the last one seen rather than
		# losing it to a GitHub rate limit.
		errors.append(f"GitHub releases: {exc}")
	doc.checked_at = now()
	doc.last_error = "\n".join(errors)[:1000] or None


def recompute_statuses():
	"""Write each install's status/update_to against the latest now on the Single."""
	apps = apps_by_name()
	now_dt = now_datetime()
	rows = frappe.get_all(
		INSTALL,
		fields=["name", "app", "app_version", "runtime_version", "last_seen", "status", "update_to"],
		limit_page_length=0,
	)
	changed = 0
	for row in rows:
		latest, dormant_days = _context(apps, row.app)
		status, update_to = install_status(
			row.app_version, row.runtime_version, row.last_seen, latest, dormant_days, now_dt
		)
		if status != row.status or update_to != _text(row.update_to):
			frappe.db.set_value(
				INSTALL, row.name, {"status": status, "update_to": update_to}, update_modified=False
			)
			changed += 1
	return changed


def refresh_latest():
	"""Hourly: read each enabled app's latest OTA + APK, store it, re-rate every install. Commits itself."""
	for name in frappe.get_all(MOBILE_APP, filters={"enabled": 1}, pluck="name"):
		try:
			doc = frappe.get_doc(MOBILE_APP, name)
			_refresh_app(doc)
			doc.flags.ignore_permissions = True
			doc.save()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Mobile App latest check failed: {name}", message=frappe.get_traceback())
	try:
		recompute_statuses()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Mobile App Install status refresh failed", message=frappe.get_traceback())


@frappe.whitelist(methods=["POST"])
def refresh_now():
	"""Desk button: run the hourly check now."""
	_require_manager()
	# Many slow manifest probes can outlast a web request: the check runs in the background.
	frappe.enqueue(
		"upande_quality.mobile.mobile_app.refresh_latest",
		queue="long",
		job_id="mobile_app_refresh_latest",
		deduplicate=True,
	)
	return latest_by_app()
