# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - CİHAZLAR & ENTEGRASYONLAR altyapısı (v1.2.291.453)

Ekrandan bağımsız, sınanabilir mantık: cihaz türleri, adres çözümleme, GERÇEK bağlantı testleri,
varsayılan cihaz çözümü, yazıcı hatırlama, para çekmecesi (ESC/POS) ve test baskısı verisi.
Program ana dosyası bind() ile veritabanı ve ayar işlevlerini bağlar.

Test sonucu üç durumdan biridir:
  'Bağlı'          cihaza / klasöre / porta gerçekten erişildi
  'Bağlı Değil'    erişilemedi (ayrıntı metni nedeni söyler)
  'Doğrulanamadı'  bu bilgisayarda otomatik doğrulanamaz (ör. klavye modundaki okuyucu, Windows dışı yazıcı listesi)
"""
import os
import re
import socket
import threading
import time

KINDS = ['Fiş Yazıcı', 'Etiket Yazıcı', 'A4/Rapor Yazıcı', 'Barkod Okuyucu', 'Para Çekmecesi', 'Yazarkasa/POS',
         'NAS', 'Çoklu Bilgisayar/Ağ', 'Diğer Cihaz']
PRINTER_KINDS = ('Fiş Yazıcı', 'Etiket Yazıcı', 'A4/Rapor Yazıcı')
# yazıcı türü -> bu türün varsayılanını taşıyan ayar anahtarı
PRINTER_SETTING = {'Fiş Yazıcı': 'receipt_printer', 'Etiket Yazıcı': 'label_printer', 'A4/Rapor Yazıcı': 'report_printer'}
CONNS = ['Otomatik', 'Windows Yazıcı', 'USB', 'TCP/IP', 'Seri Port', 'Klavye Modu', 'Ağ Klasörü', 'Yerel Ağ']
CONNS_BY_KIND = {
    'Fiş Yazıcı': ['Windows Yazıcı', 'USB', 'TCP/IP', 'Seri Port'],
    'Etiket Yazıcı': ['Windows Yazıcı', 'USB', 'TCP/IP', 'Seri Port'],
    'A4/Rapor Yazıcı': ['Windows Yazıcı', 'USB', 'TCP/IP'],
    'Barkod Okuyucu': ['Klavye Modu', 'USB', 'Seri Port'],
    'Para Çekmecesi': ['Windows Yazıcı', 'TCP/IP', 'Seri Port'],
    'Yazarkasa/POS': ['TCP/IP', 'Seri Port', 'USB', 'Klavye Modu'],
    'NAS': ['Ağ Klasörü', 'Yerel Ağ'],
    'Çoklu Bilgisayar/Ağ': ['Yerel Ağ', 'Ağ Klasörü', 'TCP/IP'],
    'Diğer Cihaz': CONNS,
}
DEFAULT_PORT = {'Fiş Yazıcı': 9100, 'Etiket Yazıcı': 9100, 'A4/Rapor Yazıcı': 9100, 'Para Çekmecesi': 9100,
                'Çoklu Bilgisayar/Ağ': 445}
ADDRESS_HINT = {
    'Windows Yazıcı': 'Windows\'taki yazıcı adı (boş bırakılırsa cihaz adı kullanılır)',
    'USB': 'Windows\'taki yazıcı adı (USB cihaz Windows sürücüsüyle görünür)',
    'TCP/IP': 'IP veya ad ve port. Örnek: 192.168.1.50:9100',
    'Seri Port': 'Port ve hız. Örnek: COM3 veya COM3:9600',
    'Klavye Modu': 'Gerekmez (okuyucu klavye gibi yazar). Okutma testi ile doğrulanır.',
    'Ağ Klasörü': 'Klasör yolu. Örnek: \\\\NAS\\yedek veya Z:\\yedek',
    'Yerel Ağ': 'Bilgisayar adı / IP veya paylaşım klasörü. Örnek: KASA-2 veya \\\\KASA-2\\ALFABE',
    'Otomatik': 'Adresten tür tahmin edilir (klasör, IP:port, COM, yazıcı adı)',
}
STATUS_OK, STATUS_BAD, STATUS_UNK, STATUS_DEF, STATUS_OFF = 'Bağlı', 'Bağlı Değil', 'Doğrulanamadı', 'Tanımlı', 'Pasif'

ESC_DRAWER = b'\x1b\x70\x00\x19\xfa'          # ESC p 0 25 250 : çekmece 1'e darbe
ESC_INIT = b'\x1b\x40'
ESC_CUT = b'\x1d\x56\x42\x00'

_S = {'dx': None, 'ayar': None, 'evet': None, 'tam': None, 'yaz': None, 'prn': None, 'now': None, 'yok': 'Windows Varsayılan Yazıcı'}


def bind(dx, ayar, ayar_evet, ayar_tam, ayar_yaz, windows_printers, now, yok='Windows Varsayılan Yazıcı'):
    _S.update(dx=dx, ayar=ayar, evet=ayar_evet, tam=ayar_tam, yaz=ayar_yaz, prn=windows_printers, now=now, yok=yok)


def _dx():
    return _S['dx']


def workstation():
    try:
        return str(_dx().get('workstation', 'KASA-1') or 'KASA-1')
    except Exception:
        return 'KASA-1'


# ----------------------------------------------------------------------------- adres çözümleme
def looks_like_path(a):
    a = str(a or '').strip()
    return bool(a) and (a.startswith('\\\\') or a.startswith('//') or re.match(r'^[A-Za-z]:[\\/]', a) is not None
                        or a.startswith('/') or a.startswith('~'))


def parse_host_port(addr, default_port=0):
    """'192.168.1.5:9100' -> ('192.168.1.5', 9100). Port yoksa default_port. Geçersizse (None, hata)."""
    a = str(addr or '').strip()
    if not a:
        return None, 'Adres boş.'
    host, port = a, default_port
    if a.count(':') == 1:
        host, p = a.split(':')
        if not p.isdigit():
            return None, 'Port sayı olmalıdır (örn. 192.168.1.50:9100).'
        port = int(p)
    host = host.strip()
    if not host:
        return None, 'Adres boş.'
    if not port:
        return None, 'Port belirtin (örn. 192.168.1.50:9100).'
    if not 1 <= int(port) <= 65535:
        return None, 'Port 1-65535 arasında olmalıdır.'
    return (host, int(port)), ''


def parse_serial(addr):
    """'COM3:9600' -> ('COM3', 9600). Hız yoksa 9600."""
    a = str(addr or '').strip().upper()
    if not a:
        return None, 'Port adı boş (örn. COM3).'
    p, _, b = a.partition(':')
    if not re.match(r'^(COM\d{1,3}|/DEV/\S+)$', p):
        return None, 'Port adı COM3 gibi olmalıdır.'
    if b and not b.isdigit():
        return None, 'Hız sayı olmalıdır (örn. COM3:9600).'
    return (p if p.startswith('COM') else p.lower().replace('/dev/', '/dev/'), int(b) if b else 9600), ''


def effective_conn(kind, conn, addr):
    """'Otomatik' / 'USB' bağlantıyı gerçek test yöntemine çevirir."""
    c = str(conn or 'Otomatik').strip()
    a = str(addr or '').strip()
    if c in ('Windows Yazıcı', 'TCP/IP', 'Seri Port', 'Klavye Modu', 'Ağ Klasörü'):
        return c
    if c == 'USB':
        return 'Windows Yazıcı' if kind in PRINTER_KINDS + ('Para Çekmecesi',) else 'Klavye Modu'
    if c == 'Yerel Ağ':
        return 'Ağ Klasörü' if looks_like_path(a) else 'TCP/IP'
    # Otomatik
    if looks_like_path(a):
        return 'Ağ Klasörü'
    if re.match(r'^(COM\d+|/dev/\S+)(:\d+)?$', a, re.I):
        return 'Seri Port'
    if re.match(r'^[\w.\-]+:\d{1,5}$', a) or re.match(r'^\d{1,3}(\.\d{1,3}){3}$', a):
        return 'TCP/IP'
    if kind in PRINTER_KINDS + ('Para Çekmecesi',):
        return 'Windows Yazıcı'
    if kind == 'NAS':
        return 'Ağ Klasörü'
    return 'Klavye Modu' if kind == 'Barkod Okuyucu' else 'Otomatik'


def printer_name_of(r):
    """Windows yazıcı adı: adres doluysa o, yoksa cihaz adı."""
    return (str(r['address'] or '').strip() or str(r['name'] or '').strip())


# ----------------------------------------------------------------------------- gerçek testler
def _t_folder(path, timeout):
    t0 = time.time()
    p = os.path.expandvars(os.path.expanduser(str(path or '').strip()))
    if not p:
        return STATUS_BAD, 'Klasör yolu boş.'
    box = {}

    def work():
        try:
            if not os.path.isdir(p):
                box['r'] = (STATUS_BAD, 'Klasör bulunamadı: ' + p + '. Yolu ve Windows erişim izinlerini kontrol edin.')
                return
            probe = os.path.join(p, '.alfabe_test_%d.tmp' % os.getpid())
            try:
                with open(probe, 'w', encoding='utf-8') as f:
                    f.write('alfabe')
                os.remove(probe)
            except Exception as e:
                box['r'] = (STATUS_BAD, 'Klasör açılıyor ama yazılamıyor (izin / salt okunur): ' + str(e))
                return
            box['r'] = (STATUS_OK, 'Klasöre erişildi ve yazma denendi (%d ms): %s' % (int((time.time() - t0) * 1000), p))
        except Exception as e:
            box['r'] = (STATUS_BAD, 'Klasör denenirken hata: ' + str(e))
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(max(1, timeout))
    return box.get('r') or (STATUS_BAD, 'Klasör %d sn içinde yanıt vermedi (ağ kopuk veya NAS kapalı olabilir): %s' % (timeout, p))


def _t_tcp(addr, default_port, timeout):
    hp, err = parse_host_port(addr, default_port)
    if not hp:
        return STATUS_BAD, err
    host, port = hp
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=max(1, timeout))
        s.close()
        return STATUS_OK, '%s:%d portuna bağlanıldı (%d ms).' % (host, port, int((time.time() - t0) * 1000))
    except socket.gaierror:
        return STATUS_BAD, '"%s" adı çözümlenemedi. Adı / IP\'yi kontrol edin.' % host
    except socket.timeout:
        return STATUS_BAD, '%s:%d %d sn içinde yanıt vermedi. Cihaz kapalı ya da ağ dışı olabilir.' % (host, port, timeout)
    except OSError as e:
        return STATUS_BAD, '%s:%d bağlantı reddedildi / ulaşılamadı: %s' % (host, port, e)


def _t_serial(addr):
    sp, err = parse_serial(addr)
    if not sp:
        return STATUS_BAD, err
    port, baud = sp
    try:
        if os.name == 'nt':
            import ctypes
            k = ctypes.windll.kernel32
            k.CreateFileW.restype = ctypes.c_void_p
            h = k.CreateFileW('\\\\.\\' + port, 0xC0000000, 0, None, 3, 0, None)
            if h in (None, ctypes.c_void_p(-1).value, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                code = ctypes.get_last_error() or k.GetLastError()
                return STATUS_BAD, '%s açılamadı (hata %s). Port yok ya da başka program kullanıyor.' % (port, code)
            k.CloseHandle(ctypes.c_void_p(h))
            return STATUS_OK, '%s portu açılabildi (%d bps ayarlı).' % (port, baud)
        if os.path.exists(port):
            return STATUS_OK, '%s portu mevcut (%d bps ayarlı).' % (port, baud)
        return STATUS_BAD, '%s portu bulunamadı.' % port
    except Exception as e:
        return STATUS_BAD, 'Seri port denenemedi: ' + str(e)


def _t_windows_printer(r):
    name = printer_name_of(r)
    if not name:
        return STATUS_BAD, 'Yazıcı adı boş.'
    if os.name != 'nt':
        return STATUS_UNK, 'Windows dışında yazıcı listesi okunamaz; "%s" Windows\'ta test edilmelidir.' % name
    try:
        names = _S['prn'](True) or []
    except Exception:
        names = []
    if not names:
        return STATUS_UNK, 'Windows yazıcı listesi okunamadı; yazıcı adı doğrulanamadı.'
    low = {n.casefold(): n for n in names}
    if name.casefold() in low:
        return STATUS_OK, 'Yazıcı Windows\'ta kurulu: ' + low[name.casefold()]
    return STATUS_BAD, '"%s" Windows yazıcı listesinde yok. Kurulu yazıcılar: %s' % (name, ', '.join(names[:6]) + (' …' if len(names) > 6 else ''))


def test_device(r, timeout=None):
    """(durum, ayrıntı). Yalnız test eder; kaydı yazmaz."""
    try:
        timeout = int(timeout or _S['tam']('device_test_timeout') or 3)
    except Exception:
        timeout = 3
    kind = str(r['kind'] or '')
    conn = effective_conn(kind, r['connection_type'], r['address'])
    addr = str(r['address'] or '').strip()
    if conn == 'Ağ Klasörü':
        return _t_folder(addr, timeout)
    if conn == 'TCP/IP':
        return _t_tcp(addr, DEFAULT_PORT.get(kind, 0), timeout)
    if conn == 'Seri Port':
        return _t_serial(addr)
    if conn == 'Windows Yazıcı':
        return _t_windows_printer(r)
    if conn == 'Klavye Modu':
        return STATUS_UNK, 'Klavye modundaki cihaz otomatik sınanamaz. "OKUTMA TESTİ" ile bir barkod okutarak doğrulayın.'
    return STATUS_UNK, 'Bu bağlantı türü için otomatik test yok; bağlantı türünü / adresi netleştirin.'


# ----------------------------------------------------------------------------- kayıt işlemleri
def log(i, kind, status, detail):
    try:
        _dx().exec('INSERT INTO integration_events(dt,integration_id,kind,status,detail,workstation) VALUES(?,?,?,?,?,?)',
                   (_S['now'](), i, kind, status, str(detail)[:500], workstation()))
    except Exception:
        pass


def save_result(r, status, detail, event='Bağlantı Testi'):
    ok = status != STATUS_BAD
    _dx().exec('UPDATE integrations SET status=?,last_test=?,last_error=? WHERE id=?',
               (status, _S['now'](), '' if ok else detail, r['id']))
    log(r['id'], event, {STATUS_OK: 'Başarılı', STATUS_BAD: 'Hata'}.get(status, 'Bilgi'), detail)


def test_and_save(r, timeout=None):
    st, d = test_device(r, timeout)
    save_result(r, st, d)
    return st, d


def test_all(timeout=None, progress=None):
    """Aktif tüm cihazları sınar, kaydeder. [(satır, durum, ayrıntı)]"""
    out = []
    for r in _dx().rows('SELECT * FROM integrations WHERE active=1 ORDER BY kind,name'):
        st, d = test_and_save(r, timeout)
        out.append((r, st, d))
        if progress:
            try:
                progress(r, st, d)
            except Exception:
                pass
    return out


def problems():
    return [r for r in _dx().rows('SELECT * FROM integrations WHERE active=1') if (r['status'] or '') in (STATUS_BAD, 'Hata')]


def make_default(r):
    """Aynı tür + aynı iş istasyonunda tek varsayılan. Yazıcı / NAS ise ilgili ayarı da eşitler."""
    d = _dx()
    d.exec('UPDATE integrations SET is_default=0 WHERE kind=? AND COALESCE(workstation,\'\')=COALESCE(?,\'\')', (r['kind'], r['workstation']))
    d.exec('UPDATE integrations SET is_default=1,active=1 WHERE id=?', (r['id'],))
    log(r['id'], 'Varsayılan', 'Başarılı', r['name'])
    msg = ''
    kind = r['kind']
    try:
        if kind in PRINTER_SETTING and effective_conn(kind, r['connection_type'], r['address']) == 'Windows Yazıcı':
            _S['yaz'](PRINTER_SETTING[kind], printer_name_of(r), 'Bu Bilgisayar', 'Yazıcı & Baskı')
            msg = 'Bu bilgisayarda varsayılan %s: %s' % (kind.lower(), printer_name_of(r))
        elif kind == 'NAS' and str(r['address'] or '').strip():
            _S['yaz']('nas_backup_path', str(r['address']).strip(), 'Firma', 'Yedekleme')
            msg = 'İkinci yedek (NAS) konumu güncellendi: ' + str(r['address']).strip()
    except Exception as e:
        msg = 'Varsayılan işaretlendi ama ayara yazılamadı: ' + str(e)
    return msg


def clear_default_setting(r):
    """Varsayılan cihaz silinir / pasif yapılırsa eşleşen ayar da temizlenir (kalıntı kalmasın)."""
    kind = r['kind']
    try:
        if kind in PRINTER_SETTING and str(_S['ayar'](PRINTER_SETTING[kind]) or '').strip().casefold() == printer_name_of(r).casefold():
            _S['yaz'](PRINTER_SETTING[kind], _S['yok'], 'Bu Bilgisayar', 'Yazıcı & Baskı')
    except Exception:
        pass


def default_for(kind):
    """Bu iş istasyonunun etkin varsayılan cihazı (yoksa tek etkin cihaz); yoksa None."""
    ws = workstation().casefold()
    rows = [r for r in _dx().rows('SELECT * FROM integrations WHERE kind=? AND active=1 ORDER BY is_default DESC,id', (kind,))
            if (str(r['workstation'] or '').strip().casefold() in (ws, '', '*', 'tümü'))]
    if not rows:
        return None
    if rows[0]['is_default']:
        return rows[0]
    return rows[0] if len(rows) == 1 else None


def printer_for(kind):
    """Yazıcı adı: önce ilgili ayar (Bu Bilgisayar > ... > Sistem), yoksa cihaz varsayılanı, yoksa ''."""
    key = PRINTER_SETTING.get(kind)
    v = ''
    try:
        v = str(_S['ayar'](key) or '').strip() if key else ''
    except Exception:
        v = ''
    if v and v.casefold() != _S['yok'].casefold():
        return v
    try:
        if _S['evet']('device_use_defaults'):
            r = default_for(kind)
            if r and effective_conn(kind, r['connection_type'], r['address']) == 'Windows Yazıcı':
                return printer_name_of(r)
    except Exception:
        pass
    return ''


def remember_printer(kind, name):
    """Kullanıcı bir yazdırma penceresinde yazıcı seçti: ayara yaz + cihaz tanımı oluştur / varsayılan yap."""
    name = str(name or '').strip()
    if not name or name.casefold() == _S['yok'].casefold() or kind not in PRINTER_SETTING:
        return
    try:
        _S['yaz'](PRINTER_SETTING[kind], name, 'Bu Bilgisayar', 'Yazıcı & Baskı')
        ws = workstation()
        rows = _dx().rows('SELECT * FROM integrations WHERE kind=? AND COALESCE(workstation,\'\')=?', (kind, ws))
        hit = next((x for x in rows if printer_name_of(x).casefold() == name.casefold()), None)
        if hit is None:
            i = _dx().exec('INSERT INTO integrations(kind,name,workstation,status,note,active,is_default,connection_type,address,last_test,last_error) '
                           'VALUES(?,?,?,?,?,1,0,?,?,?,?)',
                           (kind, name, ws, STATUS_DEF, 'Yazdırma ekranından otomatik eklendi', 'Windows Yazıcı', name, '', ''))
            hit = _dx().one('SELECT * FROM integrations WHERE id=?', (i,))
            log(i, 'Tanım', 'Başarılı', 'Otomatik eklendi: ' + name)
        if not hit['is_default'] or not hit['active']:
            _dx().exec('UPDATE integrations SET is_default=0 WHERE kind=? AND COALESCE(workstation,\'\')=?', (kind, ws))
            _dx().exec('UPDATE integrations SET is_default=1,active=1 WHERE id=?', (hit['id'],))
    except Exception:
        pass


def nas_fallback():
    """İkinci yedek konumu ayarı boşsa varsayılan NAS cihazının klasörü."""
    try:
        if _S['evet']('device_use_defaults'):
            r = default_for('NAS')
            if r and str(r['address'] or '').strip():
                return str(r['address']).strip()
    except Exception:
        pass
    return ''


def prune_events():
    try:
        days = int(_S['tam']('device_event_keep_days') or 0)
        if days > 0:
            import datetime
            cut = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
            _dx().exec('DELETE FROM integration_events WHERE dt<?', (cut,))
    except Exception:
        pass


# ----------------------------------------------------------------------------- ham gönderim (fiş yazıcı / çekmece)
def _send_tcp(addr, data, kind, timeout):
    hp, err = parse_host_port(addr, DEFAULT_PORT.get(kind, 0))
    if not hp:
        return False, err
    try:
        s = socket.create_connection(hp, timeout=max(1, timeout))
        try:
            s.sendall(data)
        finally:
            s.close()
        return True, '%s:%d adresine gönderildi.' % hp
    except Exception as e:
        return False, 'Gönderilemedi: ' + str(e)


def _send_serial(addr, data):
    sp, err = parse_serial(addr)
    if not sp:
        return False, err
    port = sp[0]
    try:
        path = ('\\\\.\\' + port) if os.name == 'nt' else port
        with open(path, 'wb', buffering=0) as f:
            f.write(data)
        return True, port + ' portuna gönderildi.'
    except Exception as e:
        return False, 'Seri porta yazılamadı: ' + str(e)


def _send_windows_raw(printer, data, doc='ALFABE'):
    if os.name != 'nt':
        return False, 'Windows dışında ham yazdırma yapılamaz.'
    try:
        import ctypes
        from ctypes import wintypes
        sp = ctypes.WinDLL('winspool.drv', use_last_error=True)

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR), ('pDatatype', wintypes.LPWSTR)]
        h = wintypes.HANDLE()
        if not sp.OpenPrinterW(printer, ctypes.byref(h), None):
            return False, '"%s" yazıcısı açılamadı (hata %s).' % (printer, ctypes.get_last_error())
        try:
            di = DOC_INFO_1(doc, None, 'RAW')
            if not sp.StartDocPrinterW(h, 1, ctypes.byref(di)):
                return False, 'Yazdırma işi başlatılamadı (hata %s).' % ctypes.get_last_error()
            try:
                sp.StartPagePrinter(h)
                w = wintypes.DWORD(0)
                ok = sp.WritePrinter(h, data, len(data), ctypes.byref(w))
                sp.EndPagePrinter(h)
            finally:
                sp.EndDocPrinter(h)
            return (True, '"%s" yazıcısına gönderildi (%d bayt).' % (printer, w.value)) if ok else (False, 'Yazıcıya yazılamadı (hata %s).' % ctypes.get_last_error())
        finally:
            sp.ClosePrinter(h)
    except Exception as e:
        return False, 'Ham yazdırma hatası: ' + str(e)


def send_raw(r, data, timeout=None):
    """Cihaza ham bayt gönderir (ESC/POS). (ok, ayrıntı)"""
    try:
        timeout = int(timeout or _S['tam']('device_test_timeout') or 3)
    except Exception:
        timeout = 3
    kind = str(r['kind'] or '')
    conn = effective_conn(kind, r['connection_type'], r['address'])
    if conn == 'TCP/IP':
        return _send_tcp(r['address'], data, kind, timeout)
    if conn == 'Seri Port':
        return _send_serial(r['address'], data)
    if conn == 'Windows Yazıcı':
        return _send_windows_raw(printer_name_of(r), data)
    return False, 'Bu bağlantı türüyle ham komut gönderilemez (%s).' % conn


def drawer_open(r=None):
    """Para çekmecesini açar. r verilmezse bu bilgisayarın varsayılan çekmecesi. (ok, ayrıntı)"""
    r = r or default_for('Para Çekmecesi')
    if r is None:
        return False, 'Bu bilgisayar için tanımlı para çekmecesi yok.'
    ok, d = send_raw(r, ESC_INIT + ESC_DRAWER)
    log(r['id'], 'Çekmece Aç', 'Başarılı' if ok else 'Hata', d)
    return ok, d


def drawer_open_async(r=None):
    """Satış akışını bekletmeden çekmeceyi açar; hata satışı etkilemez."""
    threading.Thread(target=lambda: drawer_open(r), daemon=True).start()


def test_receipt_text(r):
    ln = ['', 'ALFABE SATIS PRO', 'CIHAZ TEST FISI', '-' * 32, 'Cihaz: ' + str(r['name'] or '')[:24],
          'Tur: ' + str(r['kind'] or ''), 'Zaman: ' + _S['now'](), '-' * 32, 'Turkce: ABCCDEFGGHIIJKLMNOOPRSSTUUVYZ', 'Test basarili ise bu fis okunur.', '', '', '']
    return ESC_INIT + '\n'.join(ln).encode('cp437', 'replace') + b'\n' + ESC_CUT


def test_page_html(r):
    import html
    rows = ''.join('<tr><td>%s</td><td>%s</td></tr>' % (html.escape(a), html.escape(str(b or '-'))) for a, b in (
        ('Cihaz', r['name']), ('Tür', r['kind']), ('Bağlantı', r['connection_type']), ('Adres', r['address']), ('İş istasyonu', r['workstation']), ('Zaman', _S['now']())))
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Cihaz Test Sayfası</title><style>'
            'body{font-family:Segoe UI,Arial;margin:30px}h1{font-size:22px;color:#c00000}table{border-collapse:collapse;margin:14px 0}'
            'td{border:1px solid #000;padding:6px 14px;font-size:14px}.t{font-size:18px;letter-spacing:2px}</style></head><body>'
            '<h1>ALFABE SATIŞ PRO - Cihaz Test Sayfası</h1><table>%s</table>'
            '<p class="t">ÇĞİÖŞÜ çğıöşü 0123456789 ₺</p><p>Bu sayfa okunaklı çıktıysa yazıcı bağlantısı ve Türkçe karakterler doğrudur.</p>'
            '<div style="border:2px solid #000;width:300px;height:60px;margin-top:10px">Kenar boşluğu / ölçek kontrolü</div></body></html>') % rows


def scan_verdict(text, intervals_ms):
    """Okutma testi: hızlı ve yeterince uzun giriş okuyucudur; yavaş giriş elle yazılmış sayılır."""
    t = str(text or '').strip()
    if len(t) < 4:
        return STATUS_UNK, 'Çok kısa giriş (%d karakter). Bir ürün barkodunu okutun.' % len(t)
    if not intervals_ms:
        return STATUS_UNK, 'Hız ölçülemedi; barkodu yeniden okutun.'
    avg = sum(intervals_ms) / len(intervals_ms)
    if avg <= 80:
        return STATUS_OK, 'Okuyucu algılandı: %d karakter, karakter başına ortalama %d ms.' % (len(t), avg)
    return STATUS_UNK, 'Giriş yavaş (ortalama %d ms/karakter): elle yazılmış olabilir. Barkodu okuyucuyla tekrar okutun.' % avg
