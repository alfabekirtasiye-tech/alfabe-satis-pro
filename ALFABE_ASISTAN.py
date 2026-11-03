# -*- coding: utf-8 -*-
"""
ALFABE Yapay Zekâ İşletme Asistanı
==================================
Normal Türkçe cümlelerle soru sorulur; asistan yetki verilen program verilerini analiz eder ve sonucu
KAYNAK KAYITLARLA (belge no, tarih, tutar ...) birlikte gösterir.

Güvenlik ilkeleri
  * Asistan VERİTABANINA YAZMAZ. Tüm analizler salt-okunur, önceden tanımlı sorgulardır (serbest SQL yoktur).
  * Kullanıcının yetkisi olmayan bölümlerin verisi okunmaz (role_permissions / Yönetici kuralı).
  * Tek yazma yolu "Önerilen işlem" düğmesidir: kullanıcı açıkça basar, onay penceresi çıkar, onaylanırsa
    yalnız bir sipariş taslağı (Sipariş Havuzu) oluşur. Stok ve finans kayıtları kendiliğinden değişmez.

Çalışma kipleri
  1) Yerel analiz (varsayılan): internet gerekmez, veri bilgisayardan dışarı çıkmaz.
  2) Yapay zekâ (isteğe bağlı): Ayarlar'dan kendi Anthropic API anahtarınızı girerseniz serbest cümleler daha
     iyi anlaşılır. Bu kipte soru ve araç sonuçları (tutarlar, ürün adları) API'ye gönderilir; cari unvanı/telefon
     gibi kişisel alanlar maskelenerek gönderilir.
"""
import re
import sys
import json
import math
import datetime
import calendar
import threading
import queue
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

D = datetime.date
ST_ALL = "('FINAL','PARTIAL_RETURN','RETURN')"      # iadeler negatif tutarlı kayıtlardır
ST_SALE = "('FINAL','PARTIAL_RETURN')"

API_URL = 'https://api.anthropic.com/v1/messages'
DEFAULT_MODEL = 'claude-sonnet-5-5'


# --------------------------------------------------------------------------- yardımcılar
_TR = str.maketrans({'İ': 'i', 'I': 'ı'})


def fold(t):
    """Türkçe küçük harf + ASCII katlama (eşleştirme için)."""
    t = str(t or '').translate(_TR).lower()
    for a, b in (('ı', 'i'), ('ş', 's'), ('ğ', 'g'), ('ü', 'u'), ('ö', 'o'), ('ç', 'c'), ('â', 'a'), ('î', 'i'), ('û', 'u')):
        t = t.replace(a, b)
    return t


def fmt_money(v):
    s = f"{float(v or 0):,.2f}"
    return s.replace(',', '§').replace('.', ',').replace('§', '.') + ' ₺'


def fmt_num(v, dec=2):
    v = float(v or 0)
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}".replace(',', '.')
    return f"{v:,.{dec}f}".replace(',', '§').replace('.', ',').replace('§', '.')


def fmt_date(iso):
    try:
        y, m, d = str(iso)[:10].split('-')
        return f'{d}.{m}.{y}'
    except Exception:
        return str(iso or '')


def pct(a, b):
    try:
        if not b:
            return '—' if not a else '∞'
        return f"%{(a - b) / abs(b) * 100:+.1f}".replace('.', ',')
    except Exception:
        return '—'


def _add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return D(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _month_end(y, m):
    return D(y, m, calendar.monthrange(y, m)[1])


MONTHS = {'ocak': 1, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayis': 5, 'haziran': 6, 'temmuz': 7, 'agustos': 8,
          'eylul': 9, 'ekim': 10, 'kasim': 11, 'aralik': 12}
MONTH_NAMES = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


class Period:
    def __init__(self, start, end, label, kind='custom'):
        self.start, self.end, self.label, self.kind = start, end, label, kind

    def iso(self):
        return self.start.isoformat(), self.end.isoformat()


def parse_period(text, today=None, default='bu ay'):
    """Türkçe tarih ifadesinden dönem çıkarır: bugün, dün, bu hafta, geçen ay, son 30 gün, ekim 2026, 2026-10-01..2026-10-21 ..."""
    today = today or D.today()
    t = fold(text)
    m = re.search(r'(\d{4}-\d{2}-\d{2})\s*(?:\.\.|/|ile|to)\s*(\d{4}-\d{2}-\d{2})', t)
    if m:
        try:
            a, b = D.fromisoformat(m.group(1)), D.fromisoformat(m.group(2))
            if a > b:
                a, b = b, a
            return Period(a, b, f'{fmt_date(a.isoformat())} – {fmt_date(b.isoformat())}')
        except ValueError:
            pass
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', t)
    if m:
        try:
            a = D.fromisoformat(m.group(1))
            return Period(a, a, fmt_date(a.isoformat()), 'day')
        except ValueError:
            pass
    m = re.search(r'son\s*(\d{1,3})\s*(gun|hafta|ay|yil)', t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        n = max(1, n)
        if u == 'gun':
            return Period(today - datetime.timedelta(days=n - 1), today, f'Son {n} gün', 'last')
        if u == 'hafta':
            return Period(today - datetime.timedelta(days=7 * n - 1), today, f'Son {n} hafta', 'last')
        if u == 'ay':
            return Period(_add_months(today, -n) + datetime.timedelta(days=1), today, f'Son {n} ay', 'last')
        return Period(_add_months(today, -12 * n) + datetime.timedelta(days=1), today, f'Son {n} yıl', 'last')
    mon = today - datetime.timedelta(days=today.weekday())
    if 'gecen hafta' in t:
        return Period(mon - datetime.timedelta(days=7), mon - datetime.timedelta(days=1), 'Geçen hafta', 'week')
    if 'bu hafta' in t:
        return Period(mon, today, 'Bu hafta', 'week')
    if 'gecen ay' in t:
        f = _add_months(today.replace(day=1), -1)
        return Period(f, _month_end(f.year, f.month), f'Geçen ay ({MONTH_NAMES[f.month]} {f.year})', 'month')
    if 'bu ay' in t:
        return Period(today.replace(day=1), today, f'Bu ay ({MONTH_NAMES[today.month]} {today.year})', 'month')
    if 'gecen yil' in t:
        return Period(D(today.year - 1, 1, 1), D(today.year - 1, 12, 31), f'Geçen yıl ({today.year - 1})', 'year')
    if 'bu yil' in t:
        return Period(D(today.year, 1, 1), today, f'Bu yıl ({today.year})', 'year')
    if 'bugun' in t:
        return Period(today, today, f'Bugün ({fmt_date(today.isoformat())})', 'day')
    if re.search(r'\bdun', t):
        y = today - datetime.timedelta(days=1)
        return Period(y, y, f'Dün ({fmt_date(y.isoformat())})', 'day')
    if 'haftalik' in t:
        return Period(today - datetime.timedelta(days=6), today, 'Son 7 gün', 'last')
    if 'aylik' in t:
        return Period(today.replace(day=1), today, f'Bu ay ({MONTH_NAMES[today.month]} {today.year})', 'month')
    if 'gunluk' in t:
        return Period(today, today, f'Bugün ({fmt_date(today.isoformat())})', 'day')
    if 'yillik' in t:
        return Period(D(today.year, 1, 1), today, f'Bu yıl ({today.year})', 'year')
    m = re.search(r'\b(ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik)\w*\s*(\d{4})?', t)
    if m:
        mo = MONTHS[m.group(1)]
        if m.group(2):
            y = int(m.group(2))
        else:
            y = today.year if mo <= today.month else today.year - 1
        end = min(_month_end(y, mo), today) if (y, mo) == (today.year, today.month) else _month_end(y, mo)
        return Period(D(y, mo, 1), end, f'{MONTH_NAMES[mo]} {y}', 'month')
    return parse_period(default, today, 'bu ay') if default and fold(default) != t else Period(today.replace(day=1), today, 'Bu ay', 'month')


# --------------------------------------------------------------------------- Excel (.xlsx) çıktısı
import zipfile
import os as _os
from xml.sax.saxutils import escape as _xesc

_NUM_RX = re.compile(r'^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$')


def parse_tr_number(v):
    """'1.234,50 ₺' -> (1234.5, 'money'); '12' -> (12, 'int'); '3,5' -> (3.5, 'dec'); sayı değilse (None, '')."""
    if isinstance(v, bool):
        return None, ''
    if isinstance(v, (int, float)):
        return v, ('int' if float(v).is_integer() else 'dec')
    t = str(v or '').replace('\u00a0', ' ').strip()
    money = t.endswith('₺') or t.upper().endswith(' TL')
    t = t.replace('₺', '').replace(' TL', '').replace(' tl', '').replace(' ', '')
    if not t or not _NUM_RX.match(t):
        return None, ''
    n = float(t.replace('.', '').replace(',', '.'))
    if money:
        return n, 'money'
    return n, ('dec' if ',' in t else 'int')


def _xcol(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _xclean(v):
    return _xesc(''.join(ch for ch in str(v) if ch in '\t\n\r' or ord(ch) >= 32))


def _sheet_xml(top_lines, headers, rows, num_cols):
    """top_lines: [(metin, kalın_mı)] — tablonun üstüne yazılan başlık/özet satırları."""
    out, r = [], 1

    def txt(ref, v, st):
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, _xclean(v))
    for line, bold in top_lines:
        out.append('<row r="%d">%s</row>' % (r, txt('A%d' % r, line, 5 if bold else 0)))
        r += 1
    if top_lines:
        r += 1
    head_row = r
    if headers:
        out.append('<row r="%d">%s</row>' % (r, ''.join(txt('%s%d' % (_xcol(i), r), h, 1) for i, h in enumerate(headers))))
        r += 1
    widths = [len(str(h)) for h in headers]
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            ref = '%s%d' % (_xcol(i), r)
            if i < len(widths):
                widths[i] = max(widths[i], min(60, len(str(v))))
            if v is None or v == '':
                continue
            n, kind = parse_tr_number(v) if i in num_cols else (None, '')
            if n is None:
                cells.append(txt(ref, v, 0))
            else:
                st = {'money': 4, 'dec': 3, 'int': 2}[kind]
                cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, repr(float(n)) if kind != 'int' else int(n)))
        out.append('<row r="%d">%s</row>' % (r, ''.join(cells)))
        r += 1
    cols = ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, max(10, min(60, w + 3))) for i, w in enumerate(widths))
    pane = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            % (head_row, head_row + 1)) if headers else ''
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + pane + ('<cols>' + cols + '</cols>' if cols else '') + '<sheetData>' + ''.join(out) + '</sheetData></worksheet>')


