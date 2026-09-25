// Which sample columns are relevant for each audit type.
const AUDIT_TYPE_FIELDS = {
	"Bud Count": ["buds"],
	"Head Size": ["width", "height"],
	"Stem Weight": ["val_42", "val_52", "val_62"],
	"Spray Diameter": ["val_52", "val_62", "val_72"],
};

const ALL_MEASUREMENT_FIELDS = [...new Set(Object.values(AUDIT_TYPE_FIELDS).flat())];

frappe.ui.form.on("Flower Quality Audit", {
	refresh(frm) {
		toggle_sample_columns(frm);
	},

	audit_type(frm) {
		clear_irrelevant_values(frm);
		toggle_sample_columns(frm);
	},
});

function toggle_sample_columns(frm) {
	const grid = frm.fields_dict.samples && frm.fields_dict.samples.grid;
	if (!grid) return;

	const visible = AUDIT_TYPE_FIELDS[frm.doc.audit_type] || [];

	ALL_MEASUREMENT_FIELDS.forEach((fieldname) => {
		grid.set_column_disp(fieldname, visible.includes(fieldname));
	});

	// Not grid.refresh() -- refresh() never clears `visible_columns`, and
	// setup_visible_columns() early-returns while that cache is populated. The
	// column layout would stay frozen at whatever the first render produced.
	// reset_grid() drops the cache and the rendered rows, then re-renders.
	grid.reset_grid();
}

// Only on an actual audit_type change -- never on refresh, or loading a saved
// document would silently wipe its measurements.
function clear_irrelevant_values(frm) {
	const visible = AUDIT_TYPE_FIELDS[frm.doc.audit_type] || [];

	(frm.doc.samples || []).forEach((row) => {
		ALL_MEASUREMENT_FIELDS.forEach((fieldname) => {
			if (!visible.includes(fieldname) && row[fieldname]) {
				frappe.model.set_value(row.doctype, row.name, fieldname, null);
			}
		});
	});
}
