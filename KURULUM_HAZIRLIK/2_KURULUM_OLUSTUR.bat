@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "..\dist\ALFABE_SATIS_PRO\ALFABE_SATIS_PRO.exe" (echo HATA: Once 1_EXE_OLUSTUR.bat calistirin. & pause & exit /b 1)
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" (echo HATA: Inno Setup 6 bulunamadi. https://jrsoftware.org/isdl.php adresinden kurun. & pause & exit /b 1)
"%ISCC%" alfabe_setup.iss || (echo HATA: Setup olusturulamadi. & pause & exit /b 1)
echo.
echo TAMAM. Setup dosyasi: %~dp0..\KURULUM_CIKTI
pause
