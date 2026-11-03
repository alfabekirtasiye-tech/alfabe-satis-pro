# -*- coding: utf-8 -*-
"""ALFABE ISBN bilgi getirme (v425): barkod/ISBN okutulunca kitap adı, yazar, yayınevi ve basım yılını internetten getirir.

Kaynaklar (sırayla denenir, ilk bulunan kullanılır):
  1) Google Books API   https://www.googleapis.com/books/v1/volumes?q=isbn:<ISBN>
  2) Open Library API   https://openlibrary.org/api/books?bibkeys=ISBN:<ISBN>&format=json&jscmd=data
Kültür ve Turizm Bakanlığı ISBN Ajansı (isbn.gov.tr) için herkese açık, belgelenmiş bir API bulunmadığından kullanılmaz.
Dışarı yalnızca ISBN numarası gönderilir (müşteri, fiyat, stok bilgisi gönderilmez). Bulunan kayıtlar yerel önbelleğe alınır;
aynı ISBN ikinci kez internetsiz de gelir. İnternet yoksa sessizce vazgeçilir, form elle doldurulur.
"""
import datetime
import json
import re
import urllib.parse
import urllib.request

UA = 'ALFABE-SatisPro/1.0 (kitap-bilgi)'
last_status = ''   # 'bulundu' | 'yok' (kaynaklar yanıt verdi, kayıt yok) | 'ag' (hiçbir kaynağa ulaşılamadı)
last_error = ''


def _fetch(url, timeout=6):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _year(s):
    m = re.search(r'(1[5-9]\d\d|20\d\d)', str(s or ''))
    return m.group(1) if m else ''


def _clean(s):
    return ' '.join(str(s or '').split())


def parse_google(data):
    for it in (data or {}).get('items') or []:
        vi = it.get('volumeInfo') or {}
        title = _clean(vi.get('title'))
        if not title:
            continue
        sub = _clean(vi.get('subtitle'))
        if sub and sub.lower() not in title.lower():
            title = title + ': ' + sub
        return dict(title=title, author=', '.join(_clean(a) for a in (vi.get('authors') or []) if _clean(a)),
                    publisher=_clean(vi.get('publisher')), year=_year(vi.get('publishedDate')), source='Google Books')
    return None


def parse_openlibrary(data, isbn):
    rec = (data or {}).get('ISBN:' + isbn) or {}
    title = _clean(rec.get('title'))
    if not title:
        return None
    sub = _clean(rec.get('subtitle'))
    if sub and sub.lower() not in title.lower():
        title = title + ': ' + sub
    return dict(title=title, author=', '.join(_clean(a.get('name')) for a in (rec.get('authors') or []) if _clean(a.get('name'))),
                publisher=', '.join(_clean(p.get('name')) for p in (rec.get('publishers') or [])[:1] if _clean(p.get('name'))),
                year=_year(rec.get('publish_date')), source='Open Library')


