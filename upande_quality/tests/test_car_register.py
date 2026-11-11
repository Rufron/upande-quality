"""CAR issue parsing (CAR Register rows) and the one-open-CAR-per-greenhouse-and-concern rule."""

import unittest
from unittest.mock import patch

import frappe

from upande_quality.mobile import api
from upande_quality.mobile.api import car_concerns, is_pest_or_disease, open_car_for_concerns


class TestCarConcerns(unittest.TestCase):
	def test_intake_format(self):
		self.assertEqual(
			car_concerns("Thrips Damage (6), Tight Cut Stage (100), Bruises (5)"),
			[("Thrips Damage", 6), ("Tight Cut Stage", 100), ("Bruises", 5)],
		)

	def test_packhouse_format(self):
		self.assertEqual(
			car_concerns("Thrips Damage x5; Box Damage x3; Bruises x1"),
			[("Thrips Damage", 5), ("Box Damage", 3), ("Bruises", 1)],
		)

	def test_free_text_and_blank(self):
		self.assertEqual(car_concerns("Wrong length"), [("Wrong length", None)])
		self.assertEqual(car_concerns(None), [("", None)])


class TestPestOrDisease(unittest.TestCase):
	def test_only_pests_and_diseases_raise_cars(self):
		for concern in ("Thrips Damage", "fresh powdery mildew", "Fcm Egg", " Mites "):
			self.assertTrue(is_pest_or_disease(concern), concern)
		for concern in ("Tight Cut Stage", "Short Stem", "Bruises", "Wrong Length", "", None):
			self.assertFalse(is_pest_or_disease(concern), concern)


def _car(name, variety, issue, target):
	return frappe._dict(name=name, variety=variety, issue=issue, target_date=target)


class TestOpenCarForConcerns(unittest.TestCase):
	def _run(self, cars, concerns, variety="Moonwalk"):
		with (
			patch.object(api.frappe, "get_all", return_value=cars),
			patch.object(api.frappe.utils, "nowdate", return_value="2026-11-11"),
		):
			return open_car_for_concerns("Kapkolia GH 15 - KR", concerns, variety)

	def test_same_issue_other_variety_blocks(self):
		cars = [_car("CAR-1", "Bellalinda", "Thrips Damage (2)", "2026-11-18")]
		self.assertEqual(self._run(cars, ["Thrips Damage"]), "CAR-1")

	def test_any_overlap_blocks(self):
		cars = [_car("CAR-1", "Bellalinda", "Thrips Damage x2", "2026-11-18")]
		self.assertEqual(self._run(cars, ["Bruises", "thrips damage"]), "CAR-1")

	def test_different_issue_or_past_target_creates(self):
		cars = [
			_car("CAR-1", "Bellalinda", "Mite Damage (2)", "2026-11-18"),
			_car("CAR-2", "Moonwalk", "Thrips Damage (2)", "2026-11-10"),
		]
		self.assertIsNone(self._run(cars, ["Thrips Damage"]))

	def test_no_concerns_falls_back_to_variety(self):
		cars = [_car("CAR-1", "Moonwalk", "", "2026-11-18")]
		self.assertEqual(self._run(cars, []), "CAR-1")
		self.assertIsNone(self._run(cars, [], variety="Alicia"))
