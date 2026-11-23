"""CAR Issues table: filled from the issue text the QC endpoints write, and the summary kept in sync."""

import unittest

import frappe

from upande_quality.upande_quality.doctype.corrective_action_report.corrective_action_report import (
	sync_issues,
)


def _car(**fields):
	return frappe.get_doc({"doctype": "Corrective Action Report", **fields})


class TestCarIssues(unittest.TestCase):
	def test_intake_and_packhouse_text_become_rows(self):
		for text in ("Thrips Damage (6), Bruises (5)", "Thrips Damage x6; Bruises x5"):
			car = _car(issue=text)
			sync_issues(car)
			self.assertEqual([(r.issue, r.stems) for r in car.issues], [("Thrips Damage", 6), ("Bruises", 5)])
			self.assertEqual(car.issue, "Thrips Damage (6), Bruises (5)")

	def test_rows_win_and_rebuild_the_summary(self):
		car = _car(issue="old text", issues=[{"issue": "Botrytis", "stems": 9}, {"issue": "Rust"}])
		sync_issues(car)
		self.assertEqual(car.issue, "Botrytis (9), Rust")

	def test_free_text_without_counts_is_one_row_and_empty_stays_empty(self):
		car = _car(issue="Wrong length")
		sync_issues(car)
		self.assertEqual([(r.issue, r.stems) for r in car.issues], [("Wrong length", None)])
		empty = _car(issue="")
		sync_issues(empty)
		self.assertEqual(empty.issues, [])
