import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import notify  # noqa: E402


class NotifyCountTest(unittest.TestCase):
    def test_reads_counts_from_run_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run_summary.json"
            p.write_text(json.dumps({"seven": 30, "familymart": 6, "lawson": 0}))
            notify.RUN_SUMMARY = p
            self.assertEqual(notify.count_new_this_run(),
                             {"seven": 30, "familymart": 6, "lawson": 0})

    def test_missing_or_broken_file_means_no_notification(self):
        with tempfile.TemporaryDirectory() as d:
            notify.RUN_SUMMARY = Path(d) / "none.json"
            self.assertEqual(notify.count_new_this_run(), {})
            bad = Path(d) / "bad.json"
            bad.write_text("{oops")
            notify.RUN_SUMMARY = bad
            self.assertEqual(notify.count_new_this_run(), {})


if __name__ == "__main__":
    unittest.main()
