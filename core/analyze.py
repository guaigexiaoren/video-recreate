"""视频分析：探测信息、均匀抽帧、可选语音转写。"""
import json
import re
import subprocess
from pathlib import Path

from . import config
from .config import load_config

FRAME_RE = re.compile(r"^frame_\d+_[0-9.]+s\.(?:jpe?g|png)$", re.I)


def _tool(name: str) -> str:
    cfg = load_config()
    base = config.resolve_tool_path(cfg.get("ffmpeg_dir", ""), "runtime/bin")
    candidate = (Path(base) / f"{name}.exe") if Path(base).is_dir() else Path(base)
    if candidate.exists():
        return str(candidate)
    return name


def probe(path: Path) -> dict:
    cmd = [
        _tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    raw = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    data = json.loads(raw)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate", ""),
        "has_audio": bool(audio),
    }


def extract_frames(path: Path, out_dir: Path, count: int = 8) -> list[tuple[float, Path]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    duration = max(info["duration"], 1.0)
    count = max(1, min(count, 16))
    frames = []
    for i in range(count):
        t = round(duration * (i + 0.5) / count, 2)
        dest = out_dir / f"frame_{i + 1:02d}_{t:.1f}s.jpg"
        cmd = [
            _tool("ffmpeg"), "-y", "-ss", str(t), "-i", str(path),
            "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(dest),
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if dest.exists() and dest.stat().st_size > 0:
            frames.append((t, dest))
    if not frames:
        raise RuntimeError("抽帧失败，请检查视频文件和 ffmpeg")
    keep = {p.name for _, p in frames}
    for old in out_dir.glob("frame_*"):
        if FRAME_RE.match(old.name) and old.name not in keep:
            old.unlink(missing_ok=True)
    return frames


def transcribe(path: Path, model_size: str = "small") -> list[dict]:
    """本地 faster-whisper 转写；未安装时抛出可读错误。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("未安装语音识别组件（faster-whisper），无法转写。可在抽帧步骤跳过转写，或执行 py -m pip install faster-whisper")

    model_path = model_size
    kwargs = {"device": "cpu", "compute_type": "int8"}
    if config.FROZEN:
        local_model = config.RUNTIME_DIR / "models" / f"faster-whisper-{model_size}"
        if local_model.is_dir():
            model_path = str(local_model)
        else:
            download_root = config.DATA_DIR / "models" / "hf"
            download_root.mkdir(parents=True, exist_ok=True)
            kwargs["download_root"] = str(download_root)
    model = WhisperModel(model_path, **kwargs)
    segments, _ = model.transcribe(str(path), language="zh", vad_filter=True)
    return [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments if s.text.strip()
    ]


def segments_to_srt(segments: list[dict]) -> str:
    def stamp(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{stamp(seg['start'])} --> {stamp(seg['end'])}", seg["text"], ""]
    return "\n".join(lines)
