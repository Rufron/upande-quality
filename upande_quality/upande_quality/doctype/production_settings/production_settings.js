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

frappe.ui.form.on("Auto Allocation Farm", {
	run_now(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.sales_farm) return;
		frappe.prompt(
			[{ fieldname: "release", fieldtype: "Check", label: __("Release"), default: 0 }],
			(v) =>
				frappe.call({
					method: "upande_packhouse.api.auto_allocation.run_now",
					args: { sales_farm: row.sales_farm, release: v.release ? 1 : 0 },
					callback: (r) =>
						r.message &&
						frappe.show_alert({
							message: __("{0}: {1} started", [row.sales_farm, r.message.run]),
							indicator: "green",
						}),
				}),
			__("Run {0} now", [row.sales_farm]),
			__("Run")
		);
	},
});

// Stock Farms: a click on the cell opens the Roses farms as tick boxes; the ticked ones are saved on the row
function pick_stock_farms(frm, cdn) {
	const row = locals["Auto Allocation Farm"][cdn];
	if (!row) return;
	const have = String(row.stock_farms || "")
		.split(/\n|,/)
		.map((f) => f.trim())
		.filter(Boolean);
	// the Roses farms: the ones with shelves (Shelf Locations)
	const farms = [
		...new Set(
			(frm.doc.shelf_locations || []).filter((r) => r.enabled && r.farm).map((r) => r.farm)
		),
	].sort();
	const d = new frappe.ui.Dialog({
		title: __("Stock Farms for {0}", [row.sales_farm || ""]),
		fields: [
			{
				fieldname: "farms",
				fieldtype: "MultiCheck",
				columns: 3,
				options: farms.map((f) => ({ label: f, value: f, checked: have.includes(f) })),
			},
		],
		primary_action_label: __("Set"),
		primary_action(v) {
			frappe.model.set_value(
				row.doctype,
				row.name,
				"stock_farms",
				(v.farms || []).join(", ")
			);
			d.hide();
		},
	});
	d.show();
}

frappe.ui.form.on("Production Settings", {
	refresh(frm) {
		$(frm.wrapper)
			.off(".stock_farms")
			.on(
				"focusin.stock_farms",
				'[data-fieldname="auto_allocation_farms"] textarea[data-fieldname="stock_farms"]',
				(e) => {
					const cdn = $(e.target).closest(".grid-row").attr("data-name");
					e.target.blur();
					pick_stock_farms(frm, cdn);
				}
			);
	},
});
