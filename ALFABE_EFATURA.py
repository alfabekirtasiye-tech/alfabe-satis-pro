# -*- coding: utf-8 -*-
"""ALFABE E-Fatura / E-Arşiv modülü (GİB Portal yolu).

Bu dosya yalnız iş mantığını içerir (arayüz ana programdaki EInvoiceCenter sınıfındadır):
  * satıştan / satış iadesinden fatura taslağı oluşturma (KDV, ÖTV, istisna, GTİP, birim kodu),
  * GİB numara formatında (3 harf/rakam seri + yıl + 9 hane) numaralama ve ETTN (UUID),
  * UBL-TR 1.2 Invoice XML üretimi,
  * ön doğrulama (VKN/TCKN, adres, tutar tutarlılığı, KDV 0 istisna kodu...),
  * XML dosyası dışa aktarma, portal giriş listesi (CSV) ve HTML fatura görünümü,
  * durum takibi (Taslak -> XML Hazır -> Portala Yüklendi -> Onaylandı / İptal).

ÖNEMLİ: Bu modül faturayı GİB'e KENDİSİ göndermez; XML ve liste GİB Portal'a (veya bir entegratöre) elle
yüklenmek üzere hazırlanır. XML, UBL-TR 1.2 yapısına göre üretilmiştir; GİB'in resmî şematron doğrulaması
çalıştırılmadan canlıya çıkılmamalıdır (önce test/portal ile deneyin). Mali mühür/imza portal ya da entegratör
tarafından atılır. Satış ve stok verisi bu modül tarafından DEĞİŞTİRİLMEZ; yalnız okunur.
"""
import csv
import datetime
import html as _html
import json
import re
import uuid
from pathlib import Path
from xml.sax.saxutils import escape as _esc, quoteattr as _qa

STATUSES = ('Taslak', 'XML Hazır', 'Portala Yüklendi', 'Onaylandı', 'İptal')
DOC_TYPES = {'E-ARSIV': 'E-Arşiv Fatura', 'E-FATURA': 'E-Fatura'}
INVOICE_TYPES = {'SATIS': 'Satış', 'IADE': 'İade'}
FATURA_PROFILI = ('TEMELFATURA', 'TICARIFATURA')

SCHEMA = """
CREATE TABLE IF NOT EXISTS einvoice_docs(
 id INTEGER PRIMARY KEY, sale_id INTEGER, doc_type TEXT NOT NULL, invoice_type TEXT NOT NULL DEFAULT 'SATIS',
 number TEXT UNIQUE, ettn TEXT UNIQUE, issue_dt TEXT, customer_id INTEGER,
 buyer_name TEXT, buyer_id TEXT, buyer_tax_office TEXT, buyer_address TEXT, buyer_city TEXT, buyer_district TEXT,
 buyer_email TEXT, buyer_phone TEXT, currency TEXT DEFAULT 'TRY',
 net_total REAL DEFAULT 0, discount_total REAL DEFAULT 0, vat_total REAL DEFAULT 0, otv_total REAL DEFAULT 0, payable REAL DEFAULT 0,
 status TEXT DEFAULT 'Taslak', xml TEXT, ref_number TEXT, ref_date TEXT, note TEXT,
 created TEXT, created_by TEXT, updated TEXT, xml_at TEXT, uploaded_at TEXT, approved_at TEXT, cancelled_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_einvoice_sale_active ON einvoice_docs(sale_id) WHERE status<>'İptal' AND sale_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS einvoice_lines(
 id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, line_no INTEGER, stock_id INTEGER, name TEXT, unit_code TEXT DEFAULT 'C62',
 qty REAL, unit_price REAL, discount REAL DEFAULT 0, net REAL, vat_rate REAL DEFAULT 0, vat_amount REAL DEFAULT 0,
 otv_rate REAL DEFAULT 0, otv_amount REAL DEFAULT 0, gtip TEXT DEFAULT '', exempt_code TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS einvoice_series(
 year INTEGER, doc_type TEXT, prefix TEXT, last_no INTEGER DEFAULT 0, PRIMARY KEY(year,doc_type));
CREATE TABLE IF NOT EXISTS stock_tax_info(stock_id INTEGER PRIMARY KEY,gtip TEXT DEFAULT '',otv_rate REAL DEFAULT 0,vat_exempt TEXT DEFAULT '',einv_unit TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS einvoice_doc_sales(doc_id INTEGER NOT NULL, sale_id INTEGER NOT NULL, PRIMARY KEY(doc_id,sale_id));
CREATE TABLE IF NOT EXISTS einvoice_log(
 id INTEGER PRIMARY KEY, doc_id INTEGER, dt TEXT, user TEXT, action TEXT, detail TEXT);
"""

_UNIT_MAP = {'ADET': 'C62', 'AD': 'C62', 'ADT': 'C62', 'KG': 'KGM', 'KİLOGRAM': 'KGM', 'KILOGRAM': 'KGM', 'GR': 'GRM', 'GRAM': 'GRM',
             'LT': 'LTR', 'LİTRE': 'LTR', 'LITRE': 'LTR', 'MT': 'MTR', 'METRE': 'MTR', 'M2': 'MTK', 'M²': 'MTK', 'METREKARE': 'MTK',
             'KUTU': 'BX', 'PAKET': 'PA', 'ÇİFT': 'PR', 'CIFT': 'PR', 'TAKIM': 'SET', 'SET': 'SET', 'DÜZİNE': 'DZN', 'DUZINE': 'DZN',
             'KOLİ': 'BX', 'KOLI': 'BX', 'RULO': 'RO', 'TOP': 'RO', 'ADET (SAYFA)': 'C62', 'SAYFA': 'C62'}


# ----------------------------------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------------------------------
def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def r2(v):
    """Kuruşa yuvarlama (yarıdan yukarı)."""
    x = _f(v)
    return float(int(abs(x) * 100 + 0.5 + 1e-9)) / 100.0 * (1 if x >= 0 else -1)


def _a(v):
    """XML için 2 haneli nokta ondalıklı tutar."""
    return '%.2f' % r2(v)


def _p(v, places=4):
    return ('%.' + str(places) + 'f') % _f(v)


def only_digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def vkn_valid(v):
    d = only_digits(v)
    if len(d) != 10:
        return False
    s = 0
    for i in range(9):
        t = (int(d[i]) + (9 - i)) % 10
        x = (t * (2 ** (9 - i))) % 9
        if t != 0 and x == 0:
            x = 9
        s += x
    return (10 - s % 10) % 10 == int(d[9])


def tckn_valid(v):
    d = only_digits(v)
    if len(d) != 11 or d[0] == '0':
        return False
    n = [int(x) for x in d]
    if ((n[0] + n[2] + n[4] + n[6] + n[8]) * 7 - (n[1] + n[3] + n[5] + n[7])) % 10 != n[9]:
        return False
    return sum(n[:10]) % 10 == n[10]


def split_name(full):
    parts = str(full or '').strip().split()
    if len(parts) <= 1:
        return (parts[0] if parts else 'MÜŞTERİ'), '-'
    return ' '.join(parts[:-1]), parts[-1]


def tr_upper(s):
    return str(s or '').replace('i', 'İ').replace('ı', 'I').upper()


