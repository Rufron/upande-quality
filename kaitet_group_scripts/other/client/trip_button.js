// Name: Trip Button
// Type: Client Script · Form
// DocType: Delivery Trip
// Enabled: NO (disabled on the site)
// Modified: 2025-05-29 11:28:46
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.ui.form.on('Delivery Trip', {
    refresh: function(frm) {
        if (frm.doc.status === "Scheduled" && !frm.doc.start_time) {
            frm.add_custom_button(('Start Trip'), function () {
                frappe.call({
                    method: "start_trip_transfer",
                    args: {
                        delivery_trip: frm.doc.name,
                    },
                    callback: function() {
                        frm.reload_doc();
                        frappe.msgprint("Trip started and stock moved to Goods In Transit.");
                    }
                });
            });
        }

        if (frm.doc.status === "In Transit" && !frm.doc.end_time) {
            frm.add_custom_button(('End Trip'), function () {
                frappe.call({
                    method: "end_trip_transfer",
                    args: {
                        delivery_trip: frm.doc.name
                    },
                    callback: function() {
                        frm.reload_doc();
                        frappe.msgprint("Trip completed and stock moved to Karen Yoghurt Goods.");
                    }
                });
            });
        }
    }
});
