import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestShelfOperations(IntegrationTestCase):
	def setUp(self):
		self.farm = "Test Shelf Ops Farm"
		if not frappe.db.exists("Farm", self.farm):
			frappe.get_doc({
				"doctype": "Farm",
				"farm_name": self.farm,
				"company": "Karen Roses",
				"abbreviation": "TSOF",
				"farm_type": [{"farm_type": "Has Greenhouses"}],
			}).insert(ignore_permissions=True)
		self.bucket_id = "TEST-BUCKET-001"
		if not frappe.db.exists("Bucket QR Code", self.bucket_id):
			frappe.get_doc(
				{"doctype": "Bucket QR Code", "id": self.bucket_id, "item_code": "Reflex"}
			).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		# Cleaned up here (not at the end of the test methods that create them)
		# so a failed assertion mid-test can't leave a live allocation behind --
		# a leftover Bucket Allocation Status row (allocated_quantity=15,
		# uncancelled/unissued) would spuriously fail
		# test_offline_issuing_creates_stock_entry_and_clears_shelf on a later
		# run, since alphabetical test ordering runs the "blocked" test first.
		for bas_name in frappe.get_all(
			"Bucket Allocation Status", filters={"bucket_id": self.bucket_id}, pluck="name"
		):
			frappe.delete_doc("Bucket Allocation Status", bas_name, force=1, ignore_permissions=True)
		if frappe.db.exists("Sales Order", "SAL-ORD-TEST-0001"):
			frappe.db.delete("Sales Order", {"name": "SAL-ORD-TEST-0001"})
		frappe.db.delete("Stock Entry", {"custom_bucket_id": self.bucket_id})
		frappe.db.delete("Shelving Log", {"bucket_id": self.bucket_id})
		frappe.db.delete("Shelf Item", {"bucket_id": self.bucket_id})
		frappe.db.commit()

	def test_shelving_writes_shelved_log_row(self):
		shelf_id = "TEST-SHELF-A"
		if not frappe.db.exists("Shelf", shelf_id):
			frappe.get_doc(
				{"doctype": "Shelf", "shelf_id": shelf_id, "farm": self.farm}
			).insert(ignore_permissions=True)

		shelf_doc = frappe.get_doc("Shelf", shelf_id)
		new_item = shelf_doc.append("items", {})
		new_item.bucket_id = self.bucket_id
		new_item.variety = "Reflex"
		new_item.stem_qty = 40
		new_item.farm = self.farm
		new_item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)
		frappe.db.commit()

		from upande_quality.mobile.api import _write_shelved_log

		_write_shelved_log(new_item, shelf_id, self.farm)
		frappe.db.commit()

		log = frappe.get_all(
			"Shelving Log",
			filters={"bucket_id": self.bucket_id, "reason": "Shelved"},
			fields=["name", "shelf", "shelf_item", "shelved_by", "shelved_on", "removed_on"],
		)
		self.assertEqual(len(log), 1)
		self.assertEqual(log[0].shelf, shelf_id)
		self.assertEqual(log[0].shelf_item, new_item.name)
		self.assertEqual(log[0].shelved_by, frappe.session.user)
		self.assertTrue(log[0].shelved_on)
		self.assertFalse(log[0].removed_on)

	def test_create_shelving_entry_writes_shelved_log_row(self):
		shelf_id = "TEST-SHELF-F"
		if not frappe.db.exists("Shelf", shelf_id):
			frappe.get_doc({"doctype": "Shelf", "shelf_id": shelf_id, "farm": self.farm}).insert(
				ignore_permissions=True
			)

		today = frappe.utils.today()
		harvest = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Harvesting",
			"purpose": "Material Receipt",
			"company": "Karen Roses",
			"posting_date": today,
			"custom_bucket_id": self.bucket_id,
			"items": [{"item_code": "Reflex", "qty": 20, "t_warehouse": "Karen GH 04 - KR", "uom": "Stems", "allow_zero_valuation_rate": 1, "cost_center": "Karen Roses - KR"}],
		})
		harvest.insert(ignore_permissions=True)
		harvest.submit()

		receiving = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Receiving",
			"purpose": "Material Transfer",
			"company": "Karen Roses",
			"posting_date": today,
			"set_posting_time": 1,
			"custom_bucket_id": self.bucket_id,
			"items": [{
				"item_code": "Reflex", "qty": 20, "uom": "Stems",
				"s_warehouse": "Karen GH 04 - KR", "t_warehouse": "Karen Receiving Cold Store - KR",
				"custom_stem_length": "52cm", "allow_zero_valuation_rate": 1, "cost_center": "Karen Roses - KR",
			}],
		})
		receiving.insert(ignore_permissions=True)
		receiving.submit()
		frappe.db.commit()

		from upande_quality.mobile.api import createShelvingEntry

		frappe.local.form_dict = frappe._dict({})
		frappe.request = frappe._dict(
			get_json=lambda: {"shelf_id": shelf_id, "bucket_id": self.bucket_id, "farm": self.farm}
		)
		frappe.response = frappe._dict()
		createShelvingEntry()

		self.assertEqual(frappe.response["data"]["status"], "success")

		logs = frappe.get_all(
			"Shelving Log",
			filters={"bucket_id": self.bucket_id, "reason": "Shelved"},
			fields=["shelf", "shelf_item", "shelved_by", "shelved_on", "removed_on"],
		)
		self.assertEqual(len(logs), 1)
		self.assertEqual(logs[0].shelf, shelf_id)
		self.assertTrue(logs[0].shelf_item)
		self.assertEqual(logs[0].shelved_by, frappe.session.user)
		self.assertTrue(logs[0].shelved_on)
		self.assertFalse(logs[0].removed_on)

		frappe.db.delete("Stock Entry", {"custom_bucket_id": self.bucket_id})
		frappe.db.commit()

	def test_transfer_moves_shelf_item_and_writes_log(self):
		shelf_a = "TEST-SHELF-A"
		shelf_b = "TEST-SHELF-C"
		for sid in (shelf_a, shelf_b):
			if not frappe.db.exists("Shelf", sid):
				frappe.get_doc({"doctype": "Shelf", "shelf_id": sid, "farm": self.farm}).insert(
					ignore_permissions=True
				)

		shelf_doc = frappe.get_doc("Shelf", shelf_a)
		item = shelf_doc.append("items", {})
		item.bucket_id = self.bucket_id
		item.variety = "Reflex"
		item.stem_qty = 25
		item.farm = self.farm
		item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)
		frappe.db.commit()

		from upande_quality.mobile.api import _write_shelved_log

		_write_shelved_log(item, shelf_a, self.farm)
		frappe.db.commit()

		from upande_quality.mobile.api import transferBucket

		frappe.local.form_dict = frappe._dict({})
		frappe.request = frappe._dict(
			get_json=lambda: {"bucket_id": self.bucket_id, "to_shelf_id": shelf_b}
		)
		frappe.response = frappe._dict()
		transferBucket()

		self.assertEqual(frappe.response["data"]["status"], "success")
		remaining_on_a = frappe.get_all("Shelf Item", filters={"parent": shelf_a, "bucket_id": self.bucket_id})
		on_b = frappe.get_all(
			"Shelf Item", filters={"parent": shelf_b, "bucket_id": self.bucket_id}, fields=["stem_qty"]
		)
		self.assertEqual(len(remaining_on_a), 0)
		self.assertEqual(len(on_b), 1)
		self.assertEqual(on_b[0].stem_qty, 25)

		logs = frappe.get_all(
			"Shelving Log",
			filters={"bucket_id": self.bucket_id},
			fields=["reason", "shelf", "removed_on"],
			order_by="creation asc",
		)
		self.assertEqual(len(logs), 2)
		self.assertEqual(logs[0].reason, "Transferred (Shelf-to-Shelf)")
		self.assertTrue(logs[0].removed_on)
		self.assertEqual(logs[1].reason, "Shelved")
		self.assertEqual(logs[1].shelf, shelf_b)
		self.assertFalse(logs[1].removed_on)

	def test_transfer_syncs_unissued_pick_list_item_shelf(self):
		shelf_a = "TEST-SHELF-A"
		shelf_e = "TEST-SHELF-E"
		for sid in (shelf_a, shelf_e):
			if not frappe.db.exists("Shelf", sid):
				frappe.get_doc({"doctype": "Shelf", "shelf_id": sid, "farm": self.farm}).insert(
					ignore_permissions=True
				)

		shelf_doc = frappe.get_doc("Shelf", shelf_a)
		item = shelf_doc.append("items", {})
		item.bucket_id = self.bucket_id
		item.variety = "Reflex"
		item.stem_qty = 20
		item.farm = self.farm
		item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)

		opl = frappe.get_doc(
			{
				"doctype": "Order Pick List",
				"naming_series": "OPL-.YYYY.-",
				"farm": self.farm,
				"table_ytkc": [
					{"item_code": "Reflex", "bucket": self.bucket_id, "shelf": shelf_a,
					 "issued": 0, "qty": 20},
					{"item_code": "Reflex", "bucket": self.bucket_id, "shelf": shelf_a,
					 "issued": 1, "qty": 5},
				],
			}
		)
		opl.insert(ignore_permissions=True)
		unissued_row, issued_row = opl.table_ytkc[0].name, opl.table_ytkc[1].name
		frappe.db.commit()

		from upande_quality.mobile.api import transferBucket

		frappe.request = frappe._dict(
			get_json=lambda: {"bucket_id": self.bucket_id, "to_shelf_id": shelf_e}
		)
		frappe.response = frappe._dict()
		transferBucket()

		self.assertEqual(frappe.response["data"]["status"], "success")
		self.assertIn(opl.name, frappe.response["data"]["payload"]["synced_opls"])
		self.assertEqual(frappe.db.get_value("Pick List Item", unissued_row, "shelf"), shelf_e)
		# Already-issued row is left alone -- issuing already happened, its
		# shelf value is moot.
		self.assertEqual(frappe.db.get_value("Pick List Item", issued_row, "shelf"), shelf_a)

		frappe.delete_doc("Order Pick List", opl.name, force=1, ignore_permissions=True)

	def test_transfer_rejects_cross_farm(self):
		shelf_a = "TEST-SHELF-A"
		other_farm = "Test Shelf Ops Farm 2"
		if not frappe.db.exists("Farm", other_farm):
			frappe.get_doc({
				"doctype": "Farm",
				"farm_name": other_farm,
				"company": "Karen Roses",
				"abbreviation": "TSOF2",
				"farm_type": [{"farm_type": "Has Greenhouses"}],
			}).insert(ignore_permissions=True)
		shelf_d = "TEST-SHELF-D"
		if not frappe.db.exists("Shelf", shelf_d):
			frappe.get_doc({"doctype": "Shelf", "shelf_id": shelf_d, "farm": other_farm}).insert(
				ignore_permissions=True
			)
		if not frappe.db.exists("Shelf", shelf_a):
			frappe.get_doc({"doctype": "Shelf", "shelf_id": shelf_a, "farm": self.farm}).insert(
				ignore_permissions=True
			)

		shelf_doc = frappe.get_doc("Shelf", shelf_a)
		item = shelf_doc.append("items", {})
		item.bucket_id = self.bucket_id
		item.variety = "Reflex"
		item.stem_qty = 10
		item.farm = self.farm
		item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)
		frappe.db.commit()

		from upande_quality.mobile.api import transferBucket

		frappe.request = frappe._dict(
			get_json=lambda: {"bucket_id": self.bucket_id, "to_shelf_id": shelf_d}
		)
		frappe.response = frappe._dict()
		transferBucket()

		self.assertEqual(frappe.response["data"]["status"], "failed")
		self.assertEqual(frappe.response["data"]["reason"], "cross_farm_not_allowed")
		still_on_a = frappe.get_all("Shelf Item", filters={"parent": shelf_a, "bucket_id": self.bucket_id})
		self.assertEqual(len(still_on_a), 1)

	def test_offline_issuing_creates_stock_entry_and_clears_shelf(self):
		shelf_a = "TEST-SHELF-A"
		if not frappe.db.exists("Shelf", shelf_a):
			frappe.get_doc({"doctype": "Shelf", "shelf_id": shelf_a, "farm": self.farm}).insert(
				ignore_permissions=True
			)
		# "Karen Roses" company abbreviation is "KR" (see "Karen GH 04 - KR"
		# etc. elsewhere in this file), not "TSO" -- that's the real
		# auto-name suffix Warehouse.insert() below will produce.
		if not frappe.db.exists("Warehouse", "Test Shelf Ops WH - KR"):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Test Shelf Ops WH",
					"company": "Karen Roses",
					# custom_farm is mandatory on this site's Warehouse doctype
					# (not in the plan's original schema); not setting it makes
					# insert() raise MandatoryError.
					"custom_farm": self.farm,
				}
			).insert(ignore_permissions=True)
		warehouse = frappe.get_all("Warehouse", filters={"warehouse_name": "Test Shelf Ops WH"}, pluck="name")[0]

		# Seed real stock in this fresh warehouse -- createOfflineIssuingEntry
		# posts a real Material Issue out of it, and this site enforces
		# Stock Settings.allow_negative_stock = 0, so it needs an actual
		# balance first. Same two-step Harvesting -> Receiving recipe the
		# create_shelving_entry test above already uses (Stock Entry Type
		# "Receiving"'s purpose is fixed to "Material Transfer" server-side,
		# so it needs a source warehouse, not just a target). The final
		# Receiving entry lands the stock in the new warehouse AND is what
		# createOfflineIssuingEntry's own expense_account/cost_center lookup
		# -- which only scans Receiving/Late Receipt entries for this
		# bucket_id/item_code -- will find, so cost_center isn't left unset
		# on the Offline Issuing entry (mandatory for GL posting).
		harvest = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Harvesting",
			"purpose": "Material Receipt",
			"company": "Karen Roses",
			"posting_date": frappe.utils.today(),
			"custom_bucket_id": self.bucket_id,
			"items": [{
				"item_code": "Reflex", "qty": 15, "t_warehouse": "Karen GH 04 - KR", "uom": "Stems",
				"allow_zero_valuation_rate": 1, "cost_center": "Karen Roses - KR",
			}],
		})
		harvest.insert(ignore_permissions=True)
		harvest.submit()

		seed = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Receiving",
			"purpose": "Material Transfer",
			"company": "Karen Roses",
			"posting_date": frappe.utils.today(),
			"set_posting_time": 1,
			"custom_bucket_id": self.bucket_id,
			"items": [{
				"item_code": "Reflex", "qty": 15, "uom": "Stems",
				"s_warehouse": "Karen GH 04 - KR", "t_warehouse": warehouse,
				"allow_zero_valuation_rate": 1, "cost_center": "Karen Roses - KR",
			}],
		})
		seed.insert(ignore_permissions=True)
		seed.submit()
		frappe.db.commit()

		shelf_doc = frappe.get_doc("Shelf", shelf_a)
		item = shelf_doc.append("items", {})
		item.bucket_id = self.bucket_id
		item.variety = "Reflex"
		item.stem_qty = 15
		item.farm = self.farm
		item.warehouse = warehouse
		item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)
		frappe.db.commit()

		from upande_quality.mobile.api import _write_shelved_log

		_write_shelved_log(item, shelf_a, self.farm)
		frappe.db.commit()

		from upande_quality.mobile.api import createOfflineIssuingEntry

		frappe.request = frappe._dict(
			get_json=lambda: {"bucket_id": self.bucket_id, "reason": "Damaged in transit"}
		)
		frappe.response = frappe._dict()
		createOfflineIssuingEntry()

		self.assertEqual(frappe.response["data"]["status"], "success")
		stock_entry_name = frappe.response["data"]["payload"]["stock_entry"]
		se = frappe.get_doc("Stock Entry", stock_entry_name)
		self.assertEqual(se.stock_entry_type, "Offline Issuing")
		self.assertEqual(se.docstatus, 1)
		self.assertEqual(se.remarks, "Damaged in transit")

		remaining = frappe.get_all("Shelf Item", filters={"bucket_id": self.bucket_id})
		self.assertEqual(len(remaining), 0)

		log = frappe.get_all(
			"Shelving Log",
			filters={"bucket_id": self.bucket_id, "reason": "Offline Issuing"},
			fields=["removed_on"],
		)
		self.assertEqual(len(log), 1)
		self.assertTrue(log[0].removed_on)

	def test_offline_issuing_blocked_when_allocated(self):
		shelf_a = "TEST-SHELF-A"
		if not frappe.db.exists("Shelf", shelf_a):
			frappe.get_doc({"doctype": "Shelf", "shelf_id": shelf_a, "farm": self.farm}).insert(
				ignore_permissions=True
			)
		shelf_doc = frappe.get_doc("Shelf", shelf_a)
		item = shelf_doc.append("items", {})
		item.bucket_id = self.bucket_id
		item.variety = "Reflex"
		item.stem_qty = 15
		item.farm = self.farm
		item.date_added = frappe.utils.now_datetime()
		shelf_doc.save(ignore_permissions=True)

		# Bucket Allocations.sales_order is a Link to Sales Order, so it needs
		# a real row to exist for the link validation on bas.insert() below to
		# pass. A full Sales Order (customer, items, delivery date, ...) is
		# irrelevant to this test, so stub a bare row via db_insert(), which
		# bypasses controller validation/mandatory checks -- same trick used
		# to satisfy Frappe's link-exists check without a full fixture.
		# Cleaned up in tearDown, not here, so it survives an assertion failure.
		if not frappe.db.exists("Sales Order", "SAL-ORD-TEST-0001"):
			so_stub = frappe.new_doc("Sales Order")
			so_stub.name = "SAL-ORD-TEST-0001"
			so_stub.db_insert()
			frappe.db.commit()

		bas = frappe.get_doc(
			{
				"doctype": "Bucket Allocation Status",
				"bucket_id": self.bucket_id,
				"item_code": "Reflex",
				"total_quantity": 15,
				"allocated_quantity": 15,
				"available_quantity": 0,
				"bucket_allocations": [
					{"sales_order": "SAL-ORD-TEST-0001", "sales_order_item": "row1",
					 "quantity_allocated": 15, "cancelled": 0, "issued": 0}
				],
			}
		)
		bas.insert(ignore_permissions=True)
		frappe.db.commit()

		from upande_quality.mobile.api import createOfflineIssuingEntry

		frappe.request = frappe._dict(
			get_json=lambda: {"bucket_id": self.bucket_id, "reason": "Damaged"}
		)
		frappe.response = frappe._dict()
		createOfflineIssuingEntry()

		self.assertEqual(frappe.response["data"]["status"], "failed")
		self.assertEqual(frappe.response["data"]["reason"], "bucket_allocated")
		self.assertIn("SAL-ORD-TEST-0001", frappe.response["data"]["message"])
		still_on_shelf = frappe.get_all("Shelf Item", filters={"bucket_id": self.bucket_id})
		self.assertEqual(len(still_on_shelf), 1)
		# BAS and Sales Order stub cleanup happens in tearDown (see comment there).
