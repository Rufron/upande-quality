"""Discard age per variety: the age a bucket must reach before the nightly job puts it on a Discard Request (tasks.py), and
the figures the Discard ages tab reads and writes (api/discard_requests.py)."""

import unittest

import frappe

from upande_quality import tasks
from upande_quality.api import discard_requests as dr


class TestResolverOrder(unittest.TestCase):
	"""A variety's own age, then its item group's, then the Production Settings default."""

	def _resolver(self, own, group_ages, default=5.0, group="Roses"):
		from types import SimpleNamespace
		from unittest import mock

		ps = SimpleNamespace(
			discard_age=default,
			get=lambda k: [SimpleNamespace(item_group=g, discard_age=v) for g, v in group_ages.items()],
		)
		patches = [
			mock.patch.object(frappe, "get_cached_doc", return_value=ps),
			mock.patch.object(frappe, "get_cached_value", return_value=group),
			mock.patch.object(frappe.db, "get_value", return_value=own),
			mock.patch.object(
				frappe.db,
				"sql",
				side_effect=lambda q, *a, **k: [[group]]
				if group in group_ages
				else [[None]]
				if "MIN(" in q
				else [],
			),
		]
		for pt in patches:
			pt.start()
			self.addCleanup(pt.stop)
		from upande_quality.upande_quality.doctype.production_settings.production_settings import (
			discard_age_resolver,
		)

		return discard_age_resolver()

	def test_variety_beats_group_and_default(self):
		self.assertEqual(self._resolver(3, {"Roses": 7})("V"), 3.0)

	def test_group_beats_default_when_the_variety_has_none(self):
		for empty in (None, 0):
			self.assertEqual(self._resolver(empty, {"Roses": 7})("V"), 7.0)

	def test_default_when_neither_is_set(self):
		self.assertEqual(self._resolver(0, {})("V"), 5.0)

	def test_fallback_skips_the_variety(self):
		self.assertEqual(self._resolver(3, {"Roses": 7}).fallback("V"), 7.0)


class TestAgeValue(unittest.TestCase):
	def test_valid_ages_are_kept_to_one_decimal(self):
		self.assertEqual(dr._age_value(3), 3.0)
		self.assertEqual(dr._age_value("4.26"), 4.3)
		self.assertEqual(dr._age_value(0.5), 0.5)
		self.assertEqual(dr._age_value(365), 365.0)

	def test_empty_or_zero_clears_it(self):
		for empty in (None, "", 0, "0", -2):
			self.assertIsNone(dr._age_value(empty))

	def test_out_of_range_is_refused(self):
		for bad in (0.1, 400, 1000):
			with self.assertRaises(frappe.ValidationError):
				dr._age_value(bad)


class TestSetAndClear(unittest.TestCase):
	"""Against a site that has varieties; nothing persists."""

	def setUp(self):
		frappe.set_user("Administrator")
		rows = dr.get_discard_ages()["rows"]
		if not rows:
			self.skipTest("needs varieties under Cut Flowers")
		self.item = rows[0]["item"]

	def tearDown(self):
		frappe.db.rollback()

	def test_setting_then_clearing_a_varieties_age(self):
		out = dr.set_discard_age(self.item, 4)
		self.assertEqual((out["age"], out["effective"]), (4.0, 4.0))
		row = next(r for r in dr.get_discard_ages()["rows"] if r["item"] == self.item)
		self.assertEqual(row["age"], 4.0)
		cleared = dr.set_discard_age(
			self.item, ""
		)  # the column is NOT NULL: this used to be an integrity error
		self.assertIsNone(cleared["age"])
		self.assertEqual(cleared["effective"], dr._default_discard_age())
		row = next(r for r in dr.get_discard_ages()["rows"] if r["item"] == self.item)
		self.assertIsNone(row["age"])

	def test_an_out_of_range_age_is_refused_and_changes_nothing(self):
		dr.set_discard_age(self.item, 4)
		with self.assertRaises(frappe.ValidationError):
			dr.set_discard_age(self.item, 999)
		self.assertEqual(frappe.db.get_value("Item", self.item, "custom_discard_age"), 4.0)

	def test_an_unknown_variety_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			dr.set_discard_age("No Such Variety", 3)


if __name__ == "__main__":
	unittest.main()
