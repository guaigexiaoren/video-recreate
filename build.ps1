param(
    [string]$PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    [string]$FfmpegDir = "$env:USERPROFILE\tools\bin",
    [string]$GitRoot = "C:\Program Files\Git",
    [string]$YtDlpVersion = "latest",
    [switch]$IncludeWhisperModel,
    [switch]$ExcludeHistory,
    [switch]$SkipSelfTest,
    [switch]$NoZip
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Build = Join-Path $Root ".build"
$Venv = Join-Path $Build "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Dist = Join-Path $Root "dist\VideoRecreate"
$App = Join-Path $Dist "app"
$Runtime = Join-Path $Dist "runtime"
$Bin = Join-Path $Runtime "bin"
$Data = Join-Path $Dist "data"

function Copy-Tree {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    if (-not (Test-Path $Source)) { throw "缺少目录：$Source" }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    $roboArgs = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($dir in $ExcludeDirs) { $roboArgs += @("/XD", $dir) }
    foreach ($file in $ExcludeFiles) { $roboArgs += @("/XF", $file) }
    & robocopy @roboArgs | Out-Null
    $code = $LASTEXITCODE
    $global:LASTEXITCODE = 0
    if ($code -ge 8) { throw "robocopy 失败（$code）：$Source -> $Destination" }
}

function Download-File {
    param([string]$Url, [string]$OutFile)
    Write-Host "下载：$Url"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

if (-not (Test-Path $PythonExe)) {
    throw "找不到 Python：$PythonExe。请用 -PythonExe 指定 Python 3.13 路径。"
}
if (-not (Test-Path (Join-Path $FfmpegDir "ffmpeg.exe"))) {
    throw "找不到 ffmpeg：$FfmpegDir\ffmpeg.exe"
}
if (-not (Test-Path (Join-Path $FfmpegDir "ffprobe.exe"))) {
    throw "找不到 ffprobe：$FfmpegDir\ffprobe.exe"
}
if (-not (Test-Path $GitRoot)) {
    throw "找不到 Git：$GitRoot"
}

Write-Host "== 1/8 准备构建环境 =="
New-Item -ItemType Directory -Force $Build | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Root "dist") | Out-Null
if (-not (Test-Path $VenvPy)) {
    & $PythonExe -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "创建构建 venv 失败" }
}
& $VenvPy -m pip install --disable-pip-version-check -U pip
if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }
& $VenvPy -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "安装构建依赖失败" }

