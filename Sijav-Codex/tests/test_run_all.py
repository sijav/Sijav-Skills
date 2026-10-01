"""validation/run_all.py fails when any suite fails or is not run, or the board copies differ; no suite
is actually run here."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "validation"))
import run_all  # noqa: E402


class Exit(unittest.TestCase):
    def outcome(self, codes, same=True):
        out = Path(tempfile.mkdtemp(prefix="sijav run_all "))
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        names = iter(codes)
        with mock.patch.object(run_all, "OUT", out), \
                mock.patch.object(run_all, "run", side_effect=lambda *a, **k: next(names)), \
                mock.patch.object(run_all, "compare", return_value=(same, "")), \
                mock.patch.object(sys, "argv", ["run_all.py"]):
            code = run_all.main()
        return code, (out / "SUMMARY.txt").read_text(encoding="utf-8")

    def test_exit_code_follows_every_suite_and_the_board_copies(self):
        self.assertEqual(self.outcome([0] * 6)[0], 0)
        code, summary = self.outcome([0, 0, 1, 0, 0, 0])
        self.assertEqual(code, 1)
        self.assertIn("FAILED: loop-unittest", summary)
        self.assertEqual(self.outcome([0, 0, 0, 0, 0, "not run"])[0], 1, "a suite that did not run fails")
        code, summary = self.outcome([0] * 6, same=False)
        self.assertEqual(code, 1)
        self.assertIn("board-copies", summary)
        self.assertNotIn("dashboard", self.outcome([0] * 6)[1], "the obsolete dashboard note is gone")


if __name__ == "__main__":
    unittest.main()
