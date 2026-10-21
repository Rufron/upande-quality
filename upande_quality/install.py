# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe

# The Upande mobile apps the device register (upande_quality.mobile.mobile_app)
# knows on a fresh site. Created when missing, never overwritten: once a record
# exists the desk owns it. A future app is one more Mobile App record.
SEED_MOBILE_APPS = (
	{
		"app_name": "Upande Packhouse",
		"github_repo": "mark-judah/upande-packhouse",
		"ota_base_url": "https://mark-judah.github.io/upande-packhouse/ota",
	},
	{
		"app_name": "Upande Quality",
		"github_repo": "mark-judah/upande-quality",
		"ota_base_url": "https://mark-judah.github.io/upande-quality/ota",
	},
)


def after_install():
	ensure_mobile_apps()


def after_migrate():
	ensure_mobile_apps()


def ensure_mobile_apps():
	"""Create the seeded Mobile App records that are missing."""
	if not frappe.db.table_exists("Mobile App"):
		return
	for app in SEED_MOBILE_APPS:
		if frappe.db.exists("Mobile App", app["app_name"]):
			continue
		frappe.get_doc(
			{"doctype": "Mobile App", "enabled": 1, "platform": "android", "dormant_days": 7, **app}
		).insert(ignore_permissions=True)
