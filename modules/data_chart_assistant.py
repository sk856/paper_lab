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
import zipfile

from PIL import Image, ImageDraw, ImageFont


class DataChartAssistant:
    """AI-first workflow for locating chart opportunities and rendering charts."""

    SEARCH_TIMEOUT = 8
    PAGE_TIMEOUT = 8
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
    def _chinese_number_to_int(value):
        text = re.sub(r'\s+', '', str(value or ''))
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {
            '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
            '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        }
        units = {'十': 10, '百': 100, '千': 1000}
        total = 0
        current = 0
        for char in text:
            if char in digits:
                current = digits[char]
            elif char in units:
                total += (current or 1) * units[char]
                current = 0
            else:
                return None
        return total + current if total or current else None

    @classmethod
    def _section_number_from_title(cls, section_title):
        text = re.sub(r'\s+', '', str(section_title or '').strip())
        if not text:
            return ''
        match = re.match(r'^(?:第)?(\d+)(?:[章节篇部分]|[、.．])?', text)
        if match:
            return str(int(match.group(1)))
        match = re.match(r'^第([一二两三四五六七八九十百千〇零]+)[章节篇部分]', text)
        if not match:
            match = re.match(r'^([一二两三四五六七八九十百千〇零]+)[、.．]', text)
        if match:
            value = cls._chinese_number_to_int(match.group(1))
            return str(value) if value else ''
        return ''

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
                title = str(item.get('displayTitle') or item.get('display_title') or item.get('title', '') or '').strip() or '未命名章节'
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
        table_role = self._normalize_table_role(item.get('tableRole') or item.get('role'), f'{reason} {data_need} {intent} {chart_title}')
        table_kind = self._table_kind_for_role(table_role, item.get('tableKind'))
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
            'tableRole': table_role,
            'tableKind': table_kind,
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

    @classmethod
    def _artifact_sequence_label(cls, section_title='', kind='figure', sequence=1):
        prefix = '表' if kind == 'table' else '图'
        section_number = cls._section_number_from_title(section_title) or '1'
        try:
            seq = max(1, int(sequence or 1))
        except Exception:
            seq = 1
        return f'{prefix}{section_number}.{seq}'

    @classmethod
    def _artifact_label_parts(cls, value, kind='figure'):
        prefix = '表' if kind == 'table' else '图'
        match = re.match(rf'^\s*{prefix}\s*(\d+)(?:\.(\d+))?\s*$', str(value or '').strip())
        if not match:
            return None
        return match.group(1), match.group(2) or ''

    @classmethod
    def _should_recompute_artifact_label(cls, value, section_title='', kind='figure'):
        parts = cls._artifact_label_parts(value, kind)
        if not parts:
            return True
        section_number = cls._section_number_from_title(section_title) or '1'
        chapter, sequence = parts
        return (chapter != section_number) or not sequence

    @classmethod
    def _renumber_target_candidates(cls, candidates):
        counters = {}
        for item in candidates or []:
            kind = cls._normalize_artifact_type(item.get('artifactType') or item.get('insertType'))
            section_title = item.get('sectionTitle', '')
            section_number = cls._section_number_from_title(section_title) or '1'
            key = (section_number, kind)
            counters[key] = counters.get(key, 0) + 1
            label = cls._artifact_sequence_label(section_title, kind, counters[key])
            if kind == 'table':
                item['tableLabel'] = label
            else:
                item['figureLabel'] = label
            item['artifactLabel'] = label
        return candidates or []

    def _find_table_targets_with_ai(self, api, *, topic='', outline='', prompt_payload=None, paragraph_map=None, limit=2):
        prompt_payload = prompt_payload or []
        paragraph_map = paragraph_map or {}
        prompt = f'''请再次只从“插表”角度阅读论文段落列表，找出适合插入论文数据表的位置。

判断原则：
1. 必须按论文功能找表，不是看到关键词就选表。优先寻找四类插表：
   A. tableRole=impact_factors：在“影响因素/作用机制/驱动因素”论述处插入影响因素表，表体允许中文因素名称；
   B. tableRole=model_index：在模型、方程、综合评价指数、效果指数构建或代入处插入模型测算表，表体只允许年份、数字和变量符号；
   C. tableRole=variable_analysis：在变量选取、模型变量、因子/主成分/相关/描述性统计处插入变量分析表，表体只允许数字和变量符号；
   D. tableRole=evidence_data：在需要用基础数据增强观点处插入数据表，表体只允许年份、数字和变量符号。
2. 只要论文里有模型/方程/变量构建相关段落，就必须给出 model_index 或 variable_analysis 表候选；两者可来自同一段，也可分开。
3. 图表插入的目的必须是增强文章对应观点的解释力，不能为了凑表而放在无关位置。
4. 每个候选必须围绕具体段落生成 dataNeed、query、tableTitle、tableRole、tableKind；不要使用论文总题目或章节题作为标题。
5. tableKind 规则：impact_factors 用 impact_factors；model_index/evidence_data 用 numeric；variable_analysis 可用 test_result、correlation、descriptive 或 regression。

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
      "tableRole": "impact_factors|model_index|variable_analysis|evidence_data",
      "tableKind": "impact_factors|numeric|test_result|correlation|descriptive|regression",
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
    def _normalize_table_role(value='', context=''):
        text = re.sub(r'[\s_\-]+', '', str(value or '').strip().lower())
        aliases = {
            'impact': 'impact_factors',
            'impactfactor': 'impact_factors',
            'impactfactors': 'impact_factors',
            'factorlist': 'impact_factors',
            'influence': 'impact_factors',
            '影响因素': 'impact_factors',
            '因素表': 'impact_factors',
            'model': 'model_index',
            'modelindex': 'model_index',
            'equation': 'model_index',
            'indexresult': 'model_index',
            '模型测算': 'model_index',
            '方程代入': 'model_index',
            '综合指标': 'model_index',
            'variable': 'variable_analysis',
            'variableanalysis': 'variable_analysis',
            'variance': 'variable_analysis',
            'factoranalysis': 'variable_analysis',
            '变量分析': 'variable_analysis',
            '总方差解释': 'variable_analysis',
            'data': 'evidence_data',
            'evidencedata': 'evidence_data',
            'rawdata': 'evidence_data',
            '数据表': 'evidence_data',
            '原始数据': 'evidence_data',
        }
        if text in aliases:
            return aliases[text]
        compact = re.sub(r'\s+', '', str(context or ''))
        if re.search(r'影响因素|作用因素|驱动因素|制约因素|因素如[表图]|如表.*因素', compact):
            return 'impact_factors'
        if re.search(r'模型|方程|代入|测算|综合指标|效果指数|指数得分|Y[_A-Za-z\u4e00-\u9fff]*|X\d+', compact):
            return 'model_index'
        if re.search(r'变量分析|总方差|方差解释|特征根|贡献率|因子载荷|KMO|Bartlett|相关系数|描述性统计', compact, flags=re.IGNORECASE):
            return 'variable_analysis'
        return 'evidence_data'

    @classmethod
    def _table_kind_for_role(cls, role='', explicit=''):
        normalized = cls._normalize_table_kind(explicit)
        if normalized and not (role == 'impact_factors' and normalized in {'numeric', 'source'}):
            return normalized
        if role == 'impact_factors':
            return 'impact_factors'
        if role == 'variable_analysis':
            return normalized if normalized in {'correlation', 'regression', 'descriptive', 'test_result'} else 'test_result'
        return 'numeric'

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
            if row.get('value') is None:
                continue
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
            value = re.sub(r'^(?:该段|本文|本段|建议|适合|应当|可以|可|需|需要|提出要|提出|要|补充|展示|分析|比较|对比|绘制|生成|构建|呈现|说明|反映)+', '', value)
            value = re.sub(r'^(?:一张|一个|一份|有关|关于|用于|用来|体现|刻画|呈现)+', '', value)
            value = re.sub(r'(若不以|，|。|；|;).*$', '', value)
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
    def _normalize_table_title(cls, title, rows=None, target=None):
        target = target or {}
        value = re.sub(r'\s+', '', str(title or '').strip())
        if re.search(r'(图表|数据图|统计图|折线图|柱状图|条形图|饼图|结构图|图)$', value):
            value = re.sub(r'(图表|数据图|统计图|折线图|柱状图|条形图|饼图|结构图|图)$', '表', value)
        role = cls._normalize_table_role(target.get('tableRole') or target.get('role'), cls._table_context_text(value, target))
        if role == 'impact_factors':
            return cls._normalize_impact_factor_title(value, target)
        bad_title = bool(re.fullmatch(r'(?:论文)?(?:数据|统计|图表)?表|图表标题|表格标题|相关指标表?', value))
        instruction_like = bool(re.search(r'若不以|如果没有|建议|适合|应当|可以|需要|提出要|补充|展示|比较|对比|绘制|生成|构建|呈现|说明|反映', value))
        if value and not bad_title and not instruction_like and len(value) <= 34:
            return value
        if len(value) > 28 or instruction_like:
            value = re.sub(r'^(?:该段|本文|本段|建议|适合|应当|可以|可|需|需要|提出要|提出|要|补充|展示|分析|比较|对比|绘制|生成|构建|呈现|说明|反映)+', '', value)
            value = re.sub(r'^(?:一张|一个|一份|有关|关于|用于|用来|体现|刻画|呈现)+', '', value)
            value = re.sub(r'(若不以|，|。|；|;).*$', '', value)
        if not value or bad_title:
            hint = cls._target_title_hint(target)
            data_need = re.sub(r'\s+', '', str(target.get('dataNeed', '') or ''))
            indicator_match = re.search(r'(.{0,20}?指标体系)', data_need)
            variable_match = re.search(r'(.{0,18}?(?:变量|口径|定义|数据来源))', data_need)
            joined = f'{hint}{data_need}'
            table_kind = cls._preferred_table_kind(rows or [], value or hint, target)
            if table_kind == 'numeric':
                value = f'{hint or "论文数据"}表'
            elif indicator_match or any(term in joined for term in ('指标体系', '一级维度', '二级指标', '权重', '标准化方向', '指标属性')):
                value = f'{(indicator_match.group(1) if indicator_match else hint) or "评价指标体系"}表'
            elif variable_match or any(term in joined for term in ('变量', '口径', '定义', '数据来源')):
                value = f'{(variable_match.group(1) if variable_match else hint) or "变量口径"}表'
            elif hint:
                value = f'{hint}表'
            else:
                value = '论文数据表'
        if not re.search(r'(表|结果|统计|矩阵|分析|说明|测算|评价|影响因素|变量)$', value):
            value = f'{value}表'
        return value[:34] or '论文数据表'

    @classmethod
    def _normalize_impact_factor_title(cls, value, target=None):
        target = target or {}
        text = re.sub(r'\s+', '', str(value or '').strip())
        text = re.sub(r'^\s*表\s*\d+(?:\.\d+)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\[[\d,\-\s]+\]\s*$', '', text)
        text = re.sub(r'[（(]\s*(?:19|20)\d{2}[^）)]*[）)]?$', '', text)
        text = re.sub(r'[（(][^）)]*$', '', text)
        text = re.sub(r'(?:图表|数据图|统计图|折线图|柱状图|条形图|饼图|结构图|图|表)$', '', text)
        text = re.sub(r'(?:的)?(?:制约条件|约束条件|作用方向|风险表现|风险因素|作用机制)(?:[、和与及].*)?$', '的影响因素', text)
        generic = not text or re.fullmatch(r'(?:影响因素|因素|因素表|影响因素表|论文数据表|数据表|相关指标表)', text)
        if generic:
            for field in ('tableTitle', 'dataNeed', 'intent', 'suggestion', 'reason', 'query', 'sectionTitle'):
                candidate = re.sub(r'\s+', '', str(target.get(field, '') or '').strip())
                candidate = re.sub(r'^(?:该段|本文|本段|建议|适合|应当|可以|可|需|需要|提出要|提出|要|补充|展示|分析|比较|对比|绘制|生成|构建|呈现|说明|反映)+', '', candidate)
                candidate = re.sub(r'[，。；;：:].*$', '', candidate)
                candidate = re.sub(r'[（(]\s*(?:19|20)\d{2}[^）)]*[）)]?$', '', candidate)
                candidate = re.sub(r'[（(][^）)]*$', '', candidate)
                candidate = re.sub(r'(?:图表|数据图|统计图|折线图|柱状图|条形图|饼图|结构图|图|表)$', '', candidate)
                candidate = re.sub(r'(?:的)?(?:制约条件|约束条件|作用方向|风险表现|风险因素|作用机制)(?:[、和与及].*)?$', '的影响因素', candidate)
                if candidate and not re.fullmatch(r'(?:影响因素|因素|因素表|影响因素表)', candidate):
                    text = candidate
                    break
        if not text:
            text = '影响因素'
        if '影响因素' not in text:
            text = re.sub(r'(?:的)?(?:制约条件|约束条件|作用方向|风险表现|风险因素|作用机制)(?:[、和与及].*)?$', '', text).rstrip('的')
            text = f'{text}的影响因素' if text else '影响因素'
        return text[:34]

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
        artifact_type = self._normalize_artifact_type((target or {}).get('artifactType') or (target or {}).get('insertType'))
        if artifact_type == 'table':
            title_seed = submitted_title or (target or {}).get('tableTitle') or (target or {}).get('title') or (target or {}).get('dataNeed') or ''
            return self._normalize_table_title(title_seed, rows, target), self._choose_chart_type(chart_type, rows, target)
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

    @classmethod
    def _coerce_ai_source_rows(cls, rows):
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            value = cls._parse_number(row.get('value'))
            if value is not None:
                continue
            label = str(row.get('label', '') or row.get('indicator', '') or '').strip()
            source_name = str(row.get('sourceName', '') or '').strip()
            publisher = str(row.get('publisher', '') or '').strip()
            url = str(row.get('url', '') or '').strip()
            note = str(row.get('note', '') or row.get('manualHint', '') or '').strip()
            if not any((label, source_name, publisher, url, note)):
                continue
            result.append({
                'label': (label or f'待核验指标{len(result) + 1}')[:40],
                'value': None,
                'rawValue': '',
                'source': cls._format_source({
                    'sourceName': source_name,
                    'publisher': publisher,
                    'url': url,
                    'note': note,
                }),
                'sourceName': source_name,
                'publisher': publisher,
                'url': url,
                'note': note or '请核验该指标的年份、地区、口径和原始数值后填写。',
            })
        return result

    @classmethod
    def _coerce_ai_table_rows(cls, rows):
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            raw_value = str(row.get('value', '') or '').strip()
            value = cls._parse_number(raw_value)
            symbol = str(
                row.get('symbol', '')
                or row.get('variableSymbol', '')
                or row.get('variableCode', '')
                or row.get('code', '')
                or ''
            ).strip()
            variable_name = str(row.get('variableName', '') or row.get('indicatorName', '') or '').strip()
            variable = str(row.get('variable', '') or '').strip()
            year = str(row.get('year', '') or row.get('年份', '') or '').strip()
            stat_type = str(row.get('statType', '') or row.get('statistic', '') or row.get('统计项', '') or '').strip()
            label = str(
                row.get('label', '')
                or row.get('indicator', '')
                or variable_name
                or variable
                or row.get('name', '')
                or ''
            ).strip()
            if not symbol and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,18}', variable):
                symbol = variable
            if symbol and label and label.lower() == symbol.lower() and variable_name:
                label = variable_name
            measure = str(
                row.get('measure', '')
                or row.get('measurement', '')
                or row.get('method', '')
                or row.get('definition', '')
                or row.get('calculation', '')
                or row.get('description', '')
                or ''
            ).strip()
            source_name = str(row.get('sourceName', '') or row.get('dataSource', '') or row.get('sourceTitle', '') or '').strip()
            publisher = str(row.get('publisher', '') or row.get('organization', '') or row.get('agency', '') or '').strip()
            url = str(row.get('url', '') or row.get('link', '') or '').strip()
            note = str(row.get('note', '') or row.get('manualHint', '') or row.get('scope', '') or row.get('caliber', '') or '').strip()
            source = str(row.get('source', '') or '').strip()
            related_label = str(
                row.get('relatedLabel', '')
                or row.get('relatedVariable', '')
                or row.get('variable2', '')
                or row.get('withVariable', '')
                or row.get('column', '')
                or ''
            ).strip()
            if not related_label:
                related_label = str(row.get('relatedVariable', '') or '').strip()
            sample_size = str(row.get('sampleSize', '') or row.get('n', '') or row.get('observations', '') or '').strip()
            mean = str(row.get('mean', '') or row.get('average', '') or '').strip()
            std_dev = str(row.get('stdDev', '') or row.get('standardDeviation', '') or row.get('std', '') or '').strip()
            min_value = str(row.get('min', '') or row.get('minimum', '') or '').strip()
            max_value = str(row.get('max', '') or row.get('maximum', '') or '').strip()
            coefficient = str(row.get('coefficient', '') or row.get('coef', '') or row.get('beta', '') or '').strip()
            std_error = str(row.get('stdError', '') or row.get('standardError', '') or row.get('se', '') or '').strip()
            t_statistic = str(row.get('tStatistic', '') or row.get('tStat', '') or row.get('t', '') or '').strip()
            p_value = str(row.get('pValue', '') or row.get('p', '') or '').strip()
            significance = str(row.get('significance', '') or row.get('stars', '') or '').strip()
            correlation = str(row.get('correlation', '') or row.get('corr', '') or row.get('correlationCoefficient', '') or '').strip()
            eigenvalue = str(row.get('eigenvalue', '') or row.get('eigenValue', '') or row.get('eigen', '') or '').strip()
            contribution_rate = str(row.get('contributionRate', '') or row.get('varianceContribution', '') or row.get('varianceRate', '') or '').strip()
            cumulative_rate = str(row.get('cumulativeRate', '') or row.get('cumulativeContribution', '') or row.get('cumContribution', '') or '').strip()
            if not any((
                label, symbol, measure, raw_value, source_name, publisher, url, note, source, year, variable, stat_type,
                related_label, sample_size, mean, std_dev, min_value, max_value, coefficient,
                std_error, t_statistic, p_value, significance, correlation, eigenvalue, contribution_rate, cumulative_rate,
            )):
                continue
            result.append({
                'label': (label or f'待核验指标{len(result) + 1}')[:40],
                'year': year[:12],
                'variable': variable_name or variable or label,
                'statType': stat_type,
                'symbol': symbol[:24],
                'measure': measure,
                'value': value,
                'rawValue': raw_value if value is not None else '',
                'source': source or cls._format_source({
                    'sourceName': source_name,
                    'publisher': publisher,
                    'url': url,
                    'note': note,
                }),
                'sourceName': source_name,
                'publisher': publisher,
                'url': url,
                'note': note,
                'relatedLabel': related_label[:40],
                'relatedVariable': related_label[:40],
                'sampleSize': sample_size,
                'mean': mean,
                'stdDev': std_dev,
                'min': min_value,
                'max': max_value,
                'coefficient': coefficient,
                'stdError': std_error,
                'tStatistic': t_statistic,
                'pValue': p_value,
                'significance': significance,
                'correlation': correlation,
                'eigenvalue': eigenvalue,
                'contributionRate': contribution_rate,
                'cumulativeRate': cumulative_rate,
            })
        return cls._flatten_table_rows_to_single_value(result)

    @classmethod
    def _flatten_table_rows_to_single_value(cls, rows):
        flattened = []
        stat_fields = [
            ('sampleSize', '样本量'),
            ('mean', '均值'),
            ('stdDev', '标准差'),
            ('min', '最小值'),
            ('max', '最大值'),
            ('coefficient', '系数'),
            ('stdError', '标准误'),
            ('tStatistic', 't统计量'),
            ('pValue', 'P值'),
            ('correlation', '相关系数'),
            ('eigenvalue', '特征根'),
            ('contributionRate', '贡献率'),
            ('cumulativeRate', '累计贡献率'),
        ]
        for row in rows or []:
            item = dict(row)
            has_value = item.get('value') is not None or str(item.get('rawValue', '') or '').strip()
            if has_value or not any(str(item.get(field, '') or '').strip() for field, _ in stat_fields):
                for field, _ in stat_fields:
                    item[field] = ''
                if not str(item.get('relatedVariable', '') or '').strip():
                    item['relatedVariable'] = item.get('relatedLabel', '')
                flattened.append(item)
                continue
            for field, label_suffix in stat_fields:
                value = str(item.get(field, '') or '').strip()
                if not value:
                    continue
                canonical_stat = {
                    'sampleSize': 'sampleSize',
                    'mean': 'mean',
                    'stdDev': 'stdDev',
                    'min': 'min',
                    'max': 'max',
                    'coefficient': 'coefficient',
                    'stdError': 'stdError',
                    'tStatistic': 'tStatistic',
                    'pValue': 'pValue',
                    'correlation': 'correlation',
                    'eigenvalue': 'eigenvalue',
                    'contributionRate': 'contributionRate',
                    'cumulativeRate': 'cumulativeRate',
                }.get(field, field)
                label_parts = [str(item.get('label', '') or '').strip()]
                if field == 'correlation' and str(item.get('relatedLabel', '') or '').strip():
                    label_parts.append(str(item.get('relatedLabel', '') or '').strip())
                label_parts.append(label_suffix)
                note = str(item.get('note', '') or '').strip()
                flattened.append({
                    **item,
                    'label': ''.join(part for part in label_parts if part)[:40],
                    'statType': canonical_stat,
                    'relatedVariable': item.get('relatedVariable') or item.get('relatedLabel', ''),
                    'value': cls._parse_number(value),
                    'rawValue': value,
                    'note': f'{note}；统计项：{label_suffix}'.strip('；'),
                    **{stat_field: '' for stat_field, _ in stat_fields},
                })
        return flattened

    @staticmethod
    def _target_indicator_terms(target, fallback_text=''):
        text = ' '.join(str(part or '') for part in (
            (target or {}).get('dataNeed') if isinstance(target, dict) else '',
            (target or {}).get('reason') if isinstance(target, dict) else '',
            (target or {}).get('query') if isinstance(target, dict) else '',
            fallback_text,
        ))
        text = re.sub(r'(?:19|20)\d{2}\s*[—\-~至到]\s*(?:19|20)\d{2}年?', ' ', text)
        text = re.sub(r'(?:19|20)\d{2}年?', ' ', text)
        text = re.sub(r'(各省|省级|连续口径|面板数据|原始数值|基础指标|人工核验|上述|真正的|折线图|柱状图|若用户需要|应补充)', ' ', text)
        stop_terms = {
            '需要', '补充', '提供', '生成', '真正', '折线图', '柱状图', '上述', '基础指标',
            '原始数值', '清洗', '省级', '面板数据', '人工核验', '连续口径', '数据来源',
            '指标体系', '计算口径', '年份', '地区', '各省', '以及', '尤其', '若用户',
        }
        terms = []
        chunks = re.split(r'[、,，；;。.\s]+|以及|和|与|或', text)
        for chunk in chunks:
            term = chunk.strip(' 的等指标数据资料来源：:；;，,。.')
            term = re.sub(r'^(?:需|需要|补充|核验|提供|检索|生成|展示|分析)+', '', term)
            term = re.sub(r'(?:省份|年份|地区|口径|原始|数值|数据|资料|来源)+$', '', term)
            term = term.strip(' 的等指标数据资料来源：:；;，,。.')
            if not term:
                continue
            if not term or term in stop_terms:
                continue
            if any(skip in term for skip in ('2011', '2022', '用户需要', '真正的', '应补充')):
                continue
            if len(term) > 18:
                short_match = re.search(
                    r'([\u4e00-\u9fffA-Za-z0-9]{2,18}(?:普及率|处理水平|受教育年限|公共服务供给|覆盖率|增长率|占比|规模|水平|年限|供给|指数|密度))',
                    term,
                )
                if short_match:
                    term = short_match.group(1)
            if any(key in term for key in ('率', '水平', '年限', '供给', '服务', '厕所', '垃圾', '教育', '卫生', '收入', '指数', '规模', '占比', '密度')):
                if term not in terms:
                    terms.append(term[:24])
            if len(terms) >= 8:
                break
        return terms

    @staticmethod
    def _extract_spreadsheet_xml_text(file_bytes, limit=40000):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
                shared = []
                for name in archive.namelist():
                    if name.startswith('xl/sharedStrings') and name.endswith('.xml'):
                        xml = archive.read(name).decode('utf-8', errors='ignore')
                        shared = [
                            html.unescape(re.sub(r'<[^>]+>', '', item)).strip()
                            for item in re.findall(r'<si[^>]*>(.*?)</si>', xml, flags=re.S)
                        ]
                        break
                lines = []
                for name in archive.namelist():
                    if not name.startswith('xl/worksheets/') or not name.endswith('.xml'):
                        continue
                    xml = archive.read(name).decode('utf-8', errors='ignore')
                    for row_xml in re.findall(r'<row[^>]*>(.*?)</row>', xml, flags=re.S):
                        cells = []
                        for cell_xml in re.findall(r'<c\b[^>]*>.*?</c>', row_xml, flags=re.S):
                            cell_type = re.search(r'\bt="([^"]+)"', cell_xml)
                            value_match = re.search(r'<v[^>]*>(.*?)</v>', cell_xml, flags=re.S)
                            inline_match = re.search(r'<is[^>]*>(.*?)</is>', cell_xml, flags=re.S)
                            value = ''
                            if value_match:
                                raw = html.unescape(value_match.group(1)).strip()
                                if cell_type and cell_type.group(1) == 's':
                                    try:
                                        value = shared[int(raw)]
                                    except Exception:
                                        value = raw
                                else:
                                    value = raw
                            elif inline_match:
                                value = html.unescape(re.sub(r'<[^>]+>', '', inline_match.group(1))).strip()
                            cells.append(value)
                        if any(cells):
                            lines.append(','.join(cells))
                        if sum(len(line) for line in lines) > limit:
                            return '\n'.join(lines)[:limit]
                return '\n'.join(lines)[:limit]
        except Exception:
            return ''

    @classmethod
    def _decode_data_file_text(cls, data_file):
        if not isinstance(data_file, dict):
            return ''
        data_url = str(data_file.get('dataUrl') or data_file.get('content') or '').strip()
        name = str(data_file.get('name') or '').strip()
        if not data_url:
            return ''
        try:
            if ',' in data_url and data_url.startswith('data:'):
                raw = base64.b64decode(data_url.split(',', 1)[1])
            else:
                raw = base64.b64decode(data_url)
        except Exception:
            return ''
        lower_name = name.lower()
        if lower_name.endswith(('.xlsx', '.xlsm')):
            text = cls._extract_spreadsheet_xml_text(raw)
        else:
            text = ''
            for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk'):
                try:
                    text = raw.decode(encoding)
                    break
                except Exception:
                    continue
        text = cls._normalize_text(text)
        if not text:
            return ''
        header = f'用户上传数据文件：{name or "未命名文件"}'
        return f'{header}\n{text[:40000]}'

    @classmethod
    def _clean_evidence_content(cls, text, limit=6000):
        lines = []
        blank = False
        for line in cls._normalize_text(text).split('\n'):
            value = re.sub(r'[ \t]+', ' ', line).strip()
            if not value:
                if not blank and lines:
                    lines.append('')
                blank = True
                continue
            lines.append(value)
            blank = False
            if sum(len(item) + 1 for item in lines) >= limit:
                break
        return '\n'.join(lines).strip()[:limit]

    @staticmethod
    def _split_loose_table_line(line):
        text = str(line or '').strip()
        if not text:
            return []
        if re.match(r'^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$', text):
            return []
        if '|' in text:
            return [cell.strip() for cell in text.strip('|').split('|')]
        if '\t' in text:
            return [cell.strip() for cell in text.split('\t')]
        if ',' in text or '，' in text:
            normalized = text.replace('，', ',')
            try:
                return [cell.strip() for cell in next(csv.reader([normalized]))]
            except Exception:
                return [cell.strip() for cell in normalized.split(',')]
        return []

    @staticmethod
    def _cell_looks_like_year(value):
        return bool(re.fullmatch(r'(?:19|20)\d{2}\s*年?', str(value or '').strip()))

    @staticmethod
    def _normalize_year_label(value):
        match = re.search(r'((?:19|20)\d{2})', str(value or ''))
        return f'{match.group(1)}年' if match else str(value or '').strip()

    @classmethod
    def _append_extracted_numeric_row(cls, rows, *, label, raw_value, source_info, note=''):
        value = cls._parse_numeric_cell(raw_value)
        label = re.sub(r'\s+', '', str(label or '').strip('：:，,。.;；| '))
        if value is None or not label:
            return
        if cls._label_looks_like_variable_code(label):
            return
        if cls._looks_like_bad_numeric_value(raw_value, label):
            return
        source_info = source_info or {}
        row_note = str(note or source_info.get('note', '') or '').strip()
        rows.append({
            'label': label[:40],
            'value': value,
            'rawValue': str(raw_value or '').strip(),
            'source': cls._format_source({
                'sourceName': source_info.get('sourceName', ''),
                'publisher': source_info.get('publisher', ''),
                'url': source_info.get('url', ''),
                'note': row_note,
            }),
            'sourceName': str(source_info.get('sourceName', '') or '').strip(),
            'publisher': str(source_info.get('publisher', '') or '').strip(),
            'url': str(source_info.get('url', '') or '').strip(),
            'note': row_note,
        })

    @staticmethod
    def _parse_numeric_cell(value):
        text = str(value or '').strip()
        if not text:
            return None
        text = text.replace('，', ',').replace('％', '%')
        text = re.sub(r'^\s*[约近逾超超过不足少于大于小于]\s*', '', text)
        cleaned = text.replace(',', '')
        if len(cleaned) > 32:
            return None
        if re.search(r'https?://|!\[|\]\(|[A-Za-z]{2,}|[\u4e00-\u9fff]{4,}', cleaned):
            allowed_units = ('百分点', '亿元', '万元', '万人', '万人次', '平方公里', '公里', '千克', '公斤')
            if not any(cleaned.endswith(unit) for unit in allowed_units):
                return None
        match = re.fullmatch(
            r'[-+]?\d+(?:\.\d+)?\s*(?:%|‰|个百分点|亿元|万元|万人|万人次|人|元|年|个|项|次|吨|千克|公斤|公里|平方公里)?',
            cleaned,
        )
        if not match:
            return None
        number_match = re.search(r'[-+]?\d+(?:\.\d+)?', cleaned)
        if not number_match:
            return None
        try:
            return float(number_match.group(0))
        except Exception:
            return None

    @staticmethod
    def _looks_like_bad_numeric_value(raw_value, label=''):
        text = str(raw_value or '').strip()
        label_text = str(label or '')
        value = DataChartAssistant._parse_numeric_cell(text)
        if value is None:
            return True
        if re.fullmatch(r'(?:19|20)\d{2}', text.replace('年', '')):
            return True
        if len(text) > 32 or re.search(r'https?://|!\[|\]\(|[A-Za-z]{2,}', text):
            if not re.fullmatch(r'[-+]?\d+(?:\.\d+)?\s*(?:%|‰)?', text.replace(',', '').strip()):
                return True
        if re.search(r'页|第.{0,4}次|表\s*\d|图\s*\d|编号|序号|排名|代码', label_text):
            return True
        return False

    @classmethod
    def _source_info_from_evidence(cls, item):
        item = item or {}
        url = str(item.get('url', '') or '').strip()
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix('www.') if url else ''
        title = re.sub(r'\s+', ' ', str(item.get('title', '') or '').strip())
        return {
            'sourceName': title[:80] or host or '候选网页来源',
            'publisher': host,
            'url': url,
            'note': '网页正文自动抽取，需按原页面核验统计口径。',
        }

    @classmethod
    def _source_info_from_uploaded_text(cls, uploaded_data_note):
        first_line = cls._normalize_text(uploaded_data_note).split('\n', 1)[0] if uploaded_data_note else ''
        name = re.sub(r'^用户上传数据文件[:：]?', '', first_line).strip() or '用户上传数据文件'
        return {
            'sourceName': name[:80],
            'publisher': '用户上传',
            'url': '',
            'note': '用户上传文件自动抽取，需核验文件来源、单位和统计口径。',
        }

    @classmethod
    def _parse_narrow_numeric_table_rows(cls, table_rows, source_info, *, require_numeric=True):
        if not table_rows:
            return []
        header_index = None
        header_map = {}
        for index, row in enumerate(table_rows[:4]):
            candidate_map = cls._data_table_header_map(row)
            if len(candidate_map) >= 2:
                header_index = index
                header_map = candidate_map
                break
        if header_index is None:
            return []
        result = []
        for row_index, row in enumerate(table_rows[header_index + 1:], start=1):
            if len(row) < 2 or len(cls._data_table_header_map(row)) >= 2:
                continue
            label = cls._table_cell(row, header_map, 'label', 0) or f'项目{row_index}'
            raw_value = cls._table_cell(row, header_map, 'value', 1)
            if require_numeric and cls._parse_numeric_cell(raw_value) is None:
                continue
            source_name = cls._table_cell(row, header_map, 'sourceName', None) or source_info.get('sourceName', '')
            publisher = cls._table_cell(row, header_map, 'publisher', None) or source_info.get('publisher', '')
            url = cls._table_cell(row, header_map, 'url', None) or source_info.get('url', '')
            note = cls._table_cell(row, header_map, 'note', None) or source_info.get('note', '')
            cls._append_extracted_numeric_row(
                result,
                label=label,
                raw_value=raw_value,
                source_info={**source_info, 'sourceName': source_name, 'publisher': publisher, 'url': url},
                note=note,
            )
        return result

    @classmethod
    def _parse_wide_numeric_table_rows(cls, table_rows, source_info):
        if len(table_rows) < 2:
            return []
        result = []
        for header_index, header in enumerate(table_rows[:4]):
            if len(header) < 2:
                continue
            data_rows = [row for row in table_rows[header_index + 1:] if len(row) >= 2]
            if not data_rows:
                continue
            header_years = [cls._cell_looks_like_year(cell) for cell in header]
            first_header = str(header[0] or '').strip()
            year_columns = [index for index, is_year in enumerate(header_years) if index > 0 and is_year]
            first_column_years = sum(1 for row in data_rows[:8] if cls._cell_looks_like_year(row[0] if row else ''))

            if year_columns:
                for row in data_rows:
                    row_label = str(row[0] if row else '').strip()
                    if not row_label or len(cls._data_table_header_map(row)) >= 2:
                        continue
                    for col_index in year_columns:
                        if col_index >= len(row):
                            continue
                        year = cls._normalize_year_label(header[col_index])
                        cls._append_extracted_numeric_row(
                            result,
                            label=f'{year}{row_label}',
                            raw_value=row[col_index],
                            source_info=source_info,
                        )
            elif first_column_years >= 2:
                for row in data_rows:
                    year = cls._normalize_year_label(row[0])
                    if not year:
                        continue
                    for col_index, series in enumerate(header[1:], start=1):
                        if col_index >= len(row):
                            continue
                        series_name = str(series or '').strip()
                        if not series_name or cls._data_table_header_map([series_name]):
                            continue
                        cls._append_extracted_numeric_row(
                            result,
                            label=f'{year}{series_name}',
                            raw_value=row[col_index],
                            source_info=source_info,
                        )
            elif first_header and any(cls._parse_numeric_cell(cell) is not None for row in data_rows[:8] for cell in row[1:]):
                for row in data_rows:
                    row_label = str(row[0] if row else '').strip()
                    if not row_label:
                        continue
                    for col_index, header_cell in enumerate(header[1:], start=1):
                        if col_index >= len(row):
                            continue
                        column_label = str(header_cell or '').strip()
                        if not column_label:
                            continue
                        cls._append_extracted_numeric_row(
                            result,
                            label=f'{row_label}{column_label}',
                            raw_value=row[col_index],
                            source_info=source_info,
                        )
            if len(result) >= 2:
                return result
        return result

    @classmethod
    def _parse_loose_numeric_rows(cls, table_rows, source_info):
        result = []
        for row in table_rows:
            if len(row) < 2 or len(cls._data_table_header_map(row)) >= 2:
                continue
            label = row[0]
            raw_value = row[1]
            if cls._parse_numeric_cell(raw_value) is None:
                continue
            note = '；'.join(str(cell or '').strip() for cell in row[2:5] if str(cell or '').strip())
            cls._append_extracted_numeric_row(result, label=label, raw_value=raw_value, source_info=source_info, note=note or source_info.get('note', ''))
        return result

    @classmethod
    def _table_blocks_from_text(cls, text):
        blocks = []
        current = []
        for line in cls._normalize_text(text).split('\n'):
            cells = cls._split_loose_table_line(line)
            if cells:
                current.append(cells)
                continue
            if current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        return blocks

    @classmethod
    def _extract_numeric_rows_from_tables(cls, text, source_info):
        rows = []
        for block in cls._table_blocks_from_text(text):
            narrow = cls._parse_narrow_numeric_table_rows(block, source_info)
            if narrow:
                rows.extend(narrow)
            else:
                wide = cls._parse_wide_numeric_table_rows(block, source_info)
                if wide:
                    rows.extend(wide)
                else:
                    rows.extend(cls._parse_loose_numeric_rows(block, source_info))
            if len(rows) >= 40:
                break
        return cls._dedupe_numeric_rows(rows)[:40]

    @classmethod
    def _sentence_candidate_text(cls, text):
        kept = []
        for line in cls._normalize_text(text).split('\n'):
            stripped = line.strip()
            if not stripped:
                kept.append('')
                continue
            cells = cls._split_loose_table_line(stripped)
            numeric_cells = sum(1 for cell in cells if cls._parse_numeric_cell(cell) is not None)
            if len(cells) >= 2 and numeric_cells >= 1:
                continue
            kept.append(stripped)
        return '\n'.join(kept)

    @classmethod
    def _extract_numeric_rows_from_sentences(cls, text, source_info, target=None, query=''):
        normalized = re.sub(r'\s+', ' ', cls._sentence_candidate_text(text))
        rows = []
        range_pattern = re.compile(
            r'(?:由|从)\s*((?:19|20)\d{2})\s*年?的?\s*([-+]?\d+(?:\.\d+)?)\s*[％%]?\s*'
            r'(?:升至|增至|提高到|下降至|降至|减少到|达到|至|到)\s*'
            r'((?:19|20)\d{2})\s*年?的?\s*([-+]?\d+(?:\.\d+)?)\s*[％%]?'
        )
        for segment in re.split(r'[。；;]', normalized):
            match = range_pattern.search(segment)
            if not match:
                continue
            series = segment[:match.start()]
            series = re.sub(r'^[，,。；;：:\s]+|[，,。；;：:\s]+$', '', series)
            series = re.sub(r'^(?:其中|分别|数据显示|报告显示|统计显示|数据表明)', '', series)
            series = re.sub(r'[，,：:].*$', '', series)
            if len(series) > 24:
                series = series[-24:]
            cls._append_extracted_numeric_row(rows, label=f'{match.group(1)}年{series}', raw_value=match.group(2), source_info=source_info)
            cls._append_extracted_numeric_row(rows, label=f'{match.group(3)}年{series}', raw_value=match.group(4), source_info=source_info)

        point_pattern = re.compile(
            r'((?:19|20)\d{2}\s*年?[\u4e00-\u9fffA-Za-z0-9（）()、]{0,28}?|[\u4e00-\u9fffA-Za-z0-9（）()、]{2,28}?(?:19|20)\d{2}\s*年?)\s*(?:为|达|达到|是|约为|分别为|[:：,，])\s*([-+]?\d+(?:\.\d+)?)\s*(?:[％%]|个百分点|万人|亿元|元|年|个|项)?'
        )
        for match in point_pattern.finditer(normalized):
            label = match.group(1)
            raw_value = match.group(2)
            cls._append_extracted_numeric_row(rows, label=label, raw_value=raw_value, source_info=source_info)
        return cls._dedupe_numeric_rows(rows)[:40]

    @classmethod
    def _dedupe_numeric_rows(cls, rows):
        result = []
        seen = set()
        for row in rows or []:
            if row.get('value') is None:
                continue
            key = (
                re.sub(r'\s+', '', str(row.get('label', '') or '')),
                str(row.get('rawValue', row.get('value', '')) or ''),
                str(row.get('url', '') or ''),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    @classmethod
    def _row_relevance_score(cls, row, target=None, query=''):
        label = str(row.get('label', '') or '')
        meta = ' '.join(str(row.get(field, '') or '') for field in ('source', 'sourceName', 'publisher', 'note'))
        haystack = re.sub(r'\s+', '', f'{label} {meta}'.lower())
        context = ' '.join(str(part or '') for part in (
            query,
            (target or {}).get('dataNeed') if isinstance(target, dict) else '',
            (target or {}).get('query') if isinstance(target, dict) else '',
            (target or {}).get('chartTitle') if isinstance(target, dict) else '',
            (target or {}).get('tableTitle') if isinstance(target, dict) else '',
            (target or {}).get('reason') if isinstance(target, dict) else '',
        ))
        terms = cls._target_indicator_terms(target or {}, context)
        terms.extend(
            term[:18]
            for term in re.findall(r'[\u4e00-\u9fff]{2,18}|[A-Za-z]{3,}', context)
            if term not in {'数据', '统计', '来源', '表格', '图表', '指标', '生成', '检索'}
        )
        score = 1 if re.search(r'(?:19|20)\d{2}', label) else 0
        for term in dict.fromkeys(terms):
            if term and re.sub(r'\s+', '', term.lower()) in haystack:
                score += 3
        if row.get('url'):
            score += 1
        if re.search(r'统计局|年鉴|公报|报告|CNNIC|数据库|\.gov|\.edu', meta, flags=re.IGNORECASE):
            score += 1
        return score

    @classmethod
    def _filter_relevant_numeric_rows(cls, rows, target=None, query='', limit=24):
        scored = [
            (cls._row_relevance_score(row, target, query), index, row)
            for index, row in enumerate(cls._dedupe_numeric_rows(rows))
        ]
        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        positive = [row for score, _, row in scored if score > 0]
        return (positive or [row for _, _, row in scored])[:limit]

    @classmethod
    def _extract_numeric_rows_from_text(cls, text, source_info, target=None, query='', limit=24):
        rows = []
        rows.extend(cls._extract_numeric_rows_from_tables(text, source_info))
        rows.extend(cls._extract_numeric_rows_from_sentences(text, source_info, target=target, query=query))
        return cls._filter_relevant_numeric_rows(rows, target=target, query=query, limit=limit)

    @classmethod
    def _extract_numeric_rows_from_uploaded_note(cls, uploaded_data_note, target=None, query='', limit=32):
        if not uploaded_data_note:
            return []
        source_info = cls._source_info_from_uploaded_text(uploaded_data_note)
        return cls._extract_numeric_rows_from_text(uploaded_data_note, source_info, target=target, query=query, limit=limit)

    @classmethod
    def _extract_numeric_rows_from_evidence(cls, evidence, target=None, query='', limit=32):
        rows = []
        for item in evidence or []:
            source_info = cls._source_info_from_evidence(item)
            text = '\n'.join(str(item.get(field, '') or '') for field in ('title', 'snippet', 'content'))
            rows.extend(cls._extract_numeric_rows_from_text(text, source_info, target=target, query=query, limit=limit))
            if len(rows) >= limit * 2:
                break
        return cls._filter_relevant_numeric_rows(rows, target=target, query=query, limit=limit)

    @classmethod
    def _fallback_source_rows(cls, target, evidence, *, payload=None, search_query=''):
        source_rows = cls._coerce_ai_source_rows((payload or {}).get('rows', []) if isinstance(payload, dict) else [])
        terms = cls._target_indicator_terms(target, f'{search_query} {(payload or {}).get("manualHint", "") if isinstance(payload, dict) else ""}')
        if not terms:
            terms = [cls._target_title_hint(target) or '待核验指标']
        candidates = evidence or []
        rows = []
        seen = set()
        for index, term in enumerate(terms):
            matched = None
            for item in candidates:
                haystack = f'{item.get("title", "")} {item.get("snippet", "")} {item.get("content", "")}'
                if cls._source_matches_indicator(term, item, target):
                    matched = item
                    break
            if not matched:
                matched = cls._default_source_for_indicator(term)
            from_ai = source_rows[index] if index < len(source_rows) else {}
            title = str((matched or {}).get('title', '') or from_ai.get('sourceName', '') or '候选数据来源').strip()
            url = str((matched or {}).get('url', '') or from_ai.get('url', '') or '').strip()
            publisher = from_ai.get('publisher') or (urllib.parse.urlparse(url).netloc.lower().removeprefix('www.') if url else '')
            note_bits = [
                str(from_ai.get('note', '') or '').strip(),
                '请打开来源核验该指标的省份、年份、统计口径和原始数值后填写。',
            ]
            key = f'{term}|{url}|{title}'
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'label': term[:40],
                'value': None,
                'rawValue': '',
                'source': cls._format_source({
                    'sourceName': title,
                    'publisher': publisher,
                    'url': url,
                    'note': ' '.join(bit for bit in note_bits if bit),
                }),
                'sourceName': title,
                'publisher': publisher,
                'url': url,
                'note': ' '.join(bit for bit in note_bits if bit),
            })
            if len(rows) >= 8:
                break
        return rows or source_rows

    @classmethod
    def _fallback_impact_factor_rows(cls, target, search_query=''):
        text = ' '.join(str(part or '') for part in (
            search_query,
            (target or {}).get('tableTitle') if isinstance(target, dict) else '',
            (target or {}).get('dataNeed') if isinstance(target, dict) else '',
            (target or {}).get('intent') if isinstance(target, dict) else '',
            (target or {}).get('reason') if isinstance(target, dict) else '',
        ))
        terms = []
        for term in cls._target_indicator_terms(target, text):
            item = re.sub(r'\s+', '', str(term or '').strip('，,。.;；：: '))
            item = re.sub(r'(?:影响因素|因素|表)$', '', item).strip('的')
            if item and item not in {'数据', '指标', '论文', '表格'} and item not in terms:
                terms.append(item)
            if len(terms) >= 8:
                break
        if not terms:
            terms = ['核心约束因素']
        return [{'label': term, 'value': None, 'rawValue': '', 'source': '', 'sourceName': '', 'publisher': '', 'url': '', 'note': ''} for term in terms]

    @staticmethod
    def _indicator_keywords(term):
        text = re.sub(r'\s+', '', str(term or '').lower())
        keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-z]{3,}', text))
        aliases = {
            '城镇化率': ('城镇化率', '常住人口城镇化率', '城镇人口比重', '统计年鉴', '统计局'),
            '教育水平': ('教育水平', '受教育年限', '教育经费', '教育统计', '统计年鉴'),
            '受教育年限': ('受教育年限', '平均受教育年限', '教育水平', '教育统计'),
            '互联网普及率': ('互联网普及率', '网民规模', 'CNNIC', '互联网络发展状况统计报告'),
            '农村卫生厕所普及率': ('农村卫生厕所', '卫生厕所普及率', '农村厕所革命', '统计年鉴'),
            '农村生活垃圾处理水平': ('农村生活垃圾', '生活垃圾处理', '城乡建设统计年鉴', '住建部'),
            '基层公共服务供给': ('公共服务', '基层公共服务', '财政公共服务', '统计年鉴'),
        }
        for key, values in aliases.items():
            if key in term:
                keywords.update(value.lower() for value in values)
        return {item for item in keywords if item}

    @classmethod
    def _source_matches_indicator(cls, term, item, target=None):
        haystack = re.sub(
            r'\s+',
            '',
            f'{item.get("title", "")} {item.get("snippet", "")} {item.get("content", "")} {item.get("url", "")}'.lower(),
        )
        if not haystack:
            return False
        blocked = ('xbox', 'gamepass', '游戏', 'steam', 'playstation', 'microsoft store')
        if any(word in haystack for word in blocked):
            return False
        keywords = cls._indicator_keywords(term)
        if any(keyword and keyword in haystack for keyword in keywords):
            return True
        context = f'{(target or {}).get("dataNeed", "")} {(target or {}).get("query", "")}'
        if '互联网' in context and any(word in haystack for word in ('cnnic', '互联网络', '互联网普及率', '网民规模')):
            return True
        if any(word in haystack for word in ('统计局', '统计年鉴', '统计公报', '国家数据', 'data.stats.gov.cn')):
            return True
        return False

    @staticmethod
    def _default_source_for_indicator(term):
        text = str(term or '')
        if '互联网' in text:
            return {
                'title': '中国互联网络发展状况统计报告',
                'url': 'https://www.cnnic.net.cn/n4/2022/0401/c88-1131.html',
                'snippet': 'CNNIC 发布的互联网普及率、网民规模等统计报告。',
                'content': '',
            }
        if any(word in text for word in ('教育', '受教育')):
            return {
                'title': '中国教育统计年鉴/教育事业发展统计公报',
                'url': 'http://www.moe.gov.cn/jyb_sjzl/sjzl_fztjgb/',
                'snippet': '教育部教育统计资料，可核验教育水平、教育经费、受教育相关指标。',
                'content': '',
            }
        if any(word in text for word in ('垃圾', '厕所', '公共服务', '城镇化', '收入', '农业', '农村')):
            return {
                'title': '中国统计年鉴/国家统计局国家数据',
                'url': 'https://www.stats.gov.cn/sj/ndsj/',
                'snippet': '国家统计局统计年鉴与国家数据，可核验省级年度指标口径。',
                'content': '',
            }
        return {
            'title': '国家统计局国家数据',
            'url': 'https://data.stats.gov.cn/',
            'snippet': '国家统计局国家数据，需按指标、地区和年份检索核验。',
            'content': '',
        }

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
        usable = [
            row for row in rows or []
            if row.get('value') is not None or any(str(row.get(field, '') or '').strip() for field in ('source', 'sourceName', 'publisher', 'url', 'note'))
        ]
        if len(usable) < 1:
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
                'year': str(row.get('year', '') or ''),
                'variable': str(row.get('variable', '') or ''),
                'statType': str(row.get('statType', '') or ''),
                'symbol': str(row.get('symbol', '') or ''),
                'measure': str(row.get('measure', '') or ''),
                'value': row.get('value'),
                'rawValue': str(row.get('rawValue', row.get('value', '')) or ''),
                'relatedLabel': str(row.get('relatedLabel', '') or ''),
                'relatedVariable': str(row.get('relatedVariable', '') or ''),
                'sampleSize': str(row.get('sampleSize', '') or ''),
                'mean': str(row.get('mean', '') or ''),
                'stdDev': str(row.get('stdDev', '') or ''),
                'min': str(row.get('min', '') or ''),
                'max': str(row.get('max', '') or ''),
                'coefficient': str(row.get('coefficient', '') or ''),
                'stdError': str(row.get('stdError', '') or ''),
                'tStatistic': str(row.get('tStatistic', '') or ''),
                'pValue': str(row.get('pValue', '') or ''),
                'significance': str(row.get('significance', '') or ''),
                'correlation': str(row.get('correlation', '') or ''),
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
        preferred = [item for item in ranked if cls._source_quality_score(item) > 0]
        return (preferred or ranked)[:limit]

    @classmethod
    def _read_public_page(cls, url):
        normalized = cls._normalize_url(url)
        if not normalized:
            return ''
        reader_url = f'https://r.jina.ai/{normalized}'
        try:
            text = cls._request_text(reader_url, timeout=cls.PAGE_TIMEOUT)
        except Exception:
            try:
                text = cls._request_text(normalized, timeout=cls.PAGE_TIMEOUT)
                text = cls._strip_tags(text)
            except Exception:
                return ''
        text = re.sub(r'\n{3,}', '\n\n', str(text or '')).strip()
        return text[:9000]

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
                'content': cls._clean_evidence_content(page_text, 6000) if page_text else '',
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
2. 图表必须增强文章对应观点的解释力：插图用于解释趋势、结构、占比、组间对比和变化过程；插表用于呈现影响因素、模型测算值、变量分析或基础数据。
3. 必须优先寻找以下表格候选：
   A. 影响因素表：tableRole=impact_factors，放在解释“某内容的影响因素/作用机制/驱动因素”的段落，表体允许中文因素名称；
   B. 模型测算表：tableRole=model_index，放在模型、方程、综合评价指数、效果指数构建或代入的段落，表体只允许年份、数字和变量符号；
   C. 变量分析表：tableRole=variable_analysis，放在变量选取、模型变量、因子/主成分/相关/描述性统计相关段落，表体只允许数字和变量符号；
   D. 基础数据表：tableRole=evidence_data，放在需要用数据强化论点的段落，表体只允许年份、数字和变量符号。
4. 如果论文中出现模型/方程/变量构建相关内容，必须至少给出 model_index 或 variable_analysis 候选；如果两者都能支撑论证，应都给出。
5. 每个候选给出：paragraphId、artifactType、reason、dataNeed、query、chartType、chartTitle、tableTitle、tableRole、tableKind、confidence。
5. query 必须围绕该段真正需要说明的指标、对象、年份和地区生成，不要沿用论文总题目。
6. chartTitle/tableTitle 必须概括“要呈现什么数据”，不要写论文总题目、章节题或“影响因素”这类泛化标题。
7. artifactType 只能是 figure 或 table；chartType 只能是 line、bar、pie，插表时也给出一个备用 chartType。
8. tableKind 规则：impact_factors 用 impact_factors；model_index/evidence_data 用 numeric；variable_analysis 可用 test_result、correlation、descriptive 或 regression。
9. 至少保留 1 个插表候选；如果全文确实没有适合插表的位置，在 summary 中说明原因。

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
      "tableRole": "impact_factors|model_index|variable_analysis|evidence_data",
      "tableKind": "impact_factors|numeric|test_result|correlation|descriptive|regression",
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
        existing_roles = {
            item.get('tableRole')
            for item in candidates
            if item.get('artifactType') == 'table' and item.get('tableRole')
        }
        needs_table_pass = (
            not any(item.get('artifactType') == 'table' for item in candidates)
            or not existing_roles.intersection({'model_index', 'variable_analysis'})
        )
        if candidates and needs_table_pass:
            table_targets, table_summary = self._find_table_targets_with_ai(
                api,
                topic=topic,
                outline=outline,
                prompt_payload=prompt_payload,
                paragraph_map=paragraph_map,
                limit=4,
            )
            seen_keys = {(item.get('paragraphId'), item.get('tableRole')) for item in candidates}
            for table_target in table_targets:
                key = (table_target.get('paragraphId'), table_target.get('tableRole'))
                if key in seen_keys:
                    continue
                if table_target.get('paragraphId') in {item.get('paragraphId') for item in candidates}:
                    table_target['id'] = f'{table_target.get("id")}-table-review'
                candidates.append(table_target)
                seen_keys.add(key)
            candidates = self._limit_targets_preserving_table(candidates, limit)
        if not candidates:
            table_targets, table_summary = self._find_table_targets_with_ai(
                api,
                topic=topic,
                outline=outline,
                prompt_payload=prompt_payload,
                paragraph_map=paragraph_map,
                limit=4,
            )
            candidates = self._limit_targets_preserving_table(table_targets, limit)
        candidates = self._renumber_target_candidates(candidates)
        summary = str((payload or {}).get('summary') or f'AI 已阅读全文并定位到 {len(candidates)} 个可补充数据图表的位置。')
        if table_summary and not any(item.get('artifactType') == 'table' for item in candidates):
            summary = f'{summary} 插表复核：{table_summary}'
        return {
            'targets': candidates,
            'summary': summary,
        }

    @classmethod
    def _extract_equation_from_text(cls, text):
        normalized = cls._normalize_text(text)
        if not normalized:
            return ''
        patterns = [
            r'([A-Za-zYy][A-Za-z0-9_\u4e00-\u9fffβαε+\-*/×÷().（）,\s]{0,120}=[A-Za-z0-9_\u4e00-\u9fffβαε+\-*/×÷().（）,\s]{2,160})',
            r'([A-Za-z]\s*=\s*β0[^。；;\n]{0,180})',
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            equation = re.sub(r'\s+', '', match.group(1)).strip('，,。；;')
            if '=' in equation and len(equation) >= 5:
                return equation[:180]
        return ''

    @staticmethod
    def _coerce_analysis_list(value):
        if isinstance(value, list):
            return [str(item or '').strip() for item in value if str(item or '').strip()]
        text = str(value or '').strip()
        if not text:
            return []
        parts = re.split(r'[\n；;]+', text)
        return [part.strip(' -•\t') for part in parts if part.strip(' -•\t')]

    @classmethod
    def _coerce_analysis_parameters(cls, value, equation='', definitions=None):
        parameters = []
        seen = set()

        def add(name, meaning='', param_value=''):
            name = str(name or '').strip()
            meaning = str(meaning or '').strip()
            param_value = str(param_value or '').strip()
            if not name and not meaning and not param_value:
                return
            key = name or meaning
            if key in seen:
                return
            seen.add(key)
            parameters.append({'name': name, 'meaning': meaning, 'value': param_value})

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    add(
                        item.get('name') or item.get('symbol') or item.get('label'),
                        item.get('meaning') or item.get('description') or item.get('label'),
                        item.get('value'),
                    )
                else:
                    add(item)
        equation_text = str(equation or '')
        for beta in re.findall(r'β\s*\d+', equation_text):
            compact = re.sub(r'\s+', '', beta)
            meaning = '常数项' if compact == 'β0' else f'{compact} 对应变量的待估计系数'
            add(compact, meaning, '')
        if not parameters and re.search(r'回归|方程|模型|β|beta', equation_text, flags=re.IGNORECASE):
            add('β0', '常数项', '')
            add('β1', '核心解释变量系数', '')
        if not re.search(r'β\s*\d+', equation_text):
            for definition in definitions or []:
                symbol = definition.get('symbol')
                meaning = definition.get('meaning')
                if symbol and meaning and symbol not in {'Y', 'y'} and len(parameters) < 8:
                    add(f'β{len(parameters)}', f'{symbol}（{meaning}）对应系数', '')
        return parameters[:12]

    @staticmethod
    def _analysis_parameter_key(parameter):
        text = str((parameter or {}).get('name') or (parameter or {}).get('symbol') or (parameter or {}).get('label') or '').strip()
        return re.sub(r'\s+', '', text.replace('beta', 'β').replace('Beta', 'β')).lower()

    @classmethod
    def _merge_analysis_parameters(cls, current=None, payload=None, rows=None):
        merged = cls._coerce_analysis_parameters(current or [])

        def add_or_update(parameter):
            item = cls._coerce_analysis_parameters([parameter])
            if not item:
                return
            item = item[0]
            key = cls._analysis_parameter_key(item)
            index = -1
            if key:
                index = next((i for i, row in enumerate(merged) if cls._analysis_parameter_key(row) == key), -1)
            if index < 0 and item.get('meaning'):
                meaning = str(item.get('meaning') or '')
                index = next((
                    i for i, row in enumerate(merged)
                    if row.get('meaning') and (meaning in row.get('meaning', '') or row.get('meaning', '') in meaning)
                ), -1)
            if index >= 0:
                existing = dict(merged[index])
                if item.get('name') and not existing.get('name'):
                    existing['name'] = item['name']
                if item.get('meaning') and len(item.get('meaning', '')) > len(existing.get('meaning', '')):
                    existing['meaning'] = item['meaning']
                if str(item.get('value') or '').strip():
                    existing['value'] = str(item.get('value') or '').strip()
                merged[index] = existing
            else:
                merged.append(item)

        payload = payload if isinstance(payload, dict) else {}
        for field in ('analysisParameters', 'parameters', 'parameterRows', 'coefficients', 'coefficientParameters'):
            for parameter in payload.get(field) or []:
                if isinstance(parameter, dict):
                    add_or_update(parameter)

        coefficient_rows = [
            row for row in rows or []
            if str(row.get('statType') or '').lower() == 'coefficient'
            or str(row.get('coefficient') or '').strip()
            or re.search(r'β\s*\d+', str(row.get('label') or row.get('symbol') or ''))
        ]
        beta_index = 1
        for row in coefficient_rows:
            label = str(row.get('label') or row.get('symbol') or row.get('variable') or '').strip()
            name_match = re.search(r'β\s*\d+', label)
            name = re.sub(r'\s+', '', name_match.group(0)) if name_match else ''
            if not name:
                symbol = str(row.get('symbol') or row.get('variable') or '').strip()
                for parameter in merged:
                    meaning = str(parameter.get('meaning') or '')
                    if symbol and symbol in meaning:
                        name = str(parameter.get('name') or '')
                        break
            if not name and re.search(r'常数|截距|intercept|constant', label, flags=re.IGNORECASE):
                name = 'β0'
            if not name:
                name = f'β{beta_index}'
                beta_index += 1
            value = row.get('coefficient') or row.get('rawValue') or row.get('value') or ''
            if value is None:
                value = ''
            add_or_update({
                'name': name,
                'meaning': label or str(row.get('note') or ''),
                'value': str(value).strip(),
            })
        return [item for item in merged if item.get('name') or item.get('meaning') or item.get('value')][:16]

    @classmethod
    def _analysis_type_from_context(cls, text, table_role=''):
        compact = re.sub(r'\s+', '', str(text or ''))
        if re.search(r'总方差|方差解释|特征根|贡献率|KMO|Bartlett|因子|主成分', compact, flags=re.IGNORECASE):
            return 'factor_analysis'
        if re.search(r'相关系数|相关矩阵|相关性', compact):
            return 'correlation'
        if re.search(r'描述性统计|描述统计|均值|标准差|最大值|最小值', compact):
            return 'descriptive'
        if re.search(r'回归|β|beta|估计|方程|模型', compact, flags=re.IGNORECASE):
            return 'regression'
        if table_role == 'model_index':
            return 'model_calculation'
        if table_role == 'variable_analysis':
            return 'variable_analysis'
        return 'evidence_data'

    @classmethod
    def _default_analysis_table_kind(cls, analysis_type='', table_role=''):
        value = str(analysis_type or '').lower()
        if table_role == 'model_index':
            return 'numeric'
        if 'correlation' in value:
            return 'correlation'
        if 'regression' in value:
            return 'regression'
        if 'descriptive' in value:
            return 'descriptive'
        if table_role == 'variable_analysis' or any(part in value for part in ('factor', 'variable', 'kmo')):
            return 'test_result'
        return 'numeric'

    @classmethod
    def _fallback_analysis_required_data(cls, equation='', definitions=None, target=None):
        definitions = definitions or []
        equation_symbols = []
        equation_text = re.sub(r'β\s*\d+', ' ', str(equation or ''))
        for symbol in re.findall(r'\b[A-Za-z][A-Za-z0-9_]{0,18}\b', equation_text):
            if symbol not in equation_symbols and symbol.lower() not in {'ln', 'log', 'exp'}:
                equation_symbols.append(symbol)
        if definitions:
            rows = [
                f'{item["symbol"]}：{item["meaning"]}，按论文研究对象、年份或样本单位收集原始数值'
                for item in definitions[:10]
                if item.get('symbol') and item.get('meaning')
            ]
            defined = {item.get('symbol') for item in definitions}
            for symbol in equation_symbols:
                if symbol not in defined and len(rows) < 12:
                    rows.append(f'{symbol}：根据正文定义收集对应变量的年度或样本观测值')
            return rows
        if equation_symbols:
            return [f'{symbol}：根据正文定义收集对应变量的年度或样本观测值' for symbol in equation_symbols[:8]]
        data_need = str((target or {}).get('dataNeed') or '').strip()
        if data_need:
            return [data_need]
        return ['围绕候选段落观点收集可核验的年份、变量符号和数值']

    @classmethod
    def _normalize_analysis_plan_payload(cls, payload, target=None, table_role=''):
        source = payload.get('analysisPlan') if isinstance(payload, dict) and isinstance(payload.get('analysisPlan'), dict) else payload
        if not isinstance(source, dict):
            source = {}
        target = target or {}
        context = cls._variable_definition_context(target, '')
        definitions = cls._variable_definitions_from_text(context)
        equation = str(source.get('equation') or source.get('model') or '').strip()
        if not equation:
            equation = cls._extract_equation_from_text(context)
        analysis_type = str(source.get('analysisType') or source.get('type') or '').strip()
        if not analysis_type:
            analysis_type = cls._analysis_type_from_context(f'{context} {equation}', table_role)
        required_data = (
            cls._coerce_analysis_list(source.get('requiredData') or source.get('dataRequirements'))
            or cls._fallback_analysis_required_data(equation, definitions, target)
        )
        table_kind = cls._normalize_table_kind(source.get('tableKind') or '')
        if not table_kind:
            table_kind = cls._default_analysis_table_kind(analysis_type, table_role)
        title = str(source.get('title') or source.get('tableTitle') or target.get('tableTitle') or target.get('chartTitle') or '').strip()
        if not title:
            if table_role == 'model_index':
                title = '模型变量年度观测值'
            elif table_role == 'variable_analysis':
                title = '变量分析结果'
            else:
                title = cls._target_title_hint(target) or '数据分析表'
        search_guidance = str(source.get('searchGuidance') or source.get('searchQuery') or source.get('query') or '').strip()
        if not search_guidance:
            search_guidance = '；'.join(required_data[:5])
        software = str(source.get('software') or source.get('recommendedSoftware') or '').strip()
        method = str(source.get('method') or source.get('calculationMethod') or '').strip()
        if not software:
            software = 'Stata / SPSS / Python statsmodels / R'
        if not method:
            if 'regression' in str(analysis_type or '').lower():
                method = '先用多元线性回归、面板回归或正文指定模型估计 β0、β1 等参数，再将已核验变量观测值代入方程分析。'
            elif table_kind == 'correlation':
                method = '相关分析，输出变量间相关系数矩阵。'
            elif table_kind == 'regression':
                method = '多元线性回归或面板回归，估计各解释变量系数并检验显著性。'
            elif table_kind == 'descriptive':
                method = '描述性统计，计算样本量、均值、标准差、最小值和最大值。'
            elif table_kind == 'test_result':
                method = '因子分析、主成分分析或变量质量检验，输出特征根、贡献率、累计贡献率或检验统计量。'
            else:
                method = '按正文模型或指标定义整理年度/样本观测值，必要时进行标准化、加权或方程代入。'
        parameters = cls._coerce_analysis_parameters(
            source.get('parameters') or source.get('coefficients') or source.get('params'),
            equation,
            definitions,
        )
        return {
            'analysisType': analysis_type,
            'equation': equation,
            'requiredData': required_data[:12],
            'software': software,
            'method': method,
            'searchGuidance': search_guidance,
            'tableKind': table_kind,
            'title': title,
            'unit': str(source.get('unit') or '').strip(),
            'parameters': parameters,
        }

    def analyze_data(self, *, query='', target=None, full_text='', data_file=None):
        target = target or {}
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        table_role = self._normalize_table_role(
            target.get('tableRole') or target.get('role'),
            self._table_context_text(target.get('tableTitle') or target.get('chartTitle') or query, target),
        )
        if artifact_type == 'table':
            target = {**target, 'tableRole': table_role, 'tableKind': self._table_kind_for_role(table_role, target.get('tableKind'))}
        if artifact_type == 'table' and table_role == 'impact_factors':
            return {
                'artifactType': artifact_type,
                'tableRole': table_role,
                'tableKind': 'impact_factors',
                'title': self._normalize_table_title(target.get('tableTitle') or query, [], target),
                'analysisPlan': None,
                'sourceNote': '影响因素表是论文内容整理表，不需要先做数据分析计划。',
            }

        context = self._variable_definition_context(target, full_text)
        variable_definitions = self._variable_definitions_from_text(context)
        variable_definition_note = json.dumps(variable_definitions, ensure_ascii=False, indent=2) if variable_definitions else '未识别到明确变量符号定义。'
        uploaded_data_note = self._decode_data_file_text(data_file)
        fallback_plan = self._normalize_analysis_plan_payload({}, target, table_role)
        try:
            api = self._require_ai('data_chart.analyze')
            prompt = f'''请阅读全文上下文和候选段落，先做“AI 数据分析计划”，不要检索最终数据。

任务：
1. 判断该候选属于哪种数据分析类型：regression、model_calculation、factor_analysis、correlation、descriptive、variable_analysis 或 evidence_data。
2. 如果正文有关系式/方程/模型，例如 Y=β0+β1X+β2M+β3Controls+ε，必须提取原式，并说明需要收集哪些变量数据。
3. 需要给出“需要哪些数据”“建议用什么软件和什么方法计算”“用户需要填写哪些参数/系数”的清单。
4. 对 model_index：重点规划年度/样本观测值表，正式表体应类似“年份、Y、X、M、Controls”，只允许数字和变量符号。
5. 对 variable_analysis：根据上下文规划总方差解释表、相关矩阵、描述性统计表、回归结果表等，正式表体只允许数字和变量符号。
6. 分析计划必须给后续“AI 搜索数据”使用，searchGuidance 要清楚写明检索哪些变量、年份、地区、样本和口径。
7. 辅助数据文件只是可选材料；如果里面有字段名或数值线索，可以纳入 requiredData，但不要要求必须上传。

候选类型：{"插表" if artifact_type == "table" else "插图"}
表格角色：{table_role if artifact_type == "table" else "非表格"}
建议 tableKind：{target.get('tableKind') or fallback_plan.get('tableKind')}
检索/分析意图：{query or target.get('intent') or target.get('reason') or '未提供'}
建议标题：{target.get('tableTitle') or target.get('chartTitle') or target.get('title') or '未提供'}
所在章节：{target.get('sectionTitle') or '未提供'}
候选段落：
{self._truncate_for_prompt(target.get('originalText') or target.get('excerpt'), 2200) or '未提供'}

正文变量符号定义 JSON：
{variable_definition_note}

全文上下文：
{self._truncate_for_prompt(full_text, 6500) or '未提供'}

用户上传数据文件摘录（可选）：
{self._truncate_for_prompt(uploaded_data_note, 5000) or '未上传'}

返回 JSON：
{{
  "analysisPlan": {{
    "analysisType": "regression|model_calculation|factor_analysis|correlation|descriptive|variable_analysis|evidence_data",
    "equation": "从正文提取的方程，没有则为空",
    "requiredData": ["Y：供应链金融发展水平，按年份收集", "X：区块链应用水平，按年份收集"],
    "software": "Stata / SPSS / Python statsmodels / R / Excel",
    "method": "具体计算方式，例如多元线性回归、主成分分析、总方差解释、相关分析、描述性统计",
    "parameters": [
      {{"name": "β0", "meaning": "常数项", "value": ""}},
      {{"name": "β1", "meaning": "X 对 Y 的边际影响", "value": ""}}
    ],
    "searchGuidance": "给后续 AI 搜索数据使用的明确检索依据",
    "tableKind": "numeric|regression|correlation|descriptive|test_result",
    "title": "根据正文语义生成的表题",
    "unit": ""
  }},
  "tableRole": "{table_role}",
  "tableKind": "numeric|regression|correlation|descriptive|test_result",
  "title": "表题"
}}'''
            payload = api.call_json_sync(
                prompt,
                system='你是论文计量与数据分析设计助手。先规划数据分析与计算方式，不检索最终数据，不编造数值。',
                temperature=0.15,
                max_tokens=2200,
                request_timeout=160,
                schema_name='data_chart_analysis_plan',
                usage_context=self._usage_context('data_chart.analyze'),
            )
            plan = self._normalize_analysis_plan_payload(payload, target, table_role)
        except Exception as exc:
            plan = fallback_plan
            plan['sourceNote'] = f'AI 数据分析未返回，已根据正文规则生成基础计划：{exc}'

        table_kind = self._table_kind_for_role(table_role, plan.get('tableKind'))
        title = self._normalize_table_title(plan.get('title') or target.get('tableTitle') or query, [], {**target, 'tableRole': table_role, 'tableKind': table_kind}) if artifact_type == 'table' else self._normalize_chart_title(plan.get('title') or target.get('chartTitle') or query, [], target)
        plan['title'] = title
        plan['tableKind'] = table_kind if artifact_type == 'table' else plan.get('tableKind')
        return {
            'artifactType': artifact_type,
            'tableRole': table_role if artifact_type == 'table' else '',
            'tableKind': plan.get('tableKind') or table_kind,
            'title': title,
            'unit': plan.get('unit', ''),
            'analysisPlan': plan,
            'sourceNote': plan.get('sourceNote') or 'AI 数据分析已完成；请核对所需数据、计算方式和参数后再搜索数据。',
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
    def parse_data_table(cls, table_text, require_numeric=True):
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

        has_structured_header = any(len(cls._data_table_header_map(row)) >= 2 for row in rows[:3])
        if not has_structured_header:
            wide_rows = cls._parse_wide_numeric_table_rows(rows, {
                'sourceName': '用户审核后的数据表',
                'publisher': '用户填写',
                'url': '',
                'note': '用户粘贴或编辑的宽表自动转换，需核验单位和统计口径。',
            })
            if wide_rows and (not require_numeric or len(wide_rows) >= 2):
                return wide_rows

        header_index = None
        header_map = {}
        for index, row in enumerate(rows[:3]):
            candidate_map = cls._data_table_header_map(row)
            if len(candidate_map) >= 2 or (not require_numeric and 'label' in candidate_map):
                header_index = index
                header_map = candidate_map
                break
        has_header = header_index is not None
        data_rows = rows[header_index + 1:] if has_header else rows
        result = []
        for index, row in enumerate(data_rows, start=1):
            if len(row) < 1 or len(cls._data_table_header_map(row)) >= 2:
                continue
            label = cls._table_cell(row, header_map, 'label', 0) or f'项目{index}'
            raw_value = cls._table_cell(row, header_map, 'value', 1 if not has_header else None)
            value = cls._parse_number(raw_value)
            if require_numeric and value is None:
                continue
            source_name = cls._table_cell(row, header_map, 'sourceName', 2 if not has_header and len(row) >= 4 else None)
            publisher = cls._table_cell(row, header_map, 'publisher', 3 if not has_header and len(row) >= 4 else None)
            url = cls._table_cell(row, header_map, 'url', 4 if not has_header and len(row) >= 5 else None)
            note = cls._table_cell(row, header_map, 'note', 5 if not has_header and len(row) >= 6 else None)
            source = cls._table_cell(row, header_map, 'source', 6 if not has_header and len(row) >= 7 else None)
            year = cls._table_cell(row, header_map, 'year', None)
            variable = cls._table_cell(row, header_map, 'variable', None)
            stat_type = cls._table_cell(row, header_map, 'statType', None)
            symbol = cls._table_cell(row, header_map, 'symbol', None)
            measure = cls._table_cell(row, header_map, 'measure', None)
            related_label = cls._table_cell(row, header_map, 'relatedLabel', None)
            related_variable = cls._table_cell(row, header_map, 'relatedVariable', None)
            sample_size = cls._table_cell(row, header_map, 'sampleSize', None)
            mean = cls._table_cell(row, header_map, 'mean', None)
            std_dev = cls._table_cell(row, header_map, 'stdDev', None)
            min_value = cls._table_cell(row, header_map, 'min', None)
            max_value = cls._table_cell(row, header_map, 'max', None)
            coefficient = cls._table_cell(row, header_map, 'coefficient', None)
            std_error = cls._table_cell(row, header_map, 'stdError', None)
            t_statistic = cls._table_cell(row, header_map, 'tStatistic', None)
            p_value = cls._table_cell(row, header_map, 'pValue', None)
            significance = cls._table_cell(row, header_map, 'significance', None)
            correlation = cls._table_cell(row, header_map, 'correlation', None)
            if not source and not has_header and len(row) == 3:
                source = row[2]
            if measure and note:
                note = f'{measure}；{note}'
            elif measure:
                note = measure
            if not source:
                source = cls._format_source({
                    'sourceName': source_name,
                    'publisher': publisher,
                    'url': url,
                    'note': note,
                })
            if not require_numeric and not any((
                label, raw_value, source_name, publisher, url, note, source, symbol, measure,
                related_label, sample_size, mean, std_dev, min_value, max_value, coefficient,
                std_error, t_statistic, p_value, significance, correlation,
            )):
                continue
            result.append({
                'label': label[:40],
                'year': year,
                'variable': variable,
                'statType': stat_type,
                'symbol': symbol,
                'measure': measure,
                'value': value,
                'rawValue': raw_value,
                'source': source,
                'sourceName': source_name,
                'publisher': publisher,
                'url': url,
                'note': note,
                'relatedLabel': related_label,
                'relatedVariable': related_variable or related_label,
                'sampleSize': sample_size,
                'mean': mean,
                'stdDev': std_dev,
                'min': min_value,
                'max': max_value,
                'coefficient': coefficient,
                'stdError': std_error,
                'tStatistic': t_statistic,
                'pValue': p_value,
                'significance': significance,
                'correlation': correlation,
            })
        result = cls._flatten_table_rows_to_single_value(result)
        if require_numeric and len(result) < 2:
            raise ValueError('至少需要 2 行带数值的数据，例如：标签,数值')
        if not require_numeric and not result:
            raise ValueError('至少需要 1 行可生成表格的指标、变量或来源信息')
        return result

    @staticmethod
    def _data_table_header_map(header):
        aliases = {
            'label': {'标签', '项目', '名称', '年份', '地区', '指标', '指标/变量', '变量', '变量名称', 'label', 'name', 'indicator'},
            'value': {'数值', '值', '数据', 'value', 'number'},
            'year': {'数据年份', '年度', '年份字段', 'year'},
            'variable': {'正式表变量', '变量列', '指标列', 'variable', 'variableName', 'variable_name', 'series', 'seriesName', 'series_name'},
            'statType': {'统计项', '统计类型', 'statType', 'stat_type', 'statistic', 'metric'},
            'symbol': {'变量符号', '变量代码', '符号', '代码', 'symbol', 'code', 'variable symbol', 'variable_code'},
            'measure': {'测度方法', '衡量方式', '计算口径', '测量方法', '口径说明', '测度指标', '定义', 'measure', 'measurement', 'method', 'calculation', 'definition'},
            'relatedLabel': {'相关变量', '对照变量', '变量2', '第二变量', '列变量', 'related variable', 'relatedlabel', 'variable2', 'with variable', 'column'},
            'relatedVariable': {'相关列变量', '矩阵列变量', 'relatedVariable', 'related_variable', 'columnVariable', 'column_variable'},
            'sampleSize': {'样本量', '观测值', '观测数', 'n', 'sample size', 'observations'},
            'mean': {'均值', '平均值', 'mean', 'average'},
            'stdDev': {'标准差', 'std', 'stddev', 'standard deviation'},
            'min': {'最小值', '最小', 'min', 'minimum'},
            'max': {'最大值', '最大', 'max', 'maximum'},
            'coefficient': {'系数', '回归系数', '估计系数', 'coef', 'coefficient', 'beta'},
            'stdError': {'标准误', '标准误差', 'std error', 'stderr', 'standard error', 'se'},
            'tStatistic': {'t统计量', 't 统计量', 't值', 't 值', 't-statistic', 'tstat', 't statistic', 't'},
            'pValue': {'p值', 'p 值', 'p-value', 'pvalue', 'p'},
            'significance': {'显著性', '星号', 'stars', 'significance'},
            'correlation': {'相关系数', '相关性', 'corr', 'correlation', 'correlation coefficient'},
            'sourceName': {'来源名称', '报告名称', '数据库名称', '网站名称', '来源名', '数据来源', 'sourceName', 'sourcename', 'source_name', 'source name'},
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

    def _request_search_payload(self, api, *, artifact_type, target, search_query, chart_title_hint, full_text, evidence_note, uploaded_data_note='', quality_feedback='', analysis_plan=None, analysis_parameters=None):
        feedback = ''
        intent = str(target.get('intent') or target.get('suggestion') or target.get('reason') or '').strip()
        title_hint = self._target_title_hint(target) or chart_title_hint
        table_role = self._normalize_table_role(target.get('tableRole') or target.get('role'), f'{intent} {target.get("dataNeed", "")} {title_hint} {search_query}')
        role_table_kind = self._table_kind_for_role(table_role, target.get('tableKind'))
        variable_definitions = self._variable_definitions_from_text(self._variable_definition_context(target, full_text))
        variable_definition_note = json.dumps(variable_definitions, ensure_ascii=False, indent=2) if variable_definitions else '未在正文中识别到明确变量符号定义。'
        analysis_plan = self._normalize_analysis_plan_payload(analysis_plan or target.get('analysisPlan') or {}, target, table_role) if (analysis_plan or target.get('analysisPlan')) else None
        if analysis_plan and artifact_type == 'table':
            role_table_kind = self._table_kind_for_role(table_role, analysis_plan.get('tableKind') or role_table_kind)
        analysis_parameters = analysis_parameters if isinstance(analysis_parameters, list) else target.get('analysisParameters')
        analysis_note = '未提供。'
        if analysis_plan:
            analysis_note = json.dumps({
                'analysisPlan': analysis_plan,
                'analysisParameters': analysis_parameters if isinstance(analysis_parameters, list) else [],
            }, ensure_ascii=False, indent=2)
        if quality_feedback:
            feedback = f'''

上一次返回的数据被系统判定为不可用，原因：{quality_feedback}
请重新检索真实统计值。不要返回变量代码、控制变量名称、指标编号、序号、变量定义或“预测方向”。如果只能找到可疑数值，也可以填写 value，但必须在 note 标明“待核验”并说明核验路径。'''
        if artifact_type == 'table':
            if table_role == 'impact_factors':
                row_requirements = (
                    '4. 这是“影响因素表”。tableRole 必须返回 impact_factors，tableKind 必须返回 impact_factors。'
                    '这是对论文全文和候选段落已有观点的结构化归纳，不是外部统计数据表。'
                    'rows 每行只写一个影响因素，label 使用中文因素名称，value/sourceName/publisher/url/note 均可留空。\n'
                    '5. 只有本类表允许正式表体出现中文。不要为影响因素表编造数值，不要输出变量符号表，不要新增参考文献来源。\n'
                )
            elif table_role == 'model_index':
                row_requirements = (
                    '4. 这是“模型测算/方程代入表”。tableRole 必须返回 model_index，tableKind 必须返回 numeric。'
                    '先从论文正文已定义的变量符号中识别模型变量含义，再搜索原始数据或已构建指数，必要时按段落中的方程进行标准化、加权或代入计算。\n'
                    '5. rows 每行只放一个年份-变量的数值：year 填年份，variable 和 symbol 必须使用“正文变量符号定义 JSON”中已经定义的符号（如 Y、Y_fin、X1、GDP），value 填数值。'
                    '正式表体除首列年份外只允许数字和变量符号；不要把中文指标名放入 variable/symbol，不要自行创造未在正文定义的 X1/X2。\n'
                    '6. 如果只能找到可疑数值，也要填 value 并在 note 标明“待核验”；确实没有数值时才留空并写清核验路径。\n'
                )
            elif table_role == 'variable_analysis':
                row_requirements = (
                    '4. 这是“变量分析表”。tableRole 必须返回 variable_analysis，tableKind 选择 test_result、correlation、descriptive 或 regression。'
                    '用于变量方程构建处，必须能说明变量质量、因子/主成分贡献、相关性、描述性统计或回归结果。\n'
                    '5. 正式表体只允许数字和变量符号。variable/symbol 必须优先使用“正文变量符号定义 JSON”中已经定义的符号；'
                    '因子项可以使用 F1、F2 等模型自然符号。不要把中文变量名写入正式表字段，不要自行创造未在正文定义的普通变量符号。'
                    '若为总方差/因子贡献表，使用 statType=eigenvalue/contributionRate/cumulativeRate；'
                    '若为相关矩阵，使用 statType=correlation 和 relatedVariable；若为描述统计，使用 mean/stdDev/min/max/sampleSize。\n'
                )
            else:
                row_requirements = (
                    '4. 这是“基础数据强化表”。tableRole 必须返回 evidence_data，tableKind 必须返回 numeric。'
                    '先拆出该段观点真正需要的变量，再搜索可核验数值。\n'
                    '5. rows 每行只放一个年份-变量的数值：year 填年份，variable 和 symbol 优先使用“正文变量符号定义 JSON”中已经定义的符号或正文已有英文缩写（如 GDP、DEP、LOAN），value 填数值。'
                    '正式表体除首列年份外只允许数字和变量符号；不要把中文指标名放入 variable/symbol，不要自行创造未在正文定义的 X1/X2。\n'
                    '6. 若数值待核验，可以先填 value，但必须在 note 标明“待核验”和来源路径；确实没有数值时才留空。\n'
                )
        else:
            row_requirements = (
                '4. rows 至少 2 行；每行包含 label、value，并尽量填写 sourceName、publisher、url、note。没有 URL 时不要因为 URL 缺失而放弃，但 note 必须说明页码、表号、统计口径或核验路径。\n'
                '5. value 只填真实统计数值，单位写在 unit；禁止把变量编号、排序序号、分类编码、变量名称、预测方向写成 value。\n'
                '6. label 应该是年份、地区、组别、行业、指标项等可解释对象；禁止返回 pgdp、urban、fagri、indstr、edu、internet 等变量代码作为绘图标签。\n'
            )
        if artifact_type == 'table' and table_role == 'impact_factors':
            prompt = f'''请围绕下面论文段落和全文内容，提取适合写入论文“影响因素表”的因素清单。

硬性要求：
1. 只根据论文全文、候选段落和候选位置建议归纳影响因素，不进行外部数据检索，不新增参考文献。
2. rows 每行只写一个影响因素，label 使用中文因素名称；value、sourceName、publisher、url、note 留空。
3. 不要输出数值、变量符号、来源名称、发布机构、URL 或“待核验来源”。
4. title 必须直接概括该段讨论对象和影响因素，不要只写“影响因素”。

候选位置建议/检索意图：{intent or search_query or target.get('dataNeed') or '未提供'}
当前建议表题：{target.get('tableTitle') or chart_title_hint or '未提供'}
所在章节：{target.get('sectionTitle') or '未提供'}
候选段落：
{self._truncate_for_prompt(target.get('originalText') or target.get('excerpt'), 1800) or '未提供'}

全文摘录：
{self._truncate_for_prompt(full_text, 5000) or '未提供'}

返回 JSON：
{{
  "artifactType": "table",
  "tableRole": "impact_factors",
  "tableKind": "impact_factors",
  "title": "表格标题",
  "sourceNote": "已根据论文内容提取影响因素；该表不新增数据来源和参考文献。",
  "rows": [
    {{"label": "影响因素名称", "value": "", "sourceName": "", "publisher": "", "url": "", "note": ""}}
  ],
  "needsManualData": false,
  "chartType": "bar",
  "unit": ""
}}'''
            return api.call_json_sync(
                prompt,
                system='你是严谨的论文结构化编辑。请只归纳正文影响因素，不做外部数据检索，不新增参考文献。',
                temperature=0.15,
                max_tokens=1800,
                request_timeout=160,
                schema_name='data_chart_impact_factors',
                usage_context=self._usage_context('data_chart.search.impact_factors'),
            )
        system = (
            '你是论文数据检索助手。你需要根据论文段落和检索式寻找可用于论文图表的数据，并给出清晰来源。'
            '必须优先使用用户提供的“候选网页来源”，并诚实说明来源。'
            '如果候选网页来源中没有足够数据，且你无法确认真实数据来源，不要编造数值、网址、报告名或年份。'
        )
        prompt = f'''请围绕下面论文段落，自行判断应使用哪些真实数据来生成{"表格" if artifact_type == "table" else "图表"}，并返回可供用户审核的数据表。

硬性要求：
1. 数据必须优先服务于“候选位置黑色建议/检索意图”，不要因为论文总题目而改找无关数据。
2. 候选网页来源是后端按 AI 给出的检索方向抓取的证据；请优先从这些来源中提取或归纳可制图数据。
3. 不要编造不存在的数据来源；候选来源不足时，可以使用你能够确认的公开权威报告、统计年鉴、政府/机构数据库、用户上传数据文件或论文中的数据，但必须写明可核验的来源名称、发布机构、年份、页码/表号/检索路径。只有在无法确认真实数值时才返回 needsManualData=true。
{row_requirements.rstrip()}
7. sourceNote 要提示用户逐项审核真实性。
8. title 必须直接概括数据指标、对象和时间范围，不要使用论文总题目、章节题或“影响因素”这类泛化标题。
9. 如果提供了“用户上传数据文件摘录”，它只是辅助检索和抽数的材料；可优先从中提取真实数值和来源，但不要要求用户必须上传文件。
10. 如果找不到完全可靠的连续面板数值，不要只写“请用户补充”；请尽量返回可疑但可核验的候选数值，value 可以先填，note 必须说明“待核验”和核验路径。确实没有任何数值时才让 value 为空。
11. 如果提供了“AI 数据分析计划 JSON”，必须优先围绕其中的 equation、requiredData、searchGuidance 和 tableKind 搜索数据；不要退回论文总题目泛化检索。
12. 搜索顺序必须先找 AI 数据分析计划中的参数/系数/检验统计量：如果能直接得到 β0、β1、β2、ε、KMO、特征根、贡献率、回归系数等参数值，必须放入 analysisParameters；找不到时保留空值，不要把“用户填入计算值”当成值。
13. 对 model_index，应返回年度/样本单位与正文变量符号的观测值，便于用户代入方程；对 variable_analysis，应按计划返回总方差解释、相关矩阵、描述性统计或回归结果所需的数值。
{feedback}

论文段落：
{self._truncate_for_prompt(target.get('originalText') or target.get('excerpt'), 1800)}

数据需求：{target.get('dataNeed') or '未提供'}
候选位置黑色建议/检索意图：{intent or '未提供'}
建议图题/表题：{title_hint or '未提供'}
候选类型：{"插表" if artifact_type == "table" else "插图"}
表格功能角色：{table_role if artifact_type == "table" else '非表格'}
建议 tableKind：{role_table_kind if artifact_type == "table" else '非表格'}
正文变量符号定义 JSON：
{variable_definition_note}
AI 数据分析计划 JSON：
{analysis_note}
检索方向：{search_query or '未提供'}
全文上下文：
{self._truncate_for_prompt(full_text, 6000)}

候选网页来源 JSON：
{evidence_note}

用户上传数据文件摘录（可选）：
{self._truncate_for_prompt(uploaded_data_note, 7000) or '未上传'}

返回 JSON：
{{
  "needsManualData": false,
  "unit": "%",
  "chartType": "line|bar|pie",
  "tableRole": "impact_factors|model_index|variable_analysis|evidence_data",
  "tableKind": "impact_factors|numeric|correlation|regression|descriptive|test_result",
  "title": "图表标题",
  "sourceNote": "数据来源审核说明",
  "rows": [
    {{
      "label": "2021年",
      "year": "2021",
      "variable": "指标或变量名",
      "symbol": "变量符号",
      "statType": "",
      "relatedVariable": "",
      "value": 12.5,
      "sourceName": "报告或数据库名称",
      "publisher": "发布机构",
      "url": "https://...",
      "note": "页码、表号、统计口径或核验说明"
    }}
  ],
  "analysisParameters": [
    {{
      "name": "β1",
      "meaning": "X 对 Y 的边际影响",
      "value": "0.123",
      "sourceName": "参数值来源报告/论文/用户上传文件",
      "publisher": "发布机构",
      "url": "https://...",
      "note": "页码、表号、模型设定、待核验说明；若未找到值则 value 为空"
    }}
  ],
  "manualHint": "如果需要用户补充，写明应补充什么"
}}'''
        return api.call_json_sync(
            prompt,
            system=system,
            temperature=0.12 if quality_feedback else 0.15,
            max_tokens=3200,
            request_timeout=220,
            schema_name='data_chart_search',
            usage_context=self._usage_context('data_chart.search.retry' if quality_feedback else 'data_chart.search'),
        )

    def search_data(self, *, query='', target=None, full_text='', user_data='', data_file=None, analysis_plan=None, analysis_parameters=None):
        if user_data and self._normalize_text(user_data):
            artifact_type = self._normalize_artifact_type((target or {}).get('artifactType') or (target or {}).get('insertType'))
            table_role = self._normalize_table_role((target or {}).get('tableRole') or (target or {}).get('role'), f'{(target or {}).get("reason", "")} {(target or {}).get("intent", "")} {(target or {}).get("dataNeed", "")} {(target or {}).get("tableTitle", "")} {query}')
            if artifact_type == 'table':
                target = {**(target or {}), 'tableRole': table_role, 'tableKind': self._table_kind_for_role(table_role, (target or {}).get('tableKind'))}
            rows = self.parse_data_table(user_data, require_numeric=artifact_type != 'table')
            if artifact_type == 'table':
                rows = self._apply_article_variable_symbols(rows, target, user_data)
            title_seed = (target or {}).get('tableTitle') or (target or {}).get('chartTitle') or query
            title = (
                self._normalize_table_title(title_seed, rows, target or {})
                if artifact_type == 'table'
                else self._normalize_chart_title(title_seed, rows, target or {})
            )
            chart_type = self._choose_chart_type((target or {}).get('chartType'), rows, target or {})
            table_kind = self._preferred_table_kind(rows, title, target or {}) if artifact_type == 'table' else 'numeric'
            return {
                'artifactType': artifact_type,
                'tableRole': table_role if artifact_type == 'table' else '',
                'tableKind': table_kind,
                'tableText': self._format_table(rows),
                'sourceNote': '已使用用户提供的数据表；生成图表前仍建议核对来源与单位。',
                'foundRows': len(rows),
                'dataRows': self._public_rows(rows),
                'dataSources': self._collect_row_sources(rows),
                'sourceItems': self._build_source_items(rows, []),
                'analysisParameters': self._merge_analysis_parameters(analysis_parameters, {}, rows),
                'needsManualData': False,
                'title': title,
                'chartType': chart_type,
                'unit': '',
            }

        api = self._require_ai('data_chart.search')
        target = target or {}
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        table_role = self._normalize_table_role(target.get('tableRole') or target.get('role'), f'{target.get("reason", "")} {target.get("intent", "")} {target.get("dataNeed", "")} {target.get("tableTitle", "")} {query}')
        if artifact_type == 'table':
            target = {**target, 'tableRole': table_role, 'tableKind': self._table_kind_for_role(table_role, target.get('tableKind'))}
        analysis_plan = analysis_plan if isinstance(analysis_plan, dict) else target.get('analysisPlan')
        analysis_parameters = analysis_parameters if isinstance(analysis_parameters, list) else target.get('analysisParameters')
        normalized_analysis_plan = (
            self._normalize_analysis_plan_payload(analysis_plan, target, table_role)
            if isinstance(analysis_plan, dict) and analysis_plan
            else None
        )
        if normalized_analysis_plan:
            target = {
                **target,
                'analysisPlan': normalized_analysis_plan,
                'analysisParameters': analysis_parameters if isinstance(analysis_parameters, list) else [],
            }
            if artifact_type == 'table':
                target['tableKind'] = self._table_kind_for_role(table_role, normalized_analysis_plan.get('tableKind') or target.get('tableKind'))
        uploaded_data_note = self._decode_data_file_text(data_file)
        uploaded_rows = []
        if uploaded_data_note:
            try:
                uploaded_rows = self.parse_data_table(uploaded_data_note, require_numeric=artifact_type != 'table')
            except Exception:
                uploaded_rows = self._extract_numeric_rows_from_uploaded_note(uploaded_data_note, target=target, query=query)
        intent = str(target.get('intent') or target.get('suggestion') or target.get('reason') or '').strip()
        target_title_hint = self._target_title_hint(target)
        query_parts = []
        plan_required = ' '.join((normalized_analysis_plan or {}).get('requiredData') or [])
        plan_search = (normalized_analysis_plan or {}).get('searchGuidance') or ''
        plan_equation = (normalized_analysis_plan or {}).get('equation') or ''
        plan_parameters_text = ' '.join(
            ' '.join(str(parameter.get(field, '') or '') for field in ('name', 'meaning'))
            for parameter in (analysis_parameters if isinstance(analysis_parameters, list) else [])
            if isinstance(parameter, dict)
        )
        for part in (plan_parameters_text, plan_search, plan_required, plan_equation, intent, query, target.get('query'), target.get('dataNeed')):
            text = str(part or '').strip()
            if text and text not in query_parts:
                query_parts.append(text)
        search_query = ' '.join(query_parts)
        chart_title_hint = str((normalized_analysis_plan or {}).get('title') or target_title_hint or target.get('tableTitle') or target.get('chartTitle') or '').strip()
        evidence_query = ' '.join(part for part in (plan_parameters_text, plan_search, plan_required, intent, search_query, chart_title_hint, target.get('dataNeed')) if part).strip()
        evidence_query = evidence_query or target.get('sectionTitle')
        if chart_title_hint and chart_title_hint not in evidence_query:
            evidence_query = f'{chart_title_hint} {evidence_query}'
        if artifact_type == 'table' and table_role == 'impact_factors':
            evidence = []
        else:
            evidence = self._collect_search_evidence(evidence_query, limit=5, page_limit=1)
        if not evidence and not (artifact_type == 'table' and table_role == 'impact_factors'):
            fallback_evidence = []
            seen_urls = set()
            for term in self._target_indicator_terms(target, search_query)[:5]:
                for item in self._collect_search_evidence(f'{term} 省级 数据 统计 年鉴 公报', limit=2, page_limit=0):
                    url = item.get('url')
                    if url and url not in seen_urls:
                        fallback_evidence.append(item)
                        seen_urls.add(url)
                if len(fallback_evidence) >= 6:
                    break
            evidence = fallback_evidence[:6]
        evidence_note = (
            json.dumps(evidence, ensure_ascii=False, indent=2)
            if evidence
            else '未抓取到可核验网页来源。若模型也无法确认真实来源，必须返回 needsManualData=true。'
        )
        locally_extracted_rows = self._dedupe_numeric_rows([
            *uploaded_rows,
            *self._extract_numeric_rows_from_evidence(evidence, target=target, query=search_query),
            *self._extract_numeric_rows_from_text(full_text, {
                'sourceName': '论文全文内容',
                'publisher': '用户提供正文',
                'url': '',
                'note': '从论文全文已有表述中自动抽取，需核验原文数据来源。',
            }, target=target, query=search_query, limit=12),
        ])
        if artifact_type == 'table':
            locally_extracted_rows = self._apply_article_variable_symbols(locally_extracted_rows, target, full_text)
        min_numeric_rows = 1 if artifact_type == 'table' else 2
        try:
            payload = self._request_search_payload(
                api,
                artifact_type=artifact_type,
                target=target,
                search_query=search_query,
                chart_title_hint=chart_title_hint,
                full_text=full_text,
                evidence_note=evidence_note,
                uploaded_data_note=uploaded_data_note,
                analysis_plan=normalized_analysis_plan,
                analysis_parameters=analysis_parameters,
            )
        except Exception as exc:
            if artifact_type == 'table' and table_role == 'impact_factors':
                fallback_rows = self._fallback_impact_factor_rows(target, search_query)
                title = self._normalize_table_title(chart_title_hint or query, fallback_rows, target)
                return {
                    'artifactType': artifact_type,
                    'tableRole': table_role,
                    'tableKind': 'impact_factors',
                    'tableText': self._format_table(fallback_rows),
                    'sourceNote': f'AI 提取影响因素时未返回：{exc}。已先根据候选位置整理可编辑因素清单；该表不新增数据来源和参考文献。',
                    'foundRows': len(fallback_rows),
                    'dataRows': self._public_rows(fallback_rows),
                    'dataSources': [],
                    'sourceItems': [],
                    'needsManualData': False,
                    'sourceRisk': False,
                    'chartType': 'bar',
                    'title': title,
                    'unit': '',
                }
            if len(locally_extracted_rows) >= min_numeric_rows:
                title = (
                    self._normalize_table_title(chart_title_hint or query, locally_extracted_rows, target)
                    if artifact_type == 'table'
                    else self._normalize_chart_title(chart_title_hint or query, locally_extracted_rows, target)
                )
                return {
                    'artifactType': artifact_type,
                    'tableRole': table_role if artifact_type == 'table' else '',
                    'tableKind': self._preferred_table_kind(locally_extracted_rows, title, target) if artifact_type == 'table' else 'numeric',
                    'tableText': self._format_table(locally_extracted_rows),
                    'sourceNote': f'AI 整理数据时未返回：{exc}。已先使用本地抽取到的数值，请核验来源、口径和年份后再生成图表。',
                    'foundRows': len(locally_extracted_rows),
                    'dataRows': self._public_rows(locally_extracted_rows),
                    'dataSources': self._collect_row_sources(locally_extracted_rows),
                    'sourceItems': self._build_source_items(locally_extracted_rows, evidence),
                    'analysisParameters': self._merge_analysis_parameters(analysis_parameters, {}, locally_extracted_rows),
                    'needsManualData': False,
                    'sourceRisk': True,
                    'chartType': self._choose_chart_type(target.get('chartType'), locally_extracted_rows, target),
                    'title': title,
                    'unit': '',
                }
            fallback_rows = self._fallback_source_rows(target, evidence, payload={'manualHint': str(exc)}, search_query=search_query)
            title = (
                self._normalize_table_title(chart_title_hint, fallback_rows, target)
                if artifact_type == 'table'
                else self._normalize_chart_title(chart_title_hint, fallback_rows, target)
            )
            return {
                'artifactType': artifact_type,
                'tableRole': table_role if artifact_type == 'table' else '',
                'tableKind': self._preferred_table_kind(fallback_rows, title, target) if artifact_type == 'table' else 'source',
                'tableText': self._format_table(fallback_rows) if fallback_rows else '',
                'sourceNote': (
                    f'AI 整理数据时未返回：{exc}。已先整理出候选来源；请在可编辑数据表中补齐该表需要的字段，并核验来源、口径和年份后再生成表格。'
                    if artifact_type == 'table'
                    else f'AI 整理数据时未返回：{exc}。已先整理出候选指标来源表，请打开来源核验并在“数值”列补齐真实数值后再生成图表。'
                ),
                'foundRows': 0,
                'sourceCandidateRows': len(fallback_rows),
                'dataRows': self._public_rows(fallback_rows),
                'dataSources': self._collect_evidence_sources(evidence),
                'sourceItems': self._build_source_items(fallback_rows, evidence),
                'analysisParameters': self._merge_analysis_parameters(analysis_parameters, {}, fallback_rows),
                'needsManualData': True,
                'sourceRisk': True,
                'chartType': self._choose_chart_type(target.get('chartType'), [], target),
                'title': title,
                'unit': '',
            }
        raw_payload_rows = payload.get('rows', []) if isinstance(payload, dict) else []
        rows = (
            self._coerce_ai_rows(raw_payload_rows)
            if artifact_type != 'table'
            else self._coerce_ai_table_rows(raw_payload_rows)
        )
        if artifact_type == 'table':
            rows = self._apply_article_variable_symbols(rows, target, full_text)
        updated_analysis_parameters = self._merge_analysis_parameters(analysis_parameters, payload, rows)
        payload_table_kind = str((payload or {}).get('tableKind') or '').strip().lower()
        payload_table_role = self._normalize_table_role((payload or {}).get('tableRole') or table_role, f'{chart_title_hint} {search_query}')
        table_role = payload_table_role if artifact_type == 'table' else table_role
        if artifact_type == 'table' and payload_table_kind:
            target = {**target, 'tableRole': payload_table_role, 'tableKind': self._table_kind_for_role(payload_table_role, payload_table_kind)}
        rows_are_empty_value_table = (
            artifact_type == 'table'
            and rows
            and not any(row.get('value') is not None for row in rows)
            and self._table_kind(rows, chart_title_hint, target) in {'numeric', 'source'}
        )
        if (
            not (artifact_type == 'table' and table_role == 'impact_factors')
            and (len(rows) < min_numeric_rows or rows_are_empty_value_table)
            and len(locally_extracted_rows) >= min_numeric_rows
        ):
            rows = locally_extracted_rows
            payload = {
                **(payload if isinstance(payload, dict) else {}),
                'sourceNote': '已从网页证据、上传文件或论文全文中自动识别出候选数值；请核验来源、口径和年份后再生成图表。',
                'needsManualData': False,
            }
            updated_analysis_parameters = self._merge_analysis_parameters(analysis_parameters, payload, rows)
        quality_issue = '' if artifact_type == 'table' else self._data_quality_issue(rows, target=target, payload=payload)
        if quality_issue and len(locally_extracted_rows) >= min_numeric_rows:
            local_issue = self._data_quality_issue(locally_extracted_rows, target=target, payload=payload)
            if not local_issue:
                rows = locally_extracted_rows
                payload = {
                    **(payload if isinstance(payload, dict) else {}),
                    'sourceNote': f'AI 返回数据质量不合格，已改用本地抽取到的数值。原问题：{quality_issue} 请核验来源、口径和年份后再生成图表。',
                    'needsManualData': False,
                }
                updated_analysis_parameters = self._merge_analysis_parameters(analysis_parameters, payload, rows)
                quality_issue = ''
        if quality_issue and not bool((payload or {}).get('needsManualData')):
            retry_payload = self._request_search_payload(
                api,
                artifact_type=artifact_type,
                target=target,
                search_query=search_query,
                chart_title_hint=chart_title_hint,
                full_text=full_text,
                evidence_note=evidence_note,
                uploaded_data_note=uploaded_data_note,
                quality_feedback=quality_issue,
                analysis_plan=normalized_analysis_plan,
                analysis_parameters=analysis_parameters,
            )
            retry_raw_rows = retry_payload.get('rows', []) if isinstance(retry_payload, dict) else []
            retry_rows = (
                self._coerce_ai_rows(retry_raw_rows)
                if artifact_type != 'table'
                else self._coerce_ai_table_rows(retry_raw_rows)
            )
            if artifact_type == 'table':
                retry_rows = self._apply_article_variable_symbols(retry_rows, target, full_text)
            retry_issue = '' if artifact_type == 'table' else self._data_quality_issue(retry_rows, target=target, payload=retry_payload)
            min_retry_rows = 1 if artifact_type == 'table' else 2
            if not retry_issue and (len(retry_rows) >= min_retry_rows or len(retry_rows) >= len(rows)):
                payload = retry_payload
                rows = retry_rows
                updated_analysis_parameters = self._merge_analysis_parameters(analysis_parameters, payload, rows)
                quality_issue = ''
            elif len(locally_extracted_rows) >= min_numeric_rows:
                local_issue = self._data_quality_issue(locally_extracted_rows, target=target, payload=retry_payload)
                if not local_issue:
                    payload = {
                        **(retry_payload if isinstance(retry_payload, dict) else {}),
                        'sourceNote': f'重试仍未得到可用数值，已改用本地抽取到的数值。原问题：{quality_issue} 请核验来源、口径和年份后再生成图表。',
                        'needsManualData': False,
                    }
                    rows = locally_extracted_rows
                    updated_analysis_parameters = self._merge_analysis_parameters(analysis_parameters, payload, rows)
                    quality_issue = ''
        source_warning = bool(rows) and not self._rows_have_verifiable_sources(rows)
        if artifact_type == 'table' and table_role == 'impact_factors':
            source_warning = False
        model_needs_manual = bool((payload or {}).get('needsManualData'))
        needs_manual = (
            bool(quality_issue)
            or (len(rows) < 1 if artifact_type == 'table' else len(rows) < 2)
            or (model_needs_manual and not rows)
        )
        evidence_sources = self._collect_evidence_sources(evidence)
        if needs_manual:
            if artifact_type == 'table' and table_role == 'impact_factors':
                fallback_rows = rows or self._fallback_impact_factor_rows(target, search_query)
                result_title = self._normalize_table_title((payload or {}).get('title') or chart_title_hint, fallback_rows, target)
                return {
                    'artifactType': artifact_type,
                    'tableRole': table_role,
                    'tableKind': 'impact_factors',
                    'tableText': self._format_table(fallback_rows) if fallback_rows else '',
                    'sourceNote': str((payload or {}).get('sourceNote') or '已根据论文内容提取影响因素；该表不新增数据来源和参考文献。'),
                    'foundRows': len(fallback_rows),
                    'sourceCandidateRows': 0,
                    'dataRows': self._public_rows(fallback_rows),
                    'dataSources': [],
                    'sourceItems': [],
                    'needsManualData': False,
                    'sourceRisk': False,
                    'chartType': 'bar',
                    'title': result_title,
                    'unit': '',
                }
            source_note = str((payload or {}).get('manualHint') or (payload or {}).get('sourceNote') or '').strip()
            if quality_issue:
                source_note = f'{quality_issue} 请重新检索真实统计数据，或在下方手动录入已核验的数值、来源名称、发布机构、链接/页码。'
            else:
                source_note = source_note or 'AI 未能确认可直接作图的数据来源，请补充真实数据与来源后再生成图表。'
            fallback_rows = []
            if not quality_issue and len(rows) < (1 if artifact_type == 'table' else 2):
                fallback_rows = self._fallback_source_rows(target, evidence, payload=payload, search_query=search_query)
                if fallback_rows:
                    source_note = (
                        f'{source_note} 已先整理出可核验的候选指标来源表；'
                        + ('请核验来源、口径和备注后再生成表格。' if artifact_type == 'table' else '请在“数值”列补齐真实数值后再生成图表。')
                    )
            public_rows = [] if quality_issue else self._public_rows(rows or fallback_rows)
            result_rows = [] if quality_issue else (rows or fallback_rows)
            result_title = (
                self._normalize_table_title((payload or {}).get('title') or chart_title_hint, rows or fallback_rows, target)
                if artifact_type == 'table'
                else self._normalize_chart_title((payload or {}).get('title') or chart_title_hint, rows or fallback_rows, target)
            )
            return {
                'artifactType': artifact_type,
                'tableRole': table_role if artifact_type == 'table' else '',
                'tableKind': self._preferred_table_kind(result_rows, result_title, target) if artifact_type == 'table' else 'source',
                'tableText': '' if quality_issue else (self._format_table(rows or fallback_rows) if (rows or fallback_rows) else ''),
                'sourceNote': source_note,
                'foundRows': len(rows),
                'sourceCandidateRows': len(fallback_rows),
                'dataRows': public_rows,
                'dataSources': evidence_sources,
                'sourceItems': self._build_source_items([] if quality_issue else (rows or fallback_rows), evidence),
                'analysisParameters': updated_analysis_parameters,
                'needsManualData': True,
                'sourceRisk': bool(fallback_rows),
                'chartType': self._choose_chart_type((payload or {}).get('chartType') or target.get('chartType'), rows, target),
                'title': result_title,
                'unit': str((payload or {}).get('unit') or '').strip(),
            }
        source_note = str((payload or {}).get('sourceNote') or (
            '已根据论文内容提取影响因素；该表不新增数据来源和参考文献。'
            if artifact_type == 'table' and table_role == 'impact_factors'
            else 'AI 已整理数据来源；请用户核验来源、口径和年份后再生成图表。'
        ))
        if model_needs_manual:
            source_note = source_note or str((payload or {}).get('manualHint') or '').strip()
            if '审核' not in source_note and '核验' not in source_note:
                source_note = f'{source_note}；请用户审核真实性后再生成图表。'
        if source_warning:
            warning = '部分行缺少完整链接、页码或发布机构，已保留到可编辑数据表，请用户按来源逐项核验后再生成图表。'
            source_note = f'{source_note}；{warning}' if source_note else warning
        result_title = (
            self._normalize_table_title((payload or {}).get('title') or chart_title_hint, rows, target)
            if artifact_type == 'table'
            else self._normalize_chart_title((payload or {}).get('title') or chart_title_hint, rows, target)
        )
        return {
            'artifactType': artifact_type,
            'tableRole': table_role if artifact_type == 'table' else '',
            'tableKind': self._preferred_table_kind(rows, result_title, target) if artifact_type == 'table' else 'numeric',
            'tableText': self._format_table(rows),
            'sourceNote': source_note,
            'foundRows': len(rows),
            'dataRows': self._public_rows(rows),
            'dataSources': [] if artifact_type == 'table' and table_role == 'impact_factors' else self._merge_sources(self._collect_row_sources(rows), evidence_sources),
            'sourceItems': [] if artifact_type == 'table' and table_role == 'impact_factors' else self._build_source_items(rows, evidence),
            'analysisParameters': updated_analysis_parameters,
            'needsManualData': False,
            'sourceRisk': source_warning or model_needs_manual,
            'chartType': self._choose_chart_type((payload or {}).get('chartType') or target.get('chartType'), rows, target),
            'title': result_title,
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
        writer.writerow([
            '标签', '数值', '数据年份', '正式表变量', '统计项', '相关列变量',
            '变量符号', '测度方法', '相关变量', '样本量', '均值', '标准差', '最小值', '最大值',
            '系数', '标准误', 't统计量', 'P值', '显著性', '相关系数',
            '来源名称', '发布机构', '链接', '备注', '来源/备注',
        ])
        for row in rows:
            writer.writerow([
                row.get('label', ''),
                row.get('rawValue', row.get('value', '')),
                row.get('year', ''),
                row.get('variable', ''),
                row.get('statType', ''),
                row.get('relatedVariable', ''),
                row.get('symbol', ''),
                row.get('measure', ''),
                row.get('relatedLabel', ''),
                row.get('sampleSize', ''),
                row.get('mean', ''),
                row.get('stdDev', ''),
                row.get('min', ''),
                row.get('max', ''),
                row.get('coefficient', ''),
                row.get('stdError', ''),
                row.get('tStatistic', ''),
                row.get('pValue', ''),
                row.get('significance', ''),
                row.get('correlation', ''),
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
            return DataChartAssistant._default_artifact_label('', kind)
        field_names = ('tableLabel', 'artifactLabel') if kind == 'table' else ('figureLabel', 'artifactLabel')
        for field in field_names:
            value = re.sub(r'\s+', '', str(target.get(field, '') or '').strip())
            if value and not DataChartAssistant._should_recompute_artifact_label(value, target.get('sectionTitle', ''), kind):
                return value
        return DataChartAssistant._default_artifact_label(target.get('sectionTitle', ''), kind)

    @staticmethod
    def _default_artifact_label(section_title='', kind='figure'):
        return DataChartAssistant._artifact_sequence_label(section_title, kind, 1)

    def generate_chart(self, *, table_text='', chart_type='bar', title='', unit='', target=None):
        target = target or {}
        unit = str(unit or '').strip()
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        rows = self.parse_data_table(table_text, require_numeric=artifact_type != 'table')
        if artifact_type == 'table':
            rows = self._apply_article_variable_symbols(rows, target, table_text)
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

    def preview_chart(self, *, table_text='', chart_type='bar', title='', unit='', target=None):
        target = target or {}
        unit = str(unit or '').strip()
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        rows = self.parse_data_table(table_text, require_numeric=artifact_type != 'table')
        title_seed = title or target.get('tableTitle') or target.get('chartTitle') or target.get('title') or target.get('dataNeed') or ''
        if artifact_type == 'table':
            rows = self._apply_article_variable_symbols(rows, target, table_text)
            caption = self._build_table_caption(self._normalize_table_title(title_seed, rows, target))
            table_label = self._target_artifact_label(target, 'table')
            rows = self._sanitize_variable_table_rows(rows, caption, target)
            table_role = self._normalize_table_role(target.get('tableRole') or target.get('role'), self._table_context_text(caption, target))
            table_kind = self._public_table_kind(rows, caption, target)
            table_markdown = self._build_table_markdown(rows, caption, unit, table_label=table_label, target=target)
            return {
                'previewOnly': True,
                'artifactType': 'table',
                'tableRole': table_role,
                'tableKind': table_kind,
                'artifactLabel': table_label,
                'table': {
                    'title': caption,
                    'caption': caption,
                    'unit': unit,
                    'rows': rows,
                    'tableKind': table_kind,
                },
                'chart': None,
                'replacementText': '',
                'tableMarkdown': table_markdown,
                'sectionTitle': target.get('sectionTitle', '') if isinstance(target, dict) else '',
                'originalText': target.get('originalText', '') if isinstance(target, dict) else '',
                'summary': self._chart_summary(rows, unit),
                'referenceEntries': [],
            }
        title = self._normalize_chart_title(title_seed, rows, target)
        chart_type = self._choose_chart_type(chart_type, rows, target)
        image = self._render_chart(rows, chart_type=chart_type, title=title, unit=unit)
        data_url = self._image_to_data_url(image)
        caption = self._build_caption(rows, chart_type, title, unit)
        figure_label = self._target_artifact_label(target, 'figure')
        figure_markdown = f'![{title}]({data_url})\n\n{figure_label} {caption}'
        return {
            'previewOnly': True,
            'chart': {
                'dataUrl': data_url,
                'title': title,
                'caption': caption,
                'chartType': chart_type,
                'unit': unit,
                'rows': rows,
            },
            'replacementText': '',
            'figureMarkdown': figure_markdown,
            'artifactType': 'figure',
            'artifactLabel': figure_label,
            'sectionTitle': target.get('sectionTitle', '') if isinstance(target, dict) else '',
            'originalText': target.get('originalText', '') if isinstance(target, dict) else '',
            'summary': self._chart_summary(rows, unit),
            'referenceEntries': [],
        }

    @classmethod
    def _parameter_value_map(cls, parameters):
        values = {}
        for parameter in parameters or []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get('name') or parameter.get('symbol') or parameter.get('label') or '').strip()
            value = str(parameter.get('value') or parameter.get('rawValue') or '').strip()
            if not name or not value:
                continue
            values[cls._analysis_parameter_key({'name': name})] = value
        return values

    @classmethod
    def _substitute_equation_parameters(cls, equation, parameters):
        expression = cls._normalize_text(equation)
        if not expression or '=' not in expression:
            return ''
        for parameter in sorted(parameters or [], key=lambda item: len(str(item.get('name') or '')), reverse=True):
            name = str(parameter.get('name') or '').strip()
            value = str(parameter.get('value') or '').strip()
            if not name or not value:
                continue
            variants = {name, name.replace('β', 'beta'), name.replace('β', 'Beta')}
            for variant in variants:
                if not variant:
                    continue
                expression = re.sub(re.escape(variant), value, expression, flags=re.IGNORECASE)
        return re.sub(r'\s+', '', expression).strip()

    @classmethod
    def _factor_expression_from_rows(cls, rows, equation=''):
        candidates = []
        for row in rows or []:
            stat_type = str(row.get('statType') or '').strip().lower()
            label = str(row.get('label') or '').strip()
            value = cls._parse_number(row.get('rawValue', row.get('value', '')))
            if value is None:
                continue
            is_contribution = (
                stat_type in {'contributionrate', 'contribution_rate', 'variancecontribution', '贡献率'}
                or '贡献率' in label
                or '方差贡献' in label
            )
            if not is_contribution:
                continue
            symbol = str(row.get('symbol') or '').strip()
            if not symbol:
                match = re.search(r'\b(?:F|Y|X|PC|Factor)\s*\d+\b', f'{label} {row.get("variable", "")}', flags=re.IGNORECASE)
                symbol = re.sub(r'\s+', '', match.group(0)) if match else f'F{len(candidates) + 1}'
            clean_label = re.sub(r'(?:累计)?贡献率|方差贡献率|特征根', '', label).strip()
            candidates.append({
                'symbol': symbol,
                'label': clean_label or symbol,
                'value': value,
            })
        if not candidates:
            return '', []
        total = sum(abs(item['value']) for item in candidates)
        if total <= 0:
            return '', []
        max_value = max(abs(item['value']) for item in candidates)
        denominator = 100 if max_value > 1 else (1 if total <= 1.05 else total)
        left = ''
        if equation and '=' in equation:
            left = equation.split('=', 1)[0].strip()
        left = left or 'Y总'
        parameters = []
        terms = []
        for index, item in enumerate(candidates, start=1):
            weight = item['value'] / denominator
            value_text = f'{weight:.3f}'.rstrip('0').rstrip('.')
            gamma = f'γ{index}'
            parameters.append({
                'name': gamma,
                'meaning': f'{item["symbol"]}（{item["label"]}）权重',
                'value': value_text,
            })
            terms.append(f'{value_text}{item["symbol"]}')
        return f'{left}=' + '+'.join(terms), parameters

    @classmethod
    def _fallback_analysis_calculation(cls, rows, analysis_plan=None, analysis_parameters=None, target=None, title=''):
        plan = analysis_plan if isinstance(analysis_plan, dict) else {}
        merged_parameters = cls._merge_analysis_parameters(analysis_parameters or [], {}, rows)
        factor_expression, factor_parameters = cls._factor_expression_from_rows(rows, plan.get('equation') or '')
        if factor_parameters:
            merged_parameters = cls._merge_analysis_parameters(merged_parameters, {'analysisParameters': factor_parameters}, rows)
        expression = factor_expression or cls._substitute_equation_parameters(plan.get('equation') or '', merged_parameters)
        if not expression and plan.get('equation'):
            expression = cls._normalize_text(plan.get('equation'))
        table_label = cls._target_artifact_label(target or {}, 'table')
        method_note = ''
        if factor_expression:
            method_note = f'已根据{table_label}中贡献率换算各因子权重，并生成综合分析式。'
        elif expression and cls._parameter_value_map(merged_parameters):
            method_note = f'已将已填写或数据表中识别到的参数代入原关系式，生成可写入正文的分析式。'
        else:
            method_note = '已整理参数清单；仍为空的参数需要用户用推荐软件完成计算后填写。'
        return {
            'analysisParameters': merged_parameters,
            'analysisExpression': expression,
            'methodNote': method_note,
            'tableReferences': [table_label] if table_label else [],
            'dataRows': cls._public_rows(rows),
            'title': title or plan.get('title') or '',
        }

    def calculate_analysis_parameters(self, *, table_text='', target=None, analysis_plan=None, analysis_parameters=None, title='', unit=''):
        target = target or {}
        artifact_type = self._normalize_artifact_type(target.get('artifactType') or target.get('insertType'))
        table_role = self._normalize_table_role(
            target.get('tableRole') or target.get('role'),
            self._table_context_text(title or target.get('tableTitle') or target.get('chartTitle') or '', target),
        )
        if artifact_type == 'table' and table_role == 'impact_factors':
            return {
                'analysisParameters': [],
                'analysisExpression': '',
                'methodNote': '影响因素表是论文内容整理表，不需要计算参数或生成分析式。',
                'tableReferences': [],
            }
        plan = (
            self._normalize_analysis_plan_payload(analysis_plan, target, table_role)
            if isinstance(analysis_plan, dict) and analysis_plan
            else self._normalize_analysis_plan_payload(target.get('analysisPlan') or {}, target, table_role)
        )
        rows = self.parse_data_table(table_text, require_numeric=False)
        if artifact_type == 'table':
            rows = self._apply_article_variable_symbols(rows, {**target, 'tableRole': table_role}, table_text)
        fallback = self._fallback_analysis_calculation(rows, plan, analysis_parameters or target.get('analysisParameters') or [], target, title)
        if not self.api or not hasattr(self.api, 'call_json_sync'):
            return fallback
        rows_payload = [
            {
                'label': row.get('label', ''),
                'year': row.get('year', ''),
                'variable': row.get('variable', ''),
                'symbol': row.get('symbol', ''),
                'statType': row.get('statType', ''),
                'relatedVariable': row.get('relatedVariable', ''),
                'value': row.get('value'),
                'rawValue': row.get('rawValue', row.get('value', '')),
                'coefficient': row.get('coefficient', ''),
                'stdError': row.get('stdError', ''),
                'tStatistic': row.get('tStatistic', ''),
                'pValue': row.get('pValue', ''),
                'correlation': row.get('correlation', ''),
                'note': row.get('note', ''),
            }
            for row in rows
        ]
        try:
            payload = self.api.call_json_sync(
                f'''请根据用户已经审核或正在编辑的“分析数据表”计算论文参数，并生成最终可写入正文的分析式。

要求：
1. 只能使用数据表中已有数值、已填写参数和数据分析计划，不要编造缺失参数。
2. 如果表格是总方差解释、主成分、因子贡献率类结果，请把贡献率转换为小数权重；若正文明确要求综合得分且只保留部分因子，可按累计贡献率归一化，生成类似“Y总=0.671F1+0.149F2”的分析式。
3. 如果正文有关系式或回归方程，请把能确认的 β、γ、权重等参数代入；缺失参数保留为空并在 methodNote 中说明。
4. analysisParameters 要返回完整参数清单，已有参数也要保留；value 只填可以由表格或已填写信息确认的数值。
5. analysisExpression 是最后应写入论文正文的表达式，不要写数据来源，不要写 Markdown。

表题：{title or plan.get('title') or target.get('tableTitle') or '未提供'}
单位：{unit or '未注明'}
表格编号：{self._target_artifact_label(target, 'table')}
数据分析计划 JSON：
{json.dumps(plan, ensure_ascii=False, indent=2)}
当前参数 JSON：
{json.dumps(analysis_parameters or target.get('analysisParameters') or [], ensure_ascii=False, indent=2)}
分析数据表 JSON：
{json.dumps(rows_payload, ensure_ascii=False, indent=2)}

返回 JSON：
{{
  "analysisParameters": [
    {{"name": "γ1", "meaning": "F1 权重", "value": "0.671"}}
  ],
  "analysisExpression": "Y总=0.671F1+0.149F2",
  "methodNote": "说明这些参数如何由表格计算得出；若有缺失参数，说明需要用户用什么方法补算",
  "tableReferences": ["{self._target_artifact_label(target, 'table')}"]
}}''',
                system='你是论文计量分析与公式整理助手。输出必须是严格 JSON，只根据已给数据计算或整理参数。',
                temperature=0.05,
                max_tokens=1800,
                request_timeout=120,
                schema_name='data_chart_calculate_parameters',
                usage_context=self._usage_context('data_chart.calculate_parameters'),
            )
            if not isinstance(payload, dict):
                return fallback
            merged = self._merge_analysis_parameters(fallback.get('analysisParameters') or [], payload, rows)
            fallback_values = self._parameter_value_map(fallback.get('analysisParameters') or [])
            if fallback.get('analysisExpression') and fallback_values:
                for parameter in merged:
                    key = self._analysis_parameter_key(parameter)
                    value = str(parameter.get('value') or '').strip()
                    if key in fallback_values and ('%' in value or (self._parse_number(value) or 0) > 1):
                        parameter['value'] = fallback_values[key]
            expression = self._normalize_text(payload.get('analysisExpression') or payload.get('expression') or fallback.get('analysisExpression') or '')
            return {
                'analysisParameters': merged,
                'analysisExpression': expression,
                'methodNote': self._normalize_text(payload.get('methodNote') or payload.get('analysisMethodNote') or fallback.get('methodNote') or ''),
                'tableReferences': payload.get('tableReferences') if isinstance(payload.get('tableReferences'), list) else fallback.get('tableReferences', []),
                'dataRows': self._public_rows(rows),
                'title': title or plan.get('title') or '',
            }
        except Exception as exc:
            return {
                **fallback,
                'methodNote': f'{fallback.get("methodNote") or "已使用本地规则整理参数。"} AI 参数计算未返回：{exc}',
            }

    def _generate_table_result(self, rows, *, target=None, title='', unit='', chart_type='bar'):
        target = target or {}
        caption = self._build_table_caption(title)
        table_label = self._target_artifact_label(target, 'table')
        rows = self._sanitize_variable_table_rows(rows, caption, target)
        table_role = self._normalize_table_role(target.get('tableRole') or target.get('role'), self._table_context_text(caption, target))
        table_markdown = self._build_table_markdown(rows, caption, unit, table_label=table_label, target=target)
        replacement = self._build_table_replacement_text(target, caption, table_markdown, rows=rows, unit=unit, table_label=table_label)
        reference_entries = [] if table_role == 'impact_factors' else self._reference_entries_from_rows(rows)
        table_kind = self._public_table_kind(rows, caption, target)
        return {
            'artifactType': 'table',
            'tableRole': table_role,
            'tableKind': table_kind,
            'artifactLabel': table_label,
            'table': {
                'title': caption,
                'caption': caption,
                'unit': unit,
                'rows': rows,
                'tableKind': table_kind,
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
        kind = cls._table_kind(rows, '', {}) if rows else ''
        if kind in {'correlation', 'regression', 'descriptive', 'test_result'}:
            analysis = cls._table_stat_summary(rows, kind)
        else:
            analysis = cls._chart_analysis(rows, unit)
        if analysis:
            return analysis
        source_rows = cls._rows_without_numeric_values(rows)
        if source_rows:
            return cls._source_table_summary(source_rows)
        return ''

    @classmethod
    def _table_stat_summary(cls, rows, kind=''):
        if kind == 'correlation':
            headers, body = cls._build_correlation_rows(rows)
            if len(headers) >= 3 and body:
                variables = headers[1:]
                return f'相关系数矩阵围绕{"、".join(variables[:4])}等变量展开，用于比较变量之间的相关方向与强度。'
        if kind == 'regression':
            headers, body = cls._build_regression_rows(rows)
            if body:
                variables = [str(item[0]) for item in body if item and str(item[0]).strip()]
                return f'回归结果表展示了{"、".join(variables[:4])}等变量的系数、标准误和 t 统计量，可据此判断影响方向与统计显著性。'
        if kind == 'descriptive':
            headers, body = cls._build_descriptive_rows(rows)
            if body:
                variables = [str(item[0]) for item in body if item and str(item[0]).strip()]
                return f'描述性统计表汇总了{"、".join(variables[:4])}等变量的均值、标准差及取值范围，用于概括样本分布特征。'
        if kind == 'test_result':
            contribution_rows = [
                row for row in rows or []
                if cls._row_stat_type(row) in {'contributionRate', 'cumulativeRate', 'eigenvalue'}
            ]
            if contribution_rows:
                parts = []
                for row in contribution_rows:
                    stat = cls._row_stat_type(row)
                    if stat != 'contributionRate':
                        continue
                    variable = cls._row_variable_name(row) or cls._clean_variable_symbol(row.get('symbol', '')) or str(row.get('label', '') or '').strip()
                    value = cls._value_text(row)
                    if variable and value:
                        parts.append(f'{variable}贡献率为{value}%')
                    if len(parts) >= 3:
                        break
                if parts:
                    return f'总方差解释或因子结果表显示，{"，".join(parts)}，可据此确定综合评价式中的因子权重。'
                return '总方差解释或因子结果表汇总了特征根、贡献率和累计贡献率，可用于确定综合评价式中的因子权重。'
            return '检验结果表用于汇总统计检验、因子载荷或稳健性结果，以支撑后续模型设定和实证解释。'
        return ''

    @classmethod
    def _chart_analysis(cls, rows, unit=''):
        structure = cls._series_structure(rows)
        if structure:
            parts = cls._structured_series_analysis(structure, unit)
            if parts:
                return '；'.join(parts) + '。'
        numeric_rows = cls._numeric_rows(rows)
        values = [float(row['value']) for row in numeric_rows]
        if not values:
            return ''
        labels = [str(row.get('label', '') or f'项目{index + 1}') for index, row in enumerate(numeric_rows)]
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

    @staticmethod
    def _numeric_rows(rows):
        return [row for row in rows or [] if row.get('value') is not None]

    @staticmethod
    def _rows_without_numeric_values(rows):
        return [row for row in rows or [] if row.get('value') is None]

    @classmethod
    def _source_table_summary(cls, rows):
        labels = [
            str(row.get('label', '') or '').strip()
            for row in rows or []
            if str(row.get('label', '') or '').strip()
        ]
        sources = [
            cls._short_source_label(row)
            for row in rows or []
            if cls._short_source_label(row)
        ]
        label_part = '、'.join(labels[:4]) + ('等指标' if len(labels) > 4 else '')
        source_part = '、'.join(dict.fromkeys(sources[:3]))
        if label_part and source_part:
            return f'表中围绕{label_part}整理了可核验来源和口径说明，主要来源包括{source_part}。'
        if label_part:
            return f'表中围绕{label_part}整理了可核验来源和口径说明。'
        return '表中整理了后续测算所需的指标、来源和口径说明。'

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
        kind = cls._table_kind(rows, title, {})
        if kind == 'impact_factors' or '影响因素' in str(title or ''):
            factors = [
                cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
                for row in rows or []
                if cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
            ]
            factor_text = '、'.join(dict.fromkeys(factors[:6]))
            label = table_label or '表1'
            title_part = f'{label}所列的“{title}”' if title else f'{label}所列内容'
            if factor_text:
                return f'根据{title_part}，相关影响因素主要包括{factor_text}等方面，这些因素共同构成该问题的分析框架。'
            return f'根据{title_part}，该表对相关影响因素进行了结构化归纳，为后续论证提供分析框架。'
        analysis = cls._table_stat_summary(rows, kind) if kind in {'correlation', 'regression', 'descriptive', 'test_result'} else cls._chart_analysis(rows, unit)
        label = table_label or '表1'
        title_part = f'{label}所列的“{title}”' if title else f'{label}所列数据'
        if analysis:
            return f'根据{title_part}，{analysis}'
        source_summary = cls._source_table_summary(rows)
        return f'根据{title_part}，{source_summary}该表用于明确指标选取、数据核验路径和口径约束，为后续实证测度或比较分析提供依据。'

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
        text = re.sub(r'\s+', ' ', str(title or '').strip())
        text = re.sub(r'^\s*表\s*\d+(?:\.\d+)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\[[\d,\-\s]+\]\s*$', '', text)
        text = text.strip()
        if not text:
            text = self._clean_caption_title(title)
        text = re.sub(r'图表|数据图|统计图|图$', '表', text)
        text = re.sub(r'表{2,}$', '表', text)
        return text or '论文数据表'

    @staticmethod
    def _table_context_text(title='', target=None):
        target = target or {}
        return ' '.join(str(part or '') for part in (
            title,
            target.get('tableKind') if isinstance(target, dict) else '',
            target.get('tableTitle') if isinstance(target, dict) else '',
            target.get('chartTitle') if isinstance(target, dict) else '',
            target.get('dataNeed') if isinstance(target, dict) else '',
            target.get('intent') if isinstance(target, dict) else '',
            target.get('reason') if isinstance(target, dict) else '',
            target.get('query') if isinstance(target, dict) else '',
        ))

    @staticmethod
    def _explicit_definition_table_requested(text):
        compact = re.sub(r'\s+', '', str(text or ''))
        if not compact:
            return False
        return bool(re.search(
            r'(?:变量|指标|口径|来源|测度)(?:定义|说明)表|'
            r'(?:变量|指标)(?:代码|符号)表|'
            r'被解释变量.*解释变量|核心解释变量|控制变量|变量定义|变量说明|变量口径|口径说明表|数据来源表',
            compact,
        ))

    @staticmethod
    def _context_requests_numeric_table(text):
        compact = re.sub(r'\s+', '', str(text or ''))
        if not compact:
            return False
        return bool(re.search(
            r'数值|原始数据|指标值|年度|年份|省级|各省|面板|连续口径|'
            r'水平|规模|占比|比例|普及率|增长率|覆盖率|处理率|年限|供给|'
            r'综合评价|评价指标体系|指标体系|五个维度|正负向|权重',
            compact,
        ))

    @staticmethod
    def _markdown_table_escape(value):
        return str(value if value is not None else '').replace('|', '\\|').strip()

    @staticmethod
    def _variable_symbol_from_label(label):
        text = str(label or '')
        match = re.search(r'[（(]\s*([A-Za-z][A-Za-z0-9_]{1,18})\s*[）)]', text)
        return match.group(1) if match else ''

    @staticmethod
    def _label_without_variable_symbol(label):
        text = re.sub(r'\s+', '', str(label or '').strip())
        text = re.sub(r'[（(]\s*[A-Za-z][A-Za-z0-9_]{1,18}\s*[）)]', '', text)
        return text or str(label or '').strip()

    @classmethod
    def _looks_like_variable_definition_table(cls, rows, title='', target=None):
        context = cls._table_context_text(title, target)
        if re.search(r'描述性统计|相关系数|回归结果|均值|标准差|最大值|最小值|回归|稳健性|方差|检验结果', context):
            return False
        if cls._context_requests_numeric_table(context) and not cls._explicit_definition_table_requested(context):
            return False
        if cls._explicit_definition_table_requested(context):
            return True
        rows = rows or []
        if not rows:
            return False
        symbol_count = sum(1 for row in rows if cls._variable_symbol_for_row(row))
        note_text = ' '.join(str(row.get('note', '') or row.get('measure', '') or row.get('source', '') or '') for row in rows)
        return symbol_count / max(1, len(rows)) >= 0.45 and bool(re.search(r'测度指标|预期影响方向|变量|口径|单位|可核验', note_text))

    @staticmethod
    def _compact_table_cell(value, limit=36):
        text = re.sub(r'\s+', '', str(value or '').strip())
        text = re.sub(r'https?://\S+', '', text)
        text = text.strip('，,。.;； ')
        if len(text) <= limit:
            return text
        return text[:limit].rstrip('，,。.;；、') + '…'

    @classmethod
    def _compact_measure_note(cls, row, limit=42):
        note = re.sub(r'\s+', '', str(row.get('measure', '') or row.get('note', '') or row.get('source', '') or '').strip())
        if not note:
            return ''
        note = re.sub(r'变量符号(?:建议)?[:：]?[A-Za-z][A-Za-z0-9_]*(?:或[A-Za-z][A-Za-z0-9_]*)?[；;。]?', '', note)
        note = re.sub(r'控制变量[；;。]?', '', note)
        note = re.sub(r'^测度指标[:：]?', '', note)
        note = re.split(r'统计路径|可核验|数据来源|来源|预期影响方向|预测方向|建议核验|请核验', note, maxsplit=1)[0]
        note = re.sub(r'单位[:：][^；;。]*[；;。]?', '', note)
        note = note.strip('，,。.;； ')
        return cls._compact_table_cell(note, limit)

    @classmethod
    def _compact_source_for_table(cls, row, limit=32):
        source = cls._short_source_label(row)
        if not source:
            source = str(row.get('sourceName', '') or row.get('publisher', '') or row.get('url', '') or '').strip()
        source = re.sub(r'统计路径可核验为|可核验为|可核验|数据来源[:：]?', '', source)
        return cls._compact_table_cell(source, limit)

    @staticmethod
    def _value_text(row):
        raw_value = row.get('rawValue', row.get('value', ''))
        if raw_value == '' or raw_value is None:
            return ''
        return str(raw_value).strip()

    @classmethod
    def _variable_symbol_for_row(cls, row):
        symbol = str(row.get('symbol', '') or '').strip()
        if symbol:
            return symbol
        symbol = cls._variable_symbol_from_label(row.get('label', ''))
        if symbol:
            return symbol
        text = '；'.join(str(row.get(field, '') or '') for field in ('measure', 'note', 'source'))
        match = re.search(r'变量符号(?:建议)?[:：]?\s*([A-Za-z][A-Za-z0-9_]{1,18})', text)
        if match:
            return match.group(1)
        match = re.search(r'\b([A-Za-z][A-Za-z0-9_]{1,18})\s*(?:或|/|、)\s*[A-Za-z][A-Za-z0-9_]{1,18}', text)
        return match.group(1) if match else ''

    @staticmethod
    def _row_field_text(row, *fields):
        for field in fields:
            value = str(row.get(field, '') or '').strip()
            if value:
                return value
        return ''

    @classmethod
    def _normalize_table_kind(cls, value):
        text = re.sub(r'[\s_\-]+', '', str(value or '').strip().lower())
        aliases = {
            'numeric': 'numeric',
            'data': 'numeric',
            'rawdata': 'numeric',
            'wide': 'numeric',
            '数值': 'numeric',
            '数据': 'numeric',
            '原始数据': 'numeric',
            '年度数据': 'numeric',
            'impact': 'impact_factors',
            'impactfactors': 'impact_factors',
            'impactfactor': 'impact_factors',
            'influencefactors': 'impact_factors',
            '影响因素': 'impact_factors',
            '影响因素表': 'impact_factors',
            'definition': 'definition',
            'variabledefinition': 'definition',
            '变量定义': 'definition',
            '变量说明': 'definition',
            '指标定义': 'definition',
            'source': 'source',
            '口径': 'source',
            '来源': 'source',
            'correlation': 'correlation',
            'corr': 'correlation',
            '相关': 'correlation',
            '相关系数': 'correlation',
            '相关矩阵': 'correlation',
            'regression': 'regression',
            '回归': 'regression',
            '回归结果': 'regression',
            '回归分析': 'regression',
            'descriptive': 'descriptive',
            'descriptivestatistics': 'descriptive',
            '描述性统计': 'descriptive',
            '描述统计': 'descriptive',
            'testresult': 'test_result',
            'test': 'test_result',
            '检验结果': 'test_result',
        }
        return aliases.get(text, '')

    @classmethod
    def _normalize_stat_type(cls, value):
        text = re.sub(r'[\s_\-：:]+', '', str(value or '').strip().lower())
        aliases = {
            'value': 'value',
            '数值': 'value',
            '观测值': 'value',
            'samplesize': 'sampleSize',
            'sample': 'sampleSize',
            'observations': 'sampleSize',
            'n': 'sampleSize',
            '样本量': 'sampleSize',
            '观测数': 'sampleSize',
            'mean': 'mean',
            'average': 'mean',
            '均值': 'mean',
            '平均值': 'mean',
            'stddev': 'stdDev',
            'std': 'stdDev',
            'standarddeviation': 'stdDev',
            '标准差': 'stdDev',
            'min': 'min',
            'minimum': 'min',
            '最小值': 'min',
            'max': 'max',
            'maximum': 'max',
            '最大值': 'max',
            'coefficient': 'coefficient',
            'coef': 'coefficient',
            'beta': 'coefficient',
            '系数': 'coefficient',
            '回归系数': 'coefficient',
            '估计系数': 'coefficient',
            'stderr': 'stdError',
            'stderror': 'stdError',
            'standarderror': 'stdError',
            'se': 'stdError',
            '标准误': 'stdError',
            '标准误差': 'stdError',
            'tstatistic': 'tStatistic',
            'tstat': 'tStatistic',
            'tvalue': 'tStatistic',
            't': 'tStatistic',
            't统计量': 'tStatistic',
            't值': 'tStatistic',
            'pvalue': 'pValue',
            'p': 'pValue',
            'p值': 'pValue',
            'significance': 'significance',
            'stars': 'significance',
            '显著性': 'significance',
            '星号': 'significance',
            'correlation': 'correlation',
            'corr': 'correlation',
            '相关系数': 'correlation',
            '相关性': 'correlation',
            'eigenvalue': 'eigenvalue',
            'eigen': 'eigenvalue',
            '特征根': 'eigenvalue',
            'variancecontribution': 'contributionRate',
            'contributionrate': 'contributionRate',
            'variancerate': 'contributionRate',
            '因子贡献率': 'contributionRate',
            '贡献率': 'contributionRate',
            'cumulativecontribution': 'cumulativeRate',
            'cumulativerate': 'cumulativeRate',
            'cumcontribution': 'cumulativeRate',
            '累计贡献率': 'cumulativeRate',
            '累计方差贡献率': 'cumulativeRate',
        }
        return aliases.get(text, '')

    @classmethod
    def _stat_type_from_label(cls, label):
        text = re.sub(r'\s+', '', str(label or '').strip())
        suffixes = (
            ('相关系数', 'correlation'),
            ('回归系数', 'coefficient'),
            ('估计系数', 'coefficient'),
            ('标准误差', 'stdError'),
            ('标准误', 'stdError'),
            ('标准差', 'stdDev'),
            ('t统计量', 'tStatistic'),
            ('t值', 'tStatistic'),
            ('P值', 'pValue'),
            ('p值', 'pValue'),
            ('显著性', 'significance'),
            ('样本量', 'sampleSize'),
            ('观测数', 'sampleSize'),
            ('均值', 'mean'),
            ('平均值', 'mean'),
            ('最小值', 'min'),
            ('最大值', 'max'),
            ('累计方差贡献率', 'cumulativeRate'),
            ('累计贡献率', 'cumulativeRate'),
            ('因子贡献率', 'contributionRate'),
            ('贡献率', 'contributionRate'),
            ('特征根', 'eigenvalue'),
            ('系数', 'coefficient'),
        )
        for suffix, stat in suffixes:
            if text.endswith(suffix):
                return stat
        return ''

    @classmethod
    def _row_stat_type(cls, row):
        explicit = cls._normalize_stat_type(row.get('statType', ''))
        if explicit:
            return explicit
        note_match = re.search(r'统计项[:：]\s*([^；;，,。]+)', str(row.get('note', '') or ''))
        if note_match:
            stat = cls._normalize_stat_type(note_match.group(1))
            if stat:
                return stat
        return cls._stat_type_from_label(row.get('label', ''))

    @classmethod
    def _strip_stat_suffix(cls, label):
        text = re.sub(r'\s+', '', str(label or '').strip())
        for suffix in (
            '相关系数', '回归系数', '估计系数', '标准误差', '标准误', '标准差',
            't统计量', 't值', 'P值', 'p值', '显著性', '样本量', '观测数',
            '均值', '平均值', '最小值', '最大值', '系数',
            '累计方差贡献率', '累计贡献率', '因子贡献率', '贡献率', '特征根',
        ):
            if text.endswith(suffix):
                return text[:-len(suffix)]
        return text

    @classmethod
    def _row_year(cls, row):
        year = str(row.get('year', '') or '').strip()
        if year:
            match = re.search(r'((?:19|20)\d{2})', year)
            return f'{match.group(1)}年' if match else year
        year, _ = cls._split_label_axis(row.get('label', ''))
        return year

    @classmethod
    def _row_variable_name(cls, row):
        variable = str(
            row.get('variable', '')
            or row.get('variableName', '')
            or row.get('indicatorName', '')
            or ''
        ).strip()
        if variable:
            return cls._compact_table_cell(variable, 28)
        label = cls._strip_stat_suffix(cls._label_without_variable_symbol(row.get('label', '')))
        year, series = cls._split_label_axis(label)
        name = series if year else label
        related = cls._row_related_variable(row)
        if cls._row_stat_type(row) == 'correlation' and related:
            parts = re.split(r'[-—–~至和与、/]+', name, maxsplit=1)
            if parts and parts[0].strip():
                name = parts[0]
        return cls._compact_table_cell(name, 28)

    @classmethod
    def _row_related_variable(cls, row):
        related = str(row.get('relatedVariable', '') or row.get('relatedLabel', '') or '').strip()
        if related:
            return cls._compact_table_cell(related, 24)
        label = cls._strip_stat_suffix(cls._label_without_variable_symbol(row.get('label', '')))
        parts = [part.strip() for part in re.split(r'[-—–~至和与、/]+', label, maxsplit=1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return cls._compact_table_cell(parts[1], 24)
        return ''

    @staticmethod
    def _clean_variable_symbol(value):
        text = re.sub(r'\s+', '', str(value or '').strip())
        text = text.replace('-', '_').replace('－', '_')
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,18}', text):
            return text
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,8}[\u4e00-\u9fff]{1,4}', text):
            return text
        return ''

    @staticmethod
    def _normalize_variable_meaning(value):
        text = re.sub(r'\s+', '', str(value or '').strip())
        text = re.sub(r'^(?:变量|指标|被解释变量|解释变量|核心解释变量|控制变量)', '', text)
        text = text.strip('：:，,。.;；（）()[]【】')
        return text[:40]

    @classmethod
    def _variable_definitions_from_text(cls, text, limit=80):
        normalized = cls._normalize_text(text)
        if not normalized:
            return []
        records = []
        seen = set()

        def add(symbol, meaning):
            symbol = cls._clean_variable_symbol(symbol)
            meaning = cls._normalize_variable_meaning(meaning)
            if not symbol or not meaning:
                return
            if re.fullmatch(r'\d+(?:\.\d+)?', meaning):
                return
            if len(meaning) < 2 or not re.search(r'[\u4e00-\u9fffA-Za-z]', meaning):
                return
            key = (symbol, meaning)
            if key in seen:
                return
            seen.add(key)
            records.append({'symbol': symbol, 'meaning': meaning})

        symbol = r'([A-Za-z][A-Za-z0-9_\u4e00-\u9fff]{0,18})'
        meaning = r'([\u4e00-\u9fffA-Za-z0-9（）()、]{2,42})'
        patterns = [
            rf'{meaning}[（(]\s*{symbol}\s*[）)]',
            rf'{meaning}(?:记为|表示为|定义为|用|以)\s*{symbol}',
            rf'{symbol}\s*(?:表示|代表|反映|衡量|为)\s*{meaning}',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, normalized):
                groups = match.groups()
                if len(groups) != 2:
                    continue
                if cls._clean_variable_symbol(groups[0]):
                    add(groups[0], groups[1])
                else:
                    add(groups[1], groups[0])
                if len(records) >= limit:
                    return records
        return records

    @classmethod
    def _variable_definition_context(cls, target=None, extra_text=''):
        target = target or {}
        parts = [
            extra_text,
            target.get('variableDefinitionContext', '') if isinstance(target, dict) else '',
            target.get('fullText', '') if isinstance(target, dict) else '',
            target.get('originalText', '') if isinstance(target, dict) else '',
            target.get('excerpt', '') if isinstance(target, dict) else '',
            target.get('dataNeed', '') if isinstance(target, dict) else '',
            target.get('intent', '') if isinstance(target, dict) else '',
            target.get('reason', '') if isinstance(target, dict) else '',
        ]
        return '\n'.join(str(part or '') for part in parts if str(part or '').strip())

    @classmethod
    def _apply_article_variable_symbols(cls, rows, target=None, extra_text=''):
        role = cls._normalize_table_role((target or {}).get('tableRole', '') if isinstance(target, dict) else '', cls._table_context_text('', target))
        if role == 'impact_factors':
            return rows or []
        definitions = cls._variable_definitions_from_text(cls._variable_definition_context(target, extra_text))
        if not definitions:
            return rows or []
        result = []
        for row in rows or []:
            item = dict(row)
            haystack = cls._normalize_variable_meaning(' '.join(str(item.get(field, '') or '') for field in ('label', 'variable', 'measure', 'note')))
            current_symbol = cls._clean_variable_symbol(item.get('symbol') or item.get('variable'))
            current_is_defined = any(current_symbol and current_symbol == definition['symbol'] for definition in definitions)
            if not current_is_defined:
                matched = None
                for definition in definitions:
                    meaning = definition['meaning']
                    if meaning and (meaning in haystack or haystack in meaning):
                        matched = definition
                        break
                if matched:
                    item['symbol'] = matched['symbol']
                    item['variable'] = matched['symbol']
                    item['variableMeaning'] = matched['meaning']
            result.append(item)
        return result

    @classmethod
    def _symbol_from_row(cls, row):
        for field in ('symbol', 'variable', 'relatedVariable', 'relatedLabel'):
            symbol = cls._clean_variable_symbol(row.get(field, ''))
            if symbol:
                return symbol
        label_symbol = cls._variable_symbol_from_label(row.get('label', ''))
        return cls._clean_variable_symbol(label_symbol)

    @classmethod
    def _symbolic_variable_map(cls, rows, role='evidence_data'):
        mapping = {}
        used = set()
        counter = 1
        prefix = 'F' if role == 'variable_analysis' else 'X'
        for row in rows or []:
            for name in (cls._row_variable_name(row), cls._row_related_variable(row)):
                key = re.sub(r'\s+', '', str(name or '').strip())
                if not key or key in mapping:
                    continue
                symbol = cls._clean_variable_symbol(name)
                if not symbol and key == re.sub(r'\s+', '', str(row.get('variable', '') or '').strip()):
                    symbol = cls._clean_variable_symbol(row.get('symbol', ''))
                if not symbol or symbol in used:
                    symbol = f'{prefix}{counter}'
                    counter += 1
                    while symbol in used:
                        symbol = f'{prefix}{counter}'
                        counter += 1
                mapping[key] = symbol
                used.add(symbol)
        return mapping

    @classmethod
    def _symbolic_name(cls, value, symbol_map, fallback_prefix='X'):
        key = re.sub(r'\s+', '', str(value or '').strip())
        if not key:
            return ''
        direct = cls._clean_variable_symbol(key)
        if direct:
            return direct
        if key in symbol_map:
            return symbol_map[key]
        return f'{fallback_prefix}{len(symbol_map) + 1}'

    @classmethod
    def _table_kind_hint(cls, rows, title='', target=None):
        explicit = cls._normalize_table_kind((target or {}).get('tableKind', '') if isinstance(target, dict) else '')
        role = cls._normalize_table_role((target or {}).get('tableRole', '') if isinstance(target, dict) else '', cls._table_context_text(title, target))
        if role == 'impact_factors':
            return 'impact_factors'
        if role == 'model_index':
            return 'numeric'
        if explicit:
            if explicit in {'definition', 'source'}:
                context = cls._table_context_text(title, target)
                if not cls._explicit_definition_table_requested(context) and cls._context_requests_numeric_table(context):
                    return 'numeric'
            return explicit
        context = cls._table_context_text(title, target)
        if re.search(r'相关系数|相关矩阵|相关性', context, flags=re.IGNORECASE):
            return 'correlation'
        if re.search(r'回归结果|回归分析|参数估计|估计结果|稳健性|模型结果|回归系数', context, flags=re.IGNORECASE):
            return 'regression'
        if re.search(r'总方差|方差解释|KMO|Bartlett|因子载荷|检验结果|贡献率|主成分|特征根|累计方差', context, flags=re.IGNORECASE):
            return 'test_result'
        if re.search(r'描述性统计|描述统计|描述性分析|样本统计|均值|标准差|最大值|最小值', context, flags=re.IGNORECASE):
            return 'descriptive'
        if role == 'variable_analysis':
            return 'test_result'
        if cls._context_requests_numeric_table(context):
            return 'numeric'
        if cls._explicit_definition_table_requested(context):
            return 'definition'
        if re.search(r'数据表|统计表|指标值|年度|省级|面板|原始数值|水平|规模|占比|比例|率|均衡|差异', context):
            return 'numeric'
        return ''

    @classmethod
    def _table_kind(cls, rows, title='', target=None):
        hinted = cls._table_kind_hint(rows, title, target)
        if hinted:
            return hinted
        if cls._looks_like_variable_definition_table(rows, title, target):
            return 'definition'
        stat_types = {cls._row_stat_type(row) for row in rows or []}
        if any(cls._row_field_text(row, 'coefficient', 'stdError', 'tStatistic', 'pValue', 'significance') for row in rows or []) or stat_types.intersection({'coefficient', 'stdError', 'tStatistic', 'pValue', 'significance'}):
            return 'regression'
        if any(cls._row_field_text(row, 'correlation', 'relatedLabel', 'relatedVariable') for row in rows or []) or 'correlation' in stat_types:
            return 'correlation'
        if stat_types.intersection({'eigenvalue', 'contributionRate', 'cumulativeRate'}):
            return 'test_result'
        if any(cls._row_field_text(row, 'mean', 'stdDev', 'min', 'max', 'sampleSize') for row in rows or []) or stat_types.intersection({'mean', 'stdDev', 'min', 'max', 'sampleSize'}):
            return 'descriptive'
        if len(cls._numeric_rows(rows)) >= 1:
            return 'numeric'
        return 'source'

    @classmethod
    def _public_table_kind(cls, rows, title='', target=None):
        kind = cls._table_kind(rows, title, target)
        return kind if kind in {'impact_factors', 'definition', 'numeric', 'source', 'descriptive', 'correlation', 'regression', 'test_result'} else 'numeric'

    @classmethod
    def _preferred_table_kind(cls, rows=None, title='', target=None):
        target_for_hint = target
        if isinstance(target, dict) and str(target.get('tableKind', '') or '').strip().lower() == 'source':
            target_for_hint = {**target, 'tableKind': ''}
        kind = cls._table_kind_hint(rows or [], title, target_for_hint)
        if kind and kind != 'source':
            return kind
        inferred = cls._table_kind(rows or [], title, target_for_hint)
        if inferred != 'source':
            return inferred
        context = cls._table_context_text(title, target_for_hint)
        if re.search(r'数据表|统计表|指标值|年度|省级|面板|原始数值|水平|规模|占比|比例|率|均衡|差异', context):
            return 'numeric'
        return 'source'

    @classmethod
    def _coerce_stat_value(cls, value):
        text = str(value if value is not None else '').strip()
        if text:
            return text
        parsed = cls._parse_number(value)
        return f'{parsed:g}' if parsed is not None else ''

    @classmethod
    def _descriptive_value(cls, row, field):
        if field == 'sampleSize':
            value = cls._row_field_text(row, 'sampleSize')
            if value:
                return value
            return '1' if row.get('value') is not None else ''
        if field == 'mean':
            value = cls._row_field_text(row, 'mean')
            return value or cls._value_text(row)
        aliases = {
            'stdDev': ('stdDev',),
            'min': ('min',),
            'max': ('max',),
        }
        return cls._row_field_text(row, *aliases.get(field, (field,)))

    @classmethod
    def _build_descriptive_rows(cls, rows, symbol_only=True, role='variable_analysis'):
        symbol_map = cls._symbolic_variable_map(rows, role) if symbol_only else {}
        grouped = {}
        order = []
        for row in rows or []:
            variable = cls._row_variable_name(row)
            if not variable:
                continue
            if variable not in grouped:
                grouped[variable] = {'变量': cls._symbolic_name(variable, symbol_map) if symbol_only else variable}
                order.append(variable)
            stat_type = cls._row_stat_type(row)
            value = cls._value_text(row)
            if stat_type == 'value' and value:
                stat_type = 'mean'
            if stat_type in {'sampleSize', 'mean', 'stdDev', 'min', 'max'} and value:
                grouped[variable][stat_type] = value
            for field in ('sampleSize', 'mean', 'stdDev', 'min', 'max'):
                field_value = cls._row_field_text(row, field)
                if field_value:
                    grouped[variable][field] = field_value
        if grouped:
            body = [
                [
                    grouped[name].get('变量', name),
                    grouped[name].get('sampleSize', ''),
                    grouped[name].get('mean', ''),
                    grouped[name].get('stdDev', ''),
                    grouped[name].get('min', ''),
                    grouped[name].get('max', ''),
                ]
                for name in order
            ]
            if any(any(cell for cell in row[1:]) for row in body):
                return ['Var', 'N', 'Mean', 'SD', 'Min', 'Max'], body
        body = []
        for row in rows or []:
            variable = cls._row_variable_name(row) or cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
            variable = cls._symbolic_name(variable, symbol_map) if symbol_only else variable
            sample_size = cls._descriptive_value(row, 'sampleSize')
            mean = cls._descriptive_value(row, 'mean')
            std_dev = cls._descriptive_value(row, 'stdDev')
            min_value = cls._descriptive_value(row, 'min')
            max_value = cls._descriptive_value(row, 'max')
            if not any((variable, sample_size, mean, std_dev, min_value, max_value)):
                continue
            body.append([variable, sample_size, mean, std_dev, min_value, max_value])
        return ['Var', 'N', 'Mean', 'SD', 'Min', 'Max'], body

    @classmethod
    def _build_regression_rows(cls, rows, symbol_only=True, role='variable_analysis'):
        symbol_map = cls._symbolic_variable_map(rows, role) if symbol_only else {}
        grouped = {}
        order = []
        for row in rows or []:
            variable = cls._row_variable_name(row)
            if not variable:
                continue
            if variable not in grouped:
                grouped[variable] = {'变量': cls._symbolic_name(variable, symbol_map) if symbol_only else variable}
                order.append(variable)
            stat_type = cls._row_stat_type(row)
            value = cls._value_text(row)
            if stat_type == 'value' and value:
                stat_type = 'coefficient'
            if stat_type in {'coefficient', 'stdError', 'tStatistic', 'pValue', 'significance'} and value:
                grouped[variable][stat_type] = value
            for field in ('coefficient', 'stdError', 'tStatistic', 'pValue', 'significance'):
                field_value = cls._row_field_text(row, field)
                if field_value:
                    grouped[variable][field] = field_value
        if grouped:
            body = []
            for name in order:
                item = grouped[name]
                coefficient = item.get('coefficient', '')
                significance = item.get('significance', '')
                if coefficient and significance and not coefficient.endswith(significance):
                    coefficient = f'{coefficient}{significance}'
                body.append([
                    item.get('变量', name),
                    coefficient,
                    item.get('stdError', ''),
                    item.get('tStatistic', ''),
                    item.get('pValue', ''),
                ])
            if any(any(cell for cell in row[1:]) for row in body):
                return ['Var', 'Coef', 'SE', 't', 'p'], body
        body = []
        for row in rows or []:
            variable = cls._row_variable_name(row) or cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
            variable = cls._symbolic_name(variable, symbol_map) if symbol_only else variable
            coefficient = cls._row_field_text(row, 'coefficient') or cls._value_text(row)
            significance = cls._row_field_text(row, 'significance')
            if significance and coefficient and not coefficient.endswith(significance):
                coefficient = f'{coefficient}{significance}'
            std_error = cls._row_field_text(row, 'stdError')
            t_statistic = cls._row_field_text(row, 'tStatistic')
            p_value = cls._row_field_text(row, 'pValue')
            if not any((variable, coefficient, std_error, t_statistic, p_value)):
                continue
            body.append([variable, coefficient, std_error, t_statistic, p_value])
        return ['Var', 'Coef', 'SE', 't', 'p'], body

    @classmethod
    def _build_correlation_rows(cls, rows, symbol_only=True, role='variable_analysis'):
        symbol_map = cls._symbolic_variable_map(rows, role) if symbol_only else {}
        variables = []
        pairs = {}
        for row in rows or []:
            left_name = cls._compact_table_cell(cls._row_variable_name(row), 22)
            right_name = cls._compact_table_cell(cls._row_related_variable(row), 22)
            left = cls._symbolic_name(left_name, symbol_map) if symbol_only else left_name
            right = cls._symbolic_name(right_name, symbol_map) if symbol_only and right_name else right_name
            value = cls._row_field_text(row, 'correlation') or cls._value_text(row)
            if not left:
                continue
            if left not in variables:
                variables.append(left)
            if right and right not in variables:
                variables.append(right)
            if right and value:
                pairs[(left, right)] = value
                pairs[(right, left)] = value
        if len(variables) >= 2 and pairs:
            body = []
            for left in variables:
                row = [left]
                for right in variables:
                    row.append('1.000' if left == right else pairs.get((left, right), ''))
                body.append(row)
            return ['Var'] + variables, body
        numeric_rows = cls._numeric_rows(rows)
        if len(numeric_rows) >= 2:
            body = []
            for row in numeric_rows:
                body.append([
                    cls._compact_table_cell(row.get('label', ''), 22),
                    cls._compact_table_cell(row.get('relatedLabel', '') or '相关变量', 22),
                    cls._value_text(row),
                ])
            return ['Var', 'Var2', 'r'], body
        headers, body = cls._numeric_pivot_table(rows)
        return headers, body

    @classmethod
    def _build_test_result_rows(cls, rows, symbol_only=True, role='variable_analysis'):
        symbol_map = cls._symbolic_variable_map(rows, role) if symbol_only else {}
        grouped = {}
        order = []
        for row in rows or []:
            item_name = cls._row_variable_name(row) or cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
            item = cls._symbolic_name(item_name, symbol_map, fallback_prefix='F') if symbol_only else item_name
            if not item:
                continue
            if item not in grouped:
                grouped[item] = {'F': item}
                order.append(item)
            stat_type = cls._row_stat_type(row)
            value = cls._value_text(row)
            if stat_type in {'eigenvalue', 'contributionRate', 'cumulativeRate'} and value:
                grouped[item][stat_type] = value
            for field in ('eigenvalue', 'contributionRate', 'cumulativeRate'):
                field_value = cls._row_field_text(row, field)
                if field_value:
                    grouped[item][field] = field_value
        if grouped:
            body = [
                [
                    grouped[item].get('F', item),
                    grouped[item].get('eigenvalue', ''),
                    grouped[item].get('contributionRate', ''),
                    grouped[item].get('cumulativeRate', ''),
                ]
                for item in order
            ]
            if any(any(cell for cell in row[1:]) for row in body):
                return ['F', 'Eigen', 'Var%', 'Cum%'], body
        body = []
        for row in rows or []:
            item_name = cls._row_variable_name(row) or cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 28)
            item = cls._symbolic_name(item_name, symbol_map, fallback_prefix='F') if symbol_only else item_name
            statistic = cls._row_field_text(row, 'coefficient', 'tStatistic', 'correlation') or cls._value_text(row)
            p_value = cls._row_field_text(row, 'pValue')
            if not any((item, statistic, p_value)):
                continue
            body.append([item, statistic, p_value])
        return ['Var', 'Stat', 'p'], body

    @classmethod
    def _numeric_pivot_table(cls, rows, symbol_only=False, role='evidence_data'):
        numeric_rows = cls._numeric_rows(rows)
        pivot_rows = rows or numeric_rows
        symbol_map = cls._symbolic_variable_map(pivot_rows, role) if symbol_only else {}
        years = []
        series = []
        points = {}
        for row in pivot_rows:
            year = cls._row_year(row)
            name = cls._row_variable_name(row)
            display_name = cls._symbolic_name(name, symbol_map) if symbol_only else name
            value = cls._value_text(row)
            if not year or not name:
                continue
            if cls._row_stat_type(row) not in {'', 'value'}:
                continue
            if year not in years:
                years.append(year)
            if display_name not in series:
                series.append(display_name)
            points[(year, display_name)] = value
        if years and series and (len(years) >= 2 or len(series) >= 2):
            years.sort(key=lambda item: int(re.search(r'\d{4}', item).group(0)) if re.search(r'\d{4}', item) else 0)
            headers = ['年份'] + series
            body = [[year.replace('年', '')] + [points.get((year, name), '') for name in series] for year in years]
            return headers, body
        structure = cls._series_structure(numeric_rows)
        if structure:
            raw_series = list(structure.get('series') or [])
            mapped_series = [cls._symbolic_name(name, symbol_map) if symbol_only else name for name in raw_series]
            headers = ['年份'] + mapped_series
            body = []
            values = structure.get('values') or {}
            for year in structure.get('years') or []:
                body.append([year.replace('年', '')] + [
                    f'{values.get(series, {}).get(year):g}' if year in values.get(series, {}) else ''
                    for series in raw_series
                ])
            return headers, body
        labels = [str(row.get('label', '') or '') for row in numeric_rows]
        years = []
        series = []
        points = {}
        for row in numeric_rows:
            year, name = cls._split_label_axis(row.get('label', ''))
            if not year:
                continue
            if year not in years:
                years.append(year)
            if name not in series:
                series.append(name)
            points[(year, name)] = cls._value_text(row)
        if len(years) >= 2 and series:
            years.sort(key=lambda item: int(re.search(r'\d{4}', item).group(0)) if re.search(r'\d{4}', item) else 0)
            mapped_series = [cls._symbolic_name(name, symbol_map) if symbol_only else name for name in series]
            headers = ['年份'] + mapped_series
            body = [[year.replace('年', '')] + [points.get((year, name), '') for name in series] for year in years]
            return headers, body
        source_rows = rows or numeric_rows
        return (['Var', 'Value'] if symbol_only else ['指标/数据项', '数值']), [
            [
                (cls._symbolic_name(cls._row_variable_name(row) or row.get('label', ''), symbol_map) if symbol_only else row.get('label', '')),
                cls._value_text(row) if row.get('value') is not None or str(row.get('rawValue', '') or '').strip() else '',
            ]
            for row in source_rows
            if str(row.get('label', '') or '').strip() or row.get('value') is not None or str(row.get('rawValue', '') or '').strip()
        ]

    @classmethod
    def _build_variable_definition_rows(cls, rows):
        body = []
        for row in rows or []:
            label = cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 26)
            symbol = cls._compact_table_cell(cls._variable_symbol_for_row(row), 18)
            measure = cls._compact_measure_note(row, 52)
            if not any((label, symbol, measure)):
                continue
            body.append([label, symbol, measure])
        return ['指标/变量', '变量符号', '测度方法'], body

    @classmethod
    def _build_impact_factor_rows(cls, rows):
        factors = []
        seen = set()
        for row in rows or []:
            label = cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 32)
            if not label:
                continue
            for part in re.split(r'[、,，；;]\s*', label):
                item = cls._compact_table_cell(part, 28)
                if item and item not in seen:
                    factors.append(item)
                    seen.add(item)
        return ['影响因素'], [[factor] for factor in factors]

    @classmethod
    def _build_source_rows_for_paper(cls, rows):
        body = []
        for row in rows or []:
            label = cls._compact_table_cell(cls._label_without_variable_symbol(row.get('label', '')), 26)
            note = cls._compact_measure_note(row, 52) or cls._compact_table_cell(row.get('note', ''), 52)
            if not any((label, note)):
                continue
            body.append([label, note])
        return ['指标/变量', '口径说明'], body

    @classmethod
    def _paper_table_headers_and_rows(cls, rows, title='', target=None):
        kind = cls._table_kind(rows, title, target)
        role = cls._normalize_table_role((target or {}).get('tableRole', '') if isinstance(target, dict) else '', cls._table_context_text(title, target))
        symbol_only = role != 'impact_factors'
        if kind == 'impact_factors':
            headers, body = cls._build_impact_factor_rows(rows)
        elif kind == 'definition':
            headers, body = cls._build_variable_definition_rows(rows)
        elif kind == 'descriptive':
            headers, body = cls._build_descriptive_rows(rows, symbol_only=symbol_only, role=role)
        elif kind == 'correlation':
            headers, body = cls._build_correlation_rows(rows, symbol_only=symbol_only, role=role)
        elif kind == 'regression':
            headers, body = cls._build_regression_rows(rows, symbol_only=symbol_only, role=role)
        elif kind == 'test_result':
            headers, body = cls._build_test_result_rows(rows, symbol_only=symbol_only, role=role)
        elif kind == 'numeric':
            headers, body = cls._numeric_pivot_table(rows, symbol_only=symbol_only, role=role)
        else:
            headers, body = cls._build_source_rows_for_paper(rows)
        return kind, headers, body

    @classmethod
    def _sanitize_variable_table_rows(cls, rows, title='', target=None):
        if cls._table_kind(rows, title, target) not in {'definition', 'impact_factors'}:
            return rows
        sanitized = []
        for row in rows or []:
            item = dict(row)
            item['value'] = None
            item['rawValue'] = ''
            sanitized.append(item)
        return sanitized

    @classmethod
    def _source_note_text(cls, rows):
        sources = []
        seen = set()
        total_unique = 0
        for row in rows or []:
            if not any(str(row.get(field, '') or '').strip() for field in ('sourceName', 'publisher', 'url')):
                continue
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
    def _build_table_markdown(cls, rows, title, unit='', table_label='表1', target=None):
        lines = [f'{table_label or "表1.1"} {title}']
        table_kind, headers, body = cls._paper_table_headers_and_rows(rows, title, target)
        if not headers:
            role = cls._normalize_table_role((target or {}).get('tableRole', '') if isinstance(target, dict) else '', cls._table_context_text(title, target))
            headers = ['影响因素'] if role == 'impact_factors' else ['Var', 'Value']
        if unit and table_kind in {'numeric', 'descriptive', 'correlation', 'regression', 'test_result'}:
            lines.append(f'（单位：{unit}）')
        align = ['---'] * len(headers)
        if table_kind in {'numeric', 'descriptive', 'correlation', 'regression', 'test_result'} and len(align) > 1:
            align = ['---'] + ['---:'] * (len(headers) - 1)
        lines.append('| ' + ' | '.join(cls._markdown_table_escape(header) for header in headers) + ' |')
        lines.append('| ' + ' | '.join(align) + ' |')
        if not body:
            body = [[''] * len(headers)]
        for row in body:
            lines.append('| ' + ' | '.join(cls._markdown_table_escape(cell) for cell in row) + ' |')
        if table_kind == 'regression':
            note = next((str(row.get('note', '') or '').strip() for row in rows or [] if str(row.get('note', '') or '').strip()), '')
            lines.append(f'注：{note or "*、**、***分别表示在10%、5%、1%的水平上显著。"}')
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
        table_role = self._normalize_table_role(target.get('tableRole') or target.get('role'), self._table_context_text(caption, target))
        analysis_plan = target.get('analysisPlan') if isinstance(target, dict) and isinstance(target.get('analysisPlan'), dict) else None
        analysis_parameters = target.get('analysisParameters') if isinstance(target, dict) and isinstance(target.get('analysisParameters'), list) else []
        analysis_expression = self._normalize_text(target.get('analysisExpression', '') if isinstance(target, dict) else '')
        analysis_method_note = self._normalize_text(target.get('analysisMethodNote', '') if isinstance(target, dict) else '')
        analysis_note = ''
        if analysis_plan or analysis_parameters or analysis_expression:
            analysis_note = json.dumps({
                'analysisPlan': analysis_plan or {},
                'analysisParameters': analysis_parameters,
                'analysisExpression': analysis_expression,
                'analysisMethodNote': analysis_method_note,
            }, ensure_ascii=False, indent=2)
        has_numeric = bool(self._numeric_rows(rows))
        analysis_text = self._chart_analysis(rows, unit)
        data_points_text = self._data_points_text(rows)
        if self.api and hasattr(self.api, 'call_sync') and original:
            if table_role == 'impact_factors':
                system = (
                    f'你是严谨的论文写作助手。请根据论文已有观点和影响因素归纳表改写原段落，必须自然引入{table_label}。'
                    '该表是对正文内容的结构化整理，不是外部统计数据，不要写数据来源、URL、参考文献或引用编号。'
                )
            else:
                system = (
                    f'你是严谨的论文写作助手。请根据已确认的数据表改写原段落，必须自然引入{table_label}。'
                    '数据来源由系统写入参考文献并用引用编号标注，正文不要写来源名称、URL或“数据来源为”。'
                )
            rows_payload = [
                {
                    'label': row.get('label'),
                    'year': row.get('year', ''),
                    'variable': row.get('variable', ''),
                    'symbol': row.get('symbol', ''),
                    'statType': row.get('statType', ''),
                    'relatedVariable': row.get('relatedVariable', ''),
                    'value': row.get('value'),
                    'rawValue': row.get('rawValue', row.get('value')),
                    'coefficient': row.get('coefficient', ''),
                    'stdError': row.get('stdError', ''),
                    'tStatistic': row.get('tStatistic', ''),
                    'pValue': row.get('pValue', ''),
                    'correlation': row.get('correlation', ''),
                    'sourceName': row.get('sourceName', ''),
                    'publisher': row.get('publisher', ''),
                }
                for row in rows
            ]
            if table_role == 'impact_factors':
                factor_text = '、'.join(
                    dict.fromkeys(
                        self._compact_table_cell(self._label_without_variable_symbol(row.get('label', '')), 28)
                        for row in rows
                        if self._compact_table_cell(self._label_without_variable_symbol(row.get('label', '')), 28)
                    )
                )
                prompt = f'''请改写下面论文段落，使其自然引入{table_label}，并围绕表中的影响因素完善论述。

要求：
1. 保留原段落核心观点，但把空泛表述改成围绕关键影响因素的论文式分析。
2. 正文使用“如{table_label}所示”“见{table_label}”等表述，不要输出 Markdown 表格。
3. 这是正文内容归纳表，不要写来源名称、报告名、发布机构、URL、“数据来源为/来源来自”或参考文献编号。
4. 不要编造统计数值；应说明这些因素如何影响该段讨论对象、约束条件或风险表现。
5. 可以写成 1-2 个自然段，不要输出标题。
6. 正文提到表时使用“{table_label}”，不要自行改成其他编号。

原段落：
{original}

表题：{caption}
影响因素：
{factor_text or '请根据数据行 JSON 归纳。'}
数据行 JSON：
{json.dumps(rows_payload, ensure_ascii=False)}

请直接输出改写后的段落。'''
            elif has_numeric:
                prompt = f'''请改写下面论文段落，使其自然引入{table_label}，并分析表中数据。

要求：
1. 保留原段落核心观点，但将空泛判断改为基于真实数据的分析。
2. 正文使用“如{table_label}所示”“见{table_label}”等论文表述，不要输出 Markdown 表格。
3. 正文不要写来源名称、报告名、发布机构、URL或“数据来源为/来源来自”；来源由系统写入参考文献并用引用编号标注。
4. 必须直接引用关键数值，并解释最高值、最低值、趋势、差距变化或结构占比中至少两类信息。
5. 如果提供了 AI 数据分析计划或用户填写的 β/参数值，应结合这些信息说明表格如何服务于模型测算、方程代入或变量分析。
6. 如果提供了“最终分析式”，必须把分析式写入正文，并说明该式与{table_label}中的变量、因子或参数结果如何对应。
7. 可以写成 1-2 个自然段，不要输出标题。
8. 正文提到表时使用“{table_label}”，不要自行改成其他编号。

原段落：
{original}

表题：{caption}
单位：{unit or '未注明'}
真实数据点：
{data_points_text or '无'}
真实数据分析：
{analysis_text or '请根据数据行 JSON 自行归纳。'}
AI 数据分析计划与用户填写参数：
{analysis_note or '未提供'}
最终分析式：
{analysis_expression or '未提供'}
数据行 JSON：
{json.dumps(rows_payload, ensure_ascii=False)}

请直接输出改写后的段落。'''
            else:
                source_summary = self._source_table_summary(rows)
                prompt = f'''请改写下面论文段落，使其自然引入{table_label}。该表不是数值统计表，而是指标、变量、数据来源或口径说明表。

要求：
1. 保留原段落核心观点，但把空泛表述改成“为什么需要这些指标/变量/来源口径”的论文式说明。
2. 正文使用“如{table_label}所示”“见{table_label}”等表述，不要输出 Markdown 表格。
3. 正文不要写来源名称、报告名、发布机构、URL或“数据来源为/来源来自”；来源由系统写入参考文献并用引用编号标注。
4. 不要编造数值，不要写最高值、最低值、趋势、差距变化；应说明指标体系、变量口径、核验路径、可重复性或后续测度逻辑。
5. 可以写成 1-2 个自然段，不要输出标题。
6. 正文提到表时使用“{table_label}”，不要自行改成其他编号。

原段落：
{original}

表题：{caption}
表格作用概括：
{source_summary}
AI 数据分析计划与用户填写参数：
{analysis_note or '未提供'}
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
                    rewritten = self._ensure_rewritten_has_table_analysis(rewritten, rows, unit, caption, table_label)
                    if table_role != 'impact_factors':
                        rewritten = self._ensure_rewritten_has_analysis_expression(rewritten, analysis_expression, table_label)
                    return rewritten
            except Exception:
                pass
        intro = self._build_table_analysis_paragraph(rows, unit, caption, table_label)
        if analysis_expression and table_role != 'impact_factors':
            intro = f'根据{table_label}的参数计算结果，可得到分析式：{analysis_expression}。该式与表中变量、因子或参数结果相对应，用于解释模型测算结果。\n\n{intro}'
        if not original:
            return intro
        if table_label in original or '如表' in original or '见表' in original:
            return f'{original}\n\n{intro}'
        if table_role == 'impact_factors':
            return f'{original}\n\n为使上述论证的影响因素更加清晰，本文补充{table_label}。{intro}'
        return f'{original}\n\n为增强上述论证的数据支撑，本文补充{table_label}。{intro}'

    @classmethod
    def _ensure_rewritten_has_table_analysis(cls, rewritten, rows, unit='', title='', table_label='表1'):
        text = cls._normalize_text(cls._strip_source_prose(rewritten))
        analysis_paragraph = cls._build_table_analysis_paragraph(rows, unit, title, table_label)
        if not text:
            return analysis_paragraph
        if not cls._numeric_rows(rows) and table_label in text:
            return text
        if cls._analysis_mentions_data(text, rows):
            return text
        return f'{text}\n\n{analysis_paragraph}'.strip()

    @classmethod
    def _ensure_rewritten_has_analysis_expression(cls, rewritten, expression='', table_label='表1'):
        expression = cls._normalize_text(expression)
        text = cls._normalize_text(rewritten)
        if not expression or not text:
            return text or expression
        compact_text = re.sub(r'\s+', '', text)
        compact_expression = re.sub(r'\s+', '', expression)
        if compact_expression and compact_expression in compact_text:
            return text
        return (
            f'{text}\n\n'
            f'根据{table_label}的参数计算结果，可得到分析式：{expression}。'
            f'该式与表中变量、因子或参数结果相对应，用于进一步解释模型测算和变量分析结果。'
        ).strip()

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
