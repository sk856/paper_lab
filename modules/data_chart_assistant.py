# -*- coding: utf-8 -*-
"""
Data and chart assistant for locating data-backed writing opportunities and
generating reviewable chart assets.
"""

from __future__ import annotations

import base64
import csv
import html
import io
import json
import os
import re
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont


class DataChartAssistant:
    """AI-first workflow for locating chart opportunities and rendering charts."""

    SEARCH_TIMEOUT = 12
    PAGE_TIMEOUT = 14
    USER_AGENT = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    )
    DEFAULT_COLORS = (
        '#cc5f1b', '#1f7a4b', '#2563eb', '#9333ea', '#d97706',
        '#0f766e', '#b91c1c', '#475569',
    )

    def __init__(self, api_client=None):
        self.api = api_client

    @staticmethod
    def _normalize_text(text):
        return str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()

    @staticmethod
    def _clean_excerpt(text, limit=360):
        value = re.sub(r'\s+', ' ', str(text or '')).strip()
        return value[:limit].rstrip() + ('...' if len(value) > limit else '')

    @staticmethod
    def _split_paragraphs(text):
        normalized = DataChartAssistant._normalize_text(text)
        chunks = re.split(r'\n{2,}|(?<=[。！？!?；;])\s*\n', normalized)
        return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]

    @staticmethod
    def _section_from_heading_lines(text):
        sections = []
        current_title = '全文内容'
        current_lines = []
        heading_re = re.compile(
            r'^\s{0,8}(?:#{1,6}\s+|第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*[:：]?|\d+(?:\.\d+)*[、.．]?\s+)(.+?)\s*$'
        )
        for line in DataChartAssistant._normalize_text(text).splitlines():
            match = heading_re.match(line.strip())
            if match and len(line.strip()) <= 90:
                if current_lines:
                    sections.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
                current_title = line.strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            sections.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
        return [item for item in sections if item.get('content')]

    @classmethod
    def _normalize_sections(cls, full_text='', sections=None):
        normalized = []
        if isinstance(sections, list):
            for item in sections:
                if not isinstance(item, dict):
                    continue
                title = str(item.get('title', '') or '').strip() or '未命名章节'
                content = cls._normalize_text(item.get('content', ''))
                if content:
                    normalized.append({'title': title, 'content': content})
        if normalized:
            return normalized
        return cls._section_from_heading_lines(full_text) or [{'title': '全文内容', 'content': cls._normalize_text(full_text)}]

    @staticmethod
    def _usage_context(action):
        return {'page_id': 'datachart', 'action_id': action}

    def _require_ai(self, action='data_chart'):
        if not self.api or not hasattr(self.api, 'call_json_sync'):
            raise ValueError('请先在配置管理中启用 AI 模型，用于阅读全文、检索数据来源和改写段落。')
        return self.api

    @classmethod
    def _paragraph_items(cls, sections):
        items = []
        for section_index, section in enumerate(sections):
            title = section.get('title') or f'第 {section_index + 1} 节'
            for paragraph_index, paragraph in enumerate(cls._split_paragraphs(section.get('content', ''))):
                text = paragraph.strip()
                if not text:
                    continue
                items.append({
                    'id': f'S{section_index + 1}P{paragraph_index + 1}',
                    'sectionTitle': title,
                    'paragraphIndex': paragraph_index,
                    'text': text,
                })
        return items

    @staticmethod
    def _truncate_for_prompt(text, limit=900):
        value = str(text or '').strip()
        return value if len(value) <= limit else f'{value[:limit]}...'

    @classmethod
    def _paragraph_prompt_payload(cls, paragraphs, max_chars=24000):
        payload = []
        total = 0
        for item in paragraphs:
            text = cls._truncate_for_prompt(item.get('text', ''), 900)
            total += len(text)
            if total > max_chars:
                break
            payload.append({
                'id': item.get('id'),
                'sectionTitle': item.get('sectionTitle'),
                'paragraphIndex': item.get('paragraphIndex'),
                'text': text,
            })
        return payload

    def _target_candidate_from_ai_item(self, item, paragraph, index, raw_count):
        artifact_type = self._normalize_artifact_type(item.get('artifactType') or item.get('insertType') or item.get('type'))
        chart_title = item.get('chartTitle') or item.get('tableTitle') or item.get('title')
        reason = str(item.get('reason', '') or ('AI 判断该处适合补充数据表。' if artifact_type == 'table' else 'AI 判断该处适合补充数据图。')).strip()
        data_need = str(item.get('dataNeed', '') or '补充可核验数据').strip()
        intent = str(item.get('intent') or item.get('suggestion') or reason or data_need).strip()
        if not chart_title and intent:
            chart_title = intent
        return {
            'id': f'target-{paragraph.get("id")}-{artifact_type}-{index + 1}',
            'paragraphId': paragraph.get('id'),
            'artifactType': artifact_type,
            'artifactLabel': self._default_artifact_label(paragraph.get('sectionTitle', ''), artifact_type),
            'figureLabel': self._default_artifact_label(paragraph.get('sectionTitle', ''), 'figure'),
            'tableLabel': self._default_artifact_label(paragraph.get('sectionTitle', ''), 'table'),
            'sectionTitle': paragraph.get('sectionTitle', ''),
            'paragraphIndex': paragraph.get('paragraphIndex', 0),
            'excerpt': self._clean_excerpt(paragraph.get('text', '')),
            'originalText': paragraph.get('text', '').strip(),
            'reason': reason,
            'intent': intent,
            'dataNeed': data_need,
            'query': str(item.get('query', '') or '').strip(),
            'chartType': self._normalize_chart_type(item.get('chartType')),
            'chartTitle': self._normalize_chart_title(chart_title, [], item),
            'tableTitle': self._normalize_chart_title(item.get('tableTitle') or chart_title, [], item),
            'confidence': max(0.0, min(1.0, float(item.get('confidence') or 0.6))),
            'score': max(0, raw_count - index),
        }

    def _coerce_target_candidates(self, raw_targets, paragraph_map):
        candidates = []
        raw_count = len(raw_targets) if isinstance(raw_targets, list) else 0
        for index, item in enumerate(raw_targets if isinstance(raw_targets, list) else []):
            if not isinstance(item, dict):
                continue
            paragraph_id = str(item.get('paragraphId', '') or '').strip()
            paragraph = paragraph_map.get(paragraph_id)
            if not paragraph:
                continue
            candidates.append(self._target_candidate_from_ai_item(item, paragraph, index, raw_count))
        return candidates

    @staticmethod
    def _limit_targets_preserving_table(candidates, limit):
        max_items = max(1, int(limit or 8))
        if len(candidates or []) <= max_items:
            return candidates or []
        head = list(candidates[:max_items])
        if any(item.get('artifactType') == 'table' for item in head):
            return head
        table_target = next((item for item in candidates if item.get('artifactType') == 'table'), None)
        if table_target:
            return head[:max_items - 1] + [table_target]
        return head

    def _find_table_targets_with_ai(self, api, *, topic='', outline='', prompt_payload=None, paragraph_map=None, limit=2):
        prompt_payload = prompt_payload or []
        paragraph_map = paragraph_map or {}
        prompt = f'''请再次只从“插表”角度阅读论文段落列表，找出适合插入论文数据表的位置。

判断原则：
1. 不是看到关键词就选表，而是看该段论证是否需要保留多列原始数值、指标口径、变量定义、描述性统计、相关系数、回归结果、评价指标体系或测算结果。
2. 如果数据需要让读者逐项核对、比较多个指标或展示模型/测算结果，优先选择 table。
3. 如果全文确实没有适合插表的位置，返回空 targets，并在 summary 中说明原因。
4. 每个候选仍必须围绕具体段落生成 dataNeed、query 和 tableTitle；不要使用论文总题目或章节题作为标题。

论文主题：{topic or '未提供'}
论文大纲：{self._truncate_for_prompt(outline, 2400) or '未提供'}
段落列表 JSON：
{json.dumps(prompt_payload, ensure_ascii=False)}

返回 JSON：
{{
  "summary": "一句话说明插表判断",
  "targets": [
    {{
      "paragraphId": "S1P1",
      "artifactType": "table",
      "reason": "为什么这里更适合插表",
      "dataNeed": "需要什么表格数据",
      "query": "检索式",
      "chartType": "bar",
      "chartTitle": "备用图题",
      "tableTitle": "建议表题",
      "confidence": 0.0
    }}
  ]
}}'''
        try:
            payload = api.call_json_sync(
                prompt,
                system='你是严谨的论文表格编辑。请从论证逻辑判断插表位置，不要机械按关键词选择。',
                temperature=0.15,
                max_tokens=1800,
                request_timeout=120,
                schema_name='data_chart_table_targets',
                usage_context=self._usage_context('data_chart.find_table'),
            )
        except Exception:
            return [], ''
        raw_targets = payload.get('targets', []) if isinstance(payload, dict) else []
        coerced = self._coerce_target_candidates(raw_targets, paragraph_map)
        table_targets = [item for item in coerced if item.get('artifactType') == 'table']
        return table_targets[: max(1, int(limit or 2))], str((payload or {}).get('summary') or '').strip()

    @staticmethod
    def _normalize_chart_type(value):
        text = str(value or '').strip().lower()
        aliases = {
            '折线图': 'line',
            '趋势图': 'line',
            'line chart': 'line',
            '柱状图': 'bar',
            '条形图': 'bar',
            'bar chart': 'bar',
            '饼图': 'pie',
            '结构图': 'pie',
            'pie chart': 'pie',
        }
        text = aliases.get(text, text)
        return text if text in {'bar', 'line', 'pie'} else 'bar'

    @staticmethod
    def _normalize_artifact_type(value):
        text = str(value or '').strip().lower()
        if text in {'table', '表格', '插表', '数据表', '表'}:
            return 'table'
        return 'figure'

    @staticmethod
    def _split_label_axis(label):
        raw = re.sub(r'\s+', '', str(label or '').strip())
        match = re.search(r'((?:19|20)\d{2})\s*年?', raw)
        if not match:
            return '', raw
        year = f'{match.group(1)}年'
        series = f'{raw[:match.start()]}{raw[match.end():]}'
        series = re.sub(r'^[\-—–_:：,，、\s]+|[\-—–_:：,，、\s]+$', '', series)
        return year, series or '指标'

    @classmethod
    def _series_structure(cls, rows):
        years = []
        series_names = []
        values = {}
        usable = 0
        for row in rows or []:
            year, series = cls._split_label_axis(row.get('label', ''))
            if not year or not series:
                continue
            usable += 1
            if year not in years:
                years.append(year)
            if series not in series_names:
                series_names.append(series)
            values.setdefault(series, {})[year] = float(row.get('value') or 0)
        if usable < 4 or len(years) < 2 or len(series_names) < 2:
            return None
        years.sort(key=lambda item: int(re.search(r'\d{4}', item).group(0)) if re.search(r'\d{4}', item) else 0)
        return {'years': years, 'series': series_names, 'values': values}

    @staticmethod
    def _year_range_label(years):
        numbers = [int(match.group(0)) for year in years or [] for match in [re.search(r'\d{4}', str(year))] if match]
        if not numbers:
            return ''
        first, last = min(numbers), max(numbers)
        return f'{first}年' if first == last else f'{first}—{last}年'

    @staticmethod
    def _target_title_hint(target):
        if not isinstance(target, dict):
            return ''
        for field in ('intent', 'suggestion', 'chartTitle', 'tableTitle', 'title', 'dataNeed', 'query'):
            value = re.sub(r'\s+', '', str(target.get(field, '') or '').strip())
            value = re.sub(r'^(该段|建议|适合|应当|可以|补充|展示|分析|比较|对比|绘制|生成)', '', value)
            value = re.sub(r'(，|。|；|;).*$', '', value)
            value = re.sub(r'(数据|统计数据|图表|图|变化趋势|趋势|对比)$', '', value)
            if value and len(value) >= 3:
                return value[:24]
        return ''

    @classmethod
    def _series_indicator_title(cls, series_names, target=None):
        joined = ''.join(series_names or [])
        hint = cls._target_title_hint(target)
        hint_text = f'{hint}{str((target or {}).get("dataNeed", "") if isinstance(target, dict) else "")}'
        if any('城镇' in name for name in series_names or []) and any('农村' in name for name in series_names or []):
            has_gap = any('差距' in name for name in series_names or []) or '差距' in hint_text
            if '互联网' in f'{joined}{hint_text}' and '普及率' in f'{joined}{hint_text}':
                return '城乡互联网普及率及差距' if has_gap else '城乡互联网普及率'
            if '普及率' in f'{joined}{hint_text}':
                return '城乡普及率及差距' if has_gap else '城乡普及率'
            return '城乡差异'
        preferred_terms = (
            '互联网普及率', '普及率', '渗透率', '覆盖率', '使用率', '增长率',
            '占比', '比例', '规模', '收入', '产值', '指数', '水平', '数量',
        )
        for term in preferred_terms:
            if term in joined:
                return term
        return hint or '相关指标'

    @classmethod
    def _fallback_chart_title(cls, rows, target=None):
        structure = cls._series_structure(rows)
        if structure:
            year_label = cls._year_range_label(structure.get('years')) or ''
            indicator = cls._series_indicator_title(structure.get('series'), target)
            return f'{year_label}{indicator}变化趋势'
        labels = [str(row.get('label', '') or '') for row in rows or []]
        years = [cls._split_label_axis(label)[0] for label in labels]
        years = [year for year in years if year]
        hint = cls._target_title_hint(target) or cls._series_indicator_title(labels, target)
        if len(set(years)) >= 2:
            return f'{cls._year_range_label(sorted(set(years)))}{hint}变化趋势'
        return f'{hint}对比'

    @classmethod
    def _title_needs_rewrite(cls, title, rows, target=None):
        text = re.sub(r'\s+', '', str(title or '').strip())
        if not text:
            return True
        year_hits = re.findall(r'(?:19|20)\d{2}', text)
        if len(year_hits) > 2:
            return True
        if len(text) > 34:
            return True
        bad_terms = ('图表标题', '论文数据图表', '相关章节', '数据对比', '影响因素', '中国地图', '地图')
        if any(term in text for term in bad_terms):
            return True
        structure = cls._series_structure(rows)
        if structure:
            joined = ''.join(structure.get('series') or [])
            series_terms = set(structure.get('series') or [])
            for term in re.findall(r'[\u4e00-\u9fff]{2,}', joined):
                series_terms.add(term)
            for term in (
                '城乡', '城镇', '农村', '互联网', '普及率', '差距', '占比', '规模',
                '增长率', '覆盖率', '渗透率', '使用率', '收入', '产值', '指数',
            ):
                if term in joined:
                    series_terms.add(term)
            has_concept = any(term and term in text for term in series_terms)
            return not has_concept
        labels = ''.join(str(row.get('label', '') or '') for row in rows or [])
        return bool(labels and not any(part and part in text for part in re.findall(r'[\u4e00-\u9fff]{2,}', labels)[:8]))

    @classmethod
    def _normalize_chart_title(cls, title, rows, target=None):
        fallback = cls._fallback_chart_title(rows, target)
        value = str(title or '').strip()
        if cls._title_needs_rewrite(value, rows, target):
            value = fallback
        value = re.sub(r'\s+', '', value)
        value = re.sub(r'((?:19|20)\d{2})\s*[-—–~至到]\s*((?:19|20)\d{2})\s*年?\1\s*[-—–~至到]\s*\2\s*年?', r'\1—\2年', value)
        if cls._title_needs_rewrite(value, rows, target):
            value = fallback
        return value[:34] or fallback

    @classmethod
    def _choose_chart_type(cls, chart_type, rows, target=None):
        requested = cls._normalize_chart_type(chart_type)
        structure = cls._series_structure(rows)
        if structure and len(structure.get('years', [])) >= 2:
            return 'line'
        labels = [str(row.get('label', '') or '') for row in rows or []]
        if len({cls._split_label_axis(label)[0] for label in labels if cls._split_label_axis(label)[0]}) >= 2:
            return 'line'
        if requested == 'pie' and len(rows or []) > 6:
            return 'bar'
        return requested

    def _resolve_chart_metadata(self, rows, target, submitted_title, unit, chart_type):
        ai_title = ''
        ai_chart_type = ''
        if self.api and hasattr(self.api, 'call_json_sync'):
            try:
                row_payload = [
                    {'label': row.get('label'), 'value': row.get('value'), 'source': row.get('source', '')}
                    for row in rows[:24]
                ]
                prompt = f'''请根据论文段落和已审核的数据表，生成最贴合论文内容的图表标题，并选择图表类型。

要求：
1. 标题必须直接概括数据指标、对象和时间范围，不要使用论文总题目或章节题。
2. 如果数据是多年份趋势，标题要体现年份范围和趋势；如果包含城镇/农村/差距等多指标，要体现对比对象。
3. 不要写“地图”，除非数据确实是地域空间分布。
4. 标题控制在 14—30 个汉字左右。

论文段落：
{self._truncate_for_prompt((target or {}).get('originalText') or (target or {}).get('excerpt'), 1600)}

数据需求：{(target or {}).get('dataNeed') or '未提供'}
AI 建议标题：{(target or {}).get('chartTitle') or (target or {}).get('title') or '未提供'}
当前输入标题：{submitted_title or '未提供'}
单位：{unit or '未注明'}
数据行 JSON：
{json.dumps(row_payload, ensure_ascii=False)}

返回 JSON：
{{"title": "图表标题", "chartType": "line|bar|pie"}}'''
                payload = self.api.call_json_sync(
                    prompt,
                    system='你是严谨的论文图表编辑，只根据段落和数据表生成图题，不要编造数据。',
                    temperature=0.1,
                    max_tokens=800,
                    request_timeout=90,
                    schema_name='data_chart_metadata',
                    usage_context=self._usage_context('data_chart.metadata'),
                )
                if isinstance(payload, dict):
                    ai_title = str(payload.get('title', '') or '').strip()
                    ai_chart_type = str(payload.get('chartType', '') or '').strip()
            except Exception:
                pass
        submitted_title = str(submitted_title or '').strip()
        if ai_title and not self._title_needs_rewrite(ai_title, rows, target) and self._title_needs_rewrite(submitted_title, rows, target):
            title_seed = ai_title
        elif submitted_title and not self._title_needs_rewrite(submitted_title, rows, target):
            title_seed = submitted_title
        else:
            title_seed = ''
        title = self._normalize_chart_title(title_seed, rows, target)
        resolved_type = self._choose_chart_type(ai_chart_type or chart_type, rows, target)
        return title, resolved_type

    @classmethod
    def _format_source(cls, row):
        source = str(row.get('source', '') or '').strip()
        source_name = str(row.get('sourceName', '') or '').strip()
        publisher = str(row.get('publisher', '') or '').strip()
        url = str(row.get('url', '') or '').strip()
        note = str(row.get('note', '') or '').strip()
        parts = []
        for value in (source_name, publisher, url or source, note):
            if value and value not in parts:
                parts.append(value)
        return '；'.join(parts)

    @staticmethod
    def _shorten_source_name(value, limit=34):
        text = re.sub(r'\s+', ' ', str(value or '').strip())
        text = re.sub(r'https?://\S+', '', text)
        text = text.strip('，,。.;； ')
        return text[:limit].rstrip() + ('…' if len(text) > limit else '')

    @classmethod
    def _short_source_label(cls, row):
        source_name = cls._shorten_source_name(row.get('sourceName', ''))
        publisher = cls._shorten_source_name(row.get('publisher', ''), 22)
        if source_name and publisher and publisher not in source_name:
            return f'{publisher}《{source_name}》' if '《' not in source_name else f'{publisher}{source_name}'
        if source_name:
            return source_name
        if publisher:
            return publisher
        source = cls._shorten_source_name(row.get('source', ''))
        if source:
            return source
        url = str(row.get('url', '') or '').strip()
        if url:
            return urllib.parse.urlparse(url).netloc.lower().removeprefix('www.')
        return ''

    @classmethod
    def _coerce_ai_rows(cls, rows):
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            value = cls._parse_number(row.get('value'))
            if value is None:
                continue
            label = str(row.get('label', '') or '').strip() or f'项目{len(result) + 1}'
            result.append({
                'label': label[:40],
                'value': value,
                'rawValue': str(row.get('value', value)),
                'source': cls._format_source(row),
                'sourceName': str(row.get('sourceName', '') or '').strip(),
                'publisher': str(row.get('publisher', '') or '').strip(),
                'url': str(row.get('url', '') or '').strip(),
                'note': str(row.get('note', '') or '').strip(),
            })
        return result

    @staticmethod
    def _has_verifiable_source(row):
        text = ' '.join(str(row.get(field, '') or '').strip() for field in ('source', 'sourceName', 'publisher', 'url', 'note'))
        if not text:
            return False
        if re.search(r'(请|待|需要).{0,8}(补充|替换|核验|确认)|真实来源|来源待|用户审核|示例|项目A|项目B|项目C', text, flags=re.IGNORECASE):
            return False
        if re.search(r'https?://|doi\.org|\.gov|\.edu|统计局|年鉴|公报|报告|数据库|白皮书|CNNIC|中国互联网络信息中心', text, flags=re.IGNORECASE):
            return True
        return bool(str(row.get('sourceName', '') or '').strip() and str(row.get('publisher', '') or '').strip())

    @classmethod
    def _rows_have_verifiable_sources(cls, rows):
        usable = [row for row in rows or [] if row.get('value') is not None]
        if len(usable) < 2:
            return False
        return all(cls._has_verifiable_source(row) for row in usable)

    @staticmethod
    def _label_looks_like_variable_code(label):
        text = re.sub(r'\s+', '', str(label or '').strip())
        if not text:
            return False
        known_codes = {
            'pgdp', 'gdp', 'rgdp', 'urban', 'urb', 'fagri', 'agri', 'indstr', 'industry',
            'edu', 'education', 'internet', 'net', 'fin', 'finance', 'gov', 'fdi', 'pop',
            'labor', 'lnpgdp', 'lnurban', 'lnfagri', 'lnindstr',
        }
        if text.lower() in known_codes:
            return True
        return bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{1,12}', text))

    @staticmethod
    def _values_are_small_sequence(rows):
        values = []
        for row in rows or []:
            value = row.get('value')
            try:
                number = float(value)
            except Exception:
                return False
            if abs(number - round(number)) > 1e-9:
                return False
            values.append(int(round(number)))
        if len(values) < 3:
            return False
        if values == list(range(1, len(values) + 1)):
            return True
        unique = sorted(set(values))
        return len(unique) >= 3 and unique == list(range(1, len(unique) + 1))

    @classmethod
    def _data_quality_issue(cls, rows, target=None, payload=None):
        rows = rows or []
        if len(rows) < 2:
            return ''
        labels = [str(row.get('label', '') or '').strip() for row in rows]
        joined_labels = ' '.join(labels)
        joined_meta = ' '.join(
            str(row.get(field, '') or '').strip()
            for row in rows
            for field in ('source', 'sourceName', 'publisher', 'url', 'note')
        )
        context = ' '.join([
            str((target or {}).get('chartTitle', '') or ''),
            str((target or {}).get('tableTitle', '') or ''),
            str((target or {}).get('dataNeed', '') or ''),
            str((target or {}).get('query', '') or ''),
            str((payload or {}).get('title', '') or ''),
            str((payload or {}).get('sourceNote', '') or ''),
        ])
        code_count = sum(1 for label in labels if cls._label_looks_like_variable_code(label))
        code_ratio = code_count / max(1, len(labels))
        has_sequence = cls._values_are_small_sequence(rows)
        meta_signal = re.search(
            r'变量名称|变量名|变量代码|控制变量|解释变量|被解释变量|预测方向|单位建议|指标代码|变量说明|variable\s+name|control\s+variable',
            f'{joined_meta} {context}',
            flags=re.IGNORECASE,
        )
        placeholder_signal = re.search(r'项目A|项目B|项目C|请替换|请补充|真实来源|待核验|示例', f'{joined_labels} {joined_meta}')
        if placeholder_signal:
            return 'AI 返回的是占位数据，不是可用于论文图表的真实统计值。'
        if code_ratio >= 0.5 and (has_sequence or meta_signal):
            return 'AI 返回的是变量代码或控制变量清单，并把 1、2、3 等序号误当成数值；这不是可用于绘图或论文数据分析的统计数据。'
        if has_sequence and meta_signal:
            return 'AI 返回的是变量/指标顺序编号，不是真实观测值。'
        return ''

    @staticmethod
    def _public_row(row):
        return {
            'label': str(row.get('label', '') or ''),
            'value': row.get('value'),
            'rawValue': str(row.get('rawValue', row.get('value', '')) or ''),
            'source': str(row.get('source', '') or ''),
            'sourceName': str(row.get('sourceName', '') or ''),
            'publisher': str(row.get('publisher', '') or ''),
            'url': str(row.get('url', '') or ''),
            'note': str(row.get('note', '') or ''),
        }

    @classmethod
    def _public_rows(cls, rows):
        return [cls._public_row(row) for row in rows or []]

    @classmethod
    def _request_text(cls, url, timeout=None):
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': cls.USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
            },
        )
        with urllib.request.urlopen(request, timeout=timeout or cls.SEARCH_TIMEOUT) as response:
            raw = response.read(1024 * 1024)
            charset = response.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset, errors='ignore')

    @staticmethod
    def _strip_tags(markup):
        text = re.sub(r'(?is)<(script|style|noscript).*?</\1>', ' ', str(markup or ''))
        text = re.sub(r'(?s)<[^>]+>', ' ', text)
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _normalize_url(url):
        value = html.unescape(str(url or '')).strip()
        if not value:
            return ''
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc.endswith('bing.com') and parsed.path.startswith('/ck/a'):
            params = urllib.parse.parse_qs(parsed.query)
            encoded = (params.get('u') or [''])[0]
            if encoded.startswith('a1'):
                try:
                    value = base64.urlsafe_b64decode(encoded[2:] + '===').decode('utf-8', errors='ignore')
                except Exception:
                    pass
        return value if value.startswith(('http://', 'https://')) else ''

    @classmethod
    def _parse_bing_results(cls, markup, limit=6):
        results = []
        seen = set()
        pattern = re.compile(
            r'(?is)<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>.*?<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<p[^>]*>(.*?)</p>)?'
        )
        for match in pattern.finditer(markup or ''):
            url = cls._normalize_url(match.group(1))
            title = cls._strip_tags(match.group(2))
            snippet = cls._strip_tags(match.group(3))
            if not url or url in seen:
                continue
            results.append({'title': title, 'url': url, 'snippet': snippet})
            seen.add(url)
            if len(results) >= limit:
                break
        return results

    @classmethod
    def _parse_duckduckgo_results(cls, markup, limit=6):
        results = []
        seen = set()
        pattern = re.compile(
            r'(?is)<a[^>]+class="[^"]*\bresult__a\b[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<a[^>]+class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</div>)?'
        )
        for match in pattern.finditer(markup or ''):
            url = html.unescape(match.group(1) or '')
            if 'uddg=' in url:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                url = html.unescape((params.get('uddg') or [''])[0])
            url = cls._normalize_url(url)
            title = cls._strip_tags(match.group(2))
            snippet = cls._strip_tags(match.group(3) or match.group(4))
            if not url or url in seen:
                continue
            results.append({'title': title, 'url': url, 'snippet': snippet})
            seen.add(url)
            if len(results) >= limit:
                break
        return results

    @classmethod
    def _parse_bing_rss_results(cls, markup, limit=6):
        results = []
        seen = set()
        item_pattern = re.compile(r'(?is)<item\b[^>]*>(.*?)</item>')
        for item_match in item_pattern.finditer(markup or ''):
            item = item_match.group(1)
            title_match = re.search(r'(?is)<title>(.*?)</title>', item)
            link_match = re.search(r'(?is)<link>(.*?)</link>', item)
            desc_match = re.search(r'(?is)<description>(.*?)</description>', item)
            url = cls._normalize_url(cls._strip_tags(link_match.group(1) if link_match else ''))
            title = cls._strip_tags(title_match.group(1) if title_match else '')
            snippet = cls._strip_tags(desc_match.group(1) if desc_match else '')
            if not url or url in seen:
                continue
            results.append({'title': title, 'url': url, 'snippet': snippet})
            seen.add(url)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _source_quality_score(item):
        url = str(item.get('url', '') or '').lower()
        title = str(item.get('title', '') or '').lower()
        snippet = str(item.get('snippet', '') or '').lower()
        host = urllib.parse.urlparse(url).netloc.lower()
        text = f'{title} {snippet} {host}'
        score = 0
        preferred_domains = (
            'stats.gov.cn', '.gov.cn', '.edu.cn', 'worldbank.org', 'oecd.org',
            'imf.org', 'un.org', 'who.int', 'wto.org', 'pbc.gov.cn',
            'cnnic.net.cn', 'ndrc.gov.cn', 'mof.gov.cn', 'gov.cn',
        )
        for domain in preferred_domains:
            if host.endswith(domain) or domain in host:
                score += 12
                break
        if any(term in text for term in ('统计局', '公报', '报告', '年鉴', '数据库', '白皮书', 'data', 'dataset', 'statistics')):
            score += 4
        if any(term in text for term in ('论文', '期刊', '毕业论文', '万方', '知网', '维普', '影响效应', '机制分析', '编辑部')):
            score -= 7
        blocked_domains = ('zhihu.com', 'baike.baidu.com', 'baidu.com', 'csdn.net', 'jianshu.com')
        if any(domain in host for domain in blocked_domains) or any(term in text for term in ('知乎', '百度知道', 'csdn', 'blog', '贴吧', '论坛')):
            score -= 12
        return score

    @classmethod
    def _search_web_sources(cls, query, limit=6):
        search_query = str(query or '').strip()
        if not search_query:
            return []
        encoded = urllib.parse.quote(search_query)
        attempts = [
            ('bing_rss', f'https://www.bing.com/search?format=rss&q={encoded}'),
            ('bing', f'https://www.bing.com/search?q={encoded}'),
            ('duckduckgo', f'https://duckduckgo.com/html/?q={encoded}'),
        ]
        collected = []
        seen = set()
        for engine, url in attempts:
            try:
                markup = cls._request_text(url, timeout=cls.SEARCH_TIMEOUT)
                if engine == 'bing_rss':
                    results = cls._parse_bing_rss_results(markup, limit=limit)
                elif engine == 'bing':
                    results = cls._parse_bing_results(markup, limit=limit)
                else:
                    results = cls._parse_duckduckgo_results(markup, limit=limit)
                if results:
                    for item in results:
                        item_url = item.get('url')
                        if not item_url or item_url in seen:
                            continue
                        collected.append(item)
                        seen.add(item_url)
            except Exception:
                continue
        ranked = sorted(collected, key=cls._source_quality_score, reverse=True)
        return [item for item in ranked if cls._source_quality_score(item) > 0][:limit]

    @classmethod
    def _read_public_page(cls, url):
        normalized = cls._normalize_url(url)
        if not normalized:
            return ''
        if normalized.startswith('http://'):
            reader_url = f'https://r.jina.ai/{normalized}'
        else:
            reader_url = f'https://r.jina.ai/http://{normalized}'
        try:
            text = cls._request_text(reader_url, timeout=cls.PAGE_TIMEOUT)
        except Exception:
            try:
                text = cls._request_text(normalized, timeout=cls.PAGE_TIMEOUT)
                text = cls._strip_tags(text)
            except Exception:
                return ''
        text = re.sub(r'\n{3,}', '\n\n', str(text or '')).strip()
        return text[:5000]

    @classmethod
    def _collect_search_evidence(cls, query, *, limit=5, page_limit=3):
        search_query = str(query or '').strip()
        enriched_query = search_query
        cleaned_for_stats = False
        if re.search(r'控制变量|变量名称|变量名|变量代码|预测方向|变量说明', search_query):
            cleaned_query = re.sub(r'控制变量|变量名称|变量名|变量代码|预测方向|变量说明|类别|数量|分布|对比', ' ', search_query)
            cleaned_query = re.sub(r'\s+', ' ', cleaned_query).strip()
            if cleaned_query:
                enriched_query = f'{cleaned_query} 年度统计数据 地区数据 真实数值'
                cleaned_for_stats = True
        if search_query and not cleaned_for_stats and not any(term in search_query for term in ('数据来源', '统计数据', '报告', '数据库', '年鉴', '公报')):
            enriched_query = f'{search_query} 统计数据 报告 数据来源'
        negative_terms = ' -论文 -期刊 -毕业论文 -万方 -知网 -维普'
        if search_query and not any(term in search_query for term in ('-论文', '-期刊', '-万方', '-知网')):
            enriched_query = f'{enriched_query}{negative_terms}'
        results = cls._search_web_sources(enriched_query, limit=limit)
        evidence = []
        for index, item in enumerate(results):
            page_text = cls._read_public_page(item.get('url')) if index < page_limit else ''
            evidence.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('snippet', ''),
                'content': cls._clean_excerpt(page_text, 2200) if page_text else '',
            })
        return evidence

    def find_targets(self, full_text='', *, topic='', outline='', sections=None, limit=8):
        api = self._require_ai('data_chart.find')
        normalized_sections = self._normalize_sections(full_text, sections)
        paragraphs = self._paragraph_items(normalized_sections)
        if not paragraphs:
            return {'targets': [], 'summary': '全文中没有可供 AI 阅读的段落。'}
        paragraph_map = {item['id']: item for item in paragraphs}
        prompt_payload = self._paragraph_prompt_payload(paragraphs)
        system = (
            '你是论文写作中的数据图表编辑。你的任务是阅读全文，从论证逻辑出发判断哪些段落适合插入数据图或数据表。'
            '不要根据单个关键词机械判断；必须说明为什么该段需要数据支持、应使用什么数据，以及更适合插图还是插表。'
        )
        prompt = f'''请阅读下面的论文段落列表，找出最适合插入数据图或数据表的位置。

要求：
1. 只从给定 paragraph id 中选择位置，不要改写原文。
2. 插图适合呈现趋势、结构、占比、组间对比和变化过程；插表适合呈现原始数据、指标口径、变量定义、相关系数、回归结果、描述性统计和多列数值。
3. 如果全文确实没有合适位置，可以返回空 targets。
4. 每个候选给出：paragraphId、artifactType、reason、dataNeed、query、chartType、chartTitle、tableTitle、confidence。
5. query 必须围绕该段真正需要说明的指标、对象、年份和地区生成，不要沿用论文总题目。
6. chartTitle/tableTitle 必须概括“要呈现什么数据”，不要写论文总题目、章节题或“影响因素”这类泛化标题。
7. artifactType 只能是 figure 或 table；chartType 只能是 line、bar、pie，插表时也给出一个备用 chartType。
8. 至少保留 1 个插表候选；如果全文确实没有适合插表的位置，在 summary 中说明原因。

论文主题：{topic or '未提供'}
论文大纲：{self._truncate_for_prompt(outline, 2400) or '未提供'}
段落列表 JSON：
{json.dumps(prompt_payload, ensure_ascii=False)}

返回 JSON：
{{
  "summary": "一句话说明 AI 阅读全文后的判断",
  "targets": [
    {{
      "paragraphId": "S1P1",
      "artifactType": "figure|table",
      "reason": "为什么这里需要图表",
      "dataNeed": "需要什么数据",
      "query": "检索式",
      "chartType": "line|bar|pie",
      "chartTitle": "建议图表标题",
      "tableTitle": "建议表格标题",
      "confidence": 0.0
    }}
  ]
}}'''
        payload = api.call_json_sync(
            prompt,
            system=system,
            temperature=0.2,
            max_tokens=2600,
            request_timeout=120,
            schema_name='data_chart_targets',
            usage_context=self._usage_context('data_chart.find'),
        )
        raw_targets = payload.get('targets', []) if isinstance(payload, dict) else []
        candidates = self._coerce_target_candidates(raw_targets, paragraph_map)
        candidates = self._limit_targets_preserving_table(candidates, limit)
        table_summary = ''
        if candidates and not any(item.get('artifactType') == 'table' for item in candidates):
            table_targets, table_summary = self._find_table_targets_with_ai(
                api,
                topic=topic,
                outline=outline,
                prompt_payload=prompt_payload,
                paragraph_map=paragraph_map,
                limit=2,
            )
            seen_ids = {item.get('paragraphId') for item in candidates}
            for table_target in table_targets:
                if table_target.get('paragraphId') in seen_ids:
                    table_target['id'] = f'{table_target.get("id")}-table-review'
                candidates.append(table_target)
                seen_ids.add(table_target.get('paragraphId'))
            candidates = self._limit_targets_preserving_table(candidates, limit)
        if not candidates:
            table_targets, table_summary = self._find_table_targets_with_ai(
                api,
                topic=topic,
                outline=outline,
                prompt_payload=prompt_payload,
                paragraph_map=paragraph_map,
                limit=2,
            )
            candidates = self._limit_targets_preserving_table(table_targets, limit)
        summary = str((payload or {}).get('summary') or f'AI 已阅读全文并定位到 {len(candidates)} 个可补充数据图表的位置。')
        if table_summary and not any(item.get('artifactType') == 'table' for item in candidates):
            summary = f'{summary} 插表复核：{table_summary}'
        return {
            'targets': candidates,
            'summary': summary,
        }

    @staticmethod
    def _parse_number(value):
        text = str(value or '').strip().replace(',', '').replace('，', '')
        if not text:
            return None
        text = re.sub(r'[％%]\s*$', '', text)
        match = re.search(r'[-+]?\d+(?:\.\d+)?', text)
        if not match:
            return None
        try:
            return float(match.group(0))
        except Exception:
            return None

    @classmethod
    def parse_data_table(cls, table_text):
        text = cls._normalize_text(table_text)
        if not text:
            raise ValueError('请先填写已审核的数据表')
        delimiter = '\t' if '\t' in text else ','
        rows = []
        for row in csv.reader(io.StringIO(text), delimiter=delimiter):
            cleaned = [str(cell or '').strip() for cell in row]
            if any(cleaned):
                rows.append(cleaned)
        if not rows:
            raise ValueError('没有识别到有效数据行')

        first_row = rows[0] if rows else []
        first_value = cls._parse_number(first_row[1]) if len(first_row) >= 2 else None
        has_header = first_value is None
        data_rows = rows if not has_header else rows[1:]
        header_map = cls._data_table_header_map(first_row) if has_header else {}
        result = []
        for index, row in enumerate(data_rows, start=1):
            if len(row) < 2:
                continue
            label = cls._table_cell(row, header_map, 'label', 0) or f'项目{index}'
            raw_value = cls._table_cell(row, header_map, 'value', 1)
            value = cls._parse_number(raw_value)
            if value is None:
                continue
            source_name = cls._table_cell(row, header_map, 'sourceName', 2 if not has_header and len(row) >= 4 else None)
            publisher = cls._table_cell(row, header_map, 'publisher', 3 if not has_header and len(row) >= 4 else None)
            url = cls._table_cell(row, header_map, 'url', 4 if not has_header and len(row) >= 5 else None)
            note = cls._table_cell(row, header_map, 'note', 5 if not has_header and len(row) >= 6 else None)
            source = cls._table_cell(row, header_map, 'source', 6 if not has_header and len(row) >= 7 else None)
            if not source and not has_header and len(row) == 3:
                source = row[2]
            if not source:
                source = cls._format_source({
                    'sourceName': source_name,
                    'publisher': publisher,
                    'url': url,
                    'note': note,
                })
            result.append({
                'label': label[:40],
                'value': value,
                'rawValue': raw_value,
                'source': source,
                'sourceName': source_name,
                'publisher': publisher,
                'url': url,
                'note': note,
            })
        if len(result) < 2:
            raise ValueError('至少需要 2 行带数值的数据，例如：标签,数值')
        return result

    @staticmethod
    def _data_table_header_map(header):
        aliases = {
            'label': {'标签', '项目', '名称', '年份', '地区', '指标', 'label', 'name'},
            'value': {'数值', '值', '数据', 'value', 'number'},
            'sourceName': {'来源名称', '报告名称', '数据库名称', '网站名称', '来源名', 'sourceName', 'sourcename', 'source_name', 'source name'},
            'publisher': {'发布机构', '机构', '作者', 'publisher', 'organization'},
            'url': {'链接', '网址', 'url', '来源链接'},
            'note': {'备注', '页码/口径', '页码', '口径', '说明', 'note'},
            'source': {'来源/备注', '来源', 'source', 'reference'},
        }
        normalized = [str(cell or '').strip() for cell in header or []]
        result = {}
        for index, name in enumerate(normalized):
            key = re.sub(r'\s+', ' ', name).strip()
            lower_key = key.lower()
            for field, names in aliases.items():
                if key in names or lower_key in names:
                    result[field] = index
                    break
        return result

    @staticmethod
    def _table_cell(row, header_map, field, fallback_index):
        index = header_map.get(field, fallback_index)
        if index is None or index >= len(row):
            return ''
        return str(row[index] or '').strip()

    def _request_search_payload(self, api, *, artifact_type, target, search_query, chart_title_hint, full_text, evidence_note, quality_feedback=''):
        feedback = ''
        intent = str(target.get('intent') or target.get('suggestion') or target.get('reason') or '').strip()
        title_hint = self._target_title_hint(target) or chart_title_hint
        if quality_feedback:
            feedback = f'''

上一次返回的数据被系统判定为不可用，原因：{quality_feedback}
请重新检索真实统计值。不要返回变量代码、控制变量名称、指标编号、序号、变量定义或“预测方向”。如果只能找到变量说明而找不到真实数值，请返回 needsManualData=true。'''
        system = (
            '你是论文数据检索助手。你需要根据论文段落和检索式寻找可用于论文图表的数据，并给出清晰来源。'
            '必须优先使用用户提供的“候选网页来源”，并诚实说明来源。'
            '如果候选网页来源中没有足够数据，且你无法确认真实数据来源，不要编造数值、网址、报告名或年份。'
        )
        prompt = f'''请围绕下面论文段落，自行判断应使用哪些真实数据来生成{"表格" if artifact_type == "table" else "图表"}，并返回可供用户审核的数据表。

硬性要求：
1. 数据必须优先服务于“候选位置黑色建议/检索意图”，不要因为论文总题目而改找无关数据。
2. 候选网页来源是后端按 AI 给出的检索方向抓取的证据；请优先从这些来源中提取或归纳可制图数据。
3. 不要编造不存在的数据来源；候选来源不足时，可以使用你能够确认的公开权威报告、统计年鉴、政府/机构数据库或论文中的数据，但必须写明可核验的来源名称、发布机构、年份、页码/表号/检索路径。只有在无法确认真实数值时才返回 needsManualData=true。
4. rows 至少 2 行；每行包含 label、value，并尽量填写 sourceName、publisher、url、note。没有 URL 时不要因为 URL 缺失而放弃，但 note 必须说明页码、表号、统计口径或核验路径。
5. value 只填真实统计数值，单位写在 unit；禁止把变量编号、排序序号、分类编码、变量名称、预测方向写成 value。
6. label 应该是年份、地区、组别、行业、指标项等可解释对象；禁止返回 pgdp、urban、fagri、indstr、edu、internet 等变量代码作为绘图标签，除非这是“变量说明表”且 value 为空。
7. sourceNote 要提示用户逐项审核真实性。
8. title 必须直接概括数据指标、对象和时间范围，不要使用论文总题目、章节题或“影响因素”这类泛化标题。
{feedback}

论文段落：
{self._truncate_for_prompt(target.get('originalText') or target.get('excerpt'), 1800)}

数据需求：{target.get('dataNeed') or '未提供'}
候选位置黑色建议/检索意图：{intent or '未提供'}
建议图题/表题：{title_hint or '未提供'}
候选类型：{"插表" if artifact_type == "table" else "插图"}
检索方向：{search_query or '未提供'}
全文上下文：
{self._truncate_for_prompt(full_text, 6000)}

候选网页来源 JSON：
{evidence_note}

返回 JSON：
{{
  "needsManualData": false,
  "unit": "%",
  "chartType": "line|bar|pie",
  "title": "图表标题",
  "sourceNote": "数据来源审核说明",
  "rows": [
    {{
      "label": "2021年",
      "value": 12.5,
      "sourceName": "报告或数据库名称",
      "publisher": "发布机构",
      "url": "https://...",
      "note": "页码、表号、统计口径或核验说明"
    }}
  ],
  "manualHint": "如果需要用户补充，写明应补充什么"
}}'''
        return api.call_json_sync(
            prompt,
            system=system,
            temperature=0.12 if quality_feedback else 0.15,
            max_tokens=3200,
            request_timeout=160,
            schema_name='data_chart_search',
            usage_context=self._usage_context('data_chart.search.retry' if quality_feedback else 'data_chart.search'),
        )

    def search_data(self, *, query='', target=None, full_text='', user_data=''):
        if user_data and self._normalize_text(user_data):
            rows = self.parse_data_table(user_data)
            artifact_type = self._normalize_artifact_type((target or {}).get('artifactType') or (target or {}).get('insertType'))
            title = self._normalize_chart_title((target or {}).get('tableTitle') or (target or {}).get('chartTitle') or query, rows, target or {})
            chart_type = self._choose_chart_type((target or {}).get('chartType'), rows, target or {})
            return {
                'artifactType': artifact_type,
                'tableText': self._format_table(rows),
                'sourceNote': '已使用用户提供的数据表；生成图表前仍建议核对来源与单位。',
                'foundRows': len(rows),
                'dataRows': self._public_rows(rows),
                'dataSources': self._collect_row_sources(rows),
                'sourceItems': self._build_source_items(rows, []),
                'needsManualData': False,
                'title': title,
                'chartType': chart_type,
            }

        api = self._require_ai('data_chart.search')
        target = target or {}
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        intent = str(target.get('intent') or target.get('suggestion') or target.get('reason') or '').strip()
        target_title_hint = self._target_title_hint(target)
        search_query = query or intent or target.get('query') or ''
        chart_title_hint = str(target_title_hint or target.get('tableTitle') or target.get('chartTitle') or '').strip()
        evidence_query = ' '.join(part for part in (intent, search_query, chart_title_hint, target.get('dataNeed')) if part).strip()
        evidence_query = evidence_query or target.get('sectionTitle')
        if chart_title_hint and chart_title_hint not in evidence_query:
            evidence_query = f'{chart_title_hint} {evidence_query}'
        evidence = self._collect_search_evidence(evidence_query, limit=6, page_limit=3)
        evidence_note = (
            json.dumps(evidence, ensure_ascii=False, indent=2)
            if evidence
            else '未抓取到可核验网页来源。若模型也无法确认真实来源，必须返回 needsManualData=true。'
        )
        payload = self._request_search_payload(
            api,
            artifact_type=artifact_type,
            target=target,
            search_query=search_query,
            chart_title_hint=chart_title_hint,
            full_text=full_text,
            evidence_note=evidence_note,
        )
        rows = self._coerce_ai_rows(payload.get('rows', []) if isinstance(payload, dict) else [])
        quality_issue = self._data_quality_issue(rows, target=target, payload=payload)
        if quality_issue and not bool((payload or {}).get('needsManualData')):
            retry_payload = self._request_search_payload(
                api,
                artifact_type=artifact_type,
                target=target,
                search_query=search_query,
                chart_title_hint=chart_title_hint,
                full_text=full_text,
                evidence_note=evidence_note,
                quality_feedback=quality_issue,
            )
            retry_rows = self._coerce_ai_rows(retry_payload.get('rows', []) if isinstance(retry_payload, dict) else [])
            retry_issue = self._data_quality_issue(retry_rows, target=target, payload=retry_payload)
            if not retry_issue and (len(retry_rows) >= 2 or len(retry_rows) >= len(rows)):
                payload = retry_payload
                rows = retry_rows
                quality_issue = ''
        source_warning = bool(rows) and not self._rows_have_verifiable_sources(rows)
        model_needs_manual = bool((payload or {}).get('needsManualData'))
        needs_manual = bool(quality_issue) or len(rows) < 2 or (model_needs_manual and not rows)
        evidence_sources = self._collect_evidence_sources(evidence)
        if needs_manual:
            source_note = str((payload or {}).get('manualHint') or (payload or {}).get('sourceNote') or '').strip()
            if quality_issue:
                source_note = f'{quality_issue} 请重新检索真实统计数据，或在下方手动录入已核验的数值、来源名称、发布机构、链接/页码。'
            else:
                source_note = source_note or 'AI 未能确认可直接作图的数据来源，请补充真实数据与来源后再生成图表。'
            public_rows = [] if quality_issue else self._public_rows(rows)
            return {
                'artifactType': artifact_type,
                'tableText': '' if quality_issue else (self._format_table(rows) if rows else ''),
                'sourceNote': source_note,
                'foundRows': len(rows),
                'dataRows': public_rows,
                'dataSources': evidence_sources,
                'sourceItems': self._build_source_items([] if quality_issue else rows, evidence),
                'needsManualData': True,
                'chartType': self._choose_chart_type((payload or {}).get('chartType') or target.get('chartType'), rows, target),
                'title': self._normalize_chart_title((payload or {}).get('title') or chart_title_hint, rows, target),
                'unit': str((payload or {}).get('unit') or '').strip(),
            }
        source_note = str((payload or {}).get('sourceNote') or 'AI 已整理数据来源；请用户核验来源、口径和年份后再生成图表。')
        if model_needs_manual:
            source_note = source_note or str((payload or {}).get('manualHint') or '').strip()
            if '审核' not in source_note and '核验' not in source_note:
                source_note = f'{source_note}；请用户审核真实性后再生成图表。'
        if source_warning:
            warning = '部分行缺少完整链接、页码或发布机构，已保留到可编辑数据表，请用户按来源逐项核验后再生成图表。'
            source_note = f'{source_note}；{warning}' if source_note else warning
        return {
            'artifactType': artifact_type,
            'tableText': self._format_table(rows),
            'sourceNote': source_note,
            'foundRows': len(rows),
            'dataRows': self._public_rows(rows),
            'dataSources': self._merge_sources(self._collect_row_sources(rows), evidence_sources),
            'sourceItems': self._build_source_items(rows, evidence),
            'needsManualData': False,
            'sourceRisk': source_warning or model_needs_manual,
            'chartType': self._choose_chart_type((payload or {}).get('chartType') or target.get('chartType'), rows, target),
            'title': self._normalize_chart_title((payload or {}).get('title') or chart_title_hint, rows, target),
            'unit': str((payload or {}).get('unit') or '').strip(),
        }

    @staticmethod
    def _collect_row_sources(rows):
        sources = []
        seen = set()
        for row in rows:
            source = str(row.get('source', '') or '').strip()
            if not source or source in seen:
                continue
            sources.append(source)
            seen.add(source)
        return sources

    @staticmethod
    def _collect_evidence_sources(evidence):
        sources = []
        for item in evidence or []:
            title = str(item.get('title', '') or '').strip()
            url = str(item.get('url', '') or '').strip()
            snippet = str(item.get('snippet', '') or '').strip()
            if not title and not url:
                continue
            text = '；'.join(part for part in (title, url, snippet[:160]) if part)
            sources.append(text)
        return sources

    @classmethod
    def _build_source_items(cls, rows, evidence):
        items = []
        item_map = {}
        seen = set()
        evidence_by_url = {}
        for item in evidence or []:
            url = str(item.get('url', '') or '').strip()
            if url:
                evidence_by_url[url] = item
        for row in rows or []:
            source = str(row.get('source', '') or '').strip()
            source_name = str(row.get('sourceName', '') or '').strip()
            publisher = str(row.get('publisher', '') or '').strip()
            url = str(row.get('url', '') or '').strip()
            note = str(row.get('note', '') or '').strip()
            key = url or source or f'{source_name}|{publisher}|{note}'
            if not key:
                continue
            evidence_item = evidence_by_url.get(url, {})
            title = source_name or str(evidence_item.get('title', '') or '').strip() or source or '数据来源'
            row_bits = [str(row.get('label', '') or '').strip(), str(row.get('rawValue', row.get('value', '')) or '').strip()]
            if source_name:
                row_bits.append(source_name)
            if publisher:
                row_bits.append(publisher)
            summary = '，'.join(bit for bit in row_bits if bit)
            if note:
                summary = f'{summary}；{note}' if summary else note
            if key in item_map:
                item_map[key]['rows'].append(cls._public_row(row))
                if summary:
                    existing_summary = str(item_map[key].get('summary', '') or '')
                    if summary not in existing_summary:
                        item_map[key]['summary'] = f'{existing_summary}\n{summary}'.strip()
                continue
            item_map[key] = {
                'title': title,
                'url': url,
                'publisher': publisher,
                'sourceName': source_name,
                'summary': summary or source,
                'snippet': str(evidence_item.get('snippet', '') or '').strip(),
                'rows': [cls._public_row(row)],
            }
            items.append(item_map[key])
            seen.add(key)
        for item in evidence or []:
            url = str(item.get('url', '') or '').strip()
            title = str(item.get('title', '') or '').strip()
            key = url or title
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({
                'title': title or '候选网页来源',
                'url': url,
                'publisher': '',
                'sourceName': '',
                'summary': str(item.get('snippet', '') or '').strip(),
                'snippet': str(item.get('snippet', '') or '').strip(),
                'rows': [],
            })
        return items

    @staticmethod
    def _merge_sources(*groups):
        merged = []
        seen = set()
        for group in groups:
            for source in group or []:
                value = str(source or '').strip()
                if not value or value in seen:
                    continue
                merged.append(value)
                seen.add(value)
        return merged

    @staticmethod
    def _format_table(rows):
        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['标签', '数值', '来源名称', '发布机构', '链接', '备注', '来源/备注'])
        for row in rows:
            writer.writerow([
                row.get('label', ''),
                row.get('rawValue', row.get('value', '')),
                row.get('sourceName', ''),
                row.get('publisher', ''),
                row.get('url', ''),
                row.get('note', ''),
                row.get('source', ''),
            ])
        return output.getvalue().strip()

    @staticmethod
    def _target_artifact_label(target, kind='figure'):
        if not isinstance(target, dict):
            return '表1' if kind == 'table' else '图1'
        field_names = ('tableLabel', 'artifactLabel') if kind == 'table' else ('figureLabel', 'artifactLabel')
        for field in field_names:
            value = re.sub(r'\s+', '', str(target.get(field, '') or '').strip())
            if value:
                return value
        return DataChartAssistant._default_artifact_label(target.get('sectionTitle', ''), kind)

    @staticmethod
    def _default_artifact_label(section_title='', kind='figure'):
        prefix = '表' if kind == 'table' else '图'
        match = re.match(r'^\s*(\d+)(?:\.\d+)*', str(section_title or '').strip())
        if match:
            return f'{prefix}{match.group(1)}.1'
        return f'{prefix}1'

    def generate_chart(self, *, table_text='', chart_type='bar', title='', unit='', target=None):
        rows = self.parse_data_table(table_text)
        target = target or {}
        unit = str(unit or '').strip()
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        title, chart_type = self._resolve_chart_metadata(rows, target, title, unit, chart_type)
        if artifact_type == 'table':
            return self._generate_table_result(rows, target=target, title=title, unit=unit, chart_type=chart_type)
        image = self._render_chart(rows, chart_type=chart_type, title=title, unit=unit)
        data_url = self._image_to_data_url(image)
        caption = self._build_caption(rows, chart_type, title, unit)
        figure_label = self._target_artifact_label(target, 'figure')
        replacement = self._build_replacement_text(target, caption, rows=rows, title=title, unit=unit, chart_type=chart_type, figure_label=figure_label)
        reference_entries = self._reference_entries_from_rows(rows)
        figure_markdown = f'![{title}]({data_url})\n\n{figure_label} {caption}'
        return {
            'chart': {
                'dataUrl': data_url,
                'title': title,
                'caption': caption,
                'chartType': chart_type,
                'unit': unit,
                'rows': rows,
            },
            'replacementText': replacement,
            'figureMarkdown': figure_markdown,
            'artifactType': 'figure',
            'artifactLabel': figure_label,
            'sectionTitle': target.get('sectionTitle', '') if isinstance(target, dict) else '',
            'originalText': target.get('originalText', '') if isinstance(target, dict) else '',
            'summary': self._chart_summary(rows, unit),
            'referenceEntries': reference_entries,
        }

    def _generate_table_result(self, rows, *, target=None, title='', unit='', chart_type='bar'):
        target = target or {}
        caption = self._build_table_caption(title)
        table_label = self._target_artifact_label(target, 'table')
        table_markdown = self._build_table_markdown(rows, caption, unit, table_label=table_label)
        replacement = self._build_table_replacement_text(target, caption, table_markdown, rows=rows, unit=unit, table_label=table_label)
        reference_entries = self._reference_entries_from_rows(rows)
        return {
            'artifactType': 'table',
            'artifactLabel': table_label,
            'table': {
                'title': caption,
                'caption': caption,
                'unit': unit,
                'rows': rows,
            },
            'chart': None,
            'replacementText': replacement,
            'tableMarkdown': table_markdown,
            'sectionTitle': target.get('sectionTitle', '') if isinstance(target, dict) else '',
            'originalText': target.get('originalText', '') if isinstance(target, dict) else '',
            'summary': self._chart_summary(rows, unit),
            'referenceEntries': reference_entries,
        }

    @classmethod
    def _reference_entries_from_rows(cls, rows):
        records = cls._reference_source_records(rows)
        if not records:
            return []
        if len(records) == 1:
            return [records[0]]
        return [cls._combined_reference_entry(records)]

    @classmethod
    def _reference_source_records(cls, rows):
        entries = []
        seen = set()
        for row in rows or []:
            text = cls._reference_entry_from_row(row)
            key = re.sub(r'\s+', ' ', text).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append({
                'text': text,
                'sourceName': str(row.get('sourceName', '') or '').strip(),
                'publisher': str(row.get('publisher', '') or '').strip(),
                'url': str(row.get('url', '') or '').strip(),
            })
        return entries

    @staticmethod
    def _compact_source_list(values, limit=6):
        result = []
        seen = set()
        for value in values or []:
            raw = str(value or '').strip()
            text = raw if raw.startswith(('http://', 'https://')) else DataChartAssistant._shorten_source_name(raw, 42)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _combined_reference_entry(cls, entries):
        publishers = cls._compact_source_list([entry.get('publisher', '') for entry in entries], limit=3)
        source_names = cls._compact_source_list([entry.get('sourceName', '') for entry in entries], limit=3)
        urls = cls._compact_source_list([entry.get('url', '') for entry in entries], limit=2)
        if len(publishers) == 1:
            author = publishers[0]
        elif publishers:
            author = '；'.join(publishers[:3]) + ('等' if len(publishers) > 3 else '')
        else:
            author = '相关数据发布机构'

        if source_names:
            title = '、'.join(source_names[:3])
            if len(source_names) > 3:
                title += '等'
        else:
            title = '图表数据来源汇总'

        if urls:
            suffix = f'[R/OL]. {urls[0]}.'
            if len(urls) > 1:
                suffix += f' 另见：{"；".join(urls[1:])}.'
        else:
            suffix = '[R].'

        text = re.sub(r'\s+', ' ', f'{author}. {title}{suffix}').strip()
        return {
            'text': text,
            'sourceName': title,
            'publisher': author,
            'url': urls[0] if urls else '',
            'source': text,
            'note': '同一图表的数据来源已按发布机构和报告名称合并引用，逐行原始来源保留在可编辑数据表中。',
        }

    @staticmethod
    def _clean_reference_part(value):
        text = re.sub(r'\s+', ' ', str(value or '').strip())
        text = text.strip('，,。.;； ')
        return text

    @staticmethod
    def _reference_entry_from_row(row):
        source_name = DataChartAssistant._clean_reference_part(row.get('sourceName', ''))
        publisher = DataChartAssistant._clean_reference_part(row.get('publisher', ''))
        url = str(row.get('url', '') or '').strip()
        note = DataChartAssistant._clean_reference_part(row.get('note', ''))
        source = DataChartAssistant._clean_reference_part(row.get('source', ''))
        title = source_name or source or url or '数据来源'
        author = publisher or '数据发布机构'
        if url:
            suffix = f'[R/OL]. {url}.'
        else:
            suffix = '[R].'
        entry = f'{author}. {title}{suffix}'
        if note and note not in entry and not url.startswith(note):
            entry = f'{entry} {note}'
        return re.sub(r'\s+', ' ', entry).strip()

    @classmethod
    def _chart_summary(cls, rows, unit=''):
        analysis = cls._chart_analysis(rows, unit)
        if analysis:
            return analysis
        return ''

    @classmethod
    def _chart_analysis(cls, rows, unit=''):
        structure = cls._series_structure(rows)
        if structure:
            parts = cls._structured_series_analysis(structure, unit)
            if parts:
                return '；'.join(parts) + '。'
        values = [float(row['value']) for row in rows or [] if row.get('value') is not None]
        if not values:
            return ''
        labels = [str(row.get('label', '') or f'项目{index + 1}') for index, row in enumerate(rows or [])]
        max_index = max(range(len(values)), key=lambda idx: values[idx])
        min_index = min(range(len(values)), key=lambda idx: values[idx])
        delta = values[-1] - values[0]
        suffix = unit or ''
        trend = '上升' if delta > 0 else '下降' if delta < 0 else '基本持平'
        return (
            f'最高值为{labels[max_index]}的{values[max_index]:g}{suffix}，'
            f'最低值为{labels[min_index]}的{values[min_index]:g}{suffix}，'
            f'首末项相比{trend}{abs(delta):g}{suffix}。'
        )

    @classmethod
    def _structured_series_analysis(cls, structure, unit=''):
        parts = []
        years = structure.get('years', [])
        for series in (structure.get('series') or [])[:5]:
            values_by_year = structure.get('values', {}).get(series, {})
            points = [(year, values_by_year[year]) for year in years if year in values_by_year]
            if not points:
                continue
            first_year, first_value = points[0]
            last_year, last_value = points[-1]
            if len(points) == 1:
                parts.append(f'{series}在{first_year}为{float(first_value):g}{unit}')
                continue
            delta = float(last_value) - float(first_value)
            if '差距' in series:
                trend = '扩大' if delta > 0 else '缩小' if delta < 0 else '基本持平'
            else:
                trend = '升至' if delta > 0 else '降至' if delta < 0 else '保持在'
            if trend in {'升至', '降至'}:
                parts.append(f'{series}由{first_year}的{float(first_value):g}{unit}{trend}{last_year}的{float(last_value):g}{unit}')
            elif trend == '保持在':
                parts.append(f'{series}在{first_year}至{last_year}基本保持在{float(last_value):g}{unit}')
            else:
                parts.append(f'{series}由{first_year}的{float(first_value):g}{unit}{trend}至{last_year}的{float(last_value):g}{unit}')
        gap_text = cls._structured_gap_analysis(structure, unit)
        if gap_text:
            parts.append(gap_text)
        return parts

    @classmethod
    def _structured_gap_analysis(cls, structure, unit=''):
        series_names = structure.get('series') or []
        years = structure.get('years') or []
        values = structure.get('values') or {}
        explicit_gap = next((name for name in series_names if '差距' in name), '')
        if explicit_gap:
            return ''
        urban = next((name for name in series_names if any(term in name for term in ('城镇', '城市'))), '')
        rural = next((name for name in series_names if '农村' in name), '')
        if not urban or not rural:
            return ''
        gap_points = []
        for year in years:
            if year in values.get(urban, {}) and year in values.get(rural, {}):
                gap_points.append((year, float(values[urban][year]) - float(values[rural][year])))
        if len(gap_points) < 2:
            return ''
        first_year, first_gap = gap_points[0]
        last_year, last_gap = gap_points[-1]
        delta = last_gap - first_gap
        trend = '扩大' if delta > 0 else '缩小' if delta < 0 else '基本持平'
        return f'{urban}与{rural}差距由{first_year}的{first_gap:g}{unit}{trend}至{last_year}的{last_gap:g}{unit}'

    @classmethod
    def _data_points_text(cls, rows, limit=18):
        points = []
        for row in rows or []:
            label = str(row.get('label', '') or '').strip()
            value = row.get('rawValue', row.get('value', ''))
            if label and value != '':
                points.append(f'{label}={value}')
            if len(points) >= limit:
                break
        return '；'.join(points)

    @classmethod
    def _analysis_mentions_data(cls, text, rows):
        value_text = str(text or '')
        checked = 0
        for row in rows or []:
            raw_value = str(row.get('rawValue', row.get('value', '')) or '').strip()
            if not raw_value:
                continue
            normalized = raw_value.rstrip('0').rstrip('.') if '.' in raw_value else raw_value
            if raw_value in value_text or (normalized and normalized in value_text):
                checked += 1
            if checked >= 2:
                return True
        return checked >= min(1, len(rows or []))

    @classmethod
    def _build_data_analysis_paragraph(cls, rows, source_text='', unit='', title='', figure_label='图1'):
        analysis = cls._chart_analysis(rows, unit)
        label = figure_label or '图1'
        title_part = f'{label}所示的“{title}”' if title else f'{label}所示数据'
        if analysis:
            return f'如{title_part}，{analysis}'
        return f'如{title_part}，相关指标呈现出可比较的差异特征，需要结合研究问题进一步解释其变化方向。'

    @classmethod
    def _build_table_analysis_paragraph(cls, rows, unit='', title='', table_label='表1'):
        analysis = cls._chart_analysis(rows, unit)
        label = table_label or '表1'
        title_part = f'{label}所列的“{title}”' if title else f'{label}所列数据'
        if analysis:
            return f'根据{title_part}，{analysis}'
        return f'根据{title_part}，相关指标在不同项目之间存在可比较差异，需要结合研究问题进一步解释其变化方向。'

    @staticmethod
    def _strip_source_prose(text):
        sentences = re.split(r'([。！？!?]\s*)', str(text or ''))
        if len(sentences) <= 1:
            return re.sub(
                r'(?:数据来源|来源(?:为|于|来自)|资料来源|source\s*:?).*?$',
                '',
                str(text or ''),
                flags=re.IGNORECASE | re.MULTILINE,
            ).strip()
        kept = []
        for index in range(0, len(sentences), 2):
            sentence = sentences[index]
            punctuation = sentences[index + 1] if index + 1 < len(sentences) else ''
            compact = re.sub(r'\s+', '', sentence)
            lowered = sentence.lower()
            source_only = (
                re.search(r'(数据来源|资料来源|来源(?:为|于|来自)|上述数据来源|source\s*:?)', sentence, flags=re.IGNORECASE)
                or 'http://' in lowered
                or 'https://' in lowered
            )
            if source_only:
                continue
            kept.append(sentence + punctuation)
        return ''.join(kept).strip()

    @classmethod
    def _ensure_rewritten_has_data_analysis(cls, rewritten, rows, source_text='', unit='', title='', figure_label='图1'):
        text = cls._normalize_text(cls._strip_source_prose(rewritten))
        analysis_paragraph = cls._build_data_analysis_paragraph(rows, source_text, unit, title, figure_label)
        if not text:
            return analysis_paragraph
        if cls._analysis_mentions_data(text, rows):
            return text
        return f'{text}\n\n{analysis_paragraph}'.strip()

    @staticmethod
    def _clean_caption_title(title):
        text = re.sub(r'\s+', ' ', str(title or '论文数据图表').strip())
        text = re.sub(r'^\s*(?:图\s*\d+|Figure\s*\d+)\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^\s*表\s*\d+(?:\.\d+)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\[[\d,\-\s]+\]\s*$', '', text)
        chart_type_match = re.match(
            r'^(.+?[（(](?:柱状图|条形图|折线图|结构图|饼图|图表|line chart|bar chart|pie chart|chart)[）)])',
            text,
            flags=re.IGNORECASE,
        )
        if chart_type_match:
            text = chart_type_match.group(1).strip()
        text = re.sub(
            r'[（(](?:柱状图|条形图|折线图|结构图|饼图|图表|line chart|bar chart|pie chart|chart)[）)]\s*$',
            '',
            text,
            flags=re.IGNORECASE,
        ).strip()
        first_sentence = re.split(r'[。.!?！？]\s*', text, maxsplit=1)[0].strip()
        return first_sentence or '论文数据图表'

    def _build_caption(self, rows, chart_type, title, unit):
        clean_title = self._clean_caption_title(title)
        return clean_title

    def _build_table_caption(self, title):
        text = self._clean_caption_title(title)
        text = re.sub(r'图表|数据图|统计图|图$', '表', text)
        return text or '论文数据表'

    @staticmethod
    def _markdown_table_escape(value):
        return str(value if value is not None else '').replace('|', '\\|').strip()

    @classmethod
    def _source_note_text(cls, rows):
        sources = []
        seen = set()
        total_unique = 0
        for row in rows or []:
            text = cls._short_source_label(row)
            if not text or text in seen:
                continue
            seen.add(text)
            total_unique += 1
            if len(sources) < 2:
                sources.append(text)
        suffix = '等' if total_unique > len(sources) else ''
        return '；'.join(sources) + suffix if sources else '用户审核后的数据表'

    @classmethod
    def _build_table_markdown(cls, rows, title, unit='', table_label='表1'):
        lines = [f'{table_label or "表1"} {title}']
        if unit:
            lines.append(f'（单位：{unit}）')
        lines.extend([
            '| 指标 | 数值 |',
            '| --- | ---: |',
        ])
        for row in rows or []:
            label = cls._markdown_table_escape(row.get('label', ''))
            value = cls._markdown_table_escape(row.get('rawValue', row.get('value', '')))
            lines.append(f'| {label} | {value} |')
        lines.append(f'资料来源：{cls._source_note_text(rows)}')
        return '\n'.join(lines).strip()

    def _build_replacement_text(self, target, caption, *, rows=None, title='', unit='', chart_type='bar', figure_label='图1'):
        original = DataChartAssistant._normalize_text(target.get('originalText', '') if isinstance(target, dict) else '')
        rows = rows or []
        source_text = self._source_summary(rows)
        analysis_text = self._chart_analysis(rows, unit)
        data_points_text = self._data_points_text(rows)
        if self.api and hasattr(self.api, 'call_sync') and original:
            system = (
                '你是严谨的论文写作助手。请根据已确认的真实数据改写原段落，必须补入数据分析。'
                '如果原段落有空泛、模糊或占位式表述，可以删改，但删改后必须用真实数据解释替代。'
                '数据来源由系统写入参考文献并自动添加引用编号，正文不要写来源名称、URL或“数据来源为”。'
            )
            rows_payload = [
                {
                    'label': row.get('label'),
                    'value': row.get('value'),
                    'rawValue': row.get('rawValue', row.get('value')),
                    'sourceName': row.get('sourceName', ''),
                    'publisher': row.get('publisher', ''),
                    'source': row.get('source', ''),
                }
                for row in rows
            ]
            prompt = f'''请改写下面论文段落，使其自然引入{figure_label}，并分析图表数据。

要求：
1. 保留原段落核心观点，但语言更连贯。
2. 正文只写论文式分析，不要写数据来源、来源名称、报告名、发布机构、URL或“数据来源为/来源来自”等说明；来源将由系统写入参考文献并用引用编号标注。
3. 必须直接引用“真实数据分析”中的关键数值，不得只写“持续改善、差异明显、数字基础条件改善”等泛泛判断。
4. 必须解释最高值、最低值、趋势、差距变化或结构占比中至少两类信息；多序列数据要说明各序列的首末变化。
5. 如果删除原段落中的模糊分析，必须用真实数据重写该分析，不能只留下“请先准备原文与处理结果”等占位句。
6. 可以写成 1-2 个自然段，不要输出标题，不要输出 Markdown 图片。
7. 正文提到图时使用“{figure_label}”，不要自行改成其他编号。

原段落：
{original}

图表标题：{title}
图表类型：{chart_type}
图表说明：{caption}
单位：{unit or '未注明'}
真实数据点：
{data_points_text or '无'}
真实数据分析：
{analysis_text or '请根据数据行 JSON 自行归纳。'}
数据行 JSON：
{json.dumps(rows_payload, ensure_ascii=False)}
参考文献信息（仅供系统写入参考文献，不要出现在正文中）：
{source_text or '用户提供的数据表，来源待用户审核。'}

请直接输出改写后的段落。'''
            try:
                rewritten = self.api.call_sync(
                    prompt,
                    system=system,
                    temperature=0.35,
                    max_tokens=1800,
                    request_timeout=120,
                    usage_context=self._usage_context('data_chart.rewrite'),
                )
                rewritten = self._normalize_text(rewritten)
                if rewritten:
                    return self._ensure_rewritten_has_data_analysis(rewritten, rows, source_text, unit, title, figure_label)
            except Exception:
                pass
        intro = self._build_data_analysis_paragraph(rows, source_text, unit, title, figure_label)
        if not original:
            return intro
        if figure_label in original or '如图' in original:
            return f'{original}\n\n{intro}'
        return f'{original}\n\n为增强上述论证的数据支撑，本文补充可视化结果。{intro}'

    def _build_table_replacement_text(self, target, caption, table_markdown, *, rows=None, unit='', table_label='表1'):
        original = DataChartAssistant._normalize_text(target.get('originalText', '') if isinstance(target, dict) else '')
        rows = rows or []
        analysis_text = self._chart_analysis(rows, unit)
        data_points_text = self._data_points_text(rows)
        if self.api and hasattr(self.api, 'call_sync') and original:
            system = (
                f'你是严谨的论文写作助手。请根据已确认的数据表改写原段落，必须自然引入{table_label}并补入数据分析。'
                '数据来源由系统写入表下注释和参考文献，正文不要写来源名称、URL或“数据来源为”。'
            )
            rows_payload = [
                {
                    'label': row.get('label'),
                    'value': row.get('value'),
                    'rawValue': row.get('rawValue', row.get('value')),
                    'sourceName': row.get('sourceName', ''),
                    'publisher': row.get('publisher', ''),
                }
                for row in rows
            ]
            prompt = f'''请改写下面论文段落，使其自然引入{table_label}，并分析表中数据。

要求：
1. 保留原段落核心观点，但将空泛判断改为基于真实数据的分析。
2. 正文使用“如{table_label}所示”“见{table_label}”等论文表述，不要输出 Markdown 表格。
3. 正文不要写来源名称、报告名、发布机构、URL或“数据来源为/来源来自”；来源会出现在表下“资料来源”和参考文献中。
4. 必须直接引用关键数值，并解释最高值、最低值、趋势、差距变化或结构占比中至少两类信息。
5. 可以写成 1-2 个自然段，不要输出标题。
6. 正文提到表时使用“{table_label}”，不要自行改成其他编号。

原段落：
{original}

表题：{caption}
单位：{unit or '未注明'}
真实数据点：
{data_points_text or '无'}
真实数据分析：
{analysis_text or '请根据数据行 JSON 自行归纳。'}
数据行 JSON：
{json.dumps(rows_payload, ensure_ascii=False)}

请直接输出改写后的段落。'''
            try:
                rewritten = self.api.call_sync(
                    prompt,
                    system=system,
                    temperature=0.35,
                    max_tokens=1800,
                    request_timeout=120,
                    usage_context=self._usage_context('data_chart.table_rewrite'),
                )
                rewritten = self._normalize_text(rewritten)
                if rewritten:
                    return self._ensure_rewritten_has_table_analysis(rewritten, rows, unit, caption, table_label)
            except Exception:
                pass
        intro = self._build_table_analysis_paragraph(rows, unit, caption, table_label)
        if not original:
            return intro
        if table_label in original or '如表' in original or '见表' in original:
            return f'{original}\n\n{intro}'
        return f'{original}\n\n为增强上述论证的数据支撑，本文补充{table_label}。{intro}'

    @classmethod
    def _ensure_rewritten_has_table_analysis(cls, rewritten, rows, unit='', title='', table_label='表1'):
        text = cls._normalize_text(cls._strip_source_prose(rewritten))
        analysis_paragraph = cls._build_table_analysis_paragraph(rows, unit, title, table_label)
        if not text:
            return analysis_paragraph
        if cls._analysis_mentions_data(text, rows):
            return text
        return f'{text}\n\n{analysis_paragraph}'.strip()

    @staticmethod
    def _source_summary(rows, limit=4):
        sources = []
        seen = set()
        for row in rows or []:
            source = DataChartAssistant._short_source_label(row)
            if not source or source in seen:
                continue
            sources.append(source)
            seen.add(source)
            if len(sources) >= limit:
                break
        return '；'.join(sources)

    @classmethod
    def _render_chart(cls, rows, *, chart_type='bar', title='', unit=''):
        width, height = 1100, 680
        image = Image.new('RGB', (width, height), '#fffaf5')
        draw = ImageDraw.Draw(image)
        font_title = cls._font(34, bold=True)
        font_body = cls._font(22)
        font_small = cls._font(18)
        font_tiny = cls._font(16)
        draw.rectangle((0, 0, width - 1, height - 1), outline='#eadfd4', width=2)
        cls._draw_wrapped_centered(draw, title, width // 2, 26, font_title, '#171717', max_width=940)
        structure = cls._series_structure(rows)
        if chart_type == 'pie':
            cls._draw_pie_chart(draw, rows, width, height, unit, font_body, font_small)
        elif chart_type == 'line' and structure:
            cls._draw_multi_series_line_chart(draw, structure, width, height, unit, font_body, font_small, font_tiny)
        elif chart_type == 'line':
            cls._draw_line_chart(draw, rows, width, height, unit, font_body, font_small, font_tiny)
        else:
            cls._draw_bar_chart(draw, rows, width, height, unit, font_body, font_small, font_tiny)
        draw.text((width - 250, height - 34), 'Python 生成图表', font=font_tiny, fill='#667085')
        return image

    @staticmethod
    def _font(size, bold=False):
        names = [
            'msyhbd.ttc' if bold else 'msyh.ttc',
            'simhei.ttf',
            'simsun.ttc',
            'arial.ttf',
        ]
        dirs = [
            os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
            '/usr/share/fonts/truetype/dejavu',
            '/usr/share/fonts/opentype/noto',
        ]
        for directory in dirs:
            for name in names:
                path = os.path.join(directory, name)
                if os.path.exists(path):
                    try:
                        return ImageFont.truetype(path, size=size)
                    except Exception:
                        continue
        return ImageFont.load_default()

    @staticmethod
    def _text_size(draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    @classmethod
    def _draw_centered(cls, draw, text, center_x, y, font, fill):
        width, _ = cls._text_size(draw, text, font)
        draw.text((center_x - width / 2, y), text, font=font, fill=fill)

    @classmethod
    def _draw_wrapped_centered(cls, draw, text, center_x, y, font, fill, max_width):
        value = str(text or '').strip()
        if not value:
            return
        lines = []
        current = ''
        for char in value:
            trial = f'{current}{char}'
            trial_width, trial_height = cls._text_size(draw, trial, font)
            if current and trial_width > max_width:
                lines.append(current)
                current = char
            else:
                current = trial
        if current:
            lines.append(current)
        line_height = max(cls._text_size(draw, line, font)[1] for line in lines) + 10
        for index, line in enumerate(lines[:2]):
            cls._draw_centered(draw, line, center_x, y + index * line_height, font, fill)

    @classmethod
    def _draw_bar_chart(cls, draw, rows, width, height, unit, font_body, font_small, font_tiny):
        left, top, right, bottom = 110, 115, width - 80, height - 110
        values = [float(row['value']) for row in rows]
        max_value = max(values) if values else 1
        max_value = max(max_value, 1)
        draw.line((left, top, left, bottom), fill='#475569', width=3)
        draw.line((left, bottom, right, bottom), fill='#475569', width=3)
        for step in range(5):
            y = bottom - (bottom - top) * step / 4
            value = max_value * step / 4
            draw.line((left - 8, y, right, y), fill='#eadfd4', width=1)
            draw.text((18, y - 12), f'{value:g}{unit}', font=font_tiny, fill='#667085')
        gap = 18
        count = len(rows)
        bar_w = max(28, ((right - left) - gap * (count + 1)) / max(count, 1))
        for index, row in enumerate(rows):
            value = float(row['value'])
            bar_h = (bottom - top) * value / max_value
            x0 = left + gap + index * (bar_w + gap)
            x1 = x0 + bar_w
            y0 = bottom - bar_h
            color = cls.DEFAULT_COLORS[index % len(cls.DEFAULT_COLORS)]
            draw.rounded_rectangle((x0, y0, x1, bottom), radius=8, fill=color)
            cls._draw_centered(draw, f'{value:g}{unit}', (x0 + x1) / 2, y0 - 28, font_tiny, '#171717')
            label = cls._short_label(row['label'], 8)
            label_w, _ = cls._text_size(draw, label, font_small)
            draw.text(((x0 + x1 - label_w) / 2, bottom + 16), label, font=font_small, fill='#171717')

    @classmethod
    def _draw_line_chart(cls, draw, rows, width, height, unit, font_body, font_small, font_tiny):
        left, top, right, bottom = 110, 115, width - 80, height - 110
        values = [float(row['value']) for row in rows]
        max_value = max(max(values), 1)
        min_value = min(values)
        span = max(max_value - min_value, 1)
        draw.line((left, top, left, bottom), fill='#475569', width=3)
        draw.line((left, bottom, right, bottom), fill='#475569', width=3)
        for step in range(5):
            y = bottom - (bottom - top) * step / 4
            value = min_value + span * step / 4
            draw.line((left - 8, y, right, y), fill='#eadfd4', width=1)
            draw.text((18, y - 12), f'{value:g}{unit}', font=font_tiny, fill='#667085')
        points = []
        for index, row in enumerate(rows):
            x = left + (right - left) * index / max(len(rows) - 1, 1)
            y = bottom - (bottom - top) * (float(row['value']) - min_value) / span
            points.append((x, y))
        if len(points) >= 2:
            draw.line(points, fill='#cc5f1b', width=5, joint='curve')
        for index, (x, y) in enumerate(points):
            row = rows[index]
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill='#cc5f1b', outline='#fffaf5', width=3)
            cls._draw_centered(draw, f'{float(row["value"]):g}{unit}', x, y - 34, font_tiny, '#171717')
            label = cls._short_label(row['label'], 9)
            label_w, _ = cls._text_size(draw, label, font_small)
            draw.text((x - label_w / 2, bottom + 16), label, font=font_small, fill='#171717')

    @classmethod
    def _draw_multi_series_line_chart(cls, draw, structure, width, height, unit, font_body, font_small, font_tiny):
        left, top, right, bottom = 110, 130, width - 190, height - 115
        all_values = [
            float(value)
            for series_values in structure.get('values', {}).values()
            for value in series_values.values()
        ]
        if not all_values:
            return
        max_value = max(max(all_values), 1)
        min_value = min(all_values)
        span = max(max_value - min_value, 1)
        draw.line((left, top, left, bottom), fill='#475569', width=3)
        draw.line((left, bottom, right, bottom), fill='#475569', width=3)
        for step in range(5):
            y = bottom - (bottom - top) * step / 4
            value = min_value + span * step / 4
            draw.line((left - 8, y, right, y), fill='#eadfd4', width=1)
            draw.text((18, y - 12), f'{value:g}{unit}', font=font_tiny, fill='#667085')

        years = structure.get('years', [])
        series_names = structure.get('series', [])
        x_positions = {
            year: left + (right - left) * index / max(len(years) - 1, 1)
            for index, year in enumerate(years)
        }
        for index, series in enumerate(series_names):
            color = cls.DEFAULT_COLORS[index % len(cls.DEFAULT_COLORS)]
            points = []
            for year in years:
                value = structure.get('values', {}).get(series, {}).get(year)
                if value is None:
                    continue
                x = x_positions[year]
                y = bottom - (bottom - top) * (float(value) - min_value) / span
                points.append((x, y, float(value), year))
            if len(points) >= 2:
                draw.line([(x, y) for x, y, _, _ in points], fill=color, width=4, joint='curve')
            for x, y, value, _ in points:
                draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline='#fffaf5', width=3)
                cls._draw_centered(draw, f'{value:g}{unit}', x, y - 30, font_tiny, '#171717')

        for index, year in enumerate(years):
            x = x_positions[year]
            label = cls._short_label(year, 8)
            label_w, _ = cls._text_size(draw, label, font_small)
            draw.text((x - label_w / 2, bottom + 16), label, font=font_small, fill='#171717')

        legend_x, legend_y = right + 28, top + 4
        for index, series in enumerate(series_names[:8]):
            color = cls.DEFAULT_COLORS[index % len(cls.DEFAULT_COLORS)]
            y = legend_y + index * 36
            draw.rounded_rectangle((legend_x, y, legend_x + 24, y + 24), radius=5, fill=color)
            draw.text((legend_x + 34, y - 1), cls._short_label(series, 10), font=font_tiny, fill='#171717')

    @classmethod
    def _draw_pie_chart(cls, draw, rows, width, height, unit, font_body, font_small):
        values = [max(0, float(row['value'])) for row in rows]
        total = sum(values) or 1
        box = (120, 130, 590, 600)
        start = -90
        for index, row in enumerate(rows):
            angle = 360 * values[index] / total
            color = cls.DEFAULT_COLORS[index % len(cls.DEFAULT_COLORS)]
            draw.pieslice(box, start=start, end=start + angle, fill=color, outline='#fffaf5', width=3)
            start += angle
        legend_x, legend_y = 650, 150
        for index, row in enumerate(rows):
            color = cls.DEFAULT_COLORS[index % len(cls.DEFAULT_COLORS)]
            y = legend_y + index * 52
            draw.rounded_rectangle((legend_x, y, legend_x + 28, y + 28), radius=6, fill=color)
            percent = values[index] / total * 100
            label = f'{row["label"]}: {float(row["value"]):g}{unit} / {percent:.1f}%'
            draw.text((legend_x + 42, y - 1), cls._short_label(label, 26), font=font_small, fill='#171717')

    @staticmethod
    def _short_label(label, limit):
        value = str(label or '').strip()
        return value if len(value) <= limit else value[:limit - 1] + '…'

    @staticmethod
    def _image_to_data_url(image):
        output = io.BytesIO()
        image.save(output, format='PNG', optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode('ascii')
        return f'data:image/png;base64,{encoded}'
