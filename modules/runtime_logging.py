# -*- coding: utf-8 -*-
"""
运行日志辅助工具。
"""

import io
import traceback


class RuntimeLogStream(io.TextIOBase):
    """将标准输出重定向到日志写入器，并保留原始输出。"""

    def __init__(self, writer, level='INFO', mirror=None):
        self._writer = writer
        self._level = str(level or 'INFO').upper()
        self._mirror = mirror
        self._buffer = ''

    @property
    def encoding(self):
        return getattr(self._mirror, 'encoding', 'utf-8')

    def writable(self):
        return True

    def isatty(self):
        if self._mirror and hasattr(self._mirror, 'isatty'):
            try:
                return bool(self._mirror.isatty())
            except Exception:
                return False
        return False

    def fileno(self):
        if self._mirror and hasattr(self._mirror, 'fileno'):
            return self._mirror.fileno()
        raise OSError('当前流不支持 fileno')

    def write(self, data):
        text = '' if data is None else str(data)
        if not text:
            return 0

        if self._mirror and hasattr(self._mirror, 'write'):
            try:
                self._mirror.write(text)
            except Exception:
                pass

        self._buffer += text
        self._drain_complete_lines()
        return len(text)

    def flush(self):
        if self._mirror and hasattr(self._mirror, 'flush'):
            try:
                self._mirror.flush()
            except Exception:
                pass
        self._drain_complete_lines(force=True)

    def _drain_complete_lines(self, force=False):
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            self._emit(line)

        if force and self._buffer:
            line = self._buffer
            self._buffer = ''
            self._emit(line)

    def _emit(self, line):
        text = str(line or '').rstrip('\r')
        if not text.strip():
            return
        self._writer(text, level=self._level)


def format_exception_trace(exc_type, exc_value, exc_traceback):
    """格式化异常堆栈，便于写入日志。"""
    return ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback)).strip()
