import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns(filters)
	data = get_data(filters)
	return columns, data


def get_base_columns():
	return [
		{
			"fieldname": "name",
			"label": "Report",
			"fieldtype": "Link",
			"options": "Quality Reporting",
			"width": 160,
		},
		{"fieldname": "when", "label": "When", "fieldtype": "Datetime", "width": 150},
		{"fieldname": "variety", "label": "Variety", "fieldtype": "Data", "width": 120},
		{"fieldname": "control_point", "label": "Control Point", "fieldtype": "Data", "width": 150},
		{"fieldname": "stems_checked", "label": "Stems Checked", "fieldtype": "Int", "width": 120},
		{"fieldname": "stems_accepted", "label": "Stems Accepted", "fieldtype": "Int", "width": 120},
		{"fieldname": "stems_rejected", "label": "Stems Rejected", "fieldtype": "Int", "width": 120},
		{"fieldname": "control_action", "label": "Control Action", "fieldtype": "Data", "width": 150},
	]


def get_tail_columns():
	return [
		{
			"fieldname": "prepared_by",
			"label": "Created By",
			"fieldtype": "Link",
			"options": "User",
			"width": 150,
		},
		{"fieldname": "ftr", "label": "FTR %", "fieldtype": "Percent", "width": 100},
	]


def to_snake_case(s):
	return (s or "").strip().lower().replace(" ", "_")


def build_where(filters):
	conditions = ["qr.docstatus < 2"]
	values = {}

	if filters and filters.get("from_date"):
		conditions.append("qr.modified >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters and filters.get("to_date"):
		conditions.append("qr.modified < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)")
		values["to_date"] = filters["to_date"]

	if filters and filters.get("control_point"):
		conditions.append("qr.control_point = %(control_point)s")
		values["control_point"] = filters["control_point"]

	if filters and filters.get("control_action"):
		conditions.append("qr.control_action = %(control_action)s")
		values["control_action"] = filters["control_action"]

	return " AND ".join(conditions), values


def get_all_qc_parameters():
	"""Fetch ALL parameters from the QC Parameters master doctype."""
	params = frappe.db.sql(
		"""
        SELECT name AS parameter
        FROM `tabQC Parameters`
        ORDER BY name
    """,
		as_dict=1,
	)

	return [p.parameter for p in params if p.parameter]


def get_dynamic_defect_columns(filters):
	"""Build columns from the QC Parameters master list so every parameter
	always appears, even if no report in the current filter has it."""
	all_params = get_all_qc_parameters()

	columns = []
	for param_name in all_params:
		fieldname = to_snake_case(param_name)
		columns.append(
			{
				"fieldname": fieldname,
				"label": param_name,
				"fieldtype": "Int",
				"width": max(100, len(param_name) * 9),
			}
		)

	return columns


def get_columns(filters):
	return get_base_columns() + get_dynamic_defect_columns(filters) + get_tail_columns()


def get_data(filters):
	where_clause, values = build_where(filters)

	reports = frappe.db.sql(
		"""
        SELECT
            qr.name,
            qr.modified as `when`,
            qr.variety,
            qr.control_point,
            qr.stems_checked,
            qr.stems_accepted,
            qr.stems_rejected,
            qr.control_action,
            qr.owner as prepared_by
        FROM `tabQuality Reporting` qr
        WHERE {where_clause}
        ORDER BY qr.modified DESC
    """.format(where_clause=where_clause),
		values,
		as_dict=1,
	)

	if not reports:
		return []

	# Pre-fetch the full master list of QC parameters
	all_params = get_all_qc_parameters()
	all_param_keys = [to_snake_case(p) for p in all_params]

	report_names = [r.name for r in reports]

	defects = frappe.db.sql(
		"""
        SELECT parent, parameter_name, IFNULL(`count`, 0) as `count`
        FROM `tabQuality Parameter`
        WHERE parent IN %(parents)s
          AND parenttype = 'Quality Reporting'
    """,
		{"parents": report_names},
		as_dict=1,
	)

	defect_map = {}
	for d in defects:
		defect_map.setdefault(d.parent, []).append(d)

	data = []
	for row in reports:
		stems_checked = float(row.get("stems_checked") or 0)
		stems_rejected = float(row.get("stems_rejected") or 0)
		row["ftr"] = round(((stems_checked - stems_rejected) / stems_checked) * 100, 2) if stems_checked else 0

		# Initialize ALL QC parameter columns to 0
		for key in all_param_keys:
			row[key] = 0

		# Overlay actual values from the child table
		for defect in defect_map.get(row.name, []):
			col_name = to_snake_case(defect.parameter_name)
			row[col_name] = row.get(col_name, 0) + defect["count"]

		data.append(row)

	return data
