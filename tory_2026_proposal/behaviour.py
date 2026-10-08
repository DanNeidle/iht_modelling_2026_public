# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Conditional behavioural scenarios for the Conservative residence proposal.

"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# In our project this file sits in code/residence_codex/ and the costing scripts in code/main_residence/. In the public
# repository they all sit together in tory_2026_proposal/. Put both possible homes on the path; _paths then finds the
# household model.
HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "main_residence"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import _paths  # noqa: E402

from lib.config import RULES_2027_28, RuleSet  # noqa: E402
from lib.iht_rules import Estate, compute_tax  # noqa: E402
from model.build_units import UNITS_PATH  # noqa: E402
from model.simulate import estate_from_row  # noqa: E402
from static_cost import VARIANTS, rules_for_variant, run_variant  # noqa: E402
from distribution import death_weights  # noqa: E402

ROOT = _paths.ROOT
DESIGN = VARIANTS[1]
DEFAULT_OUTPUT = _paths.results_dir()


def reformed_estate(estate: Estate, home: float) -> Estate:
    exempt = home if estate.has_direct_descendants else 0.0
    return dataclasses.replace(estate, property_wealth=estate.property_wealth - exempt,
                               residence_value=0.0)


def reformed_tax(estate: Estate, home: float, rules: RuleSet):
    return compute_tax(reformed_estate(estate, home), rules_for_variant(rules, DESIGN))


def marginal_rate(estate: Estate, rules: RuleSet) -> float:
    """Right derivative for another pound of ordinary taxable wealth."""
    tax0 = compute_tax(estate, rules).tax
    tax1 = compute_tax(dataclasses.replace(estate, financial_wealth=estate.financial_wealth + 1), rules).tax
    return float(np.clip(round(tax1 - tax0, 8), 0.0, 0.999))


def portfolio_loss(estate: Estate, home: float, rules: RuleSet, share: float) -> float:
    """Move financial assets into an existing qualifying home; reprice tax."""
    if home <= 0 or not estate.has_direct_descendants:
        return 0.0
    moved = share * max(0.0, estate.financial_wealth)
    changed = dataclasses.replace(estate, property_wealth=estate.property_wealth + moved,
                                  financial_wealth=estate.financial_wealth - moved,
                                  residence_value=estate.residence_value + moved)
    return reformed_tax(estate, home, rules).tax - reformed_tax(changed, home + moved, rules).tax


def ordinary_nonhome_wealth(estate: Estate, home: float, rules: RuleSet) -> float:
    return max(0.0, estate.property_wealth - home + estate.financial_wealth
               + estate.physical_wealth + (estate.dc_pension_wealth if rules.pensions_in_estate else 0))


def response_at_threshold(base: float, old_rate: float, headroom: float,
                          elasticity: float, new_rate: float = 0.4) -> float:
    """Extra wealth at the optimum of an isoelastic response on a kinked budget.

    The responsive wealth x has two candidate optima. Below the new threshold
    the net return is 1; above it the net return is 1-new_rate. Where neither
    candidate lies in its own segment, the optimum is exactly at the threshold.
    This prevents applying a 0% marginal rate to wealth that then becomes taxed.
    """
    if base <= 0 or elasticity == 0:
        return 0.0
    threshold = base + headroom
    low_tax_optimum = base * (1 / (1 - old_rate)) ** elasticity
    high_tax_optimum = base * ((1 - new_rate) / (1 - old_rate)) ** elasticity
    if low_tax_optimum <= threshold:
        chosen = low_tax_optimum
    elif high_tax_optimum >= threshold:
        chosen = high_tax_optimum
    else:
        chosen = threshold
    return chosen - base


