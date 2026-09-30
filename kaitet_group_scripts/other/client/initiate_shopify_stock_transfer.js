// Name: Initiate Shopify Stock Transfer
// Type: Client Script · Form
// DocType: Daily Shopify Transfer
// Enabled: yes
// Modified: 2025-07-29 12:20:27
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.ui.form.on("Daily Shopify Transfer", {
    refresh(frm) {
        if (!frm.is_new()) {
            let btn = frm.add_custom_button("Initiate Transfer", () => {
                frappe.call({
                    method: "upande_kaitet.api.daily_transfer.initiate_transfer",
                    args: { docname: frm.doc.name },
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.msgprint("Stock Entry created successfully!");
                            frm.reload_doc();
                        }
                    }
                });
            }, "Actions");

            if (
                frm.doc.transfer_status === "Transferred" ||
                frm.doc.transfer_status === "Reversed"
            ) {
                const reason = `Transfer already ${frm.doc.transfer_status.toLowerCase()}.`;
                $(btn)
                    .prop("disabled", true)
                    .css("pointer-events", "none")
                    .css("opacity", 0.5)
                    .attr("title", reason);
            }
        }
    }
});
