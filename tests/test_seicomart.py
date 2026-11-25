#!/usr/bin/env python3
"""セイコーマート(/instore/new.html 週次一覧)のパーサ・クロール・ページ生成のテスト。

合成HTMLのテストは公式ページのコピーを含まないので、fixturesが無い環境でも動く。
実HTML(tests/fixtures/seicomart/new.html)を使うテストだけ、無ければスキップする。
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

REAL = ROOT / "tests" / "fixtures" / "seicomart" / "new.html"

TODAY = datetime.date(2026, 11, 25)


def price_table(rows) -> str:
    trs = "".join(
        f'<tr><td>{region}</td>'
        f'<td><span class="">\n{price}\n</span><br>'
        f'<span class="small_caption_tax">\n{tax}\n</span></td>'
        f'<td><span class="">\n{launch}\n</span></td></tr>'
        for region, price, tax, launch in rows
    )
    return ('<table class="price_table"><tr><th>地域</th><th>価格</th><th>発売日</th></tr>'
            + trs + "</table>")


def large_block(stem, name, desc, rows) -> str:
    return f"""<div class="snd_large_ba2">
<div class="new_img_text2"><img src="../images/instore/new/{stem}.jpg" alt="{name}" width="340" /></div>
<div class="new_text_area2">
<p class="new_comment"><span class="new_type">新発売</span></p>
<p class="new_comment1"><img src="../images/instore/rb/rb_07.png" width="150" height="34" alt="" /></p>
<h5>
{name}
</h5>
<p>
{desc}
</p>
{price_table(rows)}
</div>
</div>"""


def grid_block(stem, name, desc, rows) -> str:
    return f"""<div class="grid_new" id="new9">
