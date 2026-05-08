# -*- mode: python ; coding: utf-8 -*-

import os
import sys


SPEC_DIR = os.path.abspath(SPECPATH)
sys.path.insert(0, SPEC_DIR)
APP_VERSION = os.environ.get('APP_VERSION', '0.0.0')

RESOURCE_FILES = (
    ('logo.png', '.'),
    ('loading.gif', '.'),
    ('modules/prompt_defaults.json', 'modules'),
    ('modules/paper_ppt_agent_runner.py', 'modules'),
)

RESOURCE_DIRS = (
    ('web', 'web'),
    ('paper-ppt-agent-master/assets', 'paper-ppt-agent-master/assets'),
    ('paper-ppt-agent-master/backend', 'paper-ppt-agent-master/backend'),
    ('paper-ppt-agent-master/frontend', 'paper-ppt-agent-master/frontend'),
    ('paper-ppt-agent-master/scripts', 'paper-ppt-agent-master/scripts'),
)

PPT_AGENT_FILES = (
    'LICENSE',
    'README.md',
    'README.en.md',
    'pyproject.toml',
    'uv.lock',
)

EXCLUDED_RESOURCE_DIR_NAMES = (
    '.git',
    '.runtime',
    '.venv',
    '__pycache__',
    'node_modules',
    'tests',
    'workspaces',
)

EXCLUDED_RESOURCE_PATHS = (
    os.path.normpath('web/generated'),
)

HIDDENIMPORTS = [
    'docx',
    'docx.shared',
    'docx.enum.text',
    'fitz',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
]

if sys.platform != 'win32':
    EXCLUDES = ['win32api', 'win32com', 'win32con', 'pywintypes', 'pythoncom', 'winreg', 'tkinter']
else:
    EXCLUDES = ['tkinter', 'pystray']


def _require_path(relative_path):
    absolute_path = os.path.join(SPEC_DIR, *relative_path.split('/'))
    if not os.path.exists(absolute_path):
        raise FileNotFoundError(f'Missing resource: {absolute_path}')
    return absolute_path


def _is_excluded_resource_path(path):
    relative = os.path.normpath(os.path.relpath(path, SPEC_DIR))
    parts = set(relative.split(os.sep))
    if any(name in parts for name in EXCLUDED_RESOURCE_DIR_NAMES):
        return True
    return any(relative == excluded or relative.startswith(excluded + os.sep) for excluded in EXCLUDED_RESOURCE_PATHS)


def _build_datas():
    datas = []
    for relative_path, target_dir in RESOURCE_FILES:
        datas.append((_require_path(relative_path), target_dir))

    for filename in PPT_AGENT_FILES:
        datas.append((_require_path(f'paper-ppt-agent-master/{filename}'), 'paper-ppt-agent-master'))

    for relative_dir, target_root in RESOURCE_DIRS:
        source_root = _require_path(relative_dir)
        for current_root, dirnames, filenames in os.walk(source_root):
            dirnames[:] = [
                dirname for dirname in dirnames
                if not _is_excluded_resource_path(os.path.join(current_root, dirname))
            ]
            if _is_excluded_resource_path(current_root):
                continue
            relative_subdir = os.path.relpath(current_root, source_root)
            destination_dir = target_root if relative_subdir == '.' else os.path.join(target_root, relative_subdir)
            for filename in filenames:
                source_file = os.path.join(current_root, filename)
                if not _is_excluded_resource_path(source_file):
                    datas.append((source_file, destination_dir))
    return datas


a = Analysis(
    ['web_main.py'],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=_build_datas(),
    hiddenimports=HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AI_Paper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='AI_Paper.app',
        bundle_identifier='com.paperlab.web',
        info_plist={
            'CFBundleName': 'AI_Paper',
            'CFBundleDisplayName': '论文工坊',
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'NSHighResolutionCapable': True,
        },
    )
