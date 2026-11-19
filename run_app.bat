@echo off
rem Start the Amazon Dataset Builder frontend in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats false
