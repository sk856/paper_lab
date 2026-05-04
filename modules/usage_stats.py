# -*- coding: utf-8 -*-
"""
使用统计存储与聚合工具。
"""

from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from calendar import monthrange
from datetime import datetime, timedelta


USAGE_EVENTS_FILE = 'usage_events.jsonl'
TOKEN_PRICE_UNIT = 1_000_000
QUERY_START_AT = datetime(2000, 1, 1)

_RAW_USAGE_PERIOD_OPTIONS = (
    ('30d', '一个月'),
    ('14d', '14天'),
    ('7d', '7天'),
    ('24h', '24小时'),
)


def _unique_usage_period_options(options):
    unique_options = []
    seen_keys = set()
    for key, label in tuple(options or ()):
        normalized_key = str(key or '').strip()
        if not normalized_key or normalized_key in seen_keys:
            continue
        seen_keys.add(normalized_key)
        unique_options.append((normalized_key, str(label or '').strip()))
    return tuple(unique_options)


USAGE_PERIOD_OPTIONS = _unique_usage_period_options(_RAW_USAGE_PERIOD_OPTIONS)

PAGE_LABELS = {
    'paper_write': '论文写作',
    'ai_reduce': '降AI检测',
    'plagiarism': '降查重率',
    'polish': '学术润色',
    'correction': '智能纠错',
    'history': '历史记录',
    'api_config': '模型配置',
    '': '未分类',
}

TIME_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y-%m',
    '%Y/%m/%d %H:%M:%S',
    '%Y/%m/%d %H:%M',
    '%Y-%m-%d',
    '%Y/%m/%d',
    '%Y-%m-%d %H:%M:%S.%f',
)


@dataclass
class UsageEvent:
    request_id: str = ''
    timestamp: str = ''
    page_id: str = ''
    scene_id: str = ''
    action: str = ''
    api_id: str = ''
    provider: str = ''
    request_model: str = ''
    response_model: str = ''
    status: str = 'success'
    duration_ms: int = 0
    first_token_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_create_tokens: int = 0
    cache_hit_tokens: int = 0
    billing_multiplier: float = 1.0
    billing_mode: str = 'request_model'
    total_cost: float = 0.0
    error_message: str = ''
    request_detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return normalize_usage_event(asdict(self))


def _coerce_non_negative_int(value) -> int:
    try:
        result = int(value or 0)
    except Exception:
        result = 0
    return max(result, 0)


def _coerce_non_negative_float(value) -> float:
    try:
        result = float(value or 0.0)
    except Exception:
        result = 0.0
    return max(result, 0.0)


def _normalize_detail_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            key_text = str(key or '').strip()
            if not key_text:
                continue
            normalized[key_text] = _normalize_detail_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_detail_value(item) for item in value]
    return str(value)


