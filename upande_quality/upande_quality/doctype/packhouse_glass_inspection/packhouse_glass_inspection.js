// Copyright (c) 2026, Adminstrator and contributors
// For license information, please see license.txt

frappe.ui.form.on("Packhouse Glass Inspection", {
	setup(frm) {
		// Map of Table MultiSelect fieldname -> Packhouse Component name (glass).
		// Kept inside setup() so nothing is declared at global scope
		// (form scripts are re-evaluated on every form open).
		const component_fields = {
			window_glass: "Window Glass",
			fluorescent_tube: "Fluorescent Tube",
			digital_thermometer: "Digital Thermometer",
			high_bay_lamps: "High Bay Lamps",
			flood_lamps: "Flood Lamps",
			door_glass: "Door Glass",
			fire_extinguisher_glass: "Fire Extinguisher Glass",
			cba_frame: "CBA Frame",
			phone_gadget: "Phone Gadget",
			computer_screen: "Computer Screen",
			photo_frames: "Photo Frames",
			wall_clock: "Wall Clock",
			weighing_scale: "Weighing Scale",
			vase_container: "Vase Container",
			wall_temp: "Wall Temp",
			fire_exits: "Fire Exits",
			printer_dashboard: "Printer Dashboard",
			temperature_wall_check_box: "Temperature Wall Check Box",
		};

		Object.keys(component_fields).forEach(function (fieldname) {
			// Table MultiSelect: set the query on the field itself (2-arg form),
			// not the grid form (3-arg) which only works for child-table grids.
			frm.set_query(fieldname, function () {
				return {
					query: "upande_quality.upande_quality.doctype.packhouse_component.packhouse_component.get_allowed_conditions",
					filters: { component: component_fields[fieldname] },
				};
			});
		});
	},
});
