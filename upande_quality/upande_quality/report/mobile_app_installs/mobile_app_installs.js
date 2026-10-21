// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

const STATUS_COLOURS = {
	needs_apk: "red",
	behind_ota: "orange",
	unknown: "gray",
	ahead: "blue",
	current: "green",
	dormant: "gray",
};

frappe.query_reports["Mobile App Installs"] = {
	filters: [
		{
			fieldname: "app",
			label: __("App"),
			fieldtype: "Link",
			options: "Mobile App",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\ncurrent\nbehind_ota\nneeds_apk\nahead\ndormant\nunknown",
		},
		{
			fieldname: "user",
			label: __("User"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "since_days",
			label: __("Seen in Last (Days)"),
			fieldtype: "Int",
		},
		{
			fieldname: "show_dormant",
			label: __("Show Dormant"),
			fieldtype: "Check",
			default: 0,
		},
	],

	onload(report) {
		report.page.add_inner_button(__("Check Latest Versions"), () => {
			frappe.call({
				method: "upande_quality.mobile.mobile_app.refresh_now",
				type: "POST",
				freeze: true,
				callback: () => report.refresh(),
			});
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && data.status) {
			const colour = STATUS_COLOURS[data.status] || "gray";
			return `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(data.status)}</span>`;
		}
		return formatted;
	},
};
