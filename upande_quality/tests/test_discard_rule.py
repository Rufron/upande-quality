"""The shared discard rule (upande_quality/discard.py) used by the nightly job, Fetch Buckets and the app list."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from upande_quality import discard


def _shelf(bucket, since, variety="Mirabel"):
	return frappe._dict(
		bucket_id=bucket, since=since, variety=variety, shelf="K1", farm="Karen", date_source="harvest"
	)


class TestShelfBuckets(unittest.TestCase):
	def _run(self, shelf_rows, allocations, age=6, processed=()):
		resolver = lambda _item=None: age  # noqa: E731

		def sql(query, *a, **k):
			if "Bucket Allocation Status" in query:
				return [frappe._dict(x) for x in allocations]
			if "Pick List Item" in query:
				return [frappe._dict(bucket=b, sales_order=so) for b, so in processed]
			return shelf_rows

		fake = SimpleNamespace(db=SimpleNamespace(sql=sql))
		with (
			patch.object(discard, "frappe", fake),
			patch.object(discard, "discard_age_resolver", return_value=resolver),
			patch.object(discard, "nowdate", return_value="2026-11-11"),
		):
			return discard.shelf_buckets(["Karen"])

	def test_old_enough_and_free_is_discardable(self):
		out = self._run([_shelf("a1", "2026-11-05")], [])
		self.assertTrue(out["A1"].discardable)
		self.assertEqual(out["A1"].age_days, 6)

	def test_too_young_is_not(self):
		self.assertFalse(self._run([_shelf("a1", "2026-11-06")], [])["A1"].discardable)

	def test_allocation_for_current_harvest_blocks_even_partly(self):
		out = self._run(
			[_shelf("X03UBD", "2026-11-05")], [{"bucket_id": "x03ubd", "harvest_date": "2026-11-05"}]
		)
		self.assertTrue(out["X03UBD"].allocated)
		self.assertFalse(out["X03UBD"].discardable)

	def test_allocation_from_an_earlier_harvest_is_ignored(self):
		out = self._run(
			[_shelf("37X52V", "2026-11-05")], [{"bucket_id": "37X52V", "harvest_date": "2026-10-27"}]
		)
		self.assertFalse(out["37X52V"].allocated)
		self.assertTrue(out["37X52V"].discardable)

	def test_bucket_on_two_shelves_uses_the_latest_line(self):
		out = self._run([_shelf("b2", "2026-11-09"), _shelf("b2", "2026-11-01")], [])
		self.assertEqual(str(out["B2"].since), "2026-11-09")
		self.assertFalse(out["B2"].discardable)

	def test_allocation_to_a_past_due_order_does_not_hold(self):
		out = self._run(
			[_shelf("P1", "2026-11-05")],
			[
				{
					"bucket_id": "p1",
					"harvest_date": "2026-11-05",
					"sales_order": "SO-1",
					"sales_order_item": "soi1",
					"delivery_date": "2026-11-09",
				}
			],
		)
		self.assertFalse(out["P1"].allocated)
		self.assertEqual(len(out["P1"].past_due), 1)
		self.assertTrue(out["P1"].discardable)

	def test_past_due_order_still_holds_once_issued_or_boxed(self):
		out = self._run(
			[_shelf("P2", "2026-11-05")],
			[
				{
					"bucket_id": "p2",
					"harvest_date": "2026-11-05",
					"sales_order": "SO-2",
					"sales_order_item": "soi2",
					"delivery_date": "2026-11-09",
				}
			],
			processed=[("p2", "SO-2")],
		)
		self.assertTrue(out["P2"].allocated)
		self.assertFalse(out["P2"].discardable)

	def test_order_not_yet_due_holds(self):
		out = self._run(
			[_shelf("P3", "2026-11-05")],
			[
				{
					"bucket_id": "p3",
					"harvest_date": "2026-11-05",
					"sales_order": "SO-3",
					"sales_order_item": "soi3",
					"delivery_date": "2026-11-11",
				}
			],
		)
		self.assertTrue(out["P3"].allocated)
