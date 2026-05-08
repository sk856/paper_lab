# -*- coding: utf-8 -*-
"""Build the Web-only distribution for 论文工坊."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys


if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


APP_NAME = "AI_Paper"
APP_PACKAGE_ID = "paper-lab"
APP_DISPLAY_NAME = "论文工坊"
APP_DESCRIPTION = "AI 论文 Web 工作台"
APP_HOMEPAGE = "https://github.com/sk856/paper_lab"
APP_MAINTAINER = "PaperLab <1444170707@qq.com>"
SPEC_FILE = "web_workbench.spec"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
VERSION_PATTERN = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")


def read_command_output(command):
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def normalize_version(value):
    if not value:
        return None
    candidate = str(value).strip()
    if candidate.startswith("refs/tags/"):
        candidate = candidate[len("refs/tags/"):]
    match = VERSION_PATTERN.fullmatch(candidate)
    if not match:
        return None
    version = match.group("version")
    return version, f"v{version}"


def resolve_version():
    candidates = [
        os.environ.get("BUILD_VERSION"),
        os.environ.get("GITHUB_REF_NAME"),
    ]
    point_tags = read_command_output(["git", "tag", "--points-at", "HEAD"])
    if point_tags:
        candidates.extend(line.strip() for line in point_tags.splitlines() if line.strip())
    latest_tag = read_command_output(["git", "describe", "--tags", "--abbrev=0"])
    if latest_tag:
        candidates.append(latest_tag)
    for candidate in candidates:
        normalized = normalize_version(candidate)
        if normalized:
            return normalized
    return "0.0.0", "v0.0.0"


APP_VERSION, APP_VERSION_TAG = resolve_version()


def detect_platform():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def require_path(path, description):
    if not os.path.exists(path):
        raise FileNotFoundError(f"[build] Missing {description}: {path}")
    return path


def require_command(name):
    command = shutil.which(name)
    if not command:
        raise FileNotFoundError(f"[build] Missing dependency: {name}")
    return command


def run_pyinstaller():
    env = os.environ.copy()
    env["APP_VERSION"] = APP_VERSION
    env["APP_VERSION_TAG"] = APP_VERSION_TAG
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        DIST_DIR,
        "--workpath",
        BUILD_DIR,
        SPEC_FILE,
    ]
    print(f'[build] Running: {" ".join(cmd)}')
    subprocess.check_call(cmd, cwd=PROJECT_DIR, env=env)
    print(f"[build] PyInstaller finished. Output in {DIST_DIR}")


def release_basename(platform_name=None):
    return f"{APP_NAME}-{APP_VERSION_TAG}-{platform_name or detect_platform()}"


def release_path(extension, *, platform_name=None, suffix=""):
    basename = release_basename(platform_name)
    if suffix:
        basename = f"{basename}-{suffix}"
    return os.path.join(DIST_DIR, f"{basename}{extension}")


def copy_release_file(source_path, extension, *, platform_name=None, suffix=""):
    source_path = require_path(source_path, "release source file")
    output_path = release_path(extension, platform_name=platform_name, suffix=suffix)
    if os.path.exists(output_path):
        os.remove(output_path)
    shutil.copy2(source_path, output_path)
    print(f"[build] Release asset prepared: {output_path}")
    return output_path


def create_windows_release_executable():
    executable_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    return copy_release_file(executable_path, ".exe", platform_name="windows")


def create_windows_installer():
    iss_path = os.path.join(PROJECT_DIR, "installers", "windows_setup.iss")
    require_path(iss_path, "Windows installer script")
    iscc = None
    for candidate in [
        shutil.which("ISCC"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]:
        if candidate and os.path.isfile(candidate):
            iscc = candidate
            break
    if not iscc:
        raise FileNotFoundError("[build] Missing dependency: Inno Setup (ISCC)")
    subprocess.check_call([iscc, f"/DMyAppVersion={APP_VERSION}", iss_path], cwd=PROJECT_DIR)
    print("[build] Windows installer created")


def create_macos_dmg():
    app_path = os.path.join(DIST_DIR, f"{APP_NAME}.app")
    dmg_path = release_path(".dmg", platform_name="macos")
    require_path(app_path, "macOS app bundle")
    if os.path.exists(dmg_path):
        os.remove(dmg_path)
    subprocess.check_call([
        "hdiutil",
        "create",
        "-volname",
        APP_DISPLAY_NAME,
        "-srcfolder",
        app_path,
        "-ov",
        "-format",
        "UDZO",
        dmg_path,
    ])
    print(f"[build] DMG created: {dmg_path}")


def create_linux_archive():
    executable_path = os.path.join(DIST_DIR, APP_NAME)
    archive_path = release_path(".tar.gz", platform_name="linux", suffix=platform.machine().lower())
    require_path(executable_path, "Linux executable")
    if os.path.exists(archive_path):
        os.remove(archive_path)
    subprocess.check_call(["tar", "-czf", archive_path, "-C", DIST_DIR, APP_NAME])
    print(f"[build] Linux archive created: {archive_path}")


def main():
    parser = argparse.ArgumentParser(description=f"{APP_DISPLAY_NAME} Web-only build script")
    parser.add_argument("--installer", action="store_true", help="Also create platform installer/archive")
    parser.add_argument("--clean", action="store_true", help="Clean build/dist directories first")
    args = parser.parse_args()

    platform_name = detect_platform()
    print(f"[build] Platform: {platform_name}")
    print(f"[build] App: {APP_NAME} {APP_VERSION_TAG} (Web-only)")

    if args.clean:
        for directory in [DIST_DIR, BUILD_DIR]:
            if os.path.isdir(directory):
                print(f"[build] Cleaning {directory}")
                shutil.rmtree(directory)

    run_pyinstaller()
    if platform_name == "windows":
        create_windows_release_executable()

    if args.installer:
        if platform_name == "windows":
            create_windows_installer()
        elif platform_name == "macos":
            create_macos_dmg()
        else:
            create_linux_archive()

    print(f"[build] Done! Output in {DIST_DIR}")


if __name__ == "__main__":
    main()
