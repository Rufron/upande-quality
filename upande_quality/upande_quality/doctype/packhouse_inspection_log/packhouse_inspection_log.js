// Copyright (c) 2026, Adminstrator and contributors
// For license information, please see license.txt

frappe.ui.form.on("Packhouse Inspection Log", {
	onload(frm) {
		// The fixed set of packhouse components inspected on every visit.
		// Declared inside onload so nothing lands at global scope
		// (form scripts are re-evaluated on every form open).
		const default_components = [
			"Bunching Tables",
			"Sleeving Tables",
			"QC Table",
			"Guillotine",
			"Floor",
			"Walls",
			"Windows / Windowpanes",
			"Trolleys",
			"Drainage",
			"Sockets",
			"Lights",
			"Roof",
			"Roof Traces",
		];

		// On a brand-new, empty form, pre-load the checklist rows so the
		// inspector just fills status/conditions per component.
		if (frm.is_new() && (frm.doc.components || []).length === 0) {
			default_components.forEach(function (name) {
				const row = frm.add_child("components");
				row.component = name;
				row.status = "Not Checked";
			});
			frm.refresh_field("components");
		}
	},
});

// Keep Status in sync with the recorded conditions when edited on desk,
// mirroring the server-side derivation used for phone submissions:
//   no tags -> Not Checked;  all tags positive -> √ Okay;  any problem -> X Faulty
frappe.ui.form.on("Packhouse Inspection Component", {
	conditions(frm, cdt, cdn) {
		const OK = ["Clean", "In good condition", "Functioning"];
		const row = locals[cdt][cdn];
		const tags = (row.conditions || "")
			.split(",")
			.map(function (s) { return s.trim(); })
			.filter(Boolean);

		let status = "Not Checked";
		if (tags.length) {
			status = tags.every(function (t) { return OK.includes(t); }) ? "√ Okay" : "X Faulty";
		}
		frappe.model.set_value(cdt, cdn, "status", status);
	},
});
