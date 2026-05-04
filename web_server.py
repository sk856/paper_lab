# -*- coding: utf-8 -*-
"""Local web UI for 纸研社."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from modules.ai_reducer import AIReducer
from modules.api_client import APIClient
from modules.config import ConfigManager, resolve_model_display_name
from modules.intelligent_corrector import CATEGORY_LABELS, CATEGORY_ORDER, IntelligentCorrector
from modules.plagiarism import PlagiarismReducer
from modules.polisher import AcademicPolisher
from modules.paper_writer import PaperWriter
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
    determine_reference_mode,
    process_references_append_mode,
    process_references_reorder_mode,
    is_reference_section
)
from pages.api_config_support import merge_with_preset_defaults

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PROJECT_DIR, 'web')


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
    return _section_match_key(title) in {'参考文献', 'references', 'bibliography'}


def _is_reference_linkable_section_title(title):
    key = _section_match_key(title)
    return bool(key) and key not in {
        '摘要', '中文摘要', '内容摘要', '摘要与关键词',
        'abstract', 'abstractandkeywords',
        '关键词', '关键字', '中文关键词', '中文关键字',
        'keywords', 'keywords', 'key words'.replace(' ', ''),
        '参考文献', 'references', 'bibliography',
    }


class WebWorkbench:
    def __init__(self):
        runtime_paths = get_runtime_paths()
        self.config = ConfigManager(runtime_paths.base_data_root)
        self.api_client = APIClient(self.config)
        self.ai_reducer = AIReducer(self.api_client)
        self.plagiarism = PlagiarismReducer(self.api_client)
        self.polisher = AcademicPolisher(self.api_client)
        self.paper_writer = PaperWriter(self.api_client)
        self.corrector = IntelligentCorrector(self.api_client)

    def status(self):
        active_id = self.config.active_api
        active_cfg = self.config.get_api_config(active_id) if active_id else {}
        providers = []
        for api_id, cfg in self.config.list_saved_apis():
            providers.append({
                'id': api_id,
                'name': cfg.get('name') or api_id,
                'model': resolve_model_display_name(cfg),
                'active': api_id == active_id,
                'configured': bool(str(cfg.get('key', '') or '').strip()),
            })
        return {
            'activeApi': active_id,
            'activeName': active_cfg.get('name') or active_id or '',
            'activeModel': resolve_model_display_name(active_cfg) if active_cfg else '',
            'configured': bool(active_id and active_cfg and str(active_cfg.get('key', '') or '').strip()),
            'providers': providers,
        }

    def _public_api_record(self, api_id, cfg):
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
            'active': api_id == self.config.active_api,
            'configured': bool(str(cfg.get('key', '') or '').strip()),
            'hasKey': bool(str(cfg.get('key', '') or '').strip()),
            'apiFormat': cfg.get('api_format', ''),
        }

    def config_payload(self):
        presets = []
        for preset_id, label, defaults in PRESET_OPTIONS:
            presets.append({
                'id': preset_id,
                'label': label,
                'defaults': defaults,
                'staticModels': get_static_models(preset_id),
            })
        records = [
            self._public_api_record(api_id, cfg)
            for api_id, cfg in self.config.list_saved_apis()
        ]
        return {
            'activeApi': self.config.active_api,
            'providers': records,
            'presets': presets,
        }

    def save_api(self, payload):
        api_id = str(payload.get('id', '') or '').strip()
        provider_type = normalize_provider_type(payload.get('providerType') or 'custom')
        existing = self.config.get_api_config(api_id) if api_id else {}
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

        duplicate_id = self.config.find_api_id_by_name(cfg.get('name', ''), exclude_api_id=api_id or None)
        if duplicate_id:
            raise ValueError('接口名称已存在，请换一个名称')

        target_id = api_id or self.config.generate_api_id()
        self.config.set_api_config(target_id, cfg)
        if bool(payload.get('activate', True)):
            self.config.active_api = target_id
        self.config.save()
        return {
            'record': self._public_api_record(target_id, self.config.get_api_config(target_id)),
            'config': self.config_payload(),
        }

    def activate_api(self, payload):
        api_id = str(payload.get('id', '') or '').strip()
        if not self.config.get_api_config(api_id):
            raise ValueError('接口不存在')
        self.config.active_api = api_id
        self.config.save()
        return self.config_payload()

    def fetch_models_for_payload(self, payload):
        api_id = str(payload.get('id', '') or '').strip()
        provider_type = normalize_provider_type(payload.get('providerType') or 'custom')
        existing = self.config.get_api_config(api_id) if api_id else {}
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
        models = self.api_client.fetch_models(api_id or provider_type, cfg=cfg)
        return {'models': models}

    def analyze(self, text):
        ai = self.ai_reducer.scan_ai_features(text)
        repeat = self.plagiarism.simulate_repeat_risk(text)
        citation = self.plagiarism.check_citation_format(text)
        return {
            'ai': ai,
            'repeat': repeat,
            'citation': citation,
        }

    def run_action(self, payload):
        action = str(payload.get('action', '') or '').strip()
        text = str(payload.get('text', '') or '').strip()
        source_text = str(payload.get('sourceText', '') or '').strip()
        if not text:
            raise ValueError('请输入需要处理的文本')

        if action == 'analyze':
            return {'result': '', 'analysis': self.analyze(text)}
        if action == 'polish':
            mode = str(payload.get('polishMode', 'full') or 'full')
            result = self.polisher.run_task(text, polish_type=mode)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'ai-light':
            result = self.ai_reducer.rewrite_light(text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'ai-deep':
            result = self.ai_reducer.rewrite_deep(text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'ai-academic':
            result = self.ai_reducer.rewrite_academic(text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'repeat-light':
            result = self.plagiarism.reduce_light(text, source_text=source_text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'repeat-medium':
            result = self.plagiarism.reduce_medium(text, source_text=source_text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'repeat-deep':
            result = self.plagiarism.reduce_deep(text, source_text=source_text)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'citation':
            return {'result': '', 'analysis': {'citation': self.plagiarism.check_citation_format(text)}}
        if action == 'correction':
            citation_style = str(payload.get('citationStyle', 'auto') or 'auto')
            run = self.corrector.analyze_text(text, citation_style=citation_style)
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
            if not topic:
                raise ValueError('请输入论文题目')
            result = self.paper_writer.generate_outline(topic, style=paper_style, reference_style=reference_style, subject=subject)
            return {'result': result, 'analysis': {}}
        if action == 'section':
            outline = str(payload.get('outline', '') or '').strip()
            section_title = str(payload.get('sectionTitle', '') or '').strip()
            context = str(payload.get('context', '') or '').strip()
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            all_sections = payload.get('allSections', [])
            try:
                word_count = int(payload.get('wordCount') or 1000)
            except Exception:
                word_count = 1000
            if not outline:
                raise ValueError('请输入论文大纲')
            if not section_title:
                raise ValueError('请输入章节标题')

            # Generate section content
            raw_result = self.paper_writer.write_section(outline, section_title, context=context, word_count=word_count, reference_style=reference_style)

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
            result = self.paper_writer.write_abstract(text, language=language)
            return {'result': result, 'analysis': self.analyze(result)}
        if action == 'references':
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            result = self.paper_writer.format_references(text, style=reference_style)
            return {'result': result, 'analysis': {'citation': self.plagiarism.check_citation_format(result)}}
        if action == 'batch_write':
            outline = str(payload.get('outline', '') or '').strip()
            reference_style = str(payload.get('referenceStyle', 'GB/T 7714') or 'GB/T 7714')
            sections = payload.get('sections', [])
            all_sections = payload.get('allSections', [])
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
                    result = self.paper_writer.write_section(
                        outline,
                        section_title,
                        context=context,
                        word_count=word_count,
                        reference_style=reference_style
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

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/status':
            self._send_json({'ok': True, 'data': self.workbench.status()})
            return
        if parsed.path == '/api/config':
            self._send_json({'ok': True, 'data': self.workbench.config_payload()})
            return
        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == '/api/run':
                data = self.workbench.run_action(payload)
            elif parsed.path == '/api/config/save':
                data = self.workbench.save_api(payload)
            elif parsed.path == '/api/config/activate':
                data = self.workbench.activate_api(payload)
            elif parsed.path == '/api/config/models':
                data = self.workbench.fetch_models_for_payload(payload)
            else:
                self._send_json({'ok': False, 'error': 'Not found'}, status=404)
                return
            self._send_json({'ok': True, 'data': data})
        except Exception as exc:
            self._send_json({'ok': False, 'error': str(exc)}, status=400)

    def _serve_static(self, path):
        relative = path.lstrip('/') or 'index.html'
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
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description='纸研社 Web 工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()

    RequestHandler.workbench = WebWorkbench()
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    url = f'http://{args.host}:{args.port}/'
    print(f'[web] 纸研社 Web 工作台已启动: {url}')
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
