// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.query_reports["Vaselife Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Sampling Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Sampling Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "variety",
			label: __("Variety"),
			fieldtype: "Data",
		},
		{
			fieldname: "farm",
			label: __("Farm"),
			fieldtype: "Link",
			options: "Farm",
		},
		{
			fieldname: "breeder",
			label: __("Breeder"),
			fieldtype: "Data",
		},
		{
			fieldname: "commercial_status",
			label: __("Commercial Status"),
			fieldtype: "Select",
			options: ["", "Trials", "Semi Commercial", "Commercial"].join("\n"),
		},
		{
			fieldname: "crop",
			label: __("Crop"),
			fieldtype: "Select",
			options: ["", "Rose", "Spray Rose"].join("\n"),
		},
	],
};
