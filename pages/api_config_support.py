# -*- coding: utf-8 -*-
"""
模型配置页使用的预设与表单辅助函数。
"""

from modules.provider_registry import (
    AUTH_VALUE_MODE_BEARER,
    API_FORMAT_OPENAI_CHAT_COMPLETIONS,
    PRESET_MAP,
    normalize_provider_type,
)


FORM_KEY = '__current__'
DEFAULT_TEST_PROMPT = 'Who are you?'
DEFAULT_TEST_TIMEOUT_SEC = 45
DEFAULT_TEST_DEGRADE_MS = 6000
DEFAULT_TEST_MAX_RETRIES = 2


def build_base_form_template(provider_type='custom'):
    provider_type = normalize_provider_type(provider_type)
    return {
        'name': '',
        'remark': '',
        'website': '',
        'key': '',
        'base_url': '',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'model_mapping': '',
        'model': '',
        'model_display_name': '',
        'provider_type': provider_type,
        'hide_ai_signature': False,
        'teammates_mode': False,
        'enable_tool_search': False,
        'high_intensity_thinking': False,
        'enable_user_agent_spoof': False,
        'extra_json': '',
        'extra_headers': '',
        'temperature': '',
        'max_tokens': '',
        'timeout': '',
        'top_p': '',
        'presence_penalty': '',
        'frequency_penalty': '',
        'use_separate_params': False,
        'use_separate_test': False,
        'test_model': '',
        'test_prompt': '',
        'test_timeout': '',
        'test_degrade_ms': '',
        'test_max_retries': '',
        'use_separate_billing': False,
        'billing_multiplier': '',
        'billing_mode': '',
    }


def merge_with_preset_defaults(cfg, provider_type):
    provider_type = normalize_provider_type(provider_type)
    merged = build_base_form_template(provider_type)
    merged.update(cfg or {})
    merged['provider_type'] = provider_type

    preset_defaults = PRESET_MAP.get(provider_type, {}).get('defaults', {})
    for field in ('website', 'base_url', 'api_format', 'auth_field', 'auth_value_mode'):
        if not str(merged.get(field, '') or '').strip() and preset_defaults.get(field):
            merged[field] = preset_defaults[field]
    return merged


def _coerce_float(value, default):
    try:
        return float(value)
    except Exception:
        return float(default)


def _coerce_int(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def resolve_connection_test_settings(config_mgr, cfg):
    cfg = dict(cfg or {})
    use_separate = bool(cfg.get('use_separate_test', False))
    get_setting = getattr(config_mgr, 'get_setting', None)

    if use_separate:
        return {
            'model_override': str(cfg.get('test_model', '') or '').strip() or None,
            'prompt': str(cfg.get('test_prompt', '') or '').strip() or DEFAULT_TEST_PROMPT,
            'timeout': _coerce_float(cfg.get('test_timeout', '') or DEFAULT_TEST_TIMEOUT_SEC, DEFAULT_TEST_TIMEOUT_SEC),
            'degrade_ms': _coerce_int(cfg.get('test_degrade_ms', '') or DEFAULT_TEST_DEGRADE_MS, DEFAULT_TEST_DEGRADE_MS),
            'max_retries': _coerce_int(cfg.get('test_max_retries', '') or DEFAULT_TEST_MAX_RETRIES, DEFAULT_TEST_MAX_RETRIES),
        }

    if callable(get_setting):
        prompt = get_setting('global_test_prompt', DEFAULT_TEST_PROMPT) or DEFAULT_TEST_PROMPT
        timeout = _coerce_float(get_setting('global_test_timeout_sec', DEFAULT_TEST_TIMEOUT_SEC), DEFAULT_TEST_TIMEOUT_SEC)
        degrade_ms = _coerce_int(get_setting('global_test_degrade_ms', DEFAULT_TEST_DEGRADE_MS), DEFAULT_TEST_DEGRADE_MS)
        max_retries = _coerce_int(get_setting('global_test_max_retries', DEFAULT_TEST_MAX_RETRIES), DEFAULT_TEST_MAX_RETRIES)
    else:
        prompt = DEFAULT_TEST_PROMPT
        timeout = float(DEFAULT_TEST_TIMEOUT_SEC)
        degrade_ms = int(DEFAULT_TEST_DEGRADE_MS)
        max_retries = int(DEFAULT_TEST_MAX_RETRIES)

    return {
        'model_override': None,
        'prompt': str(prompt or DEFAULT_TEST_PROMPT),
        'timeout': timeout,
        'degrade_ms': degrade_ms,
        'max_retries': max_retries,
    }
