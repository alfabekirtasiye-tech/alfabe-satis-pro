# -*- coding: utf-8 -*-
"""ALFABE Veri Aktarım Merkezi - ortak motor.

Diğer satış / ön muhasebe programlarının (Akınsoft Wolvox, Mikro, Logo, Netsis, ETA, Nebim, Paraşüt, Ideasoft, pazaryeri dışa
aktarımları vb.) veri aktarım ekranlarındaki iyi uygulamalar tek sihirbazda toplanmıştır:

  * Dosya: .xlsx / .xlsm / .xls (Excel, HTML ya da XML tabanlı .xls dahil) / .csv / .tsv / .txt. Karakter kodlaması ve ayırıcı
    otomatik bulunur (UTF-8, Windows-1254). Birden çok sayfa varsa sayfa seçilir.
  * Sütun eşleme: başlıklardan otomatik; elle değiştirilebilir; aynı başlık düzenindeki sonraki dosyalar için hatırlanır.
  * Kopya yönetimi: yalnız yeni / yalnız güncelle / yeni + güncelle. Eşleşme anahtarı Stok'ta Barkod/Stok Kodu,
    Cari'de Cari Kodu/Vergi No/Ünvan'dır. Dosya içindeki tekrarlar hata sayılır.
  * Önizleme: her satır YENİ / GÜNCELLE / ATLA / HATA; uyarılar ayrıca gösterilir; filtre + arama; hata raporu (CSV).
  * Güvence: uygulamadan önce doğrulanmış TAM YEDEK; tek işlem (hata = hiçbir kayıt değişmez); denetim kaydı;
    aktarım sonrası "önce / sonra" kontrol raporu; aktarım geçmişi.
  * Deneme aktarımı: yalnız ilk 20 satırı aktarma seçeneği (pilot test).
  * Cari: VKN / TCKN doğrulaması (resmi algoritma), telefon / e-posta düzeltme, açılış bakiyesi (Borç/Alacak yönü).
"""
import csv
import datetime
import hashlib
import html as _html
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import ALFABE_STOK_EXCEL as XL

MAX_ROWS = XL.MAX_ROWS
PILOT_ROWS = 20


# ======================================================================================================================
# Genel yardımcılar
# ======================================================================================================================
def tr_key(s):
    """Türkçe harf / boşluk / noktalama farkından bağımsız karşılaştırma anahtarı."""
    t = str(s or '').strip().replace('İ', 'i').replace('I', 'ı').lower()
    for a, b in (('ı', 'i'), ('ş', 's'), ('ğ', 'g'), ('ü', 'u'), ('ö', 'o'), ('ç', 'c')):
        t = t.replace(a, b)
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def upper_tr(s):
    return str(s or '').replace('i', 'İ').replace('ı', 'I').upper()


def collapse(s):
    return re.sub(r'\s+', ' ', str(s or '').replace('\u00a0', ' ')).strip()


def money_txt(v):
    try:
        return ('{:,.2f}'.format(float(v))).replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(v)


def qty_txt(v):
    try:
        f = float(v)
        t = ('%.3f' % f).rstrip('0').rstrip('.')
        return (t or '0').replace('.', ',')
    except Exception:
        return str(v)


# ======================================================================================================================
# Dosya okuma (xlsx / xls / csv ...)
# ======================================================================================================================
class _HtmlTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._row, self._cell, self._depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif tag == 'tr' and self._depth >= 1:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []
        elif tag == 'br' and self._cell is not None:
            self._cell.append(' ')

    def handle_endtag(self, tag):
        if tag == 'table':
            self._depth = max(0, self._depth - 1)
        elif tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._row.append(collapse(''.join(self._cell)))
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self.tables:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _decode_text(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    for enc in ('utf-8-sig', 'cp1254', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', 'replace')


def _spreadsheetml_sheets(text):
    """Excel 2003 XML (SpreadsheetML) -> {sayfa adı: satırlar}."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text.encode('utf-8') if isinstance(text, str) else text)
    ns = 'urn:schemas-microsoft-com:office:spreadsheet'
    out = {}
    for k, ws in enumerate(root.iter('{%s}Worksheet' % ns), 1):
        name = ws.get('{%s}Name' % ns) or ('Sayfa%d' % k)
        rows = []
        for row in ws.iter('{%s}Row' % ns):
            cells, idx = [], 0
            for cell in row.findall('{%s}Cell' % ns):
                ci = cell.get('{%s}Index' % ns)
                if ci:
                    idx = int(ci) - 1
                while len(cells) < idx:
                    cells.append(None)
                d = cell.find('{%s}Data' % ns)
                val = None
                if d is not None:
                    t = (d.get('{%s}Type' % ns) or 'String')
                    txt = ''.join(d.itertext())
                    if t == 'Number':
                        try:
                            val = float(txt)
                        except ValueError:
                            val = txt
                    else:
                        val = txt
                cells.append(val)
                idx += 1
            rows.append(cells)
        out[name] = rows
    return out


def _convert_xls_to_xlsx(path):
    """Gerçek (ikili) .xls için: LibreOffice veya Excel (Windows) varsa .xlsx'e çevirir. Yoksa None."""
    tmp = Path(tempfile.mkdtemp(prefix='alfabe_xls_'))
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    cands = [shutil.which('soffice'), shutil.which('libreoffice'),
             r'C:\Program Files\LibreOffice\program\soffice.exe', r'C:\Program Files (x86)\LibreOffice\program\soffice.exe']
    for exe in cands:
        if exe and Path(exe).exists():
            try:
                subprocess.run([exe, '--headless', '--convert-to', 'xlsx', '--outdir', str(tmp), str(path)],
                               check=True, timeout=120, capture_output=True, creationflags=flags)
                out = tmp / (Path(path).stem + '.xlsx')
                if out.exists():
                    return out
            except Exception:
                pass
    if os.name == 'nt':
        dst = tmp / (Path(path).stem + '.xlsx')
        ps = ("$ErrorActionPreference='Stop';$xl=New-Object -ComObject Excel.Application;$xl.Visible=$false;$xl.DisplayAlerts=$false;"
              "try{$wb=$xl.Workbooks.Open($env:ALF_SRC,0,$true);$wb.SaveAs($env:ALF_DST,51);$wb.Close($false)}finally{$xl.Quit()}")
        try:
            env = dict(os.environ, ALF_SRC=str(path), ALF_DST=str(dst))
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], check=True, timeout=120,
                           capture_output=True, env=env, creationflags=flags)
            if dst.exists():
                return dst
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
    return None


_XLS_CACHE = {}


def _xls_sheets(path):
    """.xls dosyasını {sayfa adı: satırlar} olarak okur (HTML / SpreadsheetML / ikili). Aynı dosya tekrar dönüştürülmez."""
    try:
        stt = Path(path).stat()
        key = (str(path), stt.st_mtime_ns, stt.st_size)
    except OSError:
        key = None
    if key and key in _XLS_CACHE:
        return _XLS_CACHE[key]
    res = _xls_sheets_uncached(path)
    if key:
        _XLS_CACHE.clear()
        _XLS_CACHE[key] = res
    return res


