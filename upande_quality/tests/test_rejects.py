"""Rejects dashboard: linking a reject entry to its Quality Reporting record, and the farm fallback."""

import unittest

import frappe

from upande_quality.api.rejects import entry_farm, match_qc


def _qc(name, creation, variety="Giselle", action="Rejected"):
	return frappe._dict(name=name, creation=creation, variety=variety, control_action=action)


class TestRejects(unittest.TestCase):
	line = frappe._dict(creation="2026-11-16 12:51:58", item_code="Giselle")

	def test_same_variety_nearest_rejected_record_wins(self):
		qcs = [
			_qc("Intake-Karen-2238", "2026-11-16 12:51:58", action="Accepted"),
			_qc("Intake-Karen-2218", "2026-11-16 12:51:59"),
			_qc("Intake-Karen-2210", "2026-11-16 12:51:58", variety="Tessa"),
		]
		self.assertEqual(match_qc(self.line, qcs).name, "Intake-Karen-2218")

	def test_accepted_record_is_used_when_no_rejected_one(self):
		qcs = [_qc("Intake-Karen-2238", "2026-11-16 12:52:30", action="Accepted")]
		self.assertEqual(match_qc(self.line, qcs).name, "Intake-Karen-2238")

	def test_record_outside_the_window_is_ignored(self):
		self.assertIsNone(match_qc(self.line, [_qc("Intake-Karen-1", "2026-11-16 12:20:00")]))

	def test_farm_falls_back_to_greenhouse_then_warehouse(self):
		self.assertEqual(entry_farm(frappe._dict(custom_greenhouse="Karen GH 08 - KR")), "Karen")
		self.assertEqual(
			entry_farm(frappe._dict(s_warehouse="Chepsito Receiving Cold Store - KR")), "Chepsito"
		)
		self.assertEqual(
			entry_farm(frappe._dict(farm="Simotwo", custom_greenhouse="Karen GH 08 - KR")), "Simotwo"
		)
