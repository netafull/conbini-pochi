#!/usr/bin/env python3
"""generate_site.py のうち、ネタフル記事のキーワード照合・関連記事選定・
カード描画ロジックのユニットテスト。本番へは一切アクセスしない。"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_site as gs  # noqa: E402

KEYWORDS = [
    {"label": "から揚げ", "words": ["から揚げ", "唐揚げ", "からあげ"]},
    {"label": "パン", "words": ["パン"], "exclude": ["パンプキン"]},
    {"label": "サンド", "words": ["サンド"]},
    {"label": "たまご", "words": ["卵", "たまご"]},
]


def art(id_, title, date, chain, thumb=None):
    return {"id": id_, "title": title, "date": date, "chain": chain, "link": f"https://example/{id_}",
            "thumb": thumb}


class TestMatchKeywordForName(unittest.TestCase):
    def test_matches_simple_word(self):
        kw = gs.match_keyword_for_name("鶏からあげ弁当", KEYWORDS)
        self.assertIsNotNone(kw)
        self.assertEqual(kw["label"], "から揚げ")

    def test_no_match_returns_none(self):
        self.assertIsNone(gs.match_keyword_for_name("シーチキンマヨネーズおにぎり", KEYWORDS))

    def test_prefers_longer_more_specific_word(self):
        # 「たまごサンド」は「たまご」(3文字)・「サンド」(3文字)どちらも含むが、
        # 「コロッケサンド」のように長い語があればそちらを優先することを別途確認する
        kw = gs.match_keyword_for_name(
            "ハムたまごサンド",
            [
                {"label": "たまご", "words": ["たまご"]},
                {"label": "ハムたまごサンド専用", "words": ["ハムたまごサンド"]},
            ],
        )
        self.assertEqual(kw["label"], "ハムたまごサンド専用")

    def test_exclude_word_blocks_match(self):
        # 「パンプキンもこ」は「パン」を含むが、除外語「パンプキン」により
        # 「パン」キーワードにはマッチしない
        kw = gs.match_keyword_for_name("パンプキンもこ", KEYWORDS)
        self.assertIsNone(kw)

    def test_normal_pan_still_matches(self):
        kw = gs.match_keyword_for_name("メロンパン", KEYWORDS)
        self.assertIsNotNone(kw)
        self.assertEqual(kw["label"], "パン")


class TestArticlesForKeyword(unittest.TestCase):
    def test_finds_articles_by_word(self):
        articles = [
            art(1, "【セブン】「からあげ棒」食べてみた", "2026-01-01", "seven"),
            art(2, "【ローソン】「たまごサンド」を実食", "2026-01-02", "lawson"),
        ]
        kw = {"label": "から揚げ", "words": ["から揚げ", "唐揚げ", "からあげ"]}
        result = gs.articles_for_keyword(kw, articles)
        self.assertEqual([a["id"] for a in result], [1])

    def test_exclude_word_filters_article_title(self):
        articles = [
            art(1, "【セブン】「パンプキンもこ」ハロウィンスイーツ", "2026-01-01", "seven"),
            art(2, "【ファミマ】「メロンパン」食べてみた", "2026-01-02", "familymart"),
        ]
        kw = {"label": "パン", "words": ["パン"], "exclude": ["パンプキン"]}
        result = gs.articles_for_keyword(kw, articles)
        self.assertEqual([a["id"] for a in result], [2])


class TestSelectRelatedArticles(unittest.TestCase):
    def test_prefers_same_chain_then_newest(self):
        item = {"name": "鶏からあげ弁当", "chain": "seven", "product_id": "1"}
        articles = {
            1: art(1, "【ファミマ】「からあげ」古い記事", "2026-01-01", "familymart"),
            2: art(2, "【セブン】「からあげ」新しい記事", "2026-03-01", "seven"),
            3: art(3, "【セブン】「からあげ」もっと古い記事", "2026-01-15", "seven"),
        }
        gs.REVIEWS["matches"] = {}
        label, results = gs.select_related_articles(item, articles, KEYWORDS)
        self.assertEqual(label, "から揚げ")
        # 同じコンビニ(seven)が先、その中では新しい順
        self.assertEqual([a["id"] for a in results], [2, 3, 1])

    def test_limits_to_four(self):
        item = {"name": "からあげ弁当", "chain": "seven", "product_id": "1"}
        articles = {
            i: art(i, f"「からあげ」記事{i}", f"2026-01-{i:02d}", "seven") for i in range(1, 8)
        }
        gs.REVIEWS["matches"] = {}
        label, results = gs.select_related_articles(item, articles, KEYWORDS, limit=4)
        self.assertEqual(len(results), 4)

    def test_excludes_already_linked_review(self):
        item = {"name": "からあげ弁当", "chain": "seven", "product_id": "1"}
        linked_article = art(1, "「からあげ」直接ひも付いた記事", "2026-01-01", "seven")
        other = art(2, "「からあげ」その他記事", "2026-01-02", "seven")
        articles = {1: linked_article, 2: other}
        gs.REVIEWS["matches"] = {"seven-1": [1]}
        gs.ARTICLES_BY_ID[1] = linked_article
        try:
            label, results = gs.select_related_articles(item, articles, KEYWORDS)
            self.assertEqual([a["id"] for a in results], [2])
        finally:
            gs.REVIEWS["matches"] = {}
            gs.ARTICLES_BY_ID.pop(1, None)

    def test_no_matching_keyword_returns_none(self):
        item = {"name": "シーチキンマヨネーズおにぎり", "chain": "seven", "product_id": "1"}
        gs.REVIEWS["matches"] = {}
        self.assertIsNone(gs.select_related_articles(item, {}, KEYWORDS))

    def test_no_articles_for_keyword_returns_none(self):
        item = {"name": "からあげ弁当", "chain": "seven", "product_id": "1"}
        gs.REVIEWS["matches"] = {}
        self.assertIsNone(gs.select_related_articles(item, {}, KEYWORDS))


class TestReviewCardRendering(unittest.TestCase):
    def test_card_without_thumb_has_no_img_tag(self):
        a = art(1, "サムネイルが無い記事", "2026-01-01", "seven", thumb=None)
        html_out = gs.render_review_card(a)
        self.assertNotIn("<img", html_out)
        self.assertIn("サムネイルが無い記事", html_out)

    def test_card_with_thumb_has_img_with_dimensions(self):
        a = art(1, "サムネイルがある記事", "2026-01-01", "seven",
                thumb={"url": "https://netaful.jp/x.jpg", "width": 485, "height": 364})
        html_out = gs.render_review_card(a)
        self.assertIn('<img src="https://netaful.jp/x.jpg"', html_out)
        self.assertIn('width="485"', html_out)
        self.assertIn('height="364"', html_out)
        self.assertIn('loading="lazy"', html_out)

    def test_render_review_cards_empty_list_returns_empty_string(self):
        self.assertEqual(gs.render_review_cards("タイトル", []), "")


MAX_ADS = 5


def make_item(chain, pid, with_nutrition=True, with_desc=True):
    it = {"chain": chain, "product_id": pid, "name": f"テスト商品{pid}",
          "price_incl_tax": 198, "release_date": "2026-10-06", "fetched_at": "2026-10-07T00:00:00+09:00",
          "official_url": "https://example.com/x", "allergens": ["卵"]}
    if with_desc:
        it["description"] = "公式の説明文です。"
    if with_nutrition:
        it["nutrition"] = {"kcal": 300}
    return it


class TestAdSlots(unittest.TestCase):
    def setUp(self):
        self._orig = dict(gs.CONFIG)
        self.addCleanup(lambda: (gs.CONFIG.clear(), gs.CONFIG.update(self._orig)))
        gs.CONFIG["adsense_client_id"] = "ca-pub-1"
        gs.CONFIG["adsense_ad_slot"] = "2"
        self.items = [make_item(c, f"{i}{c[:2]}") for c in ("seven", "familymart", "lawson") for i in range(3)]

    def pages(self):
        return {
            "grid": gs.render_grid_page("週", "d", self.items, "https://x/"),
            "top": gs.render_top(self.items),
            "item": gs.render_item_page(self.items[0]),
        }

    def test_empty_config_outputs_nothing(self):
        # どちらか片方でも空なら枠は出ない。両方空なら広告関連が一切出ない
        for key in ("adsense_client_id", "adsense_ad_slot"):
            saved = gs.CONFIG[key]
            gs.CONFIG[key] = ""
            for name, h in self.pages().items():
                self.assertNotIn('<div class="ad-slot">', h, name)
            gs.CONFIG[key] = saved
        gs.CONFIG["adsense_client_id"] = ""
        gs.CONFIG["adsense_ad_slot"] = ""
        for name, h in self.pages().items():
            self.assertNotIn("adsbygoogle", h.replace(gs.CSS, ""), name)
            self.assertNotIn("data-google-vignette", h, name)

    def test_head_script_present(self):
        for name, h in self.pages().items():
            head = h.split("</head>")[0]
            self.assertIn("adsbygoogle.js?client=ca-pub-1", head, name)
            self.assertIn("crossorigin", head, name)

    def test_no_ad_inside_sortable_grid_on_server_side(self):
        # グリッド内の枠はSORT_JSが差し込む。サーバー側のHTMLには残さない
        import re
        for name, h in self.pages().items():
            for m in re.finditer(r'<div class="grid" data-sortable>(.*?)\n</div>\n', h, re.S):
                self.assertNotIn("ad-slot", m.group(1), name)
                self.assertNotIn("adsbygoogle", m.group(1), name)

    def test_grid_pages_have_ad_js_template_and_constants(self):
        # (a) 広告用のJS・定数・雛形がHTMLに入る
        for name in ("grid", "top"):
            h = self.pages()[name]
            self.assertIn("function syncAds()", h, name)
            self.assertIn(f"var AD_EVERY = {gs.ADS_EVERY};", h, name)
            self.assertIn(f"var AD_MAX = {gs.ADS_MAX_PER_GRID};", h, name)
            self.assertEqual(h.count('<template id="ad-template">'), 1, name)
            self.assertNotIn("__ADS_", h, name)
            # 初回のsyncAds()はSORT_JSより後ろ(ページ末尾)にある
            self.assertGreater(h.rindex("syncAds();"), h.index("var AD_EVERY"), name)

    def test_server_side_slot_count(self):
        # ヘッダー下1枠 + 雛形(<template>)1つ。実際の差し込みはJSで、上限は
        # ADS_MAX_PER_GRID(ページ全体の合計)
        self.assertEqual(gs.ADS_MAX_PER_GRID, 3)
        self.assertEqual(gs.ADS_EVERY, 12)
        for name in ("grid", "top"):
            h = self.pages()[name]
            body = h.split("<body>")[1]
            tpl = body[body.index("<template"):body.index("</template>")]
            self.assertEqual(body.count('<ins class="adsbygoogle"'), 2, name)
            self.assertEqual(tpl.count('<ins class="adsbygoogle"'), 1, name)
            self.assertLessEqual(body.count('<div class="ad-slot">'), MAX_ADS, name)

    def test_item_page_has_one_ad_and_no_grid_js(self):
        h = self.pages()["item"]
        self.assertEqual(h.split("<body>")[1].count('<ins class="adsbygoogle"'), 1)
        self.assertNotIn("<template", h)
        self.assertNotIn("AD_EVERY", h)
        self.assertIn("function syncAds()", h)
        # 枠ごとのインラインpushはしない(syncAdsに一本化)
        self.assertEqual(h.count("adsbygoogle || []).push"), 1)

    def test_ad_js_only_when_configured(self):
        gs.CONFIG["adsense_client_id"] = ""
        gs.CONFIG["adsense_ad_slot"] = ""
        for name, h in self.pages().items():
            self.assertNotIn("function syncAds", h, name)
            self.assertNotIn("<template", h, name)
            self.assertNotIn('class="ad-slot"', h, name)

    def test_sort_js_script_is_valid_javascript(self):
        # nodeがあれば構文チェックする(無ければ飛ばす)
        import re, shutil, subprocess, tempfile, os
        node = shutil.which("node") or "/opt/homebrew/opt/node@22/bin/node"
        if not os.path.exists(node):
            self.skipTest("node がない")
        h = self.pages()["top"]
        for i, m in enumerate(re.finditer(r"<script>(.*?)</script>", h, re.S)):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(m.group(1))
            try:
                r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            finally:
                os.unlink(f.name)

    def test_item_page_ad_not_adjacent_to_quote(self):
        import re
        h = self.pages()["item"]
        self.assertEqual(h.count('<div class="ad-slot">'), 1)
        for m in re.finditer(r'<div class="ad-slot">', h):
            before = h[:m.start()].rstrip()
            self.assertFalse(before.endswith("</blockquote>") or before.endswith("削除されている、または内容が変更されている場合があります。</p>"))
        i_ad = h.rfind('<div class="ad-slot">')
        self.assertGreater(i_ad, h.index("</blockquote>"))
        self.assertLess(i_ad, h.index("ネタフル") if "ネタフルのレビュー" in h[i_ad:] else len(h))

    def test_item_without_extras_has_one_ad_at_the_bottom(self):
        it = make_item("seven", "9", with_nutrition=False)
        it["allergens"] = None
        h = gs.render_item_page(it)
        self.assertEqual(h.count('<div class="ad-slot">'), 1)
        # 上部には置かない(スマホで最初の画面が広告で埋まるため)。引用の直後でもない
        self.assertGreater(h.index('<div class="ad-slot">'), h.index("</blockquote>") + 300)

    def test_no_ads_on_empty_pages(self):
        self.assertNotIn('class="ad-slot"', gs.render_chains_top())
        self.assertNotIn('class="ad-slot"', gs.render_weeks_index(self.items))
        self.assertNotIn('class="ad-slot"', gs.render_grid_page("週", "d", [], "https://x/"))

    def test_links_opt_out_of_vignette(self):
        h = self.pages()["top"]
        self.assertNotIn("<a href", h)
        self.assertNotIn("<a class", h)

    def _run_ad_plan(self, cases):
        import json, os, shutil, subprocess
        node = shutil.which("node") or "/opt/homebrew/opt/node@22/bin/node"
        if not os.path.exists(node):
            self.skipTest("node がない")
        script = (
            gs.AD_PLAN_JS
            + "\nconsole.log(JSON.stringify(%s.map(function (c) { return computeAdPlan(c, %d, %d); })));"
            % (json.dumps(cases), gs.ADS_EVERY, gs.ADS_MAX_PER_GRID)
        )
        r = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_compute_ad_plan(self):
        cases = [[0], [11], [12], [13], [30], [42, 65, 34, 11, 10],
                 [13, 13, 13, 13, 13], [212], [], [13, 5, 20]]
        got = self._run_ad_plan(cases)
        self.assertEqual(got[0], [[]])
        self.assertEqual(got[1], [[]])
        self.assertEqual(got[2], [[]])          # ちょうどAD_EVERY件は置かない
        self.assertEqual(got[3], [[12]])        # 13件: 末尾の1件手前
        self.assertEqual(got[4], [[12, 24]])    # 30件: 36は範囲外
        self.assertEqual(got[5], [[12], [12], [12], [], []])  # 置ける3社に1枠ずつ
        # 13件以上が5社: 等間隔に3社(0,1,3番目)を選び1枠ずつ
        self.assertEqual(got[6], [[12], [12], [], [12], []])
        self.assertEqual(got[7], [[12, 24, 36]])  # 単一グリッドは3枠
        self.assertEqual(got[8], [])
        self.assertEqual(got[9], [[12], [], [12]])
        for plan, counts in zip(got, cases):
            self.assertLessEqual(sum(len(p) for p in plan), gs.ADS_MAX_PER_GRID)
            for p, n in zip(plan, counts):
                self.assertEqual(p, sorted(set(p)))
                for i in p:
                    self.assertTrue(0 < i < n)

    def test_place_ads_never_detaches_ad_slots(self):
        # 枠をDOMから外してよいのは「余った枠を捨てる」1か所だけ。並べ替えは.itemしか動かさない
        js = gs.SORT_JS
        self.assertEqual(js.count("removeChild("), 1)
        self.assertIn("slots.pop()", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("el.classList.contains('ad-slot')", js)
        self.assertNotIn("__AD_PLAN__", js)
        self.assertIn("function computeAdPlan", js)



class TestUpdatedLabel(unittest.TestCase):
    """トップだけに「最終更新: 2026年10月14日 07:37」(日本時間・電書/家電/林檎ポチと同じ書式)を出す。"""

    def test_label_format_is_jst(self):
        import datetime
        utc = datetime.datetime(2026, 10, 13, 22, 37, tzinfo=datetime.timezone.utc)  # 日本時間 10/14 07:37
        self.assertEqual(gs.updated_label(utc.astimezone(gs.JST)), "最終更新: 2026年10月14日 07:37")

    def test_top_has_label_but_other_pages_do_not(self):
        top = gs.render_top([make_item("seven", "1se")])
        self.assertIn("最終更新: ", top)
        shell = gs.page_shell("x", "d", "<p>b</p>", "https://example.invalid/")
        self.assertNotIn("最終更新", shell)  # 全ページに入れると毎日すべてのファイルが変わる


class TestCardLinksAreRootAbsolute(unittest.TestCase):
    """カードのリンクが /items/… になっている(階層のあるページ /chains/x/ などで404にならない)。"""

    def test_card_href_starts_with_slash(self):
        card = gs.render_card(make_item("seven", "1se"))
        self.assertIn('href="/items/', card)
        self.assertNotIn('href="items/', card)

    def test_grid_page_has_no_relative_item_links(self):
        html = gs.render_grid_page("週", "d", [make_item("seven", "1se")], "https://x/")
        self.assertNotIn('href="items/', html)


class TestVisibleBreadcrumbs(unittest.TestCase):
    """見えるパンくず（タイトルの下）。crumbs を渡したグリッドページだけに出す（2026-11-13追加）。"""

    TRAIL = [("トップ", "https://conbini.netaful.jp/"),
             ("セブン-イレブン", "https://conbini.netaful.jp/chains/seven/")]

    def test_grid_page_with_crumbs_shows_nav_under_title(self):
        html = gs.render_grid_page("セブン-イレブン", "d", [make_item("seven", "1se")],
                                   "https://conbini.netaful.jp/chains/seven/", crumbs=self.TRAIL)
        nav = ('<nav class="crumbs" aria-label="パンくずリスト"><a data-google-vignette="false" href="/">トップ</a>'
               ' &gt; <span aria-current="page">セブン-イレブン</span></nav>')
        self.assertIn(nav, html)
        self.assertLess(html.index('<h1 class="page-title">'), html.index(nav))

    def test_grid_page_without_crumbs_has_no_nav(self):
        html = gs.render_grid_page("週", "d", [make_item("seven", "1se")], "https://x/")
        self.assertNotIn('aria-label="パンくずリスト"', html)


class TestRegionAndDisambiguation(unittest.TestCase):
    """同名商品の補足(販売地域→発売月→発売日→価格の順)。同名でない商品は変えない。"""

    def it(self, pid, name="炭火焼き鳥丼", regions=None, launch="2026-09-29", price=699.84, **kw):
        d = {"chain": "seven", "product_id": pid, "name": name, "regions": regions,
             "region_text": "、".join(regions) if regions else None, "launch_date": launch,
             "price_incl_tax": price, "variants": {}, "first_seen_at": "2026-09-29T07:00:00+09:00",
             "fetched_at": "2026-09-29T07:00:00+09:00"}
        d.update(kw)
        return d

    def test_region_label_short_and_full(self):
        short, full = gs.region_label(self.it("1", regions=["北海道"]))
        self.assertEqual((short, full), ("北海道", "北海道"))
        short, full = gs.region_label(self.it("1", regions=["関東", "甲信越", "北陸", "東海"]))
        self.assertEqual(short, "関東ほか")
        self.assertEqual(full, "関東・甲信越・北陸・東海")

    def test_region_label_uses_variants_union(self):
        item = self.it("1", regions=["北海道"], variants={"a": {"regions": ["北海道", "東北"]}})
        self.assertEqual(gs.region_label(item)[1], "北海道・東北")

    def test_region_label_lawson_notes_only_for_known_phrases(self):
        only = self.it("1", region_text="※中部地域のローソンのみのお取り扱いとなります。")
        self.assertEqual(gs.region_label(only), ("中部のみ", "中部のみ"))
        exc = self.it("1", region_text="※中部地域のローソンではお取り扱いしておりません。")
        self.assertEqual(gs.region_label(exc), ("中部を除く", "中部を除く"))
        other = self.it("1", region_text="※熱量は参照値となります。")
        self.assertEqual(gs.region_label(other), ("", ""))

    def test_labels_use_region_when_it_differs(self):
        g = [self.it("1", regions=["北海道"]), self.it("2", regions=["近畿"])]
        self.assertEqual(gs.disambiguation_labels(g), {"1": "北海道", "2": "近畿"})

    def test_same_region_falls_back_to_launch_month_then_day(self):
        g = [self.it("1", regions=["近畿"], launch="2026-10-20"), self.it("2", regions=["近畿"], launch="2026-11-10")]
        self.assertEqual(gs.disambiguation_labels(g), {"1": "近畿・2026年10月発売", "2": "近畿・2026年11月発売"})
        g = [self.it("1", regions=["近畿"], launch="2026-10-06"), self.it("2", regions=["近畿"], launch="2026-10-20")]
        self.assertEqual(gs.disambiguation_labels(g)["2"], "近畿・2026年10月20日発売")

    def test_price_is_last_resort_and_indistinguishable_returns_none(self):
        g = [self.it("1", regions=["近畿"], price=300), self.it("2", regions=["近畿"], price=350)]
        self.assertEqual(gs.disambiguation_labels(g), {"1": "近畿・2026年9月29日発売・税込300円",
                                                      "2": "近畿・2026年9月29日発売・税込350円"})
        same = [self.it("1", regions=["千葉県"]), self.it("2", regions=["千葉県"])]
        self.assertIsNone(gs.disambiguation_labels(same))

    def test_name_key_ignores_width_and_spaces(self):
        self.assertEqual(gs.name_key("おむすび＆おかず　焼鮭"), gs.name_key("おむすび&おかず 焼鮭"))

    def test_only_same_name_items_get_label_in_title_and_h1(self):
        items = [self.it("1", regions=["北海道"]), self.it("2", regions=["近畿"]),
                 self.it("3", name="別の商品", regions=["北海道"])]
        ctx = gs.build_context(items)
        h = gs.render_item_page(items[0], ctx)
        self.assertIn("セブンイレブン「炭火焼き鳥丼」（北海道）の価格・発売日｜コンビニポチ", h)
        self.assertIn('<h1>炭火焼き鳥丼 <span class="sub">（北海道）</span></h1>', h)
        self.assertIn('href="/items/seven-2.html">炭火焼き鳥丼（近畿）</a>', h)
        self.assertIn('rel="canonical" href="https://conbini.netaful.jp/items/seven-1.html"', h)
        plain = gs.render_item_page(items[2], ctx)
        self.assertIn("セブンイレブン「別の商品」の価格・発売日｜コンビニポチ", plain)
        self.assertIn("<h1>別の商品</h1>", plain)
        self.assertNotIn("同じ名前の商品", plain)

    def test_other_chain_same_name_is_not_a_duplicate(self):
        a = self.it("1", regions=["北海道"])
        b = dict(self.it("2", regions=["近畿"]), chain="lawson")
        self.assertEqual(gs.build_context([a, b])["same_name"], {})


class TestRelatedItems(unittest.TestCase):
    def it(self, pid, date, category=None, chain="seven", name=None):
        return {"chain": chain, "product_id": pid, "name": name or f"商品{pid}", "category": category,
                "launch_date": date, "price_incl_tax": 100}

    def test_nearest_dates_excluding_self_other_chains_and_same_name(self):
        items = [self.it("0", "2026-10-10"), self.it("1", "2026-10-11"), self.it("2", "2026-10-09"),
                 self.it("3", "2026-10-20"), self.it("4", "2026-10-10", name="商品0"),
                 self.it("5", "2026-10-10", chain="lawson")]
        ctx = gs.build_context(items)
        kind, picked = gs.related_items(items[0], ctx, limit=2)
        self.assertEqual(kind, "chain")
        self.assertEqual({p["product_id"] for p in picked}, {"1", "2"})

    def test_same_category_comes_first_then_fills_from_chain(self):
        items = [self.it("0", "2026-10-10", "パン"), self.it("1", "2026-12-01", "パン"),
                 self.it("2", "2026-10-10", "お弁当")]
        ctx = gs.build_context(items)
        kind, picked = gs.related_items(items[0], ctx, limit=2)
        self.assertEqual(kind, "mixed")
        self.assertEqual([p["product_id"] for p in picked], ["1", "2"])

    def test_deterministic_and_stable_when_far_items_are_added(self):
        base = [self.it(str(i), f"2026-10-{i + 1:02d}") for i in range(20)]
        first = [p["product_id"] for p in gs.related_items(base[10], gs.build_context(base))[1]]
        again = [p["product_id"] for p in gs.related_items(base[10], gs.build_context(list(reversed(base))))[1]]
        self.assertEqual(first, again)
        extra = base + [self.it("99", "2027-03-01")]
        self.assertEqual(first, [p["product_id"] for p in gs.related_items(base[10], gs.build_context(extra))[1]])

    def test_links_are_plain_root_absolute_anchors(self):
        items = [self.it(str(i), "2026-10-10") for i in range(3)]
        h = gs.render_item_page(items[0], gs.build_context(items))
        self.assertIn('href="/items/seven-1.html">商品1</a>', h)
        self.assertIn("<h2>同じ時期のセブンイレブンの商品</h2>", h)


class TestSeoPages(unittest.TestCase):
    def test_chain_page_title_and_description(self):
        self.assertEqual(gs.chain_page_title("ローソン"), "ローソンの新商品一覧（発売日順）")
        items = [{"chain": "lawson", "product_id": str(i), "name": f"商品{i}", "launch_date": f"2026-10-{i + 1:02d}",
                  "nutrition": {"kcal": 100}} for i in range(5)]
        d = gs.chain_page_description("ローソン", items)
        self.assertEqual(d, "ローソンの新商品5件を発売日順に記録。最近の新商品は「商品4」・「商品3」・「商品2」など。"
                            "カロリー・価格・栄養成分を掲載。")
        self.assertLessEqual(len(d), 120)

    def test_chain_description_does_not_claim_calories_without_data(self):
        items = [{"chain": "ministop", "product_id": "1", "name": "A", "launch_date": "2026-10-01"}]
        self.assertIn("価格・発売日を掲載", gs.chain_page_description("ミニストップ", items))
        self.assertNotIn("カロリー", gs.chain_page_description("ミニストップ", items))

    def test_chain_description_shortens_long_names(self):
        items = [{"chain": "seven", "product_id": str(i), "name": ("とても長い商品名" * 5) + str(i),
                  "launch_date": f"2026-10-0{i + 1}"} for i in range(3)]
        self.assertLessEqual(len(gs.chain_page_description("セブンイレブン", items)), 120)

    def test_one_h1_per_page_and_top_keeps_site_name_h1(self):
        items = [make_item("seven", "1se")]
        top = gs.render_top(items)
        self.assertEqual(top.count("<h1"), 1)
        self.assertIn("<h1><a", top)
        for h in (gs.render_grid_page("週", "d", items, "https://x/"),
                  gs.render_grid_page("週", "d", [], "https://x/"),
                  gs.render_item_page(items[0], gs.build_context(items)),
                  gs.render_weeks_index(items), gs.render_chains_top()):
            self.assertEqual(h.count("<h1"), 1)
            self.assertIn('<div class="site-name">', h)

    def test_item_section_headings_are_h2(self):
        h = gs.render_item_page(make_item("seven", "1se"))
        self.assertIn("<h2>栄養成分</h2>", h)
        # アレルゲンは節見出しではなく、行頭のラベル(今の見た目のまま)
        self.assertIn("<strong>アレルゲン：</strong>", h)
        self.assertNotIn("<h3", h)

    def test_breadcrumb_json_ld(self):
        import json
        items = [make_item("seven", "1se")]
        h = gs.render_item_page(items[0], gs.build_context(items))
        ld = [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h)]
        self.assertEqual(len(ld), 1)
        self.assertEqual([e["name"] for e in ld[0]["itemListElement"]], ["トップ", "セブンイレブン", "テスト商品1se"])
        self.assertEqual(ld[0]["itemListElement"][1]["item"], "https://conbini.netaful.jp/chains/seven/")
        chain = gs.render_grid_page("t", "d", items, "https://x/", crumbs=[("トップ", "https://a/"), ("c", "https://b/")])
        self.assertIn('"@type": "BreadcrumbList"', chain)
        self.assertNotIn("Product", h)


class TestSitemapLastmod(unittest.TestCase):
    def it(self, chain, pid, seen, fetched, launch="2026-10-01"):
        return {"chain": chain, "product_id": pid, "name": pid, "launch_date": launch,
                "first_seen_at": f"{seen}T07:00:00+09:00", "fetched_at": f"{fetched}T07:00:00+09:00"}

    def test_item_lastmod_is_newer_of_first_seen_and_fetched(self):
        self.assertEqual(gs.item_lastmod(self.it("seven", "1", "2026-10-01", "2026-10-05")), "2026-10-05")
        self.assertEqual(gs.item_lastmod({"first_seen_at": "2026-10-01T07:00:00+09:00"}), "2026-10-01")
        self.assertIsNone(gs.item_lastmod({}))

    def test_sitemap_lastmod_does_not_use_today_and_lists_use_max(self):
        items = [self.it("seven", "1", "2026-10-01", "2026-10-02"), self.it("lawson", "2", "2026-10-03", "2026-10-09")]
        xml = gs.generate_sitemap(items, ["2026-09-28"])
        self.assertIn("<loc>https://conbini.netaful.jp/items/seven-1.html</loc><lastmod>2026-10-02</lastmod>", xml)
        self.assertIn("<loc>https://conbini.netaful.jp/chains/seven/</loc><lastmod>2026-10-02</lastmod>", xml)
        self.assertIn("<loc>https://conbini.netaful.jp/chains/lawson/</loc><lastmod>2026-10-09</lastmod>", xml)
        self.assertIn("<loc>https://conbini.netaful.jp/</loc><lastmod>2026-10-09</lastmod>", xml)
        self.assertIn("<loc>https://conbini.netaful.jp/weeks/2026-09-28/</loc><lastmod>2026-10-09</lastmod>", xml)
        import datetime
        self.assertNotIn(datetime.date.today().isoformat(), xml)


if __name__ == "__main__":
    unittest.main()
