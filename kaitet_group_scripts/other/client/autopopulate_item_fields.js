// Name: Autopopulate Item Fields
// Type: Client Script · Form
// DocType: Daily Shopify Transfer
// Enabled: yes
// Modified: 2025-07-28 20:59:01
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
// Parent Doctype script
frappe.ui.form.on('Daily Shopify Transfer', {
  onload: function(frm) {
    set_defaults(frm);
  },
  refresh: function(frm) {
    set_defaults(frm);
  }
});

function set_defaults(frm) {
  if (!frm.doc.created_by) {
    frm.set_value('created_by', frappe.session.user);
  }

  if (!frm.doc.transfer_date) {
    frm.set_value('transfer_date', frappe.datetime.get_today());
  }

  if (!frm.doc.target_warehouse) {
    frm.set_value('target_warehouse', "Online Available for Sale - KR");
  }

  if (!frm.doc.batch_id) {
    const today = frappe.datetime.get_today(); // YYYY-MM-DD
    const randomString = Math.random().toString(36).substring(2, 8).toUpperCase(); // 6-char random
    const batchId = `TRANSFER-${today}-${randomString}`;
    frm.set_value('batch_id', batchId);
  }
}

// Child Table script
frappe.ui.form.on('Daily Shopify Transfer Item', {
  item_code: function (frm, cdt, cdn) {
    const row = locals[cdt][cdn];

    if (row.item_code) {
      // Set Item Name and UOM
      frappe.call({
        method: "frappe.client.get",
        args: {
          doctype: "Item",
          name: row.item_code
        },
        callback: function (r) {
          if (r.message) {
            frappe.model.set_value(cdt, cdn, "item_name", r.message.item_name);
            frappe.model.set_value(cdt, cdn, "uom", r.message.stock_uom);
          }
        }
      });

      // Set Qty Available from Source Warehouse
      update_qty_available(frm, cdt, cdn);
    }
  }
});

// Helper function to get quantity available
function update_qty_available(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  const source_warehouse = frm.doc.source_warehouse;

  if (row.item_code && source_warehouse) {
    frappe.call({
      method: "frappe.client.get_value",
      args: {
        doctype: "Bin",
        filters: {
          item_code: row.item_code,
          warehouse: source_warehouse
        },
        fieldname: "actual_qty"
      },
      callback: function (r) {
        const qty = r.message ? r.message.actual_qty : 0;
        frappe.model.set_value(cdt, cdn, "qty_available", qty);
        validate_qty_vs_available(cdt, cdn);
      }
    });
  }
}
function validate_qty_vs_available(cdt, cdn) {
  const row = locals[cdt][cdn];
  if (row.qty && row.qty_available && row.qty > row.qty_available) {
    frappe.msgprint({
      title: "Insufficient Stock",
      message: `Requested qty (${row.qty}) exceeds available qty (${row.qty_available}) in ${row.source_warehouse}`,
      indicator: "red"
    });
    frappe.model.set_value(cdt, cdn, "qty", 0);
  }
}
