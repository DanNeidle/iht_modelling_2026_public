# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Worked examples for policy rules, descendant branches and threshold responses."""
import dataclasses
import unittest

from behaviour import (Estate, RULES_2027_28, average_response, marginal_rate,
                       marginal_response, portfolio_loss, reformed_tax,
                       response_at_threshold)


class BehaviourTests(unittest.TestCase):
    def test_500k_band_and_home_exemption(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=1e6,
                   nil_rate_bands=2, residence_bands=2)
        self.assertEqual(reformed_tax(e, 1e6, RULES_2027_28).tax, 0)
        no_desc = dataclasses.replace(e, has_direct_descendants=False)
        self.assertEqual(reformed_tax(no_desc, 1e6, RULES_2027_28).tax, 400_000)

    def test_descendant_case_already_exempt_has_no_portfolio_saving(self):
        e = Estate(property_wealth=100_000, residence_value=100_000, financial_wealth=500_000)
        no_desc = dataclasses.replace(e, has_direct_descendants=False)
        self.assertEqual(portfolio_loss(e, 100_000, RULES_2027_28, .075), 0)
        self.assertEqual(portfolio_loss(no_desc, 100_000, RULES_2027_28, .075), 0)
        self.assertEqual(reformed_tax(no_desc, 100_000, RULES_2027_28).tax, 40_000)

    def test_portfolio_saving_cannot_exceed_branch_tax(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=600_000)
        self.assertAlmostEqual(portfolio_loss(e, 1e6, RULES_2027_28, .5), 40_000)

    def test_taper_marginal_rate_and_response(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=1.2e6,
                   nil_rate_bands=2, residence_bands=2)
        self.assertAlmostEqual(marginal_rate(e, RULES_2027_28), .6)
        extra, tax = marginal_response(e, 1e6, RULES_2027_28, .2)
        self.assertAlmostEqual(extra, 1.2e6 * (1.5 ** .2 - 1))
        self.assertAlmostEqual(tax, .4 * extra)

    def test_unchanged_40_percent_gives_no_marginal_response(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=3e6)
        self.assertAlmostEqual(marginal_rate(e, RULES_2027_28), .4)
        self.assertEqual(marginal_response(e, 1e6, RULES_2027_28, .2), (0, 0))

    def test_new_nonpayer_response_stops_at_threshold(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=490_000)
        extra, tax = marginal_response(e, 1e6, RULES_2027_28, .2)
        self.assertAlmostEqual(extra, 10_000)
        self.assertEqual(tax, 0)

    def test_old_taper_case_can_reenter_tax(self):
        e = Estate(property_wealth=1.8e6, residence_value=1.8e6, financial_wealth=490_000)
        extra, tax = marginal_response(e, 1.8e6, RULES_2027_28, .2)
        self.assertGreater(extra, 10_000)
        self.assertAlmostEqual(tax, .4 * (extra - 10_000))

    def test_average_scenario_can_include_new_payers(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=490_000)
        _, tax = average_response(e, 1e6, RULES_2027_28, .2, "financial")
        self.assertGreater(tax, 0)

    def test_home_growth_is_exempt_in_proportional_response(self):
        e = Estate(property_wealth=1e6, residence_value=1e6, financial_wealth=1e6)
        extra, tax = average_response(e, 1e6, RULES_2027_28, .2)
        self.assertAlmostEqual(tax, .4 * extra / 2)

    def test_no_response_at_zero_elasticity(self):
        self.assertEqual(response_at_threshold(500_000, .6, -100_000, 0), 0)


if __name__ == "__main__":
    unittest.main()