<img src="../images/instore/new/{stem}.jpg" alt="{name}" width="340" />
<p style="padding-right: 0;"><span class="new_type2">復活</span></p>
<p class="new_comment1"><img src="../images/instore/rb/rb_11.png" width="150" height="34" alt="" /></p>
<h5>
{name}
</h5>
<p style="padding-right: 0;">
{desc}<br>
{price_table(rows)}
</p>
</div>"""


ROWS_SAME = [
    ("北海道", "1個 350円", "(8%税込378.00円）", "11月23日(月)"),
    ("茨城県<br>\n埼玉県", "1個 350円", "(8%税込378.00円）", "11月24日(火)"),
]
ROWS_DIFF = [
    ("北海道", "220円", "(8%税込237.60円）", "11月23日(月)"),
    ("茨城県<br>\n埼玉県", "230円", "(8%税込248.40円）", "11月23日(月)"),
]


def sample_html() -> str:
    large = large_block("20261123_01", "合成モンブラン", "合成の説明文です。", ROWS_SAME)
    grid = grid_block("20261123_04", "合成キャンディ", "グリッド形式の説明文。", ROWS_DIFF)
    # コメントアウトされた過去週のブロック(実物にも複数残っている)。拾ってはいけない
    old = "<!--\n" + large_block("20261005_01", "過去の商品", "コメント内の説明。", ROWS_SAME) + "\n-->"
    return f"<html><body><h2>11月23日週 おすすめの新商品</h2>{large}{old}{grid}</body></html>"


class TestParseSynthetic(unittest.TestCase):
    def setUp(self):
        self.items = {i["product_id"]: i for i in parsers.parse_seicomart_list(sample_html(), TODAY)}

    def test_both_block_formats_found_with_image_stem_id(self):
        self.assertEqual(sorted(self.items), ["20261123_01", "20261123_04"])

    def test_commented_out_old_products_are_ignored(self):
        # 【最重要】HTMLコメント内の過去週ブロックを新商品として拾わないこと
        self.assertNotIn("20261005_01", self.items)
        self.assertNotIn("過去の商品", [i["name"] for i in self.items.values()])

    def test_name_and_description(self):
        a = self.items["20261123_01"]
        self.assertEqual(a["name"], "合成モンブラン")
        self.assertEqual(a["description"], "合成の説明文です。")
        # グリッド形式は <p> が価格表を内包したまま閉じるが、説明文だけが取れる
        self.assertEqual(self.items["20261123_04"]["description"], "グリッド形式の説明文。")
        self.assertIsNone(a["category"])

    def test_price_prefix_dropped_and_pair_parsed(self):
        self.assertEqual(parsers.seicomart_price_text("1個 350円 (8%税込378.00円）"),
                         "350円 (8%税込378.00円）")
        self.assertEqual(parsers.seicomart_price_text("220円 (8%税込237.60円）"),
                         "220円 (8%税込237.60円）")
        a = self.items["20261123_01"]
        self.assertEqual(parsers.parse_price_pair(a["price_text"]), (350, 378))

    def test_representative_is_hokkaido_and_variants_keep_all_rows(self):
        a = self.items["20261123_01"]
        # 代表値(北海道行): 発売日は北海道の11月23日。茨城・埼玉は11月24日で別
        self.assertEqual(a["launch_date"], "2026-11-23")
        self.assertEqual(a["launch_text"], "11月23日(月)")
        self.assertEqual(sorted(a["variants"]), ["北海道", "茨城県・埼玉県"])
        kanto = a["variants"]["茨城県・埼玉県"]
        self.assertEqual(kanto["launch_date"], "2026-11-24")
        self.assertEqual(parsers.parse_price_pair(kanto["price_text"]), (350, 378))

    def test_region_cell_br_becomes_list_and_region_text_integrates_rows(self):
        a = self.items["20261123_01"]
        self.assertEqual(a["variants"]["茨城県・埼玉県"]["regions"], ["茨城県", "埼玉県"])
        self.assertEqual(a["regions"], ["北海道", "茨城県", "埼玉県"])
        self.assertEqual(a["region_text"], "北海道、茨城県・埼玉県")

    def test_regional_price_difference_is_kept(self):
        a = self.items["20261123_04"]
        self.assertEqual(parsers.parse_price_pair(a["price_text"]), (220, 237.6))
        kanto = a["variants"]["茨城県・埼玉県"]
        self.assertEqual(parsers.parse_price_pair(kanto["price_text"]), (230, 248.4))

    def test_year_is_resolved_across_new_year(self):
        rows = [("北海道", "100円", "(8%税込108.00円）", "1月5日(月)")]
        html = large_block("20261228_01", "年跨ぎ商品", "説明。", rows)
        got = parsers.parse_seicomart_list(html, datetime.date(2026, 12, 28))
        self.assertEqual(got[0]["launch_date"], "2027-01-05")
        got = parsers.parse_seicomart_list(html, datetime.date(2027, 1, 10))
        self.assertEqual(got[0]["launch_date"], "2027-01-05")

    def test_first_row_is_representative_without_hokkaido(self):
        rows = [("茨城県<br>埼玉県", "100円", "(8%税込108.00円）", "11月24日(火)")]
        got = parsers.parse_seicomart_list(large_block("20261123_09", "関東のみ", "説明。", rows), TODAY)
        self.assertEqual(got[0]["launch_date"], "2026-11-24")
        self.assertEqual(got[0]["regions"], ["茨城県", "埼玉県"])

    def test_fullwidth_name_is_kept_as_is(self):
        rows = [("北海道", "218円", "(8%税込235.44円）", "11月23日(月)")]
        got = parsers.parse_seicomart_list(
            grid_block("20261123_05", "ＢＩＧ！合成ロール", "説明。", rows), TODAY)
        self.assertEqual(got[0]["name"], "ＢＩＧ！合成ロール")

    def test_no_block_returns_empty(self):
        self.assertEqual(parsers.parse_seicomart_list("<html></html>", TODAY), [])
        self.assertEqual(parsers.parse_seicomart_list("", TODAY), [])


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
            crawl.crawl_seicomart(pending, stats)
        finally:
            crawl.PRODUCTS, crawl.get = orig_products, orig_get
        self.assertEqual(requested, ["https://www.seicomart.co.jp/instore/new.html"])  # リクエストは一覧1回だけ
        return {"pending": pending, "stats": stats}

    def test_saves_products_and_never_deletes_or_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_crawl(sample_html(), tmp)
            self.assertEqual(res["stats"]["seicomart"]["new_products"], 2)
            files = sorted((Path(tmp) / "seicomart").glob("*.json"))
            self.assertEqual([f.stem for f in files], ["20261123_01", "20261123_04"])
            saved = crawl.json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["chain"], "seicomart")
            self.assertEqual(saved["price_excl_tax"], 350)
            self.assertEqual(saved["price_incl_tax"], 378)
            self.assertEqual(saved["launch_date"], "2026-11-23")
            self.assertEqual(saved["official_url"], "https://www.seicomart.co.jp/instore/new.html")
            self.assertEqual(saved["source_list"], "new")
            self.assertIsNone(saved["category"])
            self.assertIsNone(saved["nutrition"])
            self.assertIsNone(saved["image"])
            self.assertEqual(saved["variants"]["茨城県・埼玉県"]["price_incl_tax"], 378)
            self.assertEqual(saved["variants"]["茨城県・埼玉県"]["launch_date"], "2026-11-24")
            self.assertEqual(saved["variants"]["北海道"]["url"],
                             "https://www.seicomart.co.jp/instore/new.html")

            # 次の週に一覧が入れ替わっても、保存済みは消えず上書きもされない
            next_week = grid_block("20261130_01", "翌週の商品", "説明。", ROWS_DIFF)
            res = self.run_crawl(next_week, tmp)
            self.assertEqual(res["stats"]["seicomart"]["new_products"], 1)
            self.assertEqual(len(list((Path(tmp) / "seicomart").glob("*.json"))), 3)
            again = crawl.json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(again["price_incl_tax"], 378)

    def test_empty_list_goes_to_pending_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_crawl(sample_html(), tmp)
            res = self.run_crawl("<html>作り変えられたページ</html>", tmp)
            self.assertEqual(res["pending"]["seicomart"]["list"]["reason"], "empty_list")
            self.assertEqual(res["stats"]["seicomart"]["failed"], 1)
            self.assertEqual(len(list((Path(tmp) / "seicomart").glob("*.json"))), 2)


class TestSiteGeneration(unittest.TestCase):
    def item(self, **kw):
        base = {
            "chain": "seicomart", "product_id": "20261123_04", "name": "合成キャンディ",
            "price_incl_tax": 237.6, "price_excl_tax": 220, "launch_date": "2026-11-23",
            "launch_text": "11月23日(月)", "description": "合成の説明文です。",
            "regions": ["北海道", "茨城県", "埼玉県"], "region_text": "北海道、茨城県・埼玉県",
            "nutrition": None, "allergens": None, "spec": None,
            "variants": {
                "北海道": {"region_text": "北海道", "regions": ["北海道"],
                          "price_excl_tax": 220, "price_incl_tax": 237.6,
                          "launch_date": "2026-11-23", "launch_text": "11月23日(月)",
                          "url": "https://www.seicomart.co.jp/instore/new.html"},
                "茨城県・埼玉県": {"region_text": "茨城県・埼玉県", "regions": ["茨城県", "埼玉県"],
                                   "price_excl_tax": 230, "price_incl_tax": 248.4,
                                   "launch_date": "2026-11-23", "launch_text": "11月23日(月)",
                                   "url": "https://www.seicomart.co.jp/instore/new.html"},
            },
            "official_url": "https://www.seicomart.co.jp/instore/new.html",
            "fetched_at": "2026-11-25T07:00:00+09:00", "first_seen_at": "2026-11-25T07:00:00+09:00",
        }
        base.update(kw)
        return base

    def test_chain_is_configured(self):
        self.assertIn("seicomart", gs.CHAIN_NAME)
        self.assertEqual(gs.CHAIN_NAME["seicomart"], "セイコーマート")
        self.assertIn("--seicomart:", gs.CSS)
        self.assertIn('.item[data-chain="seicomart"] .badge', gs.CSS)
        self.assertIn('.chain-group[data-chain="seicomart"] h3', gs.CSS)
        self.assertEqual(crawl.INTERVALS["seicomart"], 3)

    def test_item_page_shows_regional_prices(self):
        h = gs.render_item_page(self.item())
        self.assertIn("セイコーマート「合成キャンディ」の価格・発売日", h)
        self.assertIn("地域別の価格", h)
        self.assertIn("<th>茨城県・埼玉県</th>", h)
        self.assertIn("出典：セイコーマート公式サイト（2026年11月25日時点）", h)

    def test_filters_and_card_use_chain(self):
        top = gs.render_top([self.item()])
        self.assertIn('<option value="seicomart">セイコーマート</option>', top)
        card = gs.render_card(self.item())
        self.assertIn('href="/items/seicomart-20261123_04.html"', card)
        self.assertIn('data-chain="seicomart"', card)

    def test_chain_page_without_netaful_tag_does_not_break(self):
        h = gs.render_grid_page("セイコーマート", "d", [self.item()], "https://x/", review_chain="seicomart")
        self.assertIn("合成キャンディ", h)


@unittest.skipUnless(REAL.is_file(), "公式ページのスナップショット(tests/fixtures/seicomart/new.html)が無い。リポジトリには入れていない")
class TestRealSnapshot(unittest.TestCase):
    def test_parses_whole_page(self):
        items = parsers.parse_seicomart_list(REAL.read_text(encoding="utf-8"), TODAY)
        self.assertGreaterEqual(len(items), 4)
        self.assertEqual(len({i["product_id"] for i in items}), len(items))
        for i in items:
            self.assertTrue(i["name"] and i["description"])
            self.assertTrue(parsers.parse_price_pair(i["price_text"])[1])
            self.assertTrue(i["launch_date"])
            self.assertTrue(i["variants"])
            for v in i["variants"].values():
                self.assertTrue(parsers.parse_price_pair(v["price_text"])[1])
                self.assertTrue(v["launch_date"])


if __name__ == "__main__":
    unittest.main()
