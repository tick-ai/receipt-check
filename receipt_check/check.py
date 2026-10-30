"""The check docs/design.md defines: does every landing on a branch carry receipts that verify?

It reads only what travels with the code: the commits of the branch's first-parent
line, the tasks each landing carries (from git alone), the notes under
refs/notes/tick, and the keys the repository allows. Each receipt is verified with
tick_verify (vendored, unchanged); this module words every failure as the design does.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ._vendor.tick_verify import verify_document
from ._vendor.tick_verify.kinds import fields

NOTES_REF = "refs/notes/tick"
LANDING_SCHEMA = "tick.landing-receipts.v1"
RECEIPT_SCHEMA = "tick.receipt.v2"
RELEASE_SCHEMA = "tick.release-receipt.v1"

# Exit codes, as docs/design.md defines them.
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_CANNOT_RUN = 2

# The notes ref is fetched again no more often than this while a note is awaited.
FETCH_EVERY_SECONDS = 15

_TASK_SUBJECT = re.compile(r"Tick: refs/heads/tick/attempt/([^/\s]+)/([^/\s]+) into session/(\S+)")
_RELEASE_SUBJECT = re.compile(r"release [0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")

# What replaces the network and the clock, so a test takes no real time.
Fetch = Callable[[str], "str | None"]
Clock = Callable[[], float]
Sleep = Callable[[float], None]


class CannotRun(Exception):
    """The check could not run (exit 2): it never passes a repository it did not check."""


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    time: int
    subject: str

    @property
    def kind(self) -> str:
        if len(self.parents) >= 2:
            return "landing"
        if _RELEASE_SUBJECT.fullmatch(self.subject):
            return "release"
        return "other"


@dataclass(frozen=True)
class Carried:
    """One task a landing carries: its delivered commit is the second parent of its commit."""

    task: str
    delivered: str | None


@dataclass
class Keys:
    coordinator: set[str] = field(default_factory=set)
    release: set[str] = field(default_factory=set)


class Git:
    def __init__(self, repo: str) -> None:
        self.repo = repo

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", self.repo, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CannotRun(f"cannot run git: {error}") from error

    def lines(self, *args: str) -> list[str]:
        done = self.run(*args)
        if done.returncode != 0:
            raise CannotRun(f"git {' '.join(args)}: {done.stderr.strip()}")
        return [line for line in done.stdout.split("\n") if line]

    def resolves(self, name: str) -> str | None:
        done = self.run("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
        return done.stdout.strip() if done.returncode == 0 else None

    def note(self, sha: str) -> str | None:
        done = self.run("notes", f"--ref={NOTES_REF}", "show", sha)
        return done.stdout if done.returncode == 0 else None

    def has_notes_ref(self) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", NOTES_REF).returncode == 0

    def commits(self, rev_range: str) -> list[Commit]:
        found = []
        for line in self.lines("log", "--first-parent", "--reverse", "--format=%H%x1f%P%x1f%ct%x1f%s", rev_range):
            sha, parents, stamp, subject = line.split("\x1f", 3)
            found.append(Commit(sha, tuple(parents.split()), int(stamp), subject))
        return found


def git_fetch(repo: str) -> str | None:
    """git fetch origin refs/notes/tick:refs/notes/tick; the problem in words, or None."""
    done = Git(repo).run("fetch", "--quiet", "origin", f"{NOTES_REF}:{NOTES_REF}")
    return None if done.returncode == 0 else (done.stderr.strip() or "git fetch failed")


def read_keys(path: str) -> Keys:
    """The keys the repository allows; a list that fails its check stops the run."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise CannotRun(f"cannot read the keys {path}: {error}") from error
    verdict = verify_document(text)
    if not verdict.valid or verdict.kind != "keys-file":
        reasons = verdict.reasons or ["it is not a tick-keys/1 document"]
        raise CannotRun(f"the keys {path} fail their check: " + "; ".join(reasons))
    keys = Keys()
    for key in json.loads(text)["keys"]:
        (keys.coordinator if key["role"] == "coordinator" else keys.release).add(key["key_id"])
    return keys


def _rev(git: Git, branch: str) -> str:
    for name in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}", branch):
        found = git.resolves(name)
        if found:
            return found
    raise CannotRun(f"no branch {branch} in the repository")


