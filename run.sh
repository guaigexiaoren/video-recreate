#!/usr/bin/env bash
export PATH="$HOME/tools/bin:$HOME/.local/bin:/c/Users/YYMY/AppData/Local/Programs/Python/Python313/Scripts:$PATH"
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
exec "C:/Users/YYMY/AppData/Local/Programs/Python/Python313/python.exe" -m streamlit run app.py
