# -*- coding: utf-8 -*-
"""Local web UI for 论文工坊."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from modules.ai_reducer import AIReducer
from modules.api_client import APIClient
from modules.config import ConfigManager, resolve_model_display_name
from modules.data_chart_assistant import DataChartAssistant
from modules.intelligent_corrector import CATEGORY_LABELS, CATEGORY_ORDER, IntelligentCorrector
from modules.plagiarism import PlagiarismReducer
from modules.polisher import AcademicPolisher
from modules.paper_writer import PaperWriter
from modules.paper_exporter import export_paper_document
from modules.paper_ppt_bridge import (
    generate_ppt_from_paper,
    read_ppt_preview,
    run_ppt_generation_task,
    run_ppt_refine_task,
)
from modules.provider_registry import PRESET_MAP, PRESET_OPTIONS, get_static_models, normalize_provider_type
from modules.runtime_paths import get_runtime_paths
from modules.reference_manager import (
    extract_references_from_section_result,
    merge_reference_entry_lists,
    build_reference_number_map,
    rewrite_citations_with_entry_map,
    build_reference_body_from_entries,
    normalize_section_body,
    parse_reference_entries,
    collect_citation_reference_keys,
    reference_entry_key,
    normalize_reference_entry_text,
    determine_reference_mode,
    process_references_append_mode,
    process_references_reorder_mode,
    reorder_references_for_full_paper,
    is_reference_section
)
from pages.api_config_support import merge_with_preset_defaults

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PROJECT_DIR, 'web')
DATA_CHART_ASSET_DIR = os.path.join(WEB_DIR, 'generated', 'charts')
SERVER_ERROR_LOG = os.path.join(PROJECT_DIR, 'server_errors.log')
WEB_USER_COOKIE = 'thesisworkshop_web_user'
LEGACY_WEB_USER_COOKIES = ('paperlab_web_user',)
WEB_USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
WEB_USERS_DIR_NAME = 'web_users'


def _write_server_error_log(exc, payload=None):
    try:
        with open(SERVER_ERROR_LOG, 'a', encoding='utf-8') as handle:
            handle.write(f'[{datetime.now().isoformat(timespec="seconds")}] {type(exc).__name__}: {exc}\n')
            if payload is not None:
                safe_payload = dict(payload or {})
                if 'dataUrl' in safe_payload:
                    safe_payload['dataUrl'] = '<omitted>'
                handle.write(json.dumps(safe_payload, ensure_ascii=False)[:4000] + '\n')
            handle.write(traceback.format_exc())
            handle.write('\n---\n')
    except Exception:
        pass


def _safe_asset_slug(value):
    text = re.sub(r'[^A-Za-z0-9_-]+', '-', str(value or '').strip())
    text = re.sub(r'-+', '-', text).strip('-')
    return text[:36] or 'chart'


def _safe_web_user_id(value):
    text = re.sub(r'[^A-Za-z0-9_-]+', '', str(value or '').strip())
    return text if 18 <= len(text) <= 80 else ''


def _positive_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        parsed = int(float(str(value).replace(',', '').strip()))
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _target_reference_count_for_words(value):
    total = _positive_int(value)
    if not total:
        return 0
    scaled = (total + 699) // 700
    floor = 15 if total >= 10000 else 6
    return min(60, max(floor, scaled))


def _persist_data_chart_image(data_url, title='chart'):
    value = str(data_url or '')
    match = re.match(r'^data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=\s]+)$', value)
    if not match:
        return value
    image_type = 'jpg' if match.group(1).lower() in {'jpg', 'jpeg'} else 'png'
    raw = base64.b64decode(re.sub(r'\s+', '', match.group(2)))
    digest = hashlib.sha256(raw).hexdigest()[:14]
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f'{stamp}-{_safe_asset_slug(title)}-{digest}.{image_type}'
    os.makedirs(DATA_CHART_ASSET_DIR, exist_ok=True)
    with open(os.path.join(DATA_CHART_ASSET_DIR, filename), 'wb') as handle:
        handle.write(raw)
    return f'/generated/charts/{filename}'


def _data_chart_reference_payload(result):
    entries = []
    seen = set()
    for item in result.get('referenceEntries', []) if isinstance(result, dict) else []:
        if isinstance(item, dict):
            text = item.get('text', '')
        else:
            text = item
        entry_text = normalize_reference_entry_text(text)
        key = reference_entry_key(entry_text)
        if not key or key in seen:
            continue
        seen.add(key)
        payload = {'text': entry_text, 'key': key}
        if isinstance(item, dict):
            for field in ('sourceName', 'publisher', 'url', 'source', 'note'):
                value = str(item.get(field, '') or '').strip()
                if value:
                    payload[field] = value
        entries.append(payload)
    if len(entries) > 3:
        return [_combine_data_chart_reference_entries(entries)]
    return entries


def _compact_data_chart_reference_values(entries, field, limit=6):
    values = []
    seen = set()
    for entry in entries or []:
        value = re.sub(r'\s+', ' ', str((entry or {}).get(field, '') or '').strip())
        if field != 'url':
            value = re.sub(r'https?://\S+', '', value).strip('，,。.;； ')
            if len(value) > 42:
                value = value[:42].rstrip() + '…'
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _combine_data_chart_reference_entries(entries):
    publishers = _compact_data_chart_reference_values(entries, 'publisher', 3)
    source_names = _compact_data_chart_reference_values(entries, 'sourceName', 3)
    urls = _compact_data_chart_reference_values(entries, 'url', 2)
    if len(publishers) == 1:
        author = publishers[0]
    elif publishers:
        author = '；'.join(publishers[:3]) + ('等' if len(publishers) > 3 else '')
    else:
        author = '相关数据发布机构'

    if source_names:
        title = '、'.join(source_names[:3]) + ('等' if len(source_names) > 3 else '')
    else:
        title = '图表数据来源汇总'

    suffix = f'[R/OL]. {urls[0]}.' if urls else '[R].'
    if len(urls) > 1:
        suffix += f' 另见：{"；".join(urls[1:])}.'
    text = normalize_reference_entry_text(f'{author}. {title}{suffix}')
    return {
        'text': text,
        'key': reference_entry_key(text),
        'sourceName': title,
        'publisher': author,
        'url': urls[0] if urls else '',
        'source': text,
        'note': '同一图表的数据来源已合并引用，逐行来源保留在数据表中。',
    }


_DATA_CHART_SOURCE_SIGNAL_RE = re.compile(
    r'('
    r'\u6570\u636e\u6765\u6e90|\u6570\u636e\u6765\u81ea|\u6765\u6e90\u4e3a|\u6765\u6e90\u4e8e|'
    r'\u6839\u636e|\u4f9d\u636e|\u7edf\u8ba1\u62a5\u544a|\u5e74\u9274|\u516c\u62a5|'
    r'\u767d\u76ae\u4e66|\u53d1\u5e03|\u4e2d\u56fd\u4e92\u8054\u7f51\u7edc\u4fe1\u606f\u4e2d\u5fc3|CNNIC|'
    r'according\s+to|data\s+source|data\s+from|source:|published\s+by|released\s+by'
    r')',
    re.IGNORECASE,
)


def _data_chart_source_terms(reference_entries):
    terms = []
    seen = set()
    ignored_ascii_terms = {
        'http', 'https', 'www', 'com', 'cn', 'net', 'org', 'edu', 'gov',
        'data', 'source', 'url', 'html', 'htm', 'index',
    }

    def add(value):
        text = str(value or '').strip()
        if not text:
            return
        candidates = [text]
        if text.startswith(('http://', 'https://')):
            host = urlparse(text).netloc.lower().removeprefix('www.')
            if host:
                candidates.append(host)
        candidates.extend(
            part.strip()
            for part in re.split(r'[\s,;:，；：。.!?！？\[\]（）()<>]+', text)
            if part.strip()
        )
        for candidate in candidates:
            normalized = re.sub(r'\s+', ' ', candidate).strip()
            key = normalized.lower()
            if len(normalized) < 2 or len(normalized) > 90 or key in seen:
                continue
            if key in ignored_ascii_terms:
                continue
            if re.fullmatch(r'[a-z0-9_-]+', key) and len(key) < 5:
                continue
            seen.add(key)
            terms.append(normalized)

    for entry in reference_entries or []:
        if not isinstance(entry, dict):
            continue
        for field in ('sourceName', 'publisher', 'url', 'source'):
            add(entry.get(field, ''))
    return terms[:24]


def _data_chart_sentence_spans(text):
    content = str(text or '')
    start = 0

    def is_period_boundary(index):
        previous = content[index - 1] if index > 0 else ''
        next_char = content[index + 1] if index + 1 < len(content) else ''
        if previous.isdigit() and next_char.isdigit():
            return False
        if previous.isalnum() and next_char.isalnum():
            return False
        return True

    for index, char in enumerate(content):
        boundary = char in '\u3002\uff01\uff1f!?' or (char == '.' and is_period_boundary(index))
        if char == '\n' or boundary:
            end = index + (1 if boundary else 0)
            sentence = content[start:end]
            if sentence.strip():
                yield start, end, sentence
            start = index + 1
    if start < len(content):
        sentence = content[start:]
        if sentence.strip():
            yield start, len(content), sentence


def _data_chart_sentence_has_source(sentence, source_terms):
    text = str(sentence or '')
    if _DATA_CHART_SOURCE_SIGNAL_RE.search(text):
        return True
    lowered = text.lower()
    return any(term.lower() in lowered for term in source_terms or [])


def _data_chart_sentence_has_citation(sentence, citation):
    return str(citation or '') in str(sentence or '')


def _data_chart_sentence_has_chart_analysis(sentence):
    text = str(sentence or '')
    if re.search(r'图\s*\d+|如图|图表|可视化', text):
        return True
    if re.search(r'\d+(?:\.\d+)?\s*(?:%|％|个百分点|点|亿元|万人|万人次|次|个|项)', text):
        return True
    return bool(re.search(r'最高值|最低值|首末|差距|升至|降至|扩大|缩小|占比|结构|趋势', text))


def _insert_citation_at_sentence_end(text, start, end, citation):
    sentence = text[start:end]
    stripped_end = start + len(sentence.rstrip())
    insert_at = stripped_end
    while insert_at > start and text[insert_at - 1] in '\u3002\uff01\uff1f!?.':
        insert_at -= 1
    return f'{text[:insert_at]}{citation}{text[insert_at:]}'


def _citation_already_marks_source(text, citation, source_terms):
    content = str(text or '')
    citation_text = str(citation or '')
    if not citation_text or citation_text not in content:
        return False
    for match in re.finditer(re.escape(citation_text), content):
        window = content[max(0, match.start() - 180): min(len(content), match.end() + 80)]
        if _data_chart_sentence_has_source(window, source_terms):
            return True
    return False


def _insert_data_source_citation(text, citation, reference_entries):
    content = str(text or '')
    citation_text = str(citation or '').strip()
    if not content or not citation_text:
        return content

    source_terms = _data_chart_source_terms(reference_entries)
    if _citation_already_marks_source(content, citation_text, source_terms):
        return content

    working = re.sub(r'\s*' + re.escape(citation_text) + r'\s*$', '', content.rstrip())
    spans = list(_data_chart_sentence_spans(working))
    if any(_data_chart_sentence_has_citation(sentence, citation_text) for _, _, sentence in spans):
        return working

    for start, end, sentence in reversed(spans):
        stripped = sentence.strip()
        if not stripped or stripped.startswith('![') or stripped.startswith('<img'):
            continue
        if _data_chart_sentence_has_chart_analysis(sentence):
            return _insert_citation_at_sentence_end(working, start, end, citation_text)

    for start, end, sentence in spans:
        stripped = sentence.strip()
        if stripped.startswith('![') or stripped.startswith('<img'):
            continue
        if _data_chart_sentence_has_source(sentence, source_terms):
            return _insert_citation_at_sentence_end(working, start, end, citation_text)

    for start, end, sentence in spans:
        stripped = sentence.strip()
        if not stripped or stripped.startswith('![') or stripped.startswith('<img'):
            continue
        return _insert_citation_at_sentence_end(working, start, end, citation_text)

    return f'{working.rstrip()}{citation_text}'


def _caption_with_data_citation(caption, citation):
    content = re.sub(r'\s+', ' ', str(caption or '').strip())
    if not content:
        return content
    content = re.sub(r'\s*\[[\d,\-\s]+\]\s*$', '', content)
    chart_type_match = re.match(
        r'^(.+?[（(](?:柱状图|条形图|折线图|结构图|饼图|图表|line chart|bar chart|pie chart|chart)[）)])',
        content,
        flags=re.IGNORECASE,
    )
    if chart_type_match:
        content = chart_type_match.group(1).strip()
    elif not re.match(r'^(?:图\s*\d+|Figure\s*\d+)\b', content, flags=re.IGNORECASE):
        content = re.split(r'[\u3002.!?！？]\s*', content, maxsplit=1)[0].strip() or content
    return re.sub(
        r'[（(](?:柱状图|条形图|折线图|结构图|饼图|图表|line chart|bar chart|pie chart|chart)[）)]\s*$',
        '',
        content,
        flags=re.IGNORECASE,
    ).strip()


def _rewrite_figure_markdown_caption(figure_markdown, citation):
    text = str(figure_markdown or '').strip()
    if not text:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^(?:图\s*\d+|Figure\s*\d+)\b', stripped, flags=re.IGNORECASE):
            lines[index] = _caption_with_data_citation(stripped, '')
            return '\n'.join(lines).strip()
    return f'{text}\n\n图1 数据图表'


def _append_data_chart_references(all_sections, section_title, replacement_text, reference_entries, reference_style='GB/T 7714', original_text=''):
    if not reference_entries:
        return {
            'content': replacement_text,
            'references': None,
            'updatedSections': [],
            'citationNumber': None,
        }

    sections = list(all_sections or [])
    ref_title = '# 参考文献'
    existing_refs = []
    current_position = len(sections)
    current_found = False
    for index, section in enumerate(sections):
        title = str(section.get('title', '') or '').strip()
        if title == section_title:
            current_position = index
            current_found = True
        if _is_reference_section_title(title) or is_reference_section(title):
            ref_title = title or ref_title
            existing_refs = parse_reference_entries(section.get('content', ''))

    current_entries = merge_reference_entry_lists(existing_refs, reference_entries)
    existing_keys = {entry.get('key') for entry in existing_refs}
    added_entries = [entry for entry in current_entries if entry.get('key') not in existing_keys]
    if not added_entries and reference_entries:
        added_entries = reference_entries[:1]

    existing_number_map = build_reference_number_map(existing_refs)
    max_existing_number = max(existing_number_map.keys(), default=0)
    temp_entries = []
    temp_number_by_key = {}
    for index, entry in enumerate(reference_entries, start=1):
        text = normalize_reference_entry_text(entry.get('text', ''))
        key = reference_entry_key(text)
        if not key:
            continue
        temp_number = max_existing_number + index
        temp_number_by_key[key] = temp_number
        temp_entries.append({'text': text, 'key': key, 'number': temp_number})

    citation_numbers = [
        temp_number_by_key.get(entry.get('key') or reference_entry_key(entry.get('text', '')))
        for entry in reference_entries
    ]
    citation_numbers = [number for number in citation_numbers if number]
    citation = ''
    if citation_numbers:
        citation = '[' + ','.join(str(number) for number in dict.fromkeys(citation_numbers)) + ']'

    content_with_citation = _insert_data_source_citation(replacement_text, citation, reference_entries) if citation else replacement_text
    content_for_reference_order = f'{content_with_citation.rstrip()}\n\n{citation}'.strip() if citation else content_with_citation

    local_number_map = build_reference_number_map(temp_entries)
    key_to_text = {}
    for entry in current_entries:
        key = entry.get('key') or reference_entry_key(entry.get('text', ''))
        text = normalize_reference_entry_text(entry.get('text', ''))
        if key and text:
            key_to_text[key] = text

    combined_current_map = dict(existing_number_map)
    combined_current_map.update(local_number_map)
    current_section_content = ''
    for section in sections:
        if str(section.get('title', '') or '').strip() == section_title:
            current_section_content = str(section.get('content', '') or '')
            break
    original = str(original_text or '').strip()
    if current_section_content:
        if original and original in current_section_content:
            current_content_for_order = current_section_content.replace(original, content_for_reference_order, 1)
        else:
            current_content_for_order = f'{current_section_content.rstrip()}\n\n{content_for_reference_order}'.strip()
    else:
        current_content_for_order = content_for_reference_order
    ordered_keys = []
    seen_keys = set()

    def remember(key):
        if key and key in key_to_text and key not in seen_keys:
            seen_keys.add(key)
            ordered_keys.append(key)

    for index, section in enumerate(sections):
        title = str(section.get('title', '') or '').strip()
        if _is_reference_section_title(title) or is_reference_section(title):
            continue
        if index == current_position:
            for key in collect_citation_reference_keys(current_content_for_order, combined_current_map):
                remember(key)
            continue
        content = normalize_section_body(section.get('content', ''))
        for key in collect_citation_reference_keys(content, existing_number_map):
            remember(key)

    if not current_found:
        for key in collect_citation_reference_keys(content_with_citation, local_number_map):
            remember(key)
    for entry in current_entries:
        remember(entry.get('key') or reference_entry_key(entry.get('text', '')))

    full_entries = [
        {'number': index, 'text': key_to_text[key], 'key': key}
        for index, key in enumerate(ordered_keys, start=1)
        if key_to_text.get(key)
    ]
    new_number_by_key = {entry['key']: entry['number'] for entry in full_entries}
    for entry in existing_number_map.values():
        entry['new_number'] = new_number_by_key.get(entry.get('key'))
    for entry in local_number_map.values():
        entry['new_number'] = new_number_by_key.get(entry.get('key'))

    updated_content = rewrite_citations_with_entry_map(content_with_citation, combined_current_map)
    updated_sections = []
    for index, section in enumerate(sections):
        if index == current_position:
            continue
        title = str(section.get('title', '') or '').strip()
        if _is_reference_section_title(title) or is_reference_section(title):
            continue
        content = normalize_section_body(section.get('content', ''))
        rewritten = rewrite_citations_with_entry_map(content, existing_number_map)
        if rewritten != content:
            updated_sections.append({'title': title, 'content': rewritten})

    return {
        'content': updated_content,
        'references': {
            'mode': 'reorder',
            'title': ref_title,
            'content': build_reference_body_from_entries(full_entries),
            'entryCount': len(full_entries),
        },
        'updatedSections': updated_sections,
        'citation': rewrite_citations_with_entry_map(citation, combined_current_map) if citation else '',
        'citationNumber': next((entry.get('new_number') for entry in local_number_map.values() if entry.get('new_number')), None),
    }


def _strip_outline_title_markup(title):
    text = str(title or '').strip()
    if not text:
        return ''
    markers = ('***', '___', '**', '__', '*', '_')
    changed = True
    while changed and text:
        changed = False
        for marker in markers:
            if text.startswith(marker) and text.endswith(marker) and len(text) > len(marker) * 2:
                inner = text[len(marker):-len(marker)].strip()
                if inner:
                    text = inner
                    changed = True
                    break
    text = re.sub(r'^\s*[-*•]\s+', '', text)
    original = text.strip()
    patterns = (
        r'^\s{0,8}#{1,6}\s+(.+)$',
        r'^\s{0,8}\d+(?:\.\d+)*[、.．]?\s+(.+)$',
        r'^\s{0,8}[一二三四五六七八九十]+[、.．]\s*(.+)$',
        r'^\s{0,8}第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*[:：]?\s+(.+)$',
        r'^\s{0,8}（[一二三四五六七八九十百千万]+）\s*(.+)$',
        r'^\s{0,8}\(\d+\)\s*(.+)$',
    )
    for pattern in patterns:
        match = re.match(pattern, original)
        if match and match.group(1).strip():
            return match.group(1).strip('：:').strip()
    return original.strip('：:').strip()


def _section_match_key(title):
    return re.sub(r'\s+', '', _strip_outline_title_markup(title)).lower()


def _is_reference_section_title(title):
    return _section_match_key(title) in {'参考文献', 'references', 'bibliography', 'reference'}


def _is_reference_linkable_section_title(title):
    key = _section_match_key(title)
    return bool(key) and key not in {
        '摘要', '中文摘要', '内容摘要', '摘要与关键词',
        'abstract', 'abstractandkeywords',
        '关键词', '关键字', '中文关键词', '中文关键字',
        'keywords', 'keywords', 'key words'.replace(' ', ''),
        '参考文献', 'references', 'bibliography',
    }


TEMPLATE_HEADING_PATTERNS = (
    (r'^\s{0,8}#{1,6}\s+(.+)$', None),
    (r'^\s{0,8}(摘要|中文摘要|英文摘要|abstract|关键词|keywords|引言|绪论|结论|参考文献|references|附录)\s*$', 1),
    (r'^\s{0,8}第[一二三四五六七八九十百千万\d]+章\s*[:：]?\s*(.+)$', 1),
    (r'^\s{0,8}第[一二三四五六七八九十百千万\d]+节\s*[:：]?\s*(.+)$', 2),
    (r'^\s{0,8}(\d+)[、.．]\s*[^\d\s].{1,80}$', 1),
    (r'^\s{0,8}(\d+)\s+[^\d\s].{1,80}$', 1),
    (r'^\s{0,8}(\d+\.\d+)\s+.{1,80}$', 2),
    (r'^\s{0,8}(\d+\.\d+\.\d+)\s+.{1,80}$', 3),
    (r'^\s{0,8}[一二三四五六七八九十]+[、.．]\s*(.{1,80})$', 2),
    (r'^\s{0,8}（[一二三四五六七八九十]+）\s*(.{1,80})$', 3),
)


def _clean_template_heading(text):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    text = text.strip('：:;；,.，。')
    if not text or len(text) > 120:
        return ''
    return text


def _heading_from_line(line):
    raw = _clean_template_heading(line)
    if not raw:
        return None
    if re.search(r'[。！？!?；;]$', raw) and not re.match(r'^\s{0,8}#{1,6}\s+', line or ''):
        return None
    for pattern, level in TEMPLATE_HEADING_PATTERNS:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if not match:
            continue
        if pattern.startswith('^\\s{0,8}#{1,6}'):
            level = min(6, len(re.match(r'^\s*(#{1,6})', raw).group(1)))
            title = re.sub(r'^\s*#{1,6}\s+', '', raw).strip()
        elif level == 1 and match.lastindex == 1 and match.group(1) != raw:
            title = raw
        else:
            title = raw
        title = _clean_template_heading(title)
        if title:
            return {'level': int(level or 1), 'title': title}
    return None


def _dedupe_template_headings(headings, limit=120):
    result = []
    seen = set()
    for item in headings:
        title = _clean_template_heading((item or {}).get('title', ''))
        if not title:
            continue
        level = max(1, min(6, int((item or {}).get('level') or 1)))
        key = (level, re.sub(r'\s+', '', title).lower())
        if key in seen:
            continue
        seen.add(key)
        result.append({'level': level, 'title': title})
        if len(result) >= limit:
            break
    return result


def _headings_from_plain_text(text):
    headings = []
    for line in str(text or '').splitlines():
        heading = _heading_from_line(line)
        if heading:
            headings.append(heading)
    return _dedupe_template_headings(headings)


TOC_PAGE_TOKEN_PATTERN = r'(?:第?\s*\d{1,4}\s*页?|[ivxlcdmIVXLCDM]{1,10}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]{1,8}|[一二三四五六七八九十百千万]{1,8})'
TOC_LEADER_PATTERN = r'(?:\.{2,}|…+|⋯+|·{2,}|•{2,}|_{2,}|-{2,})'


def _is_toc_title(line):
    text = re.sub(r'\s+', ' ', str(line or '')).strip().strip('：:')
    if not text:
        return False
    compact = re.sub(r'\s+', '', text).lower()
    return compact in {'目录', '目次', 'contents', 'tableofcontents'}


def _is_standalone_toc_page_number(line):
    text = re.sub(r'\s+', '', str(line or '')).strip()
    return bool(text and re.fullmatch(TOC_PAGE_TOKEN_PATTERN, text, flags=re.IGNORECASE))


def _strip_toc_page_number(line):
    raw = str(line or '').replace('\u3000', ' ').strip()
    if not raw:
        return ''
    text = re.sub(
        rf'\s*{TOC_LEADER_PATTERN}\s*{TOC_PAGE_TOKEN_PATTERN}\s*$',
        '',
        raw,
        flags=re.IGNORECASE,
    ).strip()
    text = re.sub(rf'[\t ]{{2,}}{TOC_PAGE_TOKEN_PATTERN}\s*$', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+', ' ', text).strip()
    candidate = re.sub(rf'\s+{TOC_PAGE_TOKEN_PATTERN}\s*$', '', text, flags=re.IGNORECASE).strip()
    if candidate != text and _heading_from_line(candidate):
        text = candidate
    return _clean_template_heading(text)


def _has_toc_page_marker(line):
    raw = str(line or '')
    if re.search(rf'{TOC_LEADER_PATTERN}\s*{TOC_PAGE_TOKEN_PATTERN}\s*$', raw, flags=re.IGNORECASE):
        return True
    if re.search(rf'[\t ]{{2,}}{TOC_PAGE_TOKEN_PATTERN}\s*$', raw, flags=re.IGNORECASE):
        return True
    return _strip_toc_page_number(raw) != _clean_template_heading(raw)


def _template_heading_key(title):
    key = re.sub(r'\s+', '', _clean_template_heading(title)).lower()
    if key in {'摘要', '中文摘要', '内容摘要'}:
        return '中文摘要'
    if key in {'abstract', '英文摘要', 'englishabstract'}:
        return '英文摘要'
    return key


def _is_probable_body_start(line):
    raw = _clean_template_heading(line)
    if not raw:
        return False
    return bool(re.match(
        r'^(摘要|中文摘要|英文摘要|abstract|关键词|keywords|引言|绪论|第[一二三四五六七八九十百千万\d]+章)',
        raw,
        flags=re.IGNORECASE,
    ))


def _extract_toc_lines(text):
    raw_lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').replace('\f', '\n\f\n').split('\n')
    toc_index = next((index for index, line in enumerate(raw_lines[:120]) if _is_toc_title(line)), -1)
    if toc_index < 0:
        return []

    collected = []
    seen = set()
    blank_count = 0
    noise_count = 0
    scanned = 0
    marker_count = 0
    for raw_line in raw_lines[toc_index + 1:]:
        scanned += 1
        if scanned > 320:
            break
        if str(raw_line).strip() == '\f':
            if len(collected) >= 2 and blank_count >= 1:
                break
            blank_count += 1
            continue
        if _is_toc_title(raw_line):
            continue
        if not str(raw_line or '').strip():
            if collected:
                blank_count += 1
                if blank_count >= 6 and len(collected) >= 2:
                    break
            continue
        if _is_standalone_toc_page_number(raw_line):
            continue

        line = _strip_toc_page_number(raw_line)
        heading = _heading_from_line(line)
        has_marker = _has_toc_page_marker(raw_line)
        key = _template_heading_key(line)

        if heading and collected and not has_marker:
            if (
                key in seen
                or (
                    len(collected) >= 2
                    and _is_probable_body_start(line)
                    and (blank_count >= 1 or marker_count >= 2)
                )
            ):
                break

        if heading:
            collected.append(line)
            seen.add(key)
            if has_marker:
                marker_count += 1
            blank_count = 0
            noise_count = 0
            if len(collected) >= 120:
                break
            continue

        if collected:
            noise_count += 1
            if noise_count >= 8 and len(collected) >= 2:
                break

    return collected


def _headings_from_toc_text(text):
    headings = []
    for line in _extract_toc_lines(text):
        heading = _heading_from_line(line)
        if heading:
            headings.append(heading)
    return _dedupe_template_headings(headings)


def _headings_from_outline_like_text(text):
    raw_lines = [line for line in str(text or '').splitlines() if _clean_template_heading(line)]
    if not raw_lines or len(raw_lines) > 180 or len(str(text or '')) > 30000:
        return []
    headings = _headings_from_plain_text('\n'.join(raw_lines))
    if len(headings) < 2:
        return []
    return headings


def _headings_from_template_text(text, allow_outline_fallback=False):
    headings = _headings_from_toc_text(text)
    if headings or not allow_outline_fallback:
        return headings
    return _headings_from_outline_like_text(text)


def _decode_template_text_bytes(data):
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'utf-16', 'utf-16le'):
        try:
            text = data.decode(encoding)
        except Exception:
            continue
        if text and text.count('\ufffd') < max(2, len(text) // 100):
            return text
    return data.decode('utf-8', errors='ignore')


def _parse_doc_like_text(data):
    head = data[:4096].lstrip().lower()
    if head.startswith(b'{\\rtf'):
        text = _decode_template_text_bytes(data)
        text = re.sub(r'\\par[d]?|\\line', '\n', text)
        text = re.sub(r'\\u(-?\d+)\??', lambda m: chr(int(m.group(1)) % 65536), text)
        text = re.sub(r'\\[a-zA-Z]+-?\d* ?', '', text)
        text = re.sub(r'[{}]', '', text)
        return _headings_from_template_text(text)
    if b'<html' in head or b'<!doctype html' in head or b'<body' in head:
        text = _decode_template_text_bytes(data)
        text = re.sub(r'(?is)<(h[1-6]|p|div|br|li)[^>]*>', '\n', text)
        text = re.sub(r'(?is)<[^>]+>', '', text)
        text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                    .replace('&lt;', '<').replace('&gt;', '>'))
        return _headings_from_template_text(text)
    if not data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return _headings_from_template_text(_decode_template_text_bytes(data))
    return []


def _extract_doc_binary_text_candidates(data):
    chunks = []
    for encoding in ('utf-16le', 'gb18030'):
        try:
            text = data.decode(encoding, errors='ignore')
        except Exception:
            continue
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]+', '\n', text)
        text = re.sub(r'\s{2,}', '\n', text)
        chunks.append(text)

    ascii_like = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]+', b'\n', data)
    try:
        chunks.append(ascii_like.decode('gb18030', errors='ignore'))
    except Exception:
        pass
    return '\n'.join(chunks)


def _parse_doc_binary_heuristic(data):
    text = _extract_doc_binary_text_candidates(data)
    if not text:
        return []
    lines = []
    for raw_line in text.splitlines():
        line = _clean_template_heading(raw_line)
        if not line:
            continue
        if any(keyword in line for keyword in ('Evaluation Only', 'Microsoft Word', 'Word.Document')):
            continue
        lines.append(line)
    return _headings_from_template_text('\n'.join(lines))


def _parse_docx_template(data):
    from docx import Document

    document = Document(io.BytesIO(data))
    paragraph_texts = []
    for paragraph in document.paragraphs:
        text = _clean_template_heading(paragraph.text)
        if text:
            paragraph_texts.append(text)
    return _headings_from_template_text('\n'.join(paragraph_texts))


def _parse_doc_with_soffice(path, temp_dir):
    soffice = _find_soffice()
    if not soffice:
        return []
    subprocess.run(
        [soffice, '--headless', '--convert-to', 'txt:Text', '--outdir', temp_dir, path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    txt_path = os.path.join(temp_dir, os.path.splitext(os.path.basename(path))[0] + '.txt')
    if not os.path.isfile(txt_path):
        return []
    with open(txt_path, 'r', encoding='utf-8', errors='ignore') as handle:
        return _headings_from_template_text(handle.read())


def _find_soffice():
    env_candidates = []
    for key in ('SOFFICE_PATH', 'LIBREOFFICE_HOME'):
        value = os.environ.get(key, '').strip()
        if not value:
            continue
        env_candidates.append(value if value.lower().endswith('.exe') else os.path.join(value, 'program', 'soffice.exe'))

    project_candidates = (
        os.path.join(PROJECT_DIR, 'tools', 'LibreOffice', 'program', 'soffice.exe'),
        os.path.join(PROJECT_DIR, 'tools', 'LibreOfficePortable', 'App', 'libreoffice', 'program', 'soffice.exe'),
        os.path.join(PROJECT_DIR, 'LibreOffice', 'program', 'soffice.exe'),
    )
    system_candidates = (
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    )
    return (
        shutil.which('soffice')
        or shutil.which('libreoffice')
        or next((candidate for candidate in [*env_candidates, *project_candidates, *system_candidates] if os.path.isfile(candidate)), None)
    )


def _convert_doc_to_docx_with_soffice(path, temp_dir):
    soffice = _find_soffice()
    if not soffice:
        return ''
    subprocess.run(
        [soffice, '--headless', '--convert-to', 'docx', '--outdir', temp_dir, path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
    )
    docx_path = os.path.join(temp_dir, os.path.splitext(os.path.basename(path))[0] + '.docx')
    return docx_path if os.path.isfile(docx_path) else ''


def _convert_doc_to_docx_with_word_subprocess(path, temp_dir):
    output_path = os.path.join(temp_dir, 'template_converted.docx')
    script = r'''
import os
import sys
import pythoncom
import win32com.client

source_path = os.path.abspath(sys.argv[1])
output_path = os.path.abspath(sys.argv[2])
pythoncom.CoInitialize()
word = None
document = None
try:
    word = win32com.client.DispatchEx('Word.Application')
    word.Visible = False
    word.DisplayAlerts = 0
    document = word.Documents.Open(source_path, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False, Visible=False)
    document.SaveAs2(output_path, FileFormat=16)
finally:
    if document is not None:
        document.Close(False)
    if word is not None:
        word.Quit()
    pythoncom.CoUninitialize()
'''
    subprocess.run(
        [sys.executable, '-c', script, path, output_path],
        check=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=8,
    )
    return output_path if os.path.isfile(output_path) else ''


def _parse_doc_via_docx_conversion(path, temp_dir):
    errors = []
    for convert in (_convert_doc_to_docx_with_soffice, _convert_doc_to_docx_with_word_subprocess):
        try:
            docx_path = convert(path, temp_dir)
            if not docx_path:
                continue
            with open(docx_path, 'rb') as handle:
                headings = _parse_docx_template(handle.read())
            if headings:
                return headings
        except subprocess.TimeoutExpired:
            errors.append('DOC 转 DOCX 超时')
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError('；'.join(errors[:2]))
    return []


def _parse_doc_with_word_subprocess(path):
    script = r'''
import json
import os
import sys
import pythoncom
import win32com.client

pythoncom.CoInitialize()
word = None
document = None
try:
    word = win32com.client.DispatchEx('Word.Application')
    word.Visible = False
    document = word.Documents.Open(os.path.abspath(sys.argv[1]), ReadOnly=True, AddToRecentFiles=False)
    lines = []
    for paragraph in document.Paragraphs:
        text = str(paragraph.Range.Text or '').replace('\r', '').replace('\x07', '').strip()
        if text:
            lines.append(text)
    print(json.dumps(lines[:400], ensure_ascii=False))
finally:
    if document is not None:
        document.Close(False)
    if word is not None:
        word.Quit()
    pythoncom.CoUninitialize()
'''
    completed = subprocess.run(
        [sys.executable, '-c', script, path],
        check=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=8,
    )
    payload = json.loads((completed.stdout or '[]').strip() or '[]')
    if isinstance(payload, list):
        return _headings_from_template_text('\n'.join(str(item) for item in payload))
    return []


def _parse_doc_template(data):
    headings = _parse_doc_like_text(data)
    if headings:
        return headings
    with tempfile.TemporaryDirectory(prefix='paper_template_') as temp_dir:
        path = os.path.join(temp_dir, 'template.doc')
        with open(path, 'wb') as handle:
            handle.write(data)
        errors = []
        try:
            headings = _parse_doc_via_docx_conversion(path, temp_dir)
            if headings:
                return headings
        except Exception as exc:
            errors.append(str(exc))
        try:
            headings = _parse_doc_with_soffice(path, temp_dir)
            if headings:
                return headings
        except Exception as exc:
            errors.append(str(exc))
        try:
            headings = _parse_doc_binary_heuristic(data)
            if headings:
                return headings
        except Exception as exc:
            errors.append(str(exc))
        try:
            headings = _parse_doc_with_word_subprocess(path)
            if headings:
                return headings
        except subprocess.TimeoutExpired:
            errors.append('Word 后台打开 DOC 超时')
        except Exception as exc:
            errors.append(str(exc))
    clean_errors = []
    for error in errors:
        error = str(error).strip()
        if not error:
            continue
        if 'Command ' in error and 'timed out' in error:
            error = 'Word 后台打开 DOC 超时'
        if error not in clean_errors:
            clean_errors.append(error)
    detail = f'：{"；".join(clean_errors[:2])}' if clean_errors else ''
    raise ValueError(f'DOC 模板读取失败，请运行 installers\\install_libreoffice_windows.ps1 安装 LibreOffice 后重试，或将模板另存为 DOCX/PDF 后上传{detail}')


def _parse_pdf_template(data):
    import fitz

    doc = fitz.open(stream=data, filetype='pdf')
    lines = []
    for page_index in range(min(len(doc), 30)):
        page = doc[page_index]
        text = page.get_text('text') or ''
        lines.extend(text.splitlines())
    doc.close()
    return _headings_from_template_text('\n'.join(lines))


def _parse_template_headings(filename, mime_type, data):
    suffix = os.path.splitext(filename or '')[1].lower()
    if suffix == '.doc':
        return _parse_doc_template(data)
    if suffix == '.docx':
        return _parse_docx_template(data)
    if suffix == '.pdf':
        return _parse_pdf_template(data)
    if suffix in {'.txt', '.md', '.markdown'} or str(mime_type or '').startswith('text/'):
        return _headings_from_template_text(data.decode('utf-8', errors='ignore'), allow_outline_fallback=True)
    raise ValueError('仅支持 DOC、DOCX、PDF、TXT、Markdown 模板')


class WebWorkbench:
    def __init__(self):
        runtime_paths = get_runtime_paths()
        self.runtime_paths = runtime_paths
        self.base_user_data_root = os.path.join(runtime_paths.base_data_root, WEB_USERS_DIR_NAME)
        os.makedirs(self.base_user_data_root, exist_ok=True)
        self._session_lock = threading.RLock()
        self._sessions = {}
        self._ppt_lock = threading.RLock()
        self._ppt_jobs = {}
        self._default_session = self._create_session(runtime_paths.base_data_root)

    def _create_session(self, data_root):
        config = ConfigManager(data_root)
        api_client = APIClient(config)
        return {
            'config': config,
            'api_client': api_client,
            'ai_reducer': AIReducer(api_client),
            'plagiarism': PlagiarismReducer(api_client),
            'polisher': AcademicPolisher(api_client),
            'paper_writer': PaperWriter(api_client),
            'corrector': IntelligentCorrector(api_client),
            'data_chart': DataChartAssistant(api_client),
        }

    def _session_for_user(self, user_id=''):
        safe_id = _safe_web_user_id(user_id)
        if not safe_id:
            return self._default_session
        with self._session_lock:
            session = self._sessions.get(safe_id)
            if session:
                return session
            data_root = os.path.join(self.base_user_data_root, safe_id)
            os.makedirs(data_root, exist_ok=True)
            session = self._create_session(data_root)
            self._sessions[safe_id] = session
            return session

    def _config(self, user_id=''):
        return self._session_for_user(user_id)['config']

    def _api_client(self, user_id=''):
        return self._session_for_user(user_id)['api_client']

    def _ai_reducer(self, user_id=''):
        return self._session_for_user(user_id)['ai_reducer']

    def _plagiarism(self, user_id=''):
        return self._session_for_user(user_id)['plagiarism']

    def _polisher(self, user_id=''):
        return self._session_for_user(user_id)['polisher']

    def _paper_writer(self, user_id=''):
        return self._session_for_user(user_id)['paper_writer']

    def _corrector(self, user_id=''):
        return self._session_for_user(user_id)['corrector']

    def _data_chart(self, user_id=''):
        return self._session_for_user(user_id)['data_chart']

    @property
    def config(self):
        return self._default_session['config']

    @property
    def api_client(self):
        return self._default_session['api_client']

    @property
    def ai_reducer(self):
        return self._default_session['ai_reducer']

    @property
    def plagiarism(self):
        return self._default_session['plagiarism']

    @property
    def polisher(self):
        return self._default_session['polisher']

    @property
    def paper_writer(self):
        return self._default_session['paper_writer']

    @property
    def corrector(self):
        return self._default_session['corrector']

    @property
    def data_chart(self):
        return self._default_session['data_chart']

    def user_metadata(self, user_id=''):
        safe_id = _safe_web_user_id(user_id)
        return {
            'userScopedConfig': bool(safe_id),
            'userIdSuffix': safe_id[-6:] if safe_id else '',
        }

    def status(self, user_id=''):
        config = self._config(user_id)
        active_id = config.active_api
        active_cfg = config.get_api_config(active_id) if active_id else {}
        providers = []
        for api_id, cfg in config.list_saved_apis():
            providers.append({
                'id': api_id,
                'name': cfg.get('name') or api_id,
                'model': resolve_model_display_name(cfg),
                'active': api_id == active_id,
                'configured': bool(str(cfg.get('key', '') or '').strip()),
                'publicDefault': bool(cfg.get('public_default')),
            })
        return {
            'activeApi': active_id,
            'activeName': active_cfg.get('name') or active_id or '',
            'activeModel': resolve_model_display_name(active_cfg) if active_cfg else '',
            'configured': bool(active_id and active_cfg and str(active_cfg.get('key', '') or '').strip()),
            'providers': providers,
            **self.user_metadata(user_id),
        }

    def _public_api_record(self, api_id, cfg, user_id=''):
        config = self._config(user_id)
        provider_type = normalize_provider_type(cfg.get('provider_type') or api_id)
        return {
            'id': api_id,
            'name': cfg.get('name') or api_id,
            'providerType': provider_type,
            'providerLabel': PRESET_MAP.get(provider_type, PRESET_MAP['custom']).get('label', provider_type),
            'baseUrl': cfg.get('base_url', ''),
            'model': cfg.get('model', ''),
            'modelDisplayName': resolve_model_display_name(cfg),
            'timeout': cfg.get('timeout', ''),
            'active': api_id == config.active_api,
            'configured': bool(str(cfg.get('key', '') or '').strip()),
            'hasKey': bool(str(cfg.get('key', '') or '').strip()),
            'apiFormat': cfg.get('api_format', ''),
            'publicDefault': bool(cfg.get('public_default')),
        }

    def config_payload(self, user_id=''):
        config = self._config(user_id)
        presets = []
        for preset_id, label, defaults in PRESET_OPTIONS:
            presets.append({
                'id': preset_id,
                'label': label,
                'defaults': defaults,
                'staticModels': get_static_models(preset_id),
            })
        records = [
            self._public_api_record(api_id, cfg, user_id=user_id)
            for api_id, cfg in config.list_saved_apis()
        ]
        return {
            'activeApi': config.active_api,
            'providers': records,
            'presets': presets,
            **self.user_metadata(user_id),
        }

    def save_api(self, payload, user_id=''):
        config = self._config(user_id)
        api_id = str(payload.get('id', '') or '').strip()
        if config.is_public_default_api(api_id):
            raise ValueError('公共默认接口不可编辑；如需使用自己的配置，请点击“新建接口”。')
        provider_type = normalize_provider_type(payload.get('providerType') or 'custom')
        existing = config.get_api_config(api_id) if api_id else {}
        cfg = merge_with_preset_defaults(existing, provider_type)

        for src, dst in (
            ('name', 'name'),
            ('remark', 'remark'),
            ('website', 'website'),
            ('baseUrl', 'base_url'),
            ('model', 'model'),
            ('modelDisplayName', 'model_display_name'),
            ('timeout', 'timeout'),
            ('apiFormat', 'api_format'),
        ):
            if src in payload:
                cfg[dst] = str(payload.get(src, '') or '').strip()

        key_value = str(payload.get('key', '') or '')
        if key_value.strip():
            cfg['key'] = key_value.strip()
        elif not api_id:
            cfg['key'] = ''

        cfg['provider_type'] = provider_type
        if not cfg.get('name'):
            cfg['name'] = PRESET_MAP.get(provider_type, PRESET_MAP['custom']).get('label', provider_type)

        if not str(cfg.get('name', '') or '').strip():
            raise ValueError('请输入接口名称')
        if not str(cfg.get('base_url', '') or '').strip():
            raise ValueError('请输入请求地址')
        if not str(cfg.get('model', '') or '').strip():
            raise ValueError('请选择或填写模型 ID')

        duplicate_id = config.find_api_id_by_name(cfg.get('name', ''), exclude_api_id=api_id or None)
        if duplicate_id:
            raise ValueError('接口名称已存在，请换一个名称')

        target_id = api_id or config.generate_api_id()
        config.set_api_config(target_id, cfg)
        if bool(payload.get('activate', True)):
            config.active_api = target_id
        config.save()
        return {
            'record': self._public_api_record(target_id, config.get_api_config(target_id), user_id=user_id),
            'config': self.config_payload(user_id),
        }

    def activate_api(self, payload, user_id=''):
        config = self._config(user_id)
        api_id = str(payload.get('id', '') or '').strip()
        if not config.get_api_config(api_id):
            raise ValueError('接口不存在')
        config.active_api = api_id
        config.save()
        return self.config_payload(user_id)

    def fetch_models_for_payload(self, payload, user_id=''):
        config = self._config(user_id)
        api_client = self._api_client(user_id)
        api_id = str(payload.get('id', '') or '').strip()
        provider_type = normalize_provider_type(payload.get('providerType') or 'custom')
        existing = config.get_api_config(api_id) if api_id else {}
        cfg = merge_with_preset_defaults(existing, provider_type)
        for src, dst in (
            ('name', 'name'),
            ('baseUrl', 'base_url'),
            ('model', 'model'),
            ('modelDisplayName', 'model_display_name'),
            ('timeout', 'timeout'),
            ('apiFormat', 'api_format'),
        ):
            if src in payload:
                cfg[dst] = str(payload.get(src, '') or '').strip()
        if str(payload.get('key', '') or '').strip():
            cfg['key'] = str(payload.get('key', '') or '').strip()
        models = api_client.fetch_models(api_id or provider_type, cfg=cfg)
        return {'models': models}

    def parse_template(self, payload):
        filename = os.path.basename(str(payload.get('filename', '') or '论文模板')).strip() or '论文模板'
        mime_type = str(payload.get('mimeType', '') or '').strip()
        data_url = str(payload.get('dataUrl', '') or '')
        if ',' not in data_url:
            raise ValueError('模板文件内容无效')
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
        if not raw:
            raise ValueError('模板文件为空')
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError('模板文件过大，请上传 12MB 以内的 DOC、DOCX、PDF、TXT 或 Markdown 文件')
        headings = _parse_template_headings(filename, mime_type, raw)
        if not headings:
            raise ValueError('未识别到可用目录结构，请上传包含目录的论文模板')
        levels = sorted({item['level'] for item in headings})
        return {
            'filename': filename,
            'fileType': os.path.splitext(filename)[1].lower().lstrip('.') or mime_type or 'template',
            'summary': f'已读取目录结构：{len(headings)} 个标题，层级包含：{", ".join(str(level) for level in levels)}',
            'headings': headings,
        }

    def run_data_chart(self, payload, user_id=''):
        data_chart = self._data_chart(user_id)
        action = str(payload.get('action', '') or '').strip()
        target = payload.get('target') if isinstance(payload.get('target'), dict) else {}
        if action == 'find':
            return data_chart.find_targets(
                payload.get('fullText', '') or payload.get('text', ''),
                topic=payload.get('topic', ''),
                outline=payload.get('outline', ''),
                sections=payload.get('sections'),
                limit=payload.get('limit', 8),
            )
        if action == 'search':
            return data_chart.search_data(
                query=payload.get('query', ''),
                target=target,
                full_text=payload.get('fullText', '') or payload.get('text', ''),
                user_data=payload.get('userData', ''),
                data_file=payload.get('dataFile') if isinstance(payload.get('dataFile'), dict) else None,
            )
        if action == 'generate':
            result = data_chart.generate_chart(
                table_text=payload.get('tableText', ''),
                chart_type=payload.get('chartType', 'bar'),
                title=payload.get('title', ''),
                unit=payload.get('unit', ''),
                target=target,
            )
            chart = result.get('chart') if isinstance(result, dict) else None
            if isinstance(chart, dict):
                image_url = _persist_data_chart_image(chart.get('dataUrl'), chart.get('title') or payload.get('title', 'chart')) if chart.get('dataUrl') else ''
                if image_url:
                    chart['imageUrl'] = image_url
                    chart['dataUrl'] = image_url
            reference_entries = _data_chart_reference_payload(result)
            reference_result = _append_data_chart_references(
                payload.get('allSections', []),
                target.get('sectionTitle', '') if isinstance(target, dict) else '',
                result.get('replacementText', ''),
                reference_entries,
                payload.get('referenceStyle', 'GB/T 7714'),
                target.get('originalText', '') if isinstance(target, dict) else '',
            )
            result['replacementText'] = reference_result['content']
            result['referenceEntries'] = reference_entries
            result['references'] = reference_result['references']
            result['updatedSections'] = reference_result['updatedSections']
            if isinstance(chart, dict):
                title = chart.get('title') or payload.get('title') or '论文数据图表'
                caption = _caption_with_data_citation(chart.get('caption') or title, '')
                chart['caption'] = caption
                figure_label = result.get('artifactLabel') or target.get('figureLabel') or target.get('artifactLabel') or '图1'
                result['figureMarkdown'] = f'![{title}]({chart.get("dataUrl") or image_url})\n\n{figure_label} {caption}'
            result['citation'] = reference_result.get('citation', '')
            result['citationNumber'] = reference_result['citationNumber']
            return result
        raise ValueError('未知数据图表操作')

    def generate_paper_ppt(self, payload, user_id=''):
        return generate_ppt_from_paper(payload, self._config(user_id), PROJECT_DIR, WEB_DIR)

    def start_paper_ppt(self, payload, user_id=''):
        job_id = uuid.uuid4().hex[:12]
        job = {
            'id': job_id,
            'kind': 'generate',
            'status': 'pending',
            'progress': 0,
            'message': '等待开始',
            'slidesCompleted': 0,
            'totalSlides': 0,
            'events': [],
            'outputPath': '',
            'projectDir': '',
            'error': '',
            'title': str(payload.get('title') or '论文PPT'),
            'options': payload.get('pptOptions') or {},
        }
        with self._ppt_lock:
            self._ppt_jobs[job_id] = job

        thread = threading.Thread(
            target=self._run_paper_ppt_job,
            args=(job_id, payload, user_id),
            daemon=True,
        )
        thread.start()
        return self.ppt_job_status(job_id)

    def start_paper_ppt_refine(self, payload, user_id=''):
        parent_id = str(payload.get('jobId') or '').strip()
        parent = self._get_ppt_job(parent_id)
        if not parent or not parent.get('projectDir'):
            raise ValueError('没有可重做的 PPT 任务')
        job_id = uuid.uuid4().hex[:12]
        refine_payload = dict(payload or {})
        refine_payload['projectDir'] = parent.get('projectDir')
        refine_payload['parentJobId'] = parent_id
        refine_payload['title'] = parent.get('title') or refine_payload.get('title') or '论文PPT'
        refine_payload.setdefault('canvasFormat', (parent.get('options') or {}).get('canvasFormat') or 'ppt169')
        refine_payload.setdefault('style', (parent.get('options') or {}).get('style') or 'academic')
        refine_payload.setdefault('language', (parent.get('options') or {}).get('language') or 'zh')
        refine_payload.setdefault('detailLevel', (parent.get('options') or {}).get('detailLevel') or 'normal')
        job = {
            'id': job_id,
            'kind': 'refine',
            'parentJobId': parent_id,
            'status': 'pending',
            'progress': 0,
            'message': '等待重做',
            'slidesCompleted': 0,
            'totalSlides': parent.get('totalSlides') or 0,
            'events': [],
            'outputPath': '',
            'projectDir': parent.get('projectDir'),
            'error': '',
            'title': refine_payload['title'],
            'options': parent.get('options') or {},
            'targetPages': refine_payload.get('targetPages') or [],
        }
        with self._ppt_lock:
            self._ppt_jobs[job_id] = job

        thread = threading.Thread(
            target=self._run_paper_ppt_refine_job,
            args=(job_id, refine_payload, user_id),
            daemon=True,
        )
        thread.start()
        return self.ppt_job_status(job_id)

    def _run_paper_ppt_job(self, job_id, payload, user_id):
        self._update_ppt_job(job_id, status='running', message='正在启动 PPT Agent')
        try:
            result = run_ppt_generation_task(
                payload,
                self._config(user_id),
                PROJECT_DIR,
                WEB_DIR,
                progress_callback=lambda event: self._record_ppt_event(job_id, event),
            )
            slides = read_ppt_preview(result.get('project_dir') or '')
            self._update_ppt_job(
                job_id,
                status='complete',
                progress=1,
                message='PPT 已生成',
                outputPath=result.get('output_path') or '',
                projectDir=result.get('project_dir') or '',
                totalSlides=len(slides),
                slidesCompleted=len(slides),
                error='',
            )
        except Exception as exc:
            self._update_ppt_job(job_id, status='error', message=str(exc), error=str(exc))

    def _run_paper_ppt_refine_job(self, job_id, payload, user_id):
        self._update_ppt_job(job_id, status='running', message='正在重做 PPT 页面')
        try:
            result = run_ppt_refine_task(
                {**payload, 'jobId': job_id},
                self._config(user_id),
                PROJECT_DIR,
                progress_callback=lambda event: self._record_ppt_event(job_id, event),
            )
            slides = read_ppt_preview(result.get('project_dir') or payload.get('projectDir') or '')
            self._update_ppt_job(
                job_id,
                status='complete',
                progress=1,
                message='PPT 页面已重做',
                outputPath=result.get('output_path') or '',
                projectDir=result.get('project_dir') or payload.get('projectDir') or '',
                totalSlides=len(slides),
                slidesCompleted=len(slides),
                error='',
            )
        except Exception as exc:
            self._update_ppt_job(job_id, status='error', message=str(exc), error=str(exc))

    def _record_ppt_event(self, job_id, event):
        data = event.get('data') if isinstance(event, dict) else {}
        updates = {
            'status': 'running',
            'message': event.get('message') or '',
            'progress': event.get('progress') or 0,
        }
        if isinstance(data, dict):
            if data.get('project_dir'):
                updates['projectDir'] = data.get('project_dir')
            if data.get('output_path'):
                updates['outputPath'] = data.get('output_path')
            if data.get('total_slides'):
                updates['totalSlides'] = data.get('total_slides')
            if data.get('page'):
                updates['slidesCompleted'] = max(int(data.get('page') or 0), 0)
        with self._ppt_lock:
            job = self._ppt_jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job['events'] = (job.get('events') or [])[-40:] + [event]

    def _update_ppt_job(self, job_id, **updates):
        with self._ppt_lock:
            job = self._ppt_jobs.get(job_id)
            if job:
                job.update(updates)

    def _get_ppt_job(self, job_id):
        with self._ppt_lock:
            job = self._ppt_jobs.get(str(job_id or '').strip())
            return dict(job) if job else None

    def ppt_job_status(self, job_id):
        job = self._get_ppt_job(job_id)
        if not job:
            raise ValueError('PPT 任务不存在')
        public = dict(job)
        public['hasOutput'] = bool(public.get('outputPath') and os.path.isfile(public.get('outputPath')))
        return public

    def ppt_job_preview(self, job_id):
        job = self._get_ppt_job(job_id)
        if not job:
            raise ValueError('PPT 任务不存在')
        return {
            'job': self.ppt_job_status(job_id),
            'slides': read_ppt_preview(job.get('projectDir') or ''),
        }

    def ppt_job_download(self, job_id):
        job = self._get_ppt_job(job_id)
        if not job:
            raise ValueError('PPT 任务不存在')
        output_path = job.get('outputPath') or ''
        if not output_path or not os.path.isfile(output_path):
            raise ValueError('PPT 文件还没有生成')
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(job.get('title') or '论文PPT')).strip(' ._') or '论文PPT'
        return {
            'filename': f'{title[:80]}-PPT.pptx',
            'content_type': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'body': open(output_path, 'rb').read(),
        }

    def analyze(self, text, user_id=''):
        ai_reducer = self._ai_reducer(user_id)
        plagiarism = self._plagiarism(user_id)
        ai = ai_reducer.scan_ai_features(text)
        repeat = plagiarism.simulate_repeat_risk(text)
        citation = plagiarism.check_citation_format(text)
        return {
            'ai': ai,
            'repeat': repeat,
            'citation': citation,
        }

    def run_action(self, payload, user_id=''):
        ai_reducer = self._ai_reducer(user_id)
        plagiarism = self._plagiarism(user_id)
        polisher = self._polisher(user_id)
        paper_writer = self._paper_writer(user_id)
        corrector = self._corrector(user_id)
        action = str(payload.get('action', '') or '').strip()
        text = str(payload.get('text', '') or '').strip()
        source_text = str(payload.get('sourceText', '') or '').strip()
        custom_prompt = str(payload.get('customPrompt', '') or '').strip()
        if not text and not (action == 'references' and payload.get('allSections')):
            raise ValueError('请输入需要处理的文本')

        if action == 'analyze':
            return {'result': '', 'analysis': self.analyze(text, user_id)}
        if action == 'polish':
            mode = str(payload.get('polishMode', 'full') or 'full')
            result = polisher.run_task(
                text,
                task_type=str(payload.get('taskType', '章节正文') or '章节正文'),
                polish_type=mode,
                execution_mode=str(payload.get('executionMode', '标准模式') or '标准模式'),
                topic=str(payload.get('topic', '') or ''),
                notes=str(payload.get('notes', '') or ''),
                custom_prompt=custom_prompt,
            )
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'ai-light':
            result = ai_reducer.rewrite_light(text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'ai-deep':
            result = ai_reducer.rewrite_deep(text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'ai-academic':
            result = ai_reducer.rewrite_academic(text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'repeat-light':
            result = plagiarism.reduce_light(text, source_text=source_text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'repeat-medium':
            result = plagiarism.reduce_medium(text, source_text=source_text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'repeat-deep':
            result = plagiarism.reduce_deep(text, source_text=source_text, custom_prompt=custom_prompt)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'citation':
            return {'result': '', 'analysis': {'citation': plagiarism.check_citation_format(text)}}
        if action == 'correction':
            citation_style = str(payload.get('citationStyle', 'auto') or 'auto')
            run = corrector.analyze_text(text, citation_style=citation_style)
            return {
                'result': run.corrected_text,
                'analysis': {
                    'correction': {
                        'counts': run.counts,
                        'issues': run.issues[:80],
                        'categoryLabels': CATEGORY_LABELS,
                        'categoryOrder': CATEGORY_ORDER,
                        'citationStyleDetected': run.citation_style_detected,
                        'citationStyleEffective': run.citation_style_effective,
                        'report': run.report_text,
                    }
                },
            }
        if action == 'outline':
            topic = str(payload.get('topic', '') or text).strip()
            subject = str(payload.get('subject', '') or '').strip()
            paper_style = str(payload.get('paperStyle', '学术论文') or '学术论文')
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            total_word_count = str(payload.get('totalWordCount', '') or '').strip()
            outline_section_limit = str(payload.get('outlineSectionLimit', '') or '').strip()
            template_structure = payload.get('templateStructure')
            if not topic:
                raise ValueError('请输入论文题目')
            result = paper_writer.generate_outline(
                topic,
                style=paper_style,
                reference_style=reference_style,
                subject=subject,
                total_word_count=total_word_count,
                outline_section_limit=outline_section_limit,
                template_structure=template_structure,
            )
            return {'result': result, 'analysis': {}}
        if action == 'section':
            outline = str(payload.get('outline', '') or '').strip()
            section_title = str(payload.get('sectionTitle', '') or '').strip()
            context = str(payload.get('context', '') or '').strip()
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            all_sections = payload.get('allSections', [])
            total_word_count = str(payload.get('totalWordCount', '') or '').strip()
            target_reference_count = _positive_int(payload.get('targetReferenceCount')) or _target_reference_count_for_words(total_word_count)
            current_reference_count = _positive_int(payload.get('currentReferenceCount'))
            remaining_section_count = _positive_int(payload.get('remainingSectionCount'), 1)
            reference_snapshot = str(payload.get('referenceSnapshot', '') or '').strip()
            try:
                word_count = int(payload.get('wordCount') or 1000)
            except Exception:
                word_count = 1000
            if not outline:
                raise ValueError('请输入论文大纲')
            if not section_title:
                raise ValueError('请输入章节标题')

            # Generate section content
            raw_result = paper_writer.write_section(
                outline,
                section_title,
                context=context,
                word_count=word_count,
                reference_style=reference_style,
                total_word_count=total_word_count,
                target_reference_count=target_reference_count,
                current_reference_count=current_reference_count,
                remaining_section_count=remaining_section_count,
                reference_snapshot=reference_snapshot,
            )

            # Determine reference processing mode
            mode = determine_reference_mode(section_title, all_sections)

            if mode == 'append':
                # Append mode: new section, continue numbering from previous sections
                result = process_references_append_mode(section_title, raw_result, all_sections, reference_style)

                # Build reference section content from appended entries
                ref_entries_to_append = result['references_to_append']
                appended_refs_text = ''
                if ref_entries_to_append:
                    appended_refs_text = build_reference_body_from_entries(ref_entries_to_append)

                # Find reference section title
                ref_section_title = '# 参考文献'
                for section in all_sections:
                    title = section.get('title', '')
                    if '参考文献' in title.lower() or 'reference' in title.lower():
                        ref_section_title = title
                        break

                return {
                    'content': result['cleaned_content'],
                    'result': result['cleaned_content'],
                    'references': {
                        'mode': 'append',
                        'title': ref_section_title,
                        'append': appended_refs_text
                    },
                    'updatedSections': []
                }

            else:
                # Reorder mode: existing section modified, reorder all references
                result = process_references_reorder_mode(section_title, raw_result, all_sections, reference_style)

                # Build complete reference section content
                full_refs_text = build_reference_body_from_entries(result['full_references'])

                # Find reference section title
                ref_section_title = '# 参考文献'
                for section in all_sections:
                    title = section.get('title', '')
                    if '参考文献' in title.lower() or 'reference' in title.lower():
                        ref_section_title = title
                        break

                return {
                    'content': result['cleaned_content'],
                    'result': result['cleaned_content'],
                    'references': {
                        'mode': 'reorder',
                        'title': ref_section_title,
                        'content': full_refs_text
                    },
                    'updatedSections': result['updated_sections']
                }
        if action == 'abstract':
            language = str(payload.get('language', '中文') or '中文')
            result = paper_writer.write_abstract(text, language=language)
            return {'result': result, 'analysis': self.analyze(result, user_id)}
        if action == 'references':
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            all_sections = payload.get('allSections', [])
            total_word_count = str(payload.get('totalWordCount', '') or '').strip()
            target_reference_count = _positive_int(payload.get('targetReferenceCount')) or _target_reference_count_for_words(total_word_count)
            if all_sections:
                result = reorder_references_for_full_paper(
                    all_sections,
                    reference_style,
                    min_reference_count=target_reference_count,
                )
                if not result['reference_content']:
                    raise ValueError('没有找到可整理的参考文献条目')
                reference_warning = result.get('reference_warning', '')
                return {
                    'result': result['reference_content'],
                    'content': result['reference_content'],
                    'references': {
                        'mode': 'reorder',
                        'title': result['reference_title'],
                        'content': result['reference_content'],
                        'entryCount': result['entry_count'],
                        'citationCount': result['citation_count'],
                        'targetCount': result.get('target_reference_count', 0),
                        'shortfall': result.get('reference_shortfall', 0),
                        'warning': reference_warning,
                    },
                    'updatedSections': result['updated_sections'],
                    'analysis': {
                        'citation': plagiarism.check_citation_format(result['reference_content']),
                        'referenceWarning': reference_warning,
                    },
                }
            result = paper_writer.format_references(text, style=reference_style)
            return {'result': result, 'analysis': {'citation': plagiarism.check_citation_format(result)}}
        if action == 'batch_write':
            outline = str(payload.get('outline', '') or '').strip()
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            sections = payload.get('sections', [])
            all_sections = payload.get('allSections', [])
            total_word_count = str(payload.get('totalWordCount', '') or '').strip()
            target_reference_count = _positive_int(payload.get('targetReferenceCount')) or _target_reference_count_for_words(total_word_count)
            try:
                word_count = int(payload.get('wordCount') or 1000)
            except Exception:
                word_count = 1000
            if not outline:
                raise ValueError('请输入论文大纲')
            if not sections:
                raise ValueError('没有需要写作的章节')

            # Collect all references from all sections
            from modules.reference_manager import parse_reference_entries
            all_references = []
            ref_section_title = None

            # Get existing references
            for section in all_sections:
                title = section.get('title', '')
                if '参考文献' in title or 'References' in title or 'REFERENCES' in title:
                    ref_section_title = title
                    content = section.get('content', '')
                    if content:
                        all_references = parse_reference_entries(content)
                    break

            results = []
            for section_data in sections:
                section_title = str(section_data.get('title', '') or '').strip()
                context = str(section_data.get('context', '') or '').strip()
                if not section_title:
                    continue
                try:
                    result = paper_writer.write_section(
                        outline,
                        section_title,
                        context=context,
                        word_count=word_count,
                        reference_style=reference_style,
                        total_word_count=total_word_count,
                        target_reference_count=target_reference_count,
                        current_reference_count=len(all_references),
                        remaining_section_count=max(1, len(sections) - len(results)),
                        reference_snapshot=build_reference_body_from_entries(all_references),
                    )

                    # Extract references from this section
                    clean_content, references_text = extract_references_from_section_result(result)

                    # Normalize section body - remove all outline-style headings
                    clean_content = normalize_section_body(clean_content)

                    if references_text:
                        new_refs = parse_reference_entries(references_text)
                        all_references = merge_reference_entry_lists(all_references, new_refs)

                    results.append({
                        'title': section_title,
                        'content': clean_content,
                        'success': True,
                        'error': None
                    })
                except Exception as e:
                    results.append({
                        'title': section_title,
                        'content': '',
                        'success': False,
                        'error': str(e)
                    })

            # Build unified reference section
            ref_map = build_reference_number_map(all_references)

            # Rewrite citations in all sections
            for result in results:
                if result['success'] and result['content']:
                    result['content'] = rewrite_citations_with_entry_map(result['content'], ref_map)

            # Generate reference section content
            final_entries = []
            for idx, entry in enumerate(all_references, start=1):
                final_entries.append({
                    'text': entry['text'],
                    'key': entry['key'],
                    'new_number': idx
                })
            updated_refs_content = build_reference_body_from_entries(final_entries)

            return {
                'results': results,
                'references': {
                    'title': ref_section_title or '# 参考文献',
                    'content': updated_refs_content
                }
            }
        raise ValueError('未知操作')


class RequestHandler(BaseHTTPRequestHandler):
    workbench: WebWorkbench | None = None

    def log_message(self, format, *args):
        return

    def _cookie_pairs(self):
        pairs = {}
        for chunk in str(self.headers.get('Cookie') or '').split(';'):
            if '=' not in chunk:
                continue
            key, value = chunk.split('=', 1)
            pairs[key.strip()] = value.strip()
        return pairs

    def _web_user_id(self):
        pairs = self._cookie_pairs()
        current = _safe_web_user_id(pairs.get(WEB_USER_COOKIE, ''))
        if current:
            return current, False
        for cookie_name in LEGACY_WEB_USER_COOKIES:
            current = _safe_web_user_id(pairs.get(cookie_name, ''))
            if current:
                return current, True
        return secrets.token_urlsafe(32), True

    def _send_user_cookie(self, user_id):
        safe_id = _safe_web_user_id(user_id)
        if not safe_id:
            return
        cookie = (
            f'{WEB_USER_COOKIE}={safe_id}; Max-Age={WEB_USER_COOKIE_MAX_AGE}; '
            'Path=/; HttpOnly; SameSite=Lax'
        )
        self.send_header('Set-Cookie', cookie)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Content-Length', str(len(body)))
        user_id = getattr(self, '_current_web_user_id', '')
        if getattr(self, '_set_web_user_cookie', False):
            self._send_user_cookie(user_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_binary(self, body, filename, content_type='application/octet-stream', status=200):
        filename = os.path.basename(str(filename or 'download.bin')).replace('\r', '').replace('\n', '')
        safe_ascii = re.sub(r'[^A-Za-z0-9_.-]+', '_', filename) or 'download.bin'
        quoted_utf8 = ''.join(f'%{byte:02X}' for byte in filename.encode('utf-8'))
        data = bytes(body or b'')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition', f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{quoted_utf8}')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Content-Length', str(len(data)))
        user_id = getattr(self, '_current_web_user_id', '')
        if getattr(self, '_set_web_user_cookie', False):
            self._send_user_cookie(user_id)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def do_GET(self):
        parsed = urlparse(self.path)
        user_id, is_new_user = self._web_user_id()
        self._current_web_user_id = user_id
        self._set_web_user_cookie = is_new_user
        if parsed.path == '/api/status':
            self._send_json({'ok': True, 'data': self.workbench.status(user_id)})
            return
        if parsed.path == '/api/config':
            self._send_json({'ok': True, 'data': self.workbench.config_payload(user_id)})
            return
        if parsed.path == '/api/paper/ppt/status':
            job_id = (parse_qs(parsed.query).get('jobId') or [''])[0]
            try:
                self._send_json({'ok': True, 'data': self.workbench.ppt_job_status(job_id)})
            except Exception as exc:
                self._send_json({'ok': False, 'error': str(exc)}, status=404)
            return
        if parsed.path == '/api/paper/ppt/preview':
            job_id = (parse_qs(parsed.query).get('jobId') or [''])[0]
            try:
                self._send_json({'ok': True, 'data': self.workbench.ppt_job_preview(job_id)})
            except Exception as exc:
                self._send_json({'ok': False, 'error': str(exc)}, status=404)
            return
        if parsed.path == '/api/paper/ppt/download':
            job_id = (parse_qs(parsed.query).get('jobId') or [''])[0]
            try:
                exported = self.workbench.ppt_job_download(job_id)
                self._send_binary(exported['body'], exported['filename'], exported['content_type'])
            except Exception as exc:
                self._send_json({'ok': False, 'error': str(exc)}, status=404)
            return
        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        user_id, is_new_user = self._web_user_id()
        self._current_web_user_id = user_id
        self._set_web_user_cookie = is_new_user
        try:
            payload = self._read_json()
            if parsed.path == '/api/run':
                data = self.workbench.run_action(payload, user_id)
            elif parsed.path == '/api/config/save':
                data = self.workbench.save_api(payload, user_id)
            elif parsed.path == '/api/config/activate':
                data = self.workbench.activate_api(payload, user_id)
            elif parsed.path == '/api/config/models':
                data = self.workbench.fetch_models_for_payload(payload, user_id)
            elif parsed.path == '/api/template/parse':
                data = self.workbench.parse_template(payload)
            elif parsed.path == '/api/data-chart':
                data = self.workbench.run_data_chart(payload, user_id)
            elif parsed.path == '/api/paper/export':
                exported = export_paper_document(payload, PROJECT_DIR, WEB_DIR)
                self._send_binary(exported.body, exported.filename, exported.content_type)
                return
            elif parsed.path == '/api/paper/ppt/start':
                data = self.workbench.start_paper_ppt(payload, user_id)
            elif parsed.path == '/api/paper/ppt/refine':
                data = self.workbench.start_paper_ppt_refine(payload, user_id)
            elif parsed.path == '/api/paper/ppt':
                exported = self.workbench.generate_paper_ppt(payload, user_id)
                self._send_binary(exported.body, exported.filename, exported.content_type)
                return
            else:
                self._send_json({'ok': False, 'error': 'Not found'}, status=404)
                return
            self._send_json({'ok': True, 'data': data})
        except Exception as exc:
            _write_server_error_log(exc, payload if 'payload' in locals() else None)
            self._send_json({'ok': False, 'error': str(exc)}, status=400)

    def _serve_static(self, path):
        relative = unquote(path.lstrip('/') or 'index.html')
        if relative.endswith('/'):
            relative += 'index.html'
        file_path = os.path.abspath(os.path.join(WEB_DIR, relative))
        if not file_path.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(file_path):
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
        with open(file_path, 'rb') as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Content-Length', str(len(body)))
        user_id = getattr(self, '_current_web_user_id', '')
        if getattr(self, '_set_web_user_cookie', False):
            self._send_user_cookie(user_id)
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description='论文工坊 Web 工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()

    RequestHandler.workbench = WebWorkbench()
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    url = f'http://{args.host}:{args.port}/'
    print(f'[web] 论文工坊 Web 工作台已启动: {url}')
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
