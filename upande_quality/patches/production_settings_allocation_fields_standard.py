import frappe

# The Planning section of Production Settings > Allocation Planning was custom fields
# (added by upande_packhouse's setup_settings); it is now standard fields of the
# doctype, same fieldnames, so the saved values carry over. Drop every custom field
# left on the doctype before it syncs.


def execute():
	for name in frappe.get_all("Custom Field", filters={"dt": "Production Settings"}, pluck="name"):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.clear_cache(doctype="Production Settings")
