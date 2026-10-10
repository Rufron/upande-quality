# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class BucketCount(Document):
	def validate(self):
		self.total_buckets = len(self.buckets or [])
