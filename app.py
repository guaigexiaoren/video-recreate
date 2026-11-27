"""视频复刻工作台：获取素材 → 拆解复刻 → 合成去重。"""
import base64
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import analyze, config, dedup, downloader, jobs, media, recreate
from core.api88 import Api88, Api88Error, pick_models

st.set_page_config(
    page_title="视频复刻工作台", page_icon=":material/movie:", layout="wide"
)
cfg = config.load_config()


def output_root() -> Path:
    return jobs.resolve_output_root({
        "output_dir": st.session_state.get("cfg_output_dir", cfg["output_dir"])
    })

MODES = ["conservative", "medium", "bold"]
MODE_LABELS = {
    "conservative": "保守（完全复刻）",
    "medium": "中度（锁结构换同类）",
    "bold": "大胆（重写画面文案）",
}
RATIO_OPTIONS = ["9:16", "16:9", "1:1", "3:4", "4:3"]
RATIO_LABELS = {
    "9:16": "竖屏 9:16",
    "16:9": "横屏 16:9",
    "1:1": "方形 1:1",
    "3:4": "竖版 3:4",
    "4:3": "横版 4:3",
}
RESOLUTION_OPTIONS = ["1080p", "720p", "480p"]
RESOLUTION_LABELS = {
    "1080p": "1080P 高清",
    "720p": "720P 标清",
    "480p": "480P 流畅",
}
WHISPER_LABELS = {"small": "小型（快）", "base": "基础", "medium": "中型（更准）"}
DEDUP_LABELS = {"subtle": "轻微（无感）", "aggressive": "强力（含扫光）"}
JOB_MENU = ["重命名", "删除", "打开文件夹"]
PREVIEW_OPTIONS = ["素材", "分镜 / 关键帧", "成片", "日志"]
API_STATUS_LABELS = {
    "pending": "排队中",
    "queued": "排队中",
    "processing": "生成中",
    "running": "生成中",
    "completed": "已完成",
    "succeeded": "已完成",
    "success": "已完成",
    "done": "已完成",
    "finished": "已完成",
    "failed": "失败",
    "error": "失败",
    "cancelled": "已取消",
    "canceled": "已取消",
}
MAX_REF_BYTES = 6 * 1024 * 1024
MAX_REF_TOTAL = 12 * 1024 * 1024
NO_MODEL_OPTION = "（先拉取模型）"
MODEL_SENTINELS = {
    "", "未选择", "请选择模型", "请选择或输入模型", "None", NO_MODEL_OPTION,
}

EDITOR_COLS = [
    "id", "start", "end", "duration", "semantic_function",
    "reference", "description", "prompt", "dialogue", "camera",
]
EDITOR_CONFIG = {
    "id": st.column_config.TextColumn("镜头 ID", width="small"),
    "start": st.column_config.NumberColumn(
        "开始（秒）", min_value=0.0, step=0.1, format="%.1f"
    ),
    "end": st.column_config.NumberColumn(
        "结束（秒）", min_value=0.0, step=0.1, format="%.1f"
    ),
    "duration": st.column_config.NumberColumn("时长（秒）", width="small", format="%.1f"),
    "semantic_function": st.column_config.TextColumn("功能", width="small"),
    "description": st.column_config.TextColumn("画面描述", width="large"),
    "prompt": st.column_config.TextColumn("生成提示词", width="large"),
    "dialogue": st.column_config.TextColumn("台词", width="medium"),
    "camera": st.column_config.TextColumn("运镜", width="medium"),
    "reference": st.column_config.TextColumn(
        "参考附件", width="medium",
        help="填 @别名，多个用空格或逗号分隔；别名由参考图文件名生成",
    ),
}

for key in ("job_dir", "source", "final_path", "merged_path", "download_log",
            "last_raw", "bgm_path", "bgm_hash"):
    st.session_state.setdefault(key, "")
for key in ("frames", "transcript", "shots", "logs", "refs"):
    st.session_state.setdefault(key, [])
for key in ("keyframe_urls", "clips", "meta"):
    st.session_state.setdefault(key, {})
st.session_state.setdefault("models", [])
st.session_state.setdefault("nonce", 0)
st.session_state.setdefault("task_token", "draft_0")
st.session_state.setdefault("active_job", "")
st.session_state.setdefault("shots_nonce", 0)
st.session_state.setdefault("ref_nonce", 0)
st.session_state.setdefault("upload_nonce", 0)
st.session_state.setdefault("preview_section", "素材")
st.session_state.setdefault("task_search", "")
st.session_state.setdefault("search_open", False)
st.session_state.setdefault("search_nonce", 0)
st.session_state.setdefault("cfg_api_key", cfg["api_key"])
st.session_state.setdefault("cfg_base_url", cfg["base_url"])
st.session_state.setdefault("cfg_analysis_model", cfg["analysis_model"])
st.session_state.setdefault("cfg_image_model", cfg["image_model"])
st.session_state.setdefault("cfg_video_model", cfg["video_model"])
st.session_state.setdefault("cfg_ratio", cfg["video_ratio"])
st.session_state.setdefault("cfg_resolution", cfg.get("video_resolution", "1080p"))
st.session_state.setdefault(
    "cfg_video_resolution", cfg.get("video_generation_resolution", "720p")
)
st.session_state.setdefault("cfg_duration", int(cfg["video_duration"]))
st.session_state.setdefault("cfg_whisper", cfg["whisper_model"])
st.session_state.setdefault("cfg_git_bash", cfg["git_bash"])
st.session_state.setdefault("cfg_ffmpeg_dir", cfg["ffmpeg_dir"])
st.session_state.setdefault("cfg_output_dir", cfg["output_dir"])

token = st.session_state.task_token


def tkey(name: str) -> str:
    return f"{name}_{token}"


meta = st.session_state.meta
mode_default = meta.get("mode") if meta.get("mode") in MODES else "conservative"
st.session_state.setdefault(tkey("cfg_mode"), mode_default)
st.session_state.setdefault(tkey("cfg_anchor"), meta.get("anchor", ""))
st.session_state.setdefault(tkey("cfg_user_req"), meta.get("user_req", ""))


def reset_business_state() -> None:
    for key in ("job_dir", "source", "final_path", "merged_path", "download_log",
                "last_raw", "bgm_path", "bgm_hash"):
        st.session_state[key] = ""
    for key in ("frames", "transcript", "shots", "logs", "refs"):
        st.session_state[key] = []
    for key in ("keyframe_urls", "clips", "meta"):
        st.session_state[key] = {}


def start_draft() -> None:
    reset_business_state()
    st.session_state.active_job = ""
    set_preview("素材")
    st.session_state.nonce += 1
    st.session_state.task_token = f"draft_{st.session_state.nonce}"


def bump_token() -> None:
    st.session_state.nonce += 1
    base = st.session_state.active_job or "draft"
    st.session_state.task_token = f"{base}_{st.session_state.nonce}"


def bump_shots() -> None:
    st.session_state.shots_nonce += 1


def current_dir() -> Path | None:
    raw = st.session_state.get("job_dir")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def ensure_job_dir() -> Path:
    path = current_dir()
    if path is None:
        path = jobs.make_job_dir(output_root())
        st.session_state.job_dir = str(path)
    if st.session_state.get("active_job") != path.name:
        st.session_state.active_job = path.name
        st.session_state.nonce += 1
        st.session_state.task_token = f"{path.name}_{st.session_state.nonce}"
    return path


def usable_source() -> Path | None:
    raw = st.session_state.get("source")
    if raw and Path(raw).is_file():
        return Path(raw)
    return None


