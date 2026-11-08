import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score  # noqa: E402

C = score.load_criteria()


def house(**kw):
    base = {"address": "123 N 400 E", "city": "Payson", "price": 340000, "beds": 3,
            "baths": 2, "sqft": 1500, "lot_acres": 0.22, "year_built": 1975, "garage": True,
            "property_type": "single_family", "description": "", "days_on_market": 10}
    base.update(kw)
    return base


class Filters(unittest.TestCase):
    def test_accepts_basic_house(self):
        self.assertTrue(score.passes_filters(house(), C)[0])

    def test_rejects_townhome(self):
        self.assertFalse(score.passes_filters(house(property_type="townhouse"), C)[0])

    def test_rejects_townhome_by_keyword(self):
        self.assertFalse(score.passes_filters(house(description="End-unit townhome"), C)[0])

    def test_rejects_one_bed(self):
        self.assertFalse(score.passes_filters(house(beds=1), C)[0])

    def test_rejects_over_stretch(self):
        self.assertFalse(score.passes_filters(house(price=420000), C)[0])

    def test_rejects_tiny_lot(self):
        self.assertFalse(score.passes_filters(house(lot_acres=0.03), C)[0])

    def test_rejects_twin_home_labeled_single_family(self):
        self.assertFalse(score.passes_filters(house(description="A 2-bedroom twin home with no HOA"), C)[0])

    def test_cash_only_flagged(self):
        r = score.score_listing(house(description="Cash offers only, sold as-is"), C)
        self.assertTrue(any("mortgage" in c for c in r["cons"]))

    def test_rejects_land_lease(self):
        self.assertFalse(score.passes_filters(house(description="Home on leased land, lot rent $600"), C)[0])


class Flip(unittest.TestCase):
    def test_math(self):
        h = house(sqft=1000, price=250000, year_built=1990, city="Provo")
        f = score.flip_estimate(h, C)
        self.assertEqual(f["tier"], "medium")
        m = C["city_market"]["Provo"]
        ppsf = round(m["ppsf"] * (1000 / m["typical_sqft"]) ** -C["flip"]["size_adjust_exponent"])
        rehab = 1000 * 35
        arv = max(1000 * ppsf * C["flip"]["arv_premium_over_median"], 250000 + rehab * 1.3)
        net = arv - 250000 - rehab - arv * 0.07 - 250000 * 0.03
        self.assertEqual(f["arv"], round(arv))
        self.assertEqual(f["net_profit"], round(net))

    def test_heavy_tier_from_keyword(self):
        self.assertEqual(score.rehab_tier(house(description="Investor special, sold as-is"), C), "heavy")

    def test_arv_cap(self):
        f = score.flip_estimate(house(price=200000, sqft=3000), C)
        self.assertLessEqual(f["arv"], 200000 * 1.6)


class Scoring(unittest.TestCase):
    def test_cheaper_scores_higher(self):
        a = score.score_listing(house(price=300000), C)["fit_score"]
        b = score.score_listing(house(price=390000), C)["fit_score"]
        self.assertGreater(a, b)

    def test_score_bounds(self):
        s = score.score_listing(house(), C)["fit_score"]
        self.assertTrue(0 <= s <= 100)

    def test_stretch_flag_and_con(self):
        r = score.score_listing(house(price=389000), C)
        self.assertTrue(r["stretch"])
        self.assertTrue(any("Stretch" in c for c in r["cons"]))


class History(unittest.TestCase):
    def test_price_drop_and_gone(self):
        db = {"listings": {}}
        score.merge_history(db, [house(price=360000)], "2026-11-01", C)
        score.merge_history(db, [house(price=350000)], "2026-11-02", C)
        rows = score.dashboard_rows(db, C)
        self.assertEqual(rows[0]["price_drop"], 10000)
        score.merge_history(db, [], "2026-11-06", C)
        self.assertEqual(score.dashboard_rows(db, C), [])


if __name__ == "__main__":
    unittest.main()
