# -*- coding: utf-8 -*-
"""ALFABE - Belgeden Alış: tedarikçiden gelen Excel / CSV / dijital PDF / e-fatura XML belgesini okur,
satırları stok kartlarıyla eşleştirir; kontrol ekranından onaylanınca Alış & Mal Kabul belgesine aktarır.

Güvence:
  * Dosya okunurken hiçbir şey kaydedilmez; kullanıcı onaylamadan stok kartı açılmaz.
  * Eşleşme sırası: barkod -> hatırlanan tedarikçi kodu -> stok kodu -> birebir ad -> benzer ad -> yeni ürün.
  * Belge toplamı ile satır toplamı karşılaştırılır (fark gösterilir, engellenmez).
"""
import datetime
import difflib
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata

import ALFABE_STOK_EXCEL as _X

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ----------------------------------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------------------------------
def tr_key(v):
    v = str(v or '').replace('ı', 'i').replace('İ', 'I')
    t = ''.join(ch for ch in unicodedata.normalize('NFKD', v).casefold() if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9% ]+', ' ', t)).strip()


def _num(v):
    try:
        return _X.parse_num(v)
    except Exception:
        return None


def new_line(**kw):
    d = {'code': '', 'barcode': '', 'name': '', 'qty': 0.0, 'unit': '', 'price': 0.0, 'vat': None, 'total': None,
         'disc': 0.0, 'src': '', 'status': 'new', 'stock_id': None, 'cands': [], 'retail': None, 'note': ''}
    d.update(kw)
    return d


def line_ok(ln):
    return bool(str(ln.get('name') or '').strip() or ln.get('code') or ln.get('barcode')) and 0 < float(ln.get('qty') or 0) < 1e6 and 0 < float(ln.get('price') or 0) < 1e7


# ----------------------------------------------------------------------------------------------
# Excel / CSV
# ----------------------------------------------------------------------------------------------
_COLS = {
    'barcode': ['barkod', 'barcode', 'ean', 'gtin', 'barkodno', 'barkodu', 'eankod', 'upc'],
    'code': ['stokkodu', 'urunkodu', 'kod', 'kodu', 'malkodu', 'sku', 'urunno', 'tedarikcikodu', 'katalogno', 'malzemekodu', 'stokno', 'itemcode', 'productcode', 'artikelno'],
    'name': ['urunadi', 'stokadi', 'aciklama', 'malinadi', 'malhizmet', 'malhizmetaciklamasi', 'urun', 'adi', 'isim', 'tanim', 'urunaciklamasi',
             'malzemeadi', 'urunismi', 'stokismi', 'description', 'name', 'product', 'malzeme', 'urunad'],
    'qty': ['miktar', 'adet', 'qty', 'quantity', 'sayi', 'mik', 'siparismiktari', 'sevkmiktari', 'miktari'],
    'unit': ['birim', 'olcubirimi', 'br', 'unit', 'birimi'],
    'price': ['birimfiyat', 'fiyat', 'netfiyat', 'birimfiyati', 'alisfiyati', 'listefiyati', 'bf', 'price', 'unitprice', 'birimfiyattl', 'netbirimfiyat', 'alisfiyat'],
    'vat': ['kdv', 'kdvorani', 'vat', 'kdvyuzde', 'kdv%'],
    'disc': ['iskonto', 'iskontoorani', 'isk', 'iskontoyuzde', 'indirim', 'indirimorani', 'iskonto1', 'iskontooran', 'iskontoyuzdesi', 'discount'],
    'total': ['tutar', 'toplam', 'satirtutari', 'malhizmettutari', 'tutartl', 'total', 'amount', 'nettutar', 'satirtoplami'],
}


def _col_of(h):
    n = _X.norm(h)
    if not n:
        return None
    for k, syn in _COLS.items():
        if n in syn:
            return k
    for k, syn in _COLS.items():
        if any(len(s) >= 5 and n.startswith(s) for s in syn):
            return k
    return None


def _read_rows(path):
    p = str(path).lower()
    if p.endswith(('.xlsx', '.xlsm')):
        return _X.read_xlsx(path)
    if p.endswith(('.csv', '.txt')):
        return _X.read_csv(path)
    raise ValueError('Desteklenmeyen dosya')


def read_excel(path):
    data = _read_rows(path)
    hdr = None
    for i, r in enumerate(data[:60]):
        cols = {}
        for j, c in enumerate(r):
            k = _col_of(c) if str(c or '').strip() else None
            if k and k not in cols:
                cols[k] = j
        if len(cols) >= 3 and ('qty' in cols or 'price' in cols) and ('name' in cols or 'code' in cols or 'barcode' in cols):
            hdr = (i, cols)
            break
    if hdr is None:
        raise ValueError('Başlık satırı bulunamadı. Sütun başlıkları örn.: Barkod, Ürün Adı, Miktar, Birim Fiyat, KDV.')
    hi, cols = hdr
    lines, meta = [], {'supplier': '', 'number': '', 'date': '', 'total': None, 'source': 'excel', 'incl': False}
    for j, c in enumerate(data[hi]):
        if 'kdvdahil' in _X.norm(c) or 'kdvli' in _X.norm(c):
            meta['incl'] = True
    for i in range(hi + 1, len(data)):
        r = data[i]
        if not any(str(x or '').strip() for x in r):
            continue

        def g(k):
            j = cols.get(k)
            return r[j] if j is not None and j < len(r) else None
        name = _X.txt(g('name'))
        first = ' '.join(str(x or '') for x in r[:3]).lower()
        if re.search(r'genel\s*toplam|ara\s*toplam|toplam\s*tutar|ödenecek|odenecek', first.replace('i̇', 'i')) and not name:
            tv = _num(g('total'))
            if tv:
                meta['total'] = tv
            continue
        try:
            q = _num(g('qty'))
            pr = _num(g('price'))
            tt = _num(g('total'))
            vt = _num(g('vat'))
            dc = _num(g('disc'))
        except Exception:
            q = pr = tt = vt = dc = None
        if pr is None and q and tt:
            pr = tt / q
        if not name and not _X.id_text(g('code')) and not _X.id_text(g('barcode')):
            continue
        bc = _X.id_text(g('barcode'))
        cd = _X.id_text(g('code'))
        ln = new_line(name=name, code=cd, barcode=bc, qty=q or 0.0, unit=_X.txt(g('unit')), price=pr or 0.0,
                      vat=vt, total=tt, disc=(dc if dc and 0 < dc < 100 else 0.0), src='Satır %d' % (i + 1))
        lines.append(ln)
    if not lines:
        raise ValueError('Belgede ürün satırı bulunamadı.')
    return meta, lines


# ----------------------------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------------------------
def _pypdf():
    try:
        import pypdf
        return pypdf
    except Exception:
        return None


