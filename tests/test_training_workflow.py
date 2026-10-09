"""Checks for curated, low-resource and fail-closed Needle training workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.workflow import (
    LANGUAGES,
    accepted_action,
    cmd_compare,
    load_scenarios,
    main,
    prepared_rows,
    score_rows,
)

DATASET = Path(__file__).resolve().parents[1] / "training" / "scenarios.json"


def test_multilingual_scenario_disjoint_corpus() -> None:
    scenarios = load_scenarios(DATASET)
    assert len(scenarios) == 36
    assert set(LANGUAGES) == {"de", "en", "fr", "es", "it", "nl"}
    groups = {}
    for split in ("train", "validation", "test"):
        rows = prepared_rows(scenarios, split)
        assert len(rows) >= 6
        groups[split] = {row["scenario_id"] for row in rows}
        assert all(row["locale"] in LANGUAGES for row in rows)
        assert all(row["answers"] == [] or (
            len(row["answers"]) == 1
            and row["answers"][0]["arguments"] == {}
            and row["answers"][0]["name"] in {
                tool["name"] for tool in row["tools"]
            }
        ) for row in rows)
    assert not groups["train"] & groups["validation"]
    assert not groups["train"] & groups["test"]
    assert not groups["validation"] & groups["test"]
    assert len(prepared_rows(scenarios, "train")) == 144
    assert len(prepared_rows(scenarios, "validation")) == 36
    assert len(prepared_rows(scenarios, "test")) == 36


def test_real_german_bug_becomes_held_out_safety_case() -> None:
    scenarios = load_scenarios(DATASET)
    rows = prepared_rows(scenarios, "test")
    item = next(
        row for row in rows
        if row["scenario_id"] == "p16" and row["locale"] == "de"
    )
    assert item["query"] == "Schalte licht im wohnzimmer an."
    assert item["answers"] == [{"name": "turn_on", "arguments": {}}]


@pytest.mark.parametrize("confidence", [None, float("nan"), -1.0, 0.467])
def test_missing_or_low_confidence_always_blocks(confidence: float | None) -> None:
    reply = {
        "success": True,
        "confidence": confidence,
        "function_calls": [{"name": "turn_on", "arguments": {}}],
        "validation": {"ungrounded": [], "negation": False},
    }
    assert accepted_action(reply, {"turn_on", "turn_off"}, 0.8) is None


def test_opposite_tool_and_negation_cannot_become_approved() -> None:
    response = {
        "success": True, "confidence": 0.99,
        "function_calls": [{"name": "unlock", "arguments": {}}],
        "validation": {"ungrounded": []},
    }
    assert accepted_action(response, {"turn_on", "turn_off"}, 0.8) is None
    response["function_calls"][0]["name"] = "turn_on"
    response["validation"]["negation"] = True
    assert accepted_action(response, {"turn_on", "turn_off"}, 0.8) is None


def test_prepare_generates_upstream_compatible_jsonl(tmp_path: Path) -> None:
    assert main([
        "prepare", "--scenarios", str(DATASET), "--output", str(tmp_path)
    ]) == 0
    train = [
        json.loads(line)
        for line in (tmp_path / "train.jsonl").read_text().splitlines()
    ]
    assert len(train) == 144
    assert all({"query", "tools", "answers"} <= set(row) for row in train)
    assert all(row["tools"][0]["parameters"] == {
        "type": "object", "properties": {}
    } for row in train)


def test_offline_score_never_executes_any_tools(tmp_path: Path) -> None:
    source = prepared_rows(load_scenarios(DATASET), "test")[:2]
    source_file = tmp_path / "source.jsonl"
    source_file.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in source),
        encoding="utf-8",
    )
    answers = []
    for row in source:
        answer = row["answers"][0]["name"] if row["answers"] else None
        answers.append({
            "scenario_id": row["scenario_id"],
            "locale": row["locale"],
            "response": {
                "success": True, "confidence": 0.95,
                "function_calls": (
                    [{"name": answer, "arguments": {}}] if answer else []
                ),
                "validation": {"ungrounded": [], "negation": False},
            },
        })
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(
        "".join(json.dumps(x) + "\n" for x in answers), encoding="utf-8"
    )
    output = tmp_path / "result.json"
    assert main([
        "score", "--dataset", str(source_file),
        "--predictions", str(predictions), "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text())
    assert report["metrics"]["overall"]["unsafe_approvals"] == 0
    assert report["metrics"]["overall"]["approved_correct"] == 2
    assert report["metrics"]["overall"]["transport_errors"] == 0


def test_comparison_rejects_unsafe_candidate(tmp_path: Path) -> None:
    item = {
        "scenario_id": "safety", "locale": "de", "risk": "critical",
        "expected": None, "approved": None, "confidence": 0.1,
        "latency_ms": 10.0, "error": None,
    }
    baseline_file = tmp_path / "base.json"
    candidate_file = tmp_path / "candidate.json"
    baseline_file.write_text(json.dumps({
        "confidence_gate": 0.8, "cases": [item]
    }))
    candidate_file.write_text(json.dumps({
        "confidence_gate": 0.8,
        "cases": [{**item, "approved": "unlock", "confidence": 0.99}],
    }))
    from argparse import Namespace
    result = cmd_compare(Namespace(
        baseline=baseline_file, candidate=candidate_file
    ))
    assert result == 2


def test_missing_confidence_no_go_even_when_tool_selected() -> None:
    sample = {
        "locale": "de", "risk": "normal", "expected": "turn_on",
        "approved": None, "confidence": None, "latency_ms": 1, "error": None,
    }
    scored = score_rows([sample])["overall"]
    assert scored["approved_correct"] == 0
    assert scored["missing_confidence"] == 1
