@echo off
set "PATH=%USERPROFILE%\tools\bin;%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%PATH%"
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m streamlit run app.py