def amount_words(value):
    """1234,50 -> 'BİN İKİ YÜZ OTUZ DÖRT TÜRK LİRASI ELLİ KURUŞ' (büyük harf)."""
    ones = ['', 'BİR', 'İKİ', 'ÜÇ', 'DÖRT', 'BEŞ', 'ALTI', 'YEDİ', 'SEKİZ', 'DOKUZ']
    tens = ['', 'ON', 'YİRMİ', 'OTUZ', 'KIRK', 'ELLİ', 'ALTMIŞ', 'YETMİŞ', 'SEKSEN', 'DOKSAN']
    scale = ['', 'BİN', 'MİLYON', 'MİLYAR', 'TRİLYON']

    def three(n):
        h, t, o = n // 100, (n // 10) % 10, n % 10
        out = []
        if h:
            out.append(('' if h == 1 else ones[h] + ' ') + 'YÜZ')
        if t:
            out.append(tens[t])
        if o:
            out.append(ones[o])
        return ' '.join(out)

    def words(n):
        if n == 0:
            return 'SIFIR'
        parts, i = [], 0
        while n > 0:
            g = n % 1000
            if g:
                w = three(g)
                if i == 1 and g == 1:
                    w = ''
                parts.append((w + ' ' + scale[i]).strip())
            n //= 1000
            i += 1
        return ' '.join(reversed(parts))

    cents = int(round(abs(_f(value)) * 100))
    lira, kurus = divmod(cents, 100)
    s = words(lira) + ' TÜRK LİRASI'
    if kurus:
        s += ' ' + words(kurus) + ' KURUŞ'
    return s


def unit_code_for(unit_name, explicit=''):
    e = str(explicit or '').strip().upper()
    if e:
        return e[:3]
    return _UNIT_MAP.get(tr_upper(unit_name).strip(), 'C62')


# ----------------------------------------------------------------------------------------------
# Ayarlar / şema / numaralama
# ----------------------------------------------------------------------------------------------
def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def get_setting(c, key, default=''):
    try:
        r = c.execute('SELECT v FROM settings WHERE k=?', (key,)).fetchone()
        return default if r is None or r[0] is None else str(r[0])
    except Exception:
        return default


def set_setting(c, key, value):
    c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))


def valid_prefix(p):
    return bool(re.fullmatch(r'[A-Z0-9]{3}', str(p or '')))


def default_prefix(doc_type):
    return 'EAR' if doc_type == 'E-ARSIV' else 'EFA'


def get_prefix(c, doc_type):
    p = tr_upper(get_setting(c, 'einv_prefix_' + doc_type.lower().replace('-', ''), '')).replace(' ', '')
    return p if valid_prefix(p) else default_prefix(doc_type)


def next_number(c, doc_type, year=None):
    """GİB biçimi: SERİ(3) + YIL(4) + SIRA(9). Aynı işlem içinde çağrılmalıdır (BEGIN IMMEDIATE)."""
    year = int(year or datetime.date.today().year)
    prefix = get_prefix(c, doc_type)
    r = c.execute('SELECT last_no,prefix FROM einvoice_series WHERE year=? AND doc_type=?', (year, doc_type)).fetchone()
    n = (r[0] if r else 0) + 1
    if r and r[1] != prefix:
        # Seri öneki yıl içinde değiştirildi: yeni önekle sıra yeniden başlar (GİB'de seri bazlı sıra).
        n = 1
    c.execute('INSERT INTO einvoice_series(year,doc_type,prefix,last_no) VALUES(?,?,?,?) '
              'ON CONFLICT(year,doc_type) DO UPDATE SET last_no=excluded.last_no,prefix=excluded.prefix', (year, doc_type, prefix, n))
    num = '%s%04d%09d' % (prefix, year, n)
    while c.execute('SELECT 1 FROM einvoice_docs WHERE number=?', (num,)).fetchone():
        n += 1
        c.execute('UPDATE einvoice_series SET last_no=? WHERE year=? AND doc_type=?', (n, year, doc_type))
        num = '%s%04d%09d' % (prefix, year, n)
    return num


def log(c, doc_id, user, action, detail=''):
    c.execute('INSERT INTO einvoice_log(doc_id,dt,user,action,detail) VALUES(?,?,?,?,?)',
              (doc_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user, action, detail))


# ----------------------------------------------------------------------------------------------
# Firma (satıcı) bilgisi
# ----------------------------------------------------------------------------------------------
def seller_info(c):
    d = {}
    try:
        d = json.loads(get_setting(c, 'company_details', '{}') or '{}')
    except Exception:
        d = {}
    title = (d.get('full') or d.get('short') or get_setting(c, 'company', '') or '').strip()
    return {'title': title, 'tax_id': only_digits(d.get('taxno', '')), 'tax_office': (d.get('taxoffice') or '').strip(),
            'address': (d.get('address') or '').strip(), 'city': (d.get('city') or '').strip(), 'district': (d.get('district') or '').strip(),
            'postal': (d.get('postal') or '').strip(), 'phone': (d.get('phone1') or d.get('mobile') or '').strip(),
            'email': (d.get('email') or '').strip(), 'web': (d.get('web') or '').strip(), 'contact': (d.get('contact') or '').strip(),
            'logo_data': (d.get('logo_data') or '').strip(), 'logo_name': (d.get('logo_name') or '').strip().lower()}


# ----------------------------------------------------------------------------------------------
# Satıştan fatura taslağı
# ----------------------------------------------------------------------------------------------
def suggest_doc_type(buyer_id):
    """10 haneli VKN -> e-fatura adayı (mükellef olup olmadığı GİB'den doğrulanmalıdır); diğer -> e-arşiv."""
    return 'E-FATURA' if len(only_digits(buyer_id)) == 10 else 'E-ARSIV'


def invoiceable_sales(c, limit=400, include_invoiced=False):
    """Faturası henüz kesilmemiş satış ve iadeler (en yeni önce)."""
    sql = ("SELECT s.id,s.doc_no,s.dt,s.status,s.total,s.customer_id,COALESCE(ca.title,'') cari,COALESCE(ca.tax_no,'') tax_no "
           "FROM sales s LEFT JOIN caris ca ON ca.id=s.customer_id WHERE s.status IN ('FINAL','PARTIAL_RETURN','RETURN') ")
    if not include_invoiced:
        sql += "AND NOT EXISTS(SELECT 1 FROM einvoice_docs d WHERE d.sale_id=s.id AND d.status<>'İptal') "
        sql += "AND NOT EXISTS(SELECT 1 FROM einvoice_doc_sales l JOIN einvoice_docs d2 ON d2.id=l.doc_id WHERE l.sale_id=s.id AND d2.status<>'İptal') "
    sql += "ORDER BY s.dt DESC,s.id DESC LIMIT ?"
    return c.execute(sql, (int(limit),)).fetchall()


