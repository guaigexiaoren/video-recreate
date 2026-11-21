"""视频下载：抖音/快手复用 video-downloader Skill 的脚本，其它平台走 yt-dlp。"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config
from .config import load_config


def _to_msys(path: Path) -> str:
    raw = str(Path(path).resolve()).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", raw)
    if match:
        return f"/{match.group(1).lower()}/{match.group(2)}"
    return raw


def build_env() -> dict:
    cfg = load_config()
    env = os.environ.copy()

    # 让 Git Bash 里的 mktemp 输出相对路径，Windows 上的 python3 才能打开
    tmp_dir = config.DATA_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = os.path.relpath(tmp_dir, config.EXE_DIR).replace("\\", "/")
    env["TEMP"] = env["TMP"] = str(tmp_dir)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    if config.FROZEN:
        dirs = [
            config.RUNTIME_DIR / "python",
            config.RUNTIME_DIR / "bin",
            config.RUNTIME_DIR / "git" / "bin",
            config.RUNTIME_DIR / "git" / "usr" / "bin",
            config.RUNTIME_DIR / "git" / "ucrt64" / "bin",
        ]
        parts = [str(path) for path in dirs if path.is_dir()]
        env["PATH"] = ";".join(parts) + (";" + env["PATH"] if env.get("PATH") else "")
        env["HOME"] = str(config.DATA_DIR / "home")
        env["HF_HOME"] = str(config.DATA_DIR / "models" / "hf")
        env["HF_HUB_DISABLE_XET"] = "1"
        for folder in (
            config.DATA_DIR / "home",
            config.DATA_DIR / "models" / "hf",
        ):
            folder.mkdir(parents=True, exist_ok=True)
    else:
        extra = [
            config.resolve_tool_path(cfg.get("ffmpeg_dir", ""), "runtime/bin"),
            Path.home() / ".local" / "bin",
            Path.home() / "tools" / "bin",
        ]
        parts = [str(path) for path in extra if str(path) and Path(path).exists()]
        if parts:
            env["PATH"] = ";".join(parts) + ";" + env.get("PATH", "")
    return env


def _bash() -> str:
    cfg = load_config()
    if config.FROZEN:
        bundled = config.resolve_tool_path(cfg.get("git_bash"), "runtime/git/bin/bash.exe")
        if bundled.is_file():
            return str(bundled)
    bash = cfg.get("git_bash") or "bash"
    if Path(bash).exists():
        return bash
    found = shutil.which("bash")
    if not found:
        raise RuntimeError("找不到 Git Bash，请在「设置」中配置命令行工具路径")
    return found


def _ytdlp() -> str:
    if config.FROZEN:
        bundled = config.RUNTIME_DIR / "bin" / "yt-dlp.exe"
        if bundled.is_file():
            return str(bundled)
    found = shutil.which("yt-dlp")
    if found:
        return found
    exe = Path.home() / ".local" / "bin" / "yt-dlp.exe"
    if exe.exists():
        return str(exe)
    raise RuntimeError("找不到 yt-dlp，请检查 runtime/bin 或先安装（uv tool install yt-dlp）")


def download(url: str, out_dir: Path, mode: str = "compat") -> tuple[Path, str]:
    """下载到 out_dir，返回 (文件路径, 日志)。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in out_dir.glob("*.mp4")}
    env = build_env()
    lower = url.lower()

    if "douyin.com" in lower:
        cmd = [_bash(), _to_msys(config.DOWNLOAD_SCRIPTS / "download-douyin.sh"),
               mode, url, _to_msys(out_dir)]
    elif "kuaishou.com" in lower:
        cmd = [_bash(), _to_msys(config.DOWNLOAD_SCRIPTS / "download-kuaishou.sh"),
               mode, url, _to_msys(out_dir)]
    else:
        template = str(out_dir / "%(title).80s.%(ext)s")
        cmd = [_ytdlp(), "-f", "bv*+ba/b", "--merge-output-format", "mp4",
               "--no-playlist", "-o", template, url]

    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(config.EXE_DIR), timeout=1800,
    )
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"下载失败（退出码 {proc.returncode}）：\n{log[-2000:]}")

    candidates = [p for p in out_dir.glob("*.mp4") if p.resolve() not in before]
    if not candidates:
        candidates = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"下载完成但没找到 mp4 文件：\n{log[-2000:]}")
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest, log[-2000:]
