# -*- coding: utf-8 -*-
"""Subprocess entry point for running paper-ppt-agent generation.

This script is executed with ``uv run`` from ``paper-ppt-agent-master`` so it
uses that project's dependency set without importing it into the main app.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path


def _event_payload(event) -> dict:
    data = dict(event.data or {})
    if "svg" in data:
        data["svg"] = "<omitted>"
    if "critic" in data:
        data["critic"] = "<omitted>"
    return {
        "stage": event.stage,
        "status": event.status,
        "message": event.message,
        "progress": event.progress,
        "data": data,
    }


def _emit_event(payload: dict) -> None:
    print("PPT_AGENT_EVENT " + json.dumps(payload, ensure_ascii=False), flush=True)


async def _run_generation(raw: dict) -> dict:
    from backend.orchestrator.pipeline import GenerationRequest, run_pipeline

    request = GenerationRequest(
        file_path=Path(raw["file_path"]),
        source_type="latex",
        provider=raw["provider"],
        model=raw["model"],
        api_key=raw["api_key"],
        base_url=raw.get("base_url") or None,
        canvas_format=raw.get("canvas_format") or "ppt169",
        style=raw.get("style") or "academic",
        num_pages=raw.get("num_pages") or None,
        instruction=raw.get("instruction") or "",
        language=raw.get("language") or "zh",
        detail_level=raw.get("detail_level") or "normal",
        timeout_seconds=raw.get("timeout_seconds") or None,
    )

    events = []
    output_path = ""
    project_dir = ""
    async for event in run_pipeline(request):
        payload = _event_payload(event)
        events.append(payload)
        _emit_event(payload)
        data = payload.get("data") or {}
        if data.get("project_dir"):
            project_dir = data["project_dir"]
        if data.get("output_path"):
            output_path = data["output_path"]

    if not output_path:
        raise RuntimeError("PPT Agent finished without an output_path.")

    return {
        "ok": True,
        "output_path": output_path,
        "project_dir": project_dir,
        "events": events[-12:],
    }


async def _run_refine(raw: dict) -> dict:
    from backend.orchestrator.pipeline import RefineRequest, run_refine_pipeline

    request = RefineRequest(
        project_dir=str(raw["project_dir"]),
        feedback=raw.get("feedback") or "",
        feedback_history=raw.get("feedback_history") or [],
        job_id=raw.get("job_id") or "",
        parent_job_id=raw.get("parent_job_id") or "",
        provider=raw["provider"],
        model=raw["model"],
        api_key=raw["api_key"],
        base_url=raw.get("base_url") or None,
        canvas_format=raw.get("canvas_format") or "ppt169",
        style=raw.get("style") or "academic",
        language=raw.get("language") or "zh",
        detail_level=raw.get("detail_level") or "normal",
        timeout_seconds=raw.get("timeout_seconds") or None,
        target_pages=raw.get("target_pages") or [],
        allow_structure_changes=bool(raw.get("allow_structure_changes")),
    )

    events = []
    output_path = ""
    async for event in run_refine_pipeline(request):
        payload = _event_payload(event)
        events.append(payload)
        _emit_event(payload)
        data = payload.get("data") or {}
        if data.get("output_path"):
            output_path = data["output_path"]

    if not output_path:
        raise RuntimeError("PPT Agent refine finished without an output_path.")

    return {
        "ok": True,
        "output_path": output_path,
        "project_dir": str(raw["project_dir"]),
        "events": events[-12:],
    }


async def _run(request_path: Path) -> dict:
    raw = json.loads(request_path.read_text(encoding="utf-8"))
    if raw.get("mode") == "refine":
        return await _run_refine(raw)
    return await _run_generation(raw)


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: paper_ppt_agent_runner.py <request.json> <response.json>", file=sys.stderr)
        return 2

    request_path = Path(sys.argv[1]).resolve()
    response_path = Path(sys.argv[2]).resolve()
    try:
        result = asyncio.run(_run(request_path))
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    response_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