def marginal_response(estate: Estate, home: float, rules: RuleSet, elasticity: float,
                      responsive_base: str = "ordinary_nonhome") -> tuple[float, float]:
    base = (max(0.0, estate.financial_wealth) if responsive_base == "financial"
            else ordinary_nonhome_wealth(estate, home, rules))
    after = reformed_tax(estate, home, rules)
    headroom = after.nil_rate_band_available - after.chargeable_estate
    extra = response_at_threshold(base, marginal_rate(estate, rules), headroom,
                                  elasticity, rules.rate)
    changed = dataclasses.replace(estate, financial_wealth=estate.financial_wealth + extra)
    return extra, reformed_tax(changed, home, rules).tax - after.tax


def average_response(estate: Estate, home: float, rules: RuleSet, elasticity: float,
                     allocation: str = "proportional") -> tuple[float, float]:
    """Linear average-rate elasticity, then recompute liability including entrants."""
    before = compute_tax(estate, rules)
    after = reformed_tax(estate, home, rules)
    gross = before.gross_estate
    if gross <= 0:
        return 0.0, 0.0
    growth = elasticity * ((1 - after.tax / gross) / (1 - before.tax / gross) - 1)
    extra = gross * growth
    if allocation == "financial":
        changed = dataclasses.replace(estate, financial_wealth=estate.financial_wealth + extra)
        new_home = home
    else:
        fields = ("property_wealth", "financial_wealth", "physical_wealth", "business_agricultural", "residence_value")
        values = {field: getattr(estate, field) * (1 + growth) for field in fields}
        if rules.pensions_in_estate:
            values["dc_pension_wealth"] = estate.dc_pension_wealth * (1 + growth)
        changed = dataclasses.replace(estate, **values)
        new_home = home * (1 + growth)
    return extra, reformed_tax(changed, new_home, rules).tax - after.tax


