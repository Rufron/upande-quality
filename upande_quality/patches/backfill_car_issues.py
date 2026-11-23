import frappe

from upande_quality.upande_quality.doctype.corrective_action_report.corrective_action_report import (
	sync_issues,
)


def execute():
	"""Fill the Issues table of every existing CAR from its issue text (without touching modified/owner)."""
	for name in frappe.get_all(
		"Corrective Action Report", filters={"issue": ["is", "set"]}, pluck="name", order_by="creation"
	):
		car = frappe.get_doc("Corrective Action Report", name)
		if car.get("issues"):
			continue
		sync_issues(car)
		for row in car.issues:
			row.db_insert()
		car.db_set("issue", car.issue, update_modified=False)
