"""Grade B holding for approved Discard Requests.

When a Discard Request is submitted (approved), each bucket's stems move from the warehouse they
were received into to the farm's "<Farm> Grade B" warehouse. The shelf is NOT touched: the bucket
stays on its shelf until the final discard. The bucket only reaches the quality app's discard list
once Production Settings > Grade B Discard Age (days) has passed since the move; the final discard
then takes the stems out of Grade B into Rejects.
"""

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime

ENTRY_TYPE = "Grade B Transfer"


def ensure_entry_type():
	if frappe.db.exists("Stock Entry Type", ENTRY_TYPE):
		return
	frappe.get_doc(
		{"doctype": "Stock Entry Type", "name": ENTRY_TYPE, "purpose": "Material Transfer"}
	).insert(ignore_permissions=True)


def grade_b_age_days():
	"""Production Settings > Grade B Discard Age, in days. No default: it must be filled in, otherwise
	approving a Discard Request is refused with instructions."""
	row = frappe.db.sql(
		"SELECT value FROM tabSingles WHERE doctype = 'Production Settings' AND field = 'grade_b_discard_age'"
	)
	if not row or row[0][0] in (None, ""):
		frappe.throw(
			"Grade B Discard Age is blank, so stems cannot be moved to the Grade B warehouse yet.<br><br>"
			"To fix it: open <b>Production Settings</b>, go to the <b>Grade B</b> tab, and enter "
			"<b>Grade B Discard Age</b> - the number of days stems stay in Grade B before they can be "
			"discarded (enter 0 for no waiting). Save, then approve this Discard Request again.",
			title="Set Grade B Discard Age first",
		)
	return cint(row[0][0])