_UNITS = {'ADET', 'AD', 'ADT', 'KG', 'KGM', 'KOLI', 'KOLİ', 'PKT', 'PAKET', 'DZN', 'DUZINE', 'DÜZİNE', 'MT', 'M', 'MTR', 'LT', 'LTR', 'TOP', 'RULO', 'DZ', 'DST', 'TAKIM', 'CIFT', 'ÇİFT', 'TAKIM', 'SET', 'KUTU', 'BOX', 'C62', 'NIU', 'PCS', 'KL', 'PK'}
_NUMTOK = re.compile(r'^[%₺]?-?\d[\d.,]*%?$|^[%₺]$|^TL$|^tl$|^TRY$', re.I)
_SKIP = re.compile(r'toplam|kdv\s*(tutar|oran)?|genel|iban|yaln[ıi]z|tarih|adres|vergi\s*d|tel\b|www|sayfa|öden|oden|matrah|iskonto|banka|fatura\s*no|irsaliye|mersis|ticaret|sicil|e-posta|mail|fax|not\s*:', re.I)


def pdf_text(path):
    pp = _pypdf()
    if pp is None:
        raise ValueError('PDF okuma bileşeni (pypdf) bulunamadı. Program klasöründe "pypdf" klasörü bulunmalıdır.')
    rd = pp.PdfReader(str(path))
    if getattr(rd, 'is_encrypted', False):
        try:
            rd.decrypt('')
        except Exception:
            raise ValueError('PDF şifreli.')
    pages = []
    for pg in rd.pages:
        t = ''
        try:
            t = pg.extract_text(extraction_mode='layout') or ''
        except Exception:
            try:
                t = pg.extract_text() or ''
            except Exception:
                t = ''
        pages.append(t)
    return '\n'.join(pages), rd


def _pdf_embedded_xml(rd):
    try:
        att = rd.attachments
    except Exception:
        return None
    for nm, blobs in (att or {}).items():
        if str(nm).lower().endswith('.xml'):
            for b in blobs:
                if b and b'Invoice' in b[:4000] or b and b'invoice' in b[:4000]:
                    return b
    return None


def _split_row(line):
    """(ad_parçaları, sayı_belirteçleri, birim). Birim, sayıların arasında da olabilir (Miktar Birim Fiyat ...)."""
    toks = line.split()
    nums, unit = [], ''
    while toks:
        t = toks[-1]
        if _NUMTOK.match(t):
            nums.insert(0, toks.pop())
        elif not unit and t.upper() in _UNITS and nums:
            unit = toks.pop()
        else:
            break
    return toks, nums, unit


def _clean_nums(nums):
    out = []   # (değer, yüzde_mi, belirteç)
    for t in nums:
        if t.upper() in ('TL', 'TRY', '\u20ba', '%'):
            continue
        pct = '%' in t
        v = _num(t.replace('\u20ba', ''))
        if v is not None:
            out.append((v, pct, t))
    return out


def _pick_numbers(nums):
    """Sondaki sayılardan (miktar, birim fiyat, KDV, tutar, kullanılmayan_baş_sayı_adedi) seçer."""
    pct = [v for v, p, _t in nums if p]
    vals = [(v, i) for i, (v, p, _t) in enumerate(nums) if not p]
    vat = pct[0] if pct else None
    q = pr = tt = None
    first = len(nums)
    n = len(vals)
    if n >= 3:
        best = None
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    a, b, c = vals[i][0], vals[j][0], vals[k][0]
                    if a > 0 and b > 0 and abs(a * b - c) <= max(0.06, abs(c) * 0.02):
                        best = (i, j, k)
        if best:
            q, pr, tt = (vals[x][0] for x in best)
            first = vals[best[0]][1]
        else:
            q, pr, tt = vals[0][0], vals[1][0], vals[2][0]   # tutmuyor: ilk üçü al, satır ⚠ ile işaretlenecek
            first = vals[0][1]
    elif n == 2:
        q, pr = vals[0][0], vals[1][0]
        first = vals[0][1]
    if vat is None and n >= 4:
        for v, _i in vals:
            if v in (0, 1, 8, 10, 18, 20) and v not in (q, pr, tt):
                vat = v
                break
    return q, pr, vat, tt, first


def _looks_code(t):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9\-_./:]{3,}', t)) and any(ch.isdigit() for ch in t)


def _n2(v):
    """Sayı çözümleme; '1.117.20' (binlik nokta + ondalık nokta) ve '28.763.90' biçimlerini de bilir."""
    t = str(v).strip().replace('₺', '').replace('TL', '').replace('tl', '').strip(' .:')
    if re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+\.\d{1,2}', t):
        i = t.rfind('.')
        return float(t[:i].replace('.', '') + '.' + t[i + 1:])
    return _num(t)


_SKIP2 = re.compile(r'ambar|\bsaat\b|kontrol|bakiye|pamukkale|\bbrm\b|mal[ıi]nc[iİ]ns|al[ıi][sş]veri[sş]|\bsayfa\b|\d/\d$', re.I)


_KDV_SET = (0, 1, 8, 10, 18, 20)


def _assign_pct(pcts, order):
    """Yüzdeli sayılardan (KDV, iskonto) ayırır."""
    if not pcts:
        return None, None
    if len(pcts) == 1:
        v = pcts[0]
        if order == ['isk']:
            return None, v
        return (v, None) if v in _KDV_SET else (None, v)
    a, b = pcts[0], pcts[1]
    if order == ['isk', 'kdv']:
        return b, a
    if order == ['kdv', 'isk']:
        return a, b
    if a in _KDV_SET and b not in _KDV_SET:
        return a, b
    if b in _KDV_SET and a not in _KDV_SET:
        return b, a
    return b, a


def _implied_disc(q, pr, tt, vat):
    """Satır tutarı miktar x fiyattan düşükse (KDV farkı hariç) örtük iskonto yüzdesi; yoksa 0."""
    if not (q and pr and tt):
        return 0.0
    base = q * pr
    for f in (1.0, 1.0 + float(vat or 0) / 100.0, 1.2, 1.1, 1.01, 1.08, 1.18):
        if abs(base * f - tt) <= max(0.06, abs(tt) * 0.005):
            return 0.0
    for f in ((1.0 + float(vat) / 100.0,) if vat else ()) + (1.0,):
        d = (1.0 - tt / (base * f)) * 100.0
        if 0.5 <= d <= 60.0:
            return round(d, 2)
    return 0.0


