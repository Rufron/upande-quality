import frappe

# The Planning section of Production Settings > Allocation Planning was custom fields
# (added by upande_packhouse's setup_settings); it is now standard fields of the
# doctype, same fieldnames, so the saved values carry over. Drop only those custom
# fields (plus allocation_harvest_lead_days, which upande_packhouse retired); any
# other site custom field on the doctype is left alone.

FIELDS = (
	"allocation_planning_section",
	"allocation_horizon_days",
	"allocation_planning_col",
	"allocation_conversion_minutes",
	"allocation_use_first_alert_stems",
	"allocation_harvest_lead_days",
)


def execute():
	for name in frappe.get_all(
		"Custom Field",
		filters={"dt": "Production Settings", "fieldname": ["in", FIELDS]},
		pluck="name",
	):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.clear_cache(doctype="Production Settings")
