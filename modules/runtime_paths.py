from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass


APP_DATA_DIR_NAME = '\u7eb8\u7814\u793e'
DATA_DIR_POINTER_FILE = 'config_dir.json'
LOGS_DIR_NAME = 'logs'
TEMP_DIR_NAME = 'temp'


def _normalize_path(path):
    return os.path.abspath(os.path.expanduser(str(path or '.')))


def _project_root():
    return _normalize_path(os.path.join(os.path.dirname(__file__), os.pardir))


def _resolve_resource_root():
    if getattr(sys, 'frozen', False):
        return _normalize_path(getattr(sys, '_MEIPASS', '') or os.path.dirname(sys.executable))
    return _project_root()


def _resolve_app_root():
    if getattr(sys, 'frozen', False):
        return _normalize_path(os.path.dirname(sys.executable))
    return _project_root()


def _resolve_default_base_data_root():
    if sys.platform == 'win32':
        local_appdata = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        if local_appdata:
            return _normalize_path(os.path.join(local_appdata, APP_DATA_DIR_NAME))
        return _normalize_path(os.path.join(_resolve_app_root(), 'user_data'))

    if not getattr(sys, 'frozen', False):
        return _project_root()

    if sys.platform == 'darwin':
        support_dir = os.path.expanduser('~/Library/Application Support')
        if os.path.isdir(support_dir):
            return _normalize_path(os.path.join(support_dir, APP_DATA_DIR_NAME))

    xdg_data = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return _normalize_path(os.path.join(xdg_data, APP_DATA_DIR_NAME))


def _read_data_dir_pointer(base_data_root):
    pointer_path = os.path.join(_normalize_path(base_data_root), DATA_DIR_POINTER_FILE)
    if not os.path.exists(pointer_path):
        return None

    try:
        with open(pointer_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        target_dir = _normalize_path(payload.get('config_dir', ''))
        if target_dir and os.path.isdir(target_dir):
            return target_dir
    except Exception:
        pass
    return None


def resolve_runtime_data_root(base_data_root):
    normalized_base = _normalize_path(base_data_root)
    pointed_dir = _read_data_dir_pointer(normalized_base)
    return pointed_dir or normalized_base


def persist_runtime_data_root(base_data_root, target_dir):
    normalized_base = _normalize_path(base_data_root)
    normalized_target = _normalize_path(target_dir)
    pointer_path = os.path.join(normalized_base, DATA_DIR_POINTER_FILE)

    os.makedirs(normalized_base, exist_ok=True)
    if not normalized_target or normalized_target == normalized_base:
        if os.path.exists(pointer_path):
            os.remove(pointer_path)
        return

    with open(pointer_path, 'w', encoding='utf-8') as handle:
        json.dump({'config_dir': normalized_target}, handle, ensure_ascii=False, indent=2)


def _join_path(base_path, *parts):
    tokens = [base_path]
    for part in parts:
        text = str(part or '').strip()
        if not text:
            continue
        tokens.append(text)
    return _normalize_path(os.path.join(*tokens))


@dataclass(frozen=True)
class RuntimePaths:
    resource_root: str
    app_root: str
    base_data_root: str
    data_root: str
    logs_dir: str
    temp_dir: str

    def resolve_resource(self, *parts):
        return _join_path(self.resource_root, *parts)

    def resolve_data(self, *parts):
        return _join_path(self.data_root, *parts)


def build_runtime_paths():
    resource_root = _resolve_resource_root()
    app_root = _resolve_app_root()
    base_data_root = _resolve_default_base_data_root()
    data_root = resolve_runtime_data_root(base_data_root)
    return RuntimePaths(
        resource_root=resource_root,
        app_root=app_root,
        base_data_root=base_data_root,
        data_root=data_root,
        logs_dir=_join_path(data_root, LOGS_DIR_NAME),
        temp_dir=_join_path(data_root, TEMP_DIR_NAME),
    )


_RUNTIME_PATHS = None


def get_runtime_paths():
    global _RUNTIME_PATHS
    if _RUNTIME_PATHS is None:
        _RUNTIME_PATHS = build_runtime_paths()
    return _RUNTIME_PATHS


def refresh_runtime_paths():
    global _RUNTIME_PATHS
    _RUNTIME_PATHS = build_runtime_paths()
    return _RUNTIME_PATHS


def resolve_resource_path(*parts):
    return get_runtime_paths().resolve_resource(*parts)


def resolve_data_path(*parts):
    return get_runtime_paths().resolve_data(*parts)
