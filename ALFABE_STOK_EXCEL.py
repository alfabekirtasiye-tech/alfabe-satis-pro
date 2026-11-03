# -*- coding: utf-8 -*-
"""ALFABE stok Excel / CSV içe ve dışa aktarma.

* Dışa aktarma: tüm stok kartları (ve vergi / e-fatura alanları) .xlsx olarak; aynı dosya düzenlenip geri yüklenebilir.
* İçe aktarma: .xlsx / .csv okur (Excel kurulu olması gerekmez), sütunları başlıklara göre otomatik eşler, her satır için
  YENİ / GÜNCELLE / ATLA / HATA önizlemesi üretir, kullanıcı onayıyla uygular.

Güvence:
  - Uygulamadan önce veritabanının yedeği alınır (excel_aktarim_oncesi klasörü).
  - Tüm yazma tek işlemdedir; herhangi bir veritabanı hatasında hiçbir kayıt değişmez.
  - Doğrulama hatası olan satırlar atlanır ve raporlanır; diğerleri aktarılır.
  - Mevcut stokta miktar varsayılan olarak DEĞİŞMEZ (isteğe bağlı: sayım farkı hareketi olarak işlenir).
  - Yeni stoğun açılış miktarı stok kartıyla aynı şekilde 'Açılış' hareketi oluşturur.
  - Mevcut ürünlerde fiyat değişiklikleri fiyat geçmişine yazılır; işlem denetim (audit) kaydına geçer.
  - Excel'de boş bırakılan hücreler mevcut değeri silmez.
"""
import csv
import datetime
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _esc

# anahtar, başlık, eşanlamlılar (normalize), tür
FIELDS = [
    ('barcode', 'Barkod', ['barkod', 'barcode', 'ean', 'barkodno', 'barkodu', 'barkodnumarasi', 'barkodkodu', 'barkodlar', 'gtin', 'upc', 'barkodnu', 'barkod1', 'barkodu1', 'barkodkod'], 'id'),
    ('code', 'Stok Kodu', ['stokkodu', 'urunkodu', 'kod', 'stokno', 'kodu', 'stokod', 'stokkod', 'malkodu', 'malzemekodu', 'urunno', 'sku', 'itemcode', 'productcode', 'stokkartkodu', 'kartkodu', 'hizmetkodu', 'urunid'], 'id'),
    ('name', 'Stok Adı', ['stokadi', 'urunadi', 'malhizmetadi', 'adi', 'ad', 'urun', 'isim', 'aciklama', 'stoisim', 'stokismi', 'urunismi', 'malinadi', 'malzemeadi', 'stokaciklama', 'urunaciklamasi', 'itemname', 'productname', 'name', 'stokunvani', 'tanim', 'malinismi'], 'text'),
    ('brand', 'Marka', ['marka', 'markaadi', 'brand', 'uretici'], 'text'),
    ('model', 'Model', ['model'], 'text'),
    ('grp', 'Grup', ['grup', 'grubu', 'stokgrubu', 'grupkodu', 'urungrubu', 'kategori', 'category', 'anagrup', 'stokgrup', 'stogrupkod', 'grupadi'], 'text'),
    ('subgrp', 'Alt Grup', ['altgrup', 'altgrubu', 'altkategori', 'subcategory', 'altgrupkodu', 'stokaltgrubu'], 'text'),
    ('unit', 'Birim', ['birim', 'anabirim', 'birimi', 'olcubirimi', 'olcubr1', 'olcubr', 'unit', 'stobirim1ad', 'birimadi', 'stokbirimi'], 'unit'),
    ('vat', 'KDV %', ['kdvorani', 'kdv', 'vat', 'kdvyuzde', 'kdvoran', 'kdvyuzdesi', 'satiskdv', 'kdvsatis', 'kdvorani1', 'vatrate', 'kdv1', 'kdvdilimi'], 'vat'),
    ('special', 'Özel Kod', ['ozelkod'], 'text'),
    ('warehouse', 'Depo', ['depo'], 'text'),
    ('section', 'Bölüm', ['bolum'], 'text'),
    ('shelf', 'Raf', ['raf'], 'text'),
    ('min_qty', 'Min Stok', ['minstok', 'minimumstok', 'minimum', 'min', 'kritikstok', 'kritikmiktar', 'asgaristok', 'emniyetstoku', 'minmiktar'], 'qty'),
    ('max_qty', 'Max Stok', ['maxstok', 'maksimumstok', 'maksimum', 'max', 'azamistok', 'maxmiktar'], 'qty'),
    ('qty', 'Mevcut / Açılış Stok', ['mevcut', 'mevcutstok', 'acilisstok', 'acilis', 'miktar', 'bakiye', 'stok', 'stokadedi', 'stokmiktari', 'kalanmiktar', 'eldekimiktar', 'eldekistok', 'quantity', 'qty', 'adet', 'sayim', 'sayimmiktari', 'depobakiye'], 'qty'),
    ('last_cost', 'Son Alış (KDV Hariç)', ['sonalis', 'sonalisfiyati', 'sonalismaliyeti', 'alisfiyati', 'alis', 'alisfiyat', 'alisfiat', 'alisfiyati1', 'sonalisfiyat', 'alismaliyeti', 'listealisfiyati', 'purchaseprice', 'buyprice', 'alisbirimfiyati'], 'price'),
    ('avg_cost', 'Ort. Maliyet (KDV Hariç)', ['ortmaliyet', 'ortalamamaliyet', 'maliyet', 'ortalamamaliyeti', 'birimmaliyet', 'maliyetfiyati', 'cost', 'ortalamaalis'], 'price'),
    ('retail', 'Perakende Fiyatı (KDV Dahil)', ['perakendefiyati', 'perakende', 'satisfiyati', 'satis', 'fiyat', 'perakendesatis', 'perakendesatisfiyati', 'satisfiyat', 'satisfiat', 'satisfiati', 'satisfiyati1', 'listefiyati', 'birimfiyat', 'birimfiyati', 'price', 'saleprice', 'sellprice', 'psf', 'tavsiyeedilensatisfiyati', 'etiketfiyati', 'fiyat1', 'fiyati'], 'price'),
    ('wholesale', 'Toptan Fiyatı (KDV Dahil)', ['toptanfiyati', 'toptan', 'toptansatis', 'toptansatisfiyati', 'toptanfiyat', 'toptanfiat', 'satisfiyati2', 'fiyat2', 'wholesaleprice', 'bayifiyati', 'bayi'], 'price'),
    ('special_price', 'Özel Fiyat (KDV Dahil)', ['ozelfiyat', 'ozelfiyati', 'ozelsatisfiyati', 'satisfiyati3', 'fiyat3', 'kampanyafiyati', 'indirimlifiyat', 'ozelfiat'], 'price'),
    ('gtip', 'GTİP', ['gtip'], 'gtip'),
    ('otv_rate', 'ÖTV %', ['otvorani', 'otv'], 'otv'),
    ('vat_exempt', 'KDV İstisna Kodu', ['kdvistisnakodu', 'istisnakodu', 'istisna', 'kdvistisna'], 'text'),
    ('einv_unit', 'E-Fatura Birim Kodu', ['efaturabirimkodu', 'efaturabirim', 'birimkodu'], 'unitcode'),
    # v461.57: kutu / koli gibi ek birimler (her biri kendi barkodu ve perakende fiyatıyla); stok adet cinsinden tutulur.
    ('u2_name', 'Birim 2 Adı', ['birim2', 'ekbirim1', 'birim2adi'], 'text'),
    ('u2_barcode', 'Birim 2 Barkodu', ['birim2barkodu', 'birim2barkod'], 'unitbc'),
    ('u2_qty', 'Birim 2 İçerdiği Adet', ['birim2adet', 'birim2icerdigiadet', 'birim2carpan'], 'qty'),
    ('u2_price', 'Birim 2 Perakende (KDV Dahil)', ['birim2fiyati', 'birim2perakende', 'birim2fiyat'], 'price'),
    ('u3_name', 'Birim 3 Adı', ['birim3', 'ekbirim2', 'birim3adi'], 'text'),
    ('u3_barcode', 'Birim 3 Barkodu', ['birim3barkodu', 'birim3barkod'], 'unitbc'),
    ('u3_qty', 'Birim 3 İçerdiği Adet', ['birim3adet', 'birim3icerdigiadet', 'birim3carpan'], 'qty'),
    ('u3_price', 'Birim 3 Perakende (KDV Dahil)', ['birim3fiyati', 'birim3perakende', 'birim3fiyat'], 'price'),
]
UNIT_SLOTS = (2, 3)
UNIT_KEYS = [k for n in UNIT_SLOTS for k in ('u%d_name' % n, 'u%d_barcode' % n, 'u%d_qty' % n, 'u%d_price' % n)]
FIELD_KEYS = [f[0] for f in FIELDS]
FIELD_LABEL = {f[0]: f[1] for f in FIELDS}
FIELD_TYPE = {f[0]: f[3] for f in FIELDS}
STOCK_UPDATE_KEYS = ['name', 'brand', 'model', 'grp', 'subgrp', 'unit', 'vat', 'special', 'warehouse', 'section', 'shelf', 'min_qty', 'max_qty',
                     'last_cost', 'avg_cost', 'retail', 'wholesale', 'special_price']
