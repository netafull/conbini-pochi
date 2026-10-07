#!/usr/bin/env python3
"""generate_site.py のうち、ネタフル記事のキーワード照合・関連記事選定・
カード描画ロジックのユニットテスト。本番へは一切アクセスしない。"""

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

    def test_no_ad_inside_sortable_grid(self):
        import re
        for name, h in self.pages().items():
            for m in re.finditer(r'<div class="grid" data-sortable>(.*?)\n</div>\n', h, re.S):
                self.assertNotIn("ad-slot", m.group(1), name)

    def test_ad_count_within_limit_and_between_groups(self):
        for name, h in self.pages().items():
            body = h.split("<body>")[1]
            self.assertLessEqual(body.count('<div class="ad-slot">'), MAX_ADS, name)
        self.assertEqual(self.pages()["top"].count('<div class="ad-slot">'), 3)

    def test_item_page_ad_not_adjacent_to_quote(self):
        import re
        h = self.pages()["item"]
        self.assertEqual(h.count('<div class="ad-slot">'), 2)
        for m in re.finditer(r'<div class="ad-slot">', h):
            before = h[:m.start()].rstrip()
            self.assertFalse(before.endswith("</blockquote>") or before.endswith("削除されている、または内容が変更されている場合があります。</p>"))
        i_ad = h.rfind('<div class="ad-slot">')
        self.assertGreater(i_ad, h.index("</blockquote>"))
        self.assertLess(i_ad, h.index("ネタフル") if "ネタフルのレビュー" in h[i_ad:] else len(h))

    def test_item_without_extras_has_only_header_ad(self):
        it = make_item("seven", "9", with_nutrition=False)
        it["allergens"] = None
        h = gs.render_item_page(it)
        self.assertEqual(h.count('<div class="ad-slot">'), 1)

    def test_no_ads_on_empty_pages(self):
        self.assertNotIn('class="ad-slot"', gs.render_chains_top())
        self.assertNotIn('class="ad-slot"', gs.render_weeks_index(self.items))
        self.assertNotIn('class="ad-slot"', gs.render_grid_page("週", "d", [], "https://x/"))

    def test_links_opt_out_of_vignette(self):
        h = self.pages()["top"]
        self.assertNotIn("<a href", h)
        self.assertNotIn("<a class", h)


if __name__ == "__main__":
    unittest.main()
