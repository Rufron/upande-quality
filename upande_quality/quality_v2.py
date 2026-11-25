# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Shared page context for the Quality v2 www pages (www/*-v2.html).

Every v2 page controller calls `page_context(context, page_key, title)` so the shell
(templates/includes/quality_v2/shell_start.html) gets the same data on every page:
CSRF token, company name, the signed-in user, the sidebar and the site > farm groups.

Same design system and the same context variable names (`ph_*`) as upande_packhouse's
packhouse_v2.py, so the shell and the PH runtime port unchanged.

CSRF: minting the token at render time makes Frappe inject a valid `frappe.csrf_token`.
Without it a session with no token yet gets `frappe.csrf_token = "None"` and every POST
from the page fails with "CSRFTokenError: Invalid Request".
"""

import os
from urllib.parse import urlencode

import frappe
from frappe.sessions import get_csrf_token

_PUBLIC = os.path.join(os.path.dirname(__file__), "public")

BASE = "/quality-dashboard-v2"

#: Sidebar, in order: (group, [(page key, label, href, icon), ...]).
#: Page key == the www page name without "-v2"; it marks the active entry.
NAV = [
	(
		"Overview",
		[
			("intake-ftr-overview", "Intake FTR Overview", BASE + "/intake-ftr-overview-v2", "check"),
			("coldroom-overview", "Coldroom Operations", BASE + "/coldroom-overview-v2", "snow"),
			("packhouse-overview", "Packhouse Overview", BASE + "/packhouse-overview-v2", "home"),
		],
	),
	(
		"Operations",
		[
			("shelving-report", "Shelving Report", BASE + "/shelving-report-v2", "layers"),
			("transit-time", "Transit Time", BASE + "/transit-time-v2", "truck"),
			("solution-mixing-overview", "Solution Mixing", BASE + "/solution-mixing-overview-v2", "flower"),
		],
	),
	(
		"Insights",
		[
			("quality-trends", "Quality Trends", BASE + "/quality-trends-v2", "chart"),
			("quality-raw-data", "Quality Raw Data", BASE + "/quality-raw-data-v2", "file"),
		],
	),
	(
		"Exceptions",
		[
			("rejects", "Rejects", BASE + "/rejects-v2", "down"),
			("discard-requests", "Discard Requests", BASE + "/discard-requests-v2", "trash"),
		],
	),
]


def page_context(context, page_key, title, section=None):
	"""Fill the context every v2 page's shell needs. Returns context.

	Signed-out visitors are sent to the login page and brought back here afterwards.
	"""
	if frappe.session.user == "Guest":
		path = frappe.request.full_path if frappe.request else BASE + "/" + page_key + "-v2"
		frappe.local.flags.redirect_location = "/login?" + urlencode({"redirect-to": path.rstrip("?")})
		raise frappe.Redirect(302)
	context.no_cache = 1
	context.csrf_token = get_csrf_token()
	context.ph_active = page_key
	context.ph_title = title
	context.ph_section = section or _section_of(page_key)
	user = frappe.session.user
	full = frappe.utils.get_fullname(user) if user and user != "Guest" else ""
	context.ph_user = user
	context.ph_user_name = full or (user or "").split("@")[0]
	context.ph_company = frappe.db.get_single_value("Global Defaults", "default_company") or "Upande"
	context.ph_nav = NAV
	context.ph_v = _asset_version()
	context.ph_regions = _regions()
	return context


def _regions():
	"""Sites and their farms ({site: [farm, ...]}) for PH.REGIONS, from Production Settings when
	upande_packhouse is installed on this site. Empty = the runtime's built-in fallback."""
	try:
		if "upande_packhouse" in frappe.get_installed_apps():
			from upande_packhouse.api.v2.core import region

			return region.sites()
	except Exception:
		frappe.log_error("quality_v2: could not read sites")
	return {}


def _asset_version():
	"""Cache-buster for the shared css/js: changes whenever either file changes."""
	try:
		return str(
			int(
				max(
					os.path.getmtime(os.path.join(_PUBLIC, "css", "quality-v2.css")),
					os.path.getmtime(os.path.join(_PUBLIC, "js", "quality-v2.js")),
				)
			)
		)
	except OSError:
		return "1"


def _section_of(page_key):
	for group, items in NAV:
		if any(i[0] == page_key for i in items):
			return group
	return "Quality"
