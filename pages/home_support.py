# -*- coding: utf-8 -*-
"""
首页数据辅助函数。
"""

from __future__ import annotations

import re
from tkinter import messagebox

from modules.app_metadata import (
    MODULE_AI_REDUCE,
    MODULE_CORRECTION,
    MODULE_PAPER_WRITE,
    MODULE_PLAGIARISM,
    MODULE_POLISH,
)
from modules.config import resolve_model_display_name


TASK_TEXT = (
    '1. 先配置自己的模型 Key、端点和模型名称\n'
    '2. 导入论文文档或新建空白草稿开始写作\n'
    '3. 生成大纲、优化表达，并补齐摘要或结论\n'
    '4. 检查格式、差异和重复风险后再导出成文档'
)

STAGE_RULES = {
    MODULE_PAPER_WRITE: {
        'label': '论文写作中',
        'target': 'paper_write',
    },
    MODULE_POLISH: {
        'label': '待降AI检测',
        'target': 'ai_reduce',
    },
    MODULE_AI_REDUCE: {
        'label': '待降查重率',
        'target': 'plagiarism',
    },
    MODULE_PLAGIARISM: {
        'label': '待智能纠错',
        'target': 'correction',
    },
    MODULE_CORRECTION: {
        'label': '待导出归档',
        'target': 'history',
    },
}

SYSTEM_PROMPT_PREFIX = 'system_'


def get_active_model_label(config_mgr):
    api_id = config_mgr.active_api
    cfg = config_mgr.get_api_config(api_id) or {}
    name = (cfg.get('name') or '').strip() or api_id
    model = resolve_model_display_name(cfg)
    return f'{name} / {model}' if model else name


def active_model_ready(config_mgr):
    active_name = config_mgr.active_api
    current = config_mgr.get_api_config(active_name)
    if not current:
        return False

    for field in ('name', 'key', 'base_url'):
        if not str(current.get(field, '') or '').strip():
            return False
    return True


def ensure_model_configured(config_mgr, parent_frame, app_bridge=None):
    """检查AI模型是否已配置，未配置则弹窗提醒。返回 True 表示已配置。"""
    if active_model_ready(config_mgr):
        return True
    messagebox.showwarning(
        '模型未配置',
        'AI模型尚未配置，请先前往「模型配置」页面完成配置后再使用此功能。',
        parent=parent_frame,
    )
    if app_bridge:
        try:
            app_bridge.show_api_config()
        except Exception:
            pass
    return False


def count_text_metrics(text):
    clean_text = (text or '').strip()
    if not clean_text:
        return {
            'word_count': 0,
            'paragraph_count': 0,
            'sentence_count': 0,
        }

    compact_text = re.sub(r'\s+', '', clean_text)
    paragraphs = [part for part in re.split(r'\n+', clean_text) if part.strip()]
    sentences = [part for part in re.split(r'[。！？?!]+', clean_text) if part.strip()]
    return {
        'word_count': len(compact_text),
        'paragraph_count': len(paragraphs) or 1,
        'sentence_count': len(sentences) or 1,
    }


def pick_latest_text(records):
    ordered = sorted(records or [], key=lambda item: item.get('time', ''), reverse=True)
    for record in ordered:
        for key in ('output_full', 'output', 'input_full', 'input'):
            value = (record.get(key) or '').strip()
            if value:
                return value
    return ''


def _latest_record(history_mgr):
    if not history_mgr:
        return None
    records = history_mgr.get_all()
    return records[0] if records else None


def _workspace_state(config_mgr, page_id):
    if not config_mgr or not hasattr(config_mgr, 'get_workspace_state'):
        return {}
    state = config_mgr.get_workspace_state(page_id, default={})
    return dict(state or {}) if isinstance(state, dict) else {}


def _extract_paper_write_text(state):
    if not isinstance(state, dict):
        return ''
    editor_text = str(state.get('editor_text', '') or '').strip()
    if editor_text:
        return editor_text
    current_section = str(state.get('current_section', '') or '').strip()
    sections = state.get('sections', {})
    if current_section and isinstance(sections, dict):
        return str(sections.get(current_section, '') or '').strip()
    if isinstance(sections, dict):
        for value in sections.values():
            text = str(value or '').strip()
            if text:
                return text
    return str(state.get('outline_text', '') or '').strip()


