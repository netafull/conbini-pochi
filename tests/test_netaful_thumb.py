#!/usr/bin/env python3
"""fetch_netaful_reviews.py のアイキャッチ画像(thumb)抽出ロジックのユニット
テスト。WP REST APIのレスポンス構造を模したdictを与えるだけで、本番へは
一切アクセスしない。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_netaful_reviews as fnr  # noqa: E402


def make_post(embedded_media=None):
    post = {"id": 1, "date": "2026-01-01T00:00:00", "link": "https://netaful.jp/x.html",
            "title": {"rendered": "「テスト商品」を食べてみた"}}
    if embedded_media is not None:
        post["_embedded"] = {"wp:featuredmedia": embedded_media}
    return post


class TestExtractThumb(unittest.TestCase):
    def test_no_embedded_media_returns_none(self):
        self.assertIsNone(fnr.extract_thumb(make_post()))
        self.assertIsNone(fnr.extract_thumb(make_post([])))

    def test_embedded_error_object_returns_none(self):
        # 権限不足等で埋め込みが失敗すると {"code": "rest_forbidden", ...} が入る
        media = [{"code": "rest_forbidden", "message": "..."}]
        self.assertIsNone(fnr.extract_thumb(make_post(media)))

    def test_prefers_medium_large_within_range(self):
        media = [{
            "source_url": "https://netaful.jp/full.jpg",
            "media_details": {
                "width": 1200, "height": 900,
                "sizes": {
                    "thumbnail": {"source_url": "https://netaful.jp/t.jpg", "width": 150, "height": 150},
                    "medium": {"source_url": "https://netaful.jp/m.jpg", "width": 300, "height": 225},
                    "medium_large": {"source_url": "https://netaful.jp/ml.jpg", "width": 485, "height": 364},
                    "large": {"source_url": "https://netaful.jp/l.jpg", "width": 1024, "height": 768},
                },
            },
        }]
        thumb = fnr.extract_thumb(make_post(media))
        self.assertEqual(thumb["url"], "https://netaful.jp/ml.jpg")
        self.assertEqual(thumb["width"], 485)
        self.assertEqual(thumb["height"], 364)

    def test_falls_back_to_smallest_size_over_min_width(self):
        media = [{
            "source_url": "https://netaful.jp/full.jpg",
            "media_details": {
                "width": 1024, "height": 768,
                "sizes": {
                    "thumbnail": {"source_url": "https://netaful.jp/t.jpg", "width": 150, "height": 150},
                    "large": {"source_url": "https://netaful.jp/l.jpg", "width": 1024, "height": 768},
                },
            },
        }]
        thumb = fnr.extract_thumb(make_post(media))
        self.assertEqual(thumb["url"], "https://netaful.jp/l.jpg")

    def test_falls_back_to_source_url_when_no_sizes(self):
        media = [{"source_url": "https://netaful.jp/only.jpg",
                  "media_details": {"width": 800, "height": 600, "sizes": {}}}]
        thumb = fnr.extract_thumb(make_post(media))
        self.assertEqual(thumb["url"], "https://netaful.jp/only.jpg")
        self.assertEqual(thumb["width"], 800)

    def test_no_source_url_anywhere_returns_none(self):
        media = [{"media_details": {"sizes": {}}}]
        self.assertIsNone(fnr.extract_thumb(make_post(media)))

    def test_thumbnail_only_still_used_as_last_resort(self):
        media = [{
            "source_url": "https://netaful.jp/full.jpg",
            "media_details": {
                "width": 150, "height": 150,
                "sizes": {
                    "thumbnail": {"source_url": "https://netaful.jp/t.jpg", "width": 150, "height": 150},
                },
            },
        }]
        thumb = fnr.extract_thumb(make_post(media))
        # 300px未満のサイズしか無ければ最終手段でsource_url(オリジナル)を使う
        self.assertEqual(thumb["url"], "https://netaful.jp/full.jpg")


if __name__ == "__main__":
    unittest.main()
