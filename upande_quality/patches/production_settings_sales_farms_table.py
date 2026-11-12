import frappe

# Remote Transfer Sales Farm was one Link (transfer_hub_farm); it is now the table
# sales_farms. Carry the saved farm into the table as its first row.


def execute():
	farm = frappe.db.sql(
		"SELECT value FROM `tabSingles` WHERE doctype = 'Production Settings' AND field = 'transfer_hub_farm'"
	)
	farm = farm[0][0] if farm else None
	frappe.reload_doc("upande_quality", "doctype", "remote_transfer_sales_farm")
	frappe.reload_doc("upande_quality", "doctype", "production_settings")
	frappe.clear_cache(doctype="Production Settings")
	if farm and not frappe.db.exists(
		"Remote Transfer Sales Farm", {"parent": "Production Settings", "parentfield": "sales_farms"}
	):
		ps = frappe.get_single("Production Settings")
		ps.append("sales_farms", {"farm": farm})
		ps.flags.ignore_validate = True
		ps.flags.ignore_mandatory = True
		ps.save(ignore_permissions=True)
	frappe.db.delete("Singles", {"doctype": "Production Settings", "field": "transfer_hub_farm"})
	frappe.clear_cache(doctype="Production Settings")