TAX_KEYS = ['gtip', 'otv_rate', 'vat_exempt', 'einv_unit']
PRICE_LABEL = {'retail': 'Perakende', 'wholesale': 'Toptan', 'special_price': 'Özel Fiyat'}
MAX_ROWS = 50000


# ----------------------------------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------------------------------
def norm(s):
    t = str(s or '').replace('İ', 'i').replace('I', 'ı').lower()
    t = t.replace('ı', 'i').replace('ş', 's').replace('ğ', 'g').replace('ü', 'u').replace('ö', 'o').replace('ç', 'c')
    t = re.sub(r'\(.*?\)', '', t)                        # (KDV Dahil) gibi açıklamalar
    return re.sub(r'[^a-z0-9%]', '', t).replace('%', '')


def auto_map(headers):
    """{alan: sütun indeksi}. Önce birebir, sonra önek eşleşmesi."""
    hn = [norm(h) for h in headers]
    mapping = {}
    used = set()
    for exact in (True, False):
        for key, _lab, syns, _t in FIELDS:
            if key in mapping:
                continue
            syns = list(syns) + [norm(_lab)]                 # şablondaki etiketin kendisi her zaman tanınır
            for i, h in enumerate(hn):
                if i in used or not h:
                    continue
                if (h in syns) if exact else any(len(sy) >= 4 and h.startswith(sy) for sy in syns):
                    mapping[key] = i
                    used.add(i)
                    break
    return mapping


def parse_num(v):
    """'1.234,56' / '12,5' / '12.5' / '₺45' / 12.0 -> float; boş -> None; geçersiz -> ValueError."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError('sayı değil')
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace('₺', '').replace('TL', '').replace('tl', '').replace('%', '').replace(' ', '').replace(' ', '')
    if t == '':
        return None
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
    elif ',' in t:
        t = t.replace(',', '.')
    elif t.count('.') > 1:
        t = t.replace('.', '')
    elif re.fullmatch(r'-?[1-9]\d{0,2}\.\d{3}', t):
        t = t.replace('.', '')        # Türkçe metin dosyalarında 1.250 = 1250 (binlik ayırıcı)
    return float(t)


UNIT_ALIASES = {'AD': 'ADET', 'AD.': 'ADET', 'ADT': 'ADET', 'ADET': 'ADET', 'NIU': 'ADET', 'C62': 'ADET', 'PIECE': 'ADET', 'PCS': 'ADET', 'ADETLI': 'ADET',
                'KGM': 'KG', 'KG.': 'KG', 'KILO': 'KG', 'KİLO': 'KG', 'KILOGRAM': 'KG', 'KİLOGRAM': 'KG',
                'MTR': 'MT', 'MT.': 'MT', 'METRE': 'MT', 'M': 'MT', 'LTR': 'LT', 'LT.': 'LT', 'LİTRE': 'LT', 'LITRE': 'LT',
                'PKT': 'PAKET', 'PAK': 'PAKET', 'PAKET': 'PAKET', 'KOLİ': 'KOLİ', 'KOLI': 'KOLİ', 'KLI': 'KOLİ', 'DZN': 'DÜZİNE', 'DÜZİNE': 'DÜZİNE', 'DUZINE': 'DÜZİNE'}


def norm_unit(v):
    u = str(v or '').strip().replace('i', 'İ').replace('ı', 'I').upper()
    return UNIT_ALIASES.get(u, u)


def id_text(v):
    """Barkod / kod hücresi: Excel'in sayı olarak sakladığı 8680869455009.0 -> '8680869455009'."""
    if v is None:
        return ''
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return repr(v)
    return str(v).strip()


def txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


# ----------------------------------------------------------------------------------------------
# Dosya okuma (.xlsx bağımlılıksız, .csv)
# ----------------------------------------------------------------------------------------------
_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
       'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def _col_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheets(path):
    """Çalışma kitabındaki sayfa adlarını sırayla döndürür (en az bir ad)."""
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(str(path)) as z:
            wb = ET.fromstring(z.read('xl/workbook.xml'))
        names = [sh.get('name') or ('Sayfa%d' % (i + 1)) for i, sh in enumerate(wb.find('m:sheets', _NS).findall('m:sheet', _NS))]
        return names or ['Sayfa1']
    except Exception:
        return ['Sayfa1']


def read_xlsx(path, sheet=None):
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(str(path)) as z:
        names = set(z.namelist())
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('m:si', _NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % _NS['m'])))
        sheet_path = 'xl/worksheets/sheet1.xml'
        if 'xl/workbook.xml' in names and 'xl/_rels/workbook.xml.rels' in names:
            try:
                wb = ET.fromstring(z.read('xl/workbook.xml'))
                rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
                sheets = wb.find('m:sheets', _NS).findall('m:sheet', _NS)
                first = next((sh for sh in sheets if sheet and sh.get('name') == sheet), sheets[0])
                rid = first.get('{%s}id' % _NS['r'])
                for rel in rels:
                    if rel.get('Id') == rid:
                        tgt = rel.get('Target')
                        sheet_path = tgt.lstrip('/') if tgt.startswith('/') else 'xl/' + tgt
                        break
            except Exception:
                sheet_path = 'xl/worksheets/sheet1.xml'
        if sheet_path not in names:
            cands = sorted(n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml'))
            if not cands:
                raise ValueError('Excel dosyasında sayfa bulunamadı.')
            sheet_path = cands[0]
        root = ET.fromstring(z.read(sheet_path))
    rows = []
    for row in root.iter('{%s}row' % _NS['m']):
        cells = {}
        for c in row.findall('m:c', _NS):
            ref = c.get('r')
            idx = _col_index(ref) if ref else len(cells)
            t = c.get('t')
            v = c.find('m:v', _NS)
            if t == 's' and v is not None:
                val = shared[int(v.text)] if v.text is not None and int(v.text) < len(shared) else ''
            elif t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter('{%s}t' % _NS['m']))
            elif t in ('str', 'e'):
                val = v.text if v is not None else ''
            elif t == 'b':
                val = 'DOĞRU' if (v is not None and v.text == '1') else 'YANLIŞ'
            elif v is not None and v.text is not None:
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            else:
                val = None
            cells[idx] = val
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i) for i in range(width)])
        else:
            rows.append([])
        if len(rows) > MAX_ROWS + 1:
            raise ValueError('Dosya çok büyük (en çok %d satır).' % MAX_ROWS)
    return rows


