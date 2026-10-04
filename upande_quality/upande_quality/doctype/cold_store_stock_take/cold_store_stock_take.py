# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import flt


class ColdStoreStockTake(Document):
	def validate(self):
		# Totals of the scanned buckets: how many, and how many stems in them.
		self.total_buckets = len(self.buckets or [])
		self.total_stems = sum(flt(r.qty) for r in self.buckets or [])
