import frappe
from frappe import _


def execute(filters=None):
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def get_base_columns():
    return [
        {"fieldname": "name", "label": "Report", "fieldtype": "Link", "options": "Quality Reporting", "width": 160},
        {"fieldname": "when", "label": "When", "fieldtype": "Datetime", "width": 150},
        {"fieldname": "farm", "label": "Farm", "fieldtype": "Data", "width": 120},
        {"fieldname": "greenhouse", "label": "Greenhouse", "fieldtype": "Data", "width": 120},
        {"fieldname": "variety", "label": "Variety", "fieldtype": "Data", "width": 120},
        {"fieldname": "control_point", "label": "Control Point", "fieldtype": "Data", "width": 150},
        {"fieldname": "harvest_time", "label": "Harvest Time", "fieldtype": "Data", "width": 120},
        {"fieldname": "arrival_time", "label": "Arrival Time", "fieldtype": "Data", "width": 120},
        {"fieldname": "transit_time", "label": "Transit Time", "fieldtype": "Data", "width": 120},
        {"fieldname": "stems_received", "label": "Stems Received", "fieldtype": "Int", "width": 120},
        {"fieldname": "stems_checked", "label": "Stems Checked", "fieldtype": "Int", "width": 120},
        {"fieldname": "stems_per_bucket", "label": "Stems Per Bucket", "fieldtype": "Int", "width": 120},
        {"fieldname": "solution_level", "label": "Solution Level", "fieldtype": "Data", "width": 120},
        {"fieldname": "solution_hygeine", "label": "Solution Hygiene", "fieldtype": "Data", "width": 120},
        {"fieldname": "solution_ph", "label": "Solution pH", "fieldtype": "Float", "width": 100},
        {"fieldname": "chlorine_ppm", "label": "Chlorine PPM", "fieldtype": "Float", "width": 100},
        {"fieldname": "control_action", "label": "Control Action", "fieldtype": "Data", "width": 150},
        {"fieldname": "quarantined_stems", "label": "Quarantined Stems", "fieldtype": "Int", "width": 120},
    ]


def get_tail_columns():
    return [
        {"fieldname": "prepared_by", "label": "Prepared By", "fieldtype": "Link", "options": "User", "width": 150},
        {"fieldname": "unit_manager", "label": "Unit Manager", "fieldtype": "Link", "options": "User", "width": 150},
        {"fieldname": "sampled_percentage", "label": "Sampled %", "fieldtype": "Percent", "width": 120},
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

    if filters and filters.get("farm"):
        conditions.append("qr.farm = %(farm)s")
        values["farm"] = filters["farm"]

    if filters and filters.get("control_action"):
        conditions.append("qr.control_action = %(control_action)s")
        values["control_action"] = filters["control_action"]

    return " AND ".join(conditions), values


def get_all_qc_parameters():
    """Fetch ALL parameters from the QC Parameters master doctype."""
    params = frappe.db.sql("""
        SELECT name AS parameter
        FROM `tabQC Parameters`
        ORDER BY name
    """, as_dict=1)

    return [p.parameter for p in params if p.parameter]


def get_dynamic_defect_columns(filters):
    """Build columns from the QC Parameters master list so every parameter
    always appears, even if no report in the current filter has it."""
    all_params = get_all_qc_parameters()

    columns = []
    for param_name in all_params:
        fieldname = to_snake_case(param_name)
        columns.append({
            "fieldname": fieldname,
            "label": param_name,
            "fieldtype": "Int",
            "width": max(100, len(param_name) * 9),
        })

    return columns


# Intake/greenhouse-only measurements — not recorded at the Packhouse, so
# these columns are dropped when the report is filtered to control_point = Packhouse.
INTAKE_ONLY_FIELDS = {
    "solution_level", "solution_hygeine", "solution_ph", "chlorine_ppm",
    "stems_per_bucket", "harvest_time", "arrival_time", "transit_time",
    "stems_received",
}


def get_columns(filters):
    base = get_base_columns()
    defects = get_dynamic_defect_columns(filters)
    tail = get_tail_columns()
    columns = base + defects + tail

    if filters and filters.get("control_point") == "Packhouse":
        columns = [c for c in columns if c["fieldname"] not in INTAKE_ONLY_FIELDS]

    return columns


def get_data(filters):
    where_clause, values = build_where(filters)

    reports = frappe.db.sql("""
        SELECT
            qr.name,
            qr.modified as `when`,
            qr.farm,
            qr.ghouse as greenhouse,
            qr.variety,
            qr.control_point,
            qr.harvest_time,
            qr.arrival_time,
            qr.transit_time,
            qr.stems_received,
            qr.stems_checked,
            qr.stems_per_bucket,
            qr.solution_level,
            qr.solution_hygeine,
            qr.solution_ph,
            qr.chlorine_ppm,
            qr.control_action,
            qr.quarantined_stems,
            qr.prepared_by,
            qr.unit_manager
        FROM `tabQuality Reporting` qr
        WHERE {where_clause}
        ORDER BY qr.modified DESC
    """.format(where_clause=where_clause), values, as_dict=1)

    if not reports:
        return []

    # Pre-fetch the full master list of QC parameters
    all_params = get_all_qc_parameters()
    all_param_keys = [to_snake_case(p) for p in all_params]

    report_names = [r.name for r in reports]

    defects = frappe.db.sql("""
        SELECT parent, parameter_name, IFNULL(`count`, 0) as `count`
        FROM `tabQuality Parameter`
        WHERE parent IN %(parents)s
          AND parenttype = 'Quality Reporting'
    """, {"parents": report_names}, as_dict=1)

    defect_map = {}
    for d in defects:
        defect_map.setdefault(d.parent, []).append(d)

    data = []
    for row in reports:
        stems_checked = float(row.get("stems_checked") or 0)
        stems_received = float(row.get("stems_received") or 0)
        quarantined = float(row.get("quarantined_stems") or 0)

        if stems_received > 0:
            row["sampled_percentage"] = round((stems_checked / stems_received) * 100, 2)
        else:
            row["sampled_percentage"] = 0

        if stems_received > 0:
            # row["ftr"] = round(((stems_checked - quarantined) / stems_checked) * 100, 2)
            row["ftr"] = round(((stems_received - quarantined) / stems_received) * 100, 2)
        else:
            row["ftr"] = 0

        # Initialize ALL QC parameter columns to 0
        for key in all_param_keys:
            row[key] = 0

        # Overlay actual values from the child table
        for defect in defect_map.get(row.name, []):
            col_name = to_snake_case(defect.parameter_name)
            row[col_name] = row.get(col_name, 0) + defect["count"]

        data.append(row)

    return data