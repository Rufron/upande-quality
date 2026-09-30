// Name: Bucket Logistics Route Vehicle Filter
// Type: Client Script · Form
// DocType: Bucket Logistics Route
// Enabled: yes
// Modified: 2026-08-07 13:53:39
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.ui.form.on('Bucket Logistics Route', {
    onload: function(frm) {
        // Only internal-logistics trucks are relevant to a daily route.
        frm.set_query('vehicle', function() {
            return { filters: { custom_is_internal_logistics_truck: 1 } };
        });
    }
});
