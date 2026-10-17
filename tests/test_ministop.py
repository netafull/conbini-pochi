#!/usr/bin/env python3
"""ミニストップ(recommend.json + 静的な商品詳細ページ)のパーサ・クロール・ページ生成のテスト。

合成データのテストは公式ページのコピーを含まないので、fixturesが無い環境でも動く。
実データ(tests/fixtures/ministop/)を使うテストだけ、無ければスキップする。
本番へは一切アクセスしない。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402
import generate_site as gs  # noqa: E402
import parsers  # noqa: E402

REAL_DIR = ROOT / "tests" / "fixtures" / "ministop"
BASE = "https://www.ministop.co.jp"


def rec(pid, title, price="本体価格100円<br>(税込108円)", release="2026年10月13日", region="全国"):
    return {"price": price, "release": release, "image": "/syohin/assets/img/1.jpg", "new": True,
            "region": region, "title": title, "link": f"/syohin/products/detail{pid}.html"}


def detail_html(name, price="本体価格100円<br>(税込108円)", region="全国", release="2026年10月13日",
                desc="合成の説明文です。", category="パン"):
    return f"""<html><head><title>{name} | 商品情報 | ミニストップ</title></head><body>
<div class="topicNavBlock"><div class="topicNav"><ul>
<li><a href="/"><span>Home</span></a></li>
<li><a href="/syohin/"><span>商品情報</span></a></li>
<li><a href="/syohin/bread/"><span>{category}</span></a></li>
<li><span>{name}</span></li>
</ul></div></div>
<div class="headTitleBox"><div class="headTitleIn"><h1 class="title01"><span class="headTx1">{name}</span></h1></div></div>
<div class="syohinInfoColumn"><div class="textCol"><div class="detailBlock">
<dl><dt>価格</dt><dd><span class="price">{price}</span></dd></dl>
<dl><dt>発売地域</dt><dd>{region}</dd></dl>
<dl><dt>発売日</dt><dd>{release}</dd></dl>
<dl><dt>商品特徴</dt><dd class="leftLayout">{desc}<br></dd></dl>
</div></div></div>
<footer><dl><dt>WAON POINTカード</dt><dd>フッターの別の表</dd></dl></footer>
</body></html>"""


def sample_json() -> str:
    return json.dumps([
        rec("900001", "合成サンド", "本体価格168円<br>(税込181.44円)"),
        rec("900002", "合成&amp;パスタ", "本体価格598円<br>(税込645.84円)", "2026年10月6日",
            "東北、関東、近畿、四国、九州"),
        rec("900003", "合成丼", "本体価格398円<br>(税込429.84円)", "2026年10月6日",
            "全国※エリアにより発売日が異なります。"),
    ], ensure_ascii=False)


DETAILS = {
    "900001": detail_html("合成サンド", "本体価格168円<br>(税込181.44円)", category="パン"),
    "900002": detail_html("合成&amp;パスタ", "本体価格598円<br>(税込645.84円)", "東北、関東、近畿、四国、九州",
                          "2026年10月6日", "パスタの説明。", "お弁当"),
    "900003": detail_html("合成丼", "本体価格398円<br>(税込429.84円)", "全国※エリアにより発売日が異なります。",
                          "2026年10月6日", "丼の説明。", "お寿司"),
}


class TestParseSynthetic(unittest.TestCase):
    def test_recommend_entries(self):
        items = {i["product_id"]: i for i in parsers.parse_ministop_recommend(sample_json())}
        self.assertEqual(sorted(items), ["900001", "900002", "900003"])
        a = items["900001"]
        self.assertEqual(a["detail_url"], "/syohin/products/detail900001.html")
        self.assertEqual(a["launch_date"], "2026-10-13")
        self.assertEqual(a["launch_text"], "2026年10月13日")
        self.assertEqual(parsers.parse_price_pair(a["price_text"]), (168, 181.44))
        self.assertEqual(items["900002"]["launch_date"], "2026-10-06")
        self.assertEqual(items["900002"]["name"], "合成&パスタ")

    def test_recommend_invalid_or_unexpected(self):
        with self.assertRaises(ValueError):
            parsers.parse_ministop_recommend("<html>not json</html>")
        self.assertEqual(parsers.parse_ministop_recommend("[]"), [])
        self.assertEqual(parsers.parse_ministop_recommend('{"a": 1}'), [])
        # IDもタイトルも取れない要素は読み飛ばす
        self.assertEqual(parsers.parse_ministop_recommend('[{"title": "x"}, {"link": "/a.html"}, 5]'), [])

    def test_detail_fields(self):
        d = parsers.parse_ministop_detail(DETAILS["900001"])
        self.assertEqual(d["name"], "合成サンド")
        self.assertEqual(parsers.parse_price_pair(d["price_text"]), (168, 181.44))
        self.assertEqual(d["launch_date"], "2026-10-13")
        self.assertEqual(d["region_text"], "全国")
        self.assertEqual(d["description"], "合成の説明文です。")
        self.assertEqual(d["category"], "パン")
        # フッターの別の<dl>(WAON等)を拾わない
        self.assertEqual(parsers.parse_ministop_detail(DETAILS["900002"])["category"], "お弁当")

    def test_detail_without_table_returns_none(self):
        self.assertIsNone(parsers.parse_ministop_detail("<html><body>準備中</body></html>"))

    def test_regions(self):
        self.assertIsNone(parsers.ministop_regions("全国"))
        self.assertIsNone(parsers.ministop_regions("全国※エリアにより発売日が異なります。"))
        self.assertEqual(parsers.ministop_regions("東北、関東、九州"), ["東北", "関東", "九州"])
        self.assertIsNone(parsers.ministop_regions(None))

    def test_price_text_without_base_price_label(self):
        # 「本体価格」が無い表記もそのまま読める
        self.assertEqual(parsers.parse_price_pair(parsers.ministop_price_text("100円(税込108円)")), (100, 108))


class TestCrawlSynthetic(unittest.TestCase):
    def run_crawl(self, list_body: str, details: dict, tmp: str) -> dict:
        orig_products, orig_get = crawl.PRODUCTS, crawl.get
        crawl.PRODUCTS = Path(tmp)
        requested: list[str] = []

        def fake_get(chain: str, url: str) -> str:
            requested.append(url)
            if url.endswith("recommend.json"):
                return list_body
            pid = url.rsplit("detail", 1)[1].split(".")[0]
            if pid not in details:
                raise RuntimeError("404")
            return details[pid]

        crawl.get = fake_get
        try:
            pending: dict = {}
            stats: dict = {}
            crawl.crawl_ministop(pending, stats)
        finally:
            crawl.PRODUCTS, crawl.get = orig_products, orig_get
        return {"pending": pending, "stats": stats, "requested": requested}

    def files(self, tmp):
        return sorted((Path(tmp) / "ministop").glob("*.json"))

    def test_saves_products_and_never_deletes_or_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self.run_crawl(sample_json(), DETAILS, tmp)
            self.assertEqual(res["stats"]["ministop"]["new_products"], 3)
            self.assertEqual(res["stats"]["ministop"]["detail_requests"], 3)
            self.assertEqual(res["requested"][0], BASE + "/syohin/js/recommend.json")
            self.assertEqual([f.stem for f in self.files(tmp)], ["900001", "900002", "900003"])
            saved = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in self.files(tmp)}
            a = saved["900001"]
            self.assertEqual(a["chain"], "ministop")
            self.assertEqual((a["price_excl_tax"], a["price_incl_tax"]), (168, 181.44))
            self.assertEqual(a["launch_date"], "2026-10-13")
            self.assertEqual(a["category"], "パン")
            self.assertEqual(a["official_url"], BASE + "/syohin/products/detail900001.html")
            self.assertEqual(a["source_list"], "recommend")
            self.assertEqual(a["description"], "合成の説明文です。")
            self.assertIsNone(a["nutrition"])
            self.assertIsNone(a["allergens"])
            self.assertIsNone(a["image"])
            self.assertIsNone(a["regions"])
            self.assertEqual(a["region_text"], "全国")
            self.assertEqual(saved["900002"]["regions"], ["東北", "関東", "近畿", "四国", "九州"])
            self.assertIn("エリアにより発売日が異なります", saved["900003"]["region_text"])

            # 一覧から2件消え、残った1件の価格が変わっても、保存済みは消えず上書きもされない。
            # 保存済みの商品の詳細ページは取り直さない
            changed = json.dumps([rec("900001", "改変", "本体価格999円<br>(税込1000円)")], ensure_ascii=False)
            res = self.run_crawl(changed, DETAILS, tmp)
            self.assertEqual(res["stats"]["ministop"]["new_products"], 0)
            self.assertEqual(res["requested"], [BASE + "/syohin/js/recommend.json"])
            self.assertEqual(len(self.files(tmp)), 3)
            again = json.loads(self.files(tmp)[0].read_text(encoding="utf-8"))
            self.assertEqual(again["price_incl_tax"], 181.44)
            self.assertEqual(again["name"], "合成サンド")

    def test_empty_list_goes_to_pending_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_crawl(sample_json(), DETAILS, tmp)
            res = self.run_crawl("[]", DETAILS, tmp)
            self.assertEqual(res["pending"]["ministop"]["list"]["reason"], "empty_list")
            self.assertEqual(res["stats"]["ministop"]["failed"], 1)
            res = self.run_crawl("<html>作り変え</html>", DETAILS, tmp)
            self.assertEqual(res["pending"]["ministop"]["list"]["reason"], "invalid_json")
            self.assertEqual(len(self.files(tmp)), 3)

    def test_detail_failure_is_pending_and_other_products_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            details = {"900001": DETAILS["900001"], "900003": "<html>変なページ</html>"}  # 900002は取得失敗
            res = self.run_crawl(sample_json(), details, tmp)
            self.assertEqual([f.stem for f in self.files(tmp)], ["900001"])
            self.assertEqual(res["stats"]["ministop"]["failed"], 2)
            self.assertIn("fetch_error", res["pending"]["ministop"]["900002"]["reason"])
            self.assertEqual(res["pending"]["ministop"]["900003"]["reason"], "empty_detail")


class TestSiteGeneration(unittest.TestCase):
    def item(self, **kw):
        base = {
            "chain": "ministop", "product_id": "900001", "name": "合成サンド", "category": "パン",
            "price_incl_tax": 181.44, "price_excl_tax": 168, "launch_date": "2026-10-13",
            "launch_text": "2026年10月13日", "description": "合成の説明文です。",
            "regions": ["東北", "関東"], "region_text": "東北、関東",
            "nutrition": None, "allergens": None,
            "official_url": BASE + "/syohin/products/detail900001.html",
            "fetched_at": "2026-10-17T07:00:00+09:00", "first_seen_at": "2026-10-17T07:00:00+09:00",
        }
        base.update(kw)
        return base

    def test_chain_is_configured(self):
        self.assertEqual(gs.CHAIN_NAME["ministop"], "ミニストップ")
        self.assertIn("--ministop:", gs.CSS)
        self.assertIn('.item[data-chain="ministop"] .badge', gs.CSS)
        self.assertIn('.chain-group[data-chain="ministop"] h3', gs.CSS)
        # 他社と色が重ならない
        import re
        found = re.findall(r"--(\w+): (#[0-9a-f]{6});", gs.CSS.split("}")[0])
        slugs = {s: c for s, c in found if s in gs.CHAIN_NAME}
        self.assertEqual(len(set(slugs.values())), len(slugs))
        self.assertIn("ministop", slugs)

    def test_item_page_without_nutrition_nor_allergens(self):
        h = gs.render_item_page(self.item())
        self.assertIn("ミニストップ「合成サンド」の価格・発売日", h)
        self.assertNotIn("栄養成分</h3>", h)
        self.assertIn(f'<blockquote cite="{BASE}/syohin/products/detail900001.html">合成の説明文です。</blockquote>', h)
        self.assertIn("出典：ミニストップ公式サイト（2026-10-17時点）", h)
        self.assertIn("販売地域：</strong>東北・関東", h)

    def test_zenkoku_region_text_is_shown(self):
        h = gs.render_item_page(self.item(regions=None, region_text="全国※エリアにより発売日が異なります。"))
        self.assertIn("販売地域：</strong>全国※エリアにより発売日が異なります。", h)

    def test_filters_and_card_use_chain(self):
        top = gs.render_top([self.item()])
        self.assertIn('<option value="ministop">ミニストップ</option>', top)
        card = gs.render_card(self.item())
        self.assertIn('href="/items/ministop-900001.html"', card)
        self.assertIn('data-chain="ministop"', card)

    def test_chain_page_without_netaful_cache_does_not_break(self):
        h = gs.render_grid_page("ミニストップ", "d", [self.item()], "https://x/", review_chain="ministop")
        self.assertIn("合成サンド", h)

    def test_config_and_tags_list_five_chains(self):
        import fetch_netaful_reviews as fr
        self.assertEqual([c["slug"] for c in gs.CHAINS],
                         ["seven", "familymart", "lawson", "dailyyamazaki", "ministop"])
        self.assertEqual(fr.TAGS["ministop"], 1064)
        self.assertEqual(crawl.INTERVALS["ministop"], 3)
        self.assertIn("5社", gs.CONFIG["about"][0])
        self.assertIn("ミニストップ", gs.CONFIG["site_tagline"])


@unittest.skipUnless((REAL_DIR / "recommend.json").is_file(),
                     "公式データのスナップショット(tests/fixtures/ministop/)が無い。リポジトリには入れていない")
class TestRealSnapshot(unittest.TestCase):
    def test_parses_list_and_all_details(self):
        entries = parsers.parse_ministop_recommend((REAL_DIR / "recommend.json").read_text(encoding="utf-8"))
        self.assertGreater(len(entries), 10)
        self.assertEqual(len({e["product_id"] for e in entries}), len(entries))
        self.assertTrue(all(e["launch_date"] for e in entries))
        checked = 0
        for e in entries:
            f = REAL_DIR / f"detail{e['product_id']}.html"
            if not f.is_file():
                continue
            d = parsers.parse_ministop_detail(f.read_text(encoding="utf-8"))
            self.assertIsNotNone(d, e["product_id"])
            self.assertTrue(d["description"] and d["category"] and d["region_text"])
            self.assertEqual(parsers.parse_price_pair(d["price_text"]),
                             parsers.parse_price_pair(e["price_text"]))
            self.assertEqual(d["launch_date"], e["launch_date"])
            checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
