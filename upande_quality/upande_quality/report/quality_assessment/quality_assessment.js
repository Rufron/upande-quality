// Copyright (c) 2025, Upande and contributors
// For license information, please see license.txt

frappe.query_reports["Quality Assessment"] = {
	"filters": [
		{
			"fieldname": "control_point",
			"label": __("Control Point"),
			"fieldtype": "Link",
			"options": "QC Control Point"
		},
		{
			"fieldname": "farm",
			"label": __("Farm"),
			"fieldtype": "Link",
			"options": "Farm"
		},
		{
			"fieldname": "control_action",
			"label": __("Control Action"),
			"fieldtype": "Select",
			"options": ["", "Accepted", "Rejected", "Quarantined"]
		},
		{
			"fieldname": "from_date",
			"label": __("From Date"),
			"fieldtype": "Date"
		},
		{
			"fieldname": "to_date",
			"label": __("To Date"),
			"fieldtype": "Date"
		}
	]
};
