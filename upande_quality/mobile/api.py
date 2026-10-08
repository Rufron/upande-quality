"""Whitelisted endpoints for the Bila Shaka quality mobile app."""

import frappe


@frappe.whitelist()
def getCurrentUserRoles():
	try:
		user = frappe.session.user
		rows = frappe.get_all(
			"Has Role",
			filters={"parent": user, "parenttype": "User"},
			fields=["role"],
		)
		roles = [r["role"] for r in rows if r.get("role")]
		frappe.response["data"] = {
			"user": user,
			# The app shows people by name, and most users cannot read their own
			# User record (only System Managers can), so the name comes from here.
			"full_name": frappe.utils.get_fullname(user),
			"roles": roles,
		}
	except Exception as e:
		frappe.log_error("getCurrentUserRoles error: " + str(e))
		frappe.response["http_status_code"] = 500
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def fetchQcParameters():
	try:
		data = frappe.get_all("QC Parameters", fields=["name", "parameter", "tolerance_thresholds"])

		frappe.response["message"] = data

	except Exception as e:
		frappe.throw(str(e))
