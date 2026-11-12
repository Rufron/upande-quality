# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import re

import frappe

from upande_quality.mobile.api import car_concerns

GH_NO = re.compile(r"GH\s*0*(\w+)", re.I)


def execute(filters=None):
	filters = filters or {}
	conditions = []
	for key, sql in (
		("greenhouse", "custom_greenhouse = %(greenhouse)s"),
		("status", "status = %(status)s"),
		("control_point", "control_point = %(control_point)s"),
		("from_date", "date_of_incident >= %(from_date)s"),
		("to_date", "date_of_incident <= %(to_date)s"),
	):
		if filters.get(key):
			conditions.append(sql)
	where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

	cars = frappe.db.sql(
		"SELECT name, farm, custom_greenhouse, variety, issue, root_cause, corrective_action_plan,"
		" target_date, actual_completion_date"
		" FROM `tabCorrective Action Report` " + where + " ORDER BY date_of_incident DESC, creation DESC",
		filters,
		as_dict=1,
	)

	# farm is mostly blank on CARs; the greenhouse name starts with it ("Kapkolia GH 15 - KR")
	for c in cars:
		gh_name = c.custom_greenhouse or ""
		c.farm = c.farm or (gh_name.split(" GH")[0].strip() if " GH" in gh_name else "")
	if filters.get("farm"):
		cars = [c for c in cars if c.farm == filters.get("farm")]

	files = {}
	if cars:
		for f in frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Corrective Action Report",
				"attached_to_name": ["in", [c.name for c in cars]],
			},
			fields=["attached_to_name", "file_url", "file_name"],
		):
			files.setdefault(f.attached_to_name, []).append(
				f'<a href="{f.file_url}" target="_blank">{frappe.utils.escape_html(f.file_name or "File")}</a>'
			)

	data = []
	for c in cars:
		gh = GH_NO.search(c.custom_greenhouse or "")
		for i, (concern, stems) in enumerate(car_concerns(c.issue)):
			row = {
				"name": c.name,
				"concern": concern,
				"stems": stems,
				"farm": c.farm,
				"gh": gh.group(1) if gh else c.custom_greenhouse,
				"variety": c.variety,
			}
			if i == 0:  # CAR-level details once per CAR, like the paper form
				row.update(
					root_cause=c.root_cause,
					corrective_action=c.corrective_action_plan,
					target_date=c.target_date,
					actual_completion_date=c.actual_completion_date,
					evidence=", ".join(files.get(c.name, [])),
				)
			data.append(row)

	columns = [
		{
			"label": "CAR ID",
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Corrective Action Report",
			"width": 120,
		},
		{"label": "Concern", "fieldname": "concern", "fieldtype": "Data", "width": 170},
		{"label": "Affected Stems", "fieldname": "stems", "fieldtype": "Int", "width": 110},
		{"label": "Farm", "fieldname": "farm", "fieldtype": "Data", "width": 100},
		{"label": "GH", "fieldname": "gh", "fieldtype": "Data", "width": 60},
		{"label": "Variety", "fieldname": "variety", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Root Cause", "fieldname": "root_cause", "fieldtype": "Data", "width": 220},
		{"label": "Corrective Action", "fieldname": "corrective_action", "fieldtype": "Data", "width": 260},
		{"label": "Target Date", "fieldname": "target_date", "fieldtype": "Date", "width": 100},
		{
			"label": "Actual Completion Date",
			"fieldname": "actual_completion_date",
			"fieldtype": "Date",
			"width": 120,
		},
		{"label": "Evidence of the CA", "fieldname": "evidence", "fieldtype": "HTML", "width": 160},
	]
	return columns, data
