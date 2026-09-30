// Name: Transfer Grading Stock
// Type: Client Script · List
// DocType: Stock Entry
// Enabled: NO (disabled on the site)
// Modified: 2025-03-13 02:53:00
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.listview_settings['Stock Entry'] = {
    onload: function(listview) {
        listview.page.add_button('Transfer Graded Stock', () => {
            // Get all stock entries from the list that were created in the last 24 hours
            let selected_entries = listview.data.filter(entry => {
                let entryDate = new Date(entry.creation);
                let last24Hours = new Date();
                last24Hours.setHours(last24Hours.getHours() - 24);

                return (
                    entry.docstatus === 1 &&
                    entryDate >= last24Hours && 
                    ["Grading", "Grading Without Receiving"].includes(entry.stock_entry_type)
                );
            }).map(entry => entry.name);
            
            if (selected_entries.length === 0) {
                frappe.msgprint(__('No recent graded stock entries found.'));
                return;
            }

            // Call the API to process the stock transfer
            frappe.call({
                method: "upande_kaitet.server_scripts.transfer_graded_stock.transfer_stock",
                args: { stock_entries: selected_entries },
                freeze: true,
                freeze_message: "Processing stock transfer...",
                callback: function(response) {

                    if (response.message) {
                        frappe.msgprint(`Stock Transfers Created: ${response.message.transferred_entries.join(", ")}`);
                        listview.refresh();
                    } else {
                        frappe.msgprint("Failed to create stock transfers.");
                    }
                }
            });

        }, 'Action');
    }
};
