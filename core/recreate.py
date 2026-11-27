"""复刻：读取 video-recreation-skill 的档位规则，生成分镜与脚本。"""
import json
import re
from pathlib import Path

from .api88 import Api88
from .config import RECREATION_DIR


def load_mode(mode: str) -> dict:
    policy_path = RECREATION_DIR / "specs" / "recreation_policy.json"
    if not policy_path.exists():
        raise RuntimeError(f"找不到复刻 Skill 的策略文件：{policy_path}")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    modes = policy.get("modes", {})
    if mode not in modes:
        raise RuntimeError(f"未知档位：{mode}")
    return modes[mode]


def _parse_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError(f"模型没有返回 JSON：{text[:500]}")
    return json.loads(raw[start:end + 1])


def analyze_shots(api: Api88, model: str, frames: list[tuple[float, Path]],
                  mode: str, user_request: str = "", transcript: list[dict] | None = None,
                  refs: list[dict] | None = None,
                  anchor: str = "") -> tuple[list[dict], str]:
    """返回 (shots, 原始模型输出)。参考图先于原片帧传入。"""
    mode_spec = load_mode(mode)
    mode_rule = mode_spec.get("analysis_prompt") or mode_spec.get("label", "")
    refs = refs or []
    offset = len(refs)

    ref_lines = "\n".join(
        f"- 第 {i + 1} 张图：@{ref['alias']}（用户参考图，不是原片帧）"
        for i, ref in enumerate(refs)
    )
    timeline = "\n".join(
        f"- 第 {i + 1 + offset} 张图：{t:.1f}s 原片帧"
        for i, (t, _) in enumerate(frames)
    )
    if refs:
        order_note = (
            f"第 1~{offset} 张是用户参考图；第 {offset + 1} 张起是按时间抽取的原片帧。"
        )
        ref_note = (
            "参考图是用户提供的人物、产品或风格依据：写每镜 prompt 时，"
            "人物/产品按参考图描述，风格图则沿用其画风；不要照抄原片里的人物/产品外观。"
            "reference 里填用到的 @别名。"
        )
    else:
        order_note = "本次没有参考图，所有附图都是原片帧。"
        ref_note = ""
    anchor_rule = (
        "每镜 prompt 必须体现「全片统一设定」中的人物/产品/风格；"
        if anchor.strip() else ""
    )
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in (transcript or []))

    prompt = f"""你是短视频导演。请根据按时间顺序抽取的参考视频帧，拆解出可复刻的分镜。

图片顺序：{order_note}
参考图别名：
{ref_lines or "（无）"}

镜头依据：原片帧是按时间均匀抽取的，相邻帧之间的内容变化就是镜头边界。
原片帧时间点：
{timeline}

原片口播/字幕（可能为空）：
{lines or "（无转写文本）"}

本次二创档位规则：
{mode_rule}

全片统一设定（必须贯穿所有镜头）：
{anchor.strip() or "（无）"}

用户额外要求：
{user_request or "（无）"}

{ref_note}

只输出一个 JSON 对象，不要解释，格式严格如下：
{{
  "shots": [
    {{
      "id": "shot_01",
      "start": 0.0,
      "end": 3.0,
      "duration": 3.0,
      "semantic_function": "钩子/卖点/转折/CTA 等",
      "description": "原片这一镜的画面描述",
      "prompt": "用于生成这一镜画面的中文提示词，需符合档位规则",
      "dialogue": "这一镜的口播或字幕，没有就留空",
      "camera": "机位与运镜",
      "reference": "@参考图别名，多个用空格分隔；没有就留空"
    }}
  ]
}}
要求：{anchor_rule}reference 只能使用上面列出的 @别名，不要编造；镜头按时间顺序；duration 等于 end-start；每镜 2-8 秒；至少 1 个镜头。"""

    images = [ref["abs_path"] for ref in refs] + [str(p) for _, p in frames]
    text = api.chat_vision(model, prompt, images, temperature=0.4, max_tokens=6000)
    data = _parse_json(text)
    shots = data.get("shots") or []
    if not shots:
        raise RuntimeError("模型返回的分镜为空")
    for i, shot in enumerate(shots, 1):
        shot.setdefault("id", f"shot_{i:02d}")
        shot.setdefault("semantic_function", "")
        shot.setdefault("description", "")
        shot.setdefault("prompt", shot.get("description", ""))
        shot.setdefault("dialogue", "")
        shot.setdefault("camera", "")
        ref_value = shot.get("reference", "")
        if isinstance(ref_value, list):
            ref_value = " ".join(str(item) for item in ref_value)
        shot["reference"] = str(ref_value or "").strip()
        start = float(shot.get("start", 0))
        end = float(shot.get("end", start + float(shot.get("duration", 3))))
        shot["start"], shot["end"] = start, end
        shot["duration"] = round(max(1.0, end - start), 2)
    return shots, text


