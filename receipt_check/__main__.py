"""python -m receipt_check: does every landing on a branch carry receipts that verify?

The command and its options are the contract docs/design.md sets; the check
itself comes with the tasks that follow. Until then it never passes: it says it
cannot check yet and exits 2, as it would for any check it could not run, so a
workflow that runs it is never green on a repository it has not checked.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

# Exit codes, as docs/design.md defines them.
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_CANNOT_RUN = 2


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


def run(argv: Sequence[str], err: Callable[[str], None]) -> int:
    parser().parse_args(list(argv))
    err("receipt_check: cannot check yet: this commit holds only the contract (docs/design.md)")
    return EXIT_CANNOT_RUN


def main() -> int:
    return run(sys.argv[1:], lambda line: print(line, file=sys.stderr))


if __name__ == "__main__":
    sys.exit(main())
