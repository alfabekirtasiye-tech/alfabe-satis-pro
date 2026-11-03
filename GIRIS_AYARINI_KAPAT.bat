@echo off
rem ALFABE SATIS PRO - giris ayarini kapatma (kurtarma) baslatici. Program KAPALIYKEN calistirin.
cd /d "%~dp0"
chcp 65001 >nul
for %%V in (3.12 3.13 3.11) do (
  py -%%V -c "import sys" >nul 2>&1 && ( py -%%V "GIRIS_AYARINI_KAPAT.py" & goto :bitti )
)
python "GIRIS_AYARINI_KAPAT.py"
:bitti
