# -*- coding: utf-8 -*-
"""
远程内容管理模块 - 从 GitHub 拉取 JSON 配置内容
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

from modules.runtime_paths import resolve_resource_path

BASE_URL = 'https://raw.githubusercontent.com/Abnerla/AI_paper/main/Management'

ENDPOINTS = {
    'announcement': f'{BASE_URL}/announcement.json',
    'about': f'{BASE_URL}/about.json',
    'push': f'{BASE_URL}/push.json',
    'version': f'{BASE_URL}/version.json',
    'skills_index': f'{BASE_URL}/skills_index.json',
}
LOCAL_FALLBACK_FILES = {
    'skills_index': resolve_resource_path('Management', 'skills_index.json'),
}

CACHE_TTL = 300  # 5 分钟
FETCH_TIMEOUT = 10  # 秒

VERSION_SEGMENT_COUNT = 3


def normalize_version(version, min_segments=VERSION_SEGMENT_COUNT):
    """将版本号规范化为至少三段，兼容旧的两段写法。"""
    raw = str(version or '').strip()
    prefix = 'v' if raw[:1] in {'v', 'V'} else ''
    body = raw.lstrip('vV')
    parts = []
    for seg in body.split('.'):
        seg = seg.strip()
        if not seg:
            parts.append(0)
            continue
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    if not parts:
        parts = [0]
    target_len = max(int(min_segments or 0), len(parts))
    parts.extend([0] * (target_len - len(parts)))
    return f'{prefix}{".".join(str(part) for part in parts)}'


def compare_versions(local, remote):
    """比较两个版本号，返回 -1（本地旧）、0（相同）、1（本地新）"""
    def _parse(v):
        v = normalize_version(v).lstrip('vV')
        parts = []
        for seg in v.split('.'):
            try:
                parts.append(int(seg))
            except ValueError:
                parts.append(0)
        return parts or [0]

    lp, rp = _parse(local), _parse(remote)
    length = max(len(lp), len(rp))
    lp.extend([0] * (length - len(lp)))
    rp.extend([0] * (length - len(rp)))
    for a, b in zip(lp, rp):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


class RemoteContentManager:
    """从 GitHub Raw URL 拉取并缓存远程 JSON 内容"""

    def __init__(self, scheduler, log_callback=None):
        self._scheduler = scheduler
        self._log = log_callback
        self._cache = {}
        self._cache_ts = {}
        self._lock = threading.Lock()

    def _write_log(self, msg, level='INFO'):
        if callable(self._log):
            try:
                self._log(msg, level=level)
            except Exception:
                pass

    def fetch(self, content_key, on_success, on_error=None, force=False):
        """异步获取远程内容，通过回调返回结果到 UI 线程。

        content_key: "announcement" | "about" | "version" | "skills_index"
        on_success(data: dict): 成功回调
        on_error(exc: Exception): 失败回调（可选）
        force: 是否忽略缓存强制拉取
        """
        url = ENDPOINTS.get(content_key)
        if not url:
            if on_error:
                self._scheduler.after(0, lambda: on_error(ValueError(f'未知内容键: {content_key}')))
            return

        if not force:
            with self._lock:
                cached = self._cache.get(content_key)
                ts = self._cache_ts.get(content_key, 0)
            if cached and (time.time() - ts) < CACHE_TTL:
                self._scheduler.after(0, lambda d=cached: on_success(d))
                return

        t = threading.Thread(target=self._worker, args=(content_key, url, on_success, on_error), daemon=True)
        t.start()

    def get_cached(self, content_key):
        """同步获取缓存内容，无缓存返回 None"""
        with self._lock:
            return self._cache.get(content_key)

    def has_new_announcement(self, last_seen_id):
        """判断缓存的公告 id 是否与 last_seen_id 不同"""
        with self._lock:
            data = self._cache.get('announcement')
        if not data:
            return False
        current_id = data.get('id', '')
        return bool(current_id) and current_id != last_seen_id

    def has_new_push(self, last_seen_id):
        with self._lock:
            data = self._cache.get('push')
        if not data:
            return False
        current_id = data.get('id', '')
        return bool(current_id) and current_id != last_seen_id

    def _do_fetch(self, url):
        """发起 HTTP GET 请求并解析 JSON（附加时间戳绕过 CDN 缓存）"""
        bust = f'{"&" if "?" in url else "?"}t={int(time.time())}'
        req = urllib.request.Request(url + bust, method='GET')
        req.add_header('User-Agent', 'PaperLab/1.0')
        req.add_header('Cache-Control', 'no-cache')
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw.decode('utf-8'))

    def _load_local_fallback(self, content_key):
        fallback_path = LOCAL_FALLBACK_FILES.get(content_key, '')
        if not fallback_path or not os.path.isfile(fallback_path):
            return None
        with open(fallback_path, 'r', encoding='utf-8') as handle:
            return json.load(handle)

    def _worker(self, content_key, url, on_success, on_error):
        """后台线程：拉取数据并回调 UI 线程"""
        try:
            data = self._do_fetch(url)
            with self._lock:
                self._cache[content_key] = data
                self._cache_ts[content_key] = time.time()
            self._write_log(f'远程内容拉取成功: {content_key}')
            self._scheduler.after(0, lambda d=data: on_success(d))
        except Exception as exc:
            self._write_log(f'远程内容拉取失败: {content_key} - {exc}', level='WARN')
            if on_error:
                self._scheduler.after(0, lambda e=exc: on_error(e))
            else:
                cached = self.get_cached(content_key)
                if cached:
                    self._scheduler.after(0, lambda d=cached: on_success(d))
