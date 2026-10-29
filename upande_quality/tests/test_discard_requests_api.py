import frappe
from frappe.tests import IntegrationTestCase

from upande_quality.api import discard_requests as dr


class IntegrationTestDiscardRequestsApi(IntegrationTestCase):
	"""Runs against the shelf data of a kaitet bench; skipped where there is none."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.pre = preview = dr.preview_buckets(age_threshold=0)
		if not preview["groups"]:
			self.skipTest("needs shelved buckets")
		self.made = []

	def tearDown(self):
		for n in self.made:
			doc = frappe.get_doc("Discard Request", n)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Discard Request", n, force=True)
		frappe.db.commit()

	def test_preview_groups_add_up(self):
		p = self.pre
		self.assertEqual(sum(g["buckets"] for g in p["groups"]), p["buckets"])
		self.assertAlmostEqual(sum(g["stems"] for g in p["groups"]), p["stems"])

	def test_filters_narrow_the_preview(self):
		g = self.pre["groups"][0]
		p = dr.preview_buckets(farms=[g["farm"]], varieties=[g["variety"]], lengths=[g["length"]] if g["length"] else None, age_threshold=0)
		self.assertTrue(p["groups"])
		self.assertTrue(all(x["farm"] == g["farm"] and x["variety"] == g["variety"] for x in p["groups"]))

	def test_age_threshold_is_applied(self):
		young = dr.preview_buckets(age_threshold=0)["buckets"]
		old = dr.preview_buckets(age_threshold=10000)["buckets"]
		self.assertEqual(old, 0)
		self.assertGreater(young, 0)

	def test_one_request_per_farm_and_buckets_not_listed_twice(self):
		by_farm = {}
		for g in self.pre["groups"]:
			by_farm.setdefault(g["farm"], g)
		picks = list(by_farm.values())[:2]
		res = dr.create_requests(
			groups=[[g["farm"], g["variety"], g["length"]] for g in picks],
			age_threshold=0,
			action="approve" if (dr._workflow() or {"can_approve": True})["can_approve"] else "send",
		)
		self.made += res["created"]
		self.assertEqual(res["failed"], [])
		self.assertEqual(len(res["created"]), len(picks))
		for name, g in zip(res["created"], picks):
			doc = frappe.get_doc("Discard Request", name)
			self.assertEqual(doc.farm, g["farm"])
			self.assertEqual(len(doc.buckets), g["buckets"])
			self.assertEqual(doc.requested_by, "Administrator")
		# the same buckets cannot be put on a second request
		again = dr.preview_buckets(age_threshold=0)
		left = {(x["farm"], x["variety"], x["length"]) for x in again["groups"]}
		for g in picks:
			self.assertNotIn((g["farm"], g["variety"], g["length"]), left)
		self.assertGreaterEqual(again["left_out"]["on_a_request"], sum(g["buckets"] for g in picks))

	def test_several_values_are_kept_in_the_remarks(self):
		vs = sorted({g["variety"] for g in self.pre["groups"]})[:2]
		if len(vs) < 2:
			self.skipTest("needs two varieties")
		farm = next(g["farm"] for g in self.pre["groups"] if g["variety"] == vs[0])
		res = dr.create_requests(farms=[farm], varieties=vs, age_threshold=0, action="draft")
		self.made += res["created"]
		doc = frappe.get_doc("Discard Request", res["created"][0])
		self.assertFalse(doc.variety)
		self.assertIn(vs[0], doc.remarks)
		self.assertTrue(all(b.variety in vs for b in doc.buckets))

	def test_nothing_to_create_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			dr.create_requests(farms=["No Such Farm"], age_threshold=0)

	def test_overview_tells_system_from_user(self):
		o = dr.get_overview()
		k = o["kpis"]
		self.assertEqual(k["requests"], k["system"] + k["user"])
		self.assertEqual(k["requests"], len(o["requests"]))
		res = dr.create_requests(groups=[[g["farm"], g["variety"], g["length"]] for g in self.pre["groups"][:1]], age_threshold=0, action="draft")
		self.made += res["created"]
		mine = [r for r in dr.get_overview(origin="User")["requests"] if r["name"] == res["created"][0]]
		self.assertEqual(mine, [] if frappe.session.user == "Administrator" else mine)
		d = dr.get_request(res["created"][0])
		self.assertEqual(d["state"], "Draft")
		self.assertTrue(d["buckets"])

	def test_workflow_actions(self):
		if not dr._workflow():
			self.skipTest("no workflow")
		g = self.pre["groups"][0]
		res = dr.create_requests(groups=[[g["farm"], g["variety"], g["length"]]], age_threshold=0, action="draft")
		self.made += res["created"]
		out = dr.act_on_requests(res["created"], "Submit for Approval")
		self.assertEqual(out["failed"], [])
		self.assertEqual(dr.get_request(res["created"][0])["state"], "Pending Approval")
		out = dr.act_on_requests(res["created"], "Approve")
		self.assertEqual(out["failed"], [])
		self.assertEqual(dr.get_request(res["created"][0])["state"], "Approved")
