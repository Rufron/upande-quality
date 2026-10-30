# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Discard Requests dashboard (www/discard-requests).

Reads: every request with where it came from (the nightly job runs as Administrator, everything else is a person),
its state and how much of it has been discarded; plus filters for a new request.

Writes: build requests for many farms / varieties / lengths / item groups / cold rooms in one go. The shelf is scanned
once with every filter applied; the buckets that qualify (old enough, not already on an open request, not allocated)
are grouped by farm, variety and length so the user can untick groups, and one request is created per farm, the same as
the nightly job does. Where the Discard Request workflow is active a request is sent for approval (or approved when the
user's roles allow it); without a workflow it is submitted.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_workflow_name
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, today

MAX_BUCKETS = 20000


def _list(v):
	v = frappe.parse_json(v) if isinstance(v, str) and v[:1] in "[{" else v
	if not v:
		return []
	if isinstance(v, str):
		return [x.strip() for x in v.split(",") if x.strip()]
	return [x for x in v if x]


def _need(ptype="read"):
	if not frappe.has_permission("Discard Request", ptype):
		frappe.throw(_("You do not have permission to {0} Discard Requests.").format(_(ptype)), frappe.PermissionError)


def _state(docstatus, workflow_state):
	if cint(docstatus) == 2:
		return "Cancelled"
	return workflow_state or ("Approved" if cint(docstatus) == 1 else "Draft")


def _location(text):
	"""'Torongo GH 04 - KR' -> 'Torongo': the part of a greenhouse or cold room name before GH / Receiving / Cold."""
	t = text or ""
	if " - " in t:
		t = t.rsplit(" - ", 1)[0]
	for clip in ("GH", "Receiving", "Cold"):
		if clip in t:
			t = t.split(clip)[0].strip()
			break
	return t.strip()


def _workflow():
	"""{name, can_approve, can_reject} for the active Discard Request workflow, or None."""
	name = get_workflow_name("Discard Request")
	if not name:
		return None
	roles = set(frappe.get_roles())
	moves = frappe.get_all(
		"Workflow Transition", filters={"parent": name}, fields=["action", "allowed"], limit_page_length=0
	)
	can = lambda a: any(m["action"] == a and (m["allowed"] == "All" or m["allowed"] in roles) for m in moves)
	return {"name": name, "can_approve": can("Approve"), "can_reject": can("Reject"), "can_send": can("Submit for Approval")}


@frappe.whitelist()
def get_options():
	"""Everything the filters and the new-request form offer."""
	_need()
	shelf = lambda col: [
		r[0]
		for r in frappe.db.sql(
			f"SELECT DISTINCT {col} FROM `tabShelf Item` WHERE IFNULL({col}, '') != '' ORDER BY {col}"
		)
	]
	farms = [r[0] for r in frappe.db.sql("SELECT DISTINCT farm FROM `tabShelf` WHERE IFNULL(farm, '') != '' ORDER BY farm")]
	return {
		"farms": farms,
		"varieties": shelf("variety"),
		"lengths": shelf("stem_length"),
		"item_groups": [
			r[0]
			for r in frappe.db.sql(
				"""SELECT DISTINCT it.item_group FROM `tabShelf Item` si JOIN `tabItem` it ON it.name = si.variety
				WHERE IFNULL(it.item_group, '') != '' ORDER BY it.item_group"""
			)
		],
		"coldrooms": [r[0] for r in frappe.db.sql("SELECT name FROM `tabWarehouse` WHERE name LIKE '%%Cold%%' ORDER BY name")],
		"discard_age": flt(frappe.db.get_single_value("Production Settings", "discard_age")) or 5.0,
		"workflow": _workflow(),
		"can_create": bool(frappe.has_permission("Discard Request", "create")),
	}


def _requests(origin=None, state=None, farms=None, date_from=None, date_to=None):
	cond, p = ["1=1"], {}
	if origin == "System":
		cond.append("dr.owner = 'Administrator'")
	elif origin == "User":
		cond.append("dr.owner != 'Administrator'")
	if farms:
		cond.append("dr.farm IN %(farms)s")
		p["farms"] = tuple(farms)
	if date_from:
		cond.append("dr.requested_date >= %(f)s")
		p["f"] = date_from
	if date_to:
		cond.append("dr.requested_date <= %(t)s")
		p["t"] = date_to
	rows = frappe.db.sql(
		f"""SELECT dr.name, dr.owner, dr.requested_by, dr.requested_date, dr.creation, dr.farm, dr.variety, dr.length,
			dr.item_group, dr.coldroom, dr.age_threshold, dr.age_unit, dr.workflow_state, dr.docstatus, dr.remarks,
			COUNT(b.name) AS buckets, IFNULL(SUM(b.stem_qty), 0) AS stems,
			IFNULL(SUM(IF(b.discarded = 1, 1, 0)), 0) AS done_buckets,
			IFNULL(SUM(IF(b.discarded = 1, b.stem_qty, 0)), 0) AS done_stems
		FROM `tabDiscard Request` dr
		LEFT JOIN `tabDiscard Request Bucket` b ON b.parent = dr.name AND b.parenttype = 'Discard Request'
		WHERE {' AND '.join(cond)}
		GROUP BY dr.name ORDER BY dr.creation DESC LIMIT 3000""",
		p,
		as_dict=True,
	)
	out = []
	for r in rows:
		r["origin"] = "System" if r["owner"] == "Administrator" else "User"
		r["state"] = _state(r["docstatus"], r["workflow_state"])
		r["waiting_stems"] = r["stems"] - r["done_stems"] if r["state"] == "Approved" else 0
		r["creation"] = str(r["creation"])
		r["requested_date"] = str(r["requested_date"] or "")
		if state and r["state"] != state:
			continue
		out.append(r)
	return out


@frappe.whitelist()
def get_overview(origin=None, state=None, farms=None, date_from=None, date_to=None):
	"""Requests (newest first) with totals and the breakdowns behind the charts."""
	_need()
	reqs = _requests(origin, state, _list(farms), date_from, date_to)
	k = {"requests": len(reqs), "system": 0, "user": 0, "buckets": 0, "stems": 0, "done_stems": 0, "waiting_stems": 0, "pending": 0}
	by_farm, by_week = {}, {}
	for r in reqs:
		k["system" if r["origin"] == "System" else "user"] += 1
		k["buckets"] += r["buckets"]
		k["stems"] += flt(r["stems"])
		k["done_stems"] += flt(r["done_stems"])
		k["waiting_stems"] += flt(r["waiting_stems"])
		k["pending"] += r["state"] in ("Draft", "Pending Approval")
		f = by_farm.setdefault(r["farm"] or "—", {"farm": r["farm"] or "—", "system": 0, "user": 0})
		f["system" if r["origin"] == "System" else "user"] += flt(r["stems"])
		if r["requested_date"]:
			d = getdate(r["requested_date"]).isocalendar()
			w = by_week.setdefault(f"{d[0]}-W{d[1]:02d}", {"week": f"{d[0]}-W{d[1]:02d}", "system": 0, "user": 0})
			w["system" if r["origin"] == "System" else "user"] += flt(r["stems"])
	by_variety = []
	if reqs:
		by_variety = frappe.db.sql(
			"""SELECT b.variety, SUM(b.stem_qty) AS stems FROM `tabDiscard Request Bucket` b
			WHERE b.parenttype = 'Discard Request' AND b.parent IN %(n)s AND IFNULL(b.variety, '') != ''
			GROUP BY b.variety ORDER BY stems DESC LIMIT 10""",
			{"n": tuple(r["name"] for r in reqs)},
			as_dict=True,
		)
	return {
		"kpis": k,
		"requests": reqs,
		"by_farm": sorted(by_farm.values(), key=lambda x: -(x["system"] + x["user"]))[:12],
		"by_week": [by_week[w] for w in sorted(by_week)],
		"by_variety": by_variety,
	}


@frappe.whitelist()
def get_request(name):
	"""One request: its header, the buckets on it and whether each has been discarded."""
	_need()
	doc = frappe.get_doc("Discard Request", name)
	doc.check_permission("read")
	buckets = [
		{k: (str(b.get(k)) if k == "harvest_date" and b.get(k) else b.get(k)) for k in
		 ("bucket_id", "farm", "greenhouse", "shelf", "variety", "stem_length", "stem_qty", "age_days", "harvest_date", "discarded")}
		for b in doc.buckets
	]
	return {
		"name": doc.name,
		"origin": "System" if doc.owner == "Administrator" else "User",
		"state": _state(doc.docstatus, doc.get("workflow_state")),
		"owner": doc.owner,
		"requested_by": doc.requested_by,
		"requested_date": str(doc.requested_date or ""),
		"approved_by": doc.approved_by,
		"approval_date": str(doc.approval_date or ""),
		"farm": doc.farm,
		"company": doc.company,
		"variety": doc.variety,
		"length": doc.length,
		"item_group": doc.item_group,
		"coldroom": doc.coldroom,
		"age_threshold": doc.age_threshold,
		"age_unit": doc.age_unit,
		"remarks": doc.remarks,
		"buckets": buckets,
		"workflow": _workflow(),
	}


def _candidates(farms, varieties, lengths, item_groups, coldrooms, age_threshold, age_unit):
	"""Shelved buckets that qualify, plus what was left out and why. Age is from the shelf line's harvest date (the
	nightly job reads the Harvesting stock entry, which agrees; that lookup scans every stock entry and takes minutes, so it is
	not repeated per click), else from when the bucket was shelved."""
	threshold = flt(age_threshold) / 24.0 if age_unit == "Hours" else flt(age_threshold)
	cond, p = ["si.bucket_id IS NOT NULL AND si.bucket_id != ''"], {}
	for col, vals, key in (
		("sh.farm", farms, "farms"),
		("si.variety", varieties, "varieties"),
		("si.stem_length", lengths, "lengths"),
		("it.item_group", item_groups, "groups"),
	):
		if vals:
			cond.append(f"{col} IN %({key})s")
			p[key] = tuple(vals)
	rows = frappe.db.sql(
		f"""SELECT si.bucket_id, si.parent AS shelf, si.date_added, si.variety, si.stem_qty, si.stem_length,
			si.greenhouse, si.warehouse, sh.farm, it.item_group, si.harvest_date
		FROM `tabShelf Item` si
		INNER JOIN `tabShelf` sh ON sh.name = si.parent
		LEFT JOIN `tabItem` it ON it.name = si.variety
		WHERE {' AND '.join(cond)} LIMIT {MAX_BUCKETS}""",
		p,
		as_dict=True,
	)
	open_names = [
		r[0]
		for r in frappe.db.sql(
			"SELECT name FROM `tabDiscard Request` WHERE docstatus != 2 AND IFNULL(workflow_state, '') != 'Rejected'"
		)
	]
	listed = set()
	if open_names:
		listed = {
			str(r[0]).upper()
			for r in frappe.db.sql(
				"SELECT bucket_id FROM `tabDiscard Request Bucket` WHERE parenttype = 'Discard Request' AND parent IN %(n)s",
				{"n": tuple(open_names)},
			)
			if r[0]
		}
	allocated = {str(r[0]).upper() for r in frappe.db.sql("SELECT DISTINCT bucket_id FROM `tabBucket Allocation Status`") if r[0]}
	rooms = {_location(c) for c in coldrooms}
	now = now_datetime()
	out, left = [], {"on_a_request": 0, "allocated": 0, "too_young": 0, "other_cold_room": 0}
	for r in rows:
		key = str(r["bucket_id"]).upper()
		if key in listed:
			left["on_a_request"] += 1
			continue
		if key in allocated:
			left["allocated"] += 1
			continue
		if r["harvest_date"]:
			age = (now.date() - getdate(r["harvest_date"])).days + 0.0
		elif r["date_added"]:
			age = (now - get_datetime(r["date_added"])).total_seconds() / 86400.0
		else:
			continue
		if age < threshold:
			left["too_young"] += 1
			continue
		if rooms and _location(r["greenhouse"]) not in rooms and (r["warehouse"] or "") not in coldrooms:
			left["other_cold_room"] += 1
			continue
		r["age_days"] = round(age, 2)
		r["age_hours"] = round(age * 24, 1)
		out.append(r)
	return out, left, len(rows)


@frappe.whitelist()
def preview_buckets(farms=None, varieties=None, lengths=None, item_groups=None, coldrooms=None, age_threshold=None, age_unit="Days"):
	"""What a new request would pick up, grouped by farm / variety / length. Writes nothing."""
	_need()
	age_unit = "Hours" if age_unit == "Hours" else "Days"
	if age_threshold in (None, ""):
		age_threshold = frappe.db.get_single_value("Production Settings", "discard_age") or 5
	rows, left, scanned = _candidates(_list(farms), _list(varieties), _list(lengths), _list(item_groups), _list(coldrooms), age_threshold, age_unit)
	groups = {}
	for r in rows:
		g = groups.setdefault(
			(r["farm"], r["variety"], r["stem_length"] or ""),
			{"farm": r["farm"], "variety": r["variety"], "length": r["stem_length"] or "", "buckets": 0, "stems": 0, "oldest_days": 0},
		)
		g["buckets"] += 1
		g["stems"] += flt(r["stem_qty"])
		g["oldest_days"] = max(g["oldest_days"], r["age_days"])
	return {
		"groups": sorted(groups.values(), key=lambda g: (g["farm"] or "", -g["stems"])),
		"buckets": len(rows),
		"stems": sum(flt(r["stem_qty"]) for r in rows),
		"scanned": scanned,
		"left_out": left,
		"age_threshold": flt(age_threshold),
		"age_unit": age_unit,
	}


def _single(vals):
	return vals[0] if len(vals) == 1 else None


def _describe(label, vals):
	return f"{label}: {', '.join(vals)}" if len(vals) > 1 else ""


@frappe.whitelist()
def create_requests(
	groups=None, farms=None, varieties=None, lengths=None, item_groups=None, coldrooms=None,
	age_threshold=None, age_unit="Days", remarks=None, action="send",
):
	"""One request per farm for the ticked groups ([[farm, variety, length], ...]; every group when omitted).

	action: "draft" (leave as a draft), "send" (send for approval; just submit when there is no workflow) or "approve"."""
	_need("create")
	age_unit = "Hours" if age_unit == "Hours" else "Days"
	if age_threshold in (None, ""):
		age_threshold = frappe.db.get_single_value("Production Settings", "discard_age") or 5
	farms_f, var_f, len_f, grp_f, cold_f = _list(farms), _list(varieties), _list(lengths), _list(item_groups), _list(coldrooms)
	rows, _left, _n = _candidates(farms_f, var_f, len_f, grp_f, cold_f, age_threshold, age_unit)
	chosen = {tuple(g) for g in (frappe.parse_json(groups) if isinstance(groups, str) else groups or [])}
	if groups is not None:
		rows = [r for r in rows if (r["farm"], r["variety"], r["stem_length"] or "") in {(a, b, c or "") for a, b, c in chosen}]
	if not rows:
		frappe.throw(_("There are no buckets to put on a request. Change the filters or tick at least one line."))
	wf = _workflow()
	if action == "approve" and wf and not wf["can_approve"]:
		frappe.throw(_("Your roles cannot approve Discard Requests. Send it for approval instead."))
	by_farm = {}
	for r in rows:
		by_farm.setdefault(r["farm"] or "Unknown", []).append(r)
	note = "; ".join(
		x for x in (_describe("Varieties", var_f), _describe("Lengths", len_f), _describe("Item groups", grp_f), _describe("Cold rooms", cold_f)) if x
	)
	text = "\n".join(x for x in ((remarks or "").strip(), note) if x)
	made, failed = [], []
	for farm, items in by_farm.items():
		try:
			made.append(_make(farm, items, age_threshold, age_unit, var_f, len_f, grp_f, cold_f, text, action, wf))
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			failed.append({"farm": farm, "error": str(e)})
	return {"created": made, "failed": failed}


def _make(farm, items, age_threshold, age_unit, var_f, len_f, grp_f, cold_f, text, action, wf):
	user = frappe.session.user
	doc = frappe.get_doc(
		{
			"doctype": "Discard Request",
			"age_threshold": age_threshold,
			"age_unit": age_unit,
			"requested_by": user,
			"requested_date": today(),
			"farm": farm if farm != "Unknown" else None,
			"company": (frappe.db.get_value("Farm", farm, "company") if farm != "Unknown" else None) or "Karen Roses",
			"variety": _single(var_f),
			"length": _single(len_f),
			"item_group": _single(grp_f),
			"coldroom": _single(cold_f),
			"remarks": text,
			"buckets": [
				{
					"bucket_id": i["bucket_id"],
					"farm": i["farm"],
					"greenhouse": i["greenhouse"],
					"shelf": i["shelf"],
					"is_shelved": 1,
					"harvest_date": i["date_added"],
					"age_days": i["age_days"],
					"age_hours": i["age_hours"],
					"variety": i["variety"],
					"stem_qty": i["stem_qty"],
					"stem_length": i["stem_length"],
				}
				for i in items
			],
		}
	)
	doc.insert()
	if action == "draft":
		return doc.name
	if not wf:
		doc.submit()
		return doc.name
	apply_workflow(doc, "Submit for Approval")
	if action == "approve":
		doc.reload()
		doc.approved_by = user
		doc.approval_date = today()
		apply_workflow(doc, "Approve")
	return doc.name


@frappe.whitelist()
def act_on_requests(names, action):
	"""Move requests along the workflow in one go: "Submit for Approval", "Approve" or "Reject"."""
	_need("write")
	if action not in ("Submit for Approval", "Approve", "Reject"):
		frappe.throw(_("Unknown action."))
	done, failed = [], []
	for n in _list(names):
		try:
			doc = frappe.get_doc("Discard Request", n)
			if action == "Approve":
				doc.approved_by, doc.approval_date = frappe.session.user, today()
			apply_workflow(doc, action)
			done.append(n)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			failed.append({"name": n, "error": str(e)})
	return {"done": done, "failed": failed}
