# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - KURTARMA BETİĞİ
'Açılışta kullanıcı adı / şifre sor' ayarını HAYIR yapar (şifresi olmayan kullanıcı yüzünden firmaya girilemiyorsa).
Yaptığı tek değişiklik budur. Önce seçilen firmanın veri dosyalarının yedeğini alır.
Kullanım: ALFABE SATIŞ PRO KAPALIYKEN GIRIS_AYARINI_KAPAT.bat dosyasına çift tıklayın.
"""
import os, sys, shutil, runpy, datetime
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
KLASOR = os.path.dirname(os.path.abspath(__file__))
os.chdir(KLASOR); sys.path.insert(0, KLASOR)

def cik(msg=''):
    if msg:
        print(msg)
    try:
        input('\nKapatmak için ENTER tuşuna basın...')
    except Exception:
        pass
    sys.exit(0)

print('ALFABE SATIŞ PRO - Giriş ayarı kurtarma\n')
try:
    g = runpy.run_path(os.path.join(KLASOR, 'ALFABE_SATIS_PRO.py'), run_name='alfabe_kurtarma')
except Exception as e:
    cik('Program dosyası yüklenemedi (ALFABE SATIŞ PRO açık olabilir, kapatıp tekrar deneyin):\n' + str(e))

try:
    reg = g['_bootstrap_company_registry']()
    print('Tanımlı firmalar:')
    for no, v in sorted(reg.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 999999):
        print(f'  {no}  -  {v.get("name") or ("Firma " + str(no))}')
    no = input('\nGiriş ayarı kapatılacak firmanın NUMARASINI yazıp ENTER\'a basın: ').strip()
    if no not in {str(k) for k in reg}:
        cik('Böyle bir firma yok. Hiçbir şey değiştirilmedi.')
    if not g['_activate_company'](no):
        cik('Firma açılamadı. Hiçbir şey değiştirilmedi.')
    base = reg[no].get('base') or str(g['_DATA_BASE'])
    periods = sorted(g['_period_registry_load']().items())
    files = []
    main_db = os.path.join(base, 'alfabe_v1_2_31.db')
    if os.path.exists(main_db):
        files.append(main_db)
    for per, rec in periods:
        p = str((rec or {}).get('path') or '')
        if p and os.path.exists(p):
            files.append(p)
    print(f'\nFirma {no}: {len(periods)} dönem bulundu. Önce {len(files)} veri dosyasının yedeği alınacak,')
    print('sonra yalnız "Açılışta kullanıcı adı / şifre sor" ayarı HAYIR yapılacak. Başka hiçbir şeye dokunulmaz.')
    if input('Devam etmek için EVET yazın: ').strip().upper() not in ('EVET', 'E'):
        cik('Vazgeçildi. Hiçbir şey değiştirilmedi.')

    yedek = os.path.join(base, 'yedekler', 'GIRIS_AYARI_ONCESI_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(yedek, exist_ok=True)
    for i, f in enumerate(files, 1):
        shutil.copy2(f, os.path.join(yedek, f'{i:02d}_' + os.path.basename(f)))
    print('\nYedek alındı:', yedek)

    ayar_yaz, DX = g['ayar_yaz'], g['DX']
    hedefler = [None] + [per for per, _ in periods]      # None = firma ana veri dosyası
    for per in hedefler:
        if per is not None and not g['_switch_period_db'](per):
            print(f'  Dönem {per}: açılamadı, atlandı.')
            continue
        if per is None:
            g['_activate_company'](no)
        ayar_yaz('login_required', 'Hayır', 'Firma', 'Güvenlik', 'Kurtarma betiği')
        r = DX.one("SELECT v FROM scoped_settings WHERE scope='Firma' AND k='login_required' ORDER BY id DESC LIMIT 1")
        print(f'  {"Ana dosya" if per is None else "Dönem " + str(per)}: açılışta şifre sor = {r["v"] if r else "?"}')
    cik('\nTamam. Şimdi BASLAT_ALFABE.bat ile programı açabilirsiniz.')
except SystemExit:
    raise
except Exception as e:
    cik('HATA: ' + str(e) + '\nYedek alındıysa veriler olduğu gibi duruyor; hata mesajını bana iletin.')
