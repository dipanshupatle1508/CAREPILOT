@echo off
REM ---------------------------------------------------------------
REM  Starts the dashboard (server.py) with a LOCAL Ollama model.
REM  Needs: .venv created + packages installed (see steps), Ollama running,
REM         and the model pulled once:   ollama pull qwen3:8b
REM  Different model? Change MODEL below.
REM  Offline rehearsal (no AI):  set MOCK_LLM=1 before running python server.py
REM ---------------------------------------------------------------
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

set MODEL=qwen3:8b

set MOCK_LLM=0
set LLM_BASE_URL=http://localhost:11434/v1
set FAST_MODEL=%MODEL%
set REASONING_MODEL=%MODEL%
set LLM_TIMEOUT=180

python server.py %*
pause
