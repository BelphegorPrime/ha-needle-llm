"""Compact, human-readable execution trace for routed Home Assistant calls.

All values come from actual stages; never infer that a device was missing
unless Home Assistant was called and returned that specific result.
"""

from __future__ import annotations

from typing import Any


def _latency(stage: dict[str, Any]) -> float | None:
    """Return an observed stage duration, if available."""
    transport = stage.get("transport")
    if not isinstance(transport, dict):
        return None
    complete = transport.get("complete_ms")
    reset = transport.get("reset_ms", 0)
    if not isinstance(complete, (int, float)):
        return None
    return round(complete + reset, 1) if isinstance(
        reset, (int, float)
    ) else round(complete, 1)


def _stage(
    name: str,
    *,
    status: str,
    tool: str | None = None,
    duration_ms: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one stage with predictable keys for the HA trace UI."""
    item: dict[str, Any] = {"step": name, "status": status}
    if tool is not None:
        item["tool"] = tool
    if duration_ms is not None:
        item["duration_ms"] = duration_ms
    if extra:
        item.update(extra)
    return item


def build_routing_trace(
    diagnostics: dict[str, Any],
    *,
    status: str,
    stage: str,
    selected_tool: str | None = None,
    arguments: dict[str, Any] | None = None,
    home_assistant: dict[str, Any] | None = None,
    total_ms: float | None = None,
) -> dict[str, Any]:
    """Build a compact per-stage diagnostic alongside full raw diagnostics."""
    backend = diagnostics.get("backend")
    strategy = diagnostics.get("routing_strategy")
    steps: list[dict[str, Any]] = []

    if isinstance(pre := diagnostics.get("preselection"), dict):
        candidates = pre.get("candidates")
        name = pre.get("candidate")
        if name is None and isinstance(candidates, list) and len(candidates) == 1:
            name = candidates[0]
        steps.append(
            _stage(
                "tool_preselection",
                status="selected" if name else "unresolved",
                tool=name,
                duration_ms=_latency(pre),
                extra={
                    "performed_by": pre.get("source"),
                    "candidates": candidates
                    if isinstance(candidates, list)
                    else [name] if name else [],
                    "confidence": pre.get("confidence"),
                    "reasoning": pre.get("reasoning"),
                    "proposal": pre.get("model_proposal"),
                },
            )
        )
    elif isinstance(disc := diagnostics.get("discovery"), dict):
        candidates = disc.get("candidate_tools", [])
        steps.append(
            _stage(
                "tool_preselection",
                status="selected" if candidates else "unresolved",
                duration_ms=_latency(disc),
                extra={
                    "performed_by": "needle",
                    "candidates": candidates,
                },
            )
        )

    if isinstance(approval := diagnostics.get("needle_approval"), dict):
        confidence = approval.get("confidence")
        call_list = approval.get("function_calls")
        decision_tool = (
            call_list[0].get("name")
            if isinstance(call_list, list)
            and len(call_list) == 1
            and isinstance(call_list[0], dict)
            else None
        )
        approved = stage not in (
            "needle_approval",
            "preselection",
            "configuration",
        )
        steps.append(
            _stage(
                "needle_approval",
                status="approved" if approved else "rejected",
                tool=decision_tool,
                duration_ms=_latency(approval),
                extra={
                    "confidence": confidence,
                    "minimum_confidence": diagnostics.get(
                        "minimum_confidence"
                    ),
                    "agreed_with_preselection": (
                        decision_tool == approval.get("candidate")
                    ),
                    "validation": approval.get("validation", {}),
                    "reasoning": approval.get("reasoning"),
                },
            )
        )

    if isinstance(route := diagnostics.get("route"), dict):
        tool_calls = route.get("tool_calls")
        needle = route.get("needle")
        if isinstance(needle, dict):
            tool_calls = needle.get("function_calls", [])
        final_call = (
            tool_calls[0]
            if isinstance(tool_calls, list) and len(tool_calls) == 1
            else None
        )
        if isinstance(final_call, dict) and "function" in final_call:
            function = final_call.get("function")
        else:
            function = final_call
        tool_name = (
            function.get("name") if isinstance(function, dict) else None
        )
        raw_args = (
            function.get("arguments") if isinstance(function, dict) else None
        )
        steps.append(
            _stage(
                "argument_generation",
                status=(
                    "accepted"
                    if stage not in (
                        "validation",
                        "argument_generation",
                        "discovery_validation",
                    )
                    else "rejected"
                ),
                tool=tool_name,
                duration_ms=_latency(route),
                extra={
                    "generated_arguments": raw_args,
                    "finish_reason": route.get("finish_reason"),
                },
            )
        )

    if selected_tool is not None:
        steps.append(
            _stage(
                "home_assistant",
                status=(
                    "executed" if status == "success"
                    else "failed" if stage == "home_assistant_execution"
                    else "not_executed"
                ),
                tool=selected_tool,
                duration_ms=diagnostics.get("ha_execution_ms"),
                extra={
                    "validated_arguments": arguments,
                    "result": home_assistant,
                    "matched_targets": (
                        home_assistant.get("data", {}).get("success", [])
                        if isinstance(home_assistant, dict)
                        and isinstance(home_assistant.get("data"), dict)
                        else []
                    ),
                    "schema_validation": "passed"
                    if stage in ("completed", "home_assistant_execution")
                    else "not_confirmed",
                },
            )
        )

    measured = [
        item["duration_ms"] for item in steps if "duration_ms" in item
    ]
    return {
        "status": status,
        "backend": backend,
        "strategy": strategy,
        "failed_at": stage if status != "success" else None,
        "available_native_tools": diagnostics.get("available_tool_count"),
        "selected_tool": selected_tool,
        "arguments": arguments,
        "request_language": diagnostics.get("request_language"),
        "minimum_confidence": diagnostics.get("minimum_confidence"),
        "target_schema": diagnostics.get("target_schema"),
        "total_ms": (
            round(total_ms, 1) if total_ms is not None
            else round(sum(measured), 1) if measured else None
        ),
        "timing_note": (
            "Total time is measured end to end"
            if total_ms is not None
            else "Stage times only (partial trace)"
        ),
        "steps": steps,
    }
