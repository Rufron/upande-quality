"""Whitelisted endpoints for the Bila Shaka quality mobile app."""

import re

import frappe

# Item names embed the grade length, e.g. "Pink Athena 40cm" / "Athena-50cm".
_LENGTH_RE = re.compile(r"[-\s]*(\d+)\s*cm", re.IGNORECASE)


@frappe.whitelist()
def getCurrentUserRoles():
	try:
		user = frappe.session.user
		rows = frappe.get_all(
			"Has Role",
			filters={"parent": user, "parenttype": "User"},
			fields=["role"],
		)
		roles = [r["role"] for r in rows if r.get("role")]
		frappe.response["data"] = {
			"user": user,
			# The app shows people by name, and most users cannot read their own
			# User record (only System Managers can), so the name comes from here.
			"full_name": frappe.utils.get_fullname(user),
			"roles": roles,
		}
	except Exception as e:
		frappe.log_error("getCurrentUserRoles error: " + str(e))
		frappe.response["http_status_code"] = 500
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def fetchQcParameters():
	try:
		data = frappe.get_all("QC Parameters", fields=["name", "parameter", "tolerance_thresholds"])

		frappe.response["message"] = data

	except Exception as e:
		frappe.throw(str(e))


@frappe.whitelist()
def getBucketStatus(bucket_id=None):
	"""Harvest history for a physical bucket, oldest first; the app shows the
	most recent. A bucket is reusable, so one id spans several harvests. Shape
	matches the mobile app's RawTraceabilityRow."""
	bucket_id = (bucket_id or "").strip()
	rows = []
	if bucket_id:
		entries = frappe.get_all(
			"Stock Entry",
			filters={
				"custom_bucket_id": bucket_id,
				"stock_entry_type": "Harvesting",
				"docstatus": 1,
			},
			fields=[
				"name", "custom_farm", "custom_greenhouse", "custom_section",
				"custom_harvester_name", "posting_date", "posting_time",
			],
			order_by="posting_date asc, posting_time asc",
		)
		for se in entries:
			items = frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": se.name},
				fields=["item_code", "item_name", "qty"],
			)
			stems = sum((i.qty or 0) for i in items)
			label = (items[0].item_name or items[0].item_code or "") if items else ""
			m = _LENGTH_RE.search(label)
			length = (m.group(1) + "cm") if m else ""
			variety = _LENGTH_RE.sub("", label).strip()
			rows.append({
				"stock_entry": se.name,
				"custom_farm": se.custom_farm,
				"custom_greenhouse": se.custom_greenhouse,
				"custom_harvester_name": se.custom_harvester_name,
				"custom_stem_length": length,
				"number_of_stems": stems,
				"variety": variety,
				"posting_date": str(se.posting_date or ""),
			})
	frappe.response["message"] = rows


@frappe.whitelist()
def getBatchByBucket(bucket_id=None):
	"""Intake QC: a bucket is the unit (no batch grouping), so return the scanned
	bucket as a single-bucket batch from its most recent Harvesting/Receiving
	Stock Entry. Shape matches the app's RawBatchByBucketResponse."""
	bucket_id = (bucket_id or "").strip()
	if not bucket_id:
		frappe.response["status"] = "error"
		frappe.response["message"] = "No bucket id supplied."
		return

	se = frappe.get_all(
		"Stock Entry",
		filters={
			"custom_bucket_id": bucket_id,
			"docstatus": 1,
			"stock_entry_type": ["in", ["Harvesting", "Receiving"]],
		},
		fields=["name", "company", "custom_farm", "custom_greenhouse",
			"posting_date", "posting_time"],
		order_by="posting_date desc, posting_time desc",
		limit=1,
	)
	if not se:
		frappe.response["status"] = "error"
		frappe.response["message"] = "No batch found for bucket " + bucket_id + "."
		return

	se = se[0]
	items = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": se.name},
		fields=["item_code", "item_name", "qty", "t_warehouse", "s_warehouse",
			"cost_center", "basic_rate"],
	)
	stems = sum((i.qty or 0) for i in items)
	it = items[0] if items else frappe._dict()
	bucket = {
		"bucket_id": bucket_id,
		"stems": stems,
		"item_code": it.get("item_code") or "",
		"item_name": it.get("item_name") or "",
		"warehouse": it.get("t_warehouse") or it.get("s_warehouse") or se.custom_greenhouse or "",
		"quarantine_warehouse": None,
		"basic_rate": it.get("basic_rate") or 0,
		"cost_center": it.get("cost_center") or "",
		"farm": se.custom_farm or "",
		"greenhouse": se.custom_greenhouse or "",
		"stock_entry": se.name,
		"status": "",
		"selected": True,
		"quarantine_stems": 0,
		"is_in_quarantine": False,
	}
	frappe.response["status"] = "success"
	frappe.response["scanned_bucket"] = bucket_id
	frappe.response["batch_no"] = se.name
	frappe.response["farm"] = se.custom_farm or ""
	frappe.response["company"] = se.company or ""
	frappe.response["total_buckets"] = 1
	frappe.response["total_stems"] = stems
	frappe.response["has_active_quarantine"] = False
	frappe.response["buckets"] = [bucket]


