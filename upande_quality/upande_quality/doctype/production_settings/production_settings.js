// Copyright (c) 2026, Adminstrator and contributors
// For license information, please see license.txt

frappe.ui.form.on("Production Settings", {});

// Discard Age by Item Group: an item group, and the varieties of it (any flower crop) the age is for --
// picked many at once from that group and the groups under it. No varieties: the whole group.
frappe.ui.form.on("Discard Age Item Group", {
	select_varieties(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_group) {
			frappe.msgprint(__("Pick the item group first."));
			return;
		}
		const have = String(row.varieties || "")
			.split(/\n|,/)
			.map((v) => v.trim())
			.filter(Boolean);
		new frappe.ui.form.MultiSelectDialog({
			doctype: "Item",
			target: frm,
			setters: {},
			add_filters_group: 0,
			primary_action_label: __("Add Varieties"),
			get_query() {
				return {
					filters: {
						item_group: ["descendants of (inclusive)", row.item_group],
						disabled: 0,
						has_variants: 0,
					},
				};
			},
			action(selections) {
				const all = [...new Set([...have, ...(selections || [])])];
				frappe.model.set_value(cdt, cdn, "varieties", all.join("\n"));
				this.dialog.hide();
			},
		});
	},
	item_group(frm, cdt, cdn) {
		// another group: varieties of the old one no longer belong here
		frappe.model.set_value(cdt, cdn, "varieties", "");
	},
});
