# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DiscardRequest(Document):
	def on_submit(self):
		# buckets held only by orders whose delivery date has passed come off those orders
		frappe.enqueue(
			"upande_quality.discard.release_past_due",
			request_name=self.name,
			enqueue_after_commit=True,
		)
