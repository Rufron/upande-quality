// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.listview_settings["GreenHouse Integrity"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Greenhouse Inspection Item Master"), () =>
			frappe.set_route("List", "Greenhouse Inspection Item Master")
		);
	},
};
