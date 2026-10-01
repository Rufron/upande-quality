import frappe

# Stock Entry fields behind pending reshelving (mobile/api.py: moveBunch,
# listPendingReshelving). Some sites got the first two by hand and never the
# source bucket, which made listPendingReshelving fail with
# "Unknown column 'custom_pending_source_bucket'". Only missing fields are
# created, so a site's existing labels and placement are left as they are.
FIELDS = [
	{
		"fieldname": "custom_pending_reshelving",
		"label": "Pending Reshelving",
		"fieldtype": "Check",
		"default": "0",
		"read_only": 1,
		"insert_after": "stock_entry_type",
	},
	{
		"fieldname": "custom_pending_since",
		"label": "Pending Since",
		"fieldtype": "Datetime",
		"read_only": 1,
		"insert_after": "custom_pending_reshelving",
	},
	{
		"fieldname": "custom_pending_source_bucket",
		"label": "Pending Source Bucket",
		"fieldtype": "Data",
		"read_only": 1,
		"insert_after": "custom_pending_since",
	},
]


def execute():
	for df in FIELDS:
		if frappe.db.exists("Custom Field", {"dt": "Stock Entry", "fieldname": df["fieldname"]}):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Stock Entry", **df}).insert(
			ignore_permissions=True
		)
	frappe.clear_cache(doctype="Stock Entry")