def create_from_sale(c, sale_id, doc_type=None, user='', note=''):
    """Satıştan (veya iadeden) fatura taslağı oluşturur. Satış verisi okunur, değiştirilmez. doc_id döndürür."""
    ensure_schema(c)
    s = c.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
    if s is None:
        raise ValueError('Satış bulunamadı.')
    if s['status'] not in ('FINAL', 'PARTIAL_RETURN', 'RETURN'):
        raise ValueError('Bu satış faturalanamaz (durum: %s).' % s['status'])
    if c.execute("SELECT 1 FROM einvoice_docs WHERE sale_id=? AND status<>'İptal'", (sale_id,)).fetchone():
        raise ValueError('Bu satış için zaten fatura oluşturulmuş.')
    if c.execute("SELECT 1 FROM einvoice_doc_sales l JOIN einvoice_docs d ON d.id=l.doc_id WHERE l.sale_id=? AND d.status<>'İptal'", (sale_id,)).fetchone():
        raise ValueError('Bu satış başka bir toplu faturaya dahil edilmiş.')
    is_return = s['status'] == 'RETURN'
    cari = c.execute('SELECT * FROM caris WHERE id=?', (s['customer_id'],)).fetchone() if s['customer_id'] else None
    buyer_id = only_digits(cari['tax_no']) if cari and cari['tax_no'] else ''
    doc_type = doc_type or suggest_doc_type(buyer_id)
    if doc_type not in DOC_TYPES:
        raise ValueError('Geçersiz belge türü.')
    if doc_type == 'E-FATURA' and len(buyer_id) != 10:
        raise ValueError('E-Fatura için alıcının 10 haneli vergi numarası (VKN) olmalıdır. Cari kartını kontrol edin ya da E-Arşiv seçin.')
    lines = c.execute("""SELECT sl.*,st.unit AS st_unit,st.vat AS st_vat,
                          ti.gtip AS gtip,ti.otv_rate AS otv_rate,ti.vat_exempt AS vat_exempt,ti.einv_unit AS einv_unit
                          FROM sale_lines sl LEFT JOIN stock st ON st.id=sl.stock_id
                          LEFT JOIN stock_tax_info ti ON ti.stock_id=sl.stock_id
                          WHERE sl.sale_id=? ORDER BY sl.id""", (sale_id,)).fetchall()
    if not lines:
        raise ValueError('Satışta kalem yok.')
    sign = -1.0 if is_return else 1.0
    out, net_t, disc_t, vat_t, otv_t = [], 0.0, 0.0, 0.0, 0.0
    for i, l in enumerate(lines, 1):
        qty = abs(_f(l['qty']))
        if qty == 0:
            continue
        total_incl = abs(_f(l['total']))                      # KDV dahil, iskonto düşülmüş
        disc_incl = abs(_f(l['discount']))
        vat = _f(l['vat_pct'])
        if vat == 0 and _f(l['vat_amount']) == 0 and _f(l['st_vat']) > 0:
            vat = _f(l['st_vat'])                       # eski kayıtlarda satır KDV'si işlenmemişse stok kartındaki oran
        otv = _f(l['otv_rate'])
        base_incl_otv = total_incl / (1 + vat / 100.0) if vat else total_incl   # KDV hariç (ÖTV dahil)
        net = base_incl_otv / (1 + otv / 100.0) if otv else base_incl_otv       # ÖTV ve KDV hariç matrah
        otv_amt = net * otv / 100.0 if otv else 0.0
        vat_amt = (net + otv_amt) * vat / 100.0 if vat else 0.0
        disc_ex = disc_incl / (1 + vat / 100.0) / (1 + otv / 100.0) if disc_incl else 0.0
        net, vat_amt, otv_amt, disc_ex = r2(net), r2(vat_amt), r2(otv_amt), r2(disc_ex)
        unit_price = (net + disc_ex) / qty
        code = unit_code_for(l['st_unit'], l['einv_unit'])
        out.append((i, l['stock_id'], str(l['name'] or ''), code, qty, unit_price, disc_ex, net, vat, vat_amt, otv, otv_amt,
                    str(l['gtip'] or ''), str(l['vat_exempt'] or '')))
        net_t += net; disc_t += disc_ex; vat_t += vat_amt; otv_t += otv_amt
    if not out:
        raise ValueError('Faturalanacak kalem bulunamadı.')
    net_t, disc_t, vat_t, otv_t = r2(net_t), r2(disc_t), r2(vat_t), r2(otv_t)
    issue_dt = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ref_number, ref_date = '', ''
    if is_return and s['original_sale_id']:
        o = c.execute("SELECT number,issue_dt FROM einvoice_docs WHERE sale_id=? AND status<>'İptal'", (s['original_sale_id'],)).fetchone()
        if o:
            ref_number, ref_date = o['number'] or '', (o['issue_dt'] or '')[:10]
        else:
            os_ = c.execute('SELECT doc_no,dt FROM sales WHERE id=?', (s['original_sale_id'],)).fetchone()
            if os_:
                ref_number, ref_date = os_['doc_no'] or '', (os_['dt'] or '')[:10]
    buyer_name = (cari['title'] if cari else '') or ('Perakende Müşteri' if doc_type == 'E-ARSIV' else '')
    if doc_type == 'E-ARSIV' and not buyer_id:
        buyer_id = '11111111111'
    c.execute('BEGIN IMMEDIATE')
    try:
        number = next_number(c, doc_type)
        cur = c.execute("""INSERT INTO einvoice_docs(sale_id,doc_type,invoice_type,number,ettn,issue_dt,customer_id,buyer_name,buyer_id,buyer_tax_office,
            buyer_address,buyer_city,buyer_district,buyer_email,buyer_phone,net_total,discount_total,vat_total,otv_total,payable,status,ref_number,ref_date,note,created,created_by,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Taslak',?,?,?,?,?,?)""",
                        (sale_id, doc_type, 'IADE' if is_return else 'SATIS', number, str(uuid.uuid4()), issue_dt, s['customer_id'], buyer_name, buyer_id,
                         (cari['tax_office'] if cari else '') or '', (cari['address'] if cari else '') or '', (cari['city'] if cari else '') or '',
                         (cari['district'] if cari else '') or '', (cari['email'] if cari else '') or '', (cari['phone'] if cari else '') or '',
                         net_t, disc_t, vat_t, otv_t, r2(net_t + vat_t + otv_t), ref_number, ref_date, note, issue_dt, user, issue_dt))
        did = cur.lastrowid
        for row in out:
            c.execute("""INSERT INTO einvoice_lines(doc_id,line_no,stock_id,name,unit_code,qty,unit_price,discount,net,vat_rate,vat_amount,otv_rate,otv_amount,gtip,exempt_code)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (did,) + row)
        log(c, did, user, 'Taslak oluşturuldu', 'Satış %s' % s['doc_no'])
        c.commit()
    except Exception:
        c.rollback()
        raise
    return did


def create_from_sales(c, sale_ids, doc_type=None, user='', note=''):
    """Aynı cariye ait birden çok (açık hesap) satışı TEK fatura taslağında birleştirir. doc_id döndürür.
    Yalnız FINAL/PARTIAL_RETURN satışlar, hepsi aynı ve kayıtlı cariye ait, hiçbiri daha önce faturalanmamış olmalıdır."""
    ensure_schema(c)
    ids = []
    for x in sale_ids:
        if int(x) not in ids:
            ids.append(int(x))
    if len(ids) < 2:
        raise ValueError('Toplu fatura için en az iki satış seçin.')
    sales = []
    for sid in ids:
        s = c.execute('SELECT * FROM sales WHERE id=?', (sid,)).fetchone()
        if s is None:
            raise ValueError('Satış bulunamadı: %s' % sid)
        if s['status'] not in ('FINAL', 'PARTIAL_RETURN'):
            raise ValueError('%s faturalanamaz (durum: %s). İadeler toplu faturaya katılamaz.' % (s['doc_no'], s['status']))
        if c.execute("SELECT 1 FROM einvoice_docs WHERE sale_id=? AND status<>'İptal'", (sid,)).fetchone() or \
           c.execute("SELECT 1 FROM einvoice_doc_sales l JOIN einvoice_docs d ON d.id=l.doc_id WHERE l.sale_id=? AND d.status<>'İptal'", (sid,)).fetchone():
            raise ValueError('%s için zaten fatura oluşturulmuş.' % s['doc_no'])
        sales.append(s)
    cust = {s['customer_id'] for s in sales}
    if len(cust) != 1 or None in cust:
        raise ValueError('Toplu fatura için tüm satışlar aynı cari müşteriye ait olmalıdır.')
    cari = c.execute('SELECT * FROM caris WHERE id=?', (sales[0]['customer_id'],)).fetchone()
    if cari is None:
        raise ValueError('Cari bulunamadı.')
    buyer_id = only_digits(cari['tax_no']) if cari['tax_no'] else ''
    doc_type = doc_type or suggest_doc_type(buyer_id)
    if doc_type not in DOC_TYPES:
        raise ValueError('Geçersiz belge türü.')
    if doc_type == 'E-FATURA' and len(buyer_id) != 10:
        raise ValueError('E-Fatura için alıcının 10 haneli vergi numarası (VKN) olmalıdır. Cari kartını kontrol edin ya da E-Arşiv seçin.')
    out, net_t, disc_t, vat_t, otv_t = [], 0.0, 0.0, 0.0, 0.0
    n = 0
    for s in sales:
        lines = c.execute("""SELECT sl.*,st.unit AS st_unit,st.vat AS st_vat,
                          ti.gtip AS gtip,ti.otv_rate AS otv_rate,ti.vat_exempt AS vat_exempt,ti.einv_unit AS einv_unit
                          FROM sale_lines sl LEFT JOIN stock st ON st.id=sl.stock_id
                          LEFT JOIN stock_tax_info ti ON ti.stock_id=sl.stock_id
                          WHERE sl.sale_id=? ORDER BY sl.id""", (s['id'],)).fetchall()
        for l in lines:
            qty = abs(_f(l['qty']))
            if qty == 0:
                continue
            total_incl = abs(_f(l['total']))
            disc_incl = abs(_f(l['discount']))
            vat = _f(l['vat_pct'])
            if vat == 0 and _f(l['vat_amount']) == 0 and _f(l['st_vat']) > 0:
                vat = _f(l['st_vat'])
            otv = _f(l['otv_rate'])
            base_incl_otv = total_incl / (1 + vat / 100.0) if vat else total_incl
            net = base_incl_otv / (1 + otv / 100.0) if otv else base_incl_otv
            otv_amt = net * otv / 100.0 if otv else 0.0
            vat_amt = (net + otv_amt) * vat / 100.0 if vat else 0.0
            disc_ex = disc_incl / (1 + vat / 100.0) / (1 + otv / 100.0) if disc_incl else 0.0
            net, vat_amt, otv_amt, disc_ex = r2(net), r2(vat_amt), r2(otv_amt), r2(disc_ex)
            unit_price = (net + disc_ex) / qty
            code = unit_code_for(l['st_unit'], l['einv_unit'])
            n += 1
            out.append((n, l['stock_id'], str(l['name'] or ''), code, qty, unit_price, disc_ex, net, vat, vat_amt, otv, otv_amt,
                        str(l['gtip'] or ''), str(l['vat_exempt'] or '')))
            net_t += net; disc_t += disc_ex; vat_t += vat_amt; otv_t += otv_amt
    if not out:
        raise ValueError('Faturalanacak kalem bulunamadı.')
    net_t, disc_t, vat_t, otv_t = r2(net_t), r2(disc_t), r2(vat_t), r2(otv_t)
    issue_dt = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    buyer_name = cari['title'] or ''
    if doc_type == 'E-ARSIV' and not buyer_id:
        buyer_id = '11111111111'
    note_full = (note + ' ' if note else '') + 'Birleştirilen satışlar: ' + ', '.join(str(s['doc_no']) for s in sales)
    c.execute('BEGIN IMMEDIATE')
    try:
        number = next_number(c, doc_type)
        cur = c.execute("""INSERT INTO einvoice_docs(sale_id,doc_type,invoice_type,number,ettn,issue_dt,customer_id,buyer_name,buyer_id,buyer_tax_office,
            buyer_address,buyer_city,buyer_district,buyer_email,buyer_phone,net_total,discount_total,vat_total,otv_total,payable,status,ref_number,ref_date,note,created,created_by,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Taslak',?,?,?,?,?,?)""",
                        (ids[0], doc_type, 'SATIS', number, str(uuid.uuid4()), issue_dt, cari['id'], buyer_name, buyer_id,
                         cari['tax_office'] or '', cari['address'] or '', cari['city'] or '', cari['district'] or '', cari['email'] or '', cari['phone'] or '',
                         net_t, disc_t, vat_t, otv_t, r2(net_t + vat_t + otv_t), '', '', note_full, issue_dt, user, issue_dt))
        did = cur.lastrowid
        for row in out:
            c.execute("""INSERT INTO einvoice_lines(doc_id,line_no,stock_id,name,unit_code,qty,unit_price,discount,net,vat_rate,vat_amount,otv_rate,otv_amount,gtip,exempt_code)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (did,) + row)
        for sid in ids:
            c.execute('INSERT INTO einvoice_doc_sales(doc_id,sale_id) VALUES(?,?)', (did, sid))
        log(c, did, user, 'Toplu taslak oluşturuldu', '%d satış: %s' % (len(ids), ', '.join(str(s['doc_no']) for s in sales)))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return did


def parse_incoming_xml(path):
    """Gelen UBL-TR fatura XML'ini okur (tedarikçi, fatura no/tarih, kalemler). Dosyayı değiştirmez.
    {'supplier','vkn','number','date','lines':[{'name','qty','price','vat','codes':[...],'unit'}],'payable'} döndürür."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as ex:
        raise ValueError('XML dosyası okunamadı: %s' % ex)

    def loc(t):
        return t.rsplit('}', 1)[-1]

    def find(el, *names):
        cur = [el]
        for nm in names:
            nxt = []
            for e in cur:
                nxt += [ch for ch in e if loc(ch.tag) == nm]
            cur = nxt
        return cur

    def text(el, *names):
        r = find(el, *names)
        return (r[0].text or '').strip() if r else ''
    if loc(root.tag) != 'Invoice':
        raise ValueError('Bu dosya bir UBL fatura (Invoice) değil.')
    sup = find(root, 'AccountingSupplierParty', 'Party')
    name = vkn = ''
    if sup:
        name = text(sup[0], 'PartyName', 'Name')
        if not name:
            name = (text(sup[0], 'Person', 'FirstName') + ' ' + text(sup[0], 'Person', 'FamilyName')).strip()
        for pid in find(sup[0], 'PartyIdentification', 'ID'):
            if (pid.get('schemeID') or '').upper() in ('VKN', 'TCKN'):
                vkn = (pid.text or '').strip()
                break
    lines = []
    for ln in find(root, 'InvoiceLine'):
        qty_el = find(ln, 'InvoicedQuantity')
        qty = _f(qty_el[0].text) if qty_el else 0.0
        unit = (qty_el[0].get('unitCode') or '') if qty_el else ''
        price = _f(text(ln, 'Price', 'PriceAmount'))
        ext = _f(text(ln, 'LineExtensionAmount'))
        if price <= 0 and qty > 0:
            price = ext / qty
        vat = 0.0
        for ts in find(ln, 'TaxTotal', 'TaxSubtotal'):
            if text(ts, 'TaxCategory', 'TaxScheme', 'TaxTypeCode') in ('0015', ''):
                vat = _f(text(ts, 'Percent')) or vat
        codes = [c for c in (text(ln, 'Item', 'SellersItemIdentification', 'ID'), text(ln, 'Item', 'StandardItemIdentification', 'ID'),
                             text(ln, 'Item', 'BuyersItemIdentification', 'ID')) if c]
        lines.append({'name': text(ln, 'Item', 'Name') or text(ln, 'Item', 'Description'), 'qty': qty, 'price': price, 'vat': vat, 'codes': codes, 'unit': unit})
    return {'supplier': name, 'vkn': vkn, 'number': text(root, 'ID'), 'date': text(root, 'IssueDate'), 'lines': lines,
            'payable': _f(text(root, 'LegalMonetaryTotal', 'PayableAmount'))}


# ----------------------------------------------------------------------------------------------
# Doğrulama
# ----------------------------------------------------------------------------------------------
def validate_doc(c, doc_id):
    """(hatalar, uyarılar) listesi döndürür. Hata varsa XML üretilmemelidir."""
    d = c.execute('SELECT * FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    if d is None:
        return ['Fatura bulunamadı.'], []
    L = c.execute('SELECT * FROM einvoice_lines WHERE doc_id=? ORDER BY line_no', (doc_id,)).fetchall()
    sel = seller_info(c)
    err, warn = [], []
    if not sel['title']:
        err.append('Firma ünvanı boş (Ayarlar > Firma bilgileri).')
    sid = sel['tax_id']
    if not (vkn_valid(sid) or tckn_valid(sid)):
        err.append('Firma vergi numarası/TCKN geçersiz ya da boş (Firma bilgileri > Vergi No).')
    if not sel['tax_office']:
        warn.append('Firma vergi dairesi boş.')
    if not sel['address'] or not sel['city']:
        err.append('Firma adresi ve ili zorunludur.')
    if not valid_prefix(str(d['number'] or '')[:3]) or not re.fullmatch(r'[A-Z0-9]{3}\d{13}', str(d['number'] or '')):
        err.append('Fatura numarası GİB biçiminde değil (3 harf/rakam + yıl + 9 hane).')
    bid = only_digits(d['buyer_id'])
    if d['doc_type'] == 'E-FATURA':
        if not vkn_valid(bid):
            err.append('Alıcı VKN geçersiz.')
        if not d['buyer_address'] or not d['buyer_city']:
            err.append('E-Fatura için alıcı adresi ve ili gerekir (cari kartı).')
        warn.append('Alıcının e-fatura mükellefi olduğunu GİB mükellef listesinden doğrulayın; değilse E-Arşiv kesilmelidir.')
    else:
        if bid != '11111111111' and not (vkn_valid(bid) or tckn_valid(bid)):
            err.append('Alıcı VKN/TCKN geçersiz. Boş bırakılırsa perakende (11111111111) kullanılır.')
        if bid == '11111111111':
            warn.append('Alıcı kimliği yok: perakende (11111111111) olarak düzenlenir.')
        if len(bid) == 10 and vkn_valid(bid):
            warn.append('Alıcı VKN sahibi: alıcı e-fatura mükellefiyse e-arşiv yerine e-fatura kesilmelidir.')
    if not str(d['buyer_name'] or '').strip():
        err.append('Alıcı adı/unvanı boş.')
    if d['invoice_type'] == 'IADE' and not d['ref_number']:
        err.append('İade faturası için iade edilen faturanın numarası gerekir.')
    if not L:
        err.append('Fatura kalemi yok.')
    s_net = r2(sum(_f(x['net']) for x in L)); s_vat = r2(sum(_f(x['vat_amount']) for x in L)); s_otv = r2(sum(_f(x['otv_amount']) for x in L))
    if abs(s_net - _f(d['net_total'])) > 0.011 or abs(s_vat - _f(d['vat_total'])) > 0.011 or abs(s_otv - _f(d['otv_total'])) > 0.011:
        err.append('Kalem toplamları fatura toplamıyla uyuşmuyor.')
    if abs(r2(s_net + s_vat + s_otv) - _f(d['payable'])) > 0.011:
        err.append('Ödenecek tutar kalem toplamıyla uyuşmuyor.')
    try:
        # Satır başına kuruşa yuvarlama nedeniyle fatura toplamı fişten 1-2 kuruş sapabilir; bilgi amaçlı uyarı (hesap değiştirilmez).
        ids = [d['sale_id']] if d['sale_id'] else [r[0] for r in c.execute('SELECT sale_id FROM einvoice_doc_sales WHERE doc_id=?', (doc_id,)).fetchall()]
        if ids:
            st = sum(abs(_f(r[0])) for r in c.execute('SELECT total FROM sales WHERE id IN (%s)' % ','.join('?' * len(ids)), ids).fetchall())
            diff = r2(_f(d['payable']) - st)
            if abs(diff) >= 0.01 and abs(diff) <= 0.05:
                warn.append('Fatura toplamı satış/fiş toplamından %s TL farklı (satır bazlı kuruş yuvarlaması). Normaldir; fatura kalemlerinin toplamı esastır.' % ('%.2f' % diff).replace('.', ','))
            elif abs(diff) > 0.05:
                warn.append('Fatura toplamı satış toplamından %s TL farklı; satış değiştirilmiş olabilir, kontrol edin.' % ('%.2f' % diff).replace('.', ','))
    except Exception:
        pass
    for x in L:
        if _f(x['vat_rate']) == 0 and not str(x['exempt_code'] or '').strip():
            warn.append('Satır %s (%s): KDV %%0 ama istisna sebep kodu yok (Stok kartı > Vergi / E-Fatura).' % (x['line_no'], x['name']))
        if _f(x['qty']) <= 0:
            err.append('Satır %s miktarı sıfır.' % x['line_no'])
        g = only_digits(x['gtip'])
        if g and not (4 <= len(g) <= 12):
            err.append('Satır %s GTİP 4-12 hane olmalı.' % x['line_no'])
    warn = list(dict.fromkeys(warn))
    return err, warn


# ----------------------------------------------------------------------------------------------
# UBL-TR XML
# ----------------------------------------------------------------------------------------------
_NS = ('xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
       'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
       'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
       'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
       'xsi:schemaLocation="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.1.xsd"')


def _t(tag, val, attrs=''):
    return '<%s%s>%s</%s>' % (tag, (' ' + attrs) if attrs else '', _esc(str(val)), tag)


def _party(p, role):
    """UBL Party (satıcı / alıcı). p: {id,title,tax_office,address,city,district,postal,phone,email,web,person}"""
    id_scheme = 'VKN' if len(p['id']) == 10 else 'TCKN'
    x = ['<cac:Party>']
    if p.get('web'):
        x.append('<cbc:WebsiteURI>%s</cbc:WebsiteURI>' % _esc(p['web']))
    x.append('<cac:PartyIdentification><cbc:ID schemeID="%s">%s</cbc:ID></cac:PartyIdentification>' % (id_scheme, _esc(p['id'])))
    if id_scheme == 'VKN':
        x.append('<cac:PartyName><cbc:Name>%s</cbc:Name></cac:PartyName>' % _esc(p['title']))
    x.append('<cac:PostalAddress>')
    if p.get('address'):
        x.append('<cbc:StreetName>%s</cbc:StreetName>' % _esc(p['address']))
    if p.get('district'):
        x.append('<cbc:CitySubdivisionName>%s</cbc:CitySubdivisionName>' % _esc(p['district']))
    x.append('<cbc:CityName>%s</cbc:CityName>' % _esc(p.get('city') or '-'))
    if p.get('postal'):
        x.append('<cbc:PostalZone>%s</cbc:PostalZone>' % _esc(p['postal']))
    x.append('<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress>')
    if p.get('tax_office'):
        x.append('<cac:PartyTaxScheme><cac:TaxScheme><cbc:Name>%s</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>' % _esc(p['tax_office']))
    if p.get('phone') or p.get('email'):
        x.append('<cac:Contact>')
        if p.get('phone'):
            x.append('<cbc:Telephone>%s</cbc:Telephone>' % _esc(p['phone']))
        if p.get('email'):
            x.append('<cbc:ElectronicMail>%s</cbc:ElectronicMail>' % _esc(p['email']))
        x.append('</cac:Contact>')
    if id_scheme == 'TCKN':
        fn, ln = split_name(p['title'])
        x.append('<cac:Person><cbc:FirstName>%s</cbc:FirstName><cbc:FamilyName>%s</cbc:FamilyName></cac:Person>' % (_esc(fn), _esc(ln)))
    x.append('</cac:Party>')
    return ''.join(x)


def render_xml(c, doc_id):
    """UBL-TR 1.2 Invoice XML metnini üretir (doğrulama hatası kontrolü çağırana aittir)."""
    d = c.execute('SELECT * FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    L = c.execute('SELECT * FROM einvoice_lines WHERE doc_id=? ORDER BY line_no', (doc_id,)).fetchall()
    sel = seller_info(c)
    dt = str(d['issue_dt'] or '')
    date, tm = dt[:10], (dt[11:19] or '00:00:00')
    is_arsiv = d['doc_type'] == 'E-ARSIV'
    profile = 'EARSIVFATURA' if is_arsiv else (get_setting(c, 'einv_profile', 'TEMELFATURA') if get_setting(c, 'einv_profile', 'TEMELFATURA') in FATURA_PROFILI else 'TEMELFATURA')
    itype = 'IADE' if d['invoice_type'] == 'IADE' else 'SATIS'
    cur = d['currency'] or 'TRY'
    x = ['<?xml version="1.0" encoding="UTF-8"?>', '<Invoice %s>' % _NS]
    x.append('<cbc:UBLVersionID>2.1</cbc:UBLVersionID><cbc:CustomizationID>TR1.2</cbc:CustomizationID>')
    x.append(_t('cbc:ProfileID', profile) + _t('cbc:ID', d['number']) + '<cbc:CopyIndicator>false</cbc:CopyIndicator>' + _t('cbc:UUID', d['ettn']))
    x.append(_t('cbc:IssueDate', date) + _t('cbc:IssueTime', tm) + _t('cbc:InvoiceTypeCode', itype))
    x.append(_t('cbc:Note', 'Yalnız: ' + amount_words(d['payable'])))
    if d['note']:
        x.append(_t('cbc:Note', d['note']))
    x.append(_t('cbc:DocumentCurrencyCode', cur) + '<cbc:LineCountNumeric>%d</cbc:LineCountNumeric>' % len(L))
    if itype == 'IADE' and d['ref_number']:
        x.append('<cac:BillingReference><cac:InvoiceDocumentReference>%s%s<cbc:DocumentTypeCode>IADE</cbc:DocumentTypeCode>'
                 '</cac:InvoiceDocumentReference></cac:BillingReference>' % (_t('cbc:ID', d['ref_number']), _t('cbc:IssueDate', d['ref_date'] or date)))
    if is_arsiv:
        # E-Arşiv: gönderim şekli (elektronik/kağıt). Gerekirse ayarlardan değiştirilir.
        send = 'KAGIT' if get_setting(c, 'einv_send_type', 'ELEKTRONIK') == 'KAGIT' else 'ELEKTRONIK'
        x.append('<cac:AdditionalDocumentReference>%s%s%s</cac:AdditionalDocumentReference>'
                 % (_t('cbc:ID', send), _t('cbc:IssueDate', date), _t('cbc:DocumentType', send)))
    # İmza bilgisi (yapısal; imzayı portal/entegratör atar)
    x.append('<cac:Signature><cbc:ID schemeID="VKN_TCKN">%s</cbc:ID><cac:SignatoryParty><cac:PartyIdentification><cbc:ID schemeID="%s">%s</cbc:ID>'
             '</cac:PartyIdentification><cac:PostalAddress><cbc:CityName>%s</cbc:CityName><cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress>'
             '</cac:SignatoryParty><cac:DigitalSignatureAttachment><cac:ExternalReference><cbc:URI>#Signature</cbc:URI></cac:ExternalReference>'
             '</cac:DigitalSignatureAttachment></cac:Signature>'
             % (_esc(sel['tax_id']), 'VKN' if len(sel['tax_id']) == 10 else 'TCKN', _esc(sel['tax_id']), _esc(sel['city'] or '-')))
    x.append('<cac:AccountingSupplierParty>%s</cac:AccountingSupplierParty>' % _party(
        {'id': sel['tax_id'], 'title': sel['title'], 'tax_office': sel['tax_office'], 'address': sel['address'], 'city': sel['city'],
         'district': sel['district'], 'postal': sel['postal'], 'phone': sel['phone'], 'email': sel['email'], 'web': sel['web']}, 'S'))
    x.append('<cac:AccountingCustomerParty>%s</cac:AccountingCustomerParty>' % _party(
        {'id': only_digits(d['buyer_id']), 'title': d['buyer_name'], 'tax_office': d['buyer_tax_office'], 'address': d['buyer_address'],
         'city': d['buyer_city'], 'district': d['buyer_district'], 'phone': d['buyer_phone'], 'email': d['buyer_email']}, 'C'))
    # Belge KDV / ÖTV toplamları (oran bazında)
    vat_groups, otv_groups = {}, {}
    for l in L:
        g = vat_groups.setdefault((_f(l['vat_rate']), str(l['exempt_code'] or '').strip()), [0.0, 0.0])
        g[0] += _f(l['net']) + _f(l['otv_amount']); g[1] += _f(l['vat_amount'])
        if _f(l['otv_rate']):
            o = otv_groups.setdefault(_f(l['otv_rate']), [0.0, 0.0])
            o[0] += _f(l['net']); o[1] += _f(l['otv_amount'])
    x.append('<cac:TaxTotal>' + _t('cbc:TaxAmount', _a(d['vat_total'] + d['otv_total']), 'currencyID="%s"' % cur))
    for (rate, ex), (base, amt) in sorted(vat_groups.items()):
        x.append(_tax_subtotal(base, amt, rate, '0015', 'KDV', cur, ex))
    for rate, (base, amt) in sorted(otv_groups.items()):
        x.append(_tax_subtotal(base, amt, rate, '0071', 'ÖTV', cur, ''))
    x.append('</cac:TaxTotal>')
    net, incl = _f(d['net_total']), _f(d['payable'])
    x.append('<cac:LegalMonetaryTotal>' + _t('cbc:LineExtensionAmount', _a(net), 'currencyID="%s"' % cur) + _t('cbc:TaxExclusiveAmount', _a(net), 'currencyID="%s"' % cur)
             + _t('cbc:TaxInclusiveAmount', _a(incl), 'currencyID="%s"' % cur)
             + (_t('cbc:AllowanceTotalAmount', _a(d['discount_total']), 'currencyID="%s"' % cur) if _f(d['discount_total']) else '')
             + _t('cbc:PayableAmount', _a(incl), 'currencyID="%s"' % cur) + '</cac:LegalMonetaryTotal>')
    for l in L:
        x.append(_line_xml(l, cur))
    x.append('</Invoice>')
    return '\n'.join(x)


def _tax_subtotal(base, amt, rate, code, name, cur, exempt):
    s = ('<cac:TaxSubtotal>' + _t('cbc:TaxableAmount', _a(base), 'currencyID="%s"' % cur) + _t('cbc:TaxAmount', _a(amt), 'currencyID="%s"' % cur)
         + '<cbc:Percent>%s</cbc:Percent><cac:TaxCategory>' % ('%g' % rate))
    if exempt and code == '0015' and rate == 0:
        s += _t('cbc:TaxExemptionReasonCode', exempt)
    s += '<cac:TaxScheme>' + _t('cbc:Name', name) + _t('cbc:TaxTypeCode', code) + '</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>'
    return s


def _line_xml(l, cur):
    c_attr = 'currencyID="%s"' % cur
    q = _f(l['qty'])
    s = ['<cac:InvoiceLine>', _t('cbc:ID', l['line_no']),
         _t('cbc:InvoicedQuantity', ('%g' % q), 'unitCode="%s"' % (l['unit_code'] or 'C62')), _t('cbc:LineExtensionAmount', _a(l['net']), c_attr)]
    if _f(l['discount']):
        base = _f(l['net']) + _f(l['discount'])
        pct = (_f(l['discount']) / base * 100.0) if base else 0.0
        s.append('<cac:AllowanceCharge><cbc:ChargeIndicator>false</cbc:ChargeIndicator><cbc:MultiplierFactorNumeric>%.4f</cbc:MultiplierFactorNumeric>%s%s</cac:AllowanceCharge>'
                 % (pct / 100.0, _t('cbc:Amount', _a(l['discount']), c_attr), _t('cbc:BaseAmount', _a(base), c_attr)))
    tax_total = _f(l['vat_amount']) + _f(l['otv_amount'])
    s.append('<cac:TaxTotal>' + _t('cbc:TaxAmount', _a(tax_total), c_attr))
    if _f(l['otv_rate']):
        s.append(_tax_subtotal(_f(l['net']), _f(l['otv_amount']), _f(l['otv_rate']), '0071', 'ÖTV', cur, ''))
    s.append(_tax_subtotal(_f(l['net']) + _f(l['otv_amount']), _f(l['vat_amount']), _f(l['vat_rate']), '0015', 'KDV', cur, str(l['exempt_code'] or '').strip()))
    s.append('</cac:TaxTotal>')
    item = '<cac:Item>' + _t('cbc:Name', l['name'] or '-')
    g = only_digits(l['gtip'])
    if g:
        item += '<cac:CommodityClassification><cbc:ItemClassificationCode listID="GTIP">%s</cbc:ItemClassificationCode></cac:CommodityClassification>' % g
    item += '</cac:Item>'
    s.append(item)
    s.append('<cac:Price>' + _t('cbc:PriceAmount', _p(l['unit_price']), c_attr) + '</cac:Price></cac:InvoiceLine>')
    return ''.join(s)


# ----------------------------------------------------------------------------------------------
# İşlemler: XML üret, dışa aktar, durum, liste
# ----------------------------------------------------------------------------------------------
def generate_xml(c, doc_id, user=''):
    """Doğrular, XML üretir ve kaydeder. (hatalar, uyarılar) döndürür; hata varsa XML üretilmez."""
    st0 = c.execute('SELECT status FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    if st0 is not None and st0['status'] == 'İptal':
        return ['Fatura iptal edilmiş; XML üretilemez.'], []
    err, warn = validate_doc(c, doc_id)
    if err:
        return err, warn
    xml = render_xml(c, doc_id)
    from xml.dom import minidom
    minidom.parseString(xml.encode('utf-8'))      # iyi biçimlilik denetimi
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st = c.execute('SELECT status FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()['status']
    c.execute("UPDATE einvoice_docs SET xml=?,xml_at=?,updated=?,status=CASE WHEN status='Taslak' THEN 'XML Hazır' ELSE status END WHERE id=?", (xml, now, now, doc_id))
    log(c, doc_id, user, 'XML üretildi', 'Önceki durum: ' + str(st))
    c.commit()
    return [], warn


def export_xml_files(c, doc_ids, folder, user=''):
    """Seçili faturaların XML'ini klasöre yazar (<ETTN veya numara>.xml). {doc_id: (dosya|None, hatalar)} döndürür."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    res = {}
    for did in doc_ids:
        err, _w = generate_xml(c, did, user)
        if err:
            res[did] = (None, err)
            continue
        d = c.execute('SELECT number,xml FROM einvoice_docs WHERE id=?', (did,)).fetchone()
        p = folder / (str(d['number']) + '.xml')
        p.write_text(d['xml'], encoding='utf-8')
        res[did] = (str(p), [])
    return res


_STATUS_ORDER = {'Taslak': 0, 'XML Hazır': 1, 'Portala Yüklendi': 2, 'Onaylandı': 3}


def set_status(c, doc_id, status, user='', note=''):
    if status not in STATUSES:
        raise ValueError('Geçersiz durum.')
    cur = c.execute('SELECT status,xml,number FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    if cur is None:
        raise ValueError('Fatura bulunamadı.')
    old = cur['status']
    if old == 'İptal':
        raise ValueError('%s iptal edilmiş; durumu değiştirilemez. Satıştan yeni fatura oluşturabilirsiniz.' % cur['number'])
    if status != 'İptal':
        if _STATUS_ORDER.get(status, 0) < _STATUS_ORDER.get(old, 0):
            raise ValueError('%s: durum geriye alınamaz (%s -> %s).' % (cur['number'], old, status))
        if status in ('Portala Yüklendi', 'Onaylandı') and not cur['xml']:
            raise ValueError('%s: önce DOĞRULA ve XML KAYDET / PORTAL LİSTESİ ile XML hazırlanmalıdır.' % cur['number'])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    col = {'Portala Yüklendi': 'uploaded_at', 'Onaylandı': 'approved_at', 'İptal': 'cancelled_at'}.get(status)
    c.execute('UPDATE einvoice_docs SET status=?,updated=?' + (',%s=?' % col if col else '') + ' WHERE id=?',
              (status, now) + ((now,) if col else ()) + (doc_id,))
    log(c, doc_id, user, 'Durum: ' + status, note)
    c.commit()


def set_number(c, doc_id, number, user=''):
    """Portalın verdiği resmî numarayı kaydeder (XML üretildiyse yeniden üretilir)."""
    number = str(number or '').strip().upper().replace(' ', '')
    if not re.fullmatch(r'[A-Z0-9]{3}\d{13}', number):
        raise ValueError('Numara 16 karakter olmalı: 3 harf/rakam + 4 hane yıl + 9 hane sıra (örn. ABC2026000000001).')
    if c.execute('SELECT 1 FROM einvoice_docs WHERE number=? AND id<>?', (number, doc_id)).fetchone():
        raise ValueError('Bu numara başka faturada kullanılıyor.')
    old = c.execute('SELECT number,xml FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    c.execute('UPDATE einvoice_docs SET number=?,updated=? WHERE id=?', (number, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), doc_id))
    log(c, doc_id, user, 'Numara değişti', '%s -> %s' % (old['number'], number))
    c.commit()
    if old['xml']:
        generate_xml(c, doc_id, user)


def portal_rows(c, doc_ids):
    """Portal'a elle giriş için satır listesi (her kalem bir satır)."""
    heads = ['Fatura No', 'ETTN', 'Tarih', 'Saat', 'Belge Türü', 'Fatura Tipi', 'Alıcı Ünvan / Ad Soyad', 'VKN / TCKN', 'Vergi Dairesi', 'Adres', 'İl', 'İlçe',
             'Satır', 'Mal / Hizmet', 'Miktar', 'Birim Kodu', 'Birim Fiyat (KDV Hariç)', 'İskonto (KDV Hariç)', 'Tutar (KDV Hariç)', 'KDV %', 'KDV Tutarı',
             'ÖTV %', 'ÖTV Tutarı', 'GTİP', 'İstisna Kodu', 'Fatura Toplamı (KDV Dahil)', 'Ref. Fatura No', 'Ref. Fatura Tarihi']
    rows = []
    for did in doc_ids:
        d = c.execute('SELECT * FROM einvoice_docs WHERE id=?', (did,)).fetchone()
        if d is None:
            continue
        dt = str(d['issue_dt'] or '')
        for l in c.execute('SELECT * FROM einvoice_lines WHERE doc_id=? ORDER BY line_no', (did,)).fetchall():
            rows.append([d['number'], d['ettn'], dt[:10], dt[11:19], DOC_TYPES.get(d['doc_type'], d['doc_type']), INVOICE_TYPES.get(d['invoice_type'], d['invoice_type']),
                         d['buyer_name'], d['buyer_id'], d['buyer_tax_office'], d['buyer_address'], d['buyer_city'], d['buyer_district'],
                         l['line_no'], l['name'], '%g' % _f(l['qty']), l['unit_code'], _fmt(l['unit_price'], 4), _fmt(l['discount']), _fmt(l['net']), '%g' % _f(l['vat_rate']),
                         _fmt(l['vat_amount']), '%g' % _f(l['otv_rate']), _fmt(l['otv_amount']), l['gtip'], l['exempt_code'], _fmt(d['payable']), d['ref_number'], d['ref_date']])
    return heads, rows


def _fmt(v, places=2):
    return (('%.' + str(places) + 'f') % _f(v)).replace('.', ',')


def export_portal_csv(c, doc_ids, path):
    heads, rows = portal_rows(c, doc_ids)
    with open(str(path), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(heads)
        w.writerows(rows)
    return len(rows)


def _preview_html_base(c, doc_id):
    """Basit fatura görünümü (resmî görünüm GİB/portal tarafından üretilir; bu yalnız kontrol amaçlıdır)."""
    d = c.execute('SELECT * FROM einvoice_docs WHERE id=?', (doc_id,)).fetchone()
    L = c.execute('SELECT * FROM einvoice_lines WHERE doc_id=? ORDER BY line_no', (doc_id,)).fetchall()
    sel = seller_info(c)
    e = _html.escape
    m = lambda v: _fmt(v)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td class=r>%s %s</td><td class=r>%s</td><td class=r>%s</td><td class=r>%%%g</td><td class=r>%s</td><td class=r>%s</td></tr>'
                   % (l['line_no'], e(str(l['name'])), '%g' % _f(l['qty']), e(str(l['unit_code'])), _fmt(l['unit_price'], 4), m(l['discount']), _f(l['vat_rate']),
                      m(l['vat_amount']), m(_f(l['net']))) for l in L)
    return ('<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>%s</title><style>body{font:12px Segoe UI,Arial;margin:24px;color:#102a4c}'
            'table{border-collapse:collapse;width:100%%}td,th{border:1px solid #b8c6d6;padding:5px 7px}th{background:#e6f1ff}.r{text-align:center}'
            '.box{border:1px solid #b8c6d6;padding:10px;margin:8px 0}h1{font-size:20px}.w{background:#fff6d6;border:1px solid #e6c34d;padding:6px;margin:8px 0}</style></head><body>'
            '<h1>%s - %s</h1><div class=w>Bu bir KONTROL GÖRÜNÜMÜDÜR; resmî fatura görünümünü GİB Portal / entegratör üretir.</div>'
            '<div class=box><b>Fatura No:</b> %s &nbsp; <b>ETTN:</b> %s &nbsp; <b>Tarih:</b> %s &nbsp; <b>Durum:</b> %s</div>'
            '<div class=box><b>SATICI:</b> %s<br>VKN/TCKN: %s &nbsp; V.D.: %s<br>%s %s/%s</div>'
            '<div class=box><b>ALICI:</b> %s<br>VKN/TCKN: %s &nbsp; V.D.: %s<br>%s %s/%s</div>'
            '%s<table><tr><th>#</th><th>Mal / Hizmet</th><th>Miktar</th><th>Birim Fiyat</th><th>İskonto</th><th>KDV</th><th>KDV Tutarı</th><th>Tutar (KDV Hariç)</th></tr>%s</table>'
            '<table style="width:320px;margin-left:auto;margin-top:10px"><tr><td>Mal/Hizmet Toplamı</td><td class=r>%s</td></tr><tr><td>İskonto Toplamı</td><td class=r>%s</td></tr>'
            '<tr><td>KDV Toplamı</td><td class=r>%s</td></tr>%s<tr><th>ÖDENECEK TUTAR</th><th class=r>%s TL</th></tr></table>'
            '<p>Yalnız: %s</p></body></html>') % (
        e(str(d['number'])), e(DOC_TYPES.get(d['doc_type'], '')), e(INVOICE_TYPES.get(d['invoice_type'], '')), e(str(d['number'])), e(str(d['ettn'])), e(str(d['issue_dt'])), e(str(d['status'])),
        e(sel['title']), e(sel['tax_id']), e(sel['tax_office']), e(sel['address']), e(sel['district']), e(sel['city']),
        e(str(d['buyer_name'])), e(str(d['buyer_id'])), e(str(d['buyer_tax_office'])), e(str(d['buyer_address'] or '')), e(str(d['buyer_district'] or '')), e(str(d['buyer_city'] or '')),
        ('<div class=box><b>İade edilen fatura:</b> %s (%s)</div>' % (e(str(d['ref_number'])), e(str(d['ref_date'])))) if d['invoice_type'] == 'IADE' else '',
        rows, m(d['net_total']), m(d['discount_total']), m(d['vat_total']),
        ('<tr><td>ÖTV Toplamı</td><td class=r>%s</td></tr>' % m(d['otv_total'])) if _f(d['otv_total']) else '', m(d['payable']), e(amount_words(d['payable'])))


def preview_html(c, doc_id):
    """Kontrol görünümü; Ayarlar > Firma Logosu seçiliyse sağ üstte logo gösterilir."""
    out = _preview_html_base(c, doc_id)
    try:
        sel = seller_info(c)
        if sel.get('logo_data'):
            nm = sel.get('logo_name', '')
            mime = 'image/jpeg' if nm.endswith(('.jpg', '.jpeg')) else ('image/gif' if nm.endswith('.gif') else 'image/png')
            out = out.replace('<body>', '<body><img src="data:%s;base64,%s" style="height:16mm;max-width:50mm;object-fit:contain;float:right">' % (mime, sel['logo_data']), 1)
    except Exception:
        pass
    return out