_XL_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="165" formatCode="#,##0.00\\ &quot;₺&quot;"/></numFmts>'
              '<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font>'
              '<font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Calibri"/></font>'
              '<font><b/><sz val="12"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF143D68"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write_result_xlsx(path, res):
    """Asistan sonucunu .xlsx yazar: 1. sayfa 'Sonuç' (başlık, dönem, özet satırları, tablo), 2. sayfa 'Kaynak Kayıtlar'.
    Tutar/sayı sütunları gerçek sayı olarak yazılır (Excel'de toplanabilir). Bağımlılık gerekmez."""
    top = [(res.get('title', ''), True)]
    if res.get('period'):
        top.append((res['period'], False))
    top += [(x, False) for x in res.get('summary', [])]
    top += [('ℹ ' + x, False) for x in res.get('notes', [])]
    sheets = [('Sonuç', _sheet_xml(top, res.get('columns', []), res.get('rows', []), set(res.get('num_cols', []))))]
    if res.get('src_rows'):
        sheets.append(('Kaynak Kayıtlar', _sheet_xml([(res.get('src_title', 'Kaynak kayıtlar'), True)], res.get('src_columns', []),
                                                    res['src_rows'], set(res.get('src_num_cols', [])))))
    n = len(sheets)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          + ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(n))
          + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_xesc(nm), i + 1, i + 1) for i, (nm, _) in enumerate(sheets)) + '</sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(n))
            + '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' % (n + 1))
    tmp = str(path) + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                  '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', rels)
        z.writestr('xl/styles.xml', _XL_STYLES)
        for i, (_, xml) in enumerate(sheets):
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), xml)
    _os.replace(tmp, str(path))


# --------------------------------------------------------------------------- araç kayıt defteri
SALES_NEEDS = ['Rapor & Analiz', 'Hızlı Satış', 'Cari Satış']
STOCK_NEEDS = ['Stok Merkezi', 'Alış & Mal Kabul', 'Rapor & Analiz']

TOOLS = {
    'sales_summary': {
        'title': 'Satış özeti', 'needs': SALES_NEEDS,
        'desc': 'Bir dönemin net satış toplamı, iade, belge sayısı, ortalama sepet, ödeme türü dağılımı ve hizmet/fotokopi geliri.',
        'params': {'period': {'type': 'string', 'description': "Dönem: 'bugün', 'dün', 'bu hafta', 'bu ay', 'geçen ay', 'son 30 gün', 'ekim 2026' veya 'YYYY-MM-DD..YYYY-MM-DD'."}}},
    'top_products': {
        'title': 'En çok satan ürünler', 'needs': SALES_NEEDS,
        'desc': 'Bir dönemde en çok satılan ürünler (adet ve ciro) ve ilgili satış belgeleri.',
        'params': {'period': {'type': 'string', 'description': 'Dönem (bugün, bu ay, son 7 gün ...).'},
                   'limit': {'type': 'integer', 'description': 'Kaç ürün (varsayılan 10).'}}},
    'compare_sales': {
        'title': 'Dönem karşılaştırma', 'needs': SALES_NEEDS,
        'desc': 'Bu dönem ile bir önceki dönemin (aynı gün sayısı) satışlarını karşılaştırır. kind: gun, hafta, ay, yil.',
        'params': {'kind': {'type': 'string', 'description': "'gun', 'hafta', 'ay' veya 'yil' (varsayılan ay)."}}},
    'service_income': {
        'title': 'Hizmet / fotokopi geliri', 'needs': ['Hizmetler / Fotokopi', 'Rapor & Analiz'],
        'desc': 'Hizmet satışlarından (fotokopi, baskı, ciltleme vb.) elde edilen gelir. keyword ile hizmet adına göre süzülür.',
        'params': {'period': {'type': 'string', 'description': 'Dönem.'},
                   'keyword': {'type': 'string', 'description': "Hizmet adında geçen sözcük, örn. 'fotokopi'."}}},
    'slow_stock': {
        'title': 'Uzun süredir bekleyen stoklar', 'needs': STOCK_NEEDS,
        'desc': 'Stokta olup belirli gündür satılmayan ürünler ve bağlı sermaye tutarı.',
        'params': {'days': {'type': 'integer', 'description': 'Kaç gündür satılmadı (varsayılan 90).'},
                   'limit': {'type': 'integer', 'description': 'Kaç ürün (varsayılan 30).'}}},
    'reorder': {
        'title': 'Sipariş önerisi', 'needs': STOCK_NEEDS,
        'desc': 'Minimum stok altındaki veya satış hızına göre kısa sürede bitecek ürünler ve önerilen sipariş miktarı.',
        'params': {'limit': {'type': 'integer', 'description': 'Kaç ürün (varsayılan 40).'}}},
    'critical_stock': {
        'title': 'Kritik / negatif stok', 'needs': STOCK_NEEDS,
        'desc': 'Minimum seviyenin altındaki ve negatif stoklu ürünler.', 'params': {}},
    'debtors': {
        'title': 'Borçlu cariler', 'needs': ['Cari Merkezi', 'Finans Merkezi', 'Rapor & Analiz'],
        'desc': 'Borç bakiyesi en yüksek cariler.', 'params': {'limit': {'type': 'integer', 'description': 'Kaç cari (varsayılan 15).'}}},
    'profit': {
        'title': 'Kâr analizi', 'needs': ['Rapor & Analiz'],
        'desc': 'Dönem brüt kârı (satış − KDV − maliyet) ve en kârlı ürünler.',
        'params': {'period': {'type': 'string', 'description': 'Dönem.'}}},
    'daily_brief': {
        'title': 'Günlük özet', 'needs': SALES_NEEDS + STOCK_NEEDS + ['Cari Merkezi', 'Finans Merkezi'],
        'desc': 'İşletmenin genel durumu: dün ve bugünkü satış, kritik stok sayısı, toplam alacak (borçlu cariler), uzun süredir satılmayan stok. Yetkisiz bölümler atlanır.',
        'params': {}},
    'product_lookup': {
        'title': 'Ürün sorgulama', 'needs': STOCK_NEEDS + ['Hızlı Satış', 'Cari Satış', 'Fiyat Gör'],
        'desc': 'Ürün adı/barkod/kod ile stok miktarı, fiyat ve satış hızı.',
        'params': {'query': {'type': 'string', 'description': 'Ürün adı, marka, barkod veya stok kodu.'}}},
}

# Yerel (kural tabanlı) yönlendirme: (regex, ağırlık). Metin fold() ile ASCII'ye katlanmış gelir.
RULES = {
    'top_products': [(r'en cok.{0,25}sat', 5), (r'cok satan', 5), (r'ne sattim', 5), (r'hangi urun.{0,20}sat', 3), (r'satis.{0,10}urun', 1), (r'en cok', 1)],
    'reorder': [(r'siparis', 4), (r'almam', 4), (r'almali', 3), (r'alinmali', 3), (r'tedarik', 3), (r'ne eksik', 3)],
    'compare_sales': [(r'gecen (ay|hafta|yil)\w* gore', 6), (r'karsilastir', 6), (r'dune gore', 6), (r'gore nasil', 3), (r'\bfark', 2), (r'artti|azaldi|dustu|yukseldi|geriledi', 3)],
    'service_income': [(r'fotokopi', 6), (r'hizmet', 3), (r'baski', 3), (r'cilt', 3), (r'tarama|laminasyon', 3)],
    'slow_stock': [(r'uzun suredir', 5), (r'bekleyen', 4), (r'hareketsiz', 5), (r'satilmayan|satilmiyor|satilmamis|satilmadi', 5), (r'durgun|olu stok', 5), (r'raf.{0,10}kalan', 2)],
    'critical_stock': [(r'kritik', 5), (r'azalan', 4), (r'bitmek uzere|bitecek|tukenen|tukenecek|tukendi', 4), (r'negatif|eksiye', 4)],
    'debtors': [(r'borc', 5), (r'alacak', 4), (r'vadesi', 3), (r'tahsil edil', 3)],
    'profit': [(r'\bkar\b|karim|kari\b|kazanc|kazandim|marj', 6)],
    'sales_summary': [(r'ciro', 5), (r'ne kadar.{0,15}sat', 4), (r'satis', 2), (r'toplam', 1), (r'kac (tl|lira)', 3), (r'hasilat', 4)],
    'daily_brief': [(r'gunluk ozet', 9), (r'genel durum', 9), (r'sabah ozet', 9), (r'isletme.{0,12}durum', 9), (r'bugun ne durum', 9), (r'nasil gidiyor', 9), (r'\bozet\b', 1)],
    'product_lookup': [(r'\bstok', 1), (r'kac (tane|adet)', 3), (r'fiyat', 1)],
}
PRIORITY = ['daily_brief', 'compare_sales', 'top_products', 'service_income', 'slow_stock', 'reorder', 'critical_stock', 'debtors', 'profit',
            'sales_summary', 'product_lookup']
STOP = set('stok stogu stokta stoklar kac tane adet var mi mu ne kadar fiyat fiyati urun urunu bul goster kalan durum durumu '
           'bana nedir hangi nasil kalem icin ile ve bir bu su o de da ki mı mi olan kaldi kalmis'.split())

EXAMPLES = ['Bugün en çok ne sattım?', 'Hangi ürünleri sipariş etmeliyim?', 'Bu ay satışlar geçen aya göre nasıl?',
            'Fotokopi gelirleri ne kadar?', 'Stokta uzun süredir bekleyen ürünler hangileri?']


def mask_text(s):
    s = str(s or '')
    return ' '.join((w[0] + '***') if w else '' for w in s.split())


