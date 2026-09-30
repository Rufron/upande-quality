// Name: Copy From Last Transfer Button
// Type: Client Script · List
// DocType: Daily Shopify Transfer
// Enabled: yes
// Modified: 2025-07-29 11:51:55
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.listview_settings['Daily Shopify Transfer'] = {
    onload: function (listview) {
        listview.page.add_inner_button('Copy From Last Transfer', function () {
            frappe.call({
                method: "upande_kaitet.api.daily_transfer.copy_last_transfer",
                callback: function (r) {
                    if (r.message) {
                        frappe.model.with_doc("Daily Shopify Transfer", r.message, function () {
                            let doc = frappe.model.get_doc("Daily Shopify Transfer", r.message);
                            frappe.set_route("Form", "Daily Shopify Transfer", doc.name);
                        });
                    }
                }
            });
        });
    }
};