def parse_pdf_text(text):
    meta = {'supplier': '', 'number': '', 'date': '', 'total': None, 'source': 'pdf', 'incl': False}
    rows = [re.sub(r'[ \t ]+', ' ', l).strip() for l in text.splitlines()]
    rows = [l for l in rows if l]
    head = []
    for l in rows[:25]:
        if re.search(r'tutar|b\.?\s*fiyat', l, re.I) and re.search(r'adet|miktar|brm', l, re.I):
            break
        head.append(l)
    for l in head:
        if not meta['supplier'] and re.search(r'\bA\.?\s?Ş\.?\b|LTD|ŞTİ|STI\b|SAN\.|TİC\.|TIC\.|LİMİTED|ANONİM|ŞTİ|STI|SAN\.|TİC\.|TIC\.|LİMİTED|ANONİM', l, re.I) \
                and not re.search(r'\b(CAD|CADDE|CD|MAH|SOK|SK|NO)\b\.?\s*:?|alfabe', l, re.I):
            meta['supplier'] = re.sub(r'^(sat[ıi]c[ıi]|tedarik[çc]i|firma)\s*:?\s*', '', l, flags=re.I)[:80]
    full = '\n'.join(rows)
    m = re.search(r'(?:fatura|belge|irsaliye|seri|fi[sş]|sipari[sş])\s*(?:no|numaras[ıi])\s*[:\-]?\s*([A-Za-z0-9\-/]{4,})', full, re.I)
    if m:
        meta['number'] = m.group(1)
    m = re.search(r'(\d{2})[./-](\d{2})[./-](\d{4})', full)
    if m:
        meta['date'] = '%s-%s-%s' % (m.group(3), m.group(2), m.group(1))
    for l in rows:
        if re.search(r'öd?enecek\s*tutar|odenecek\s*tutar|genel\s*toplam|toplam\s*tutar', l, re.I):
            ns = [x for x in re.findall(r'\d[\d.,]*\d|\d', l.split('TOPLAM')[-1] if 'TOPLAM' in l else l)]
            vals = [_n2(x) for x in ns]
            vals = [v for v in vals if v is not None]
            if vals:
                meta['total'] = vals[-1]
    if re.search(r'kdv\s*:\s*0[.,]00', full, re.I) and not re.search(r'kdv\s*%|%\s*kdv|kdv\s*oran', full, re.I):
        meta['novat'] = True      # KDV ayrı gösterilmiyor: fiyatlar nihai (KDV dahil) kabul edilir
        meta['incl'] = True
    order = []
    for l in rows:
        if re.search(r'adet|miktar|brm', l, re.I) and re.search(r'tutar|fiyat', l, re.I):
            mi, mk = re.search(r'\bisk', l, re.I), re.search(r'\bkdv', l, re.I)
            if mi and mk:
                order = ['isk', 'kdv'] if mi.start() < mk.start() else ['kdv', 'isk']
            elif mi:
                order = ['isk']
            break
    lines = []
    last = None
    pend_bc = ''
    for l in rows:
        toks, nums, unit = _split_row(l)
        nums = [t.rstrip('.') if t.upper().rstrip('.') in ('TL', 'TRY') else t for t in nums]
        isbc = lambda t: bool(re.fullmatch(r'\d{8,14}', t))
        bcs = [t for t in nums if isbc(t)] + [t for t in toks if isbc(t)]
        nums = [t for t in nums if not isbc(t)]
        toks = [t for t in toks if not isbc(t)]
        for t in [t for t in toks if not isbc(t) and re.search(r'\d{13}', t) and re.search(r'[A-Za-zÇĞİÖŞÜçğıöşü]', t)]:
            bcs.append(re.search(r'\d{13}', t).group(0))
            toks.remove(t)
        qty0 = None
        if len(toks) > 2 and re.fullmatch(r'\d+(?:[.,]\d+)?', toks[0]) and toks[1].upper() in _UNITS:
            qty0 = _n2(toks[0])         # "ADET BRM ... B.FİYAT TUTAR" düzeni: satır miktar + birimle başlar
            unit = toks[1]
            toks = toks[2:]
        elif toks and not unit and toks[0].upper() in _UNITS and len(toks) > 2:
            unit = toks.pop(0)
        cn = [(_n2(t) if '%' not in t else _num(t), '%' in t, t) for t in nums if t.upper().strip('.') not in ('TL', 'TRY', '₺', '%')]
        cn = [(v, p, t) for v, p, t in cn if v is not None]
        letters = sum(ch.isalpha() for ch in ' '.join(toks))
        if len(cn) >= (2 if qty0 is None else 1) and letters >= 3 and not (_SKIP.search(l) and qty0 is None) and not (_SKIP2.search(l) or (qty0 is None and re.search(r'sipari[sş]\s*fi[sş]i', l, re.I))):
            disc = 0.0
            if qty0 is not None:
                q = qty0
                plain = [x for x in cn if not x[1]]
                pr = plain[-2][0] if len(plain) >= 2 else plain[-1][0]
                tt = plain[-1][0] if len(plain) >= 2 else None
                extra = plain[:-2] if len(plain) >= 2 else []
                vat, dsc = _assign_pct([v for v, p, _t in cn if p], order)
                if dsc is None and extra and 'isk' in order and 0 < extra[0][0] <= 90:
                    dsc = extra[0][0]
            else:
                q, pr, vat, tt, first = _pick_numbers(cn)
                vat, dsc = _assign_pct([v for v, p, _t in cn if p], order)
                toks = toks + [t for (_v, _p, t) in cn[:first]]
                if not unit and toks and toks[-1].upper() in _UNITS:
                    unit = toks.pop()
            implied = False
            if dsc:
                disc = float(dsc)
            else:
                disc = _implied_disc(q, pr, tt, vat)
                implied = bool(disc)
            if q and pr:
                if toks and re.fullmatch(r'\d{1,3}[.)]?', toks[0]) and len(toks) > 2 and qty0 is None:
                    toks.pop(0)
                code = ''
                if qty0 is None and len(toks) > 1 and _looks_code(toks[0]):
                    code = toks.pop(0)
                if qty0 is not None and len(toks) > 2 and _looks_code(toks[-1]):
                    lt = re.sub(r'[^a-z0-9]', '', toks[-1].lower())
                    inside = any(lt in re.sub(r'[^a-z0-9]', '', x.lower()) or (len(re.sub(r'[^a-z0-9]', '', x.lower())) > 2 and re.sub(r'[^a-z0-9]', '', x.lower()) in lt) for x in toks[:-1])
                    if inside or re.search(r'[-:]', toks[-1]) or re.fullmatch(r'\d{4,7}', toks[-1]):
                        code = toks.pop()
                name = ' '.join(toks).strip()
                bc = next((x for x in bcs if len(x) == 13), bcs[0] if bcs else '')
                if not bc and pend_bc:
                    bc = pend_bc          # barkodu bir alt satıra kaymış satır (PDF yerleşimi)
                pend_bc = ''
                if code and isbc(code):
                    bc, code = code, ''
                if not code:
                    code = next((x for x in bcs if x != bc), '')
                last = new_line(name=name, code=code, barcode=bc, qty=q, unit=unit, price=pr, vat=vat, total=tt, disc=disc, src=l[:60])
                last['disc_implied'] = implied
                lines.append(last)
                continue
        if not cn and bcs and letters < 2:
            pend_bc = next((x for x in bcs if len(x) == 13), bcs[0])
            last = None
            continue
        # devam satırı (uzun ürün adının ikinci satırı) - en çok bir satır
        if last is not None and not cn and letters >= 2 and len(' '.join(toks)) < 45 and not _SKIP.search(l) and not _SKIP2.search(l) and ':' not in l and '*' not in l:
            last['name'] = (last['name'] + ' ' + ' '.join(toks)).strip()
        last = None
    if not lines:
        raise ValueError('PDF içinde ürün satırı okunamadı (taranmış/fotoğraf PDF olabilir).')
    return meta, lines


