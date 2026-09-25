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


if __name__ == "__main__":
    unittest.main()
