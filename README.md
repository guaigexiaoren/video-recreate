# 视频复刻工作台

把「下载无水印素材 → 拆解复刻 → 合成去重」串成一个 Streamlit 网页。

## 组成

| 环节 | 复用/实现 | 位置 |
|---|---|---|
| 去水印下载 | `video-downloader` Skill（yt-dlp + 抖音/快手分享页解析） | `~/.claude/skills/video-downloader` |
| 拆解复刻 | `video-recreation-skill` 的档位规则 + 本项目的 88api 执行器 | `~/.claude/skills/video-recreation-skill` |
| 音画去重 | `video-original-dedup` Skill | `~/.claude/skills/video-original-dedup` |
| 历史任务 | 扫描 `outputs/` 产物、元数据与日志恢复 | `core/jobs.py` |
| 网页工作台 | Streamlit | `app.py` + `core/` |

## 启动

双击 `run.bat`，或在 Git Bash 里：

```bash
./run.sh
```

然后打开 http://127.0.0.1:8501

## 便携分发版（原生窗口 exe）

适合发给别人：双击 `VideoRecreate.exe` 打开原生桌面窗口，不依赖对方安装 Python。

```powershell
# 先准备本机工具：ffmpeg/ffprobe 在 ~/tools/bin，Git 在 C:\Program Files\Git
powershell -ExecutionPolicy Bypass -File build.ps1
# 可选：-IncludeWhisperModel 附带 small 模型；-ExcludeHistory 不带个人任务
```

产物：

```text
dist/VideoRecreate/
  VideoRecreate.exe
  app/          # app.py + core + assets + .streamlit
  runtime/      # ffmpeg/ffprobe、yt-dlp、便携 Python、Git Bash、skills
  data/         # config.json（首次保存时生成）、outputs、logs、tmp、models
dist/VideoRecreate-1.0.0.zip
```

- 构建依赖单独放在 `requirements-build.txt`，不改 `requirements.txt`。
- 体积约 1.0–1.1GB（不带 Whisper 模型）；带 small 模型约 1.5–1.6GB。
- 目标机需要 Windows 10/11 64 位 + WebView2 或 Edge（缺失时回退 Edge `--app` 独立窗口）。
- 口播转写默认首次联网下载模型；用 `-IncludeWhisperModel` 可离线。
- **分发前**：确认 `data/config.json` 中没有你的 API 密钥，删除 `data/outputs` 里的个人素材，并核对 `THIRD_PARTY_NOTICES.txt` 与 `LICENSES/`。
- FFmpeg 使用 GPLv3 + libx264 构建；Git for Windows 为 GPLv2；分发时需遵守对应许可。

## 页面布局

- 左侧：顶部「新建任务」；放大镜按钮弹出搜索；中间任务列表；最底部「设置」按钮（弹窗）。
- 中间：一条纵向流程，三张分节卡片：01 获取素材 → 02 拆解复刻 → 03 合成去重。
- 右侧：预览卡片，用一排菜单切换「素材 / 分镜·关键帧 / 成片 / 日志」。
- 右上角 Deploy 和 ⋮ 菜单已隐藏；重新运行、关于、API 配置都在左下「设置」里。

### 自适应规则

