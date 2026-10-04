import frappe

# The Remote Transfers tab of Production Settings was custom fields (shipped by
# upande_packhouse); it is now standard fields of the doctype, same fieldnames, so
# the saved values carry over. Drop the custom copies before the doctype syncs.
FIELDS = (
	"custom_remote_transfers_tab",
	"custom_transfer_hub_farm",
	"custom_auto_transfer_section",
	"custom_auto_remote_transfer_scheduling",
	"custom_auto_transfer_frequency",
	"custom_hub_shelving_section",
	"custom_allow_hub_shelving_without_transfer",
)


def execute():
	for name in frappe.get_all(
		"Custom Field",
		filters={"dt": "Production Settings", "fieldname": ["in", FIELDS]},
		pluck="name",
	):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.clear_cache(doctype="Production Settings")
