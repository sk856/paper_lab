# -*- coding: utf-8 -*-
"""
AI 服务商预设注册表。
"""

from __future__ import annotations


HANDLER_OPENAI = 'openai'
HANDLER_CLAUDE = 'claude'
HANDLER_GEMINI = 'gemini'
HANDLER_BEDROCK_RESERVED = 'bedrock_reserved'

MODEL_LIST_REMOTE = 'remote'
MODEL_LIST_STATIC = 'static'
MODEL_LIST_MANUAL = 'manual'
MODEL_LIST_UNAVAILABLE = 'unavailable'

AUTH_VALUE_MODE_BEARER = 'bearer'
AUTH_VALUE_MODE_RAW = 'raw'
AUTH_OPTION_CUSTOM = 'custom'

API_FORMAT_OPENAI_CHAT_COMPLETIONS = 'openai_chat_completions'
API_FORMAT_ANTHROPIC_MESSAGES = 'anthropic_messages'
API_FORMAT_GOOGLE_GENERATIVE_AI = 'google_generative_ai'
API_FORMAT_AWS_BEDROCK_RESERVED = 'aws_bedrock_reserved'

ANTHROPIC_VERSION = '2023-06-01'

DEFAULT_EXTRA_HEADERS_HINT = (
    '填写 JSON 对象，为当前请求追加自定义请求头；不会覆盖认证字段和 Content-Type。'
)
BEDROCK_RESERVED_MESSAGE = 'AWS Bedrock 协议当前仅预留扩展位，暂未实现请求签名与模型列表获取。'
MANUAL_MODEL_LIST_MESSAGE = '当前模板不支持自动获取模型列表，请直接填写模型 ID。'
UNAVAILABLE_MODEL_LIST_MESSAGE = '当前协议暂未实现模型列表获取。'

API_FORMAT_ALIASES = {
    'openai': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
    'custom': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
    'claude': API_FORMAT_ANTHROPIC_MESSAGES,
    'anthropic': API_FORMAT_ANTHROPIC_MESSAGES,
    'gemini': API_FORMAT_GOOGLE_GENERATIVE_AI,
    API_FORMAT_OPENAI_CHAT_COMPLETIONS: API_FORMAT_OPENAI_CHAT_COMPLETIONS,
    API_FORMAT_ANTHROPIC_MESSAGES: API_FORMAT_ANTHROPIC_MESSAGES,
    API_FORMAT_GOOGLE_GENERATIVE_AI: API_FORMAT_GOOGLE_GENERATIVE_AI,
    API_FORMAT_AWS_BEDROCK_RESERVED: API_FORMAT_AWS_BEDROCK_RESERVED,
}