def parse_sru(xml_text):
    """SRU yanıtındaki MARCXML (245 başlık, 100/700 yazar, 260/264 yayınevi-yıl) veya Dublin Core kaydını okur."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    def loc(e):
        return e.tag.split('}')[-1]
    def sub(df, code):
        for sf in df:
            if loc(sf) == 'subfield' and sf.get('code') == code and (sf.text or '').strip():
                return _clean(sf.text).strip(' /:;,.')
        return ''
    title = author = publisher = year = ''
    for df in root.iter():
        if loc(df) != 'datafield':
            continue
        t = df.get('tag')
        if t == '245' and not title:
            title = sub(df, 'a')
            b = sub(df, 'b')
            if title and b:
                title += ': ' + b
        elif t in ('100', '700') and not author:
            author = sub(df, 'a')
        elif t in ('260', '264') and not publisher:
            publisher = sub(df, 'b')
            year = _year(sub(df, 'c'))
    if not title:
        for e in root.iter():
            n, v = loc(e), _clean(e.text)
            if not v:
                continue
            if n == 'title' and not title:
                title = v
            elif n == 'creator' and not author:
                author = v
            elif n == 'publisher' and not publisher:
                publisher = v
            elif n == 'date' and not year:
                year = _year(v)
    if not title:
        return None
    return dict(title=title, author=author, publisher=publisher, year=year, source='SRU kütüphane kataloğu')


def _fetch_text(url, timeout=6):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/xml,text/xml'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


import html as _html
import os as _os
import tempfile as _tmp

_BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
_COVER_RE = re.compile(r'\s*\((k\.?\s*kapak|karton\s*kapak|ciltli|ci̇ltli|kapak[^)]*|broş[^)]*|bros[^)]*)\)\s*$', re.I)


def _price(s):
    """'110,00 TL' / '1.250,50' / '110.00' -> 110.0 ; bulunamazsa None."""
    m = re.search(r'(\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+,\d{1,2}|\d+\.\d{1,2}|\d+)', str(s or ''))
    if not m:
        return None
    t = m.group(1)
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return None


def _html_lines(h):
    h = re.sub(r'(?is)<(script|style|noscript)\b.*?</\1>', ' ', h)
    h = re.sub(r'(?is)<!--.*?-->', ' ', h)
    h = re.sub(r'(?i)</?(td|th|tr|div|p|li|ul|h\d|span|label|dt|dd|br|table|tbody|section|article|b|strong|a)\b[^>]*>', '\n', h)
    h = re.sub(r'(?s)<[^>]+>', ' ', h)
    h = _html.unescape(h).replace('\xa0', ' ')
    return [' '.join(x.split()) for x in h.split('\n') if x.strip()]


_NOISE = re.compile(r'yanlış|yanlis|hata|bildir|gönder|gonder|sepet|ekle\b|giriş|kullanıcı|şifre|e-?posta|lütfen|bekleyen|detaylar|kitabın özellikleri|^stokta$', re.I)


def _noise(t):
    return bool(_NOISE.search(str(t or '')))


def _by_id(h, words):
    """ASP.NET gibi sitelerde id içinde ad geçen etiketin metni (lblYazar, txtYayinevi ...). Hata bildirme / düğme metinleri atlanır."""
    for w in words:
        for m in re.finditer(r'(?is)<(span|div|td|label|a|h\d|p|input)\b[^>]*\bid\s*=\s*"[^"]*%s[^"]*"[^>]*>(.*?)</\1>' % w, h):
            t = ' '.join(_html.unescape(re.sub(r'(?s)<[^>]+>', ' ', m.group(2))).split())
            if t and not _noise(t):
                return t
        for m in re.finditer(r'(?is)<input\b[^>]*\bid\s*=\s*"[^"]*%s[^"]*"[^>]*\bvalue\s*=\s*"([^"]*)"' % w, h):
            t = _html.unescape(m.group(1)).strip()
            if t and not _noise(t):
                return t
    return ''


def _after_label(lines, labels, stop=()):
    """Etiket satırından sonraki değer: 'Yazar: X' veya 'Yazar' + sonraki satır."""
    low = [l.lower() for l in lines]
    for lab in labels:
        lab = lab.lower()
        for i, l in enumerate(low):
            if l.startswith(lab) and not _noise(lines[i]):
                rest = lines[i][len(lab):].lstrip(' :.-\t')
                if rest and (len(rest) < 200) and l[len(lab):len(lab) + 1] in (':', ' ', ''):
                    if l[len(lab):].lstrip().startswith(':') or not l[len(lab):len(lab) + 1].strip() == '':
                        return rest
                if l.rstrip(' :') == lab and i + 1 < len(lines):
                    nxt = lines[i + 1].lstrip(': ').strip()
                    if nxt and nxt.lower().rstrip(' :') not in stop:
                        return nxt
    return ''


def parse_product_html(h, isbn=''):
    """Bayi/dağıtıcı ürün sayfasından kitap adı, yazar, yayınevi, yıl, fiyat okur (id'ler ve etiket-değer çiftleri birlikte denenir)."""
    if not h or len(h) < 200:
        return None
    lines = _html_lines(h)
    d13 = ''.join(ch for ch in str(isbn or '') if ch.isdigit())
    if d13 and d13 not in re.sub(r'[\s-]', '', ' '.join(lines)):
        return None   # sayfada bu barkod yok: site vitrin/başka ürün göstermiş olabilir, yanlış kitabı ASLA doldurma
    labels_all = {'yazar', 'yayınevi', 'yayinevi', 'yayıncı', 'fiyat', 'barkod', 'kategori', 'isbn', 'stok', 'baskı yılı', 'basım yeri', 'sayfa sayısı', 'ürün adı', 'kitap adı'}
    author = _by_id(h, ('yazar', 'author')) or _after_label(lines, ('yazar', 'yazarı'), labels_all)
    publisher = _by_id(h, ('yayinevi', 'yayınevi', 'yayinci', 'publisher')) or _after_label(lines, ('yayınevi', 'yayinevi', 'yayıncı'), labels_all)
    year = _year(_by_id(h, ('baski', 'basim', 'yil', 'year')) or _after_label(lines, ('baskı yılı', 'basım yılı', 'basim yili', 'yayın yılı', 'yayın tarihi', 'basım tarihi'), labels_all))
    price = _price(_by_id(h, ('fiyat', 'price', 'tutar')) or _after_label(lines, ('fiyat', 'satış fiyatı', 'liste fiyatı'), labels_all))
    title = _by_id(h, ('urunadi', 'urunad', 'kitapadi', 'productname'))
    if not title:
        title = _after_label(lines, ('kitap adı', 'ürün adı', 'urun adi'), labels_all)
    if not title:
        low = [l.lower() for l in lines]
        for i, l in enumerate(low):
            if (l.rstrip(' :') in ('yazar', 'yazarı') or l.startswith(('yazar:', 'yazarı:'))) and i > 0 and not _noise(lines[i]):
                j = i - 1
                while j >= 0 and (_noise(lines[j]) or len(lines[j]) < 2) and i - j < 6:
                    j -= 1
                title = lines[j] if j >= 0 and not _noise(lines[j]) else ''
                break
    title = _COVER_RE.sub('', _clean(title))
    if not title or len(title) > 250 or _noise(title):
        return None
    if _noise(author):
        author = ''
    if _noise(publisher):
        publisher = ''
    return dict(title=title, author=_clean(author), publisher=_clean(publisher), year=year, price=price, source='')


def _fetch_html(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': _BROWSER_UA, 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'tr-TR,tr;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        cs = r.headers.get_content_charset() or ''
    for enc in ([cs] if cs else []) + ['utf-8', 'cp1254']:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('utf-8', 'replace')


def tr_upper(t):
    """Türkçe büyük harf: i→İ, ı→I."""
    return str(t or '').replace('i', 'İ').replace('ı', 'I').upper()


_MAP = (('title', ('kitap_adi', 'title', 'baslik', 'başlık', 'name', 'ad')), ('author', ('yazar', 'author', 'authors', 'creator')),
        ('publisher', ('yayinevi', 'yayınevi', 'publisher', 'yayinci')), ('year', ('yayin_tarihi', 'yayın tarihi', 'year', 'publishedDate', 'date', 'yil', 'yıl', 'publish_date')))


def parse_json_generic(data):
    """Bilinmeyen JSON yapısında kitap adı/yazar/yayınevi/yıl alanlarını bulur (iç içe de olsa)."""
    def walk(o):
        if isinstance(o, dict):
            yield o
            for x in o.values():
                yield from walk(x)
        elif isinstance(o, list):
            for x in o:
                yield from walk(x)
    for d in walk(data):
        low = {str(k).lower().rstrip(': '): v for k, v in d.items()}
        out = {}
        for key, names in _MAP:
            for n in names:
                v = low.get(n.lower())
                if isinstance(v, list):
                    v = ', '.join(_clean(x.get('name') if isinstance(x, dict) else x) for x in v)
                if isinstance(v, (str, int)) and _clean(v):
                    out[key] = _clean(v)
                    break
        if out.get('title'):
            return dict(title=out['title'], author=out.get('author', ''), publisher=out.get('publisher', ''), year=_year(out.get('year', '')), source='')
    return None


def sources_load(c):
    """Kullanıcının kayıtlı kitap kaynakları: [{'ad','tur','url','aktif'}]. tur: SRU | JSON | SAYFA"""
    try:
        r = c.execute("SELECT v FROM settings WHERE k='kitap_kaynaklar'").fetchone()
        lst = json.loads(r[0]) if r and r[0] else []
    except Exception:
        lst = []
    out = []
    for x in lst if isinstance(lst, list) else []:
        if isinstance(x, dict) and str(x.get('url', '')).strip().lower().startswith(('http://', 'https://')):
            out.append(dict(ad=str(x.get('ad', '')).strip() or 'Kaynak', tur=x.get('tur') if x.get('tur') in ('SRU', 'JSON', 'SAYFA', 'URUN') else 'JSON',
                            url=str(x['url']).strip(), aktif=0 if str(x.get('aktif', 1)) == '0' else 1))
            if 'boradagitim.com' in out[-1]['url'].lower() and '{isbn}' not in out[-1]['url']:
                # Bora Dağıtım: ürün sayfası adresi (barkodla) otomatik kullanılır.
                out[-1]['tur'] = 'URUN'
                out[-1]['url'] = 'https://boradagitim.com/Istem/Urun/Detay.aspx?Barcode={isbn}'
    return out


def sources_save(c, lst):
    c.execute('CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)')
    c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('kitap_kaynaklar',?)", (json.dumps(lst, ensure_ascii=False),))
    c.commit()


def page_url(src, isbn):
    u = src['url']
    return u.replace('{isbn}', isbn) if '{isbn}' in u else u + ('&' if '?' in u else '?') + 'q=' + isbn


KITAPI_URL = 'http://digittall.duckdns.org:5010/kitapi/v1/'


def parse_kitapi(data):
    """Topluluk Türkçe kitap API'si (github.com/beeali68/kitapi): message[] içinde kitap_adi, yazar, yayinevi, kitap_bilgileri."""
    msg = (data or {}).get('message') if isinstance(data, dict) else None
    if isinstance(msg, dict):
        msg = [msg]
    for it in msg or []:
        if not isinstance(it, dict):
            continue
        title = _clean(it.get('kitap_adi'))
        if not title:
            continue
        kb = it.get('kitap_bilgileri')
        kbs = json.dumps(kb, ensure_ascii=False) if isinstance(kb, (dict, list)) else str(kb or '')
        m = re.search(r'Yay[ıi]n Tarihi[^0-9]{0,6}([0-9./-]{4,12})', kbs)
        return dict(title=title, author=_clean(it.get('yazar')), publisher=_clean(it.get('yayinevi')),
                    year=_year(m.group(1)) if m else '', source='Türkçe kitap API (kitapi)')
    return None


def ensure_cache(c):
    c.execute("CREATE TABLE IF NOT EXISTS book_isbn_cache(isbn TEXT PRIMARY KEY,title TEXT,author TEXT,publisher TEXT,year TEXT,source TEXT,dt TEXT)")
    try:
        c.execute('ALTER TABLE book_isbn_cache ADD COLUMN price REAL')
    except Exception:
        pass
    # v460 düzeltmesi: ilk ürün sayfası okuyucusunun yanlış kaydettiği (Bora) önbellek satırlarını bir kez temizle.
    try:
        c.execute('CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)')
        if not c.execute("SELECT 1 FROM settings WHERE k='kitap_cache_temiz_460b'").fetchone():
            c.execute("DELETE FROM book_isbn_cache WHERE UPPER(source) LIKE 'BORA%' OR UPPER(title) LIKE 'HATA B%' OR UPPER(author) LIKE '%YANLI%'")
            c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('kitap_cache_temiz_460b','1')")
            c.commit()
    except Exception:
        pass


def cache_get(c, isbn):
    ensure_cache(c)
    r = c.execute('SELECT title,author,publisher,year,source,price FROM book_isbn_cache WHERE isbn=?', (isbn,)).fetchone()
    return dict(title=r[0], author=r[1], publisher=r[2], year=r[3], source=(r[4] or '') + ' (kayıtlı)', price=r[5]) if r else None


def cache_put(c, isbn, d):
    ensure_cache(c)
    c.execute('INSERT OR REPLACE INTO book_isbn_cache(isbn,title,author,publisher,year,source,dt,price) VALUES(?,?,?,?,?,?,?,?)',
              (isbn, d['title'], d['author'], d['publisher'], d['year'], d['source'], datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), d.get('price')))
    c.commit()


def _name(url):
    return 'Google' if 'googleapis' in url else 'OpenLibrary' if 'openlibrary' in url else 'kitapi' if 'duckdns' in url else 'SRU'


def _short(ex):
    t = str(getattr(ex, 'reason', '') or ex)
    code = getattr(ex, 'code', None)
    return ('HTTP %s' % code) if code else ('zaman aşımı' if 'timed out' in t else t[:50])


def lookup(isbn, timeout=10, api_key='', sru_url='', kitapi=False, sources=None):
    """İnternetten arar. Bulursa dict(title,author,publisher,year,source), bulamazsa None. Ağ hatası fırlatmaz."""
    global last_status, last_error
    last_status, last_error = 'yok', ''
    answered = 0
    errs = []
    isbn = ''.join(ch for ch in str(isbn or '') if ch.isdigit())
    if len(isbn) not in (10, 13):
        return None
    g = 'https://www.googleapis.com/books/v1/volumes?q=isbn:%s&maxResults=3' % urllib.parse.quote(isbn)
    if api_key:
        g += '&key=' + urllib.parse.quote(api_key)
    steps = ((g, parse_google), ('https://openlibrary.org/api/books?bibkeys=ISBN:%s&format=json&jscmd=data' % urllib.parse.quote(isbn), lambda d: parse_openlibrary(d, isbn)))
    if sru_url:
        # Koha SRU: ayarlardaki adrese isbn sorgusu eklenir (örn. http://sunucu:9998/biblios)
        sep = '&' if '?' in sru_url else '?'
        u = sru_url + sep + urllib.parse.urlencode({'version': '1.1', 'operation': 'searchRetrieve', 'query': 'bath.isbn=' + isbn,
                                                     'maximumRecords': '1', 'recordSchema': 'marcxml'})
        try:
            r = parse_sru(_fetch_text(u, timeout))
            answered += 1
        except Exception as ex:
            r = None
            last_error = repr(ex)[:120]
        if r:
            last_status = 'bulundu'
            return r
    for src in sources or []:
        if not src.get('aktif', 1) or src.get('tur') == 'SAYFA':
            continue
        try:
            if src['tur'] == 'URUN':
                u = src['url'].replace('{isbn}', isbn) if '{isbn}' in src['url'] else src['url'] + ('&' if '?' in src['url'] else '?') + 'Barcode=' + isbn
                htm = _fetch_html(u, timeout)
                answered += 1
                r = parse_product_html(htm, isbn)
                if r is None:
                    try:
                        with open(_os.path.join(_tmp.gettempdir(), 'alfabe_kitap_urun_sayfasi.html'), 'w', encoding='utf-8') as fh:
                            fh.write(htm)
                    except Exception:
                        pass
                    errs.append(src['ad'] + ': sayfadan kitap bilgisi okunamadı')
                    last_error = ' | '.join(errs)
                    continue
                r['source'] = src['ad']
                last_status = 'bulundu'
                return r
            if src['tur'] == 'SRU':
                sep = '&' if '?' in src['url'] else '?'
                u = src['url'] + sep + urllib.parse.urlencode({'version': '1.1', 'operation': 'searchRetrieve', 'query': 'bath.isbn=' + isbn,
                                                              'maximumRecords': '1', 'recordSchema': 'marcxml'})
                r = parse_sru(_fetch_text(u, timeout))
            else:
                u = src['url'].replace('{isbn}', isbn) if '{isbn}' in src['url'] else src['url'] + ('&' if '?' in src['url'] else '?') + 'isbn=' + isbn
                r = parse_json_generic(_fetch(u, timeout))
            answered += 1
            if r:
                r['source'] = src['ad']
                last_status = 'bulundu'
                return r
        except Exception as ex:
            errs.append(src['ad'] + ': ' + _short(ex))
            last_error = ' | '.join(errs)
    if kitapi:
        steps = ((KITAPI_URL + '?isbn=' + urllib.parse.quote(isbn), parse_kitapi),) + tuple(steps)
    for url, parser in steps:
        try:
            r = parser(_fetch(url, timeout))
            answered += 1
        except Exception as ex:
            r = None
            errs.append(_name(url) + ': ' + _short(ex))
            last_error = ' | '.join(errs)
        if r:
            last_status = 'bulundu'
            return r
    last_status = 'yok' if answered else 'ag'
    return None
