"""crawl.py の実行要約(blocked / list_failed / errors / 連続日数)のテスト。ネットワーク不使用。"""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402
from fetch import BlockedError  # noqa: E402


class RunSummaryTest(unittest.TestCase):
    def test_summary_contains_problems(self):
        stats = {
            "seven": {"new_products": 2, "failed": 3, "blocked": False},
            "lawson": {"new_products": 0, "blocked": True},
            "ministop": {"new_products": 0, "failed": 1, "list_failed": "empty_list"},
            "familymart": {"error": "ValueError: x"},
        }
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(crawl, "RUN_SUMMARY", Path(d) / "s.json"):
            crawl.save_run_summary(stats, {"ministop": 2})
            data = json.loads((Path(d) / "s.json").read_text(encoding="utf-8"))
        self.assertEqual(data["new"]["seven"], 2)
        self.assertEqual(data["blocked"], ["lawson"])
        self.assertEqual(data["list_failed"], ["ministop"])
        self.assertEqual(data["errors"], ["familymart"])
        self.assertEqual(data["streaks"], {"ministop": 2})
        # 詳細単発の失敗は件数のみ。一覧失敗に伴うfailedは含めない
        self.assertEqual(data["detail_failed"], {"seven": 3})

    def test_ministop_list_fetch_error_marks_list_failed(self):
        def boom(chain, url):
            raise OSError("down")
        pending, stats = {}, {}
        with mock.patch.object(crawl, "get", boom):
            crawl.crawl_ministop(pending, stats)
        self.assertEqual(stats["ministop"]["list_failed"], "fetch_error")
        self.assertEqual(pending["ministop"]["list"]["reason"].split(":")[0], "fetch_error")

    def test_ministop_invalid_json_marks_list_failed(self):
        pending, stats = {}, {}
        with mock.patch.object(crawl, "get", lambda c, u: "{oops"):
            crawl.crawl_ministop(pending, stats)
        self.assertEqual(stats["ministop"]["list_failed"], "invalid_json")

    def test_blocked_is_not_list_failed(self):
        def blocked(chain, url):
            raise BlockedError("403")
        pending, stats = {}, {}
        with mock.patch.object(crawl, "get", blocked):
            crawl.crawl_dailyyamazaki(pending, stats)
        self.assertTrue(stats["dailyyamazaki"]["blocked"])
        self.assertNotIn("list_failed", stats["dailyyamazaki"])
        self.assertEqual(crawl.collect_problems(stats)["blocked"], ["dailyyamazaki"])

    def test_lawson_start_page_failure_marks_list_failed(self):
        def boom(chain, url):
            raise OSError("down")
        pending, stats = {}, {}
        with mock.patch.object(crawl, "get", boom):
            crawl.crawl_lawson(pending, stats)
        self.assertEqual(crawl.collect_problems(stats)["list_failed"], ["lawson"])

    def test_main_records_exception_as_error(self):
        def boom(pending, stats):
            raise RuntimeError("bug")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(crawl, "RUN_SUMMARY", Path(d) / "s.json"), \
                mock.patch.object(crawl, "PENDING_PATH", Path(d) / "p.json"), \
                mock.patch.object(crawl, "crawl_ministop", boom), \
                mock.patch.object(sys, "argv", ["crawl.py", "ministop"]):
            crawl.main()
            data = json.loads((Path(d) / "s.json").read_text(encoding="utf-8"))
            pend = json.loads((Path(d) / "p.json").read_text(encoding="utf-8"))
        self.assertEqual(data["errors"], ["ministop"])
        self.assertEqual(data["streaks"], {"ministop": 1})
        self.assertEqual(pend["_health"]["ministop"]["days"], 1)


class HealthStreakTest(unittest.TestCase):
    D = datetime.date

    def test_streak_counts_by_date(self):
        pending = {}
        bad = {"ministop": {"list_failed": "empty_list"}}
        self.assertEqual(crawl.update_health(pending, bad, self.D(2026, 10, 1)), {"ministop": 1})
        # 同じ日の再実行では増えない
        self.assertEqual(crawl.update_health(pending, bad, self.D(2026, 10, 1)), {"ministop": 1})
        self.assertEqual(crawl.update_health(pending, bad, self.D(2026, 10, 2)), {"ministop": 2})
        self.assertEqual(crawl.update_health(pending, bad, self.D(2026, 10, 3)), {"ministop": 3})
        # 日が空けば数え直し
        self.assertEqual(crawl.update_health(pending, bad, self.D(2026, 10, 6)), {"ministop": 1})

    def test_recovery_clears_and_untouched_chains_kept(self):
        pending = {}
        crawl.update_health(pending, {"ministop": {"blocked": True}, "seven": {"blocked": True}},
                            self.D(2026, 10, 1))
        # 次の日は ministop だけ巡回して成功 -> ministop は消え、seven は触らない
        crawl.update_health(pending, {"ministop": {"new_products": 1}}, self.D(2026, 10, 2))
        self.assertNotIn("ministop", pending["_health"])
        self.assertEqual(pending["_health"]["seven"]["days"], 1)
        crawl.update_health(pending, {"seven": {"new_products": 0}}, self.D(2026, 10, 2))
        self.assertNotIn("_health", pending)

    def test_detail_failure_alone_is_not_a_problem(self):
        pending = {}
        self.assertEqual(crawl.update_health(pending, {"seven": {"failed": 5}}, self.D(2026, 10, 1)), {})
        self.assertNotIn("_health", pending)


if __name__ == "__main__":
    unittest.main()