def normalize_request_detail(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    normalized = _normalize_detail_value(payload)
    return normalized if isinstance(normalized, dict) else {}


def safe_datetime_fromtimestamp(timestamp):
    """安全地从时间戳转换为 datetime 对象，处理 Windows 平台可能的 range error"""
    try:
        # Windows 上的 time_t 限制，处理超出 1970-2038/3000 范围的情况
        return datetime.fromtimestamp(timestamp)
    except (ValueError, OSError, OverflowError):
        # 如果超出范围，返回一个合理的边界值或默认值
        if timestamp <= 0:
            return datetime(1970, 1, 1)
        return datetime(2099, 12, 31)


def parse_time_string(value: str):
    text = str(value or '').strip()
    if not text:
        return None
    
    # 尝试直接解析 ISO 格式或带毫秒的格式
    if '.' in text:
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            pass

    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    
    # 如果是纯数字，尝试作为时间戳解析
    if text.replace('.', '', 1).isdigit():
        try:
            ts = float(text)
            # 兼容秒、毫秒和微秒时间戳
            # 1e10 ~ 300+ 年，通常秒级时间戳 < 1e10
            # 1e13 ~ 300+ 年，通常毫秒级时间戳 < 1e13
            # 1e16 ~ 300+ 年，通常微秒级时间戳 < 1e16
            if ts > 1e15: # 微秒
                ts /= 1000000
            elif ts > 1e12: # 毫秒
                ts /= 1000
            return safe_datetime_fromtimestamp(ts)
        except Exception:
            pass
            
    return None


def parse_time_bound(value: str, *, is_end=False):
    text = str(value or '').strip()
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            bound = datetime.strptime(text, fmt)
        except ValueError:
            continue
        
        # Windows 平台 timestamp() 限制 (1970 以前会报错)
        try:
            _ = bound.timestamp()
        except (ValueError, OSError, OverflowError):
            if not is_end:
                return datetime(1970, 1, 1, 0, 0, 1)
            return datetime(2099, 12, 31, 23, 59, 59)

        if not is_end:
            return bound
        if fmt in {'%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M'}:
            return bound.replace(second=59)
        if fmt in {'%Y-%m-%d', '%Y/%m/%d'}:
            return bound.replace(hour=23, minute=59, second=59)
        if fmt == '%Y-%m':
            last_day = monthrange(bound.year, bound.month)[1]
            return bound.replace(day=last_day, hour=23, minute=59, second=59)
        return bound
    return None


def normalize_usage_event(payload: dict | None) -> dict:
    payload = dict(payload or {})
    timestamp = str(payload.get('timestamp', '') or '').strip()
    if not timestamp:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    status = str(payload.get('status', 'success') or 'success').strip().lower()
    if status not in {'success', 'error'}:
        status = 'success'
    billing_mode = str(payload.get('billing_mode', 'request_model') or 'request_model').strip()
    if billing_mode not in {'request_model', 'response_model'}:
        billing_mode = 'request_model'
    return {
        'request_id': str(payload.get('request_id', '') or '').strip(),
        'timestamp': timestamp,
        'page_id': str(payload.get('page_id', '') or '').strip(),
        'scene_id': str(payload.get('scene_id', '') or '').strip(),
        'action': str(payload.get('action', '') or '').strip(),
        'api_id': str(payload.get('api_id', '') or '').strip(),
        'provider': str(payload.get('provider', '') or '').strip(),
        'request_model': str(payload.get('request_model', '') or '').strip(),
        'response_model': str(payload.get('response_model', '') or '').strip(),
        'status': status,
        'duration_ms': _coerce_non_negative_int(payload.get('duration_ms', 0)),
        'first_token_ms': _coerce_non_negative_int(payload.get('first_token_ms', 0)),
        'input_tokens': _coerce_non_negative_int(payload.get('input_tokens', 0)),
        'output_tokens': _coerce_non_negative_int(payload.get('output_tokens', 0)),
        'cache_create_tokens': _coerce_non_negative_int(payload.get('cache_create_tokens', 0)),
        'cache_hit_tokens': _coerce_non_negative_int(payload.get('cache_hit_tokens', 0)),
        'billing_multiplier': _coerce_non_negative_float(payload.get('billing_multiplier', 1.0)) or 1.0,
        'billing_mode': billing_mode,
        'total_cost': round(_coerce_non_negative_float(payload.get('total_cost', 0.0)), 8),
        'error_message': str(payload.get('error_message', '') or '').strip(),
        'request_detail': normalize_request_detail(payload.get('request_detail')),
    }


def normalize_pricing_rule(rule: dict | None) -> dict | None:
    if not isinstance(rule, dict):
        return None
    provider = str(rule.get('provider', '') or '').strip()
    model = str(rule.get('model', '') or '').strip()
    if not provider or not model:
        return None
    return {
        'provider': provider,
        'model': model,
        'input_price': round(_coerce_non_negative_float(rule.get('input_price', 0.0)), 8),
        'output_price': round(_coerce_non_negative_float(rule.get('output_price', 0.0)), 8),
        'cache_create_price': round(_coerce_non_negative_float(rule.get('cache_create_price', 0.0)), 8),
        'cache_hit_price': round(_coerce_non_negative_float(rule.get('cache_hit_price', 0.0)), 8),
        'enabled': bool(rule.get('enabled', True)),
    }


def normalize_pricing_rules(rules) -> list[dict]:
    normalized = []
    seen = set()
    for item in list(rules or []):
        rule = normalize_pricing_rule(item)
        if not rule:
            continue
        key = (rule['provider'].lower(), rule['model'].lower())
        if key in seen:
            continue
        seen.add(key)
        normalized.append(rule)
    return normalized


def resolve_period_window(range_key='24h', *, now=None):
    now = now or datetime.now()
    if range_key == 'all':
        return None, now
    if range_key == '14d':
        return now - timedelta(days=14), now
    if range_key == '7d':
        return now - timedelta(days=7), now
    if range_key == '30d':
        return now - timedelta(days=30), now
    return now - timedelta(hours=24), now


def default_time_range_strings(range_key='24h', *, now=None) -> tuple[str, str]:
    start_at, end_at = resolve_period_window(range_key, now=now)
    if start_at is None or end_at is None:
        return '', ''
    return start_at.strftime('%Y-%m-%d %H:%M'), end_at.strftime('%Y-%m-%d %H:%M')


def default_query_time_range_strings(*, now=None) -> tuple[str, str]:
    end_at = (now or datetime.now()).replace(second=0, microsecond=0)
    start_at = end_at - timedelta(days=1)
    return start_at.strftime('%Y-%m-%d %H:%M'), end_at.strftime('%Y-%m-%d %H:%M')


def build_period_caption(range_key='24h') -> str:
    if range_key == 'all':
        return '总览（按月）'
    if range_key == '30d':
        return '过去一个月（按天）'
    if range_key == '14d':
        return '过去14天（按天）'
    if range_key == '7d':
        return '过去7天（按天）'
    return '过去24小时（按小时）'


def format_currency(value: float) -> str:
    return f'${float(value or 0.0):.4f}'


def format_token_count(value: int) -> str:
    amount = int(value or 0)
    if amount >= 1_000_000:
        return f'{amount / 1_000_000:.1f}m'
    if amount >= 1_000:
        return f'{amount / 1_000:.1f}k'
    return str(amount)


def resolve_page_label(page_id: str) -> str:
    key = str(page_id or '').strip()
    return PAGE_LABELS.get(key, key or PAGE_LABELS[''])


def resolve_billed_model(event: dict) -> str:
    billing_mode = str(event.get('billing_mode', 'request_model') or 'request_model').strip()
    request_model = str(event.get('request_model', '') or '').strip()
    response_model = str(event.get('response_model', '') or '').strip()
    if billing_mode == 'response_model' and response_model:
        return response_model
    return request_model or response_model


def resolve_event_billing(config_mgr, api_id: str, cfg: dict | None, response_model: str = '') -> dict:
    cfg = dict(cfg or {})
    global_settings = {'multiplier': 1.0, 'mode': 'request_model'}
    if config_mgr and hasattr(config_mgr, 'get_global_billing_settings'):
        global_settings = config_mgr.get_global_billing_settings()

    multiplier = float(global_settings.get('multiplier', 1.0) or 1.0)
    mode = str(global_settings.get('mode', 'request_model') or 'request_model').strip()
    if mode not in {'request_model', 'response_model'}:
        mode = 'request_model'

    if bool(cfg.get('use_separate_billing', False)):
        raw_multiplier = str(cfg.get('billing_multiplier', '') or '').strip()
        try:
            local_multiplier = float(raw_multiplier) if raw_multiplier else multiplier
        except Exception:
            local_multiplier = multiplier
        if local_multiplier > 0:
            multiplier = local_multiplier
        local_mode = str(cfg.get('billing_mode', '') or '').strip()
        if local_mode in {'request_model', 'response_model'}:
            mode = local_mode

    request_model = str(cfg.get('model', '') or '').strip()
    billed_model = response_model.strip() if mode == 'response_model' and str(response_model or '').strip() else request_model
    if not billed_model:
        billed_model = request_model or str(response_model or '').strip()

    return {
        'billing_multiplier': max(float(multiplier or 1.0), 0.0) or 1.0,
        'billing_mode': mode,
        'billed_model': billed_model,
        'api_id': str(api_id or '').strip(),
    }


def find_pricing_rule(config_mgr, provider: str, model: str) -> dict | None:
    if not config_mgr or not hasattr(config_mgr, 'get_usage_pricing_rules'):
        return None
    provider_key = str(provider or '').strip().lower()
    model_key = str(model or '').strip().lower()
    if not provider_key or not model_key:
        return None
    for rule in config_mgr.get_usage_pricing_rules():
        if not rule.get('enabled', True):
            continue
        if rule['provider'].strip().lower() == provider_key and rule['model'].strip().lower() == model_key:
            return copy.deepcopy(rule)
    return None


def calculate_total_cost(usage: dict | None, pricing_rule: dict | None, multiplier: float = 1.0) -> float:
    usage = dict(usage or {})
    rule = normalize_pricing_rule(pricing_rule) if pricing_rule else None
    if not rule:
        return 0.0
    factor = max(float(multiplier or 1.0), 0.0) or 1.0
    total = (
        _coerce_non_negative_int(usage.get('input_tokens', 0)) * rule['input_price']
        + _coerce_non_negative_int(usage.get('output_tokens', 0)) * rule['output_price']
        + _coerce_non_negative_int(usage.get('cache_create_tokens', 0)) * rule['cache_create_price']
        + _coerce_non_negative_int(usage.get('cache_hit_tokens', 0)) * rule['cache_hit_price']
    ) / TOKEN_PRICE_UNIT
    return round(total * factor, 8)


def extract_openai_usage(payload: dict | None) -> dict:
    payload = dict(payload or {})
    usage = dict(payload.get('usage', {}) or {})
    prompt_details = dict(usage.get('prompt_tokens_details', {}) or {})
    completion_details = dict(usage.get('completion_tokens_details', {}) or {})
    cache_create = (
        prompt_details.get('cache_creation_tokens')
        or prompt_details.get('cache_creation_input_tokens')
        or completion_details.get('cache_creation_tokens')
        or 0
    )
    cache_hit = (
        prompt_details.get('cached_tokens')
        or prompt_details.get('cache_read_input_tokens')
        or completion_details.get('cached_tokens')
        or 0
    )
    return {
        'input_tokens': _coerce_non_negative_int(usage.get('prompt_tokens', 0)),
        'output_tokens': _coerce_non_negative_int(usage.get('completion_tokens', 0)),
        'cache_create_tokens': _coerce_non_negative_int(cache_create),
        'cache_hit_tokens': _coerce_non_negative_int(cache_hit),
    }


def extract_claude_usage(payload: dict | None) -> dict:
    payload = dict(payload or {})
    usage = dict(payload.get('usage', {}) or {})
    return {
        'input_tokens': _coerce_non_negative_int(usage.get('input_tokens', 0)),
        'output_tokens': _coerce_non_negative_int(usage.get('output_tokens', 0)),
        'cache_create_tokens': _coerce_non_negative_int(usage.get('cache_creation_input_tokens', 0)),
        'cache_hit_tokens': _coerce_non_negative_int(usage.get('cache_read_input_tokens', 0)),
    }


def extract_gemini_usage(payload: dict | None) -> dict:
    payload = dict(payload or {})
    usage = dict(payload.get('usageMetadata', {}) or payload.get('usage', {}) or {})
    return {
        'input_tokens': _coerce_non_negative_int(
            usage.get('promptTokenCount', usage.get('input_tokens', 0))
        ),
        'output_tokens': _coerce_non_negative_int(
            usage.get('candidatesTokenCount', usage.get('output_tokens', 0))
        ),
        'cache_create_tokens': 0,
        'cache_hit_tokens': _coerce_non_negative_int(
            usage.get('cachedContentTokenCount', usage.get('cache_hit_tokens', 0))
        ),
    }


def extract_tongyi_usage(payload: dict | None) -> dict:
    payload = dict(payload or {})
    usage = dict(payload.get('usage', {}) or payload.get('output', {}).get('usage', {}) or {})
    return {
        'input_tokens': _coerce_non_negative_int(usage.get('input_tokens', usage.get('prompt_tokens', 0))),
        'output_tokens': _coerce_non_negative_int(usage.get('output_tokens', usage.get('completion_tokens', 0))),
        'cache_create_tokens': _coerce_non_negative_int(usage.get('cache_creation_tokens', 0)),
        'cache_hit_tokens': _coerce_non_negative_int(usage.get('cached_tokens', usage.get('cache_hit_tokens', 0))),
    }


class UsageStatsStore:
    def __init__(self, data_dir: str, config_mgr=None, filename: str = USAGE_EVENTS_FILE):
        base_dir = os.path.abspath(str(data_dir or '.'))
        self.data_dir = base_dir
        self.app_dir = base_dir
        self.config = config_mgr
        self.file_path = os.path.join(base_dir, filename)

    def append_event(self, event: dict | UsageEvent):
        try:
            payload = event.to_dict() if isinstance(event, UsageEvent) else normalize_usage_event(event)
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
            return payload
        except Exception:
            # 即使记录统计失败，也不应阻断 AI 主流程
            return {}

    def iter_events(self):
        if not os.path.exists(self.file_path):
            return []
        items = []
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as handle:
            for line in handle:
                text = str(line or '').strip()
                if not text:
                    continue
                try:
                    items.append(normalize_usage_event(json.loads(text)))
                except Exception:
                    continue
        return items

    def clear_events(self) -> int:
        items = self.iter_events()
        removed_count = len(items)
        if os.path.exists(self.file_path):
            with open(self.file_path, 'w', encoding='utf-8') as handle:
                handle.write('')
        return removed_count

    def query_events(
        self,
        range_key='24h',
        *,
        page_id='',
        status='',
        provider_keyword='',
        model_keyword='',
        start_text='',
        end_text='',
    ):
        start_at, end_at = self._resolve_query_window(range_key, start_text=start_text, end_text=end_text)
        provider_keyword = str(provider_keyword or '').strip().lower()
        model_keyword = str(model_keyword or '').strip().lower()
        page_id = str(page_id or '').strip()
        status = str(status or '').strip().lower()
        result = []
        for event in self.iter_events():
            event_time = parse_time_string(event.get('timestamp', ''))
            if not event_time:
                continue
            if event_time < start_at or event_time > end_at:
                continue
            if page_id and event.get('page_id', '') != page_id:
                continue
            if status and event.get('status', '') != status:
                continue
            if provider_keyword and provider_keyword not in str(event.get('provider', '') or '').lower():
                continue
            billed_model = resolve_billed_model(event)
            if model_keyword and model_keyword not in billed_model.lower():
                continue
            row = dict(event)
            row['billed_model'] = billed_model
            row['page_label'] = resolve_page_label(event.get('page_id', ''))
            result.append(row)
        result.sort(key=lambda item: item.get('timestamp', ''), reverse=True)
        return result

    def summarize(self, range_key='24h'):
        events = self.query_events(range_key)
        summary = {
            'total_requests': len(events),
            'total_cost': 0.0,
            'input_tokens': 0,
            'output_tokens': 0,
            'cache_create_tokens': 0,
            'cache_hit_tokens': 0,
            'total_tokens': 0,
        }
        for event in events:
            summary['total_cost'] += _coerce_non_negative_float(event.get('total_cost', 0.0))
            summary['input_tokens'] += _coerce_non_negative_int(event.get('input_tokens', 0))
            summary['output_tokens'] += _coerce_non_negative_int(event.get('output_tokens', 0))
            summary['cache_create_tokens'] += _coerce_non_negative_int(event.get('cache_create_tokens', 0))
            summary['cache_hit_tokens'] += _coerce_non_negative_int(event.get('cache_hit_tokens', 0))
        summary['total_cost'] = round(summary['total_cost'], 8)
        summary['total_tokens'] = summary['input_tokens'] + summary['output_tokens']
        return summary

    def build_trends(self, range_key='24h'):
        trend_events = []
        if range_key == 'all':
            for event in self.iter_events():
                event_time = parse_time_string(event.get('timestamp', ''))
                if not event_time:
                    continue
                trend_events.append((event_time, event))
            if trend_events:
                start_at = min(item[0] for item in trend_events)
                end_at = max(item[0] for item in trend_events)
            else:
                now = datetime.now()
                start_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_at = now
        else:
            start_at, end_at = self._resolve_query_window(range_key)

        buckets = self._build_buckets(range_key, start_at, end_at)
        bucket_map = {item['key']: item for item in buckets}
        bucket_start = parse_time_string(f'{buckets[0]["key"]}') if buckets else start_at
        if range_key == 'all':
            event_items = trend_events
        else:
            event_items = []
            for event in self.iter_events():
                event_time = parse_time_string(event.get('timestamp', ''))
                if not event_time:
                    continue
                event_items.append((event_time, event))

        for event_time, event in event_items:
            if event_time < bucket_start or event_time > end_at:
                continue
            key = self._bucket_key(event_time, range_key)
            bucket = bucket_map.get(key)
            if not bucket:
                continue
            bucket['cost'] += _coerce_non_negative_float(event.get('total_cost', 0.0))
            bucket['cache_create'] += _coerce_non_negative_int(event.get('cache_create_tokens', 0))
            bucket['cache_hit'] += _coerce_non_negative_int(event.get('cache_hit_tokens', 0))
            bucket['input'] += _coerce_non_negative_int(event.get('input_tokens', 0))
            bucket['output'] += _coerce_non_negative_int(event.get('output_tokens', 0))

        labels = [item['label'] for item in buckets]
        series = {
            'cost': [round(item['cost'], 8) for item in buckets],
            'cache_create': [item['cache_create'] for item in buckets],
            'cache_hit': [item['cache_hit'] for item in buckets],
            'input': [item['input'] for item in buckets],
            'output': [item['output'] for item in buckets],
        }
        return {
            'labels': labels,
            'series': series,
            'token_max': max(
                max(series['cache_create'] or [0]),
                max(series['cache_hit'] or [0]),
                max(series['input'] or [0]),
                max(series['output'] or [0]),
                0,
            ),
            'cost_max': max(series['cost'] or [0.0]),
            'caption': build_period_caption(range_key),
        }

    def provider_stats(self, range_key='24h'):
        groups = defaultdict(lambda: {
            'provider': '',
            'request_count': 0,
            'success_count': 0,
            'error_count': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_create_tokens': 0,
            'total_cost': 0.0,
            'duration_ms': 0,
        })
        for event in self.query_events(range_key):
            key = str(event.get('provider', '') or '').strip() or 'unknown'
            item = groups[key]
            item['provider'] = key
            item['request_count'] += 1
            item['success_count'] += 1 if event.get('status') == 'success' else 0
            item['error_count'] += 1 if event.get('status') == 'error' else 0
            item['input_tokens'] += _coerce_non_negative_int(event.get('input_tokens', 0))
            item['output_tokens'] += _coerce_non_negative_int(event.get('output_tokens', 0))
            item['cache_hit_tokens'] += _coerce_non_negative_int(event.get('cache_hit_tokens', 0))
            item['cache_create_tokens'] += _coerce_non_negative_int(event.get('cache_create_tokens', 0))
            item['total_cost'] += _coerce_non_negative_float(event.get('total_cost', 0.0))
            item['duration_ms'] += _coerce_non_negative_int(event.get('duration_ms', 0))
        result = []
        for item in groups.values():
            row = dict(item)
            row['total_cost'] = round(row['total_cost'], 8)
            row['avg_duration_ms'] = int(row['duration_ms'] / row['request_count']) if row['request_count'] else 0
            result.append(row)
        result.sort(key=lambda item: (-item['total_cost'], -item['request_count'], item['provider']))
        return result

    def model_stats(self, range_key='24h'):
        groups = defaultdict(lambda: {
            'provider': '',
            'model': '',
            'request_count': 0,
            'success_count': 0,
            'error_count': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_create_tokens': 0,
            'total_cost': 0.0,
            'duration_ms': 0,
        })
        for event in self.query_events(range_key):
            model = resolve_billed_model(event) or 'unknown'
            provider = str(event.get('provider', '') or '').strip() or 'unknown'
            key = (provider, model)
            item = groups[key]
            item['provider'] = provider
            item['model'] = model
            item['request_count'] += 1
            item['success_count'] += 1 if event.get('status') == 'success' else 0
            item['error_count'] += 1 if event.get('status') == 'error' else 0
            item['input_tokens'] += _coerce_non_negative_int(event.get('input_tokens', 0))
            item['output_tokens'] += _coerce_non_negative_int(event.get('output_tokens', 0))
            item['cache_hit_tokens'] += _coerce_non_negative_int(event.get('cache_hit_tokens', 0))
            item['cache_create_tokens'] += _coerce_non_negative_int(event.get('cache_create_tokens', 0))
            item['total_cost'] += _coerce_non_negative_float(event.get('total_cost', 0.0))
            item['duration_ms'] += _coerce_non_negative_int(event.get('duration_ms', 0))
        result = []
        for item in groups.values():
            row = dict(item)
            row['total_cost'] = round(row['total_cost'], 8)
            row['avg_duration_ms'] = int(row['duration_ms'] / row['request_count']) if row['request_count'] else 0
            result.append(row)
        result.sort(key=lambda item: (-item['total_cost'], -item['request_count'], item['provider'], item['model']))
        return result

    def _resolve_query_window(self, range_key='24h', *, start_text='', end_text=''):
        default_start, default_end = resolve_period_window(range_key)
        start_at = parse_time_bound(start_text, is_end=False) or default_start
        end_at = parse_time_bound(end_text, is_end=True) or default_end or datetime.now()
        if start_at is None:
            start_at = QUERY_START_AT
        if start_at > end_at:
            start_at, end_at = end_at, start_at
        return start_at, end_at

    @staticmethod
    def _bucket_key(event_time: datetime, range_key='24h'):
        if range_key == '24h':
            return event_time.strftime('%Y-%m-%d %H:00:00')
        if range_key == 'all':
            return event_time.strftime('%Y-%m')
        return event_time.strftime('%Y-%m-%d')

    @staticmethod
    def _build_buckets(range_key, start_at: datetime, end_at: datetime):
        buckets = []
        if range_key == '24h':
            current = end_at.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
            for _ in range(24):
                buckets.append({
                    'key': current.strftime('%Y-%m-%d %H:00:00'),
                    'label': current.strftime('%m/%d %H:%M'),
                    'cost': 0.0,
                    'cache_create': 0,
                    'cache_hit': 0,
                    'input': 0,
                    'output': 0,
                })
                current += timedelta(hours=1)
            return buckets

        if range_key == 'all':
            current = start_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_month = end_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            while current <= end_month:
                buckets.append({
                    'key': current.strftime('%Y-%m'),
                    'label': current.strftime('%Y/%m'),
                    'cost': 0.0,
                    'cache_create': 0,
                    'cache_hit': 0,
                    'input': 0,
                    'output': 0,
                })
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
            return buckets

        day_count = 30
        if range_key == '14d':
            day_count = 14
        elif range_key == '7d':
            day_count = 7
        current = end_at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day_count - 1)
        for _ in range(day_count):
            buckets.append({
                'key': current.strftime('%Y-%m-%d'),
                'label': current.strftime('%m/%d'),
                'cost': 0.0,
                'cache_create': 0,
                'cache_hit': 0,
                'input': 0,
                'output': 0,
            })
            current += timedelta(days=1)
        return buckets
