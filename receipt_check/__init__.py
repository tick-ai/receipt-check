"""receipt-check: every commit an agent lands carries a receipt that verifies.

A check any CI can run on a repository whose work lands through Tick. It reads
the receipts that travel with the code, the git notes under refs/notes/tick
(tick.landing-receipts.v1), verifies each with tick_verify (vendored), and holds
each receipt's key against the keys the repository allows, a tick-keys/1
document. docs/design.md is the contract: what is read, which tasks a landing
carries, what fails a commit, and the exit codes.
"""
