# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Cold Store Stock Take buckets gained Stems (qty) and the stock take Total Buckets /
# Total Stems. Rows synced before that have no stems: fill them the way the sync now
# does — the bucket's Shelf Item rows, or its latest Harvesting Stock Entry when it is
# not on a shelf — and set every stock take's totals. Only rows still empty are
# touched, so running it again changes nothing.

import frappe
from frappe.utils import flt


def execute():
	if not frappe.db.has_column("Cold Store Stock Take Bucket", "qty"):
		return
	rows = frappe.get_all(
		"Cold Store Stock Take Bucket",
		filters={"qty": ["is", "not set"], "parenttype": "Cold Store Stock Take"},
		fields=["name", "bucket_id"],
	)
	buckets = list({r.bucket_id for r in rows if r.bucket_id})
	stems = {}
	if buckets:
		for si in frappe.get_all(
			"Shelf Item", filters={"bucket_id": ["in", buckets]}, fields=["bucket_id", "stem_qty"]
		):
			stems[si.bucket_id] = stems.get(si.bucket_id, 0) + flt(si.stem_qty)
		unshelved = [b for b in buckets if b not in stems]
		if unshelved:
			for r in frappe.db.sql(
				"""SELECT se.custom_bucket_id AS bucket_id, SUM(sed.qty) AS qty
				FROM `tabStock Entry` se
				JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE se.name = (
					SELECT s2.name FROM `tabStock Entry` s2
					WHERE s2.stock_entry_type = 'Harvesting' AND s2.custom_bucket_id = se.custom_bucket_id
					ORDER BY s2.posting_date DESC, s2.posting_time DESC, s2.creation DESC LIMIT 1
				) AND se.custom_bucket_id IN %(ids)s
				GROUP BY se.custom_bucket_id""",
				{"ids": tuple(unshelved)},
				as_dict=True,
			):
				stems[r.bucket_id] = flt(r.qty)
	for r in rows:
		if r.bucket_id in stems:
			frappe.db.set_value(
				"Cold Store Stock Take Bucket", r.name, "qty", stems[r.bucket_id], update_modified=False
			)

	for name in frappe.get_all("Cold Store Stock Take", pluck="name"):
		totals = frappe.db.sql(
			"""SELECT COUNT(*), COALESCE(SUM(qty), 0) FROM `tabCold Store Stock Take Bucket`
			WHERE parent = %s AND parenttype = 'Cold Store Stock Take'""",
			name,
		)[0]
		frappe.db.set_value(
			"Cold Store Stock Take",
			name,
			{"total_buckets": totals[0], "total_stems": totals[1]},
			update_modified=False,
		)
