# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PackhouseComponent(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_allowed_conditions(doctype, txt, searchfield, start, page_len, filters):
	"""Link query: restrict `condition` choices to those allowed for the
	selected Packhouse Component. Used by the checks grids in Packhouse
	Inspection Log and Packhouse Glass Inspection."""
	component = (filters or {}).get("component")
	if not component:
		return []

	allowed = frappe.get_all(
		"Packhouse Condition Selection",
		filters={"parent": component, "parenttype": "Packhouse Component"},
		pluck="condition",
	)
	if not allowed:
		return []

	return frappe.db.sql(
		"""
		SELECT name
		FROM `tabPackhouse Condition`
		WHERE name IN %(allowed)s
			AND name LIKE %(txt)s
		ORDER BY name
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"allowed": tuple(allowed),
			"txt": "%%%s%%" % txt,
			"start": start,
			"page_len": page_len,
		},
	)
