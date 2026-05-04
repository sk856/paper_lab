# -*- coding: utf-8 -*-
"""
AI 接口客户端模块 - 统一调用各种 AI 服务
"""

import json
import http.client
import re
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse
import threading
import uuid
from datetime import datetime
from typing import Callable, Optional

from modules.provider_registry import (
    ANTHROPIC_VERSION,
    API_FORMAT_AWS_BEDROCK_RESERVED,
    AUTH_VALUE_MODE_BEARER,
    AUTH_VALUE_MODE_RAW,
    HANDLER_BEDROCK_RESERVED,
    HANDLER_CLAUDE,
    HANDLER_GEMINI,
    HANDLER_OPENAI,
    MODEL_LIST_MANUAL,
    MODEL_LIST_STATIC,
    MODEL_LIST_UNAVAILABLE,
    get_preset_definition,
    get_model_list_manual_message,
    get_static_models,
    normalize_api_format,
    normalize_provider_type,
    resolve_handler_name as resolve_provider_handler_name,
    resolve_model_list_strategy,
)
from modules.usage_stats import (
    UsageEvent,
    UsageStatsStore,
    calculate_total_cost,
    extract_claude_usage,
    extract_gemini_usage,
    extract_openai_usage,
    find_pricing_rule,
    normalize_request_detail,
    resolve_event_billing,
)


class APIRequestError(RuntimeError):
    """携带请求详情的运行时错误。"""

    def __init__(self, message, *, detail=None):
        super().__init__(message)
        self.detail = normalize_request_detail(detail)


