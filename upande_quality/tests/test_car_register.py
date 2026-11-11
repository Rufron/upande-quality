"""CAR Register splits a CAR's issue text into one row per concern."""

import unittest

from upande_quality.upande_quality.report.car_register.car_register import split_concerns


class TestSplitConcerns(unittest.TestCase):
	def test_counts(self):
		self.assertEqual(
			split_concerns("Thrips Damage (6), Tight Cut Stage (100), Bruises (5)"),
			[("Thrips Damage", 6), ("Tight Cut Stage", 100), ("Bruises", 5)],
		)

	def test_free_text_and_blank(self):
		self.assertEqual(split_concerns("Wrong length"), [("Wrong length", None)])
		self.assertEqual(split_concerns(None), [("", None)])
