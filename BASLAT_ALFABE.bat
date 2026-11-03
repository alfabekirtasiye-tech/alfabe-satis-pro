@echo off
rem ALFABE SATIS PRO baslatici: Tcl/Tk 8.6 iceren Python (3.12 / 3.13 / 3.11) ile acar; yoksa varsayilan Python ile dener.
cd /d "%~dp0"
set "PRG=ALFABE_SATIS_PRO.py"
for %%V in (3.12 3.13 3.11) do (
  py -%%V -c "import sys" >nul 2>&1 && ( start "" py -%%V "%PRG%" & exit /b 0 )
)
start "" python "%PRG%"
