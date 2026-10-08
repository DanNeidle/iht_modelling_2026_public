# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Worked examples for the additional scenarios in the article."""
import unittest

from review_calculations import retention_saving, fixed_cost_offset
from downsizing_channel import article_estimate


class ArticleCalculationTests(unittest.TestCase):
    def test_new_allowances_remove_the_whole_bill(self):
        self.assertEqual(retention_saving(100_000, 2, 0, 600_000), 0)

    def test_saving_is_capped_at_remaining_tax(self):
        self.assertEqual(retention_saving(200_000, 2, 0, 600_000), 60_000)

    def test_saving_is_capped_at_home_value(self):
        self.assertEqual(retention_saving(1_000_000, 1, 0, 500_000), 200_000)

    def test_old_downsizing_relief_is_replaced(self):
        self.assertEqual(retention_saving(100_000, 2, 350_000, 600_000), 100_000)

    def test_fixed_cost_example_in_article(self):
        self.assertEqual(fixed_cost_offset(100_000, 30_000, 10_000), 6_000)

    def test_continuing_planning_has_no_exit_offset(self):
        self.assertEqual(fixed_cost_offset(100_000, 80_000, 10_000), 0)

    def test_no_tax_restored_from_exempt_estate(self):
        self.assertEqual(fixed_cost_offset(100_000, 0, 10_000), 0)

    def test_downsizing_uses_article_share_and_scale(self):
        out = article_estimate()
        self.assertEqual(out['low_2023'], 125_000_000)
        self.assertAlmostEqual(out['high_2023'], 326_666_666.6666666)
        self.assertAlmostEqual(out['low_2029']/out['low_2023'], 13.7/7.03)


if __name__ == '__main__':
    unittest.main()
