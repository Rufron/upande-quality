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
from frappe.utils import cint, flt, getdate, today

from upande_quality.discard import listed_today, shelf_buckets
from upande_quality.upande_quality.doctype.production_settings.production_settings import (
	discard_age_resolver,
)

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
		frappe.throw(
			_("You do not have permission to {0} Discard Requests.").format(_(ptype)), frappe.PermissionError
		)


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

	def can(a):
		return any(m["action"] == a and (m["allowed"] == "All" or m["allowed"] in roles) for m in moves)

	return {
		"name": name,
		"can_approve": can("Approve"),
		"can_reject": can("Reject"),
		"can_send": can("Submit for Approval"),
	}


@frappe.whitelist()
def get_options():
	"""Everything the filters and the new-request form offer."""
	_need()

	def shelf(col):
		return [
			r[0]
			for r in frappe.db.sql(
				f"SELECT DISTINCT {col} FROM `tabShelf Item` WHERE IFNULL({col}, '') != '' ORDER BY {col}"
			)
		]

	farms = [
		r[0]
		for r in frappe.db.sql(
			"SELECT DISTINCT farm FROM `tabShelf` WHERE IFNULL(farm, '') != '' ORDER BY farm"
		)
	]
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
		"coldrooms": [
			r[0]
			for r in frappe.db.sql("SELECT name FROM `tabWarehouse` WHERE name LIKE '%%Cold%%' ORDER BY name")
		],
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
		WHERE {" AND ".join(cond)}
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
	k = {
		"requests": len(reqs),
		"system": 0,
		"user": 0,
		"buckets": 0,
		"stems": 0,
		"done_stems": 0,
		"waiting_stems": 0,
		"pending": 0,
	}
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
			w = by_week.setdefault(
				f"{d[0]}-W{d[1]:02d}", {"week": f"{d[0]}-W{d[1]:02d}", "system": 0, "user": 0}
			)
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
		{
			k: (str(b.get(k)) if k == "harvest_date" and b.get(k) else b.get(k))
			for k in (
				"bucket_id",
				"farm",
				"greenhouse",
				"shelf",
				"variety",
				"stem_length",
				"stem_qty",
				"age_days",
				"harvest_date",
				"discarded",
			)
		}
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
	"""Discardable shelved buckets matching the filters, plus what was left out and why. The rule is shared with
	the nightly job and the app list (upande_quality/discard.py); a typed age threshold replaces each variety's
	discard age. Buckets already on a request made today are left out so they are not listed twice."""
	if age_threshold not in (None, ""):
		t = flt(age_threshold) / 24.0 if age_unit == "Hours" else flt(age_threshold)
	listed = listed_today()
	rooms = {_location(c) for c in coldrooms}
	buckets = shelf_buckets(farms)
	out, left = [], {"on_a_request": 0, "allocated": 0, "too_young": 0, "other_cold_room": 0}
	for key, b in buckets.items():
		if (
			(varieties and b.variety not in varieties)
			or (lengths and b.stem_length not in lengths)
			or (item_groups and b.item_group not in item_groups)
		):
			continue
		if key in listed:
			left["on_a_request"] += 1
		elif b.allocated:
			left["allocated"] += 1
		# shortcut: age is whole days (date of harvest / shelving), so an "Hours" threshold rounds to days.
		elif b.age_days < (t if age_threshold not in (None, "") else b.discard_age):
			left["too_young"] += 1
		elif rooms and _location(b.greenhouse) not in rooms and (b.warehouse or "") not in coldrooms:
			left["other_cold_room"] += 1
		else:
			b.harvest_date = b.since
			b.age_hours = b.age_days * 24
			out.append(b)
	return out[:MAX_BUCKETS], left, len(buckets)


@frappe.whitelist()
def get_shelf_items_for_discard(
	age_threshold=None,
	age_unit="Days",
	variety=None,
	item_group=None,
	farm=None,
	company=None,
	length=None,
	coldroom=None,
):
	"""The Discard Request form's "Fetch Buckets", replacing the live Server Script of the same name with the
	same filters and response. A blank age threshold uses each item group's discard age (Production Settings),
	and buckets already on an open request are left out, as on the dashboard and in the nightly job."""
	_need()
	age_unit = "Hours" if age_unit == "Hours" else "Days"
	one = lambda v: [v.strip()] if v and v.strip() else []  # noqa: E731
	farms = one(farm)
	if company:
		of_company = frappe.get_all("Farm", filters={"company": company}, pluck="name")
		farms = [f for f in farms if f in of_company] if farms else of_company
	if company and not farms:
		rows, left, scanned = [], {"allocated": 0, "on_a_request": 0}, 0
	else:
		rows, left, scanned = _candidates(
			farms, one(variety), one(length), one(item_group), one(coldroom), age_threshold, age_unit
		)
	for r in rows:
		r["company"] = company or None
	rows.sort(key=lambda r: -r["age_days"])
	return {
		"success": True,
		"shelf_items": rows,
		"total_scanned": scanned,
		"total_qualifying": len(rows),
		"buckets_without_harvest_entry": sum(1 for r in rows if r["date_source"] == "shelved"),
		"allocated_skipped": left["allocated"],
		"already_on_request": left["on_a_request"],
		"applied_filters": {
			"age_threshold": age_threshold if age_threshold not in (None, "") else "item group",
			"age_unit": age_unit,
			"variety": variety,
			"item_group": item_group,
			"farm": farm,
			"company": company,
			"length": length,
			"coldroom": coldroom,
		},
	}


