import frappe


def execute():
	"""Production Settings no longer has "Enforce the Allocation Plan" / "Enforce at": Sales Allocation v2 uses
	the plan as a guide only. Drop their child table doctype and the stored values."""
	if frappe.db.exists("DocType", "Enforce Plan Location"):
		frappe.delete_doc("DocType", "Enforce Plan Location", force=True, ignore_missing=True)
	frappe.db.delete(
		"Singles",
		{
			"doctype": "Production Settings",
			"field": ("in", ["enforce_allocation_plan", "enforce_plan_locations"]),
		},
	)
