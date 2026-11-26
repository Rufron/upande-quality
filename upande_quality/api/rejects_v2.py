# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Rejects dashboard v2 (www/rejects-v2): one query for every reject line in a date window.

The page used to page through frappe.client.get_list for the Stock Entry headers (5,000 rows a call, up to 40,000),
then fetch the lines of those entries by name list in batches of 100. Here the header and its lines come back in one
statement, narrowed to the two reject types and the date window, as compact rows.
"""

import frappe
from frappe import _
from frappe.model.db_query import DatabaseQuery
from frappe.utils import add_days, getdate

TYPES = (("Field Rejects", "field"), ("Quarantine Rejects", "intake"))
MAX_ROWS = 200000
COLUMNS = ["kind", "entry", "date", "farm", "gh", "owner", "item_code", "qty"]


@frappe.whitelist()
def get_rejects(date_from=None, date_to=None):
	"""Every reject line (Field + Quarantine) posted in [date_from, date_to], as compact arrays.

	Row layout is `columns`: kind (field|intake), Stock Entry, posting date, farm, greenhouse, owner, item code, qty.
	Cancelled entries are included, as they were when the page read them through get_list."""
	if not frappe.has_permission("Stock Entry", "read"):
		frappe.throw(_("You do not have permission to read Stock Entries."), frappe.PermissionError)
	cond, p = [], {}
	if date_from:
		cond.append("se.posting_date >= %(f)s")
		p["f"] = getdate(date_from)
	if date_to:
		# sargable upper bound, same as posting_date <= date_to for a date column
		cond.append("se.posting_date < %(t)s")
		p["t"] = add_days(getdate(date_to), 1)
	# per-user permission rules (User Permissions etc.) exactly as get_list applies them
	match = DatabaseQuery("Stock Entry").build_match_conditions()
	if match:
		cond.append("(" + match.replace("`tabStock Entry`", "se") + ")")
	rows = []
	for stype, kind in TYPES:
		p["ty"] = stype
		rows += [
			[kind] + list(r)
			for r in frappe.db.sql(
				"""SELECT se.name, se.posting_date, se.farm, se.custom_greenhouse, se.owner, d.item_code, d.qty
				FROM `tabStock Entry` se
				JOIN `tabStock Entry Detail` d ON d.parent = se.name
				WHERE se.stock_entry_type = %(ty)s AND se.docstatus IN (0, 1, 2)"""
				+ "".join(" AND " + c for c in cond)
				+ " ORDER BY se.posting_date DESC LIMIT %(lim)s",
				dict(p, lim=MAX_ROWS),
			)
		]
	for r in rows:
		r[2] = str(r[2] or "")
		r[3] = r[3] or ""
		r[4] = r[4] or ""
		r[5] = r[5] or ""
		r[6] = r[6] or ""
		r[7] = float(r[7] or 0)
	return {"columns": COLUMNS, "rows": rows, "truncated": len(rows) >= MAX_ROWS}
