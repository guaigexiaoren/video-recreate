"""本地配置读写。API Key 只保存在本机 config.json，不写入代码。"""
import json
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# 源码：项目根；冻结：VideoRecreate.exe 同级的 app/
APP_DIR = Path(__file__).resolve().parent.parent
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else APP_DIR
DATA_DIR = EXE_DIR / "data" if FROZEN else APP_DIR
RUNTIME_DIR = EXE_DIR / "runtime" if FROZEN else APP_DIR / "runtime"
ROOT = APP_DIR

CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.88api.ai",
    "analysis_model": "",
    "image_model": "doubao-seedream-5-0-pro",
    "video_model": "doubao-seedance-2.5",
    "output_dir": "outputs",
    "git_bash": "runtime/git/bin/bash.exe" if FROZEN else "C:/Program Files/Git/bin/bash.exe",
    "ffmpeg_dir": "runtime/bin" if FROZEN else str(Path.home() / "tools" / "bin"),
    "whisper_model": "small",
    "video_ratio": "9:16",
    "video_resolution": "1080p",
    "video_duration": 5,
}

# 工作台依赖的第三方 Skill：源码用本机安装，冻结用随包 runtime/skills
SKILLS_DIR = RUNTIME_DIR / "skills" if FROZEN else Path.home() / ".claude" / "skills"
DEDUP_SCRIPT = SKILLS_DIR / "video-original-dedup" / "scripts" / "dedup_av.py"
DOWNLOAD_SCRIPTS = SKILLS_DIR / "video-downloader" / "scripts"
RECREATION_DIR = SKILLS_DIR / "video-recreation-skill"


def resolve_data_path(value: str | Path) -> Path:
    """相对路径按 DATA_DIR 解析；源码模式下 DATA_DIR 就是项目根。"""
    path = Path(value)
    return path if path.is_absolute() else DATA_DIR / path


def resolve_tool_path(value: str | Path, fallback_rel: str) -> Path:
    """工具目录/文件：优先配置值，其次 EXE_DIR/fallback_rel。"""
    raw = str(value or "").strip()
    candidates: list[Path] = []
    if raw:
        path = Path(raw)
        candidates.append(path if path.is_absolute() else EXE_DIR / path)
    fallback = EXE_DIR / fallback_rel
    candidates.append(fallback)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else EXE_DIR / path
    return fallback


def python_exe() -> Path:
    """外部 .py 脚本使用的 Python；冻结时用随包便携 Python。"""
    if FROZEN:
        path = RUNTIME_DIR / "python" / "python.exe"
        if path.is_file():
            return path
        raise RuntimeError(f"便携 Python 缺失：{path}")
    return Path(sys.executable)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    data = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