_PROTOCOL_DEFINITIONS = (
    {
        'id': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'label': 'OpenAI Chat Completions',
        'docs_url': 'https://platform.openai.com/docs/api-reference/chat',
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'ui_enabled': True,
        'default_auth_field': 'Authorization',
        'default_auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'auth_options': (
            {
                'id': 'authorization_bearer',
                'label': 'Authorization',
                'auth_field': 'Authorization',
                'auth_value_mode': AUTH_VALUE_MODE_BEARER,
            },
            {
                'id': 'proxy_authorization_bearer',
                'label': 'Proxy-Authorization',
                'auth_field': 'Proxy-Authorization',
                'auth_value_mode': AUTH_VALUE_MODE_BEARER,
            },
            {
                'id': 'api_key_raw',
                'label': 'api-key',
                'auth_field': 'api-key',
                'auth_value_mode': AUTH_VALUE_MODE_RAW,
            },
            {
                'id': 'x_api_key_raw',
                'label': 'x-api-key',
                'auth_field': 'x-api-key',
                'auth_value_mode': AUTH_VALUE_MODE_RAW,
            },
            {
                'id': AUTH_OPTION_CUSTOM,
                'label': '自定义',
            },
        ),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写兼容 OpenAI Chat Completions 的 API Key 或访问令牌。',
    },
    {
        'id': API_FORMAT_ANTHROPIC_MESSAGES,
        'label': 'Anthropic Messages',
        'docs_url': 'https://docs.anthropic.com/en/api/messages',
        'handler_name': HANDLER_CLAUDE,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'ui_enabled': True,
        'default_auth_field': 'x-api-key',
        'default_auth_value_mode': AUTH_VALUE_MODE_RAW,
        'auth_options': (
            {
                'id': 'anthropic_x_api_key',
                'label': 'x-api-key',
                'auth_field': 'x-api-key',
                'auth_value_mode': AUTH_VALUE_MODE_RAW,
            },
        ),
        'extra_headers_hint': 'Anthropic Messages 接口使用固定认证头，不需要在这里追加额外认证字段。',
        'credential_hint': '请填写 Anthropic Console 中生成的 API Key。',
    },
    {
        'id': API_FORMAT_GOOGLE_GENERATIVE_AI,
        'label': 'Google Generative AI',
        'docs_url': 'https://ai.google.dev/gemini-api/docs',
        'handler_name': HANDLER_GEMINI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'ui_enabled': True,
        'default_auth_field': 'x-goog-api-key',
        'default_auth_value_mode': AUTH_VALUE_MODE_RAW,
        'auth_options': (
            {
                'id': 'x_goog_api_key_raw',
                'label': 'x-goog-api-key',
                'auth_field': 'x-goog-api-key',
                'auth_value_mode': AUTH_VALUE_MODE_RAW,
            },
            {
                'id': 'authorization_bearer',
                'label': 'Authorization',
                'auth_field': 'Authorization',
                'auth_value_mode': AUTH_VALUE_MODE_BEARER,
            },
            {
                'id': 'proxy_authorization_bearer',
                'label': 'Proxy-Authorization',
                'auth_field': 'Proxy-Authorization',
                'auth_value_mode': AUTH_VALUE_MODE_BEARER,
            },
            {
                'id': AUTH_OPTION_CUSTOM,
                'label': '自定义',
            },
        ),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 Google Generative AI 对应的 API Key 或代理访问令牌。',
    },
    {
        'id': API_FORMAT_AWS_BEDROCK_RESERVED,
        'label': 'AWS Bedrock（预留）',
        'docs_url': 'https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started-api-ex-python.html',
        'handler_name': HANDLER_BEDROCK_RESERVED,
        'model_list_strategy': MODEL_LIST_UNAVAILABLE,
        'ui_enabled': False,
        'default_auth_field': 'Authorization',
        'default_auth_value_mode': AUTH_VALUE_MODE_RAW,
        'auth_options': (
            {
                'id': 'bedrock_reserved',
                'label': '暂未实现',
                'auth_field': 'Authorization',
                'auth_value_mode': AUTH_VALUE_MODE_RAW,
            },
        ),
        'extra_headers_hint': BEDROCK_RESERVED_MESSAGE,
        'credential_hint': BEDROCK_RESERVED_MESSAGE,
    },
)

PROTOCOL_REGISTRY = {
    item['id']: dict(
        item,
        auth_options=tuple(dict(option) for option in item.get('auth_options', ())),
    )
    for item in _PROTOCOL_DEFINITIONS
}

