@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ALFABE SATIS PRO - ACILIS TESHISI
echo  Program bu pencereden acilacak. 1 dakika bekleyin.
echo  Program acilmazsa veya bos pencere kalirsa o pencereyi kapatin; bu pencere teshis dosyalarini hazirlar.
echo.
set "PYV="
for %%V in (3.12 3.13 3.11) do (
  if not defined PYV py -%%V -c "import sys" >nul 2>&1 && set "PYV=%%V"
)
if defined PYV (
  py -%PYV% -X faulthandler ALFABE_SATIS_PRO.py > ACILIS_TESHIS_CIKTI.txt 2>&1
) else (
  python -X faulthandler ALFABE_SATIS_PRO.py > ACILIS_TESHIS_CIKTI.txt 2>&1
)
copy /y "%TEMP%\ALFABE_ACILIS_IZI.log" "ACILIS_IZI.txt" >nul 2>&1
echo Python surumu: %PYV% >> ACILIS_TESHIS_CIKTI.txt
py --list >> ACILIS_TESHIS_CIKTI.txt 2>&1
echo.
echo  Bitti. Bu klasordeki su iki dosyayi gonderin:
echo    ACILIS_IZI.txt
echo    ACILIS_TESHIS_CIKTI.txt
explorer "%~dp0"
pause
