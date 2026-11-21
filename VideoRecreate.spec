# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

datas = []
binaries = []
hiddenimports = []

# Streamlit：静态资源 + 子模块 + 版本 metadata（import 时必需）
datas += collect_data_files("streamlit")
hiddenimports += collect_submodules(
    "streamlit",
    filter=lambda name: not name.startswith("streamlit.testing"),
)
hiddenimports += [
    "uvicorn",
    "starlette",
    "anyio",
    "websockets",
    "multipart",
    "python_multipart",
    "itsdangerous",
    "click",
    "h11",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "altair",
    "pydeck",
    "jinja2",
]

# faster-whisper 链路
datas += collect_data_files("faster_whisper")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += [
    "ctranslate2",
    "tokenizers",
    "av",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "huggingface_hub",
    "tqdm",
    "yaml",
    "flatbuffers",
]
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("av")
hiddenimports += collect_submodules(
    "huggingface_hub",
    filter=lambda name: not any(
        part in name
        for part in (
            "inference", "cli", "_space", "_jobs", "_webhooks",
            "_oauth", "fastai", "tensorboard", "mcp",
        )
    ),
)

# pywebview / pythonnet
hiddenimports += [
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "webview.platforms.win32",
    "clr",
    "clr_loader",
    "pythonnet",
]

# 基础运行时与自检
hiddenimports += [
    "pandas",
    "pyarrow",
    "numpy",
    "PIL",
    "PIL.Image",
    "requests",
    "certifi",
    "charset_normalizer",
    "idna",
    "urllib3",
    "selftest",
]

for dist in (
    "streamlit",
    "pandas",
    "pyarrow",
    "numpy",
    "faster-whisper",
    "ctranslate2",
    "tokenizers",
    "av",
    "onnxruntime",
    "huggingface-hub",
    "pywebview",
    "pythonnet",
    "clr-loader",
    "requests",
    "starlette",
    "uvicorn",
    "anyio",
    "websockets",
    "itsdangerous",
    "python-multipart",
    "click",
    "packaging",
    "pillow",
    "altair",
    "pydeck",
    "protobuf",
    "certifi",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

a = Analysis(
    ["launcher.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "core",
        "hf_xet",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "tkinter",
        "matplotlib",
        "plotly",
        "torch",
        "transformers",
        "streamlit.testing",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoRecreate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get("VR_CONSOLE") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideoRecreate",
)
