# -*- coding: utf-8 -*-
"""
报告导入与段落标注解析。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import time
import threading
import uuid
import zlib
from dataclasses import asdict, dataclass, field


RISK_ORDER = {
    'safe': 0,
    'low': 1,
    'medium': 2,
    'high': 3,
}

RISK_BY_COLOR = {
    'red': 'high',
    'orange': 'medium',
    'purple': 'low',
    'black': 'safe',
    'gray': 'safe',
    'unknown': 'safe',
}

SOURCE_COLOR_PROTOTYPES = {
    'red': ((255, 0, 0), (192, 0, 0), (214, 64, 64)),
    'orange': ((237, 125, 49), (255, 165, 0), (255, 192, 0)),
    'purple': ((112, 48, 160), (128, 0, 128), (153, 102, 204), (192, 0, 255)),
    'black': ((0, 0, 0), (21, 22, 26), (64, 64, 64)),
    'gray': ((96, 96, 96), (128, 128, 128), (166, 166, 166), (208, 208, 208)),
}

REFERENCE_HEADING_RE = re.compile(r'^(参考文献|引用文献|参考资料)\s*[:：]?$')
CAPTION_RE = re.compile(r'^(图|表)\s*\d+|^(Figure|Table)\s+\d+', re.IGNORECASE)
HEADING_RE = re.compile(
    r'^(#{1,6}\s+)'
    r'|^(第[一二三四五六七八九十百千万\d]+[章节部分篇])'
    r'|^(\d+(?:\.\d+){0,3}\s+)'
    r'|^([一二三四五六七八九十百千万\d]+[、.．)])'
    r'|^(（[一二三四五六七八九十百千万\d]+）)'
)
PERCENT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[％%]')
AI_RATE_RE = re.compile(
    r'(?:'
    r'AI(?:检测|生成|疑似)?率'
    r'|AIGC(?:检测|生成|疑似)?率'
    r'|疑似AI率'
    r')\s*(?:为|是|[:：])?\s*(\d+(?:\.\d+)?)\s*[％%]?',
    re.IGNORECASE,
)
REPEAT_RATE_RE = re.compile(
    r'(?:重复率|查重率|文字复制比|总文字复制比|相似度)\s*(?:为|是|[:：])?\s*(\d+(?:\.\d+)?)\s*[％%]?',
    re.IGNORECASE,
)
AI_BADGE_RE = re.compile(
    r'(?:AI\s*[:：]?\s*(\d+(?:\.\d+)?)\s*[％%]|(\d+(?:\.\d+)?)\s*[％%]\s*AI)',
    re.IGNORECASE,
)
REPEAT_BADGE_RE = re.compile(
    r'(?:查重|相似度|重复率|查重率|文字复制比|similarity|repeat(?:\s*rate)?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*[％%]?',
    re.IGNORECASE,
)
AI_BADGE_PREFIX_RE = re.compile(
    r'^\s*(?:AI\s*[:：]?\s*(\d+(?:\.\d+)?)\s*[％%]?|(\d+(?:\.\d+)?)\s*[％%]?\s*AI)\s*',
    re.IGNORECASE,
)
REPEAT_BADGE_PREFIX_RE = re.compile(
    r'^\s*(?:查重|相似度|重复率|查重率|文字复制比|similarity|repeat(?:\s*rate)?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*[％%]?\s*',
    re.IGNORECASE,
)
REPORT_KIND_BY_PAGE = {
    'ai_reduce': 'aigc',
    'plagiarism': 'plagiarism',
}
AIGC_REPORT_HINTS = (
    'aigc检测报告',
    'aigc总体疑似度',
    'aigc生成疑似度',
    '高度疑似aigc',
    '中度疑似aigc',
    '轻度疑似aigc',
    'aigc检测(',
)
PLAGIARISM_REPORT_HINTS = (
    '查重报告',
    '查重结果(相似度)',
    '句子相似度分布图',
    '本地库相似资源列表',
    '文字复制比',
    '查重(',
)
HIGH_RISK_RE = re.compile(r'高风险|严重|标红|重度|高重复|高疑似|high\s*risk|marked\s*red', re.IGNORECASE)
MEDIUM_RISK_RE = re.compile(r'中风险|中度|疑似|需关注|较高|medium\s*risk|suspect', re.IGNORECASE)
LOW_RISK_RE = re.compile(r'低风险|轻度|一般|较低|low\s*risk', re.IGNORECASE)
TOKEN_RE = re.compile(r'[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}|\d+')
SEARCH_CHAR_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9%％\[\]（）()：:，,。.!?、\-]')
COMMON_LATIN_STOPWORDS = {
    'this', 'that', 'these', 'those', 'is', 'are', 'was', 'were', 'be', 'been',
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'for', 'in', 'on', 'with',
    'paragraph', 'report', 'rate', 'score', 'risk',
}
PARAGRAPH_END_RE = re.compile(r'[。！？?!；;][”」』）\]]*$')
PARAGRAPH_INDENT_RE = re.compile(r'^[ \t\u3000]+')
DOCX_HIGHLIGHT_NAME_MAP = {
    'red': 'high',
    'darkred': 'high',
    'orange': 'medium',
    'yellow': 'medium',
    'violet': 'low',
    'pink': 'low',
    'purple': 'low',
    'black': 'safe',
}
COLOR_PROTOTYPES = {
    'high': ((255, 0, 0), (192, 0, 0), (214, 64, 64)),
    'medium': ((237, 125, 49), (255, 165, 0), (255, 192, 0)),
    'low': ((112, 48, 160), (128, 0, 128), (153, 102, 204)),
    'safe': ((0, 0, 0), (21, 22, 26), (64, 64, 64)),
}
LEGEND_COLOR_NAME_MAP = {
    '红色': 'red',
    '橙色': 'orange',
    '橘色': 'orange',
    '紫色': 'purple',
    '黑色': 'black',
    '灰色': 'gray',
    'red': 'red',
    'orange': 'orange',
    'purple': 'purple',
    'black': 'black',
    'gray': 'gray',
    'grey': 'gray',
}
LEGEND_LINE_RE = re.compile(
    r'^\s*(?P<color>红色|橙色|橘色|紫色|黑色|灰色|red|orange|purple|black|gray|grey)\s*[：:]\s*(?P<body>.+?)\s*$',
    re.IGNORECASE,
)
LEGEND_RANGE_BETWEEN_RE = re.compile(
    r'(?P<lower>\d+(?:\.\d+)?)\s*%?\s*(?:~|-|—|–|至)\s*(?P<upper>\d+(?:\.\d+)?)\s*%?'
)
LEGEND_RANGE_ABOVE_RE = re.compile(
    r'(?P<lower>\d+(?:\.\d+)?)\s*%?\s*(?:以上|及以上|>=|>)'
)
LEGEND_RANGE_BELOW_RE = re.compile(
    r'(?P<upper>\d+(?:\.\d+)?)\s*%?\s*(?:以下|及以下|<=|<)'
)
META_FRAGMENT_RE = re.compile(
    r'^(?:'
    r'AI(?:检测)?率|AIGC(?:疑似)?率|疑似AI率|重复率|查重率|文字复制比|总文字复制比|'
    r'检测结论|检测结果|检测报告|综合结论|总体结论|风险等级|相似来源|来源|说明|目录|摘要'
    r')',
    re.IGNORECASE,
)
PDF_NUMBER_RE = r'-?(?:\d+\.\d+|\d+|\.\d+)'
PDF_RGB_OP_RE = re.compile(
    rf'(?<![\w.])(?P<r>{PDF_NUMBER_RE})\s+(?P<g>{PDF_NUMBER_RE})\s+(?P<b>{PDF_NUMBER_RE})\s+(?:rg|RG)\b'
)
PDF_GRAY_OP_RE = re.compile(
    rf'(?<![\w.])(?P<gray_value>{PDF_NUMBER_RE})\s+(?:g|G)\b'
)
PDF_TEXT_MOVE_OP_RE = re.compile(
    rf'(?<![\w.]){PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+(?:Td|TD)\b'
)
PDF_TEXT_MATRIX_OP_RE = re.compile(
    rf'(?<![\w.]){PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+{PDF_NUMBER_RE}\s+Tm\b'
)
PDF_TEXT_OBJECT_BEGIN_RE = re.compile(r'(?<!\S)BT(?!\S)')
PDF_TEXT_OBJECT_END_RE = re.compile(r'(?<!\S)ET(?!\S)')
PDF_TEXT_SHOW_MARKERS = (b'Tj', b'TJ', b"'", b'"')
PDF_TEXT_OBJECT_MARKERS = (b'BT', b'ET')
PDF_TEXT_LAYOUT_MARKERS = (b'Td', b'TD', b'Tm', b'Tf', b'T*')
PDF_IMAGE_MARKERS = (b'/Subtype /Image', b'/Subtype/Image')
PDF_FONT_MARKERS = (
    b'/Type /Font',
    b'/Type/Font',
    b'/Type /FontDescriptor',
    b'/FontDescriptor',
    b'/FontFile',
    b'/FontFile2',
    b'/FontFile3',
    b'/CIDToGIDMap',
    b'/ToUnicode',
    b'/Type /CMap',
    b'/Type/CMap',
    b'/CMapName',
    b'/CIDSystemInfo',
    b'/Subtype /TrueType',
    b'/Subtype/TrueType',
    b'/Subtype /Type1',
    b'/Subtype/Type1',
    b'/Subtype /OpenType',
    b'/Subtype/OpenType',
    b'/Subtype /CIDFontType2',
    b'/Subtype/CIDFontType2',
    b'/Subtype /CIDFontType0',
    b'/Subtype/CIDFontType0',
    b'/Widths',
    b'/DW ',
)
PDF_OBJECT_STREAM_MARKERS = (
    b'/Type /ObjStm',
    b'/Type/ObjStm',
    b'/Type /XRef',
    b'/Type/XRef',
    b'/Type /Metadata',
    b'/Type/Metadata',
)
PDF_STREAM_SLOW_MS = 200


@dataclass
class DocumentParagraph:
    paragraph_id: str
    kind: str
    text: str
    start: int
    end: int
    section_path: str = ''
    level: int = 0


@dataclass
class ParagraphAnnotation:
    paragraph_id: str
    section_path: str
    start: int
    end: int
    risk_level: str
    ai_score: float | None = None
    repeat_score: float | None = None
    duplicate_status: str | None = None
    source_color: str = 'unknown'
    include_in_run: bool = True
    source_excerpt: str = ''
    is_auto_generated: bool = True
    is_user_modified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict | None) -> 'ParagraphAnnotation | None':
        if not isinstance(payload, dict):
            return None
        return cls(
            paragraph_id=str(payload.get('paragraph_id', '') or ''),
            section_path=str(payload.get('section_path', '') or ''),
            start=int(payload.get('start', -1) or -1),
            end=int(payload.get('end', -1) or -1),
            risk_level=_normalize_risk_level(payload.get('risk_level')),
            ai_score=_coerce_optional_float(payload.get('ai_score')),
            repeat_score=_coerce_optional_float(payload.get('repeat_score')),
            duplicate_status=_normalize_duplicate_status(payload.get('duplicate_status')),
            source_color=_normalize_source_color(payload.get('source_color')),
            include_in_run=bool(payload.get('include_in_run', True)),
            source_excerpt=str(payload.get('source_excerpt', '') or ''),
            is_auto_generated=bool(payload.get('is_auto_generated', True)),
            is_user_modified=bool(payload.get('is_user_modified', False)),
        )


@dataclass
class ImportSession:
    session_id: str
    page_kind: str
    report_kind: str
    vendor: str
    file_format: str
    file_path: str
    file_name: str
    original_text: str
    report_text: str = ''
    annotations: list[ParagraphAnnotation] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)
    matched_count: int = 0
    total_body_paragraphs: int = 0
    unmatched_blocks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload['annotations'] = [item.to_dict() for item in self.annotations]
        return payload

    @classmethod
    def from_dict(cls, payload: dict | None) -> 'ImportSession | None':
        if not isinstance(payload, dict):
            return None
        annotations = []
        for item in payload.get('annotations', []):
            annotation = ParagraphAnnotation.from_dict(item)
            if annotation is not None:
                annotations.append(annotation)
        return cls(
            session_id=str(payload.get('session_id', '') or ''),
            page_kind=str(payload.get('page_kind', '') or ''),
            report_kind=str(payload.get('report_kind', '') or ''),
            vendor=str(payload.get('vendor', '') or ''),
            file_format=str(payload.get('file_format', '') or ''),
            file_path=str(payload.get('file_path', '') or ''),
            file_name=str(payload.get('file_name', '') or ''),
            original_text=str(payload.get('original_text', '') or ''),
            report_text=str(payload.get('report_text', '') or ''),
            annotations=annotations,
            parse_notes=[str(item or '') for item in payload.get('parse_notes', []) if str(item or '').strip()],
            matched_count=int(payload.get('matched_count', 0) or 0),
            total_body_paragraphs=int(payload.get('total_body_paragraphs', 0) or 0),
            unmatched_blocks=[str(item or '') for item in payload.get('unmatched_blocks', []) if str(item or '').strip()],
        )


def split_document_paragraphs(text: str) -> list[DocumentParagraph]:
    """按段落块拆分正文，并记录原文偏移。"""
    content = normalize_block_text(text)
    if not content:
        return []

    blocks = []
    heading_stack: list[tuple[int, str]] = []
    in_reference_section = False
    block_index = 0

    for start, end in _collect_block_spans(content):
        block_text = content[start:end]
        if block_text.strip():
            paragraph = _build_document_paragraph(
                block_text,
                start,
                end,
                block_index,
                heading_stack,
                in_reference_section,
            )
            blocks.append(paragraph)
            if paragraph.kind == 'heading':
                _update_heading_stack(heading_stack, paragraph.level, paragraph.text.strip())
                if REFERENCE_HEADING_RE.match(paragraph.text.strip()):
                    in_reference_section = True
            elif paragraph.kind == 'body' and in_reference_section:
                paragraph.kind = 'reference'
        block_index += 1

    return blocks


class ReportImportEngine:
    """报告导入入口。"""

    WORKER_FLAG = '--report-import-worker'
    WORKER_MODULE = 'modules.report_importer_worker'
    WORKER_TIMEOUT_SECONDS = 240

    def __init__(self, log_callback=None):
        self.log_callback = log_callback

    def parse(self, path: str, page_kind: str, original_text: str) -> ImportSession:
        started_at = time.perf_counter()
        if not str(original_text or '').strip():
            raise ValueError('导入报告前请先准备原文区正文内容')
        page_kind = _validate_import_page_kind(page_kind)

        normalized_path = os.path.abspath(path)
        ext = os.path.splitext(normalized_path)[1].lower()
        if ext not in {'.docx', '.pdf'}:
            raise RuntimeError('当前仅支持导入 PDF 打印版与 DOCX 颜色标记版报告')

        self._log(
            f'[report_import] parse_begin page={page_kind} ext={ext.lstrip(".")} '
            f'path={normalized_path} text_chars={len(original_text)}'
        )

        extract_started_at = time.perf_counter()

        if ext == '.docx':
            report_text, fragments, notes = self._extract_docx_report(normalized_path, page_kind)
        else:
            report_text, fragments, notes = self._extract_pdf_report(normalized_path, page_kind)

        self._log(
            f'[report_import] extract_done ext={ext.lstrip(".")} fragments={len(fragments)} '
            f'report_chars={len(report_text)} elapsed={time.perf_counter() - extract_started_at:.3f}s'
        )

        notes = list(notes or [])
        report_kind = _detect_report_kind(normalized_path, report_text)
        _validate_report_kind_for_page(page_kind, report_kind)
        if report_kind == 'unknown':
            notes.insert(0, '未能明确识别报告类型，解析结果可能不完整。')

        vendor = _detect_vendor(normalized_path, report_text)
        match_started_at = time.perf_counter()
        annotations, matched_count, unmatched = _build_annotations(
            page_kind=page_kind,
            original_text=original_text,
            report_text=report_text,
            fragments=fragments,
        )
        self._log(
            f'[report_import] match_done annotations={len(annotations)} matched={matched_count} '
            f'unmatched={len(unmatched)} elapsed={time.perf_counter() - match_started_at:.3f}s'
        )
        body_count = len([item for item in split_document_paragraphs(original_text) if item.kind == 'body'])

        session = ImportSession(
            session_id=uuid.uuid4().hex,
            page_kind=page_kind,
            report_kind=report_kind,
            vendor=vendor,
            file_format=ext.lstrip('.'),
            file_path=normalized_path,
            file_name=os.path.basename(normalized_path),
            original_text=normalize_block_text(original_text),
            report_text=report_text,
            annotations=annotations,
            parse_notes=notes,
            matched_count=matched_count,
            total_body_paragraphs=body_count,
            unmatched_blocks=unmatched,
        )
        self._log(
            f'[report_import] parse_done vendor={vendor} total_body={body_count} matched={matched_count} '
            f'elapsed={time.perf_counter() - started_at:.3f}s'
        )
        return session

    def parse_in_subprocess(self, path: str, page_kind: str, original_text: str, *, timeout=None) -> ImportSession:
        timeout_seconds = int(timeout or self.WORKER_TIMEOUT_SECONDS)
        payload = {
            'path': _strip_unpaired_surrogates(path),
            'page_kind': _strip_unpaired_surrogates(page_kind),
            'original_text': _strip_unpaired_surrogates(original_text),
        }
        command = self._build_worker_command()
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        started_at = time.perf_counter()
        self._log(
            f'[report_import] worker_spawn page={page_kind} timeout={timeout_seconds}s '
            f'path={os.path.abspath(path)}'
        )

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creationflags,
        )
        self._log(f'[report_import] worker_pid={process.pid}')
        stdout_parts = []
        stderr_parts = []
        last_worker_log = {'line': ''}

        def read_stdout():
            if process.stdout is None:
                return
            try:
                content = process.stdout.read()
                if content:
                    stdout_parts.append(content)
            finally:
                process.stdout.close()

        def read_stderr():
            if process.stderr is None:
                return
            try:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    stderr_parts.append(line)
                    last_worker_log['line'] = line.strip()
                    self._relay_worker_log_line(line)
            finally:
                process.stderr.close()

        stdout_thread = threading.Thread(target=read_stdout, name='report-import-stdout', daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name='report-import-stderr', daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            if process.stdin is not None:
                process.stdin.write(_strip_unpaired_surrogates(json.dumps(payload, ensure_ascii=False)))
                process.stdin.close()
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            stdout = ''.join(stdout_parts)
            stderr = ''.join(stderr_parts)
            self._log(
                f'[report_import] worker_timeout timeout={timeout_seconds}s path={os.path.abspath(path)} '
                f'last_worker_log={json.dumps(last_worker_log["line"], ensure_ascii=False)}',
                level='ERROR',
            )
            raise RuntimeError(f'报告解析超时，已超过 {timeout_seconds} 秒') from exc
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        stdout = ''.join(stdout_parts)
        stderr = ''.join(stderr_parts)
        elapsed = time.perf_counter() - started_at
        self._log(f'[report_import] worker_exit code={process.returncode} elapsed={elapsed:.3f}s')

        response = self._parse_worker_response(stdout)
        if process.returncode != 0 or not response.get('ok'):
            message = str(response.get('error') or '').strip()
            if not message:
                message = stderr.strip() or '报告解析子进程执行失败'
            raise RuntimeError(message)

        session = ImportSession.from_dict(response.get('session'))
        if session is None:
            raise RuntimeError('报告解析结果为空，无法恢复导入会话')

        self._log(
            f'[report_import] worker_success annotations={len(session.annotations)} '
            f'matched={session.matched_count}/{session.total_body_paragraphs}'
        )
        return session

    def _extract_docx_report(self, path: str, page_kind: str):
        try:
            import docx
        except ImportError as exc:
            raise RuntimeError('缺少 python-docx，无法解析 DOCX 报告') from exc

        document = docx.Document(path)
        fragments = []
        paragraphs = []
        notes = []
        fragment_order = 0

        for paragraph in document.paragraphs:
            full_text = ''.join(run.text for run in paragraph.runs).strip()
            if not full_text:
                continue
            paragraphs.append(full_text)
            clean_full_text = _strip_leading_report_badge(full_text, page_kind)
            paragraph_risk = 'safe'
            paragraph_source_color = 'unknown'
            paragraph_score = _extract_primary_score(full_text, page_kind)
            paragraph_kind = _classify_report_fragment(clean_full_text)
            for run in paragraph.runs:
                run_text = str(run.text or '').strip()
                if not run_text:
                    continue
                clean_run_text = _strip_leading_report_badge(run_text, page_kind)
                style_info = _docx_style_from_run(run)
                risk_level = style_info['risk_level']
                source_color = style_info['source_color']
                run_score = _extract_primary_score(run_text, page_kind)
                if RISK_ORDER.get(risk_level, 0) > RISK_ORDER.get(paragraph_risk, 0):
                    paragraph_risk = risk_level
                    if source_color != 'unknown':
                        paragraph_source_color = source_color
                elif paragraph_source_color == 'unknown' and source_color != 'unknown':
                    paragraph_source_color = source_color
                if risk_level != 'safe' or len(clean_run_text) >= 8:
                    fragments.append(
                        {
                            'text': clean_run_text,
                            'risk_level': risk_level,
                            'score': run_score if run_score is not None else paragraph_score,
                            'duplicate_status': _risk_to_duplicate_status(risk_level),
                            'source_color': source_color,
                            'kind': paragraph_kind if paragraph_kind == 'body' else 'meta',
                            'order': fragment_order,
                        }
                    )
                    fragment_order += 1
            fragments.append(
                {
                    'text': clean_full_text,
                    'risk_level': _infer_risk_from_text(full_text, paragraph_risk),
                    'score': paragraph_score,
                    'duplicate_status': _infer_duplicate_status(full_text, paragraph_risk),
                    'source_color': paragraph_source_color,
                    'kind': paragraph_kind,
                    'order': fragment_order,
                }
            )
            fragment_order += 1

        if not paragraphs:
            raise RuntimeError('DOCX 报告未解析到有效文本内容')

        if not any(item.get('risk_level') != 'safe' for item in fragments):
            notes.append('未识别到明确颜色标注，已按文本内容与比例信息生成初次风险判断。')

        self._log(
            f'[report_import] docx_extract paragraphs={len(paragraphs)} fragments={len(fragments)} '
            f'notes={len(notes)}'
        )
        return '\n\n'.join(paragraphs), fragments, notes

    def _extract_pdf_report(self, path: str, page_kind: str):
        parser_name = 'builtin'
        try:
            text, fragments, used_color_detection = _extract_pdf_fragments_with_pymupdf(
                path,
                page_kind,
                log_callback=self._log,
            )
            parser_name = 'pymupdf'
        except Exception as exc:
            self._log(
                f'[report_import] pymupdf_fallback reason={str(exc)}',
                level='WARN',
            )
            text, fragments, used_color_detection = _extract_pdf_fragments(
                path,
                page_kind,
                log_callback=self._log,
            )
        if not text.strip():
            raise RuntimeError('PDF 报告未解析到有效文本内容')
        notes = []
        if parser_name == 'pymupdf':
            notes.append('当前 PDF 初次标注已结合版面文本与段落角标信息生成。')
        elif used_color_detection:
            notes.append('当前 PDF 初次标注已结合打印色与文本内容生成。')
        else:
            notes.append('当前 PDF 解析为无额外依赖版本，初次标注优先依据文本匹配与比例信息生成。')
        self._log(
            f'[report_import] pdf_extract parser={parser_name} fragments={len(fragments)} text_chars={len(text)} '
            f'color_detection={used_color_detection}'
        )
        return text, fragments, notes

    @classmethod
    def _build_worker_command(cls):
        if getattr(sys, 'frozen', False):
            return [sys.executable, cls.WORKER_FLAG]
        return [sys.executable, '-m', cls.WORKER_MODULE]

    @staticmethod
    def _parse_worker_response(stdout: str) -> dict:
        content = str(stdout or '').strip()
        if not content:
            return {'ok': False, 'error': '报告解析子进程未返回结果'}
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'报告解析子进程返回了不可识别的数据：{exc}') from exc
        if not isinstance(payload, dict):
            raise RuntimeError('报告解析子进程返回的数据结构无效')
        return payload

    def _relay_worker_logs(self, stderr: str):
        for line in str(stderr or '').splitlines():
            self._relay_worker_log_line(line)

    def _relay_worker_log_line(self, line: str):
        text = str(line or '').strip()
        if not text:
            return
        level = 'INFO'
        content = text
        match = re.match(r'^\[(DEBUG|INFO|WARN|ERROR)\]\s*(.*)$', text)
        if match:
            level = match.group(1)
            content = match.group(2)
        self._log(content, level=level)

    def _log(self, message, level='INFO'):
        if callable(self.log_callback):
            self.log_callback(str(message or ''), level=level)


def normalize_block_text(text: str) -> str:
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    return _strip_unpaired_surrogates(normalized)


def _strip_unpaired_surrogates(text: str) -> str:
    value = str(text or '')
    # Some imported files may contain lone surrogate code points which cannot be
    # encoded to UTF-8 in IPC/log serialization; replace them proactively.
    return value.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


def _emit_report_import_log(log_callback, message, level='INFO'):
    if callable(log_callback):
        log_callback(str(message or ''), level=level)


def _collect_block_spans(content: str) -> list[tuple[int, int]]:
    spans = []
    current_start = None
    current_lines = []

    for line_text, start, end in _iter_lines_with_offsets(content):
        stripped = line_text.strip()
        if not stripped:
            if current_lines:
                spans.append((current_start, current_lines[-1][2]))
                current_start = None
                current_lines = []
            continue

        if current_start is None:
            current_start = start
        elif _should_split_before_line(current_lines, line_text):
            spans.append((current_start, current_lines[-1][2]))
            current_start = start
            current_lines = []

        current_lines.append((line_text, start, end))

    if current_lines:
        spans.append((current_start, current_lines[-1][2]))

    return spans


def _iter_lines_with_offsets(content: str):
    start = 0
    for index, char in enumerate(content):
        if char != '\n':
            continue
        yield content[start:index], start, index
        start = index + 1
    yield content[start:], start, len(content)


def _should_split_before_line(current_lines, next_line: str) -> bool:
    previous_line = current_lines[-1][0].rstrip()
    next_value = str(next_line or '')
    next_stripped = next_value.strip()
    if not previous_line or not next_stripped:
        return False

    if REFERENCE_HEADING_RE.match(next_stripped) or _heading_level(next_stripped) or CAPTION_RE.match(next_stripped):
        return True

    if PARAGRAPH_INDENT_RE.match(next_value):
        return True

    if re.match(r'^\s*(\[\d+\]|\d+[.、])', next_stripped):
        return True

    if not PARAGRAPH_END_RE.search(previous_line):
        return False

    current_block_text = '\n'.join(item[0] for item in current_lines).strip()
    prev_len = len(_normalize_search_text(previous_line))
    next_len = len(_normalize_search_text(next_stripped))
    block_len = len(_normalize_search_text(current_block_text))

    if prev_len >= 70:
        return True
    if block_len >= 120 and next_len >= 28:
        return True
    if prev_len >= 45 and next_len >= 60:
        return True
    return False


def _build_document_paragraph(text, start, end, block_index, heading_stack, in_reference_section):
    stripped = str(text or '').strip()
    if REFERENCE_HEADING_RE.match(stripped):
        return DocumentParagraph(
            paragraph_id=f'paragraph-{block_index}',
            kind='heading',
            text=text,
            start=start,
            end=end,
            section_path=' > '.join(item[1] for item in heading_stack),
            level=1,
        )
    if _heading_level(stripped):
        return DocumentParagraph(
            paragraph_id=f'paragraph-{block_index}',
            kind='heading',
            text=text,
            start=start,
            end=end,
            section_path=' > '.join(item[1] for item in heading_stack),
            level=_heading_level(stripped),
        )
    if CAPTION_RE.match(stripped):
        kind = 'caption'
    elif in_reference_section or re.match(r'^\s*(\[\d+\]|\d+[.、])', stripped):
        kind = 'reference'
    else:
        kind = 'body'
    return DocumentParagraph(
        paragraph_id=f'paragraph-{block_index}',
        kind=kind,
        text=text,
        start=start,
        end=end,
        section_path=' > '.join(item[1] for item in heading_stack),
        level=0,
    )


def _update_heading_stack(stack, level, title):
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def _heading_level(text: str) -> int:
    if not text or not HEADING_RE.match(text):
        return 0
    if text.startswith('#'):
        return min(text.count('#'), 6)
    if re.match(r'^第[一二三四五六七八九十百千万\d]+章', text):
        return 1
    if re.match(r'^第[一二三四五六七八九十百千万\d]+节', text):
        return 2
    if re.match(r'^\d+\s+', text):
        return 1
    if re.match(r'^\d+\.\d+\s+', text):
        return 2
    if re.match(r'^\d+\.\d+\.\d+\s+', text):
        return 3
    if re.match(r'^[一二三四五六七八九十百千万\d]+[、.．)]', text):
        return 2
    if re.match(r'^（[一二三四五六七八九十百千万\d]+）', text):
        return 3
    return 1


def _build_annotations(*, page_kind, original_text, report_text, fragments):
    annotations = []
    matched_count = 0
    unmatched = []
    paragraphs = split_document_paragraphs(original_text)
    body_paragraphs = [item for item in paragraphs if item.kind == 'body']
    used_fragment_indexes = set()
    min_fragment_index = 0

    for paragraph in body_paragraphs:
        matched_fragment, matched_index, matched_score = _match_fragment(
            paragraph.text,
            fragments,
            report_text,
            used_fragment_indexes=used_fragment_indexes,
            min_index=min_fragment_index,
        )
        annotation = _build_annotation_for_page(page_kind, paragraph, report_text, matched_fragment, matched_score)
        if _annotation_counts_as_marked(page_kind, annotation, matched_fragment):
            matched_count += 1
            if matched_index is not None:
                used_fragment_indexes.add(matched_index)
                min_fragment_index = max(min_fragment_index, matched_index + 1)
        else:
            unmatched.append(paragraph.text.strip()[:80])
        annotations.append(annotation)

    return annotations, matched_count, unmatched[:12]


def _build_annotation_for_page(page_kind, paragraph, report_text, matched_fragment, matched_score):
    if page_kind == 'ai_reduce':
        return _build_ai_annotation(paragraph, report_text, matched_fragment, matched_score)
    if page_kind == 'plagiarism':
        return _build_plagiarism_annotation(paragraph, report_text, matched_fragment, matched_score)
    raise ValueError(f'不支持的报告导入页面类型：{page_kind or "unknown"}')


def _annotation_counts_as_marked(page_kind, annotation, matched_fragment):
    if matched_fragment is None:
        return False
    if page_kind == 'plagiarism':
        if annotation.risk_level != 'safe':
            return True
        score = _coerce_optional_float(annotation.repeat_score)
        return score is not None and score > 0
    return True


def _build_ai_annotation(paragraph, report_text, matched_fragment, matched_score):
    matched_text = ''
    ai_score = None
    risk_level = 'safe'
    source_color = 'unknown'

    if matched_fragment is not None:
        matched_text = _strip_leading_report_badge(str(matched_fragment.get('text', '') or '').strip(), 'ai_reduce')
        ai_score = _coerce_optional_float(matched_fragment.get('score'))
        risk_level = _normalize_risk_level(matched_fragment.get('risk_level'))
        source_color = _normalize_source_color(matched_fragment.get('source_color'))

    if ai_score is None:
        ai_score = _extract_nearby_score(report_text, matched_text or paragraph.text, page_kind='ai_reduce')
    if ai_score is None and matched_text:
        ai_score = _extract_nearby_score(report_text, paragraph.text, page_kind='ai_reduce')

    has_fragment_score = False
    if matched_fragment is not None:
        has_fragment_score = _coerce_optional_float(matched_fragment.get('score')) is not None

    if risk_level == 'safe' and ai_score is not None:
        if matched_fragment is None or has_fragment_score or matched_score >= 0.35:
            risk_level = _score_to_ai_risk(ai_score)

    if not matched_text:
        matched_text = paragraph.text.strip()[:80]

    return ParagraphAnnotation(
        paragraph_id=paragraph.paragraph_id,
        section_path=paragraph.section_path,
        start=paragraph.start,
        end=paragraph.end,
        risk_level=risk_level,
        ai_score=round(float(ai_score), 1) if ai_score is not None else None,
        repeat_score=None,
        duplicate_status=None,
        source_color=source_color,
        include_in_run=risk_level != 'safe',
        source_excerpt=matched_text,
        is_auto_generated=True,
        is_user_modified=False,
    )


def _build_plagiarism_annotation(paragraph, report_text, matched_fragment, matched_score):
    matched_text = ''
    repeat_score = None
    risk_level = 'safe'
    duplicate_status = 'safe'
    source_color = 'unknown'
    annotation_start = paragraph.start
    annotation_end = paragraph.end

    if matched_fragment is not None:
        matched_text = _strip_leading_report_badge(str(matched_fragment.get('text', '') or '').strip(), 'plagiarism')
        repeat_score = _coerce_optional_float(matched_fragment.get('score'))
        risk_level = _normalize_risk_level(matched_fragment.get('risk_level'))
        duplicate_status = _normalize_duplicate_status(matched_fragment.get('duplicate_status'))
        source_color = _normalize_source_color(matched_fragment.get('source_color'))
        resolved_range = _resolve_annotation_excerpt_range(paragraph, matched_text)
        if resolved_range is not None:
            annotation_start, annotation_end = resolved_range

    has_fragment_score = False
    if matched_fragment is not None:
        has_fragment_score = _coerce_optional_float(matched_fragment.get('score')) is not None

    allow_nearby_score = matched_fragment is not None and (risk_level != 'safe' or has_fragment_score or matched_score >= 0.35)
    if repeat_score is None and allow_nearby_score:
        repeat_score = _extract_nearby_score(report_text, matched_text or paragraph.text, page_kind='plagiarism')
    if repeat_score is None and matched_text and allow_nearby_score:
        repeat_score = _extract_nearby_score(report_text, paragraph.text, page_kind='plagiarism')

    if risk_level == 'safe' and repeat_score is not None:
        if matched_fragment is None or has_fragment_score or matched_score >= 0.35:
            risk_level = _score_to_repeat_risk(repeat_score)

    if duplicate_status in {'none', None}:
        duplicate_status = _risk_to_duplicate_status(risk_level)

    if not matched_text:
        matched_text = paragraph.text.strip()[:80]

    return ParagraphAnnotation(
        paragraph_id=paragraph.paragraph_id,
        section_path=paragraph.section_path,
        start=annotation_start,
        end=annotation_end,
        risk_level=risk_level,
        ai_score=None,
        repeat_score=round(float(repeat_score), 1) if repeat_score is not None else None,
        duplicate_status=duplicate_status,
        source_color=source_color,
        include_in_run=risk_level != 'safe',
        source_excerpt=matched_text,
        is_auto_generated=True,
        is_user_modified=False,
    )


def _match_fragment(paragraph_text, fragments, report_text, *, used_fragment_indexes=None, min_index=0):
    paragraph_norm = _normalize_search_text(paragraph_text)
    if not paragraph_norm:
        return None, None, 0.0
    paragraph_tokens = _tokenize_for_match(paragraph_text)
    used_fragment_indexes = set(used_fragment_indexes or set())

    best_after = (None, None, 0.0)
    best_before = (None, None, 0.0)

    for index, fragment in enumerate(fragments or []):
        if index in used_fragment_indexes:
            continue
        fragment_text = str((fragment or {}).get('text', '') or '').strip()
        fragment_norm = _normalize_search_text(fragment_text)
        if len(fragment_norm) < 6:
            continue
        fragment_kind = str((fragment or {}).get('kind', 'body') or 'body')
        fragment_tokens = _tokenize_for_match(fragment_text)
        if paragraph_tokens and fragment_tokens:
            overlap_tokens = paragraph_tokens & fragment_tokens
            if not overlap_tokens and fragment_kind != 'meta':
                continue
        else:
            overlap_tokens = set()

        score = 0.0
        if fragment_norm in paragraph_norm or paragraph_norm in fragment_norm:
            score = 1.0
        else:
            for prefix_len, prefix_score in ((12, 0.84), (16, 0.9), (20, 0.94)):
                prefix = paragraph_norm[: min(prefix_len, len(paragraph_norm))]
                if len(prefix) >= 10 and prefix in fragment_norm:
                    score = max(score, prefix_score)
                    break
            ratio = difflib.SequenceMatcher(None, paragraph_norm[:300], fragment_norm[:300]).ratio()
            overlap_ratio = len(overlap_tokens) / max(len(paragraph_tokens), 1) if paragraph_tokens else 0.0
            score = max(score, ratio, overlap_ratio * 0.88)

        if fragment_kind != 'body':
            score -= 0.12
        else:
            score += 0.03

        if index < min_index:
            score -= min(0.16, (min_index - index) * 0.02)
        else:
            score += max(0.0, 0.06 - (index - min_index) * 0.002)

        if (fragment or {}).get('risk_level') not in {'', None, 'safe'}:
            score += 0.02

        if index >= min_index:
            if score > best_after[2]:
                best_after = (fragment, index, score)
        elif score > best_before[2]:
            best_before = (fragment, index, score)

    if best_after[0] is not None and best_after[2] >= 0.42:
        return best_after
    if best_before[0] is not None and best_before[2] >= 0.58:
        return best_before

    nearby_score = _extract_nearby_score(report_text, paragraph_text, page_kind='')
    if nearby_score is not None:
        return {
            'text': paragraph_text.strip()[:80],
            'risk_level': 'safe',
            'score': nearby_score,
            'source_color': 'unknown',
        }, None, 0.35
    return None, None, 0.0


def _extract_nearby_score(report_text, paragraph_text, page_kind=''):
    report_value = str(report_text or '')
    if not report_value.strip():
        return None
    paragraph_value = str(paragraph_text or '').strip()
    if not paragraph_value:
        return None
    seeds = [paragraph_value[:18], paragraph_value[:24], paragraph_value[:32]]
    for seed in seeds:
        if len(seed.strip()) < 8:
            continue
        index = report_value.find(seed)
        if index < 0:
            continue
        window = report_value[max(0, index - 120): index + max(len(seed), 120)]
        value = _extract_primary_score(window, page_kind)
        if value is not None:
            return value
    return None


def _extract_primary_score(text, page_kind):
    if not text:
        return None
    patterns = []
    allow_percent_fallback = _looks_like_standalone_percent_snippet(text)
    if page_kind == 'ai_reduce':
        patterns.append(AI_BADGE_RE)
        patterns.append(AI_RATE_RE)
        if allow_percent_fallback:
            patterns.append(PERCENT_RE)
    elif page_kind == 'plagiarism':
        patterns.append(REPEAT_BADGE_RE)
        patterns.append(REPEAT_RATE_RE)
        if allow_percent_fallback:
            patterns.append(PERCENT_RE)
    else:
        patterns.extend((AI_BADGE_RE, REPEAT_BADGE_RE, AI_RATE_RE, REPEAT_RATE_RE))
        if allow_percent_fallback:
            patterns.append(PERCENT_RE)
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except Exception:
            continue
        return max(0.0, min(100.0, value))
    return None


def _estimate_ai_score(text):
    value = str(text or '')
    score = 0.0
    for pattern in ('综上所述', '由此可见', '值得注意的是', '不可否认', '众所周知'):
        if pattern in value:
            score += 8
    sentences = [item.strip() for item in re.split(r'[。！？?!]', value) if item.strip()]
    if len(sentences) >= 4:
        lengths = [len(item) for item in sentences]
        average = sum(lengths) / max(len(lengths), 1)
        variance = sum((item - average) ** 2 for item in lengths) / max(len(lengths), 1)
        if variance < 120:
            score += 12
    transition_hits = sum(value.count(item) for item in ('因此', '此外', '同时', '进一步说', '综上'))
    if sentences and transition_hits > len(sentences) * 0.35:
        score += 10
    return max(0.0, min(100.0, score))


def _estimate_repeat_score(text):
    value = str(text or '')
    phrases = re.findall(r'[\u4e00-\u9fff]{4,}', value)
    score = 0.0
    seen = {}
    for item in phrases:
        seen[item] = seen.get(item, 0) + 1
    score += sum(6 for count in seen.values() if count >= 3)
    long_sentences = [item for item in re.split(r'[。！？?!]', value) if len(item.strip()) > 100]
    score += len(long_sentences) * 6
    return max(0.0, min(100.0, score))


def _score_to_ai_risk(score):
    value = float(score or 0.0)
    if value >= 30:
        return 'high'
    if value >= 15:
        return 'medium'
    if value >= 6:
        return 'low'
    return 'safe'


def _score_to_repeat_risk(score):
    value = float(score or 0.0)
    if value >= 35:
        return 'high'
    if value >= 18:
        return 'medium'
    if value >= 8:
        return 'low'
    return 'safe'


def _looks_like_standalone_percent_snippet(text):
    value = str(text or '').strip()
    if not value or ('%' not in value and '％' not in value):
        return False
    compact = re.sub(r'\s+', '', value)
    if len(compact) <= 20:
        return True
    keywords = (
        '查重', '重复率', '查重率', '文字复制比', '总文字复制比',
        '相似度', 'similarity', 'repeat', 'aigc', 'aigc rate',
        'ai率', 'ai检测', 'ai rate', 'ai score', 'ai',
    )
    for match in PERCENT_RE.finditer(value):
        window_start = max(0, match.start() - 18)
        window = value[window_start:match.start()]
        window_lower = window.lower()
        if any(keyword in window or keyword in window_lower for keyword in keywords):
            return True
    return False


def _risk_to_duplicate_status(risk_level):
    risk = _normalize_risk_level(risk_level)
    if risk == 'high':
        return 'red'
    if risk in {'medium', 'low'}:
        return 'suspect'
    return 'safe'


def _infer_duplicate_status(text, default_risk):
    value = str(text or '')
    if HIGH_RISK_RE.search(value):
        return 'red'
    if MEDIUM_RISK_RE.search(value) or LOW_RISK_RE.search(value):
        return 'suspect'
    return _risk_to_duplicate_status(default_risk)


def _infer_risk_from_text(text, default_risk):
    value = str(text or '')
    if HIGH_RISK_RE.search(value):
        return 'high'
    if MEDIUM_RISK_RE.search(value):
        return 'medium'
    if LOW_RISK_RE.search(value):
        return 'low'
    return _normalize_risk_level(default_risk)


def _docx_style_from_run(run):
    risk_candidates = ['safe']
    source_color_candidates = ['unknown']

    try:
        font_color = getattr(run.font, 'color', None)
    except Exception:
        font_color = None

    rgb = getattr(font_color, 'rgb', None)
    rgb_value = _rgb_from_hex_string(str(rgb or ''))
    if rgb_value is not None:
        source_color_candidates.append(_source_color_from_rgb(rgb_value))
        risk_candidates.append(_risk_from_rgb(rgb_value))

    theme_color = getattr(font_color, 'theme_color', None)
    theme_color_name = getattr(theme_color, 'name', str(theme_color or '')).lower()
    theme_source_color = _normalize_source_color(theme_color_name)
    if theme_source_color != 'unknown':
        source_color_candidates.append(theme_source_color)
        risk_candidates.append(RISK_BY_COLOR.get(theme_source_color, 'safe'))

    try:
        highlight = getattr(run.font, 'highlight_color', None)
    except Exception:
        highlight = None
    highlight_name = getattr(highlight, 'name', str(highlight or '')).lower()
    highlight_source_color = _normalize_source_color(highlight_name)
    if highlight_source_color != 'unknown':
        source_color_candidates.append(highlight_source_color)
    if highlight_name in DOCX_HIGHLIGHT_NAME_MAP:
        risk_candidates.append(DOCX_HIGHLIGHT_NAME_MAP[highlight_name])

    risk_level = max(risk_candidates, key=lambda item: RISK_ORDER.get(_normalize_risk_level(item), 0))
    return {
        'risk_level': _normalize_risk_level(risk_level),
        'source_color': _select_source_color_for_risk(source_color_candidates, risk_level),
    }


def _risk_from_docx_run(run):
    return _docx_style_from_run(run)['risk_level']


def _detect_report_kind(path, report_text):
    path_value = str(path or '').lower()
    text_value = str(report_text or '')
    text_lower = text_value.lower()
    haystack = f'{path_value}\n{text_lower}'

    aigc_score = 0
    plagiarism_score = 0

    for hint in AIGC_REPORT_HINTS:
        if hint in haystack:
            aigc_score += 3
    for hint in PLAGIARISM_REPORT_HINTS:
        if hint in haystack:
            plagiarism_score += 3

    if 'aigc' in path_value:
        aigc_score += 4
    if '查重' in path_value or 'plagiarism' in path_value or 'repeat' in path_value:
        plagiarism_score += 4

    aigc_badges = len(AI_BADGE_RE.findall(text_value))
    repeat_badges = len(REPEAT_BADGE_RE.findall(text_value))
    if aigc_badges:
        aigc_score += min(aigc_badges, 4)
    if repeat_badges:
        plagiarism_score += min(repeat_badges, 4)

    if aigc_score == 0 and plagiarism_score == 0:
        return 'unknown'
    if aigc_score >= plagiarism_score + 2:
        return 'aigc'
    if plagiarism_score >= aigc_score + 2:
        return 'plagiarism'
    return 'unknown'


def _validate_import_page_kind(page_kind):
    normalized = str(page_kind or '').strip()
    if normalized in REPORT_KIND_BY_PAGE:
        return normalized
    raise ValueError(f'不支持的报告导入页面类型：{normalized or "unknown"}')


def _validate_report_kind_for_page(page_kind, report_kind):
    expected_kind = REPORT_KIND_BY_PAGE.get(str(page_kind or '').strip())
    current_kind = str(report_kind or '').strip().lower()
    if not expected_kind or current_kind in {'', 'unknown', expected_kind}:
        return

    if expected_kind == 'plagiarism' and current_kind == 'aigc':
        raise RuntimeError('当前页面为“降查重率”，请前往“降AI检测”页面导入 AIGC 报告。')
    if expected_kind == 'aigc' and current_kind == 'plagiarism':
        raise RuntimeError('当前页面为“降AI检测”，请前往“降查重率”页面导入查重报告。')


def _detect_vendor(path, report_text):
    haystack = f'{path}\n{report_text}'.lower()
    if 'paperpass' in haystack:
        return 'PaperPass'
    if '知网' in haystack or 'cnki' in haystack:
        return '知网'
    if '万方' in haystack or 'wanfang' in haystack:
        return '万方'
    if '维普' in haystack or 'vip' in haystack or 'cqvip' in haystack:
        return '维普'
    return '通用模板'


def _extract_text_from_pdf(path):
    text, _fragments, _used_color_detection = _extract_pdf_fragments(path, page_kind='')
    if text.strip():
        return text

    with open(path, 'rb') as handle:
        data = handle.read()

    raw = data.decode('latin-1', errors='ignore')
    lines = [item.strip() for item in re.findall(r'[\u4e00-\u9fffA-Za-z0-9][^\r\n]{4,}', raw)]
    return '\n'.join(_dedupe_keep_order(lines))


def _extract_text_from_pdf_stream(stream):
    pieces = [item.get('text', '').strip() for item in _extract_text_fragments_from_pdf_stream(stream, page_kind='') if item.get('text', '').strip()]
    return '\n'.join(_dedupe_keep_order(pieces))


def _extract_pdf_fragments_with_pymupdf(path, page_kind, log_callback=None):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('缺少 PyMuPDF，无法启用增强 PDF 解析') from exc

    started_at = time.perf_counter()
    document = fitz.open(path)
    fragments = []
    page_texts = []
    fragment_order = 0
    scored_fragments = 0
    legend_map = None

    try:
        _emit_report_import_log(
            log_callback,
            f'[report_import] pymupdf_begin pages={document.page_count}',
        )
        legend_map = _extract_pymupdf_legend_map(document, page_kind, log_callback)
        for page_index in range(document.page_count):
            page = document[page_index]
            lines = _extract_pymupdf_page_lines(page, page_kind, legend_map=legend_map)
            visible_lines = [item['text'] for item in lines if not item.get('is_noise')]
            if visible_lines:
                page_texts.append('\n'.join(visible_lines))

            page_fragments = _build_pymupdf_page_fragments(
                lines,
                page_kind,
                legend_map=legend_map,
                log_callback=log_callback,
                page_index=page_index,
            )
            scored_fragments += sum(1 for item in page_fragments if item.get('score') is not None)
            for fragment in page_fragments:
                fragment['order'] = fragment_order
                fragment_order += 1
                fragments.append(fragment)

            _emit_report_import_log(
                log_callback,
                f'[report_import] pymupdf_page page_index={page_index} lines={len(lines)} '
                f'fragments={len(page_fragments)} scored={sum(1 for item in page_fragments if item.get("score") is not None)}',
            )
    finally:
        document.close()

    text = '\n\n'.join(item for item in page_texts if str(item or '').strip())
    _emit_report_import_log(
        log_callback,
        f'[report_import] pymupdf_done fragments={len(fragments)} text_chars={len(text)} '
        f'scored={scored_fragments} elapsed={time.perf_counter() - started_at:.3f}s',
    )
    if not text.strip():
        raise RuntimeError('PyMuPDF 未解析到有效文本内容')
    return text, fragments, scored_fragments > 0


def _extract_pymupdf_page_lines(page, page_kind, legend_map=None):
    words = page.get_text('words', sort=True)
    if not words:
        return []

    fill_regions = _extract_pymupdf_fill_regions(page)
    text_color_lines = _extract_pymupdf_text_dict_lines(page)
    lines = []
    current_words = []
    current_top = None
    page_height = float(page.rect.height)

    def flush():
        nonlocal current_words, current_top
        if not current_words:
            return
        line = _build_pymupdf_line(
            current_words,
            page_kind,
            page_height,
            fill_regions,
            text_color_lines=text_color_lines,
            legend_map=legend_map,
        )
        if line is not None:
            lines.append(line)
        current_words = []
        current_top = None

    for word in words:
        token = str(word[4] or '').strip()
        if not token:
            continue
        top = float(word[1])
        if current_words and current_top is not None and abs(top - current_top) > 2.2:
            flush()
        if not current_words:
            current_top = top
        current_words.append(word)
    flush()
    lines.sort(key=lambda item: (item['bbox'][1], item['bbox'][0]))
    return lines


def _build_pymupdf_line(words, page_kind, page_height, fill_regions, *, text_color_lines=None, legend_map=None):
    ordered = sorted(words, key=lambda item: (float(item[0]), float(item[1])))
    raw_text = _join_pymupdf_words(ordered).strip()
    if not raw_text:
        return None
    badge_score, body_text = _split_report_badge_text(raw_text, page_kind)
    text = body_text or raw_text
    bbox = (
        min(float(item[0]) for item in ordered),
        min(float(item[1]) for item in ordered),
        max(float(item[2]) for item in ordered),
        max(float(item[3]) for item in ordered),
    )
    fill_rgb = _match_pymupdf_line_fill(bbox, fill_regions)
    text_rgb = None if fill_rgb is not None else _match_pymupdf_line_text_rgb(raw_text, bbox, text_color_lines)
    background_rgb = fill_rgb if fill_rgb is not None else text_rgb
    background_risk = _risk_from_rgb(background_rgb, legend_map=legend_map) if background_rgb is not None else 'safe'
    background_source_color = _source_color_from_rgb(background_rgb, legend_map=legend_map)
    return {
        'text': text,
        'bbox': bbox,
        'badge_score': badge_score,
        'background_rgb': background_rgb,
        'background_risk': background_risk,
        'background_source_color': background_source_color,
        'background_source': 'fill' if fill_rgb is not None else ('text' if text_rgb is not None else None),
        'is_badge_only': badge_score is not None and not body_text,
        'is_noise': _is_pymupdf_noise_line(raw_text, bbox, page_height, page_kind),
    }


def _join_pymupdf_words(words):
    result = []
    previous_text = ''
    previous_x1 = None
    for word in words:
        token = str(word[4] or '')
        x0 = float(word[0])
        if result and previous_x1 is not None and _needs_pymupdf_space(previous_text, token, x0 - previous_x1):
            result.append(' ')
        result.append(token)
        previous_text = token
        previous_x1 = float(word[2])
    return ''.join(result)


def _needs_pymupdf_space(previous_text, current_text, gap):
    if gap <= 1.2:
        return False
    if not previous_text or not current_text:
        return False
    previous_char = previous_text[-1]
    current_char = current_text[0]
    if previous_char.isascii() and current_char.isascii():
        return True
    if previous_char.isdigit() and current_char.isdigit():
        return True
    return False


def _extract_pdf_badge_score(text, page_kind):
    value = str(text or '').strip()
    if not value:
        return None
    score, _remainder = _split_report_badge_text(value, page_kind)
    if score is not None:
        return score
    if page_kind == 'ai_reduce':
        match = AI_BADGE_RE.search(value)
    elif page_kind == 'plagiarism':
        match = REPEAT_BADGE_RE.search(value)
    else:
        match = None
    if not match:
        return None
    groups = [item for item in match.groups() if item]
    raw_value = groups[0] if groups else match.group(1)
    try:
        return max(0.0, min(100.0, float(raw_value)))
    except Exception:
        return None
    return None


def _split_report_badge_text(text, page_kind):
    value = str(text or '').strip()
    if not value:
        return None, ''
    if page_kind == 'ai_reduce':
        match = AI_BADGE_PREFIX_RE.match(value)
    elif page_kind == 'plagiarism':
        match = REPEAT_BADGE_PREFIX_RE.match(value)
    else:
        match = None
    if not match:
        return None, value
    groups = [item for item in match.groups() if item]
    raw_value = groups[0] if groups else None
    try:
        score = max(0.0, min(100.0, float(raw_value))) if raw_value is not None else None
    except Exception:
        score = None
    return score, value[match.end():].strip()


def _strip_leading_report_badge(text, page_kind):
    _score, remainder = _split_report_badge_text(text, page_kind)
    return remainder or str(text or '').strip()


def _is_pymupdf_noise_line(text, bbox, page_height, page_kind):
    value = str(text or '').strip()
    if not value:
        return True
    top = float(bbox[1])
    bottom = float(bbox[3])
    if top < 70 and _extract_pdf_badge_score(value, page_kind) is None:
        return True
    if bottom > page_height - 35:
        return True
    if re.fullmatch(r'-\s*\d+\s*-', value):
        return True
    if re.fullmatch(r'(?:AIGC|AI|查重)检测\(\d+/\d+\)', value):
        return True
    return False


def _extract_pymupdf_text_dict_lines(page):
    try:
        text_dict = page.get_text('dict')
    except Exception:
        return []

    lines = []
    for block in text_dict.get('blocks', []):
        if int(block.get('type', 0) or 0) != 0:
            continue
        for line in block.get('lines', []):
            spans = [span for span in line.get('spans', []) if str(span.get('text', '') or '').strip()]
            if not spans:
                continue
            text = ''.join(str(span.get('text', '') or '') for span in spans).strip()
            if not text:
                continue
            bbox = line.get('bbox') or spans[0].get('bbox') or (0, 0, 0, 0)
            lines.append(
                {
                    'text': text,
                    'bbox': tuple(float(value) for value in bbox),
                    'normalized_text': _normalize_search_text(text),
                    'text_rgb': _pick_pymupdf_line_text_rgb(spans),
                }
            )
    lines.sort(key=lambda item: (item['bbox'][1], item['bbox'][0]))
    return lines


def _pick_pymupdf_line_text_rgb(spans):
    rgb_weights = {}
    for span in spans:
        rgb = _pymupdf_text_to_rgb(span.get('color'))
        if rgb is None:
            continue
        text = str(span.get('text', '') or '').strip()
        weight = max(len(_normalize_search_text(text)), 1)
        rgb_weights[rgb] = rgb_weights.get(rgb, 0) + weight
    if not rgb_weights:
        return None
    return max(
        rgb_weights,
        key=lambda rgb: (
            rgb_weights[rgb],
            RISK_ORDER.get(_risk_from_rgb(rgb), 0),
        ),
    )


def _pymupdf_text_to_rgb(value):
    if value is None:
        return None
    if isinstance(value, int):
        return (
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        )
    if isinstance(value, float):
        channel = max(0, min(255, round(value * 255 if 0.0 <= value <= 1.0 else value)))
        return (channel, channel, channel)
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        channels = []
        for item in value[:3]:
            try:
                numeric = float(item)
            except Exception:
                return None
            if 0.0 <= numeric <= 1.0:
                numeric *= 255
            channels.append(max(0, min(255, round(numeric))))
        return tuple(channels)
    return None


def _match_pymupdf_line_text_rgb(text, line_bbox, text_lines):
    if not text_lines:
        return None

    x0, y0, x1, y1 = line_bbox
    line_area = max((x1 - x0) * (y1 - y0), 1.0)
    target_norm = _normalize_search_text(text)
    best_rgb = None
    best_score = float('-inf')

    for line in text_lines:
        rgb = line.get('text_rgb')
        if rgb is None:
            continue
        tx0, ty0, tx1, ty1 = line.get('bbox') or (0, 0, 0, 0)
        overlap_width = max(0.0, min(x1, tx1) - max(x0, tx0))
        overlap_height = max(0.0, min(y1, ty1) - max(y0, ty0))
        if overlap_width <= 0 or overlap_height <= 0:
            continue
        coverage = (overlap_width * overlap_height) / line_area
        if coverage < 0.2:
            continue

        score = coverage * 10.0
        line_norm = str(line.get('normalized_text', '') or '')
        if target_norm and line_norm:
            if line_norm in target_norm or target_norm in line_norm:
                score += 4.0
            else:
                score += difflib.SequenceMatcher(None, target_norm[:120], line_norm[:120]).ratio() * 2.0
        if score > best_score:
            best_score = score
            best_rgb = rgb
    return best_rgb


def _extract_pymupdf_legend_map(document, page_kind, log_callback=None):
    entries = []
    pages_to_scan = min(int(document.page_count or 0), 4)
    for page_index in range(pages_to_scan):
        page = document[page_index]
        text_lines = _extract_pymupdf_text_dict_lines(page)
        if not text_lines:
            continue
        fill_regions = _extract_pymupdf_fill_regions(page)
        for line in text_lines:
            entry = _parse_pymupdf_legend_line(line.get('text', ''), page_kind)
            if entry is None:
                continue
            entry['rgb'] = _match_pymupdf_legend_fill(line.get('bbox'), fill_regions)
            entry['page_index'] = page_index
            entries.append(entry)
        if len({item.get('color_name') for item in entries}) >= 4:
            break

    entries = _dedupe_pymupdf_legend_entries(entries)
    if not entries:
        return None

    rgb_count = sum(1 for item in entries if item.get('rgb') is not None)
    score_rule_count = sum(1 for item in entries if item.get('score_min') is not None or item.get('score_max') is not None)
    labels = ','.join(
        f'{item.get("color_name")}:{item.get("risk_level")}'
        for item in entries
    )
    _emit_report_import_log(
        log_callback,
        f'[report_import] pymupdf_legend entries={len(entries)} rgb={rgb_count} score_rules={score_rule_count} labels={labels}',
    )
    return {'entries': entries}


def _parse_pymupdf_legend_line(text, page_kind):
    value = str(text or '').strip()
    if not value:
        return None

    match = LEGEND_LINE_RE.match(value)
    if match is None:
        return None

    body = str(match.group('body') or '').strip()
    if not body:
        return None

    if page_kind == 'ai_reduce':
        if not any(keyword in value for keyword in ('疑似', 'AIGC', 'AI', '%', '检测', '颜色')):
            return None
    elif not any(keyword in value for keyword in ('相似', '重复', '风险', '检测', '%', '颜色', '疑似')):
        return None

    color_key = str(match.group('color') or '').lower()
    color_name = LEGEND_COLOR_NAME_MAP.get(color_key)
    if not color_name:
        return None

    score_min, score_max = _extract_legend_score_range(body)
    risk_level = _infer_legend_risk_level(color_name, body)
    if (
        score_min is None
        and score_max is None
        and risk_level == 'safe'
        and not any(keyword in body for keyword in ('不予检测', '不检测'))
        and not any(keyword in body.lower() for keyword in ('not detected', 'excluded', 'ignore'))
    ):
        return None

    return {
        'color_name': color_name,
        'risk_level': risk_level,
        'score_min': score_min,
        'score_max': score_max,
        'raw_text': value,
    }


def _extract_legend_score_range(text):
    value = str(text or '')
    match = LEGEND_RANGE_BETWEEN_RE.search(value)
    if match is not None:
        lower = _coerce_optional_float(match.group('lower'))
        upper = _coerce_optional_float(match.group('upper'))
        if lower is not None and upper is not None and lower > upper:
            lower, upper = upper, lower
        return lower, upper

    match = LEGEND_RANGE_ABOVE_RE.search(value)
    if match is not None:
        return _coerce_optional_float(match.group('lower')), None

    match = LEGEND_RANGE_BELOW_RE.search(value)
    if match is not None:
        return None, _coerce_optional_float(match.group('upper'))

    return None, None


def _infer_legend_risk_level(color_name, text):
    value = f'{color_name}:{str(text or "").strip()}'
    value_lower = value.lower()
    if any(keyword in value for keyword in ('不予检测', '不检测')) or any(
        keyword in value_lower for keyword in ('not detected', 'excluded', 'ignore')
    ):
        return 'safe'
    if any(keyword in value for keyword in ('高度疑似', '高风险')) or any(
        keyword in value_lower for keyword in ('high risk', 'very high')
    ):
        return 'high'
    if any(keyword in value for keyword in ('中度疑似', '中风险')) or 'medium risk' in value_lower:
        return 'medium'
    if any(keyword in value for keyword in ('轻度疑似', '低风险')) or 'low risk' in value_lower:
        return 'low'
    if color_name == 'gray':
        return 'safe'
    return RISK_BY_COLOR.get(color_name, 'safe')


def _dedupe_pymupdf_legend_entries(entries):
    best_entries = {}
    for entry in entries:
        key = str(entry.get('color_name') or '')
        if not key:
            continue
        current = best_entries.get(key)
        score = 0
        if entry.get('rgb') is not None:
            score += 3
        if entry.get('score_min') is not None or entry.get('score_max') is not None:
            score += 2
        if entry.get('risk_level') not in {'', None, 'safe'}:
            score += 1
        if current is None or score > current[0]:
            best_entries[key] = (score, dict(entry))
    return [item[1] for item in best_entries.values()]


def _extract_pymupdf_fill_regions(page):
    regions = []
    page_area = max(float(page.rect.width) * float(page.rect.height), 1.0)
    for drawing in page.get_drawings():
        rect = drawing.get('rect')
        fill = drawing.get('fill')
        if rect is None or fill is None:
            continue
        rgb = _pymupdf_fill_to_rgb(fill)
        if rgb is None:
            continue
        width = max(float(rect.width), 0.0)
        height = max(float(rect.height), 0.0)
        if width < 4 or height < 4:
            continue
        if width * height >= page_area * 0.75 and _is_near_white(rgb):
            continue
        opacity = float(drawing.get('fill_opacity') or 1.0)
        if opacity <= 0.05:
            continue
        if _is_near_white(rgb) and opacity >= 0.95:
            continue
        regions.append(
            {
                'bbox': (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                'rgb': rgb,
                'opacity': opacity,
            }
        )
    return regions


def _pymupdf_fill_to_rgb(fill):
    if not isinstance(fill, (tuple, list)) or len(fill) < 3:
        return None
    try:
        return tuple(max(0, min(255, round(float(value) * 255))) for value in fill[:3])
    except Exception:
        return None


def _is_near_white(rgb):
    return all(channel >= 245 for channel in rgb)


def _match_pymupdf_legend_fill(line_bbox, fill_regions):
    if not fill_regions:
        return None

    x0, y0, _, y1 = line_bbox
    line_height = max(y1 - y0, 1.0)
    best_region = None
    best_score = float('-inf')

    for region in fill_regions:
        rx0, ry0, rx1, ry1 = region['bbox']
        width = rx1 - rx0
        height = ry1 - ry0
        if width > 30 or height > 24:
            continue
        vertical_overlap = max(0.0, min(y1, ry1) - max(y0, ry0))
        if vertical_overlap / line_height < 0.45:
            continue
        horizontal_gap = x0 - rx1
        if horizontal_gap < -6 or horizontal_gap > 36:
            continue
        score = vertical_overlap * 12.0 - abs(horizontal_gap)
        if score > best_score:
            best_score = score
            best_region = region

    if best_region is None:
        return None
    return best_region['rgb']


def _match_pymupdf_line_fill(line_bbox, fill_regions):
    if not fill_regions:
        return None
    x0, y0, x1, y1 = line_bbox
    line_area = max((x1 - x0) * (y1 - y0), 1.0)
    best_region = None
    best_score = 0.0
    for region in fill_regions:
        rx0, ry0, rx1, ry1 = region['bbox']
        overlap_width = max(0.0, min(x1, rx1) - max(x0, rx0))
        overlap_height = max(0.0, min(y1, ry1) - max(y0, ry0))
        if overlap_width <= 0 or overlap_height <= 0:
            continue
        overlap_area = overlap_width * overlap_height
        coverage = overlap_area / line_area
        if coverage < 0.25:
            continue
        score = coverage * 10.0 + region['opacity']
        if score > best_score:
            best_score = score
            best_region = region
    if best_region is None:
        return None
    return best_region['rgb']


def _build_pymupdf_page_fragments(lines, page_kind, legend_map=None, log_callback=None, page_index=None):
    fragments = []
    current_lines = []
    current_score = None
    pending_score = None

    def flush():
        nonlocal current_lines, current_score
        if not current_lines:
            return
        text = '\n'.join(item['text'] for item in current_lines if str(item.get('text', '')).strip()).strip()
        if not text:
            current_lines = []
            current_score = None
            return

        fragment_color_risk = _pick_pymupdf_fragment_risk(current_lines)
        fragment_source_color = _pick_pymupdf_fragment_source_color(current_lines)
        risk_level = fragment_color_risk or _infer_risk_from_text(text, 'safe')
        score = current_score
        legend_score_risk = None
        duplicate_status = None
        if page_kind == 'ai_reduce':
            if score is None:
                score = _extract_primary_score(text, 'ai_reduce')
            if score is not None:
                legend_score_risk = _risk_from_legend_score(score, legend_map)
            if risk_level == 'safe':
                risk_level = legend_score_risk or risk_level
            if score is not None and risk_level == 'safe':
                risk_level = _score_to_ai_risk(score)
        else:
            if score is None:
                score = _extract_primary_score(text, 'plagiarism')
            if score is not None:
                legend_score_risk = _risk_from_legend_score(score, legend_map)
            if risk_level == 'safe':
                risk_level = legend_score_risk or risk_level
            if score is not None and risk_level == 'safe':
                risk_level = _score_to_repeat_risk(score)
            duplicate_status = _infer_duplicate_status(text, risk_level)

        if (
            fragment_color_risk is not None
            and legend_score_risk is not None
            and fragment_color_risk != legend_score_risk
        ):
            preview = re.sub(r'\s+', ' ', text).strip()[:48]
            _emit_report_import_log(
                log_callback,
                f'[report_import] pymupdf_color_score_mismatch page_index={page_index} '
                f'color_risk={fragment_color_risk} score_risk={legend_score_risk} '
                f'score={score} text={json.dumps(preview, ensure_ascii=False)}',
                level='WARN',
            )

        fragments.append(
            {
                'text': text,
                'risk_level': risk_level,
                'score': score,
                'duplicate_status': duplicate_status,
                'source_color': fragment_source_color,
                'kind': _classify_report_fragment(text),
                'color_applied': fragment_color_risk is not None,
            }
        )
        current_lines = []
        current_score = None

    for line in lines:
        text = str(line.get('text', '') or '').strip()
        if not text or line.get('is_noise'):
            continue

        badge_score = line.get('badge_score')
        if badge_score is not None:
            flush()
            pending_score = badge_score
            if line.get('is_badge_only'):
                continue

        if _heading_level(text) or REFERENCE_HEADING_RE.match(text) or CAPTION_RE.match(text):
            flush()
            pending_score = None
            continue

        if current_lines and _should_split_pymupdf_fragment(current_lines[-1], line):
            flush()

        if not current_lines:
            current_score = pending_score
            pending_score = None
        current_lines.append(line)

    flush()
    return fragments


def _pick_pymupdf_fragment_risk(lines):
    counts = {}
    for line in lines:
        risk_level = _normalize_risk_level(line.get('background_risk'))
        if risk_level == 'safe':
            continue
        counts[risk_level] = counts.get(risk_level, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda item: (counts[item], RISK_ORDER.get(item, 0)))


def _pick_pymupdf_fragment_source_color(lines):
    counts = {}
    for line in lines:
        color_name = _normalize_source_color(line.get('background_source_color'))
        if color_name == 'unknown':
            continue
        counts[color_name] = counts.get(color_name, 0) + 1
    if not counts:
        return 'unknown'
    return max(
        counts,
        key=lambda item: (
            counts[item],
            RISK_ORDER.get(_normalize_risk_level(RISK_BY_COLOR.get(item, 'safe')), 0),
        ),
    )


def _should_split_pymupdf_fragment(previous_line, current_line):
    previous_bbox = previous_line.get('bbox') or (0, 0, 0, 0)
    current_bbox = current_line.get('bbox') or (0, 0, 0, 0)
    previous_risk = _normalize_risk_level(previous_line.get('background_risk'))
    current_risk = _normalize_risk_level(current_line.get('background_risk'))
    if previous_risk != current_risk and ({previous_risk, current_risk} - {'safe'}):
        return True
    gap = float(current_bbox[1]) - float(previous_bbox[3])
    if gap > 16:
        return True
    previous_text = str(previous_line.get('text', '') or '').strip()
    if gap > 8 and PARAGRAPH_END_RE.search(previous_text):
        return True
    return False


def _risk_from_legend_score(score, legend_map):
    value = _coerce_optional_float(score)
    if value is None or not isinstance(legend_map, dict):
        return None

    matched_entries = []
    for entry in legend_map.get('entries', []):
        if _legend_entry_matches_score(entry, value):
            matched_entries.append(entry)

    if not matched_entries:
        return None

    best_entry = max(
        matched_entries,
        key=lambda item: (
            _legend_entry_has_closed_bounds(item),
            -_legend_entry_range_width(item),
            RISK_ORDER.get(_normalize_risk_level(item.get('risk_level')), 0),
        ),
    )
    return _normalize_risk_level(best_entry.get('risk_level'))


def _legend_entry_matches_score(entry, score):
    lower = _coerce_optional_float((entry or {}).get('score_min'))
    upper = _coerce_optional_float((entry or {}).get('score_max'))
    if lower is None and upper is None:
        return False
    if lower is not None and score < lower:
        return False
    if upper is not None and score > upper:
        return False
    return True


def _legend_entry_range_width(entry):
    lower = _coerce_optional_float((entry or {}).get('score_min'))
    upper = _coerce_optional_float((entry or {}).get('score_max'))
    if lower is None and upper is None:
        return float('inf')
    if lower is None:
        return upper if upper is not None else float('inf')
    if upper is None:
        return max(0.0, 100.0 - lower)
    return max(0.0, upper - lower)


def _legend_entry_has_closed_bounds(entry):
    lower = _coerce_optional_float((entry or {}).get('score_min'))
    upper = _coerce_optional_float((entry or {}).get('score_max'))
    return int(lower is not None and upper is not None)


def _decode_pdf_hex_text(value):
    normalized = re.sub(r'[^0-9A-Fa-f]', '', str(value or ''))
    if len(normalized) % 2 == 1:
        normalized += '0'
    try:
        payload = bytes.fromhex(normalized)
    except Exception:
        return ''
    for encoding in ('utf-16-be', 'utf-8', 'gbk', 'latin-1'):
        try:
            return payload.decode(encoding)
        except Exception:
            continue
    return ''


def _decode_pdf_literal_text(value):
    result = []
    index = 0
    source = str(value or '')
    while index < len(source):
        current = source[index]
        if current != '\\':
            result.append(current)
            index += 1
            continue
        if index + 1 >= len(source):
            break
        escaped = source[index + 1]
        escape_map = {
            'n': '\n',
            'r': '\r',
            't': '\t',
            'b': '\b',
            'f': '\f',
            '(': '(',
            ')': ')',
            '\\': '\\',
        }
        if escaped in escape_map:
            result.append(escape_map[escaped])
            index += 2
            continue
        if escaped in '01234567':
            octal = escaped
            cursor = index + 2
            while cursor < len(source) and len(octal) < 3 and source[cursor] in '01234567':
                octal += source[cursor]
                cursor += 1
            result.append(chr(int(octal, 8)))
            index = cursor
            continue
        result.append(escaped)
        index += 2
    return ''.join(result)


def _extract_pdf_fragments(path, page_kind, log_callback=None):
    with open(path, 'rb') as handle:
        data = handle.read()

    fragments = []
    texts = []
    used_color_detection = False
    fragment_order = 0
    stream_matches = list(re.finditer(rb'(?s)(<<.*?>>)?\s*stream\r?\n(.*?)\r?\nendstream', data))
    _emit_report_import_log(
        log_callback,
        f'[report_import] pdf_stream_total total={len(stream_matches)} bytes={len(data)}',
    )

    for stream_index, match in enumerate(stream_matches):
        header = match.group(1) or b''
        stream = match.group(2) or b''
        raw_len = len(stream)
        header_summary = _summarize_pdf_stream_header(header)
        if _is_pdf_image_stream(header):
            _emit_report_import_log(
                log_callback,
                f'[report_import] pdf_stream_skipped_image stream_index={stream_index} '
                f'raw_len={raw_len} decoded_len={raw_len} header_summary={json.dumps(header_summary, ensure_ascii=False)}',
            )
            continue
        decoded = _decode_pdf_stream(stream, header)
        decoded_len = len(decoded)
        if _is_pdf_font_stream(header):
            _emit_report_import_log(
                log_callback,
                f'[report_import] pdf_stream_skipped_font stream_index={stream_index} '
                f'raw_len={raw_len} decoded_len={decoded_len} header_summary={json.dumps(header_summary, ensure_ascii=False)}',
            )
            continue
        if _is_pdf_object_stream(header):
            _emit_report_import_log(
                log_callback,
                f'[report_import] pdf_stream_skipped_nontext stream_index={stream_index} '
                f'raw_len={raw_len} decoded_len={decoded_len} reason=object header_summary={json.dumps(header_summary, ensure_ascii=False)}',
            )
            continue
        if not _pdf_stream_may_contain_text(decoded):
            _emit_report_import_log(
                log_callback,
                f'[report_import] pdf_stream_skipped_nontext stream_index={stream_index} '
                f'raw_len={raw_len} decoded_len={decoded_len} reason=filter header_summary={json.dumps(header_summary, ensure_ascii=False)}',
            )
            continue

        stream_started_at = time.perf_counter()
        _emit_report_import_log(
            log_callback,
            f'[report_import] pdf_stream_parse_begin stream_index={stream_index} '
            f'raw_len={raw_len} decoded_len={decoded_len} header_summary={json.dumps(header_summary, ensure_ascii=False)}',
        )
        stream_fragments = _extract_text_fragments_from_pdf_stream(decoded, page_kind=page_kind)
        elapsed_ms = round((time.perf_counter() - stream_started_at) * 1000)
        _emit_report_import_log(
            log_callback,
            f'[report_import] pdf_stream_parse_done stream_index={stream_index} '
            f'raw_len={raw_len} decoded_len={decoded_len} fragment_count={len(stream_fragments)} '
            f'elapsed_ms={elapsed_ms} header_summary={json.dumps(header_summary, ensure_ascii=False)}',
        )
        if elapsed_ms >= PDF_STREAM_SLOW_MS:
            _emit_report_import_log(
                log_callback,
                f'[report_import] pdf_stream_parse_slow stream_index={stream_index} '
                f'raw_len={raw_len} decoded_len={decoded_len} fragment_count={len(stream_fragments)} '
                f'elapsed_ms={elapsed_ms} header_summary={json.dumps(header_summary, ensure_ascii=False)}',
                level='WARN',
            )
        for fragment in stream_fragments:
            text = str(fragment.get('text', '') or '').strip()
            if not text:
                continue
            fragment['order'] = fragment_order
            fragment_order += 1
            fragments.append(fragment)
            texts.append(text)
            if fragment.get('risk_level') not in {'', None, 'safe'} and fragment.get('color_applied'):
                used_color_detection = True

    text = '\n'.join(_dedupe_keep_order(texts))
    if fragments:
        return text, fragments, used_color_detection

    raw = data.decode('latin-1', errors='ignore')
    lines = [item.strip() for item in re.findall(r'[\u4e00-\u9fffA-Za-z0-9][^\r\n]{4,}', raw)]
    fallback_lines = _dedupe_keep_order(lines)
    fallback_fragments = []
    for order, line in enumerate(fallback_lines):
        clean_line = _strip_leading_report_badge(line, page_kind)
        fallback_fragments.append(
            {
                'text': clean_line,
                'risk_level': _infer_risk_from_text(line, 'safe'),
                'score': _extract_primary_score(line, page_kind),
                'duplicate_status': _infer_duplicate_status(line, 'safe'),
                'source_color': 'unknown',
                'kind': _classify_report_fragment(clean_line),
                'order': order,
                'color_applied': False,
            }
        )
    return '\n'.join(fallback_lines), fallback_fragments, False


def _extract_text_fragments_from_pdf_stream(stream, page_kind):
    source = stream.decode('latin-1', errors='ignore')
    fragments = []
    for text_object in _iter_pdf_text_objects(source):
        fragments.extend(_extract_pdf_fragments_from_text_object(text_object, page_kind))
    return fragments


def _iter_pdf_text_objects(source: str):
    index = 0
    while index < len(source):
        begin = PDF_TEXT_OBJECT_BEGIN_RE.search(source, index)
        if begin is None:
            return
        end = PDF_TEXT_OBJECT_END_RE.search(source, begin.end())
        if end is None:
            return
        yield source[begin.end():end.start()]
        index = end.end()


def _extract_pdf_fragments_from_text_object(text_object, page_kind):
    fragments = []
    buffer = []
    current_rgb = None
    current_risk = 'safe'
    current_color_applied = False

    def flush():
        nonlocal buffer, current_color_applied
        raw_text = ''.join(buffer).strip()
        text = _strip_leading_report_badge(raw_text, page_kind)
        if not text:
            buffer = []
            return
        text_risk = _infer_risk_from_text(raw_text, 'safe')
        if current_color_applied and RISK_ORDER.get(current_risk, 0) >= RISK_ORDER.get(text_risk, 0):
            risk_level = current_risk
        else:
            risk_level = text_risk if text_risk != 'safe' else current_risk
        fragments.append(
            {
                'text': text,
                'risk_level': risk_level,
                'score': _extract_primary_score(text, page_kind),
                'duplicate_status': _infer_duplicate_status(text, risk_level),
                'source_color': _source_color_from_rgb(current_rgb) if current_color_applied else 'unknown',
                'kind': _classify_report_fragment(text),
                'color_applied': current_color_applied,
            }
        )
        buffer = []

    index = 0
    while index < len(text_object):
        index = _skip_pdf_whitespace_and_comments(text_object, index)
        if index >= len(text_object):
            break

        match = PDF_RGB_OP_RE.match(text_object, index)
        if match is not None:
            flush()
            current_rgb = _parse_pdf_rgb(
                match.group('r'),
                match.group('g'),
                match.group('b'),
            )
            current_risk = _risk_from_rgb(current_rgb)
            current_color_applied = current_rgb is not None
            index = match.end()
            continue

        match = PDF_GRAY_OP_RE.match(text_object, index)
        if match is not None:
            flush()
            gray = _pdf_number_to_rgb(match.group('gray_value'))
            current_rgb = (gray, gray, gray)
            current_risk = _risk_from_rgb(current_rgb)
            current_color_applied = current_rgb is not None
            index = match.end()
            continue

        match = PDF_TEXT_MOVE_OP_RE.match(text_object, index)
        if match is not None:
            flush()
            index = match.end()
            continue

        match = PDF_TEXT_MATRIX_OP_RE.match(text_object, index)
        if match is not None:
            flush()
            index = match.end()
            continue

        if text_object.startswith('T*', index):
            flush()
            index += 2
            continue

        current = text_object[index]
        if current == '(':
            text, next_index = _read_pdf_literal_string(text_object, index)
            tail = _skip_pdf_whitespace_and_comments(text_object, next_index)
            if text_object.startswith('Tj', tail):
                if text.strip():
                    buffer.append(text)
                index = tail + 2
                continue
            if text_object.startswith("'", tail):
                flush()
                if text.strip():
                    buffer.append(text)
                index = tail + 1
                continue
            if text_object.startswith('"', tail):
                flush()
                if text.strip():
                    buffer.append(text)
                index = tail + 1
                continue
            index = next_index
            continue

        if current == '<' and not text_object.startswith('<<', index):
            text, next_index = _read_pdf_hex_string(text_object, index)
            tail = _skip_pdf_whitespace_and_comments(text_object, next_index)
            if text_object.startswith('Tj', tail):
                if text.strip():
                    buffer.append(text)
                index = tail + 2
                continue
            index = next_index
            continue

        if current == '[':
            text, next_index = _read_pdf_array_text(text_object, index)
            tail = _skip_pdf_whitespace_and_comments(text_object, next_index)
            if text_object.startswith('TJ', tail):
                if text.strip():
                    buffer.append(text)
                index = tail + 2
                continue
            index = next_index
            continue

        index += 1

    flush()
    return fragments


def _skip_pdf_whitespace_and_comments(source: str, index: int) -> int:
    length = len(source)
    while index < length:
        current = source[index]
        if current in ' \t\r\n\f\x00':
            index += 1
            continue
        if current == '%':
            while index < length and source[index] not in '\r\n':
                index += 1
            continue
        break
    return index


def _read_pdf_literal_string(source: str, index: int) -> tuple[str, int]:
    cursor = index + 1
    depth = 1
    pieces = []
    while cursor < len(source):
        current = source[cursor]
        if current == '\\':
            if cursor + 1 < len(source):
                pieces.append(source[cursor:cursor + 2])
                cursor += 2
                continue
            pieces.append(current)
            cursor += 1
            continue
        if current == '(':
            depth += 1
            pieces.append(current)
            cursor += 1
            continue
        if current == ')':
            depth -= 1
            if depth == 0:
                cursor += 1
                break
            pieces.append(current)
            cursor += 1
            continue
        pieces.append(current)
        cursor += 1
    return _decode_pdf_literal_text(''.join(pieces)), cursor


def _read_pdf_hex_string(source: str, index: int) -> tuple[str, int]:
    cursor = index + 1
    pieces = []
    while cursor < len(source):
        current = source[cursor]
        if current == '>':
            cursor += 1
            break
        pieces.append(current)
        cursor += 1
    return _decode_pdf_hex_text(''.join(pieces)), cursor


def _read_pdf_array_text(source: str, index: int) -> tuple[str, int]:
    cursor = index + 1
    pieces = []
    while cursor < len(source):
        cursor = _skip_pdf_whitespace_and_comments(source, cursor)
        if cursor >= len(source):
            break
        current = source[cursor]
        if current == ']':
            cursor += 1
            break
        if current == '(':
            text, cursor = _read_pdf_literal_string(source, cursor)
            if text:
                pieces.append(text)
            continue
        if current == '<' and not source.startswith('<<', cursor):
            text, cursor = _read_pdf_hex_string(source, cursor)
            if text:
                pieces.append(text)
            continue
        cursor += 1
    return ''.join(pieces), cursor


def _parse_pdf_rgb(red, green, blue):
    try:
        return (
            _pdf_number_to_rgb(red),
            _pdf_number_to_rgb(green),
            _pdf_number_to_rgb(blue),
        )
    except Exception:
        return None


def _pdf_number_to_rgb(value):
    number = float(value)
    if number <= 1.0:
        return max(0, min(255, round(number * 255)))
    return max(0, min(255, round(number)))


def _is_pdf_image_stream(header: bytes) -> bool:
    header_value = bytes(header or b'')
    return any(marker in header_value for marker in PDF_IMAGE_MARKERS)


def _is_pdf_font_stream(header: bytes) -> bool:
    header_value = bytes(header or b'')
    return any(marker in header_value for marker in PDF_FONT_MARKERS)


def _is_pdf_object_stream(header: bytes) -> bool:
    header_value = bytes(header or b'')
    return any(marker in header_value for marker in PDF_OBJECT_STREAM_MARKERS)


def _decode_pdf_stream(stream: bytes, header: bytes) -> bytes:
    header_value = bytes(header or b'')
    stream_value = bytes(stream or b'')
    if not stream_value:
        return b''
    if b'/FlateDecode' in header_value:
        try:
            return zlib.decompress(stream_value)
        except Exception:
            return stream_value
    return stream_value


def _pdf_ascii_printable_ratio(stream: bytes) -> float:
    sample = bytes(stream or b'')[:16384]
    if not sample:
        return 0.0
    printable = sum(1 for value in sample if value in (9, 10, 13) or 32 <= value <= 126)
    return printable / len(sample)


def _pdf_operator_hit_count(stream: bytes) -> int:
    sample = bytes(stream or b'')[:16384]
    if not sample:
        return 0
    hits = 0
    for marker in (b'BT', b'ET', b'Tj', b'TJ', b'Td', b'TD', b'Tm', b'Tf', b'T*'):
        hits += sample.count(marker)
        if hits >= 4:
            break
    return hits


def _summarize_pdf_stream_header(header: bytes) -> str:
    value = bytes(header or b'').decode('latin-1', errors='ignore')
    value = re.sub(r'\s+', ' ', value).strip()
    if not value:
        return '<empty>'
    return value[:160]


def _pdf_stream_may_contain_text(stream: bytes) -> bool:
    stream_value = bytes(stream or b'')
    if not stream_value:
        return False
    sample = stream_value[:16384]
    has_text_object = b'BT' in sample and b'ET' in sample
    has_text_show = b'Tj' in sample or b'TJ' in sample
    if not has_text_object and not has_text_show:
        return False
    if _pdf_ascii_printable_ratio(sample) < 0.35:
        return False
    if _pdf_operator_hit_count(sample) < 2:
        return False
    if sample.count(b'\x00') > max(32, len(sample) // 20):
        return False
    return True


def _normalize_search_text(text):
    value = re.sub(r'\s+', '', str(text or ''))
    value = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9%％\[\]（）()：:，,。.!?、\-]', '', value)
    return value


def _build_normalized_search_index(text):
    normalized_chars = []
    mapping = []
    for index, char in enumerate(str(text or '')):
        if char.isspace():
            continue
        if not SEARCH_CHAR_RE.fullmatch(char):
            continue
        normalized_chars.append(char)
        mapping.append(index)
    return ''.join(normalized_chars), mapping


def _locate_excerpt_range(text, excerpt):
    source_text = str(text or '')
    excerpt_text = str(excerpt or '').strip()
    if not source_text or not excerpt_text:
        return None
    direct_index = source_text.find(excerpt_text)
    if direct_index >= 0:
        return direct_index, direct_index + len(excerpt_text)

    normalized_source, source_mapping = _build_normalized_search_index(source_text)
    normalized_excerpt, _excerpt_mapping = _build_normalized_search_index(excerpt_text)
    if len(normalized_excerpt) < 6 or not normalized_source:
        return None
    position = normalized_source.find(normalized_excerpt)
    if position >= 0:
        start_index = source_mapping[position]
        end_index = source_mapping[position + len(normalized_excerpt) - 1] + 1
        return start_index, end_index

    match = difflib.SequenceMatcher(None, normalized_source, normalized_excerpt).find_longest_match(
        0,
        len(normalized_source),
        0,
        len(normalized_excerpt),
    )
    if match.size < max(12, int(len(normalized_excerpt) * 0.45)):
        return None
    start_index = source_mapping[match.a]
    end_index = source_mapping[match.a + match.size - 1] + 1
    return start_index, end_index


def _resolve_annotation_excerpt_range(paragraph, excerpt_text):
    local_range = _locate_excerpt_range(paragraph.text, excerpt_text)
    if local_range is None:
        return None
    local_start, local_end = local_range
    normalized_paragraph = _normalize_search_text(paragraph.text)
    normalized_excerpt = _normalize_search_text(excerpt_text)
    if not normalized_excerpt:
        return None
    if len(normalized_excerpt) >= max(len(normalized_paragraph) - 4, 1):
        return paragraph.start, paragraph.end
    if local_end <= local_start:
        return None
    return paragraph.start + local_start, paragraph.start + local_end


def _tokenize_for_match(text):
    tokens = set()
    for token in TOKEN_RE.findall(str(text or '')):
        value = token.strip().lower()
        if not value:
            continue
        if value.isascii() and value in COMMON_LATIN_STOPWORDS:
            continue
        tokens.add(value)
    return tokens


def _normalize_risk_level(value):
    current = str(value or 'safe').strip().lower()
    if current in RISK_ORDER:
        return current
    alias_map = {
        '高风险': 'high',
        '中风险': 'medium',
        '低风险': 'low',
        '安全': 'safe',
        'red': 'high',
        'orange': 'medium',
        'purple': 'low',
        'black': 'safe',
    }
    return alias_map.get(current, alias_map.get(str(value or '').strip(), 'safe'))


def _normalize_source_color(value):
    current = str(value or 'unknown').strip().lower()
    alias_map = {
        '': 'unknown',
        'unknown': 'unknown',
        'none': 'unknown',
        'red': 'red',
        'darkred': 'red',
        'orange': 'orange',
        'yellow': 'orange',
        'purple': 'purple',
        'violet': 'purple',
        'pink': 'purple',
        'magenta': 'purple',
        'black': 'black',
        'gray': 'gray',
        'grey': 'gray',
        '红色': 'red',
        '标红': 'red',
        '橙色': 'orange',
        '橘色': 'orange',
        '黄色': 'orange',
        '紫色': 'purple',
        '标紫': 'purple',
        '黑色': 'black',
        '灰色': 'gray',
    }
    return alias_map.get(current, alias_map.get(str(value or '').strip(), 'unknown'))


def _select_source_color_for_risk(candidates, risk_level):
    normalized_risk = _normalize_risk_level(risk_level)
    normalized_candidates = []
    for item in candidates or []:
        current = _normalize_source_color(item)
        if current not in normalized_candidates:
            normalized_candidates.append(current)

    for color_name in normalized_candidates:
        if color_name == 'unknown':
            continue
        if _normalize_risk_level(RISK_BY_COLOR.get(color_name, 'safe')) == normalized_risk:
            return color_name
    for color_name in normalized_candidates:
        if color_name != 'unknown':
            return color_name
    return 'unknown'


def _normalize_duplicate_status(value):
    current = str(value or '').strip().lower()
    alias_map = {
        '': 'none',
        'red': 'red',
        'suspect': 'suspect',
        'safe': 'safe',
        'none': 'none',
        '标红': 'red',
        '疑似重复': 'suspect',
        '疑似': 'suspect',
        '安全': 'safe',
        '无': 'none',
        '未标注': 'none',
    }
    return alias_map.get(current, alias_map.get(str(value or '').strip(), 'none'))


def _coerce_optional_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _classify_report_fragment(text):
    value = str(text or '').strip()
    normalized = _normalize_search_text(value)
    if not value or len(normalized) < 6:
        return 'meta'
    if META_FRAGMENT_RE.search(value):
        return 'meta'
    if len(normalized) <= 20 and (
        _extract_primary_score(value, page_kind='') is not None
        or HIGH_RISK_RE.search(value)
        or MEDIUM_RISK_RE.search(value)
        or LOW_RISK_RE.search(value)
    ):
        return 'meta'
    return 'body'


def _default_score_for_risk(risk_level, page_kind):
    risk = _normalize_risk_level(risk_level)
    if risk == 'safe':
        return None
    if page_kind == 'ai_reduce':
        return {
            'high': 35.0,
            'medium': 20.0,
            'low': 8.0,
        }.get(risk)
    return {
        'high': 40.0,
        'medium': 22.0,
        'low': 10.0,
    }.get(risk)


def _rgb_from_hex_string(value):
    hex_value = re.sub(r'[^0-9A-Fa-f]', '', str(value or ''))
    if len(hex_value) >= 8:
        hex_value = hex_value[-6:]
    elif len(hex_value) > 6:
        hex_value = hex_value[:6]
    if len(hex_value) != 6:
        return None
    try:
        return tuple(int(hex_value[index:index + 2], 16) for index in (0, 2, 4))
    except Exception:
        return None


def _risk_from_rgb(rgb, legend_map=None):
    if rgb is None:
        return 'safe'

    legend_entry = _match_legend_rgb_entry(rgb, legend_map)
    if legend_entry is not None:
        return _normalize_risk_level(legend_entry.get('risk_level'))

    best_risk = 'safe'
    best_distance = float('inf')
    for risk_level, prototypes in COLOR_PROTOTYPES.items():
        for prototype in prototypes:
            distance = sum((left - right) ** 2 for left, right in zip(rgb, prototype))
            if distance < best_distance:
                best_distance = distance
                best_risk = risk_level

    if best_risk == 'safe':
        return 'safe'
    if best_distance <= 120 ** 2:
        return best_risk
    return 'safe'


def _source_color_from_rgb(rgb, legend_map=None):
    if rgb is None:
        return 'unknown'

    legend_entry = _match_legend_rgb_entry(rgb, legend_map)
    if legend_entry is not None:
        return _normalize_source_color(legend_entry.get('color_name'))

    red, green, blue = rgb
    if max(rgb) - min(rgb) <= 18:
        return 'black' if (red + green + blue) / 3 <= 96 else 'gray'

    best_color = 'unknown'
    best_distance = float('inf')
    for color_name, prototypes in SOURCE_COLOR_PROTOTYPES.items():
        for prototype in prototypes:
            distance = sum((left - right) ** 2 for left, right in zip(rgb, prototype))
            if distance < best_distance:
                best_distance = distance
                best_color = color_name

    if best_distance <= 140 ** 2:
        return best_color
    return 'unknown'


def _risk_from_legend_rgb(rgb, legend_map):
    best_entry = _match_legend_rgb_entry(rgb, legend_map)
    if best_entry is None:
        return None
    return _normalize_risk_level(best_entry.get('risk_level'))


def _match_legend_rgb_entry(rgb, legend_map):
    if rgb is None or not isinstance(legend_map, dict):
        return None

    best_entry = None
    best_distance = float('inf')
    for entry in legend_map.get('entries', []):
        entry_rgb = entry.get('rgb')
        if not isinstance(entry_rgb, tuple):
            continue
        distance = sum((left - right) ** 2 for left, right in zip(rgb, entry_rgb))
        if distance < best_distance:
            best_distance = distance
            best_entry = entry

    if best_entry is None or best_distance > 120 ** 2:
        return None
    return best_entry


def _dedupe_keep_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
