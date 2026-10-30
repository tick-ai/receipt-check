"""Fixture repositories in temporary folders, each with a bare remote, and the receipts a test writes.

A landing is shaped as Tick makes it: each task's delivered commit is merged into a
session branch with the subject `Tick: refs/heads/tick/attempt/<task>/<attempt> into
session/<session>`, and the session is merged into main by a merge. The notes are the
test's own: receipts of version two built from spec/SPEC.md's fields and signed with
keys made on the spot.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .signer import Signer, document_hash

LANDING_SCHEMA = "tick.landing-receipts.v1"
NOTES_REF = "refs/notes/tick"
REPOSITORY = "demo/repo"
# Commit dates of fixtures count up from here (2020), far enough back that no note is awaited.
EPOCH = 1_577_836_800
ISSUED_AT = "2026-10-30T12:00:00Z"


@dataclass
class Landing:
    sha: str
    base: str
    session: str
    # task -> its delivered commit
    delivered: dict[str, str] = field(default_factory=dict)


class Fixture:
    def __init__(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="receipt-check-")
        self.bare = os.path.join(self.folder, "remote.git")
        self.work = os.path.join(self.folder, "work")
        self.clone = os.path.join(self.folder, "clone")
        self.counter = 0
        self.date = EPOCH
        self._git(self.folder, "init", "--quiet", "--bare", "-b", "main", self.bare)
        self._git(self.folder, "init", "--quiet", "-b", "main", self.work)
        self.git("remote", "add", "origin", self.bare)
        self.root = self.commit_file("README", "Tick: the order of project demo")
        self.git("push", "--quiet", "origin", "main")

    def close(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def __enter__(self) -> "Fixture":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # git

    def _git(self, cwd: str, *args: str, stdin: str | None = None) -> str:
        self.date += 60
        stamp = f"{self.date} +0000"
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_TERMINAL_PROMPT": "0",
        }
        done = subprocess.run(["git", *args], cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def git(self, *args: str, stdin: str | None = None) -> str:
        return self._git(self.work, *args, stdin=stdin)

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter:04d}"

    # commits on main

    def commit_file(self, name: str, subject: str) -> str:
        with open(os.path.join(self.work, name), "w", encoding="utf-8") as handle:
            handle.write(f"{self.next_id(name)}\n")
        self.git("add", name)
        self.git("commit", "--quiet", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def plain_commit(self, subject: str = "A change made by hand") -> str:
        return self.commit_file(self.next_id("hand"), subject)

    def release(self, version: str = "1.0.0") -> str:
        return self.commit_file("VERSION", f"release {version}")

    def land(self, tasks: tuple[str, ...] | None = None, at: int | None = None) -> Landing:
        """A landing as Tick makes it: each task on its own attempt branch from main, merged into a
        session branch, and the session merged into main. `at` is the landing's commit time."""
        tasks = tasks or (self.next_id("task"),)
        base = self.git("rev-parse", "main")
        session = self.next_id("session")
        self.git("checkout", "--quiet", "-b", f"session/{session}", base)
        landing = Landing("", base, session)
        for task in tasks:
            attempt = self.next_id("attempt")
            ref = f"tick/attempt/{task}/{attempt}"
            self.git("checkout", "--quiet", "-b", ref, base)
            with open(os.path.join(self.work, f"{task}.txt"), "w", encoding="utf-8") as handle:
                handle.write(f"work of {task}\n")
            self.git("add", f"{task}.txt")
            self.git("commit", "--quiet", "-m", f"Work of {task}")
            landing.delivered[task] = self.git("rev-parse", "HEAD")
            self.git("checkout", "--quiet", f"session/{session}")
            self.git("merge", "--quiet", "--no-ff", "-m", f"Tick: refs/heads/{ref} into session/{session}", ref)
        self.git("checkout", "--quiet", "main")
        if at is not None:
            self.date = at - 60
        self.git("merge", "--quiet", "--no-ff", "-m", f"Tick: land session/{session} in main, as accepted", f"session/{session}")
        landing.sha = self.git("rev-parse", "HEAD")
        self.git("push", "--quiet", "origin", "main")
        return landing

    # receipts

    def tree_of(self, commit: str) -> str:
        return self.git("rev-parse", f"{commit}^{{tree}}")

    def receipt(self, landing: Landing, task: str, signer: Signer, receipt_id: str | None = None) -> dict[str, Any]:
        """A tick.receipt.v2 for one task of a landing, its delivered commit that landing's real one,
        every part signed by `signer`."""
        delivered = landing.delivered[task]
        order = signed_document(
            signer,
            "tick.work-order.v1",
            "order_id",
            self.next_id("order"),
            "order",
            {
                "what": f"Do {task}",
                "repository": REPOSITORY,
                "base": landing.base,
                "territory": [f"{task}.txt"],
                "checks": {"commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}]},
                "budget": {"cap_usd": None},
                "deadline": None,
                "acceptor": {"kind": "checks"},
                "payment": {"kind": "none"},
                "from_task": {"run_id": "run-1", "task_id": task},
            },
        )
        order["checks_hash"] = document_hash(order["order"]["checks"])
        delivery = signed_document(
            signer,
            "tick.delivery.v1",
            "delivery_id",
            self.next_id("delivery"),
            "delivery",
            {
                "order_hash": order["content_hash"],
                "repository": REPOSITORY,
                "base": landing.base,
                "commit": delivered,
                "tree": self.tree_of(delivered),
                "tries": [{"agent": "claude", "model": None, "minutes": 1, "cents": None, "cost": "unknown"}],
            },
        )
        record = signed_document(
            signer,
            "tick.check-record.v1",
            "record_id",
            self.next_id("record"),
            "record",
            {
                "order_hash": order["content_hash"],
                "checks_hash": order["checks_hash"],
                "repository": REPOSITORY,
                "base": landing.base,
                "commit": delivered,
                "tree": self.tree_of(delivered),
                "territory_held": True,
                "results": [{"name": "tests", "exit_code": 0, "outcome": "passed", "duration_ms": 10}],
                "contained": "none",
                "verdict": "passed",
            },
        )
        return signed_document(
            signer,
            "tick.receipt.v2",
            "receipt_id",
            receipt_id or self.next_id("receipt"),
            "receipt",
            {
                "order": order,
                "delivery": delivery,
                "check": record,
                "acceptance": {"by": "checks", "who": "checks", "at": ISSUED_AT},
                "money": {"kind": "none"},
            },
        )

    def release_receipt(self, commit: str, signer: Signer, version: str = "1.0.0") -> dict[str, Any]:
        """A tick.release-receipt.v1 for a release commit."""
        digit = "0123456789abcdef"
        document = {
            "schema": "tick.release-receipt.v1",
            "alg": "ed25519",
            "issued_at": ISSUED_AT,
            "coordinator": "coordinator-1",
            "run_id": "run-1",
            "task_id": self.next_id("release-task"),
            "release": {
                "version": version,
                "gated": commit,
                "base": None,
                "commit": commit,
                "tarball": f"demo-{version}.tgz",
                "tarball_sha256": digit[1] * 64,
                "shasum": digit[2] * 40,
                "pushed": ["origin"],
                "record_sha256": digit[3] * 64,
                "gate_check_sha256": digit[4] * 64,
                "gate_summary_sha256": digit[5] * 64,
                "task_receipt_hash": digit[6] * 64,
            },
        }
        document["content_hash"] = document_hash(document["release"])
        document["key"], document["key_id"] = signer.key, signer.key_id
        label = f"tick-release-receipt:v1:{document['run_id']}:{document['task_id']}:{document['content_hash']}"
        document["signature"] = signer.sign_text(label)
        return document

    # notes

    def write_note(self, sha: str, receipts: list[dict[str, Any]], commit: str | None = None) -> str:
        """The note on `sha` as a string of one line of JSON; written to the local notes and pushed."""
        text = note_text(commit or sha, receipts)
        self.put_note(sha, text)
        return text

    def put_note(self, sha: str, text: str) -> None:
        self.git("notes", f"--ref={NOTES_REF}", "add", "-f", "-F", "-", sha, stdin=text)
        self.git("push", "--quiet", "origin", f"{NOTES_REF}:{NOTES_REF}")

    # the checker's clone

    def fresh_clone(self) -> str:
        shutil.rmtree(self.clone, ignore_errors=True)
        self._git(self.folder, "clone", "--quiet", self.bare, self.clone)
        return self.clone

    def keys_file(self, *keys: tuple[str, str], name: str = "keys.json") -> str:
        path = os.path.join(self.folder, name)
        document = {"schema": "tick-keys/1", "keys": [{"key_id": i, "role": r, "from": "2026-10-30"} for i, r in keys]}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return path


def note_text(commit: str, receipts: list[dict[str, Any]]) -> str:
    return json.dumps({"schema": LANDING_SCHEMA, "commit": commit, "receipts": receipts}, separators=(",", ":"))


def signed_document(signer: Signer, schema: str, id_name: str, identity: str, body_name: str, body: dict[str, Any]) -> dict[str, Any]:
    """A document with the shape every signed Tick document has, signed under the label spec/SPEC.md names."""
    labels = {
        "tick.work-order.v1": "tick-work-order:v1",
        "tick.delivery.v1": "tick-delivery:v1",
        "tick.check-record.v1": "tick-check-record:v1",
        "tick.receipt.v2": "tick-receipt:v2",
    }
    document: dict[str, Any] = {
        "schema": schema,
        "alg": "ed25519",
        "issued_at": ISSUED_AT,
        "coordinator": "coordinator-1",
        id_name: identity,
        body_name: body,
        "content_hash": document_hash(body),
        "key": signer.key,
        "key_id": signer.key_id,
    }
    resign(signer, document, labels[schema], id_name)
    return document


def resign(signer: Signer, document: dict[str, Any], label: str, id_name: str) -> None:
    document["signature"] = signer.sign_text(f"{label}:{document[id_name]}:{document['content_hash']}")
