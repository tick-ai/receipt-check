# Design

What this check is for: a company that has agents land work can require, in its
own CI, that every commit they land carries a receipt that verifies, signed by a
key the repository allows. It needs Tick for nothing: the receipts travel with
the code, and the verifier is written from [spec/SPEC.md](../spec/SPEC.md).

## What it reads

- The notes on the branch's commits under `refs/notes/tick`: one line of JSON
  each, a `tick.landing-receipts.v1` that holds the receipts of what that
  landing carries. They are fetched with
  `git fetch origin refs/notes/tick:refs/notes/tick`.
- The keys the repository allows: a `tick-keys/1` document (spec/SPEC.md, "The
  keys a company names"), the same document a company serves at
  `/.well-known/tick-keys.json`, kept in the repository at `.tick/keys.json`
  unless `--keys` names another file. It is checked as the specification checks
  it, and a key list that fails that check stops the run (exit 2): it never
  passes a repository. A task's receipt counts only under a `coordinator` key, a
  release receipt only under a `release` key. A key is never removed from the
  list, so a receipt signed under an older key still counts.
- `--since`: the last commit not checked, by default the repository's first. A
  repository born in Tick has its order as its first commit ("Tick: the order of
  project <name>"), which no task made and no note carries.

## Which tasks a landing carries, from git alone

A landing L is a merge. Its first parent P is the branch before it, and its
second parent is the landing's session. Each commit in `P..L^2` whose subject is
`Tick: refs/heads/tick/attempt/<task>/<attempt> into session/<session>` names one
task the landing carries, and its second parent is that task's delivered commit.
A release commit (`release x.y.z`) carries no task, and its note holds its
`tick.release-receipt.v1`. Any other commit on the branch's first-parent line
after `--since` did not land through Tick.

## What fails a commit

Each commit on the branch's first-parent line after `--since`, oldest first, is
checked, and every failure is printed, not only the first:

- `<sha> did not land through Tick`: it is neither a landing nor a release;
- `<sha> carries no receipt`: there is no note on it;
- `<sha>: the note is not a tick.landing-receipts.v1`, or its `commit` is not
  `<sha>`;
- `<sha>: task <task>: <reason>`: for each reason the verifier gives for a
  receipt in the note, worded as spec/SPEC.md words it;
- `<sha>: task <task>: the receipt is not of this landing`: its delivered commit
  (`receipt.delivery.delivery.commit`) is not one the landing carries;
- `<sha>: task <task>: signed by <key_id>, which this repository does not allow`;
- `<sha>: task <task> has no receipt`: a task the landing carries that no
  receipt's order (`receipt.order.order.from_task.task_id`) names; and
  `<sha>: task <task> is not carried by this landing` for a receipt whose order
  names one it does not carry.

The specification's own words for a landing's note: a reader counts a landing
only when every receipt is valid, each names the landing, and together they name
every task it carries.

## The newest landing's note comes after it

Tick writes a landing's note once its coordinator has recorded the landing,
about a minute after the push, and a push to `refs/notes/tick` starts no
workflow. So a run started by the landing's own push can find no note on the
tip although one is on its way. Before it fails a commit for having no note, the
check fetches the notes again until `--wait-seconds` (180 by default) have passed
since that commit's push; a commit older than that with no note fails at once.

## Exit codes

0 when no commit fails, 1 when one does, 2 when the check could not run: no git,
no repository, no notes ref, or a key list that fails its check.

## Shape

- Python 3.10 or later, its standard library alone. The verifier is
  `tick_verify` from the verify-py repository, copied at a named commit into
  `receipt_check/_vendor/tick_verify`, the commit written in
  `receipt_check/_vendor/VENDORED.md`, so the check installs nothing.
- `python -m receipt_check [--repo PATH] [--branch NAME] [--keys FILE]
[--since COMMIT] [--wait-seconds N]` prints one line per commit checked and a
  summary.
- `action.yml`, a composite GitHub Action: it checks out with the whole history,
  fetches the notes ref and runs the module. Inputs: `branch`, `keys`, `since`,
  `wait-seconds`.

## Checks

`python -m unittest discover -s tests -t . -v` over fixture repositories made in
temporary folders, each with a bare remote and notes the test writes itself,
signed with a key the test makes on the spot and never a real one.