def branch_commits(git: Git, branch: str, since: str | None) -> list[Commit]:
    """The first-parent commits of the branch after `since`, oldest first."""
    tip = _rev(git, branch)
    if since is None:
        roots = git.lines("rev-list", "--first-parent", "--max-parents=0", tip)
        base = roots[-1] if roots else None
    else:
        base = git.resolves(since)
        if base is None:
            raise CannotRun(f"--since {since} is not a commit of the repository")
    return git.commits(f"{base}..{tip}" if base else tip)


def carried_tasks(git: Git, landing: Commit) -> list[Carried]:
    """The tasks a landing carries, read from git alone: the commits in P..L^2 whose subject
    names an attempt into a session, each with its second parent as the delivered commit."""
    carried = []
    first, second = landing.parents[0], landing.parents[1]
    for line in git.lines("log", "--format=%H%x1f%P%x1f%s", f"{first}..{second}"):
        _, parents, subject = line.split("\x1f", 2)
        named = _TASK_SUBJECT.fullmatch(subject)
        if named is None:
            continue
        parent_list = parents.split()
        carried.append(Carried(named.group(1), parent_list[1] if len(parent_list) >= 2 else None))
    return carried


def _dig(value: Any, *path: str) -> Any:
    for name in path:
        value = value.get(name) if isinstance(value, dict) else None
    return value


def _named_task(receipt: Any) -> str | None:
    task = _dig(receipt, "receipt", "order", "order", "from_task", "task_id")
    return task if isinstance(task, str) and task else None


def _label(receipt: Any, index: int) -> str:
    named = _named_task(receipt)
    if named is not None:
        return f"task {named}"
    receipt_id = _dig(receipt, "receipt_id")
    return f"task {receipt_id}" if isinstance(receipt_id, str) and receipt_id else f"task (receipt {index + 1})"


def _note_receipts(sha: str, note: str) -> tuple[list[Any], None] | tuple[None, str]:
    """The receipts a note holds, or the failure that says the note is not usable."""
    not_a_note = f"{sha}: the note is not a {LANDING_SCHEMA}"
    try:
        document = json.loads(note)
    except ValueError:
        return None, not_a_note
    if not isinstance(document, dict) or document.get("schema") != LANDING_SCHEMA:
        return None, not_a_note
    if document.get("commit") != sha:
        return None, f"{not_a_note} of this commit: its commit is {json.dumps(document.get('commit'))}, not {sha}"
    receipts = document.get("receipts")
    if not isinstance(receipts, list):
        return None, f"{not_a_note}: its receipts are not a list"
    return receipts, None


def _verify(receipt: Any, schema: str, allowed: set[str], prefix: str) -> tuple[list[str], bool]:
    """The failures of one receipt as the verifier and the key list give them; whether it is a
    receipt of that schema at all."""
    if not isinstance(receipt, dict) or receipt.get("schema") != schema:
        return [f"{prefix}: it is not a {schema}"], False
    verdict = verify_document(json.dumps(receipt, ensure_ascii=False))
    failures = [f"{prefix}: {reason}" for reason in verdict.reasons]
    if schema == RECEIPT_SCHEMA:
        signed_by = verdict.signers.roles.get("acceptor") if verdict.signers is not None else None
    else:
        signed_by = fields.key_id_of(receipt)
    if signed_by is not None and signed_by not in allowed:
        failures.append(f"{prefix}: signed by {signed_by}, which this repository does not allow")
    return failures, True


def check_landing(commit: Commit, carried: list[Carried], note: str | None, keys: Keys) -> list[str]:
    sha = commit.sha
    if note is None:
        return [f"{sha} carries no receipt"]
    receipts, problem = _note_receipts(sha, note)
    if receipts is None:
        return [problem or ""]
    delivered = {item.delivered for item in carried if item.delivered}
    tasks = list(dict.fromkeys(item.task for item in carried))
    failures: list[str] = []
    covered: set[str] = set()
    for index, receipt in enumerate(receipts):
        label = _label(receipt, index)
        found, is_receipt = _verify(receipt, RECEIPT_SCHEMA, keys.coordinator, f"{sha}: {label}")
        failures += found
        if not is_receipt:
            continue
        if _dig(receipt, "receipt", "delivery", "delivery", "commit") not in delivered:
            failures.append(f"{sha}: {label}: the receipt is not of this landing")
        named = _named_task(receipt)
        if named is None or named not in tasks:
            failures.append(f"{sha}: {label} is not carried by this landing")
        else:
            covered.add(named)
    for task in tasks:
        if task not in covered:
            failures.append(f"{sha}: task {task} has no receipt")
    return failures


