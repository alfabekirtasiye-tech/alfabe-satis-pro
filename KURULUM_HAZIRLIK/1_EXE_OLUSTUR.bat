@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem === Ana program dosyasinin adi (surumden bagimsizdir; degistirmeniz gerekmez) ===
set ALFABE_GIRIS=ALFABE_SATIS_PRO.py

echo [1/4] Python ve tkinter kontrol ediliyor...
python --version || (echo HATA: Python bulunamadi. python.org'dan 64 bit Python kurun ^(tcl/tk secili^). & pause & exit /b 1)
python -c "import tkinter" || (echo HATA: tkinter yok. Python kurulumunda "tcl/tk and IDLE" secenegini isaretleyin. & pause & exit /b 1)

echo [2/4] PyInstaller kuruluyor/guncelleniyor...
python -m pip install --upgrade pyinstaller || (echo HATA: PyInstaller kurulamadi. & pause & exit /b 1)

echo [3/4] Surum bilgisi hazirlaniyor...
python hazirla.py "%ALFABE_GIRIS%" || (pause & exit /b 1)

echo [4/4] EXE paketi olusturuluyor ^(birkac dakika surebilir^)...
python -m PyInstaller --noconfirm --clean --distpath "..\dist" --workpath "..\build" ALFABE_SATIS_PRO.spec || (echo HATA: Paket olusturulamadi. & pause & exit /b 1)

echo.
echo TAMAM. Once su dosyayi calistirip deneyin:
echo   %~dp0..\dist\ALFABE_SATIS_PRO\ALFABE_SATIS_PRO.exe
echo Sorun yoksa 2_KURULUM_OLUSTUR.bat ile setup dosyasini uretin.
pause
