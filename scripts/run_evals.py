#!/usr/bin/env python3
"""Run deterministic behavioral-contract evals for the bundled skills."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "cases.yaml"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def main() -> None:
    document = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise AssertionError("evals/cases.yaml must contain a nonempty cases list")

    seen: set[str] = set()
    failures: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            failures.append("every case must be a mapping")
            continue
        case_id = case.get("id")
        skill = case.get("skill")
        prompt = case.get("prompt")
        requires = case.get("requires")
        forbids = case.get("forbids", [])
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            failures.append(f"invalid or duplicate case id: {case_id!r}")
            continue
        seen.add(case_id)
        if not isinstance(prompt, str) or not prompt.strip():
            failures.append(f"{case_id}: prompt must be a nonempty string")
        if not isinstance(skill, str):
            failures.append(f"{case_id}: skill must be a string")
            continue
        skill_path = ROOT / "skills" / skill / "SKILL.md"
        if not skill_path.is_file():
            failures.append(f"{case_id}: unknown skill {skill!r}")
            continue
        if not isinstance(requires, list) or not requires or not all(isinstance(item, str) for item in requires):
            failures.append(f"{case_id}: requires must be a nonempty string list")
            continue
        if not isinstance(forbids, list) or not all(isinstance(item, str) for item in forbids):
            failures.append(f"{case_id}: forbids must be a string list")
            continue

        body = normalize(skill_path.read_text(encoding="utf-8"))
        for required in requires:
            if normalize(required) not in body:
                failures.append(f"{case_id}: missing required contract {required!r}")
        for forbidden in forbids:
            if normalize(forbidden) in body:
                failures.append(f"{case_id}: forbidden behavior present {forbidden!r}")

    if failures:
        raise AssertionError("Behavioral eval failures:\n- " + "\n- ".join(failures))
    print(f"Passed {len(cases)} behavioral contract evals")


if __name__ == "__main__":
    main()
