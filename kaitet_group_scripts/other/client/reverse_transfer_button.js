// Name: Reverse Transfer Button
// Type: Client Script · Form
// DocType: Daily Shopify Transfer
// Enabled: yes
// Modified: 2025-07-29 12:24:08
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.ui.form.on('Daily Shopify Transfer', {
    refresh(frm) {
        if (!frm.is_new()) {
            const reverseBtn = frm.add_custom_button('Reverse Transfer', () => {
                frappe.confirm(
                    'Are you sure you want to reverse this transfer?',
                    () => {
                        frappe.call({
                            method: 'upande_kaitet.api.daily_transfer.reverse_transfer',
                            args: {
                                transfer_name: frm.doc.name
                            },
                            freeze: true,
                            freeze_message: 'Reversing transfer...',
                            callback: function (r) {
                                if (!r.exc) {
                                    frappe.msgprint(__('Transfer reversed successfully.'));
                                    frm.reload_doc();
                                } else {
                                    frappe.msgprint(__('Failed to reverse transfer.'));
                                }
                            }
                        });
                    }
                );
            }, 'Actions');

            if (
                frm.doc.transfer_status === 'Reversed' ||
                frm.doc.transfer_status === 'Draft'
            ) {
                const reason = `Cannot reverse a ${frm.doc.transfer_status.toLowerCase()} transfer.`;
                $(reverseBtn)
                    .prop('disabled', true)
                    .css('pointer-events', 'none')
                    .css('opacity', 0.5)
                    .attr('title', reason);
            }
        }
    }
});
