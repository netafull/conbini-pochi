import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import notify  # noqa: E402

NAMES = {"seven": "セブンイレブン", "ministop": "ミニストップ", "dailyyamazaki": "デイリーヤマザキ"}


def _summary(**kw):
    base = {"new": {}, "blocked": [], "list_failed": [], "errors": [],
            "streaks": {}, "detail_failed": {}}
    base.update(kw)
    return base


class NotifyCountTest(unittest.TestCase):
    def test_reads_counts_from_old_flat_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run_summary.json"
            p.write_text(json.dumps({"seven": 30, "familymart": 6, "lawson": 0}))
            notify.RUN_SUMMARY = p
            self.assertEqual(notify.count_new_this_run(),
                             {"seven": 30, "familymart": 6, "lawson": 0})
            s = notify.load_run_summary()
            self.assertEqual(s["blocked"], [])
            self.assertEqual(s["list_failed"], [])

    def test_reads_new_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run_summary.json"
            p.write_text(json.dumps({"new": {"seven": 2}, "blocked": ["lawson"],
                                     "list_failed": ["ministop"], "errors": [],
                                     "streaks": {"ministop": 3}, "detail_failed": {"seven": 1}}))
            notify.RUN_SUMMARY = p
            s = notify.load_run_summary()
            self.assertEqual(s["new"], {"seven": 2})
            self.assertEqual(s["blocked"], ["lawson"])
            self.assertEqual(s["streaks"], {"ministop": 3})

    def test_missing_or_broken_file_means_no_notification(self):
        with tempfile.TemporaryDirectory() as d:
            notify.RUN_SUMMARY = Path(d) / "none.json"
            self.assertEqual(notify.count_new_this_run(), {})
            bad = Path(d) / "bad.json"
            bad.write_text("{oops")
            notify.RUN_SUMMARY = bad
            self.assertEqual(notify.count_new_this_run(), {})
            self.assertIsNone(notify.build_summary_message(notify.load_run_summary(), NAMES))


class BuildSummaryMessageTest(unittest.TestCase):
    def test_nothing_to_report(self):
        self.assertIsNone(notify.build_summary_message(_summary(new={"seven": 0}), NAMES))

    def test_new_only(self):
        title, body, prio = notify.build_summary_message(_summary(new={"seven": 3}), NAMES)
        self.assertEqual(title, "コンビニポチ: 新商品 3件")
        self.assertIn("セブンイレブン 3件", body)
        self.assertNotIn("巡回の問題", body)
        self.assertEqual(prio, 0)

    def test_problem_only(self):
        s = _summary(new={"seven": 0}, list_failed=["ministop"], streaks={"ministop": 3})
        title, body, prio = notify.build_summary_message(s, NAMES)
        self.assertEqual(title, "コンビニポチ: 巡回に問題(ミニストップ)")
        self.assertIn("ミニストップ: 一覧の取得に失敗(3日連続)", body)
        self.assertEqual(prio, 4)

    def test_both_in_one_message(self):
        s = _summary(new={"seven": 2}, blocked=["seven"], errors=["dailyyamazaki"],
                     streaks={"seven": 1, "dailyyamazaki": 2}, detail_failed={"seven": 4})
        title, body, prio = notify.build_summary_message(s, NAMES)
        self.assertEqual(title, "コンビニポチ: 新商品 2件(問題あり: セブンイレブン・デイリーヤマザキ)")
        self.assertIn("セブンイレブン: ブロックされた\n", body + "\n")
        self.assertNotIn("セブンイレブン: ブロックされた(1日連続)", body)
        self.assertIn("デイリーヤマザキ: 例外で停止(2日連続)", body)
        self.assertIn("詳細ページの取得失敗 4件", body)
        self.assertEqual(prio, 4)


class MainTest(unittest.TestCase):
    def _run(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run_summary.json"
            p.write_text(json.dumps(data))
            notify.RUN_SUMMARY = p
            with mock.patch.object(notify, "send") as send, \
                    mock.patch.dict("os.environ", {"NTFY_TOPIC": "t"}), \
                    mock.patch.object(sys, "argv", ["notify.py", "--summary"]):
                rc = notify.main()
        return rc, send

    def test_sends_for_problem_only(self):
        rc, send = self._run({"new": {"seven": 0}, "blocked": ["lawson"], "list_failed": [],
                              "errors": [], "streaks": {}, "detail_failed": {}})
        self.assertEqual(rc, 0)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "t")
        self.assertEqual(send.call_args.args[4], 4)

    def test_no_send_when_quiet(self):
        rc, send = self._run({"new": {"seven": 0}, "blocked": [], "list_failed": [],
                              "errors": [], "streaks": {}, "detail_failed": {"seven": 2}})
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_old_format_with_new_sends(self):
        rc, send = self._run({"seven": 1})
        send.assert_called_once()

    def test_old_format_zero_no_send(self):
        rc, send = self._run({"seven": 0})
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
