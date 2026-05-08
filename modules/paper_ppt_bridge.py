# -*- coding: utf-8 -*-
"""Bridge from the main paper workspace to paper-ppt-agent-master."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from modules.paper_exporter import ExportedPaper
from modules.runtime_paths import get_runtime_paths
from modules.provider_registry import (
    API_FORMAT_ANTHROPIC_MESSAGES,
    API_FORMAT_GOOGLE_GENERATIVE_AI,
    HANDLER_CLAUDE,
    HANDLER_GEMINI,
    resolve_handler_name,
    normalize_api_format,
    normalize_provider_type,
)


IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")


@dataclass
class PptAgentConfig:
    provider: str
    model: str
    api_key: str
    base_url: str = ""


def run_ppt_generation_task(payload, config_mgr, project_dir, web_dir, generated_dir=None, output_root=None, progress_callback=None) -> dict:
    agent_config = _ppt_agent_config(config_mgr)
    run_dir = _prepare_agent_run_dir(project_dir, output_root)
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    tex_path = source_dir / "paper_for_ppt.tex"
    tex_path.write_text(_paper_payload_to_latex(payload, source_dir, project_dir, web_dir, generated_dir), encoding="utf-8")

    options = payload.get("pptOptions") or {}
    request = {
        "mode": "generate",
        "file_path": str(tex_path),
        "provider": agent_config.provider,
        "model": agent_config.model,
        "api_key": agent_config.api_key,
        "base_url": agent_config.base_url,
        "canvas_format": str(options.get("canvasFormat") or "ppt169"),
        "style": str(options.get("style") or "academic"),
        "num_pages": _positive_int(options.get("numPages")),
        "instruction": _ppt_instruction(payload),
        "language": str(options.get("language") or "zh"),
        "detail_level": str(options.get("detailLevel") or "normal"),
        "timeout_seconds": _positive_int(options.get("timeoutSeconds")) or 1800,
    }
    result = _run_agent_request(request, run_dir, project_dir, progress_callback=progress_callback)
    result["source_path"] = str(tex_path)
    result["title"] = _clean_text(payload.get("title") or "论文PPT")
    return result


def run_ppt_refine_task(payload, config_mgr, project_dir, output_root=None, progress_callback=None) -> dict:
    project_path = Path(str(payload.get("projectDir") or ""))
    if not project_path.is_dir():
        raise ValueError("没有可重做的 PPT 项目，请先完成一次生成")

    agent_root = _agent_work_dir(project_dir)
    try:
        project_path.resolve().relative_to((agent_root / "workspaces").resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("PPT 项目路径无效") from exc

    agent_config = _ppt_agent_config(config_mgr)
    run_dir = _prepare_agent_run_dir(project_dir, output_root)
    request = {
        "mode": "refine",
        "project_dir": str(project_path),
        "feedback": str(payload.get("feedback") or "").strip(),
        "feedback_history": payload.get("feedbackHistory") or [],
        "job_id": str(payload.get("jobId") or run_dir.name),
        "parent_job_id": str(payload.get("parentJobId") or ""),
        "provider": agent_config.provider,
        "model": agent_config.model,
        "api_key": agent_config.api_key,
        "base_url": agent_config.base_url,
        "canvas_format": str(payload.get("canvasFormat") or "ppt169"),
        "style": str(payload.get("style") or "academic"),
        "language": str(payload.get("language") or "zh"),
        "detail_level": str(payload.get("detailLevel") or "normal"),
        "timeout_seconds": _positive_int(payload.get("timeoutSeconds")) or 1800,
        "target_pages": [page for page in (_positive_int(item) for item in (payload.get("targetPages") or [])) if page],
        "allow_structure_changes": bool(payload.get("allowStructureChanges")),
    }
    if not request["feedback"]:
        raise ValueError("请填写重做要求")
    result = _run_agent_request(request, run_dir, project_dir, progress_callback=progress_callback)
    result["title"] = str(payload.get("title") or "论文PPT")
    return result


def generate_ppt_from_paper(payload, config_mgr, project_dir, web_dir, generated_dir=None, output_root=None) -> ExportedPaper:
    result = run_ppt_generation_task(payload, config_mgr, project_dir, web_dir, generated_dir, output_root)
    output_path = Path(str(result.get("output_path") or ""))
    if not output_path.is_file():
        raise RuntimeError("PPT Agent 未生成可下载的 PPTX 文件")

    title = _clean_text(payload.get("title") or "论文PPT")
    filename = f"{_safe_filename(title)}-PPT.pptx"
    return ExportedPaper(
        filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        body=output_path.read_bytes(),
    )


def read_ppt_preview(project_dir: str) -> list[dict]:
    path = Path(str(project_dir or ""))
    if not path.is_dir():
        return []
    source = path / "svg_final"
    if not any(source.glob("*.svg")):
        source = path / "svg_output"
    slides = []
    for index, svg_path in enumerate(sorted(source.glob("*.svg")), start=1):
        try:
            content = svg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        slides.append({
            "index": index,
            "name": svg_path.stem,
            "content": content,
        })
    return slides


def _prepare_agent_run_dir(project_dir, output_root=None) -> Path:
    agent_dir = _agent_work_dir(project_dir)
    if not (agent_dir / "pyproject.toml").is_file() or not (agent_dir / "backend").is_dir():
        raise ValueError("未找到 paper-ppt-agent-master，无法生成 PPT")

    uv = shutil.which("uv")
    if not uv:
        raise ValueError("未找到 uv，请先安装 uv 或把 uv 加入 PATH")
    return _new_run_dir(output_root)


def _run_agent_request(request: dict, run_dir: Path, project_dir, progress_callback=None) -> dict:
    agent_dir = _agent_work_dir(project_dir)
    uv = shutil.which("uv")
    request_path = run_dir / "request.json"
    response_path = run_dir / "response.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    runner = Path(project_dir) / "modules" / "paper_ppt_agent_runner.py"
    try:
        process = subprocess.Popen(
            [uv, "run", "--locked", "python", str(runner), str(request_path), str(response_path)],
            cwd=str(agent_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_lines = []
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            if line.startswith("PPT_AGENT_EVENT "):
                try:
                    event = json.loads(line[len("PPT_AGENT_EVENT "):])
                except Exception:
                    event = None
                if event and progress_callback:
                    progress_callback(event)
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait(timeout=5)
    finally:
        _scrub_secret_request(request_path)

    result = _read_runner_response(response_path)
    if return_code != 0 or not result.get("ok"):
        details = result.get("error") or (stderr or "".join(stdout_lines) or "PPT Agent 执行失败")
        raise RuntimeError(f"PPT 生成失败：{details}")
    return result


def _ppt_agent_config(config_mgr) -> PptAgentConfig:
    api_id = getattr(config_mgr, "active_api", "") or ""
    cfg = config_mgr.get_api_config(api_id) if hasattr(config_mgr, "get_api_config") else {}
    if not cfg:
        raise ValueError("请先在配置管理中启用一个可用的模型接口")

    api_key = str(cfg.get("key") or "").strip()
    model = str(cfg.get("model") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    if not api_key:
        raise ValueError("当前模型接口没有 API Key，无法调用 PPT Agent")
    if not model:
        raise ValueError("当前模型接口没有模型 ID，无法调用 PPT Agent")

    provider_type = normalize_provider_type(cfg.get("provider_type") or api_id)
    api_format = normalize_api_format(cfg.get("api_format") or "")
    handler = resolve_handler_name(provider_type, api_format)
    if handler == HANDLER_CLAUDE or api_format == API_FORMAT_ANTHROPIC_MESSAGES:
        provider = "anthropic"
        base_url = ""
    elif handler == HANDLER_GEMINI or api_format == API_FORMAT_GOOGLE_GENERATIVE_AI:
        provider = "gemini"
        base_url = ""
    elif provider_type == "deepseek" or "deepseek" in base_url.lower():
        provider = "deepseek"
    else:
        provider = "openai"
    return PptAgentConfig(provider=provider, model=model, api_key=api_key, base_url=base_url)


def _agent_work_dir(project_dir) -> Path:
    source = Path(project_dir) / "paper-ppt-agent-master"
    if not getattr(sys, "frozen", False):
        return source

    target = Path(get_runtime_paths().resolve_data("paper-ppt-agent-master"))
    _sync_agent_runtime_copy(source, target)
    return target


def _sync_agent_runtime_copy(source: Path, target: Path) -> None:
    if not source.is_dir():
        return

    ignore = shutil.ignore_patterns(
        ".git",
        ".runtime",
        ".venv",
        "__pycache__",
        "node_modules",
        "tests",
        "workspaces",
    )
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
    (target / "workspaces").mkdir(parents=True, exist_ok=True)


def _new_run_dir(output_root=None) -> Path:
    if output_root:
        root = Path(output_root)
    else:
        root = Path(get_runtime_paths().resolve_data("output", "paper_ppt_agent"))
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = root / stamp
    suffix = 2
    while run_dir.exists():
        run_dir = root / f"{stamp}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def _paper_payload_to_latex(payload, source_dir: Path, project_dir, web_dir, generated_dir=None) -> str:
    title = _clean_text(payload.get("title") or "论文")
    subject = _clean_text(payload.get("subject") or "")
    sections = payload.get("sections") or []
    parts = [
        r"\documentclass[UTF8]{ctexart}",
        r"\usepackage{graphicx}",
        r"\usepackage{amsmath}",
        r"\usepackage{booktabs}",
        r"\usepackage[margin=1in]{geometry}",
        rf"\title{{{_latex_escape(title)}}}",
        rf"\author{{{_latex_escape(subject or '论文工坊')}}}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
    ]

    if payload.get("outline"):
        parts.extend([r"\section*{论文大纲}", _latex_escape(str(payload.get("outline") or ""))])

    for section in sections:
        if not isinstance(section, dict):
            continue
        display_title = _clean_text(section.get("displayTitle") or section.get("title") or "")
        content = str(section.get("content") or "").strip()
        has_children = bool(section.get("hasChildren") or section.get("has_children"))
        if display_title:
            level = max(1, min(_positive_int(section.get("level")) or 1, 3))
            command = ("section", "subsection", "subsubsection")[level - 1]
            parts.append(rf"\{command}{{{_latex_escape(display_title)}}}")
        if content and not has_children:
            parts.append(_markdownish_to_latex(content, source_dir, project_dir, web_dir, generated_dir))

    parts.append(r"\end{document}")
    return "\n\n".join(part for part in parts if str(part or "").strip())


def _markdownish_to_latex(content: str, source_dir: Path, project_dir, web_dir, generated_dir=None) -> str:
    output = []
    in_table = False
    for raw_line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if in_table:
                output.append(r"\end{verbatim}")
                in_table = False
            output.append("")
            continue

        image = IMAGE_LINE_RE.match(stripped)
        if image:
            if in_table:
                output.append(r"\end{verbatim}")
                in_table = False
            figure_path = _copy_image_for_latex(image.group(2), source_dir, project_dir, web_dir, generated_dir)
            caption = _latex_escape(image.group(1) or "论文图表")
            if figure_path:
                output.extend([
                    r"\begin{figure}[htbp]",
                    r"\centering",
                    rf"\includegraphics[width=0.88\linewidth]{{{figure_path}}}",
                    rf"\caption{{{caption}}}",
                    r"\end{figure}",
                ])
            else:
                output.append(_latex_escape(f"[图片：{image.group(1) or '论文图表'}] {image.group(2)}"))
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            if in_table:
                output.append(r"\end{verbatim}")
                in_table = False
            level = min(max(len(heading.group(1)), 1), 3)
            command = ("section", "subsection", "subsubsection")[level - 1]
            output.append(rf"\{command}{{{_latex_escape(heading.group(2))}}}")
            continue

        if TABLE_LINE_RE.match(stripped):
            if not in_table:
                output.append(r"\begin{verbatim}")
                in_table = True
            output.append(stripped[:500])
            continue
        if in_table:
            output.append(r"\end{verbatim}")
            in_table = False

        bullet = re.match(r"^\s*[-*•]\s+(.+)$", stripped)
        if bullet:
            output.append(_latex_escape(f"- {bullet.group(1)}"))
        else:
            output.append(_latex_escape(stripped))
    if in_table:
        output.append(r"\end{verbatim}")
    return "\n".join(output)


def _copy_image_for_latex(src, source_dir: Path, project_dir, web_dir, generated_dir=None) -> str:
    source = _resolve_image_source(src, project_dir, web_dir, generated_dir)
    if not source:
        return ""
    images_dir = source_dir / "images"
    images_dir.mkdir(exist_ok=True)
    source_path = Path(source)
    suffix = source_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
        suffix = ".png"
    stem = _safe_filename(source_path.stem)[:36] or "image"
    dest = images_dir / f"{stem}{suffix}"
    counter = 2
    while dest.exists():
        dest = images_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    try:
        shutil.copyfile(source_path, dest)
    except OSError:
        return ""
    return dest.relative_to(source_dir).as_posix()


def _resolve_image_source(src, project_dir, web_dir, generated_dir=None):
    value = str(src or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    candidates = []
    if parsed.scheme == "file":
        candidates.append(unquote(parsed.path))
    elif parsed.scheme in {"http", "https", "data"}:
        return ""
    else:
        relative = unquote(value.lstrip("/\\"))
        if generated_dir and (relative == "generated" or relative.startswith("generated/")):
            candidates.append(os.path.join(generated_dir, relative[len("generated"):].lstrip("/\\")))
        candidates.append(os.path.join(web_dir, relative))
        candidates.append(os.path.join(project_dir, relative))
        if os.path.isabs(value):
            candidates.append(value)
    allowed_roots = [os.path.abspath(project_dir), os.path.abspath(web_dir)]
    if generated_dir:
        allowed_roots.append(os.path.abspath(generated_dir))
    for candidate in candidates:
        path = os.path.abspath(candidate)
        if os.path.isfile(path) and any(path.startswith(root) for root in allowed_roots):
            return path
    return ""


def _ppt_instruction(payload) -> str:
    options = payload.get("pptOptions") or {}
    instruction = str(options.get("instruction") or "").strip()
    default = (
        "请基于这篇已经完成的论文生成适合答辩或汇报的学术 PPT。"
        "突出研究背景、问题、方法、核心论证、图表证据、结论与不足，"
        "页面文字保持简洁，保留关键术语、数据和引用含义。"
    )
    return f"{default}\n{instruction}".strip() if instruction else default


def _read_runner_response(response_path: Path) -> dict:
    if not response_path.is_file():
        return {}
    try:
        return json.loads(response_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _scrub_secret_request(request_path: Path) -> None:
    try:
        if not request_path.is_file():
            return
        data = json.loads(request_path.read_text(encoding="utf-8"))
        data["api_key"] = "<omitted>"
        request_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _positive_int(value):
    try:
        number = int(value)
    except Exception:
        return None
    return number if number > 0 else None


def _latex_escape(value: str) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def _safe_filename(value):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text[:80] or "论文PPT"
