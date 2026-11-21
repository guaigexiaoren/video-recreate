"""便携版自检：最小 PATH 下检查运行时、工具链和本地流程。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_self_test(full: bool = False) -> int:
    frozen = bool(getattr(sys, "frozen", False))
    exe_dir = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
    app_dir = exe_dir / "app" if frozen else Path(__file__).resolve().parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    from core import analyze, config, dedup, downloader, media

    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    failed = 0

    def record(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
        nonlocal failed
        if not ok and required:
            failed += 1
        tag = "OK" if ok else ("SKIP" if not required else "FAIL")
        lines.append(f"[{tag}] {name}" + (f" - {detail}" if detail else ""))
        print(lines[-1])
        return ok

    env = downloader.build_env()
    exe_dir = config.EXE_DIR

    # 1) Python 依赖
    for module in (
        "streamlit", "pandas", "pyarrow", "faster_whisper", "ctranslate2",
        "tokenizers", "av", "onnxruntime", "huggingface_hub", "webview",
        "clr", "requests",
    ):
        try:
            __import__(module)
            record(f"导入 {module}", True)
        except Exception as exc:
            record(f"导入 {module}", False, str(exc))

    # 2) core 路径
    try:
        import core
        core_path = Path(core.__file__).resolve()
        record(
            "core 路径",
            core_path.is_relative_to(app_dir.resolve()),
            str(core_path),
        )
    except Exception as exc:
        record("core 路径", False, str(exc))

    # 3) ffmpeg / ffprobe
    ffdir = config.resolve_tool_path(
        config.load_config().get("ffmpeg_dir", ""), "runtime/bin"
    )
    for tool in ("ffmpeg", "ffprobe"):
        candidate = (Path(ffdir) / f"{tool}.exe") if Path(ffdir).is_dir() else Path(ffdir)
        if not candidate.is_file():
            candidate = Path(shutil.which(tool) or "")
        if candidate.is_file():
            proc = subprocess.run(
                [str(candidate), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
            )
            record(f"{tool} 版本", proc.returncode == 0, proc.stdout.splitlines()[0] if proc.stdout else "")
        else:
            record(f"{tool} 路径", False, f"未找到，ffmpeg_dir={ffdir}")

    # 4) yt-dlp
    try:
        ytdlp = downloader._ytdlp()
        proc = subprocess.run(
            [ytdlp, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
            cwd=str(exe_dir), timeout=60,
        )
        record("yt-dlp 版本", proc.returncode == 0, proc.stdout.strip())
    except Exception as exc:
        record("yt-dlp 版本", False, str(exc))

    # 5) Git Bash 工具链
    try:
        bash = downloader._bash()
        proc = subprocess.run(
            [bash, "-lc",
             "command -v bash curl awk grep sed sort stat mktemp dirname basename python3 ffmpeg ffprobe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(exe_dir), timeout=60,
        )
        record("Git Bash 工具链", proc.returncode == 0,
               (proc.stdout or proc.stderr).strip().replace("\n", " | "))
    except Exception as exc:
        record("Git Bash 工具链", False, str(exc))

    # 6) 便携 Python / python3
    try:
        py = config.python_exe()
        proc = subprocess.run(
            [str(py), "-c", "import ssl; print(ssl.OPENSSL_VERSION)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(exe_dir), timeout=60,
        )
        record("便携 Python", proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip())
        py3 = shutil.which("python3", path=env.get("PATH"))
        record("python3 命令", bool(py3), py3 or "")
    except Exception as exc:
        record("便携 Python", False, str(exc))

    # 7) 去重脚本 / 复刻策略
    try:
        proc = subprocess.run(
            [str(config.python_exe()), str(config.DEDUP_SCRIPT), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(exe_dir), timeout=60,
        )
        record("去重脚本", proc.returncode == 0, "dedup_av.py --help")
    except Exception as exc:
        record("去重脚本", False, str(exc))
    policy = config.RECREATION_DIR / "specs" / "recreation_policy.json"
    try:
        json.loads(policy.read_text(encoding="utf-8"))
        record("复刻策略文件", True, str(policy))
    except Exception as exc:
        record("复刻策略文件", False, str(exc))

    # 8) 完整本地流程
    if full:
        tmp = config.DATA_DIR / "tmp" / "selftest"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            ffmpeg = media._ffmpeg()
            a, b = tmp / "a.mp4", tmp / "b.mp4"
            for dest, freq in ((a, "440"), (b, "660")):
                proc = subprocess.run(
                    [ffmpeg, "-y",
                     "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                     "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                     "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", str(dest)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(exe_dir), timeout=120,
                )
                record(f"生成测试片段 {dest.name}", proc.returncode == 0, proc.stderr[-300:])
            info = analyze.probe(a)
            record("视频探测", info["duration"] > 0, str(info))
            frames = analyze.extract_frames(a, tmp / "frames", 2)
            record("抽帧", len(frames) == 2, f"{len(frames)} 帧")
            merged = media.concat(
                [a, b], tmp / "merged.mp4", ratio="1:1", resolution="480p"
            )
            record("拼接", merged.is_file() and merged.stat().st_size > 0,
                   analyze.probe(merged).__str__())
            final, _log = dedup.run_dedup(merged, tmp / "dedup.mp4", "subtle")
            record("音画去重", final.is_file() and final.stat().st_size > 0,
                   final.name)

            local_model = config.RUNTIME_DIR / "models" / "faster-whisper-small"
            if local_model.is_dir():
                transcript = analyze.transcribe(a, "small")
                record("语音转写", True, f"{len(transcript)} 句")
            else:
                record("语音转写", False, "未附带 small 模型，首次使用需联网下载", required=False)
        except Exception as exc:
            import traceback

            trace = traceback.format_exc()
            (log_dir / "full-traceback.txt").write_text(trace, encoding="utf-8")
            record("完整本地流程", False, str(exc))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    lines.append("")
    lines.append("自检通过" if failed == 0 else f"自检失败：{failed} 项")
    (log_dir / "self-test.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_self_test(full="--full" in sys.argv))
