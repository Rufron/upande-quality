# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

# Flower Audits
# -------------
# One row per `Flower Audit Sample Item`, with its parent `Flower Quality Audit`
# header repeated on each row.
#
# The child table carries the measurement columns for every audit type, but any
# single audit only fills in the ones its type calls for. So the column set is
# built from the `audit_type` filter: pick a type and the report shows only that
# type's measurements, exactly as the form's grid does. With no type selected
# every measurement column is shown.
#
# This mapping is the server-side twin of AUDIT_TYPE_FIELDS in
# flower_quality_audit.js -- keep the two in step when an audit type is added.

AUDIT_TYPE_FIELDS = {
	"Bud Count": ["buds"],
	"Head Size": ["width", "height"],
	"Stem Weight": ["val_42", "val_52", "val_62"],
	"Spray Diameter": ["val_52", "val_62", "val_72"],
}

# Declared explicitly rather than flattened from the mapping above, so the report
# column order follows the child doctype's own field order.
ALL_MEASUREMENT_FIELDS = [
	"buds",
	"width",
	"height",
	"val_42",
	"val_52",
	"val_62",
	"val_72",
]

CHILD_DOCTYPE = "Flower Audit Sample Item"
PARENT_DOCTYPE = "Flower Quality Audit"


def execute(filters=None):
	filters = frappe._dict(filters or {})

	measurement_fields = get_measurement_fields(filters.get("audit_type"))
	audits = get_audits(filters)
	data = get_rows(audits, measurement_fields)
	columns = get_columns(measurement_fields)

	return columns, data, None, None, get_report_summary(data, measurement_fields)


def get_measurement_fields(audit_type):
	"""Measurement fieldnames relevant to `audit_type`, in child-table order."""
	if not audit_type:
		return list(ALL_MEASUREMENT_FIELDS)

	relevant = set(AUDIT_TYPE_FIELDS.get(audit_type, []))
	return [f for f in ALL_MEASUREMENT_FIELDS if f in relevant]


def get_audits(filters):
	audit_filters = {}

	for fieldname in ("audit_type", "farm", "variety", "greenhouse"):
		if filters.get(fieldname):
			audit_filters[fieldname] = filters[fieldname]

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")

	# `Flower Quality Audit` has no date field of its own, so the date range runs
	# against `creation` -- i.e. when the audit was entered, not when the flowers
	# were assessed. Add a proper audit_date field and switch this over if the two
	# ever need to differ.
	if from_date and to_date:
		audit_filters["creation"] = ["between", [from_date, to_date]]
	elif from_date:
		audit_filters["creation"] = [">=", from_date]
	elif to_date:
		audit_filters["creation"] = ["<=", to_date]

	return frappe.get_all(
		PARENT_DOCTYPE,
		filters=audit_filters,
		fields=["name", "audit_type", "farm", "greenhouse", "variety", "remarks", "creation"],
		order_by="creation desc",
	)


def get_rows(audits, measurement_fields):
	if not audits:
		return []

	samples_by_audit = get_samples_by_audit([a.name for a in audits])
	rows = []

	for audit in audits:
		for sample in samples_by_audit.get(audit.name, []):
			row = {
				"audit": audit.name,
				"audit_type": audit.audit_type,
				"farm": audit.farm,
				"greenhouse": audit.greenhouse,
				"variety": audit.variety,
				"recorded_on": audit.creation,
				"sample_number": sample.sample_number,
				"remarks": audit.remarks,
			}
			for fieldname in measurement_fields:
				row[fieldname] = sample.get(fieldname)
			rows.append(row)

	return rows


def get_samples_by_audit(audit_names):
	samples = frappe.get_all(
		CHILD_DOCTYPE,
		filters={"parent": ["in", audit_names], "parenttype": PARENT_DOCTYPE},
		fields=["parent", "sample_number"] + ALL_MEASUREMENT_FIELDS,
		order_by="parent asc, sample_number asc",
		parent_doctype=PARENT_DOCTYPE,
	)

	grouped = {}
	for sample in samples:
		grouped.setdefault(sample.parent, []).append(sample)

	return grouped


def get_columns(measurement_fields):
	columns = [
		{
			"fieldname": "audit",
			"label": _("Audit"),
			"fieldtype": "Link",
			"options": PARENT_DOCTYPE,
			"width": 110,
		},
		{
			"fieldname": "audit_type",
			"label": _("Audit Type"),
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"fieldname": "farm",
			"label": _("Farm"),
			"fieldtype": "Link",
			"options": "Farm",
			"width": 120,
		},
		{
			"fieldname": "greenhouse",
			"label": _("Greenhouse"),
			"fieldtype": "Link",
			"options": "Greenhouse",
			"width": 120,
		},
		{
			"fieldname": "variety",
			"label": _("Variety"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 130,
		},
		{
			"fieldname": "recorded_on",
			"label": _("Recorded On"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"fieldname": "sample_number",
			"label": _("Sample No."),
			"fieldtype": "Int",
			"width": 90,
		},
	]

	# Labels come from the child doctype so the report follows any relabelling
	# there instead of keeping its own copy.
	child_meta = frappe.get_meta(CHILD_DOCTYPE)
	for fieldname in measurement_fields:
		df = child_meta.get_field(fieldname)
		columns.append(
			{
				"fieldname": fieldname,
				"label": _(df.label) if df else _(fieldname),
				"fieldtype": "Float",
				"precision": 2,
				"width": 120,
			}
		)

	columns.append(
		{
			"fieldname": "remarks",
			"label": _("Remarks"),
			"fieldtype": "Data",
			"width": 200,
		}
	)

	return columns


def get_report_summary(data, measurement_fields):
	if not data:
		return []

	summary = [
		{
			"label": _("Audits"),
			"value": len({row["audit"] for row in data}),
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"label": _("Samples"),
			"value": len(data),
			"datatype": "Int",
			"indicator": "Blue",
		},
	]

	child_meta = frappe.get_meta(CHILD_DOCTYPE)
	for fieldname in measurement_fields:
		values = [flt(row.get(fieldname)) for row in data if row.get(fieldname) is not None]
		if not values:
			continue

		df = child_meta.get_field(fieldname)
		summary.append(
			{
				"label": _("Avg {0}").format(_(df.label) if df else _(fieldname)),
				"value": sum(values) / len(values),
				"datatype": "Float",
				"indicator": "Green",
			}
		)

	return summary
