"""The check over fixture repositories: what passes, what fails in the design's words, what cannot run."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Callable

from receipt_check.__main__ import run
from receipt_check.check import FETCH_EVERY_SECONDS, git_fetch

from .fixtures import Fixture, Landing, note_text
from .signer import Signer, public_key, sign

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A time after every fixture commit, for the tests that wait on a clock they control.
NOW = 1_900_000_000


class FakeClock:
    """A clock that moves only when the check sleeps, so waiting takes no real time."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class SignerVector(unittest.TestCase):
    """The test-only signer gives RFC 8032 section 7.1's first test vector, so what it signs means something."""

    def test_rfc_8032_test_1(self) -> None:
        secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        self.assertEqual(public_key(secret).hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        self.assertEqual(
            sign(secret, b"").hex(),
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        )


class CheckCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.coordinator = Signer()
        cls.older = Signer()
        cls.release_signer = Signer()
        cls.stranger = Signer()

    def keys(self, fx: Fixture) -> str:
        return fx.keys_file(
            (self.older.key_id, "coordinator"),
            (self.coordinator.key_id, "coordinator"),
            (self.release_signer.key_id, "release"),
        )

    def note_landing(self, fx: Fixture, landing: Landing, signer: Signer | None = None) -> None:
        signer = signer or self.coordinator
        fx.write_note(landing.sha, [fx.receipt(landing, task, signer) for task in landing.delivered])

    def check(
        self,
        fx: Fixture,
        keys: str | None = None,
        wait: int = 0,
        extra: tuple[str, ...] = (),
        **injected: Any,
    ) -> tuple[int, list[str], list[str]]:
        out: list[str] = []
        err: list[str] = []
        argv = ["--repo", fx.fresh_clone(), "--keys", keys or self.keys(fx)]
        code = run([*argv, "--wait-seconds", str(wait), *extra], out.append, err.append, **injected)
        return code, out, err

    def assertChecked(self, out: list[str], lines: list[str], passed: int, failed: int) -> None:
        self.assertEqual(out[:-1], lines)
        self.assertEqual(out[-1], f"{passed + failed} commit(s) checked on main: {passed} passed, {failed} failed")


class Passed(CheckCase):
    def test_every_landing_receipted_under_an_allowed_key_and_a_release(self) -> None:
        with Fixture() as fx:
            first = fx.land()
            second = fx.land(tasks=("task-x", "task-y"))
            release = fx.release("1.2.3")
            self.note_landing(fx, first, self.older)  # a key from before the current one still counts
            self.note_landing(fx, second)
            fx.write_note(release, [fx.release_receipt(release, self.release_signer, "1.2.3")])
            code, out, err = self.check(fx)
        self.assertEqual((code, err), (0, []))
        self.assertChecked(
            out,
            [
                f"{first.sha} ok: landing carrying 1 task(s)",
                f"{second.sha} ok: landing carrying 2 task(s)",
                f"{release} ok: release 1.2.3",
            ],
            3,
            0,
        )

    def test_since_leaves_out_what_came_before(self) -> None:
        with Fixture() as fx:
            by_hand = fx.plain_commit()
            landing = fx.land()
            self.note_landing(fx, landing)
            code, out, _ = self.check(fx, extra=("--since", by_hand))
        self.assertEqual(code, 0)
        self.assertChecked(out, [f"{landing.sha} ok: landing carrying 1 task(s)"], 1, 0)


class Failed(CheckCase):
    def failing(self, fx: Fixture, **kwargs: Any) -> tuple[list[str], list[str]]:
        code, out, err = self.check(fx, **kwargs)
        self.assertEqual(code, 1, (out, err))
        return out, err

    def test_a_landing_with_no_note(self) -> None:
        with Fixture() as fx:
            noted = fx.land()
            bare = fx.land()
            self.note_landing(fx, noted)
            out, _ = self.failing(fx)
        self.assertChecked(out, [f"{noted.sha} ok: landing carrying 1 task(s)", f"{bare.sha} carries no receipt"], 1, 1)

    def test_a_note_naming_another_commit(self) -> None:
        with Fixture() as fx:
            first = fx.land()
            second = fx.land()
            self.note_landing(fx, first)
            fx.write_note(second.sha, [fx.receipt(second, t, self.coordinator) for t in second.delivered], commit=first.sha)
            out, _ = self.failing(fx)
        self.assertChecked(
            out,
            [
                f"{first.sha} ok: landing carrying 1 task(s)",
                f'{second.sha}: the note is not a tick.landing-receipts.v1 of this commit: its commit is "{first.sha}", not {second.sha}',
            ],
            1,
            1,
        )

    def test_a_receipt_changed_by_one_byte(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            (task,) = landing.delivered
            text = note_text(landing.sha, [fx.receipt(landing, task, self.coordinator)])
            changed = text.replace("Do ", "Dp ", 1)
            self.assertEqual(len(changed), len(text))
            fx.put_note(landing.sha, changed)
            out, _ = self.failing(fx)
        failures = out[:-1]
        prefix = f"{landing.sha}: task {task}: "
        self.assertTrue(all(line.startswith(prefix) for line in failures), failures)
        self.assertIn(prefix + "order: content_hash is not the hash of the order", failures)
        self.assertIn(prefix + "content_hash is not the hash of the receipt", failures)
        self.assertEqual(out[-1], "1 commit(s) checked on main: 0 passed, 1 failed")

    def test_a_receipt_under_a_key_the_list_does_not_allow(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            (task,) = landing.delivered
            self.note_landing(fx, landing, self.stranger)
            out, _ = self.failing(fx)
        self.assertChecked(
            out, [f"{landing.sha}: task {task}: signed by {self.stranger.key_id}, which this repository does not allow"], 0, 1
        )

    def test_a_release_receipt_counts_only_under_a_release_key(self) -> None:
        with Fixture() as fx:
            noted = fx.land()
            self.note_landing(fx, noted)
            release = fx.release()
            fx.write_note(release, [fx.release_receipt(release, self.coordinator)])
            out, _ = self.failing(fx)
        self.assertEqual(out[1], f"{release}: release: signed by {self.coordinator.key_id}, which this repository does not allow")

    def test_a_task_the_landing_carries_with_no_receipt(self) -> None:
        with Fixture() as fx:
            landing = fx.land(tasks=("task-x", "task-y"))
            fx.write_note(landing.sha, [fx.receipt(landing, "task-x", self.coordinator)])
            out, _ = self.failing(fx)
        self.assertChecked(out, [f"{landing.sha}: task task-y has no receipt"], 0, 1)

    def test_a_receipt_of_another_landing(self) -> None:
        with Fixture() as fx:
            first = fx.land(tasks=("task-p",))
            second = fx.land(tasks=("task-q",))
            self.note_landing(fx, first)
            fx.write_note(second.sha, [fx.receipt(first, "task-p", self.coordinator)])
            out, _ = self.failing(fx)
        self.assertChecked(
            out,
            [
                f"{first.sha} ok: landing carrying 1 task(s)",
                f"{second.sha}: task task-p: the receipt is not of this landing",
                f"{second.sha}: task task-p is not carried by this landing",
                f"{second.sha}: task task-q has no receipt",
            ],
            1,
            1,
        )

    def test_a_commit_that_did_not_land_through_tick(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            by_hand = fx.plain_commit()
            self.note_landing(fx, landing)
            out, _ = self.failing(fx)
        self.assertChecked(out, [f"{landing.sha} ok: landing carrying 1 task(s)", f"{by_hand} did not land through Tick"], 1, 1)

    def test_every_failure_is_printed_not_only_the_first(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            by_hand = fx.plain_commit()
            second = fx.land()
            self.note_landing(fx, landing, self.stranger)
            out, _ = self.failing(fx)
        self.assertEqual(len(out) - 1, 3)
        self.assertIn(f"{by_hand} did not land through Tick", out)
        self.assertIn(f"{second.sha} carries no receipt", out)


class CannotRun(CheckCase):
    def test_no_repository(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            out: list[str] = []
            err: list[str] = []
            code = run(["--repo", empty], out.append, err.append)
        self.assertEqual((code, out), (2, []))
        self.assertIn("no repository", err[0])

    def test_a_key_list_that_fails_its_check(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            self.note_landing(fx, landing)
            bad = fx.keys_file(("not-a-key-id", "owner"), name="bad.json")
            code, out, err = self.check(fx, keys=bad)
        self.assertEqual((code, out), (2, []))
        self.assertIn("keys[0].key_id is not 16 lowercase hexadecimal characters", err[0])
        self.assertIn("keys[0].role is not coordinator or release", err[0])

    def test_a_missing_key_list(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            self.note_landing(fx, landing)
            code, out, err = self.check(fx, keys=os.path.join(fx.folder, "nowhere.json"))
        self.assertEqual((code, out), (2, []))
        self.assertIn("cannot read the keys", err[0])

    def test_a_repository_with_no_notes_ref(self) -> None:
        with Fixture() as fx:
            fx.land()
            code, out, err = self.check(fx, keys=self.keys(fx))
        self.assertEqual((code, out), (2, []))
        self.assertIn("refs/notes/tick", err[-1])

    def test_an_unknown_option_is_refused_not_ignored(self) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "receipt_check", "--allow-everything"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=ROOT,
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments", done.stderr)

    def test_the_module_runs_as_a_command(self) -> None:
        with Fixture() as fx:
            landing = fx.land()
            self.note_landing(fx, landing)
            clone = fx.fresh_clone()
            done = subprocess.run(
                [sys.executable, "-m", "receipt_check", "--repo", clone, "--keys", self.keys(fx), "--wait-seconds", "0"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=ROOT,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{landing.sha} ok", done.stdout)


class Waiting(CheckCase):
    """The newest landing's note comes after it: the notes are fetched again, but not too often."""

    def run_waiting(self, fx: Fixture, clock: FakeClock, on_fetch: Callable[[int], None]) -> tuple[int, list[str], list[str], list[float]]:
        fetched: list[float] = []

        def fetch(repo: str) -> str | None:
            fetched.append(clock.now)
            on_fetch(len(fetched))
            return git_fetch(repo)

        code, out, err = self.check(fx, wait=180, clock=clock, sleep=clock.sleep, fetch=fetch)
        return code, out, err, fetched

    def test_a_note_that_arrives_late_is_found(self) -> None:
        with Fixture() as fx:
            older = fx.land()
            self.note_landing(fx, older)
            newest = fx.land(at=NOW - 5)
            late = [fx.receipt(newest, task, self.coordinator) for task in newest.delivered]
            clock = FakeClock()
            code, out, err, fetched = self.run_waiting(
                fx, clock, lambda count: fx.put_note(newest.sha, note_text(newest.sha, late)) if count == 3 else None
            )
        self.assertEqual((code, err), (0, []))
        self.assertChecked(
            out, [f"{older.sha} ok: landing carrying 1 task(s)", f"{newest.sha} ok: landing carrying 1 task(s)"], 2, 0
        )
        self.assertEqual(fetched, [NOW, NOW + 15, NOW + 30])

    def test_the_first_landing_of_a_repository_waits_for_a_notes_ref_that_does_not_exist_yet(self) -> None:
        with Fixture() as fx:
            only = fx.land(at=NOW - 5)
            late = [fx.receipt(only, task, self.coordinator) for task in only.delivered]
            clock = FakeClock()
            code, out, err, fetched = self.run_waiting(
                fx, clock, lambda count: fx.put_note(only.sha, note_text(only.sha, late)) if count == 2 else None
            )
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(fetched, [NOW, NOW + 15])

    def test_a_note_that_never_comes_fails_once_the_wait_is_over_and_never_fetches_too_often(self) -> None:
        with Fixture() as fx:
            older = fx.land()
            self.note_landing(fx, older)
            newest = fx.land(at=NOW - 5)
            clock = FakeClock()
            code, out, _, fetched = self.run_waiting(fx, clock, lambda count: None)
        self.assertEqual(code, 1)
        self.assertIn(f"{newest.sha} carries no receipt", out)
        gaps = [later - earlier for earlier, later in zip(fetched, fetched[1:])]
        self.assertTrue(all(gap >= FETCH_EVERY_SECONDS for gap in gaps), gaps)
        # Waited until 180 seconds after the landing's push, and not a fetch round longer.
        self.assertGreaterEqual(fetched[-1], NOW - 5 + 180)
        self.assertLess(fetched[-1], NOW - 5 + 180 + FETCH_EVERY_SECONDS)

    def test_a_commit_older_than_the_wait_with_no_note_fails_at_once(self) -> None:
        with Fixture() as fx:
            older = fx.land()
            self.note_landing(fx, older)
            old = fx.land()
            clock = FakeClock()
            code, out, _, fetched = self.run_waiting(fx, clock, lambda count: None)
        self.assertEqual(code, 1)
        self.assertIn(f"{old.sha} carries no receipt", out)
        self.assertEqual(fetched, [NOW])
        self.assertEqual(clock.slept, [])


if __name__ == "__main__":
    unittest.main()
