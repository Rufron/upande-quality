"""The one rule for what can be discarded, shared by the nightly Auto Discard Request (tasks.py), the Discard
Request form's Fetch Buckets and the dashboard (api/discard_requests.py) and the app's discard list (mobile/api.py),
so a request and the app always agree.

A bucket is discardable when it is on a shelf, is at least its discard age (days since its latest Harvesting entry,
else since it was shelved; the age comes from the variety, its item group or Production Settings), and none of the
stems of its current harvest are allocated. Partly allocated buckets are not discardable.

An allocation to an order whose delivery date has passed does not hold a bucket back, unless the bucket was issued
or packed into a box label for that order. When a request listing such a bucket is submitted, that allocation is
released (release_past_due): the bucket comes off the order's pick list, with a note on the order."""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, nowdate

from upande_quality.upande_quality.doctype.production_settings.production_settings import (
	discard_age_resolver,
)


def shelf_buckets(farms=None):
	"""Every bucket on a shelf (at `farms` when given), keyed by upper-case bucket id: its shelf line plus `since`
	(the date its age counts from), `age_days`, `discard_age`, `allocated` and `discardable`."""
	cond, inner, params = "", "", {}
	if farms:
		cond, inner, params = (
			" AND sh.farm IN %(farms)s",
			" AND sh2.farm IN %(farms)s",
			{"farms": tuple(farms)},
		)
	rows = frappe.db.sql(
		f"""SELECT si.bucket_id, si.parent AS shelf, si.date_added, si.variety, si.stem_qty, si.stem_length,
			si.greenhouse, si.warehouse, sh.farm, it.item_group,
			COALESCE(h.hd, DATE(si.date_added)) AS since, IF(h.hd IS NULL, 'shelved', 'harvest') AS date_source
		FROM `tabShelf Item` si
		JOIN `tabShelf` sh ON sh.name = si.parent
		LEFT JOIN `tabItem` it ON it.name = si.variety
		LEFT JOIN (
			SELECT custom_bucket_id AS b, MAX(posting_date) AS hd FROM `tabStock Entry`
			WHERE stock_entry_type = 'Harvesting' AND custom_bucket_id IN (
				SELECT si2.bucket_id FROM `tabShelf Item` si2 JOIN `tabShelf` sh2 ON sh2.name = si2.parent
				WHERE si2.bucket_id > ''{inner})
			GROUP BY custom_bucket_id
		) h ON h.b = si.bucket_id
		WHERE si.bucket_id > ''{cond}""",
		params,
		as_dict=True,
	)
	age_for = discard_age_resolver()
	today = getdate(nowdate())
	out = {}
	for r in rows:
		key = str(r.bucket_id).upper()
		if not r.since or (key in out and str(out[key].since) >= str(r.since)):
			continue  # no date to age from, or an older line of a bucket shelved twice
		r.age_days = date_diff(today, r.since)
		r.discard_age = age_for(r.variety)
		r.allocated = False
		r.past_due = []
		out[key] = r

	# Bucket Allocation Status is never cleared and buckets are reused: only rows with stems still allocated for
	# the current harvest (harvested no earlier than the bucket's age date) count. A line for an order whose
	# delivery date has passed is set aside in `past_due`; it holds the bucket only once issued or boxed.
	today_s = str(today)
	for a in frappe.db.sql(
		"""SELECT bas.bucket_id, bas.harvest_date, bas.item_code, bas.stem_length,
			ba.sales_order, ba.sales_order_item, ba.quantity_allocated, so.delivery_date
		FROM `tabBucket Allocation Status` bas
		LEFT JOIN `tabBucket Allocations` ba ON ba.parent = bas.name AND IFNULL(ba.cancelled, 0) = 0
			AND IFNULL(ba.issued, 0) = 0 AND ba.quantity_allocated > 0
		LEFT JOIN `tabSales Order` so ON so.name = ba.sales_order
		WHERE bas.allocated_quantity > 0""",
		as_dict=True,
	):
		b = out.get(str(a.bucket_id or "").upper())
		if not b or (a.harvest_date and str(a.harvest_date) < str(b.since)):
			continue
		if a.sales_order_item and a.delivery_date and str(a.delivery_date) < today_s:
			b.past_due.append(a)
		else:
			b.allocated = True
	_held_by_processed(out)

	for b in out.values():
		b.discardable = not b.allocated and b.age_days >= b.discard_age
	return out