def _extract_transform_text(state):
    if not isinstance(state, dict):
        return ''
    for key in ('output_text', 'preview_text', 'input_text'):
        value = str(state.get(key, '') or '').strip()
        if value:
            return value
    return ''


def _extract_polish_text(state):
    if not isinstance(state, dict):
        return ''
    for key in ('latest_result_text', 'preview_text', 'input_text'):
        value = str(state.get(key, '') or '').strip()
        if value:
            return value
    return ''


def _extract_correction_text(state):
    if not isinstance(state, dict):
        return ''
    run = state.get('current_run', {})
    if isinstance(run, dict):
        for key in ('corrected_text', 'input_text'):
            value = str(run.get(key, '') or '').strip()
            if value:
                return value
    return str(state.get('input_text', '') or '').strip()


def _extract_workspace_text(config_mgr, page_id):
    state = _workspace_state(config_mgr, page_id)
    if page_id == 'paper_write':
        return _extract_paper_write_text(state)
    if page_id in {'ai_reduce', 'plagiarism'}:
        return _extract_transform_text(state)
    if page_id == 'polish':
        return _extract_polish_text(state)
    if page_id == 'correction':
        return _extract_correction_text(state)
    return ''


def _resolve_current_paper_topic(config_mgr):
    paper_state = _workspace_state(config_mgr, 'paper_write')
    topic = str(paper_state.get('topic', '') or '').strip()
    if topic:
        return topic

    return '未命名文稿'


def _resolve_stage(model_ready, latest_record, config_mgr):
    if not model_ready:
        return {
            'label': '待配置模型',
            'target': 'api_config',
        }

    if latest_record:
        module_name = str(latest_record.get('module', '') or '').strip()
        if module_name in STAGE_RULES:
            return dict(STAGE_RULES[module_name])

    paper_state = _workspace_state(config_mgr, 'paper_write')
    if any(
        str(paper_state.get(key, '') or '').strip()
        for key in ('topic', 'outline_text', 'editor_text')
    ):
        return {
            'label': '论文写作中',
            'target': 'paper_write',
        }
    if isinstance(paper_state.get('sections', {}), dict) and paper_state.get('sections'):
        return {
            'label': '论文写作中',
            'target': 'paper_write',
        }

    return {
        'label': '待导入文稿',
        'target': 'paper_write',
    }


def _resolve_current_word_count(config_mgr, stage_target, latest_record):
    text = _extract_workspace_text(config_mgr, stage_target)
    if not text and latest_record:
        text = pick_latest_text([latest_record])
    if not text:
        for page_id in ('paper_write', 'polish', 'ai_reduce', 'plagiarism', 'correction'):
            text = _extract_workspace_text(config_mgr, page_id)
            if text:
                break
    return count_text_metrics(text).get('word_count', 0)


def _count_pending_risks(config_mgr):
    total = 0

    correction_state = _workspace_state(config_mgr, 'correction')
    current_run = correction_state.get('current_run', {})
    if isinstance(current_run, dict):
        counts = current_run.get('counts', {})
        if isinstance(counts, dict):
            total += int(counts.get('pending', 0) or 0)

    for page_id in ('ai_reduce', 'plagiarism'):
        state = _workspace_state(config_mgr, page_id)
        annotations = state.get('annotations', [])
        if not annotations:
            session = state.get('import_session', {})
            if isinstance(session, dict):
                annotations = session.get('annotations', [])
        for item in list(annotations or []):
            if not isinstance(item, dict):
                continue
            risk_level = str(item.get('risk_level', '') or '').strip().lower()
            if risk_level and risk_level != 'safe':
                total += 1
    return total


def _modified_prompt_scenes(config_mgr):
    if not config_mgr or not hasattr(config_mgr, 'get_all_prompt_scenes'):
        return []
    scenes = config_mgr.get_all_prompt_scenes() or {}
    modified = []
    for scene_id, scene in scenes.items():
        if not isinstance(scene, dict):
            continue
        active_prompt_id = str(scene.get('active_prompt_id', '') or '').strip()
        if not active_prompt_id:
            continue
        prompts = scene.get('prompts', [])
        active_prompt = next(
            (prompt for prompt in prompts if isinstance(prompt, dict) and prompt.get('id') == active_prompt_id),
            None,
        )
        default_prompt_id = f'{SYSTEM_PROMPT_PREFIX}{str(scene_id).replace(".", "_")}'
        if not active_prompt:
            continue
        if active_prompt.get('source') != 'system' or active_prompt_id != default_prompt_id:
            modified.append(str(scene_id))
    return modified