# --------------------------------------------------------------------------- analiz motoru (salt-okunur)
class Engine:
    def __init__(self, rows_fn, one_fn, can_view, today_fn=None):
        self._rows, self._one, self.can_view = rows_fn, one_fn, can_view
        self.today = today_fn or D.today

    # veri erişimi (yalnız SELECT)
    def rows(self, sql, params=()):
        assert sql.lstrip().upper().startswith('SELECT'), 'Asistan yalnız SELECT çalıştırabilir.'
        return list(self._rows(sql, params))

    def one(self, sql, params=()):
        assert sql.lstrip().upper().startswith('SELECT'), 'Asistan yalnız SELECT çalıştırabilir.'
        return self._one(sql, params)

    # ---- sonuç iskeleti
    @staticmethod
    def new(title, period='', **kw):
        r = {'title': title, 'period': period, 'summary': [], 'columns': [], 'rows': [], 'num_cols': [],
             'src_title': 'Kaynak kayıtlar', 'src_columns': [], 'src_rows': [], 'src_num_cols': [], 'notes': [],
             'action': None, 'sensitive': [], 'tool': ''}
        r.update(kw)
        return r

    def denied(self, tool):
        need = ', '.join(TOOLS[tool]['needs'])
        return self.new('Yetki yok', summary=[f'Bu bilgiyi görmek için yetkiniz yok. Gerekli bölümlerden biri: {need}.',
                                              'Yöneticiden ilgili bölümde "Görüntüle" yetkisi isteyebilirsiniz.'], tool=tool)

    # ---- çalıştır
    def run(self, tool, **args):
        if tool not in TOOLS:
            return self.new('Bilinmeyen analiz', summary=['Bu analiz tanımlı değil.'])
        if not any(self.can_view(m) for m in TOOLS[tool]['needs']):
            return self.denied(tool)
        fn = getattr(self, 't_' + tool)
        res = fn(**self._clean(tool, args))
        res['tool'] = tool
        return res

    def _clean(self, tool, args):
        out = {}
        for k, spec in TOOLS[tool]['params'].items():
            if k in args and args[k] not in (None, ''):
                v = args[k]
                if spec['type'] == 'integer':
                    try:
                        v = max(1, min(500, int(v)))
                    except (TypeError, ValueError):
                        continue
                else:
                    v = str(v)[:120]
                out[k] = v
        return out

    # ---- yönlendirme
    def route(self, question):
        t = fold(question)
        scores = {}
        for tool, rules in RULES.items():
            sc = sum(w for rx, w in rules if re.search(rx, t))
            if sc:
                scores[tool] = sc
        words = self._product_words(t)
        if words and 'product_lookup' not in scores:
            try:
                if self._find_stock(words, 1):
                    scores['product_lookup'] = 2
            except Exception:
                pass
        if not scores:
            return None, {}
        best = max(scores.items(), key=lambda kv: (kv[1], -PRIORITY.index(kv[0])))[0]
        args = {}
        m = re.search(r'(?:ilk|en cok|en|top)\s*(\d{1,2})\b', t)
        lim = int(m.group(1)) if m else None
        if best in ('top_products', 'sales_summary', 'service_income', 'profit'):
            dflt = {'top_products': 'bu ay', 'sales_summary': 'bugun', 'service_income': 'bu ay', 'profit': 'bu ay'}[best]
            args['period'] = question if self._has_period(t) else dflt
        if best == 'compare_sales':
            args['kind'] = 'yil' if 'yil' in t else 'hafta' if 'hafta' in t else 'gun' if re.search(r'bugun|\bdun', t) else 'ay'
        if best == 'service_income':
            args['keyword'] = next((k for k in ('fotokopi', 'baski', 'cilt', 'tarama', 'laminasyon') if k in t), '')
        if best == 'slow_stock':
            m2 = re.search(r'(\d{1,3})\s*gun', t)
            m3 = re.search(r'(\d{1,2})\s*ay', t)
            if m2:
                args['days'] = int(m2.group(1))
            elif m3:
                args['days'] = int(m3.group(1)) * 30
        if best == 'product_lookup':
            args['query'] = ' '.join(words)
        if lim and best in ('top_products', 'slow_stock', 'reorder', 'debtors'):
            args['limit'] = lim
        return best, args

    @staticmethod
    def _has_period(t):
        return bool(re.search(r'bugun|\bdun|bu (hafta|ay|yil)|gecen (hafta|ay|yil)|son \d|haftalik|aylik|gunluk|yillik|ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik|\d{4}-\d{2}-\d{2}', t))

    @staticmethod
    def _product_words(t):
        words = [w for w in re.findall(r'[a-z0-9]+', t) if w not in STOP and len(w) >= 2]
        return words[:5]

    FOLLOW_PERIOD = ('sales_summary', 'top_products', 'service_income', 'profit')
    FOLLOW_LIMIT = ('top_products', 'slow_stock', 'reorder', 'debtors')

    def _followup(self, question):
        """Önceki soruya devam cümlesi ("peki geçen ay", "ilk 20", "yıllık") ise (analiz, argümanlar) döndürür."""
        last = getattr(self, 'last', None)
        if not last:
            return None
        lt, la = last
        t = fold(question)
        if len(t.split()) > 8:
            return None
        if lt in self.FOLLOW_PERIOD and self._has_period(t):
            return lt, {**la, 'period': question}
        if lt == 'compare_sales':
            m = re.search(r'\b(gun|gunluk|dun|bugun|hafta|haftalik|ay|aylik|yil|yillik)\b', t)
            if m:
                k = m.group(1)
                return lt, {**la, 'kind': 'yil' if k.startswith('yil') else 'hafta' if k.startswith('hafta') else 'gun' if k in ('gun', 'gunluk', 'dun', 'bugun') else 'ay'}
        if lt == 'slow_stock':
            m = re.search(r'(\d{1,3})\s*gun', t)
            m2 = re.search(r'(\d{1,2})\s*ay\b', t)
            if m or m2:
                return lt, {**la, 'days': int(m.group(1)) if m else int(m2.group(1)) * 30}
        if lt in self.FOLLOW_LIMIT:
            m = re.search(r'(?:ilk|en cok|en|top)\s*(\d{1,3})\b', t) or re.search(r'\b(\d{1,3})\s*(?:tane|adet|urun|cari|kisi)?\s*(?:goster|listele|yaz)', t)
            if m:
                return lt, {**la, 'limit': int(m.group(1))}
        return None

    def answer(self, question):
        tool, args = self.route(question)
        follow = None
        if tool is None:
            follow = self._followup(question)
            if follow:
                tool, args = follow
        if tool is not None and (tool in self.FOLLOW_PERIOD or tool in self.FOLLOW_LIMIT or tool in ('compare_sales', 'slow_stock')):
            self.last = (tool, dict(args))
        if follow:
            res = self.run(tool, **args)
            res['notes'].insert(0, f"Önceki sorunuzun devamı olarak yanıtlandı ({TOOLS[tool]['title']}).")
            return res
        if tool is None:
            return self.new('Sorunuzu anlayamadım', summary=['Şu tür sorular sorabilirsiniz:'] + ['• ' + e for e in EXAMPLES] +
                            ['• Bu ayki kârım ne kadar?', '• En çok borcu olan müşteriler kimler?', '• Kritik stoklar hangileri?',
                             '• "Serve kalem" stokta kaç tane var?'])
        return self.run(tool, **args)

    @staticmethod
    def text_of(res):
        head = res['title'] + (f" — {res['period']}" if res.get('period') else '')
        lines = [head] + [''] + res['summary']
        if res.get('notes'):
            lines += [''] + ['ℹ ' + n for n in res['notes']]
        return '\n'.join(lines).strip()

    @staticmethod
    def for_llm(res, max_rows=40):
        sens = {i for i, c in enumerate(res['columns']) if c in res.get('sensitive', [])}
        rows = [[(mask_text(c) if i in sens else c) for i, c in enumerate(r)] for r in res['rows'][:max_rows]]
        return {'baslik': res['title'], 'donem': res.get('period', ''), 'ozet': res['summary'], 'kolonlar': res['columns'],
                'satirlar': rows, 'toplam_satir_sayisi': len(res['rows']), 'notlar': res.get('notes', [])}

    # ================================================================= analizler
    def _sales_totals(self, d1, d2):
        r = self.one(f"""SELECT COALESCE(SUM(CASE WHEN status IN {ST_SALE} THEN total ELSE 0 END),0) gross,
                                COALESCE(SUM(CASE WHEN status='RETURN' THEN -total ELSE 0 END),0) ret,
                                COUNT(CASE WHEN status IN {ST_SALE} THEN 1 END) n, COUNT(CASE WHEN status='RETURN' THEN 1 END) nr
                         FROM sales WHERE status IN {ST_ALL} AND substr(dt,1,10) BETWEEN ? AND ?""", (d1, d2))
        sv = self.one("SELECT COALESCE(SUM(total),0) v, COUNT(*) n FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ?", (d1, d2))
        gross, ret, n = float(r['gross']), float(r['ret']), int(r['n'])
        return {'gross': gross, 'ret': ret, 'net': gross - ret, 'n': n, 'nr': int(r['nr']), 'avg': (gross / n if n else 0.0),
                'service': float(sv['v']), 'service_n': int(sv['n'])}

    def _sale_docs(self, d1, d2, limit=300):
        return self.rows(f"""SELECT s.doc_no,s.dt,COALESCE(c.title,'Perakende') cust,s.payment,s.total,s.status
                             FROM sales s LEFT JOIN caris c ON c.id=s.customer_id
                             WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ? ORDER BY s.dt DESC LIMIT ?""", (d1, d2, limit))

    @staticmethod
    def _doc_rows(docs):
        stt = {'FINAL': 'Satış', 'PARTIAL_RETURN': 'Kısmi iade', 'RETURN': 'İade'}
        return [[d['doc_no'], fmt_date(d['dt']) + ' ' + str(d['dt'])[11:16], d['cust'], d['payment'] or '', fmt_money(d['total']), stt.get(d['status'], d['status'])] for d in docs]

    DOC_COLS = ['Belge No', 'Tarih', 'Müşteri', 'Ödeme', 'Tutar', 'Tür']

    def t_sales_summary(self, period='bugun'):
        p = parse_period(period, self.today(), 'bugun')
        d1, d2 = p.iso()
        s = self._sales_totals(d1, d2)
        res = self.new('Satış özeti', p.label)
        res['summary'] = [f"Net satış: {fmt_money(s['net'])}  ({s['n']} satış belgesi" + (f", {s['nr']} iade" if s['nr'] else '') + ')',
                          f"Brüt satış: {fmt_money(s['gross'])}   İade: {fmt_money(s['ret'])}   Ortalama sepet: {fmt_money(s['avg'])}"]
        if s['service_n']:
            res['summary'].append(f"Hizmet / fotokopi geliri (ayrı kayıt): {fmt_money(s['service'])}  ({s['service_n']} işlem)")
        pay = self.rows(f"""SELECT p.kind, SUM(p.amount) amt FROM sale_payments p JOIN sales s ON s.id=p.sale_id
                            WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY p.kind ORDER BY amt DESC""", (d1, d2))
        res['columns'] = ['Ödeme türü', 'Tutar']
        res['rows'] = [[r['kind'] or '—', fmt_money(r['amt'])] for r in pay]
        res['num_cols'] = [1]
        res['src_title'] = 'Kaynak satış belgeleri'
        res['src_columns'] = self.DOC_COLS
        docs = self._sale_docs(d1, d2)
        res['src_rows'] = self._doc_rows(docs)
        res['src_num_cols'] = [4]
        if not docs:
            res['summary'].append('Bu dönemde satış kaydı bulunamadı.')
        res['notes'].append('Tutarlar KDV dahildir. İadeler negatif belge olarak düşülür.')
        return res

    def t_top_products(self, period='bu ay', limit=10):
        p = parse_period(period, self.today(), 'bu ay')
        d1, d2 = p.iso()
        rows = self.rows(f"""SELECT COALESCE(sl.stock_id,0) sid, COALESCE(MAX(st.name),MAX(sl.name)) name, MAX(st.barcode) barcode,
                                    SUM(sl.qty) qty, SUM(sl.total) total, COUNT(DISTINCT s.id) docs, group_concat(DISTINCT s.doc_no) docnos
                             FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id
                             WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ?
                             GROUP BY COALESCE(sl.stock_id,0), CASE WHEN sl.stock_id IS NULL THEN sl.name END
                             HAVING SUM(sl.qty)>0 ORDER BY qty DESC, total DESC LIMIT ?""", (d1, d2, int(limit)))
        res = self.new('En çok satan ürünler', p.label)
        if not rows:
            res['summary'] = ['Bu dönemde satış kaydı bulunamadı.']
            return res
        top = rows[0]
        res['summary'] = [f"En çok satılan: {top['name']} — {fmt_num(top['qty'])} adet, {fmt_money(top['total'])}"]
        if len(rows) > 1:
            res['summary'].append('Sonrakiler: ' + '; '.join(f"{r['name']} ({fmt_num(r['qty'])})" for r in rows[1:4]))
        res['columns'] = ['#', 'Ürün', 'Barkod', 'Adet', 'Ciro', 'Belge', 'Örnek belgeler']
        res['rows'] = [[i + 1, r['name'], r['barcode'] or '', fmt_num(r['qty']), fmt_money(r['total']), r['docs'],
                        ', '.join(str(r['docnos'] or '').split(',')[:4])] for i, r in enumerate(rows)]
        res['num_cols'] = [0, 3, 4, 5]
        ids = [r['sid'] for r in rows if r['sid']]
        if ids:
            ph = ','.join('?' * len(ids))
            src = self.rows(f"""SELECT s.doc_no,s.dt,sl.name,sl.qty,sl.total FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
                                WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ? AND sl.stock_id IN ({ph})
                                ORDER BY s.dt DESC LIMIT 400""", (d1, d2, *ids))
            res['src_title'] = 'Kaynak satış satırları'
            res['src_columns'] = ['Belge No', 'Tarih', 'Ürün', 'Adet', 'Tutar']
            res['src_rows'] = [[x['doc_no'], fmt_date(x['dt']), x['name'], fmt_num(x['qty']), fmt_money(x['total'])] for x in src]
            res['src_num_cols'] = [3, 4]
        res['notes'].append('Sıralama adede göredir; iadeler düşülmüştür. Tutarlar KDV dahildir.')
        return res

    def t_compare_sales(self, kind='ay'):
        today = self.today()
        kind = fold(kind)
        if kind.startswith('gun'):
            a1 = a2 = today
            b1 = b2 = today - datetime.timedelta(days=1)
            la, lb, full = 'Bugün', 'Dün', None
        elif kind.startswith('hafta'):
            mon = today - datetime.timedelta(days=today.weekday())
            a1, a2 = mon, today
            b1 = mon - datetime.timedelta(days=7)
            b2 = b1 + (today - mon)
            la, lb = 'Bu hafta', 'Geçen hafta (aynı günler)'
            full = (b1, mon - datetime.timedelta(days=1), 'Geçen haftanın tamamı')
        elif kind.startswith('yil'):
            a1, a2 = D(today.year, 1, 1), today
            b1, b2 = D(today.year - 1, 1, 1), _add_months(today, -12)
            la, lb = f'Bu yıl ({today.year})', f'Geçen yıl aynı dönem ({today.year - 1})'
            full = (b1, D(today.year - 1, 12, 31), 'Geçen yılın tamamı')
        else:
            a1, a2 = today.replace(day=1), today
            pm = _add_months(today.replace(day=1), -1)
            b1 = pm
            b2 = D(pm.year, pm.month, min(today.day, calendar.monthrange(pm.year, pm.month)[1]))
            la, lb = f'Bu ay ({MONTH_NAMES[today.month]})', f'Geçen ay aynı dönem ({MONTH_NAMES[pm.month]})'
            full = (pm, _month_end(pm.year, pm.month), f'Geçen ayın tamamı ({MONTH_NAMES[pm.month]})')
        A, B = self._sales_totals(a1.isoformat(), a2.isoformat()), self._sales_totals(b1.isoformat(), b2.isoformat())
        res = self.new('Dönem karşılaştırma', f"{la}: {fmt_date(a1.isoformat())}–{fmt_date(a2.isoformat())}  |  {lb}: {fmt_date(b1.isoformat())}–{fmt_date(b2.isoformat())}")
        diff = A['net'] - B['net']
        yon = 'artış' if diff > 0 else 'düşüş' if diff < 0 else 'değişim yok'
        res['summary'] = [f"{la} net satış {fmt_money(A['net'])}; {lb} {fmt_money(B['net'])}.",
                          f"Fark: {fmt_money(diff)} ({pct(A['net'], B['net'])}) — {yon}."]
        if full:
            F = self._sales_totals(full[0].isoformat(), full[1].isoformat())
            if F['net'] > 0:
                res['summary'].append(f"{full[2]}: {fmt_money(F['net'])} — {la.split(' (')[0].lower()} şu ana kadar bunun %{A['net'] / F['net'] * 100:.0f}'ine ulaştı.")
            else:
                res['summary'].append(f"{full[2]}: {fmt_money(F['net'])}.")
        res['columns'] = ['Gösterge', la, lb, 'Fark', 'Değişim']
        mets = [('Net satış', 'net', fmt_money), ('Satış belgesi', 'n', fmt_num), ('Ortalama sepet', 'avg', fmt_money),
                ('İade tutarı', 'ret', fmt_money), ('Hizmet / fotokopi geliri', 'service', fmt_money)]
        res['rows'] = [[n, f(A[k]), f(B[k]), f(A[k] - B[k]), pct(A[k], B[k])] for n, k, f in mets]
        res['num_cols'] = [1, 2, 3, 4]
        docs = [dict(d, per='A') for d in self._sale_docs(a1.isoformat(), a2.isoformat(), 150)] + \
               [dict(d, per='B') for d in self._sale_docs(b1.isoformat(), b2.isoformat(), 150)]
        res['src_title'] = 'Kaynak satış belgeleri (her iki dönem)'
        res['src_columns'] = ['Dönem'] + self.DOC_COLS
        res['src_rows'] = [[la if d['per'] == 'A' else lb] + self._doc_rows([d])[0] for d in docs]
        res['src_num_cols'] = [5]
        res['notes'].append('Karşılaştırma eşit gün sayısı üzerinden yapılır. Tutarlar KDV dahildir.')
        return res

    def t_service_income(self, period='bu ay', keyword=''):
        p = parse_period(period, self.today(), 'bu ay')
        d1, d2 = p.iso()
        kw = fold(keyword).strip()
        rows = self.rows("SELECT doc_no,dt,name,variant,qty,unit_price,total,payment FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ? ORDER BY dt DESC", (d1, d2))
        note = None
        if kw:
            f = [r for r in rows if kw in fold(r['name']) or kw in fold(r['variant'] or '')]
            if f:
                rows = f
            elif rows:
                note = f"'{keyword}' adıyla eşleşen hizmet bulunamadı; tüm hizmet satışları gösteriliyor."
        res = self.new('Hizmet / fotokopi geliri', p.label)
        total = sum(float(r['total'] or 0) for r in rows)
        res['summary'] = [f"Toplam gelir: {fmt_money(total)}  ({len(rows)} işlem, {fmt_num(sum(float(r['qty'] or 0) for r in rows))} adet)"]
        if not rows:
            res['summary'] = ['Bu dönemde hizmet satışı bulunamadı.']
        grp = {}
        for r in rows:
            k = (r['name'] or '—') + (f" / {r['variant']}" if r['variant'] else '')
            g = grp.setdefault(k, [0.0, 0.0, 0])
            g[0] += float(r['qty'] or 0)
            g[1] += float(r['total'] or 0)
            g[2] += 1
        res['columns'] = ['Hizmet', 'Adet', 'İşlem', 'Gelir']
        res['rows'] = [[k, fmt_num(v[0]), v[2], fmt_money(v[1])] for k, v in sorted(grp.items(), key=lambda kv: -kv[1][1])]
        res['num_cols'] = [1, 2, 3]
        if grp:
            best = max(grp.items(), key=lambda kv: kv[1][1])
            res['summary'].append(f"En yüksek gelir: {best[0]} — {fmt_money(best[1][1])}")
        res['src_title'] = 'Kaynak hizmet kayıtları'
        res['src_columns'] = ['Belge No', 'Tarih', 'Hizmet', 'Adet', 'Birim fiyat', 'Tutar', 'Ödeme']
        res['src_rows'] = [[r['doc_no'], fmt_date(r['dt']), (r['name'] or '') + (f" / {r['variant']}" if r['variant'] else ''), fmt_num(r['qty']),
                            fmt_money(r['unit_price']), fmt_money(r['total']), r['payment'] or ''] for r in rows[:400]]
        res['src_num_cols'] = [3, 4, 5]
        if note:
            res['notes'].append(note)
        return res

    def _last_sales(self):
        return {r['stock_id']: r['d'] for r in self.rows(
            f"""SELECT sl.stock_id, MAX(substr(s.dt,1,10)) d FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
                WHERE sl.stock_id IS NOT NULL AND s.status IN {ST_SALE} GROUP BY sl.stock_id""")}

    def t_slow_stock(self, days=90, limit=30):
        days = int(days)
        today = self.today()
        last = self._last_sales()
        stocks = self.rows("SELECT id,code,barcode,name,qty,avg_cost,last_cost,retail,created FROM stock WHERE active=1 AND qty>0")
        items = []
        for s in stocks:
            ls = last.get(s['id'])
            ref = ls or (str(s['created'] or '')[:10] or None)
            try:
                idle = (today - D.fromisoformat(ref)).days if ref else 9999
            except ValueError:
                idle = 9999
            if idle >= days:
                cost = float(s['avg_cost'] or s['last_cost'] or 0)
                items.append((float(s['qty']) * cost, idle, s, ls))
        items.sort(key=lambda x: (-x[0], -x[1]))
        res = self.new('Uzun süredir bekleyen stoklar', f'{days} gün ve üzeri satış yok')
        if not items:
            res['summary'] = [f'{days} gündür satılmayan stoklu ürün bulunmuyor.']
            return res
        tot = sum(i[0] for i in items)
        res['summary'] = [f"{len(items)} üründe {days} gündür satış yok; bağlı sermaye (maliyet): {fmt_money(tot)}.",
                          f"En yüksek tutar: {items[0][2]['name']} — {fmt_money(items[0][0])} ({items[0][1] if items[0][1] < 9999 else 'hiç satılmamış'} gün)."]
        res['columns'] = ['Stok Kodu', 'Ürün', 'Stok', 'Son satış', 'Bekleme (gün)', 'Maliyet tutarı']
        shown = items[:int(limit)]
        res['rows'] = [[s['code'] or '', s['name'], fmt_num(s['qty']), fmt_date(ls) if ls else 'Hiç satılmadı',
                        idle if idle < 9999 else '—', fmt_money(v)] for v, idle, s, ls in shown]
        res['num_cols'] = [2, 4, 5]
        # kaynak: ürünlerin son satış belgeleri
        src = []
        for v, idle, s, ls in shown:
            r = self.one(f"""SELECT s2.doc_no, s2.dt FROM sale_lines sl JOIN sales s2 ON s2.id=sl.sale_id
                             WHERE sl.stock_id=? AND s2.status IN {ST_SALE} ORDER BY s2.dt DESC LIMIT 1""", (s['id'],))
            src.append([s['code'] or '', s['barcode'] or '', s['name'], r['doc_no'] if r else '—', fmt_date(r['dt']) if r else '—'])
        res['src_title'] = 'Kaynak stok kartları ve son satış belgeleri'
        res['src_columns'] = ['Stok Kodu', 'Barkod', 'Ürün', 'Son satış belgesi', 'Tarih']
        res['src_rows'] = src
        res['notes'].append('Hiç satılmamış ürünlerde bekleme süresi stok kartının oluşturulma tarihinden hesaplanır.')
        if len(items) > len(shown):
            res['notes'].append(f'Tabloda tutarı en yüksek {len(shown)} ürün gösterilir (toplam {len(items)}).')
        return res

    def _sold(self, days):
        d1 = (self.today() - datetime.timedelta(days=days - 1)).isoformat()
        return {r['stock_id']: float(r['q']) for r in self.rows(
            f"""SELECT sl.stock_id, SUM(sl.qty) q FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
                WHERE sl.stock_id IS NOT NULL AND s.status IN {ST_ALL} AND substr(s.dt,1,10)>=? GROUP BY sl.stock_id""", (d1,))}

    def t_reorder(self, limit=40):
        sold30 = self._sold(30)
        stocks = self.rows("SELECT id,code,barcode,name,qty,min_qty,max_qty,avg_cost,last_cost,unit FROM stock WHERE active=1")
        cover, safety = 30, 7
        def _ayar(k, d, lo, hi):
            try:
                rows = self.rows("SELECT scope,v FROM scoped_settings WHERE k=?", (k,))
                rank = {'Bu Bilgisayar': 5, 'Kullanıcı': 4, 'Dönem': 3, 'Firma': 2, 'Sistem': 1}
                rows = sorted([r for r in rows if r['v'] not in (None, '')], key=lambda r: rank.get(r['scope'], 0))
                if not rows:
                    rows = self.rows("SELECT v FROM settings WHERE k=?", (k,))
                    rows = [r for r in rows if r['v'] not in (None, '')]
                return max(lo, min(hi, int(float(rows[-1]['v'])))) if rows else d
            except Exception:
                return d
        cover = _ayar('asistan_cover_days', 30, 1, 365)
        safety = _ayar('asistan_safety_days', 7, 0, 365)
        items = []
        for s in stocks:
            qty, mn, mx = float(s['qty'] or 0), float(s['min_qty'] or 0), float(s['max_qty'] or 0)
            daily = max(0.0, sold30.get(s['id'], 0.0)) / 30.0
            days_left = (qty / daily) if daily > 0 else None
            reasons = []
            if mn > 0 and qty <= mn:
                reasons.append('Minimum stok altında')
            if days_left is not None and days_left < 10:
                reasons.append(f'~{days_left:.0f} gün yeter')
            if qty < 0:
                reasons.append('Negatif stok')
            if not reasons:
                continue
            target = daily * (cover + safety)
            need = max(target - qty, (mn - qty) if mn > 0 else 0, 0)
            if mx > 0:
                need = min(need, max(0, mx - qty)) if need > 0 else need
            sug = math.ceil(need) if need > 0 else 0
            if sug <= 0:
                continue
            cost = float(s['avg_cost'] or s['last_cost'] or 0)
            items.append({'s': s, 'qty': qty, 'min': mn, 'sold30': sold30.get(s['id'], 0.0), 'daily': daily,
                          'days_left': days_left, 'sug': sug, 'cost': cost, 'why': ' / '.join(reasons)})
        items.sort(key=lambda x: (x['days_left'] if x['days_left'] is not None else 9999, x['qty']))
        res = self.new('Sipariş önerisi', 'Son 30 günlük satış hızına göre')
        if not items:
            res['summary'] = ['Şu an siparişe ihtiyaç duyan ürün görünmüyor (minimum altı veya kısa sürede bitecek ürün yok).']
            return res
        shown = items[:int(limit)]
        tot = sum(i['sug'] * i['cost'] for i in shown)
        res['summary'] = [f"{len(items)} ürün için sipariş önerisi var; listelenen {len(shown)} ürünün tahmini maliyeti {fmt_money(tot)}.",
                          'En acil: ' + '; '.join(f"{i['s']['name']} (öneri {fmt_num(i['sug'])})" for i in shown[:3])]
        res['columns'] = ['Stok Kodu', 'Ürün', 'Mevcut', 'Min', '30 gün satış', 'Kaç gün yeter', 'Önerilen', 'Birim maliyet', 'Tutar', 'Neden']
        res['rows'] = [[i['s']['code'] or '', i['s']['name'], fmt_num(i['qty']), fmt_num(i['min']), fmt_num(i['sold30']),
                        f"{i['days_left']:.0f}".replace('.', ',') if i['days_left'] is not None else '—', fmt_num(i['sug']),
                        fmt_money(i['cost']), fmt_money(i['sug'] * i['cost']), i['why']] for i in shown]
        res['num_cols'] = [2, 3, 4, 5, 6, 7, 8]
        # kaynak: son 30 gündeki satış belgeleri (en acil 5 ürün)
        ids = [i['s']['id'] for i in shown[:5]]
        ph = ','.join('?' * len(ids))
        d1 = (self.today() - datetime.timedelta(days=29)).isoformat()
        src = self.rows(f"""SELECT s.doc_no,s.dt,sl.name,sl.qty FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
                            WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10)>=? AND sl.stock_id IN ({ph}) ORDER BY s.dt DESC LIMIT 300""", (d1, *ids))
        res['src_title'] = 'Kaynak: en acil ürünlerin son 30 gündeki satışları'
        res['src_columns'] = ['Belge No', 'Tarih', 'Ürün', 'Adet']
        res['src_rows'] = [[x['doc_no'], fmt_date(x['dt']), x['name'], fmt_num(x['qty'])] for x in src]
        res['src_num_cols'] = [3]
        res['notes'].append(f'Öneri = ({cover} gün + {safety} gün güvenlik) × günlük satış − mevcut stok; minimum stok ve maksimum stok dikkate alınır.')
        res['notes'].append('Sipariş oluşturmak için aşağıdaki düğmeyi kullanın; işlem onayınızla yalnız taslak (Sipariş Havuzu) oluşturur.')
        res['action'] = {'type': 'pool_order', 'label': f'SİPARİŞ HAVUZUNA EKLE ({len(shown)} ürün)…',
                         'lines': [{'stock_id': i['s']['id'], 'name': i['s']['name'], 'qty': i['sug'], 'cost': i['cost']} for i in shown]}
        return res

    def t_daily_brief(self):
        today = self.today()
        yday = today - datetime.timedelta(days=1)
        res = self.new('Günlük özet', fmt_date(today.isoformat()))
        rows, notes = [], []
        if any(self.can_view(m) for m in SALES_NEEDS):
            y = self._sales_totals(yday.isoformat(), yday.isoformat())
            t = self._sales_totals(today.isoformat(), today.isoformat())
            rows.append(['Dün net satış', fmt_money(y['net']), f"{y['n']} belge, ortalama sepet {fmt_money(y['avg'])}"])
            rows.append(['Bugün (şu ana kadar)', fmt_money(t['net']), f"{t['n']} belge"])
            if float(y['net'] or 0) > 0:
                rows[-1][2] += f"; dünün %{float(t['net'] or 0) / float(y['net']) * 100:.0f}'i"
            docs = self._sale_docs(yday.isoformat(), yday.isoformat())
            res['src_title'] = 'Kaynak: dünün satış belgeleri'
            res['src_columns'] = self.DOC_COLS
            res['src_rows'] = self._doc_rows(docs)
            res['src_num_cols'] = [4]
        else:
            notes.append('Satış bilgisi için yetkiniz yok; bu bölüm atlandı.')
        if any(self.can_view(m) for m in STOCK_NEEDS):
            c = self.one("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN qty<0 THEN 1 ELSE 0 END),0) neg FROM stock "
                         "WHERE active=1 AND (qty<0 OR (min_qty>0 AND qty<=min_qty))")
            rows.append(['Kritik stok', str(int(c['n'])), f"{int(c['neg'])} ürün negatif stokta" if int(c['neg']) else 'Negatif stok yok'])
            sl = self.t_slow_stock(90, 1)
            rows.append(['90 gündür satılmayan stok', sl['summary'][0].split(';')[0] if sl['summary'] else '—', 'Ayrıntı için "durgun stoklar" sorun'])
        else:
            notes.append('Stok bilgisi için yetkiniz yok; bu bölüm atlandı.')
        if any(self.can_view(m) for m in ('Cari Merkezi', 'Finans Merkezi', 'Rapor & Analiz')):
            d = self.rows("""SELECT c.id, SUM(m.debit)-SUM(m.credit) bal FROM caris c JOIN cari_moves m ON m.cari_id=c.id
                             WHERE c.active=1 GROUP BY c.id HAVING bal>0.005""")
            rows.append(['Toplam alacak (borçlu cariler)', fmt_money(sum(float(x['bal']) for x in d)), f'{len(d)} cari borçlu'])
        else:
            notes.append('Cari bilgisi için yetkiniz yok; bu bölüm atlandı.')
        res['columns'] = ['Başlık', 'Değer', 'Ayrıntı']
        res['rows'] = rows
        res['num_cols'] = [1]
        res['summary'] = [f'{r[0]}: {r[1]}  ({r[2]})' for r in rows] or ['Gösterilecek bölüm yok.']
        res['notes'] = notes + ['Tutarlar KDV dahildir; ayrıntı için ilgili soruyu ayrıca sorabilirsiniz (ör. "kritik stoklar", "borçlu müşteriler").']
        return res

    def t_critical_stock(self):
        stocks = self.rows("SELECT id,code,barcode,name,qty,min_qty FROM stock WHERE active=1 AND (qty<0 OR (min_qty>0 AND qty<=min_qty)) ORDER BY qty-min_qty")
        res = self.new('Kritik / negatif stok', 'Güncel durum')
        neg = [s for s in stocks if float(s['qty']) < 0]
        res['summary'] = [f"{len(stocks)} ürün kritik seviyede ({len(neg)} tanesi negatif stokta)."] if stocks else ['Kritik veya negatif stoklu ürün yok.']
        res['columns'] = ['Stok Kodu', 'Barkod', 'Ürün', 'Mevcut', 'Minimum', 'Durum']
        res['rows'] = [[s['code'] or '', s['barcode'] or '', s['name'], fmt_num(s['qty']), fmt_num(s['min_qty']),
                        'NEGATİF' if float(s['qty']) < 0 else 'Minimum altı'] for s in stocks[:200]]
        res['num_cols'] = [3, 4]
        res['src_title'] = 'Kaynak stok kartları'
        res['src_columns'] = res['columns']
        res['src_rows'] = res['rows']
        res['src_num_cols'] = [3, 4]
        return res

    def t_debtors(self, limit=15):
        rows = self.rows("""SELECT c.id,c.title,c.phone,SUM(m.debit)-SUM(m.credit) bal, MAX(substr(m.dt,1,10)) last_dt, COUNT(*) n
                            FROM caris c JOIN cari_moves m ON m.cari_id=c.id WHERE c.active=1 GROUP BY c.id HAVING bal>0.005 ORDER BY bal DESC LIMIT ?""", (int(limit),))
        res = self.new('Borçlu cariler', 'Güncel bakiye', sensitive=['Cari', 'Telefon'])
        if not rows:
            res['summary'] = ['Borç bakiyesi olan cari bulunmuyor.']
            return res
        tot = sum(float(r['bal']) for r in rows)
        res['summary'] = [f"En yüksek borç: {rows[0]['title']} — {fmt_money(rows[0]['bal'])}.",
                          f"Listelenen {len(rows)} carinin toplam borcu: {fmt_money(tot)}."]
        res['columns'] = ['Cari', 'Telefon', 'Bakiye (borç)', 'Son hareket', 'Hareket sayısı']
        res['rows'] = [[r['title'], r['phone'] or '', fmt_money(r['bal']), fmt_date(r['last_dt']), r['n']] for r in rows]
        res['num_cols'] = [2, 4]
        ids = [r['id'] for r in rows[:5]]
        ph = ','.join('?' * len(ids))
        src = self.rows(f"""SELECT c.title,m.dt,m.kind,m.debit,m.credit,m.note FROM cari_moves m JOIN caris c ON c.id=m.cari_id
                            WHERE m.cari_id IN ({ph}) ORDER BY m.dt DESC LIMIT 200""", tuple(ids))
        res['src_title'] = 'Kaynak cari hareketleri (ilk 5 cari)'
        res['src_columns'] = ['Cari', 'Tarih', 'Tür', 'Borç', 'Alacak', 'Not']
        res['src_rows'] = [[x['title'], fmt_date(x['dt']), x['kind'] or '', fmt_money(x['debit']), fmt_money(x['credit']), x['note'] or ''] for x in src]
        res['src_num_cols'] = [3, 4]
        res['notes'].append('Bakiye = toplam borç hareketleri − toplam alacak (tahsilat) hareketleri.')
        return res

    def t_profit(self, period='bu ay'):
        p = parse_period(period, self.today(), 'bu ay')
        d1, d2 = p.iso()
        vat = ("CASE WHEN COALESCE(sl.vat_amount,0)<>0 THEN sl.vat_amount "
               "ELSE sl.total*COALESCE(sl.vat_pct,0)/(100.0+COALESCE(sl.vat_pct,0)) END")
        r = self.one(f"""SELECT COALESCE(SUM(sl.total),0) gross, COALESCE(SUM({vat}),0) vat, COALESCE(SUM(COALESCE(sl.cost_snapshot,0)*sl.qty),0) cost
                         FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ?""", (d1, d2))
        gross, v, cost = float(r['gross']), float(r['vat']), float(r['cost'])
        net = gross - v
        prof = net - cost
        res = self.new('Kâr analizi', p.label)
        res['summary'] = [f"Brüt kâr: {fmt_money(prof)}  (kâr oranı {('%' + format(prof / net * 100, '.1f')).replace('.', ',') if net else '—'})",
                          f"Satış (KDV dahil): {fmt_money(gross)} − KDV: {fmt_money(v)} − Maliyet: {fmt_money(cost)}"]
        top = self.rows(f"""SELECT COALESCE(MAX(st.name),MAX(sl.name)) name, SUM(sl.qty) qty,
                                   SUM(sl.total-({vat})-COALESCE(sl.cost_snapshot,0)*sl.qty) prof
                            FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id
                            WHERE s.status IN {ST_ALL} AND substr(s.dt,1,10) BETWEEN ? AND ?
                            GROUP BY COALESCE(sl.stock_id,0), CASE WHEN sl.stock_id IS NULL THEN sl.name END ORDER BY prof DESC LIMIT 10""", (d1, d2))
        res['columns'] = ['#', 'Ürün', 'Adet', 'Brüt kâr']
        res['rows'] = [[i + 1, t['name'], fmt_num(t['qty']), fmt_money(t['prof'])] for i, t in enumerate(top)]
        res['num_cols'] = [0, 2, 3]
        docs = self._sale_docs(d1, d2)
        res['src_title'] = 'Kaynak satış belgeleri'
        res['src_columns'] = self.DOC_COLS
        res['src_rows'] = self._doc_rows(docs)
        res['src_num_cols'] = [4]
        res['notes'].append('Maliyet, satış anındaki maliyet kaydından (cost_snapshot) alınır. Eski kayıtlarda maliyet/KDV eksikse kâr olduğundan yüksek görünebilir.')
        res['notes'].append('Hizmet/fotokopi gelirleri bu hesaba dahil değildir.')
        return res

    def _find_stock(self, words, limit=15):
        words = [w for w in words if w]
        if not words:
            return []
        cond = ' AND '.join(["(lower(name) LIKE ? OR lower(COALESCE(brand,'')) LIKE ? OR COALESCE(barcode,'') LIKE ? OR lower(COALESCE(code,'')) LIKE ?)"] * len(words))
        params = []
        for w in words:
            params += [f'%{w}%'] * 4
        rows = self.rows(f"SELECT id,code,barcode,name,brand,qty,min_qty,retail,unit,active FROM stock WHERE {cond} ORDER BY active DESC,name LIMIT ?", (*params, limit))
        if rows:
            return rows
        # Türkçe karakter farkları için katlanmış karşılaştırma
        allrows = self.rows("SELECT id,code,barcode,name,brand,qty,min_qty,retail,unit,active FROM stock")
        out = [r for r in allrows if all(w in fold(f"{r['name']} {r['brand'] or ''} {r['barcode'] or ''} {r['code'] or ''}") for w in words)]
        return out[:limit]

    def t_product_lookup(self, query=''):
        words = self._product_words(fold(query)) if query else []
        res = self.new('Ürün sorgulama', query)
        if not words:
            res['summary'] = ['Hangi ürünü sorguladığınızı yazın (ürün adı, barkod veya stok kodu).']
            return res
        found = self._find_stock([w for w in words], 15)
        if not found:
            res['summary'] = [f"'{query}' ile eşleşen ürün bulunamadı."]
            return res
        last, sold30 = self._last_sales(), self._sold(30)
        res['summary'] = [f"{len(found)} ürün bulundu." + (f" {found[0]['name']}: stok {fmt_num(found[0]['qty'])} {found[0]['unit'] or ''}, satış fiyatı {fmt_money(found[0]['retail'])}." if len(found) == 1 else '')]
        res['columns'] = ['Stok Kodu', 'Barkod', 'Ürün', 'Stok', 'Min', 'Satış fiyatı', '30 gün satış', 'Son satış', 'Durum']
        res['rows'] = [[f['code'] or '', f['barcode'] or '', f['name'], fmt_num(f['qty']), fmt_num(f['min_qty']), fmt_money(f['retail']),
                        fmt_num(sold30.get(f['id'], 0)), fmt_date(last.get(f['id'])) if last.get(f['id']) else '—',
                        'Aktif' if f['active'] else 'Pasif'] for f in found]
        res['num_cols'] = [3, 4, 5, 6]
        res['src_title'] = 'Kaynak stok kartları'
        res['src_columns'] = res['columns']
        res['src_rows'] = res['rows']
        res['src_num_cols'] = [3, 4, 5, 6]
        return res


