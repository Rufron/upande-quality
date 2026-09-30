# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
    filters = filters or {}
    conditions = []
    values = {}
    if filters.get("status"):
        conditions.append("status = %(status)s")
        values["status"] = filters.get("status")
    if filters.get("control_point"):
        conditions.append("control_point = %(control_point)s")
        values["control_point"] = filters.get("control_point")
    if filters.get("from_date"):
        conditions.append("date_of_incident >= %(from_date)s")
        values["from_date"] = filters.get("from_date")
    if filters.get("to_date"):
        conditions.append("date_of_incident <= %(to_date)s")
        values["to_date"] = filters.get("to_date")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    data = frappe.db.sql(
        "SELECT name, date_of_incident, control_point, farm, custom_greenhouse, variety,"
        " issue, requested_by, assigned_to, status, target_date, actual_completion_date"
        " FROM `tabCorrective Action Report` " + where +
        " ORDER BY FIELD(status,'Pending','In Progress','Complete'), date_of_incident DESC, creation DESC",
        values, as_dict=1,
    )

    columns = [
        {"label": "CAR", "fieldname": "name", "fieldtype": "Link", "options": "Corrective Action Report", "width": 150},
        {"label": "Date", "fieldname": "date_of_incident", "fieldtype": "Date", "width": 95},
        {"label": "Control Point", "fieldname": "control_point", "fieldtype": "Link", "options": "QC Control Point", "width": 110},
        {"label": "Farm", "fieldname": "farm", "fieldtype": "Link", "options": "Farm", "width": 95},
        {"label": "Greenhouse", "fieldname": "custom_greenhouse", "fieldtype": "Data", "width": 150},
        {"label": "Variety", "fieldname": "variety", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": "Issue", "fieldname": "issue", "fieldtype": "Data", "width": 230},
        {"label": "Requested By", "fieldname": "requested_by", "fieldtype": "Link", "options": "User", "width": 160},
        {"label": "Assigned To", "fieldname": "assigned_to", "fieldtype": "Link", "options": "User", "width": 160},
        {"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 90},
        {"label": "Target Date", "fieldname": "target_date", "fieldtype": "Date", "width": 95},
        {"label": "Completed On", "fieldname": "actual_completion_date", "fieldtype": "Date", "width": 105},
    ]
    return columns, data
