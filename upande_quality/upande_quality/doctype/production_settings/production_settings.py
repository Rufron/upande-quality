# Copyright (c) 2026, Adminstrator and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ProductionSettings(Document):
	def validate(self):
		seen = set()
		for row in self.get("sales_farms") or []:
			if row.farm in seen:
				frappe.throw(
					frappe._("Remote Transfer Sales Farms row {0}: {1} is already listed.").format(
						row.idx, row.farm
					)
				)
			seen.add(row.farm)
		for row in self.get("head_size_mandatory_for") or []:
			if not row.item_group and not row.variety:
				frappe.throw(
					frappe._("Head Size Mandatory For row {0}: pick an item group or a variety.").format(
						row.idx
					)
				)


def discard_age_resolver():
	"""Returns age(item_code) -> discard age in days. A variety's own Discard Age (Item > Roses
	Packhouse) wins; else its item group's row in Discard Age by Item Group, or the nearest
	parent group with a row (Spray Roses covers Spray Roses - Bombastics); else the top-level
	Discard Age (5 when blank). age.fallback(item) skips the variety's own age. Build one per
	request and reuse it in loops; lookups are cached inside."""
	ps = frappe.get_cached_doc("Production Settings")
	default = flt(ps.discard_age) or 5.0
	ages = {
		r.item_group: flt(r.discard_age)
		for r in ps.get("discard_age_by_item_group") or []
		if r.item_group and flt(r.discard_age) > 0
	}
	by_item = {}

	def fallback(item_code=None):
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

	own_ages = {}

	def age(item_code=None):
		if item_code:
			if item_code not in own_ages:
				own_ages[item_code] = flt(frappe.db.get_value("Item", item_code, "custom_discard_age"))
			if own_ages[item_code] > 0:
				return own_ages[item_code]
		return fallback(item_code)

	youngest_own = frappe.db.sql(
		"SELECT MIN(custom_discard_age) FROM `tabItem` WHERE custom_discard_age > 0 AND disabled = 0"
	)[0][0]
	age.fallback = fallback
	# for SQL pre-filters: the youngest age any item can have
	age.youngest = min([default, *ages.values(), *([flt(youngest_own)] if youngest_own else [])])
	return age


def discard_age_for(item_code=None):
	"""Discard age in days for one item; see discard_age_resolver."""
	return discard_age_resolver()(item_code)