def grade_b_warehouse(farm, company):
	"""'<Farm> Grade B - <abbr>', created on first use."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{farm} Grade B - {abbr}"
	if not frappe.db.exists("Warehouse", name):
		wh = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"{farm} Grade B",
				"company": company,
				"is_group": 0,
			}
		)
		if wh.meta.has_field("custom_farm"):
			wh.custom_farm = farm
		wh.insert(ignore_permissions=True)
	return name


def due_on(moved_on):
	"""Date from which a bucket moved to Grade B at `moved_on` may be discarded."""
	return add_days(get_datetime(moved_on).date(), grade_b_age_days())


def is_due(moved_on):
	return not moved_on or due_on(moved_on) <= now_datetime().date()


def _latest_receiving(bucket_id):
	rows = frappe.get_all(
		"Stock Entry",
		filters={
			"stock_entry_type": ["in", ["Receiving", "Late Receipt"]],
			"custom_bucket_id": bucket_id,
			"docstatus": 1,
		},
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	return frappe.get_doc("Stock Entry", rows[0].name) if rows else None


def _already_discarded(bucket_id, receiving):
	return bool(
		frappe.db.exists(
			"Stock Entry",
			{
				"stock_entry_type": "Discard",
				"custom_bucket_id": bucket_id,
				"docstatus": 1,
				"creation": [">", receiving.creation],
			},
		)
	)


def grade_b_row(bucket_id):
	"""The Grade B record for a bucket's current stems: the most recent Discard Request Bucket row
	that was moved to Grade B and not yet discarded. None when the bucket was never moved."""
	rows = frappe.get_all(
		"Discard Request Bucket",
		filters={"bucket_id": bucket_id, "parenttype": "Discard Request", "grade_b_entry": ["is", "set"]},
		fields=["name", "parent", "discarded", "grade_b_moved_on", "grade_b_qty", "grade_b_warehouse"],
		order_by="grade_b_moved_on desc",
		limit=1,
	)
	return rows[0] if rows and not rows[0].discarded else None


def _move_bucket(row, company, farm):
	bucket_id = row.bucket_id
	if grade_b_row(bucket_id):
		return "already in Grade B (moved by an earlier request)"
	receiving = _latest_receiving(bucket_id)
	if not receiving or not receiving.items:
		return "no receiving entry"
	if _already_discarded(bucket_id, receiving):
		return "already discarded"
	item = receiving.items[0]
	source = item.t_warehouse
	target = grade_b_warehouse(farm, company)
	if source == target:
		return "already in Grade B"
	on_hand = flt(
		frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": source}, "actual_qty")
	)
	qty = min(flt(item.qty), on_hand)
	if qty <= 0:
		return f"no stock of {item.item_code} in {source}"

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = ENTRY_TYPE
	se.purpose = "Material Transfer"
	se.company = company
	se.posting_date = now_datetime().date()
	se.posting_time = now_datetime().time()
	se.set_posting_time = 1
	se.from_warehouse = source
	se.to_warehouse = target
	for f in (
		"farm",
		"business_unit",
		"custom_business_unit",
		"custom_greenhouse",
		"custom_bucket_id",
		"custom_stem_length",
		"custom_harvest_date",
	):
		if se.meta.has_field(f) and receiving.get(f) is not None:
			se.set(f, receiving.get(f))
	line = se.append("items", {})
	line.item_code = item.item_code
	line.item_name = item.item_name
	line.qty = qty
	line.uom = item.uom
	line.stock_uom = item.stock_uom
	line.conversion_factor = item.conversion_factor
	line.s_warehouse = source
	line.t_warehouse = target
	line.expense_account = item.expense_account
	line.cost_center = item.cost_center
	line.allow_zero_valuation_rate = 1
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value(
		"Discard Request Bucket",
		row.name,
		{
			"grade_b_moved_on": now_datetime(),
			"grade_b_qty": int(qty),
			"grade_b_warehouse": target,
			"grade_b_entry": se.name,
			"in_grade_b": 1,
			"ready_for_discard": 1 if grade_b_age_days() == 0 else 0,
		},
		update_modified=False,
	)
	return None


def move_to_grade_b(doc, method=None):
	"""Discard Request on_submit. A bucket that cannot be moved (no stock, no receiving, ...) is
	left as it was, so it follows the old rules (listed straight away); the reason is logged."""
	ensure_entry_type()
	grade_b_age_days()  # refuse to approve while the setting is blank
	company = doc.company or "Karen Roses"
	skipped = []
	for row in doc.get("buckets") or []:
		if not row.bucket_id or row.discarded or row.get("grade_b_entry"):
			continue
		farm = row.farm or doc.farm
		if not farm:
			skipped.append(f"{row.bucket_id}: no farm")
			continue
		try:
			frappe.db.savepoint("grade_b_bucket")
			why = _move_bucket(row, company, farm)
			if why:
				skipped.append(f"{row.bucket_id}: {why}")
		except Exception as e:
			frappe.db.rollback(save_point="grade_b_bucket")
			skipped.append(f"{row.bucket_id}: {e}")
	if skipped:
		frappe.log_error(
			title=f"Grade B move: {len(skipped)} bucket(s) not moved on {doc.name}",
			message="\n".join(skipped),
		)


def grade_b_flags(row):
	"""(in_grade_b, ready_for_discard) for a Discard Request Bucket row, as 0/1.
	in_grade_b: moved to the Grade B warehouse and not discarded since.
	ready_for_discard: not discarded, and either never moved to Grade B or past the Grade B age."""
	if row.get("discarded"):
		return 0, 0
	if not row.get("grade_b_entry"):
		return 0, 1
	return 1, 1 if is_due(row.get("grade_b_moved_on")) else 0


def refresh_flags():
	"""Daily: store the two flags on every open bucket row so list views and reports stay current."""
	rows = frappe.get_all(
		"Discard Request Bucket",
		filters={"parenttype": "Discard Request", "discarded": 0},
		fields=["name", "grade_b_entry", "grade_b_moved_on", "discarded", "in_grade_b", "ready_for_discard"],
	)
	for r in rows:
		g, ready = grade_b_flags(r)
		if (cint(r.in_grade_b), cint(r.ready_for_discard)) != (g, ready):
			frappe.db.set_value(
				"Discard Request Bucket",
				r.name,
				{"in_grade_b": g, "ready_for_discard": ready},
				update_modified=False,
			)
