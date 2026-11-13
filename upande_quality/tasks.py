import frappe
from frappe.utils import today

from upande_quality.discard import listed_today, shelf_buckets
from upande_quality.upande_quality.doctype.production_settings.production_settings import (
	discard_age_resolver,
)


def auto_discard_request():
	"""Nightly (cron 0 0 * * *) equivalent of the old "Auto Discard Request"
	v15 Server Script (Scheduler Event). For every shelved bucket whose age
	(harvest -> now) >= Production Settings `discard_age` days, create a
	Discard Request PER FARM and auto-approve it by walking the workflow one
	edge at a time -- Draft -> Pending Approval -> Approved -- via plain
	save()/submit(). Jumping straight from Draft to Approved fails
	workflow-graph validation ("Workflow State transition not allowed from
	Draft to Approved"), since the workflow only defines Draft->Pending
	Approval and Pending Approval->Approved as valid edges; that check runs
	on every save/submit regardless of how the field is set, so each edge
	must be applied as its own save.

	Which buckets: see upande_quality/discard.py (the same rule as Fetch Buckets and the app's discard
	list). Every discardable bucket is listed each night, including ones left from an earlier request;
	a bucket already on a request made today is skipped.

	Unlike the v15 script, `company` is resolved per-farm from Farm.company
	rather than hardcoded to "Karen Roses" -- farms belonging to other
	companies (Westwood Dairies Limited, Kaitet Ltd., Greenville Plantations)
	exist in this dataset and would otherwise get a Discard Request filed
	under the wrong company.
	"""
	try:
		# One rule with Fetch Buckets and the app list (upande_quality/discard.py): on a shelf and old enough
		# for its variety, allocated or not. Every such bucket is listed each night,
		# even when an earlier request listed it, so each day's request is complete on its own; only a bucket
		# already on a request made today is skipped, so a second run the same day adds nothing twice.
		discard_age = discard_age_resolver()()  # the default, recorded on the request
		today_date = today()
		run_user = frappe.session.user
		listed = listed_today()

		by_farm = {}
		for key, b in shelf_buckets().items():
			if not b.discardable or key in listed:
				continue
			farm = b.farm or "Unknown"
			by_farm.setdefault(farm, []).append(
				{
					"bucket_id": b.bucket_id,
					"farm": farm,
					"greenhouse": b.greenhouse,
					"shelf": b.shelf,
					"is_shelved": 1,
					"harvest_date": b.date_added,
					"age_days": b.age_days,
					"age_hours": b.age_days * 24,
					"discard_age": b.discard_age,
					"variety": b.variety,
					"stem_qty": b.stem_qty,
					"stem_length": b.stem_length,
				}
			)

		created = []
		failed = []
		for farm in by_farm:
			buckets = by_farm[farm]
			if not buckets:
				continue
			try:
				company = frappe.db.get_value("Farm", farm, "company") or "Karen Roses"
				doc = frappe.get_doc(
					{
						"doctype": "Discard Request",
						"age_threshold": discard_age,
						"age_unit": "Days",
						"requested_by": run_user,
						"requested_date": today_date,
						"farm": farm if farm != "Unknown" else None,
						"company": company,
						"workflow_state": "Draft",
						"buckets": buckets,
					}
				)
				doc.insert(ignore_permissions=True)

				# frappe.model.workflow.apply_workflow can behave unreliably
				# outside a request context. Walk the same two edges the
				# workflow defines directly via save()/submit() instead -- each
				# individual state change is still validated against the
				# workflow's transition graph, so jumping straight
				# Draft -> Approved would still fail; one edge at a time
				# doesn't.
				doc.workflow_state = "Pending Approval"
				doc.save(ignore_permissions=True)

				doc.workflow_state = "Approved"
				doc.approved_by = run_user
				doc.approval_date = today_date
				doc.submit()

				created.append(doc.name + " (" + farm + ": " + str(len(buckets)) + ")")
				# Commit per farm -- a later farm's failure must only roll back
				# that farm's own partial writes, not every farm already done.
				frappe.db.commit()
			except Exception as fe:
				frappe.db.rollback()
				failed.append(farm + ": " + str(fe))

		if created:
			frappe.log_error(title="Auto Discard Request", message="Auto-approved: " + ", ".join(created))
		if failed:
			frappe.log_error(title="Auto Discard Request FAILED (per-farm)", message="; ".join(failed))
	except Exception as e:
		frappe.log_error(title="Auto Discard Request FAILED", message=str(e))
