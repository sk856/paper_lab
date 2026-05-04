# -*- coding: utf-8 -*-
"""
Shared background task runner for Tk pages.
"""

import threading


class TaskRunner:
    """Run background work and marshal callbacks back to the UI thread."""

    def __init__(self, scheduler, loading=None, set_status=None, thread_factory=None, log_callback=None):
        self.scheduler = scheduler
        self.loading = loading
        self.set_status = set_status
        self.thread_factory = thread_factory or threading.Thread
        self.log_callback = log_callback or self._resolve_log_callback(set_status)

    def run(
        self,
        *,
        work,
        on_success,
        on_error=None,
        on_start=None,
        loading_text='',
        status_text='',
        status_color=None,
    ):
        task_label = self._build_task_label(work, status_text=status_text, loading_text=loading_text)
        if callable(on_start):
            on_start()
        
        # 确保在启动线程前显示加载动画
        if self.loading and loading_text:
            self.loading.show(loading_text)
            # 在某些情况下 Tkinter 可能需要一点时间来渲染 place 出来的组件
            # 特别是如果后续紧跟着 CPU 密集型操作
            self.scheduler.update_idletasks()
            
        if self.set_status and status_text:
            self.set_status(status_text, status_color)
        self._log(f'[task_start] {task_label}')

        def worker():
            try:
                result = work()
            except Exception as exc:
                # 即使出错也稍微延迟隐藏，避免闪烁
                self.scheduler.after(100, lambda err=exc: self._finish_error(err, on_error, task_label))
                return

            # 成功时也稍微延迟隐藏，让用户看清“处理完成”等状态
            self.scheduler.after(100, lambda value=result: self._finish_success(value, on_success, task_label))

        thread = self.thread_factory(target=worker, daemon=True)
        thread.start()
        return thread

    def _finish_success(self, result, callback, task_label):
        if self.loading:
            self.loading.hide()
        self._log(f'[task_success] {task_label}')
        if callable(callback):
            callback(result)

    def _finish_error(self, exc, callback, task_label):
        if self.loading:
            self.loading.hide()
        self._log(f'[task_error] {task_label} | {exc}', level='ERROR')
        if callable(callback):
            callback(exc)

    @staticmethod
    def _resolve_log_callback(set_status):
        owner = getattr(set_status, '__self__', None)
        callback = getattr(owner, '_write_app_log', None)
        return callback if callable(callback) else None

    @staticmethod
    def _build_task_label(work, *, status_text='', loading_text=''):
        for candidate in (status_text, loading_text):
            text = str(candidate or '').strip()
            if text:
                return text
        work_name = getattr(work, '__name__', '') or work.__class__.__name__
        return work_name or 'unnamed_task'

    def _log(self, message, level='INFO'):
        if callable(self.log_callback):
            self.log_callback(message, level=level)
