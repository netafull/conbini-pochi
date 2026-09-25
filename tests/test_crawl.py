#!/usr/bin/env python3
"""crawl.py のうち、ネットワークに依存しないロジック(対象外カテゴリの
スキップ等)のユニットテスト。本番へは一切アクセスしない。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
DETAIL_HTML = (FIX / "familymart" / "item_0415613.html").read_text(encoding="utf-8")


class TestFamilymartExcludeCategory(unittest.TestCase):
    def test_is_familymart_excluded(self):
        self.assertTrue(crawl.is_familymart_excluded("キャラクターくじ・エンタメ雑貨など"))
        self.assertTrue(crawl.is_familymart_excluded("スキンケア・コスメ"))
        self.assertTrue(crawl.is_familymart_excluded("コンビニエンスウェア"))
        self.assertTrue(crawl.is_familymart_excluded("ファミマオンライン"))
        self.assertFalse(crawl.is_familymart_excluded("おむすび"))
        self.assertFalse(crawl.is_familymart_excluded(""))
        self.assertFalse(crawl.is_familymart_excluded(None))

    def test_crawl_familymart_skips_excluded_category_without_detail_fetch(self):
        """一覧に対象外カテゴリの商品が混ざっていても、詳細ページへの
        リクエストは発生せず、data/products/にも保存されないことを確認する。"""
        list_html = (FIX / "familymart" / "newgoods.html").read_text(encoding="utf-8")
        fetched_urls: list[str] = []

        def fake_get(chain: str, url: str) -> str:
            fetched_urls.append(url)
            if chain != "familymart":
                raise AssertionError(f"unexpected chain: {chain}")
            return list_html if "newgoods" in url else DETAIL_HTML

        with tempfile.TemporaryDirectory() as tmp:
            orig_products = crawl.PRODUCTS
            orig_get = crawl.get
            crawl.PRODUCTS = Path(tmp)
            crawl.get = fake_get
            try:
                pending: dict = {}
                stats: dict = {}
                crawl.crawl_familymart(pending, stats)

                excluded_ids = {
                    e["product_id"]
                    for e in crawl.parsers.parse_familymart_list(list_html)
                    if crawl.is_familymart_excluded(e.get("category"))
                }
                self.assertGreater(len(excluded_ids), 0)

                # 除外カテゴリの詳細URLへはアクセスしていないこと
                for pid in excluded_ids:
                    for url in fetched_urls:
                        self.assertNotIn(pid, url.replace("newgoods", ""))

                # 除外カテゴリの商品はファイルとして保存されていないこと
                saved = {p.stem for p in (Path(tmp) / "familymart").glob("*.json")}
                self.assertTrue(excluded_ids.isdisjoint(saved))

                # スキップ件数が集計されていること
                self.assertEqual(stats["familymart"]["excluded_category"], len(excluded_ids))
            finally:
                crawl.PRODUCTS = orig_products
                crawl.get = orig_get


if __name__ == "__main__":
    unittest.main()