@frappe.whitelist()
def preview_buckets(
	farms=None,
	varieties=None,
	lengths=None,
	item_groups=None,
	coldrooms=None,
	age_threshold=None,
	age_unit="Days",
):
	"""What a new request would pick up, grouped by farm / variety / length. Writes nothing."""
	_need()
	age_unit = "Hours" if age_unit == "Hours" else "Days"
	rows, left, scanned = _candidates(
		_list(farms),
		_list(varieties),
		_list(lengths),
		_list(item_groups),
		_list(coldrooms),
		age_threshold,
		age_unit,
	)
	groups = {}
	for r in rows:
		g = groups.setdefault(
			(r["farm"], r["variety"], r["stem_length"] or ""),
			{
				"farm": r["farm"],
				"variety": r["variety"],
				"length": r["stem_length"] or "",
				"buckets": 0,
				"stems": 0,
				"oldest_days": 0,
			},
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
	groups=None,
	farms=None,
	varieties=None,
	lengths=None,
	item_groups=None,
	coldrooms=None,
	age_threshold=None,
	age_unit="Days",
	remarks=None,
	action="send",
):
	"""One request per farm for the ticked groups ([[farm, variety, length], ...]; every group when omitted).

	action: "draft" (leave as a draft), "send" (send for approval; just submit when there is no workflow) or "approve"."""
	_need("create")
	age_unit = "Hours" if age_unit == "Hours" else "Days"
	farms_f, var_f, len_f, grp_f, cold_f = (
		_list(farms),
		_list(varieties),
		_list(lengths),
		_list(item_groups),
		_list(coldrooms),
	)
	rows, _left, _n = _candidates(farms_f, var_f, len_f, grp_f, cold_f, age_threshold, age_unit)
	chosen = {tuple(g) for g in (frappe.parse_json(groups) if isinstance(groups, str) else groups or [])}
	if groups is not None:
		rows = [
			r
			for r in rows
			if (r["farm"], r["variety"], r["stem_length"] or "") in {(a, b, c or "") for a, b, c in chosen}
		]
	if not rows:
		frappe.throw(
			_("There are no buckets to put on a request. Change the filters or tick at least one line.")
		)
	wf = _workflow()
	if action == "approve" and wf and not wf["can_approve"]:
		frappe.throw(_("Your roles cannot approve Discard Requests. Send it for approval instead."))
	by_farm = {}
	for r in rows:
		by_farm.setdefault(r["farm"] or "Unknown", []).append(r)
	note = "; ".join(
		x
		for x in (
			_describe("Varieties", var_f),
			_describe("Lengths", len_f),
			_describe("Item groups", grp_f),
			_describe("Cold rooms", cold_f),
		)
		if x
	)
	text = "\n".join(x for x in ((remarks or "").strip(), note) if x)
	made, failed = [], []
	for farm, items in by_farm.items():
		try:
			made.append(
				_make(farm, items, age_threshold, age_unit, var_f, len_f, grp_f, cold_f, text, action, wf)
			)
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
			"company": (frappe.db.get_value("Farm", farm, "company") if farm != "Unknown" else None)
			or "Karen Roses",
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


# --------------------------------------------------------------------------- #
# Discard age per variety (Item > Roses Packhouse > Discard Age). The nightly job and the Discard Request rows read it.
# --------------------------------------------------------------------------- #
VARIETY_ROOT = "Cut Flowers"


def _default_discard_age():
	try:
		return flt(frappe.db.get_single_value("Production Settings", "discard_age")) or 5.0
	except Exception:
		return 5.0


def _age_value(days):
	"""A valid discard age in days (0.5 to 365, one decimal), or None to clear it."""
	if days in (None, ""):
		return None
	value = flt(days)
	if value <= 0:
		return None
	if value < 0.5 or value > 365:
		frappe.throw(_("A discard age is between 0.5 and 365 days."))
	return round(value, 1)


@frappe.whitelist()
def get_discard_ages(search=None, group=None, set_only=0):
	"""Every variety with its discard age (empty = the default in Production Settings), and the default."""
	_need("read")
	default = _default_discard_age()
	fallback = discard_age_resolver().fallback  # the item group's age, else the default
	cond, p = ["it.disabled = 0"], {"root": VARIETY_ROOT}
	if search:
		cond.append("(it.name LIKE %(q)s OR it.item_name LIKE %(q)s)")
		p["q"] = "%" + str(search).strip().replace("%", "")[:60] + "%"
	if group:
		cond.append("it.item_group = %(g)s")
		p["g"] = group
	if cint(set_only):
		cond.append("IFNULL(it.custom_discard_age, 0) > 0")
	rows = frappe.db.sql(
		f"""SELECT it.name, it.item_name, it.item_group, it.custom_discard_age AS age
		FROM `tabItem` it
		JOIN `tabItem Group` g ON g.name = it.item_group
		JOIN `tabItem Group` r ON r.name = %(root)s AND g.lft >= r.lft AND g.rgt <= r.rgt
		WHERE {" AND ".join(cond)} ORDER BY g.name, it.item_name LIMIT 2000""",
		p,
		as_dict=True,
	)
	return {
		"default": default,
		"rows": [
			{
				"item": r.name,
				"name": r.item_name or r.name,
				"group": r.item_group,
				"age": flt(r.age) or None,
				"effective": flt(r.age) if flt(r.age) > 0 else fallback(r.name),
			}
			for r in rows
		],
	}


@frappe.whitelist(methods=["POST"])
def set_discard_age(item=None, days=None):
	"""Set (or clear, with an empty value) a variety's discard age. Needs write access to Discard Requests."""
	_need("write")
	if not item or not frappe.db.exists("Item", item):
		frappe.throw(_("Unknown variety."), frappe.DoesNotExistError)
	value = _age_value(days)
	# The column cannot be NULL: 0 means "none of its own, use the group's or the default".
	frappe.db.set_value("Item", item, "custom_discard_age", value or 0)
	return {"item": item, "age": value, "effective": value or discard_age_resolver().fallback(item)}
