# receipt-check

A check any CI can run: every commit an agent lands on a branch carries a
receipt that verifies, signed by a key the repository allows. It reads the
receipts that travel with the code, the git notes Tick writes under
`refs/notes/tick`, and checks each one offline with a verifier written from
[spec/SPEC.md](spec/SPEC.md). It needs Python 3.10 or later and nothing else.

**Not usable yet.** This commit holds the contract,
[docs/design.md](docs/design.md): what is read, what fails a commit, and the
exit codes. Until the check exists, `python -m receipt_check` says it cannot
check yet and exits 2, so a workflow that runs it is never green. This line goes
with the commit that makes it check.

## Use

```sh
python -m receipt_check --branch main --keys .tick/keys.json
```

It prints one line per commit and a summary, and exits 0 when every landing
carries receipts that verify under an allowed key, 1 when one does not, and 2
when it could not check.

The keys a repository allows are a `tick-keys/1` document, the same file a
company serves at `/.well-known/tick-keys.json`:

```json
{
  "schema": "tick-keys/1",
  "keys": [{ "key_id": "0123456789abcdef", "role": "coordinator", "from": "2026-10-30" }]
}
```

## Checks

`python -m unittest discover -s tests -t . -v` runs every test; Tick runs the
same as this repository's check.

## How it was built

Every commit after this repository's first was made by an AI agent working in
Tick, and each carries a receipt that verifies. The first commit holds only the
order: the specification, the vectors and the check that decides done.

Licensed under the Apache License, Version 2.0.
