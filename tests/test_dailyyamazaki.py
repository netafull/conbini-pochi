#!/usr/bin/env python3
"""デイリーヤマザキ(/new/ のモーダル)のパーサ・クロール・ページ生成のテスト。

合成HTMLのテストは公式ページのコピーを含まないので、fixturesが無い環境でも動く。
実HTML(tests/fixtures/dailyyamazaki/new.html)を使うテストだけ、無ければスキップする。
本番へは一切アクセスしない。"""

import datetime
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402
import generate_site as gs  # noqa: E402
import parsers  # noqa: E402

REAL = ROOT / "tests" / "fixtures" / "dailyyamazaki" / "new.html"

TODAY = datetime.date(2026, 10, 16)


def listing_link(pid: str) -> str:
    return f'<section class="c-products-list-sec"><a href="#product{pid}" class="js-product-modal"></a></section>'


def modal(pid, name, num, tax, text, kcal=None, allergens=None, other=None, cates=("パン",)):
    cate_html = "".join(f'<li><img src="x.png" alt="{c}"></li>' for c in cates)
    kcal_html = f'<p class="kcal">熱量：{kcal}kcal</p>' if kcal is not None else ""
    alg_html = ""
    if allergens is not None:
        lis = "".join(f'<li><img src="a.png" alt="{a}"></li>' for a in allergens)
        if other:
            lis += f'<li class="other">{other}</li>'
        alg_html = f'<section class="allergen"><h4>アレルゲン</h4><div class="allergen__list"><ul>{lis}</ul></div></section>'
    return f"""<div id="product{pid}" class="c-products-modal">
<div class="c-products-modal__inner">
<ul class="cate">{cate_html}</ul>
<h3 class="ttl" data-mh="ttl">{name}</h3>
<p class="price"><span class="num">{num} </span>円 <span class="tax">(税込{tax}円)</span></p>
<p class="txt01">{text}
</p>
{kcal_html}
{alg_html}
</div>
</div>"""


def sample_html() -> str:
    listing = (
        '<h3 class="c-title03">10月13日（火）発売の新商品</h3>' + listing_link("100001") + listing_link("100002")
        + '<h3 class="c-title03">10月度(10月15日(木)発売)のデイリーホット新商品</h3>' + listing_link("100003")
    )
    modals = (
        modal("100001", "合成あんぱん&amp;クリーム", 165, 178, "合成の説明文です。", kcal=214,
              allergens=["卵", "小麦"], other="※その他「大豆」「とり肉」を含む。")
        + modal("100002", "合成おにぎり", 130, 140, "栄養なしの商品です。")
        + modal("100003", "合成ホットパイ", 276, 298, "DailyHot商品。", kcal=326, allergens=[],
                cates=("DailyHot", "スイーツ"))
    )
    return f"<html><body>{listing}\n{modals}\n<footer>フッター</footer></body></html>"


