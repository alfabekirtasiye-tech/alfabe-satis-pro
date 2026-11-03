# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Günlük araçlar (v414)
 1) Açılış özeti        : kritik stok, vadesi gelen/geçen çek-senet-taksit, borçlu cariler, yedek durumu
 2) Veri sağlık taraması: çift barkod, eksi stok, maliyetsiz/fiyatsız ürün, yetim kayıt, veritabanı bütünlüğü
 3) Yedek kontrolü      : son yedek var mı, kaç günlük, paket sağlam mı, içindeki veritabanı açılıyor mu
 4) Yetki raporu        : hangi kullanıcı hangi ekranı görüyor / kritik işlem yapabiliyor
 5) Yardım & kısayollar : F12
Hepsi YALNIZ OKUR; hiçbir kayıt değiştirilmez (tek istisna: "açılışta göster" ayarı).
"""
import datetime
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

CLOSED_CHECK = ('Tahsil', 'İptal', 'Ödendi')
SETTING_START = 'daily_summary_on_start'


def _start_on(app, con):
    # Ana program Ayarlar'daki "startup_health" ayarını bağlar; bağlı değilse eski özel ayar kullanılır.
    f = getattr(app, '_alf_start_get', None)
    if f:
        try:
            return bool(f())
        except Exception:
            pass
    return get_setting(con, SETTING_START, '1') != '0'


def _start_set(app, con, on):
    f = getattr(app, '_alf_start_set', None)
    if f:
        try:
            f(bool(on))
            return
        except Exception:
            pass
    set_setting(con, SETTING_START, '1' if on else '0')
BACKUP_WARN_DAYS = 2
BACKUP_BAD_DAYS = 7


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _money(v):
    s = '{:,.2f}'.format(_f(v))
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _date(s):
    t = str(s or '')[:10]
    try:
        y, m, d = t.split('-')
        return '%s.%s.%s' % (d, m, y)
    except Exception:
        return t


def _q(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def get_setting(c, key, default=''):
    r = _q(c, 'SELECT v FROM settings WHERE k=?', (key,))
    return r[0][0] if r else default


def set_setting(c, key, val):
    c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)', (key, str(val)))
    c.commit()


# ----------------------------------------------------------------------------------------------
# Yedek
# ----------------------------------------------------------------------------------------------
def list_backups(back_dir, nas_dir=''):
    out = []
    for d in (back_dir, nas_dir):
        if not d:
            continue
        d = Path(d)
        if not d.exists():
            continue
        try:
            for p in d.iterdir():
                n = p.name.upper()
                if p.is_file() and n.startswith('ALFABE') and p.suffix.lower() in ('.zip', '.bck'):
                    out.append(p)
        except Exception:
            pass
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def backup_age(back_dir, nas_dir='', now=None):
    """Hızlı kontrol: (son yedek yolu | None, gün farkı | None)."""
    bs = list_backups(back_dir, nas_dir)
    if not bs:
        return None, None
    now = now or datetime.datetime.now()
    age = (now - datetime.datetime.fromtimestamp(bs[0].stat().st_mtime)).total_seconds() / 86400.0
    return bs[0], age


def verify_backup(path, live_db=None):
    """Yedek paketini sınar. Dönüş: dict(ok, steps=[(ad, durum, ayrıntı)]). Canlı veritabanına dokunmaz."""
    steps = []
    ok = True

    def add(name, good, detail=''):
        nonlocal ok
        steps.append((name, 'TAMAM' if good else 'HATA', detail))
        ok = ok and good
    p = Path(path)
    if not p.exists():
        add('Dosya', False, 'Bulunamadı')
        return dict(ok=False, steps=steps)
    add('Dosya', p.stat().st_size > 0, '%s, %.1f MB, %s' % (p.name, p.stat().st_size / 1048576.0,
                                                         datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%d.%m.%Y %H:%M')))
    tmp = Path(tempfile.mkdtemp(prefix='alfabe_ykt_'))
    try:
        try:
            z = zipfile.ZipFile(p)
        except Exception as ex:
            add('Paket açılıyor', False, str(ex))
            return dict(ok=False, steps=steps)
        with z:
            bad = z.testzip()
            add('Paket bütünlüğü (ZIP)', bad is None, '' if bad is None else 'Bozuk: ' + bad)
            names = z.namelist()
            has_db = 'alfabe_veritabani.db' in names
            add('Veritabanı dosyası pakette', has_db)
            if not has_db:
                return dict(ok=False, steps=steps)
            z.extract('alfabe_veritabani.db', tmp)
            if 'manifest.json' in names:
                try:
                    man = json.loads(z.read('manifest.json').decode('utf-8'))
                    h = hashlib.sha256((tmp / 'alfabe_veritabani.db').read_bytes()).hexdigest()
                    want = (man.get('sha256') or {}).get('alfabe_veritabani.db')
                    add('SHA256 doğrulama', (not want) or want == h, 'Firma: %s Dönem: %s' % (man.get('company', ''), man.get('period', '')))
                except Exception as ex:
                    add('SHA256 doğrulama', False, str(ex))
            else:
                steps.append(('SHA256 doğrulama', 'ATLANDI', 'Eski tip yedek (manifest yok)'))
        dbp = tmp / 'alfabe_veritabani.db'
        try:
            c = sqlite3.connect(str(dbp))
            r = c.execute('PRAGMA quick_check').fetchone()
            add('Veritabanı açılıyor ve bütün', bool(r and str(r[0]).lower() == 'ok'), str(r[0]) if r else '')
            ns = c.execute('SELECT COUNT(*) FROM stock').fetchone()[0]
            ncari = c.execute('SELECT COUNT(*) FROM caris').fetchone()[0]
            nsale = c.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
            add('Kayıtlar okunuyor', True, 'Stok %d, Cari %d, Satış %d' % (ns, ncari, nsale))
            c.close()
            if live_db and Path(live_db).exists():
                lc = sqlite3.connect('file:%s?mode=ro' % Path(live_db).as_posix(), uri=True)
                lsale = lc.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
                lc.close()
                steps.append(('Canlı veriyle karşılaştırma', 'BİLGİ', 'Yedekten sonra %d yeni satış belgesi var' % max(lsale - nsale, 0) if lsale >= nsale
                              else 'Canlı veritabanında yedekten %d eksik satış var (dikkat)' % (nsale - lsale)))
        except Exception as ex:
            add('Veritabanı açılıyor', False, str(ex))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dict(ok=ok, steps=steps)


# ----------------------------------------------------------------------------------------------
# Açılış özeti
# ----------------------------------------------------------------------------------------------
def collect_summary(c, can=lambda m: True, back_dir=None, nas_dir='', today=None, horizon=7):
    """Bölüm listesi döndürür. Her bölüm: key,title,module,count,headline,level(ok|warn|bad),columns,rows."""
    today = today or datetime.date.today()
    t_iso = today.isoformat()
    h_iso = (today + datetime.timedelta(days=horizon)).isoformat()
    secs = []
    if can('Stok Merkezi'):
        rows = _q(c, "SELECT code,name,qty,min_qty FROM stock WHERE active=1 AND (qty<0 OR (min_qty>0 AND qty<=min_qty)) ORDER BY (qty-min_qty) LIMIT 300")
        neg = _q(c, "SELECT COUNT(*) FROM stock WHERE active=1 AND qty<0")
        neg = int(neg[0][0]) if neg else 0      # v470: 300 satırlık listeden değil, tüm negatif kartlardan sayılır
        n = _q(c, "SELECT COUNT(*) FROM stock WHERE active=1 AND (qty<0 OR (min_qty>0 AND qty<=min_qty))")
        n = int(n[0][0]) if n else 0
        secs.append(dict(key='stock', title='Kritik stok', module='Stok Merkezi', count=n,
                         headline=('%d ürün kritik seviyede' % n) + (' (%d negatif)' % neg if neg else '') if n else 'Kritik stok yok',
                         level='bad' if neg else ('warn' if n else 'ok'),
                         columns=['Kod', 'Ürün', 'Mevcut', 'Minimum', 'Durum'],
                         rows=[[r[0] or '', r[1], '%g' % _f(r[2]), '%g' % _f(r[3]), 'NEGATİF' if _f(r[2]) < 0 else 'Minimum altı'] for r in rows[:100]], num=[2, 3]))
    if can('Finans Merkezi') or can('Cari Merkezi'):
        due = []
        for r in _q(c, "SELECT kind,number,customer,due_date,amount,status FROM checks_notes WHERE status NOT IN ('Tahsil','İptal','Ödendi') "
                       "AND COALESCE(due_date,'')<>'' AND substr(due_date,1,10)<=? ORDER BY due_date", (h_iso,)):
            d = str(r[3])[:10]
            due.append((d, r[0] or 'Çek/Senet', r[2] or '', _f(r[4]), r[1] or ''))
        for r in _q(c, "SELECT i.due_date,i.amount-i.paid,COALESCE(c.title,'') FROM installments i LEFT JOIN caris c ON c.id=i.cari_id "
                       "WHERE COALESCE(i.status,'')!='Ödendi' AND i.amount-i.paid>0.0001 AND COALESCE(i.due_date,'')<>'' AND substr(i.due_date,1,10)<=? ORDER BY i.due_date", (h_iso,)):
            due.append((str(r[0])[:10], 'Taksit', r[2], _f(r[1]), ''))
        due.sort()
        late = [x for x in due if x[0] < t_iso]
        tot = sum(x[3] for x in due)
        secs.append(dict(key='due', title='Vadesi gelen / geçen', module='Finans Merkezi', count=len(due),
                         headline=('%d kayıt, %s ₺ (%d tanesi vadesi GEÇMİŞ)' % (len(due), _money(tot), len(late))) if due else '%d gün içinde vade yok' % horizon,
                         level='bad' if late else ('warn' if due else 'ok'),
                         columns=['Vade', 'Tür', 'Cari / Borçlu', 'Tutar', 'Durum'],
                         rows=[[_date(x[0]), x[1] + (' ' + x[4] if x[4] else ''), x[2], _money(x[3]), 'GEÇMİŞ' if x[0] < t_iso else ('BUGÜN' if x[0] == t_iso else 'Yaklaşıyor')] for x in due[:200]], num=[3]))
    if can('Finans Merkezi') or can('Banka Merkezi'):
        pr = _q(c, "SELECT dt,pos_name,gross,commission,net,due_date FROM pos_settlements WHERE status='Bekliyor' AND due_date<=? ORDER BY due_date,pos_name", (t_iso,))
        if pr or _q(c, "SELECT 1 FROM pos_settlements WHERE status='Bekliyor' LIMIT 1"):
            tot = sum(_f(r[4]) for r in pr)
            secs.append(dict(key='pos', title='POS: bankaya geçmesi beklenen', module='Banka Merkezi', count=len(pr),
                             headline=('%d günlük kart satışı bankaya geçmiş olmalı: %s ₺' % (len(pr), _money(tot))) if pr else 'Yatış günü gelen POS satışı yok',
                             level='warn' if pr else 'ok',
                             columns=['Satış günü', 'POS', 'Brüt', 'Komisyon', 'Net', 'Beklenen yatış'],
                             rows=[[_date(r[0]), r[1], _money(r[2]), _money(r[3]), _money(r[4]), _date(r[5])] for r in pr[:200]], num=[2, 3, 4]))
    if can('Finans Merkezi') or can('Banka Merkezi'):
        ib = _q(c, "SELECT s.doc_no,substr(s.dt,1,10),sp.bank_account,sp.ref,sp.amount FROM sale_payments sp JOIN sales s ON s.id=sp.sale_id "
                   "WHERE sp.kind='Havale/IBAN' AND COALESCE(sp.verified,0)=0 AND sp.amount>0 AND COALESCE(s.status,'FINAL')<>'CANCEL' ORDER BY s.dt")
        if ib:
            tot = sum(_f(r[4]) for r in ib)
            secs.append(dict(key='iban', title='Havale/IBAN: bankada görülmesi beklenen', module='Banka Merkezi', count=len(ib),
                             headline='%d havale henüz bankada doğrulanmadı: %s ₺' % (len(ib), _money(tot)),
                             level='warn', columns=['Belge', 'Tarih', 'Banka hesabı', 'Açıklama / Ref', 'Tutar'],
                             rows=[[r[0], _date(r[1]), r[2], r[3], _money(r[4])] for r in ib[:200]], num=[4]))
    if can('Cari Merkezi') or can('Finans Merkezi'):
        rows = _q(c, "SELECT c.title,COALESCE(c.phone,''),SUM(m.debit)-SUM(m.credit) bal,MAX(substr(m.dt,1,10)) FROM caris c JOIN cari_moves m ON m.cari_id=c.id "
                     "WHERE c.active=1 GROUP BY c.id HAVING bal>0.005 ORDER BY bal DESC")
        tot = sum(_f(r[2]) for r in rows)
        secs.append(dict(key='debtors', title='Borçlu cariler', module='Cari Merkezi', count=len(rows),
                         headline=('%d cari, toplam alacak %s ₺' % (len(rows), _money(tot))) if rows else 'Borçlu cari yok',
                         level='warn' if rows else 'ok',
                         columns=['Cari', 'Telefon', 'Bakiye (borç)', 'Son hareket'],
                         rows=[[r[0], r[1], _money(r[2]), _date(r[3])] for r in rows[:100]], num=[2]))
    if can('Ayarlar') or can('Sistem Merkezi'):
        if back_dir is not None:
            p, age = backup_age(back_dir, nas_dir)
            if p is None:
                secs.append(dict(key='backup', title='Yedek durumu', module='Ayarlar', count=1, headline='HİÇ YEDEK BULUNAMADI', level='bad',
                                 columns=['Bilgi'], rows=[['Yedek klasöründe yedek yok. Ayarlar > Yedekleme bölümünden yedek alın.']], num=[]))
            else:
                lvl = 'bad' if age > BACKUP_BAD_DAYS else ('warn' if age > BACKUP_WARN_DAYS else 'ok')
                secs.append(dict(key='backup', title='Yedek durumu', module='Ayarlar', count=0 if lvl == 'ok' else 1,
                                 headline='Son yedek %s (%s)' % (datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%d.%m.%Y %H:%M'),
                                                                 'bugün' if age < 1 else '%d gün önce' % int(age)), level=lvl,
                                 columns=['Dosya', 'Boyut'], rows=[[p.name, '%.1f MB' % (p.stat().st_size / 1048576.0)]], num=[]))
    return secs


# ----------------------------------------------------------------------------------------------
# Veri sağlık taraması
# ----------------------------------------------------------------------------------------------
def health_scan(c, back_dir=None, nas_dir='', deep=False):
    """Sorun listesi: dict(sev=HATA|UYARI|BİLGİ, cat, detail, kind=stock|cari|None, ref)."""
    out = []

    def add(sev, cat, detail, kind=None, ref=None):
        out.append(dict(sev=sev, cat=cat, detail=detail, kind=kind, ref=ref))
    # çift barkod
    own = {}
    for r in _q(c, "SELECT id,TRIM(barcode),name FROM stock WHERE active=1 AND TRIM(COALESCE(barcode,''))<>''"):
        own.setdefault(r[1], []).append((r[0], r[2]))
    for r in _q(c, "SELECT sb.stock_id,TRIM(sb.barcode),s.name FROM stock_barcodes sb JOIN stock s ON s.id=sb.stock_id WHERE sb.active=1 AND s.active=1 AND TRIM(COALESCE(sb.barcode,''))<>''"):
        own.setdefault(r[1], []).append((r[0], r[2]))
    for bc, lst in own.items():
        ids = {i for i, _ in lst}
        if len(ids) > 1:
            add('HATA', 'Çift barkod', 'Barkod %s: %s' % (bc, ' / '.join(sorted({n for _, n in lst}))[:120]), 'stock', min(ids))
    # aynı ad
    seen = {}
    for r in _q(c, "SELECT id,name FROM stock WHERE active=1"):
        k = ''.join(ch for ch in str(r[1]).lower() if ch.isalnum())
        if k:
            seen.setdefault(k, []).append(r[0])
    for k, ids in seen.items():
        if len(ids) > 1:
            nm = _q(c, 'SELECT name FROM stock WHERE id=?', (ids[0],))[0][0]
            add('UYARI', 'Aynı adlı kart', '%s — %d ayrı kart var' % (nm, len(ids)), 'stock', ids[0])
    for r in _q(c, "SELECT id,name,qty FROM stock WHERE active=1 AND qty<0 ORDER BY qty LIMIT 500"):
        add('HATA', 'Eksi stok', '%s: %g adet' % (r[1], _f(r[2])), 'stock', r[0])
    for r in _q(c, "SELECT id,name,qty FROM stock WHERE active=1 AND qty>0 AND COALESCE(avg_cost,0)<=0 AND COALESCE(last_cost,0)<=0 ORDER BY name LIMIT 500"):
        add('UYARI', 'Maliyeti sıfır', '%s: stokta %g adet, maliyet yok (kâr hesabı yanlış çıkar)' % (r[1], _f(r[2])), 'stock', r[0])
    for r in _q(c, "SELECT id,name FROM stock WHERE active=1 AND COALESCE(retail,0)<=0 ORDER BY name LIMIT 500"):
        add('UYARI', 'Satış fiyatı yok', r[1], 'stock', r[0])
    for r in _q(c, "SELECT id,name,retail,avg_cost,COALESCE(vat,0) FROM stock WHERE active=1 AND COALESCE(retail,0)>0 AND COALESCE(avg_cost,0)>0 "
                   "AND retail/(1+COALESCE(vat,0)/100.0) < avg_cost-0.005 ORDER BY name LIMIT 500"):
        add('UYARI', 'Zararına satış fiyatı', '%s: satış (KDV hariç) %s < maliyet %s' % (r[1], _money(_f(r[2]) / (1 + _f(r[4]) / 100.0)), _money(r[3])), 'stock', r[0])
    n = _q(c, "SELECT COUNT(*) FROM stock WHERE active=1 AND TRIM(COALESCE(barcode,''))=''")
    if n and n[0][0]:
        add('BİLGİ', 'Barkodsuz ürün', '%d aktif üründe barkod yok' % n[0][0])
    # yetim kayıtlar
    for sql, cat, lab in (
            ("SELECT COUNT(*) FROM sale_lines l LEFT JOIN sales s ON s.id=l.sale_id WHERE s.id IS NULL", 'Yetim kayıt', 'satış satırı (belgesi yok)'),
            ("SELECT COUNT(*) FROM stock_moves m LEFT JOIN stock s ON s.id=m.stock_id WHERE s.id IS NULL", 'Yetim kayıt', 'stok hareketi (kartı yok)'),
            ("SELECT COUNT(*) FROM cari_moves m LEFT JOIN caris c ON c.id=m.cari_id WHERE c.id IS NULL", 'Yetim kayıt', 'cari hareketi (carisi yok)')):
        r = _q(c, sql)
        if r and r[0][0]:
            add('HATA', cat, '%d adet %s' % (r[0][0], lab))
    # çok geçmiş vadeler
    lim = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    r = _q(c, "SELECT COUNT(*),COALESCE(SUM(amount),0) FROM checks_notes WHERE status NOT IN ('Tahsil','İptal','Ödendi') AND COALESCE(due_date,'')<>'' AND substr(due_date,1,10)<?", (lim,))
    if r and r[0][0]:
        add('UYARI', '30+ gün geçmiş vade', '%d çek/senet, %s ₺ — hâlâ açık' % (r[0][0], _money(r[0][1])))
    # kapanmamış satış belgeleri (taslak vb. değil: sadece sayım)
    # veritabanı bütünlüğü
    try:
        r = c.execute('PRAGMA quick_check').fetchone()
        if not r or str(r[0]).lower() != 'ok':
            add('HATA', 'Veritabanı', 'Bütünlük denetimi başarısız: %s' % (r[0] if r else '?'))
    except sqlite3.Error as ex:
        add('HATA', 'Veritabanı', str(ex))
    if back_dir is not None:
        p, age = backup_age(back_dir, nas_dir)
        if p is None:
            add('HATA', 'Yedek', 'Hiç yedek bulunamadı')
        elif age > BACKUP_BAD_DAYS:
            add('HATA', 'Yedek', 'Son yedek %d gün önce' % int(age))
        elif age > BACKUP_WARN_DAYS:
            add('UYARI', 'Yedek', 'Son yedek %d gün önce' % int(age))
    order = {'HATA': 0, 'UYARI': 1, 'BİLGİ': 2}
    out.sort(key=lambda x: (order[x['sev']], x['cat']))
    return out


# ----------------------------------------------------------------------------------------------
# Yetki raporu
# ----------------------------------------------------------------------------------------------
def permission_report(c, modules):
    """Dönüş: (kullanıcı_satırları, matris). Rol 'Yönetici' tüm ekranlara sahiptir (programın kuralı)."""
    perms = {}
    for r in _q(c, 'SELECT role,module,view,add_rec,edit,passive,print,critical FROM role_permissions'):
        perms[(r[0], r[1])] = dict(view=int(r[2] or 0), add=int(r[3] or 0), edit=int(r[4] or 0), passive=int(r[5] or 0), print=int(r[6] or 0), critical=int(r[7] or 0))

    def role_of(role, mod, key='view'):
        if role == 'Yönetici':
            return True
        p = perms.get((role, 'Rapor & Analiz' if mod == 'Yazdırma Merkezi' else mod))
        return bool(p and p[key])
    users = _q(c, 'SELECT username,COALESCE(role,\'\'),active FROM users ORDER BY username')
    urows = []
    for u, role, act in users:
        role = role or 'Personel'
        seen = [m for m in modules if role_of(role, m)]
        crit = [m for m in modules if role_of(role, m, 'critical')]
        edit = [m for m in modules if role_of(role, m, 'edit')]
        urows.append(dict(user=u, role=role, active=bool(act), see=seen, crit=crit, edit=edit, blocked=[m for m in modules if m not in seen]))
    roles = sorted({r[0] for r in _q(c, 'SELECT name FROM roles')} | {x[1] for x in users if x[1]} | {'Yönetici'})
    matrix = []
    for m in modules:
        row = [m]
        for ro in roles:
            if role_of(ro, m, 'critical'):
                row.append('KRİTİK')
            elif role_of(ro, m, 'edit'):
                row.append('Değiştirir')
            elif role_of(ro, m, 'add'):
                row.append('Ekler')
            elif role_of(ro, m, 'view'):
                row.append('Görür')
            else:
                row.append('—')
        matrix.append(row)
    return urows, (roles, matrix)


# ----------------------------------------------------------------------------------------------
# Yardım metinleri
# ----------------------------------------------------------------------------------------------
GENERAL_KEYS = [
    ('F12', 'Bu yardım penceresi (açık olan ekranın kısa kullanımı + tüm kısayollar)'),
    ('Ctrl+C', 'Tabloda seçili satırları kopyalar (Excel\'e yapıştırılabilir)'),
    ('Ctrl+Shift+C', 'Tablo başlıklarıyla birlikte kopyalar'),
    ('Sağ tık', 'Tabloda / metin kutusunda Kopyala - Yapıştır menüsü'),
    ('Ctrl+A', 'Metin kutusunda tümünü seçer'),
]
SCREEN_KEYS = {
    'Ana Sayfa': [('F2', 'Hızlı Satış'), ('F3', 'Cari Satış'), ('F4', 'Yeni Stok Kartı')],
    'Hızlı Satış': [('F1', 'Nakit ödeme'), ('F2', 'Kart / POS ödeme'), ('F3', 'Kaydet ve yazdır'), ('F7', 'Seçili satırın birim fiyatı'), ('F9', 'Ödeme'),
                    ('Delete', 'Seçili satırı sil'), ('Esc', 'Satışı iptal edip kapat')],
    'Cari Satış': [('F4', 'Cari seç'), ('F6', 'Kaydet'), ('F7', 'Birim fiyat değiştir'), ('F8', 'İskonto'), ('F10', 'Kaydet ve kapat'), ('F11', 'Stok ara')],
    'Etiket & Barkod': [('F9', 'Baskı önizleme (tam boy)')],
    'Alış & Mal Kabul': [('F7', 'Birim fiyat değiştir')],
}
SCREEN_HELP = {
    'Ana Sayfa': 'Günün durumu ve hızlı erişim. Soldaki menüden bölümlere geçersiniz. Açılış özetini istediğiniz an altta "Günlük Özet" düğmesiyle görebilirsiniz.',
    'Hızlı Satış': 'Barkodu okutun veya ürün adını yazın; satır sepete eklenir. Ödemeyi F1 (nakit), F2 (kart) ya da parçalı ödeme ile tamamlayın.',
    'Cari Satış': 'Önce cariyi seçin (F4), ürünleri ekleyin, F6 ile kaydedin. Vadeli satışlar cari hesaba işlenir.',
    'Stok Merkezi': 'Stok kartları, hareketler, sayım, Excel aktarım. Kitap girişi için "📚 KİTAP GİRİŞ", çok ürün için "EXCEL ▾ > İçe aktar" kullanın.',
    'Cari Merkezi': 'Müşteri/tedarikçi kartları, bakiye, ekstre ve tahsilat işlemleri.',
    'Finans Merkezi': 'Gelir/gider, çek-senet, taksit ve gün sonu işlemleri.',
    'Yazdırma Merkezi': 'Raporu soldan seçin, dönemi belirleyin; ön gösterimden sonra yazdırın, PDF veya Excel olarak kaydedin.',
    'AI Asistan': 'Soruyu Türkçe yazın (ör. "kritik stoklar", "borçlu müşteriler", "bu ay satış"). Sonucu Excel\'e aktarabilirsiniz.',
    'Etiket & Barkod': 'Ürünü seçin, etiket tasarımını belirleyin, adedi yazın, F9 ile önizleyip yazdırın.',
    'Ayarlar': 'Firma bilgileri, yedekleme ve genel tercihler.',
}


def tr_upper(s):
    return str(s).replace('i', 'İ').replace('ı', 'I').upper()


def help_for(module):
    return (SCREEN_HELP.get(module) or 'Bu ekran için kısa yardım metni henüz hazırlanmadı. Genel kısayollar aşağıdadır.'), SCREEN_KEYS.get(module, [])


# ----------------------------------------------------------------------------------------------
# Pencereler
# ----------------------------------------------------------------------------------------------
_COL = {'ok': ('#15803d', '#ecfdf3'), 'warn': ('#b45309', '#fffbeb'), 'bad': ('#b91c1c', '#fef2f2')}


def _top(parent, title, geom):
    import tkinter as tk
    w = tk.Toplevel(parent)
    w.title(title)
    try:
        import re as _re
        mm = _re.match(r'(\d+)x(\d+)', geom)
        if mm and not getattr(tk.Toplevel, '_ab_center', False):      # pencere kuralı (ALFABE_PENCERE) yoksa ekranın ortasında aç
            ww, hh = int(mm.group(1)), int(mm.group(2))
            sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
            ww, hh = min(ww, sw - 40), min(hh, sh - 80)
            geom = '%dx%d+%d+%d' % (ww, hh, max(0, (sw - ww) // 2), max(0, (sh - hh) // 2 - 20))
    except Exception:
        pass
    w.geometry(geom)
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    return w


def _tree(parent, columns, widths=None, num=(), height=10):
    import tkinter as tk
    from tkinter import ttk
    fr = tk.Frame(parent)
    t = ttk.Treeview(fr, columns=list(range(len(columns))), show='headings', height=height)
    for i, h in enumerate(columns):
        t.heading(i, text=h)
        t.column(i, width=(widths[i] if widths else 140), anchor='e' if i in num else 'w', stretch=True)
    sb = ttk.Scrollbar(fr, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    return fr, t


def open_daily_summary(parent, connection_factory, can=lambda m: True, back_dir=None, nas_dir='', go=None, startup=False):
    import tkinter as tk
    from tkinter import ttk
    con = connection_factory()
    w = _top(parent, 'Günlük Özet', '1040x680')
    try:
        w.transient('')      # v470: bağlı (transient) pencerede Windows küçült/büyüt düğmelerini gizler; bu pencerede Küçült / Büyüt / Kapat görünsün
        w.resizable(True, True)
    except Exception:
        pass
    tk.Label(w, text='GÜNLÜK ÖZET', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    sub = tk.Label(w, text='', font=('Segoe UI', 10), fg='#475569', bg='#f4f7fb')
    sub.pack(anchor='w', padx=18, pady=(0, 8))
    cards = tk.Frame(w, bg='#f4f7fb')
    cards.pack(fill='x', padx=14)
    nb = ttk.Notebook(w)
    nb.pack(fill='both', expand=True, padx=18, pady=10)
    foot = tk.Frame(w, bg='#f4f7fb')
    foot.pack(fill='x', padx=18, pady=(0, 12))
    app = parent.nametowidget('.')     # ana pencere (Ayarlar'a bağlı açılış ayarı onda durur)
    var = tk.BooleanVar(value=_start_on(app, con))

    def toggle():
        try:
            _start_set(app, con, var.get())
        except Exception:
            pass
    tk.Checkbutton(foot, text='Program açılırken bu özeti göster', variable=var, command=toggle, bg='#f4f7fb', font=('Segoe UI', 10)).pack(side='left')
    state = {'secs': []}

    def jump(module):
        # Özet penceresi ana ekranın önünde kaldığı için önce kapatılır, sonra ilgili bölüm açılır
        try:
            w.destroy()
        except Exception:
            pass
        go(module)

    def render():
        for ch in cards.winfo_children():
            ch.destroy()
        for tab in nb.tabs():
            nb.forget(tab)
        secs = collect_summary(con, can, back_dir, nas_dir)
        state['secs'] = secs
        sub.config(text=datetime.date.today().strftime('%d.%m.%Y') + ' — ' + ('Dikkat gerektiren konu var.' if any(s['level'] != 'ok' for s in secs) else 'Her şey yolunda görünüyor.'))
        if not secs:
            tk.Label(cards, text='Görüntülemeye yetkili olduğunuz özet bölümü yok.', bg='#f4f7fb').pack()
        for i, s in enumerate(secs):
            fg, bg = _COL[s['level']]
            card = tk.Frame(cards, bg=bg, highlightbackground=fg, highlightthickness=1, cursor='hand2')
            card.grid(row=0, column=i, sticky='nsew', padx=4, pady=2)
            cards.grid_columnconfigure(i, weight=1, uniform='c')
            l1 = tk.Label(card, text=s['title'].upper(), font=('Segoe UI', 9, 'bold'), fg=fg, bg=bg, wraplength=160, justify='left', cursor='hand2')
            l1.pack(anchor='w', padx=10, pady=(8, 0))
            l2 = tk.Label(card, text=s['headline'], font=('Segoe UI', 10, 'bold'), fg='#111827', bg=bg, wraplength=160, justify='left', cursor='hand2')
            l2.pack(anchor='w', padx=10, pady=(2, 8))

            def _fit(e, a=l1, b=l2):      # metin kartın genişliğine göre alt satıra sarılır, kesilmez
                n = max(90, e.width - 26)
                a.configure(wraplength=n)
                b.configure(wraplength=n)
            card.bind('<Configure>', _fit)
            if go:
                for wd in (card,) + tuple(card.winfo_children()):
                    wd.bind('<Button-1>', lambda e, m=s['module']: jump(m))
            fr, t = _tree(nb, s['columns'], num=s.get('num', ()))
            if go:
                t.bind('<Double-1>', lambda e, m=s['module']: jump(m))
            for r in s['rows']:
                t.insert('', 'end', values=r)
            nb.add(fr, text='%s (%d)' % (s['title'], s['count']) if s['count'] else s['title'])
    render()
    if go:
        def goto():
            i = nb.index(nb.select()) if nb.tabs() else -1
            if 0 <= i < len(state['secs']):
                jump(state['secs'][i]['module'])
        tk.Button(foot, text='SEÇİLİ BÖLÜME GİT', command=goto, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='right', padx=(6, 0))
    tk.Button(foot, text='YENİLE', command=render, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='right', padx=6)
    tk.Button(foot, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='right')
    w._gunluk = dict(state=state, var=var, nb=nb, cards=cards)
    return w


def open_health(parent, connection_factory, back_dir=None, nas_dir='', live_db=None, go_stock=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    con = connection_factory()
    w = _top(parent, 'Veri Sağlık Taraması', '1100x700')
    tk.Label(w, text='VERİ SAĞLIK TARAMASI', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    sub = tk.Label(w, text='', font=('Segoe UI', 10, 'bold'), bg='#f4f7fb')
    sub.pack(anchor='w', padx=18, pady=(0, 6))
    fr, t = _tree(w, ['Önem', 'Konu', 'Ayrıntı'], widths=[80, 170, 700], height=16)
    fr.pack(fill='both', expand=True, padx=18)
    t.tag_configure('HATA', foreground='#b91c1c')
    t.tag_configure('UYARI', foreground='#b45309')
    t.tag_configure('BİLGİ', foreground='#475569')
    st = {'issues': []}

    def scan():
        t.delete(*t.get_children())
        iss = health_scan(con, back_dir, nas_dir)
        st['issues'] = iss
        for i, x in enumerate(iss):
            t.insert('', 'end', iid=str(i), values=(x['sev'], x['cat'], x['detail']), tags=(x['sev'],))
        h = sum(1 for x in iss if x['sev'] == 'HATA')
        u = sum(1 for x in iss if x['sev'] == 'UYARI')
        sub.config(text=('%d hata, %d uyarı, %d bilgi.' % (h, u, len(iss) - h - u)) if iss else 'Sorun bulunamadı.', fg='#b91c1c' if h else ('#b45309' if u else '#15803d'))
    scan()
    box = tk.Text(w, height=6, font=('Consolas', 10), bg='white', relief='solid', bd=1)
    box.pack(fill='x', padx=18, pady=(8, 0))
    box.insert('1.0', 'Yedeğin sınanması için "SON YEDEĞİ SINA" düğmesine basın (canlı veriye dokunmaz).')
    box.config(state='disabled')

    def test_backup():
        bs = list_backups(back_dir, nas_dir) if back_dir is not None else []
        box.config(state='normal')
        box.delete('1.0', 'end')
        if not bs:
            box.insert('end', 'Yedek bulunamadı.')
        else:
            w.config(cursor='watch')
            w.update_idletasks()
            r = verify_backup(bs[0], live_db)
            w.config(cursor='')
            for n, s, d in r['steps']:
                box.insert('end', '%-9s %-34s %s\n' % (s, n, d))
            box.insert('end', '\nSONUÇ: ' + ('YEDEK SAĞLAM, GERİ YÜKLENEBİLİR.' if r['ok'] else 'YEDEKTE SORUN VAR — hemen yeni yedek alın.'))
        box.config(state='disabled')

    def open_sel(_=None):
        s = t.selection()
        if s and go_stock:
            x = st['issues'][int(s[0])]
            if x['kind'] == 'stock':
                go_stock(x['ref'])
    t.bind('<Double-1>', open_sel)
    foot = tk.Frame(w, bg='#f4f7fb')
    foot.pack(fill='x', padx=18, pady=10)
    tk.Button(foot, text='SON YEDEĞİ SINA', command=test_backup, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='left')
    tk.Button(foot, text='YENİDEN TARA', command=scan, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='left', padx=8)
    tk.Label(foot, text='Stok sorunlarına çift tıklayınca kart açılır. Ctrl+C ile liste kopyalanır.', bg='#f4f7fb', fg='#64748b').pack(side='left', padx=10)
    tk.Button(foot, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='right')
    w._saglik = dict(tree=t, state=st, box=box, test_backup=test_backup, open_sel=open_sel)
    return w


def open_permission_report(parent, connection_factory, modules):
    import tkinter as tk
    from tkinter import ttk
    con = connection_factory()
    urows, (roles, matrix) = permission_report(con, modules)
    w = _top(parent, 'Kullanıcı Yetki Raporu', '1180x700')
    tk.Label(w, text='KİM HANGİ EKRANI KULLANABİLİR?', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    note = 'Yönetici rolü tüm ekranlara tam yetkilidir. ' + ('Henüz kullanıcı tanımlı değil: program tek Yönetici olarak çalışır.' if not urows else '')
    tk.Label(w, text=note, bg='#f4f7fb', fg='#475569', font=('Segoe UI', 10)).pack(anchor='w', padx=18, pady=(0, 6))
    nb = ttk.Notebook(w)
    nb.pack(fill='both', expand=True, padx=18, pady=(0, 10))
    fr1, t1 = _tree(nb, ['Kullanıcı', 'Rol', 'Durum', 'Görebildiği ekran', 'Kritik işlem yapabildiği ekranlar', 'Göremediği ekranlar'], widths=[110, 90, 70, 90, 380, 380], num=(3,))
    for u in urows:
        t1.insert('', 'end', values=(u['user'], u['role'], 'Aktif' if u['active'] else 'PASİF', '%d / %d' % (len(u['see']), len(modules)),
                                     ', '.join(u['crit']) or '—', ', '.join(u['blocked']) or '—'))
    nb.add(fr1, text='Kullanıcılar (%d)' % len(urows))
    fr2, t2 = _tree(nb, ['Ekran'] + roles, widths=[220] + [110] * len(roles))
    for r in matrix:
        t2.insert('', 'end', values=r)
    nb.add(fr2, text='Ekran × Rol tablosu')
    foot = tk.Frame(w, bg='#f4f7fb')
    foot.pack(fill='x', padx=18, pady=(0, 12))
    tk.Label(foot, text='Görür < Ekler < Değiştirir < KRİTİK. Düzenlemek için Kullanıcı & Güvenlik > Rol & Yetkiler. Ctrl+C ile kopyalanır.', bg='#f4f7fb', fg='#64748b').pack(side='left')
    tk.Button(foot, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=10, pady=4).pack(side='right')
    w._yetki = dict(users=t1, matrix=t2, nb=nb)
    return w


def open_help(parent, module=''):
    import tkinter as tk
    w = _top(parent, 'Yardım & Kısayollar', '760x640')
    tk.Label(w, text='YARDIM & KISAYOLLAR', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 4))
    txt, keys = help_for(module)
    tk.Label(w, text=('Şu an: %s' % module) if module else '', font=('Segoe UI', 11, 'bold'), bg='#f4f7fb').pack(anchor='w', padx=18)
    tk.Label(w, text=txt, font=('Segoe UI', 11), bg='#f4f7fb', wraplength=700, justify='left').pack(anchor='w', padx=18, pady=(2, 10))
    box = tk.Text(w, font=('Segoe UI', 11), bg='white', relief='solid', bd=1, wrap='word')
    box.pack(fill='both', expand=True, padx=18, pady=(0, 10))
    box.tag_configure('h', font=('Segoe UI', 11, 'bold'), foreground='#0b376d', spacing1=8)
    if keys:
        box.insert('end', 'BU EKRANDA\n', 'h')
        for k, d in keys:
            box.insert('end', '  %-14s %s\n' % (k, d))
    for scr, ks in SCREEN_KEYS.items():
        if scr == module:
            continue
        box.insert('end', '\n%s\n' % tr_upper(scr), 'h')
        for k, d in ks:
            box.insert('end', '  %-14s %s\n' % (k, d))
    box.insert('end', '\nGENEL\n', 'h')
    for k, d in GENERAL_KEYS:
        box.insert('end', '  %-14s %s\n' % (k, d))
    box.config(state='disabled')
    def _tam():
        try:
            import ALFABE_YARDIM as Y
            Y.ac(parent, module)
        except Exception as ex:
            from tkinter import messagebox
            messagebox.showerror('Yardım', 'Kılavuz açılamadı: %s' % ex, parent=w)
    tk.Button(w, text='📖 TAM KILAVUZ', command=_tam, font=('Segoe UI', 10, 'bold'), padx=12, pady=4).pack(pady=(0, 6))
    tk.Button(w, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=4).pack(pady=(0, 12))
    w._yardim = dict(box=box)
    return w


# ----------------------------------------------------------------------------------------------
# Ana pencereye kurulum
# ----------------------------------------------------------------------------------------------
def install(app, connection_factory, back_dir, db_path, modules, can, go, nas_dir_fn=None, go_stock=None, pre=None):
    """App.__init__ sonunda çağrılır: F12, alt çubuk düğmeleri, açılışta özet. Hata verirse program etkilenmez."""
    import tkinter as tk
    nas = lambda: (nas_dir_fn() if nas_dir_fn else '') or ''

    def cur():
        return getattr(app, 'current_module', '') or ''

    def f_help(e=None):
        # v461.87: Hızlı Satış'ta F12 = Kaydet ve Kapat; yardım penceresi o ekranda F12'yi kapmasın.
        if getattr(app, 'current_module', '') == 'Hızlı Satış':
            return None
        open_help(app, cur())
        return 'break'

    def f_summary():
        if pre:
            pre()
        open_daily_summary(app, connection_factory, can, back_dir, nas(), go)

    def f_health():
        open_health(app, connection_factory, back_dir, nas(), db_path, go_stock)

    def f_perm():
        open_permission_report(app, connection_factory, modules)
    app.bind('<F12>', f_help, add='+')

    def f_kilavuz(e=None):
        # Hızlı Satış'ta F1 = nakit öde; orada kılavuz F1'i kapmaz.
        if getattr(app, 'current_module', '') == 'Hızlı Satış':
            return None
        app.yardim_ac()
        return 'break'
    app.bind('<F1>', f_kilavuz, add='+')

    def add_buttons():
        bot = getattr(app, 'bottombar', None)
        if bot is None:
            return
        for txt, cmd in (('📖 Kılavuz (F1)', f_kilavuz), ('❓ Yardım (F12)', f_help), ('📋 Günlük Özet', f_summary), ('🩺 Veri Kontrol', f_health), ('🔐 Yetki Raporu', f_perm)):
            tk.Button(bot, text=txt, command=cmd, bg='white', fg='#0b376d', relief='flat', font=('Segoe UI', 8, 'bold'), cursor='hand2').pack(side='left', padx=6)
    app.after(200, add_buttons)

    def start():
        try:
            if pre:
                pre()
            c = connection_factory()
            if not _start_on(app, c):
                return
            secs = collect_summary(c, can, back_dir, nas())
            if not secs or all(s['level'] == 'ok' for s in secs):
                return          # sorun yoksa açılışta rahatsız etme
            open_daily_summary(app, connection_factory, can, back_dir, nas(), go, startup=True)
        except Exception:
            pass
    app.after(1500, start)
    app._gunluk_arac = dict(help=f_help, summary=f_summary, health=f_health, perm=f_perm)