def _xls_sheets_uncached(path):
    raw = Path(path).read_bytes()
    if raw[:4] == b'PK\x03\x04':
        return {n: XL.read_xlsx(path, n) for n in XL.xlsx_sheets(path)}
    if raw[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        try:
            import xlrd
            wb = xlrd.open_workbook(str(path))
            return {sh.name: [[(c if c != '' else None) for c in sh.row_values(i)] for i in range(sh.nrows)] for sh in wb.sheets()}
        except ImportError:
            pass
        conv = _convert_xls_to_xlsx(path)
        if conv:
            try:
                return {n: XL.read_xlsx(conv, n) for n in XL.xlsx_sheets(conv)}
            finally:
                shutil.rmtree(conv.parent, ignore_errors=True)
        raise ValueError('Bu dosya eski ikili Excel (.xls) biçimindedir ve bu bilgisayarda okunamadı.\n'
                         'Excel\'de dosyayı açıp "Farklı Kaydet → Excel Çalışma Kitabı (.xlsx)" ile kaydedin ya da CSV olarak kaydedin.')
    text = _decode_text(raw)
    low = text[:4000].lower()
    if 'urn:schemas-microsoft-com:office:spreadsheet' in low:
        return _spreadsheetml_sheets(text)
    if '<table' in text.lower():
        p = _HtmlTables()
        p.feed(text)
        tabs = [t for t in p.tables if t]
        if not tabs:
            raise ValueError('Dosyada tablo bulunamadı.')
        return {('Tablo %d' % (i + 1)): t for i, t in enumerate(tabs)}
    return {'Sayfa1': _csv_rows(text)}


def _csv_rows(text):
    first = next((ln for ln in text.splitlines() if ln.strip()), '')
    delim = max((';', ',', '\t', '|'), key=lambda d: first.count(d))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _to_table(data):
    hi = next((i for i, r in enumerate(data) if any(str(x if x is not None else '').strip() for x in r)), None)
    if hi is None:
        raise ValueError('Dosyada veri bulunamadı.')
    headers, seen = [], {}
    for j, x in enumerate(data[hi]):
        h = collapse(x if x is not None else '')
        if not h:
            h = 'Sütun %d' % (j + 1)
        if h in seen:                       # aynı başlık iki kez -> ayırt et
            seen[h] += 1
            h = '%s (%d)' % (h, seen[h])
        else:
            seen[h] = 1
        headers.append(h)
    rows = [(i + 1, r) for i, r in enumerate(data) if i > hi and any(str(x if x is not None else '').strip() for x in r)]
    if len(rows) > MAX_ROWS:
        raise ValueError('Dosya çok büyük (en çok %d satır). Dosyayı bölerek aktarın.' % MAX_ROWS)
    return headers, rows


def list_sheets(path):
    ext = Path(path).suffix.lower()
    if ext in ('.xlsx', '.xlsm'):
        return XL.xlsx_sheets(path)
    if ext == '.xls':
        try:
            return list(_xls_sheets(path).keys()) or ['Sayfa1']
        except Exception:
            return ['Sayfa1']
    return ['Sayfa1']


def read_file(path, sheet=None):
    """(başlıklar, [(satır_no, [hücreler])]) döndürür. Ham dosya hataları ValueError olarak yükselir."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in ('.xlsx', '.xlsm'):
            data = XL.read_xlsx(p, sheet)
        elif ext == '.xls':
            sheets = _xls_sheets(p)
            data = sheets.get(sheet) if sheet in sheets else next(iter(sheets.values()))
        elif ext in ('.csv', '.txt', '.tsv'):
            data = _csv_rows(_decode_text(p.read_bytes()))
        else:
            raise ValueError('Desteklenen dosya türleri: .xlsx, .xls, .csv, .tsv, .txt')
    except zipfile.BadZipFile:
        raise ValueError('Dosya geçerli bir Excel (.xlsx) dosyası değil ya da bozuk.')
    return _to_table(data)


# ======================================================================================================================
# Sütun eşleme (genel) + kayıtlı eşlemeler
# ======================================================================================================================
def auto_map_fields(headers, fields):
    """fields: [(anahtar, etiket, [eş anlamlılar], tür)]. {anahtar: sütun indeksi}. Önce birebir, sonra önek eşleşmesi."""
    hn = [XL.norm(h) for h in headers]
    mapping, used = {}, set()
    for exact in (True, False):
        for key, _lab, syns, _t in fields:
            if key in mapping:
                continue
            syns = set(syns) | {XL.norm(_lab)}               # şablondaki etiketin kendisi her zaman tanınır
            for i, h in enumerate(hn):
                if i in used or not h:
                    continue
                if (h in syns) if exact else any(len(sy) >= 5 and h.startswith(sy) for sy in syns):
                    mapping[key] = i
                    used.add(i)
                    break
    return mapping


def header_signature(headers):
    return hashlib.sha1('|'.join(sorted(XL.norm(h) for h in headers if XL.norm(h))).encode('utf-8')).hexdigest()[:12]


def _map_key(kind, headers):
    return 'import_map::%s::%s' % (kind, header_signature(headers))


def load_saved_mapping(c, kind, headers):
    try:
        r = c.execute('SELECT v FROM settings WHERE k=?', (_map_key(kind, headers),)).fetchone()
        if not r or not r[0]:
            return None
        saved = json.loads(r[0])
        hn = {XL.norm(h): i for i, h in enumerate(headers)}
        m = {}
        for key, hname in saved.items():
            i = hn.get(XL.norm(hname))
            if i is not None:
                m[key] = i
        return m or None
    except Exception:
        return None


def save_mapping(c, kind, headers, mapping):
    try:
        data = {k: headers[i] for k, i in mapping.items() if i is not None and i < len(headers)}
        c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)', (_map_key(kind, headers), json.dumps(data, ensure_ascii=False)))
        c.commit()
    except Exception:
        pass


def forget_mapping(c, kind, headers):
    try:
        c.execute('DELETE FROM settings WHERE k=?', (_map_key(kind, headers),))
        c.commit()
    except Exception:
        pass


# ======================================================================================================================
# CARİ motoru
# ======================================================================================================================
CARI_FIELDS = [
    ('cari_code', 'Cari Kodu', ['carikodu', 'carikod', 'kod', 'kodu', 'musterikodu', 'hesapkodu', 'hesapno', 'carino', 'musterino', 'cariid', 'tedarikcikodu',
                                'firmakodu', 'cariknumarasi', 'hesapkod', 'carihesapkodu'], 'id'),
    ('title', 'Ünvan / Ad Soyad', ['unvan', 'unvani', 'cariunvan', 'cariunvani', 'cariadi', 'cariisim', 'adsoyad', 'adisoyadi', 'musteriadi', 'musteriunvani',
                                  'hesapadi', 'firmaadi', 'firmaunvani', 'isim', 'ad', 'tamunvan', 'carihesapadi', 'tedarikciadi', 'cariunvan1', 'unvan1',
                                  'definition', 'faturaunvani', 'adi', 'hesapunvani', 'tanim', 'musteri', 'firma', 'cariismi', 'adiunvani', 'adunvan'], 'text'),
    ('role', 'Cari Türü', ['caritipi', 'caritur', 'carituru', 'turu', 'tur', 'tipi', 'tip', 'hesaptipi', 'hesapturu', 'musteritedarikci', 'kartturu',
                          'sinif', 'cardtype', 'cariturur'], 'role'),
    ('tax_no', 'Vergi No / TCKN', ['vergino', 'vergikimlikno', 'vkn', 'tckn', 'vkntckn', 'tcvkn', 'tcno', 'tckimlikno', 'kimlikno', 'vergitckn',
                                  'vergitckimlikno', 'verginumarasi', 'vergino1', 'taxnr', 'taxno', 'carivdaireno', 'vdaireno', 'tcvergino',
                                  'vergikimliknumarasi', 'vergiveyatckimlikno', 'tckimliknovkn'], 'tax'),
    ('tax_office', 'Vergi Dairesi', ['vergidairesi', 'vergidairesiadi', 'vd', 'vdairesi', 'vdaire', 'carivdaireadi', 'taxoffice', 'vdadi', 'vergidaire'], 'text'),
    ('phone', 'Telefon', ['telefon', 'tel', 'telefon1', 'tel1', 'telno', 'telefonno', 'gsm', 'cep', 'ceptelefonu', 'ceptel', 'cepno', 'cepnumarasi',
                         'telnrs1', 'sabittelefon', 'isyeritelefonu', 'telefonnumarasi', 'mobil', 'phone', 'ceptelefon', 'caricep'], 'phone'),
    ('email', 'E-posta', ['eposta', 'epostaadresi', 'email', 'emailadresi', 'mail', 'emailaddr', 'epostaadres', 'mailadresi', 'eposta1'], 'email'),
    ('city', 'İl', ['il', 'sehir', 'sehiril', 'city', 'ili', 'carivil'], 'text'),
    ('district', 'İlçe', ['ilce', 'town', 'semt', 'ilcesi'], 'text'),
    ('address', 'Adres', ['adres', 'adres1', 'acikadres', 'faturaadresi', 'adresi', 'addr1', 'address', 'teslimatadresi', 'adresbilgisi'], 'text'),
    ('grp', 'Cari Grubu', ['grup', 'grubu', 'carigrubu', 'grupkodu', 'carigrup', 'kategori', 'musterigrubu', 'grupadi'], 'text'),
    ('risk', 'Risk Limiti', ['risklimiti', 'risk', 'kredilimiti', 'kredilimit', 'limit', 'carilimit', 'borclimiti', 'risklimit'], 'num'),
    ('due_days', 'Vade (gün)', ['vade', 'vadegun', 'vadegunu', 'vadesi', 'odemevadesi', 'vadesure', 'ortvade', 'vadegunsayisi', 'odemegunu'], 'int'),
    ('web', 'Web Sitesi', ['web', 'website', 'webadresi', 'internetadresi', 'www', 'webadres', 'webssayfasi'], 'text'),
    ('contact_name', 'Yetkili Kişi', ['yetkili', 'yetkilikisi', 'yetkiliadi', 'iletisimkisisi', 'ilgilikisi', 'yetkiliadsoyad', 'yetkiliisim'], 'text'),
    ('contact_phone', 'Yetkili Telefon', ['yetkilitelefon', 'yetkilitel', 'yetkilicep', 'yetkilitelefonu', 'yetkilitelno'], 'phone'),
    ('contact_email', 'Yetkili E-posta', ['yetkilieposta', 'yetkilimail', 'yetkiliemail', 'yetkiliepostaadresi'], 'email'),
    ('balance', 'Açılış Bakiyesi', ['bakiye', 'acilisbakiyesi', 'acilisbakiye', 'devirbakiye', 'devir', 'toplambakiye', 'guncelbakiye', 'caribakiye',
                                   'carihesapbakiyesi', 'netbakiye', 'bakiyetutari', 'bakiyetutar', 'bakiyesi', 'kalanbakiye', 'acilistutari'], 'bal'),
    ('balance_dir', 'Bakiye Yönü (Borç/Alacak)', ['bakiyetipi', 'bakiyeyonu', 'borcalacak', 'bakiyeborcalacak', 'yon', 'bt', 'bakiyeturu', 'dengesi',
                                                  'bakiyedurumu', 'ba', 'bakiyeba', 'borcalacakdurumu'], 'dir'),
    ('debit', 'Borç Tutarı', ['borc', 'borctutari', 'borctoplami', 'toplamborc', 'borctutar', 'debit'], 'bal'),
    ('credit', 'Alacak Tutarı', ['alacak', 'alacaktutari', 'alacaktoplami', 'toplamalacak', 'alacaktutar', 'credit'], 'bal'),
]
CARI_KEYS = [f[0] for f in CARI_FIELDS]
CARI_LABEL = {f[0]: f[1] for f in CARI_FIELDS}
CARI_TYPE = {f[0]: f[3] for f in CARI_FIELDS}
CARI_UPDATE_KEYS = ['title', 'role', 'tax_no', 'tax_office', 'phone', 'email', 'city', 'district', 'address', 'grp', 'risk', 'due_days', 'web',
                    'contact_name', 'contact_phone', 'contact_email']
ROLE_BASE = ('Müşteri', 'Tedarikçi', 'Ortak')
_INFO_PREFIX = ('Cari kodu otomatik', 'Stok kodu otomatik', 'Barkod yok', 'Mevcut stok miktarı değiştirilmedi')


def _nw(msgs):
    """Bilgi amaçlı notlar dışındaki uyarı sayısı."""
    return sum(1 for m in msgs if not str(m).startswith(_INFO_PREFIX))


_EMAIL_RE = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')


def cari_auto_map(headers):
    return auto_map_fields(headers, CARI_FIELDS)


def valid_tckn(s):
    if not (s.isdigit() and len(s) == 11 and s[0] != '0'):
        return False
    d = [int(x) for x in s]
    if ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def valid_vkn(s):
    if not (s.isdigit() and len(s) == 10):
        return False
    d = [int(x) for x in s]
    tot = 0
    for i in range(9):
        t = (d[i] + (9 - i)) % 10
        v = (t * (2 ** (9 - i))) % 9
        if t != 0 and v == 0:
            v = 9
        tot += v
    return (10 - tot % 10) % 10 == d[9]


def norm_tax(raw):
    """(değer, uyarı). Sayı olarak saklanmış VKN'nin kaybolan baştaki sıfırı tamamlanır."""
    t = XL.id_text(raw)
    t = re.sub(r'[\s.\-/]', '', t)
    if not t:
        return '', ''
    if not t.isdigit():
        return t, 'Vergi No / TCKN rakam olmalı: ' + t
    if len(t) == 9:
        return '0' + t, 'Vergi No 9 haneliydi, başına 0 eklendi'
    if len(t) == 10:
        return t, '' if valid_vkn(t) else 'VKN algoritma kontrolünden geçmedi: ' + t
    if len(t) == 11:
        return t, '' if valid_tckn(t) else 'TCKN algoritma kontrolünden geçmedi: ' + t
    return t, 'Vergi No / TCKN 10 veya 11 hane olmalı (%d hane): %s' % (len(t), t)


def norm_phone(raw):
    t = XL.id_text(raw) if isinstance(raw, (int, float)) else collapse(raw)
    if not t:
        return ''
    digits = re.sub(r'\D', '', t)
    if re.fullmatch(r'[\d\s()+\-./]+', t) and digits:
        if digits.startswith('90') and len(digits) == 12:
            digits = '0' + digits[2:]
        if len(digits) == 10 and digits[0] in '5432':
            digits = '0' + digits
        return digits
    return t                                   # "0212 111 22 33 / 444" gibi serbest metin olduğu gibi kalır


def norm_role(raw, default_role):
    t = tr_key(raw)
    if not t:
        return default_role, False
    if 'tedar' in t or t in ('satici', 'supplier', 'vendor'):
        return 'Tedarikçi', False
    if 'must' in t or t in ('alici', 'customer', 'client'):
        return 'Müşteri', False
    if 'ortak' in t:
        return 'Ortak', False
    return upper_tr(collapse(raw))[:40], True


def _dir_sign(raw):
    """'B' / 'Borç' / '(B)' -> +1; 'A' / 'Alacak' / '(A)' -> -1; anlaşılmazsa 0."""
    t = tr_key(raw).replace(' ', '')
    if not t:
        return 0
    if t in ('b', 'borc', 'bb', 'borclu', 'd', 'debit', 'borc b'):
        return 1
    if t in ('a', 'alacak', 'aa', 'alacakli', 'c', 'credit'):
        return -1
    if t.startswith('borc'):
        return 1
    if t.startswith('alacak'):
        return -1
    return 0


def parse_balance(raw, dir_raw, sign_mode):
    """Açılış bakiyesini ALFABE yönüne çevirir: + = cari bize borçlu (borç), - = alacak. Boşsa None.
    sign_mode: 'borc' (+ borç / - alacak) ya da 'alacak' (+ alacak / - borç)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    suffix = 0
    if isinstance(raw, str):
        m = re.match(r'^\s*([-+]?[\d.,\s]+?)\s*[\(\[]?\s*(borç|borc|alacak|bb|aa|b|a|d|c)?\s*[\)\]]?\s*$', raw.strip(), re.I)
        if m:
            raw = m.group(1)
            if m.group(2):
                suffix = _dir_sign(m.group(2))
        else:
            raw = raw.strip()
    v = XL.parse_num(raw)
    if v is None:
        return None
    sgn = _dir_sign(dir_raw) if dir_raw is not None else 0
    if suffix:
        sgn = suffix
    if sgn:
        return abs(v) * sgn
    return v if sign_mode == 'borc' else -v


def _cari_index(c):
    rows = [dict(r) for r in c.execute('SELECT * FROM caris').fetchall()]
    by_id = {int(r['id']): r for r in rows}
    by_code, by_tax, by_title = {}, {}, {}
    for r in rows:
        cid = int(r['id'])
        code = str(r.get('cari_code') or '').strip()
        if code:
            by_code[code] = cid
        tx = re.sub(r'\D', '', str(r.get('tax_no') or ''))
        if tx:
            by_tax.setdefault(tx, []).append(cid)
        k = tr_key(r.get('title'))
        if k:
            by_title.setdefault(k, []).append(cid)
    return by_id, by_code, by_tax, by_title


def _next_cari_code_factory(by_code, reserved=()):
    mx = max([int(x) for x in by_code if x.isdigit()] or [0])
    st = {'n': mx}

    def nxt():
        while True:
            st['n'] += 1
            k = str(st['n'])
            if k not in by_code and k not in reserved:      # dosyada sonradan gelen açık kodlarla da çakışmaz
                return k
    return nxt


def build_cari_plan(c, headers, rows, mapping, mode='both', default_role='Müşteri', sign_mode='borc', strict_tax=False, use_balance=True):
    """Her satır için dict: row_no, action (YENİ/GÜNCELLE/ATLA/HATA), msg (hata/uyarılar), warn (uyarı sayısı), data, changes, sid, balance."""
    by_id, by_code, by_tax, by_title = _cari_index(c)
    ci = mapping.get('cari_code')
    reserved = {XL.id_text(cells[ci]) for _rn, cells in rows if ci is not None and ci < len(cells) and XL.id_text(cells[ci])}
    next_code = _next_cari_code_factory(by_code, reserved)
    seen_code, seen_tax, seen_title, seen_cid = {}, {}, {}, {}
    plan = []
    for row_no, cells in rows:
        def g(key):
            i = mapping.get(key)
            return None if i is None or i >= len(cells) else cells[i]

        res = {'row_no': row_no, 'action': 'ATLA', 'msg': [], 'warn': 0, 'data': {}, 'changes': {}, 'sid': None, 'balance': None}
        errs, warns, data = [], [], {}
        # ---- alan ayrıştırma ----
        title = collapse(XL.txt(g('title')))
        code = XL.id_text(g('cari_code'))
        tax, tw = norm_tax(g('tax_no')) if 'tax_no' in mapping else ('', '')
        if tw:
            (errs if (strict_tax and not tw.startswith('Vergi No 9 haneliydi')) else warns).append(tw)
        if title:
            data['title'] = title[:200]
        if tax:
            data['tax_no'] = tax
        for key in CARI_UPDATE_KEYS:
            if key in ('title', 'tax_no'):
                continue
            raw = g(key)
            if raw is None or str(raw).strip() == '':
                continue
            t = CARI_TYPE[key]
            try:
                if t == 'phone':
                    data[key] = norm_phone(raw)
                elif t == 'email':
                    em = collapse(XL.txt(raw)).lower()
                    if _EMAIL_RE.match(em):
                        data[key] = em
                    else:
                        warns.append('%s geçersiz, aktarılmadı: %s' % (CARI_LABEL[key], em))
                elif t == 'num':
                    v = XL.parse_num(raw)
                    if v is not None:
                        if v < 0:
                            raise ValueError('negatif olamaz')
                        data[key] = round(v, 2)
                elif t == 'int':
                    v = XL.parse_num(raw)
                    if v is not None:
                        if v < 0 or v > 3650:
                            raise ValueError('0-3650 arası olmalı')
                        data[key] = int(round(v))
                elif t == 'role':
                    r, custom = norm_role(raw, default_role)
                    data[key] = r
                    if custom:
                        warns.append('Özel cari türü kullanılacak: ' + r)
                else:
                    data[key] = collapse(XL.txt(raw))
            except (ValueError, TypeError) as ex:
                errs.append('%s: %s' % (CARI_LABEL[key], ex))
        # ---- bakiye ----
        bal = None
        if use_balance:
            try:
                if g('balance') is not None and str(g('balance')).strip() != '':
                    bal = parse_balance(g('balance'), g('balance_dir') if 'balance_dir' in mapping else None, sign_mode)
                elif ('debit' in mapping or 'credit' in mapping):
                    d = XL.parse_num(g('debit')) or 0.0
                    cr = XL.parse_num(g('credit')) or 0.0
                    bal = (d - cr) if (d or cr) else None
            except (ValueError, TypeError):
                errs.append('Bakiye sayıya çevrilemedi: %s' % (g('balance'),))
        if bal is not None:
            bal = round(bal, 2)
            if abs(bal) < 0.005:
                bal = None
        # ---- eşleşme ----
        cid_code = by_code.get(code) if code else None
        if cid_code == -1:
            cid_code = None
        cid_tax = None
        if tax:
            lst = by_tax.get(tax, [])
            if len(lst) > 1:
                errs.append('Bu vergi no sistemde %d kartta kayıtlı (Cari Kodu ile eşleyin)' % len(lst))
            elif lst:
                cid_tax = lst[0]
        if cid_code and cid_tax and cid_code != cid_tax:
            errs.append('Cari Kodu ve Vergi No farklı iki kartı gösteriyor (#%s / #%s)' % (cid_code, cid_tax))
        cid = cid_code or cid_tax
        matched_by = 'kod' if cid_code else ('vergi no' if cid_tax else '')
        if cid is None and title and not code:
            lst = by_title.get(tr_key(title), [])
            cand = [i for i in lst if not (tax and re.sub(r'\D', '', str(by_id[i].get('tax_no') or '')) not in ('', tax))]
            if len(cand) > 1:
                errs.append('Aynı ünvanda %d cari kartı var; Cari Kodu veya Vergi No ile eşleyin' % len(cand))
            elif cand:
                cid, matched_by = cand[0], 'ünvan'
        if not code and not tax and not title:
            res.update(action='ATLA', msg=['Satır boş / tanımlanamadı'])
            plan.append(res)
            continue
        # ---- dosya içi tekrarlar ----
        ident = []
        if code:
            ident.append(('kod', code, seen_code))
        if tax:
            ident.append(('vergi no', tax, seen_tax))
        if title:
            tk_ = tr_key(title)
            if not code and not tax:
                if tk_ in seen_title:
                    errs.append('Aynı ünvan dosyada tekrar ediyor (satır %d); ayırt etmek için Cari Kodu veya Vergi No ekleyin' % seen_title[tk_])
            seen_title.setdefault(tk_, row_no)
        for lab, val, store in ident:
            if val in store:
                errs.append('Aynı %s dosyada tekrar ediyor (satır %d)' % (lab, store[val]))
            else:
                store[val] = row_no
        if cid is not None:
            if cid in seen_cid:
                errs.append('Bu cari kartı dosyada daha önce de geçiyor (satır %d)' % seen_cid[cid])
            else:
                seen_cid[cid] = row_no
        if errs:
            res.update(action='HATA', msg=errs + warns, warn=_nw(warns))
            plan.append(res)
            continue
        # ---- YENİ ----
        if cid is None:
            if mode == 'update':
                res.update(action='ATLA', msg=['Cari bulunamadı (yalnız güncelleme modu)'] + warns, warn=_nw(warns))
                plan.append(res)
                continue
            if not title:
                res.update(action='HATA', msg=['Yeni cari için Ünvan zorunlu'] + warns, warn=_nw(warns))
                plan.append(res)
                continue
            if not code:
                code = next_code()
                warns.append('Cari kodu otomatik: ' + code)
            data.setdefault('role', default_role)
            full = dict(data)
            full['cari_code'] = code
            by_code[code] = -1                      # yalnız sonraki otomatik kodların çakışmaması için
            res.update(action='YENİ', data=full, msg=warns, warn=_nw(warns), balance=bal)
            plan.append(res)
            continue
        # ---- mevcut kart ----
        if mode == 'new':
            res.update(action='ATLA', sid=cid, msg=['Cari zaten kayıtlı (%s ile eşleşti; yalnız yeni ekleme modu)' % matched_by] + warns, warn=_nw(warns))
            plan.append(res)
            continue
        cur = by_id.get(cid, {})
        changes = {}
        for k in CARI_UPDATE_KEYS:
            if k in data:
                if k == 'title' and matched_by == 'ünvan':
                    continue
                old = cur.get(k)
                if CARI_TYPE[k] in ('num', 'int'):
                    if abs(float(old or 0) - float(data[k])) > 0.0049:
                        changes[k] = (float(old or 0), data[k])
                elif k == 'tax_no':
                    if re.sub(r'\D', '', str(old or '')) != data[k]:
                        changes[k] = (old or '', data[k])
                elif str(old or '').strip() != str(data[k]).strip():
                    changes[k] = (old or '', data[k])
        if code and not str(cur.get('cari_code') or '').strip():
            changes['cari_code'] = ('', code)
        msg = list(warns)
        if bal is not None:
            msg.append('Mevcut cariye açılış bakiyesi uygulanmaz (bakiye değişmedi)')
        if not changes:
            res.update(action='ATLA', sid=cid, msg=['Değişiklik yok (%s ile eşleşti)' % matched_by] + msg, warn=_nw(warns))
            plan.append(res)
            continue
        res.update(action='GÜNCELLE', sid=cid, changes=changes, data=data, msg=msg, warn=_nw(warns))
        plan.append(res)
    summary = {k: sum(1 for p in plan if p['action'] == k) for k in ('YENİ', 'GÜNCELLE', 'ATLA', 'HATA')}
    summary['UYARI'] = sum(1 for p in plan if p.get('warn'))
    return plan, summary


def describe_cari(p):
    if p['action'] == 'YENİ':
        d = p['data']
        bits = [d.get('role', ''), ('VN ' + d['tax_no']) if d.get('tax_no') else '', d.get('phone', ''), d.get('city', '')]
        if p.get('balance') is not None:
            bits.append('açılış %s %s' % (money_txt(abs(p['balance'])), 'BORÇ' if p['balance'] > 0 else 'ALACAK'))
        txt = ' | '.join(b for b in bits if b)
        return txt + ('   [' + '; '.join(p['msg']) + ']' if p['msg'] else '')
    if p['action'] == 'GÜNCELLE':
        parts = []
        for k, (a, b) in p['changes'].items():
            lab = CARI_LABEL.get(k, k)
            parts.append('%s: %s → %s' % (lab, a if a not in ('', None) else '∅', b))
        return '; '.join(parts) + ('   [' + '; '.join(p['msg']) + ']' if p['msg'] else '')
    return '; '.join(p['msg'])


def apply_cari_plan(c, plan, user='', source_name='', open_date=None, backup=''):
    """Planı tek işlemde uygular. Hata olursa hiçbir kayıt değişmez."""
    todo = [p for p in plan if p['action'] in ('YENİ', 'GÜNCELLE')]
    if not todo:
        raise ValueError('Uygulanacak satır yok.')
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    open_dt = ((open_date or stamp[:10]) + ' 00:00:00') if len(str(open_date or '')) == 10 else stamp
    batch = 'CARI-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    cols_all = {r[1] for r in c.execute('PRAGMA table_info(caris)').fetchall()}
    n_new = n_upd = n_move = 0
    bal_total = 0.0
    cur_row = None
    c.execute('BEGIN IMMEDIATE')
    try:
        for p in todo:
            cur_row = p['row_no']
            if p['action'] == 'YENİ':
                d = {k: v for k, v in p['data'].items() if k in cols_all}
                if 'active' in cols_all:
                    d['active'] = 1
                names = list(d.keys())
                cid = c.execute('INSERT INTO caris(%s) VALUES(%s)' % (','.join(names), ','.join('?' * len(names))), [d[k] for k in names]).lastrowid
                bal = p.get('balance')
                if bal is not None:
                    c.execute('INSERT INTO cari_moves(cari_id,dt,kind,debit,credit,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)',
                              (cid, open_dt, 'Açılış Bakiyesi', max(bal, 0.0), max(-bal, 0.0), 'IMPORT_OPEN', None, 'Excel aktarım (%s)' % batch))
                    n_move += 1
                    bal_total += bal
                role = d.get('role')
                if role and role not in ROLE_BASE:
                    try:
                        c.execute('CREATE TABLE IF NOT EXISTS cari_types (name TEXT PRIMARY KEY COLLATE NOCASE)')
                        c.execute('INSERT OR IGNORE INTO cari_types(name) VALUES(?)', (role,))
                    except sqlite3.Error:
                        pass
                n_new += 1
            else:
                sets = {k: v[1] for k, v in p['changes'].items() if k in cols_all}
                if sets:
                    c.execute('UPDATE caris SET %s WHERE id=?' % ','.join(k + '=?' for k in sets), list(sets.values()) + [p['sid']])
                role = sets.get('role')
                if role and role not in ROLE_BASE:
                    try:
                        c.execute('CREATE TABLE IF NOT EXISTS cari_types (name TEXT PRIMARY KEY COLLATE NOCASE)')
                        c.execute('INSERT OR IGNORE INTO cari_types(name) VALUES(?)', (role,))
                    except sqlite3.Error:
                        pass
                n_upd += 1
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (stamp, user, 'Cari', 'Excel İçe Aktarım', 0, '',
                   json.dumps({'yeni': n_new, 'guncellenen': n_upd, 'bakiye_hareketi': n_move, 'toplu_no': batch}, ensure_ascii=False),
                   'Dosya: %s; yedek: %s' % (source_name, backup)))
        c.commit()
    except Exception as ex:
        c.rollback()
        raise RuntimeError('Aktarım geri alındı (hiçbir kayıt değişmedi). Satır %s: %s' % (cur_row, ex))
    return {'yeni': n_new, 'guncel': n_upd, 'hareket': n_move, 'bakiye_toplam': round(bal_total, 2), 'toplu_no': batch}


def export_cari(c, path, template=False):
    headers = [CARI_LABEL[k] for k in CARI_KEYS]
    text_cols = {i for i, k in enumerate(CARI_KEYS) if CARI_TYPE[k] in ('id', 'tax', 'phone')}
    if template:
        sample = {'cari_code': '1001', 'title': 'ÖRNEK MÜŞTERİ LTD. ŞTİ.', 'role': 'Müşteri', 'tax_no': '1234567890', 'tax_office': 'KADIKÖY',
                  'phone': '05320000000', 'email': 'ornek@firma.com', 'city': 'İSTANBUL', 'district': 'KADIKÖY', 'address': 'Örnek Mah. Örnek Sok. No:1',
                  'grp': 'PERAKENDE', 'risk': 5000.0, 'due_days': 30, 'web': '', 'contact_name': 'Ayşe Yılmaz', 'contact_phone': '', 'contact_email': '',
                  'balance': 1250.50, 'balance_dir': 'Borç', 'debit': '', 'credit': ''}
        XL.write_xlsx(path, headers, [[sample.get(k, '') for k in CARI_KEYS]], sheet_name='Cari', text_cols=text_cols)
        return 1
    bal = {int(r[0]): float(r[1] or 0) for r in c.execute('SELECT cari_id,SUM(debit-credit) FROM cari_moves GROUP BY cari_id').fetchall()}
    rows = []
    for r in c.execute('SELECT * FROM caris WHERE COALESCE(active,1)=1 ORDER BY title').fetchall():
        d = dict(r)
        b = bal.get(int(d['id']), 0.0)
        d['balance'] = abs(b) if abs(b) > 0.004 else ''
        d['balance_dir'] = ('Borç' if b > 0 else 'Alacak') if abs(b) > 0.004 else ''
        d['debit'] = d['credit'] = ''
        rows.append([(d.get(k) if d.get(k) is not None else '') for k in CARI_KEYS])
    XL.write_xlsx(path, headers, rows, sheet_name='Cari', text_cols=text_cols)
    return len(rows)


# ======================================================================================================================
# Stok / Fiyat yardımcıları (motor: ALFABE_STOK_EXCEL)
# ======================================================================================================================
PRICE_FIELD_KEYS = ('barcode', 'code', 'retail', 'wholesale', 'special_price')
PRICE_FIELDS = [f for f in XL.FIELDS if f[0] in PRICE_FIELD_KEYS]


def build_stock_plan(c, headers, rows, mapping, kind, mode, default_vat, sales_ex_vat, cost_inc_vat, update_qty):
    if kind == 'fiyat':
        mapping = {k: v for k, v in mapping.items() if k in PRICE_FIELD_KEYS}
        mode = 'update'
        update_qty = False
    return XL.build_plan(c, headers, rows, mapping, mode, default_vat, sales_ex_vat, cost_inc_vat, update_qty)


# ======================================================================================================================
# Kontrol raporu (önce / sonra)
# ======================================================================================================================
def snapshot(c):
    def one(sql):
        try:
            r = c.execute(sql).fetchone()
            return float(r[0] or 0)
        except sqlite3.Error:
            return 0.0
    return {'stok_adet': one('SELECT COUNT(*) FROM stock WHERE COALESCE(active,1)=1'),
            'stok_miktar': one('SELECT COALESCE(SUM(qty),0) FROM stock WHERE COALESCE(active,1)=1'),
            'stok_deger': one('SELECT COALESCE(SUM(qty*avg_cost),0) FROM stock WHERE COALESCE(active,1)=1'),
            'cari_adet': one('SELECT COUNT(*) FROM caris WHERE COALESCE(active,1)=1'),
            'cari_borc': one('SELECT COALESCE(SUM(debit),0) FROM cari_moves'),
            'cari_alacak': one('SELECT COALESCE(SUM(credit),0) FROM cari_moves'),
            'cari_bakiye': one('SELECT COALESCE(SUM(debit-credit),0) FROM cari_moves')}


def build_report(kind, before, after, plan, res, source, backup, update_qty=False):
    """Kontrol maddeleri: (başlık, beklenen, gerçekleşen, sonuç)."""
    checks = []
    yeni = sum(1 for p in plan if p['action'] == 'YENİ')
    if kind == 'cari':
        checks.append(('Cari kart sayısı farkı', yeni, after['cari_adet'] - before['cari_adet']))
        exp_bal = round(sum((p.get('balance') or 0.0) for p in plan if p['action'] == 'YENİ'), 2)
        checks.append(('Cari bakiye toplamı farkı (borç - alacak)', exp_bal, round(after['cari_bakiye'] - before['cari_bakiye'], 2)))
        checks.append(('Eklenen açılış hareketi', sum(1 for p in plan if p['action'] == 'YENİ' and p.get('balance') is not None), res.get('hareket', 0)))
    elif kind == 'stok':
        checks.append(('Stok kartı sayısı farkı', yeni, after['stok_adet'] - before['stok_adet']))
        exp_q = sum(float(p['data'].get('qty') or 0) for p in plan if p['action'] == 'YENİ')
        if update_qty:
            exp_q += sum(p['changes']['qty'][1] - p['changes']['qty'][0] for p in plan if p['action'] == 'GÜNCELLE' and 'qty' in p['changes'])
        checks.append(('Toplam stok miktarı farkı', round(exp_q, 3), round(after['stok_miktar'] - before['stok_miktar'], 3)))
    else:
        checks.append(('Stok kartı sayısı (değişmemeli)', 0, after['stok_adet'] - before['stok_adet']))
        checks.append(('Toplam stok miktarı (değişmemeli)', 0, round(after['stok_miktar'] - before['stok_miktar'], 3)))
    out = []
    for name, exp, got in checks:
        out.append({'name': name, 'expected': exp, 'actual': got, 'ok': abs(float(exp) - float(got)) < 0.005})
    return {'type': 'excel', 'kind': kind, 'source': source, 'backup': str(backup), 'before': before, 'after': after, 'result': res,
            'checks': out, 'counts': {k: sum(1 for p in plan if p['action'] == k) for k in ('YENİ', 'GÜNCELLE', 'ATLA', 'HATA')}}


KIND_NAME = {'stok': 'Stok kartları', 'fiyat': 'Fiyat güncelleme', 'cari': 'Cari kartları'}


def report_text(rep):
    b, a = rep.get('before', {}), rep.get('after', {})
    L = ['AKTARIM KONTROL RAPORU', '=' * 60, 'Tür     : ' + KIND_NAME.get(rep.get('kind'), rep.get('kind', '')),
         'Dosya   : ' + str(rep.get('source', '')), 'Yedek   : ' + str(rep.get('backup', '')), '']
    cn = rep.get('counts', {})
    L.append('Yeni: %s   Güncellenen: %s   Atlanan: %s   Hatalı (aktarılmadı): %s' % (cn.get('YENİ', 0), cn.get('GÜNCELLE', 0), cn.get('ATLA', 0), cn.get('HATA', 0)))
    L.append('')
    L.append('%-26s %18s %18s' % ('', 'Aktarım ÖNCESİ', 'Aktarım SONRASI'))
    rows = [('Stok kartı', 'stok_adet', qty_txt), ('Toplam stok miktarı', 'stok_miktar', qty_txt), ('Stok değeri (ort. maliyet)', 'stok_deger', money_txt),
            ('Cari kartı', 'cari_adet', qty_txt), ('Cari toplam borç', 'cari_borc', money_txt), ('Cari toplam alacak', 'cari_alacak', money_txt),
            ('Cari net bakiye (B-A)', 'cari_bakiye', money_txt)]
    for lab, key, f in rows:
        L.append('%-26s %18s %18s' % (lab, f(b.get(key, 0)), f(a.get(key, 0))))
    L += ['', 'KONTROLLER']
    for ch in rep.get('checks', []):
        L.append('  [%s] %s: beklenen %s / gerçekleşen %s' % ('TAMAM' if ch['ok'] else 'FARK VAR', ch['name'], qty_txt(ch['expected']), qty_txt(ch['actual'])))
    L += ['', 'Önerilen son kontrol: birkaç kartı elle açıp dosyadaki değerlerle karşılaştırın; bakiye ve stok toplamlarını eski programla eşleştirin.']
    return '\n'.join(L)


def record_history(c, kind, source, plan, backup, status, rep=None, note=''):
    cn = {k: sum(1 for p in plan if p['action'] == k) for k in ('YENİ', 'GÜNCELLE', 'ATLA', 'HATA')}
    c.execute('INSERT INTO import_history(dt,kind,source_file,total,inserted,updated,skipped,errors,backup_file,status,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), {'stok': 'STOK EXCEL', 'fiyat': 'FİYAT EXCEL', 'cari': 'CARİ EXCEL'}.get(kind, kind), str(source),
               len(plan), cn['YENİ'], cn['GÜNCELLE'], cn['ATLA'], cn['HATA'], str(backup or ''), status,
               json.dumps(rep, ensure_ascii=False, default=str) if rep else note))
    c.commit()


def error_report(plan, path, kind='stok'):
    desc = describe_cari if kind == 'cari' else XL.describe
    with open(str(path), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(['Dosya Satırı', 'Durum', 'Açıklama'])
        for p in plan:
            if p['action'] in ('HATA', 'ATLA') or p.get('warn'):
                w.writerow([p['row_no'], p['action'] if p['action'] in ('HATA', 'ATLA') else p['action'] + ' (uyarılı)', '; '.join(p['msg']) if p['msg'] else desc(p)])
    return path


def template_file(kind, path):
    """Boş şablon (.xlsx ya da .csv; uzantıya göre) + örnek satır."""
    path = str(path)
    is_csv = path.lower().endswith('.csv')
    if kind == 'cari':
        hdr = [CARI_LABEL[k] for k in CARI_KEYS]
        sample = dict(zip(CARI_KEYS, ['1001', 'ÖRNEK MÜŞTERİ LTD. ŞTİ.', 'Müşteri', '1234567890', 'KADIKÖY', '05320000000', 'ornek@firma.com', 'İSTANBUL', 'KADIKÖY',
                                      'Örnek Mah. Örnek Sok. No:1', 'PERAKENDE', '5000', '30', '', 'Ayşe Yılmaz', '', '', '1250,50', 'Borç', '', '']))
        if not is_csv:
            export_cari(None, path, template=True)
            return path
    elif kind == 'fiyat':
        hdr = [XL.FIELD_LABEL[k] for k in PRICE_FIELD_KEYS]
        sample = {'barcode': '8680000000017', 'code': '1001', 'retail': '18,00', 'wholesale': '15,00', 'special_price': ''}
        if not is_csv:
            XL.write_xlsx(path, hdr, [['8680000000017', '1001', 18.0, 15.0, '']], sheet_name='Fiyat', text_cols={0, 1})
            return path
        sample = [sample[k] for k in PRICE_FIELD_KEYS]
    else:
        hdr = [XL.FIELD_LABEL[k] for k in XL.FIELD_KEYS]
        sample = ['8680000000017', '1001', 'ÖRNEK KALEM 0.7', 'MARKA', 'M1', 'KALEM', 'JEL KALEM', 'ADET', '20', '', 'ANA DEPO', 'A', 'R1', '5', '50', '20', '10,00', '10,00',
                  '18,00', '15,00', '', '', '', '', 'C62']
        if not is_csv:
            XL.export_stock(None, path, template=True)
            return path
    if isinstance(sample, dict):
        sample = list(sample.values())
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(hdr)
        w.writerow(sample)
    return path


# ======================================================================================================================
# Arayüz: tek sihirbaz (Stok / Fiyat / Cari)
# ======================================================================================================================
def parse_date_input(text):
    t = str(text or '').strip()
    if not t:
        return datetime.date.today().strftime('%Y-%m-%d')
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(t, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    raise ValueError('Tarih biçimi geçersiz: %s (örn. 31.12.2025)' % t)


def show_report_window(parent, rep, on_close=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox
    w = tk.Toplevel(parent)
    w.title('Aktarım Kontrol Raporu')
    w.geometry('860x640')
    ok = all(ch['ok'] for ch in rep.get('checks', []))
    tk.Label(w, text=('AKTARIM TAMAMLANDI — KONTROLLER TAMAM' if ok else 'AKTARIM TAMAMLANDI — FARK VAR, RAPORU İNCELEYİN'), bg='#0b376d' if ok else '#b00020',
             fg='white', font=('Segoe UI', 14, 'bold'), padx=14, pady=10).pack(fill='x')
    txt = tk.Text(w, font=('Consolas', 10), wrap='none', padx=10, pady=8)
    txt.insert('1.0', report_text(rep))
    txt.configure(state='disabled')
    txt.pack(fill='both', expand=True)
    bar = tk.Frame(w)
    bar.pack(fill='x', padx=12, pady=10)

    def save():
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.txt', initialfile='aktarim_kontrol_raporu.txt', filetypes=[('Metin', '*.txt')])
        if f:
            Path(f).write_text(report_text(rep), encoding='utf-8-sig')
            messagebox.showinfo('ALFABE', 'Rapor kaydedildi:\n' + f, parent=w)

    def close():
        w.destroy()
        if on_close:
            on_close()
    tk.Button(bar, text='RAPORU KAYDET', command=save, relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left')
    tk.Button(bar, text='KAPAT', command=close, bg='#c8102e', fg='white', relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), padx=18, pady=6).pack(side='right')
    w.protocol('WM_DELETE_WINDOW', close)
    return w


def open_wizard(parent, ctx, kind='stok'):
    """Excel / CSV aktarım sihirbazı. ctx: con (bağlantı üretici), user, default_vat, backup (doğrulanmış tam yedek alan işlev),
    on_done(rapor), toplevel(parent) [isteğe bağlı], log(bağlam, hata) [isteğe bağlı]. Test için API sözlüğü döndürür."""
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    NAVY, RED, MUT = '#0b376d', '#c8102e', '#34516f'
    mk = ctx.get('toplevel') or (lambda p: tk.Toplevel(p))
    w = mk(parent)
    w.title('Veri Aktarım Sihirbazı — Excel / CSV')
    w.geometry('1340x840')
    w.minsize(1080, 660)
    try:
        w.configure(bg='#f3f5f9')
    except Exception:
        pass
    st = {'kind': kind, 'path': '', 'sheet': None, 'sheets': [], 'headers': [], 'rows': [], 'mapping': {}, 'plan': [], 'summary': {}, 'rawrow': {}, 'info': {}, 'saved': False, 'busy': False}
    var_kind = tk.StringVar(value=kind)
    var_mode = tk.StringVar(value='both')
    v_ex_vat, v_cost_inc, v_qty, v_pilot, v_strict, v_bal = tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=1)
    var_role = tk.StringVar(value='Müşteri')
    var_sign = tk.StringVar(value='Pozitif = Borç (cari bize borçlu), Negatif = Alacak')
    var_date = tk.StringVar(value=datetime.date.today().strftime('%d.%m.%Y'))
    var_filter = tk.StringVar(value='Tümü')
    var_search = tk.StringVar(value='')
    var_path = tk.StringVar(value='(dosya seçilmedi)')
    var_sheet = tk.StringVar(value='')

    def log(where, ex):
        try:
            if ctx.get('log'):
                ctx['log'](where, ex)
        except Exception:
            pass

    def con():
        return ctx['con']()

    def btn(p, text, cmd, primary=False):
        return tk.Button(p, text=text, command=cmd, bg=RED if primary else '#ffffff', fg='#ffffff' if primary else '#111111', activebackground='#a90d27' if primary else '#f2f2f2',
                         activeforeground='#ffffff' if primary else '#111111', relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), padx=12, pady=6, cursor='hand2')

    # ---------------- üst alan ----------------
    tk.Label(w, text='EXCEL / CSV İLE VERİ AKTARIMI', font=('Segoe UI', 17, 'bold'), fg=NAVY, bg='#f3f5f9').pack(anchor='w', padx=16, pady=(12, 0))
    tk.Label(w, text='Tür seçin → dosyayı seçin → sütunlar başlıklardan otomatik eşlenir → önizlemeyi inceleyin → İÇE AKTAR. Uygulamadan önce doğrulanmış tam yedek alınır; '
                     'herhangi bir hatada hiçbir kayıt değişmez. Boş hücreler mevcut değeri silmez. İlk kez kullanıyorsanız "İlk 20 satırı dene" ile deneme yapın.',
             fg=MUT, bg='#f3f5f9', wraplength=1290, justify='left').pack(anchor='w', padx=16, pady=(0, 6))
    r1 = tk.Frame(w, bg='#f3f5f9')
    r1.pack(fill='x', padx=16, pady=(0, 4))
    tk.Label(r1, text='Aktarım türü:', bg='#f3f5f9', font=('Segoe UI', 10, 'bold')).pack(side='left')
    for text, val in (('Stok kartları', 'stok'), ('Fiyat güncelleme', 'fiyat'), ('Cari kartları', 'cari')):
        tk.Radiobutton(r1, text=text, value=val, variable=var_kind, bg='#f3f5f9', font=('Segoe UI', 10), command=lambda: on_kind()).pack(side='left', padx=(10, 0))
    r2 = tk.Frame(w, bg='#f3f5f9')
    r2.pack(fill='x', padx=16, pady=4)
    btn(r2, 'DOSYA SEÇ', lambda: choose()).pack(side='left')
    btn(r2, 'SÜTUN EŞLEME', lambda: map_dialog()).pack(side='left', padx=6)
    btn(r2, 'ŞABLON İNDİR', lambda: template()).pack(side='left')
    sheet_lbl = tk.Label(r2, text='  Sayfa:', bg='#f3f5f9')
    sheet_cb = ttk.Combobox(r2, textvariable=var_sheet, values=[], state='readonly', width=22)
    sheet_cb.bind('<<ComboboxSelected>>', lambda e=None: reload_sheet())
    tk.Label(r2, textvariable=var_path, fg=NAVY, bg='#f3f5f9', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=12)
    opt = tk.Frame(w, bg='white', highlightthickness=1, highlightbackground='#d5dbe5')
    opt.pack(fill='x', padx=16, pady=6)

    def rebuild_options():
        for ch in opt.winfo_children():
            ch.destroy()
        k = var_kind.get()
        row1 = tk.Frame(opt, bg='white')
        row1.pack(fill='x', padx=10, pady=(6, 2))
        row2 = tk.Frame(opt, bg='white')
        row2.pack(fill='x', padx=10, pady=(0, 6))
        if k in ('stok', 'cari'):
            tk.Label(row1, text='Kayıt işlemi:', bg='white', font=('Segoe UI', 9, 'bold')).pack(side='left')
            for text, val in (('Yeni ekle + mevcutları güncelle', 'both'), ('Yalnız yeni ekle', 'new'), ('Yalnız mevcutları güncelle', 'update')):
                tk.Radiobutton(row1, text=text, variable=var_mode, value=val, bg='white', command=preview).pack(side='left', padx=(8, 0))
        else:
            tk.Label(row1, text='Yalnız mevcut stokların fiyatları güncellenir (Barkod / Stok Kodu ile eşleşir); yeni stok kartı açılmaz, miktara dokunulmaz. Fiyat değişimleri fiyat geçmişine yazılır.',
                     bg='white', fg=MUT).pack(side='left')
        if k in ('stok', 'fiyat'):
            tk.Checkbutton(row2, text='Satış fiyatları KDV HARİÇ girildi', variable=v_ex_vat, bg='white', command=preview).pack(side='left')
        if k == 'stok':
            tk.Checkbutton(row2, text='Maliyetler KDV DAHİL girildi', variable=v_cost_inc, bg='white', command=preview).pack(side='left', padx=(10, 0))
            tk.Checkbutton(row2, text='Mevcut kartların stok miktarını da güncelle (sayım farkı hareketi)', variable=v_qty, bg='white', command=preview).pack(side='left', padx=(10, 0))
        if k == 'cari':
            tk.Label(row1, text='   Varsayılan cari türü:', bg='white').pack(side='left', padx=(14, 0))
            cb = ttk.Combobox(row1, textvariable=var_role, values=list(ROLE_BASE), state='readonly', width=12)
            cb.pack(side='left', padx=4)
            cb.bind('<<ComboboxSelected>>', lambda e=None: preview())
            tk.Checkbutton(row2, text='Açılış bakiyelerini aktar', variable=v_bal, bg='white', command=preview).pack(side='left')
            tk.Label(row2, text='  Bakiye işareti:', bg='white').pack(side='left')
            sc = ttk.Combobox(row2, textvariable=var_sign, values=['Pozitif = Borç (cari bize borçlu), Negatif = Alacak', 'Pozitif = Alacak (biz cariye borçluyuz), Negatif = Borç'], state='readonly', width=52)
            sc.pack(side='left', padx=4)
            sc.bind('<<ComboboxSelected>>', lambda e=None: preview())
            tk.Label(row2, text='  Açılış tarihi:', bg='white').pack(side='left')
            tk.Entry(row2, textvariable=var_date, width=11).pack(side='left', padx=4)
            tk.Checkbutton(row2, text='Geçersiz VKN/TCKN satırlarını hata say', variable=v_strict, bg='white', command=preview).pack(side='left', padx=(10, 0))
        tk.Checkbutton(row2, text='İlk %d satırı dene (pilot)' % PILOT_ROWS, variable=v_pilot, bg='white', fg=RED, font=('Segoe UI', 9, 'bold'), command=preview).pack(side='right')

    summ = tk.Label(w, text='', fg=NAVY, bg='#f3f5f9', font=('Segoe UI', 10, 'bold'), anchor='w')
    summ.pack(fill='x', padx=16, pady=(4, 0))
    note = tk.Label(w, text='', fg='#8a5a00', bg='#f3f5f9', anchor='w', justify='left', wraplength=1290)
    note.pack(fill='x', padx=16)
    fr = tk.Frame(w, bg='#f3f5f9')
    fr.pack(fill='x', padx=16, pady=(4, 0))
    tk.Label(fr, text='Göster:', bg='#f3f5f9').pack(side='left')
    cbf = ttk.Combobox(fr, textvariable=var_filter, values=['Tümü', 'YENİ', 'GÜNCELLE', 'ATLA', 'HATA', 'UYARILI'], state='readonly', width=12)
    cbf.pack(side='left', padx=6)
    cbf.bind('<<ComboboxSelected>>', lambda e=None: fill())
    tk.Label(fr, text='Ara:', bg='#f3f5f9').pack(side='left', padx=(12, 0))
    ent = tk.Entry(fr, textvariable=var_search, width=30)
    ent.pack(side='left', padx=6)
    ent.bind('<KeyRelease>', lambda e=None: fill())
    bar = tk.Frame(w, bg='#f3f5f9')
    bar.pack(side='bottom', fill='x', padx=16, pady=(4, 12))
    tfr = tk.Frame(w, bg='#f3f5f9')
    tfr.pack(fill='both', expand=True, padx=16, pady=6)
    cols = ('row', 'action', 'key', 'name', 'detail')
    tree = ttk.Treeview(tfr, columns=cols, show='headings', selectmode='browse')
    for k_, h_, wd in (('row', 'Satır', 55), ('action', 'Durum', 95), ('key', 'Barkod / Kod / VKN', 150), ('name', 'Stok Adı / Ünvan', 300), ('detail', 'Ayrıntı / Değişiklik / Uyarı', 700)):
        tree.heading(k_, text=h_)
        tree.column(k_, width=wd, anchor='w', stretch=(k_ == 'detail'))
    for tag, col in (('YENİ', '#0f7a3d'), ('GÜNCELLE', '#0b5cad'), ('HATA', '#b00020'), ('ATLA', '#8a94a3')):
        tree.tag_configure(tag, foreground=col)
    vs = ttk.Scrollbar(tfr, orient='vertical', command=tree.yview)
    hs = ttk.Scrollbar(tfr, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    vs.pack(side='right', fill='y')
    hs.pack(side='bottom', fill='x')
    tree.pack(fill='both', expand=True)

    # ---------------- iş mantığı ----------------
    def fields():
        k = var_kind.get()
        return CARI_FIELDS if k == 'cari' else (PRICE_FIELDS if k == 'fiyat' else XL.FIELDS)

    def auto_mapping():
        k = var_kind.get()
        if k == 'cari':
            m = cari_auto_map(st['headers'])
        elif k == 'fiyat':
            m = auto_map_fields(st['headers'], PRICE_FIELDS)
        else:
            m = XL.auto_map(st['headers'])
        c = con()
        try:
            sv = load_saved_mapping(c, k, st['headers'])
        finally:
            c.close()
        st['saved'] = bool(sv)
        return sv if sv else m

    def busy(on, msg=''):
        st['busy'] = on
        try:
            w.configure(cursor='watch' if on else '')
            if msg:
                summ.config(text=msg)
            w.update_idletasks()
        except Exception:
            pass

    def raw(row_no, key):
        cells = st['rawrow'].get(row_no)
        i = st['mapping'].get(key)
        if cells is None or i is None or i >= len(cells):
            return ''
        v = cells[i]
        return XL.id_text(v) if key in ('barcode', 'code', 'cari_code', 'tax_no') else XL.txt(v)

    def row_info(p):
        """(anahtar, ad) görünür sütunlar için."""
        k = var_kind.get()
        sid = p.get('sid')
        d = p.get('data') or {}
        if k == 'cari':
            inf = st['info'].get(sid) if sid else None
            key = d.get('cari_code') or (inf[0] if inf else '') or d.get('tax_no') or raw(p['row_no'], 'tax_no') or raw(p['row_no'], 'cari_code')
            name = d.get('title') or (inf[1] if inf else '') or raw(p['row_no'], 'title')
            return key, name
        inf = st['info'].get(sid) if sid else None
        key = d.get('barcode') or (inf[0] if inf else '') or d.get('code') or (inf[1] if inf else '') or raw(p['row_no'], 'barcode') or raw(p['row_no'], 'code')
        name = d.get('name') or (inf[2] if inf else '') or raw(p['row_no'], 'name')
        return key, name

    def preview():
        if not st['headers'] or st['busy']:
            return
        k = var_kind.get()
        rows = st['rows'][:PILOT_ROWS] if v_pilot.get() else st['rows']
        st['rawrow'] = {rn: cells for rn, cells in rows}
        m = st['mapping']
        problem = ''
        if k == 'cari' and 'title' not in m and not any(x in m for x in ('cari_code', 'tax_no')):
            problem = 'Ünvan (veya Cari Kodu / Vergi No) sütunu eşlenmemiş. "SÜTUN EŞLEME" ile sütunları seçin.'
        if k == 'fiyat' and not any(x in m for x in ('barcode', 'code')):
            problem = 'Barkod veya Stok Kodu sütunu eşlenmemiş. "SÜTUN EŞLEME" ile sütunları seçin.'
        if k == 'fiyat' and not any(x in m for x in ('retail', 'wholesale', 'special_price')):
            problem = problem or 'Fiyat sütunu (Perakende / Toptan / Özel Fiyat) eşlenmemiş.'
        if k == 'stok' and not any(x in m for x in ('barcode', 'code', 'name')):
            problem = 'Barkod, Stok Kodu veya Stok Adı sütunu eşlenmemiş. "SÜTUN EŞLEME" ile sütunları seçin.'
        if problem:
            st['plan'], st['summary'] = [], {}
            tree.delete(*tree.get_children())
            summ.config(text=problem, fg='#b00020')
            note.config(text='')
            return
        busy(True, 'Önizleme hazırlanıyor...')
        c = con()
        try:
            if k == 'cari':
                role = var_role.get()
                sign = 'borc' if var_sign.get().startswith('Pozitif = Borç') else 'alacak'
                plan, s = build_cari_plan(c, st['headers'], rows, m, var_mode.get(), role, sign, bool(v_strict.get()), bool(v_bal.get()))
                st['info'] = {int(r[0]): (str(r[1] or ''), str(r[2] or '')) for r in c.execute('SELECT id,cari_code,title FROM caris').fetchall()}
            else:
                plan, s = build_stock_plan(c, st['headers'], rows, m, k, var_mode.get(), float(ctx.get('default_vat', 20.0)), bool(v_ex_vat.get()), bool(v_cost_inc.get()), bool(v_qty.get()))
                for p in plan:
                    p['warn'] = _nw(p.get('msg') or []) if p['action'] in ('YENİ', 'GÜNCELLE') else 0
                s['UYARI'] = sum(1 for p in plan if p['warn'])
                st['info'] = {int(r[0]): (str(r[1] or ''), str(r[2] or ''), str(r[3] or '')) for r in c.execute('SELECT id,barcode,code,name FROM stock').fetchall()}
        except Exception as ex:
            log('Aktarım önizleme', ex)
            busy(False)
            summ.config(text='Önizleme hazırlanamadı: %s' % ex, fg='#b00020')
            return
        finally:
            c.close()
        busy(False)
        st['plan'], st['summary'] = plan, s
        used = set(i for i in m.values() if i is not None)
        unused = [h for i, h in enumerate(st['headers']) if i not in used]
        pil = '  [PİLOT: yalnız ilk %d satır]' % PILOT_ROWS if v_pilot.get() else ''
        summ.config(text='Satır: %d   •   YENİ: %d   •   GÜNCELLE: %d   •   ATLA: %d   •   HATA: %d   •   UYARILI: %d   (eşlenen sütun: %d / %d)%s' %
                    (len(rows), s['YENİ'], s['GÜNCELLE'], s['ATLA'], s['HATA'], s.get('UYARI', 0), len(used), len(st['headers']), pil), fg=NAVY)
        txt = ('Kayıtlı sütun eşlemesi uygulandı. ' if st['saved'] else '') + ('Aktarılmayan dosya sütunları: ' + ', '.join(unused[:12]) + (' …' if len(unused) > 12 else '') if unused else '')
        note.config(text=txt)
        fill()

    def fill():
        tree.delete(*tree.get_children())
        f = var_filter.get()
        q = tr_key(var_search.get())
        desc = describe_cari if var_kind.get() == 'cari' else XL.describe
        shown = 0
        for i, p in enumerate(st['plan']):
            if f == 'UYARILI':
                if not p.get('warn'):
                    continue
            elif f != 'Tümü' and p['action'] != f:
                continue
            key, name = row_info(p)
            det = desc(p)
            if q and q not in tr_key('%s %s %s' % (key, name, det)):
                continue
            label = p['action'] + (' ⚠' if p.get('warn') and p['action'] in ('YENİ', 'GÜNCELLE') else '')
            tree.insert('', 'end', iid=str(i), tags=(p['action'],), values=(p['row_no'], label, key, name, det))
            shown += 1
            if shown >= 3000:
                tree.insert('', 'end', values=('', '', '', '… ilk 3000 satır gösteriliyor; filtre / arama kullanın', ''))
                break

    def show_row(event=None):
        sel = tree.selection()
        if not sel:
            return
        try:
            p = st['plan'][int(sel[0])]
        except (ValueError, IndexError):
            return
        d = tk.Toplevel(w)
        d.title('Satır %s' % p['row_no'])
        d.geometry('780x420')
        t = tk.Text(d, wrap='word', font=('Segoe UI', 10), padx=10, pady=8)
        cells = st['rawrow'].get(p['row_no'], [])
        lines = ['Durum: %s' % p['action'], '', 'DOSYADAKİ DEĞERLER']
        for i, h in enumerate(st['headers']):
            v = cells[i] if i < len(cells) else None
            if v not in (None, ''):
                lines.append('  %s: %s' % (h, v))
        lines += ['', 'AÇIKLAMA', '  ' + (describe_cari(p) if var_kind.get() == 'cari' else XL.describe(p))]
        if p.get('msg'):
            lines += ['', 'UYARI / HATA'] + ['  • ' + x for x in p['msg']]
        t.insert('1.0', '\n'.join(lines))
        t.configure(state='disabled')
        t.pack(fill='both', expand=True)
    tree.bind('<Double-1>', show_row)

    def set_file(path, sheet=None):
        busy(True, 'Dosya okunuyor...')
        try:
            if sheet is None:
                st['sheets'] = list_sheets(path)
                sheet = st['sheets'][0]
            headers, rows = read_file(path, sheet)
        except Exception as ex:
            log('Aktarım dosya okuma', ex)
            busy(False)
            summ.config(text='')
            messagebox.showerror('ALFABE', 'Dosya okunamadı:\n' + str(ex), parent=w)
            return False
        busy(False)
        st.update(path=str(path), sheet=sheet, headers=headers, rows=rows)
        st['mapping'] = auto_mapping()
        var_path.set('%s  (%d satır)' % (Path(path).name, len(rows)))
        if len(st['sheets']) > 1:
            sheet_cb.configure(values=st['sheets'])
            var_sheet.set(sheet)
            sheet_lbl.pack(side='left')
            sheet_cb.pack(side='left')
        else:
            sheet_lbl.pack_forget()
            sheet_cb.pack_forget()
        if not rows:
            messagebox.showwarning('ALFABE', 'Seçilen sayfada veri satırı bulunamadı. Başka bir sayfa seçin.', parent=w)
        preview()
        return True

    def choose():
        f = filedialog.askopenfilename(parent=w, title='Aktarılacak dosyayı seçin',
                                       filetypes=[('Excel / CSV', '*.xlsx *.xlsm *.xls *.csv *.tsv *.txt'), ('Tüm dosyalar', '*.*')])
        if f:
            set_file(f)

    def reload_sheet():
        if st['path']:
            set_file(st['path'], var_sheet.get())

    def on_kind():
        if st['headers']:
            st['mapping'] = auto_mapping()
        rebuild_options()
        preview()

    def template():
        k = var_kind.get()
        base = {'stok': 'Stok_Aktarim_Sablonu', 'fiyat': 'Fiyat_Guncelleme_Sablonu', 'cari': 'Cari_Aktarim_Sablonu'}[k]
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.xlsx', initialfile=base + '.xlsx', filetypes=[('Excel', '*.xlsx'), ('CSV (noktalı virgül)', '*.csv')])
        if not f:
            return
        try:
            template_file(k, f)
        except Exception as ex:
            log('Aktarım şablon', ex)
            return messagebox.showerror('ALFABE', 'Şablon oluşturulamadı:\n' + str(ex), parent=w)
        messagebox.showinfo('ALFABE', 'Şablon kaydedildi:\n' + f + '\n\nÖrnek satırı silip kendi kayıtlarınızı yazın. Barkod, kod, vergi no ve telefon sütunlarını METİN biçiminde bırakın.'
                            + ('\nCari bakiyesi: tutarı "Bakiye", yönünü "Bakiye Yönü" sütununa (Borç / Alacak) yazın.' if k == 'cari' else
                               '\nMaliyetler KDV hariç, satış fiyatları KDV dahil kabul edilir (seçeneklerden değiştirilebilir).'), parent=w)

    def map_dialog():
        if not st['headers']:
            return messagebox.showinfo('ALFABE', 'Önce dosya seçin.', parent=w)
        k = var_kind.get()
        d = tk.Toplevel(w)
        d.title('Sütun Eşleme')
        d.geometry('820x720')
        d.transient(w)
        none = '(kullanma)'
        opts = [none] + ['%d - %s' % (i + 1, h) for i, h in enumerate(st['headers'])]
        samples = [[] for _ in st['headers']]
        for _rn, cells in st['rows'][:30]:
            for i in range(len(st['headers'])):
                if i < len(cells) and cells[i] not in (None, '') and len(samples[i]) < 3:
                    samples[i].append(XL.txt(cells[i]) if not isinstance(cells[i], float) else ('%g' % cells[i]))
        tk.Label(d, text='Her alan için dosyadaki sütunu seçin. Sağda seçilen sütunun örnek değerleri görünür.', fg=MUT, wraplength=780, justify='left').pack(anchor='w', padx=12, pady=(10, 4))
        body = tk.Frame(d)
        body.pack(fill='both', expand=True, padx=8)
        canvas = tk.Canvas(body, highlightthickness=0)
        inner = tk.Frame(canvas)
        sb = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e=None: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind_all('<MouseWheel>', lambda e=None: canvas.yview_scroll(int(-e.delta / 120), 'units') if e is not None else None)
        d.bind('<Destroy>', lambda e=None: canvas.unbind_all('<MouseWheel>') if (e is None or e.widget is d) else None)
        vars_, sample_lbls = {}, {}

        def upd(key):
            sel = vars_[key].get()
            if sel == none:
                sample_lbls[key].config(text='')
            else:
                i = int(sel.split(' - ')[0]) - 1
                sample_lbls[key].config(text=' | '.join(samples[i]) if samples[i] else '(boş)')
        for r_, (key, lab, _s, _t) in enumerate(fields()):
            tk.Label(inner, text=lab, width=28, anchor='w').grid(row=r_, column=0, padx=8, pady=3)
            cur = st['mapping'].get(key)
            v = tk.StringVar(value=opts[cur + 1] if cur is not None and cur + 1 < len(opts) else none)
            cb = ttk.Combobox(inner, textvariable=v, values=opts, state='readonly', width=32)
            cb.grid(row=r_, column=1, padx=4)
            sl = tk.Label(inner, text='', fg='#555', anchor='w', width=36)
            sl.grid(row=r_, column=2, padx=6, sticky='w')
            vars_[key] = v
            sample_lbls[key] = sl
            cb.bind('<<ComboboxSelected>>', lambda e, key=key: upd(key))
            upd(key)

        def ok():
            m = {}
            for key, v in vars_.items():
                if v.get() != none:
                    m[key] = int(v.get().split(' - ')[0]) - 1
            used = {}
            for key, i in m.items():
                used.setdefault(i, []).append(key)
            dup = [(st['headers'][i], ks) for i, ks in used.items() if len(ks) > 1]
            if dup and not messagebox.askyesno('ALFABE', 'Bir sütun birden fazla alana eşlenmiş:\n' + '\n'.join('%s → %s' % (h, ', '.join(CARI_LABEL.get(x) or XL.FIELD_LABEL.get(x, x) for x in ks)) for h, ks in dup)
                                               + '\n\nYine de devam edilsin mi?', parent=d):
                return
            st['mapping'] = m
            c = con()
            try:
                save_mapping(c, k, st['headers'], m)
            finally:
                c.close()
            st['saved'] = True
            d.destroy()
            preview()

        def reset():
            c = con()
            try:
                forget_mapping(c, k, st['headers'])
            finally:
                c.close()
            st['mapping'] = auto_mapping()
            d.destroy()
            preview()
        fb = tk.Frame(d)
        fb.pack(fill='x', padx=12, pady=10)
        tk.Button(fb, text='TAMAM (eşlemeyi hatırla)', command=ok, bg=RED, fg='white', font=('Segoe UI', 10, 'bold'), padx=14, pady=5).pack(side='left')
        tk.Button(fb, text='OTOMATİĞE DÖN', command=reset, font=('Segoe UI', 10, 'bold'), padx=10, pady=5).pack(side='left', padx=8)
        tk.Button(fb, text='İPTAL', command=d.destroy, padx=10, pady=5).pack(side='right')

    def save_errors():
        if not st['plan']:
            return messagebox.showinfo('ALFABE', 'Önce dosya seçin.', parent=w)
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.csv', initialfile='aktarim_hata_raporu.csv', filetypes=[('CSV', '*.csv')])
        if f:
            error_report(st['plan'], f, var_kind.get())
            messagebox.showinfo('ALFABE', 'Hata / atlanan / uyarılı satır raporu kaydedildi:\n' + f, parent=w)

    def do_import():
        k = var_kind.get()
        if not st['plan']:
            return messagebox.showinfo('ALFABE', 'Önce geçerli bir dosya seçip önizlemeyi görün.', parent=w)
        s = st['summary']
        if s.get('YENİ', 0) + s.get('GÜNCELLE', 0) == 0:
            return messagebox.showinfo('ALFABE', 'Aktarılacak (yeni / güncellenecek) satır yok.', parent=w)
        open_date = None
        if k == 'cari':
            try:
                open_date = parse_date_input(var_date.get())
            except ValueError as ex:
                return messagebox.showerror('ALFABE', str(ex), parent=w)
        what = {'stok': 'stok kartı', 'fiyat': 'fiyat', 'cari': 'cari kartı'}[k]
        msg = '%d yeni %s eklenecek, %d %s güncellenecek.' % (s['YENİ'], what, s['GÜNCELLE'], what)
        if s['HATA']:
            msg += '\n%d HATALI satır aktarılmayacak (hata raporunu kaydedebilirsiniz).' % s['HATA']
        if v_pilot.get():
            msg += '\n\nPİLOT: yalnız dosyanın ilk %d satırı işlenir.' % PILOT_ROWS
        if k == 'cari' and v_bal.get():
            nb = sum(1 for p in st['plan'] if p['action'] == 'YENİ' and p.get('balance') is not None)
            tot = sum((p.get('balance') or 0.0) for p in st['plan'] if p['action'] == 'YENİ')
            msg += '\n\n%d yeni cariye açılış bakiyesi yazılacak. Net bakiye: %s %s' % (nb, money_txt(abs(tot)), 'BORÇ' if tot > 0 else ('ALACAK' if tot < 0 else ''))
        msg += '\n\nÖnce doğrulanmış TAM YEDEK alınacak, sonra aktarım tek işlemde yapılacaktır. Devam edilsin mi?'
        if not messagebox.askyesno('ALFABE', msg, parent=w):
            return
        backup = ''
        busy(True, '1/3 Güvenlik yedeği alınıyor...')
        try:
            backup = ctx['backup']()
        except Exception as ex:
            log('Aktarım yedeği', ex)
            busy(False)
            return messagebox.showerror('ALFABE', 'Aktarım BAŞLATILMADI. Güvenlik yedeği alınamadı, hiçbir veri değişmedi:\n' + str(ex), parent=w)
        c = con()
        try:
            before = snapshot(c)
            busy(True, '2/3 Kayıtlar aktarılıyor...')
            if k == 'cari':
                res = apply_cari_plan(c, st['plan'], ctx.get('user', ''), Path(st['path']).name, open_date, backup)
            else:
                res = XL.apply_plan(c, st['plan'], ctx.get('user', ''), bool(v_qty.get()) and k == 'stok', None, Path(st['path']).name)
                res['yedek'] = str(backup)
            busy(True, '3/3 Kontrol raporu hazırlanıyor...')
            after = snapshot(c)
            rep = build_report(k, before, after, st['plan'], res, Path(st['path']).name, backup, bool(v_qty.get()) and k == 'stok')
            record_history(c, k, st['path'], st['plan'], backup, 'Tamamlandı', rep)
        except Exception as ex:
            log('Aktarım uygulama', ex)
            busy(False)
            try:
                record_history(c, k, st['path'], st['plan'], backup, 'Hata', None, str(ex))
            except Exception:
                pass
            return messagebox.showerror('ALFABE', str(ex) + '\n\nGüvenlik yedeği korunuyor:\n' + str(backup), parent=w)
        finally:
            c.close()
        busy(False)
        api['report'] = rep
        if ctx.get('on_done'):
            try:
                ctx['on_done'](rep)
            except Exception as ex:
                log('Aktarım on_done', ex)
        try:
            w.destroy()
        except Exception:
            pass
        if not ctx.get('silent'):
            show_report_window(parent, rep)

    btn(bar, 'İÇE AKTAR', do_import, True).pack(side='left')
    btn(bar, 'HATA / ATLANAN RAPORU', save_errors).pack(side='left', padx=6)
    tk.Label(bar, text='Satıra çift tıklayın: dosyadaki değerler ve ayrıntı', fg=MUT, bg='#f3f5f9').pack(side='left', padx=10)
    btn(bar, 'KAPAT', w.destroy).pack(side='right')
    w.bind('<Escape>', lambda e=None: w.destroy())
    rebuild_options()
    api = {'window': w, 'state': st, 'set_file': set_file, 'preview': preview, 'import': do_import, 'set_kind': lambda k: (var_kind.set(k), on_kind()),
           'vars': {'mode': var_mode, 'pilot': v_pilot, 'role': var_role, 'sign': var_sign, 'date': var_date, 'bal': v_bal, 'strict': v_strict, 'qty': v_qty, 'ex_vat': v_ex_vat,
                    'filter': var_filter, 'search': var_search},
           'map_dialog': map_dialog, 'show_row': show_row, 'template': template, 'save_errors': save_errors,
           'tree': tree, 'summary_label': summ, 'note_label': note, 'fill': fill, 'report': None}
    return api
