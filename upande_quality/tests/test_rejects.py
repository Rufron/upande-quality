"""Rejects dashboard: linking an intake reject to the Quarantined QC record behind it, and the farm fallback."""

import unittest

import frappe

from upande_quality.api.rejects import QUARANTINED, REJECTED, entry_farm, match_qc, quarantine_for


def _qc(name, creation, variety="Giselle", action="Quarantined"):
	return frappe._dict(name=name, creation=creation, variety=variety, control_action=action)


def _q(name, creation, batch):
	return frappe._dict(name=name, creation=creation, custom_receiving_batch_id=batch, owner="bethy@x")


class TestRejects(unittest.TestCase):
	line = frappe._dict(
		creation="2026-11-16 15:32:00",
		item_code="Giselle",
		custom_bucket_id="yg7xhl",
		custom_receiving_batch_id="B2",
	)

	def test_only_a_quarantined_record_of_the_same_variety_is_linked(self):
		qcs = [
			_qc("Intake-1", "2026-11-16 14:15:00", action="Accepted"),
			_qc("Intake-2", "2026-11-16 14:15:01", action="Rejected"),
			_qc("Intake-3", "2026-11-16 14:15:02", variety="Tessa"),
			_qc("Intake-4", "2026-11-16 14:15:03"),
		]
		self.assertEqual(match_qc(self.line, qcs, "2026-11-16 14:15:00", QUARANTINED).name, "Intake-4")

	def test_direct_reject_links_the_rejected_record_never_an_accepted_one(self):
		qcs = [
			_qc("Intake-Karen-2241", "2026-11-16 15:32:00", action="Accepted"),
			_qc("Intake-Karen-2218", "2026-11-16 15:32:01", action="Rejected"),
		]
		self.assertEqual(match_qc(self.line, qcs, self.line.creation, REJECTED).name, "Intake-Karen-2218")
		self.assertIsNone(match_qc(self.line, qcs[:1], self.line.creation, REJECTED))

	def test_no_quarantined_record_links_nothing(self):
		qcs = [_qc("Intake-1", "2026-11-16 14:15:00", action="Accepted")]
		self.assertIsNone(match_qc(self.line, qcs, "2026-11-16 14:15:00", QUARANTINED))

	def test_record_outside_the_window_is_ignored(self):
		self.assertIsNone(
			match_qc(self.line, [_qc("Intake-1", "2026-11-16 13:00:00")], "2026-11-16 14:15:00")
		)

	def test_quarantine_move_of_the_same_batch_wins_over_an_older_use_of_the_bucket(self):
		moves = {
			"YG7XHL": [_q("SE-old", "2026-09-20 12:04:00", "B1"), _q("SE-now", "2026-11-16 14:15:00", "B2")]
		}
		self.assertEqual(quarantine_for(self.line, moves).name, "SE-now")

	def test_reject_without_a_quarantine_move_for_its_batch_is_not_quarantined(self):
		self.assertIsNone(quarantine_for(self.line, {"YG7XHL": [_q("SE-old", "2026-09-20 12:04:00", "B1")]}))
		self.assertIsNone(quarantine_for(self.line, {}))

	def test_farm_falls_back_to_greenhouse_then_warehouse(self):
		self.assertEqual(entry_farm(frappe._dict(custom_greenhouse="Karen GH 08 - KR")), "Karen")
		self.assertEqual(
			entry_farm(frappe._dict(s_warehouse="Chepsito Receiving Cold Store - KR")), "Chepsito"
		)
		self.assertEqual(
			entry_farm(frappe._dict(farm="Simotwo", custom_greenhouse="Karen GH 08 - KR")), "Simotwo"
		)
