# -*- coding: utf-8 -*-
"""Sürüm numarasını programdan okur; exe sürüm bilgisini ve Inno Setup sürüm dosyasını üretir.
Kullanım: python hazirla.py <ana_program_dosyasi.py>   (tek sürüm kaynağı: ana dosyadaki VERSION=...)"""
import re,sys
from pathlib import Path
kok=Path(__file__).resolve().parent.parent
giris=kok/(sys.argv[1] if len(sys.argv)>1 else 'ALFABE_SATIS_PRO.py')
t=giris.read_text(encoding='utf-8-sig')
m=re.search(r"^APP\s*=.*?VERSION\s*=\s*'([^']+)'",t,re.M)
if not m:sys.exit('HATA: ana dosyada VERSION bulunamadı: '+str(giris))
tam=m.group(1);sayilar=re.match(r'(\d+)\.(\d+)\.(\d+)\.(\d+)',tam)
if not sayilar:sys.exit('HATA: VERSION "A.B.C.D ..." ile başlamalı: '+tam)
a,b,c,d=map(int,sayilar.groups());num=f'{a}.{b}.{c}.{d}'
bilgi=f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a},{b},{c},{d}),prodvers=({a},{b},{c},{d}),mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),
  kids=[
    StringFileInfo([StringTable('041F04B0',[
      StringStruct('CompanyName','ALFABE KIRTASİYE'),
      StringStruct('FileDescription','ALFABE SATIŞ PRO'),
      StringStruct('FileVersion','{num}'),
      StringStruct('InternalName','ALFABE_SATIS_PRO'),
      StringStruct('OriginalFilename','ALFABE_SATIS_PRO.exe'),
      StringStruct('ProductName','ALFABE SATIŞ PRO'),
      StringStruct('ProductVersion','{tam}')])]),
    VarFileInfo([VarStruct('Translation',[1055,1200])])
  ]
)
"""
(Path(__file__).resolve().parent/'version_info.txt').write_text(bilgi,encoding='utf-8')
(Path(__file__).resolve().parent/'surum.iss').write_text(f'#define AppVersion "{num}"\n#define AppVersionText "{tam}"\n',encoding='utf-8')
print('Surum:',tam,'->',num)
