"""Grade B holding: a bucket moved to Grade B becomes discardable only after Grade B Discard Age days."""

import unittest
from unittest import mock

from frappe.utils import add_days, now_datetime

from upande_quality import grade_b


class TestGradeBDue(unittest.TestCase):
	def _due(self, days_ago, age):
		with mock.patch.object(grade_b, "grade_b_age_days", return_value=age):
			return grade_b.is_due(add_days(now_datetime(), -days_ago))

	def test_not_due_before_the_age(self):
		self.assertFalse(self._due(2, 3))

	def test_due_on_the_age(self):
		self.assertTrue(self._due(3, 3))

	def test_zero_age_is_due_straight_away(self):
		self.assertTrue(self._due(0, 0))

	def test_never_moved_is_due(self):
		self.assertTrue(grade_b.is_due(None))