_PRESET_DEFINITIONS = (
    {
        'id': 'custom',
        'label': '自定义',
        'website': '',
        'docs_url': 'https://platform.openai.com/docs/api-reference/chat',
        'base_url': '',
        'model': '',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'openrouter',
        'label': 'OpenRouter',
        'website': 'https://openrouter.ai',
        'docs_url': 'https://openrouter.ai/docs/quickstart',
        'base_url': 'https://openrouter.ai/api/v1',
        'model': 'openai/gpt-4o-mini',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': (
            '填写 JSON 对象，为 OpenAI 兼容请求追加自定义请求头。'
            'OpenRouter 常见字段包括 HTTP-Referer、X-Title。'
            '示例：{"HTTP-Referer": "https://your-app.example", "X-Title": "纸研社"}'
        ),
        'credential_hint': '',
    },
    {
        'id': 'openai',
        'label': 'OpenAI',
        'website': 'https://openai.com',
        'docs_url': 'https://platform.openai.com/docs/api-reference/models/list',
        'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4o',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'claude',
        'label': 'Claude',
        'website': 'https://anthropic.com',
        'docs_url': 'https://docs.anthropic.com/en/api/models-list',
        'base_url': 'https://api.anthropic.com',
        'model': 'claude-sonnet-4-6',
        'api_format': API_FORMAT_ANTHROPIC_MESSAGES,
        'auth_field': 'x-api-key',
        'auth_value_mode': AUTH_VALUE_MODE_RAW,
        'handler_name': HANDLER_CLAUDE,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': 'Claude 原生接口使用固定请求头，不需要在这里追加额外认证字段。',
        'credential_hint': '请填写 Anthropic Console 中生成的 API Key。',
    },
    {
        'id': 'gemini',
        'label': 'Google Gemini',
        'website': 'https://ai.google.dev',
        'docs_url': 'https://ai.google.dev/api/rest/generativelanguage/models/list',
        'base_url': 'https://generativelanguage.googleapis.com/v1beta',
        'model': 'gemini-2.5-flash',
        'api_format': API_FORMAT_GOOGLE_GENERATIVE_AI,
        'auth_field': 'x-goog-api-key',
        'auth_value_mode': AUTH_VALUE_MODE_RAW,
        'handler_name': HANDLER_GEMINI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 Google Gemini Developer API 密钥。',
    },
    {
        'id': 'newapi',
        'label': 'New API·OpenAI',
        'website': 'https://www.newapi.ai/',
        'docs_url': 'https://www.newapi.ai/zh/docs',
        'base_url': 'https://your-newapi-domain/v1',
        'model': '',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 New API 站点提供的访问令牌。',
    },
    {
        'id': 'newapi_gemini',
        'label': 'New API·Gemini',
        'website': 'https://www.newapi.ai/',
        'docs_url': 'https://www.newapi.ai/zh/docs',
        'base_url': 'https://your-newapi-domain/v1beta',
        'model': 'gemini-2.5-flash',
        'api_format': API_FORMAT_GOOGLE_GENERATIVE_AI,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_GEMINI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 New API 站点提供的访问令牌。',
    },
    {
        'id': 'sub2api',
        'label': 'Sub2API·OpenAI',
        'website': 'https://github.com/Wei-Shaw/sub2api',
        'docs_url': 'https://github.com/Wei-Shaw/sub2api',
        'base_url': 'https://your-sub2api-domain/v1',
        'model': '',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 Sub2API 站点提供的访问令牌。',
    },
    {
        'id': 'sub2api_gemini',
        'label': 'Sub2API·Gemini',
        'website': 'https://github.com/Wei-Shaw/sub2api',
        'docs_url': 'https://github.com/Wei-Shaw/sub2api',
        'base_url': 'https://your-sub2api-domain/v1beta',
        'model': 'gemini-2.5-flash',
        'api_format': API_FORMAT_GOOGLE_GENERATIVE_AI,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_GEMINI,
        'model_list_strategy': MODEL_LIST_REMOTE,
        'static_models': (),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写 Sub2API 站点提供的访问令牌。',
    },
    {
        'id': 'deepseek',
        'label': 'DeepSeek',
        'website': 'https://platform.deepseek.com',
        'docs_url': 'https://api-docs.deepseek.com',
        'base_url': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('deepseek-chat', 'deepseek-reasoner'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'doubao',
        'label': '豆包',
        'website': 'https://www.volcengine.com/product/doubao',
        'docs_url': 'https://www.volcengine.com/docs/82379',
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'model': 'doubao-pro-4k',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('doubao-pro-4k',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'zhipu',
        'label': '智谱',
        'website': 'https://open.bigmodel.cn',
        'docs_url': 'https://open.bigmodel.cn/dev/howuse/model',
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'model': 'glm-4-plus',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('glm-4-plus', 'glm-4-air', 'glm-4-flash'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'tongyi',
        'label': '通义',
        'website': 'https://dashscope.aliyun.com',
        'docs_url': 'https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen-plus',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('qwen-plus', 'qwen-turbo', 'qwen-max'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'baidu',
        'label': '文心一言',
        'website': 'https://cloud.baidu.com',
        'docs_url': 'https://cloud.baidu.com/doc/qianfan/s/Hmh4suq26',
        'base_url': 'https://qianfan.baidubce.com/v2',
        'model': 'ernie-4.5-8k-preview',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('ernie-4.5-8k-preview',),
        'extra_headers_hint': (
            '填写 JSON 对象，为百度千帆兼容模式追加自定义请求头。'
            '如需补充应用级标识，请按官方文档填写对应请求头字段。'
        ),
        'credential_hint': '请填写百度千帆 V2 API Key；如需区分调用量和账单，可在额外请求头 JSON 中追加 appid。',
    },
    {
        'id': 'spark',
        'label': '讯飞星火',
        'website': 'https://xinghuo.xfyun.cn',
        'docs_url': 'https://www.xfyun.cn/doc/spark/HTTP%E8%B0%83%E7%94%A8%E6%96%87%E6%A1%A3.html',
        'base_url': 'https://spark-api-open.xf-yun.com/v1',
        'model': '4.0Ultra',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('4.0Ultra', 'generalv3.5', 'max-32k', 'generalv3', 'pro-128k', 'lite'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '请填写讯飞控制台对应模型版本生成的 APIPassword。',
    },
    {
        'id': 'minimax',
        'label': 'MiniMax',
        'website': 'https://platform.minimaxi.com',
        'docs_url': 'https://platform.minimaxi.com/document',
        'base_url': 'https://api.minimaxi.chat/v1',
        'model': 'MiniMax-Text-01',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('MiniMax-Text-01',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'moonshot',
        'label': 'Moonshot',
        'website': 'https://platform.moonshot.cn',
        'docs_url': 'https://platform.moonshot.cn/docs',
        'base_url': 'https://api.moonshot.cn/v1',
        'model': 'moonshot-v1-8k',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'yi',
        'label': '零一万物',
        'website': 'https://platform.lingyiwanwu.com',
        'docs_url': 'https://platform.lingyiwanwu.com/docs',
        'base_url': 'https://api.lingyiwanwu.com/v1',
        'model': 'yi-large',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('yi-large', 'yi-lightning'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'siliconflow',
        'label': 'SiliconFlow',
        'website': 'https://siliconflow.cn',
        'docs_url': 'https://docs.siliconflow.cn',
        'base_url': 'https://api.siliconflow.cn/v1',
        'model': 'deepseek-ai/DeepSeek-V3',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('deepseek-ai/DeepSeek-V3', 'deepseek-ai/DeepSeek-R1'),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'baichuan',
        'label': '百川',
        'website': 'https://platform.baichuan-ai.com',
        'docs_url': 'https://platform.baichuan-ai.com/docs',
        'base_url': 'https://api.baichuan-ai.com/v1',
        'model': 'Baichuan4',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('Baichuan4',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'hunyuan',
        'label': '混元',
        'website': 'https://cloud.tencent.com/product/hunyuan',
        'docs_url': 'https://cloud.tencent.com/document/product/1729',
        'base_url': 'https://api.hunyuan.cloud.tencent.com/v1',
        'model': 'hunyuan-turbo',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('hunyuan-turbo',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'sensenova',
        'label': '商汤日日新',
        'website': 'https://platform.sensenova.cn',
        'docs_url': 'https://platform.sensenova.cn/document',
        'base_url': 'https://api.sensenova.cn/compatible-mode/v1',
        'model': 'SenseChat-5',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('SenseChat-5',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'stepfun',
        'label': '阶跃星辰',
        'website': 'https://platform.stepfun.com',
        'docs_url': 'https://platform.stepfun.com/docs',
        'base_url': 'https://api.stepfun.com/v1',
        'model': 'step-2-16k',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('step-2-16k',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': '360ai',
        'label': '360 智脑',
        'website': 'https://ai.360.com',
        'docs_url': 'https://ai.360.com/platform/docs',
        'base_url': 'https://api.360.cn/v1',
        'model': '360gpt2-pro',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('360gpt2-pro',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
    {
        'id': 'tiangong',
        'label': '天工',
        'website': 'https://model.tiangong.cn',
        'docs_url': 'https://model.tiangong.cn/docs',
        'base_url': 'https://sky-api.singularity-ai.com/saas/api/v4',
        'model': 'tiangong-pro',
        'api_format': API_FORMAT_OPENAI_CHAT_COMPLETIONS,
        'auth_field': 'Authorization',
        'auth_value_mode': AUTH_VALUE_MODE_BEARER,
        'handler_name': HANDLER_OPENAI,
        'model_list_strategy': MODEL_LIST_STATIC,
        'static_models': ('tiangong-pro',),
        'extra_headers_hint': DEFAULT_EXTRA_HEADERS_HINT,
        'credential_hint': '',
    },
)

PROVIDER_TYPE_ALIASES = {
    'newapi_openai': 'newapi',
    'newapi_gemini': 'newapi',
    'sub2api_openai': 'sub2api',
    'sub2api_gemini': 'sub2api',
}

PRESET_LABEL_OVERRIDES = {
    'newapi': 'New API',
    'sub2api': 'Sub2API',
}

LEGACY_PRESET_IDS = set(PROVIDER_TYPE_ALIASES)

PRESET_REGISTRY = {
    item['id']: dict(item, label=PRESET_LABEL_OVERRIDES.get(item['id'], item['label']))
    for item in _PRESET_DEFINITIONS
}

PRESET_OPTIONS = [
    (
        item['id'],
        PRESET_LABEL_OVERRIDES.get(item['id'], item['label']),
        {
            'website': item['website'],
            'base_url': item['base_url'],
            'model': item['model'],
            'api_format': item['api_format'],
            'auth_field': item['auth_field'],
            'auth_value_mode': item['auth_value_mode'],
        },
    )
    for item in _PRESET_DEFINITIONS
    if item['id'] not in LEGACY_PRESET_IDS
]

PRESET_MAP = {
    item['id']: {
        'label': PRESET_LABEL_OVERRIDES.get(item['id'], item['label']),
        'defaults': {
            'website': item['website'],
            'base_url': item['base_url'],
            'model': item['model'],
            'api_format': item['api_format'],
            'auth_field': item['auth_field'],
            'auth_value_mode': item['auth_value_mode'],
        },
        'docs_url': item['docs_url'],
        'handler_name': item['handler_name'],
        'model_list_strategy': item['model_list_strategy'],
        'static_models': list(item['static_models']),
        'extra_headers_hint': item['extra_headers_hint'],
        'credential_hint': item['credential_hint'],
    }
    for item in _PRESET_DEFINITIONS
    if item['id'] not in LEGACY_PRESET_IDS
}


def normalize_api_format(api_format, default=API_FORMAT_OPENAI_CHAT_COMPLETIONS):
    value = str(api_format or '').strip().lower()
    value = API_FORMAT_ALIASES.get(value, value)
    return value if value in PROTOCOL_REGISTRY else default


def get_protocol_definition(api_format):
    return dict(PROTOCOL_REGISTRY[normalize_api_format(api_format)])


def list_protocol_definitions(include_hidden=False):
    definitions = [dict(item) for item in _PROTOCOL_DEFINITIONS]
    if include_hidden:
        return definitions
    return [item for item in definitions if item.get('ui_enabled')]


def list_visible_protocol_options():
    return [(item['id'], item['label']) for item in list_protocol_definitions(include_hidden=False)]


def get_protocol_default_auth(api_format):
    protocol = get_protocol_definition(api_format)
    return {
        'auth_field': protocol.get('default_auth_field', 'Authorization'),
        'auth_value_mode': protocol.get('default_auth_value_mode', AUTH_VALUE_MODE_BEARER),
    }


def get_protocol_auth_options(api_format):
    protocol = get_protocol_definition(api_format)
    return [dict(item) for item in protocol.get('auth_options', ())]


def format_auth_scheme_label(auth_field, auth_value_mode):
    field = str(auth_field or '').strip() or 'Authorization'
    return field


def resolve_auth_option_id(api_format, auth_field='', auth_value_mode=''):
    field = str(auth_field or '').strip().lower()
    mode = str(auth_value_mode or '').strip().lower()
    options = get_protocol_auth_options(api_format)
    if not options:
        return AUTH_OPTION_CUSTOM
    if not field and not mode:
        for option in options:
            option_id = str(option.get('id', '') or '').strip()
            if option_id and option_id != AUTH_OPTION_CUSTOM:
                return option_id
        return str(options[0].get('id', '') or '').strip()
    for option in options:
        option_id = str(option.get('id', '') or '').strip()
        if option_id == AUTH_OPTION_CUSTOM:
            continue
        if field == str(option.get('auth_field', '') or '').strip().lower() and mode == str(
            option.get('auth_value_mode', '') or ''
        ).strip().lower():
            return option_id
    if field or mode:
        for option in options:
            if str(option.get('id', '') or '').strip() == AUTH_OPTION_CUSTOM:
                return AUTH_OPTION_CUSTOM
    for option in options:
        option_id = str(option.get('id', '') or '').strip()
        if option_id and option_id != AUTH_OPTION_CUSTOM:
            return option_id
    return str(options[0].get('id', '') or '').strip()


def resolve_auth_option_definition(api_format, option_id):
    wanted = str(option_id or '').strip()
    for option in get_protocol_auth_options(api_format):
        if str(option.get('id', '') or '').strip() == wanted:
            return dict(option)
    return {}


def get_protocol_auth_option_label(api_format, option_id):
    option = resolve_auth_option_definition(api_format, option_id)
    if option.get('label'):
        return str(option['label'])
    return format_auth_scheme_label(option.get('auth_field', ''), option.get('auth_value_mode', ''))


def get_api_format_label(api_format):
    return get_protocol_definition(api_format).get('label', 'OpenAI Chat Completions')


def normalize_provider_type(provider_type):
    value = str(provider_type or '').strip().lower()
    value = PROVIDER_TYPE_ALIASES.get(value, value)
    return value if value in PRESET_REGISTRY else 'custom'


def get_preset_definition(provider_type):
    return dict(PRESET_REGISTRY[normalize_provider_type(provider_type)])


def list_preset_definitions():
    return [dict(item) for item in _PRESET_DEFINITIONS]


def resolve_handler_name(provider_type, api_format=API_FORMAT_OPENAI_CHAT_COMPLETIONS):
    normalized_provider = normalize_provider_type(provider_type)
    if normalized_provider == 'custom':
        protocol = get_protocol_definition(api_format)
        return protocol['handler_name']
    return PRESET_REGISTRY[normalized_provider]['handler_name']


def resolve_model_list_strategy(provider_type, api_format=API_FORMAT_OPENAI_CHAT_COMPLETIONS):
    normalized_provider = normalize_provider_type(provider_type)
    if normalized_provider == 'custom':
        if normalize_api_format(api_format) == API_FORMAT_AWS_BEDROCK_RESERVED:
            return MODEL_LIST_UNAVAILABLE
        return MODEL_LIST_REMOTE
    return PRESET_REGISTRY[normalized_provider]['model_list_strategy']


def get_static_models(provider_type):
    return list(get_preset_definition(provider_type).get('static_models', ()))


def _resolve_display_meta(provider_type, api_format=None):
    normalized_provider = normalize_provider_type(provider_type)
    if normalized_provider == 'custom':
        return get_protocol_definition(api_format)
    return get_preset_definition(normalized_provider)


def get_docs_url(provider_type, api_format=None):
    meta = _resolve_display_meta(provider_type, api_format=api_format)
    return meta.get('docs_url') or ''


def get_extra_headers_hint(provider_type, api_format=None):
    meta = _resolve_display_meta(provider_type, api_format=api_format)
    return meta.get('extra_headers_hint') or DEFAULT_EXTRA_HEADERS_HINT


def get_credential_hint(provider_type, api_format=None):
    meta = _resolve_display_meta(provider_type, api_format=api_format)
    return meta.get('credential_hint') or ''


def get_model_list_manual_message(provider_type, api_format=None):
    strategy = resolve_model_list_strategy(provider_type, api_format=api_format)
    if strategy == MODEL_LIST_MANUAL:
        return MANUAL_MODEL_LIST_MESSAGE
    if strategy == MODEL_LIST_UNAVAILABLE:
        return UNAVAILABLE_MODEL_LIST_MESSAGE
    return ''
