"""FFmpeg 合成：统一规格、拼接、BGM、字幕烧录。"""
import subprocess
import tempfile
from pathlib import Path

from . import config
from .analyze import probe
from .config import load_config


def _ffmpeg() -> str:
    base = config.resolve_tool_path(
        load_config().get("ffmpeg_dir", ""), "runtime/bin"
    )
    candidate = (Path(base) / "ffmpeg.exe") if Path(base).is_dir() else Path(base)
    return str(candidate) if candidate.exists() else "ffmpeg"


RESOLUTIONS = ("1080p", "720p", "480p")

_SIZES = {
    ("9:16", "1080p"): (1080, 1920),
    ("9:16", "720p"): (720, 1280),
    ("9:16", "480p"): (480, 854),
    ("16:9", "1080p"): (1920, 1080),
    ("16:9", "720p"): (1280, 720),
    ("16:9", "480p"): (854, 480),
    ("1:1", "1080p"): (1080, 1080),
    ("1:1", "720p"): (720, 720),
    ("1:1", "480p"): (480, 480),
    ("3:4", "1080p"): (1080, 1440),
    ("3:4", "720p"): (720, 960),
    ("3:4", "480p"): (480, 640),
    ("4:3", "1080p"): (1440, 1080),
    ("4:3", "720p"): (960, 720),
    ("4:3", "480p"): (640, 480),
}


def size_for_ratio(ratio: str, resolution: str = "1080p") -> tuple[int, int]:
    if resolution not in RESOLUTIONS:
        resolution = "1080p"
    return _SIZES.get((ratio, resolution)) or _SIZES[("9:16", "1080p")]


def normalize_clip(clip: Path, dest: Path, ratio: str = "9:16", fps: int = 30,
                   resolution: str = "1080p") -> Path:
    w, h = size_for_ratio(ratio, resolution)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}"
    )
    if probe(clip)["has_audio"]:
        cmd = [
            _ffmpeg(), "-y", "-i", str(clip), "-map", "0:v:0", "-map", "0:a:0",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(dest),
        ]
    else:
        cmd = [
            _ffmpeg(), "-y", "-i", str(clip),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(dest),
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"镜头统一规格失败：{proc.stderr[-1500:]}")
    return dest


def concat(clips: list[Path], out: Path, ratio: str = "9:16", bgm: Path | None = None,
           resolution: str = "1080p") -> Path:
    if not clips:
        raise RuntimeError("没有可拼接的镜头")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(out.parent)) as tmp:
        tmp_dir = Path(tmp)
        normalized = []
        for i, clip in enumerate(clips, 1):
            normalized.append(
                normalize_clip(Path(clip), tmp_dir / f"norm_{i:03d}.mp4", ratio,
                               resolution=resolution)
            )

        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in normalized), encoding="utf-8"
        )
        joined = tmp_dir / "joined.mp4"
        cmd = [
            _ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(joined),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"拼接失败：{proc.stderr[-1500:]}")

        if bgm and Path(bgm).exists():
            cmd = [
                _ffmpeg(), "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", "[1:a]volume=0.25[bg];[0:a][bg]amix=inputs=2:duration=first[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out),
            ]
        else:
            cmd = [_ffmpeg(), "-y", "-i", str(joined), "-c", "copy", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"输出失败：{proc.stderr[-1500:]}")
    return out


def burn_subtitles(video: Path, srt: Path, out: Path) -> Path:
    """把 SRT 烧进画面，使用微软雅黑，避免中文方块。"""
    video, srt, out = Path(video).resolve(), Path(srt).resolve(), Path(out).resolve()
    style = (
        "FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&HFFFFFF&,"
        "OutlineColour=&H000000&,BorderStyle=1,Outline=1,Shadow=0,MarginV=40"
    )
    vf = f"subtitles={srt.name}:force_style='{style}'"
    cmd = [
        _ffmpeg(), "-y", "-i", str(video), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy", str(out),
    ]
    proc = subprocess.run(
        cmd, cwd=str(srt.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"字幕烧录失败：{proc.stderr[-1500:]}")
    return out