def task_name() -> str:
    return st.session_state.meta.get("name") or (
        Path(st.session_state.job_dir).name if st.session_state.job_dir else "未开始"
    )


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    return cleaned[:60] or "任务"


def flash(kind: str, message: str) -> None:
    st.session_state["_flash"] = (kind, message)


def set_preview(section: str) -> None:
    st.session_state["_preview_target"] = section


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if not item:
        return
    kind, message = item
    {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(message)


def add_log(message: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    logs = st.session_state.logs
    logs.append(line)
    if len(logs) > 500:
        st.session_state.logs = logs[-500:]
    path = current_dir()
    if path is not None:
        jobs.append_log(path, line)


def client() -> Api88:
    return Api88(
        st.session_state.get("cfg_api_key", cfg["api_key"]),
        st.session_state.get("cfg_base_url", cfg["base_url"]),
    )


def model_label(model: str) -> str:
    lower = (model or "").lower()
    if "seedream" in lower:
        return f"豆包 Seedream · {model}"
    if "seedance" in lower:
        return f"豆包 Seedance · {model}"
    if "kling" in lower:
        return f"可灵 · {model}"
    if "wan" in lower:
        return f"通义万相 · {model}"
    if "veo" in lower:
        return f"Veo · {model}"
    if "gemini" in lower:
        return f"Gemini · {model}"
    return model or "未选择"


def model_picker(label: str, key: str, default: str, kind: str) -> str | None:
    options = pick_models(st.session_state.models, kind)
    current = st.session_state.get(key, default)
    if current in MODEL_SENTINELS:
        current = ""
    if not current and options:
        current = options[0]
    choices = sorted({*options, *([current] if current else [])})
    if choices:
        if st.session_state.get(key) not in choices:
            st.session_state[key] = current or choices[0]
        value = st.selectbox(
            label, choices, key=key, placeholder="请选择模型",
            format_func=model_label, accept_new_options=True, width="stretch",
        )
        return (value or "").strip() or None
    st.session_state.pop(key, None)
    value = st.selectbox(
        label, [NO_MODEL_OPTION], key=key, index=None,
        placeholder="先拉取模型，或直接输入模型 ID",
        accept_new_options=True, width="stretch",
    )
    value = (value or "").strip()
    return None if value in MODEL_SENTINELS else (value or None)


@st.cache_data(show_spinner=False)
def probe_cached(path: str, mtime: float) -> dict:
    return analyze.probe(Path(path))


def safe_probe(path: Path) -> dict | None:
    try:
        return probe_cached(str(path), path.stat().st_mtime)
    except Exception:
        return None


def rel_to_job(path: Path) -> str:
    folder = current_dir()
    try:
        return Path(path).resolve().relative_to(folder.resolve()).as_posix()
    except (AttributeError, ValueError):
        return ""


def auto_name_from_source(path: Path) -> None:
    folder = current_dir()
    if folder is None:
        return
    old = jobs.read_meta(folder)
    if old.get("name"):
        return
    stem = path.stem.strip()
    if not stem or stem.lower() in {"下载", "video", "未命名", "output", "final"}:
        return
    jobs.write_meta(folder, name=stem[:60])


def save_shots(shots: list[dict], mode: str, anchor: str, user_req: str) -> None:
    folder = ensure_job_dir()
    (folder / "shots.json").write_text(
        json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        label = recreate.load_mode(mode).get("label", "") or MODE_LABELS.get(mode, mode)
    except Exception:
        label = MODE_LABELS.get(mode, mode)
    (folder / "script.md").write_text(
        recreate.shots_to_script(shots, label), encoding="utf-8"
    )
    jobs.write_meta(folder, mode=mode, anchor=anchor, user_req=user_req)
    st.session_state.meta = jobs.read_meta(folder)


def commit_shots(rows: list[dict], mode: str, anchor: str, user_req: str) -> None:
    rows = jobs.normalize_shots(rows)
    st.session_state.shots = rows
    save_shots(rows, mode, anchor, user_req)
    bump_shots()
    flash("success", "分镜表已保存")
    st.rerun()


def open_folder(path: Path) -> None:
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))
        else:
            st.warning("当前系统不支持自动打开文件夹，请手动打开：" + str(path))
    except Exception as exc:
        st.error(f"打开文件夹失败：{exc}")


def stamp(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def shots_to_srt(shots: list[dict]) -> str:
    lines, clock = [], 0.0
    for shot in shots:
        text = (shot.get("dialogue") or "").strip()
        dur = float(shot.get("duration") or 3)
        if text:
            lines += [str(len(lines) // 4 + 1), f"{stamp(clock)} --> {stamp(clock + dur)}", text, ""]
        clock += dur
    return "\n".join(lines)


def table_changed(edited: pd.DataFrame, shots: list[dict]) -> bool:
    left = (
        edited.reindex(columns=EDITOR_COLS).fillna("").astype(str)
        .reset_index(drop=True)
    )
    right = (
        pd.DataFrame(shots).reindex(columns=EDITOR_COLS).fillna("").astype(str)
        .reset_index(drop=True)
    )
    return len(left) != len(right) or not left.equals(right)


def merge_edited(edited: pd.DataFrame, shots: list[dict]) -> list[dict]:
    try:
        records = edited.to_dict("records")
    except Exception:
        records = shots
    rows = []
    for i, record in enumerate(records):
        base = dict(shots[i]) if i < len(shots) else {}
        for col in EDITOR_COLS:
            base[col] = record.get(col, base.get(col, ""))
        rows.append(base)
    return jobs.normalize_shots(rows)


def new_blank_shot(rows: list[dict]) -> dict:
    last_end = max((float(shot.get("end") or 0) for shot in rows), default=0.0)
    return {
        "id": f"shot_{len(rows) + 1:02d}",
        "start": last_end,
        "end": last_end + 3,
        "duration": 3,
        "semantic_function": "",
        "description": "",
        "prompt": "",
        "dialogue": "",
        "camera": "",
        "reference": "",
    }


def strip_alias(text: str, alias: str) -> str:
    cleaned = re.sub(rf"@{re.escape(alias)}(?![\w-])", "", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def ref_data_url(path: Path) -> str:
    data = Path(path).read_bytes()
    if len(data) > MAX_REF_BYTES:
        raise ValueError(f"参考图超过 {MAX_REF_BYTES // (1024 * 1024)}MB")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def shot_ref_payload(shot: dict) -> tuple[list[dict], list[str], list[str]]:
    """按分镜里的 @别名取参考图，返回（有效参考图、data URL、警告）。"""
    matched = recreate.match_shot_refs(shot, st.session_state.refs)
    used, urls, warnings, total = [], [], [], 0
    for ref in matched:
        try:
            size = Path(ref["abs_path"]).stat().st_size
            if total + size > MAX_REF_TOTAL:
                raise ValueError("参考图总大小超过 12MB")
            url = ref_data_url(Path(ref["abs_path"]))
        except Exception as exc:
            warnings.append(f"@{ref['alias']} 跳过：{exc}")
            continue
        total += size
        used.append(ref)
        urls.append(url)
    return used, urls, warnings


def _clean_model(value: str | None) -> str:
    return "" if value in MODEL_SENTINELS else (value or "").strip()


def collect_config() -> dict:
    return {
        "api_key": st.session_state.get("cfg_api_key", cfg["api_key"]),
        "base_url": st.session_state.get("cfg_base_url", cfg["base_url"]),
        "analysis_model": _clean_model(
            st.session_state.get("cfg_analysis_model", cfg["analysis_model"])
        ),
        "image_model": _clean_model(
            st.session_state.get("cfg_image_model", cfg["image_model"])
        ),
        "video_model": _clean_model(
            st.session_state.get("cfg_video_model", cfg["video_model"])
        ),
        "output_dir": st.session_state.get("cfg_output_dir", cfg["output_dir"]),
        "git_bash": st.session_state.get("cfg_git_bash", cfg["git_bash"]),
        "ffmpeg_dir": st.session_state.get("cfg_ffmpeg_dir", cfg["ffmpeg_dir"]),
        "whisper_model": st.session_state.get("cfg_whisper", cfg["whisper_model"]),
        "video_ratio": st.session_state.get("cfg_ratio", cfg["video_ratio"]),
        "video_resolution": st.session_state.get(
            "cfg_resolution", cfg.get("video_resolution", "1080p")
        ),
        "video_generation_resolution": st.session_state.get(
            "cfg_video_resolution", cfg.get("video_generation_resolution", "720p")
        ),
        "video_duration": st.session_state.get("cfg_duration", cfg["video_duration"]),
    }


LAYOUT_HTML = """
<style>
  [data-testid="stMainBlockContainer"] {
    padding-left: clamp(var(--size-3, 1rem), 2vw, var(--size-7, 2rem)) !important;
    padding-right: clamp(var(--size-3, 1rem), 2vw, var(--size-7, 2rem)) !important;
  }
  .st-key-app_header h2 {
    font-size: clamp(1.5rem, 2.4vw, 2rem) !important;
    line-height: 1.2 !important;
    margin-bottom: var(--size-1, .25rem) !important;
  }
  .st-key-app_header {
    margin-bottom: var(--size-2, .5rem);
  }

  /* 分节卡片 */
  .st-key-sec_material,
  .st-key-sec_recreate,
  .st-key-sec_compose {
    border-radius: var(--radius-3, 1rem);
    box-shadow: var(--shadow-1, 0 1px 2px rgba(0, 0, 0, .06));
  }

  /* 任务行：名称统一左对齐，避免主/次按钮文字位置跳动 */
  [class*="st-key-open_"] button > div {
    justify-content: flex-start !important;
  }
  [class*="st-key-open_"] button p {
    width: 100%;
    text-align: left !important;
  }
  [data-testid="stSidebar"] hr {
    opacity: .55;
  }
  [data-testid="stSidebarCollapseButton"] {
    visibility: visible !important;
  }

  /* 设置固定在左栏底部 */
  [data-testid="stSidebarUserContent"] > div {
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 5rem);
  }
  [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: calc(100vh - 5rem);
  }
  .st-key-sidebar_footer {
    flex: 0 0 auto !important;
    padding-top: .35rem;
    background: #EEF1F4;
    position: sticky;
    bottom: 0;
    z-index: 2;
  }
  [data-testid="stSidebarUserContent"]
    [data-testid="stLayoutWrapper"]:has(.st-key-sidebar_footer) {
    margin-top: auto !important;
  }

  /* 上传器中文化 */
  [data-testid="stFileUploaderDropzone"] button p {
    font-size: 0 !important;
  }
  [data-testid="stFileUploaderDropzone"] button p::after {
    content: "上传文件";
    font-size: .875rem;
  }
  [data-testid="stFileUploaderDropzoneInstructions"] {
    display: none !important;
  }

  /* 分镜表编辑器顶部英文工具栏 */
  [class*="st-key-shots_editor"] [data-testid="stElementToolbar"] {
    display: none !important;
  }

  /* 脚本汇总：压缩 Markdown 标题，避免在卡片里过大 */
  .st-key-script_summary h1 {
    font-size: 1.2rem !important;
    margin: .1rem 0 .6rem !important;
  }
  .st-key-script_summary h2 {
    font-size: 1rem !important;
    margin: .7rem 0 .3rem !important;
  }
  .st-key-script_summary p,
  .st-key-script_summary li {
    font-size: .875rem;
  }

  /* 宽屏：右栏固定宽度并跟随滚动 */
  @media (min-width: 1201px) {
    div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane) {
      align-items: flex-start;
    }
    div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane)
      > div[data-testid="stColumn"]:has(.st-key-preview_pane) {
      flex: 0 0 clamp(320px, 28vw, 400px) !important;
      width: clamp(320px, 28vw, 400px) !important;
      min-width: 320px !important;
      max-width: 400px !important;
      position: sticky;
      top: 4rem;
      align-self: flex-start;
      max-height: calc(100vh - 5rem);
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
      padding-bottom: var(--size-3, 1rem);
    }
    div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane)
      > div[data-testid="stColumn"]:not(:has(.st-key-preview_pane)) {
      flex: 1 1 0 !important;
      min-width: 0 !important;
    }
  }

  .st-key-preview_pane [data-testid="stVideo"] {
    max-width: 420px;
    margin-inline: auto;
  }

  /* 中等屏：工作区在上、预览在下 */
  @media (max-width: 1200px) {
    div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane) {
      flex-wrap: wrap !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane)
      > div[data-testid="stColumn"] {
      flex: 1 1 100% !important;
      width: 100% !important;
      min-width: 100% !important;
    }
  }

  /* 手机 */
  @media (max-width: 768px) {
    [data-testid="stMainBlockContainer"] {
      padding-top: 3.5rem !important;
    }
    .st-key-app_header h2 {
      font-size: 1.45rem !important;
    }
    .st-key-preview_pane [data-testid="stVideo"] {
      max-width: 100%;
    }
  }
</style>
"""


@st.cache_data(show_spinner=False)
def open_props_css() -> str:
    path = Path(__file__).resolve().parent / "assets" / "open-props.min.css"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def inject_layout() -> None:
    st.html(f"<style>{open_props_css()}</style>" + LAYOUT_HTML)
    st.html(
        """
        <script>
        (() => {
          document.documentElement.lang = "zh-CN";
          document.documentElement.setAttribute("translate", "no");
          if (!document.querySelector('meta[name="google"]')) {
            const meta = document.createElement("meta");
            meta.name = "google";
            meta.content = "notranslate";
            document.head.appendChild(meta);
          }
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


# ================= 任务切换 / 历史操作 =================
def new_task_cb() -> None:
    start_draft()
    add_log("新建任务")


def open_job_cb(jid: str) -> None:
    try:
        state = jobs.load_job_state(output_root() / jid)
    except Exception as exc:
        flash("error", f"打开任务失败：{exc}")
        return
    reset_business_state()
    for key, value in state.items():
        st.session_state[key] = value
    st.session_state.job_dir = str(output_root() / jid)
    st.session_state.active_job = jid
    st.session_state.nonce += 1
    st.session_state.task_token = f"{jid}_{st.session_state.nonce}"
    add_log(f"打开任务：{state['meta'].get('name') or jid}")


def sync_search() -> None:
    key = f"task_search_{st.session_state.search_nonce}"
    st.session_state.task_search = st.session_state.get(key, "")


def clear_search_cb() -> None:
    st.session_state.task_search = ""
    st.session_state.search_nonce += 1
    st.session_state.search_open = False


@st.dialog("重命名任务")
def rename_dialog(jid: str) -> None:
    folder = output_root() / jid
    old = jobs.read_meta(folder)
    with st.form(f"rename_form_{jid}"):
        name = st.text_input("任务名称", value=old.get("name") or jid, max_chars=80)
        submitted = st.form_submit_button("保存", type="primary")
    if submitted:
        name = name.strip()
        if not name:
            st.error("名称不能为空")
            return
        jobs.rename_job(folder, name)
        if st.session_state.active_job == jid:
            st.session_state.meta = jobs.read_meta(folder)
        flash("success", f"已重命名为 {name}")
        st.rerun()


@st.dialog("删除任务")
def delete_dialog(jid: str) -> None:
    folder = output_root() / jid
    old = jobs.read_meta(folder)
    title = old.get("name") or jid
    st.warning(f"将永久删除任务「{title}」及全部文件，无法恢复。")
    c1, c2 = st.columns(2)
    if c1.button("确认删除", type="primary", key=f"dialog_delete_ok_{jid}", width="stretch"):
        is_current = st.session_state.active_job == jid
        try:
            jobs.delete_job(output_root(), folder)
        except Exception as exc:
            st.error(f"删除失败：{exc}")
            return
        if is_current:
            start_draft()
        flash("success", f"已删除任务「{title}」")
        st.rerun()
    if c2.button("取消", key=f"dialog_delete_cancel_{jid}", width="stretch"):
        st.rerun()


@st.dialog("设置", width="medium", on_dismiss="rerun")
def settings_dialog() -> None:
    st.text_input("API 密钥", type="password", key="cfg_api_key")
    st.text_input("接口地址", key="cfg_base_url", placeholder="https://api.88api.ai")

    if st.button("测试连接 / 拉取模型", width="stretch", key="btn_test_conn"):
        try:
            models = client().list_models()
            st.session_state.models = models
            st.success(
                f"连接成功，可用模型 {len(models)} 个。关闭设置后，"
                "在拆解步骤重新选择模型即可。"
            )
        except Exception as exc:
            st.error(str(exc))

    with st.expander("高级设置"):
        st.text_input("命令行工具路径（Git Bash）", key="cfg_git_bash")
        st.text_input("视频处理工具目录（FFmpeg）", key="cfg_ffmpeg_dir")
        st.text_input(
            "任务输出目录", key="cfg_output_dir",
            help="修改后新任务保存到新目录；当前任务仍指向原目录。",
        )

    c1, c2 = st.columns(2)
    if c1.button("保存配置", type="primary", width="stretch", key="btn_save_config"):
        config.save_config(collect_config())
        flash("success", "已保存到 config.json")
        st.rerun()
    if c2.button("重新运行", icon=":material/refresh:", width="stretch", key="btn_rerun"):
        st.rerun()

    with st.expander("关于"):
        st.markdown(
            "视频复刻工作台 · 本地运行\n\n"
            "流程：获取素材 → 拆解复刻 → 合成去重。\n\n"
            "API 密钥和路径配置只保存在本机 `config.json`。"
        )


# ================= 侧栏：任务管理 =================
pending_dialog = None

with st.sidebar:
    st.button(
        "新建任务", type="primary", icon=":material/add:", width="stretch",
        on_click=new_task_cb, key="btn_new_task",
    )

    search_col, icon_col = st.columns([5, 1], vertical_alignment="center")
    search_col.caption("任务")
    with icon_col:
        with st.popover(
            "", icon=":material/search:", help="搜索任务", key="search_open",
            on_change="rerun", width="content",
        ):
            search_key = f"task_search_{st.session_state.search_nonce}"
            if search_key not in st.session_state:
                st.session_state[search_key] = st.session_state.get("task_search", "")
            st.text_input(
                "搜索任务", key=search_key, type="search",
                placeholder="任务名 / 日期 / 文件名",
                label_visibility="collapsed", on_change=sync_search,
            )
            st.button(
                "清除并关闭", icon=":material/close:", width="stretch",
                on_click=clear_search_cb, key="btn_clear_search",
            )

    query = (st.session_state.get("task_search") or "").strip()
    folded = query.casefold()
    all_jobs = jobs.list_jobs(output_root())
    shown = [
        job for job in all_jobs
        if not folded
        or folded in job["name"].casefold()
        or folded in job["id"].casefold()
        or folded in job["source_name"].casefold()
    ]
    if query:
        st.caption(f"筛选中：{query}")

    with st.container(height=460, border=True):
        if not all_jobs:
            st.caption("还没有历史任务。下载或上传素材后会自动出现在这里。")
        elif not shown:
            st.caption("没有匹配的任务。")
        else:
            for job in shown:
                active = job["id"] == st.session_state.active_job
                c_open, c_menu = st.columns(
                    [4.5, 1.3], gap="xsmall", vertical_alignment="center"
                )
                c_open.button(
                    job["name"],
                    key=f"open_{job['id']}",
                    type="primary" if active else "tertiary",
                    width="stretch",
                    on_click=open_job_cb,
                    args=(job["id"],),
                    help=job["name"],
                )
                choice = c_menu.menu_button(
                    "", JOB_MENU, icon=":material/more_horiz:",
                    key=f"menu_{job['id']}", type="tertiary",
                    width="stretch", help="更多操作",
                )
                st.caption(f"{job['time']} · {job['status']}")
                if choice == "重命名":
                    pending_dialog = ("rename", job["id"])
                elif choice == "删除":
                    pending_dialog = ("delete", job["id"])
                elif choice == "打开文件夹":
                    open_folder(output_root() / job["id"])

    with st.container(key="sidebar_footer"):
        st.divider()
        if st.button(
            "设置", icon=":material/settings:", width="stretch", key="btn_settings"
        ):
            pending_dialog = ("settings", "")

if pending_dialog:
    action, jid = pending_dialog
    if action == "rename":
        rename_dialog(jid)
    elif action == "delete":
        delete_dialog(jid)
    elif action == "settings":
        settings_dialog()


# ================= 右侧预览 =================
def render_material(folder: Path | None) -> None:
    source = usable_source()
    if not source:
        st.caption("暂无素材。先在左侧完成下载或上传。")
        return
    info = safe_probe(source)
    if info:
        st.caption(
            f"时长 {info['duration']:.1f}s · "
            f"{info['width']}×{info['height']} · "
            f"音轨：{'有' if info['has_audio'] else '无'}"
        )
    st.video(str(source))
    st.caption(source.name)


def render_shot_preview(folder: Path | None) -> None:
    shots = st.session_state.shots
    frames = st.session_state.frames
    if not shots and not frames:
        st.caption("暂无分镜。先在中间完成拆解。")
        return
    if shots:
        total = sum(float(shot.get("duration") or 0) for shot in shots)
        st.caption(
            f"{len(shots)} 个镜头 · 约 {total:.1f}s · "
            f"已生成 {len(st.session_state.clips)} 段视频"
        )
    elif frames:
        st.caption(f"已抽帧 {len(frames)} 张，等待拆解生成分镜。")
    keyframes = st.session_state.keyframe_urls
    items = []
    for shot in shots:
        sid = shot.get("id", "")
        local = folder / "keys" / f"{sid}.png" if folder else None
        if local is not None and local.exists():
            items.append((sid, str(local)))
        elif keyframes.get(sid):
            items.append((sid, keyframes[sid]))
    if items:
        cols = st.columns(2)
        for i, (sid, src) in enumerate(items):
            with cols[i % 2]:
                st.image(src, caption=sid, width="stretch", alt=f"关键帧 {sid}")
    if frames:
        with st.expander(f"抽帧预览（{len(frames)}）"):
            cols = st.columns(2)
            for i, (t, img) in enumerate(frames):
                with cols[i % 2]:
                    st.image(img, caption=f"{t:.1f}s", width="stretch", alt=f"抽帧 {t:.1f} 秒")
    if not items and not frames:
        st.caption("暂无关键帧。")


def render_final() -> None:
    final = st.session_state.final_path
    if final and Path(final).is_file():
        st.video(final)
        st.download_button(
            "下载成片", data=lambda: Path(final).read_bytes(),
            file_name=f"{safe_filename(task_name())}_成片.mp4",
            mime="video/mp4", width="stretch", key=f"dl_final_{token}",
        )
        st.caption(Path(final).name)
    else:
        st.caption("暂无成片。先在合成去重步骤生成。")


def render_logs() -> None:
    logs = st.session_state.logs
    if logs:
        st.code("\n".join(reversed(logs[-120:])), language=None, height=240, wrap_lines=True)
    else:
        st.caption("暂无日志。")


def render_preview() -> None:
    folder = current_dir()
    st.subheader("预览", icon=":material/preview:")
    status_label, status_color = jobs.job_status(folder) if folder else ("未开始", "gray")
    st.badge(status_label, color=status_color)
    target = st.session_state.pop("_preview_target", None)
    if target in PREVIEW_OPTIONS:
        st.session_state.preview_section = target
    section = st.segmented_control(
        "预览内容", PREVIEW_OPTIONS, required=True,
        key="preview_section", label_visibility="collapsed",
        width="stretch", wrap=True,
    )
    if section == "素材":
        render_material(folder)
    elif section == "分镜 / 关键帧":
        render_shot_preview(folder)
    elif section == "成片":
        render_final()
    else:
        render_logs()


# ================= 主界面 =================
inject_layout()

with st.container(key="app_header"):
    st.header("视频复刻工作台")
    st.caption(f"当前任务：{task_name()} · 获取素材 → 拆解复刻 → 合成去重")

show_flash()

center, right = st.columns([2, 1.3], gap="large")

with center:
    # ---------- 01 获取素材 ----------
    with st.container(border=True, key="sec_material"):
        st.subheader("01 获取素材", icon=":material/download:")
        st.session_state.setdefault("material_method", "链接下载")
        material_method = st.segmented_control(
            "获取方式", ["链接下载", "本地上传"], required=True,
            key="material_method", width="stretch",
        ) or "链接下载"

        if material_method == "链接下载":
            with st.container(horizontal=True, vertical_alignment="bottom", wrap=False):
                url = st.text_input(
                    "粘贴视频链接", key=tkey("url"),
                    placeholder="抖音 / 快手 / B站 / 小红书 / 视频链接",
                    label_visibility="collapsed",
                )
                download_clicked = st.button(
                    "下载", type="primary", icon=":material/download:",
                    key=tkey("download"),
                )
            st.caption("默认按原画下载。")
            if download_clicked:
                if not url.strip():
                    st.warning("先粘贴视频链接")
                else:
                    try:
                        add_log(f"开始下载：{url.strip()}")
                        folder = ensure_job_dir()
                        with st.spinner("下载中，抖音 / 快手会走分享页解析..."):
                            path, log = downloader.download(
                                url.strip(), folder / "source", "quality"
                            )
                        st.session_state.source = str(path)
                        st.session_state.download_log = log
                        jobs.write_meta(
                            folder, source_rel=rel_to_job(path), source_url=url.strip()
                        )
                        auto_name_from_source(path)
                        st.session_state.meta = jobs.read_meta(folder)
                        set_preview("素材")
                        add_log(f"下载完成：{path.name}")
                        flash("success", f"已下载：{path.name}")
                        st.rerun()
                    except Exception as exc:
                        add_log(f"下载失败：{exc}")
                        st.error(str(exc))
        else:
            uploaded = st.file_uploader(
                "上传本地视频", type=["mp4", "mov", "mkv", "webm"],
                key=f"{tkey('upload')}_{st.session_state.upload_nonce}",
            )
            if uploaded and st.button("保存上传文件", key=tkey("save_upload")):
                folder = ensure_job_dir()
                name = Path(uploaded.name).name or "upload.mp4"
                dest = folder / "source" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(uploaded.getbuffer())
                st.session_state.source = str(dest)
                jobs.write_meta(folder, source_rel=rel_to_job(dest))
                auto_name_from_source(dest)
                st.session_state.meta = jobs.read_meta(folder)
                st.session_state.upload_nonce += 1
                set_preview("素材")
                add_log(f"上传素材：{dest.name}")
                flash("success", f"已保存：{dest.name}")
                st.rerun()
            st.caption("支持 MP4 / MOV / MKV / WEBM，单个文件不超过 200MB。")

        source = usable_source()
        if source:
            st.caption(f"当前素材：{source.name}（预览见右侧）")

        if st.session_state.download_log:
            with st.expander("下载日志"):
                st.code(st.session_state.download_log)

    # ---------- 02 拆解复刻 ----------
    with st.container(border=True, key="sec_recreate"):
        st.subheader("02 拆解复刻", icon=":material/auto_awesome:")

        st.markdown("**复刻设定**")
        mode = st.segmented_control(
            "二创档位", MODES, required=True, key=tkey("cfg_mode"),
            format_func=lambda m: MODE_LABELS.get(m, m),
            width="stretch", wrap=True,
        ) or "conservative"
        c1, c2 = st.columns(2, gap="medium")
        anchor = c1.text_area(
            "全片统一设定", key=tkey("cfg_anchor"), height=110,
            placeholder="人物 / 产品 / 风格锚点\n例如：25 岁女性，黑色长发，白色衬衫；产品是白色保温杯",
            help="复刻原片的节奏和结构，但把人物、产品或画风固定成你写的样子；"
                 "会写进每个镜头，拆解和生视频都会带上。留空表示完全照原片。",
        )
        user_req = c2.text_area(
            "额外要求（可选）", key=tkey("cfg_user_req"), height=110,
            placeholder="例如：换成我的产品，保留原片节奏和卖点",
            help="在复刻基础上额外要改的地方，比如换卖点、改口播、调整镜头顺序；"
                 "只在拆解生成分镜时使用。留空表示只按原片复刻。",
        )
        st.caption(
            "原片内容来自抽帧和口播；这两项只描述你要「固定」或「改成」的部分。"
        )

        ref_files = st.file_uploader(
            "参考图（人物 / 产品 / 风格）", type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"{tkey('refs_upload')}_{st.session_state.ref_nonce}",
        )
        if ref_files:
            folder = ensure_job_dir()
            payload, too_big = [], []
            total = 0
            for item in ref_files:
                data = item.getvalue()
                if len(data) > MAX_REF_BYTES or total + len(data) > MAX_REF_TOTAL:
                    too_big.append(item.name)
                    continue
                total += len(data)
                payload.append((item.name, data))
            refs, added, skipped = jobs.add_ref_files(folder, payload)
            st.session_state.refs = refs
            st.session_state.ref_nonce += 1
            parts = []
            if added:
                parts.append("已添加：" + "、".join(f"@{alias}" for alias in added))
            if skipped:
                parts.append("跳过：" + "、".join(skipped))
            if too_big:
                parts.append("超出大小限制：" + "、".join(too_big))
            if added:
                add_log("添加参考图：" + "、".join(f"@{alias}" for alias in added))
                flash("success", "；".join(parts))
            else:
                flash("warning", "；".join(parts) or "没有可用参考图")
            st.rerun()

        refs = st.session_state.refs
        if refs:
            shot_alias_sets = [
                set(recreate.shot_aliases(shot)) for shot in st.session_state.shots
            ]
            cols = st.columns(3)
            for i, ref in enumerate(refs):
                used_count = sum(
                    1 for aliases in shot_alias_sets if ref["alias"] in aliases
                )
                with cols[i % 3]:
                    st.image(
                        ref["abs_path"],
                        caption=(
                            f"@{ref['alias']} · 已用于 {used_count} 个镜头"
                            if used_count else
                            f"@{ref['alias']} · 暂未被镜头引用"
                        ),
                        width="stretch", alt=f"参考图 {ref['alias']}",
                    )
                    if st.button(
                        "删除", key=f"del_ref_{token}_{ref['alias']}",
                        width="stretch", icon=":material/delete:",
                    ):
                        folder = current_dir()
                        if folder is not None:
                            jobs.remove_ref(folder, ref["alias"])
                            changed = False
                            for shot in st.session_state.shots:
                                new_ref = strip_alias(shot.get("reference", ""), ref["alias"])
                                if new_ref != shot.get("reference", ""):
                                    shot["reference"] = new_ref
                                    changed = True
                            if changed:
                                save_shots(
                                    st.session_state.shots, mode, anchor, user_req
                                )
                                bump_shots()
                            st.session_state.refs = jobs.read_refs(folder)
                            add_log(f"删除参考图：@{ref['alias']}")
                            flash("success", f"已删除参考图 @{ref['alias']}")
                            st.rerun()
        else:
            st.caption(
                "可上传人物、产品或风格参考图；文件名就是别名，"
                "如 女主.png → @女主。"
            )
        if refs:
            st.caption(
                "用法：点「拆解并生成脚本」时 AI 会先看参考图，并把 @别名 自动填进"
                "分镜表「参考附件」列；也可以手动填写。生视频时会提交这些参考图。"
            )

        if st.button("保存设定", key=tkey("save_meta")):
            folder = current_dir()
            if folder is None:
                st.warning("任务还没有创建，先下载或上传素材")
            else:
                jobs.write_meta(folder, mode=mode, anchor=anchor, user_req=user_req)
                st.session_state.meta = jobs.read_meta(folder)
                flash("success", "设定已保存")
                st.rerun()

        source = usable_source()
        if source:
            st.divider()
            st.markdown("**抽帧与转写**")
            frame_count = st.slider(
                "分析帧数", 4, 12, 8, key=tkey("frame_count"),
                help="均匀抽帧，帧越多拆解越细，模型调用也越贵",
            )
            c1, c2, c3 = st.columns(
                [1.05, 1.15, 0.75], vertical_alignment="bottom"
            )
            with c1:
                st.selectbox(
                    "语音识别模型", ["small", "base", "medium"], key="cfg_whisper",
                    format_func=lambda value: WHISPER_LABELS.get(value, value),
                    help="仅勾选口播转写时使用。",
                )
            with c2:
                do_transcribe = st.checkbox(
                    "同时提取口播文案（较慢）", value=False,
                    key=tkey("do_transcribe"),
                )
            with c3:
                extract_clicked = st.button(
                    "抽帧分析", type="primary", icon=":material/photo_library:",
                    key=tkey("extract"), width="stretch",
                )
            if extract_clicked:
                try:
                    folder = ensure_job_dir()
                    with st.spinner("抽帧中..."):
                        frames = analyze.extract_frames(source, folder / "frames", frame_count)
                    st.session_state.frames = [(t, str(p)) for t, p in frames]
                    add_log(f"抽帧完成，共 {len(frames)} 帧")
                    transcribe_done = 0
                    transcribe_error = ""
                    if do_transcribe:
                        try:
                            with st.spinner("语音转写中，首次会下载语音模型..."):
                                transcript = analyze.transcribe(
                                    source, st.session_state.cfg_whisper
                                )
                            st.session_state.transcript = transcript
                            jobs.save_transcript(folder, transcript)
                            transcribe_done = len(transcript)
                            add_log(f"转写完成，{transcribe_done} 句")
                        except Exception as exc:
                            transcribe_error = str(exc)
                            add_log(f"转写失败：{exc}")
                    set_preview("分镜 / 关键帧")
                    message = f"抽帧完成，共 {len(frames)} 帧"
                    if transcribe_done:
                        message += f"；转写 {transcribe_done} 句"
                    if transcribe_error:
                        message += f"；转写失败：{transcribe_error}"
                    flash("warning" if transcribe_error else "success", message)
                    st.rerun()
                except Exception as exc:
                    add_log(f"抽帧失败：{exc}")
                    st.error(str(exc))
            st.caption("抽帧结果可在右侧「分镜 / 关键帧」查看。")
        else:
            st.caption("当前任务没有可用素材；已有分镜时仍可继续编辑和生成。")

        if st.session_state.transcript:
            with st.expander("口播转写"):
                st.write("\n".join(item["text"] for item in st.session_state.transcript))

        frames = st.session_state.frames
        if frames:
            st.divider()
            st.markdown("**拆解**")
            c1, c2 = st.columns([1.3, 1], vertical_alignment="bottom")
            with c1:
                analysis_model = model_picker(
                    "分析模型（视觉 / 文本）", "cfg_analysis_model",
                    cfg["analysis_model"], "analysis",
                )
            with c2:
                analyze_clicked = st.button(
                    "拆解并生成脚本", type="primary", icon=":material/auto_awesome:",
                    key=tkey("analyze"), width="stretch",
                )
            if analyze_clicked:
                if not analysis_model:
                    st.error("先选择分析模型；没有模型列表时，请在设置里拉取模型")
                else:
                    try:
                        with st.spinner("视觉模型拆解中..."):
                            shots, raw = recreate.analyze_shots(
                                client(), analysis_model,
                                [(t, Path(p)) for t, p in frames],
                                mode, user_req, st.session_state.transcript,
                                refs=st.session_state.refs, anchor=anchor,
                            )
                        shots = jobs.normalize_shots(shots)
                        st.session_state.shots = shots
                        st.session_state.last_raw = raw
                        save_shots(shots, mode, anchor, user_req)
                        set_preview("分镜 / 关键帧")
                        add_log(f"拆解完成，共 {len(shots)} 个镜头")
                        flash("success", f"拆解完成，共 {len(shots)} 个镜头，已保存分镜表和脚本")
                        bump_token()
                        st.rerun()
                    except Exception as exc:
                        add_log(f"拆解失败：{exc}")
                        st.error(str(exc))

        if st.session_state.last_raw:
            with st.expander("模型原始输出"):
                st.code(st.session_state.last_raw)

        shots = st.session_state.shots
        if shots:
            st.divider()
            st.markdown("**分镜表**")
            table_rows = [
                {col: shot.get(col, "") for col in EDITOR_COLS} for shot in shots
            ]
            edited = st.data_editor(
                pd.DataFrame(table_rows, columns=EDITOR_COLS),
                key=f"shots_editor_{token}_{st.session_state.shots_nonce}",
                hide_index=True, num_rows="fixed", disabled=["id", "duration"],
                column_config=EDITOR_CONFIG, column_order=EDITOR_COLS,
                row_height=35, width="stretch",
                placeholder="", alt="分镜表",
            )
            if table_changed(edited, shots):
                st.caption("有未保存修改；下面的脚本汇总会同步显示当前表格内容。")

            c1, c2, c3 = st.columns([1, 1, 1])
            if c1.button(
                "保存修改", icon=":material/save:", width="stretch",
                key=f"save_table_{token}_{st.session_state.shots_nonce}",
            ):
                commit_shots(merge_edited(edited, shots), mode, anchor, user_req)
            if c2.button(
                "添加镜头", icon=":material/add:", width="stretch",
                key=f"add_shot_{token}_{st.session_state.shots_nonce}",
            ):
                rows = merge_edited(edited, shots)
                rows.append(new_blank_shot(rows))
                commit_shots(rows, mode, anchor, user_req)
            with c3.popover(
                "删除镜头", icon=":material/delete:", width="stretch",
            ):
                shot_ids = [shot["id"] for shot in shots]
                sid = st.selectbox(
                    "选择镜头", shot_ids,
                    key=f"del_sid_{token}_{st.session_state.shots_nonce}",
                )
                if st.button(
                    "确认删除", type="primary", width="stretch",
                    key=f"del_shot_{token}_{st.session_state.shots_nonce}",
                ):
                    rows = [
                        shot for shot in merge_edited(edited, shots)
                        if shot["id"] != sid
                    ]
                    if not rows:
                        flash("warning", "至少保留一个镜头")
                        st.rerun()
                    commit_shots(rows, mode, anchor, user_req)

            aliases = "、".join(f"@{ref['alias']}" for ref in st.session_state.refs)
            st.caption("可用参考别名：" + (aliases or "（暂无参考图）"))

            preview_rows = merge_edited(edited, shots)
            script_text = recreate.shots_to_script(
                preview_rows, MODE_LABELS.get(mode, mode)
            )
            st.markdown("**脚本汇总**")
            with st.container(key="script_summary", height=320, border=True):
                st.markdown(script_text)
            st.download_button(
                "下载复刻脚本", script_text.encode("utf-8"),
                file_name=f"{safe_filename(task_name())}_复刻脚本.md",
                mime="text/markdown", width="stretch",
                key=f"dl_script_{token}_{st.session_state.shots_nonce}",
            )
            st.caption("点「保存修改」后会把当前表格写入 shots.json 和 script.md。")

            st.divider()
            st.markdown("**关键帧**")
            keyframes = st.session_state.keyframe_urls
            c1, c2, c3 = st.columns(
                [1.25, 0.75, 1.0], vertical_alignment="bottom"
            )
            with c1:
                image_model = model_picker(
                    "生图模型", "cfg_image_model", cfg["image_model"], "image"
                )
            with c2:
                st.selectbox(
                    "画面比例", RATIO_OPTIONS, key="cfg_ratio",
                    format_func=lambda value: RATIO_LABELS.get(value, value),
                )
            with c3:
                gen_keys = st.button(
                    "生成关键帧（未生成的镜头）", type="primary",
                    icon=":material/image:", key=tkey("gen_keys"),
                    width="stretch",
                )
            if gen_keys:
                if not image_model:
                    st.error("先选择生图模型")
                else:
                    api = client()
                    folder = ensure_job_dir()
                    progress = st.progress(0.0)
                    for i, shot in enumerate(shots):
                        sid = shot.get("id", f"shot_{i + 1:02d}")
                        if keyframes.get(sid):
                            progress.progress((i + 1) / len(shots))
                            continue
                        try:
                            url = api.generate_image(
                                image_model,
                                recreate.image_prompt(
                                    shot, anchor, st.session_state.cfg_ratio
                                ),
                                size="2K",
                            )
                            keyframes[sid] = url
                            api.download(url, folder / "keys" / f"{sid}.png")
                            jobs.save_keyframes(folder, keyframes)
                            add_log(f"关键帧 {sid} 生成完成")
                        except Exception as exc:
                            add_log(f"关键帧 {sid} 生成失败：{exc}")
                            st.error(f"{sid} 生成失败：{exc}")
                        progress.progress((i + 1) / len(shots))
                    st.session_state.keyframe_urls = keyframes
                    set_preview("分镜 / 关键帧")
                    flash("success", "关键帧生成完成")
                    st.rerun()

            if keyframes:
                with st.popover(
                    "单镜重新生成", icon=":material/refresh:", width="stretch",
                ):
                    shot_ids = [
                        shot.get("id", f"shot_{i + 1:02d}")
                        for i, shot in enumerate(shots)
                    ]
                    sid = st.selectbox(
                        "选择镜头", shot_ids,
                        key=f"regen_sid_{token}_{st.session_state.shots_nonce}",
                    )
                    if st.button(
                        "重新生成该镜头关键帧", width="stretch",
                        key=f"regen_btn_{token}_{st.session_state.shots_nonce}",
                    ):
                        if not image_model:
                            st.error("先选择生图模型")
                        else:
                            try:
                                with st.spinner(f"重生成 {sid}..."):
                                    folder = ensure_job_dir()
                                    shot = next(
                                        (item for item in shots if item.get("id") == sid),
                                        None,
                                    )
                                    url = client().generate_image(
                                        image_model,
                                        recreate.image_prompt(
                                            shot, anchor, st.session_state.cfg_ratio
                                        ),
                                        size="2K",
                                    )
                                    keyframes[sid] = url
                                    client().download(url, folder / "keys" / f"{sid}.png")
                                    jobs.save_keyframes(folder, keyframes)
                                st.session_state.keyframe_urls = keyframes
                                set_preview("分镜 / 关键帧")
                                add_log(f"关键帧 {sid} 重新生成完成")
                                flash("success", f"{sid} 已重新生成")
                                st.rerun()
                            except Exception as exc:
                                add_log(f"关键帧 {sid} 重新生成失败：{exc}")
                                st.error(str(exc))

            st.divider()
            st.markdown("**生成视频**")
            c1, c2, c3 = st.columns(
                [1.35, 0.85, 0.8], vertical_alignment="bottom"
            )
            with c1:
                video_model = model_picker(
                    "生视频模型", "cfg_video_model", cfg["video_model"], "video"
                )
            with c2:
                st.selectbox(
                    "生成分辨率", RESOLUTION_OPTIONS, key="cfg_video_resolution",
                    format_func=lambda value: RESOLUTION_LABELS.get(value, value),
                    help="分辨率越高越清晰，计费也越高；不同模型支持档位不同，"
                         "不支持时接口会直接报错。",
                )
            with c3:
                st.number_input(
                    "单镜时长（秒）", min_value=4, max_value=30,
                    key="cfg_duration",
                )
            ratio_label = RATIO_LABELS.get(
                st.session_state.cfg_ratio, st.session_state.cfg_ratio
            )
            st.caption(
                f"画面比例：{ratio_label}（在上方关键帧设置里修改）；"
                "生成分辨率按所选模型档位计费，fast / mini 通常只支持 480P / 720P。"
            )
            shot_ids = [shot.get("id", f"shot_{i + 1:02d}") for i, shot in enumerate(shots)]
            has_first = any(st.session_state.keyframe_urls.get(sid) for sid in shot_ids)
            use_first_key = tkey("use_first")
            if not has_first:
                st.session_state[use_first_key] = False
            use_first_frame = st.checkbox(
                "用关键帧作为首帧（推荐）", key=use_first_key,
                disabled=not has_first,
                help=None if has_first else "没有关键帧云端地址，将使用参考图或文生视频",
            )
            st.caption(
                "填写了 @参考图的镜头会优先用参考图生成，首帧设置对这些镜头不生效。"
            )
            c1, c2 = st.columns([1.65, 1], vertical_alignment="bottom")
            with c1:
                selected = st.multiselect(
                    "选择要生成的镜头", shot_ids, default=shot_ids,
                    key=f"selected_{token}_{st.session_state.shots_nonce}",
                    placeholder="请选择镜头",
                )
            with c2:
                gen_videos = st.button(
                    "生成选中镜头的视频", type="primary", icon=":material/movie:",
                    key=tkey("gen_videos"), width="stretch",
                )
            if gen_videos:
                if not video_model:
                    st.error("先选择生视频模型")
                elif not selected:
                    st.warning("至少勾选一个镜头")
                else:
                    api = client()
                    folder = ensure_job_dir()
                    clips_dir = folder / "clips"
                    clips_dir.mkdir(parents=True, exist_ok=True)
                    done, failed = 0, 0
                    for sid in selected:
                        shot = next(
                            (item for item in shots if item.get("id") == sid), None
                        )
                        if shot is None:
                            continue
                        used_refs, ref_urls, warnings = shot_ref_payload(shot)
                        for warning in warnings:
                            add_log(f"{sid} 参考图 {warning}")
                            st.warning(f"{sid} 参考图 {warning}")
                        aliases = [ref["alias"] for ref in used_refs]
                        fallback_first = (
                            st.session_state.keyframe_urls.get(sid)
                            if use_first_frame else None
                        )
                        fallback_first = fallback_first or None
                        first = None if ref_urls else fallback_first
                        prompt = recreate.video_prompt(shot, aliases, anchor)
                        duration = max(4, min(30, int(round(float(
                            shot.get("duration") or st.session_state.cfg_duration
                        )))))
                        resolution = st.session_state.cfg_video_resolution
                        add_log(
                            f"{sid} 提交生成（{resolution}）"
                            + ("；参考图" if ref_urls else "；首帧" if first else "；文生视频")
                        )
                        try:
                            with st.status(f"{sid} 提交生成...", expanded=False) as status:
                                try:
                                    vid = api.create_video(
                                        video_model, prompt, duration=duration,
                                        ratio=st.session_state.cfg_ratio,
                                        images=ref_urls or None, first_frame=first,
                                        resolution=resolution,
                                    )
                                except Api88Error as exc:
                                    if not ref_urls or exc.status not in {400, 413, 415, 422}:
                                        raise
                                    add_log(
                                        f"{sid} 参考图提交被拒（{exc.status}），"
                                        "回退到首帧 / 文生视频"
                                    )
                                    st.warning(
                                        f"{sid} 参考图提交被拒，已回退到"
                                        + ("首帧" if fallback_first else "文生视频")
                                    )
                                    vid = api.create_video(
                                        video_model, prompt, duration=duration,
                                        ratio=st.session_state.cfg_ratio,
                                        first_frame=fallback_first,
                                        resolution=resolution,
                                    )
                                status.update(label=f"{sid} 生成中，任务 {vid}")

                                def tick(obj, _sid=sid, _status=status):
                                    raw = str(obj.get("status") or obj.get("state") or "")
                                    label = API_STATUS_LABELS.get(
                                        raw.lower(), raw or "等待中"
                                    )
                                    progress = obj.get("progress", "")
                                    _status.update(
                                        label=f"{_sid}：{label} {progress}"
                                    )

                                data = api.wait_video(vid, on_tick=tick)
                                path = clips_dir / f"{sid}.mp4"
                                path.write_bytes(data)
                                st.session_state.clips[sid] = str(path)
                                status.update(label=f"{sid} 完成", state="complete")
                            done += 1
                            add_log(f"{sid} 生成完成")
                        except Exception as exc:
                            failed += 1
                            add_log(f"{sid} 生成失败：{exc}")
                            st.error(f"{sid} 生成失败：{exc}")
                    if done:
                        set_preview("分镜 / 关键帧")
                        flash(
                            "success",
                            f"本次完成 {done} 个镜头"
                            + (f"，失败 {failed} 个" if failed else ""),
                        )
                        st.rerun()

    # ---------- 03 合成去重 ----------
    with st.container(border=True, key="sec_compose"):
        st.subheader("03 合成去重", icon=":material/movie:")
        folder = current_dir()
        clips_dir = folder / "clips" if folder else None
        shots = st.session_state.shots
        available = (
            [str(p) for p in sorted(clips_dir.glob("*.mp4"))]
            if clips_dir is not None and clips_dir.is_dir() else []
        )
        if not available:
            st.caption("先在「02 拆解复刻」里生成视频镜头。")
        else:
            order = {shot.get("id"): i for i, shot in enumerate(shots)}
            available.sort(key=lambda p: order.get(Path(p).stem, 999))
            chosen = st.multiselect(
                "选择要拼接的镜头", available, default=available,
                format_func=lambda p: Path(p).stem,
                key=f"chosen_{token}_{st.session_state.shots_nonce}",
                placeholder="请选择镜头",
            )

            bgm_file = st.file_uploader(
                "背景音乐（可选）", type=["mp3", "wav", "m4a", "aac"],
                key=tkey("bgm"),
            )
            bgm_path = None
            if bgm_file:
                suffix = Path(bgm_file.name).suffix or ".mp3"
                data = bgm_file.getvalue()
                digest = hashlib.sha256(data).hexdigest()
                if digest != st.session_state.bgm_hash:
                    target = ensure_job_dir() / f"bgm{suffix}"
                    target.write_bytes(data)
                    st.session_state.bgm_path = str(target)
                    st.session_state.bgm_hash = digest
                bgm_path = Path(st.session_state.bgm_path)
            else:
                st.session_state.bgm_path = ""
                st.session_state.bgm_hash = ""
            st.caption("支持 MP3 / WAV / M4A / AAC；不选则不添加背景音乐。")

            add_subs = st.checkbox(
                "烧录字幕（需要分镜里有台词）", value=False, key=tkey("add_subs")
            )
            do_dedup = st.checkbox("执行音画去重", value=True, key=tkey("do_dedup"))
            dedup_mode = st.radio(
                "去重模式", ["subtle", "aggressive"], horizontal=True,
                format_func=lambda value: DEDUP_LABELS.get(value, value),
                help="轻微无感；强力去重包含微扫光，适合切片 / 带货。",
                key=tkey("dedup_mode"),
            )

            c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
            with c1:
                st.selectbox(
                    "成片输出分辨率", RESOLUTION_OPTIONS, key="cfg_resolution",
                    format_func=lambda value: RESOLUTION_LABELS.get(value, value),
                    help="只影响本地拼接导出的成片。",
                )
            with c2:
                make_final = st.button(
                    "生成成片", type="primary", icon=":material/check_circle:",
                    key=tkey("make_final"), width="stretch",
                )
            st.caption("成片预览与下载在右侧「成片」菜单。")
            if make_final:
                try:
                    resolution = st.session_state.cfg_resolution
                    add_log(f"开始拼接 {len(chosen)} 个镜头（{resolution}）")
                    with st.spinner("统一规格并拼接..."):
                        merged = media.concat(
                            [Path(p) for p in chosen], ensure_job_dir() / "merged.mp4",
                            ratio=st.session_state.cfg_ratio, bgm=bgm_path,
                            resolution=resolution,
                        )
                    st.session_state.merged_path = str(merged)
                    add_log(f"拼接完成：{merged.name}")

                    current = merged
                    if add_subs:
                        srt_text = shots_to_srt(shots)
                        if not srt_text.strip():
                            st.warning("分镜里没有台词，跳过字幕")
                        else:
                            srt = ensure_job_dir() / "subtitles.srt"
                            srt.write_text(srt_text, encoding="utf-8")
                            with st.spinner("烧录字幕..."):
                                current = media.burn_subtitles(
                                    current, srt, ensure_job_dir() / "subtitled.mp4"
                                )
                            add_log("字幕烧录完成")

                    if do_dedup:
                        with st.spinner("音画去重中..."):
                            current, log = dedup.run_dedup(
                                current, ensure_job_dir() / "final.mp4", dedup_mode
                            )
                        add_log(f"去重完成：{current.name}")

                    st.session_state.final_path = str(current)
                    set_preview("成片")
                    add_log(f"成片完成：{current.name}")
                    flash("success", f"成片完成：{current.name}")
                    st.rerun()
                except Exception as exc:
                    add_log(f"成片失败：{exc}")
                    st.error(str(exc))

with right:
    with st.container(key="preview_pane", border=True):
        render_preview()