def check_release(commit: Commit, note: str | None, keys: Keys) -> list[str]:
    sha = commit.sha
    if note is None:
        return [f"{sha} carries no receipt"]
    receipts, problem = _note_receipts(sha, note)
    if receipts is None:
        return [problem or ""]
    if len(receipts) != 1:
        return [f"{sha}: release: the note holds {len(receipts)} receipts, not the release's one"]
    failures, is_receipt = _verify(receipts[0], RELEASE_SCHEMA, keys.release, f"{sha}: release")
    if is_receipt and _dig(receipts[0], "release", "commit") != sha:
        failures.append(f"{sha}: release: the receipt is not of this commit")
    return failures


def _load_notes(
    git: Git,
    needing: list[Commit],
    wait_seconds: int,
    last_fetch: float,
    started: float,
    clock: Clock,
    sleep: Sleep,
    fetch: Fetch,
    err: Callable[[str], None],
) -> dict[str, str | None]:
    """Each commit's note. A commit with none that was pushed less than wait_seconds ago is
    waited for, the notes fetched again no more often than every FETCH_EVERY_SECONDS."""
    notes = {commit.sha: git.note(commit.sha) for commit in needing}
    while True:
        waiting = [c for c in needing if notes[c.sha] is None and clock() < min(c.time, started) + wait_seconds]
        if not waiting:
            return notes
        pause = last_fetch + FETCH_EVERY_SECONDS - clock()
        if pause > 0:
            sleep(pause)
        problem = fetch(git.repo)
        last_fetch = clock()
        if problem:
            err(f"receipt_check: fetching {NOTES_REF}: {problem}")
        for commit in waiting:
            notes[commit.sha] = git.note(commit.sha)


def check(
    repo: str,
    branch: str,
    keys_path: str,
    since: str | None,
    wait_seconds: int,
    out: Callable[[str], None],
    err: Callable[[str], None],
    clock: Clock = time.time,
    sleep: Sleep = time.sleep,
    fetch: Fetch = git_fetch,
) -> int:
    if shutil.which("git") is None:
        raise CannotRun("git is not installed")
    if not os.path.isdir(repo):
        raise CannotRun(f"no repository at {repo}")
    git = Git(repo)
    if git.run("rev-parse", "--git-dir").returncode != 0:
        raise CannotRun(f"no repository at {repo}")
    keys = read_keys(keys_path if os.path.isabs(keys_path) else os.path.join(repo, keys_path))

    started = clock()
    problem = fetch(repo)
    last_fetch = clock()
    if problem:
        err(f"receipt_check: fetching {NOTES_REF}: {problem}")

    commits = branch_commits(git, branch, since)
    carried = {c.sha: carried_tasks(git, c) for c in commits if c.kind == "landing"}
    needing = [c for c in commits if c.kind != "other"]
    notes = _load_notes(git, needing, wait_seconds, last_fetch, started, clock, sleep, fetch, err)
    if not git.has_notes_ref():
        raise CannotRun(f"the repository has no {NOTES_REF}")

    failed = 0
    for commit in commits:
        if commit.kind == "landing":
            failures = check_landing(commit, carried[commit.sha], notes[commit.sha], keys)
            ok = f"{commit.sha} ok: landing carrying {len(carried[commit.sha])} task(s)"
        elif commit.kind == "release":
            failures = check_release(commit, notes[commit.sha], keys)
            ok = f"{commit.sha} ok: {commit.subject}"
        else:
            failures = [f"{commit.sha} did not land through Tick"]
            ok = ""
        if failures:
            failed += 1
            for line in failures:
                out(line)
        else:
            out(ok)
    passed = len(commits) - failed
    out(f"{len(commits)} commit(s) checked on {branch}: {passed} passed, {failed} failed")
    return EXIT_FAILED if failed else EXIT_PASSED
