# Copyright (c) 2026, Upande and contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime

from upande_quality.mobile import mobile_app as devices

INSTALL_A = "_test-install-aaaa1111"
INSTALL_B = "_test-install-bbbb2222"
INSTALL_C = "_test-install-cccc3333"
INSTALLS = [INSTALL_A, INSTALL_B, INSTALL_C]
# Two Android IDs of identical scanners.
KEY_1 = "a1b2c3d4e5f60718"
KEY_2 = "0f1e2d3c4b5a6978"
USER_A = "mobile-install-a@example.com"
USER_B = "mobile-install-b@example.com"
# Test apps of their own, so the seeded Packhouse / Quality records are never touched.
PACKHOUSE = "Test Packhouse App"
QUALITY = "Test Quality App"
PACKHOUSE_OTA = "https://example.test/packhouse/ota"
QUALITY_OTA = "https://example.test/quality/ota"


def _ensure_user(email):
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"user_type": "System User",
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value("User", email, "user_type", "System User", update_modified=False)


def _ensure_app(app, ota_base_url, enabled=1):
	if frappe.db.exists(devices.MOBILE_APP, app):
		frappe.delete_doc(devices.MOBILE_APP, app, force=True, ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": devices.MOBILE_APP,
			"app_name": app,
			"enabled": enabled,
			"github_repo": "example/" + app.lower().replace(" ", "-"),
			"ota_base_url": ota_base_url,
			"dormant_days": 7,
		}
	).insert(ignore_permissions=True)


def _set_latest(app, version, runtime, apk=None):
	frappe.db.set_value(
		devices.MOBILE_APP,
		app,
		{
			"latest_version": version,
			"latest_runtime": runtime,
			"latest_apk_version": apk,
			"checked_at": now_datetime(),
		},
	)


def _only_test_apps(real_get_all):
	"""refresh_latest walks every enabled app; keep it to this module's two."""

	def get_all(doctype, *args, **kwargs):
		if doctype == devices.MOBILE_APP and kwargs.get("pluck") == "name":
			return [PACKHOUSE, QUALITY]
		return real_get_all(doctype, *args, **kwargs)

	return get_all


class FakeResponse:
	def __init__(self, status_code=200, data=None):
		self.status_code = status_code
		self._data = data

	def json(self):
		return self._data

	def raise_for_status(self):
		if self.status_code >= 400:
			raise Exception(f"HTTP {self.status_code}")


def _manifest(version, runtime):
	return {
		"runtimeVersion": runtime,
		"createdAt": "2026-10-21T16:54:22.191Z",
		"extra": {"expoClient": {"version": version}, "appVersion": version},
	}


