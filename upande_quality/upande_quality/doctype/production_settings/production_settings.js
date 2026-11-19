// Copyright (c) 2026, Adminstrator and contributors
// For license information, please see license.txt

// Discard Age by Item Group: one click on a row's Varieties opens the item group's varieties (and the groups under
// it) as a list to tick; the ticked ones are saved on the row. None ticked: the age is for the whole group.
function pick_discard_varieties(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item_group) {
		frappe.msgprint(__("Pick the item group first."));
		return;
	}
	const have = String(row.varieties || "")
		.split(/\n|,/)
		.map((v) => v.trim())
		.filter(Boolean);
	frappe
		.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Item",
				filters: [
					["item_group", "descendants of (inclusive)", row.item_group],
					["disabled", "=", 0],
					["has_variants", "=", 0],
				],
				fields: ["name", "item_name"],
				order_by: "name asc",
				limit_page_length: 0,
			},
		})
		.then((r) => {
			const items = r.message || [];
			if (!items.length) {
				frappe.msgprint(__("{0} has no varieties.", [row.item_group]));
				return;
			}
			const known = new Set(items.map((i) => i.name));
			const d = new frappe.ui.Dialog({
				title: __("Varieties of {0}", [row.item_group]),
				size: "large",
				fields: [
					{
						fieldtype: "MultiCheck",
						fieldname: "varieties",
						columns: 3,
						select_all: 1,
						options: items.map((i) => ({
							label:
								i.item_name && i.item_name !== i.name
									? `${i.name} (${i.item_name})`
									: i.name,
							value: i.name,
							checked: have.includes(i.name) ? 1 : 0,
						})),
					},
				],
				primary_action_label: __("Save"),
				primary_action(values) {
					// varieties ticked before that are no longer in the group are kept as they were
					const kept = have.filter((v) => !known.has(v));
					const picked = [...kept, ...(values.varieties || [])];
					frappe.model.set_value(cdt, cdn, "varieties", picked.join("\n"));
					d.hide();
				},
			});
			d.show();
		});
}

frappe.ui.form.on("Production Settings", {
	refresh(frm) {
		const grid =
			frm.fields_dict.discard_age_by_item_group &&
			frm.fields_dict.discard_age_by_item_group.grid;
		if (!grid || grid.wrapper.data("variety-click")) return;
		grid.wrapper.data("variety-click", 1);
		// the Varieties cell itself opens the list: no row form, no button
		grid.wrapper.on("click", '[data-fieldname="varieties"]', function (e) {
			const $row = $(this).closest(".grid-row");
			const name = $row.attr("data-name");
			if (!name || $(this).closest(".grid-heading-row").length) return;
			e.preventDefault();
			e.stopPropagation();
			pick_discard_varieties(frm, "Discard Age Item Group", name);
		});
	},
});

frappe.ui.form.on("Discard Age Item Group", {
	select_varieties(frm, cdt, cdn) {
		pick_discard_varieties(frm, cdt, cdn);
	},
	item_group(frm, cdt, cdn) {
		// another group: varieties of the old one no longer belong here
		frappe.model.set_value(cdt, cdn, "varieties", "");
	},
});
