"""Until the check exists, the command never passes: a workflow that runs it is not green."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest


class CannotCheckYet(unittest.TestCase):
    def test_exits_2_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            done = subprocess.run(
                [sys.executable, "-m", "receipt_check", "--repo", empty],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("cannot check yet", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_an_unknown_option_is_refused_not_ignored(self) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "receipt_check", "--allow-everything"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments", done.stderr)


if __name__ == "__main__":
    unittest.main()
