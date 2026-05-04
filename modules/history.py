# -*- coding: utf-8 -*-
"""
History storage and rollback helpers.
"""

import copy
import json
import os
import re
from collections import Counter
from datetime import datetime

from modules.app_metadata import (
    MODULE_AI_REDUCE,
    MODULE_CORRECTION,
    MODULE_PAPER_WRITE,
    MODULE_PLAGIARISM,
    MODULE_POLISH,
    SOURCE_KIND_LABELS,
)
from modules.runtime_paths import resolve_runtime_data_root


class HistoryManager:
    """Manage saved history records and rollback metadata."""

    HISTORY_FILE = 'history.json'
    MAX_RECORDS = 200

    RECORD_TYPE_VERSION = 'version'
    RECORD_TYPE_ROLLBACK_AUDIT = 'rollback_audit'

    RESTORE_MODE_FULL_SNAPSHOT = 'full_snapshot'
    RESTORE_MODE_LEGACY_PARTIAL = 'legacy_partial'

    PAGE_STATE_BY_MODULE = {
        MODULE_PAPER_WRITE: 'paper_write',
        MODULE_AI_REDUCE: 'ai_reduce',
        MODULE_PLAGIARISM: 'plagiarism',
        MODULE_POLISH: 'polish',
        MODULE_CORRECTION: 'correction',
    }
    SOURCE_KIND_BY_LABEL = {label: key for key, label in SOURCE_KIND_LABELS.items()}
    POLISH_TYPE_BY_LABEL = {
        '语法校对': 'grammar',
        '词汇优化': 'vocab',
        '逻辑优化': 'logic',
        '全面润色': 'full',
    }

    def __init__(self, data_dir):
        self.base_data_dir = os.path.abspath(str(data_dir or '.'))
        self.data_dir = self.base_data_dir
        self.app_dir = self.data_dir
        self.history_path = os.path.join(self.data_dir, self.HISTORY_FILE)
        self._records = []
        self.reload_data_directory()

    def reload_data_directory(self):
        self.data_dir = resolve_runtime_data_root(self.base_data_dir)
        self.app_dir = self.data_dir
        self.history_path = os.path.join(self.data_dir, self.HISTORY_FILE)
        self._records = self._load()

    def _load(self):
        if not os.path.exists(self.history_path):
            return []
        try:
            with open(self.history_path, 'r', encoding='utf-8') as f:
                raw_records = json.load(f)
        except Exception:
            return []

        if not isinstance(raw_records, list):
            return []

        cleaned = []
        for record in raw_records:
            sanitized = self._sanitize_record(record)
            if sanitized:
                cleaned.append(sanitized)
        return cleaned

    def _save(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.history_path, 'w', encoding='utf-8') as f:
                json.dump(self._records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _normalize_int(value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_title_candidate(value, limit=80):
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        text = text.strip('：:，,。.;；')
        if not text:
            return ''
        return text[:limit]

    @classmethod
    def _looks_like_paper_write_workspace(cls, workspace_state):
        if not isinstance(workspace_state, dict):
            return False
        if {'outline_text', 'sections', 'section_order'}.issubset(workspace_state.keys()):
            return True
        return {'topic', 'current_section', 'editor_text'}.issubset(workspace_state.keys())

    @classmethod
    def _is_disallowed_title_candidate(cls, text):
        candidate = cls._normalize_title_candidate(text, limit=200)
        if not candidate:
            return True

        lower = candidate.lower()
        if lower in {
            '未命名论文',
            '未命名文稿',
            '未命名',
            '摘要',
            '中文摘要',
            'abstract',
            '英文摘要',
            '关键词',
            '关键字',
            '中文关键词',
            '中文关键字',
            '英文关键词',
            '英文关键字',
            '目录',
            '参考文献',
            'references',
            'bibliography',
            '附录',
            'appendix',
            '致谢',
            '引言',
            '绪论',
            '结论',
            '当前章节',
        }:
            return True

        if re.match(r'^第[一二三四五六七八九十百千\d]+[章节部分篇]', candidate):
            return True
        if re.match(r'^[一二三四五六七八九十]+[、.．)]', candidate):
            return True
        if re.match(r'^\d{1,2}[、.．)]', candidate):
            return True
        if re.match(r'^\d{1,2}(\.\d+)+\s*', candidate):
            return True
        if candidate.startswith((
            '本文',
            '本研究',
            '本章',
            '通过',
            '针对',
            '为了',
            '随着',
            '近年来',
            '这是',
            '该文',
            '我们',
            '首先',
            '其次',
            '最后',
        )):
            return True
        if lower.endswith(('.doc', '.docx', '.txt', '.pdf')):
            return True
        if re.search(r'[\\/]', candidate) or re.match(r'^[A-Za-z]:', candidate):
            return True
        return False

    def _extract_workspace_paper_title(self, workspace_state, page_state_id=''):
        if not isinstance(workspace_state, dict):
            return ''

        for key in ('paper_title', 'current_paper_title'):
            title = self._normalize_title_candidate(workspace_state.get(key, ''))
            if title:
                return title

        if page_state_id == 'paper_write' or self._looks_like_paper_write_workspace(workspace_state):
            return self._normalize_title_candidate(workspace_state.get('topic', ''))
        return ''

    def _fallback_first_line_title(self, input_text, output_text):
        for candidate in (input_text, output_text):
            text = str(candidate or '')
            if not text.strip():
                continue
            for line in text.splitlines():
                fallback = self._normalize_title_candidate(line, limit=10)
                if fallback:
                    return fallback
        return '未命名论文'

    def _resolve_record_paper_title(
        self,
        input_text,
        output_text,
        extra,
        *,
        workspace_state=None,
        page_state_id='',
        stored_title='',
    ):
        extra = extra if isinstance(extra, dict) else {}

        explicit_title = self._normalize_title_candidate(
            extra.get('paper_title') or extra.get('current_paper_title')
        )
        if explicit_title:
            return explicit_title

        workspace_title = self._extract_workspace_paper_title(workspace_state, page_state_id=page_state_id)
        if workspace_title:
            return workspace_title

        normalized_stored_title = self._normalize_title_candidate(stored_title)
        if normalized_stored_title and not self._is_disallowed_title_candidate(normalized_stored_title):
            return normalized_stored_title

        return self._infer_paper_title(input_text, output_text, extra)

    def _sanitize_record(self, record):
        if not isinstance(record, dict):
            return None

        extra = record.get('extra')
        if not isinstance(extra, dict):
            extra = {}

        record_type = str(record.get('record_type') or self.RECORD_TYPE_VERSION).strip()
        if record_type not in {self.RECORD_TYPE_VERSION, self.RECORD_TYPE_ROLLBACK_AUDIT}:
            record_type = self.RECORD_TYPE_VERSION

        page_state_id = str(record.get('page_state_id') or '').strip()
        workspace_state = record.get('workspace_state')
        if not isinstance(workspace_state, dict):
            workspace_state = None

        source_record_id = record.get('source_record_id')
        if source_record_id in (None, ''):
            source_record_id = None
        else:
            source_record_id = self._normalize_int(source_record_id, default=None)

        rollback_restore_mode = str(record.get('rollback_restore_mode') or '').strip()
        if rollback_restore_mode not in {
            '',
            self.RESTORE_MODE_FULL_SNAPSHOT,
            self.RESTORE_MODE_LEGACY_PARTIAL,
        }:
            rollback_restore_mode = ''

        input_full = str(record.get('input_full', record.get('input', '')) or '')
        output_full = str(record.get('output_full', record.get('output', '')) or '')

        cleaned = {
            'id': self._normalize_int(record.get('id'), default=0),
            'time': str(record.get('time', '') or ''),
            'module': str(record.get('module', '') or ''),
            'operation': str(record.get('operation', '') or ''),
            'input': str(record.get('input', '') or ''),
            'output': str(record.get('output', '') or ''),
            'input_full': input_full,
            'output_full': output_full,
            'extra': copy.deepcopy(extra),
            'paper_title': self._resolve_record_paper_title(
                input_full,
                output_full,
                extra,
                workspace_state=workspace_state,
                page_state_id=page_state_id,
                stored_title=record.get('paper_title', ''),
            ),
            'word_count': self._normalize_int(
                record.get('word_count'),
                default=self._estimate_word_count(output_full or input_full),
            ),
            'record_type': record_type,
            'page_state_id': page_state_id,
            'workspace_state': copy.deepcopy(workspace_state) if workspace_state else None,
            'source_record_id': source_record_id,
            'rollback_restore_mode': rollback_restore_mode,
        }
        return cleaned

    def add(
        self,
        operation,
        input_text,
        output_text,
        module='',
        extra=None,
        *,
        page_state_id='',
        workspace_state=None,
        record_type='version',
        source_record_id=None,
        rollback_restore_mode='',
    ):
        extra = dict(extra or {})
        next_id = max((int(item.get('id', 0) or 0) for item in self._records), default=0) + 1
        page_state_id = str(page_state_id or '').strip()

        if record_type not in {self.RECORD_TYPE_VERSION, self.RECORD_TYPE_ROLLBACK_AUDIT}:
            record_type = self.RECORD_TYPE_VERSION
        if rollback_restore_mode not in {
            '',
            self.RESTORE_MODE_FULL_SNAPSHOT,
            self.RESTORE_MODE_LEGACY_PARTIAL,
        }:
            rollback_restore_mode = ''

        normalized_workspace_state = None
        if isinstance(workspace_state, dict):
            normalized_workspace_state = copy.deepcopy(workspace_state)

        normalized_source_record_id = None
        if source_record_id not in (None, ''):
            normalized_source_record_id = self._normalize_int(source_record_id, default=None)

        record = self._sanitize_record(
            {
                'id': next_id,
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'module': module,
                'operation': operation,
                'input': input_text[:500] if input_text else '',
                'output': output_text[:500] if output_text else '',
                'input_full': input_text,
                'output_full': output_text,
                'extra': extra,
                'paper_title': self._resolve_record_paper_title(
                    input_text,
                    output_text,
                    extra,
                    workspace_state=normalized_workspace_state,
                    page_state_id=page_state_id,
                ),
                'word_count': self._estimate_word_count(output_text or input_text or ''),
                'record_type': record_type,
                'page_state_id': page_state_id,
                'workspace_state': normalized_workspace_state,
                'source_record_id': normalized_source_record_id,
                'rollback_restore_mode': rollback_restore_mode,
            }
        )
        self._records.append(record)
        self._trim_records()
        self._save()
        return record['id']

    def _trim_records(self):
        while len(self._records) > self.MAX_RECORDS:
            oldest = self._records[0]
            if oldest.get('record_type') == self.RECORD_TYPE_ROLLBACK_AUDIT:
                self._records.pop(0)
                continue

            blockers = self.get_referencing_audits(oldest.get('id'))
            if blockers:
                blocker_ids = {item.get('id') for item in blockers}
                self._records = [item for item in self._records if item.get('id') not in blocker_ids]
                continue

            self._records.pop(0)

    def _infer_paper_title(self, input_text, output_text, extra):
        extra = extra if isinstance(extra, dict) else {}
        title = self._normalize_title_candidate(extra.get('paper_title') or extra.get('current_paper_title'))
        if title:
            return title

        for candidate in (input_text, output_text):
            text = str(candidate or '').strip()
            if not text:
                continue
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
            if not first_line:
                continue
            first_line = re.sub(r'^[#>\-*]+\s*', '', first_line).strip()
            first_line = self._normalize_title_candidate(first_line)
            if first_line and not self._is_disallowed_title_candidate(first_line):
                return first_line
        return self._fallback_first_line_title(input_text, output_text)

    @staticmethod
    def _estimate_word_count(text):
        content = re.sub(r'\s+', '', str(text or ''))
        return len(content)

    def get_all(self):
        return list(reversed(self._records))

    def get_by_module(self, module):
        return [r for r in reversed(self._records) if r.get('module') == module]

    def get_by_id(self, record_id):
        record_id = self._normalize_int(record_id, default=0)
        for record in self._records:
            if record.get('id') == record_id:
                return record
        return None

    def get_paper_title(self, record_or_id):
        record = self.resolve_display_source(record_or_id) or self._coerce_record(record_or_id)
        if not record:
            return '未命名论文'

        title = self._resolve_record_paper_title(
            self._record_input_text(record),
            self._record_output_text(record),
            record.get('extra'),
            workspace_state=record.get('workspace_state'),
            page_state_id=self.get_page_state_id(record),
            stored_title=record.get('paper_title', ''),
        )
        return title or '未命名论文'

    def get_referencing_audits(self, source_record_id):
        source_record_id = self._normalize_int(source_record_id, default=0)
        return [
            record for record in self._records
            if record.get('record_type') == self.RECORD_TYPE_ROLLBACK_AUDIT
            and record.get('source_record_id') == source_record_id
        ]

    def can_delete(self, record_id):
        blockers = self.get_referencing_audits(record_id)
        if not blockers:
            return {'ok': True, 'blocking_records': []}
        return {
            'ok': False,
            'reason': 'referenced_by_audit',
            'blocking_records': list(reversed(blockers)),
        }

    def delete(self, record_id, force=False):
        check = self.can_delete(record_id)
        if not force and not check.get('ok'):
            return check

        before = len(self._records)
        self._records = [r for r in self._records if r.get('id') != self._normalize_int(record_id, default=0)]
        self._save()
        return {'ok': len(self._records) != before}

    def clear(self):
        self._records = []
        self._save()

    def count(self):
        return len(self._records)

    def resolve_display_source(self, record_or_id):
        record = self._coerce_record(record_or_id)
        if not record:
            return None
        if record.get('record_type') != self.RECORD_TYPE_ROLLBACK_AUDIT:
            return record

        source = self.get_by_id(record.get('source_record_id'))
        if not source:
            return None
        if source.get('id') == record.get('id'):
            return None
        return self.resolve_display_source(source)

    def resolve_rollback_source(self, record_or_id):
        record = self._coerce_record(record_or_id)
        if not record:
            return None
        if record.get('record_type') == self.RECORD_TYPE_ROLLBACK_AUDIT:
            source = self.get_by_id(record.get('source_record_id'))
            if not source:
                return None
            if source.get('id') == record.get('id'):
                return None
            return self.resolve_rollback_source(source)
        return record

    def create_rollback_audit(self, source_record_id, rollback_restore_mode):
        source = self.resolve_rollback_source(source_record_id)
        if not source:
            return None

        new_id = self.add(
            operation=f'回滚审计·{source.get("operation", "")}',
            input_text='',
            output_text='',
            module=source.get('module', ''),
            extra={'paper_title': source.get('paper_title', '')},
            page_state_id=self.get_page_state_id(source),
            workspace_state=None,
            record_type=self.RECORD_TYPE_ROLLBACK_AUDIT,
            source_record_id=source.get('id'),
            rollback_restore_mode=rollback_restore_mode,
        )
        return self.get_by_id(new_id)

    def prepare_rollback(self, record_or_id):
        selected = self._coerce_record(record_or_id)
        if not selected:
            return None

        source = self.resolve_rollback_source(selected)
        if not source:
            return None

        page_state_id = self.get_page_state_id(source)
        workspace_state = source.get('workspace_state')
        if isinstance(workspace_state, dict) and workspace_state and page_state_id:
            return {
                'selected_record': selected,
                'source_record': source,
                'page_state_id': page_state_id,
                'workspace_state': copy.deepcopy(workspace_state),
                'rollback_restore_mode': self.RESTORE_MODE_FULL_SNAPSHOT,
            }

        legacy_state = self.build_legacy_workspace_state(source, page_state_id)
        if not page_state_id or not legacy_state:
            return None

        return {
            'selected_record': selected,
            'source_record': source,
            'page_state_id': page_state_id,
            'workspace_state': legacy_state,
            'rollback_restore_mode': self.RESTORE_MODE_LEGACY_PARTIAL,
        }

    def build_legacy_workspace_state(self, record_or_id, page_state_id=''):
        record = self._coerce_record(record_or_id)
        if not record:
            return None

        page_state_id = page_state_id or self.get_page_state_id(record)
        if page_state_id == 'paper_write':
            return self._build_legacy_paper_write_state(record)
        if page_state_id in {'ai_reduce', 'plagiarism'}:
            return self._build_legacy_transform_state(record, page_state_id)
        if page_state_id == 'polish':
            return self._build_legacy_polish_state(record)
        if page_state_id == 'correction':
            return self._build_legacy_correction_state(record)
        return None

    def get_page_state_id(self, record_or_id):
        record = self._coerce_record(record_or_id)
        if not record:
            return ''
        page_state_id = str(record.get('page_state_id') or '').strip()
        if page_state_id:
            return page_state_id
        return self.PAGE_STATE_BY_MODULE.get(record.get('module', ''), '')

    def _coerce_record(self, record_or_id):
        if isinstance(record_or_id, dict):
            return record_or_id
        return self.get_by_id(record_or_id)

    @staticmethod
    def _record_input_text(record):
        return str(record.get('input_full') or record.get('input') or '')

    @staticmethod
    def _record_output_text(record):
        return str(record.get('output_full') or record.get('output') or '')

    def _build_legacy_paper_write_state(self, record):
        extra = record.get('extra') or {}
        input_text = self._record_input_text(record).strip()
        output_text = self._record_output_text(record).strip()
        operation = str(record.get('operation', '') or '')
        topic = self.get_paper_title(record)

        outline_text = ''
        editor_text = output_text
        current_section = ''
        sections = {}
        section_order = []

        if '大纲' in operation:
            outline_text = output_text or input_text
            sections, section_order = self._outline_sections_from_text(outline_text)
            if not sections and outline_text:
                sections = {'论文大纲': outline_text}
                section_order = ['论文大纲']
        elif '章节' in operation:
            current_section = input_text or str(extra.get('topic', '') or '').strip() or '当前章节'
            if editor_text:
                sections[current_section] = editor_text
                section_order.append(current_section)
        elif '摘要' in operation:
            current_section = '摘要'
            if editor_text:
                sections[current_section] = editor_text
                section_order.append(current_section)
        else:
            current_section = str(extra.get('topic', '') or '').strip()
            if current_section and editor_text:
                sections[current_section] = editor_text
                section_order.append(current_section)

        return {
            'topic': topic,
            'outline_text': outline_text,
            'sections': sections,
            'section_order': section_order,
            'selected_section': current_section if current_section in sections else '',
            'current_section': current_section,
            'editor_text': editor_text,
            'snapshots': [],
            'selection_snapshot': {},
            'context_revision': 0,
        }

    def _build_legacy_transform_state(self, record, page_state_id):
        input_text = self._record_input_text(record).strip()
        output_text = self._record_output_text(record).strip()
        extra = record.get('extra') or {}
        mode = self._infer_transform_mode(record.get('operation', ''), page_state_id)
        notice = '兼容部分恢复：旧历史记录未保存完整工作区快照，已尽量恢复输入、结果与预览。'

        compare_text = str(extra.get('compare_text', '') or '').strip()
        if not compare_text and page_state_id == 'ai_reduce':
            compare_text = input_text

        return {
            'mode': mode,
            'input_text': input_text,
            'output_text': output_text,
            'compare_text': compare_text,
            'analysis_text': notice,
            'preview_text': output_text or input_text,
            'analysis_status_text': notice,
            'analysis_status_color': '#D48806',
            'info_text': notice,
            'compare_section_expanded': bool(compare_text),
        }

    def _build_legacy_polish_state(self, record):
        extra = record.get('extra') or {}
        input_text = self._record_input_text(record).strip()
        output_text = self._record_output_text(record).strip()
        source_kind, source_desc = self._normalize_source_kind(extra.get('source_kind'))
        notice = '兼容部分恢复：旧历史记录缺少完整润色工作区快照。'

        polish_type = str(extra.get('polish_type', '') or '').strip()
        polish_value = self.POLISH_TYPE_BY_LABEL.get(polish_type, polish_type if polish_type in self.POLISH_TYPE_BY_LABEL.values() else 'full')

        return {
            'task_type': str(extra.get('task_type', '') or '').strip() or '章节正文',
            'execution_mode': str(extra.get('execution_mode', '') or '').strip() or '标准模式',
            'polish_type': polish_value,
            'topic': str(extra.get('topic', '') or record.get('paper_title', '') or '').strip(),
            'input_text': input_text,
            'note_text': '',
            'current_source_kind': source_kind,
            'current_source_desc': source_desc or notice,
            'latest_result_text': output_text,
            'latest_result_summary': str(record.get('operation', '') or '兼容回滚结果预览'),
            'latest_target_summary': '',
            'preview_text': output_text,
            'preview_detail': notice,
            'last_task_config': {
                'task_type': str(extra.get('task_type', '') or '').strip() or '章节正文',
                'execution_mode': str(extra.get('execution_mode', '') or '').strip() or '标准模式',
                'polish_type': polish_value,
                'topic': str(extra.get('topic', '') or '').strip(),
                'source_kind': extra.get('source_kind', ''),
            },
            'check_text': '',
            'check_color': '#7A7F8A',
            'info_text': notice,
            'info_color': '#D48806',
            'tools_visible': True,
        }

    def _build_legacy_correction_state(self, record):
        extra = record.get('extra') or {}
        input_text = self._record_input_text(record).strip()
        output_text = self._record_output_text(record).strip()
        source_kind, source_desc = self._normalize_source_kind(extra.get('source_kind'))
        notice = '兼容回滚：原问题明细缺失，仅恢复修正文与基础信息。'
        effective_style = str(extra.get('citation_style_effective', '') or '').strip() or 'GB/T 7714'
        detected_style = str(extra.get('citation_style_detected', '') or '').strip() or effective_style

        return {
            'citation_style': effective_style if effective_style in {'auto', 'GB/T 7714', 'APA', 'MLA'} else 'auto',
            'input_text': input_text,
            'current_source_kind': source_kind,
            'current_source_desc': source_desc or notice,
            'current_docx_path': '',
            'current_run': {
                'input_text': input_text,
                'corrected_text': output_text,
                'issues': [],
                'counts': {
                    'pending': 0,
                    'applied': 0,
                    'ignored': 0,
                    'stale': 0,
                    'auto_fixable': 0,
                    'by_category': {},
                },
                'source_kind': source_kind,
                'citation_style_detected': detected_style,
                'citation_style_effective': effective_style,
                'report_text': notice,
            },
            'latest_auto_fixed_count': self._normalize_int(extra.get('auto_fixed_count', 0), default=0),
            'selected_issue_id': '',
            'info_text': notice,
            'info_color': '#D48806',
        }

    def _outline_sections_from_text(self, text):
        if not text:
            return {}, []

        heading_pattern = re.compile(
            r'^(第[一二三四五六七八九十百千]+[章节部分].*|'
            r'[一二三四五六七八九十]+\s*[、.].*|'
            r'\d+\s*[、.]\s*.+|'
            r'\d+\.\d*\s*.+|'
            r'#{1,3}\s*.+)$',
            re.MULTILINE,
        )
        lines = text.splitlines()
        sections = []
        for idx, line in enumerate(lines):
            candidate = line.strip()
            if candidate and heading_pattern.match(candidate) and len(candidate) <= 80:
                sections.append((candidate, idx))

        if not sections:
            return {}, []

        payload = {}
        order = []
        for index, (title, start_line) in enumerate(sections):
            end_line = sections[index + 1][1] if index + 1 < len(sections) else len(lines)
            content = '\n'.join(lines[start_line:end_line]).strip()
            payload[title] = content
            order.append(title)
        return payload, order

    def _infer_transform_mode(self, operation, page_state_id):
        operation = str(operation or '')
        match = re.search(r'\(([^)]+)\)', operation)
        if match:
            candidate = match.group(1).strip()
            valid = {
                'ai_reduce': {'light', 'deep', 'academic'},
                'plagiarism': {'light', 'medium', 'deep'},
            }.get(page_state_id, set())
            if candidate in valid:
                return candidate
        return ''

    def _normalize_source_kind(self, value):
        raw = str(value or '').strip()
        if not raw:
            return 'manual', ''
        if raw in SOURCE_KIND_LABELS:
            return raw, SOURCE_KIND_LABELS.get(raw, '')
        if raw in self.SOURCE_KIND_BY_LABEL:
            return self.SOURCE_KIND_BY_LABEL[raw], raw
        return 'manual', raw

    def _records_in_period(self, period_key='24h'):
        if period_key == 'all':
            return list(self._records)

        day_map = {
            '24h': 1,
            '7d': 7,
            '14d': 14,
            '30d': 30,
        }
        days = day_map.get(period_key, 1)
        now = datetime.now()
        filtered = []

        for record in self._records:
            time_text = record.get('time', '')
            try:
                record_time = datetime.strptime(time_text, '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if (now - record_time).total_seconds() <= days * 86400:
                filtered.append(record)

        return filtered

    def get_records_in_period(self, period_key='24h'):
        return list(self._records_in_period(period_key))

    def get_dashboard_stats(self, period_key='24h'):
        records = self.get_records_in_period(period_key)
        ordered_records = sorted(records, key=lambda item: item.get('time', ''), reverse=True)

        module_counter = Counter()
        operation_counter = Counter()
        for record in records:
            module_counter[record.get('module') or '未分类'] += 1
            operation_counter[record.get('operation') or '未命名操作'] += 1

        latest_time = ordered_records[0].get('time', '') if ordered_records else ''

        return {
            'period_key': period_key,
            'total': len(records),
            'latest_time': latest_time,
            'module_ranking': module_counter.most_common(5),
            'operation_ranking': operation_counter.most_common(5),
            'recent_activity': [
                {
                    'time': item.get('time', ''),
                    'module': item.get('module', ''),
                    'operation': item.get('operation', ''),
                }
                for item in ordered_records[:5]
            ],
        }
