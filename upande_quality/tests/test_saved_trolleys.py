# Copyright (c) 2026, Upande and contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from upande_quality.mobile import api

# Deleting a saved trolley in the farm app puts its buckets back exactly as before the
# scan: off the trolley, on their farm shelf, waiting for transfer unless the order
# delivers today or earlier. The database is mocked: these run on a bare site as well.

FARM = "Torongo"


class UnitTestDeleteSavedTrolley(UnitTestCase):
	def setUp(self):
		self.today = str(frappe.utils.today())
		self.tomorrow = str(frappe.utils.add_days(self.today, 1))

	def _delete(self, due):
		from upande_packhouse.api.remote_transfer import transfer_scheduling as tc

		row = frappe._dict(
			name="pli-1",
			parent="OPL-G",
			bucket="ZL0141",
			farm=FARM,
			source_warehouse="Torongo Receiving Cold Store - K",
			trolley_id="TRL-1",
			in_transit=0,
			transit_truck="SIM Truck",
		)

		def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
			if doctype == "Pick List Item":
				return [row]
			if doctype == "Vehicle":
				return ["KDA 001A"]
			return [frappe._dict(name="OPL-G", farm="Kapkolia")]

		pick = frappe._dict(name="pli-1", parent="OPL-G", shelf="TRG-B7B", transit_truck="SIM Truck")
		frappe.local.form_dict = frappe._dict(trolley_ids="TRL-1", farm="")
		frappe.local.response = frappe._dict()
		with (
			patch.object(api.frappe, "get_all", side_effect=get_all),
			patch.object(tc.frappe.db, "sql", return_value=[pick]),
			patch.object(tc.frappe.db, "has_column", return_value=True),
			patch.object(tc, "_opl_delivery_dates", return_value={"OPL-G": due}),
			patch.object(tc, "_restore_farm_shelf", return_value="TRG-B7B") as restore,
			patch.object(tc, "log_transfer_event") as event,
			patch.object(frappe.db, "set_value") as set_value,
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe.db, "savepoint"),
		):
			api.deleteSavedTrolleys()
		self.restore, self.event = restore, event
		return frappe.local.response["message"], set_value

	def test_c_deleted_trolley_bucket_is_back_on_its_shelf_waiting(self):
		msg, set_value = self._delete(due=self.tomorrow)
		self.assertEqual(msg["status"], "success")
		self.assertEqual(msg["cleared_count"], 1)
		self.assertEqual(
			msg["reshelved"], [{"bucket": "ZL0141", "opl": "OPL-G", "shelf": "TRG-B7B", "awaiting": 1}]
		)
		self.restore.assert_called_once()
		set_value.assert_called_once_with(
			"Pick List Item",
			"pli-1",
			{
				"loaded_in_trolley": 0,
				"awaiting_transfer": 1,
				"loaded_on_truck": 0,
				"trolley_id": None,
				"shelf": "TRG-B7B",
			},
		)
		self.assertEqual(self.event.call_args[0][1], "Back to awaiting transfer")

	def test_c_order_due_today_goes_back_on_the_shelf_only(self):
		msg, set_value = self._delete(due=self.today)
		self.assertEqual(msg["reshelved"][0]["awaiting"], 0)
		values = set_value.call_args[0][2]
		self.assertNotIn("awaiting_transfer", values)
		# The order's delivery label is no transfer truck: kept.
		self.assertNotIn("transit_truck", values)
		self.assertEqual((values["loaded_in_trolley"], values["trolley_id"]), (0, None))
		self.assertEqual(self.event.call_args[0][1], "Back on farm shelf")
		self.assertIn("not carried over: order due " + self.today, self.event.call_args[1]["details"])
