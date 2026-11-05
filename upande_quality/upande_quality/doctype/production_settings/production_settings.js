// Copyright (c) 2026, Adminstrator and contributors
// For license information, please see license.txt

frappe.ui.form.on("Production Settings", {
	refresh(frm) {
		// Remote transfers: end every trip of an earlier day now (the same as the
		// midnight run), so no trip carries over into today.
		frm.add_custom_button(__("Close Previous Days' Trips"), () => {
			frappe.confirm(__("Close every trip planned before today?"), () => {
				frappe.call({
					method: "upande_packhouse.api.remote_transfer.transfer_scheduling.closePreviousDayTrips",
					freeze: true,
					callback: (r) => {
						const n = (r.message && r.message.count) || 0;
						frappe.show_alert({
							message: __("{0} trip(s) closed", [n]),
							indicator: n ? "green" : "blue",
						});
					},
				});
			});
		});
	},
});
