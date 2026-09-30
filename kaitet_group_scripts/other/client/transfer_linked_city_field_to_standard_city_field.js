// Name: Transfer Linked City field to Standard City Field
// Type: Client Script · Form
// DocType: Lead
// Enabled: NO (disabled on the site)
// Modified: 2025-09-23 09:16:07
// Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
// ----------------------------------------------------------------------
frappe.ui.form.on('Lead', {
    validate(frm) {
        if (frm.doc.custom_city_2) {
            // Keep only text before the first dash
            const onlyCity = frm.doc.custom_city_2.split('-')[0].trim();
            frm.set_value('city', onlyCity);
        }
    }
});
