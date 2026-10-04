"""Let people sign in with their username as well as their email: the mobile
apps' login field takes either. Frappe only accepts a username when System
Settings allows it."""

import frappe


def execute():
	if not frappe.db.get_single_value("System Settings", "allow_login_using_user_name"):
		frappe.db.set_single_value("System Settings", "allow_login_using_user_name", 1)
