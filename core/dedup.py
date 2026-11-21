"""调用 video-original-dedup Skill 的音画去重脚本。"""
import subprocess
from pathlib import Path

from . import config
from .downloader import build_env


def run_dedup(input_path: Path, output_path: Path, mode: str = "subtle") -> tuple[Path, str]:
    if not config.DEDUP_SCRIPT.exists():
        raise RuntimeError(f"找不到去重脚本：{config.DEDUP_SCRIPT}")
    input_path, output_path = Path(input_path), Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 该脚本默认用 macOS 硬件编码器，Windows 必须指定 libx264
    cmd = [
        str(config.python_exe()), str(config.DEDUP_SCRIPT),
        str(input_path), str(output_path),
        "--mode", mode, "--encoder", "libx264",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=build_env(), cwd=str(config.EXE_DIR), timeout=1800,
    )
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"去重失败（退出码 {proc.returncode}）：\n{log[-2000:]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"去重完成但没有输出文件：\n{log[-2000:]}")
    return output_path, log[-2000:]
