"""python -m receipt_check: does every landing on a branch carry receipts that verify?

The command and its options are the contract docs/design.md sets; the check
itself is receipt_check.check. Exit 0 when no commit fails, 1 when one does, 2 when
the check could not run.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Callable, Sequence

from .check import EXIT_CANNOT_RUN, EXIT_FAILED, EXIT_PASSED, CannotRun, Clock, Fetch, Sleep, check, git_fetch

__all__ = ["EXIT_CANNOT_RUN", "EXIT_FAILED", "EXIT_PASSED", "main", "parser", "run"]


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(
        prog="python -m receipt_check",
        description="Check that every landing on a branch carries receipts that verify, "
        "signed by keys the repository allows.",
    )
    made.add_argument("--repo", default=".", help="the repository to check (default: here)")
    made.add_argument("--branch", default="main", help="the branch whose landings are checked")
    made.add_argument(
        "--keys",
        default=".tick/keys.json",
        help="the keys the repository allows: a tick-keys/1 document (spec/SPEC.md)",
    )
    made.add_argument(
        "--since",
        default=None,
        help="the last commit not checked (default: the repository's first commit)",
    )
    made.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
        help="how long to wait for the notes of the newest landings before failing them",
    )
    return made


def run(
    argv: Sequence[str],
    out: Callable[[str], None],
    err: Callable[[str], None],
    clock: Clock = time.time,
    sleep: Sleep = time.sleep,
    fetch: Fetch = git_fetch,
) -> int:
    args = parser().parse_args(list(argv))
    try:
        return check(args.repo, args.branch, args.keys, args.since, args.wait_seconds, out, err, clock, sleep, fetch)
    except CannotRun as error:
        err(f"receipt_check: cannot check: {error}")
        return EXIT_CANNOT_RUN


def main() -> int:
    return run(sys.argv[1:], print, lambda line: print(line, file=sys.stderr))


if __name__ == "__main__":
    sys.exit(main())