class APIClient:
    """Unified AI API client."""

    DEFAULT_SPOOFED_USER_AGENT = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/135.0.0.0 Safari/537.36'
    )
    DEFAULT_BROWSER_ACCEPT = 'application/json, text/plain, */*'
    DEFAULT_BROWSER_ACCEPT_LANGUAGE = 'zh-CN,zh;q=0.9,en;q=0.8'
    DEFAULT_TRANSPORT_RETRY_ATTEMPTS = 2
    DEFAULT_TRANSPORT_RETRY_DELAY_SECONDS = 0.35
    RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
    PROTECTED_AUTH_FIELDS = {
        'Authorization',
        'Proxy-Authorization',
        'x-api-key',
        'api-key',
        'x-goog-api-key',
    }
    PARAMETER_FIELDS = (
        'temperature',
        'max_tokens',
        'timeout',
        'top_p',
        'presence_penalty',
        'frequency_penalty',
    )

    def __init__(self, config_mgr, log_callback=None):
        self.config = config_mgr
        self.log_callback = log_callback
        self.skills_runtime = None
        data_dir = getattr(config_mgr, 'data_dir', '') or getattr(config_mgr, 'app_dir', '') or '.'
        self.usage_store = UsageStatsStore(data_dir, config_mgr=config_mgr)

    def set_log_callback(self, log_callback):
        self.log_callback = log_callback

    def set_skills_runtime(self, skills_runtime):
        self.skills_runtime = skills_runtime

    def _get_active(self):
        return self.config.active_api

    def _resolve_api_for_request(self, api_name, usage_context, cfg):
        """根据调用上下文解析最终使用的 API id。

        优先级：
        1. 显式传入的 api_name（保持原有行为）
        2. 直接提供的 cfg（测试连接等场景）
        3. scene_model_map[scene_id]
        4. feature_model_map[feature_id]（scene_id 的前缀或 usage_context.page_id）
        5. fallback_api
        6. active_api
        """
        explicit_name = str(api_name or '').strip()
        if explicit_name:
            return explicit_name

        # 如果直接提供了配置（如测试连接场景），则不需要再解析路由
        if cfg is not None:
            return ''

        resolver = getattr(self.config, 'resolve_routed_api', None)
        if not callable(resolver):
            return self._get_active()

        scene_id = ''
        feature_id = ''
        if isinstance(usage_context, dict):
            scene_id = str(usage_context.get('scene_id', '') or '').strip()
            feature_id = str(usage_context.get('page_id', '') or '').strip()

        try:
            resolved = resolver(scene_id=scene_id, feature_id=feature_id)
        except Exception:
            resolved = self._get_active()
        if not resolved:
            resolved = self._get_active()

        if resolved and resolved != self._get_active():
            self._log(
                '[api_routing] '
                f'scene={scene_id or "-"} '
                f'feature={feature_id or "-"} '
                f'resolved={resolved}'
            )
        return resolved

    def _get_config(self, api_name, cfg=None):
        if cfg is not None:
            return dict(cfg)
        return dict(self.config.get_api_config(api_name) or {})

    def _resolve_handler_name(self, api_name, cfg):
        provider_type = normalize_provider_type(cfg.get('provider_type') or api_name)
        return resolve_provider_handler_name(provider_type, cfg.get('api_format', ''))

    def _resolve_service_provider_name(self, api_name, cfg):
        record = dict(cfg or {})
        provider_type = normalize_provider_type(record.get('provider_type') or api_name)
        if provider_type and provider_type != 'custom':
            return provider_type
        custom_name = str(record.get('name', '') or '').strip()
        if custom_name:
            return custom_name
        return self._resolve_handler_name(api_name, record)

    def _resolve_model_list_strategy(self, api_name, cfg):
        provider_type = normalize_provider_type(cfg.get('provider_type') or api_name)
        return resolve_model_list_strategy(provider_type, cfg.get('api_format', ''))

    @staticmethod
    def _coerce_positive_float(value, default=None):
        try:
            number = float(value)
        except Exception:
            return default
        if number <= 0:
            return default
        return number

    @staticmethod
    def _coerce_positive_int(value, default=None):
        try:
            number = int(value)
        except Exception:
            return default
        if number <= 0:
            return default
        return number

    def _get_global_parameter_settings(self):
        if not self.config or not hasattr(self.config, 'get_global_parameter_settings'):
            return {field: '' for field in self.PARAMETER_FIELDS}

        raw_settings = self.config.get_global_parameter_settings()
        if not isinstance(raw_settings, dict):
            return {field: '' for field in self.PARAMETER_FIELDS}

        return {
            field: str(raw_settings.get(field, '') or '').strip()
            for field in self.PARAMETER_FIELDS
        }

    def _resolve_effective_request_config(self, cfg):
        resolved = dict(cfg or {})
        global_settings = self._get_global_parameter_settings()
        use_separate = bool(resolved.get('use_separate_params', False))

        for field in self.PARAMETER_FIELDS:
            global_value = str(global_settings.get(field, '') or '').strip()
            local_value = str(resolved.get(field, '') or '').strip()
            if use_separate:
                resolved[field] = local_value or global_value
            else:
                resolved[field] = local_value if local_value != '' else global_value
        return resolved

    def _resolve_request_temperature(self, cfg, temperature):
        explicit_temperature = self._coerce_float(temperature, default=None)
        if explicit_temperature is not None:
            return explicit_temperature

        if isinstance(cfg, dict):
            configured_temperature = self._coerce_float(cfg.get('temperature', ''), default=None)
            if configured_temperature is not None:
                return configured_temperature
        return None

    @classmethod
    def _resolve_auto_request_timeout(cls, *, default=30.0, prompt='', system='', max_tokens=None):
        # 默认超时仍然较短；但对于长提示词/大生成量，需要更大的读取超时。
        timeout_value = max(float(default or 30.0), 1.0)  # 默认30秒
        total_prompt_chars = len(str(prompt or '')) + len(str(system or ''))
        token_budget = cls._coerce_positive_int(max_tokens, default=0) or 0

        if total_prompt_chars >= 1500:
            timeout_value = max(timeout_value, 60.0)
        if total_prompt_chars >= 3000:
            timeout_value = max(timeout_value, 90.0)
        if total_prompt_chars >= 4500:
            timeout_value = max(timeout_value, 120.0)
        if total_prompt_chars >= 6000:
            timeout_value = max(timeout_value, 150.0)
        if total_prompt_chars >= 9000:
            timeout_value = max(timeout_value, 180.0)

        if token_budget >= 1024:
            timeout_value = max(timeout_value, 60.0)
        if token_budget >= 2000:
            timeout_value = max(timeout_value, 90.0)
        if token_budget >= 3000:
            timeout_value = max(timeout_value, 120.0)
        if token_budget >= 4096:
            timeout_value = max(timeout_value, 150.0)
        if token_budget >= 8192:
            timeout_value = max(timeout_value, 180.0)

        return min(timeout_value, 180.0)

    def _resolve_request_timeout(self, cfg, request_timeout, *, default=60.0, prompt='', system='', max_tokens=None):
        explicit_timeout = self._coerce_positive_float(request_timeout)
        if explicit_timeout is not None:
            return explicit_timeout

        configured_timeout = None
        if isinstance(cfg, dict):
            configured_timeout = self._coerce_positive_float(cfg.get('timeout', ''))
        if configured_timeout is not None:
            return configured_timeout
        return self._resolve_auto_request_timeout(
            default=default,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
        )

    def _resolve_request_max_tokens(self, cfg, max_tokens, *, default=None):
        explicit_max_tokens = self._coerce_positive_int(max_tokens)
        configured_max_tokens = None
        if isinstance(cfg, dict):
            configured_max_tokens = self._coerce_positive_int(cfg.get('max_tokens', ''))
        if explicit_max_tokens is not None and configured_max_tokens is not None:
            return min(explicit_max_tokens, configured_max_tokens)
        if explicit_max_tokens is not None:
            return explicit_max_tokens
        if configured_max_tokens is not None:
            return configured_max_tokens
        if default is None:
            return None
        return max(int(default or 1), 1)

    @staticmethod
    def _truncate_debug_text(value, limit=12000):
        text = str(value or '').strip()
        if not text:
            return ''
        if len(text) <= limit:
            return text
        return f'{text[:limit]}\n...(已截断，共 {len(text)} 个字符)'

    @classmethod
    def _is_sensitive_header_name(cls, header_name):
        normalized = str(header_name or '').strip().lower()
        if not normalized:
            return False
        if normalized in {item.lower() for item in cls.PROTECTED_AUTH_FIELDS}:
            return True
        return any(fragment in normalized for fragment in ('auth', 'token', 'secret', 'cookie', 'key'))

    @classmethod
    def _sanitize_headers_for_debug(cls, headers):
        sanitized = {}
        for key, value in dict(headers or {}).items():
            header_name = str(key or '').strip()
            if not header_name:
                continue
            if cls._is_sensitive_header_name(header_name):
                sanitized[header_name] = '***'
            else:
                sanitized[header_name] = str(value)
        return sanitized

    @classmethod
    def _format_debug_payload(cls, payload, *, limit=12000):
        if payload in (None, '', {}, []):
            return ''
        if isinstance(payload, str):
            return cls._truncate_debug_text(payload, limit=limit)
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            text = str(payload)
        return cls._truncate_debug_text(text, limit=limit)

    @classmethod
    def _build_request_debug_section(
        cls,
        *,
        method='POST',
        url='',
        headers=None,
        body=None,
        timeout=None,
        prompt='',
        system='',
        extra=None,
    ):
        section = {
            'method': str(method or '').strip().upper() or 'POST',
            'url': cls._sanitize_url_for_log(url),
            'timeout_sec': timeout,
        }
        headers_text = cls._format_debug_payload(cls._sanitize_headers_for_debug(headers), limit=8000)
        body_text = cls._format_debug_payload(body, limit=16000)
        prompt_text = cls._truncate_debug_text(prompt, limit=4000)
        system_text = cls._truncate_debug_text(system, limit=4000)
        if headers_text:
            section['headers_text'] = headers_text
        if body_text:
            section['body_text'] = body_text
        if prompt_text:
            section['prompt_text'] = prompt_text
        if system_text:
            section['system_text'] = system_text
        for key, value in dict(extra or {}).items():
            if value in (None, '', [], {}):
                continue
            section[str(key)] = value
        return normalize_request_detail(section)

    @classmethod
    def _build_response_debug_section(cls, *, body=None, extra=None):
        section = {}
        body_text = cls._format_debug_payload(body, limit=16000)
        if body_text:
            section['body_text'] = body_text
        for key, value in dict(extra or {}).items():
            if value in (None, '', [], {}):
                continue
            section[str(key)] = value
        return normalize_request_detail(section)

    @staticmethod
    def _merge_request_details(*parts):
        merged = {}
        for part in parts:
            current = normalize_request_detail(part)
            for key, value in current.items():
                if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                    merged[key] = APIClient._merge_request_details(merged[key], value)
                else:
                    merged[key] = value
        return merged

    @classmethod
    def _wrap_request_error(cls, exc, *, detail=None):
        merged_detail = cls._merge_request_details(getattr(exc, 'detail', {}), detail or {})
        return APIRequestError(str(exc), detail=merged_detail)

    @staticmethod
    def _should_store_verbose_request_detail(usage_context, status):
        context = dict(usage_context or {})
        scene_id = str(context.get('scene_id', '') or '').strip()
        action = str(context.get('action', '') or '').strip()
        if str(status or '').strip().lower() == 'error':
            return True
        if scene_id in {'connection_test', 'global_connection_test'}:
            return True
        return action in {'test_connection', 'global_test_connection'}

    @classmethod
    def _build_usage_request_detail(
        cls,
        *,
        usage_context,
        request_id,
        api_name,
        provider_name,
        handler_name,
        request_model,
        response_model,
        status,
        duration_ms,
        timeout_sec,
        temperature,
        max_tokens,
        prompt,
        system,
        request_detail=None,
        error_message='',
    ):
        context = dict(usage_context or {})
        detail = cls._merge_request_details(request_detail or {})
        detail['summary'] = {
            'request_id': str(request_id or '').strip(),
            'api_id': str(api_name or '').strip(),
            'provider': str(provider_name or '').strip(),
            'handler': str(handler_name or '').strip(),
            'request_model': str(request_model or '').strip(),
            'response_model': str(response_model or '').strip(),
            'status': str(status or '').strip(),
            'duration_ms': int(duration_ms or 0),
            'timeout_sec': timeout_sec,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'prompt_length': len(prompt or ''),
            'system_length': len(system or ''),
            'page_id': str(context.get('page_id', '') or '').strip(),
            'scene_id': str(context.get('scene_id', '') or '').strip(),
            'action': str(context.get('action', '') or '').strip(),
        }
        if error_message:
            detail['error'] = cls._merge_request_details(
                detail.get('error', {}),
                {'message': cls._truncate_debug_text(error_message, limit=16000)},
            )
        if not cls._should_store_verbose_request_detail(usage_context, status):
            request_section = dict(detail.get('request', {}) or {})
            response_section = dict(detail.get('response', {}) or {})
            for key in ('headers_text', 'body_text', 'prompt_text', 'system_text'):
                request_section.pop(key, None)
            body_preview = response_section.pop('body_text', '')
            if body_preview and not response_section.get('body_preview'):
                response_section['body_preview'] = cls._truncate_debug_text(body_preview, limit=1000)
            detail['request'] = request_section
            detail['response'] = response_section
        return normalize_request_detail(detail)

    @staticmethod
    def _normalize_auth_field(field_name):
        value = str(field_name or '').strip()
        return value or 'Authorization'

    @staticmethod
    def _normalize_auth_value_mode(value, default=AUTH_VALUE_MODE_BEARER):
        normalized = str(value or '').strip().lower()
        if normalized in {AUTH_VALUE_MODE_BEARER, AUTH_VALUE_MODE_RAW}:
            return normalized
        return default

    @classmethod
    def _build_auth_headers(
        cls,
        key,
        *,
        auth_field='Authorization',
        auth_value_mode=AUTH_VALUE_MODE_BEARER,
        include_content_type=True,
    ):
        headers = {}
        if include_content_type:
            headers['Content-Type'] = 'application/json'
        normalized_field = str(auth_field or 'Authorization').strip() or 'Authorization'
        normalized_mode = cls._normalize_auth_value_mode(auth_value_mode)
        if normalized_mode == AUTH_VALUE_MODE_RAW:
            headers[normalized_field] = str(key)
        else:
            headers[normalized_field] = f'Bearer {key}'
        return headers

    @staticmethod
    def _contains_header(headers, header_name):
        wanted = str(header_name or '').strip().lower()
        if not wanted:
            return False
        return any(str(key or '').strip().lower() == wanted for key in dict(headers or {}))

    @staticmethod
    def _coerce_float(value, default=None):
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _load_extra_headers_payload(cfg):
        raw = str((cfg or {}).get('extra_headers', '') or '').strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid extra_headers JSON: {exc}') from exc
        if not isinstance(payload, dict):
            raise ValueError('Extra headers JSON must be an object')

        normalized = {}
        for key, value in payload.items():
            header_name = str(key or '').strip()
            if not header_name:
                raise ValueError('Extra headers JSON contains an empty header name')
            if isinstance(value, (dict, list)):
                raise ValueError(f'Extra header {header_name} must be a scalar value')
            normalized[header_name] = '' if value is None else str(value)
        return normalized

    @staticmethod
    def _merge_extra_headers(base_headers, extra_headers, *, protected_fields=None):
        merged = dict(base_headers or {})
        existing = {
            str(key).strip().lower()
            for key in merged
            if str(key).strip()
        }
        protected = {
            str(key).strip().lower()
            for key in (protected_fields or [])
            if str(key).strip()
        }
        added_keys = []
        ignored_keys = []

        for key, value in dict(extra_headers or {}).items():
            header_name = str(key or '').strip()
            if not header_name:
                ignored_keys.append(str(key))
                continue
            header_name_lower = header_name.lower()
            if header_name_lower in protected or header_name_lower in existing:
                ignored_keys.append(header_name)
                continue
            merged[header_name] = str(value)
            existing.add(header_name_lower)
            added_keys.append(header_name)
        return merged, sorted(set(added_keys)), sorted(set(ignored_keys))

    @staticmethod
    def _should_apply_user_agent_spoof(cfg):
        if bool((cfg or {}).get('enable_user_agent_spoof', False)):
            return True
        provider_type = normalize_provider_type((cfg or {}).get('provider_type', ''))
        return provider_type == 'sub2api'

    @classmethod
    def _apply_user_agent_spoof(cls, headers, cfg):
        merged = dict(headers or {})
        if not cls._should_apply_user_agent_spoof(cfg):
            return merged
        if cls._contains_header(merged, 'User-Agent'):
            return merged
        merged['User-Agent'] = cls.DEFAULT_SPOOFED_USER_AGENT
        return merged

    @staticmethod
    def _should_apply_browser_compat_headers(cfg):
        provider_type = normalize_provider_type((cfg or {}).get('provider_type', ''))
        return provider_type in {'sub2api', 'newapi'}

    @classmethod
    def _apply_browser_compat_headers(cls, headers, cfg):
        merged = dict(headers or {})
        if not cls._should_apply_browser_compat_headers(cfg):
            return merged
        if not cls._contains_header(merged, 'Accept'):
            merged['Accept'] = cls.DEFAULT_BROWSER_ACCEPT
        if not cls._contains_header(merged, 'Accept-Language'):
            merged['Accept-Language'] = cls.DEFAULT_BROWSER_ACCEPT_LANGUAGE
        if not cls._contains_header(merged, 'Cache-Control'):
            merged['Cache-Control'] = 'no-cache'
        if not cls._contains_header(merged, 'Pragma'):
            merged['Pragma'] = 'no-cache'
        if not cls._contains_header(merged, 'Connection'):
            merged['Connection'] = 'keep-alive'

        base_url = str((cfg or {}).get('base_url', '') or '').strip()
        if base_url:
            parts = urllib.parse.urlsplit(base_url)
            origin = urllib.parse.urlunsplit((parts.scheme, parts.netloc, '', '', ''))
            if origin:
                if not cls._contains_header(merged, 'Origin'):
                    merged['Origin'] = origin
                if not cls._contains_header(merged, 'Referer'):
                    merged['Referer'] = f'{origin}/'
        return merged

    def _merge_request_headers(self, base_headers, cfg, *, protected_fields=None):
        extra_headers_payload = self._load_extra_headers_payload(cfg)
        headers, extra_header_keys, ignored_extra_header_keys = self._merge_extra_headers(
            base_headers,
            extra_headers_payload,
            protected_fields=protected_fields,
        )
        headers = self._apply_user_agent_spoof(headers, cfg)
        headers = self._apply_browser_compat_headers(headers, cfg)
        return headers, extra_headers_payload, extra_header_keys, ignored_extra_header_keys

    @staticmethod
    def _normalize_openai_compat_base_url(base_url, provider_type=''):
        normalized_base_url = str(base_url or 'https://api.openai.com/v1').strip() or 'https://api.openai.com/v1'
        normalized_provider_type = normalize_provider_type(provider_type)
        if normalized_provider_type not in {'newapi', 'sub2api'}:
            return normalized_base_url

        parts = urllib.parse.urlsplit(normalized_base_url)
        path = parts.path.rstrip('/')
        if path.endswith('/v1beta'):
            path = f'{path[:-len("/v1beta")]}/v1'
        elif '/v1beta/' in path:
            path = path.replace('/v1beta/', '/v1/', 1)
        elif not path:
            path = '/v1'
        elif not (
            path.endswith('/v1')
            or '/v1/' in path
            or path.endswith('/chat/completions')
            or path.endswith('/models')
        ):
            path = f'{path}/v1'

        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    @classmethod
    def _resolve_openai_compat_urls(cls, base_url, provider_type=''):
        normalized_base_url = cls._normalize_openai_compat_base_url(base_url, provider_type=provider_type)
        parts = urllib.parse.urlsplit(normalized_base_url)
        path = parts.path.rstrip('/')
        chat_suffix = '/chat/completions'

        if path.endswith(chat_suffix):
            root_path = path[:-len(chat_suffix)] or ''
            chat_path = path
        else:
            root_path = path
            chat_path = f'{root_path}{chat_suffix}' if root_path else chat_suffix

        models_path = f'{root_path}/models' if root_path else '/models'
        root_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, root_path, '', ''))
        chat_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, chat_path, '', ''))
        models_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, models_path, '', ''))
        return {
            'root_url': root_url or normalized_base_url.rstrip('/'),
            'chat_completions_url': chat_url,
            'models_url': models_url,
        }

    @staticmethod
    def _build_invalid_json_response_error(raw):
        content = str(raw or '').strip()
        if not content:
            return 'Response body is empty and cannot be parsed as JSON'
        preview = content.replace('\r', ' ').replace('\n', ' ')
        preview = preview[:96]
        if content.lstrip().startswith('<'):
            return (
                'Response is HTML instead of JSON. '
                'The Base URL may point to a website page, or the relay returned an HTML error page instead of JSON. '
                f'Preview: {preview}'
            )
        return f'Response is not valid JSON. Preview: {preview}'

    @classmethod
    def _is_retryable_http_status(cls, status_code):
        """判断HTTP状态码是否应该重试 - 避免对认证失败等错误进行无效重试"""
        try:
            code = int(status_code)
        except Exception:
            return False
        
        # 某些错误不应该重试（立即返回）
        NON_RETRYABLE = {400, 401, 403, 404, 422}  # 请求错误或认证失败等
        if code in NON_RETRYABLE:
            return False
        
        return code in cls.RETRYABLE_HTTP_STATUS_CODES

    @classmethod
    def _format_http_error_message(cls, status_code, raw_body):
        body = str(raw_body or '').strip()
        if not body:
            return f'HTTP {status_code}'
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            detail = cls._build_invalid_json_response_error(body)
            return f'HTTP {status_code}: {detail}'

        if isinstance(payload, dict):
            openai_error = cls._extract_openai_error_message(payload)
            if openai_error:
                return f'HTTP {status_code}: {openai_error}'
        preview = json.dumps(payload, ensure_ascii=False)
        if len(preview) > 240:
            preview = f'{preview[:240]}...'
        return f'HTTP {status_code}: {preview}'

    @staticmethod
    def _resolve_claude_urls(base_url):
        normalized_base_url = str(base_url or 'https://api.anthropic.com').strip() or 'https://api.anthropic.com'
        parts = urllib.parse.urlsplit(normalized_base_url)
        path = parts.path.rstrip('/')
        if path.endswith('/v1/messages'):
            api_root_path = path[:-len('/messages')]
        elif path.endswith('/v1/models'):
            api_root_path = path[:-len('/models')]
        elif path.endswith('/v1'):
            api_root_path = path
        else:
            api_root_path = f'{path}/v1' if path else '/v1'

        messages_path = f'{api_root_path}/messages'
        models_path = f'{api_root_path}/models'
        return {
            'root_url': urllib.parse.urlunsplit((parts.scheme, parts.netloc, api_root_path, '', '')),
            'messages_url': urllib.parse.urlunsplit((parts.scheme, parts.netloc, messages_path, '', '')),
            'models_url': urllib.parse.urlunsplit((parts.scheme, parts.netloc, models_path, '', '')),
        }

    @staticmethod
    def _normalize_gemini_model_name(model_name):
        value = str(model_name or '').strip()
        if value.startswith('models/'):
            return value.split('/', 1)[1].strip()
        return value

    @classmethod
    def _resolve_gemini_urls(cls, base_url, model_name=''):
        normalized_base_url = (
            str(base_url or 'https://generativelanguage.googleapis.com/v1beta').strip()
            or 'https://generativelanguage.googleapis.com/v1beta'
        )
        parts = urllib.parse.urlsplit(normalized_base_url)
        path = parts.path.rstrip('/')
        root_path = path

        if ':generateContent' in path and '/models/' in path:
            root_path = path.split('/models/', 1)[0]
        elif path.endswith('/models'):
            root_path = path[:-len('/models')]
        elif '/models/' in path:
            root_path = path.split('/models/', 1)[0]

        if not root_path.endswith('/v1beta') and not root_path.endswith('/v1'):
            root_path = f'{root_path}/v1beta' if root_path else '/v1beta'

        models_path = f'{root_path}/models'
        request_model = cls._normalize_gemini_model_name(model_name)
        encoded_model = urllib.parse.quote(request_model, safe='-._~')
        generate_content_path = f'{models_path}/{encoded_model}:generateContent' if encoded_model else ''
        return {
            'root_url': urllib.parse.urlunsplit((parts.scheme, parts.netloc, root_path, '', '')),
            'models_url': urllib.parse.urlunsplit((parts.scheme, parts.netloc, models_path, '', '')),
            'generate_content_url': urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, generate_content_path, '', '')
            ) if generate_content_path else '',
        }

    @staticmethod
    def _parse_model_mapping(raw_mapping):
        mapping = {}
        text = str(raw_mapping or '').strip()
        if not text:
            return mapping

        for item in re.split(r'[\r\n,;?]+', text):
            entry = str(item or '').strip()
            if not entry:
                continue
            delimiter = ':' if ':' in entry else '=' if '=' in entry else ''
            if not delimiter:
                continue
            source, target = entry.split(delimiter, 1)
            source = source.strip()
            target = target.strip()
            if source and target:
                mapping[source] = target
        return mapping

    def _apply_model_mapping(self, model_name, cfg):
        current_model = str(model_name or '').strip()
        if not current_model:
            return current_model, False

        mappings = self._parse_model_mapping((cfg or {}).get('model_mapping', ''))
        if not mappings:
            return current_model, False

        if current_model in mappings:
            return mappings[current_model], True

        lowered = current_model.lower()
        for source, target in mappings.items():
            if source.lower() == lowered:
                return target, True
        return current_model, False

    def _resolve_request_model_name(self, provider_name, cfg):
        model_name = str((cfg or {}).get('model', '') or '').strip() or 'unknown'
        if provider_name not in {HANDLER_OPENAI, HANDLER_GEMINI}:
            return model_name
        mapped_model, _mapping_hit = self._apply_model_mapping(model_name, cfg)
        if provider_name == HANDLER_GEMINI:
            return self._normalize_gemini_model_name(mapped_model or model_name)
        return mapped_model or model_name

    @staticmethod
    def _load_extra_json_payload(cfg):
        raw = str((cfg or {}).get('extra_json', '') or '').strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f'额外请求体 JSON 格式错误: {exc}') from exc
        if not isinstance(payload, dict):
            raise ValueError('额外请求体 JSON 必须是一个对象')
        return payload

    @staticmethod
    def _merge_openai_extra_json(base_payload, extra_payload):
        merged = dict(base_payload or {})
        ignored_keys = []
        removed_keys = []
        protected_keys = {
            'model',
            'messages',
            'system',
            'prompt',
            'input',
        }
        for key, value in dict(extra_payload or {}).items():
            if key in protected_keys:
                ignored_keys.append(str(key))
                continue
            if value is None:
                if key in merged:
                    merged.pop(key, None)
                    removed_keys.append(str(key))
                continue
            merged[key] = value

        if 'max_completion_tokens' in extra_payload and 'max_tokens' in merged and 'max_tokens' not in extra_payload:
            merged.pop('max_tokens', None)
            removed_keys.append('max_tokens')

        return merged, sorted(set(ignored_keys)), sorted(set(removed_keys))

    @staticmethod
    def _normalize_model_identifier(model_name):
        return re.sub(r'[\s_]+', '-', str(model_name or '').strip().lower())

    @classmethod
    def _uses_openai_reasoning_parameter_rules(cls, model_name):
        normalized = cls._normalize_model_identifier(model_name)
        if not normalized:
            return False
        return normalized.startswith(('gpt-5', 'o1', 'o3', 'o4'))

    @classmethod
    def _should_eagerly_apply_openai_reasoning_rules(cls, cfg):
        record = dict(cfg or {})
        provider_type = normalize_provider_type(record.get('provider_type', ''))
        if provider_type == 'openai':
            return True
        base_url = str(record.get('base_url', '') or '').strip()
        if not base_url:
            return False
        host = urllib.parse.urlsplit(base_url).netloc.strip().lower()
        return host == 'api.openai.com'

    @classmethod
    def _apply_openai_reasoning_parameter_rules(cls, payload, request_model, cfg=None):
        merged = dict(payload or {})
        applied_rules = []
        if not cls._uses_openai_reasoning_parameter_rules(request_model):
            return merged, applied_rules
        if not cls._should_eagerly_apply_openai_reasoning_rules(cfg):
            return merged, applied_rules

        if 'temperature' in merged:
            merged.pop('temperature', None)
            applied_rules.append('remove_temperature')
        if 'max_tokens' in merged and 'max_completion_tokens' not in merged:
            merged['max_completion_tokens'] = merged.pop('max_tokens')
            applied_rules.append('max_tokens_to_max_completion_tokens')
        return merged, applied_rules

    @classmethod
    def _apply_openai_error_compatibility_retry(cls, payload, error_message):
        merged = dict(payload or {})
        message = str(error_message or '').strip()
        lowered = message.lower()
        applied_rules = []

        if (
            'max_tokens' in merged
            and 'max_completion_tokens' not in merged
            and 'max_tokens' in lowered
            and any(fragment in lowered for fragment in ('unsupported', 'not supported', 'reasoning'))
        ):
            merged['max_completion_tokens'] = merged.pop('max_tokens')
            applied_rules.append('max_tokens_to_max_completion_tokens')

        if (
            'temperature' in merged
            and 'temperature' in lowered
            and any(fragment in lowered for fragment in ('unsupported', 'not supported', 'does not support'))
        ):
            merged.pop('temperature', None)
            applied_rules.append('remove_temperature')

        return merged, applied_rules

    @staticmethod
    def _merge_gemini_extra_json(base_payload, extra_payload):
        merged = dict(base_payload or {})
        ignored_keys = []
        removed_keys = []
        protected_keys = {
            'contents',
            'systemInstruction',
            'model',
        }
        for key, value in dict(extra_payload or {}).items():
            if key in protected_keys:
                ignored_keys.append(str(key))
                continue
            if value is None:
                if key in merged:
                    merged.pop(key, None)
                    removed_keys.append(str(key))
                continue
            merged[key] = value
        return merged, sorted(set(ignored_keys)), sorted(set(removed_keys))

    def _prepare_openai_compat_request(self, prompt, system, cfg, temperature, max_tokens):
        key = str((cfg or {}).get('key', '') or '').strip()
        if not key:
            raise ValueError('OpenAI API Key is not configured')

        urls = self._resolve_openai_compat_urls(
            (cfg or {}).get('base_url', 'https://api.openai.com/v1'),
            provider_type=(cfg or {}).get('provider_type', ''),
        )
        configured_model = str((cfg or {}).get('model', '') or '').strip()
        if not configured_model:
            provider_type = normalize_provider_type((cfg or {}).get('provider_type', ''))
            if provider_type == 'sub2api':
                raise ValueError('Sub2API 模型未配置。请在 API 配置中指定具体的模型名称（如 gpt-3.5-turbo、gpt-4 等）')
            configured_model = 'gpt-4o'
        request_model, mapping_hit = self._apply_model_mapping(configured_model, cfg)
        auth_field = self._normalize_auth_field((cfg or {}).get('auth_field', 'Authorization'))
        auth_value_mode = self._normalize_auth_value_mode(
            (cfg or {}).get('auth_value_mode', AUTH_VALUE_MODE_BEARER),
            default=AUTH_VALUE_MODE_BEARER,
        )

        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})

        payload = {
            'model': request_model,
            'messages': messages,
        }
        if temperature is not None:
            payload['temperature'] = temperature
        if max_tokens is not None:
            payload['max_tokens'] = max_tokens
        extra_json_payload = self._load_extra_json_payload(cfg)
        payload, ignored_keys, removed_keys = self._merge_openai_extra_json(payload, extra_json_payload)
        payload, compatibility_rules = self._apply_openai_reasoning_parameter_rules(payload, request_model, cfg=cfg)
        removed_keys.extend(
            'temperature' if rule == 'remove_temperature' else 'max_tokens'
            for rule in compatibility_rules
            if rule in {'remove_temperature', 'max_tokens_to_max_completion_tokens'}
        )
        headers = self._build_auth_headers(
            key,
            auth_field=auth_field,
            auth_value_mode=auth_value_mode,
            include_content_type=True,
        )
        headers, extra_headers_payload, extra_header_keys, ignored_extra_header_keys = self._merge_request_headers(
            headers,
            cfg,
            protected_fields=self.PROTECTED_AUTH_FIELDS | {'Content-Type'},
        )

        return {
            'url': urls['chat_completions_url'],
            'models_url': urls['models_url'],
            'root_url': urls['root_url'],
            'payload': payload,
            'headers': headers,
            'request_model': request_model,
            'auth_field': auth_field,
            'auth_value_mode': auth_value_mode,
            'model_mapping_hit': mapping_hit,
            'extra_json_keys': sorted(extra_json_payload.keys()),
            'ignored_extra_json_keys': ignored_keys,
            'removed_extra_json_keys': removed_keys,
            'compatibility_rules': sorted(set(compatibility_rules)),
            'extra_header_keys': extra_header_keys,
            'ignored_extra_header_keys': ignored_extra_header_keys,
        }

    def _build_gemini_headers(self, cfg):
        key = str((cfg or {}).get('key', '') or '').strip()
        if not key:
            raise ValueError('Gemini API Key is not configured')

        raw_auth_field = str((cfg or {}).get('auth_field', '') or '').strip()
        auth_field = raw_auth_field or 'x-goog-api-key'
        fallback_mode = AUTH_VALUE_MODE_BEARER if auth_field.lower() in {'authorization', 'proxy-authorization'} else AUTH_VALUE_MODE_RAW
        auth_value_mode = self._normalize_auth_value_mode(
            (cfg or {}).get('auth_value_mode', fallback_mode),
            default=fallback_mode,
        )
        use_bearer = auth_value_mode == AUTH_VALUE_MODE_BEARER
        headers = self._build_auth_headers(
            key,
            auth_field=auth_field,
            auth_value_mode=auth_value_mode,
            include_content_type=True,
        )
        headers, _extra_headers_payload, extra_header_keys, ignored_extra_header_keys = self._merge_request_headers(
            headers,
            cfg,
            protected_fields=self.PROTECTED_AUTH_FIELDS | {'Content-Type'},
        )
        return {
            'headers': headers,
            'auth_field': auth_field,
            'auth_value_mode': auth_value_mode,
            'use_bearer': use_bearer,
            'extra_header_keys': extra_header_keys,
            'ignored_extra_header_keys': ignored_extra_header_keys,
        }

    def _prepare_gemini_request(self, prompt, system, cfg, temperature, max_tokens):
        key = str((cfg or {}).get('key', '') or '').strip()
        if not key:
            raise ValueError('Gemini API Key is not configured')

        configured_model = (
            str((cfg or {}).get('model', '') or '').strip()
            or get_preset_definition('gemini').get('model', 'gemini-2.5-flash')
        )
        request_model, mapping_hit = self._apply_model_mapping(configured_model, cfg)
        request_model = self._normalize_gemini_model_name(request_model or configured_model)
        urls = self._resolve_gemini_urls(
            (cfg or {}).get('base_url', 'https://generativelanguage.googleapis.com/v1beta'),
            request_model,
        )
        if not urls['generate_content_url']:
            raise ValueError('Gemini request URL is invalid')

        generation_config = {}
        if temperature is not None:
            generation_config['temperature'] = temperature
        if max_tokens is not None:
            generation_config['maxOutputTokens'] = max_tokens
        top_p = self._coerce_float((cfg or {}).get('top_p', ''), default=None)
        if top_p is not None:
            generation_config['topP'] = top_p
        presence_penalty = self._coerce_float((cfg or {}).get('presence_penalty', ''), default=None)
        if presence_penalty is not None:
            generation_config['presencePenalty'] = presence_penalty
        frequency_penalty = self._coerce_float((cfg or {}).get('frequency_penalty', ''), default=None)
        if frequency_penalty is not None:
            generation_config['frequencyPenalty'] = frequency_penalty

        payload = {
            'contents': [
                {
                    'role': 'user',
                    'parts': [{'text': prompt}],
                }
            ],
        }
        if generation_config:
            payload['generationConfig'] = generation_config
        if system:
            payload['systemInstruction'] = {
                'role': 'system',
                'parts': [{'text': system}],
            }

        extra_json_payload = self._load_extra_json_payload(cfg)
        payload, ignored_keys, removed_keys = self._merge_gemini_extra_json(payload, extra_json_payload)
        header_bundle = self._build_gemini_headers(cfg)

        return {
            'url': urls['generate_content_url'],
            'models_url': urls['models_url'],
            'root_url': urls['root_url'],
            'payload': payload,
            'headers': header_bundle['headers'],
            'auth_field': header_bundle['auth_field'],
            'use_bearer': header_bundle['use_bearer'],
            'request_model': request_model,
            'model_mapping_hit': mapping_hit,
            'extra_json_keys': sorted(extra_json_payload.keys()),
            'ignored_extra_json_keys': ignored_keys,
            'removed_extra_json_keys': removed_keys,
            'extra_header_keys': header_bundle['extra_header_keys'],
            'ignored_extra_header_keys': header_bundle['ignored_extra_header_keys'],
        }

    @staticmethod
    def _extract_gemini_text_from_part(part):
        if isinstance(part, str):
            return part
        if not isinstance(part, dict):
            return ''
        text = part.get('text')
        if isinstance(text, str):
            return text
        return ''

    @classmethod
    def _extract_gemini_response_text(cls, payload):
        resp = dict(payload or {})
        candidates = resp.get('candidates') if isinstance(resp.get('candidates'), list) else []
        fragments = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get('content')
            if not isinstance(content, dict):
                continue
            parts = content.get('parts') if isinstance(content.get('parts'), list) else []
            for part in parts:
                fragment = cls._extract_gemini_text_from_part(part)
                if fragment:
                    fragments.append(fragment)

        text = '\n'.join(fragment for fragment in fragments if fragment).strip()
        prompt_feedback = resp.get('promptFeedback') if isinstance(resp.get('promptFeedback'), dict) else {}
        block_reason = str(prompt_feedback.get('blockReason', '') or '').strip()
        finish_reasons = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            finish_reason = str(candidate.get('finishReason', '') or '').strip()
            if finish_reason:
                finish_reasons.append(finish_reason)

        return {
            'text': text,
            'text_source': 'candidates.content.parts.text' if text else 'empty',
            'content_kind': 'gemini_parts' if text else 'empty',
            'has_choices': bool(candidates),
            'has_output_text': bool(text),
            'block_reason': block_reason,
            'finish_reasons': sorted(set(finish_reasons)),
        }

    @classmethod
    def _extract_openai_text_from_content_item(cls, item):
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            return ''

        text_value = item.get('text')
        if isinstance(text_value, str):
            return text_value
        if isinstance(text_value, dict):
            nested_text = text_value.get('value') or text_value.get('text') or ''
            if isinstance(nested_text, str):
                return nested_text

        output_text = item.get('output_text')
        if isinstance(output_text, str):
            return output_text
        if isinstance(output_text, dict):
            nested_output_text = output_text.get('value') or output_text.get('text') or ''
            if isinstance(nested_output_text, str):
                return nested_output_text

        for field_name in ('content', 'refusal', 'reasoning_content', 'reasoning'):
            field_value = item.get(field_name)
            if isinstance(field_value, str):
                return field_value
            if isinstance(field_value, (list, dict)):
                nested_text, _content_kind = cls._extract_openai_text_from_content(field_value)
                if nested_text:
                    return nested_text
        return ''

    @classmethod
    def _extract_openai_text_from_content(cls, content):
        if isinstance(content, str):
            return content, 'string'
        if isinstance(content, list):
            fragments = []
            for item in content:
                fragment = cls._extract_openai_text_from_content_item(item)
                if fragment:
                    fragments.append(fragment)
            return '\n'.join(fragment for fragment in fragments if fragment), 'array'
        if isinstance(content, dict):
            fragment = cls._extract_openai_text_from_content_item(content)
            if fragment:
                return fragment, 'object'
        return '', 'empty'

    @classmethod
    def _extract_openai_text_from_output_items(cls, output_items):
        if not isinstance(output_items, list):
            return ''

        fragments = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content = item.get('content')
            fragment, _content_kind = cls._extract_openai_text_from_content(content)
            if fragment:
                fragments.append(fragment)
                continue
            fallback_fragment = cls._extract_openai_text_from_content_item(item)
            if fallback_fragment:
                fragments.append(fallback_fragment)
        return '\n'.join(fragment for fragment in fragments if fragment)

    @classmethod
    def _extract_openai_message_fallback_text(cls, message):
        if not isinstance(message, dict):
            return '', 'empty'
        for field_name in ('refusal', 'reasoning_content', 'reasoning'):
            field_value = message.get(field_name)
            text, content_kind = cls._extract_openai_text_from_content(field_value)
            if text:
                return text, f'message_{field_name}_{content_kind}'
        return '', 'empty'

    @classmethod
    def _extract_openai_recursive_text(cls, value, *, depth=0):
        if depth > 6:
            return ''
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            fragments = []
            for item in value:
                fragment = cls._extract_openai_recursive_text(item, depth=depth + 1)
                if fragment:
                    fragments.append(fragment)
            return '\n'.join(fragment for fragment in fragments if fragment)
        if not isinstance(value, dict):
            return ''

        preferred_fields = (
            'text',
            'output_text',
            'answer',
            'response',
            'result',
            'content',
            'parts',
            'message',
            'delta',
            'refusal',
            'reasoning_content',
            'reasoning',
            'thinking',
            'value',
        )
        for field_name in preferred_fields:
            if field_name not in value:
                continue
            fragment = cls._extract_openai_recursive_text(value.get(field_name), depth=depth + 1)
            if fragment:
                return fragment
        return ''

    @staticmethod
    def _extract_openai_error_message(payload):
        resp = dict(payload or {})
        error = resp.get('error')
        if isinstance(error, str):
            return error.strip()
        if not isinstance(error, dict):
            return ''

        message = str(error.get('message', '') or '').strip()
        error_type = str(error.get('type', '') or '').strip()
        error_code = str(error.get('code', '') or '').strip()
        error_param = str(error.get('param', '') or '').strip()

        details = []
        if error_type:
            details.append(f'type={error_type}')
        if error_code:
            details.append(f'code={error_code}')
        if error_param:
            details.append(f'param={error_param}')

        if not message:
            message = json.dumps(error, ensure_ascii=False)
        if details:
            return f'{message} ({", ".join(details)})'
        return message

    @staticmethod
    def _is_connection_response_valid(response_payload):
        payload = dict(response_payload or {})
        if bool(payload.get('has_choices', False)) or bool(payload.get('has_output_text', False)):
            return True
        usage = payload.get('usage', {})
        if isinstance(usage, dict) and any(value for value in usage.values()):
            return True
        response_model = str(payload.get('response_model', '') or '').strip()
        return bool(response_model)

    @classmethod
    def _extract_openai_response_text(cls, payload):
        resp = dict(payload or {})
        has_choices = isinstance(resp.get('choices'), list) and bool(resp.get('choices'))
        has_output_text = bool(str(resp.get('output_text', '') or '').strip())
        has_output_items = isinstance(resp.get('output'), list) and bool(resp.get('output'))

        choices = resp.get('choices') if isinstance(resp.get('choices'), list) else []
        if choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = first_choice.get('message') if isinstance(first_choice.get('message'), dict) else {}
            message_content = message.get('content')
            text, content_kind = cls._extract_openai_text_from_content(message_content)
            if text:
                return {
                    'text': text,
                    'text_source': 'choices.message.content',
                    'content_kind': content_kind,
                    'has_choices': has_choices,
                    'has_output_text': has_output_text,
                }

            fallback_text, fallback_kind = cls._extract_openai_message_fallback_text(message)
            if fallback_text:
                return {
                    'text': fallback_text,
                    'text_source': 'choices.message.fallback',
                    'content_kind': fallback_kind,
                    'has_choices': has_choices,
                    'has_output_text': has_output_text,
                }

            delta = first_choice.get('delta') if isinstance(first_choice.get('delta'), dict) else {}
            delta_text, delta_kind = cls._extract_openai_text_from_content(delta.get('content'))
            if delta_text:
                return {
                    'text': delta_text,
                    'text_source': 'choices.delta.content',
                    'content_kind': delta_kind,
                    'has_choices': has_choices,
                    'has_output_text': has_output_text,
                }

            delta_fallback_text, delta_fallback_kind = cls._extract_openai_message_fallback_text(delta)
            if delta_fallback_text:
                return {
                    'text': delta_fallback_text,
                    'text_source': 'choices.delta.fallback',
                    'content_kind': delta_fallback_kind,
                    'has_choices': has_choices,
                    'has_output_text': has_output_text,
                }

            choice_text = first_choice.get('text')
            if isinstance(choice_text, str) and choice_text:
                return {
                    'text': choice_text,
                    'text_source': 'choices.text',
                    'content_kind': 'legacy_text',
                    'has_choices': has_choices,
                    'has_output_text': has_output_text,
                }

        output_text = resp.get('output_text')
        if isinstance(output_text, str) and output_text:
            return {
                'text': output_text,
                'text_source': 'output_text',
                'content_kind': 'responses_output_text',
                'has_choices': has_choices,
                'has_output_text': has_output_text,
            }

        output_items_text = cls._extract_openai_text_from_output_items(resp.get('output'))
        if output_items_text:
            return {
                'text': output_items_text,
                'text_source': 'output.content',
                'content_kind': 'responses_output_array',
                'has_choices': has_choices,
                'has_output_text': has_output_text or has_output_items,
            }

        recursive_choice_text = cls._extract_openai_recursive_text(choices[0]) if choices else ''
        if recursive_choice_text:
            return {
                'text': recursive_choice_text,
                'text_source': 'choices.recursive',
                'content_kind': 'recursive',
                'has_choices': has_choices,
                'has_output_text': has_output_text or has_output_items,
            }

        return {
            'text': '',
            'text_source': 'empty',
            'content_kind': 'empty',
            'has_choices': has_choices,
            'has_output_text': has_output_text or has_output_items,
        }

    def _build_claude_headers(self, cfg):
        key = str((cfg or {}).get('key', '') or '').strip()
        if not key:
            raise ValueError('Claude API Key is not configured')
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': key,
            'anthropic-version': ANTHROPIC_VERSION,
        }
        headers, _extra_headers_payload, _added_keys, _ignored_keys = self._merge_request_headers(
            headers,
            cfg,
            protected_fields=self.PROTECTED_AUTH_FIELDS | {'anthropic-version', 'Content-Type'},
        )
        return headers

    @staticmethod
    def _merge_model_candidates(model_ids, current_model=''):
        merged = []
        seen = set()
        current = str(current_model or '').strip()
        for model_id in ([current] if current else []) + list(model_ids or []):
            value = str(model_id or '').strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(value)
        return merged

    @staticmethod
    def _extract_model_ids(payload):
        items = payload.get('data') or payload.get('models') or []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get('id') or item.get('name') or ''
            if model_id:
                result.append(model_id)
        return sorted(result)

    @classmethod
    def _extract_gemini_model_ids(cls, payload):
        items = payload.get('models') or payload.get('data') or []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            supported_methods = item.get('supportedGenerationMethods')
            if isinstance(supported_methods, list) and supported_methods:
                normalized_methods = {str(method or '').strip() for method in supported_methods}
                if 'generateContent' not in normalized_methods:
                    continue
            model_id = item.get('id') or item.get('name') or ''
            model_id = cls._normalize_gemini_model_name(model_id)
            if model_id:
                result.append(model_id)
        return sorted(set(result), key=lambda item: item.lower())

    def _fetch_remote_models(self, provider_name, cfg, *, timeout=15):
        key = str(cfg.get('key', '') or '').strip()
        base_url = str(cfg.get('base_url', '') or '').strip()
        if not key or not base_url:
            raise ValueError('Please fill in both API Key and base URL first')

        if provider_name == HANDLER_OPENAI:
            auth_field = self._normalize_auth_field(cfg.get('auth_field', 'Authorization'))
            auth_value_mode = self._normalize_auth_value_mode(
                cfg.get('auth_value_mode', AUTH_VALUE_MODE_BEARER),
                default=AUTH_VALUE_MODE_BEARER,
            )
            url = self._resolve_openai_compat_urls(base_url, provider_type=cfg.get('provider_type', ''))['models_url']
            headers = self._build_auth_headers(
                key,
                auth_field=auth_field,
                auth_value_mode=auth_value_mode,
                include_content_type=True,
            )
            headers, _extra_headers_payload, extra_header_keys, ignored_extra_header_keys = self._merge_request_headers(
                headers,
                cfg,
                protected_fields=self.PROTECTED_AUTH_FIELDS | {'Content-Type'},
            )
            extractor = self._extract_model_ids
        elif provider_name == HANDLER_CLAUDE:
            auth_field = 'x-api-key'
            url = self._resolve_claude_urls(base_url)['models_url']
            headers = self._build_claude_headers(cfg)
            extra_header_keys = []
            ignored_extra_header_keys = []
            extractor = self._extract_model_ids
        elif provider_name == HANDLER_GEMINI:
            header_bundle = self._build_gemini_headers(cfg)
            auth_field = header_bundle['auth_field']
            url = self._resolve_gemini_urls(base_url)['models_url']
            headers = header_bundle['headers']
            extra_header_keys = header_bundle['extra_header_keys']
            ignored_extra_header_keys = header_bundle['ignored_extra_header_keys']
            extractor = self._extract_gemini_model_ids
        elif provider_name == HANDLER_BEDROCK_RESERVED:
            raise NotImplementedError('AWS Bedrock model list fetch is not implemented yet')
        else:
            raise ValueError(f'不支持的 API 或协议: {provider_name}')

        service_provider_name = self._resolve_service_provider_name((cfg or {}).get('provider_type') or provider_name, cfg)
        self._log(
            '[fetch_models_remote] '
            f'provider={service_provider_name} '
            f'handler={provider_name} '
            f'endpoint={self._sanitize_url_for_log(url)} '
            f'auth_field={auth_field} '
            f'extra_header_keys={",".join(extra_header_keys) or "-"} '
            f'ignored_extra_header_keys={",".join(ignored_extra_header_keys) or "-"}'
        )
        payload = self._http_get_json(url, headers=headers, timeout=timeout)
        return extractor(payload)

    def call(
        self,
        prompt: str,
        system: str = '',
        api_name: str = None,
        on_complete: Callable[[str], None] = None,
        on_error: Callable[[str], None] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cfg: dict = None,
        usage_context: dict = None,
        disable_skills: bool = False,
    ):
        """Call the AI API asynchronously and return the result by callback."""
        if api_name is None:
            api_name = self._resolve_api_for_request(api_name, usage_context, cfg)

        def _run():
            try:
                call_result = self._call_sync(
                    prompt,
                    system,
                    api_name,
                    temperature,
                    max_tokens,
                    cfg=cfg,
                    usage_context=usage_context,
                    disable_skills=disable_skills,
                )
                if on_complete:
                    on_complete(call_result)
            except Exception as e:
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    def call_sync(
        self,
        prompt: str,
        system: str = '',
        api_name: str = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        request_timeout: float = None,
        model_override: str = None,
        cfg: dict = None,
        usage_context: dict = None,
        disable_skills: bool = False,
    ) -> str:
        """同步调用 AI API"""
        if api_name is None:
            api_name = self._resolve_api_for_request(api_name, usage_context, cfg)
        return self._call_sync(
            prompt,
            system,
            api_name,
            temperature,
            max_tokens,
            request_timeout=request_timeout,
            model_override=model_override,
            cfg=cfg,
            usage_context=usage_context,
            disable_skills=disable_skills,
        )

    def call_json_sync(
        self,
        prompt: str,
        system: str = '',
        api_name: str = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        request_timeout: float = None,
        model_override: str = None,
        cfg: dict = None,
        schema_name: str = '',
        usage_context: dict = None,
        disable_skills: bool = False,
    ):
        """Call the AI API and force JSON parsing."""
        schema_hint = f' (schema={schema_name})' if schema_name else ''
        enforced_prompt = (
            f'{prompt}\n\n'
            f'Return only valid JSON. Do not include Markdown fences, explanations, or extra text{schema_hint}.'
        )
        raw = self.call_sync(
            enforced_prompt,
            system=system,
            api_name=api_name,
            temperature=temperature,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
            model_override=model_override,
            cfg=cfg,
            usage_context=usage_context,
            disable_skills=disable_skills,
        )
        payload = self._extract_json_payload(raw)
        try:
            return json.loads(payload)
        except Exception as exc:
            raise ValueError(f'AI did not return valid JSON: {exc}') from exc

    def _call_sync(
        self,
        prompt,
        system,
        api_name,
        temperature,
        max_tokens,
        request_timeout=None,
        model_override=None,
        cfg=None,
        usage_context=None,
        return_payload=False,
        allow_empty_response=False,
        disable_skills=False,
    ):
        handlers = {
            HANDLER_OPENAI: self._call_openai_compat,
            HANDLER_CLAUDE: self._call_claude,
            HANDLER_GEMINI: self._call_gemini,
            HANDLER_BEDROCK_RESERVED: self._call_reserved_protocol,
        }
        cfg = self._get_config(api_name, cfg=cfg)
        handler_name = self._resolve_handler_name(api_name, cfg)
        handler = handlers.get(handler_name)
        if not handler:
            if not api_name:
                raise ValueError('未配置活动模型。请前往「模型配置」页面配置 AI 模型并将其设为活动状态。')
            raise ValueError(f'不支持的 API 或协议: {api_name} (handler={handler_name})')
        if model_override:
            cfg['model'] = model_override
        cfg = self._resolve_effective_request_config(cfg)
        provider_name = self._resolve_service_provider_name(api_name, cfg)
        handler_name = self._resolve_handler_name(api_name, cfg)
        model_name = self._resolve_request_model_name(handler_name, cfg)
        temperature_value = self._resolve_request_temperature(cfg, temperature)
        max_tokens_value = self._resolve_request_max_tokens(cfg, max_tokens)
        request_state = {
            'prompt': prompt,
            'system': system,
            'temperature': temperature_value,
            'max_tokens': max_tokens_value,
            'metadata': {},
            'applied_skills': [],
        }
        if not disable_skills and getattr(self, 'skills_runtime', None):
            try:
                request_state = self.skills_runtime.prepare_request(
                    prompt,
                    system,
                    temperature=temperature_value,
                    max_tokens=max_tokens_value,
                    usage_context=usage_context,
                )
                prompt = request_state.get('prompt', prompt)
                system = request_state.get('system', system)
                temperature_value = request_state.get('temperature', temperature_value)
                max_tokens_value = request_state.get('max_tokens', max_tokens_value)
            except Exception as exc:
                self._log(f'[skills_hook_error] stage=prepare_request error={exc}', level='WARN')
        timeout_value = self._resolve_request_timeout(
            cfg,
            request_timeout,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens_value,
        )
        started_at = time.perf_counter()
        request_id = uuid.uuid4().hex
        self._log(
            '[api_request] '
            f'api={api_name or "active"} '
            f'provider={provider_name} '
            f'handler={handler_name} '
            f'model={model_name} '
            f'prompt_len={len(prompt or "")} '
            f'system_len={len(system or "")} '
            f'max_tokens={max_tokens_value if max_tokens_value is not None else "-"} '
            f'temperature={temperature_value if temperature_value is not None else "-"} '
            f'timeout={timeout_value}'
        )
        try:
            response_payload = handler(
                prompt,
                system,
                cfg,
                temperature_value,
                max_tokens_value,
                request_timeout=timeout_value,
                allow_empty_response=allow_empty_response,
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            error_detail = self._build_usage_request_detail(
                usage_context=usage_context,
                request_id=request_id,
                api_name=api_name,
                provider_name=provider_name,
                handler_name=handler_name,
                request_model=model_name,
                response_model='',
                status='error',
                duration_ms=elapsed_ms,
                timeout_sec=timeout_value,
                temperature=temperature_value,
                max_tokens=max_tokens_value,
                prompt=prompt,
                system=system,
                request_detail=getattr(exc, 'detail', {}),
                error_message=str(exc),
            )
            self._log(
                '[api_error] '
                f'api={api_name or "active"} '
                f'provider={provider_name} '
                f'handler={handler_name} '
                f'model={model_name} '
                f'elapsed_ms={elapsed_ms} '
                f'error={exc}',
                level='ERROR',
            )
            self._record_usage_event(
                request_id=request_id,
                usage_context=usage_context,
                api_name=api_name,
                provider_name=provider_name,
                handler_name=handler_name,
                request_model=model_name,
                response_model='',
                cfg=cfg,
                duration_ms=elapsed_ms,
                usage={},
                status='error',
                error_message=str(exc),
                request_detail=error_detail,
            )
            raise
        result_text = str(response_payload.get('text', '') or '')
        if not disable_skills and getattr(self, 'skills_runtime', None):
            try:
                finalized = self.skills_runtime.finalize_response(
                    result_text,
                    request_state=request_state,
                    usage_context=usage_context,
                )
                result_text = str(finalized.get('text', result_text) or '')
                response_payload = dict(response_payload or {})
                response_payload['text'] = result_text
                response_payload['skill_metadata'] = finalized.get('metadata', {})
            except Exception as exc:
                self._log(f'[skills_hook_error] stage=finalize_response error={exc}', level='WARN')
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        result_preview = result_text.strip().replace('\n', ' ')[:80]
        success_detail = self._build_usage_request_detail(
            usage_context=usage_context,
            request_id=request_id,
            api_name=api_name,
            provider_name=provider_name,
            handler_name=handler_name,
            request_model=model_name,
            response_model=response_payload.get('response_model', ''),
            status='success',
            duration_ms=elapsed_ms,
            timeout_sec=timeout_value,
            temperature=temperature_value,
            max_tokens=max_tokens_value,
            prompt=prompt,
            system=system,
            request_detail=response_payload.get('request_detail', {}),
            error_message='',
        )
        self._log(
            '[api_response] '
            f'api={api_name or "active"} '
            f'provider={provider_name} '
            f'handler={handler_name} '
            f'model={model_name} '
            f'elapsed_ms={elapsed_ms} '
            f'result_len={len(result_text)} '
            f'text_source={response_payload.get("text_source", "-")} '
            f'content_kind={response_payload.get("content_kind", "-")} '
            f'result_preview_len={len(result_preview)} '
            f'has_choices={int(bool(response_payload.get("has_choices", False)))} '
            f'has_output_text={int(bool(response_payload.get("has_output_text", False)))}'
        )
        self._record_usage_event(
            request_id=request_id,
            usage_context=usage_context,
            api_name=api_name,
            provider_name=provider_name,
            handler_name=handler_name,
            request_model=model_name,
            response_model=response_payload.get('response_model', ''),
            cfg=cfg,
            duration_ms=elapsed_ms,
            usage=response_payload.get('usage', {}) or {},
            status='success',
            error_message='',
            request_detail=success_detail,
        )
        if return_payload:
            return result_text, dict(response_payload or {})
        return result_text

    def _call_reserved_protocol(self, prompt, system, cfg, temperature, max_tokens, request_timeout=None, allow_empty_response=False):
        api_format = normalize_api_format((cfg or {}).get('api_format', ''))
        if api_format == API_FORMAT_AWS_BEDROCK_RESERVED:
            raise NotImplementedError('AWS Bedrock protocol is reserved but not implemented yet')
        raise ValueError(f'Unsupported protocol: {api_format}')

    def _http_post(self, url, data: dict, headers: dict, timeout=60) -> dict:
        """Send an HTTP POST request."""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        safe_url = self._sanitize_url_for_log(url)
        self._log(f'[http_post] url={safe_url} timeout={timeout} body_bytes={len(body)}')
        max_attempts = self._resolve_transport_retry_attempts()
        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()
            req = urllib.request.Request(url, data=body, headers=headers, method='POST')
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode('utf-8')
                    self._log(
                        '[http_response] '
                        f'url={safe_url} status={getattr(resp, "status", "unknown")} '
                        f'elapsed_ms={int((time.perf_counter() - started_at) * 1000)} '
                        f'body_chars={len(raw)}'
                    )
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        error_message = self._build_invalid_json_response_error(raw)
                        self._log(
                            '[http_error] '
                            f'url={safe_url} '
                            f'elapsed_ms={int((time.perf_counter() - started_at) * 1000)} '
                            f'error={error_message}',
                            level='ERROR',
                        )
                        raise APIRequestError(
                            error_message,
                            detail={
                                'response': self._build_response_debug_section(
                                    body=raw,
                                    extra={'status_code': getattr(resp, 'status', '')},
                                )
                            },
                        ) from exc
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode('utf-8', errors='replace')
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                error_message = self._format_http_error_message(exc.code, err_body)
                if self._is_retryable_http_status(exc.code) and attempt < max_attempts:
                    # 检测速率限制，使用更长的延迟
                    is_rate_limited = exc.code in {429, 430}
                    delay_seconds = self._resolve_transport_retry_delay(attempt, is_rate_limited=is_rate_limited)
                    self._log(
                        '[http_retry] '
                        f'method=POST url={safe_url} '
                        f'attempt={attempt}/{max_attempts} '
                        f'status={exc.code} '
                        f'elapsed_ms={elapsed_ms} '
                        f'delay_ms={int(delay_seconds * 1000)} '
                        f'error={error_message}',
                        level='WARN',
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    continue
                self._log(
                    '[http_error] '
                    f'url={safe_url} status={exc.code} '
                    f'elapsed_ms={elapsed_ms} '
                    f'error={error_message}',
                    level='ERROR',
                )
                raise APIRequestError(
                    error_message,
                    detail={
                        'response': self._build_response_debug_section(
                            body=err_body,
                            extra={'status_code': exc.code},
                        )
                    },
                )
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                error_message = self._format_transport_error(exc)
                is_timeout_error = self._is_timeout_transport_error(exc)
                
                # 如果单次尝试耗时极长（如超过 30 秒）且失败，通常重试意义不大，直接抛出以节省用户时间
                if elapsed_ms > 30000 and attempt < max_attempts:
                    self._log(
                        '[http_skip_retry] '
                        f'url={safe_url} '
                        f'elapsed_ms={elapsed_ms} '
                        'duration too long, skipping further retries',
                        level='WARN'
                    )
                    raise APIRequestError(
                        f'请求响应过慢 ({int(elapsed_ms/1000)}s) 且失败。请检查网络状态或尝试更换模型。',
                        detail={'error': {'transport_message': error_message}},
                    ) from exc

                if self._is_retryable_transport_error(exc) and not is_timeout_error and attempt < max_attempts:
                    delay_seconds = self._resolve_transport_retry_delay(attempt)
                    self._log(
                        '[http_retry] '
                        f'method=POST url={safe_url} '
                        f'attempt={attempt}/{max_attempts} '
                        f'elapsed_ms={elapsed_ms} '
                        f'delay_ms={int(delay_seconds * 1000)} '
                        f'error={error_message}',
                        level='WARN',
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    continue
                self._log(
                    '[http_error] '
                    f'url={safe_url} '
                    f'elapsed_ms={elapsed_ms} '
                    f'error={error_message}',
                    level='ERROR',
                )
                if isinstance(exc, urllib.error.URLError):
                    raise APIRequestError(
                        f'网络传输异常: {error_message}',
                        detail={'error': {'transport_message': error_message}},
                    )
                raise APIRequestError(
                    error_message,
                    detail={'error': {'transport_message': error_message}},
                )

    def _http_get_json(self, url, headers: dict, timeout=15) -> dict:
        """Send an HTTP GET request and parse JSON."""
        safe_url = self._sanitize_url_for_log(url)
        self._log(f'[http_get] url={safe_url} timeout={timeout}')
        max_attempts = self._resolve_transport_retry_attempts()
        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()
            req = urllib.request.Request(url, headers=headers, method='GET')
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode('utf-8')
                    self._log(
                        '[http_response] '
                        f'url={safe_url} status={getattr(resp, "status", "unknown")} '
                        f'elapsed_ms={int((time.perf_counter() - started_at) * 1000)} '
                        f'body_chars={len(raw)}'
                    )
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        error_message = self._build_invalid_json_response_error(raw)
                        self._log(
                            '[http_error] '
                            f'url={safe_url} '
                            f'elapsed_ms={int((time.perf_counter() - started_at) * 1000)} '
                            f'error={error_message}',
                            level='ERROR',
                        )
                        raise RuntimeError(error_message) from exc
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode('utf-8', errors='replace')
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                error_message = self._format_http_error_message(exc.code, err_body)
                if self._is_retryable_http_status(exc.code) and attempt < max_attempts:
                    # 检测速率限制，使用更长的延迟
                    is_rate_limited = exc.code in {429, 430}
                    delay_seconds = self._resolve_transport_retry_delay(attempt, is_rate_limited=is_rate_limited)
                    self._log(
                        '[http_retry] '
                        f'method=GET url={safe_url} '
                        f'attempt={attempt}/{max_attempts} '
                        f'status={exc.code} '
                        f'elapsed_ms={elapsed_ms} '
                        f'delay_ms={int(delay_seconds * 1000)} '
                        f'error={error_message}',
                        level='WARN',
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    continue
                self._log(
                    '[http_error] '
                    f'url={safe_url} status={exc.code} '
                    f'elapsed_ms={elapsed_ms} '
                    f'error={error_message}',
                    level='ERROR',
                )
                raise RuntimeError(error_message)
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                error_message = self._format_transport_error(exc)
                if self._is_retryable_transport_error(exc) and attempt < max_attempts:
                    delay_seconds = self._resolve_transport_retry_delay(attempt)
                    self._log(
                        '[http_retry] '
                        f'method=GET url={safe_url} '
                        f'attempt={attempt}/{max_attempts} '
                        f'elapsed_ms={elapsed_ms} '
                        f'delay_ms={int(delay_seconds * 1000)} '
                        f'error={error_message}',
                        level='WARN',
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    continue
                self._log(
                    '[http_error] '
                    f'url={safe_url} '
                    f'elapsed_ms={elapsed_ms} '
                    f'error={error_message}',
                    level='ERROR',
                )
                if isinstance(exc, urllib.error.URLError):
                    raise RuntimeError(f'网络传输异常: {error_message}')
                raise RuntimeError(error_message)
    def _call_openai_compat(self, prompt, system, cfg, temperature, max_tokens, request_timeout=None, allow_empty_response=False):
        """Call an OpenAI-compatible endpoint."""
        prepared = self._prepare_openai_compat_request(prompt, system, cfg, temperature, max_tokens)
        compatibility_rules = sorted(set(prepared.get('compatibility_rules', [])))
        request_extra = {
            'auth_field': prepared['auth_field'],
            'request_model': prepared['request_model'],
            'model_mapping_hit': bool(prepared['model_mapping_hit']),
            'compatibility_rules': compatibility_rules,
            'extra_json_keys': list(prepared.get('extra_json_keys', []) or []),
            'ignored_extra_json_keys': list(prepared.get('ignored_extra_json_keys', []) or []),
            'removed_extra_json_keys': list(prepared.get('removed_extra_json_keys', []) or []),
            'extra_header_keys': list(prepared.get('extra_header_keys', []) or []),
            'ignored_extra_header_keys': list(prepared.get('ignored_extra_header_keys', []) or []),
        }
        self._log(
            '[openai_compat] '
            f'endpoint={self._sanitize_url_for_log(prepared["url"])} '
            f'auth_field={prepared["auth_field"]} '
            f'model={prepared["request_model"]} '
            f'model_mapping_hit={prepared["model_mapping_hit"]} '
            f'extra_json_keys={",".join(prepared["extra_json_keys"]) or "-"} '
            f'ignored_extra_json_keys={",".join(prepared["ignored_extra_json_keys"]) or "-"} '
            f'removed_extra_json_keys={",".join(prepared["removed_extra_json_keys"]) or "-"} '
            f'compatibility_rules={",".join(compatibility_rules) or "-"} '
            f'extra_header_keys={",".join(prepared["extra_header_keys"]) or "-"} '
            f'ignored_extra_header_keys={",".join(prepared["ignored_extra_header_keys"]) or "-"} '
            f'timeout={request_timeout}'
        )
        payload = dict(prepared['payload'])
        compatibility_retry_attempts = 0
        while True:
            request_detail = self._build_request_debug_section(
                method='POST',
                url=prepared['url'],
                headers=prepared['headers'],
                body=payload,
                timeout=request_timeout,
                prompt=prompt,
                system=system,
                extra=request_extra,
            )
            try:
                resp = self._http_post(
                    prepared['url'],
                    payload,
                    prepared['headers'],
                    timeout=request_timeout,
                )
            except Exception as exc:
                adjusted_payload, retry_rules = self._apply_openai_error_compatibility_retry(payload, str(exc))
                if retry_rules and adjusted_payload != payload and compatibility_retry_attempts < 2:
                    compatibility_retry_attempts += 1
                    self._log(
                        '[openai_compat_retry] '
                        f'endpoint={self._sanitize_url_for_log(prepared["url"])} '
                        f'model={prepared["request_model"]} '
                        f'attempt={compatibility_retry_attempts} '
                        f'rules={",".join(retry_rules)} '
                        f'error={exc}',
                        level='WARN',
                    )
                    payload = adjusted_payload
                    continue
                raise self._wrap_request_error(exc, detail={'request': request_detail}) from exc
            error_message = self._extract_openai_error_message(resp)
            if error_message:
                adjusted_payload, retry_rules = self._apply_openai_error_compatibility_retry(payload, error_message)
                if retry_rules and adjusted_payload != payload and compatibility_retry_attempts < 2:
                    compatibility_retry_attempts += 1
                    self._log(
                        '[openai_compat_retry] '
                        f'endpoint={self._sanitize_url_for_log(prepared["url"])} '
                        f'model={prepared["request_model"]} '
                        f'attempt={compatibility_retry_attempts} '
                        f'rules={",".join(retry_rules)} '
                        f'error={error_message}',
                        level='WARN',
                    )
                    payload = adjusted_payload
                    continue
                response_detail = self._build_response_debug_section(
                    body=resp,
                    extra={
                        'response_model': str(resp.get('model', '') or prepared['request_model']),
                        'response_keys': sorted(str(key) for key in resp.keys()),
                    },
                )
                raise APIRequestError(
                    f'OpenAI 兼容接口返回错误: {error_message}',
                    detail={'request': request_detail, 'response': response_detail, 'error': {'message': error_message}},
                )
            break
        extracted = self._extract_openai_response_text(resp)
        if not extracted['text']:
            response_keys = ', '.join(sorted(str(key) for key in resp.keys()))
            response_detail = self._build_response_debug_section(
                body=resp,
                extra={
                    'response_model': str(resp.get('model', '') or prepared['request_model']),
                    'response_keys': sorted(str(key) for key in resp.keys()),
                },
            )
            if not allow_empty_response:
                raise APIRequestError(
                    f'OpenAI-compatible endpoint returned no displayable text. Response fields: {response_keys or "-"}',
                    detail={'request': request_detail, 'response': response_detail},
                )
            return {
                'text': '',
                'text_source': extracted['text_source'],
                'content_kind': extracted['content_kind'],
                'has_choices': extracted['has_choices'],
                'has_output_text': extracted['has_output_text'],
                'response_keys': sorted(str(key) for key in resp.keys()),
                'response_model': str(resp.get('model', '') or prepared['request_model']),
                'request_model': prepared['request_model'],
                'usage': extract_openai_usage(resp),
                'request_detail': {'request': request_detail, 'response': response_detail},
            }
        response_detail = self._build_response_debug_section(
            body=resp,
            extra={
                'response_model': str(resp.get('model', '') or prepared['request_model']),
                'text_source': extracted['text_source'],
                'content_kind': extracted['content_kind'],
                'has_choices': bool(extracted['has_choices']),
                'has_output_text': bool(extracted['has_output_text']),
                'response_keys': sorted(str(key) for key in resp.keys()),
                'text_preview': self._truncate_debug_text(extracted['text'], limit=1000),
            },
        )
        return {
            'text': extracted['text'],
            'text_source': extracted['text_source'],
            'content_kind': extracted['content_kind'],
            'has_choices': extracted['has_choices'],
            'has_output_text': extracted['has_output_text'],
            'response_keys': sorted(str(key) for key in resp.keys()),
            'response_model': str(resp.get('model', '') or prepared['request_model']),
            'request_model': prepared['request_model'],
            'usage': extract_openai_usage(resp),
            'request_detail': {'request': request_detail, 'response': response_detail},
        }

    def _call_claude(self, prompt, system, cfg, temperature, max_tokens, request_timeout=None, allow_empty_response=False):
        """閻犲鍟伴弫顥thropic Claude API"""
        urls = self._resolve_claude_urls(cfg.get('base_url', 'https://api.anthropic.com'))
        model = cfg.get('model', get_preset_definition('claude').get('model', 'claude-sonnet-4-6'))
        data = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if max_tokens is not None:
            data['max_tokens'] = max_tokens
        if temperature is not None:
            data['temperature'] = temperature
        top_p = self._coerce_float((cfg or {}).get('top_p', ''), default=None)
        if top_p is not None:
            data['top_p'] = top_p
        if system:
            data['system'] = system
        headers = self._build_claude_headers(cfg)
        request_detail = self._build_request_debug_section(
            method='POST',
            url=urls['messages_url'],
            headers=headers,
            body=data,
            timeout=request_timeout or 60,
            prompt=prompt,
            system=system,
            extra={'request_model': model},
        )
        try:
            resp = self._http_post(urls['messages_url'], data, headers, timeout=request_timeout or 60)
        except Exception as exc:
            raise self._wrap_request_error(exc, detail={'request': request_detail}) from exc
        response_detail = self._build_response_debug_section(
            body=resp,
            extra={
                'response_model': str(resp.get('model', '') or model),
                'text_preview': self._truncate_debug_text(resp['content'][0]['text'], limit=1000),
            },
        )
        return {
            'text': resp['content'][0]['text'],
            'response_model': str(resp.get('model', '') or model),
            'usage': extract_claude_usage(resp),
            'request_detail': {'request': request_detail, 'response': response_detail},
        }

    def _call_gemini(self, prompt, system, cfg, temperature, max_tokens, request_timeout=None, allow_empty_response=False):
        """Call the Gemini native API."""
        prepared = self._prepare_gemini_request(prompt, system, cfg, temperature, max_tokens)
        self._log(
            '[gemini] '
            f'endpoint={self._sanitize_url_for_log(prepared["url"])} '
            f'auth_field={prepared["auth_field"]} '
            f'use_bearer={int(prepared["use_bearer"])} '
            f'model={prepared["request_model"]} '
            f'model_mapping_hit={prepared["model_mapping_hit"]} '
            f'extra_json_keys={",".join(prepared["extra_json_keys"]) or "-"} '
            f'ignored_extra_json_keys={",".join(prepared["ignored_extra_json_keys"]) or "-"} '
            f'removed_extra_json_keys={",".join(prepared["removed_extra_json_keys"]) or "-"} '
            f'extra_header_keys={",".join(prepared["extra_header_keys"]) or "-"} '
            f'ignored_extra_header_keys={",".join(prepared["ignored_extra_header_keys"]) or "-"} '
            f'timeout={request_timeout}'
        )
        request_detail = self._build_request_debug_section(
            method='POST',
            url=prepared['url'],
            headers=prepared['headers'],
            body=prepared['payload'],
            timeout=request_timeout,
            prompt=prompt,
            system=system,
            extra={
                'auth_field': prepared['auth_field'],
                'use_bearer': bool(prepared['use_bearer']),
                'request_model': prepared['request_model'],
                'model_mapping_hit': bool(prepared['model_mapping_hit']),
                'extra_json_keys': list(prepared.get('extra_json_keys', []) or []),
                'ignored_extra_json_keys': list(prepared.get('ignored_extra_json_keys', []) or []),
                'removed_extra_json_keys': list(prepared.get('removed_extra_json_keys', []) or []),
                'extra_header_keys': list(prepared.get('extra_header_keys', []) or []),
                'ignored_extra_header_keys': list(prepared.get('ignored_extra_header_keys', []) or []),
            },
        )
        try:
            resp = self._http_post(
                prepared['url'],
                prepared['payload'],
                prepared['headers'],
                timeout=request_timeout,
            )
        except Exception as exc:
            raise self._wrap_request_error(exc, detail={'request': request_detail}) from exc
        extracted = self._extract_gemini_response_text(resp)
        if not extracted['text']:
            finish_reasons = ','.join(extracted['finish_reasons']) or '-'
            block_reason = extracted['block_reason'] or '-'
            response_detail = self._build_response_debug_section(
                body=resp,
                extra={
                    'response_model': str(resp.get('modelVersion', '') or resp.get('model', '') or prepared['request_model']),
                    'block_reason': block_reason,
                    'finish_reasons': finish_reasons,
                },
            )
            raise APIRequestError(
                f'Gemini 返回内容不可显示，block_reason={block_reason}，finish_reasons={finish_reasons}',
                detail={'request': request_detail, 'response': response_detail},
            )
        response_detail = self._build_response_debug_section(
            body=resp,
            extra={
                'response_model': str(resp.get('modelVersion', '') or resp.get('model', '') or prepared['request_model']),
                'text_source': extracted['text_source'],
                'content_kind': extracted['content_kind'],
                'has_choices': bool(extracted['has_choices']),
                'has_output_text': bool(extracted['has_output_text']),
                'block_reason': extracted['block_reason'],
                'finish_reasons': list(extracted['finish_reasons']),
                'text_preview': self._truncate_debug_text(extracted['text'], limit=1000),
            },
        )
        return {
            'text': extracted['text'],
            'text_source': extracted['text_source'],
            'content_kind': extracted['content_kind'],
            'has_choices': extracted['has_choices'],
            'has_output_text': extracted['has_output_text'],
            'response_model': str(resp.get('modelVersion', '') or resp.get('model', '') or prepared['request_model']),
            'request_model': prepared['request_model'],
            'usage': extract_gemini_usage(resp),
            'request_detail': {'request': request_detail, 'response': response_detail},
        }

    @staticmethod
    def _sanitize_url_for_log(url):
        parts = urllib.parse.urlsplit(str(url or ''))
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))

    @staticmethod
    def _is_retryable_transport_error(exc):
        if isinstance(exc, urllib.error.HTTPError):
            return False
        if isinstance(
            exc,
            (
                http.client.BadStatusLine,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                TimeoutError,
            ),
        ):
            return True
        if isinstance(exc, ssl.SSLError):
            return True
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            if isinstance(
                reason,
                (
                    ssl.SSLError,
                    http.client.BadStatusLine,
                    http.client.IncompleteRead,
                    http.client.RemoteDisconnected,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    TimeoutError,
                ),
            ):
                return True
            text = str(reason or '').strip()
            return any(
                fragment in text
                for fragment in (
                    'EOF occurred in violation of protocol',
                    'Remote end closed connection without response',
                    'Connection reset by peer',
                    'Connection aborted',
                    'timed out',
                )
            )
        text = str(exc or '').strip()
        return any(
            fragment in text
            for fragment in (
                'EOF occurred in violation of protocol',
                'Remote end closed connection without response',
                'Connection reset by peer',
                'Connection aborted',
                'timed out',
            )
        )

    @staticmethod
    def _format_transport_error(exc):
        text = str(exc or '').strip()
        if 'timestamp out of range' in text.lower():
            return '系统时钟异常或 SSL 证书日期无效 (timestamp out of range)。请检查您的 Windows 系统时间是否准确，或者 API 代理地址是否存在证书过期/无效日期问题。'
        
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            return str(reason or exc)
        return text or str(exc.__class__.__name__)

    @staticmethod
    def _is_timeout_transport_error(exc):
        if isinstance(exc, TimeoutError):
            return True
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                return True
            text = str(reason or '').strip().lower()
            return 'timed out' in text
        text = str(exc or '').strip().lower()
        return 'timed out' in text

    @classmethod
    def _resolve_transport_retry_attempts(cls):
        try:
            attempts = int(cls.DEFAULT_TRANSPORT_RETRY_ATTEMPTS)
        except Exception:
            attempts = 1
        return max(1, attempts)

    @classmethod
    def _resolve_transport_retry_delay(cls, attempt, is_rate_limited=False):
        """指数级退避+Jitter，而不是线性递增"""
        import random
        
        # 新策略：指数级退避 (0.1s -> 0.2s -> 0.4s)
        base_delay = 0.1  # 起始延迟100ms
        exponential_delay = base_delay * (2 ** (attempt - 1))
        
        # 对于速率限制(429)，使用更激进的延迟
        if is_rate_limited:
            exponential_delay = exponential_delay * 5
        
        # 添加随机jitter以避免雷群效应(±10%)
        jitter = random.uniform(0, exponential_delay * 0.1)
        
        # 设置延迟上限：
        # - 普通错误：最多5秒
        # - 速率限制：最多30秒
        max_delay = 30.0 if is_rate_limited else 5.0
        return min(exponential_delay + jitter, max_delay)

    def _record_usage_event(
        self,
        *,
        request_id,
        usage_context,
        api_name,
        provider_name,
        handler_name,
        request_model,
        response_model,
        cfg,
        duration_ms,
        usage,
        status,
        error_message,
        request_detail=None,
    ):
        if not getattr(self, 'usage_store', None):
            return
        try:
            billing = resolve_event_billing(self.config, api_name, cfg, response_model=str(response_model or ''))
            pricing_rule = find_pricing_rule(self.config, provider_name, billing['billed_model'])
            if not pricing_rule and str(handler_name or '').strip() and str(handler_name or '').strip() != str(provider_name or '').strip():
                pricing_rule = find_pricing_rule(self.config, handler_name, billing['billed_model'])
            total_cost = calculate_total_cost(usage, pricing_rule, billing['billing_multiplier'])
            context = dict(usage_context or {})
            event = UsageEvent(
                request_id=str(request_id or '').strip(),
                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                page_id=str(context.get('page_id', '') or '').strip(),
                scene_id=str(context.get('scene_id', '') or '').strip(),
                action=str(context.get('action', '') or '').strip(),
                api_id=str(api_name or self._get_active() or '').strip(),
                provider=str(provider_name or '').strip(),
                request_model=str(request_model or '').strip(),
                response_model=str(response_model or '').strip(),
                status=str(status or 'success').strip(),
                duration_ms=int(duration_ms or 0),
                first_token_ms=0,
                input_tokens=int(usage.get('input_tokens', 0) or 0),
                output_tokens=int(usage.get('output_tokens', 0) or 0),
                cache_create_tokens=int(usage.get('cache_create_tokens', 0) or 0),
                cache_hit_tokens=int(usage.get('cache_hit_tokens', 0) or 0),
                billing_multiplier=float(billing['billing_multiplier']),
                billing_mode=str(billing['billing_mode']),
                total_cost=total_cost,
                error_message=str(error_message or '').strip(),
                request_detail=normalize_request_detail(request_detail),
            )
            self.usage_store.append_event(event)
        except Exception as exc:
            self._log(f'[usage_event_error] {exc}', level='WARN')

    def _log(self, message, level='INFO'):
        if callable(self.log_callback):
            self.log_callback(message, level=level)

    def fetch_models(self, api_name: str, cfg: dict = None) -> list:
        """Test API connectivity and return (success, message)."""
        cfg = self._get_config(api_name, cfg=cfg)
        provider_type = normalize_provider_type(cfg.get('provider_type') or api_name)
        strategy = self._resolve_model_list_strategy(api_name, cfg)
        current_model = str(cfg.get('model', '') or '').strip()
        provider_name = self._resolve_handler_name(api_name, cfg)

        if strategy == MODEL_LIST_STATIC:
            static_models = get_static_models(provider_type)
            key = str(cfg.get('key', '') or '').strip()
            base_url = str(cfg.get('base_url', '') or '').strip()
            if key and base_url:
                try:
                    models = self._fetch_remote_models(provider_name, cfg, timeout=15)
                    return self._merge_model_candidates(models, current_model)
                except RuntimeError as exc:
                    error_text = str(exc)
                    if not (error_text.startswith('HTTP 404') or error_text.startswith('HTTP 405')):
                        raise
                    self._log(
                        '[fetch_models_static_fallback] '
                        f'provider={provider_type} '
                        f'reason={error_text}',
                        level='WARN',
                    )
            models = self._merge_model_candidates(static_models, current_model)
            self._log(
                '[fetch_models_static] '
                f'provider={provider_type} '
                f'models={",".join(models) or "-"}'
            )
            return models

        if strategy == MODEL_LIST_MANUAL:
            if current_model:
                return [current_model]
            raise ValueError(get_model_list_manual_message(provider_type, api_format=cfg.get('api_format', '')))

        if strategy == MODEL_LIST_UNAVAILABLE:
            raise NotImplementedError('Model list fetching is not implemented for the current protocol')

        result = self._fetch_remote_models(provider_name, cfg, timeout=15)
        return self._merge_model_candidates(result, current_model)

    def test_connection(
        self,
        api_name: str,
        prompt: str = '鐠囧嘲褰ч崶鐐差槻 ok',
        model_override: str = None,
        timeout: float = 45,
        degrade_threshold_ms: int = None,
        max_retries: int = 0,
        cfg: dict = None,
        usage_context: dict = None,
    ) -> tuple:
        """濞村鐦?API 鏉╃偞甯撮敍宀冪箲閸?(閺勵垰鎯侀幋鎰, 閹绘劗銇氬☉鍫熶紖)"""
        last_error = None
        attempts = max(1, int(max_retries) + 1)
        request_timeout = max(float(timeout or 45), 1.0)

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                call_result = self._call_sync(
                    prompt,
                    'Return the shortest possible result only.',
                    api_name,
                    0.0,
                    16,
                    request_timeout=request_timeout,
                    model_override=(model_override or '').strip() or None,
                    cfg=cfg,
                    usage_context=usage_context,
                    return_payload=True,
                    allow_empty_response=True,
                )
                if isinstance(call_result, tuple) and len(call_result) == 2:
                    result, response_payload = call_result
                else:
                    result, response_payload = call_result, {}
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                normalized_result = (result or '').strip()
                if not normalized_result:
                    if self._is_connection_response_valid(response_payload):
                        response_keys = ', '.join(response_payload.get('response_keys', []) or [])
                        success_message = 'Connection succeeded, but the response did not include displayable text.'
                        if response_keys:
                            success_message = f'{success_message} Response fields: {response_keys}'
                        if degrade_threshold_ms and elapsed_ms > int(degrade_threshold_ms):
                            return True, (
                                f'{success_message} But latency was {elapsed_ms}ms, above the degrade threshold {int(degrade_threshold_ms)}ms.'
                            )
                        retry_note = f', success on attempt {attempt}' if attempt > 1 else ''
                        return True, f'{success_message} Latency {elapsed_ms}ms{retry_note}.'
                    last_error = 'Connection succeeded but returned no displayable text. Check the model type or response format.'
                    continue

                response_preview = normalized_result.replace('\n', ' ')[:80]
                if degrade_threshold_ms and elapsed_ms > int(degrade_threshold_ms):
                    return True, (
                        f'Connection succeeded, but latency was {elapsed_ms}ms, above the degrade threshold {int(degrade_threshold_ms)}ms.'
                        f' Preview: {response_preview}'
                    )

                retry_note = f', success on attempt {attempt}' if attempt > 1 else ''
                return True, f'Connection succeeded, latency {elapsed_ms}ms{retry_note}. Preview: {response_preview}'
            except Exception as exc:
                last_error = str(exc)

        return False, f'Connection failed after {attempts} attempts. Last error: {last_error}'

    @staticmethod
    def _extract_json_payload(text: str) -> str:
        content = str(text or '').strip()
        if not content:
            raise ValueError('AI response is empty')

        fence_match = re.match(r'^```(?:json)?\s*(.*?)\s*```$', content, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

        candidates = []
        for opener, closer in (('{', '}'), ('[', ']')):
            start = content.find(opener)
            end = content.rfind(closer)
            if start >= 0 and end > start:
                candidate = content[start:end + 1].strip()
                if candidate:
                    candidates.append((start, candidate))
        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]
        return content