def read_csv(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'cp1254', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ''
    delim = max((';', ',', '\t'), key=lambda d: first.count(d))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def read_table(path, sheet=None):
    """(başlıklar, satırlar[(excel_satır_no, [hücreler])]) döndürür. İlk dolu satır başlık kabul edilir."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in ('.xlsx', '.xlsm'):
        data = read_xlsx(p, sheet)
    elif ext in ('.csv', '.txt'):
        data = read_csv(p)
    else:
        raise ValueError('Desteklenen dosya türleri: .xlsx ve .csv (eski .xls dosyasını Excel\'de "Farklı Kaydet > .xlsx" yapın).')
    hi = next((i for i, r in enumerate(data) if any(str(x or '').strip() for x in r)), None)
    if hi is None:
        raise ValueError('Dosyada veri bulunamadı.')
    headers = [str(x or '').strip() for x in data[hi]]
    rows = [(i + 1, r) for i, r in enumerate(data) if i > hi and any(str(x or '').strip() for x in r)]
    if len(rows) > MAX_ROWS:
        raise ValueError('Dosya çok büyük (en çok %d satır).' % MAX_ROWS)
    return headers, rows


# ----------------------------------------------------------------------------------------------
# Dosya yazma (.xlsx)
# ----------------------------------------------------------------------------------------------
def _xl_col(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, headers, rows, sheet_name='Stok', text_cols=None):
    def cell(v, ref, style=0):
        if v is None or v == '':
            return ''
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(float(v)) if isinstance(v, float) else v)
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, _esc(str(v)))
    xml_rows = ['<row r="1">' + ''.join(cell(h, '%s1' % _xl_col(i), 1) for i, h in enumerate(headers)) + '</row>']
    for ri, r in enumerate(rows, 2):
        xml_rows.append('<row r="%d">' % ri + ''.join(cell(v, '%s%d' % (_xl_col(i), ri), 2 if isinstance(v, float) else 3 if ((i in text_cols) if text_cols is not None else FIELD_TYPE.get(FIELD_KEYS[i] if i < len(FIELD_KEYS) else '', '') == 'id') else 0)
                                                     for i, v in enumerate(r)) + '</row>')
    widths = ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, max(12, min(40, len(str(h)) + 4))) for i, h in enumerate(headers))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             '<cols>' + widths + '</cols><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
              '<fonts count="2"><font><sz val="10"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF143D68"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                               '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % _esc(sheet_name),
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': sheet, 'xl/styles.xml': styles}
    with zipfile.ZipFile(str(path), 'w', zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)


def export_stock(c, path, template=False):
    """Tüm aktif stokları şablon sütunlarıyla yazar (template=True: yalnız başlık + örnek satır)."""
    headers = [FIELD_LABEL[k] for k in FIELD_KEYS]
    if template:
        sample = {'barcode': '8680000000017', 'code': '1001', 'name': 'ÖRNEK KALEM 0.7', 'brand': 'MARKA', 'model': 'M1', 'grp': 'KALEM', 'subgrp': 'JEL KALEM', 'unit': 'ADET',
                  'vat': 20.0, 'special': '', 'warehouse': 'ANA DEPO', 'section': 'A', 'shelf': 'R1', 'min_qty': 5.0, 'max_qty': 50.0, 'qty': 20.0, 'last_cost': 10.0,
                  'avg_cost': 10.0, 'retail': 18.0, 'wholesale': 15.0, 'special_price': 0.0, 'gtip': '', 'otv_rate': 0.0, 'vat_exempt': '', 'einv_unit': 'C62',
                  'u2_name': 'KUTU', 'u2_barcode': '8680000000024', 'u2_qty': 12.0, 'u2_price': 200.0, 'u3_name': 'KOLİ', 'u3_barcode': '8680000000031', 'u3_qty': 120.0, 'u3_price': 1800.0}
        write_xlsx(path, headers, [[sample[k] for k in FIELD_KEYS]])
        return 1
    try:
        tax = {int(r[0]): r for r in c.execute('SELECT stock_id,gtip,otv_rate,vat_exempt,einv_unit FROM stock_tax_info').fetchall()}
    except sqlite3.Error:
        tax = {}
    try:
        units = {}
        for r in c.execute("SELECT stock_id,unit_name,base_quantity,barcode,retail FROM stock_unit_conversions ORDER BY base_quantity,unit_name").fetchall():
            units.setdefault(int(r[0]), []).append(r)
    except sqlite3.Error:
        units = {}
    rows = []
    for s in c.execute('SELECT * FROM stock WHERE active=1 ORDER BY name').fetchall():
        t = tax.get(int(s['id']))
        d = {k: s[k] for k in STOCK_UPDATE_KEYS + ['barcode', 'code', 'qty']}
        for n, u in zip(UNIT_SLOTS, units.get(int(s['id']), [])):    # ilk iki ek birim; fazlası dosyaya yazılmaz
            d.update({'u%d_name' % n: u[1], 'u%d_barcode' % n: u[3] or '', 'u%d_qty' % n: float(u[2]), 'u%d_price' % n: (float(u[4]) if u[4] else None)})
        for k in UNIT_KEYS:
            d.setdefault(k, None)
        d.update({'gtip': t['gtip'] if t else '', 'otv_rate': (t['otv_rate'] or 0.0) if t else 0.0, 'vat_exempt': t['vat_exempt'] if t else '', 'einv_unit': t['einv_unit'] if t else ''})
        rows.append([(float(d[k]) if FIELD_TYPE[k] in ('price', 'qty', 'vat', 'otv') and d[k] is not None else (d[k] if d[k] is not None else '')) for k in FIELD_KEYS])
    write_xlsx(path, headers, rows)
    return len(rows)


# ----------------------------------------------------------------------------------------------
# Plan (önizleme)
# ----------------------------------------------------------------------------------------------
def _load_index(c):
    stocks = {}
    by_barcode, by_code = {}, {}
    for s in c.execute('SELECT * FROM stock').fetchall():
        sid = int(s['id'])
        stocks[sid] = dict(s)
        if s['barcode']:
            by_barcode[str(s['barcode']).strip()] = sid
        if s['code']:
            by_code[str(s['code']).strip()] = sid
    try:
        for r in c.execute('SELECT stock_id,barcode FROM stock_barcodes WHERE active=1').fetchall():
            if r[1]:
                by_barcode.setdefault(str(r[1]).strip(), int(r[0]))
    except sqlite3.Error:
        pass
    try:
        tax = {int(r[0]): dict(gtip=r[1] or '', otv_rate=float(r[2] or 0), vat_exempt=r[3] or '', einv_unit=r[4] or '') for r in
               c.execute('SELECT stock_id,gtip,otv_rate,vat_exempt,einv_unit FROM stock_tax_info').fetchall()}
    except sqlite3.Error:
        tax = {}
    return stocks, by_barcode, by_code, tax


def _next_code_factory(c, by_code, reserved=()):
    mx = 0
    codes = list(by_code.keys())
    try:
        codes += [str(r[0]) for r in c.execute("SELECT code FROM deleted_stock_codes WHERE trim(COALESCE(code,''))<>''").fetchall()]
    except sqlite3.Error:
        pass
    for x in codes:
        if x.strip().isdigit():
            mx = max(mx, int(x.strip()))
    state = {'n': mx}

    def nxt():
        while True:
            state['n'] += 1
            k = str(state['n'])
            if k not in by_code and k not in reserved:     # dosyada sonradan gelen açık kodlarla çakışmasın
                return k
    return nxt


def build_plan(c, headers, rows, mapping, mode='both', default_vat=20.0, sales_ex_vat=False, cost_inc_vat=False, update_qty=False):
    """Her satır için dict: row_no, action(YENİ/GÜNCELLE/ATLA/HATA), msg, data, changes, sid. (plan, özet) döndürür."""
    stocks, by_barcode, by_code, tax = _load_index(c)
    unit_idx = {}
    try:
        for r in c.execute('SELECT stock_id,unit_name,base_quantity,barcode,retail FROM stock_unit_conversions').fetchall():
            unit_idx.setdefault(int(r[0]), {})[norm_unit(r[1])] = (float(r[2]), str(r[3] or ''), float(r[4] or 0))
    except sqlite3.Error:
        unit_idx = {}
    ci = mapping.get('code')
    reserved = {id_text(cells[ci]) for _rn, cells in rows if ci is not None and ci < len(cells) and id_text(cells[ci])}
    next_code = _next_code_factory(c, by_code, reserved)
    seen_bc, seen_code = {}, {}
    plan = []
    for row_no, cells in rows:
        def g(key):
            i = mapping.get(key)
            return None if i is None or i >= len(cells) else cells[i]
        res = {'row_no': row_no, 'action': 'ATLA', 'msg': [], 'data': {}, 'changes': {}, 'sid': None}
        errs = []
        data = {}
        bc, code = id_text(g('barcode')), id_text(g('code'))
        # --- alan ayrıştırma ---
        for key in FIELD_KEYS:
            if key in ('barcode', 'code'):
                continue
            raw = g(key)
            if raw is None or str(raw).strip() == '':
                continue
            t = FIELD_TYPE[key]
            try:
                if t in ('price', 'qty', 'vat', 'otv'):
                    v = parse_num(raw)
                    if v is None:
                        continue
                    if v < 0 and key != 'qty':
                        raise ValueError('negatif olamaz')
                    if key == 'qty' and v < 0:
                        raise ValueError('negatif olamaz')
                    if t == 'vat' and 0 < v < 1:
                        v = v * 100.0                       # 0,18 -> %18
                    if t == 'vat' or t == 'otv':
                        if v > 100 if t == 'vat' else v > 1000:
                            raise ValueError('geçersiz oran')
                    data[key] = round(v, 4)
                elif t == 'unitbc':
                    data[key] = id_text(raw)
                elif t == 'unit':
                    data[key] = norm_unit(txt(raw))
                elif t == 'unitcode':
                    data[key] = txt(raw).split(' ')[0].upper()[:3]
                elif t == 'gtip':
                    d = ''.join(ch for ch in txt(raw) if ch not in ' .-')
                    if d and (not d.isdigit() or not 4 <= len(d) <= 12):
                        raise ValueError('GTİP 4-12 rakam olmalı')
                    data[key] = d
                else:
                    data[key] = txt(raw)
            except ValueError as ex:
                errs.append('%s: %s' % (FIELD_LABEL[key], ex))
        # --- eşleşme ---
        sid_b = by_barcode.get(bc) if bc else None
        sid_c = by_code.get(code) if code else None
        if sid_b and sid_c and sid_b != sid_c:
            errs.append('Barkod ve Stok Kodu farklı iki kartı gösteriyor (#%s / #%s)' % (sid_b, sid_c))
        sid = sid_b or sid_c
        if not bc and not code and not data.get('name'):
            res['action'] = 'ATLA'
            res['msg'] = ['Satır boş / tanımlanamadı']
            plan.append(res)
            continue
        if bc and bc in seen_bc and not sid:
            errs.append('Aynı barkod dosyada tekrar ediyor (satır %d)' % seen_bc[bc])
        if code and code in seen_code and not sid:
            errs.append('Aynı stok kodu dosyada tekrar ediyor (satır %d)' % seen_code[code])
        if sid and (bc in seen_bc or code in seen_code):
            prev = seen_bc.get(bc) or seen_code.get(code)
            errs.append('Bu kart dosyada daha önce de geçiyor (satır %s)' % prev)
        if bc:
            seen_bc.setdefault(bc, row_no)
        if code:
            seen_code.setdefault(code, row_no)
        # vergi dönüşümleri için geçerli KDV
        cur = stocks.get(sid) if sid else None
        vat = data.get('vat', float(cur['vat']) if cur and cur['vat'] is not None else default_vat)
        if sales_ex_vat:
            for k in ('retail', 'wholesale', 'special_price', 'u2_price', 'u3_price'):
                if k in data:
                    data[k] = round(data[k] * (1 + vat / 100.0), 4)
        if cost_inc_vat:
            for k in ('last_cost', 'avg_cost'):
                if k in data:
                    data[k] = round(data[k] / (1 + vat / 100.0), 4)
        # --- ek birimler (kutu/koli): doğrulama ---
        units = []
        for n in UNIT_SLOTS:
            un, ub, uq, up = (data.get('u%d_name' % n, ''), data.get('u%d_barcode' % n, ''), data.get('u%d_qty' % n), data.get('u%d_price' % n))
            if not (un or ub or uq or up):
                continue
            if not un:
                errs.append('Birim %d: birim adı boş' % n)
                continue
            if not uq or uq <= 0:
                errs.append('Birim %d (%s): içerdiği adet sıfırdan büyük olmalı' % (n, un))
                continue
            un = norm_unit(un)
            if un == norm_unit(data.get('unit') or (cur['unit'] if cur else '')):
                errs.append('Birim %d: "%s" ana birimle aynı olamaz' % (n, un))
                continue
            if ub:
                owner = by_barcode.get(ub)
                if ub == bc or (owner is not None and owner != sid) or (ub in seen_bc and seen_bc[ub] != row_no):
                    errs.append('Birim %d barkodu (%s) başka bir kartta/satırda kullanılıyor' % (n, ub))
                    continue
                if ub in [u['barcode'] for u in units]:
                    errs.append('Birim %d barkodu aynı satırda tekrar ediyor' % n)
                    continue
            units.append({'name': un, 'barcode': ub, 'factor': round(uq, 6), 'price': (round(up, 4) if up else 0.0)})
        if errs:
            res['action'] = 'HATA'
            res['msg'] = errs
            plan.append(res)
            continue
        for u in units:
            if u['barcode']:
                seen_bc.setdefault(u['barcode'], row_no)
        res['units'] = units
        if sid is None:
            if mode == 'update':
                res.update(action='ATLA', msg=['Stok bulunamadı (yalnız güncelleme modu)'])
                plan.append(res)
                continue
            if not data.get('name'):
                res.update(action='HATA', msg=['Yeni stok için Stok Adı zorunlu'])
                plan.append(res)
                continue
            res['msg'] = []
            if not bc:
                res['msg'].append('Barkod yok')
            if not code:
                code = next_code()
                res['msg'].append('Stok kodu otomatik: ' + code)
            full = {'barcode': bc or None, 'code': code, 'name': data['name'], 'unit': data.get('unit') or 'ADET', 'vat': vat}
            for k in STOCK_UPDATE_KEYS + ['qty']:
                if k in data and k not in full:
                    full[k] = data[k]
            res['data'] = full
            res['tax'] = {k: data[k] for k in TAX_KEYS if k in data}
            res['action'] = 'YENİ'
            if bc:
                by_barcode[bc] = -1
            by_code[code] = -1
            plan.append(res)
            continue
        # mevcut kart
        if mode == 'new':
            res.update(action='ATLA', msg=['Stok zaten kayıtlı (yalnız yeni ekleme modu)'], sid=sid)
            plan.append(res)
            continue
        changes = {}
        for k in STOCK_UPDATE_KEYS:
            if k in data:
                old = cur.get(k)
                if FIELD_TYPE[k] in ('price', 'qty', 'vat'):
                    if abs(float(old or 0) - data[k]) > 0.00005:
                        changes[k] = (float(old or 0), data[k])
                elif str(old or '').strip() != str(data[k]).strip():
                    changes[k] = (old, data[k])
        if update_qty and 'qty' in data and abs(float(cur.get('qty') or 0) - data['qty']) > 0.00005:
            changes['qty'] = (float(cur.get('qty') or 0), data['qty'])
        if bc and not (cur.get('barcode') or '').strip() and by_barcode.get(bc) in (None, sid):
            changes['barcode'] = ('', bc)
        otax = tax.get(sid, {})
        taxchg = {}
        for k in TAX_KEYS:
            if k in data and str(otax.get(k, '' if k != 'otv_rate' else 0.0)) != str(data[k]) and not (k == 'otv_rate' and abs(float(otax.get(k, 0)) - data[k]) < 0.00005):
                taxchg[k] = (otax.get(k, ''), data[k])
        unitchg = []
        for u in units:
            o = unit_idx.get(sid, {}).get(u['name'])
            if o is None or abs(o[0] - u['factor']) > 0.000001 or (u['barcode'] and o[1] != u['barcode']) or (u['price'] and abs(o[2] - u['price']) > 0.00005):
                unitchg.append(u)
        if not changes and not taxchg and not unitchg:
            res.update(action='ATLA', msg=['Değişiklik yok'], sid=sid)
            plan.append(res)
            continue
        if 'qty' in data and not update_qty:
            res['msg'].append('Mevcut stok miktarı değiştirilmedi')
        res.update(action='GÜNCELLE', sid=sid, changes=changes, data=data, tax=taxchg, units=unitchg)
        plan.append(res)
    summary = {k: sum(1 for p in plan if p['action'] == k) for k in ('YENİ', 'GÜNCELLE', 'ATLA', 'HATA')}
    return plan, summary


def describe(p, stocks_by_id=None):
    note = ('   [' + '; '.join(p['msg']) + ']') if p.get('msg') and p['action'] in ('YENİ', 'GÜNCELLE') else ''
    if p['action'] == 'YENİ':
        d = p['data']
        un = ''.join(' | %s=%g adet%s' % (u['name'], u['factor'], (' (%s)' % u['barcode']) if u['barcode'] else '') for u in p.get('units', []))
        return '%s | %s | stok %g | satış %g%s%s' % (d.get('name', ''), d.get('unit', ''), d.get('qty', 0), d.get('retail', 0), un, note)
    if p['action'] == 'GÜNCELLE':
        parts = ['%s: %s → %s' % (FIELD_LABEL.get(k, k), ('%g' % a) if isinstance(a, float) else a or '∅', ('%g' % b) if isinstance(b, float) else b) for k, (a, b) in p['changes'].items()]
        parts += ['%s: %s → %s' % (FIELD_LABEL.get(k, k), a or '∅', b) for k, (a, b) in p.get('tax', {}).items()]
        parts += ['Birim %s: %g adet%s%s' % (u['name'], u['factor'], (' | barkod ' + u['barcode']) if u['barcode'] else '', (' | fiyat %g' % u['price']) if u['price'] else '') for u in p.get('units', [])]
        return '; '.join(parts) + note
    return '; '.join(p['msg'])


# ----------------------------------------------------------------------------------------------
# Uygulama
# ----------------------------------------------------------------------------------------------
def backup_database(c, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ('excel_aktarim_oncesi_%s.db' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    d = sqlite3.connect(str(dest))
    try:
        c.backup(d)
    finally:
        d.close()
    return dest


def _apply_units(c, sid, units):
    """Ek birimleri (kutu/koli) yazar; birim barkodu ek barkod tablosuna da işlenir (tüm barkod aramaları ürünü bulsun diye)."""
    if not units:
        return
    c.execute("CREATE TABLE IF NOT EXISTS stock_unit_conversions(id INTEGER PRIMARY KEY,stock_id INTEGER NOT NULL,unit_name TEXT NOT NULL,base_quantity REAL NOT NULL CHECK(base_quantity>0),UNIQUE(stock_id,unit_name))")
    have = {r[1] for r in c.execute('PRAGMA table_info(stock_unit_conversions)').fetchall()}
    for col, typ in (('barcode', "TEXT NOT NULL DEFAULT ''"), ('retail', 'REAL NOT NULL DEFAULT 0'), ('wholesale', 'REAL NOT NULL DEFAULT 0'), ('special_price', 'REAL NOT NULL DEFAULT 0')):
        if col not in have:
            c.execute('ALTER TABLE stock_unit_conversions ADD COLUMN %s %s' % (col, typ))
    for u in units:
        bc = u['barcode']
        if bc:
            other = c.execute('SELECT 1 FROM stock WHERE barcode=? AND id<>? UNION ALL SELECT 1 FROM stock_barcodes WHERE barcode=? AND stock_id<>? LIMIT 1', (bc, sid, bc, sid)).fetchone()
            if other:
                raise ValueError('Birim barkodu (%s) başka bir kartta kullanılıyor' % bc)
        old = c.execute('SELECT id,barcode,retail FROM stock_unit_conversions WHERE stock_id=? AND unit_name=?', (sid, u['name'])).fetchone()
        if old:
            newbc = bc or (old[1] or '')
            if old[1] and old[1] != newbc:
                c.execute('DELETE FROM stock_barcodes WHERE stock_id=? AND barcode=?', (sid, old[1]))
            c.execute('UPDATE stock_unit_conversions SET base_quantity=?,barcode=?,retail=? WHERE id=?', (u['factor'], newbc, u['price'] or float(old[2] or 0), old[0]))
            bc = newbc
        else:
            c.execute('INSERT INTO stock_unit_conversions(stock_id,unit_name,base_quantity,barcode,retail) VALUES(?,?,?,?,?)', (sid, u['name'], u['factor'], bc, u['price']))
        if bc:
            c.execute('INSERT OR IGNORE INTO stock_barcodes(stock_id,barcode,active) VALUES(?,?,1)', (sid, bc))
            c.execute('UPDATE stock_barcodes SET active=1 WHERE stock_id=? AND barcode=?', (sid, bc))


def apply_plan(c, plan, user='', update_qty=False, backup_folder=None, source_name=''):
    """Planı tek işlemde uygular. Dönüş: dict(yeni, guncel, hareket, yedek). Hata olursa her şey geri alınır."""
    todo = [p for p in plan if p['action'] in ('YENİ', 'GÜNCELLE')]
    if not todo:
        raise ValueError('Uygulanacak satır yok.')
    backup = backup_database(c, backup_folder) if backup_folder else None
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    batch = 'XLS-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    n_new = n_upd = n_move = 0
    c.execute('BEGIN IMMEDIATE')
    cur_row = None
    try:
        c.execute("CREATE TABLE IF NOT EXISTS stock_tax_info(stock_id INTEGER PRIMARY KEY,gtip TEXT DEFAULT '',otv_rate REAL DEFAULT 0,vat_exempt TEXT DEFAULT '',einv_unit TEXT DEFAULT '')")
        for p in todo:
            cur_row = p['row_no']
            if p['action'] == 'YENİ':
                d = dict(p['data'])
                qty = float(d.get('qty') or 0)
                cols = [k for k in ('barcode', 'code', 'name', 'brand', 'model', 'grp', 'subgrp', 'unit', 'vat', 'special', 'warehouse', 'section', 'shelf', 'min_qty', 'max_qty',
                                    'qty', 'last_cost', 'avg_cost', 'retail', 'wholesale', 'special_price') if k in d]
                cols += ['active', 'created', 'updated']
                vals = [d[k] for k in cols[:-3]] + [1, now, now]
                rid = c.execute('INSERT INTO stock(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), vals).lastrowid
                if abs(qty) > 0.000001:
                    c.execute('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)',
                              (rid, now, 'Açılış', qty, float(d.get('avg_cost') or d.get('last_cost') or 0), 'STOCK', rid, 'Excel aktarım (%s)' % batch))
                    n_move += 1
                c.execute("INSERT OR IGNORE INTO stock_price_vat_modes(stock_id,last_cost,retail,wholesale,special_price) VALUES(?,'HARIC','DAHIL','DAHIL','DAHIL')", (rid,))
                c.execute("INSERT OR IGNORE INTO stock_barcode_types(stock_id,barcode_type) VALUES(?,'EAN13')", (rid,)) if _table_exists(c, 'stock_barcode_types') else None
                _ensure_definitions(c, d, now)
                _upsert_tax(c, rid, p.get('tax', {}), {})
                _apply_units(c, rid, p.get('units', []))
                n_new += 1
            else:
                sid = p['sid']
                ch = p['changes']
                sets = {k: v[1] for k, v in ch.items() if k in STOCK_UPDATE_KEYS or k == 'barcode'}
                if update_qty and 'qty' in ch:
                    sets['qty'] = ch['qty'][1]
                if 'retail' in ch:
                    sets['previous_price'] = ch['retail'][0]      # stok kartı / Fiyat Yönetimi ile aynı: önceki satış fiyatı korunur
                if sets:
                    c.execute('UPDATE stock SET %s,updated=? WHERE id=?' % ','.join(k + '=?' for k in sets), list(sets.values()) + [now, sid])
                for k, lab in PRICE_LABEL.items():
                    if k in ch:
                        c.execute('INSERT INTO price_history(stock_id,dt,price_type,old_price,new_price,user,reason,batch_id) VALUES(?,?,?,?,?,?,?,?)',
                                  (sid, now, lab, ch[k][0], ch[k][1], user, 'Excel aktarım', batch))
                if update_qty and 'qty' in ch:
                    diff = ch['qty'][1] - ch['qty'][0]
                    c.execute('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)',
                              (sid, now, 'Excel Aktarım Miktar Düzeltme', diff, float(p['data'].get('avg_cost') or 0), 'STOCK_IMPORT', sid,
                               'Eski %g / Yeni %g / Fark %+g (%s)' % (ch['qty'][0], ch['qty'][1], diff, batch)))
                    n_move += 1
                _ensure_definitions(c, {k: v for k, v in sets.items()}, now)
                _upsert_tax(c, sid, {k: v[1] for k, v in p.get('tax', {}).items()}, {})
                _apply_units(c, sid, p.get('units', []))
                n_upd += 1
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (now, user, 'Stok', 'Excel İçe Aktarım', 0, '', json.dumps({'yeni': n_new, 'guncellenen': n_upd, 'hareket': n_move, 'toplu_no': batch}, ensure_ascii=False),
                   'Dosya: %s; yedek: %s' % (source_name, backup)))
        c.commit()
    except Exception as ex:
        c.rollback()
        raise RuntimeError('Aktarım geri alındı (hiçbir kayıt değişmedi). Satır %s: %s' % (cur_row, ex))
    return {'yeni': n_new, 'guncel': n_upd, 'hareket': n_move, 'yedek': str(backup) if backup else '', 'toplu_no': batch}


_PUR_SYN = {
    'key': {'barkod', 'barcode', 'barkodno', 'stokkodu', 'kod', 'urunkodu', 'stokkod'},
    'qty': {'miktar', 'adet', 'qty', 'quantity', 'mikar'},
    'cost': {'birimfiyat', 'fiyat', 'alisfiyati', 'alisfiyat', 'maliyet', 'birimmaliyet', 'cost', 'price', 'birimalis'},
    'vat': {'kdv', 'kdvorani', 'kdvyuzde'},
    'incl': {'kdvdahil', 'kdvdahilmi'},
    'name': {'urunadi', 'stokadi', 'ad', 'aciklama', 'urun', 'name'},
}


def parse_purchase_rows(c, headers, rows):
    """Excel/CSV alış faturası satırlarını stoklarla eşler. (kalemler, hatalar) döndürür.
    Kalem: {stock_id, qty, cost, vat(None=stok KDV'si), included(bool), excel_row}. Stok değişmez."""
    hn = [norm(h) for h in headers]
    idx = {}
    for k, syns in _PUR_SYN.items():
        for i, h in enumerate(hn):
            if h in syns and i not in idx.values():
                idx[k] = i
                break
    missing = [lab for k, lab in (('key', 'Barkod / Stok Kodu'), ('qty', 'Miktar'), ('cost', 'Birim Fiyat')) if k not in idx]
    if missing:
        raise ValueError('Gerekli sütun(lar) bulunamadı: ' + ', '.join(missing) + '. Başlıklar örn.: Barkod, Miktar, Birim Fiyat, KDV, KDV Dahil.')
    items, errors = [], []
    for rno, cells in rows:
        def g(k):
            i = idx.get(k)
            return cells[i] if i is not None and i < len(cells) else None
        key = id_text(g('key'))
        if not key:
            errors.append((rno, 'Barkod / stok kodu boş'))
            continue
        try:
            qty = parse_num(g('qty'))
            cost = parse_num(g('cost'))
            vat = parse_num(g('vat')) if 'vat' in idx else None
        except ValueError:
            errors.append((rno, 'Sayısal değer okunamadı'))
            continue
        if qty is None or qty <= 0:
            errors.append((rno, 'Miktar sıfırdan büyük olmalı'))
            continue
        if cost is None or cost <= 0:
            errors.append((rno, 'Birim fiyat sıfırdan büyük olmalı'))
            continue
        if vat is not None and not (0 <= vat <= 100):
            errors.append((rno, 'KDV oranı 0-100 arasında olmalı'))
            continue
        incl = norm(txt(g('incl'))) in ('evet', 'e', '1', 'true', 'var', 'dahil') if 'incl' in idx else False
        r = c.execute("SELECT id FROM stock WHERE active=1 AND (trim(COALESCE(barcode,''))=? OR trim(COALESCE(code,''))=?) LIMIT 1", (key, key)).fetchone()
        if r is None:
            try:
                r = c.execute("SELECT stock_id id FROM stock_barcodes b JOIN stock s ON s.id=b.stock_id WHERE s.active=1 AND b.active=1 AND trim(COALESCE(b.barcode,''))=? LIMIT 1", (key,)).fetchone()
            except Exception:
                r = None
        if r is None:
            errors.append((rno, 'Stok bulunamadı: ' + key))
            continue
        items.append({'stock_id': int(r[0]), 'qty': qty, 'cost': cost, 'vat': vat, 'included': incl, 'excel_row': rno})
    return items, errors


def parse_terminal_rows(c, headers, rows):
    """El terminali dosyası (CSV/TXT/XLSX): barkod[;miktar[;fiyat]]. Başlık satırı olmayabilir; miktar yoksa her satır 1 adet sayılır.
    Aynı ürünün satırları toplanır. Fiyat yoksa stoktaki son alış / ortalama maliyet kullanılır. (kalemler, hatalar) döndürür."""
    known = set()
    for syns in _PUR_SYN.values():
        known |= syns
    hn = [norm(h) for h in headers]
    has_header = any(h in known for h in hn)
    if has_header:
        idx = {}
        for k, syns in _PUR_SYN.items():
            for i, h in enumerate(hn):
                if h in syns and i not in idx.values():
                    idx[k] = i
                    break
        if 'key' not in idx:
            raise ValueError('Barkod / Stok Kodu sütunu bulunamadı.')
        data = rows
    else:
        # başlıksız dosya: ilk satır da veridir
        idx = {'key': 0}
        if len(headers) > 1:
            idx['qty'] = 1
        if len(headers) > 2:
            idx['cost'] = 2
        data = [(1, list(headers))] + list(rows)
    agg, order, errors = {}, [], []
    for rno, cells in data:
        def g(k):
            i = idx.get(k)
            return cells[i] if i is not None and i < len(cells) else None
        key = id_text(g('key'))
        if not key:
            errors.append((rno, 'Barkod boş'))
            continue
        try:
            qty = parse_num(g('qty')) if 'qty' in idx else 1.0
            if qty is None:
                qty = 1.0
            cost = parse_num(g('cost')) if 'cost' in idx else None
        except ValueError:
            errors.append((rno, 'Sayısal değer okunamadı'))
            continue
        if qty <= 0:
            errors.append((rno, 'Miktar sıfırdan büyük olmalı'))
            continue
        r = c.execute("SELECT id,last_cost,avg_cost FROM stock WHERE active=1 AND (trim(COALESCE(barcode,''))=? OR trim(COALESCE(code,''))=?) LIMIT 1", (key, key)).fetchone()
        if r is None:
            try:
                r = c.execute("SELECT s.id,s.last_cost,s.avg_cost FROM stock_barcodes b JOIN stock s ON s.id=b.stock_id WHERE s.active=1 AND b.active=1 AND trim(COALESCE(b.barcode,''))=? LIMIT 1", (key,)).fetchone()
            except Exception:
                r = None
        if r is None:
            errors.append((rno, 'Stok bulunamadı: ' + key))
            continue
        sid = int(r[0])
        use_cost = cost if (cost is not None and cost > 0) else (float(r[1] or 0) or float(r[2] or 0))
        if use_cost <= 0:
            errors.append((rno, 'Alış fiyatı bulunamadı (dosyada ve stok kartında yok): ' + key))
            continue
        if sid in agg:
            agg[sid]['qty'] += qty
        else:
            agg[sid] = {'stock_id': sid, 'qty': qty, 'cost': use_cost, 'vat': None, 'included': False, 'excel_row': rno}
            order.append(sid)
    return [agg[i] for i in order], errors


def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _ensure_definitions(c, d, now):
    """Yeni grup / alt grup / birim adlarını tanım listelerine ekler (stok kartı açılır listeleri için)."""
    try:
        if d.get('grp'):
            c.execute('INSERT OR IGNORE INTO stock_groups(name,active,created) VALUES(?,1,?)', (d['grp'].strip(), now))
        if d.get('unit'):
            c.execute('INSERT OR IGNORE INTO stock_units(name,active,created) VALUES(?,1,?)', (d['unit'].strip().upper(), now))
        if d.get('subgrp'):
            c.execute('INSERT OR IGNORE INTO stock_subgroups(group_name,name,active,created) VALUES(?,?,1,?)', ((d.get('grp') or '').strip(), d['subgrp'].strip(), now))
    except sqlite3.Error:
        pass


def _upsert_tax(c, sid, new, _old):
    if not new:
        return
    c.execute('INSERT OR IGNORE INTO stock_tax_info(stock_id) VALUES(?)', (sid,))
    for k, v in new.items():
        if k in TAX_KEYS:
            c.execute('UPDATE stock_tax_info SET %s=? WHERE stock_id=?' % k, (v, sid))


def error_report(plan, path):
    with open(str(path), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';', lineterminator='\n')
        w.writerow(['Excel Satırı', 'Durum', 'Açıklama'])
        for p in plan:
            if p['action'] in ('HATA', 'ATLA'):
                w.writerow([p['row_no'], p['action'], '; '.join(p['msg'])])
    return path


# ----------------------------------------------------------------------------------------------
# Arayüz
# ----------------------------------------------------------------------------------------------
def open_import_dialog(parent, connection_factory, user='', default_vat=20.0, db_path=None, on_done=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    w = tk.Toplevel(parent)
    w.title('Excel / CSV ile Stok İçe Aktarma')
    w.geometry('1280x760')
    w.minsize(1000, 600)
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    st = {'headers': [], 'rows': [], 'mapping': {}, 'plan': [], 'path': ''}
    tk.Label(w, text='EXCEL / CSV İLE STOK İÇE AKTARMA', font=('Segoe UI', 17, 'bold'), fg='#0b376d').pack(anchor='w', padx=16, pady=(14, 2))
    tk.Label(w, text='Dosya seçin → sütunlar başlıklardan otomatik eşlenir → önizlemeyi inceleyin → İÇE AKTAR. Uygulamadan önce veritabanı yedeği alınır; herhangi bir hatada hiçbir kayıt değişmez. '
                     'Boş hücreler mevcut değeri silmez.', fg='#34516f', wraplength=1230, justify='left').pack(anchor='w', padx=16)
    top = tk.Frame(w)
    top.pack(fill='x', padx=16, pady=8)
    pathv = tk.StringVar(value='(dosya seçilmedi)')
    mode = tk.StringVar(value='both')
    v_qty, v_sales, v_cost = tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=0)

    def btn(parent_, text, cmd, primary=False):
        return tk.Button(parent_, text=text, command=cmd, bg='#c8102e' if primary else '#ffffff', fg='#ffffff' if primary else '#111111', relief='solid', bd=1,
                         font=('Segoe UI', 10, 'bold'), padx=12, pady=6, cursor='hand2')
    btn(top, 'DOSYA SEÇ', lambda: choose()).pack(side='left')
    btn(top, 'ŞABLON İNDİR', lambda: template()).pack(side='left', padx=6)
    btn(top, 'SÜTUN EŞLEME', lambda: map_dialog()).pack(side='left')
    tk.Label(top, textvariable=pathv, fg='#0b376d', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=12)
    opt = tk.Frame(w)
    opt.pack(fill='x', padx=16)
    for text, val in (('Ekle + güncelle', 'both'), ('Yalnız yeni ekle', 'new'), ('Yalnız mevcutları güncelle', 'update')):
        tk.Radiobutton(opt, text=text, variable=mode, value=val, command=lambda: preview()).pack(side='left', padx=(0, 12))
    for text, var in (('Satış fiyatları KDV HARİÇ girildi', v_sales), ('Maliyetler KDV DAHİL girildi', v_cost), ('Mevcut kartların stok miktarını da güncelle (sayım farkı hareketi)', v_qty)):
        tk.Checkbutton(opt, text=text, variable=var, command=lambda: preview()).pack(side='left', padx=(0, 10))
    cols = ('row', 'action', 'barcode', 'code', 'name', 'detail')
    t = ttk.Treeview(w, columns=cols, show='headings', selectmode='browse')
    for k, h, wd in (('row', 'Satır', 55), ('action', 'Durum', 80), ('barcode', 'Barkod', 130), ('code', 'Kod', 80), ('name', 'Stok Adı', 280), ('detail', 'Ayrıntı / Değişiklik', 600)):
        t.heading(k, text=h)
        t.column(k, width=wd, anchor='w')
    for tag, col in (('YENİ', '#0f7a3d'), ('GÜNCELLE', '#0b5cad'), ('HATA', '#b00020'), ('ATLA', '#8a94a3')):
        t.tag_configure(tag, foreground=col)
    summ = tk.Label(w, text='', fg='#0b376d', font=('Segoe UI', 10, 'bold'))
    summ.pack(anchor='w', padx=16, pady=(8, 0))
    flt = tk.StringVar(value='Tümü')
    fr = tk.Frame(w)
    fr.pack(fill='x', padx=16)
    tk.Label(fr, text='Göster:').pack(side='left')
    cb = ttk.Combobox(fr, textvariable=flt, values=['Tümü', 'YENİ', 'GÜNCELLE', 'ATLA', 'HATA'], state='readonly', width=12)
    cb.pack(side='left', padx=6)
    cb.bind('<<ComboboxSelected>>', lambda e: fill())
    bar = tk.Frame(w)
    bar.pack(side='bottom', fill='x', padx=16, pady=(0, 12))
    vs = ttk.Scrollbar(w, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=vs.set)
    vs.pack(side='right', fill='y', padx=(0, 8), pady=8)
    t.pack(fill='both', expand=True, padx=(16, 0), pady=8)

    def fill():
        t.delete(*t.get_children())
        shown = 0
        for i, p in enumerate(st['plan']):
            if flt.get() != 'Tümü' and p['action'] != flt.get():
                continue
            d = p.get('data', {})
            sid_row = None
            bc = d.get('barcode') or ''
            code = d.get('code') or ''
            name = d.get('name') or ''
            t.insert('', 'end', iid=str(i), tags=(p['action'],), values=(p['row_no'], p['action'], bc, code, name, describe(p)))
            shown += 1
            if shown >= 3000:
                break

    def preview():
        if not st['headers']:
            return
        c = connection_factory()
        try:
            st['plan'], s = build_plan(c, st['headers'], st['rows'], st['mapping'], mode.get(), default_vat, bool(v_sales.get()), bool(v_cost.get()), bool(v_qty.get()))
        except Exception as ex:
            return messagebox.showerror('ALFABE', 'Önizleme hazırlanamadı:\n' + str(ex), parent=w)
        finally:
            c.close()
        summ.config(text='Satır: %d   •   YENİ: %d   •   GÜNCELLE: %d   •   ATLA: %d   •   HATA: %d   (eşlenen sütun: %d / %d)' %
                    (len(st['rows']), s['YENİ'], s['GÜNCELLE'], s['ATLA'], s['HATA'], len(st['mapping']), len(st['headers'])))
        fill()

    def choose():
        f = filedialog.askopenfilename(parent=w, title='Stok dosyası seçin', filetypes=[('Excel / CSV', '*.xlsx *.csv'), ('Tüm dosyalar', '*.*')])
        if not f:
            return
        try:
            h, r = read_table(f)
        except Exception as ex:
            return messagebox.showerror('ALFABE', 'Dosya okunamadı:\n' + str(ex), parent=w)
        st.update(headers=h, rows=r, mapping=auto_map(h), path=f)
        pathv.set('%s  (%d satır)' % (Path(f).name, len(r)))
        if 'name' not in st['mapping'] and 'barcode' not in st['mapping'] and 'code' not in st['mapping']:
            messagebox.showwarning('ALFABE', 'Barkod, Stok Kodu veya Stok Adı başlığı bulunamadı. "SÜTUN EŞLEME" ile sütunları elle eşleyin.', parent=w)
        preview()

    def template():
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')], initialfile='Stok_Aktarim_Sablonu.xlsx')
        if not f:
            return
        export_stock(None, f, template=True)
        messagebox.showinfo('ALFABE', 'Şablon kaydedildi:\n' + f + '\n\nÖrnek satırı silip kendi ürünlerinizi yazın. Maliyetler KDV hariç, satış fiyatları KDV dahil kabul edilir.', parent=w)

    def map_dialog():
        if not st['headers']:
            return messagebox.showinfo('ALFABE', 'Önce dosya seçin.', parent=w)
        d = tk.Toplevel(w)
        d.title('Sütun Eşleme')
        d.geometry('520x720')
        opts = ['(kullanma)'] + ['%d - %s' % (i + 1, h) for i, h in enumerate(st['headers'])]
        vars_ = {}
        canvas = tk.Canvas(d, highlightthickness=0)
        inner = tk.Frame(canvas)
        sb = ttk.Scrollbar(d, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        for r_, (key, lab, _s, _t) in enumerate(FIELDS):
            tk.Label(inner, text=lab, width=28, anchor='w').grid(row=r_, column=0, padx=8, pady=3)
            v = tk.StringVar(value=opts[st['mapping'][key] + 1] if key in st['mapping'] and st['mapping'][key] + 1 < len(opts) else opts[0])
            ttk.Combobox(inner, textvariable=v, values=opts, state='readonly', width=34).grid(row=r_, column=1, padx=4)
            vars_[key] = v

        def ok():
            m = {}
            for key, v in vars_.items():
                if v.get() != '(kullanma)':
                    m[key] = int(v.get().split(' - ')[0]) - 1
            st['mapping'] = m
            d.destroy()
            preview()
        tk.Button(inner, text='TAMAM', command=ok, bg='#c8102e', fg='white', font=('Segoe UI', 10, 'bold'), padx=14, pady=5).grid(row=len(FIELDS), column=0, columnspan=2, pady=12)

    def do_import():
        if not st['plan']:
            return messagebox.showinfo('ALFABE', 'Önce dosya seçin.', parent=w)
        s = {k: sum(1 for p in st['plan'] if p['action'] == k) for k in ('YENİ', 'GÜNCELLE', 'HATA')}
        if s['YENİ'] + s['GÜNCELLE'] == 0:
            return messagebox.showinfo('ALFABE', 'Aktarılacak (yeni / güncellenecek) satır yok.', parent=w)
        msg = '%d yeni stok eklenecek, %d stok güncellenecek.' % (s['YENİ'], s['GÜNCELLE'])
        if s['HATA']:
            msg += '\n%d HATALI satır atlanacak (hata raporunu kaydedebilirsiniz).' % s['HATA']
        msg += '\n\nUygulamadan önce veritabanı yedeği alınır. Devam edilsin mi?'
        if not messagebox.askyesno('ALFABE', msg, parent=w):
            return
        c = connection_factory()
        try:
            folder = (Path(db_path).parent / 'excel_aktarim_oncesi') if db_path else None
            res = apply_plan(c, st['plan'], user, bool(v_qty.get()), folder, Path(st['path']).name)
        except Exception as ex:
            return messagebox.showerror('ALFABE', str(ex), parent=w)
        finally:
            c.close()
        messagebox.showinfo('ALFABE', 'Aktarım tamamlandı.\n\nYeni: %d\nGüncellenen: %d\nStok hareketi: %d\nYedek: %s' % (res['yeni'], res['guncel'], res['hareket'], res['yedek'] or '-'), parent=w)
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        w.destroy()

    def save_errors():
        if not st['plan']:
            return
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.csv', filetypes=[('CSV', '*.csv')], initialfile='stok_aktarim_hata_raporu.csv')
        if f:
            error_report(st['plan'], f)
            messagebox.showinfo('ALFABE', 'Rapor kaydedildi:\n' + f, parent=w)
    btn(bar, 'İÇE AKTAR', do_import, True).pack(side='left')
    btn(bar, 'HATA / ATLANAN RAPORU', save_errors).pack(side='left', padx=6)
    btn(bar, 'KAPAT', w.destroy).pack(side='right')


def open_export_dialog(parent, connection_factory):
    from tkinter import filedialog, messagebox
    f = filedialog.asksaveasfilename(parent=parent, defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')], initialfile='Stok_Listesi_%s.xlsx' % datetime.date.today().strftime('%Y%m%d'))
    if not f:
        return
    c = connection_factory()
    try:
        n = export_stock(c, f)
    finally:
        c.close()
    messagebox.showinfo('ALFABE', '%d stok kartı kaydedildi:\n%s\n\nBu dosyayı Excel\'de düzenleyip İÇE AKTAR ile geri yükleyebilirsiniz.' % (n, f), parent=parent)