def _held_by_processed(out):
	"""A past-due order still holds a bucket once its pick list rows for it were issued or packed into a box label."""
	keys = sorted({k for k, b in out.items() if b.past_due and not b.allocated})
	if not keys:
		return
	done = {
		(str(r.bucket).upper(), r.sales_order)
		for r in frappe.db.sql(
			"""SELECT DISTINCT p.bucket, o.sales_order FROM `tabPick List Item` p
			JOIN `tabOrder Pick List` o ON o.name = p.parent
			WHERE p.parenttype = 'Order Pick List' AND p.bucket IN %(b)s
			  AND (IFNULL(p.issued, 0) = 1 OR IFNULL(p.custom_box_label, '') != '')""",
			{"b": tuple(keys)},
			as_dict=True,
		)
	}
	for k in keys:
		b = out[k]
		if any((k, line.sales_order) in done for line in b.past_due):
			b.allocated = True


def release_past_due(request_name):
	"""After a Discard Request is submitted (background job): every bucket on it held only by orders whose delivery
	date has passed is unallocated from them -- its rows come off the order's pick list (an emptied pick list goes)
	-- and the order and its pick list get a note."""
	try:
		from upande_packhouse.upande_packhouse.page.sales_allocation_v2.sales_allocation_v2 import (
			unallocate_bucket_from_opl,
		)
	except ImportError:
		return
	doc = frappe.get_doc("Discard Request", request_name)
	keys = {str(r.bucket_id).upper() for r in doc.buckets if r.bucket_id and not r.discarded}
	farms = sorted({r.farm for r in doc.buckets if r.farm})
	if not keys or not farms:
		return
	shelf = shelf_buckets(farms)
	for key in sorted(keys):
		b = shelf.get(key)
		if not b or b.allocated or not b.past_due:
			continue
		for line in b.past_due:
			opls = frappe.db.sql_list(
				"""SELECT DISTINCT parent FROM `tabPick List Item`
				WHERE parenttype = 'Order Pick List' AND sales_order_item = %s AND bucket = %s""",
				(line.sales_order_item, b.bucket_id),
			)
			res = unallocate_bucket_from_opl(line.sales_order_item, b.bucket_id, line.stem_length or "")
			if not (res or {}).get("success"):
				frappe.log_error(
					title="Discard: releasing a past-due allocation failed",
					message=f"{request_name} bucket={b.bucket_id} line={line.sales_order_item}\n{res}",
				)
				continue
			note = _(
				"Bucket {0} taken off this order by Discard Request {1}: past its discard age and the delivery "
				"date {2} has passed. {3} stems of {4} unallocated."
			).format(
				b.bucket_id,
				request_name,
				frappe.utils.formatdate(line.delivery_date),
				int(line.quantity_allocated)
				if frappe.utils.flt(line.quantity_allocated).is_integer()
				else line.quantity_allocated,
				line.item_code,
			)
			for dt, name in [("Sales Order", line.sales_order)] + [("Order Pick List", o) for o in opls]:
				if frappe.db.exists(dt, name):
					frappe.get_doc(dt, name).add_comment("Comment", note)
			frappe.db.commit()  # nosemgrep -- each release stands on its own


def todays_requests(farm=None):
	"""Submitted Discard Requests created today: name -> creation. The app shows only these."""
	return {
		r[0]: str(r[1])
		for r in frappe.db.sql(
			"SELECT name, creation FROM `tabDiscard Request` WHERE docstatus = 1 AND creation >= %s"
			+ (
				" AND name IN (SELECT parent FROM `tabDiscard Request Bucket` WHERE farm = %s)"
				if farm
				else ""
			),
			(nowdate(), farm) if farm else (nowdate(),),
		)
	}


def listed_today():
	"""Upper-case ids of buckets already on a request made today (draft or submitted, not cancelled or rejected),
	so running the nightly job or Fetch Buckets again the same day does not list a bucket twice."""
	return {
		str(r[0]).upper()
		for r in frappe.db.sql(
			"""SELECT b.bucket_id FROM `tabDiscard Request Bucket` b
			JOIN `tabDiscard Request` d ON d.name = b.parent
			WHERE b.parenttype = 'Discard Request' AND d.docstatus < 2
			  AND IFNULL(d.workflow_state, '') != 'Rejected' AND d.creation >= %s AND b.bucket_id > ''""",
			nowdate(),
		)
	}