def build_system_status_items(config_mgr):
    items = []

    if not active_model_ready(config_mgr):
        items.append({
            'level': 'error',
            'title': '模型未配置',
            'detail': '当前激活模型配置不完整，AI 功能暂时不可用。',
            'action_name': '前往配置',
            'action_kind': 'bridge',
            'action_value': 'show_api_config',
        })

    modified_scenes = _modified_prompt_scenes(config_mgr)
    if modified_scenes:
        items.append({
            'level': 'warning',
            'title': '提示词已修改',
            'detail': f'当前有 {len(modified_scenes)} 个场景使用了自定义提示词。',
            'action_name': '打开提示词',
            'action_kind': 'bridge',
            'action_value': 'show_prompt_manager',
        })

    if not bool(config_mgr.get_setting('auto_save_history', True)):
        items.append({
            'level': 'warning',
            'title': '历史自动保存关闭',
            'detail': '历史版本不会自动写入，本次处理结果可能无法回看。',
            'action_name': '打开设置',
            'action_kind': 'bridge',
            'action_value': 'show_settings',
        })

    failure = config_mgr.get_home_last_import_failure() if hasattr(config_mgr, 'get_home_last_import_failure') else None
    if failure:
        page_id = str(failure.get('page_id', '') or '').strip()
        file_name = str(failure.get('file_name', '') or '').strip() or '未命名文件'
        timestamp = str(failure.get('timestamp', '') or '').strip() or '未知时间'
        error_message = str(failure.get('error_message', '') or '').strip() or '导入失败'
        items.append({
            'level': 'error',
            'title': '最近导入失败',
            'detail': f'{timestamp} | {file_name} | {error_message}',
            'action_name': '前往页面',
            'action_kind': 'navigate',
            'action_value': page_id,
        })

    return items


def build_dashboard_view_model(show_home_stats, config_mgr, history_mgr, period_key='all'):
    stats = history_mgr.get_dashboard_stats(period_key) if show_home_stats and history_mgr else {
        'total': 0,
        'latest_time': '',
    }
    latest_record = _latest_record(history_mgr)
    latest_time = stats.get('latest_time') or '暂无记录'
    total = int(stats.get('total', 0) or 0)
    model_ready = active_model_ready(config_mgr)

    if not show_home_stats:
        tip_text = '今日建议：首页统计已关闭，你仍然可以从这里直接进入写作与润色流程。'
        board_text = '把模型配置、写作、润色、检测和导出串成一套真实的论文处理流程。'
        completion_hint = '静态首页\n视图'
    elif not model_ready:
        tip_text = '今日建议：先完成模型配置，再导入论文开始写作。'
        board_text = '先连通模型，再把写作、润色、检测和导出串起来。'
        completion_hint = f'最近处理\n{latest_time}'
    elif total == 0:
        tip_text = '今日建议：先导入论文文档，再生成大纲、摘要与正文草稿。'
        board_text = '本地优先的写作工作台，准备好后就从导入文稿开始。'
        completion_hint = '暂无本地\n处理记录'
    else:
        tip_text = '今日建议：先跑一轮润色，再检查格式与差异。'
        board_text = '把近期写作、润色、检测和导出数据放在同一块看板里，方便你继续推进当前论文任务。'
        completion_hint = f'最近处理\n{latest_time}'

    stage = _resolve_stage(model_ready, latest_record, config_mgr)
    active_model = get_active_model_label(config_mgr) if model_ready else '未配置'
    status_fields = (
        ('当前文稿主题', _resolve_current_paper_topic(config_mgr)),
        ('当前阶段', stage['label']),
        ('活跃模型', active_model),
        ('当前字数', str(_resolve_current_word_count(config_mgr, stage['target'], latest_record))),
        ('最近一次处理时间', str(latest_record.get('time', '') or '暂无记录') if latest_record else '暂无记录'),
        ('待处理风险数', str(_count_pending_risks(config_mgr))),
    )

    return {
        'tip_text': tip_text,
        'board_text': board_text,
        'completion_hint': completion_hint,
        'total': total,
        'status_card': {
            'fields': status_fields,
            'continue_target': stage['target'],
        },
        'task_text': TASK_TEXT,
        'system_status_items': build_system_status_items(config_mgr),
    }
