"""88api (OpenAI 兼容) 客户端：模型发现、对话、生图、生视频、轮询、下载。"""
import base64
import mimetypes
import time
from pathlib import Path

import requests


class Api88Error(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Api88:
    def __init__(self, api_key: str, base_url: str = "https://api.88api.ai", timeout: int = 120):
        if not api_key:
            raise Api88Error("请先在「设置」中填写 API 密钥")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        resp = requests.request(method, f"{self.base_url}{path}", headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            raise Api88Error(f"HTTP {resp.status_code}: {resp.text[:800]}", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            raise Api88Error(f"返回不是 JSON：{resp.text[:500]}")

    # ---------- 模型 ----------
    def list_models(self) -> list[str]:
        data = self._request("GET", "/v1/models")
        return [m["id"] for m in data.get("data", []) if m.get("id")]

    # ---------- 对话 / 视觉分析 ----------
    def chat(self, model: str, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._request("POST", "/v1/chat/completions", json=body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise Api88Error(f"无法解析对话响应：{str(data)[:500]}")

    def chat_vision(self, model: str, prompt: str, image_paths: list[str], **kwargs) -> str:
        content = [{"type": "text", "text": prompt}]
        for path in image_paths:
            file = Path(path)
            mime = mimetypes.guess_type(file.name)[0] or "image/jpeg"
            b64 = base64.b64encode(file.read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        return self.chat(model, [{"role": "user", "content": content}], **kwargs)

    # ---------- 生图 ----------
    def generate_image(self, model: str, prompt: str, size: str = "1K",
                       image: str | None = None, images: list[str] | None = None) -> str:
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": size,
            "response_format": "url",
        }
        if image:
            body["image"] = image
        if images:
            body["images"] = images
        data = self._request("POST", "/v1/images/generations", json=body)
        items = data.get("data") or []
        if not items:
            raise Api88Error(f"生图响应没有 data：{str(data)[:500]}")
        item = items[0]
        if item.get("url"):
            return item["url"]
        if item.get("b64_json"):
            return "data:image/png;base64," + item["b64_json"]
        raise Api88Error(f"生图响应没有图片地址：{str(item)[:300]}")

    # ---------- 生视频 ----------
    def create_video(self, model: str, prompt: str, duration: int = 5,
                     ratio: str = "9:16", first_frame: str | None = None,
                     images: list[str] | None = None,
                     negative_prompt: str | None = None,
                     resolution: str | None = None) -> str:
        body = {"model": model, "prompt": prompt, "duration": duration, "ratio": ratio}
        if resolution:
            body["resolution"] = str(resolution).lower()
        # Seedance 等模型的首帧与普通参考图互斥，因此首帧优先且不再传 images
        if first_frame:
            body["metadata"] = {"firstFrame": first_frame}
        elif images:
            body["images"] = images
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        data = self._request("POST", "/v1/videos", json=body)
        video_id = data.get("id") or data.get("task_id")
        if not video_id:
            raise Api88Error(f"生视频响应没有任务 id：{str(data)[:500]}")
        return str(video_id)

    def get_video(self, video_id: str) -> dict:
        return self._request("GET", f"/v1/videos/{video_id}")

    @staticmethod
    def _status(obj: dict) -> str:
        return str(obj.get("status") or obj.get("state") or "").lower()

    @staticmethod
    def _video_url(obj: dict) -> str | None:
        for key in ("url", "video_url", "result_url", "output_url"):
            if obj.get(key):
                return obj[key]
        for key in ("data", "output", "result", "video"):
            value = obj.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for sub in ("url", "video_url", "result_url"):
                    if value.get(sub):
                        return value[sub]
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    for sub in ("url", "video_url", "result_url"):
                        if first.get(sub):
                            return first[sub]
        return None

    def wait_video(self, video_id: str, timeout: int = 900, interval: int = 10, on_tick=None) -> bytes:
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.get_video(video_id)
            status = self._status(last)
            if on_tick:
                on_tick(last)
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise Api88Error(f"视频任务失败：{str(last)[:600]}")
            if status in {"completed", "succeeded", "success", "done", "finished"}:
                url = self._video_url(last)
                if not url:
                    raise Api88Error(f"任务完成但没有视频地址：{str(last)[:600]}")
                return requests.get(url, timeout=self.timeout).content
            url = self._video_url(last)
            if url and status == "":
                return requests.get(url, timeout=self.timeout).content
            time.sleep(interval)
        raise Api88Error(f"视频任务超时（{timeout}s）：{str(last)[:600]}")

    # ---------- 下载 ----------
    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            dest.write_bytes(base64.b64decode(b64))
            return dest
        resp = requests.get(url, timeout=self.timeout, stream=True)
        if resp.status_code >= 400:
            raise Api88Error(f"下载失败 HTTP {resp.status_code}")
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
        return dest


def pick_models(models: list[str], kind: str) -> list[str]:
    """按名称粗筛模型，避免把生图模型当成生视频模型。"""
    names = [m.lower() for m in models]
    if kind == "video":
        keys = ("seedance", "kling", "klingai", "wan", "veo", "minimax", "sora", "grok-video", "jimeng", "hailuo")
    elif kind == "image":
        keys = ("seedream", "image", "imagen", "ideogram", "nano-banana", "midjourney", "flux", "gpt-image")
    else:
        keys = ("gemini", "gpt", "qwen", "claude", "glm", "vl", "vision", "doubao-seed")
    result = [m for m, n in zip(models, names) if any(k in n for k in keys)]
    return result or models
