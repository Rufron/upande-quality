"""Shelving a bucket an order still waits to transfer: the transfer only counts while the label has not
outlived it -- not harvested again since the order took the bucket, and the order not already gone out.
Otherwise the scan is the label's new fill and its own (latest) receiving is used. The database is
mocked."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from upande_quality.mobile import api


def _row(creation="2026-11-22 12:15:59"):
	return frappe._dict(parent="OPL-1", creation=creation)


class UnitTestTransferOutlived(UnitTestCase):
	def _check(self, harvested_since, opl, row=None):
		with (
			patch.object(api.frappe.db, "exists", return_value=harvested_since) as exists,
			patch.object(api.frappe.db, "get_value", return_value=opl),
			patch.object(api.frappe.utils, "today", return_value="2026-11-23"),
		):
			out = api._transfer_outlived("LL2ZG7", row or _row())
		return out, exists

	def test_a_label_harvested_again_outlived_the_transfer(self):
		out, exists = self._check(True, frappe._dict(docstatus=0, delivery_date="2026-11-24"))
		self.assertTrue(out)
		filters = exists.call_args.args[1]
		self.assertEqual(filters["posting_date"], [">", frappe.utils.getdate("2026-11-22")])
		self.assertEqual(filters["stock_entry_type"], "Harvesting")

	def test_a_waiting_transfer_for_a_coming_order_still_counts(self):
		out, _ = self._check(False, frappe._dict(docstatus=0, delivery_date="2026-11-24"))
		self.assertFalse(out)

	def test_a_submitted_order_still_due_still_counts(self):
		out, _ = self._check(False, frappe._dict(docstatus=1, delivery_date="2026-11-23"))
		self.assertFalse(out)

	def test_a_submitted_order_whose_day_has_passed_outlived_it(self):
		out, _ = self._check(False, frappe._dict(docstatus=1, delivery_date="2026-11-11"))
		self.assertTrue(out)

	def test_a_row_with_no_creation_falls_back_to_the_order(self):
		out, exists = self._check(True, frappe._dict(docstatus=0, delivery_date="2026-11-24"), _row(None))
		exists.assert_not_called()
		self.assertFalse(out)