- 宽度 ≥ 1201px：中间工作区 + 右侧固定宽度预览（约 320–400px），预览在页面滚动时固定跟随。
- 宽度 ≤ 1200px：工作区变成整行，预览自动移到工作区下方，避免三栏互相挤压。
- 手机宽度：自动缩小标题字号和顶部留白，右侧菜单自动换行。
- 布局层使用开源 [Open Props](https://open-props.style) v1.7.23（MIT）设计变量，CSS 已内置在 `assets/open-props.min.css`，不依赖外网。

## 首次使用

1. 点左下「设置」，填 API 密钥和接口地址（默认 `https://api.88api.ai`），点「测试连接 / 拉取模型」，再点「保存配置」。
2. 点左上「新建任务」，在「01 获取素材」里粘贴链接下载或上传本地视频；两者在同一行，默认按原画下载。
3. 「02 拆解复刻」：
   - 填二创档位、全片统一设定、额外要求；可上传人物 / 产品 / 风格参考图。
   - 「全片统一设定」写要固定的人物 / 产品 / 风格，会贯穿拆解、关键帧和生视频；留空表示完全照原片。
   - 「额外要求」写要在复刻基础上额外修改的地方（换卖点、改口播、调顺序等），只在拆解时使用。
   - 抽帧与转写旁选语音识别模型；拆解旁选分析模型；拆解后得到分镜表。
   - 在分镜表「参考附件」列填 `@别名`；表格下面紧接脚本汇总，可直接下载。
   - 关键帧旁选生图模型和画面比例；生成视频旁选生视频模型、单镜时长和生成分辨率。
4. 「03 合成去重」：选镜头、可选 BGM / 字幕 / 去重、选成片输出分辨率；成片在右侧「成片」菜单预览和下载。
5. 历史任务：点左侧放大镜搜索，或点任务名恢复；每行 ⋯ 菜单可重命名、删除、打开文件夹。

## 参考图与 @ 引用

- 上传参考图后，别名由文件名生成（如 `女主.png → @女主`），重名自动加序号。
- 拆解时参考图会排在原片帧前面一起发给视觉模型；模型会把 `@别名` 自动填进分镜表「参考附件」列，也可以手动填写。
- 参考图缩略图下方会显示「已用于 N 个镜头」，方便确认引用是否生效。
- 生成视频时，填写了 `@参考图` 的镜头优先用参考图生成（首帧设置对该镜头不生效）。
- 参考图会以 data URL 传给生视频接口；接口拒绝 400/413/415/422 时自动回退到首帧或文生视频，并记录警告。

## 88api 调用说明

- 模型必须先 `GET /v1/models` 获取当前 Key 可见的精确 ID，工作台不会硬编码。
- 生图：`POST /v1/images/generations`，默认 `doubao-seedream-5-0-pro`。
- 生视频：`POST /v1/videos` → `GET /v1/videos/{id}` 轮询，默认 `doubao-seedance-2.5`。
- 生视频的 `resolution` 由「生成分辨率」控制（480p / 720p / 1080p，服务端不分大小写，工作台统一传小写）；模型不支持的档位会返回 400。
- 有参考图的镜头传 `images`；没有参考图时，关键帧作为首帧放在 `metadata.firstFrame`。
- Seedance 时长 4–30 秒，工作台会自动把镜头时长夹到这个范围。

## 历史任务

每个任务保存在 `outputs/<时间戳>/`。打开历史任务时会自动恢复：素材、抽帧、分镜、口播转写、关键帧地址、参考图、已生成镜头、成片和运行日志。相关持久化文件：

- `meta.json`：任务名、模式、锚点、额外要求、源文件路径
- `transcript.json`：口播转写
- `keyframes.json`：关键帧云端地址
- `refs.json` + `refs/`：参考图别名和文件
- `log.txt`：运行日志

## 配置

`config.json` 由左下「设置」生成，已被 `.gitignore` 排除。模板见 `config.example.json`。`video_resolution` 控制成片输出分辨率。

`.streamlit/config.toml` 设置了 `toolbarMode = "minimal"`，用于隐藏右上角 Deploy 和 ⋮ 菜单；修改后需要重启 Streamlit 进程生效。

## 已完成的验证

- `ffmpeg 9.1` / `ffprobe` / `yt-dlp 2026.10.26` / Python 3.13.12 可用。
- 去重脚本 `dedup_av.py --encoder libx264` 跑通，输出时长 3.83s、含音轨。
- 两段镜头拼接跑通，输出 8.02s、1080×1920、含音轨。
- 中文字幕烧录跑通。
- Streamlit 页面无头渲染无异常，HTTP 200。
- 纵向三段流程、右侧四菜单、设置弹窗、任务搜索 / 打开恢复 / 重命名 / 删除已通过无头 Agent API 与浏览器验证。
- 分镜表中文表头、参考图上传 / 删除、`@别名`、添加 / 删除 / 保存镜头已实测。
- 成片 1080p / 720p / 480p 拼接实测；720p 输出 720×1280、含音轨。
- 抖音分享页解析脚本可正常执行（真实下载需要有效分享链接）。
- 88api `/v1/models` 返回 401，说明接口可达，填入 Key 后即可用。

## 已知限制

- YouTube 在当前网络下超时，其它平台以实际网络为准。
- 视频生成按次计费，工作台只对勾选的镜头提交。
- 去重功能用于规避平台原创检测，可能违反平台规则，风险由使用者承担。
- 半自动：关键帧和分镜需要人工确认后再生成视频。
- 参考图以 data URL 传给生视频接口；如果接口不接受，会自动回退首帧或文生视频。真实接口行为需要填 Key 后验证。
- 文件上传器拖拽提示、分镜表列头右键菜单、视频播放器控件属于 Streamlit / 浏览器内置，无法完全中文化；应用自己的标签和表头已全部中文化。
