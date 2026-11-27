"""视频复刻工作台桌面启动器：原生窗口 + Streamlit 子进程。"""
from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
APP_DIR = EXE_DIR / "app" if FROZEN else EXE_DIR
DATA_DIR = EXE_DIR / "data"
RUNTIME_DIR = EXE_DIR / "runtime"
LOG_DIR = DATA_DIR / "logs"

SPLASH = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  html,body{height:100%;margin:0;background:#f7f7f8;color:#31333f;
    font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    display:flex;align-items:center;justify-content:center}
  .box{text-align:center}
  .spinner{width:28px;height:28px;border:3px solid #e5e5e7;border-top-color:#ff4b4b;
    border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 14px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .title{font-size:18px;font-weight:600}
  .hint{font-size:13px;color:#808495;margin-top:6px}
</style></head><body><div class="box">
  <div class="spinner"></div>
  <div class="title">视频复刻工作台</div>
  <div class="hint">正在启动本地服务...</div>
</div></body></html>
"""

ERROR_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei',sans-serif;padding:32px;color:#31333f">
<h3>启动失败</h3>
<p>请查看 <code>data/logs/streamlit.log</code> 获取详细错误。</p>
</body></html>
"""

TIMEOUT_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei',sans-serif;padding:32px;color:#31333f">
<h3>启动超时</h3>
<p>本地服务没有在 60 秒内就绪，请查看 <code>data/logs/streamlit.log</code>。</p>
</body></html>
"""

_MUTEX_HANDLE = None


def fix_streams() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(LOG_DIR / "launcher.log", "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout


def message_box(text: str, title: str = "视频复刻工作台") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        print(text)


def single_instance() -> bool:
    global _MUTEX_HANDLE
    try:
        kernel32 = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\VideoRecreate")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            message_box("视频复刻工作台已经在运行。")
            return False
    except Exception:
        pass
    return True


def prepare_dirs() -> bool:
    for name in ("outputs", "logs", "tmp", "home", "webview", "models"):
        (DATA_DIR / name).mkdir(parents=True, exist_ok=True)
    try:
        (DATA_DIR / ".write_test").write_text("ok", encoding="utf-8")
    except OSError:
        message_box(
            "data 目录不可写。\n\n请把整个文件夹移到桌面或文档目录，"
            "不要放在 C:\\Program Files 等只读位置。"
        )
        return False
    return True


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def healthy(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        conn.request("GET", "/_stcore/health")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status == 200 and b"ok" in body.lower()
    except OSError:
        return False


def child_env() -> dict:
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "STREAMLIT_SERVER_PORT"):
        env.pop(key, None)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HOME": str(DATA_DIR / "models" / "hf"),
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TEMP": str(DATA_DIR / "tmp"),
        "TMP": str(DATA_DIR / "tmp"),
        "TMPDIR": os.path.relpath(DATA_DIR / "tmp", EXE_DIR).replace("\\", "/"),
        "HOME": str(DATA_DIR / "home"),
    })
    if FROZEN:
        paths = [
            RUNTIME_DIR / "python",
            RUNTIME_DIR / "bin",
            RUNTIME_DIR / "git" / "bin",
            RUNTIME_DIR / "git" / "usr" / "bin",
            RUNTIME_DIR / "git" / "ucrt64" / "bin",
        ]
        parts = [str(path) for path in paths if path.is_dir()]
        env["PATH"] = ";".join(parts) + (";" + env["PATH"] if env.get("PATH") else "")
    return env


def spawn_server() -> tuple[subprocess.Popen, int, object]:
    if FROZEN:
        base = [sys.executable, "--serve"]
    else:
        base = [sys.executable, str(Path(__file__).resolve()), "--serve"]
    last_error = ""
    for _ in range(3):
        port = free_port()
        log = open(LOG_DIR / "streamlit.log", "w", encoding="utf-8", buffering=1)
        cmd = [*base, "--port", str(port), "--parent-pid", str(os.getpid())]
        proc = subprocess.Popen(
            cmd, cwd=str(EXE_DIR), env=child_env(),
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(0.4)
        if proc.poll() is None:
            return proc, port, log
        last_error = f"退出码 {proc.returncode}"
        log.close()
    raise RuntimeError(f"Streamlit 启动失败：{last_error}")


def serve(port: int, parent_pid: int) -> None:
    if parent_pid:
        def watch_parent() -> None:
            try:
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
                if not handle:
                    os._exit(0)
                while kernel32.WaitForSingleObject(handle, 2000) == 0x102:
                    pass
                os._exit(0)
            except Exception:
                pass

        threading.Thread(target=watch_parent, daemon=True).start()

    from streamlit import config as st_config
    from streamlit.web import bootstrap

    app = str(APP_DIR / "app.py")
    st_config._main_script_path = app
    flags = {
        "server.headless": True,
        "server.address": "127.0.0.1",
        "server.port": port,
        "server.fileWatcherType": "none",
        "server.runOnSave": False,
        "browser.serverAddress": "127.0.0.1",
        "browser.serverPort": port,
        "browser.gatherUsageStats": False,
        "client.toolbarMode": "minimal",
        "global.developmentMode": False,
    }
    bootstrap.load_config_options(flag_options=flags)
    bootstrap.run(app, False, [], flags)


def webview2_ok() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
            if version and not str(version).startswith("0."):
                return True
        except OSError:
            continue
    return False


def dotnet_ok() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        ) as handle:
            release, _ = winreg.QueryValueEx(handle, "Release")
        return int(release) >= 394802
    except Exception:
        return False


def edge_exe() -> Path | None:
    for candidate in (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if candidate.is_file():
            return candidate
    return None


def wait_health(proc: subprocess.Popen, port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if healthy(port):
            return True
        time.sleep(0.25)
    return False


def run_webview(proc: subprocess.Popen, port: int, log_handle) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        "视频复刻工作台", html=SPLASH,
        width=1440, height=900, min_size=(1100, 700),
        text_select=True, zoomable=True,
    )
    url = f"http://127.0.0.1:{port}"

    def load() -> None:
        if wait_health(proc, port):
            window.load_url(url)
        elif proc.poll() is not None:
            window.load_html(ERROR_HTML)
        else:
            window.load_html(TIMEOUT_HTML)

    try:
        webview.start(
            load, gui="edgechromium", private_mode=False,
            storage_path=str(DATA_DIR / "webview"),
        )
    except Exception as exc:
        print(f"pywebview 启动失败：{exc}")
        run_edge(proc, port)


def prepare_edge_profile() -> None:
    """关闭 Edge 自带翻译，避免本地页面被当成英文而弹出翻译提示。"""
    prefs = DATA_DIR / "edge-profile" / "Default" / "Preferences"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if prefs.is_file():
        try:
            loaded = json.loads(prefs.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
    translate = data.get("translate")
    if not isinstance(translate, dict):
        translate = {}
    if translate.get("enabled") is False:
        return
    translate["enabled"] = False
    data["translate"] = translate
    prefs.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def run_edge(proc: subprocess.Popen, port: int) -> None:
    if not wait_health(proc, port):
        message_box(
            "本地服务启动失败，请查看 data/logs/streamlit.log。",
        )
        return
    edge = edge_exe()
    if edge is None:
        message_box("找不到 Microsoft Edge，请安装 WebView2 或 Edge 后重试。")
        return
    url = f"http://127.0.0.1:{port}"
    prepare_edge_profile()
    edge_proc = subprocess.Popen([
        str(edge), f"--app={url}",
        f"--user-data-dir={DATA_DIR / 'edge-profile'}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-translate",
        "--disable-features=Translate,TranslateUI,msEdgeTranslate",
        "--disable-background-mode",
        "--lang=zh-CN",
        "--window-size=1440,900",
    ])
    edge_proc.wait()


def gui() -> None:
    proc, port, log_handle = spawn_server()
    try:
        if webview2_ok() and dotnet_ok():
            try:
                run_webview(proc, port, log_handle)
                return
            except Exception as exc:
                print(f"原生窗口启动失败，回退 Edge：{exc}")
        run_edge(proc, port)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_handle.close()


def main() -> None:
    fix_streams()
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    if args.serve:
        serve(args.port, args.parent_pid)
        return

    if args.self_test:
        sys.path.insert(0, str(APP_DIR))
        from selftest import run_self_test
        sys.exit(run_self_test(full=args.full))

    if not single_instance():
        return
    if not prepare_dirs():
        return

    try:
        gui()
    except Exception as exc:
        print(f"启动失败：{exc}")
        message_box(f"启动失败：{exc}\n\n请查看 data/logs/streamlit.log。")


if __name__ == "__main__":
    main()