Write-Host "== 2/8 PyInstaller 打包 =="
Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" |
    Where-Object { $_.CommandLine -like "*$Dist*edge-profile*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
if (Test-Path (Join-Path $Build "pyi")) { Remove-Item -Recurse -Force (Join-Path $Build "pyi") }
Push-Location $Root
try {
    & (Join-Path $Venv "Scripts\pyinstaller.exe") --noconfirm --clean `
        --distpath (Join-Path $Root "dist") --workpath (Join-Path $Build "pyi") `
        (Join-Path $Root "VideoRecreate.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }
} finally {
    Pop-Location
}

Write-Host "== 3/8 复制应用源码 =="
New-Item -ItemType Directory -Force $App | Out-Null
Copy-Item (Join-Path $Root "app.py") (Join-Path $App "app.py") -Force
Copy-Tree (Join-Path $Root "core") (Join-Path $App "core") @("__pycache__") @("*.pyc")
Copy-Tree (Join-Path $Root "assets") (Join-Path $App "assets")
Copy-Tree (Join-Path $Root ".streamlit") (Join-Path $App ".streamlit")
Copy-Tree (Join-Path $Root ".streamlit") (Join-Path $Dist ".streamlit")

Write-Host "== 4/8 复制外部工具 =="
New-Item -ItemType Directory -Force $Bin | Out-Null
Copy-Item (Join-Path $FfmpegDir "ffmpeg.exe") (Join-Path $Bin "ffmpeg.exe") -Force
Copy-Item (Join-Path $FfmpegDir "ffprobe.exe") (Join-Path $Bin "ffprobe.exe") -Force

$YtCache = Join-Path $Build "yt-dlp.exe"
if ((Test-Path $YtCache) -and ((Get-Item $YtCache).Length -gt 5MB)) {
    Write-Host "使用已下载的 yt-dlp：$YtCache"
    Copy-Item $YtCache (Join-Path $Bin "yt-dlp.exe") -Force
} else {
    $YtUrl = if ($YtDlpVersion -eq "latest") {
        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    } else {
        "https://github.com/yt-dlp/yt-dlp/releases/download/$YtDlpVersion/yt-dlp.exe"
    }
    Download-File $YtUrl (Join-Path $Bin "yt-dlp.exe")
    if ((Get-Item (Join-Path $Bin "yt-dlp.exe")).Length -lt 5MB) {
        Write-Host "指定版本的 yt-dlp 过小，改用最新版"
        Download-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $Bin "yt-dlp.exe")
    }
}
& (Join-Path $Bin "yt-dlp.exe") --version
if ($LASTEXITCODE -ne 0) { throw "yt-dlp 校验失败" }

$PyZip = Join-Path $Build "python-embed.zip"
if (-not (Test-Path $PyZip) -or (Get-Item $PyZip).Length -lt 5MB) {
    Download-File "https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip" $PyZip
}
Expand-Archive -Path $PyZip -DestinationPath (Join-Path $Runtime "python") -Force
Copy-Item (Join-Path $Runtime "python\python.exe") (Join-Path $Runtime "python\python3.exe") -Force

Copy-Tree $GitRoot (Join-Path $Runtime "git") @("dev", "tmp") @(
    "unins000.exe", "unins000.dat", "unins000.msg",
    "git-bash.exe", "git-cmd.exe", "ReleaseNotes.html"
)

Copy-Tree "$env:USERPROFILE\.claude\skills\video-downloader" `
    (Join-Path $Runtime "skills\video-downloader") @(".git", "__pycache__") @("*.pyc")
Copy-Tree "$env:USERPROFILE\.claude\skills\video-original-dedup" `
    (Join-Path $Runtime "skills\video-original-dedup") @(".git", "__pycache__") @("*.pyc")
Copy-Tree "$env:USERPROFILE\.claude\skills\video-recreation-skill" `
    (Join-Path $Runtime "skills\video-recreation-skill") @(".git", "__pycache__") @("*.pyc")

New-Item -ItemType Directory -Force (Join-Path $Runtime "models") | Out-Null

if ($IncludeWhisperModel) {
    Write-Host "== 附带 faster-whisper-small 模型 =="
    $env:HF_HOME = Join-Path $Build "hf"
    $env:HF_HUB_DISABLE_XET = "1"
    $ModelDir = Join-Path $Runtime "models\faster-whisper-small"
    New-Item -ItemType Directory -Force $ModelDir | Out-Null
    & $VenvPy -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir=r'$ModelDir')"
    if ($LASTEXITCODE -ne 0) { throw "下载 Whisper 模型失败" }
}

Write-Host "== 5/8 准备 data 目录 =="
foreach ($dir in "outputs", "logs", "tmp", "home", "webview", "models") {
    New-Item -ItemType Directory -Force (Join-Path $Data $dir) | Out-Null
}
if (-not $ExcludeHistory -and (Test-Path (Join-Path $Root "outputs"))) {
    Copy-Tree (Join-Path $Root "outputs") (Join-Path $Data "outputs") @("_smoke", "__pycache__") @("*.pyc")
}

Write-Host "== 6/8 写入说明与许可 =="
Copy-Item (Join-Path $Root "packaging\使用说明.txt") $Dist -Force
Copy-Item (Join-Path $Root "packaging\THIRD_PARTY_NOTICES.txt") $Dist -Force
$Lic = Join-Path $Dist "LICENSES"
New-Item -ItemType Directory -Force $Lic | Out-Null
Copy-Item (Join-Path $Root "packaging\LICENSES\*") $Lic -Force
Copy-Item "$env:USERPROFILE\.claude\skills\video-downloader\LICENSE" (Join-Path $Lic "video-downloader-MIT.txt") -Force
Copy-Item "$env:USERPROFILE\.claude\skills\video-original-dedup\LICENSE" (Join-Path $Lic "video-original-dedup-MIT.txt") -Force
if (Test-Path "C:\Program Files\Git\LICENSE.txt") {
    Copy-Item "C:\Program Files\Git\LICENSE.txt" (Join-Path $Lic "Git-GPLv2.txt") -Force
}
if (Test-Path (Join-Path $Runtime "python\LICENSE.txt")) {
    Copy-Item (Join-Path $Runtime "python\LICENSE.txt") (Join-Path $Lic "Python-PSF.txt") -Force
}

if (-not $SkipSelfTest) {
    Write-Host "== 7/8 最小 PATH 自检 =="
    $oldPath = $env:PATH
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        $selfTest = Start-Process -FilePath (Join-Path $Dist "VideoRecreate.exe") `
            -ArgumentList @("--self-test", "--full") -PassThru -Wait -WindowStyle Hidden
        $selfTestCode = $selfTest.ExitCode
    } finally {
        $env:PATH = $oldPath
    }
    if ($selfTestCode -ne 0) {
        Get-Content (Join-Path $Data "logs\self-test.txt") -ErrorAction SilentlyContinue
        throw "自检失败，退出码 $selfTestCode"
    }

    Write-Host "健康检查..."
    $serve = Start-Process -FilePath (Join-Path $Dist "VideoRecreate.exe") `
        -ArgumentList @("--serve", "--port", "8799") -PassThru -WindowStyle Hidden
    try {
        $healthy = $false
        for ($i = 0; $i -lt 90; $i++) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 `
                    -Uri "http://127.0.0.1:8799/_stcore/health"
                if ($response.StatusCode -eq 200) { $healthy = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $healthy) { throw "健康检查失败" }
    } finally {
        Get-CimInstance Win32_Process -Filter "Name = 'VideoRecreate.exe'" |
            Where-Object { $_.CommandLine -like "*--port*8799*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "== 清理构建日志 =="
Start-Sleep -Milliseconds 500
Get-ChildItem (Join-Path $Data "logs") -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $Data "tmp") -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Data ".write_test") -Force -ErrorAction SilentlyContinue

if (-not $NoZip) {
    Write-Host "== 8/8 生成 zip =="
    $ZipPath = Join-Path $Root "dist\VideoRecreate-1.0.0.zip"
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    & $VenvPy -X utf8 (Join-Path $Root "packaging\zip_dist.py") $Dist $ZipPath
    if ($LASTEXITCODE -ne 0) { throw "zip 打包失败" }
    Get-FileHash $ZipPath -Algorithm SHA256 | Format-List
}

Write-Host "完成：$Dist"
