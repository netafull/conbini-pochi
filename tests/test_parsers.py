#!/usr/bin/env python3
"""fixtures/ を相手にしたパーサのユニットテスト。本番へは一切アクセスしない。"""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def read(path: str) -> str:
    return (FIX / path).read_text(encoding="utf-8")


class TestSeven(unittest.TestCase):
    def test_list_parses_multiple_items_with_code(self):
        items = parsers.parse_seven_list(read("seven/thisweek.html"))
        self.assertGreater(len(items), 10)
        first = items[0]
        self.assertEqual(first["product_id"], "044723")
        self.assertTrue(first["name"])
        self.assertTrue(first["detail_url"].startswith("/products/a/item/"))

    def test_detail_parses_description_price_nutrition(self):
        d = parsers.parse_seven_detail(read("seven/item_044723_hokkaido.html"))
        self.assertIsNotNone(d)
        self.assertEqual(d["product_id"], "044723")
        self.assertIn("たらこ", d["description"])
        self.assertEqual(parsers.parse_price_yen(d["price_text"].split("（")[0]), 248)
        self.assertEqual(d["nutrition"]["kcal"], 176)
        self.assertEqual(d["nutrition"]["sugar_g"], 36.3)
        self.assertEqual(d["allergens"], [])
        self.assertEqual(d["regions"], ["北海道"])
        self.assertEqual(parsers.parse_launch_date_iso(d["launch_text"]), "2026-09-22")

    def test_blacklisted_detail_returns_none(self):
        html = """
        <div class="detail_wrap -item-code-hide-099999" style="display: none">
          <p class="blacklist_error">現在この商品の情報を表示できません。</p>
        </div>
        <div class="detail_wrap -item-code-099999"></div>
        """
        self.assertIsNone(parsers.parse_seven_detail(html))


class TestFamilyMart(unittest.TestCase):
    def test_list_parses_items_and_splits_region_prefix(self):
        items = parsers.parse_familymart_list(read("familymart/newgoods.html"))
        self.assertGreater(len(items), 10)
        first = items[0]
        self.assertEqual(first["product_id"], "0415613")
        self.assertNotIn("【", first["name"])
        self.assertEqual(first["region_prefix"], "北海道・東海")

    def test_detail_without_nutrition_is_still_ok(self):
        d = parsers.parse_familymart_detail(read("familymart/item_0415613.html"))
        self.assertIsNotNone(d)
        self.assertIn("海老天", d["description"])
        self.assertIsNone(d["nutrition"])
        self.assertIsNone(d["allergens"])
        self.assertIn("北海道", d["regions"])
        self.assertNotIn("東北", d["regions"])
        self.assertEqual(parsers.parse_launch_date_iso(d["launch_text"]), "2026-09-22")

    def test_detail_with_nutrition_and_allergens(self):
        d = parsers.parse_familymart_detail(read("familymart/item_0920018.html"))
        self.assertIsNotNone(d)
        self.assertIsNotNone(d["nutrition"])
        self.assertEqual(d["nutrition"]["kcal"], 327.0)
        self.assertIn("小麦", d["allergens"])

    def test_detail_price_incl_tax_not_lost_to_nested_span(self):
        # 実物HTMLでは「（税込」「）」がそれぞれ入れ子の<span>で囲まれており、
        # 外側spanの閉じまで取らないと税込価格が欠落する(全167件nullのバグ)
        d = parsers.parse_familymart_detail(read("familymart/item_0920018.html"))
        self.assertIsNotNone(d)
        excl, incl = parsers.parse_price_pair(d["price_text"])
        self.assertEqual(excl, 406)
        self.assertEqual(incl, 438)

    def test_list_week_start_extracted_from_heading(self):
        w = parsers.parse_familymart_list_week_start(
            read("familymart/newgoods.html"), datetime.date(2026, 9, 25)
        )
        self.assertEqual(w, "2026-09-22")

    def test_list_week_start_handles_year_boundary(self):
        html = "今週の新商品&nbsp;(12/29～01/04)"
        w = parsers.parse_familymart_list_week_start(html, datetime.date(2026, 1, 2))
        self.assertEqual(w, "2025-12-29")


class TestLawson(unittest.TestCase):
    def test_meta_refresh_extracted(self):
        url = parsers.parse_meta_refresh(read("lawson/new_redirect.html"))
        self.assertEqual(url, "/recommend/new/list/1532630_5162.html")

    def test_nav_urls_found(self):
        urls = parsers.parse_lawson_nav_urls(read("lawson/list_1532630_5162.html"))
        self.assertGreater(len(urls), 3)
        self.assertTrue(all(u.startswith("/recommend/new/list/") for u in urls))

    def test_list_parses_items(self):
        items = parsers.parse_lawson_list(read("lawson/list_1532630_5162.html"))
        self.assertGreater(len(items), 3)
        first = items[0]
        self.assertEqual(first["product_id"], "1532627")
        self.assertEqual(parsers.parse_price_yen(first["price_text"]), 297)
        self.assertEqual(parsers.parse_launch_date_iso(first["launch_text"]), "2026-09-29")

    def test_detail_parses_nutrition_and_allergens(self):
        d = parsers.parse_lawson_detail(read("lawson/item_1532627.html"))
        self.assertIsNotNone(d)
        self.assertEqual(d["nutrition"]["kcal"], 382.0)
        self.assertEqual(d["nutrition"]["sugar_g"], 77.4)
        self.assertIn("小麦", d["allergens"])
        self.assertIn("乳成分", d["allergens"])
        self.assertNotIn("卵", d["allergens"])


if __name__ == "__main__":
    unittest.main()
