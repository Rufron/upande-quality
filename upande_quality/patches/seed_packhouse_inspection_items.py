import frappe

# The items and conditions the mobile app and submitPackhouseInspection had hard-coded until the
# Packhouse Inspection Item master existed. Quality Managers maintain them in the desk from now on.
ITEMS = [
	(
		"Bunching Tables",
		["Clean", "Dusty", "Dirty", "Cobwebs", "Needs painting", "Needs repair", "In good condition"],
	),
	(
		"Sleeving Tables",
		["Clean", "Dusty", "Dirty", "Cobwebs", "Needs painting", "Broken", "In good condition"],
	),
	("QC Table", ["Clean", "Dusty", "Dirty", "Cobwebs", "Dim light", "Blown light", "Needs painting"]),
	("Guillotine", ["Clean", "Dirty", "Blurred", "Detached", "In good condition"]),
	("Floor", ["Clean", "Dirty", "Broken tiles", "Faded marks", "Slippery"]),
	("Walls", ["Clean", "Dusty", "Dirty", "Cobwebs", "Needs painting"]),
	("Windows / Windowpanes", ["Clean", "Dusty", "Dirty", "Cobwebs"]),
	("Trolleys", ["Clean", "Dirty", "Broken", "Unmovable", "Rusty", "In good condition"]),
	("Drainage", ["Clean", "Dirty", "Blocked"]),
	("Sockets", ["In good condition", "Broken", "Naked wire"]),
	("Lights", ["Clean", "Dusty", "Dirty", "Cobwebs", "Blown", "Dim light"]),
	("Roof", ["Clean", "Dusty", "Dirty", "Cobwebs", "Needs painting"]),
	("Roof Traces", ["Clean", "Dusty", "Dirty", "Cobwebs", "Rusty", "Broken"]),
]
OK = {"Clean", "In good condition", "Functioning"}


def execute():
	if frappe.db.count("Packhouse Inspection Item"):
		return  # already set up; never overwrite what Quality Managers changed
	for order, (item, conditions) in enumerate(ITEMS, start=1):
		frappe.get_doc(
			{
				"doctype": "Packhouse Inspection Item",
				"item_name": item,
				"sort_order": order,
				"active": 1,
				"conditions": [{"condition": c, "counts_as_ok": int(c in OK)} for c in conditions],
			}
		).insert(ignore_permissions=True)