def read_pdf(path):
    text, rd = pdf_text(path)
    blob = _pdf_embedded_xml(rd)
    if blob:
        try:
            fd, tmp = tempfile.mkstemp(suffix='.xml')
            os.close(fd)
            with open(tmp, 'wb') as fh:
                fh.write(blob)
            try:
                return read_xml(tmp, source='pdf-xml')
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        except Exception:
            pass
    if len(text.strip()) < 20:
        return read_scanned(path)      # taranmış / fotoğraf PDF
    return parse_pdf_text(text)


def read_xml(path, source='xml'):
    import ALFABE_EFATURA as _E
    xd = _E.parse_incoming_xml(path)
    lines = []
    for i, x in enumerate(xd['lines']):
        codes = list(x.get('codes') or [])
        bc = next((c for c in codes if re.fullmatch(r'\d{8,14}', str(c))), '')
        cd = next((c for c in codes if c != bc), '')
        lines.append(new_line(name=x.get('name') or '', code=cd, barcode=bc, qty=float(x.get('qty') or 0), unit=x.get('unit') or '',
                              price=float(x.get('price') or 0), vat=x.get('vat') or None, src='Kalem %d' % (i + 1)))
    try:
        import xml.etree.ElementTree as _ET
        _root = _ET.parse(str(path)).getroot()
        _ils = [e for e in _root.iter() if e.tag.rsplit('}', 1)[-1] == 'InvoiceLine']
        for ln, el in zip(lines, _ils):
            for ac in el:
                if ac.tag.rsplit('}', 1)[-1] != 'AllowanceCharge':
                    continue
                kids = {k.tag.rsplit('}', 1)[-1]: (k.text or '').strip() for k in ac}
                if kids.get('ChargeIndicator', '').lower() == 'false':
                    f = _num(kids.get('MultiplierFactorNumeric'))
                    amt = _num(kids.get('Amount'))
                    if f:
                        ln['disc'] = round(f * 100.0 if f <= 1 else f, 2)
                    elif amt and ln['qty'] and ln['price']:
                        ln['disc'] = round(amt / (ln['qty'] * ln['price']) * 100.0, 2)
    except Exception:
        pass
    meta = {'supplier': xd.get('supplier') or '', 'number': xd.get('number') or '', 'date': xd.get('date') or '',
            'total': xd.get('payable'), 'source': source, 'incl': False}
    return meta, lines


# ----------------------------------------------------------------------------------------------
# Fotoğraf / taranmış PDF (Windows 10/11 yerleşik OCR; kelime konumlarıyla satır yeniden kurulur)
# ----------------------------------------------------------------------------------------------
_PS_WORDS = r"""
param([string]$Path,[string]$Lang)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asAct=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Await($op,$type){ $t=$asTask.MakeGenericMethod($type).Invoke($null,@($op)); $t.Wait(-1) | Out-Null; $t.Result }
function AwaitAct($op){ $t=$asAct.Invoke($null,@($op)); $t.Wait(-1) | Out-Null }
$eng=$null
if($Lang){ try{ $eng=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang)) }catch{} }
if(-not $eng){ $eng=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if(-not $eng){ Write-Output '##NOLANG'; exit 3 }
function OcrBitmap($bmp,$page){
  $res=Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  foreach($l in $res.Lines){ foreach($w in $l.Words){ $r=$w.BoundingRect; Write-Output ("{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $page,[int]$r.X,[int]$r.Y,[int]$r.Width,[int]$r.Height,$w.Text) } }
}
if($Path.ToLower().EndsWith('.pdf')){
  $null=[Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
  $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
  $doc=Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
  $n=[Math]::Min($doc.PageCount,20)
  for($i=0;$i -lt $n;$i++){
    $pg=$doc.GetPage($i)
    $ms=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $o=New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $o.DestinationWidth=[uint32]2400
    AwaitAct ($pg.RenderToStreamAsync($ms,$o))
    $dec=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ms)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp=Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    OcrBitmap $bmp $i
  }
} else {
  $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
  $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $dec=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bmp=Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  OcrBitmap $bmp 0
}
"""


