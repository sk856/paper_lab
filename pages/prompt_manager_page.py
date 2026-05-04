# -*- coding: utf-8 -*-
"""
Prompt manager panel shared by full and compact dialogs.
"""

import tkinter as tk
from tkinter import messagebox

from modules.prompt_center import (
    PAGE_META,
    PAGE_ORDER,
    PAGE_SCENE_MAP,
    PROMPT_MODE_TEMPLATE,
    PromptCenter,
    PromptValidationError,
)
from modules.ui_components import (
    apply_adaptive_window_geometry,
    COLORS,
    FONTS,
    CardFrame,
    RoundedFrame,
    ScrollablePage,
    ResponsiveButtonBar,
    bind_adaptive_wrap,
    bind_responsive_two_pane,
    create_home_shell_button,
    create_scrolled_text,
)


class PromptManagerPanel:
    def __init__(
        self,
        parent,
        config_mgr,
        set_status,
        *,
        compact=False,
        page_id=None,
        scene_id=None,
        open_full=None,
        close_panel=None,
    ):
        self.config = config_mgr
        self.set_status = set_status
        self.compact = compact
        self.open_full = open_full
        self.close_panel = close_panel
        self.prompt_center = PromptCenter(config_mgr)
        self.frame = tk.Frame(parent, bg=COLORS['bg_main'])

        self.current_page_id = page_id or PAGE_ORDER[0]
        self.current_scene_id = scene_id or ''
        self._page_buttons = {}
        self._scene_buttons = {}
        self._editing_prompt_id = None
        self._editing_source = 'user'

        self.name_var = tk.StringVar(value='')
        self.desc_var = tk.StringVar(value='')

        self.summary_label = None
        self.scene_card = None
        self.scene_desc_label = None
        self.scene_note_label = None
        self.list_card = None
        self.prompt_list_view = None
        self.prompt_list_inner = None
        self.variable_tip_label = None
        self.content_text = None
        self.editor_title_label = None
        self.scene_tabs_bar = None
        self._editor_window = None
        self._editor_dialog_title_label = None
        self._editor_dialog_variable_tip_label = None
        self._editor_dialog_content_text = None
        self._editor_dialog_name_entry = None
        self._editor_dialog_scroll_view = None

        self._build()
        self.focus_scene(page_id=page_id, scene_id=scene_id)

    def _build(self):
        if self.compact:
            self._build_compact()
            return
        self._build_full()

    def _create_prompt_button(self, parent, text, *, command, style='secondary', **kwargs):
        return create_home_shell_button(
            parent,
            text,
            command=command,
            style=style,
            **kwargs,
        )

    def _build_full(self):
        header = tk.Frame(self.frame, bg=COLORS['bg_main'])
        header.pack(fill=tk.X, pady=(0, 12))

        title_row = tk.Frame(header, bg=COLORS['bg_main'])
        title_row.pack(fill=tk.X)

        tk.Label(
            title_row,
            text='提示词管理中心',
            font=FONTS['title'],
            fg=COLORS['text_main'],
            bg=COLORS['bg_main'],
        ).pack(side=tk.LEFT)

        self.summary_label = tk.Label(
            title_row,
            text='',
            font=FONTS['small'],
            fg=COLORS['text_sub'],
            bg=COLORS['bg_main'],
            justify='left',
            anchor='w',
        )
        self.summary_label.pack(side=tk.LEFT, padx=(12, 0), pady=(8, 0))

        body = tk.Frame(self.frame, bg=COLORS['bg_main'])
        body.pack(fill=tk.BOTH, expand=True)

        nav_card = CardFrame(body, title='页面分组')
        nav_card.pack_propagate(False)
        self._build_page_nav(nav_card.inner)

        content_host = tk.Frame(body, bg=COLORS['bg_main'])
        self._build_content_host(content_host)

        bind_responsive_two_pane(body, nav_card, content_host, breakpoint=1220, gap=10, left_minsize=280)

    def _build_compact(self):
        header = tk.Frame(self.frame, bg=COLORS['bg_main'])
        header.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            header,
            text='提示词',
            font=FONTS['title'],
            fg=COLORS['text_main'],
            bg=COLORS['bg_main'],
        ).pack(side=tk.LEFT)

        if callable(self.open_full):
            head_actions = ResponsiveButtonBar(header, min_item_width=120, gap_x=8, gap_y=8, bg=COLORS['bg_main'])
            head_actions.pack(side=tk.RIGHT)
            shell, _button = self._create_prompt_button(head_actions, '打开总管理', style='secondary', command=self._open_full)
            head_actions.add(shell)

        self.summary_label = tk.Label(
            self.frame,
            text='',
            font=FONTS['body'],
            fg=COLORS['text_sub'],
            bg=COLORS['bg_main'],
            justify='left',
            anchor='w',
        )
        self.summary_label.pack(fill=tk.X, pady=(0, 10))

        self._build_content_host(self.frame)

    def _build_page_nav(self, parent):
        for page_id in PAGE_ORDER:
            shell, button = self._create_prompt_button(
                parent,
                PAGE_META.get(page_id, {}).get('label', page_id),
                style='pill',
                command=lambda pid=page_id: self.focus_scene(page_id=pid),
                padx=14,
                pady=8,
            )
            shell.pack(fill=tk.X, pady=(10, 0))
            self._page_buttons[page_id] = button

    def _build_content_host(self, parent):
        self.scene_card = CardFrame(parent, title='当前分组')
        self.scene_card.pack(fill=tk.X, pady=(0, 10))

        self.scene_tabs_bar = ResponsiveButtonBar(self.scene_card.inner, min_item_width=160, gap_x=8, gap_y=8, bg=COLORS['card_bg'])
        self.scene_tabs_bar.pack(fill=tk.X)

        self.scene_desc_label = tk.Label(
            self.scene_card.inner,
            text='',
            font=FONTS['body'],
            fg=COLORS['text_sub'],
            bg=COLORS['card_bg'],
            justify='left',
            anchor='w',
        )
        self.scene_desc_label.pack(fill=tk.X, pady=(10, 0))
        bind_adaptive_wrap(self.scene_desc_label, self.scene_card.inner, padding=12, min_width=220)

        self.scene_note_label = tk.Label(
            self.scene_card.inner,
            text='',
            font=FONTS['small'],
            fg=COLORS['warning'],
            bg=COLORS['card_bg'],
            justify='left',
            anchor='w',
        )
        self.scene_note_label.pack(fill=tk.X, pady=(6, 0))
        bind_adaptive_wrap(self.scene_note_label, self.scene_card.inner, padding=12, min_width=220)

        self.list_card = CardFrame(parent)
        self.list_card.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        list_header = tk.Frame(self.list_card.inner, bg=COLORS['card_bg'])
        list_header.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            list_header,
            text='提示词列表',
            font=FONTS['heading'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
        ).pack(side=tk.LEFT)

        list_actions = tk.Frame(list_header, bg=COLORS['card_bg'])
        list_actions.pack(side=tk.RIGHT, anchor='e')
        create_shell, _button = self._create_prompt_button(
            list_actions,
            '添加提示词',
            style='primary',
            command=self._start_create,
            font=FONTS['heading'],
            padx=10,
            pady=0,
        )
        create_shell.pack(side=tk.LEFT)
        refresh_shell, _button = self._create_prompt_button(
            list_actions,
            '刷新列表',
            style='ghost',
            command=self._refresh_scene,
            font=FONTS['heading'],
            padx=10,
            pady=0,
        )
        refresh_shell.pack(side=tk.LEFT, padx=(8, 0))

        self.prompt_list_view = ScrollablePage(self.list_card.inner, bg=COLORS['card_bg'])
        self.prompt_list_view.pack(fill=tk.BOTH, expand=True)
        self.prompt_list_inner = self.prompt_list_view.inner

    def _build_editor(self, parent):
        self.editor_title_label = tk.Label(
            parent,
            text='新建提示词',
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
        )
        self.editor_title_label.pack(anchor='w')

        name_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        name_shell.pack(fill=tk.X, pady=(10, 0))
        tk.Label(name_shell, text='名称', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')
        tk.Entry(
            name_shell,
            textvariable=self.name_var,
            font=FONTS['body'],
            bg=COLORS['input_bg'],
            fg=COLORS['text_main'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS['input_border'],
        ).pack(fill=tk.X, pady=(6, 0), ipady=4)

        desc_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        desc_shell.pack(fill=tk.X, pady=(10, 0))
        tk.Label(desc_shell, text='描述', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')
        tk.Entry(
            desc_shell,
            textvariable=self.desc_var,
            font=FONTS['body'],
            bg=COLORS['input_bg'],
            fg=COLORS['text_main'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS['input_border'],
        ).pack(fill=tk.X, pady=(6, 0), ipady=4)

        mode_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        mode_shell.pack(fill=tk.X, pady=(10, 0))
        tk.Label(mode_shell, text='可用变量', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')

        self.variable_tip_label = tk.Label(
            mode_shell,
            text='',
            font=FONTS['small'],
            fg=COLORS['text_sub'],
            bg=COLORS['card_bg'],
            justify='left',
            anchor='w',
        )
        self.variable_tip_label.pack(fill=tk.X, pady=(6, 0))
        bind_adaptive_wrap(self.variable_tip_label, parent, padding=12, min_width=220)

        tk.Label(parent, text='内容', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w', pady=(12, 0))
        content_frame, self.content_text = create_scrolled_text(parent, height=14)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        editor_actions = ResponsiveButtonBar(parent, min_item_width=140, gap_x=8, gap_y=8, bg=COLORS['card_bg'])
        editor_actions.pack(fill=tk.X, pady=(12, 0))
        save_shell, _button = self._create_prompt_button(editor_actions, '保存提示词', style='primary', command=self._save_current)
        cancel_shell, _button = self._create_prompt_button(editor_actions, '取消编辑', style='ghost', command=self._reset_editor)
        editor_actions.add(save_shell)
        editor_actions.add(cancel_shell)

    def _build_dialog_editor(self, parent):
        title_label = tk.Label(
            parent,
            text='新建提示词',
            font=FONTS['title'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
        )
        title_label.pack(anchor='w')

        name_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        name_shell.pack(fill=tk.X, pady=(18, 0))
        tk.Label(name_shell, text='名称', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')
        name_entry = tk.Entry(
            name_shell,
            textvariable=self.name_var,
            font=FONTS['body'],
            bg=COLORS['input_bg'],
            fg=COLORS['text_main'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS['input_border'],
        )
        name_entry.pack(fill=tk.X, pady=(6, 0), ipady=4)

        desc_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        desc_shell.pack(fill=tk.X, pady=(12, 0))
        tk.Label(desc_shell, text='描述', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')
        tk.Entry(
            desc_shell,
            textvariable=self.desc_var,
            font=FONTS['body'],
            bg=COLORS['input_bg'],
            fg=COLORS['text_main'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS['input_border'],
        ).pack(fill=tk.X, pady=(6, 0), ipady=4)

        mode_shell = tk.Frame(parent, bg=COLORS['card_bg'])
        mode_shell.pack(fill=tk.X, pady=(12, 0))
        tk.Label(mode_shell, text='可用变量', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w')

        variable_tip_label = tk.Label(
            mode_shell,
            text='',
            font=FONTS['small'],
            fg=COLORS['text_sub'],
            bg=COLORS['card_bg'],
            justify='left',
            anchor='w',
        )
        variable_tip_label.pack(fill=tk.X, pady=(6, 0))
        bind_adaptive_wrap(variable_tip_label, parent, padding=12, min_width=260)

        tk.Label(parent, text='内容', font=FONTS['body_bold'], fg=COLORS['text_main'], bg=COLORS['card_bg']).pack(anchor='w', pady=(14, 0))
        content_frame, content_text = create_scrolled_text(parent, height=18)
        content_frame.pack(fill=tk.BOTH, expand=False, pady=(8, 0))

        self._editor_dialog_title_label = title_label
        self._editor_dialog_variable_tip_label = variable_tip_label
        self._editor_dialog_content_text = content_text
        self._editor_dialog_name_entry = name_entry

    def _ensure_editor_dialog(self):
        if self._editor_window and self._editor_window.winfo_exists():
            self._editor_window.lift()
            self._editor_window.focus_force()
            return self._editor_window

        window = tk.Toplevel(self.frame.winfo_toplevel())
        window.title('提示词编辑')
        window.configure(bg=COLORS['bg_main'])
        window.transient(self.frame.winfo_toplevel())
        window.resizable(True, True)
        apply_adaptive_window_geometry(window, '1600x1200', min_width=1320, min_height=960)

        shell = tk.Frame(window, bg=COLORS['bg_main'])
        shell.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        body = tk.Frame(
            shell,
            bg=COLORS['card_bg'],
            highlightbackground=COLORS['card_border'],
            highlightthickness=1,
            bd=0,
        )
        body.pack(fill=tk.BOTH, expand=True)

        content_view = ScrollablePage(body, bg=COLORS['card_bg'])
        content_view.pack(fill=tk.BOTH, expand=True)

        content = tk.Frame(content_view.inner, bg=COLORS['card_bg'])
        content.pack(fill=tk.BOTH, expand=True, padx=28, pady=28)
        self._build_dialog_editor(content)

        footer = tk.Frame(body, bg=COLORS['card_bg'])
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=28, pady=(0, 22))
        save_shell, _button = self._create_prompt_button(
            footer,
            '保存提示词',
            style='primary',
            command=self._save_current,
            padx=14,
            pady=6,
            font=FONTS['body_bold'],
        )
        save_shell.pack(side=tk.RIGHT)

        footer_divider = tk.Frame(body, bg=COLORS['card_border'], height=1, bd=0, highlightthickness=0)
        footer_divider.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 12))

        self._editor_window = window
        self._editor_dialog_scroll_view = content_view
        window.protocol('WM_DELETE_WINDOW', self._close_editor_dialog)
        self._refresh_variable_tip()
        content_view.scroll_to_top()
        return window

    def _close_editor_dialog(self):
        if self._editor_window and self._editor_window.winfo_exists():
            self._editor_window.destroy()
        self._editor_window = None
        self._editor_dialog_title_label = None
        self._editor_dialog_variable_tip_label = None
        self._editor_dialog_content_text = None
        self._editor_dialog_name_entry = None
        self._editor_dialog_scroll_view = None

    def _set_editor_title(self, text):
        if self.editor_title_label:
            self.editor_title_label.configure(text=text)
        if self._editor_dialog_title_label:
            self._editor_dialog_title_label.configure(text=text)

    def _set_editor_content(self, content):
        content = content or ''
        if self.content_text:
            self.content_text.delete('1.0', tk.END)
            self.content_text.insert('1.0', content)
        if self._editor_dialog_content_text:
            self._editor_dialog_content_text.delete('1.0', tk.END)
            self._editor_dialog_content_text.insert('1.0', content)

    def _get_editor_content(self):
        if self._editor_dialog_content_text and self._editor_window and self._editor_window.winfo_exists():
            return self._editor_dialog_content_text.get('1.0', tk.END).strip()
        if self.content_text:
            return self.content_text.get('1.0', tk.END).strip()
        return ''

    def _focus_editor(self, *, focus_name=False):
        if self._editor_window and self._editor_window.winfo_exists():
            self._editor_window.lift()
            self._editor_window.focus_force()
            if self._editor_dialog_scroll_view:
                self._editor_dialog_scroll_view.scroll_to_top()
            target = self._editor_dialog_name_entry if focus_name else self._editor_dialog_name_entry
            if target:
                target.focus_set()
            return

        target = None
        if focus_name and self.editor_title_label:
            target = self.editor_title_label
        elif self.content_text:
            target = self.content_text
        if target:
            target.focus_set()

    def _reset_editor_state(self):
        self._editing_prompt_id = None
        self._editing_source = 'user'
        self._set_editor_title('新建提示词')
        self.name_var.set('')
        self.desc_var.set('')
        self._set_editor_content('')
        self._refresh_variable_tip()

    def _open_full(self):
        if callable(self.open_full):
            self.open_full(page_id=self.current_page_id, scene_id=self.current_scene_id)

    def focus_scene(self, page_id=None, scene_id=None):
        previous_scene_id = self.current_scene_id
        if scene_id:
            scene_def = self.prompt_center.get_scene_def(scene_id)
            self.current_page_id = scene_def['page_id']
            self.current_scene_id = scene_id
        else:
            self.current_page_id = page_id or self.current_page_id
            scenes = PAGE_SCENE_MAP.get(self.current_page_id, [])
            if scenes:
                self.current_scene_id = self.current_scene_id if self.current_scene_id in scenes else scenes[0]

        self._refresh_page_buttons()
        self._refresh_scene()
        if previous_scene_id != self.current_scene_id and self._editor_window and self._editor_window.winfo_exists():
            self._reset_editor_state()

    def _refresh_page_buttons(self):
        for page_id, button in self._page_buttons.items():
            button.set_style('pill_active' if page_id == self.current_page_id else 'pill')

    def _refresh_scene(self):
        self._refresh_summary()
        self._refresh_scene_visibility()
        self._refresh_scene_tabs()
        self._refresh_scene_header()
        self._refresh_prompt_list()
        self._refresh_variable_tip()

    def _refresh_summary(self):
        summary = self.prompt_center.count_summary(page_id=self.current_page_id if self.compact else None)
        self.summary_label.configure(
            text=f'共 {summary["total"]} 条提示词，已覆盖 {summary["active_groups"]} 个场景'
        )

    def _refresh_scene_visibility(self):
        if not self.scene_card:
            return
        show_scene_card = bool(PAGE_SCENE_MAP.get(self.current_page_id))
        is_visible = self.scene_card.winfo_manager() == 'pack'
        if show_scene_card and not is_visible:
            pack_kwargs = {'fill': tk.X, 'pady': (0, 10)}
            if self.list_card and self.list_card.winfo_manager() == 'pack':
                pack_kwargs['before'] = self.list_card
            self.scene_card.pack(**pack_kwargs)
        elif not show_scene_card and is_visible:
            self.scene_card.pack_forget()

    def _refresh_scene_tabs(self):
        if self.scene_tabs_bar:
            self.scene_tabs_bar.destroy()

        self.scene_tabs_bar = ResponsiveButtonBar(self.scene_card.inner, min_item_width=160, gap_x=8, gap_y=8, bg=COLORS['card_bg'])
        self.scene_tabs_bar.pack(fill=tk.X)
        self._scene_buttons = {}

        for scene_id in PAGE_SCENE_MAP.get(self.current_page_id, []):
            label = self.prompt_center.get_scene_def(scene_id)['label']
            shell, button = self._create_prompt_button(
                self.scene_tabs_bar,
                label,
                style='pill_active' if scene_id == self.current_scene_id else 'pill',
                command=lambda sid=scene_id: self.focus_scene(scene_id=sid),
                padx=14,
                pady=7,
            )
            self.scene_tabs_bar.add(shell)
            self._scene_buttons[scene_id] = button
        self.scene_tabs_bar.after_idle(self.scene_tabs_bar._relayout)
        self.scene_tabs_bar.update_idletasks()

    def _refresh_scene_header(self):
        scene_def = self.prompt_center.get_scene_def(self.current_scene_id)
        description = (scene_def.get('description') or '').strip()
        warning = (scene_def.get('warning') or '').strip()

        self.scene_desc_label.configure(text=description)
        if description:
            if self.scene_desc_label.winfo_manager() != 'pack':
                self.scene_desc_label.pack(fill=tk.X, pady=(10, 0), before=self.scene_note_label)
        elif self.scene_desc_label.winfo_manager() == 'pack':
            self.scene_desc_label.pack_forget()

        self.scene_note_label.configure(text=warning)
        if warning:
            if self.scene_note_label.winfo_manager() != 'pack':
                self.scene_note_label.pack(fill=tk.X, pady=(6, 0))
        elif self.scene_note_label.winfo_manager() == 'pack':
            self.scene_note_label.pack_forget()

    def _refresh_prompt_list(self):
        for widget in self.prompt_list_inner.winfo_children():
            widget.destroy()

        scene = self.prompt_center.get_scene_state(self.current_scene_id)
        prompts = scene.get('prompts', [])
        active_id = scene.get('active_prompt_id', '')

        if not prompts:
            tk.Label(
                self.prompt_list_inner,
                text='当前分组还没有提示词。请先新增一条提示词。',
                font=FONTS['body'],
                fg=COLORS['text_muted'],
                bg=COLORS['card_bg'],
                justify='left',
                anchor='w',
            ).pack(fill=tk.X, pady=24)
            return

        for prompt in prompts:
            self._build_prompt_card(prompt, is_active=prompt.get('id') == active_id)

    def _build_prompt_card(self, prompt, *, is_active):
        shell = tk.Frame(self.prompt_list_inner, bg=self.prompt_list_inner.cget('bg'))
        shell.pack(fill=tk.X, pady=(0, 10))

        card = RoundedFrame(
            shell,
            bg=shell.cget('bg'),
            fill=COLORS['card_bg'],
            outline=COLORS['primary'] if is_active else COLORS['card_border'],
            radius=14,
        )
        card.pack(fill=tk.X)

        row = tk.Frame(card.body, bg=COLORS['card_bg'])
        row.pack(fill=tk.X, padx=14, pady=12)
        row.grid_columnconfigure(0, weight=1)

        left = tk.Frame(row, bg=COLORS['card_bg'])
        left.grid(row=0, column=0, sticky='ew')

        badge = tk.Label(
            left,
            text='已启用' if is_active else '未启用',
            font=FONTS['small'],
            fg='#FFFFFF' if is_active else COLORS['text_sub'],
            bg=COLORS['primary'] if is_active else COLORS['surface_alt'],
            padx=8,
            pady=4,
        )
        badge.pack(anchor='w')

        tk.Label(
            left,
            text=prompt.get('name', '未命名提示词'),
            font=FONTS['body_bold'],
            fg=COLORS['text_main'],
            bg=COLORS['card_bg'],
        ).pack(anchor='w', pady=(8, 2))

        desc = prompt.get('description', '').strip() or '暂无描述'
        source_label = '系统默认' if prompt.get('source') == 'system' else '自定义'
        tk.Label(
            left,
            text=f'{source_label} · {desc}',
            font=FONTS['small'],
            fg=COLORS['text_sub'],
            bg=COLORS['card_bg'],
            justify='left',
            anchor='w',
        ).pack(anchor='w')

        right = tk.Frame(row, bg=COLORS['card_bg'])
        right.grid(row=0, column=1, padx=(16, 0), sticky='e')
        if not is_active:
            activate_shell, _button = self._create_prompt_button(right, '启用', style='accent', command=lambda pid=prompt['id']: self._activate(pid), padx=10, pady=6)
            activate_shell.pack(side=tk.LEFT, padx=(0, 8))
        copy_shell, _button = self._create_prompt_button(right, '复制', style='secondary', command=lambda item=prompt: self._copy_prompt(item), padx=10, pady=6)
        edit_shell, _button = self._create_prompt_button(right, '编辑', style='secondary', command=lambda item=prompt: self._start_edit(item), padx=10, pady=6)
        delete_shell, _button = self._create_prompt_button(right, '删除', style='ghost', command=lambda pid=prompt['id'], name=prompt.get('name', ''): self._delete(pid, name), padx=10, pady=6)
        copy_shell.pack(side=tk.LEFT, padx=(0, 8))
        edit_shell.pack(side=tk.LEFT, padx=(0, 8))
        delete_shell.pack(side=tk.LEFT)

    def _start_create(self):
        self._ensure_editor_dialog()
        self._reset_editor_state()
        self.set_status('已打开提示词编辑窗口')
        self._focus_editor(focus_name=True)

    def _start_edit(self, prompt):
        self._ensure_editor_dialog()
        self._editing_prompt_id = prompt.get('id')
        self._editing_source = prompt.get('source', 'user')
        self._set_editor_title(f'编辑提示词：{prompt.get("name", "")}')
        self.name_var.set(prompt.get('name', ''))
        self.desc_var.set(prompt.get('description', ''))
        self._set_editor_content(prompt.get('content', ''))
        self._refresh_variable_tip()
        self.set_status(f'正在编辑提示词：{prompt.get("name", "未命名提示词")}')
        self._focus_editor(focus_name=False)

    def _copy_prompt(self, prompt):
        copied_prompt = self.prompt_center.duplicate_prompt(self.current_scene_id, prompt.get('id'))
        self._refresh_scene()
        self._start_edit(copied_prompt)
        self.set_status('提示词已复制')

    def _reset_editor(self):
        self._reset_editor_state()
        self._close_editor_dialog()
        self.set_status('已取消编辑')

    def _refresh_variable_tip(self):
        if not self.current_scene_id:
            return
        scene_def = self.prompt_center.get_scene_def(self.current_scene_id)
        variables = scene_def.get('variables', ())
        required = set(scene_def.get('required_variables', ()))
        parts = []
        for name, label in variables:
            placeholder = f'{{{name}}}'
            marker = '（必填）' if name in required else ''
            parts.append(f'{placeholder}={label}{marker}')
        text = (
            '直接书写完整 Markdown 提示词。可用变量：'
            + ('；'.join(parts) if parts else '（无）')
            + '。保存时会校验变量是否合法。'
        )
        if self.variable_tip_label:
            self.variable_tip_label.configure(text=text)
        if self._editor_dialog_variable_tip_label:
            self._editor_dialog_variable_tip_label.configure(text=text)

    def _save_current(self):
        content = self._get_editor_content()
        try:
            self.prompt_center.save_prompt(
                self.current_scene_id,
                self._editing_prompt_id,
                name=self.name_var.get(),
                description=self.desc_var.get(),
                mode=PROMPT_MODE_TEMPLATE,
                content=content,
                source=self._editing_source,
            )
        except PromptValidationError as exc:
            messagebox.showerror('保存失败', str(exc), parent=self.frame.winfo_toplevel())
            return

        self._refresh_scene()
        self.set_status('提示词已保存')
        self._reset_editor_state()
        self._close_editor_dialog()

    def _activate(self, prompt_id):
        self.prompt_center.activate_prompt(self.current_scene_id, prompt_id)
        self._refresh_scene()
        self.set_status('提示词已切换')

    def _delete(self, prompt_id, name):
        if not messagebox.askyesno('删除提示词', f'确定删除「{name or "未命名提示词"}」吗？此操作不可恢复。', parent=self.frame.winfo_toplevel()):
            return
        self.prompt_center.delete_prompt(self.current_scene_id, prompt_id)
        self._refresh_scene()
        if self._editing_prompt_id == prompt_id:
            self._reset_editor()
        self.set_status('提示词已删除')