class IntegrationTestMobileAppInstalls(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		_ensure_user(USER_A)
		_ensure_user(USER_B)
		frappe.db.delete(devices.INSTALL, {"name": ["in", INSTALLS]})
		frappe.db.delete(devices.INSTALL_USER, {"parent": ["in", INSTALLS]})
		_ensure_app(PACKHOUSE, PACKHOUSE_OTA)
		_ensure_app(QUALITY, QUALITY_OTA)
		self._saved_ip = getattr(frappe.local, "request_ip", None)
		frappe.local.request_ip = "41.90.64.10"

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request_ip = self._saved_ip
		frappe.db.rollback()
		super().tearDown()

	def _register(self, install_id=INSTALL_A, **kw):
		payload = {
			"install_id": install_id,
			"app": PACKHOUSE,
			"platform": "android",
			"device_brand": "Zebra",
			"device_model": "TC26",
			"os_version": "11",
			"app_version": "1.1.15",
			"runtime_version": "101.1",
			"is_physical_device": "true",
		}
		payload.update(kw)
		return devices.register_install(**payload)

	# ----------------------------------------------------------------- status

	def test_status_rules(self):
		latest = frappe._dict(latest_version="1.1.21", latest_runtime="101.1", latest_apk_version="1.1.0")
		now = now_datetime()
		self.assertEqual(devices.install_status("1.1.21", "101.1", now, latest, 7, now), ("current", ""))
		self.assertEqual(
			devices.install_status("1.1.15", "101.1", now, latest, 7, now),
			("behind_ota", "1.1.21 (reopen the app)"),
		)
		self.assertEqual(
			devices.install_status("1.0.9", "101.0", now, latest, 7, now),
			("needs_apk", "APK 1.1.0, then 1.1.21 over the air"),
		)
		# A line-1 install (runtime 1.5) needs the line-2 APK, whatever its x.y.z says.
		self.assertEqual(devices.install_status("1.5.0", "1.5", now, latest, 7, now)[0], "needs_apk")
		self.assertEqual(devices.install_status("1.1.22", "101.1", now, latest, 7, now), ("ahead", ""))
		self.assertEqual(
			devices.install_status("1.1.15", "101.1", add_days(now, -8), latest, 7, now), ("dormant", "")
		)
		# No published latest known: unknown, never a guess from the fleet.
		self.assertEqual(devices.install_status("1.1.15", "101.1", now, None, 7, now), ("unknown", ""))
		self.assertEqual(devices.install_status("", "101.1", now, latest, 7, now), ("unknown", ""))

	def test_needs_apk_when_new_runtime_has_no_apk_yet(self):
		latest = frappe._dict(latest_version="1.2.3", latest_runtime="101.2", latest_apk_version="1.1.0")
		now = now_datetime()
		status, update_to = devices.install_status("1.1.21", "101.1", now, latest, 7, now)
		self.assertEqual(status, "needs_apk")
		self.assertIn("101.2", update_to)

	# --------------------------------------------------------------- register

	def test_register_creates_row_with_status(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1", "1.1.0")
		frappe.set_user(USER_A)
		result = self._register(reason="login")
		self.assertEqual(result["status"], "behind_ota")
		frappe.set_user("Administrator")
		row = frappe.db.get_value(
			devices.INSTALL, INSTALL_A, ["app", "user", "status", "update_to", "launches"], as_dict=True
		)
		self.assertEqual(row.app, PACKHOUSE)
		self.assertEqual(row.user, USER_A)
		self.assertEqual(row.status, "behind_ota")
		self.assertEqual(row.update_to, "1.1.16 (reopen the app)")
		pairing = devices._install_user_row(INSTALL_A, USER_A)
		self.assertEqual(pairing.logins, 1)

	def test_unknown_app_still_recorded_as_unknown(self):
		name = "Test Future App"
		frappe.db.delete(devices.MOBILE_APP, {"name": name})
		frappe.set_user(USER_A)
		result = self._register(app=name, app_version="1.0.0")
		self.assertEqual(result["status"], "unknown")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "app"), name)
		# Created disabled: enabling it on the desk starts version checks.
		self.assertEqual(frappe.db.get_value(devices.MOBILE_APP, name, "enabled"), 0)

	def test_disabled_app_is_unknown(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.db.set_value(devices.MOBILE_APP, PACKHOUSE, "enabled", 0)
		frappe.set_user(USER_A)
		self.assertEqual(self._register(app_version="1.1.15")["status"], "unknown")

	def test_bad_app_name_refused(self):
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.ValidationError):
			self._register(app="")

	def test_bad_install_id_refused(self):
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.ValidationError):
			self._register(install_id="../x")

	def test_throttle_keeps_same_version_launch(self):
		frappe.set_user(USER_A)
		self._register()
		self._register()
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "launches"), 1)

	def test_foreground_only_refreshes_last_seen(self):
		frappe.set_user(USER_A)
		self._register()
		frappe.db.set_value(
			devices.INSTALL,
			INSTALL_A,
			"last_seen",
			add_to_date(now_datetime(), hours=-3),
			update_modified=False,
		)
		self._register(reason="foreground")
		row = frappe.db.get_value(devices.INSTALL, INSTALL_A, ["launches", "last_seen"], as_dict=True)
		self.assertEqual(row.launches, 1)
		self.assertGreater(row.last_seen, add_to_date(now_datetime(), minutes=-1))
		# A version applied over the air is recorded even from a foreground report.
		self._register(reason="foreground", app_version="1.1.16")
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "app_version"), "1.1.16")

	def test_version_change_bypasses_throttle(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.set_user(USER_A)
		self._register(app_version="1.1.15")
		result = self._register(app_version="1.1.16")
		self.assertEqual(result["status"], "current")
		frappe.set_user("Administrator")
		row = frappe.db.get_value(
			devices.INSTALL,
			INSTALL_A,
			["app_version", "previous_app_version", "upgrades", "launches", "status"],
			as_dict=True,
		)
		self.assertEqual(row.app_version, "1.1.16")
		self.assertEqual(row.previous_app_version, "1.1.15")
		self.assertEqual(row.upgrades, 1)
		self.assertEqual(row.launches, 2)
		self.assertEqual(row.status, "current")
		self.assertEqual(devices._install_user_row(INSTALL_A, USER_A).app_version, "1.1.16")

	def test_other_user_not_throttled_and_row_shows_latest_report(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.set_user(USER_A)
		self._register(app_version="1.1.16")
		frappe.set_user(USER_B)
		self._register(app_version="1.1.16")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "user"), USER_B)
		rows = [r for r in devices.install_rows(app=PACKHOUSE) if r.install_id == INSTALL_A]
		# One row per device — its latest report — not one per past pairing.
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].user, USER_B)

	# ------------------------------------------------------------- device key

	def _rows_of_test_installs(self):
		return frappe.get_all(
			devices.INSTALL,
			filters={"name": ["in", INSTALLS]},
			fields=["name", "device_key"],
			order_by="name",
		)

	def test_device_key_is_stored_hashed(self):
		frappe.set_user(USER_A)
		self._register(device_key=KEY_1)
		stored = frappe.db.get_value(devices.INSTALL, INSTALL_A, "device_key")
		self.assertEqual(stored, devices.device_key_of(KEY_1))
		self.assertEqual(len(stored), 64)
		self.assertNotIn(KEY_1, stored)
		# A key hashed on the device lands on the same row key.
		self.assertEqual(devices.device_key_of(stored), stored)
		self.assertEqual(devices.device_key_of("9774d56d682e549c"), "")
		self.assertEqual(devices.device_key_of(""), "")

	def test_same_device_key_under_two_install_ids_is_one_row(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, device_key=KEY_1, app_version="1.1.15")
		# The app lost its stored id: the same scanner reports under a new one.
		frappe.set_user(USER_B)
		result = self._register(install_id=INSTALL_B, device_key=KEY_1, app_version="1.1.16")
		frappe.set_user("Administrator")

		self.assertEqual(result["install_id"], INSTALL_A)
		self.assertEqual([r.name for r in self._rows_of_test_installs()], [INSTALL_A])
		row = frappe.db.get_value(devices.INSTALL, INSTALL_A, ["user", "app_version", "status"], as_dict=True)
		self.assertEqual((row.user, row.app_version, row.status), (USER_B, "1.1.16", "current"))
		users = {u.user: u for u in devices._install_users(INSTALL_A)}
		self.assertEqual(set(users), {USER_A, USER_B})

	def test_different_device_keys_with_same_model_are_two_rows(self):
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, device_key=KEY_1, device_model="EDA52", os_version="11")
		self._register(install_id=INSTALL_B, device_key=KEY_2, device_model="EDA52", os_version="11")
		frappe.set_user("Administrator")
		rows = self._rows_of_test_installs()
		self.assertEqual([r.name for r in rows], [INSTALL_A, INSTALL_B])
		self.assertNotEqual(rows[0].device_key, rows[1].device_key)

	def test_keyless_duplicate_is_merged_when_it_reports_with_the_key(self):
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, device_key=KEY_1, app_version="1.1.15", reason="login")
		# Row B: the same scanner on an old build that sent no key.
		self._register(install_id=INSTALL_B, app_version="1.1.15", reason="login")
		frappe.set_user(USER_B)
		self._register(install_id=INSTALL_B, app_version="1.1.15", reason="login")
		frappe.set_user("Administrator")
		frappe.db.set_value(
			devices.INSTALL_USER,
			devices._install_user_row(INSTALL_B, USER_A).name,
			"first_seen",
			add_days(now_datetime(), -3),
			update_modified=False,
		)
		frappe.db.set_value(
			devices.INSTALL, INSTALL_B, "first_seen", add_days(now_datetime(), -3), update_modified=False
		)
		self.assertEqual(len(self._rows_of_test_installs()), 2)

		# Row B's app updates and now sends the key: B is folded into A.
		frappe.set_user(USER_B)
		result = self._register(install_id=INSTALL_B, device_key=KEY_1, app_version="1.1.16")
		frappe.set_user("Administrator")

		self.assertEqual(result["install_id"], INSTALL_A)
		self.assertEqual([r.name for r in self._rows_of_test_installs()], [INSTALL_A])
		self.assertFalse(frappe.db.exists(devices.INSTALL_USER, {"parent": INSTALL_B}))
		row = frappe.db.get_value(
			devices.INSTALL,
			INSTALL_A,
			["user", "app_version", "previous_app_version", "launches", "first_seen"],
			as_dict=True,
		)
		self.assertEqual(row.user, USER_B)
		self.assertEqual(row.app_version, "1.1.16")
		self.assertEqual(row.previous_app_version, "1.1.15")
		# A's 1 + B's 2 + this report.
		self.assertEqual(row.launches, 4)
		self.assertLess(row.first_seen, add_days(now_datetime(), -2))

		users = {u.user: u for u in devices._install_users(INSTALL_A)}
		self.assertEqual(set(users), {USER_A, USER_B})
		self.assertEqual(users[USER_A].logins, 2)
		self.assertLess(users[USER_A].first_seen, add_days(now_datetime(), -2))
		self.assertEqual(users[USER_B].app_version, "1.1.16")
		self.assertEqual(
			[
				u.idx
				for u in frappe.get_all(
					devices.INSTALL_USER, filters={"parent": INSTALL_A}, fields=["idx"], order_by="idx"
				)
			],
			[1, 2],
		)

	def test_keyless_row_takes_the_key_when_it_reports_one(self):
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A)
		self.assertFalse(frappe.db.get_value(devices.INSTALL, INSTALL_A, "device_key"))
		# Inside the throttle window: the key is still written.
		self._register(install_id=INSTALL_A, device_key=KEY_1)
		self.assertEqual(
			frappe.db.get_value(devices.INSTALL, INSTALL_A, "device_key"), devices.device_key_of(KEY_1)
		)

	def test_row_with_another_key_is_not_merged(self):
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, device_key=KEY_1)
		self._register(install_id=INSTALL_B, device_key=KEY_2)
		self._register(install_id=INSTALL_B, device_key=KEY_1, reason="login")
		frappe.set_user("Administrator")
		self.assertEqual([r.name for r in self._rows_of_test_installs()], [INSTALL_A, INSTALL_B])

	def test_without_device_key_each_install_id_is_its_own_row(self):
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, device_model="EDA52")
		self._register(install_id=INSTALL_B, device_model="EDA52")
		frappe.set_user("Administrator")
		rows = self._rows_of_test_installs()
		self.assertEqual([r.name for r in rows], [INSTALL_A, INSTALL_B])
		self.assertFalse(any(r.device_key for r in rows))

	def test_each_account_keeps_its_own_version(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.set_user(USER_A)
		self._register(device_key=KEY_1, app_version="1.1.15")
		frappe.set_user(USER_B)
		self._register(install_id=INSTALL_B, device_key=KEY_1, app_version="1.1.16")
		frappe.set_user("Administrator")

		reply = devices.installs(app=PACKHOUSE)
		entries = [r for r in reply["rows"] if r["install_id"] in INSTALLS]
		self.assertEqual(len(entries), 1)
		entry = entries[0]
		self.assertEqual(
			(entry["app_version"], entry["status"], entry["user"]), ("1.1.16", "current", USER_B)
		)
		self.assertEqual(entry["user_count"], 2)
		versions = {u["user"]: u["app_version"] for u in entry["users"]}
		self.assertEqual(versions, {USER_A: "1.1.15", USER_B: "1.1.16"})
		self.assertEqual(entry["users"][0]["user"], USER_B)
		self.assertTrue(all(u["last_seen"] for u in entry["users"]))
		self.assertTrue(entry["keyed"])
		self.assertNotIn("device_key", entry)
		# Filtering by an earlier account still finds the device.
		by_a = [r for r in devices.install_rows(app=PACKHOUSE, user=USER_A) if r.install_id in INSTALLS]
		self.assertEqual(len(by_a), 1)

	# ---------------------------------------------------------------- listing

	def test_installs_sorts_behind_first_and_hides_dormant(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1", "1.1.0")
		frappe.set_user(USER_A)
		self._register(install_id=INSTALL_A, app_version="1.1.16")
		self._register(install_id=INSTALL_B, app_version="1.0.5", runtime_version="101.0")
		frappe.set_user("Administrator")

		rows = [r for r in devices.install_rows(app=PACKHOUSE) if r.install_id in (INSTALL_A, INSTALL_B)]
		self.assertEqual([r.install_id for r in rows], [INSTALL_B, INSTALL_A])
		self.assertEqual(rows[0].status, "needs_apk")

		frappe.db.set_value(
			devices.INSTALL,
			INSTALL_B,
			"last_seen",
			add_to_date(now_datetime(), days=-30),
			update_modified=False,
		)
		visible = {r.install_id for r in devices.install_rows(app=PACKHOUSE)}
		self.assertNotIn(INSTALL_B, visible)
		dormant = {r.install_id for r in devices.install_rows(app=PACKHOUSE, status="dormant")}
		self.assertIn(INSTALL_B, dormant)

	def test_installs_is_system_manager_only(self):
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.PermissionError):
			devices.installs()

	def test_report_runs(self):
		from upande_quality.upande_quality.report.mobile_app_installs import mobile_app_installs

		_set_latest(QUALITY, "1.1.21", "101.1")
		frappe.set_user(USER_A)
		self._register(app=QUALITY, app_version="1.1.20")
		frappe.set_user("Administrator")
		columns, data = mobile_app_installs.execute({"app": QUALITY})
		self.assertTrue(columns)
		row = next(r for r in data if r["install_id"] == INSTALL_A)
		self.assertEqual(row["status"], "behind_ota")
		self.assertEqual(row["update_to"], "1.1.21 (reopen the app)")
		self.assertEqual(row["user_count"], 1)
		self.assertIn("(1.1.20)", row["users"])

	# ----------------------------------------------------------------- latest

	def test_refresh_latest_reads_manifest_walks_runtimes_and_apk(self):
		pages = {
			"https://example.test/packhouse/ota/android/101.0/manifest.json": _manifest("1.0.40", "101.0"),
			"https://example.test/packhouse/ota/android/101.1/manifest.json": _manifest("1.1.16", "101.1"),
			"https://example.test/quality/ota/android/101.0/manifest.json": _manifest("1.0.30", "101.0"),
			"https://example.test/quality/ota/android/101.1/manifest.json": _manifest("1.1.21", "101.1"),
		}
		releases = [
			{"tag_name": "v1.1.16", "assets": []},
			{
				"tag_name": "v1.1.0",
				"assets": [{"name": "x_v1.1.0.apk", "browser_download_url": "https://example/x_v1.1.0.apk"}],
			},
			{
				"tag_name": "legacy-v1.5.0",
				"prerelease": True,
				"assets": [{"name": "x_v1.5.0.apk", "browser_download_url": "https://example/x_v1.5.0.apk"}],
			},
		]

		def fake_get(url, **kwargs):
			if "api.github.com" in url:
				return FakeResponse(200, releases)
			if url in pages:
				return FakeResponse(200, pages[url])
			return FakeResponse(404)

		frappe.set_user(USER_A)
		self._register(app_version="1.1.15")
		frappe.set_user("Administrator")

		with (
			patch.object(devices.requests, "get", side_effect=fake_get),
			patch.object(frappe.db, "commit"),
			patch.object(frappe, "get_all", side_effect=_only_test_apps(frappe.get_all)),
		):
			devices.refresh_latest()

		latest = devices.latest_by_app()
		self.assertEqual(latest[PACKHOUSE].latest_version, "1.1.16")
		self.assertEqual(latest[PACKHOUSE].latest_runtime, "101.1")
		self.assertEqual(latest[PACKHOUSE].latest_apk_version, "1.1.0")
		self.assertEqual(latest[QUALITY].latest_version, "1.1.21")
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "status"), "behind_ota")

	def test_refresh_failure_leaves_status_unknown(self):
		_set_latest(PACKHOUSE, "1.1.16", "101.1")
		frappe.set_user(USER_A)
		self._register(app_version="1.1.15")
		frappe.set_user("Administrator")

		def offline(url, **kwargs):
			raise Exception("offline")

		with (
			patch.object(devices.requests, "get", side_effect=offline),
			patch.object(frappe.db, "commit"),
			patch.object(frappe, "get_all", side_effect=_only_test_apps(frappe.get_all)),
		):
			devices.refresh_latest()

		self.assertNotIn(PACKHOUSE, devices.latest_by_app())
		self.assertEqual(frappe.db.get_value(devices.INSTALL, INSTALL_A, "status"), "unknown")
		error = frappe.db.get_value(devices.MOBILE_APP, PACKHOUSE, "last_error")
		self.assertIn("offline", error)