def _snake_param(s):
	"""Mirror the app's snakeCase so concern keys map back to QC Parameters."""
	s = re.sub(r"[^\w\s]", "", (s or "").strip())
	return re.sub(r"\s+", "_", s).lower()


def _sync_qc_parameters():
	"""Ensure every QC Parameter has a matching Quality Inspection Parameter (the
	readings child's required link). Returns {snake_key: parameter_name}."""
	key_to_name = {}
	for p in frappe.get_all("QC Parameters", fields=["parameter"]):
		name = p.parameter
		if not name:
			continue
		key_to_name[_snake_param(name)] = name
		if not frappe.db.exists("Quality Inspection Parameter", name):
			frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": name}).insert()
	return key_to_name


def _bucket_entry(bucket_id, types):
	"""Most recent submitted Stock Entry of the given type(s) for a bucket."""
	rows = frappe.get_all(
		"Stock Entry",
		filters={"custom_bucket_id": bucket_id, "stock_entry_type": ["in", types], "docstatus": 1},
		fields=["name", "custom_greenhouse", "custom_section", "custom_harvester_name"],
		order_by="posting_date desc, posting_time desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def submitBatchQuality(data=None):
	"""Intake QC submit: create and submit one standard Quality Inspection per
	scanned bucket, with a reading per QC Parameter (synced into Quality
	Inspection Parameter). ERPNext sets overall status from the readings."""
	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}

	decision = (data.get("batch_decision") or "").upper()
	status = "Accepted" if decision == "ACCEPT" else "Rejected"
	control_point = (data.get("control_point") or "Intake").strip()
	is_grading = control_point == "Grading"
	sampled = data.get("checked_stems_sampled") or 0
	hygiene = data.get("solution_hygiene") or ""
	by_variety = data.get("stems_checked_by_variety") or {}
	concerns = data.get("quality_concerns") or {}
	buckets = data.get("buckets") or []

	# Concern counts summed per parameter key (snake_case), across varieties.
	totals = {}
	for _variety, params in concerns.items():
		for pkey, info in (params or {}).items():
			totals[pkey] = totals.get(pkey, 0) + ((info or {}).get("count") or 0)

	key_to_name = _sync_qc_parameters()
	readings = [
		{
			"specification": name,
			"numeric": 0,
			"manual_inspection": 1,
			"status": "Rejected" if totals.get(key) else "Accepted",
			"reading_value": str(totals.get(key, 0)),
		}
		for key, name in key_to_name.items()
	]

	base_lines = ["{0} QC (mobile)".format(control_point), "Decision: " + decision]
	if not is_grading:
		base_lines.append("Solution hygiene: " + str(hygiene))
	base_lines.append("Stems sampled: {0} (total {1})".format(by_variety, sampled))

	created = []
	for b in buckets:
		item_code = b.get("item_code")
		if not item_code:
			continue
		# Intake QC happens at receiving; Grading QC later in the packhouse. Point
		# each QI at a different Stock Entry so ERPNext's one-QI-per-row rule
		# allows both: Intake -> the Receiving entry, Grading -> the Harvesting
		# entry (which carries greenhouse/bed/harvester, so grading rejects stay
		# attributable to where and who harvested).
		if is_grading:
			se = _bucket_entry(b.get("bucket_id"), ["Harvesting"])
		else:
			se = _bucket_entry(b.get("bucket_id"), ["Receiving"]) or _bucket_entry(b.get("bucket_id"), ["Harvesting"])
		reference_name = se.name if se else b.get("stock_entry")
		lines = list(base_lines)
		if is_grading and se:
			lines.append("Greenhouse: " + (se.custom_greenhouse or "—"))
			lines.append("Bed: " + (se.custom_section or "—"))
			lines.append("Harvester: " + (se.custom_harvester_name or "—"))
		doc = frappe.get_doc({
			"doctype": "Quality Inspection",
			"inspection_type": "In Process" if is_grading else "Incoming",
			"reference_type": "Stock Entry",
			"reference_name": reference_name,
			"item_code": item_code,
			"sample_size": sampled or b.get("stems") or 1,
			"report_date": frappe.utils.nowdate(),
			"inspected_by": frappe.session.user,
			"status": status,
			"remarks": "\n".join(lines),
			"readings": readings,
		})
		doc.insert()
		doc.submit()
		created.append(doc.name)

	if created:
		frappe.response["status"] = "success"
		frappe.response["message"] = "Quality Inspection submitted: " + ", ".join(created)
	else:
		frappe.response["status"] = "error"
		frappe.response["message"] = "No bucket supplied to inspect."