def calculate(units: pd.DataFrame, rules: RuleSet) -> dict:
    deaths = death_weights(units).to_numpy()
    shares = (0.075, 0.10, 0.15, 0.20, 0.30, 0.50)
    elasticities = (0.0, 0.1, 0.2, 0.3)
    weights = {"stock": units.weight.to_numpy(), "annual_deaths": deaths}
    totals = {label: dict(baseline_tax=0.0, post_reform_tax=0.0, baseline_payers=0.0,
                         post_reform_payers=0.0, post_reform_taxable_excess=0.0,
                         financial_wealth_of_post_reform_payers=0.0,
                         portfolio_eligible_financial_wealth=0.0)
              for label in weights}
    portfolio = {label: {s: 0.0 for s in shares} for label in weights}
    transitions: dict[tuple[float, float], dict] = {}
    scenarios = {(method, basis, e): dict(extra_wealth=0.0, tax_offset=0.0,
                    tax_offset_from_static_nonpayers=0.0, new_payers=0.0)
                 for method, bases in (("marginal", ("ordinary_nonhome", "financial")),
                                       ("average", ("proportional", "financial")))
                 for basis in bases for e in elasticities}
    for i, row in enumerate(units.itertuples()):
        home = max(0.0, float(row.residence_value))
        for desc, probability in ((True, row.p_has_descendants), (False, 1 - row.p_has_descendants)):
            if probability <= 0:
                continue
            estate = estate_from_row(row, desc)
            before, after = compute_tax(estate, rules), reformed_tax(estate, home, rules)
            assert after.tax <= before.tax + 1e-6
            for label, ww in weights.items():
                w = float(ww[i]) * probability
                t = totals[label]
                t["baseline_tax"] += w * before.tax
                t["post_reform_tax"] += w * after.tax
                t["baseline_payers"] += w * (before.tax > 0)
                t["post_reform_payers"] += w * (after.tax > 0)
                t["post_reform_taxable_excess"] += w * after.taxable_amount
                t["financial_wealth_of_post_reform_payers"] += w * max(estate.financial_wealth, 0) * (after.tax > 0)
                t["portfolio_eligible_financial_wealth"] += w * max(estate.financial_wealth, 0) * (after.tax > 0 and desc and home > 0)
                for s in shares:
                    loss = portfolio_loss(estate, home, rules, s)
                    assert -1e-6 <= loss <= after.tax + 1e-6
                    portfolio[label][s] += w * loss
            w = float(deaths[i]) * probability
            if w == 0:
                continue
            old_m = marginal_rate(estate, rules)
            new_m = marginal_rate(reformed_estate(estate, home), rules_for_variant(rules, DESIGN))
            group = transitions.setdefault((old_m, new_m), dict(estates=0.0, baseline_payers=0.0,
                         post_reform_payers=0.0, baseline_tax=0.0, post_reform_tax=0.0))
            group["estates"] += w
            group["baseline_payers"] += w * (before.tax > 0)
            group["post_reform_payers"] += w * (after.tax > 0)
            group["baseline_tax"] += w * before.tax
            group["post_reform_tax"] += w * after.tax
            for (method, basis, e), out in scenarios.items():
                extra, offset = (marginal_response(estate, home, rules, e, basis) if method == "marginal"
                                 else average_response(estate, home, rules, e, basis))
                assert offset >= -1e-5, (method, basis, offset)
                out["extra_wealth"] += w * extra
                out["tax_offset"] += w * offset
                if after.tax < 1e-6:
                    out["tax_offset_from_static_nonpayers"] += w * offset
                    out["new_payers"] += w * (offset > 1e-6)
    # Independent comparison with the static implementation, both stock and flow.
    for variant, key in ((VARIANTS[0], "baseline_tax"), (DESIGN, "post_reform_tax")):
        f = run_variant(units, rules, variant)
        for label, ww in weights.items():
            reference = float((f.expected_tax.to_numpy() * ww).sum())
            assert np.isclose(reference, totals[label][key], rtol=1e-10)
    annual_base = totals["annual_deaths"]["baseline_tax"]
    annual_after = totals["annual_deaths"]["post_reform_tax"]
    return dict(rules=rules.name, price_basis="April 2027" if rules == RULES_2027_28 else "2023-24 validation",
                totals=totals,
                marginal_transitions=[dict(old_rate=k[0], new_rate=k[1], **v)
                                      for k, v in sorted(transitions.items())],
                portfolio=[dict(weight_basis=label, share_moved=s, tax_loss=loss,
                                share_of_remaining_tax=loss / totals[label]["post_reform_tax"],
                                share_of_baseline_tax=loss / totals[label]["baseline_tax"])
                           for label, pp in portfolio.items() for s, loss in pp.items()],
                offsets=[dict(method=k[0], responsive_base=k[1], elasticity=k[2], **v,
                              share_of_baseline_tax=v["tax_offset"] / annual_base,
                              share_of_remaining_tax=v["tax_offset"] / annual_after,
                              illustrative_2029_30_gbp=v["tax_offset"] / annual_base * 13.7e9)
                         for k, v in scenarios.items()])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation", action="store_true", help="Rebuild survey at 2023-24 prices and rules")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    # In our project, derived data must live under notes/. Elsewhere there is no such folder and anything goes.
    if (ROOT / "notes").is_dir() and not output.is_relative_to(ROOT / "notes"):
        parser.error("Aggregate derived data must be written under notes/")
    if args.validation:
        from model.validate import units_at_validation_prices
        from lib.config import RULES_2023_24
        units, _ = units_at_validation_prices()
        rules = RULES_2023_24
    else:
        units, rules = pd.read_parquet(UNITS_PATH), RULES_2027_28
    result = calculate(units, rules)
    output.mkdir(parents=True, exist_ok=True)
    suffix = "validation" if args.validation else "2027"
    (output / f"behaviour_{suffix}.json").write_text(json.dumps(result, indent=2) + "\n")
    for field in ("marginal_transitions", "portfolio", "offsets"):
        pd.DataFrame(result[field]).to_csv(output / f"{field}_{suffix}.csv", index=False)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
