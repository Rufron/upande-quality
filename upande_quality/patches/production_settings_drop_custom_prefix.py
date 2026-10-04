import frappe

# Production Settings fields lost their custom_ prefix (they are standard fields of
# the doctype). Move the saved values to the new names before the doctype syncs.
RENAMED = (
	"remote_transfers_tab",
	"transfer_hub_farm",
	"auto_transfer_section",
	"auto_remote_transfer_scheduling",
	"auto_transfer_frequency",
	"hub_shelving_section",
	"allow_hub_shelving_without_transfer",
	"takt_time",
)


def execute():
	for new in RENAMED:
		old = "custom_" + new
		if frappe.db.exists("Custom Field", {"dt": "Production Settings", "fieldname": old}):
			frappe.delete_doc(
				"Custom Field", "Production Settings-" + old, ignore_permissions=True, force=True
			)
		value = frappe.db.sql(
			"SELECT value FROM `tabSingles` WHERE doctype = 'Production Settings' AND field = %s", old
		)
		if not value:
			continue
		frappe.db.sql(
			"DELETE FROM `tabSingles` WHERE doctype = 'Production Settings' AND field IN (%s, %s)", (old, new)
		)
		frappe.db.sql(
			"INSERT INTO `tabSingles` (doctype, field, value) VALUES ('Production Settings', %s, %s)",
			(new, value[0][0]),
		)
	frappe.clear_cache(doctype="Production Settings")
