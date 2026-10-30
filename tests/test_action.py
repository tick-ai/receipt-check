"""action.yml, read without a YAML library.

The file keeps to a subset this test reads line by line: block mappings indented
by two spaces; scalars on one line after `key: `; the step list written as `- `
items; `run:` either one line or a `|` block scalar; no flow collections, anchors
or multi-line scalars besides `run: |`.
"""

from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = re.compile(r"[0-9a-f]{40}")


def read_lines() -> list[str]:
    with open(os.path.join(ROOT, "action.yml"), encoding="utf-8") as handle:
        return handle.read().splitlines()


def scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def inputs(lines: list[str]) -> dict[str, dict[str, str]]:
    """The mappings under the top-level `inputs:`: name -> {key: scalar}."""
    found: dict[str, dict[str, str]] = {}
    inside = False
    name = ""
    for line in lines:
        if re.match(r"\S", line):
            inside = line.rstrip() == "inputs:"
            continue
        if not inside:
            continue
        top = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if top:
            name = top.group(1)
            found[name] = {}
            continue
        field = re.fullmatch(r"    ([A-Za-z0-9_-]+):(?: (.*))?", line)
        if field and name:
            found[name][field.group(1)] = scalar(field.group(2) or "")
    return found


def uses(lines: list[str]) -> list[str]:
    found = []
    for line in lines:
        match = re.match(r"\s*(?:- )?uses:\s*(\S+)", line)
        if match:
            found.append(scalar(match.group(1)))
    return found


def run_lines(lines: list[str]) -> list[str]:
    """Every line of every `run:` in the file: the one-line form and the `|` block."""
    found = []
    index = 0
    while index < len(lines):
        match = re.match(r"(\s*)(?:- )?run:\s*(.*)", lines[index])
        index += 1
        if not match:
            continue
        indent, rest = len(match.group(1)), match.group(2).strip()
        if rest in ("|", "|-", ">", ">-"):
            while index < len(lines) and (not lines[index].strip() or len(lines[index]) - len(lines[index].lstrip()) > indent):
                found.append(lines[index])
                index += 1
        else:
            found.append(rest)
    return found


class ActionTest(unittest.TestCase):
    def test_names_the_four_inputs_with_their_defaults(self) -> None:
        found = inputs(read_lines())
        self.assertEqual(set(found), {"branch", "keys", "since", "wait-seconds"})
        self.assertEqual(found["branch"]["default"], "main")
        self.assertEqual(found["keys"]["default"], ".tick/keys.json")
        self.assertEqual(found["since"]["default"], "")
        self.assertEqual(found["wait-seconds"]["default"], "180")

    def test_pins_every_action_to_a_sha(self) -> None:
        used = uses(read_lines())
        self.assertTrue(used, "action.yml uses no action, but it checks out")
        for reference in used:
            name, _, ref = reference.partition("@")
            self.assertIsNotNone(SHA.fullmatch(ref), f"{name} is not pinned to a 40-character sha")
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", used)

    def test_checks_out_the_whole_history(self) -> None:
        text = "\n".join(read_lines())
        self.assertRegex(text, r"fetch-depth:\s*0\b")
        self.assertIn("refs/notes/tick", text)

    def test_no_input_is_interpolated_into_a_run_line(self) -> None:
        lines = run_lines(read_lines())
        self.assertTrue(lines, "action.yml has no run: line")
        for line in lines:
            self.assertNotRegex(line, r"\$\{\{\s*inputs\.", line)
            self.assertNotIn("${{", line)

    def test_inputs_reach_the_module_through_env_as_quoted_arguments(self) -> None:
        text = "\n".join(read_lines())
        self.assertIn("python3 -m receipt_check", text)
        for name in ("branch", "keys", "since", "wait-seconds"):
            self.assertRegex(text, r"\$\{\{ inputs\." + re.escape(name) + r" \}\}")
        for line in read_lines():
            if "${{ inputs." in line:
                self.assertRegex(line, r"^\s+INPUT_[A-Z_]+: \$\{\{ inputs\.[a-z-]+ \}\}$")
        self.assertIn('"${args[@]}"', text)


if __name__ == "__main__":
    unittest.main()
