import frappe

# Pack While Waiting for a Replacement From (%) went live as a Custom Field ahead of the
# release; it is a standard field of the doctype now, same fieldname, so the saved value
# carries over. Drop the custom copy before the doctype syncs.


def execute():
	name = "Production Settings-pack_waiting_replacement_pct"
	if frappe.db.exists("Custom Field", name):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype="Production Settings")
