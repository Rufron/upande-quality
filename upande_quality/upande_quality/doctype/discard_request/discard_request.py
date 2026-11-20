# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from upande_quality.grade_b import grade_b_flags


class DiscardRequest(Document):
	def onload(self):
		# "In Grade B warehouse" / "Ready for discard" on each bucket row depend on today's date
		# (Grade B Discard Age), so they are worked out when the request is opened.
		for row in self.get("buckets") or []:
			row.in_grade_b, row.ready_for_discard = grade_b_flags(row)

	def on_submit(self):
		# buckets held only by orders whose delivery date has passed come off those orders
		frappe.enqueue(
			"upande_quality.discard.release_past_due",
			request_name=self.name,
			enqueue_after_commit=True,
		)
