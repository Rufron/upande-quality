// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.query_reports["Flower Audits"] = {
	filters: [
		{
			fieldname: "audit_type",
			label: __("Audit Type"),
			fieldtype: "Select",
			options: ["", "Bud Count", "Head Size", "Stem Weight", "Spray Diameter"].join("\n"),
		},
		{
			fieldname: "farm",
			label: __("Farm"),
			fieldtype: "Link",
			options: "Farm",
		},
		{
			fieldname: "greenhouse",
			label: __("Greenhouse"),
			fieldtype: "Link",
			options: "Greenhouse",
		},
		{
			fieldname: "variety",
			label: __("Variety"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
	],
};
