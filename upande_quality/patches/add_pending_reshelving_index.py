"""Index the pending-reshelving flag: Pending Reshelving lists the few gradings with
custom_pending_reshelving = 1, and without an index that scanned every Grading entry
(over a million) — about 24 s per open."""

import frappe


def execute():
	if not frappe.db.has_column("Stock Entry", "custom_pending_reshelving"):
		return  # add_pending_reshelving_fields hasn't created it on this site
	frappe.db.add_index("Stock Entry", ["custom_pending_reshelving", "stock_entry_type"])
