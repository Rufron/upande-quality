# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ProductionSettings(Document):
	def validate(self):
		# Enforce at: sales locations only (a Farm's Farm Location), never a farm.
		if not self.get("enforce_plan_locations"):
			return
		locations = _sales_locations()
		for row in self.get("enforce_plan_locations") or []:
			if row.location not in locations:
				frappe.throw(_("{0} is not a sales location.").format(row.location))


def _sales_locations():
	return set(frappe.get_all("Farm", filters={"farm_location": ("is", "set")}, pluck="farm_location"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def sales_locations(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | None = None
):
	"""Link search for Enforce at: Locations some Farm belongs to."""
	txt = (txt or "").lower()
	names = sorted(n for n in _sales_locations() if txt in n.lower())
	return [[n] for n in names[cint(start) : cint(start) + cint(page_len)]]
