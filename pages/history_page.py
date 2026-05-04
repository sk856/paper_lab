# -*- coding: utf-8 -*-
"""
History page.
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from modules.app_metadata import MODULE_FILTER_OPTIONS, SOURCE_KIND_LABELS as GLOBAL_SOURCE_KIND_LABELS
from modules.aux_tools import AuxTools
from modules.ui_components import (
    COLORS,
    FONTS,
    CardFrame,
    ModernButton,
    ResponsiveButtonBar,
    bind_adaptive_wrap,
    bind_responsive_two_pane,
    create_home_shell_button,
    create_scrolled_text,
    THEMES,
)


class HistoryPage:
    FILTER_OPTIONS = MODULE_FILTER_OPTIONS
    SOURCE_KIND_LABELS = dict(GLOBAL_SOURCE_KIND_LABELS)
    RESTORE_MODE_LABELS = {
        'full_snapshot': '完整快照回滚',
        'legacy_partial': '兼容部分恢复',
    }
    FILTER_BUTTON_BORDER = 'card_border'
    FILTER_ACTIVE_BUTTON_STYLE = 'primary_fixed'
    HISTORY_ACTION_BUTTON_MIN_WIDTH = 136
    EXPORT_OPTIONS = (
        ('导出 docx', 'docx', 'secondary'),
        ('导出 doc', 'doc', 'secondary'),
        ('导出 LaTex', 'latex', 'secondary'),
        ('导出 txt', 'txt', 'secondary'),
        ('导出 PDF', 'pdf', 'secondary'),
    )
    EXPORT_FILETYPES = {
        'doc': [('Word 97-2003 文档', '*.doc')],
        'docx': [('Word 文档', '*.docx')],
        'latex': [('LaTeX 源文件', '*.tex')],
        'txt': [('文本文件', '*.txt')],
        'pdf': [('PDF 文件', '*.pdf')],
    }
    EXPORT_EXTENSIONS = {
        'doc': '.doc',
        'docx': '.docx',
        'latex': '.tex',
        'txt': '.txt',
        'pdf': '.pdf',
    }
    EXPORT_LABELS = {
        'doc': 'DOC',
        'docx': 'DOCX',
        'latex': 'LaTeX',
        'txt': 'TXT',
        'pdf': 'PDF',
    }

    def __init__(self, parent, config_mgr, api_client, history_mgr, set_status, navigate_page=None, app_bridge=None):
        self.config = config_mgr
        self.api = api_client
        self.history = history_mgr
        self.set_status = set_status
        self.navigate_page = navigate_page
        self.app_bridge = app_bridge
        self.aux = AuxTools(api_client)
        self.frame = tk.Frame(parent, bg=COLORS['bg_main'])
        self.filter_key = '全部'
        self.filter_buttons = {}
        self._records_map = {}
        self._build()

    def _build(self):
        self._build_control_card()

        body = tk.Frame(self.frame, bg=COLORS['bg_main'])
        body.pack(fill=tk.BOTH, expand=True)

        list_card = CardFrame(body, title='记录列表')
        self._build_list_card(list_card)

        export_card = CardFrame(body, title='导出与引用格式')
        self._build_export_card(export_card.inner)

        bind_responsive_two_pane(
            body,
            list_card,
            export_card,
            breakpoint=1420,
            gap=8,
            left_minsize=0,
            left_weight=1,
            right_weight=1,
            uniform_group='history_split',
        )
        self._refresh()

    def _build_control_card(self):
        ctrl_card = CardFrame(self.frame, title='历史管理')
        ctrl_card.pack(fill=tk.X, pady=(0, 10))
        inner = ctrl_card.inner

        head = tk.Frame(inner, bg=COLORS['card_bg'])
        head.pack(fill=tk.X)
        tk.Label(
            head,
            text='筛选模块',
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
        ).pack(side=tk.LEFT)
        clear_shell, _clear_button = create_home_shell_button(
            head,
            '清空全部历史',
            command=self._clear_all,
            style='danger',
            padx=12,
            pady=6,
        )
        clear_shell.pack(side=tk.RIGHT)

        filter_bar = ResponsiveButtonBar(inner, min_item_width=126, gap_x=8, gap_y=8, bg=COLORS['card_bg'])
        filter_bar.pack(fill=tk.X, pady=(10, 0))
        for label in self.FILTER_OPTIONS:
            shell, button = self._make_filter_shell_button(
                filter_bar,
                label,
                command=lambda current=label: self._set_filter(current),
            )
            filter_bar.add(shell)
            self.filter_buttons[label] = {'shell': shell, 'button': button}

        self._refresh_filter_styles()

    def _make_filter_shell_button(self, parent, text, command):
        shell = tk.Frame(parent, bg=COLORS[self.FILTER_BUTTON_BORDER], bd=0, highlightthickness=0)
        shell._home_shell_border_key = self.FILTER_BUTTON_BORDER
        shell._home_shell_border_color = None
        button = ModernButton(
            shell,
            text,
            style='secondary',
            command=command,
            padx=12,
            pady=7,
            font=FONTS['body_bold'],
            highlightthickness=0,
        )
        button.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        return shell, button

    def _build_list_card(self, card):
        if card.title_frame is not None:
            action_row = tk.Frame(card.title_frame, bg=COLORS['card_bg'])
            action_row.grid(row=0, column=1, sticky='e')
            self.history_action_hosts = []
            self.history_action_buttons = []

            button_height = self._get_history_action_button_height()
            button_specs = [
                ('刷新历史版本', 'ghost', self._refresh),
                ('回滚到选中版本', 'accent', self._rollback_selected),
                ('删除选中版本', 'danger', self._delete_selected),
            ]
            for index, (text, style, command) in enumerate(button_specs):
                host = tk.Frame(
                    action_row,
                    bg=COLORS['card_bg'],
                    width=self.HISTORY_ACTION_BUTTON_MIN_WIDTH,
                    height=button_height,
                )
                host.grid(row=0, column=index, padx=(0 if index == 0 else 8, 0), sticky='e')
                host.grid_propagate(False)

                shell, button = create_home_shell_button(
                    host,
                    text,
                    command=command,
                    style=style,
                    padx=10,
                    pady=2,
                    font=FONTS['body_bold'],
                )
                shell.pack(fill=tk.BOTH, expand=True)

                self.history_action_hosts.append(host)
                self.history_action_buttons.append(button)

            self.frame.after_idle(self._sync_history_action_button_width)

        table_shell = tk.Frame(
            card.inner,
            bg=COLORS['surface_alt'],
            highlightbackground=COLORS['card_border'],
            highlightthickness=1,
        )
        table_shell.pack(fill=tk.BOTH, expand=True)

        cols = ('时间', '论文题目', '模块', '操作', '字数')
        self.tree = ttk.Treeview(table_shell, columns=cols, show='headings', height=20, selectmode='browse')
        self.tree.heading('时间', text='时间')
        self.tree.heading('论文题目', text='论文题目')
        self.tree.heading('模块', text='模块')
        self.tree.heading('操作', text='操作')
        self.tree.heading('字数', text='字数')
        self.tree.column('时间', width=150, anchor='w')
        self.tree.column('论文题目', width=300, anchor='w')
        self.tree.column('模块', width=110, anchor='center')
        self.tree.column('操作', width=220, anchor='w')
        self.tree.column('字数', width=90, anchor='center')

        vsb = ttk.Scrollbar(table_shell, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_shell, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        table_shell.grid_columnconfigure(0, weight=1)
        table_shell.grid_rowconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        self.count_label = tk.Label(
            card.inner,
            text='',
            font=FONTS['small'],
            fg=COLORS['text_muted'],
            bg=COLORS['card_bg'],
        )
        self.count_label.pack(side=tk.BOTTOM, anchor='w', pady=(10, 0))

    def _get_history_action_button_height(self):
        body_font = tkfont.Font(root=self.frame, font=FONTS['body'])
        return max(30, body_font.metrics('linespace') + 12)

    def _sync_history_action_button_width(self):
        if not getattr(self, 'history_action_hosts', None):
            return
        try:
            required_width = max(button.winfo_reqwidth() for button in self.history_action_buttons)
        except tk.TclError:
            return
        target_width = max(self.HISTORY_ACTION_BUTTON_MIN_WIDTH, required_width + 8)
        for host in self.history_action_hosts:
            host.configure(width=target_width)

    def _build_export_card(self, parent):
        section_bg = COLORS['card_bg']
        self.selected_title_label = tk.Label(
            parent,
            text='当前未选中版本',
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
            anchor='w',
            justify='left',
        )
        self.selected_title_label.pack(fill=tk.X)

        self.selected_meta_label = tk.Label(
            parent,
            text='请选择左侧历史记录后再执行导出。',
            font=FONTS['small'],
            fg=COLORS['text_sub'],
            bg=COLORS['card_bg'],
            anchor='w',
            justify='left',
        )
        self.selected_meta_label.pack(fill=tk.X, pady=(6, 10))
        bind_adaptive_wrap(self.selected_meta_label, parent, padding=12, min_width=220)

        export_block = tk.Frame(parent, bg=section_bg, highlightbackground=COLORS['card_border'], highlightthickness=1)
        export_block.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            export_block,
            text='导出',
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=section_bg,
        ).pack(anchor='w', padx=12, pady=(12, 6))

        export_btns = ResponsiveButtonBar(export_block, min_item_width=150, gap_x=8, gap_y=8, bg=section_bg)
        export_btns.pack(fill=tk.X, padx=12, pady=(0, 12))
        for label, fmt, style in self.EXPORT_OPTIONS:
            export_btns.add(
                create_home_shell_button(
                    export_btns,
                    label,
                    command=lambda current=fmt: self._export_selected(current),
                    style=style,
                    padx=12,
                    pady=8,
                )[0]
            )

        detail_block = tk.Frame(parent, bg=section_bg, highlightbackground=COLORS['card_border'], highlightthickness=1)
        detail_block.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            detail_block,
            text='版本摘要与引用备注',
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=section_bg,
        ).pack(anchor='w', padx=12, pady=(12, 6))

        self.summary_text = self._create_readonly_section(
            detail_block,
            title='版本摘要',
            height=7,
            copy_label='复制摘要',
            command=self._copy_summary,
        )
        self.citation_text = self._create_readonly_section(
            detail_block,
            title='引用备注',
            height=8,
            copy_label='复制备注',
            command=self._copy_citation_notes,
        )
        self.preview_text = self._create_readonly_section(
            detail_block,
            title='内容预览',
            height=12,
            expand=True,
        )

        self.preview_hint_label = tk.Label(
            detail_block,
            text='预览仅展示部分正文，导出后可获取完整内容。',
            font=FONTS['small'],
            fg=COLORS['text_muted'],
            bg=section_bg,
            justify='left',
            anchor='w',
        )
        self.preview_hint_label.pack(fill=tk.X, padx=12, pady=(0, 12))
        bind_adaptive_wrap(self.preview_hint_label, detail_block, padding=24, min_width=220)

    def _create_readonly_section(self, parent, title, height, copy_label='', command=None, expand=False):
        header = tk.Frame(parent, bg=parent.cget('bg'))
        header.pack(fill=tk.X, padx=12, pady=(6, 6))

        tk.Label(
            header,
            text=title,
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=parent.cget('bg'),
        ).pack(side=tk.LEFT)

        if copy_label and command:
            copy_shell, _copy_button = create_home_shell_button(
                header,
                copy_label,
                command=command,
                style='ghost',
                padx=10,
                pady=4,
                font=FONTS['small'],
            )
            copy_shell.pack(side=tk.RIGHT)

        box, text_widget = create_scrolled_text(parent, height=height)
        box.pack(fill=tk.BOTH if expand else tk.X, expand=expand, padx=12, pady=(0, 4))
        text_widget.configure(
            font=FONTS['small'],
            padx=10,
            pady=8,
            state=tk.DISABLED,
            cursor='arrow',
        )
        return text_widget

    def _set_filter(self, key):
        if key == self.filter_key:
            return
        self.filter_key = key
        self._refresh_filter_styles()
        self._refresh()

    def _refresh_filter_styles(self):
        for key, refs in self.filter_buttons.items():
            selected = key == self.filter_key
            refs['button'].set_style(self.FILTER_ACTIVE_BUTTON_STYLE if selected else 'secondary')
            refs['button'].configure(font=FONTS['body_bold'])
            shell = refs['shell']
            if selected:
                shell._home_shell_border_key = None
                shell._home_shell_border_color = THEMES['light']['card_border']
                shell.configure(bg=THEMES['light']['card_border'])
            else:
                shell._home_shell_border_key = self.FILTER_BUTTON_BORDER
                shell._home_shell_border_color = None
                shell.configure(bg=COLORS[self.FILTER_BUTTON_BORDER])

    def _filter_records(self):
        if self.filter_key == '全部':
            return self.history.get_all()
        if self.filter_key == '降查重率':
            return [item for item in self.history.get_all() if item.get('module') in {'降查重', '降查重率'}]
        return self.history.get_by_module(self.filter_key)

    def _refresh(self, select_record_id=None):
        current = self._get_selected_record()
        target_id = select_record_id if select_record_id is not None else (current or {}).get('id')

        records = self._filter_records()
        self.tree.delete(*self.tree.get_children())
        self._records_map = {}

        for rec in records:
            display = self._resolve_display_record(rec)
            iid = self.tree.insert(
                '',
                tk.END,
                values=(
                    rec.get('time', ''),
                    self._get_paper_title(display),
                    rec.get('module', ''),
                    rec.get('operation', '')[:28],
                    self._get_word_count(display),
                ),
            )
            self._records_map[iid] = rec

        self.count_label.configure(text=f'共 {len(records)} 条记录')
        if target_id is not None and self._select_record_by_id(target_id):
            self._on_select()
            return
        self._clear_export_panel()

    def _select_record_by_id(self, record_id):
        for iid, record in self._records_map.items():
            if record.get('id') == record_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return True
        return False

    def _resolve_display_record(self, record):
        return self.history.resolve_display_source(record) or record

    def _resolve_rollback_record(self, record):
        return self.history.resolve_rollback_source(record) or record

    def _build_selected_bundle(self):
        selected = self._get_selected_record()
        if not selected:
            return None
        display = self._resolve_display_record(selected)
        rollback_source = self._resolve_rollback_record(selected)
        return {
            'selected': selected,
            'display': display,
            'rollback_source': rollback_source,
        }

    def _get_paper_title(self, rec):
        if not rec:
            return '未命名论文'
        title = self.history.get_paper_title(rec) if hasattr(self.history, 'get_paper_title') else ''
        return (title or '未命名论文')[:50]

    def _get_word_count(self, rec):
        if not rec:
            return 0
        stored = rec.get('word_count')
        if isinstance(stored, int):
            return stored
        content = rec.get('output_full') or rec.get('output') or rec.get('input_full') or rec.get('input') or ''
        return self.aux.count_words(content).get('total', 0)

    def _get_content_text(self, rec):
        output_text = str(rec.get('output_full') or rec.get('output') or '').strip()
        if output_text:
            return output_text, '输出结果'

        input_text = str(rec.get('input_full') or rec.get('input') or '').strip()
        if input_text:
            return input_text, '输入内容'
        return '', '无正文内容'

    def _get_export_text(self, display_record):
        if not display_record:
            return ''
        ws = display_record.get('workspace_state') or {}
        if isinstance(ws, dict) and ws.get('sections') and ws.get('section_order'):
            text = self._build_full_text_from_workspace(ws)
            if text:
                return text
        return display_record.get('output_full') or display_record.get('output') or display_record.get('input_full') or display_record.get('input') or ''

    @staticmethod
    def _build_full_text_from_workspace(ws):
        section_order = ws.get('section_order', [])
        sections = ws.get('sections', {})
        if not isinstance(sections, dict) or not sections:
            return ''
        parts = []
        for title in (section_order or list(sections.keys())):
            body = str(sections.get(title, '') or '').strip()
            if title.strip():
                parts.append(title.strip())
            if body:
                parts.append(body)
            parts.append('')
        return '\n'.join(parts).strip()

    def _format_source_kind(self, value):
        text = str(value or '').strip()
        if not text:
            return ''
        return self.SOURCE_KIND_LABELS.get(text, text)

    def _format_restore_mode(self, value):
        return self.RESTORE_MODE_LABELS.get(str(value or '').strip(), str(value or '').strip() or '未知')

    def _compose_summary_text(self, bundle):
        selected = bundle['selected']
        display = bundle['display']
        extra = (display or {}).get('extra') or {}
        lines = [
            f'展示版本 ID：{display.get("id", "") if display else ""}',
            f'展示版本时间：{display.get("time", "") if display else ""}',
            f'论文题目：{self._get_paper_title(display)}',
            f'模块：{selected.get("module", "") or "未分类"}',
            f'操作：{selected.get("operation", "") or "未命名操作"}',
            f'字数：{self._get_word_count(display)}',
        ]

        if extra.get('snapshot_type') == 'workspace':
            if extra.get('style'):
                lines.append(f'论文类型：{extra["style"]}')
            if extra.get('subject'):
                lines.append(f'学科方向：{extra["subject"]}')
            if extra.get('reference_style'):
                lines.append(f'引用格式：{extra["reference_style"]}')
            if extra.get('section_count'):
                lines.append(f'章节数量：{extra["section_count"]}')
            if extra.get('snapshot_time'):
                lines.append(f'快照时间：{extra["snapshot_time"]}')
            ws = (display or {}).get('workspace_state') or {}
            lfs = ws.get('level_font_styles', {}) if isinstance(ws, dict) else {}
            if lfs:
                for key, label in (('h1', '一级标题'), ('h2', '二级标题'), ('h3', '三级标题'), ('body', '正文')):
                    s = lfs.get(key, {})
                    if s:
                        lines.append(f'{label}字体：{s.get("font", "")} / {s.get("font_en", "")} {s.get("size_name", "")}')
            if extra.get('outline_summary'):
                lines.append(f'大纲摘要：{extra["outline_summary"][:100]}')

        optional_fields = (
            ('task_type', '任务类型'),
            ('polish_type', '润色方式'),
            ('execution_mode', '执行模式'),
            ('topic', '主题/章节'),
            ('source_kind', '内容来源'),
            ('citation_style_detected', '识别规范'),
            ('citation_style_effective', '执行规范'),
            ('auto_fixed_count', '自动修复数'),
        )
        for key, label in optional_fields:
            value = extra.get(key)
            if value in (None, ''):
                continue
            if key == 'source_kind':
                value = self._format_source_kind(value)
            lines.append(f'{label}：{value}')

        if extra.get('issue_counts'):
            counts = extra.get('issue_counts') or {}
            count_text = '；'.join(f'{name} {value}' for name, value in counts.items() if value)
            if count_text:
                lines.append(f'问题统计：{count_text}')

        if selected.get('record_type') == 'rollback_audit':
            lines.extend(
                [
                    '',
                    '当前条目：回滚审计记录',
                    f'审计记录 ID：{selected.get("id", "")}',
                    f'回滚时间：{selected.get("time", "") or "未记录"}',
                    f'源版本 ID：{display.get("id", "") if display else ""}',
                    f'源版本时间：{display.get("time", "") if display else ""}',
                    f'恢复模式：{self._format_restore_mode(selected.get("rollback_restore_mode"))}',
                    '说明：当前条目不保存正文，预览与导出均引用源版本。',
                ]
            )
        return '\n'.join(lines)

    def _compose_citation_notes(self, bundle):
        selected = bundle['selected']
        display = bundle['display']
        title = self._get_paper_title(display)
        module = selected.get('module', '') or '未分类'
        operation = selected.get('operation', '') or '未命名操作'
        word_count = self._get_word_count(display)
        version_id = display.get('id', '') if display else ''

        display_time = display.get('time', '') if display else ''
        lines = [
            f'版本标注：{title} | {module} | {display_time}',
            '',
            f'文内备注：本文基于「{module}」模块执行「{operation}」后对应的历史版本整理，正文来源版本时间为 {display_time}。',
            '',
            f'附录说明：正文引用版本 ID 为 {version_id}，当前正文约 {word_count} 字，可与导出的 DOC / DOCX / TXT / PDF 文件一并归档。',
        ]

        extra = (display or {}).get('extra') or {}
        if extra.get('snapshot_type') == 'workspace':
            ws = (display or {}).get('workspace_state') or {}
            section_order = ws.get('section_order', []) if isinstance(ws, dict) else []
            section_levels = ws.get('section_levels', {}) if isinstance(ws, dict) else {}
            if section_order:
                lines.extend(['', '章节结构：'])
                level_indent = {1: '', 2: '  ', 3: '    '}
                for sec_title in section_order[:20]:
                    lvl = section_levels.get(sec_title, 2)
                    indent = level_indent.get(lvl, '  ')
                    lines.append(f'{indent}H{lvl} {sec_title}')
                if len(section_order) > 20:
                    lines.append(f'  ...共 {len(section_order)} 个章节')

        if selected.get('record_type') == 'rollback_audit':
            lines.extend(
                [
                    '',
                    f'审计补充：当前条目是回滚审计记录（ID {selected.get("id", "")}），正文与导出内容均引用源版本 {version_id}。',
                    f'恢复模式：{self._format_restore_mode(selected.get("rollback_restore_mode"))}。',
                ]
            )
        lines.extend(
            [
                '',
                '导出建议：DOC / DOCX 适合继续修改，TXT 适合复制留档，PDF 适合发送导师或归档保存。',
            ]
        )
        return '\n'.join(lines)

    def _compose_preview_text(self, bundle):
        display = bundle['display']

        extra = (display or {}).get('extra') or {}
        ws = (display or {}).get('workspace_state') or {}
        if extra.get('snapshot_type') == 'workspace' and isinstance(ws, dict) and ws.get('sections'):
            section_order = ws.get('section_order', [])
            sections = ws.get('sections', {})
            if section_order and sections:
                parts = ['预览来源：工作区快照（按章节结构展示）', '']
                total_chars = 0
                for sec_title in section_order:
                    body = str(sections.get(sec_title, '') or '').strip()
                    parts.append(f'【{sec_title}】')
                    if body:
                        preview_body = body[:200] + ('…' if len(body) > 200 else '')
                        parts.append(preview_body)
                    else:
                        parts.append('（暂无正文）')
                    parts.append('')
                    total_chars += len(body)
                    if total_chars > 2000:
                        remaining = len(section_order) - section_order.index(sec_title) - 1
                        if remaining > 0:
                            parts.append(f'……还有 {remaining} 个章节未展示，导出文件可查看完整内容。')
                        break
                return '\n'.join(parts)

        content, source_name = self._get_content_text(display)
        if not content:
            return '当前版本没有可预览的正文内容。'

        preview_limit = 900
        if len(content) > preview_limit:
            content = f'{content[:preview_limit].rstrip()}\n\n……预览已截断，导出文件可查看完整内容。'

        header = f'预览来源：{source_name}'
        if bundle['selected'].get('record_type') == 'rollback_audit':
            header += f'（引用源版本 {display.get("id", "") if display else ""}）'
        return f'{header}\n\n{content}'

    def _set_readonly_text(self, widget, text):
        widget.configure(state=tk.NORMAL)
        widget.delete('1.0', tk.END)
        widget.insert('1.0', text)
        widget.configure(state=tk.DISABLED)

    def _copy_to_clipboard(self, text, success_status):
        if not text.strip():
            messagebox.showwarning('提示', '当前没有可复制的内容', parent=self.frame)
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.frame.update_idletasks()
        self.set_status(success_status)

    def _copy_summary(self):
        bundle = self._build_selected_bundle()
        if not bundle:
            messagebox.showwarning('提示', '请先选择一条历史记录', parent=self.frame)
            return
        self._copy_to_clipboard(self._compose_summary_text(bundle), '已复制版本摘要')

    def _copy_citation_notes(self):
        bundle = self._build_selected_bundle()
        if not bundle:
            messagebox.showwarning('提示', '请先选择一条历史记录', parent=self.frame)
            return
        self._copy_to_clipboard(self._compose_citation_notes(bundle), '已复制引用备注模板')

    def _get_selected_record(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self._records_map.get(selection[0])

    def _on_select(self, _event=None):
        bundle = self._build_selected_bundle()
        if not bundle:
            self._clear_export_panel()
            return

        selected = bundle['selected']
        display = bundle['display']
        title = self._get_paper_title(display)
        module = selected.get('module', '') or '未分类'
        operation = selected.get('operation', '') or '未命名操作'
        word_count = self._get_word_count(display)

        prefix = '当前选中回滚审计' if selected.get('record_type') == 'rollback_audit' else '当前选中版本'
        self.selected_title_label.configure(text=f'{prefix}：{title}')

        lines = [
            f'当前条目时间：{selected.get("time", "")}',
            f'模块：{module}',
            f'操作：{operation}',
            f'展示字数：{word_count}',
        ]
        if selected.get('record_type') == 'rollback_audit':
            lines.extend(
                [
                    f'源版本 ID：{display.get("id", "") if display else ""}',
                    f'源版本时间：{display.get("time", "") if display else ""}',
                    f'恢复模式：{self._format_restore_mode(selected.get("rollback_restore_mode"))}',
                ]
            )

        self.selected_meta_label.configure(text='\n'.join(lines))
        self._set_readonly_text(self.summary_text, self._compose_summary_text(bundle))
        self._set_readonly_text(self.citation_text, self._compose_citation_notes(bundle))
        self._set_readonly_text(self.preview_text, self._compose_preview_text(bundle))

    def _clear_export_panel(self):
        self.selected_title_label.configure(text='当前未选中版本')
        self.selected_meta_label.configure(text='请选择左侧历史记录后再执行导出。')
        self._set_readonly_text(self.summary_text, '选择左侧历史版本后，这里会显示版本编号、任务参数、回滚来源与正文统计。')
        self._set_readonly_text(self.citation_text, '选择历史版本后，这里会生成可直接复制的版本备注模板，方便留档、附录说明或提交前整理。')
        self._set_readonly_text(self.preview_text, '选择历史版本后，这里会展示正文预览；如需完整内容，请使用上方导出功能。')

    def _rollback_selected(self):
        bundle = self._build_selected_bundle()
        if not bundle:
            messagebox.showwarning('提示', '请先选择要回滚的历史版本', parent=self.frame)
            return
        if not self.app_bridge:
            messagebox.showwarning('提示', '当前版本未提供工作区恢复桥接能力。', parent=self.frame)
            return

        rollback_plan = self.history.prepare_rollback(bundle['selected'])
        if not rollback_plan:
            messagebox.showwarning('提示', '无法解析该历史版本的可恢复工作区。', parent=self.frame)
            return

        source = rollback_plan['source_record']
        title = self._get_paper_title(source)
        if not messagebox.askyesno(
            '回滚确认',
            f'确认回滚到以下版本对应的工作区状态？\n\n论文题目：{title}\n操作：{source.get("operation", "")}',
            parent=self.frame,
        ):
            return

        restore_result = self.app_bridge.restore_page_workspace(
            rollback_plan['page_state_id'],
            rollback_plan['workspace_state'],
            save_to_disk=True,
        )
        if not restore_result or not restore_result.get('ok'):
            messagebox.showwarning(
                '提示',
                (restore_result or {}).get('message', '工作区恢复失败，请稍后重试。'),
                parent=self.frame,
            )
            return

        audit_record = self.history.create_rollback_audit(
            source.get('id'),
            rollback_plan['rollback_restore_mode'],
        )
        self._refresh(select_record_id=(audit_record or {}).get('id'))

        restore_mode = rollback_plan['rollback_restore_mode']
        page_id = rollback_plan['page_state_id']
        if restore_mode == 'full_snapshot':
            self.set_status('已完成完整快照回滚')
            prompt = '已恢复对应模块的完整页面快照。是否立即跳转到对应模块页面？'
            prompt_title = '回滚完成'
        else:
            self.set_status('已按兼容模式完成部分恢复', COLORS['warning'])
            prompt = '旧历史记录未保存完整快照，已按兼容模式尽量恢复。是否立即跳转到对应模块页面？'
            prompt_title = '兼容回滚完成'

        if messagebox.askyesno(prompt_title, prompt, parent=self.frame):
            if callable(self.navigate_page):
                self.navigate_page(page_id)

    def _delete_selected(self):
        record = self._get_selected_record()
        if not record:
            messagebox.showwarning('提示', '请先选择要删除的历史版本', parent=self.frame)
            return

        check = self.history.can_delete(record.get('id'))
        if not check.get('ok'):
            blocker_ids = '、'.join(str(item.get('id', '')) for item in check.get('blocking_records', []))
            messagebox.showwarning(
                '无法删除',
                f'该源版本仍被回滚审计记录引用（审计记录 ID：{blocker_ids}）。\n请先删除相关审计记录。',
                parent=self.frame,
            )
            return

        if messagebox.askyesno('确认', '删除选中版本？此操作不可恢复。', parent=self.frame):
            result = self.history.delete(record['id'])
            if not result.get('ok'):
                messagebox.showwarning('提示', result.get('message', '删除失败'), parent=self.frame)
                return
            self._refresh()
            self.set_status('已删除选中版本')

    def _export_selected(self, fmt):
        bundle = self._build_selected_bundle()
        if not bundle:
            messagebox.showwarning('提示', '请先选择要导出的历史版本', parent=self.frame)
            return

        display = bundle['display']
        text = self._get_export_text(display)
        if not text.strip():
            messagebox.showwarning('提示', '当前选中版本没有可导出的内容', parent=self.frame)
            return

        title = self._get_paper_title(display)
        time_str = (display.get('time', '') if display else '').replace(':', '-').replace(' ', '_')
        safe_title = ''.join(c for c in (title or '历史版本') if c not in r'\/:*?"<>|')[:40]
        initial_name = f'{safe_title} - {time_str}' if time_str else safe_title
        ext = self.EXPORT_EXTENSIONS.get(fmt, f'.{fmt}')
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=self.EXPORT_FILETYPES.get(fmt, [('所有文件', '*.*')]),
            initialfile=initial_name,
            parent=self.frame,
        )
        if not path:
            return

        font_styles = self._get_record_font_styles(display)
        sections_data = self._get_record_sections_data(display)

        try:
            exporters = {
                'doc': lambda: self.aux.export_doc(text, path, title=title,
                                                     level_font_styles=font_styles, sections_data=sections_data),
                'docx': lambda: self.aux.export_docx(text, path, title=title,
                                                       level_font_styles=font_styles, sections_data=sections_data),
                'latex': lambda: self.aux.export_latex(text, path, title=title),
                'txt': lambda: self.aux.export_txt(text, path),
                'pdf': lambda: self.aux.export_pdf(text, path, title=title),
            }
            exporter = exporters.get(fmt)
            if exporter is None:
                raise RuntimeError(f'不支持的导出格式: {fmt}')
            exporter()
            self.set_status(f'已导出 {self.EXPORT_LABELS.get(fmt, fmt.upper())} 文件')
            messagebox.showinfo('导出成功', f'已导出到：\n{path}', parent=self.frame)
        except Exception as exc:
            messagebox.showerror('导出失败', str(exc), parent=self.frame)

    def _get_record_font_styles(self, record):
        if record:
            ws = record.get('workspace_state') or {}
            if isinstance(ws, dict) and ws.get('level_font_styles'):
                return ws['level_font_styles']
        return self._get_paper_write_font_styles()

    def _get_paper_write_font_styles(self):
        bridge = getattr(self, 'app_bridge', None)
        if bridge:
            context = bridge.pull_paper_write_context() or {}
            if isinstance(context, dict):
                styles = context.get('level_font_styles')
                if isinstance(styles, dict) and styles:
                    return {
                        key: dict(value) if isinstance(value, dict) else {}
                        for key, value in styles.items()
                    }
        try:
            from pages.paper_write_page import PaperWritePage
            return {
                key: dict(value)
                for key, value in PaperWritePage.LEVEL_STYLE_DEFAULTS.items()
            }
        except Exception:
            return {
                'h1': {
                    'font': '\u9ed1\u4f53',
                    'font_en': 'Times New Roman',
                    'size_name': '\u4e09\u53f7',
                    'size_pt': 16,
                },
                'h2': {
                    'font': '\u9ed1\u4f53',
                    'font_en': 'Times New Roman',
                    'size_name': '\u56db\u53f7',
                    'size_pt': 14,
                },
                'h3': {
                    'font': '\u9ed1\u4f53',
                    'font_en': 'Times New Roman',
                    'size_name': '\u5c0f\u56db',
                    'size_pt': 12,
                },
                'body': {
                    'font': '\u5b8b\u4f53',
                    'font_en': 'Times New Roman',
                    'size_name': '\u5c0f\u56db',
                    'size_pt': 12,
                },
            }

    def _get_record_sections_data(self, record):
        if not record:
            return None
        ws = record.get('workspace_state') or {}
        if isinstance(ws, dict) and ws.get('sections') and ws.get('section_order'):
            return {
                'section_order': ws['section_order'],
                'sections': ws['sections'],
                'section_levels': ws.get('section_levels', {}),
            }
        return None

    def _clear_all(self):
        if messagebox.askyesno('确认', '确定要清空所有历史记录吗？此操作不可恢复。', parent=self.frame):
            self.history.clear()
            self._refresh()
            self.set_status('历史记录已清空')