def shot_aliases(shot: dict) -> list[str]:
    """解析镜头里的 @别名；兼容模型漏写 @ 的纯别名文本。"""
    text = str(shot.get("reference") or "")
    raw = [
        part.strip().lstrip("@")
        for part in re.split(r"[,，、;；\s]+", text)
        if part.strip()
    ]
    result, seen = [], set()
    for alias in raw:
        if alias and alias not in seen:
            seen.add(alias)
            result.append(alias)
    return result


def match_shot_refs(shot: dict, refs: list[dict] | None) -> list[dict]:
    """按 @别名精确匹配参考图，保持镜头里的顺序。"""
    by_alias = {ref["alias"]: ref for ref in (refs or [])}
    return [by_alias[alias] for alias in shot_aliases(shot) if alias in by_alias]


def shots_to_script(shots: list[dict], mode_label: str) -> str:
    parts = [f"# 复刻脚本（档位：{mode_label}）", ""]
    for shot in shots:
        reference = (shot.get("reference") or "").strip()
        parts += [
            f"## {shot['id']}  {shot['start']:.1f}-{shot['end']:.1f}s",
            f"- 功能：{shot.get('semantic_function', '')}",
            f"- 画面：{shot.get('description', '')}",
            f"- 镜头：{shot.get('camera', '')}",
            f"- 台词：{shot.get('dialogue', '')}",
        ]
        if reference:
            parts.append(f"- 参考附件：{reference}")
        parts += [
            f"- 生成提示词：{shot.get('prompt', '')}",
            "",
        ]
    return "\n".join(parts)


def image_prompt(shot: dict, anchor: str, ratio: str) -> str:
    text = shot.get("prompt") or shot.get("description", "")
    suffix = f"画面比例 {ratio}。"
    reference = (shot.get("reference") or "").strip()
    if reference:
        suffix += f"参考附件：{reference}，请保持参考图中的人物、产品或风格一致。"
    if anchor.strip():
        suffix += f"全片统一设定：{anchor.strip()}。"
    return f"{text} {suffix}"


def video_prompt(shot: dict, ref_aliases: list[str] | None = None,
                 anchor: str = "") -> str:
    text = shot.get("prompt") or shot.get("description", "")
    camera = shot.get("camera", "")
    dialogue = shot.get("dialogue", "")
    extra = []
    if camera:
        extra.append(f"镜头运动：{camera}")
    if dialogue:
        extra.append(f"口播：{dialogue}")
    if ref_aliases:
        order = "；".join(
            f"第 {i} 张=@{alias}" for i, alias in enumerate(ref_aliases, 1)
        )
        extra.append(f"参考图顺序：{order}")
    if anchor.strip():
        extra.append(f"全片统一设定：{anchor.strip()}")
    extra.append(f"时长约 {shot.get('duration', 5)} 秒")
    return text + "。" + "；".join(extra) + "。"
