"""Bila Shaka runs a slimmed-down copy of the Karen Roses build. The features that were
dropped (intake QC, shelving, cold room, cleaning, vaselife, solution mixing, transport,
dashboards) left records behind that only lived in the database via fixtures:

- API Server Scripts that fed the removed www/ dashboards
- the "QC" / "Upande Quality" Custom HTML Blocks
- Stock Entry property setters gating Karen-only custom fields; several of them also
  hide standard fields (from/to warehouse, set posting time) behind Karen conditions
- QC Control Points other than Packhouse (QC only happens at grading)

DocTypes and Reports whose files were deleted are removed by migrate itself. The old
"Upande Quality" Workspace Sidebar is left alone: it is a read-only archive of the previous
navigation, and the module's nav now comes from the workspace."""

import frappe

KEPT_CONTROL_POINTS = {"Packhouse"}


def execute():
	for name in frappe.get_all("Server Script", filters={"module": "Upande Quality"}, pluck="name"):
		frappe.delete_doc("Server Script", name, force=True, ignore_permissions=True)

	for name in ("QC", "Upande Quality"):
		frappe.delete_doc("Custom HTML Block", name, force=True, ignore_missing=True, ignore_permissions=True)

	for name in frappe.get_all(
		"Property Setter",
		filters={"doc_type": "Stock Entry", "property": ["in", ["depends_on", "reqd"]]},
		pluck="name",
	):
		frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
	frappe.clear_cache(doctype="Stock Entry")

	frappe.delete_doc(
		"Custom Field", "Farm Distance-via_farms", force=True, ignore_missing=True, ignore_permissions=True
	)

	for name in frappe.get_all("QC Control Point", pluck="name"):
		if name not in KEPT_CONTROL_POINTS:
			frappe.delete_doc("QC Control Point", name, force=True, ignore_permissions=True)