# --------------------------------------------------------------------------- yapay zekâ (isteğe bağlı)
SYSTEM_PROMPT = ("Sen ALFABE Satış Pro programının Türkçe işletme asistanısın. Kullanıcının sorularını YALNIZCA verilen araçlarla "
                 "program verisini okuyarak yanıtla. Rakamları ve ürün adlarını daima araç sonuçlarından al, asla uydurma. "
                 "Hiçbir kaydı değiştiremezsin; sipariş, ödeme, fiyat veya stok işlemi isteniyorsa bunu yapamayacağını, "
                 "yalnızca analiz sunabileceğini ve işlem için kullanıcının ekrandaki onaylı düğmeleri kullanması gerektiğini söyle. "
                 "Yanıtı kısa, net ve Türkçe yaz; tutarları 1.234,56 ₺ biçiminde göster; önemli belge numaralarını belirt. "
                 "Araç sonucunda yetki yok denirse bunu açıkça belirt. Veri bulunmazsa bunu dürüstçe söyle.")


def llm_tools():
    out = []
    for name, t in TOOLS.items():
        out.append({'name': name, 'description': t['desc'],
                    'input_schema': {'type': 'object', 'properties': t['params'], 'required': []}})
    return out


def llm_chat(api_key, model, messages, timeout=60):
    body = json.dumps({'model': model or DEFAULT_MODEL, 'max_tokens': 1500, 'system': SYSTEM_PROMPT,
                       'messages': messages, 'tools': llm_tools()}).encode('utf-8')
    req = urllib.request.Request(API_URL, data=body, method='POST', headers={
        'content-type': 'application/json', 'x-api-key': api_key, 'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise RuntimeError(f'API hatası {e.code}: {detail}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Bağlantı kurulamadı: {e.reason}')


def llm_answer(engine, api_key, model, question, history, chat=llm_chat):
    """(metin, son_sonuç) döndürür. history: [(soru, yanıt), ...] düz metin."""
    msgs = []
    for q, a in history[-6:]:
        msgs += [{'role': 'user', 'content': q}, {'role': 'assistant', 'content': a}]
    msgs.append({'role': 'user', 'content': question})
    last = None
    for _ in range(5):
        resp = chat(api_key, model, msgs)
        blocks = resp.get('content', [])
        uses = [b for b in blocks if b.get('type') == 'tool_use']
        if not uses or resp.get('stop_reason') != 'tool_use':
            text = '\n'.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
            return text or 'Yanıt alınamadı.', last
        msgs.append({'role': 'assistant', 'content': blocks})
        results = []
        for b in uses:
            res = engine.run(b.get('name', ''), **(b.get('input') or {}))
            last = res
            results.append({'type': 'tool_result', 'tool_use_id': b['id'],
                            'content': json.dumps(Engine.for_llm(res), ensure_ascii=False)})
        msgs.append({'role': 'user', 'content': results})
    return 'Yanıt oluşturulamadı (çok fazla araç çağrısı).', last


# --------------------------------------------------------------------------- arayüz
def build_assistant(host, ctx):
    """host: modül çerçevesi (Base). ctx: {'DX','role','user','APP','BG','TXT','MUT','RED','BORDER',
    'create_pool_order','audit','log_error'}"""
    DX = ctx['DX']
    BG, TXT, MUT, RED = ctx.get('BG', '#f4f6f9'), ctx.get('TXT', '#0b2540'), ctx.get('MUT', '#55708f'), ctx.get('RED', '#c8102e')
    APP = ctx.get('APP', 'ALFABE')
    role = ctx.get('role', '')
    is_admin = role == 'Yönetici'
    perm_cache = {}

    def can_view(module):
        if is_admin:
            return True
        if module not in perm_cache:
            try:
                r = DX.one('SELECT view FROM role_permissions WHERE role=? AND module=?', (role, module))
                perm_cache[module] = bool(r and int(r['view'] or 0))
            except Exception:
                perm_cache[module] = False
        return perm_cache[module]

    eng = Engine(DX.rows, DX.one, can_view)

    def can_do(module, what='view'):
        if what == 'view':
            return can_view(module)
        if is_admin:
            return True
        try:
            r = DX.one('SELECT add_rec, edit FROM role_permissions WHERE role=? AND module=?', (role, module))
            return bool(r and int(r['add_rec' if what == 'add' else 'edit'] or 0))
        except Exception:
            return False
    eng.can_do = can_do
    eng.back_dir = ctx.get('back_dir')
    state = {'history': [], 'result': None, 'busy': False, 'q': queue.Queue()}

    def setting(k, d=''):
        try:
            return DX.get(k, d)
        except Exception:
            return d

    def llm_on():
        return setting('ai_enabled', '0') == '1' and bool(setting('ai_api_key', '').strip())

    # ---- üst bilgi
    head = tk.Frame(host, bg='white', highlightthickness=1, highlightbackground=ctx.get('BORDER', '#d6e0ea'))
    head.pack(fill='x', padx=24, pady=(0, 8))
    tk.Label(head, text='Verilerinizi normal cümlelerle sorgulayın. Asistan yalnızca okur; hiçbir kaydı kendiliğinden değiştirmez, '
                        'işlem gerektiren önerilerde onayınızı ister.', bg='white', fg=MUT, wraplength=1100, justify='left',
             font=('Segoe UI', 10)).pack(anchor='w', padx=12, pady=(8, 2))
    mode_var = tk.StringVar()
    mrow = tk.Frame(head, bg='white')
    mrow.pack(fill='x', padx=12, pady=(0, 8))
    tk.Label(mrow, textvariable=mode_var, bg='white', fg=TXT, font=('Segoe UI', 9, 'bold')).pack(side='left')

    def refresh_mode():
        mode_var.set('Kip: Yapay zekâ (Claude) — soru ve araç sonuçları API\'ye gönderilir' if llm_on()
                     else 'Kip: Yerel analiz — internet gerekmez, veri bilgisayardan çıkmaz')
    refresh_mode()
    chips = tk.Frame(head, bg='white')
    chips.pack(fill='x', padx=12, pady=(0, 8))

    # ---- sohbet kaydı
    chat_box = tk.Frame(host, bg='white', highlightthickness=1, highlightbackground=ctx.get('BORDER', '#d6e0ea'))
    chat_box.pack(fill='x', padx=24, pady=(0, 8))
    log = tk.Text(chat_box, height=9, wrap='word', bg='white', relief='flat', font=('Segoe UI', 10), state='disabled', padx=10, pady=8)
    sb = ttk.Scrollbar(chat_box, orient='vertical', command=log.yview)
    log.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    log.pack(fill='x')
    log.tag_configure('user', foreground='#0b5a9d', font=('Segoe UI', 10, 'bold'))
    log.tag_configure('bot', foreground=TXT)
    log.tag_configure('meta', foreground=MUT, font=('Segoe UI', 9, 'italic'))
    log.tag_configure('head', foreground=TXT, font=('Segoe UI', 10, 'bold'))

    def put(tag, text):
        log.configure(state='normal')
        log.insert('end', text + '\n', tag)
        log.configure(state='disabled')
        log.see('end')

    # ---- sonuç sekmeleri
    nb = ttk.Notebook(host)
    nb.pack(fill='both', expand=True, padx=24, pady=(0, 6))
    trees = {}
    holders = {}

    def make_tree(key):
        fr, vs, hs = holders[key]
        tv = ttk.Treeview(fr, show='headings', height=9)
        vs.configure(command=tv.yview)
        hs.configure(command=tv.xview)
        tv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tv.pack(fill='both', expand=True)
        trees[key] = tv
        return tv

    for key, title in (('res', 'Sonuç'), ('src', 'Kaynak Kayıtlar')):
        fr = tk.Frame(nb, bg='white')
        nb.add(fr, text=title)
        vs = ttk.Scrollbar(fr, orient='vertical')
        hs = ttk.Scrollbar(fr, orient='horizontal')
        hs.pack(side='bottom', fill='x')
        vs.pack(side='right', fill='y')
        holders[key] = (fr, vs, hs)
        make_tree(key)

    def fill(tv, cols, rows, num_cols):
        # v461.83: sütun değişimi bazı Tk sürümlerinde "Invalid column index c0" verebilir; o durumda tablo yeniden kurulur.
        try:
            return _fill(tv, cols, rows, num_cols)
        except Exception as _e1:
            try:
                key = next(k for k, v in trees.items() if v is tv)
                try:
                    tv.destroy()
                except Exception:
                    pass
                return _fill(make_tree(key), cols, rows, num_cols)
            except Exception as _e2:
                try:
                    ctx['log_error']('AI Asistan tablo', _e2)
                except Exception:
                    pass

    def _fill(tv, cols, rows, num_cols):
        tv.delete(*tv.get_children())
        tv['columns'] = [f'c{i}' for i in range(len(cols))] if cols else []
        for i, c in enumerate(cols):
            sample = [len(str(r[i])) for r in rows[:60] if i < len(r)] or [0]
            w = max(70, min(380, 9 * max(len(c), max(sample)) + 18))
            tv.heading(f'c{i}', text=c)
            tv.column(f'c{i}', width=w, anchor='e' if i in num_cols else 'w', stretch=False)
        for r in rows:
            tv.insert('', 'end', values=[('' if v is None else v) for v in r])

    abar = tk.Frame(host, bg=BG)
    abar.pack(fill='x', padx=24)
    extra_btns = []
    sug_row = tk.Frame(host, bg=BG)
    sug_row.pack(fill='x', padx=24, pady=(2, 0))
    act_btn = tk.Button(abar, text='', bg=RED, fg='white', font=('Segoe UI', 10, 'bold'), padx=12, pady=4)
    csv_btn = tk.Button(abar, text='TABLOYU CSV OLARAK KAYDET', font=('Segoe UI', 9, 'bold'), padx=10, pady=4)
    csv_btn.pack(side='left')
    xlsx_btn = tk.Button(abar, text='TABLOYU EXCEL OLARAK KAYDET', font=('Segoe UI', 9, 'bold'), padx=10, pady=4, bg='#1b7f3b', fg='white')
    xlsx_btn.pack(side='left', padx=(6, 0))

    def export_csv():
        res = state['result']
        if not res or not res.get('columns'):
            return messagebox.showinfo(APP, 'Dışa aktarılacak tablo yok.', parent=host)
        path = filedialog.asksaveasfilename(parent=host, defaultextension='.csv', filetypes=[('CSV', '*.csv')],
                                            initialfile='asistan_sonuc.csv')
        if not path:
            return
        import csv
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(res['columns'])
            w.writerows(res['rows'])
            if res['src_rows']:
                w.writerow([])
                w.writerow([res['src_title']])
                w.writerow(res['src_columns'])
                w.writerows(res['src_rows'])
        messagebox.showinfo(APP, 'Kaydedildi:\n' + path, parent=host)
    csv_btn.configure(command=export_csv)

    def export_xlsx():
        res = state['result']
        if not res or not res.get('columns'):
            return messagebox.showinfo(APP, 'Dışa aktarılacak tablo yok.', parent=host)
        path = filedialog.asksaveasfilename(parent=host, defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')],
                                            initialfile='asistan_sonuc.xlsx')
        if not path:
            return
        try:
            write_result_xlsx(path, res)
        except Exception as e:
            try:
                ctx['log_error']('AI Asistan Excel', e)
            except Exception:
                pass
            return messagebox.showerror(APP, 'Excel dosyası yazılamadı (dosya açık olabilir):\n' + str(e), parent=host)
        messagebox.showinfo(APP, 'Kaydedildi:\n' + path, parent=host)
    xlsx_btn.configure(command=export_xlsx)

    def show(res):
        state['result'] = res
        fill(trees['res'], res['columns'], res['rows'], res['num_cols'])
        fill(trees['src'], res['src_columns'], res['src_rows'], res['src_num_cols'])
        nb.tab(1, text=f"Kaynak Kayıtlar ({len(res['src_rows'])})" if res['src_rows'] else 'Kaynak Kayıtlar')
        nb.select(0)
        for b in extra_btns:
            b.destroy()
        extra_btns.clear()
        for c in sug_row.winfo_children():
            c.destroy()
        acts = list(res.get('actions') or [])
        if res.get('action'):
            act_btn.configure(text=res['action']['label'], command=lambda: do_action(res['action']))
            act_btn.pack(side='left', padx=8)
        else:
            act_btn.pack_forget()
        for a in acts:
            b = tk.Button(abar, text=a['label'], command=lambda a=a: do_action(a), bg='#0b5a9d', fg='white', font=('Segoe UI', 10, 'bold'), padx=12, pady=4)
            b.pack(side='left', padx=6)
            extra_btns.append(b)
        if res.get('suggest'):
            tk.Label(sug_row, text='Önerilen sorular:', bg=BG, fg=MUT, font=('Segoe UI', 9)).pack(side='left')
            for sx in res['suggest'][:5]:
                tk.Button(sug_row, text=sx, command=lambda t=sx: ask(t), bg='#eaf2fb', fg='#0b3158', relief='flat', font=('Segoe UI', 9),
                          padx=8, pady=2, cursor='hand2').pack(side='left', padx=4)
        state['actions'] = acts

    def do_action(a):
        if a['type'] in ('goto', 'open_custom', 'form', 'iban_verify'):
            if _A2 is None:
                return
            return _A2.run_action(host, ctx, eng, a, put, messagebox, APP)
        if a['type'] != 'pool_order':
            return
        lines = a['lines']
        pre = '\n'.join(f"• {x['name']} — {fmt_num(x['qty'])} adet" for x in lines[:12]) + (f"\n… ve {len(lines) - 12} ürün daha" if len(lines) > 12 else '')
        if not ctx.get('create_pool_order'):
            return messagebox.showinfo(APP, 'Bu kurulumda sipariş oluşturma kapalı.', parent=host)
        if not can_view('Sipariş & Teklif') and not is_admin:
            return messagebox.showwarning(APP, 'Sipariş oluşturmak için Sipariş & Teklif bölümü yetkiniz gerekir.', parent=host)
        if not messagebox.askyesno(APP, f"{len(lines)} ürün için 'Sipariş Havuzu'nda BİR SİPARİŞ TASLAĞI oluşturulacak:\n\n{pre}\n\n"
                                        "Bu işlem stok ve finans kayıtlarını DEĞİŞTİRMEZ; yalnızca sipariş kaydı ekler. Onaylıyor musunuz?", parent=host):
            put('meta', 'İşlem iptal edildi; hiçbir kayıt değiştirilmedi.')
            return
        try:
            doc = ctx['create_pool_order'](lines)
            put('bot', f'Sipariş taslağı oluşturuldu: {doc}  (Sipariş & Teklif bölümünde görebilirsiniz.)')
            act_btn.pack_forget()
        except Exception as e:
            try:
                ctx['log_error']('AI Asistan sipariş', e)
            except Exception:
                pass
            messagebox.showerror(APP, 'Sipariş oluşturulamadı:\n' + str(e), parent=host)

    # ---- soru-cevap
    def answer_popup(question, text):
        # v461.82: Cevap, programın ortasında açılan ayrı bir pencerede de yazılır.
        try:
            top = host.winfo_toplevel()
            win = state.get('pop')
            if win is not None:
                try:
                    if not win.winfo_exists():
                        win = None
                except Exception:
                    win = None
            if win is None:
                win = tk.Toplevel(top)
                win.title('AI Asistan — Cevap')
                win.configure(bg='white')
                try:
                    win.transient(top)
                except Exception:
                    pass
                tb = tk.Frame(win, bg='white')
                tb.pack(side='bottom', fill='x', padx=12, pady=(0, 10))
                tk.Button(tb, text='KAPAT', command=win.destroy, bg=RED, fg='white', font=('Segoe UI', 10, 'bold'), padx=22, pady=4).pack(side='right')
                qlab = tk.Label(win, text='', bg='white', fg='#0b5a9d', font=('Segoe UI', 11, 'bold'), wraplength=860, justify='left', anchor='w')
                qlab.pack(fill='x', padx=16, pady=(14, 6))
                body = tk.Frame(win, bg='white')
                body.pack(fill='both', expand=True, padx=12)
                tx = tk.Text(body, wrap='word', bg='white', relief='flat', font=('Segoe UI', 12), padx=10, pady=6)
                sbp = ttk.Scrollbar(body, orient='vertical', command=tx.yview)
                tx.configure(yscrollcommand=sbp.set)
                sbp.pack(side='right', fill='y')
                tx.pack(fill='both', expand=True)
                win.bind('<Escape>', lambda e: win.destroy())
                win._qlab, win._tx = qlab, tx
                state['pop'] = win
                ww, wh = 920, 520
                try:
                    top.update_idletasks()
                    px, py, pw, ph = top.winfo_rootx(), top.winfo_rooty(), top.winfo_width(), top.winfo_height()
                    if pw < 300 or ph < 300:
                        px, py, pw, ph = 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()
                    x = max(0, px + (pw - ww) // 2)
                    y = max(0, py + (ph - wh) // 2)
                    win.geometry(f'{ww}x{wh}+{x}+{y}')
                except Exception:
                    win.geometry(f'{ww}x{wh}')
            win._qlab.configure(text='Soru: ' + str(question))
            win._tx.configure(state='normal')
            win._tx.delete('1.0', 'end')
            win._tx.insert('1.0', str(text))
            win._tx.configure(state='disabled')
            win.lift()
            try:
                win.focus_force()
            except Exception:
                pass
        except Exception as _e:
            try:
                ctx['log_error']('AI Asistan cevap penceresi', _e)
            except Exception:
                pass

    def finish(question, text, res, note=''):
        state['busy'] = False
        send_btn.configure(state='normal')
        answer_popup(question, text)
        if note:
            put('meta', note)
        put('head', 'Asistan:')
        put('bot', text)
        put('meta', '')
        state['history'].append((question, text))
        if res:
            try:
                show(res)
            except Exception as _e:
                try:
                    ctx['log_error']('AI Asistan sonuç tablosu', _e)
                except Exception:
                    pass
        try:
            ctx['audit']('AI Asistan', 'Soru', 0, new=f"{question} → {(res or {}).get('tool', '-')}")
        except Exception:
            pass

    def ask(question=None):
        q = (question or entry.get()).strip()
        if not q or state['busy']:
            return
        entry.delete(0, 'end')
        put('user', 'Siz: ' + q)
        if llm_on():
            state['busy'] = True
            send_btn.configure(state='disabled')
            put('meta', 'Yapay zekâ düşünüyor…')

            def work():
                try:
                    text, res = llm_answer(eng, setting('ai_api_key', '').strip(), setting('ai_model', DEFAULT_MODEL).strip() or DEFAULT_MODEL,
                                           q, state['history'])
                    state['q'].put(('ok', q, text, res, ''))
                except Exception as e:
                    state['q'].put(('err', q, str(e), None, ''))
            threading.Thread(target=work, daemon=True).start()
            host.after(200, poll)
        else:
            try:
                res = eng.answer(q)
                finish(q, Engine.text_of(res), res)
            except Exception as e:
                try:
                    ctx['log_error']('AI Asistan', e)
                except Exception:
                    pass
                finish(q, 'Bu soru yanıtlanırken bir hata oluştu: ' + str(e), None)

    def poll():
        try:
            kind, q, text, res, note = state['q'].get_nowait()
        except queue.Empty:
            if state['busy']:
                host.after(200, poll)
            return
        if kind == 'err':
            try:
                r2 = eng.answer(q)
                finish(q, Engine.text_of(r2), r2, note=f'Yapay zekâ bağlantısı kurulamadı ({text}). Yerel analiz kullanıldı.')
            except Exception as e:
                finish(q, 'Hata: ' + str(e), None)
        else:
            finish(q, text, res)

    # ---- giriş satırı
    inp = tk.Frame(host, bg=BG)
    inp.pack(fill='x', padx=24, pady=(4, 14))
    entry = tk.Entry(inp, font=('Segoe UI', 12), bg='#fff9c4', insertbackground='black')
    entry.pack(side='left', fill='x', expand=True, ipady=5)
    entry.bind('<Return>', lambda e: ask())
    entry.bind('<KP_Enter>', lambda e: ask())
    send_btn = tk.Button(inp, text='SOR', command=ask, bg=RED, fg='white', font=('Segoe UI', 10, 'bold'), padx=18, pady=3)
    send_btn.pack(side='left', padx=8)
    tk.Button(inp, text='TEMİZLE', command=lambda: (log.configure(state='normal'), log.delete('1.0', 'end'), log.configure(state='disabled'),
                                                    state['history'].clear(), setattr(eng, 'last', None)), font=('Segoe UI', 9, 'bold'), padx=10, pady=3).pack(side='left')
    if is_admin:
        tk.Button(mrow, text='YAPAY ZEKÂ AYARLARI', command=lambda: settings_dialog(), font=('Segoe UI', 9, 'bold'), padx=8).pack(side='right')

    for i, ex in enumerate(['Günlük özet'] + EXAMPLES + (_A2.chip_examples() if _A2 else [])):
        tk.Button(chips, text=ex, command=lambda t=ex: ask(t), bg='#eaf2fb', fg='#0b3158', relief='flat', font=('Segoe UI', 9),
                  padx=8, pady=3, cursor='hand2').pack(side='left', padx=(0, 6), pady=2)

    # ---- yapay zekâ ayarları (yalnız Yönetici)
    def settings_dialog():
        w = tk.Toplevel(host)
        w.title('Yapay Zekâ Ayarları')
        w.geometry('620x400')
        w.configure(bg='white')
        try:
            w.transient(host.winfo_toplevel())
            w.grab_set()
        except Exception:
            pass
        on = tk.IntVar(value=1 if setting('ai_enabled', '0') == '1' else 0)
        key = tk.StringVar(value=setting('ai_api_key', ''))
        model = tk.StringVar(value=setting('ai_model', DEFAULT_MODEL) or DEFAULT_MODEL)
        tk.Label(w, text='Yapay Zekâ (Claude) Kipi', bg='white', fg=TXT, font=('Segoe UI', 14, 'bold')).pack(anchor='w', padx=18, pady=(14, 4))
        tk.Label(w, text='Kapalıyken asistan tamamen yerel çalışır (internet gerekmez). Açarsanız serbest cümleler daha iyi anlaşılır; '
                         'soru ve analiz sonuçları (tutar, ürün adı) Anthropic API\'sine gönderilir. Cari unvanı ve telefon gibi alanlar '
                         'maskelenir. Asistan yine hiçbir kaydı değiştiremez.', bg='white', fg=MUT, wraplength=580, justify='left').pack(anchor='w', padx=18)
        tk.Checkbutton(w, text='Yapay zekâ kipini kullan', variable=on, bg='white', font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=18, pady=(12, 4))
        tk.Label(w, text='Anthropic API anahtarı', bg='white').pack(anchor='w', padx=18)
        tk.Entry(w, textvariable=key, show='•', width=64).pack(anchor='w', padx=18, pady=(0, 6))
        tk.Label(w, text='Model', bg='white').pack(anchor='w', padx=18)
        tk.Entry(w, textvariable=model, width=30).pack(anchor='w', padx=18, pady=(0, 6))
        msg = tk.Label(w, text='', bg='white', fg=MUT, wraplength=580, justify='left')
        msg.pack(anchor='w', padx=18, pady=4)

        def save():
            for k, v in (('ai_enabled', '1' if on.get() else '0'), ('ai_api_key', key.get().strip()), ('ai_model', model.get().strip() or DEFAULT_MODEL)):
                DX.exec("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            refresh_mode()
            w.destroy()

        def test():
            if not key.get().strip():
                return msg.configure(text='Önce Anthropic API anahtarınızı yukarıdaki kutuya yapıştırın. Yapay zekâ kipini kullanmak istemiyorsanız bu ayara gerek yoktur; asistan anahtarsız da çalışır.', fg=RED)
            msg.configure(text='Bağlantı sınanıyor…', fg=MUT)
            w.update_idletasks()
            try:
                r = llm_chat(key.get().strip(), model.get().strip() or DEFAULT_MODEL, [{'role': 'user', 'content': 'Sadece "tamam" yaz.'}], timeout=30)
                msg.configure(text='Bağlantı başarılı.', fg='#1b7f3b')
            except Exception as e:
                t = str(e)
                if '401' in t:
                    t = 'API anahtarı geçersiz ya da hatalı yapıştırılmış. Anahtarı kontrol edip tekrar deneyin.\n(' + t[:120] + ')'
                msg.configure(text=t, fg=RED)
        bb = tk.Frame(w, bg='white')
        bb.pack(fill='x', padx=18, pady=10)
        tk.Button(bb, text='BAĞLANTIYI SINA', command=test).pack(side='left')
        tk.Button(bb, text='KAYDET', command=save, bg=RED, fg='white', font=('Segoe UI', 10, 'bold'), padx=14).pack(side='right')
        tk.Button(bb, text='VAZGEÇ', command=w.destroy).pack(side='right', padx=8)

    put('meta', 'Merhaba! Aşağıdaki örneklerden birine tıklayın veya kendi sorunuzu yazın.')
    try:
        _b = eng.run('daily_brief')
        if _b.get('rows'):
            put('head', 'Günlük özet:')
            put('bot', Engine.text_of(_b))
            show(_b)
    except Exception as _e:
        try:
            ctx['log_error']('AI Asistan günlük özet', _e)
        except Exception:
            pass
    entry.focus_set()
    return {'engine': eng, 'ask': ask, 'do_action': do_action, 'state': state}


try:
    import ALFABE_ASISTAN2 as _A2
    _A2.install(sys.modules[__name__])
except Exception as _e2:   # v2 katmanı yüklenemezse eski asistan aynen çalışır
    _A2 = None
