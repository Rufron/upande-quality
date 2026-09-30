// Name: Flower Material Transfer Listview Custom Status
// Type: Client Script · List
// DocType: Stock Entry
// Enabled: yes
// Modified: 2025-12-04 12:51:25
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.listview_settings['Stock Entry'] = {
    add_fields: ['custom_allocation_status', 'stock_entry_type', 'custom_sales_order'],
    has_indicator_for_draft: true,
    has_indicator_for_cancelled: true,

    get_indicator: function (doc) {
        // Only apply custom status to Material Transfer entries with sales order
        if (doc.stock_entry_type === "Material Transfer" && doc.custom_sales_order && doc.custom_allocation_status) {
            const status = doc.custom_allocation_status;
            
            // Status color map
            const status_map = {
                "Awaiting Truck Loading": "orange",
                "Loading": "blue",
                "In Transit": "gray",
                "Shelved":"green"
            };

            const label = `${status}`;
            const color = status_map[status] || "gray";

            return [label, color, `custom_allocation_status,=,${status}`];
        }
        
        // For all other Stock Entries, use default docstatus behavior
        if (doc.docstatus === 0) {
            return [__('Draft'), 'red', "docstatus,=,0"];
        } else if (doc.docstatus === 1) {
            return [__('Submitted'), 'blue', "docstatus,=,1"];
        } else {
            return [__('Cancelled'), 'red', "docstatus,=,2"];
        }
    }
};
