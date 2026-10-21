# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from upande_quality.mobile.mobile_app import install_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("The app install register is visible to System Managers only."), frappe.PermissionError
		)
	rows = install_rows(
		app=filters.get("app") or None,
		status=filters.get("status") or None,
		user=filters.get("user") or None,
		since_days=filters.get("since_days"),
		include_dormant=bool(frappe.utils.cint(filters.get("show_dormant"))),
	)
	data = [
		{
			"full_name": r.full_name or r.user,
			"user": r.user,
			"device": " ".join(x for x in (r.device_brand, r.device_model) if x) or r.device_name,
			"install_id": r.install_id,
			"app": r.app,
			"app_version": r.app_version,
			"runtime_version": r.runtime_version,
			"last_seen": r.last_seen,
			"logins": r.logins,
			"launches": r.launches,
			"status": r.status,
			"update_to": r.update_to,
		}
		for r in rows
	]
	return get_columns(), data


def get_columns():
	return [
		{"label": _("User"), "fieldname": "full_name", "fieldtype": "Data", "width": 170},
		{"label": _("User ID"), "fieldname": "user", "fieldtype": "Link", "options": "User", "width": 170},
		{"label": _("Device"), "fieldname": "device", "fieldtype": "Data", "width": 160},
		{
			"label": _("Install ID"),
			"fieldname": "install_id",
			"fieldtype": "Link",
			"options": "Mobile App Install",
			"width": 140,
		},
		{"label": _("App"), "fieldname": "app", "fieldtype": "Link", "options": "Mobile App", "width": 130},
		{"label": _("Version"), "fieldname": "app_version", "fieldtype": "Data", "width": 85},
		{"label": _("Runtime"), "fieldname": "runtime_version", "fieldtype": "Data", "width": 80},
		{"label": _("Last Seen"), "fieldname": "last_seen", "fieldtype": "Datetime", "width": 160},
		{"label": _("Logins"), "fieldname": "logins", "fieldtype": "Int", "width": 70},
		{"label": _("Launches"), "fieldname": "launches", "fieldtype": "Int", "width": 80},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Update To"), "fieldname": "update_to", "fieldtype": "Data", "width": 240},
	]
