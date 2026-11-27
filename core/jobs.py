"""任务目录：扫描历史、恢复产物、读写元数据与日志。"""
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
REF_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FRAME_RE = re.compile(r"^frame_(\d+)_([0-9.]+)s\.(?:jpe?g|png)$", re.I)
REF_UNSAFE_RE = re.compile(r"[^\w一-鿿-]+", re.UNICODE)


def resolve_output_root(cfg: dict) -> Path:
    from .config import resolve_data_path

    return resolve_data_path(cfg.get("output_dir") or "outputs")


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_meta(d: Path) -> dict:
    data = _read_json(Path(d) / "meta.json", {})
    return data if isinstance(data, dict) else {}


def write_meta(d: Path, **updates) -> dict:
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    meta = read_meta(d)
    meta.update({k: v for k, v in updates.items() if v is not None})
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def save_transcript(d: Path, transcript: list[dict]) -> None:
    (Path(d) / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_keyframes(d: Path, urls: dict) -> None:
    (Path(d) / "keyframes.json").write_text(
        json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _safe_alias(name: str) -> str:
    stem = Path(name).stem.strip()
    alias = REF_UNSAFE_RE.sub("", stem).strip("-_")
    return (alias or "参考图")[:40]


def unique_alias(base: str, taken: set[str]) -> str:
    base = (base or "参考图").strip() or "参考图"
    if base.casefold() not in taken:
        return base
    n = 2
    while f"{base}{n}".casefold() in taken:
        n += 1
    return f"{base}{n}"


def read_refs(d: Path) -> list[dict]:
    d = Path(d)
    data = _read_json(d / "refs.json", [])
    items = data if isinstance(data, list) else []
    root = d.resolve()
    refs, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or "").strip()
        rel = str(item.get("path") or "").strip()
        name = str(item.get("name") or Path(rel).name)
        if not alias or not rel or alias.casefold() in seen:
            continue
        path = (d / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        seen.add(alias.casefold())
        refs.append({
            "alias": alias,
            "name": name,
            "path": rel,
            "sha256": str(item.get("sha256") or ""),
            "abs_path": str(path),
        })
    if not refs and (d / "refs").is_dir():
        for path in sorted((d / "refs").glob("*")):
            if not path.is_file() or path.suffix.lower() not in REF_EXTS:
                continue
            if path.stat().st_size <= 0:
                continue
            alias = unique_alias(_safe_alias(path.name), seen)
            seen.add(alias.casefold())
            refs.append({
                "alias": alias,
                "name": path.name,
                "path": str(path.relative_to(d)),
                "sha256": "",
                "abs_path": str(path.resolve()),
            })
    return refs


def save_refs(d: Path, refs: list[dict]) -> None:
    data = [
        {key: ref.get(key, "") for key in ("alias", "name", "path", "sha256")}
        for ref in refs
        if ref.get("alias") and ref.get("path")
    ]
    (Path(d) / "refs.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_ref_files(d: Path, files: list[tuple[str, bytes]]) -> tuple[list[dict], list[str], list[str]]:
    d = Path(d)
    refs = read_refs(d)
    refs_dir = d / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    taken = {ref["alias"].casefold() for ref in refs}
    fingerprints = {
        (ref.get("sha256"), Path(ref["name"]).name)
        for ref in refs if ref.get("sha256")
    }
    added, skipped = [], []
    for raw_name, data in files:
        name = Path(raw_name).name
        suffix = Path(name).suffix.lower()
        if not name or suffix not in REF_EXTS or not data:
            skipped.append(name or "未命名文件")
            continue
        digest = hashlib.sha256(data).hexdigest()
        if (digest, name) in fingerprints:
            skipped.append(f"{name}（已存在）")
            continue
        dest = refs_dir / name
        n = 2
        while dest.exists():
            dest = refs_dir / f"{Path(name).stem}_{n}{suffix}"
            n += 1
        dest.write_bytes(data)
        alias = unique_alias(_safe_alias(name), taken)
        taken.add(alias.casefold())
        fingerprints.add((digest, name))
        refs.append({
            "alias": alias,
            "name": name,
            "path": str(dest.relative_to(d)),
            "sha256": digest,
            "abs_path": str(dest.resolve()),
        })
        added.append(alias)
    if added:
        save_refs(d, refs)
    return refs, added, skipped


def remove_ref(d: Path, alias: str) -> dict | None:
    d = Path(d)
    refs = read_refs(d)
    removed = next((ref for ref in refs if ref["alias"] == alias), None)
    if removed is None:
        return None
    save_refs(d, [ref for ref in refs if ref["alias"] != alias])
    path = (d / removed["path"]).resolve()
    try:
        path.relative_to((d / "refs").resolve())
        path.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
    return removed


def append_log(d: Path, line: str) -> None:
    try:
        with (Path(d) / "log.txt").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def has_content(d: Path) -> bool:
    d = Path(d)
    if not d.is_dir():
        return False
    return any(p.is_file() for p in d.rglob("*"))


def _job_time(name: str) -> str:
    try:
        return datetime.strptime(name[:15], "%Y%m%d_%H%M%S").strftime("%m-%d %H:%M")
    except ValueError:
        return name


def find_final(d: Path) -> Path | None:
    for name in ("final.mp4", "subtitled.mp4", "merged.mp4"):
        path = Path(d) / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def find_source(d: Path, meta: dict | None = None) -> Path | None:
    d = Path(d)
    meta = meta if meta is not None else read_meta(d)
    rel = meta.get("source_rel")
    if rel:
        path = (d / rel).resolve()
        try:
            path.relative_to(d.resolve())
        except ValueError:
            path = None
        if path and path.is_file() and path.stat().st_size > 0:
            return path
    candidates = [
        p for p in (d / "source").glob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _nonempty_files(folder: Path, pattern: str) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return [p for p in folder.glob(pattern) if p.is_file() and p.stat().st_size > 0]


def job_status(d: Path) -> tuple[str, str]:
    d = Path(d)
    final = find_final(d)
    if final:
        label = {
            "final.mp4": "已出片",
            "subtitled.mp4": "已合成",
            "merged.mp4": "已拼接",
        }[final.name]
        return label, "green"
    clips = _nonempty_files(d / "clips", "*.mp4")
    if clips:
        return f"镜头 {len(clips)}", "blue"
    if _nonempty_files(d / "keys", "*.png"):
        return "关键帧", "violet"
    if (d / "shots.json").is_file():
        return "已拆解", "orange"
    if _nonempty_files(d / "frames", "frame_*"):
        return "已抽帧", "blue"
    if find_source(d):
        return "有素材", "gray"
    return "空", "gray"


def list_jobs(root: Path) -> list[dict]:
    root = Path(root)
    if not root.is_dir():
        return []
    result = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith((".", "_")) or not has_content(d):
            continue
        meta = read_meta(d)
        source = find_source(d, meta)
        label, color = job_status(d)
        result.append({
            "id": d.name,
            "path": str(d),
            "name": meta.get("name") or d.name,
            "time": _job_time(d.name),
            "status": label,
            "color": color,
            "source_name": source.name if source else "",
        })
    result.sort(key=lambda job: job["id"], reverse=True)
    return result


def load_frames(d: Path) -> list[tuple[float, str]]:
    frames = []
    for path in (Path(d) / "frames").glob("frame_*"):
        match = FRAME_RE.match(path.name)
        if not match or not path.is_file() or path.stat().st_size <= 0:
            continue
        frames.append((float(match.group(2)), str(path)))
    frames.sort(key=lambda item: item[0])
    return frames


def scan_clips(d: Path) -> dict[str, str]:
    return {p.stem: str(p) for p in sorted(_nonempty_files(Path(d) / "clips", "*.mp4"))}


def scan_keyframes(d: Path) -> dict[str, str]:
    d = Path(d)
    data = _read_json(d / "keyframes.json", {})
    urls = {}
    if isinstance(data, dict):
        urls = {str(k): str(v or "") for k, v in data.items()}
    for path in _nonempty_files(d / "keys", "*.png"):
        urls.setdefault(path.stem, "")
    return urls


def load_log(d: Path, limit: int = 200) -> list[str]:
    path = Path(d) / "log.txt"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def normalize_shots(shots: list | None) -> list[dict]:
    result, seen = [], set()
    for i, shot in enumerate(shots or [], 1):
        if not isinstance(shot, dict):
            continue
        shot = dict(shot)
        raw_id = shot.get("id")
        if raw_id is None or (isinstance(raw_id, float) and raw_id != raw_id) or not str(raw_id).strip():
            sid = f"shot_{i:02d}"
        else:
            sid = str(raw_id).strip()
        base, n = sid, 2
        while sid in seen:
            sid = f"{base}_{n}"
            n += 1
        seen.add(sid)
        shot["id"] = sid

        try:
            start = float(shot.get("start") or 0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(shot.get("duration") or 3)
        except (TypeError, ValueError):
            duration = 3.0
        try:
            end = float(shot.get("end") or (start + duration))
        except (TypeError, ValueError):
            end = start + duration
        if end <= start:
            end = start + max(1.0, duration)
        shot["start"] = start
        shot["end"] = end
        shot["duration"] = round(max(1.0, end - start), 2)
        for key in ("semantic_function", "description", "prompt", "dialogue",
                    "camera", "reference"):
            value = shot.get(key, "")
            if value is None or (isinstance(value, float) and value != value):
                shot[key] = ""
            else:
                shot[key] = str(value)
        result.append(shot)
    return result


def load_job_state(d: Path) -> dict:
    d = Path(d)
    meta = read_meta(d)
    shots = _read_json(d / "shots.json", [])
    transcript = _read_json(d / "transcript.json", [])
    final = find_final(d)
    merged = d / "merged.mp4"
    return {
        "meta": meta,
        "source": str(find_source(d, meta) or ""),
        "frames": load_frames(d),
        "shots": normalize_shots(shots if isinstance(shots, list) else []),
        "transcript": transcript if isinstance(transcript, list) else [],
        "keyframe_urls": scan_keyframes(d),
        "clips": scan_clips(d),
        "refs": read_refs(d),
        "final_path": str(final) if final else "",
        "merged_path": str(merged) if merged.is_file() else "",
        "logs": load_log(d),
    }


def make_job_dir(root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / base
    n = 2
    while path.exists():
        path = root / f"{base}_{n}"
        n += 1
    path.mkdir(parents=True)
    return path


def rename_job(d: Path, name: str) -> None:
    write_meta(Path(d), name=name.strip()[:80])


def delete_job(root: Path, d: Path) -> None:
    root = Path(root).resolve()
    target = Path(d).resolve()
    if target.parent != root:
        raise ValueError("只能删除输出目录下的任务")
    if not target.is_dir():
        raise FileNotFoundError(str(target))
    shutil.rmtree(target)