class TestParseSynthetic(unittest.TestCase):
    def setUp(self):
        self.items = {i["product_id"]: i for i in parsers.parse_dailyyamazaki_list(sample_html(), TODAY)}

    def test_all_modals_found_with_wordpress_id(self):
        self.assertEqual(sorted(self.items), ["100001", "100002", "100003"])

    def test_name_unescaped_and_description(self):
        a = self.items["100001"]
        self.assertEqual(a["name"], "合成あんぱん&クリーム")
        self.assertEqual(a["description"], "合成の説明文です。")

    def test_price_pair_handles_space_inside_span(self):
        # 実物には <span class="num">276 </span> のように空白が入る商品がある
        excl, incl = parsers.parse_price_pair(self.items["100003"]["price_text"])
        self.assertEqual((excl, incl), (276, 298))
        self.assertEqual(parsers.parse_price_pair(self.items["100001"]["price_text"]), (165, 178))

    def test_launch_date_from_heading_with_year_filled(self):
        self.assertEqual(self.items["100001"]["launch_date"], "2026-10-13")
        self.assertEqual(self.items["100002"]["launch_date"], "2026-10-13")
        # 「10月度(10月15日(木)発売)…」型の見出しは、最初の「M月D日」が発売日
        self.assertEqual(self.items["100003"]["launch_date"], "2026-10-15")
        self.assertIn("10月13日", self.items["100001"]["launch_text"])

    def test_kcal_only_nutrition(self):
        n = self.items["100001"]["nutrition"]
        self.assertEqual(n["kcal"], 214.0)
        for key in ("protein_g", "fat_g", "carbs_g", "sugar_g", "fiber_g", "salt_g"):
            self.assertIsNone(n[key])
        self.assertIsNone(self.items["100002"]["nutrition"])

    def test_allergens_from_icons_and_other_text(self):
        self.assertEqual(self.items["100001"]["allergens"], ["卵", "小麦", "大豆", "とり肉"])
        # アレルゲン欄そのものが無い商品は「不明」(None)。空配列(=含まない)とは区別する
        self.assertIsNone(self.items["100002"]["allergens"])
        self.assertEqual(self.items["100003"]["allergens"], [])

    def test_category_skips_dailyhot_badge(self):
        self.assertEqual(self.items["100001"]["category"], "パン")
        self.assertEqual(self.items["100003"]["category"], "スイーツ")

    def test_year_is_resolved_across_new_year(self):
        html = '<h3 class="c-title03">1月5日（月）発売の新商品</h3>' + listing_link("1") + modal("1", "a", 100, 108, "x")
        got = parsers.parse_dailyyamazaki_list(html, datetime.date(2026, 12, 28))
        self.assertEqual(got[0]["launch_date"], "2027-01-05")
        got = parsers.parse_dailyyamazaki_list(html, datetime.date(2027, 1, 10))
        self.assertEqual(got[0]["launch_date"], "2027-01-05")

    def test_modal_without_listing_heading_has_no_date(self):
        got = parsers.parse_dailyyamazaki_list(modal("7", "孤立", 100, 108, "x"), TODAY)
        self.assertIsNone(got[0]["launch_date"])

    def test_no_modal_returns_empty(self):
        self.assertEqual(parsers.parse_dailyyamazaki_list("<html></html>", TODAY), [])


class TestCrawlSynthetic(unittest.TestCase):
    def run_crawl(self, html: str, tmp: str) -> dict:
        orig_products, orig_get = crawl.PRODUCTS, crawl.get
        crawl.PRODUCTS = Path(tmp)
        requested: list[str] = []

        def fake_get(chain: str, url: str) -> str:
            requested.append(url)
            return html

        crawl.get = fake_get
        try:
            pending: dict = {}
            stats: dict = {}
            crawl.crawl_dailyyamazaki(pending, stats)
        finally:
            crawl.PRODUCTS, crawl.get = orig_products, orig_get
        self.assertEqual(requested, ["https://www.daily-yamazaki.jp/new/"])  # リクエストは一覧1回だけ
        return {"pending": pending, "stats": stats}

    def test_saves_products_and_never_deletes_or_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_crawl(sample_html(), tmp)
            self.assertEqual(res["stats"]["dailyyamazaki"]["new_products"], 3)
            files = sorted((Path(tmp) / "dailyyamazaki").glob("*.json"))
            self.assertEqual([f.stem for f in files], ["100001", "100002", "100003"])
            saved = crawl.json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["chain"], "dailyyamazaki")
            self.assertEqual(saved["price_incl_tax"], 178)
            self.assertEqual(saved["official_url"], "https://www.daily-yamazaki.jp/new/")
            self.assertIsNone(saved["region_text"])

            # 一覧から2件消え、残った1件の価格が変わっても、保存済みは消えず上書きもされない
            only_first = '<h3 class="c-title03">10月13日（火）発売の新商品</h3>' + listing_link("100001") + modal(
                "100001", "合成あんぱん", 999, 1000, "改変")
            res = self.run_crawl(only_first, tmp)
            self.assertEqual(res["stats"]["dailyyamazaki"]["new_products"], 0)
            self.assertEqual(len(list((Path(tmp) / "dailyyamazaki").glob("*.json"))), 3)
            again = crawl.json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(again["price_incl_tax"], 178)

    def test_empty_list_goes_to_pending_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_crawl(sample_html(), tmp)
            res = self.run_crawl("<html>作り変えられたページ</html>", tmp)
            self.assertEqual(res["pending"]["dailyyamazaki"]["list"]["reason"], "empty_list")
            self.assertEqual(res["stats"]["dailyyamazaki"]["failed"], 1)
            self.assertEqual(len(list((Path(tmp) / "dailyyamazaki").glob("*.json"))), 3)


