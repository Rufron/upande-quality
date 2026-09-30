// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt
//
// Client Script "Bucket Logistics Route Vehicle Filter" from kaitet-group live.

frappe.ui.form.on("Bucket Logistics Route", {
	onload: function (frm) {
		// Only internal-logistics trucks are relevant to a daily route.
		frm.set_query("vehicle", function () {
			return { filters: { custom_is_internal_logistics_truck: 1 } };
		});
	},
});
