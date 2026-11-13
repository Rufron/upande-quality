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
	def _run(self, shelf_rows, allocations, age=6):
		resolver = lambda _item=None: age  # noqa: E731
		fake = SimpleNamespace(
			db=SimpleNamespace(sql=lambda *a, **k: shelf_rows),
			get_all=lambda *a, **k: [frappe._dict(a) for a in allocations],
		)
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

	def test_past_age_is_discardable_even_when_allocated(self):
		out = self._run(
			[_shelf("X03UBD", "2026-11-05")], [{"bucket_id": "x03ubd", "harvest_date": "2026-11-05"}]
		)
		self.assertTrue(out["X03UBD"].allocated)
		self.assertTrue(out["X03UBD"].discardable)

	def test_allocated_and_too_young_is_not(self):
		out = self._run(
			[_shelf("X03UBD", "2026-11-08")], [{"bucket_id": "x03ubd", "harvest_date": "2026-11-08"}]
		)
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


class TestBucketStock(unittest.TestCase):
	def test_moved_bucket_is_found_where_it_is_now(self):
		# YWAHU5: received at Simotwo, remote-transferred to Kapkolia; part allocated to Sold.
		moves = [
			frappe._dict(
				s_warehouse="Simotwo GH 20 - KR", t_warehouse="Simotwo Receiving Cold Store - KR", qty=200
			),
			frappe._dict(
				s_warehouse="Simotwo Receiving Cold Store - KR",
				t_warehouse="Kapkolia Receiving Cold Store - KR",
				qty=200,
			),
			frappe._dict(
				s_warehouse="Kapkolia Receiving Cold Store - KR",
				t_warehouse="Kapkolia Graded Sold - KR",
				qty=50,
			),
		]
		fake = SimpleNamespace(db=SimpleNamespace(sql=lambda *a, **k: moves))
		with patch.object(discard, "frappe", fake):
			out = discard.bucket_stock("YWAHU5", "2026-11-06", "Madam Cerise")
		self.assertEqual(out, {"Kapkolia Receiving Cold Store - KR": 150, "Kapkolia Graded Sold - KR": 50})
