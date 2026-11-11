# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ProductionSettings(Document):
	pass


def discard_age_resolver():
	"""Returns age(item_code) -> discard age in days. The item's group uses its own row in
	Discard Age by Item Group, else the nearest parent group with a row (Spray Roses covers
	Spray Roses - Bombastics), else the top-level Discard Age (5 when blank). Build one per
	request and reuse it in loops; lookups are cached inside."""
	ps = frappe.get_cached_doc("Production Settings")
	default = flt(ps.discard_age) or 5.0
	ages = {
		r.item_group: flt(r.discard_age)
		for r in ps.get("discard_age_by_item_group") or []
		if r.item_group and flt(r.discard_age) > 0
	}
	by_item = {}

	def age(item_code=None):
		if not ages or not item_code:
			return default
		if item_code not in by_item:
			group = frappe.get_cached_value("Item", item_code, "item_group")
			hit = None
			if group:
				hit = frappe.db.sql(
					"""SELECT g.name FROM `tabItem Group` g
					JOIN `tabItem Group` me ON me.name = %s
					WHERE g.lft <= me.lft AND g.rgt >= me.rgt AND g.name IN %s
					ORDER BY g.lft DESC LIMIT 1""",
					(group, tuple(ages)),
				)
			by_item[item_code] = ages[hit[0][0]] if hit else default
		return by_item[item_code]

	age.youngest = min([default, *ages.values()])  # for SQL pre-filters
	return age


def discard_age_for(item_code=None):
	"""Discard age in days for one item; see discard_age_resolver."""
	return discard_age_resolver()(item_code)
