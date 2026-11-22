"""Packhouse Inspection Log reads its items and their OK conditions from the Packhouse Inspection Item master."""

import json
import unittest
import unittest.mock

import frappe

from upande_quality.mobile import api


def _item(name, conditions, ok=(), active=1, order=99):
	frappe.get_doc(
		{
			"doctype": "Packhouse Inspection Item",
			"item_name": name,
			"sort_order": order,
			"active": active,
			"conditions": [{"condition": c, "counts_as_ok": int(c in ok)} for c in conditions],
		}
	).insert(ignore_permissions=True)


def _submit(checks):
	frappe.form_dict.data = json.dumps({"area": "Team A", "date": frappe.utils.today(), "checks": checks})
	frappe.response["message"] = None
	api.submitPackhouseInspection()
	return frappe.response["message"]


class TestPackhouseInspectionItems(unittest.TestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_setup_lists_active_items_in_order_with_their_conditions(self):
		_item("ZZ Test Benches", ["Spotless", "Grimy"], ok=["Spotless"], order=-1)
		_item("ZZ Retired Thing", ["Fine"], ok=["Fine"], active=0)
		components = api.getPackhouseInspectionSetup()["components"]
		labels = [c["label"] for c in components]
		self.assertEqual(labels[0], "ZZ Test Benches")
		self.assertNotIn("ZZ Retired Thing", labels)
		self.assertEqual(components[0]["options"], ["Spotless", "Grimy"])
		self.assertEqual(components[0]["ok"], ["Spotless"])

	def test_status_follows_counts_as_ok(self):
		_item("ZZ Test Benches", ["Spotless", "Grimy"], ok=["Spotless"])
		for condition, status in (("Spotless", "√ Okay"), ("Grimy", "X Faulty")):
			built = []
			# Capture the log instead of saving it: its Area links to a doctype that only exists on the live site.
			with unittest.mock.patch(
				"frappe.model.document.Document.insert", lambda doc, *a, **k: built.append(doc) or doc
			):
				_submit([{"component": "ZZ Test Benches", "condition": condition}])
			log = [d for d in built if d.doctype == "Packhouse Inspection Log"][0]
			rows = {r.component: r.status for r in log.components}
			self.assertEqual(rows["ZZ Test Benches"], status)
			self.assertEqual(rows["Floor"], "Not Checked")
