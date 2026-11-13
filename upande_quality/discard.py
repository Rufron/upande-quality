"""The one rule for what can be discarded, shared by the nightly Auto Discard Request (tasks.py), the Discard
Request form's Fetch Buckets and the dashboard (api/discard_requests.py) and the app's discard list (mobile/api.py),
so a request and the app always agree.

A bucket is discardable when it is on a shelf and at least its discard age (days since its latest Harvesting entry,
else since it was shelved; the age comes from the variety, its item group or Production Settings). Past its discard
age a bucket is discarded even when it is allocated to an order: allocation only protects buckets that are not yet
old enough. `allocated` is still reported on each bucket for information."""

import frappe
from frappe.utils import date_diff, flt, getdate, nowdate

from upande_quality.upande_quality.doctype.production_settings.production_settings import (
	discard_age_resolver,
)

REJECTS = "Rejects - KR"


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
		out[key] = r

	# Bucket Allocation Status is never cleared and buckets are reused: only rows with stems still allocated for
	# the current harvest (harvested no earlier than the bucket's age date) count.
	for a in frappe.get_all(
		"Bucket Allocation Status",
		filters={"allocated_quantity": [">", 0]},
		fields=["bucket_id", "harvest_date"],
		limit_page_length=0,
	):
		b = out.get(str(a.bucket_id or "").upper())
		if b and (not a.harvest_date or str(a.harvest_date) >= str(b.since)):
			b.allocated = True

	for b in out.values():
		b.discardable = b.age_days >= b.discard_age
	return out


def bucket_stock(bucket_id, since, item_code):
	"""Where a bucket's stems are now: {warehouse: qty} netted over every submitted stock entry for the bucket since
	`since` (its latest receiving), so a bucket that was moved (remote transfer, allocated to Sold, issued) is found
	where it is, not where it was received. Bucket ids are only on the entry header. Rejects (discarded) is left out."""
	bal = {}
	for r in frappe.db.sql(
		"""SELECT d.s_warehouse, d.t_warehouse, d.qty FROM `tabStock Entry` se
		JOIN `tabStock Entry Detail` d ON d.parent = se.name
		WHERE se.docstatus = 1 AND se.custom_bucket_id = %s AND se.creation >= %s AND d.item_code = %s""",
		(bucket_id, since, item_code),
		as_dict=True,
	):
		if r.s_warehouse:
			bal[r.s_warehouse] = bal.get(r.s_warehouse, 0) - flt(r.qty)
		if r.t_warehouse:
			bal[r.t_warehouse] = bal.get(r.t_warehouse, 0) + flt(r.qty)
	return {w: q for w, q in bal.items() if q > 0.001 and w != REJECTS}


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
