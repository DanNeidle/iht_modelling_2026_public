# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Rough first-principles sizing of the downsizing channel of the lock-in effect, England.
"""
OLDER_HOUSEHOLDS = 7.5e6
OWNER_SHARE = 0.74
MOVES_PER_YEAR = 0.02
DOWNSIZE_SHARE = (0.36, 0.47)
EQUITY_PER_MOVE = 129e3
TAXABLE_SHARE = (0.25, 1 / 3)
RETAINED = (0.5, 0.7)


def article_estimate():
    """Article scenario: £5bn to £7bn released; half as many moves.

    The taxable share is the share remaining taxable after the new allowances.
    Historical amounts are scaled by total IHT, not a consumer price index.
    """
    lo = .4 * 5e9 * TAXABLE_SHARE[0] * RETAINED[0] * .5
    hi = .4 * 7e9 * TAXABLE_SHARE[1] * RETAINED[1] * .5
    return dict(low_2023=lo, high_2023=hi,
                low_2029=lo * 13.7 / 7.03, high_2029=hi * 13.7 / 7.03)


if __name__ == "__main__":
    owners = OLDER_HOUSEHOLDS * OWNER_SHARE
    moves = owners * MOVES_PER_YEAR
    lo, hi = float("inf"), 0.0
    for d in DOWNSIZE_SHARE:
        released = moves * d * EQUITY_PER_MOVE
        print(f"downsize share {d:.0%}: {moves*d:,.0f} downsizing moves a year, £{released/1e9:.1f}bn released")
        for t in TAXABLE_SHARE:
            for r in RETAINED:
                at_stake = 0.4 * released * t * r
                lo, hi = min(lo, at_stake), max(hi, at_stake)
                print(f"  taxable share {t:.0%}, retained {r:.0%}: £{at_stake/1e9:.2f}bn a year if all downsizing stopped, £{at_stake/2e9:.2f}bn at a 50% fall")
    print(f"\nrange if all downsizing by taxable estates stopped: £{lo/1e9:.2f}bn to £{hi/1e9:.2f}bn a year (2023-24 money)")
    article = article_estimate()
    print("\nArticle scenario, using the rounded £5bn to £7bn equity-release range:")
    print(f"  50% fewer moves: £{article['low_2023']/1e6:.0f}m to £{article['high_2023']/1e6:.0f}m (2023-24)")
    print(f"  2029-30 receipts scale: £{article['low_2029']/1e6:.0f}m to £{article['high_2029']/1e6:.0f}m")
    print("  Article rounds this to £250m to £650m a year.")
