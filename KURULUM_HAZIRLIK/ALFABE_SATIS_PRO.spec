# -*- mode: python ; coding: utf-8 -*-
# PyInstaller tarifi: tek klasörlü (onedir) kurulum paketi. Antivirüs uyarısı onefile'a göre çok daha az olur.
import os
from pathlib import Path
KOK=Path(SPECPATH).resolve().parent
GIRIS=os.environ.get('ALFABE_GIRIS') or 'ALFABE_SATIS_PRO.py'
a=Analysis([str(KOK/GIRIS)],pathex=[str(KOK)],binaries=[],
  datas=[(str(KOK/'alfabe_modern_header.png'),'.'),(str(KOK/'alfabe.ico'),'.'),(str(KOK/'ALFABE_KULLANIM_KILAVUZU.pdf'),'.')],
  hiddenimports=['ALFABE_YARDIM','ALFABE_BELGE_ALIS','ALFABE_LISTE_FIYAT','ALFABE_EDGE_SERVIS','ALFABE_ENVANTER','ALFABE_ETIKET_IS','pypdf','ALFABE_URUN_BILGI','ALFABE_SAYIM','ALFABE_CIHAZ','ALFABE_AYAR_KAYIT','ALFABE_AYAR_LISTE','ALFABE_STOK_EK_OZELLIKLER','ALFABE_ASISTAN','ALFABE_EFATURA','ALFABE_PERSONEL','ALFABE_OKUL_LISTE','ALFABE_KONSINYE','ALFABE_IRSALIYE','ALFABE_OKC_MUTABAKAT','ALFABE_FOTOKOPI','ALFABE_KUR','ALFABE_PANO','ALFABE_KITAP_GIRIS','ALFABE_GUNLUK','ALFABE_FIS_OKU','ALFABE_POS_TAKIP','ALFABE_IBAN','ALFABE_OZEL_RAPOR','ALFABE_ASISTAN2','ALFABE_ASISTAN_BILGI','ALFABE_UYARI','ALFABE_PENCERE','ALFABE_ISBN','ALFABE_KITAP_RAPOR','ALFABE_POS_HESAP','ALFABE_DUYURU','ALFABE_MUSTERI_EKRANI','ALFABE_STOK_MALIYET_KONTROL','ALFABE_STOK_EXCEL','ALFABE_VERI_AKTARIM','ALFABE_YEDEK_BAKIM','ALFABE_RAPOR_EK','ALFABE_GUVENLIK','ALFABE_FIYAT_YONETIMI','ALFABE_MUHASEBE','PIL.Image'],hookspath=[],runtime_hooks=[],
  excludes=['numpy','pandas','matplotlib','scipy','PIL','IPython','pytest'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ALFABE_SATIS_PRO',debug=False,strip=False,upx=False,
  console=False,icon=str(KOK/'alfabe.ico'),version=str(Path(SPECPATH)/'version_info.txt'))
coll=COLL(exe,a.binaries,a.datas,strip=False,upx=False,name='ALFABE_SATIS_PRO')
