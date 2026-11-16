# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Rejects dashboard (www/rejects): every reject stock entry line with its bucket, receiving batch, farm, the Quality
Reporting record it came from and that record's reasons, and who rejected it.

A reject entry carries the bucket and the receiving batch id but no reason. At intake a bucket is quarantined before
its stems are rejected: the intake QC moves it to quarantine ("Receiving Quarantined", same bucket and batch) and
writes a Quarantined Quality Reporting record (one per variety, with the issues found) in the same submit. So an intake
reject links to the Quarantined record of whoever quarantined the bucket, for the same variety, closest in time to that
quarantine move. Accepted and Rejected records are never linked; a reject with no quarantine move is flagged."""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

REJECT_TYPES = (
	"Field Rejects",
	"Quarantine Rejects",
	"Coldroom Rejects",
	"Packhouse Rejects",
	"Airport rejects",
)
STAGE = {
	"Field Rejects": "Field",
	"Quarantine Rejects": "Intake",
	"Coldroom Rejects": "Coldroom",
	"Packhouse Rejects": "Packhouse",
	"Airport rejects": "Airport",
}
QC_WINDOW_SECONDS = 15 * 60
QUARANTINED = ("Quarantined", "Quarantine")
MAX_LINES = 20000


@frappe.whitelist()
def get_rejects(from_date=None, to_date=None, q=None):
	"""Reject lines by posting date, or for one bucket / receiving batch (`q`, all time).
	-> { rows: [...], batches: {batch: received/rejected buckets and stems}, truncated }"""
	if not frappe.has_permission("Stock Entry", "read"):
		frappe.throw(_("You do not have permission to read Stock Entries."), frappe.PermissionError)
	q = (q or "").strip().upper()
	cond, params = "", {"types": REJECT_TYPES}
	if q:
		cond = " AND (UPPER(se.custom_bucket_id) = %(q)s OR UPPER(se.custom_receiving_batch_id) = %(q)s)"
		params["q"] = q
	else:
		if from_date:
			cond += " AND se.posting_date >= %(from_date)s"
			params["from_date"] = from_date
		if to_date:
			cond += " AND se.posting_date <= %(to_date)s"
			params["to_date"] = to_date
	lines = frappe.db.sql(
		f"""SELECT se.name, se.stock_entry_type, se.posting_date, se.creation, se.owner, se.farm,
			se.custom_greenhouse, se.custom_bucket_id, se.custom_receiving_batch_id, se.remarks,
			d.item_code, d.qty, d.s_warehouse, d.t_warehouse, d.custom_rejection_reason, d.farm AS line_farm
		FROM `tabStock Entry` se JOIN `tabStock Entry Detail` d ON d.parent = se.name
		WHERE se.docstatus = 1 AND se.stock_entry_type IN %(types)s{cond}
		ORDER BY se.creation DESC LIMIT {MAX_LINES}""",
		params,
		as_dict=True,
	)
	quarantines = _quarantine_moves(lines)
	for l in lines:
		l.quarantine = quarantine_for(l, quarantines) if l.stock_entry_type == "Quarantine Rejects" else None
	qcs = _qc_records(lines)
	batches = _batches({l.custom_receiving_batch_id for l in lines if l.custom_receiving_batch_id})
	names = {
		u.name: u.full_name
		for u in frappe.get_all(
			"User",
			filters={"name": ["in", list(_people(lines)) or ["-"]]},
			fields=["name", "full_name"],
		)
	}
	rows = []
	for l in lines:
		qc = None
		if l.quarantine:
			qc = match_qc(l, qcs.get(l.quarantine.owner, []), l.quarantine.creation, QUARANTINED)
		elif l.stock_entry_type not in ("Field Rejects", "Quarantine Rejects"):
			qc = match_qc(l, [x for x in qcs.get(l.owner, []) if x.control_action != "Accepted"], l.creation)
		qn = l.quarantine
		reason = l.custom_rejection_reason or ""
		if qc:
			reason = "; ".join(
				f"{p.parameter_name} x{p.count}" if p.count else p.parameter_name for p in qc.params
			)
			reason = reason or qc.custom_remarks or ""
		if not reason and l.stock_entry_type != "Field Rejects":
			reason = l.remarks or ""
		rows.append(
			{
				"entry": l.name,
				"stage": STAGE.get(l.stock_entry_type, l.stock_entry_type),
				"type": l.stock_entry_type,
				"date": str(l.posting_date),
				"time": str(l.creation)[11:16],
				"bucket": str(l.custom_bucket_id or "").upper(),
				"batch": l.custom_receiving_batch_id or "",
				"farm": entry_farm(l),
				"greenhouse": l.custom_greenhouse or "",
				"variety": l.item_code,
				"stems": flt(l.qty),
				"reason": reason,
				"qc": qc.name if qc else "",
				"qc_action": qc.control_action if qc else "",
				"from_warehouse": l.s_warehouse or "",
				"to_warehouse": l.t_warehouse or "",
				"rejected_by": l.owner,
				"rejected_by_name": names.get(l.owner) or l.owner,
				"quarantined": 1 if qn else 0,
				"not_quarantined": 1 if l.stock_entry_type == "Quarantine Rejects" and not qn else 0,
				"quarantine_entry": qn.name if qn else "",
				"quarantined_at": str(qn.creation)[:16] if qn else "",
				"quarantined_by": qn.owner if qn else "",
				"quarantined_by_name": (names.get(qn.owner) or qn.owner) if qn else "",
			}
		)
	return {"rows": rows, "batches": batches, "truncated": len(lines) >= MAX_LINES}


def entry_farm(line):
	"""The entry's farm; intake rejects leave it blank, so fall back to the greenhouse ("Karen GH 08 - KR" -> Karen)
	and then the source warehouse."""
	return (
		line.farm
		or line.line_farm
		or (line.custom_greenhouse or "").split(" GH ")[0]
		or (line.s_warehouse or "").split(" ")[0]
		or "?"
	)


def match_qc(line, candidates, at, actions=None):
	"""Of `candidates` (one person's QC records), the one for the line's variety closest to `at`, within the window,
	and with one of `actions` when given."""
	t = get_datetime(at)
	best, best_gap = None, None
	for qc in candidates:
		if actions and qc.control_action not in actions:
			continue
		if qc.variety and line.item_code and qc.variety != line.item_code:
			continue
		gap = abs((get_datetime(qc.creation) - t).total_seconds())
		if gap <= QC_WINDOW_SECONDS and (best_gap is None or gap < best_gap):
			best, best_gap = qc, gap
	return best


def quarantine_for(line, quarantines):
	"""The move that quarantined this bucket before the reject: same receiving batch when both carry one (bucket ids
	are reused), else the latest one before the reject."""
	bucket = str(line.custom_bucket_id or "").upper()
	before = [q for q in quarantines.get(bucket, []) if str(q.creation) <= str(line.creation)]
	if line.custom_receiving_batch_id and any(q.custom_receiving_batch_id for q in before):
		before = [q for q in before if q.custom_receiving_batch_id == line.custom_receiving_batch_id]
	return max(before, key=lambda q: str(q.creation)) if before else None


def _quarantine_moves(lines):
	"""Receiving Quarantined entries for the buckets of the intake rejects, by upper-case bucket id."""
	buckets = {
		str(l.custom_bucket_id).upper()
		for l in lines
		if l.stock_entry_type == "Quarantine Rejects" and l.custom_bucket_id
	}
	out = {}
	if buckets:
		for q in frappe.db.sql(
			"""SELECT name, UPPER(custom_bucket_id) AS bucket, creation, owner, custom_receiving_batch_id
			FROM `tabStock Entry`
			WHERE docstatus = 1 AND stock_entry_type = 'Receiving Quarantined'
			  AND UPPER(custom_bucket_id) IN %(b)s""",
			{"b": tuple(buckets)},
			as_dict=True,
		):
			out.setdefault(q.bucket, []).append(q)
	return out


def _people(lines):
	return {l.owner for l in lines} | {l.quarantine.owner for l in lines if l.get("quarantine")}


def _qc_records(lines):
	"""Quality Reporting records (not Accepted) by owner, made around the reject lines and their quarantine moves,
	each with its `params` (issues)."""
	if not lines:
		return {}
	created = [str(l.creation) for l in lines] + [
		str(l.quarantine.creation) for l in lines if l.get("quarantine")
	]
	qcs = frappe.db.sql(
		"""SELECT name, owner, creation, variety, control_point, control_action, custom_remarks
		FROM `tabQuality Reporting`
		WHERE control_action != 'Accepted' AND owner IN %(owners)s
		  AND creation BETWEEN DATE_SUB(%(first)s, INTERVAL 15 MINUTE) AND DATE_ADD(%(last)s, INTERVAL 15 MINUTE)""",
		{"owners": tuple(_people(lines)), "first": min(created), "last": max(created)},
		as_dict=True,
	)
	params = {}
	if qcs:
		for p in frappe.db.sql(
			"""SELECT parent, parameter_name, `count` FROM `tabQuality Parameter`
			WHERE parenttype = 'Quality Reporting' AND parent IN %(names)s ORDER BY idx""",
			{"names": tuple(qc.name for qc in qcs)},
			as_dict=True,
		):
			params.setdefault(p.parent, []).append(p)
	out = {}
	for qc in qcs:
		qc.params = params.get(qc.name, [])
		out.setdefault(qc.owner, []).append(qc)
	return out


def _batches(batch_ids):
	"""Buckets and stems received and rejected per receiving batch (whole batch, not only the rows shown)."""
	if not batch_ids:
		return {}
	out = {}
	for b in _batch_totals(("Receiving", "Late Receipt"), batch_ids):
		out[b.batch] = {
			"received_buckets": b.buckets,
			"received_stems": flt(b.stems),
			"rejected_buckets": 0,
			"rejected_stems": 0,
		}
	for b in _batch_totals(REJECT_TYPES, batch_ids):
		if b.batch in out:
			out[b.batch].update(rejected_buckets=b.buckets, rejected_stems=flt(b.stems))
	return out


def _batch_totals(types, batch_ids):
	return frappe.db.sql(
		"""SELECT se.custom_receiving_batch_id AS batch, COUNT(DISTINCT UPPER(se.custom_bucket_id)) AS buckets,
			SUM(d.qty) AS stems
		FROM `tabStock Entry` se JOIN `tabStock Entry Detail` d ON d.parent = se.name
		WHERE se.docstatus = 1 AND se.stock_entry_type IN %(types)s AND se.custom_receiving_batch_id IN %(b)s
		GROUP BY se.custom_receiving_batch_id""",
		{"types": tuple(types), "b": tuple(batch_ids)},
		as_dict=True,
	)