class TestSiteGeneration(unittest.TestCase):
    def item(self, **kw):
        base = {
            "chain": "dailyyamazaki", "product_id": "100001", "name": "合成あんぱん",
            "price_incl_tax": 178, "price_excl_tax": 165, "launch_date": "2026-10-13",
            "launch_text": "10月13日（火）発売の新商品", "description": "合成の説明文です。",
            "nutrition": {"kcal": 214.0, "protein_g": None, "fat_g": None, "carbs_g": None,
                          "sugar_g": None, "fiber_g": None, "salt_g": None, "raw": "熱量：214kcal"},
            "allergens": ["卵"], "official_url": "https://www.daily-yamazaki.jp/new/",
            "fetched_at": "2026-10-16T07:00:00+09:00", "first_seen_at": "2026-10-16T07:00:00+09:00",
        }
        base.update(kw)
        return base

    def test_chain_is_configured(self):
        self.assertIn("dailyyamazaki", gs.CHAIN_NAME)
        self.assertEqual(gs.CHAIN_NAME["dailyyamazaki"], "デイリーヤマザキ")
        self.assertIn("--dailyyamazaki:", gs.CSS)
        self.assertIn('.item[data-chain="dailyyamazaki"] .badge', gs.CSS)

    def test_kcal_only_item_page(self):
        h = gs.render_item_page(self.item())
        self.assertIn("デイリーヤマザキ「合成あんぱん」のカロリー・価格・栄養成分", h)
        self.assertIn("<tr><th>熱量</th><td>214kcal</td></tr>", h)
        self.assertNotIn("たんぱく質", h)
        self.assertIn('<blockquote cite="https://www.daily-yamazaki.jp/new/">合成の説明文です。</blockquote>', h)
        self.assertIn("出典：デイリーヤマザキ公式サイト（2026-10-16時点）", h)
        self.assertEqual(h.count('<div class="ad-slot">'), 1)

    def test_item_page_without_nutrition_uses_fallback_title(self):
        h = gs.render_item_page(self.item(nutrition=None, allergens=None))
        self.assertIn("デイリーヤマザキ「合成あんぱん」の価格・発売日", h)
        self.assertNotIn("栄養成分</h3>", h)

    def test_filters_and_card_use_chain(self):
        top = gs.render_top([self.item()])
        self.assertIn('<option value="dailyyamazaki">デイリーヤマザキ</option>', top)
        card = gs.render_card(self.item())
        self.assertIn('href="/items/dailyyamazaki-100001.html"', card)
        self.assertIn('data-chain="dailyyamazaki"', card)

    def test_chain_page_without_netaful_tag_does_not_break(self):
        h = gs.render_grid_page("デイリーヤマザキ", "d", [self.item()], "https://x/", review_chain="dailyyamazaki")
        self.assertIn("合成あんぱん", h)
        h = gs.render_item_page(self.item())
        self.assertIn("合成あんぱん", h)


@unittest.skipUnless(REAL.is_file(), "公式ページのスナップショット(tests/fixtures/dailyyamazaki/new.html)が無い。リポジトリには入れていない")
class TestRealSnapshot(unittest.TestCase):
    def test_parses_whole_page(self):
        items = parsers.parse_dailyyamazaki_list(REAL.read_text(encoding="utf-8"), TODAY)
        self.assertGreater(len(items), 30)
        self.assertEqual(len({i["product_id"] for i in items}), len(items))
        self.assertTrue(all(i["name"] and i["description"] for i in items))
        self.assertTrue(all(parsers.parse_price_pair(i["price_text"])[1] for i in items))
        self.assertTrue(all(i["launch_date"] for i in items))
        self.assertTrue(any(i["nutrition"] for i in items))
        self.assertTrue(any(i["nutrition"] is None for i in items))


if __name__ == "__main__":
    unittest.main()