def ocr_words(path, timeout=240):
    """([{page,x,y,w,h,t}], hata). Windows dışında ya da motor yoksa liste boş, hata açıklamalıdır."""
    import shutil
    import subprocess
    if not sys.platform.startswith('win'):
        return [], 'Fotoğraf / taranmış PDF okuma yalnız Windows 10/11 üzerinde çalışır.'
    tmp = tempfile.mkdtemp(prefix='alfabe_ocr_')
    try:
        ps1 = os.path.join(tmp, 'ocr.ps1')
        with open(ps1, 'w', encoding='utf-8-sig') as fh:
            fh.write(_PS_WORDS)
        flags = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        r = None
        for lang in ('tr-TR', ''):
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', ps1, '-Path', os.path.abspath(path), '-Lang', lang],
                               capture_output=True, timeout=timeout, creationflags=flags)
            out = r.stdout.decode('utf-8', 'replace')
            if r.returncode == 3 or '##NOLANG' in out:
                return [], "Windows'ta okuma dili bulunamadı (Ayarlar > Zaman ve Dil > Dil bölümünden Türkçe ekleyin)."
            words = []
            for ln in out.splitlines():
                p = ln.split('\t', 5)
                if len(p) == 6:
                    try:
                        words.append({'page': int(p[0]), 'x': int(p[1]), 'y': int(p[2]), 'w': int(p[3]), 'h': int(p[4]), 't': p[5].strip()})
                    except ValueError:
                        pass
            if r.returncode == 0 and words:
                return words, ''
        err = (r.stderr.decode('utf-8', 'replace').strip().splitlines() if r is not None else [])
        return [], 'Belgede okunabilir yazı bulunamadı' + (' (' + err[0][:120] + ')' if err else '') + '. Daha net/aydınlık bir fotoğraf deneyin.'
    except subprocess.TimeoutExpired:
        return [], 'Okuma çok uzun sürdü.'
    except Exception as ex:
        return [], 'Okuma yapılamadı: %s' % ex
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def words_to_text(words):
    """OCR kelimelerini konumlarına göre satırlara dizer (tablo satırı = tek metin satırı)."""
    out = []
    for pg in sorted({w['page'] for w in words}):
        ws = [w for w in words if w['page'] == pg and w['t']]
        if not ws:
            continue
        hs = sorted(w['h'] for w in ws)
        med = max(4, hs[len(hs) // 2])
        ws.sort(key=lambda w: w['y'] + w['h'] / 2.0)
        rows, cur, cy = [], [], None
        for w in ws:
            c = w['y'] + w['h'] / 2.0
            if cur and abs(c - cy) <= med * 0.6:
                cur.append(w)
                cy = sum(x['y'] + x['h'] / 2.0 for x in cur) / len(cur)
            else:
                if cur:
                    rows.append(cur)
                cur, cy = [w], c
        if cur:
            rows.append(cur)
        for r in rows:
            r.sort(key=lambda w: w['x'])
            out.append('  '.join(w['t'] for w in r))
    return '\n'.join(out)


def read_scanned(path):
    words, err = ocr_words(path)
    if not words:
        raise ValueError(err or 'Belge okunamadı.')
    meta, lines = parse_pdf_text(words_to_text(words))
    meta['source'] = 'ocr'
    meta['ocr'] = True
    return meta, lines


def _line_warn(ln, ocr=False):
    t = ln.get('total')
    if not (t and ln.get('qty') and ln.get('price')):
        return bool(ocr and ln.get('disc_implied'))
    base = float(ln['qty']) * float(ln['price']) * (1 - float(ln.get('disc') or 0) / 100.0)
    vat = ln.get('vat')
    for f in (1.0, 1.0 + float(vat) / 100.0 if vat else 1.0):
        if abs(base * f - float(t)) <= max(0.06, abs(float(t)) * 0.02):
            return bool(ocr and ln.get('disc_implied'))
    return True


def flag_lines(lines, meta=None):
    """Miktar x birim fiyat (iskontolu) belgedeki satır tutarıyla uyuşmayan satırları ⚠ ile işaretler (OCR hatası olabilir)."""
    ocr = bool(meta and meta.get('ocr'))
    for ln in lines:
        ln['warn'] = _line_warn(ln, ocr)
    return lines


def read_document(path):
    p = str(path).lower()
    if p.endswith(('.xlsx', '.xlsm', '.csv', '.txt')):
        meta, lines = read_excel(path)
    elif p.endswith('.pdf'):
        meta, lines = read_pdf(path)
    elif p.endswith('.xml'):
        meta, lines = read_xml(path)
    elif p.endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff')):
        meta, lines = read_scanned(path)
    elif p.endswith('.xls'):
        raise ValueError('Eski .xls dosyasını Excel\'de "Farklı Kaydet > .xlsx" yapın.')
    else:
        raise ValueError('Desteklenen türler: Excel (.xlsx), CSV, PDF, JPEG/PNG fotoğraf, e-fatura XML.')
    for ln in lines:
        if ln.get('vat') is not None and not (0 <= float(ln['vat']) <= 100):
            ln['vat'] = None
    flag_lines(lines, meta)
    return meta, lines


def lines_total(lines, vat_default=0.0, incl=False, novat=False):
    """Satırların KDV dahil toplamı (belge toplamıyla karşılaştırmak için)."""
    t = 0.0
    for ln in lines:
        if not line_ok(ln):
            continue
        v = ln['vat'] if ln.get('vat') is not None else (0.0 if novat else vat_default)
        net = float(ln['qty']) * float(ln['price']) * (1 - float(ln.get('disc') or 0) / 100.0)
        t += net if incl else net * (1 + float(v) / 100.0)
    return round(t, 2)


# ----------------------------------------------------------------------------------------------
# Eşleştirme + tedarikçi kodu hafızası
# ----------------------------------------------------------------------------------------------
def ensure_tables(c):
    c.execute('CREATE TABLE IF NOT EXISTS supplier_product_codes(supplier TEXT NOT NULL,code TEXT NOT NULL,stock_id INTEGER NOT NULL,doc_name TEXT,updated TEXT,PRIMARY KEY(supplier,code))')


def _sk(s):
    return tr_key(s)


def remember_codes(c, supplier, pairs):
    """pairs: [(belge_kodu, stock_id, belge_adı)]"""
    ensure_tables(c)
    n = 0
    for code, sid, nm in pairs:
        code = str(code or '').strip()
        if not code or not sid or not supplier.strip():
            continue
        c.execute('INSERT OR REPLACE INTO supplier_product_codes(supplier,code,stock_id,doc_name,updated) VALUES(?,?,?,?,?)',
                  (_sk(supplier), code, int(sid), nm or '', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        n += 1
    return n


def _find_key(c, key):
    key = str(key or '').strip()
    if not key:
        return None
    r = c.execute("SELECT id FROM stock WHERE active=1 AND (trim(COALESCE(barcode,''))=? OR trim(COALESCE(code,''))=?) LIMIT 1", (key, key)).fetchone()
    if r is None:
        try:
            r = c.execute("SELECT s.id id FROM stock_barcodes b JOIN stock s ON s.id=b.stock_id WHERE s.active=1 AND b.active=1 AND trim(COALESCE(b.barcode,''))=? LIMIT 1", (key,)).fetchone()
        except sqlite3.Error:
            r = None
    return int(r[0]) if r else None


def _nums(k):
    return set(re.findall(r'\d+', k))


def num_conflict(a, b):
    """İki ürün adında birbirini tutmayan sayılar varsa (3.SINIF / 7.SINIF) farklı üründür."""
    na, nb = _nums(a), _nums(b)
    da = {x for x in na - nb if not any(y.endswith(x) or x.endswith(y) for y in nb if len(y) >= 2 and len(x) >= 2)}
    db = {x for x in nb - na if not any(y.endswith(x) or x.endswith(y) for y in na if len(y) >= 2 and len(x) >= 2)}
    return bool(da and db)


def _tokens(s):
    return [t for t in tr_key(s).split() if len(t) > 1]


def match_lines(c, lines, supplier=''):
    ensure_tables(c)
    stocks = [(int(r[0]), r[1] or '', tr_key(r[1])) for r in c.execute('SELECT id,name FROM stock WHERE active=1')]
    by_name = {}
    for sid, nm, k in stocks:
        by_name.setdefault(k, sid)
    tok_index = {}
    for sid, nm, k in stocks:
        for t in set(k.split()):
            if len(t) > 2:
                tok_index.setdefault(t, set()).add(sid)
    names = {sid: nm for sid, nm, k in stocks}
    keys = {sid: k for sid, nm, k in stocks}
    skey = _sk(supplier)
    for ln in lines:
        ln['stock_id'] = None
        ln['cands'] = []
        ln['status'] = 'new'
        if not line_ok(ln):
            ln['status'] = 'bad'
            continue
        sid = None
        if ln.get('barcode'):
            sid = _find_key(c, ln.get('barcode'))
        if not sid and ln.get('code') and skey:
            r = c.execute('SELECT stock_id FROM supplier_product_codes WHERE supplier=? AND code=?', (skey, str(ln['code']).strip())).fetchone()
            if r and r[0] in names:
                sid = int(r[0])
        if not sid and ln.get('barcode') and skey:
            r = c.execute('SELECT stock_id FROM supplier_product_codes WHERE supplier=? AND code=?', (skey, str(ln['barcode']).strip())).fetchone()
            if r and r[0] in names:
                sid = int(r[0])
        if not sid and ln.get('code'):
            sid = _find_key(c, ln.get('code'))
        if sid:
            ln['stock_id'] = sid
            ln['status'] = 'ok'
            continue
        k = tr_key(ln.get('name'))
        if k and k in by_name:
            ln['stock_id'] = by_name[k]
            ln['status'] = 'ok'
            continue
        cand = set()
        for t in set(k.split()):
            cand |= tok_index.get(t, set())
        scored = []
        for s in cand:
            a, b = k, keys[s]
            r1 = difflib.SequenceMatcher(None, a, b).ratio()
            ta, tb = set(a.split()), set(b.split())
            r2 = len(ta & tb) / max(1, len(ta | tb))
            scored.append((max(r1, (r1 + r2) / 2 + 0.05 if r2 >= 0.5 else r1), s))
        scored.sort(reverse=True)
        ln['cands'] = [(s, names[s], round(sc, 2)) for sc, s in scored[:3]]
        good = [(sc, s) for sc, s in scored if not num_conflict(k, keys[s])]
        if good and good[0][0] >= 0.72:
            ln['stock_id'] = good[0][1]
            ln['status'] = 'sim'
    return lines


def unit_info(c, stock_id, unit_text):
    """Belgedeki birim, stoğun alt birimlerinden biriyse (KOLİ gibi) (ad, çarpan) döndürür; değilse None."""
    u = _X.norm_unit(unit_text)
    if not u:
        return None
    try:
        for r in c.execute('SELECT unit_name,base_quantity FROM stock_unit_conversions WHERE stock_id=?', (stock_id,)):
            if _X.norm_unit(r[0]) == u and float(r[1] or 0) > 0:
                return str(r[0]).strip(), float(r[1])
    except sqlite3.Error:
        pass
    return None


def next_code(c):
    """Sıradaki sayısal stok kodu (silinmiş/boşa çıkan kodlar dahil; geriye dönmez)."""
    mx = 0
    sqls = ["SELECT code FROM stock WHERE trim(COALESCE(code,''))<>''", "SELECT code FROM deleted_stock_codes WHERE trim(COALESCE(code,''))<>''"]
    for q in sqls:
        try:
            for r in c.execute(q):
                x = str(r[0] or '').strip()
                if x.isdigit():
                    mx = max(mx, int(x))
        except sqlite3.Error:
            pass
    return str(mx + 1)


def create_card(c, ln, code, default_unit='Adet', vat_default=20.0, retail=0.0, price_net=None):
    """Onaydan sonra yeni stok kartı açar (stok 0; miktar alış belgesiyle girer). stock_id döndürür."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    bc = str(ln.get('barcode') or '').strip()
    if bc and c.execute('SELECT 1 FROM stock WHERE barcode=?', (bc,)).fetchone():
        bc = ''
    unit = _X.norm_unit(ln.get('unit')) if ln.get('unit') else ''
    unit = {'ADET': 'Adet'}.get(unit, unit.title() if unit else default_unit)
    vat = ln['vat'] if ln.get('vat') is not None else vat_default
    cost = float(price_net if price_net is not None else ln['price'])
    cur = c.execute('INSERT INTO stock(barcode,code,name,unit,vat,qty,last_cost,avg_cost,retail,active,created,updated) VALUES(?,?,?,?,?,0,?,?,?,1,?,?)',
                    (bc or None, code, str(ln['name']).strip()[:200], unit, float(vat), cost, cost, float(retail or 0), now, now))
    return int(cur.lastrowid)


# ----------------------------------------------------------------------------------------------
# Kontrol ekranının modeli (arayüzden bağımsız, test edilebilir)
# ----------------------------------------------------------------------------------------------
class Review:
    def __init__(self, meta, lines, supplier='', markup=30.0, default_vat=20.0, incl=None):
        self.meta = meta
        self.lines = lines
        self.supplier = supplier or meta.get('supplier') or ''
        self.markup = float(markup)
        self.default_vat = float(default_vat)
        self.incl = bool(meta.get('incl')) if incl is None else bool(incl)
        for ln in lines:
            ln['use'] = ln['status'] != 'bad'

    def counts(self):
        r = {'ok': 0, 'sim': 0, 'new': 0, 'bad': 0}
        for ln in self.lines:
            if ln.get('use'):
                r[ln['status']] = r.get(ln['status'], 0) + 1
            elif ln['status'] == 'bad':
                r['bad'] += 1
        return r

    def revalidate(self, i):
        ln = self.lines[i]
        if ln.get('warn'):
            ln['warn'] = _line_warn(ln, bool(self.meta.get('ocr')))
        if not line_ok(ln):
            ln['status'] = 'bad'
            ln['use'] = False
        elif ln['status'] == 'bad':
            ln['status'] = 'sim' if ln.get('stock_id') else 'new'
            ln['use'] = True

    def set_stock(self, i, sid):
        ln = self.lines[i]
        ln['stock_id'] = int(sid) if sid else None
        ln['status'] = 'ok' if sid else 'new'
        self.revalidate(i)

    def make_new(self, i):
        self.lines[i]['stock_id'] = None
        self.lines[i]['status'] = 'new'
        self.revalidate(i)

    def remove(self, i):
        self.lines[i]['use'] = False

    def lines_sum(self):
        return lines_total([ln for ln in self.lines if ln.get('use')], self.default_vat, self.incl, bool(self.meta.get('novat')))

    def diff(self):
        t = self.meta.get('total')
        if t in (None, 0):
            return None
        return round(self.lines_sum() - float(t), 2)

    def retail_for(self, ln):
        if ln.get('retail') not in (None, ''):
            return float(ln['retail'])
        vat = ln['vat'] if ln.get('vat') is not None else self.default_vat
        net = float(ln['price']) / (1 + float(vat) / 100.0) if self.incl else float(ln['price'])
        return round(net * (1 + float(vat) / 100.0) * (1 + self.markup / 100.0), 2)

    @staticmethod
    def _learn_barcode(c, sid, bc):
        """Eşleşen stok kartında barkod boşsa, belgedeki barkod karta yazılır (başka kartta kullanılmıyorsa)."""
        bc = str(bc or '').strip()
        if not re.fullmatch(r'\d{8,14}', bc):
            return False
        r = c.execute('SELECT barcode FROM stock WHERE id=?', (sid,)).fetchone()
        if r is None or str(r[0] or '').strip():
            return False
        if c.execute("SELECT 1 FROM stock WHERE trim(COALESCE(barcode,''))=? UNION SELECT 1 FROM stock_barcodes WHERE barcode=?", (bc, bc)).fetchone():
            return False
        c.execute('UPDATE stock SET barcode=?,updated=? WHERE id=?', (bc, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sid))
        return True

    def commit(self, c, remember=True, default_unit='Adet'):
        """Seçili satırlar için gerekirse stok kartı açar; alış sepetine eklenecek kalemleri döndürür.
        Tek işlem: hata olursa hiçbir kart açılmaz."""
        entries, pairs = [], []
        c.execute('SAVEPOINT belge_alis')
        try:
            ensure_tables(c)
            used = set()
            for ln in self.lines:
                if not ln.get('use') or ln['status'] == 'bad':
                    continue
                vat = ln['vat'] if ln.get('vat') is not None else None
                sid = ln.get('stock_id')
                created = False
                if not sid:
                    code = next_code(c)
                    net = float(ln['price']) / (1 + float(vat if vat is not None else self.default_vat) / 100.0) if self.incl else float(ln['price'])
                    sid = create_card(c, ln, code, default_unit, self.default_vat, self.retail_for(ln), net)
                    ln['stock_id'] = sid
                    created = True
                if not created:
                    self._learn_barcode(c, sid, ln.get('barcode'))
                ui = None if created else unit_info(c, sid, ln.get('unit'))
                entries.append({'stock_id': sid, 'qty': float(ln['qty']), 'price': float(ln['price']), 'vat': vat, 'included': self.incl, 'disc': float(ln.get('disc') or 0),
                                'unit': ui[0] if ui else '', 'factor': ui[1] if ui else 1.0, 'created': created, 'name': ln['name']})
                key = ln.get('code') or ln.get('barcode')
                if key:
                    pairs.append((key, sid, ln['name']))
            if remember and self.supplier.strip():
                remember_codes(c, self.supplier, pairs)
            c.execute('RELEASE belge_alis')
        except Exception:
            c.execute('ROLLBACK TO belge_alis')
            c.execute('RELEASE belge_alis')
            raise
        return entries


# ----------------------------------------------------------------------------------------------
# Kontrol ekranı (Tk)
# ----------------------------------------------------------------------------------------------
def open_review(parent, con_factory, meta, lines, supplier, default_vat, on_done, money=None, markup=30.0, title='Belgeden Alış - Kontrol'):
    import tkinter as tk
    from tkinter import ttk
    money = money or (lambda v: ('%.2f' % v).replace('.', ','))
    R = Review(meta, lines, supplier, markup, default_vat)
    d = tk.Toplevel(parent)
    d.title(title)
    d.geometry('1250x700')
    d.minsize(1000, 560)
    try:
        d.state('zoomed')
    except Exception:
        try:
            d.attributes('-zoomed', True)
        except Exception:
            pass
    top = tk.Frame(d)
    top.pack(fill='x', padx=12, pady=(10, 4))
    sup = tk.StringVar(value=R.supplier)
    incl = tk.BooleanVar(value=R.incl)
    rem = tk.BooleanVar(value=True)
    mk = tk.StringVar(value=('%g' % markup))
    tk.Label(top, text='Tedarikçi:', font=('Segoe UI', 10, 'bold')).pack(side='left')
    tk.Entry(top, textvariable=sup, width=36).pack(side='left', padx=(4, 14))
    tk.Label(top, text='Belge: %s  %s' % (meta.get('number') or '-', meta.get('date') or ''), font=('Segoe UI', 10)).pack(side='left')
    top2 = tk.Frame(d)
    top2.pack(fill='x', padx=12)
    ttk.Checkbutton(top2, text='Belgedeki fiyatlar KDV dahil', variable=incl).pack(side='left')
    tk.Label(top2, text='   Yeni ürünlerde satış kârı %:').pack(side='left')
    tk.Entry(top2, textvariable=mk, width=5).pack(side='left')
    ttk.Checkbutton(top2, text='Bu tedarikçinin ürün kodlarını hatırla', variable=rem).pack(side='left', padx=14)
    cols = ('st', 'dbar', 'dcode', 'dname', 'qty', 'unit', 'price', 'disc', 'vat', 'stock', 'retail')
    heads = [('st', 'Durum', 90), ('dbar', 'Barkod', 120), ('dcode', 'Ürün Kodu', 90), ('dname', 'Stok Adı', 290), ('qty', 'Miktar', 65), ('unit', 'Birim', 60),
             ('price', 'Birim Fiyat', 85), ('disc', 'İskonto %', 70), ('vat', 'KDV %', 55), ('stock', 'Eşleşen Stok Kartı', 300), ('retail', 'Satış (yeni)', 85)]
    tree = ttk.Treeview(d, columns=cols, show='headings', selectmode='extended')
    for k, h, w in heads:
        tree.heading(k, text=h)
        tree.column(k, width=w, anchor='w' if k in ('st', 'dbar', 'dcode', 'dname', 'unit', 'stock') else 'e', stretch=(k in ('dname', 'stock')))
    tree.tag_configure('ok', background='#e3f6e3')
    tree.tag_configure('sim', background='#fff3c4')
    tree.tag_configure('new', background='#dfeeff')
    tree.tag_configure('bad', background='#ffd9d9')
    tree.tag_configure('off', foreground='#999999')
    tree.pack(fill='both', expand=True, padx=12, pady=6)
    if meta.get('ocr'):
        tk.Label(d, text='Bu belge fotoğraftan/taramadan okundu: sayıları (miktar, fiyat, KDV) özellikle kontrol edin. ⚠ işaretli satırlarda miktar × fiyat belgedeki tutarla tutmuyor.',
                 fg='#8a4b00', anchor='w', justify='left', wraplength=1180).pack(fill='x', padx=14)
    info = tk.Label(d, text='', font=('Segoe UI', 11, 'bold'), anchor='w', justify='left')
    info.pack(fill='x', padx=14)
    sname = {}

    def stock_label(sid):
        if sid not in sname:
            c = con_factory()
            try:
                r = c.execute('SELECT name,COALESCE(code,"") FROM stock WHERE id=?', (sid,)).fetchone()
            finally:
                c.close()
            sname[sid] = ('%s  [%s]' % (r[0], r[1])) if r else '?'
        return sname[sid]

    ST = {'ok': '✔ Eşleşti', 'sim': '≈ Benzer', 'new': '＋ Yeni ürün', 'bad': '✖ Okunamadı'}

    def refresh():
        R.incl = bool(incl.get())
        try:
            R.markup = float(mk.get().replace(',', '.') or 0)
        except ValueError:
            pass
        sel = tree.selection()
        tree.delete(*tree.get_children())
        for i, ln in enumerate(R.lines):
            tags = (ln['status'],) + (() if ln.get('use') else ('off',))
            tree.insert('', 'end', iid=str(i), tags=tags, values=(
                ('⚠ ' if ln.get('warn') else '') + ST[ln['status']] + ('' if ln.get('use') or ln['status'] == 'bad' else ' (çıkarıldı)'), ln.get('barcode') or '', ln.get('code') or '', ln['name'],
                ('%g' % ln['qty']), ln.get('unit') or '', money(ln['price']) if ln['price'] else '', ('%g' % ln['disc']) if ln.get('disc') else '', ('%g' % ln['vat']) if ln.get('vat') is not None else '',
                stock_label(ln['stock_id']) if ln.get('stock_id') else ('— yeni stok kartı açılacak —' if ln['status'] == 'new' else '—'),
                money(R.retail_for(ln)) if ln['status'] == 'new' else ''))
        for s in sel:
            if tree.exists(s):
                tree.selection_add(s)
        n = R.counts()
        df = R.diff()
        msg = '✔ %d eşleşti   ≈ %d benzer   ＋ %d yeni ürün   ✖ %d okunamadı        Satır toplamı (KDV dahil): %s' % (n['ok'], n['sim'], n['new'], n['bad'], money(R.lines_sum()))
        if meta.get('total'):
            msg += '   Belge toplamı: %s   ' % money(float(meta['total']))
            msg += 'FARK YOK ✔' if abs(df) < 0.06 else 'FARK: %s' % money(df)
        info.config(text=msg, fg='#0a7a0a' if (df is not None and abs(df) < 0.06) else '#8a1c1c' if df is not None else '#333333')

    def cur():
        s = tree.selection()
        return [int(x) for x in s]

    def edit_cell(ev):
        if tree.identify_region(ev.x, ev.y) != 'cell':
            return
        row = tree.identify_row(ev.y)
        col = tree.identify_column(ev.x)
        if not row:
            return
        key = cols[int(col[1:]) - 1]
        i = int(row)
        ln = R.lines[i]
        fmap = {'dname': 'name', 'qty': 'qty', 'unit': 'unit', 'price': 'price', 'vat': 'vat', 'dcode': 'code', 'dbar': 'barcode', 'disc': 'disc', 'retail': 'retail'}
        if key == 'stock':
            return pick_stock()
        if key not in fmap or (key == 'retail' and ln['status'] != 'new'):
            return
        f = fmap[key]
        x, y, w, h = tree.bbox(row, col)
        e = tk.Entry(tree)
        e.insert(0, '' if ln.get(f) in (None, 0, 0.0) else str(ln[f]).replace('.', ',') if isinstance(ln.get(f), float) else str(ln.get(f)))
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        e.select_range(0, 'end')

        def done(_=None):
            v = e.get().strip()
            try:
                if f in ('qty', 'price', 'vat', 'retail', 'disc'):
                    nv = _num(v)
                    ln[f] = (nv if nv is not None else (None if f in ('vat', 'retail') else 0.0))
                    if f == 'disc':
                        ln['disc_implied'] = False
                else:
                    ln[f] = v
            except Exception:
                pass
            e.destroy()
            R.revalidate(i)
            refresh()
        e.bind('<Return>', done)
        e.bind('<FocusOut>', done)
        e.bind('<Escape>', lambda ev=None: e.destroy())
    tree.bind('<Double-1>', edit_cell)

    def pick_stock(i=None):
        sel = cur()
        if i is None:
            if not sel:
                return
            i = sel[0]
        ln = R.lines[i]
        p = tk.Toplevel(d)
        p.title('Stok kartı seç')
        p.geometry('620x420')
        p.transient(d)
        q = tk.StringVar(value=ln['name'])
        e = tk.Entry(p, textvariable=q, font=('Segoe UI', 11))
        e.pack(fill='x', padx=10, pady=8)
        lb = tk.Listbox(p, font=('Segoe UI', 10))
        lb.pack(fill='both', expand=True, padx=10)
        ids = []

        def fill(*_):
            lb.delete(0, 'end')
            ids.clear()
            t = q.get().strip()
            c = con_factory()
            try:
                cs = []
                for s, nm, sc in ln.get('cands', []):
                    cs.append((s, nm))
                words = [w for w in tr_key(t).split() if w]
                sql = 'SELECT id,name,COALESCE(code,"") FROM stock WHERE active=1'
                par = []
                for w in words[:4]:
                    sql += ' AND lower(name) LIKE ?'
                    par.append('%' + w + '%')
                rows = [(r[0], '%s  [%s]' % (r[1], r[2])) for r in c.execute(sql + ' ORDER BY name LIMIT 60', par)] if words else []
            finally:
                c.close()
            seen = set()
            for s, nm in [(s, stock_label(s)) for s, nm in cs] + rows:
                if s in seen:
                    continue
                seen.add(s)
                lb.insert('end', nm)
                ids.append(s)
        q.trace_add('write', fill)
        fill()

        def ok(_=None):
            z = lb.curselection()
            if z:
                R.set_stock(i, ids[z[0]])
                p.destroy()
                refresh()
        lb.bind('<Double-1>', ok)
        e.bind('<Return>', ok)
        tk.Button(p, text='SEÇ', command=ok, width=12).pack(pady=8)
        e.focus_set()

    def as_new():
        for i in cur():
            R.make_new(i)
        refresh()

    def drop():
        for i in cur():
            R.remove(i)
        refresh()

    def restore():
        for i in cur():
            if R.lines[i]['status'] != 'bad':
                R.lines[i]['use'] = True
        refresh()

    def accept():
        R.supplier = sup.get().strip()
        R.incl = bool(incl.get())
        refresh()
        c = con_factory()
        try:
            entries = R.commit(c, rem.get())
            c.commit()
        except Exception as ex:
            try:
                c.rollback()
            except Exception:
                pass
            info.config(text='Aktarılamadı: %s' % ex, fg='#8a1c1c')
            return
        finally:
            c.close()
        d.destroy()
        on_done(entries, R.supplier, meta)
    bar = tk.Frame(d)
    bar.pack(fill='x', padx=12, pady=(2, 10))
    for t, f in (('ÜRÜNÜ DEĞİŞTİR', pick_stock), ('YENİ ÜRÜN OLARAK AÇ', as_new), ('SATIRI ÇIKAR', drop), ('GERİ AL', restore)):
        tk.Button(bar, text=t, command=f, padx=10).pack(side='left', padx=(0, 6))
    tk.Button(bar, text='ALIŞ BELGESİNE AKTAR', command=accept, bg='#0b6b2b', fg='white', font=('Segoe UI', 10, 'bold'), padx=14).pack(side='right')
    tk.Button(bar, text='VAZGEÇ', command=d.destroy, padx=14).pack(side='right', padx=8)
    incl.trace_add('write', lambda *_: refresh())
    mk.trace_add('write', lambda *_: refresh())
    d._review = R
    d._accept = accept
    d._refresh = refresh
    refresh()
    return d
