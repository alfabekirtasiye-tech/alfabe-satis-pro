# -*- coding: utf-8 -*-
"""ALFABE LİSTE FİYATLA: müşterinin getirdiği okul/ihtiyaç listesini yapıştırın; satırlar ayrılır,
stoktaki en uygun ürünle eşleşir, toplam tutar hemen görünür. Seçtiğiniz eşleşmeler hatırlanır.
Stok ve satış verisine dokunmaz; yalnız kendi tablosuna (liste_eslesme) yazar."""
import math
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

_FOLD = str.maketrans({'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ç': 'c', 'ç': 'c', 'Ğ': 'g', 'ğ': 'g', 'Ö': 'o', 'ö': 'o', 'Ü': 'u', 'ü': 'u', 'â': 'a', 'Â': 'a', 'î': 'i', 'Î': 'i', 'û': 'u', 'Û': 'u', 'ê': 'e', 'ô': 'o', '’': "'", '‘': "'"})
STOP = set('bir iki uc ve ile olan olsun olmali olmalidir icin kullanacak kullanilacak kaliteli marka tane adet paket takim '
           'cocuk cocugunuzun sagligi ozellikle mutlaka lutfen tercihen gibi veya yada ya da en bu su de da '
           'olanlardan olanlar olani renkte renklerde olanlari'.split())
MODS = set('buyuk kucuk boy orta kalin ince renkli renk sade yumusak uclu kaliteli ten rengi'.split())          # ayırt edici olmayan niteleyiciler (düşük ağırlık)
NUMW = {'bir': 1, 'iki': 2, 'uc': 3, 'dort': 4, 'bes': 5, 'alti': 6, 'yedi': 7, 'sekiz': 8, 'dokuz': 9, 'on': 10}
UNIT = r'(?:adet\w*|ad\.?|tane\w*|paket|takim|kutu|cift|rulo|top)'

SCHEMA = "CREATE TABLE IF NOT EXISTS liste_eslesme(key TEXT PRIMARY KEY, stock_id INTEGER NOT NULL)"


def fold(x):
    return str(x if x is not None else '').translate(_FOLD).lower()


def _clean_line(s):
    s = s.strip()
    s = re.sub(r'^[\s•\-\*–—·●▪■□☐✓✔►>]+', '', s)
    s = re.sub(r'^\(?\d{1,2}\s*[\.\)]\s+(?=\D)', '', s)          # "1. " "2) " madde numarası
    return s.strip(' \t;:')


_QTY_START = r"(?:\d+|bir|iki|uc|üç)\s*(?:adet\w*|paket|takim|kutu|cift|rulo|top|tane\w*)\b"
_COL_WORDS = None


def _mask_parens(t):
    """Parantez İÇİNİ 'x' yapar (uzunluk korunur); kapanış ')' görünür kalır -> parantez dışı sınırlar bulunur."""
    out, d = [], 0
    for ch in t:
        if ch == '(':
            d += 1
            out.append('x')
        elif ch == ')' and d > 0:
            d -= 1
            out.append(')' if d == 0 else 'x')
        else:
            out.append('x' if d > 0 else ch)
    return ''.join(out)


def _is_color_word(w):
    f = fold(w).strip(".,;:")
    return any(f == c or (len(f) >= 4 and (f.startswith(c) or c.startswith(f)) and abs(len(f) - len(c)) <= 5) for c in COLORS) or f == 'ten'


def _split_commas0(seg):
    m = _mask_parens(seg)
    parts, last = [], 0
    for mm in re.finditer(',', m):
        parts.append(seg[last:mm.start()])
        last = mm.end()
    parts.append(seg[last:])
    return [x.strip() for x in parts if x.strip()]


def _color_list(seg):
    """'5 adet büyük boy kırmızı, mavi, sarı, pembe fon kartonu' -> her renk için ayrı satır. Uymazsa None."""
    parts = _split_commas0(seg)
    if len(parts) < 3:
        return None
    fw = parts[0].split()
    lw = parts[-1].split()
    if len(fw) < 2 or not _is_color_word(fw[-1]) or len(lw) < 2 or not _is_color_word(lw[0]):
        return None
    mids = parts[1:-1]
    if not all(len(x.split()) <= 2 and _is_color_word(x.split()[0]) for x in mids):
        return None
    k = 1
    if fold(lw[0]) == 'ten' and len(lw) > 1 and fold(lw[1]).startswith('reng'):
        k = 2
    noun = ' '.join(lw[k:])
    if not noun:
        return None
    pre = ' '.join(fw[:-1])
    cols = [fw[-1]] + [x for x in mids] + [' '.join(lw[:k])]
    return [(pre + ' ' + c.strip() + ' ' + noun).strip() for c in cols]


def split_runon(ln):
    """Tek satıra yapışmış uzun listeyi (PDF/WhatsApp'tan kopyalanan paragraf) ürünlere ayırır.
    Sınırlar (parantez dışında): ')' sonrası, iki+ boşluk/sekme, '.'/';' sonrası, 'N adet/paket' başlangıcı."""
    ln = ln.replace('\u00a0', ' ').replace('\t', '  ')
    if len(ln) < 70:
        return [ln]
    ln = re.sub(r"(\d+)\s*'\s*(?:er|ar|şer|ser)\b", r'\1', ln, flags=re.I)             # "5'er adet" -> "5 adet"
    ln = re.sub(r'\bpembe\s?renk\w*', 'pembe', ln, flags=re.I)
    m = _mask_parens(ln)
    cuts = set()
    # Belge 'N'li ürün' üslubundaysa (')' veya '.' sonrası N'li ile başlayan kalemler >= 2) N'li başlangıcı her yerde yeni kalemdir
    _pk = r"\d{1,3}'?(?:li|lı|lu|lü)\b"
    if len(re.findall(_pk + r'\s+(?:[^\s()]+\s+){0,4}[^\s()]*\(', ln, flags=re.I)) + len(re.findall(r'[\).]\s+' + _pk, m, flags=re.I)) >= 2:
        for mm in re.finditer(r'(?<=[^\s,(])\s+(?=' + _pk + r"\s+[^\W\d_]{2,}[^,]*?(?:\(|$|\s[A-ZÇĞİÖŞÜ]))", m, flags=re.I):
            if len(ln[:mm.start()].split()) >= 2 and re.match(_pk + r'\s+[^\W\d_]{2,}', ln[mm.end():], flags=re.I):
                cuts.add((mm.start(), mm.end()))
    for mm in re.finditer(r'[ ]+(?=[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,})', m):
        pw = ln[:mm.start()].split()[-1:] or ['']
        if pw[0].lower().endswith(('acak', 'ecek', 'aktır', 'ektir', 'ederiz', 'alıdır', 'elidir', 'ınız', 'iniz', 'olunur')):
            cuts.add((mm.start(), mm.end()))
    for pat in (r'(?<=\))[ ]+(?=[^\s,;.)])', r'[ ]{2,}', r'(?<=[^\W\d_][\.;])[ ]+(?=[A-ZÇĞİÖŞÜ0-9])',
                r'(?<=[^,\s]) (?=' + _QTY_START + r')', r',[ ]*(?=' + _QTY_START + r')'):
        for mm in re.finditer(pat, m, flags=re.I if pat.startswith('(?<=[^,') else 0):
            before = ln[:mm.start()].split()
            if pat.startswith('(?<=[^,') and len(before) < 2:
                continue
            cuts.add((mm.start(), mm.end()))
    segs, last = [], 0
    for a, b in sorted(cuts):
        if a >= last:
            segs.append(ln[last:a])
            last = b
    segs.append(ln[last:])
    segs = [x.strip(' .;') for x in segs if x.strip(' .;')]
    out = []
    for sg in segs:
        cl = _color_list(sg)
        if cl:
            out += cl
            continue
        parts = _split_commas0(sg)
        if len(parts) >= 2 and re.match(r'^\s*' + _QTY_START, fold(parts[0])) and all(len(x.split()) <= 2 and not re.search(r'\d', x) and not _is_color_word(x.split()[0]) for x in parts[1:]):
            out += parts                                             # "2 adet kurşun kalem, silgi, kalemtraş"
        else:
            out.append(sg)
    # sonda renk kalıp ismi eksik kalan satırlara bir sonraki satırın ismini ver: "2 adet siyah, 2 adet beyaz, 2 adet ten rengi fon kartonu"
    carry = ''
    for i in range(len(out) - 1, -1, -1):
        w = out[i].split()
        idx = [j for j, x in enumerate(w) if _is_color_word(x)]
        if idx:
            j = idx[-1] + (1 if fold(w[idx[-1]]) == 'ten' and idx[-1] + 1 < len(w) and fold(w[idx[-1] + 1]).startswith('reng') else 0)
            noun = ' '.join(w[j + 1:])
            if noun:
                carry = noun
            elif carry and re.match(r'^\s*' + _QTY_START, fold(out[i])):
                out[i] = out[i] + ' ' + carry
        else:
            carry = ''
    return out


def split_lines(text):
    """Metni ürün satırlarına böler (madde işaretleri, satır sonları, ')' sonrası virgül)."""
    t = str(text or '').replace('\r', '\n')
    t = re.sub(r'\s*[•●▪■☐]\s*', '\n', t)
    out = []
    for ln in t.split('\n'):
        ln = _clean_line(ln)
        if not ln:
            continue
        ln = re.sub(r'(?<![\w])[Il|!](?=\s+' + UNIT + r'\b)', '1', ln, flags=re.I)        # fotoğraf okumada "I adet" = "1 adet"
        qn = r'(?:\d+|bir|iki|uc|üç)\s*' + UNIT + r'\b'
        for piece in split_runon(ln):
            parts = re.split(r'(?<=\)),\s*(?=\S)|,\s*(?=' + qn + r')|\s+ve\s+(?=' + qn + r')|\s+ile\s+(?=' + qn + r')', piece, flags=re.I)
            out += [p.strip() for p in parts if p.strip()]
    return out


def is_header(line):
    f = fold(line)
    wn_ = re.sub(r'\([^)]*\)?', ' ', f).split()
    if len(wn_) <= 16 and wn_ and wn_[-1] == 'listesi' and re.search(r'ihtiyac|kirtasiye|okul|kurum|sinif', f) and not re.match(r'^\s*\d+\s*' + UNIT, f):
        return True                                   # 'ÖZEL ... KURUMLARI 5 YAŞ ANA SINIFI KIRTASİYE VE İHTİYAÇ LİSTESİ' gibi uzun başlık
    if len(wn_) >= 6 and re.search(r'rica ed|lutfen|gonder|ayrica|karismamasi|karismasin|yazilacaktir$|bulunsun|olunuz|edilmelidir|unutmayiniz', ' '.join(wn_)) and not re.match(r'^\s*\d+\s*' + UNIT, f):
        return True                                   # parantez dışında uzun cümle: açıklama/not, ürün değil
    if re.match(r'^\W*not\s*:', f):   # 'NOT: ...' genel açıklama satırı ürün değildir
        return True
    if re.search(r'\bliste(si|m)?\b', f) and re.search(r'ihtiyac|sinif|kirtasiye|malzeme', f) and len(f.split()) <= 8:
        return True
    return bool(re.fullmatch(r'[\W\d_]*(ihtiyac|malzeme|sinif)[^,]{0,25}', f)) and len(f.split()) <= 4 and not re.search(UNIT, f)


def parse_qty(line):
    """(adet, kalan_metin). '3 Adet X' -> 3; 'Bir adet X' -> 1; '12'li kuru boya' adet DEĞİL; '(1 adet)' sonda -> 1."""
    s = re.sub(r'^\s*[Il|!](?=\s+' + UNIT + r'\b)', '1', line.strip(), flags=re.I)
    f = fold(s)
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*' + UNIT + r'\b\s*(.*)$', f)
    if m:
        n = float(m.group(1).replace(',', '.'))
        return (n if n > 0 else 1.0), s[len(s) - len(m.group(2)):] if m.group(2) else ''
    m = re.match(r'^(' + '|'.join(NUMW) + r')\s+' + UNIT + r'\b\s*(.*)$', f)
    if m:
        return float(NUMW[m.group(1)]), s[len(s) - len(m.group(2)):] if m.group(2) else ''
    m = re.search(r'\((\d+)\s*' + UNIT + r'\)\s*$', f)
    if m:
        return float(m.group(1)), s
    return 1.0, s


def _norm_tok(t):
    """Ünsüz yumuşaması farkını eşitler: bandı = bantı, kağıdı = kağıtı, belirli = ... (nd/ld/rd -> nt/lt/rt)."""
    return re.sub(r'([nlr])d', r'\1t', t) if t.isalpha() else t


def query_tokens(text, norm=True):
    text = re.sub(r'\(?\s*(?:2|iki)\s*b[öo]l[üu]ml[üu]\s*\)?', ' çift ', str(text), flags=re.I)     # '(2 Bölümlü)' = çift (fermuarlı kalem kutu)
    t = re.sub(r'\([^)]*\)?', ' ', text)                 # parantez içi açıklamaları atla
    f = fold(t)
    f = re.sub(r'([a-z]{3,})(a[3-5])\b', r'\1 \2', f)         # 'beyazA4' -> 'beyaz A4' (fotoğraf okuma bitişik yazar)
    f = _pack_norm(f)                                     # 12'li -> 12li
    toks = [x for x in re.findall(r'[a-z0-9]+', f) if x not in STOP and len(x) > 1 and not x.startswith(('adet', 'tane'))]
    return [_norm_tok(x) for x in toks] if norm else toks


def key_of(text):
    return ' '.join(sorted(set(query_tokens(text, norm=False))))


COLORS = ['kirmizi', 'mavi', 'siyah', 'yesil', 'sari', 'turuncu', 'mor', 'pembe', 'kahverengi', 'beyaz', 'lacivert', 'gri']
_COLOR_DISPLAY = {'kirmizi': 'Kırmızı', 'mavi': 'Mavi', 'siyah': 'Siyah', 'yesil': 'Yeşil', 'sari': 'Sarı', 'turuncu': 'Turuncu', 'mor': 'Mor',
                  'pembe': 'Pembe', 'kahverengi': 'Kahverengi', 'beyaz': 'Beyaz', 'lacivert': 'Lacivert', 'gri': 'Gri'}


def paren_hints(rest):
    """Parantez içindeki marka/seçenek listesi ('pritt, adel, faber castell') -> tercih edilecek sözcükler."""
    hints = []
    for g in re.findall(r'\(([^)]*)\)?', rest):
        if re.search(r'\d', g):
            continue
        items = [x.strip() for x in re.split(r',|\bve\b|/', g) if x.strip()]
        if (len(items) >= 2 and all(len(x.split()) <= 2 for x in items)) or (items and len(g.split()) <= 4):     # '(çıtçıtlı)', '(metal, küt uçlu)', '(iki gözlü)': ayırt edici nitelik
            for x in items:
                hints += [t for t in re.findall(r'[a-z0-9]+', fold(x)) if len(t) >= 3 and t not in STOP]
    return hints


def _expand(q, ln, rest):
    """Bir satırı gerekirse birden çok ürüne böler:
    - '(Kırmızı, Mavi ve Siyah renkte)' gibi renk sıralaması -> her renk ayrı satır (adet renk sayısına eşitse 1'er adet)
    - 'Fasulye, sayma çubuğu' gibi açıklamasız virgüllü kısa liste -> her biri ayrı ürün"""
    m = re.search(r'\(([^)]*)\)', rest)
    if m:
        inner = fold(m.group(1))
        cols = [c for c in COLORS if re.search(r'\b' + c + r'\b', inner)]
        if len(cols) >= 2:
            base = re.sub(r'\([^)]*\)', ' ', rest).strip()
            each = 1.0 if int(q) == len(cols) and q == int(q) else q
            return [(each, ln + ' — ' + _COLOR_DISPLAY[c], base + ' ' + _COLOR_DISPLAY[c]) for c in cols]
    plain = re.sub(r'\([^)]*\)?', ' ', rest)
    tail = ''
    mt = re.search(r"\s(\d+\s*'?\s*(?:li|lu|lü|lı|lik|luk|gr|mm|cm|mt|ml))\s*$", plain, flags=re.I) if re.search(r'\sve\s', plain, flags=re.I) else None
    plain_nt = plain[:mt.start()] if mt else plain
    if mt:
        tail = ' ' + mt.group(1).strip()                     # "Karton Bardak ve Tabak 100'lü" -> ortak paket boyu ikisine de
    m2 = re.fullmatch(r'\s*(.+?)\s+ve\s+(\S+(?:\s+\S+){0,2})\s*', plain_nt, flags=re.I)
    if m2 and ':' not in rest and not re.search(r'\d', m2.group(2)) and not (fold(m2.group(1)).split()[-1] in COLORS and fold(m2.group(2)).split()[0] in COLORS):
        a_, b_ = m2.group(1).split(), m2.group(2).split()
        first = ' '.join(a_)
        if len(b_) <= 2:
            second = ' '.join(a_[:-len(b_)] + b_) if len(a_) >= 3 and not re.search(r'\d', m2.group(1)) else ' '.join(b_)      # "şeffaf plastik kaşık ve çatal" -> "şeffaf plastik çatal"
        else:
            second = ' '.join(b_)                                                         # "Tam Tabaka ve Orta Boy Mukavva"
            if len(a_) <= 2 and fold(b_[-1]) not in [fold(x) for x in a_]:
                first = ' '.join(a_ + [b_[-1]])                                           # -> "Tam Tabaka Mukavva" + "Orta Boy Mukavva"
        return [(q, ln + ' — ' + first, first + tail), (q, ln + ' — ' + second, second + tail)]
    if ',' in plain and ':' not in rest and not re.search(r'\d', plain):
        parts = [x.strip() for x in plain.split(',') if x.strip()]
        words = sum(len(x.split()) for x in parts)
        if len(parts) >= 2 and words <= 6 and all(len(x.split()) <= 3 for x in parts):
            return [(q, x, x) for x in parts]
    return [(q, ln, rest)]


_QTY = r"(\d{1,3})\s*(?:'?\s*(?:er|ar|ser|şer))?\s*(adet|paket|takim|kutu|top|rulo|cift|tane)?"
_QTY_LINE = re.compile(r'^\s*(?:' + _QTY + r'|(adet|paket|takim|kutu|top|rulo|cift|tane))\s*\.?\s*$')
_NUM_LINE = re.compile(r'^\s*\d{1,3}\s*[\.\)]?\s*$')
_SECT_LINE = re.compile(r'^(kirtasiye|kisisel|okul|ogrenci)\b.{0,25}\bmalzeme(ler|leri|ler listesi)?\b.{0,10}$')
LAST_NOTES = []


def _qty_text(line):
    """'3'er Adet' -> '3 adet'; '1' -> '1 adet'; 'Adet' -> '1 adet'; '1 Paket' -> '1 paket'."""
    f = fold(line).strip()
    m = _QTY_LINE.match(f)
    if not m:
        return None
    n = m.group(1) or '1'
    u = m.group(2) or m.group(3) or 'adet'
    u = {'takim': 'takım', 'cift': 'çift'}.get(u, u)
    return f"{int(n)} {u}"


def normalize_table(text):
    """Tablodan kopyalanan listeyi düzgün 'adet ürün' satırlarına çevirir. İki durum:
    1) SÜTUN SÜTUN yapışmış (PDF/fotoğraf tablosu): önce sıra numaraları, sonra ürün adları, sonda adetler;
    2) SATIR SATIR ama '12 Ürün adı 3 Adet' (başta sıra numarası, sonda adet).
    Tanımazsa metne dokunmaz. (yeni_metin, notlar) döner."""
    raw = str(text or '').replace('\r', '\n')
    lines = [x.strip() for x in raw.split('\n') if x.strip()]
    notes = []
    nb = sum(1 for x in lines if _NUM_LINE.match(x))
    if nb >= 4 and nb >= 0.8 * len(lines):
        notes.append("Yapıştırılan metinde yalnızca numaralar var, ürün adı yok. Tabloda ürün adı sütununu da seçip kopyalayın (numaralar ürün sayılmadı).")
        return '', notes
    if len(lines) < 6:
        return text, notes
    # --- 1) sütun sütun
    a = 0
    while a < len(lines) and _NUM_LINE.match(lines[a]):
        a += 1
    c = len(lines)
    while c > a and _qty_text(lines[c - 1]) is not None:
        c -= 1
    qtys = lines[c:]
    if len(qtys) >= 4 and (a >= 4 or a == 0) and c - a >= 4:
        mid = lines[a:c]
        # sütun başlığı ("Adet") tek başına en başta ise at
        if qtys and fold(qtys[0]) in ('adet', 'miktar') and len(qtys) > 1:
            q2 = qtys[1:]
        else:
            q2 = qtys
        items = [x for x in mid if not _SECT_LINE.match(fold(x))]
        if mid and fold(mid[-1]) in ('adet', 'miktar'):
            items = [x for x in items if x is not mid[-1]]

        joined = []
        for x in items:
            if joined and x[:1].islower():                 # bölünmüş hücre: küçük harfle başlayan satır öncekine eklenir
                joined[-1] += ' ' + x
            else:
                joined.append(x)
        got, used = None, q2
        for its in (items, joined):
            if len(its) == len(q2):
                got = its
                break
        if got is not None:
            out = [f"{_qty_text(q)} {it}" for it, q in zip(got, used)]
            notes.append(f"Sütun sütun yapışan liste satırlara çevrildi ({len(out)} ürün).")
            return '\n'.join(out), notes
        notes.append(f"Sütunlar eşleşmedi ({len(items)} ürün, {len(q2)} adet); sıra numaraları atıldı, adetler 1 sayıldı — lütfen kontrol edin.")
        return '\n'.join(items), notes
    # --- 2) satır satır: başta sıra numarası (artan), sonda adet
    rowre = re.compile(r'^(\d{1,3})\s*[\.\)]?\s+(?!' + UNIT + r'\b)(.+)$', re.I)
    cand = []
    for ln in lines:
        m = rowre.match(ln)
        cand.append((int(m.group(1)), m.group(2).strip()) if m else None)
    ok = [x for x in cand if x]
    if len(ok) >= 5 and len(ok) >= 0.7 * len(lines):
        up = sum(1 for k in range(1, len(ok)) if ok[k][0] > ok[k - 1][0])
        if up >= 0.7 * (len(ok) - 1):
            out = []
            for ln, x in zip(lines, cand):
                if not x:
                    out.append(ln)
                    continue
                body = x[1]
                m = re.search(r'\s' + _QTY + r'\s*$', fold(body))
                q, u = '1', 'adet'
                if m and len(body) - len(fold(body)[m.start():]) >= 3 and len(body[:len(body) - len(fold(body)[m.start():])].split()) >= 2:
                    cut = len(fold(body)) - len(fold(body)[m.start():])
                    qt = _qty_text(body[cut:].strip())
                    if qt:
                        q, u = qt.split()
                        body = body[:cut].strip()
                out.append(f"{q} {u} {body}")
            notes.append(f"Tablo satırlarındaki sıra numaraları atıldı, adetler sondan okundu ({len(ok)} satır).")
            return '\n'.join(out), notes
    return text, notes


def parse_list(text):
    """[(adet, ham_satir, sorgu_metni)] — başlık satırları atlanır; renk/virgül listeleri ayrı satırlara bölünür."""
    text = str(text or '').replace('\u2019', "'").replace('\u2018', "'").replace('\u00b4', "'")      # kıvrık tırnak (100’lü) -> düz
    text, _n = normalize_table(text)
    LAST_NOTES[:] = _n
    rows = []
    for ln in split_lines(text):
        if is_header(ln):
            continue
        if _NUM_LINE.match(ln):                             # yalnızca numara olan satır ürün değildir
            continue
        q, rest = parse_qty(ln)
        if not query_tokens(rest):
            continue
        rows += _expand(q, ln, rest)
    return rows


import functools


def _lev(a, b, cap):
    """Düzenleme uzaklığı (cap'i aşınca cap+1 döner)."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


@functools.lru_cache(maxsize=400000)
def _tok_match(q, n):
    if q == n:
        return 1.0
    if len(q) >= 3 and len(n) >= 3 and (n.startswith(q) or q.startswith(n)) and min(len(q), len(n)) >= max(len(q), len(n)) - (3 if min(len(q), len(n)) >= 6 else 2):
        k_ = min(len(q), len(n))
        if (q if len(q) > len(n) else n)[k_:] in ('lik', 'luk', 'lık', 'lük', 'ci', 'cı', 'cu', 'cü', 'lari', 'leri'):
            return 0.0                                # türetme eki: 'kalem' ile 'kalemlik' (kalemi/kalemlik de) farklı üründür
        return 0.9                                    # çekim eki farkı (kalem/kalemi)
    if q.isalpha() and n.isalpha() and len(q) >= 7 and len(n) >= 7 and abs(len(q) - len(n)) <= 2:
        lim = 1 if min(len(q), len(n)) < 8 else 2
        if _lev(q, n, lim) <= lim:                # tek/çift harf yazım hatası veya eksik/fazla harf ('kalemtıraş' = 'kalemtraş')
            return 0.8
    return 0.0


def load_stock(c):
    try:
        loc = {r['id']: ' '.join(x for x in (r['section'], r['shelf']) if x) for r in c.execute("SELECT id,section,shelf FROM stock WHERE active=1").fetchall()}
    except Exception:
        loc = {}
    try:                                   # satış sıklığı: aynı sözcüklere uyan ürünler arasında dükkânın gerçekten sattığı tercih edilir
        pop = {r[0]: float(r[1] or 0) for r in c.execute("SELECT l.stock_id, SUM(l.qty) FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='FINAL' AND s.dt>=date('now','-400 day') GROUP BY l.stock_id").fetchall()}
    except Exception:
        pop = {}
    return [dict(loc=loc.get(r['id'], ''), pop=pop.get(r['id'], 0.0), **dict(id=r['id'], name=r['name'] or '', grp=r['grp'] or '', brand=r['brand'] or '', retail=float(r['retail'] or 0), qty=float(r['qty'] or 0),
                 p_retail=float(r['retail'] or 0), p_wh=float(r['wholesale'] or 0), p_sp=float(r['special_price'] or 0)))
            for r in c.execute("SELECT id,name,grp,brand,retail,wholesale,special_price,qty FROM stock WHERE active=1").fetchall()]


def apply_price_kind(stock, kind):
    """Aktif fiyatı ('retail' alanı) seçilen türe çevirir: 'perakende' | 'toptan' | 'ozel'. Fiyat 0 ise perakendeye döner."""
    for s in stock:
        v = {'toptan': s['p_wh'], 'ozel': s['p_sp']}.get(kind, s['p_retail'])
        s['retail'] = v if v > 0 else s['p_retail']


def _pack_norm(f):
    """'12 Lİ', "12'li", '100 LU', '100LÜK' -> '12li' (paket adedi yazımı listede ve stokta aynı olsun)."""
    return re.sub(r"(\d+)\s*'?\s*(li|lu|lik|luk)\b", r'\1li', f)


def _prep(stock):
    for s in stock:
        if '_nt' not in s:
            s['_nt'] = [_norm_tok(x) for x in re.findall(r'[a-z0-9]+', _pack_norm(fold(s['name'] + ' ' + s['brand'])))]
            s['_gt'] = [_norm_tok(x) for x in re.findall(r'[a-z0-9]+', fold(s['grp']))]
    return stock


_IDXC = {}


def _index(stock):
    """Ürün adı/grup sözcükleri için ters dizin (bir kez kurulur). Arama yalnız ilgili ürünleri puanlar."""
    key = (id(stock), len(stock), id(stock[0]) if stock else 0)
    if _IDXC.get('key') == key:
        return _IDXC['idx']
    _prep(stock)
    vocab, pref, gvocab, gpref = {}, {}, {}, {}
    for i, s_ in enumerate(stock):
        nt = s_['_nt']
        toks = set(nt) | {nt[k] + nt[k + 1] for k in range(len(nt) - 1)}
        for t in toks:
            vocab.setdefault(t, []).append(i)
        for t in set(s_['_gt']):
            gvocab.setdefault(t, []).append(i)
    for t in vocab:
        if len(t) >= 3:
            pref.setdefault(t[:3], []).append(t)
    for t in gvocab:
        if len(t) >= 3:
            gpref.setdefault(t[:3], []).append(t)
    idx = dict(vocab=vocab, pref=pref, gvocab=gvocab, gpref=gpref)
    _IDXC.clear()
    _IDXC.update(key=key, idx=idx)
    return idx


def _positions(stock, qt):
    ix = _index(stock)
    out = set()
    for q in qt:
        for voc, pre in ((ix['vocab'], ix['pref']), (ix['gvocab'], ix['gpref'])):
            toks = {q} if q in voc else set()
            if len(q) >= 3:
                toks.update(pre.get(q[:3], ()))
            for t in toks:
                out.update(voc[t])
    return sorted(out)


# Liste dili -> dükkân/stok dili. (öbek, gereken bağlam sözcüğü veya None, yerine geçen sözcükler)
SYN = [
    (('buyuk', 'boy'), {'fon'}, ['50', '70']), (('orta', 'boy'), {'fon'}, ['35', '50']), (('kucuk', 'boy'), {'fon'}, ['25', '35']),     # fon kartonu ebatları
    (('jumbo',), {'kursun'}, []),          # 'jumbo kurşun kalem' stokta ayrı yok: kurşun kaleme düşer (kontrol olarak)
    (('simsiz',), None, ['duz']),
    (('a4', 'kagidi'), None, ['a4', 'fotokopi', 'kagidi']),
    (('kagidi', 'a4'), None, ['fotokopi', 'kagidi', 'a4']),
    (('buyuk', 'boy'), {'kuru', 'mum', 'pastel', 'keceli', 'boya'}, ['jumbo']),
    (('boy', 'kuru'), None, ['jumbo', 'kuru']),
    (('yuvarlak', 'uclu'), None, ['kut', 'uclu']),
    (('yuvarlak', 'uclu', 'makas'), None, ['anaokulu', 'makasi']),
    (('tam', 'tabaka'), {'mukavva', 'karton'}, ['70', '100']),
    (('orta', 'boy'), {'mukavva', 'karton'}, ['50', '70']),
    (('kucuk', 'boy'), {'mukavva', 'karton'}, ['35', '50']),
]


def _variants(qt, voc):
    """Sorgu sözcüklerinin eşdeğer yazımları: özgün; bitişik yazım ('el işi'='elişi'); liste/stok dili eşanlamlıları."""
    out = [qt]
    k_, mer = 0, []
    while k_ < len(qt):
        if k_ + 1 < len(qt) and len(qt[k_] + qt[k_ + 1]) >= 5 and qt[k_] + qt[k_ + 1] in voc and qt[k_] not in MODS and qt[k_ + 1] not in MODS:
            mer.append(qt[k_] + qt[k_ + 1])
            k_ += 2
        else:
            mer.append(qt[k_])
            k_ += 1
    if mer != qt:
        out.append(mer)
    for pat, ctx, rep_ in SYN:
        n = len(pat)
        for base in list(out):
            for i in range(len(base) - n + 1):
                if tuple(base[i:i + n]) == pat and (ctx is None or ctx & set(base)):
                    v = base[:i] + [_norm_tok(x) for x in rep_] + base[i + n:]
                    if v not in out:
                        out.append(v)
                    break
    return out


def candidates_ex(stock, query, limit=None, hints=()):
    """[(skor, kapsama, stok)] en iyiden başlayarak. kapsama = sorgu sözcüklerinin (ağırlıklı) ne kadarı ürün adında var (0..1).
    Skor = kapsama - fazla sözcük cezası + stok/marka ipucu bonusu."""
    qt0 = query_tokens(query)
    if not qt0:
        return []
    _prep(stock)
    voc_ = _index(stock)['vocab'] if stock else {}
    hs = [_norm_tok(h) for h in hints]
    best_by = {}
    pref_ = _index(stock)['pref'] if stock else {}

    def known(q):                              # stokta hiçbir ürün adında karşılığı olmayan sözcük (marka/yabancı sözcük) -> düşük ağırlık
        if q in voc_:
            return True
        return any(_tok_match(q, t) > 0 for t in pref_.get(q[:3], ()))
    for qt in _variants(qt0, voc_):
        if len(qt) >= 3:
            qt = [q for q in qt if not (q.isalpha() and len(q) <= 2)] or qt      # 'my', 've' gibi 1-2 harfli sözcükler ayırt edici değildir
        wts = [0.4 if q in MODS else (1.3 if len(q) >= 7 else 1.0) for q in qt]
        wts = [w_ if known(q) else 0.25 for q, w_ in zip(qt, wts)]
        if len(qt) >= 3:
            wts = [min(w_, 0.5) if q in COLORS else w_ for q, w_ in zip(qt, wts)]       # renk sözcüğü ürün türünü ezmesin ('kahverengi fon kartonu' != kahverengi akrilik boya)
        if len(stock) >= 500:                  # ayırt edici (az ürün adında geçen) sözcük, çok ürün adında geçen sözcükten ağır basar
            lg_ = math.log(len(stock))
            wts = [w_ * (0.65 + 0.7 * min(1.0, math.log(len(stock) / max(1, len(voc_.get(q, ())))) / lg_)) if q in voc_ and q not in MODS else w_ for q, w_ in zip(qt, wts)]
        wsum = sum(wts)
        alpha = [q for q in qt if q.isalpha() and len(q) >= 3 and q not in MODS and q not in COLORS and known(q)] or [q for q in qt if q.isalpha() and len(q) >= 3 and q not in MODS and known(q)]
        rare = None                            # listedeki en ayırt edici sözcük ('çıtçıtlı', 'lastikli'): ürün adında yoksa eşleşme zayıflar
        _cd = [(len(voc_[q]), q) for q in alpha if len(q) >= 4 and q in voc_ and len(voc_[q]) >= 3]
        if len(alpha) >= 2 and _cd:
            rare = min(_cd)[1]
        for pos in _positions(stock, qt):
            s = stock[pos]
            tot = 0.0
            for q, w_ in zip(qt, wts):
                best = max([_tok_match(q, n) for n in s['_nt']] or [0.0])
                if best == 0.0:   # 'kalemtıraş' = 'KALEM TIRAŞ' (bitişik/ayrı yazım)
                    best = max([_tok_match(q, s['_nt'][k] + s['_nt'][k + 1]) for k in range(len(s['_nt']) - 1)] or [0.0])
                if best == 0.0:
                    best = 0.5 * max([_tok_match(q, g) for g in s['_gt']] or [0.0])
                tot += best * w_
            if tot <= 0:
                continue
            cov = tot / wsum
            if alpha and not any(_tok_match(q, n) >= 0.8 for q in alpha for n in s['_nt']):
                continue                              # yalnızca sayı/paket ('100 LU') benzerliği ile eşleşme olmaz
            extra = max(max(0, len(s['_nt']) - len(qt)), sum(1 for n in s['_nt'] if not any(_tok_match(q, n) > 0 for q in qt)))   # sorguda karşılığı olmayan ürün sözcükleri
            sc = cov - min(0.15, 0.02 * extra) + (0.005 if s['qty'] > 0 else 0)
            nmiss = sum(1 for q in qt if sum(c.isdigit() for c in q) >= 2 and not any(_tok_match(q, n) > 0 for n in s['_nt']))
            if nmiss:
                sc -= min(0.16, 0.08 * nmiss)          # listedeki ölçü/paket sayısı (100'lü, 50*70) üründe yoksa farklı ürün olabilir
            if cov >= 0.97 and extra == 0:
                sc += 0.08                             # ürün adı listedeki yazımla birebir aynı: popülerlik/stok farkı bunu aşamaz
            if rare and not any(_tok_match(rare, n) >= 0.8 for n in s['_nt']):
                sc -= 0.07 if rare in ('jumbo', 'mini', 'maxi', 'super', 'buyukboy') else 0.2
            pp_ = s.get('pop') or 0
            if pp_ > 0 and cov >= 0.6:               # çok satılan ürün, eşit kapsamadaki az satılana tercih edilir
                sc += 0.05 * min(1.0, math.log1p(pp_) / math.log1p(50))
            if hs and any(_tok_match(h, n) >= 0.9 for h in hs for n in s['_nt']):
                sc += 0.25
            old = best_by.get(pos)
            if old is None or sc > old[0]:
                best_by[pos] = (sc, cov, s)
    res = list(best_by.values())
    res.sort(key=lambda x: (-x[0], len(x[2]['name']), x[2]['name']))
    return res[:limit]


def candidates(stock, query, limit=8, hints=()):
    """[(skor, stok)] en iyiden başlayarak."""
    return [(sc, s) for sc, cov, s in candidates_ex(stock, query, limit, hints)]


def make_pool(ex):
    """Fiyat kademeleri için havuz: aramaya en uyan ürünlerle AYNI STOK GRUBUNDAKİ, sözcüklerinin büyük kısmını taşıyan tüm ürünler
    (kısa/uzun ad fark etmez; ör. ADEL KOLİ BANTI'nın tüm boy/fiyatları). Fiyatı olanlar; stokta olan varsa yalnız onlar. Fiyata göre sıralı."""
    if not ex:
        return []
    topcov = max(x[1] for x in ex)
    groups = {x[2]['grp'] for x in ex[:3]}
    floor = max(0.5, topcov - 0.40)
    pool = [x[2] for x in ex if x[1] >= floor and x[2]['grp'] in groups and x[2]['retail'] > 0]
    ins = [z for z in pool if z['qty'] > 0]
    # Stok sayımı güvenilmez olabilir (eksi stok): aramaya en uyan ürün stoğu olmasa da havuzda kalır.
    top_ids = {x[2]['id'] for x in ex if x[0] >= ex[0][0] - 1e-9 and x[2]['retail'] > 0}
    keep_ = [z for z in pool if z['qty'] > 0 or z['id'] in top_ids]
    return sorted(ins and keep_ or pool, key=lambda z: (z['retail'], z['name']))


def ensure_schema(c):
    c.execute(SCHEMA)
    c.commit()


def remembered(c, query):
    ensure_schema(c)
    k = key_of(query)
    if not k:
        return None
    r = c.execute("SELECT s.id FROM liste_eslesme m JOIN stock s ON s.id=m.stock_id WHERE m.key=? AND s.active=1", (k,)).fetchone()
    return r['id'] if r else None


def remember(c, query, stock_id):
    ensure_schema(c)
    k = key_of(query)
    if not k:
        return
    if stock_id is None:
        c.execute("DELETE FROM liste_eslesme WHERE key=?", (k,))
    else:
        c.execute("INSERT INTO liste_eslesme(key,stock_id) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET stock_id=excluded.stock_id", (k, int(stock_id)))
    c.commit()


_OCR_CONF = (('n', 'ri'), ('ri', 'n'), ('m', 'rn'), ('rn', 'm'), ('i', 'l'), ('l', 'i'), ('0', 'o'), ('1', 'l'), ('1', 'i'), ('5', 's'), ('c', 'e'), ('e', 'c'))


def ocr_fix_query(stock, query):
    """OCR'ın sık karıştırdığı harfleri ('ri'='n', 'rn'='m', 'i'='l'...) deneyerek stokta bulunmayan sözcüğü stokta bulunan sözcüğe çevirir.
    ('Pntt' -> 'pritt'). Yalnız stok sözcük dizininde OLAN tek harf-grubu değişimleri kabul edilir. (düzeltilmiş sorgu, [(eski, yeni)]) döner; düzeltme yoksa (None, [])."""
    toks = query_tokens(query)
    if not toks or not stock:
        return None, []
    voc = _index(stock)['vocab']
    out, fixes = [], []
    for t in toks:
        if t in voc or len(t) < 3 or not t.isalpha() or t in MODS or t in COLORS:
            out.append(t)
            continue
        best = None
        for a_, b_ in _OCR_CONF:
            i = t.find(a_)
            while i >= 0:
                v = t[:i] + b_ + t[i + len(a_):]
                if v in voc and (best is None or len(voc[v]) > len(voc[best])):
                    best = v
                i = t.find(a_, i + 1)
        if best:
            out.append(best)
            fixes.append((t, best))
        else:
            out.append(t)
    return (' '.join(out), fixes) if fixes else (None, [])


def match_all(c, text, stock=None, progress=None, ocr_text=None):
    """Satır sözlükleri: adet, ham, sorgu, stok (veya None), durum ('hatırlanan'|'kesin'|'kontrol'|'yok'), adaylar."""
    stock = stock if stock is not None else load_stock(c)
    byid = {s['id']: s for s in stock}
    out = []
    parsed = parse_list(text)
    for n_, (q, raw, rest) in enumerate(parsed, 1):
        if progress:
            progress(n_ - 1, len(parsed))
        ocr_note = None
        ex = candidates_ex(stock, rest, None, paren_hints(rest))
        cands = [(x[0], x[2]) for x in ex[:40]]
        pool = make_pool(ex)
        rid = remembered(c, rest)
        if rid in byid:
            st, status = byid[rid], 'hatırlanan'
        elif cands and cands[0][0] >= 0.75 and (len(cands) < 2 or cands[0][0] - cands[1][0] >= 0.08 or ex[0][1] >= 0.97):     # listedeki tüm sözcükler üründe varsa (fark yalnız marka/seçenek) eşleşme kesindir
            st, status = cands[0][1], 'kesin'
        elif cands and cands[0][0] >= 0.45:
            st, status = cands[0][1], 'kontrol'
        else:
            st, status = None, 'yok'
            if ocr_text and fold(raw.split(' — ')[0]) in ocr_text:          # fotoğraftan okunan satırda OCR harf karışıklığı: bulunamayan sözcüğü dene
                fq, fx = ocr_fix_query(stock, rest)
                if fq:
                    ex2 = candidates_ex(stock, fq, None, paren_hints(fq))
                    c2 = [(x[0], x[2]) for x in ex2[:40]]
                    if c2 and c2[0][0] >= 0.45:
                        ex, cands, pool = ex2, c2, make_pool(ex2)
                        st, status = c2[0][1], 'kontrol'                    # tahmindir: hiçbir zaman 'kesin' olmaz, sarı kalır
                        ocr_note = fx
        if st is not None and status != 'hatırlanan' and st['retail'] <= 0:          # fiyatı 0 olan ürün otomatik seçilmez
            alt = [x for x in cands if x[1]['retail'] > 0 and x[0] >= cands[0][0] - 0.12]
            # fiyatlı yakın ürün varsa o; yoksa ürün 'yok' denmez, fiyatı 0 olarak SARI (kontrol) kalır ki satıcı fiyatı yazsın
            st, status = (alt[0][1], 'kontrol') if alt else (st, 'kontrol')
        out.append(dict(qty=q, raw=raw, query=rest, stock=st, status=status, cands=[x[1] for x in cands], pool=pool, best=st, status0=status, ocr_fix=ocr_note))
    return out


def unit(row, s):
    """Satırın birim fiyatı: elle değiştirildiyse o, yoksa ürünün aktif fiyatı."""
    return row['unit'] if row.get('unit') is not None else s['retail']


def tier_pick(row, tier):
    """tier: 'eslesen' (en iyi/hatırlanan eşleşme), 'uygun' (en ucuz), 'orta', 'pahali' (en pahalı).
    Elle seçilmiş satır ('seçildi') hiçbir kademede değişmez."""
    if row.get('status') == 'seçildi':
        return row.get('stock')
    pool = row.get('pool') or []
    if tier == 'eslesen' or not pool:
        return row.get('best')
    if tier == 'uygun':
        return pool[0]
    if tier == 'pahali':
        return pool[-1]
    return pool[len(pool) // 2] if len(pool) >= 3 else pool[0]


def tier_total(rows, tier):
    t = 0.0
    for r in rows:
        s = tier_pick(r, tier)
        if s:
            t += r['qty'] * unit(r, s)
    return round(t, 2)


def run_with_progress(parent, title, work, on_done, min_seconds=0.0, sync=False):
    """İlerleme penceresi: sayaç (geçen süre) + adım yazısı + çubuk. work(report) arka planda çalışır; report(metin, oran=None).
    Bitince on_done(sonuç, hata) ana iş parçacığında çağrılır. sync=True: pencere/iş parçacığı olmadan hemen çalışır (test)."""
    import threading
    import time
    box = {'text': 'Hazırlanıyor…', 'frac': None, 'res': None, 'err': None, 'done': False}

    def report(text, frac=None):
        box['text'] = text
        box['frac'] = frac

    def runner():
        try:
            box['res'] = work(report)
        except Exception as ex:
            box['err'] = ex
        box['done'] = True
    if sync:
        runner()
        return on_done(box['res'], box['err'])
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry('460x190')
    win.resizable(False, False)
    try:
        win.transient(parent)
        win.protocol('WM_DELETE_WINDOW', lambda: None)
    except Exception:
        pass
    try:
        win.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - 460) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - 190) // 3)
        win.geometry('460x190+%d+%d' % (x, y))
    except Exception:
        pass
    tk.Label(win, text=title, font=('Segoe UI', 12, 'bold')).pack(pady=(14, 4))
    lt = tk.Label(win, text=box['text'], font=('Segoe UI', 10), wraplength=420)
    lt.pack()
    pb = ttk.Progressbar(win, mode='indeterminate', length=400)
    pb.pack(pady=8)
    pb.start(12)
    lc = tk.Label(win, text='⏱ 0.0 sn', font=('Segoe UI', 16, 'bold'), fg='#0b6b3a')
    lc.pack()
    try:
        win.grab_set()
        win.lift()
        win.focus_force()
    except Exception:
        pass
    t0 = time.time()
    state = {'det': False}
    threading.Thread(target=runner, daemon=True).start()

    def poll():
        try:
            el = time.time() - t0
            lt.config(text=box['text'])
            lc.config(text='⏱ %.1f sn' % el)
            f = box['frac']
            if f is not None:
                if not state['det']:
                    pb.stop()
                    pb.config(mode='determinate', maximum=100)
                    state['det'] = True
                pb['value'] = max(0, min(100, f * 100))
            if box['done'] and el >= min_seconds:
                try:
                    pb.stop()
                    win.grab_release()
                except Exception:
                    pass
                win.destroy()
                return on_done(box['res'], box['err'])
        except tk.TclError:
            return
        win.after(80, poll)
    win.after(80, poll)


def total(rows):
    return round(sum(r['qty'] * unit(r, r['stock']) for r in rows if r.get('stock')), 2)


TIER_AD = {'uygun': 'UYGUN', 'orta': 'ORTA', 'pahali': 'PAHALI / MARKALI', 'eslesen': 'LİSTE'}

DISC_MODES = (('yok', 'İskonto yok'), ('yuzde', 'Yüzde (%)'), ('tutar', 'Tutar indirimi (₺)'),
              ('r1', 'Aşağı yuvarla: tam ₺'), ('r5', 'Aşağı yuvarla: 5 ₺'), ('r10', 'Aşağı yuvarla: 10 ₺'))


def apply_discount(sub, disc):
    """(iskonto_tutarı, ödenecek). disc = {'mode': yok|yuzde|tutar|r1|r5|r10, 'val': sayı}."""
    import math
    mode = (disc or {}).get('mode', 'yok')
    try:
        v = float((disc or {}).get('val') or 0)
    except (TypeError, ValueError):
        v = 0.0
    if mode == 'yuzde':
        d = sub * min(max(v, 0.0), 100.0) / 100.0
    elif mode == 'tutar':
        d = min(max(v, 0.0), sub)
    elif mode in ('r1', 'r5', 'r10'):
        step = {'r1': 1, 'r5': 5, 'r10': 10}[mode]
        d = sub - math.floor(round(sub, 2) / step) * step
    else:
        d = 0.0
    d = round(d, 2)
    return d, round(sub - d, 2)


def short_of(r):
    """İstenen adet stoktan fazlaysa eksik miktar (yoksa 0)."""
    s_ = r.get('stock')
    if not s_:
        return 0.0
    return max(0.0, round(float(r['qty']) - float(s_.get('qty') or 0), 4))


def step_row(row, delta):
    """Satırdaki ürünü aynı türün bir sonraki ucuz(-1)/pahalı(+1) ürününe kaydırır. Değiştiyse True."""
    pool = row.get('pool') or []
    if not pool:
        return False
    cur_ = row.get('stock')
    idx = None
    if cur_:
        for k, z in enumerate(pool):
            if z['id'] == cur_['id']:
                idx = k
                break
        if idx is None:
            idx = min(range(len(pool)), key=lambda k: abs(pool[k]['retail'] - cur_['retail']))
    else:
        idx = 0 if delta > 0 else len(pool) - 1
        delta = 0
    nk = max(0, min(len(pool) - 1, idx + delta))
    if cur_ and pool[nk]['id'] == cur_['id']:
        return False
    row['stock'], row['status'] = pool[nk], 'seçildi'
    return True


def find_duplicates(rows):
    """Aynı ürünü gösteren satırlar: {stok_id: [satır indeksleri]} (yalnız 2+ olanlar)."""
    g = {}
    for i, r in enumerate(rows):
        if r.get('stock'):
            g.setdefault(r['stock']['id'], []).append(i)
    return {k: v for k, v in g.items() if len(v) > 1}


def merge_duplicates(rows):
    """Aynı ürünlü satırları ilkinde toplar (adetler toplanır); birleştirilen satır sayısını döndürür."""
    dup = find_duplicates(rows)
    drop_ = set()
    for idxs in dup.values():
        first = rows[idxs[0]]
        for k in idxs[1:]:
            first['qty'] = round(first['qty'] + rows[k]['qty'], 4)
            first['raw'] = (first['raw'] + ' + ' + rows[k]['raw'])[:200]
            drop_.add(k)
    rows[:] = [r for i, r in enumerate(rows) if i not in drop_]
    return len(drop_)


def missing_rows(rows):
    return [r for r in rows if not r.get('stock')]


def prep_order(rows, tier):
    """Hazırlık listesi için satırları raf/grup sırasına dizer (rafı olmayanlar sonda, kendi sırasıyla)."""
    def key(ir):
        i, r = ir
        s_ = tier_pick(r, tier) or {}
        loc = s_.get('loc') or ''
        return (0 if loc else 1, fold(loc), fold(s_.get('grp') or ''), i)
    return [r for _, r in sorted(enumerate(rows), key=key)]


def build_missing_html(rows, customer='', phone=''):
    import html as H
    import datetime
    e = lambda x: H.escape(str(x))
    ms = missing_rows(rows)
    body = ''.join(f"<tr><td>&#9744;</td><td class=r>{r['qty']:g}</td><td>{e(r['raw'])}</td></tr>" for r in ms)
    d = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Eksik Ürünler</title><style>body{font-family:Segoe UI,Arial;margin:18px}'
            'table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #999;padding:6px 8px}th{background:#eee}.r{text-align:right}@page{size:A4;margin:10mm}</style></head><body>'
            '<h1>EKSİK / BULUNAMAYAN ÜRÜNLER</h1><div>' + e(d) + ((' • Müşteri: ' + e(customer)) if customer else '') + ((' • ' + e(phone)) if phone else '') + '</div>'
            '<table><tr><th></th><th>Adet</th><th>Listedeki satır</th></tr>' + body + '</table></body></html>')


def missing_text(rows):
    return '\n'.join(f"{r['qty']:g} x {r['raw']}" for r in missing_rows(rows))


def ready_message(customer, rows, tier, opt):
    fr = dict(footer_rows(rows, tier, False, opt))
    fin = (fr.get('KALAN') or fr.get('ÖDENECEK') or fr['TOPLAM'])[0]
    label = 'Kalan tutar' if 'KALAN' in fr else 'Tutar'
    nm = ' '.join(str(customer or '').split())
    return f"Merhaba{(' ' + nm) if nm else ''}, listeniz hazır. {label}: {fin:,.2f} TL. Bekliyoruz."


def preview_text(rows, tier, opt, paper='a4', prep=False, customer='', kind='Perakende'):
    """Ekrandaki canlı ön izleme için düz metin (80 mm = 40 sütun)."""
    W = 40 if paper != 'a4' else 78
    out = []
    ttl = 'HAZIRLIK LİSTESİ' if prep else 'FİYAT TEKLİFİ'
    out.append(ttl.center(W))
    hx, nt = head_extra(opt)
    meta = ' • '.join([x for x in [customer, ('' if prep else kind)] + hx if x])
    if meta:
        out.append(meta[:W].center(W))
    out.append('-' * W)
    rr = prep_order(rows, tier) if prep else rows
    for r in rr:
        s_ = tier_pick(r, tier)
        nm = s_['name'] if s_ else '?? ' + r['raw']
        if prep:
            loc = (s_ or {}).get('loc') or ''
            out.append(f"[ ] {r['qty']:g} x {nm}"[:W + 30])
            if loc:
                out.append(f"      raf: {loc}")
        else:
            out.append(f"{r['qty']:g} x {nm}"[:W + 30])
            if s_:
                out.append(f"{unit(r, s_):,.2f} x {r['qty']:g} = {r['qty'] * unit(r, s_):,.2f}".rjust(W))
    out.append('-' * W)
    if prep:
        out.append(f"{len(rows)} kalem")
    else:
        for lb, v in footer_rows(rows, tier, False, opt):
            out.append(f"{lb}: {v[0]:,.2f} TL".rjust(W))
    if nt:
        out.append('Not: ' + nt)
    return '\n'.join(out)


def season_stats(c, stock):
    """Kayıtlı müşteri listelerinden: en çok istenen ürünler ve bulunamayan satırlar."""
    import json
    job_schema(c)
    names = {z['id']: z['name'] for z in stock}
    asked, miss = {}, {}
    for (rj,) in c.execute("SELECT rows_json FROM liste_musteri").fetchall():
        try:
            data = json.loads(rj or '[]')
        except Exception:
            continue
        for d in data:
            if d.get('sid') and d['sid'] in names:
                a = asked.setdefault(d['sid'], [names[d['sid']], 0.0, 0])
                a[1] += float(d.get('qty') or 0)
                a[2] += 1
            else:
                k = fold(d.get('raw') or '')
                m = miss.setdefault(k, [d.get('raw') or '', 0.0, 0])
                m[1] += float(d.get('qty') or 0)
                m[2] += 1
    top = sorted(asked.values(), key=lambda x: (-x[1], x[0]))
    mm = sorted(miss.values(), key=lambda x: (-x[2], x[0]))
    return top, mm


def footer_rows(rows, tier, compare, opt):
    """[(etiket, [değerler])] — yazdırma/Excel/ön izleme/kopya için ortak alt toplamlar (iskonto, kapora dahil)."""
    opt = opt or {}
    disc = opt.get('disc')
    extra = opt.get('extra') or {}
    tiers = ('uygun', 'orta', 'pahali') if compare else (tier,)
    subs = [tier_total(rows, t) for t in tiers]
    out = [('TOPLAM', subs)]
    ds = [apply_discount(x, disc) for x in subs]
    if any(d for d, _ in ds):
        out.append(('İSKONTO', [-d for d, _ in ds]))
        out.append(('ÖDENECEK', [f for _, f in ds]))
    try:
        kap = float(extra.get('kapora') or 0)
    except (TypeError, ValueError):
        kap = 0.0
    if kap > 0 and not compare:
        out.append(('KAPORA', [-kap]))
        out.append(('KALAN', [round(ds[0][1] - kap, 2)]))
    return out


def head_extra(opt):
    extra = (opt or {}).get('extra') or {}
    parts = []
    if extra.get('teslim'):
        parts.append('Teslim: ' + str(extra['teslim']))
    return parts, str(extra.get('not') or '')


def build_html_80(rows, tier='eslesen', compare=False, title='FİYAT TEKLİFİ', kind='Perakende', customer='', prep=False, phone='', opt=None):
    """80 mm fiş/barkod yazıcı için dar çıktı. prep=True: HAZIRLIK listesi (fiyatsız, ☐ kutulu, raf bilgili)."""
    import html as H
    import datetime
    e = lambda x: H.escape(str(x))
    tiers = ('uygun', 'orta', 'pahali') if (compare and not prep) else (tier,)
    out = []
    for i, r in enumerate(prep_order(rows, tier) if prep else rows, 1):
        s_ = tier_pick(r, tiers[0])
        nm = s_['name'] if s_ else '— ürün seçilmedi: ' + r['raw'][:40]
        if prep:
            loc = (s_.get('loc') if s_ else '') or ''
            out.append(f"<div class=l><span class=b>&#9744;</span> <b>{r['qty']:g} x</b> {e(nm)}" + (f"<div class=s>raf: {e(loc)}</div>" if loc else '') + '</div>')
        elif compare:
            vals = []
            for t in tiers:
                x = tier_pick(r, t)
                vals.append(f"{TIER_AD[t][:1]}: {r['qty'] * unit(r, x):,.2f}" if x else f"{TIER_AD[t][:1]}: -")
            out.append(f"<div class=l><b>{r['qty']:g} x</b> {e(nm)}<div class=s>{' &nbsp; '.join(vals)}</div></div>")
        else:
            val = f"{unit(r, s_):,.2f} x {r['qty']:g} = <b>{r['qty'] * unit(r, s_):,.2f}</b>" if s_ else ''
            out.append(f"<div class=l><b>{r['qty']:g} x</b> {e(nm)}<div class=s>{val}</div></div>")
    d = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    if prep:
        foot = f"<div class=t>{len(rows)} kalem — hazırlandı: ____</div>"
    elif compare:
        fr = dict(footer_rows(rows, tier, True, opt))
        fin = fr.get('ÖDENECEK') or fr['TOPLAM']
        foot = ''.join(f"<div class=t>{e(TIER_AD[t])}: {fin[j]:,.2f} ₺</div>" for j, t in enumerate(('uygun', 'orta', 'pahali')))
        if 'İSKONTO' in fr:
            foot += '<div class=s>(iskonto dahil, ödenecek tutarlar)</div>'
    else:
        foot = ''.join(f"<div class=t>{e(lb)}: {v[0]:,.2f} ₺</div>" for lb, v in footer_rows(rows, tier, False, opt))
    hx, nt = head_extra(opt)
    ttl = 'HAZIRLIK LİSTESİ' if prep else title
    return ('<!doctype html><html><head><meta charset="utf-8"><title>' + e(ttl) + '</title><style>@page{size:80mm auto;margin:2mm}'
            'body{width:72mm;margin:0;font:12px Arial,sans-serif;color:#000}h1{font-size:15px;text-align:center;margin:2px 0}'
            '.c{text-align:center;font-size:11px}.l{border-bottom:1px dashed #000;padding:3px 0}.s{font-size:11px;color:#222;padding-left:14px}'
            '.b{font-size:15px}.t{font-size:14px;font-weight:bold;text-align:right;padding:4px 0}</style></head><body><h1>' + e(ttl) + '</h1><div class=c>'
            + e(d) + ((' • ' + e(customer)) if customer else '') + ((' • ' + e(phone)) if phone else '') + (('' if prep else ' • ' + e(kind)))
            + ''.join(' • ' + e(x) for x in hx) + '</div>' + (f'<div class=c><b>Not:</b> {e(nt)}</div>' if nt else '')
            + ''.join(out) + foot + '</body></html>')


def build_html(rows, tier='eslesen', compare=False, title='FİYAT TEKLİFİ', kind='Perakende', customer='', paper='a4', prep=False, phone='', opt=None):
    if paper != 'a4':
        return build_html_80(rows, tier, compare, title, kind, customer, prep, phone, opt)
    if prep:
        return build_html_prep_a4(rows, tier, customer, phone)
    return build_html_a4(rows, tier, compare, title, kind, customer, opt)


def build_html_prep_a4(rows, tier, customer='', phone=''):
    import html as H
    import datetime
    e = lambda x: H.escape(str(x))
    body = ''.join(f"<tr><td>&#9744;</td><td class=r>{r['qty']:g}</td><td>{e((tier_pick(r, tier) or {}).get('name') or '— ürün seçilmedi: ' + r['raw'])}</td><td>{e((tier_pick(r, tier) or {}).get('loc', ''))}</td></tr>" for r in prep_order(rows, tier))
    d = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Hazırlık Listesi</title><style>body{font-family:Segoe UI,Arial;margin:18px}'
            'table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #999;padding:6px 8px}th{background:#eee}.r{text-align:right}@page{size:A4;margin:10mm}</style></head><body>'
            '<h1>HAZIRLIK LİSTESİ</h1><div>' + e(d) + ((' • Müşteri: ' + e(customer)) if customer else '') + ((' • ' + e(phone)) if phone else '') + '</div>'
            '<table><tr><th></th><th>Adet</th><th>Ürün</th><th>Raf</th></tr>' + body + '</table></body></html>')


def build_html_a4(rows, tier='eslesen', compare=False, title='FİYAT TEKLİFİ', kind='Perakende', customer='', opt=None):
    """Yazdırılabilir A4 HTML. compare=True: üç seçenek yan yana (uygun/orta/pahalı) + her biri için toplam."""
    import html as H
    import datetime
    e = lambda x: H.escape(str(x))
    tiers = ('uygun', 'orta', 'pahali') if compare else (tier,)
    head = ''.join(f'<th class=r>{e(TIER_AD[t])}</th>' for t in tiers)
    body = []
    for i, r in enumerate(rows, 1):
        cells = []
        names = []
        for t in tiers:
            s_ = tier_pick(r, t)
            names.append(s_['name'] if s_ else '— stokta yok / bulunamadı —')
            cells.append(f"{r['qty'] * unit(r, s_):,.2f}" if s_ else '')
        if compare:
            inner = ''.join(f'<td>{e(n)}<br><b>{e(c) + " ₺" if c else ""}</b></td>' for n, c in zip(names, cells))
            body.append(f"<tr><td>{i}</td><td class=r>{r['qty']:g}</td>{inner}</tr>")
        else:
            s_ = tier_pick(r, tiers[0])
            bf = f"{unit(r, s_):,.2f}" if s_ else ''
            body.append(f"<tr><td>{i}</td><td>{e(names[0])}</td><td class=r>{r['qty']:g}</td><td class=r>{bf}</td><td class=r>{cells[0]}</td></tr>")
    span = 2 if compare else 4
    foot = ''.join(f"<tr class=t><td colspan={span} class=r>{e(lb)}</td>" + ''.join(f"<td class=r><b>{v:,.2f} ₺</b></td>" for v in vals) + '</tr>'
                   for lb, vals in footer_rows(rows, tier, compare, opt))
    if compare:
        thead = f'<tr><th>#</th><th class=r>Adet</th>' + ''.join(f'<th>{e(TIER_AD[t])}</th>' for t in tiers) + '</tr>'
    else:
        thead = '<tr><th>#</th><th>Ürün</th><th class=r>Adet</th><th class=r>Birim</th><th class=r>Tutar</th></tr>'
    hx, nt = head_extra(opt)
    d = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    return ('<!doctype html><html><head><meta charset="utf-8"><title>' + e(title) + '</title><style>'
            'body{font-family:Segoe UI,Arial,sans-serif;margin:18px;color:#111}h1{font-size:20px;margin:0 0 4px}'
            'table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}th,td{border:1px solid #999;padding:5px 7px;vertical-align:top}'
            'th{background:#eee}.r{text-align:right}.t td{font-size:15px;background:#f4f4f4}.s{color:#555;font-size:12px}'
            '@page{size:A4;margin:10mm}</style></head><body><h1>' + e(title) + '</h1><div class=s>' + e(d) + '  •  Fiyat türü: ' + e(kind)
            + (('  •  Müşteri: ' + e(customer)) if customer else '') + ''.join('  •  ' + e(x) for x in hx) + '</div>'
            + (f'<div class=s><b>Not:</b> {e(nt)}</div>' if nt else '') + '<table>' + thead + ''.join(body) + foot
            + '</table><p class=s>Fiyatlar bilgi amaçlıdır; stok durumuna göre değişebilir.</p></body></html>')


def export_table(rows, tier='eslesen', compare=False, opt=None):
    """Excel için (başlıklar, satırlar, toplam_satırı). Sayılar sayı olarak kalır."""
    tiers = ('uygun', 'orta', 'pahali') if compare else (tier,)
    if compare:
        heads = ['#', 'Liste satırı', 'Adet']
        for t in tiers:
            heads += [TIER_AD[t] + ' ürün', TIER_AD[t] + ' birim', TIER_AD[t] + ' tutar']
    else:
        heads = ['#', 'Liste satırı', 'Ürün', 'Adet', 'Birim Fiyat', 'Tutar']
    out = []
    for i, r in enumerate(rows, 1):
        if compare:
            line = [i, r['raw'], r['qty']]
            for t in tiers:
                s_ = tier_pick(r, t)
                line += [s_['name'] if s_ else '— bulunamadı —', unit(r, s_) if s_ else '', round(r['qty'] * unit(r, s_), 2) if s_ else '']
        else:
            s_ = tier_pick(r, tiers[0])
            line = [i, r['raw'], s_['name'] if s_ else '— bulunamadı —', r['qty'], unit(r, s_) if s_ else '', round(r['qty'] * unit(r, s_), 2) if s_ else '']
        out.append(line)
    fr = footer_rows(rows, tier, compare, opt)
    lines = []
    for lb, vals in fr:
        if compare:
            ln = ['', lb, '']
            for v in vals:
                ln += ['', '', v]
        else:
            ln = ['', lb, '', '', '', vals[0]]
        lines.append(ln)
    out += lines[:-1]
    return heads, out, lines[-1]


def report_spec(rows, tier='eslesen', compare=False, opt=None, prep=False, small=False, customer='', phone='', kind=''):
    """Programın Rapor Motoru için (rapor_anahtarı, başlıklar, satırlar, toplam_satırı, dönem_metni).
    small=True: 80 mm / dar kâğıt için daralmış sütunlar (ayrı kayıtlı tasarım anahtarı)."""
    key = 'Liste Fiyatla / ' + ('Hazırlık Listesi (Sipariş)' if prep else ('3 Seçenekli Teklif' if compare else 'Fiyat Teklifi')) + (' - Fiş' if small else '')
    hx, nt = head_extra(opt)
    period = ' • '.join(x for x in [customer, phone, ('' if prep else kind)] + hx + [('Not: ' + nt) if nt else ''] if x)
    if prep:
        heads = ['Adet', 'Ürün', 'Raf'] if small else ['☐', 'Adet', 'Ürün', 'Raf']
        out = []
        for r in prep_order(rows, tier):
            s_ = tier_pick(r, tier)
            line = [r['qty'], s_['name'] if s_ else '— ürün seçilmedi: ' + r['raw'][:40], (s_ or {}).get('loc') or '']
            out.append(line if small else ['☐'] + line)
        return key, heads, out, None, (period + ' • ' if period else '') + f'{len(rows)} kalem'
    heads, out, last = export_table(rows, tier, compare, opt)
    if not small:
        return key, heads, out, tuple(last), period
    fr = footer_rows(rows, tier, compare, opt)
    tiers = ('uygun', 'orta', 'pahali') if compare else (tier,)
    if compare:
        heads2 = ['Adet', 'Liste satırı'] + [TIER_AD[t] for t in tiers]
        out2 = [[x[2], x[1]] + [x[5], x[8], x[11]] for x in out if x[0] != '']
        lines = [['', lb] + list(v) for lb, v in fr]
    else:
        heads2 = ['Adet', 'Ürün', 'Tutar']
        out2 = [[x[3], x[2], x[5]] for x in out if x[0] != '']
        lines = [['', lb, v[0]] for lb, v in fr]
    return key, heads2, out2 + lines[:-1], tuple(lines[-1]), period


def paper_of(label, nrows, cw=100.0, ch=150.0, rows=None):
    """Seçilen kâğıt etiketi -> {'kind','w','h'} (mm). Fiş/etiket boyu: satır başına ürün adının sarma sayısına göre (uzun adlar 2-3 satır olur)."""
    if str(label).startswith('A4'):
        return {'kind': 'a4', 'w': 210.0, 'h': 297.0}
    if str(label).startswith('Özel'):
        return {'kind': 'ozel', 'w': max(30.0, float(cw)), 'h': max(30.0, float(ch))}
    h = 50 + 11 * nrows
    if rows:
        h = 45.0
        for r in rows:
            nm = (r.get('stock') or {}).get('name') or '— bulunamadı —'
            h += 6.5 + 3.8 * max(1, -(-len(str(nm)) // 17))
    return {'kind': 'fis' if 'Fiş' in str(label) else 'barkod', 'w': 80.0, 'h': float(max(60, round(h)))}


def final_html(render, rows, tier, compare, opt, prep, pap, customer='', phone='', kind=''):
    """Rapor Motoru varsa onunla (kayıtlı tasarım + ebat), yoksa yerleşik üreticiyle HTML."""
    if render:
        key, heads, out, total, period = report_spec(rows, tier, compare, opt, prep, pap['kind'] != 'a4', customer, phone, kind)
        return render(key, heads, out, total, period, pap)
    return build_html(rows, tier, compare, 'FİYAT TEKLİFİ' + (' (3 SEÇENEK)' if compare else ''), kind, customer, 'a4' if pap['kind'] == 'a4' else '80', prep, phone, opt)


_BUL = re.compile(r'^\s*(?:[•●▪■☐✔✓✅☑√\-\*–—·]|\d{1,2}\s*[\.\)])\s*')


_OCR_QTY_LED = re.compile(r'^\s*(?:[•●▪■☐✔✓✅☑√\-\*–—·]\s*)?\d{1,3}\s*(?:adet\w*|ad\.?|tane\w*|paket|takim|takım|kutu|cift|çift|rulo|top|lu|li)?\b', re.I)
_OCR_SPEC = ('kareli', 'cizgili', 'çizgili', 'telli', 'duz', 'düz', 'boy', 'yaprak', 'yapraklı', 'yp', 'dik', 'klavuz', 'büyük', 'buyuk', 'küçük', 'kucuk', 'orta')


def _ocr_spec_last(line):
    w = re.findall(r'[\wçğıöşüÇĞİÖŞÜ]+', str(line).lower())
    return bool(w) and w[-1] in ('kareli', 'cizgili', 'çizgili', 'telli', 'duz', 'düz')


def _ocr_dot_commas(ln):
    """OCR virgülü nokta okuyabilir ('Pntt. küçük bant. küt makas'): en az 3 harfli sözcükten sonra gelen '. ' ve ardından
    KÜÇÜK harfle başlayan sözcük varsa nokta virgül sayılır. Satır sonu noktası, kısaltmalar (H.B., yp.) ve rakamlı yerler değişmez."""
    return re.sub(r'(?<=[A-Za-zÇĞİÖŞÜçğıöşü]{3})\.\s+(?=[a-zçğıöşü])', ', ', ln)


def _ocr_keep_commas(ln):
    """Miktarla başlayan satırda virgülden sonra sayı/ölçü sözcüğü varsa virgül tek ürünün özelliğini sayar
    ('1 adet büyük boy,60 yaprak, dik klavuz çizgili defter'): virgülleri boşluğa çevirir; yoksa satıra dokunmaz."""
    if ',' not in ln or not _OCR_QTY_LED.match(ln):
        return ln
    rest = ln.split(',', 1)[1]
    parts = [x for x in rest.split(',')]
    if any(re.search(r'\d', x) or any(w in re.findall(r'[\wçğıöşüÇĞİÖŞÜ]+', x.lower()) for w in _OCR_SPEC) for x in parts):
        return re.sub(r'\s*,\s*', ' ', ln).strip()
    return ln


def _pdf_metin_bozuk(t):
    """Yazı katmanı var ama okunamaz (özel kodlu yazı tipi: '(cid:12)', anlamsız simge) mi? Evet ise taranmış gibi OCR denenir."""
    t = str(t or '')
    if '(cid:' in t:
        return True
    ns = [ch for ch in t if not ch.isspace()]
    if not ns:
        return True
    harf = sum(1 for ch in ns if ch.isalpha() or ch.isdigit() or ch in '.,;:()-+/%\'"•*&?!_')
    return harf < 0.85 * len(ns)


def _pdf_tablo_basligi(lines):
    """Tablo başlığı ('Sıra Malzeme Adet') varsa: başlık satırı atılır, sonraki satırlarda baştaki sıra no atılıp
    sondaki sayı adet olarak başa taşınır. Başlık yoksa satırlara dokunmaz."""
    for i, ln in enumerate(lines):
        w = fold(ln).split()
        if 2 <= len(w) <= 6 and w[-1] in ('adet', 'miktar') and any(x in ('malzeme', 'urun', 'ad', 'adi', 'aciklama', 'cinsi') for x in w):
            sira = w[0] in ('sira', 'no', 's.no', 'sn')
            out = lines[:i]
            for x in lines[i + 1:]:
                if sira:
                    m = re.match(r'^\d{1,3}\s*[.)]?\s+(.+?)\s+(\d{1,3})$', x)
                else:
                    m = re.match(r'^(.+?)\s+(\d{1,3})$', x)
                out.append('%s adet %s' % (m.group(2), m.group(1).strip()) if m else x)
            return out
    return lines


def pdf_list_text(path, max_pages=60):
    """Dijital (yazı katmanlı) PDF'ten liste metni. (metin, hata, taranmis) döner.
    taranmis=True: PDF'te okunabilir yazı katmanı yok (fotoğraf/tarama PDF) -> çağıran OCR'a geçer.
    Hata: şifreli / bozuk dosya / pypdf yok. Sütun boşlukları tek boşluğa indirilir; satırlar korunur."""
    try:
        import ALFABE_BELGE_ALIS as BA
        pp = BA._pypdf()
    except Exception:
        pp = None
    if pp is None:
        return '', 'PDF okuma bileşeni (pypdf) bulunamadı. Program klasöründe "pypdf" klasörü bulunmalıdır.', False
    try:
        rd = pp.PdfReader(str(path))
        if getattr(rd, 'is_encrypted', False):
            try:
                if not rd.decrypt(''):
                    return '', 'PDF şifreli; şifresiz bir kopyasını seçin.', False
            except Exception:
                return '', 'PDF şifreli; şifresiz bir kopyasını seçin.', False
        pages = list(rd.pages)
    except Exception as ex:
        return '', 'PDF açılamadı (bozuk ya da desteklenmeyen dosya): %s' % str(ex)[:100], False
    if not pages:
        return '', 'PDF içinde sayfa yok.', False
    out = []
    for pg in pages[:max_pages]:
        t = ''
        for kw in ({'extraction_mode': 'layout'}, {}):
            try:
                t = pg.extract_text(**kw) or ''
                if t.strip():
                    break
            except Exception:
                t = ''
        for ln in t.replace('\r', '\n').replace('\x00', '').split('\n'):
            ln = re.sub(r'[ \t\u00a0]{2,}', ' ', ln.replace('\u00a0', ' ')).strip()
            if ln:
                out.append(ln)
    out = _pdf_tablo_basligi(out)
    text = '\n'.join(out)
    harf = sum(1 for ch in text if ch.isalpha())
    if harf < 12 or _pdf_metin_bozuk(text):
        return '', '', True
    return text, '', False


def join_ocr_lines(text):
    """Fotoğraftan okunan metinde satır sonunda bölünmüş maddeleri birleştirir
    ('Büyük Boy 40 Yapraklı Güzel' / 'Yazı Defteri' -> tek satır). Madde işareti varsa onu; yoksa küçük harfle başlayan
    ya da parantezi açık kalan satırı bir öncekine ekler."""
    lines = [x.rstrip() for x in str(text or '').replace('\r', '\n').split('\n') if x.strip()]
    has_b = sum(1 for x in lines if _BUL.match(x)) >= 2
    out = []
    for ln in lines:
        if not out:
            out.append(ln)
            continue
        prev = out[-1]
        if has_b:
            cont = not _BUL.match(ln)
        else:
            cont = ln.lstrip()[:1].islower() or prev.count('(') > prev.count(')') or prev.rstrip().endswith(',')
            if not cont and _OCR_QTY_LED.match(prev) and _ocr_spec_last(prev) and not _OCR_QTY_LED.match(ln):
                cont = True                    # '1 adet büyük, kareli' + 'Matematik defteri.' (alt satıra taşan ürün adı)
        if cont and not is_header(prev) and not re.match(r'^\s*\d+\s*' + UNIT + r'\b', fold(ln)):
            out[-1] = prev.rstrip() + ' ' + ln.strip()
        else:
            out.append(ln)
    return '\n'.join(_ocr_keep_commas(_ocr_dot_commas(x)) for x in out)


JOB_SCHEMA = """CREATE TABLE IF NOT EXISTS liste_musteri(
 id INTEGER PRIMARY KEY, customer TEXT NOT NULL, phone TEXT DEFAULT '', note TEXT DEFAULT '', created TEXT, updated TEXT,
 raw_text TEXT DEFAULT '', rows_json TEXT DEFAULT '[]', tier TEXT DEFAULT 'eslesen', kind TEXT DEFAULT 'Perakende', status TEXT DEFAULT 'bekliyor')"""


def job_schema(c):
    c.execute(JOB_SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(liste_musteri)").fetchall()}
    if 'extra' not in cols:
        c.execute("ALTER TABLE liste_musteri ADD COLUMN extra TEXT DEFAULT ''")
    c.commit()


def save_job(c, customer, phone, note, raw_text, rows, tier, kind, job_id=None, opt=None):
    import json
    import datetime
    job_schema(c)
    customer = ' '.join(str(customer or '').split())
    if not customer:
        raise ValueError('Müşteri adı yazın.')
    data = [dict(qty=r['qty'], raw=r['raw'], query=r['query'], unit=r.get('unit'), sid=(r['stock']['id'] if r.get('stock') else None),
                 best=(r['best']['id'] if r.get('best') else None), status=r.get('status', ''), status0=r.get('status0', '')) for r in rows]
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    ex = json.dumps(opt or {}, ensure_ascii=False)
    if job_id:
        c.execute("UPDATE liste_musteri SET customer=?,phone=?,note=?,updated=?,raw_text=?,rows_json=?,tier=?,kind=?,extra=? WHERE id=?",
                  (customer, phone or '', note or '', now, raw_text or '', json.dumps(data, ensure_ascii=False), tier, kind, ex, job_id))
        c.commit()
        return job_id
    cur = c.execute("INSERT INTO liste_musteri(customer,phone,note,created,updated,raw_text,rows_json,tier,kind,status,extra) VALUES(?,?,?,?,?,?,?,?,?,'bekliyor',?)",
                    (customer, phone or '', note or '', now, now, raw_text or '', json.dumps(data, ensure_ascii=False), tier, kind, ex))
    c.commit()
    return cur.lastrowid


def list_jobs(c, q='', only_open=False):
    import json
    job_schema(c)
    qf = fold(q)
    out = []
    for r in c.execute("SELECT id,customer,phone,created,updated,status,rows_json,note,extra FROM liste_musteri ORDER BY updated DESC,id DESC").fetchall():
        if only_open and r['status'] != 'bekliyor':
            continue
        if qf and qf not in fold(r['customer'] + ' ' + (r['phone'] or '') + ' ' + (r['note'] or '')):
            continue
        try:
            n = len(json.loads(r['rows_json'] or '[]'))
        except Exception:
            n = 0
        try:
            xo = json.loads(r['extra'] or '{}')
        except Exception:
            xo = {}
        xe = xo.get('extra') or {}
        out.append(dict(id=r['id'], customer=r['customer'], phone=r['phone'] or '', created=r['created'], updated=r['updated'], status=r['status'], n=n, note=r['note'] or '',
                        teslim=xe.get('teslim') or '', kapora=xe.get('kapora') or 0))
    return out


def load_job(c, job_id, stock):
    """Kayıtlı listeyi geri kurar. Ürün adı/fiyatı GÜNCEL stoktan gelir (fiyat değiştiyse yeni fiyat görünür);
    stoktan silinen/pasif ürün satırı 'ürün seçilmedi' olur. stock: dialogdaki aktif stok listesi (fiyat türü uygulanmış)."""
    import json
    job_schema(c)
    r = c.execute("SELECT * FROM liste_musteri WHERE id=?", (job_id,)).fetchone()
    if not r:
        raise ValueError('Kayıt bulunamadı.')
    byid = {s['id']: s for s in stock}
    rows = []
    for d in json.loads(r['rows_json'] or '[]'):
        ex = candidates_ex(stock, d['query'], None, paren_hints(d['query']))
        cands = [(x[0], x[2]) for x in ex[:40]]
        pool = make_pool(ex)
        st = byid.get(d.get('sid'))
        rows.append(dict(qty=float(d['qty']), raw=d['raw'], query=d['query'], stock=st, status=(d.get('status') or 'kontrol') if st else 'yok',
                         cands=[x[1] for x in cands], pool=pool, best=byid.get(d.get('best')) or st, status0=d.get('status0') or 'kontrol',
                         **({'unit': d['unit']} if d.get('unit') is not None else {})))
    try:
        opt = json.loads(r['extra'] or '{}')
    except Exception:
        opt = {}
    return dict(id=r['id'], customer=r['customer'], phone=r['phone'] or '', note=r['note'] or '', raw_text=r['raw_text'] or '',
                tier=r['tier'] or 'eslesen', kind=r['kind'] or 'Perakende', status=r['status'], rows=rows, opt=opt)


def set_job_status(c, job_id, status):
    job_schema(c)
    c.execute("UPDATE liste_musteri SET status=? WHERE id=?", (status, job_id))
    c.commit()


def delete_job(c, job_id):
    job_schema(c)
    c.execute("DELETE FROM liste_musteri WHERE id=?", (job_id,))
    c.commit()


def print_html(parent, html_text):
    import tempfile, os, webbrowser, pathlib
    f = tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, encoding='utf-8', prefix='alfabe_liste_')
    f.write(html_text)
    f.close()
    webbrowser.open(pathlib.Path(f.name).resolve().as_uri())
    return f.name


def open_dialog(parent, connection_factory, on_add=None, export_excel=None, export_pdf=None, printer_suggest=None, on_missing=None, render=None, designer=None, render_png=None):
    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()

    stock = db(load_stock)
    w = tk.Toplevel(parent)
    w.title('Liste Fiyatla — müşteri listesini yapıştır, toplamı gör')
    w.geometry('1100x720')
    try:
        w.state('zoomed')            # tam ekran (Windows)
    except Exception:
        try:
            w.attributes('-zoomed', True)
        except Exception:
            pass
    state = {'rows': [], 'tier': 'eslesen'}

    def front():
        try:
            if w.winfo_exists():
                for ch in w.winfo_children():          # açık alt pencere (kayıtlı listeler, ürün seç, önizleme) varsa onu ÖRTME
                    try:
                        if ch.winfo_class() == 'Toplevel' and ch.winfo_exists() and ch.winfo_viewable():
                            ch.lift()
                            return
                    except Exception:
                        pass
                w.lift()
                w.focus_force()
        except Exception:
            pass

    def keep(fn):
        """Düğme işi bitince (mesaj/dosya penceresi dahil) bu pencereyi tekrar öne alır; ana ekrana atlamayı önler."""
        def f(*a, **k):
            try:
                return fn(*a, **k)
            finally:
                try:
                    w.after(30, front)
                    w.after(350, front)
                except Exception:
                    pass
        return f

    top = ttk.Frame(w, padding=8)
    top.pack(fill='x')
    ttk.Label(top, text='Müşterinin listesini buraya yapıştırın (Ctrl+V), sonra HESAPLA:', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
    txt = tk.Text(top, height=7, font=('Segoe UI', 10), wrap='word')
    txt.pack(fill='x', pady=4)
    cf = ttk.Frame(top)            # Müşteri/Telefon kutuları ve kayıt düğmeleri ana ekranda görünmez; 📝 LİSTEYİ DÜZENLE (TAM EKRAN) penceresindedir
    custv, telv = tk.StringVar(), tk.StringVar()
    ttk.Label(cf, text='Müşteri:').pack(side='left')
    ttk.Entry(cf, textvariable=custv, width=32).pack(side='left', padx=(4, 12))
    ttk.Label(cf, text='Telefon:').pack(side='left')
    ttk.Entry(cf, textvariable=telv, width=16).pack(side='left', padx=(4, 0))
    bt = ttk.Frame(top)
    bt.pack(fill='x')
    bt_gizli = ttk.Frame(top)     # hiç gösterilmeyen kap
    mid = ttk.Frame(w, padding=(8, 0))
    mid.pack(fill='both', expand=True)
    cols = ('n', 'raw', 'q', 'ad', 'p', 't', 'sk', 'd')
    tv = ttk.Treeview(mid, columns=cols, show='headings', selectmode='browse')
    for k, h, wd, an in (('n', '#', 32, 'e'), ('raw', 'Listedeki satır', 300, 'w'), ('q', 'Adet (tıkla: değiştir)', 120, 'e'), ('ad', 'Seçilen ürün (çift tıkla: değiştir)', 340, 'w'),
                         ('p', 'Birim (tıkla: değiştir)', 130, 'e'), ('t', 'Tutar', 90, 'e'), ('sk', 'Stok', 70, 'e'), ('d', 'Durum', 90, 'w')):
        tv.heading(k, text=h)
        tv.column(k, width=wd, anchor=an)
    tv.tag_configure('yok', background='#fde2e2')
    tv.tag_configure('kontrol', background='#fff3cd')
    tv.tag_configure('az', background='#ffd9b3')
    sb = ttk.Scrollbar(mid, orient='vertical', command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side='left', fill='both', expand=True)
    sb.pack(side='left', fill='y')
    pv_on = {'on': False}
    PV = {}          # önizleme penceresi parçaları (pencere açıkken dolu)
    pv_state = {'job': None, 'ph': None, 'seq': 0, 'mode': 'text'}
    tot = tk.Label(w, text='', font=('Segoe UI', 16, 'bold'), fg='#0b6b3a', anchor='w')
    tot.pack(fill='x', padx=10, pady=(6, 0))
    xf = ttk.Frame(w, padding=(10, 2))   # İskonto/Kapora/Teslim/Not çubuğu görünmez (geri açmak için xf.pack(fill='x'))
    discm = tk.StringVar(value=DISC_MODES[0][1])
    discval = tk.StringVar(value='')
    kapv = tk.StringVar(value='')
    teslimv = tk.StringVar(value='')
    notev = tk.StringVar(value='')
    ttk.Label(xf, text='İskonto:').pack(side='left')
    dcb = ttk.Combobox(xf, textvariable=discm, values=[m[1] for m in DISC_MODES], state='readonly', width=20)
    dcb.pack(side='left', padx=(2, 4))
    dent = ttk.Entry(xf, textvariable=discval, width=7)
    dent.pack(side='left', padx=(0, 10))
    ttk.Label(xf, text='Kapora ₺:').pack(side='left')
    ttk.Entry(xf, textvariable=kapv, width=8).pack(side='left', padx=(2, 10))
    ttk.Label(xf, text='Teslim tarihi:').pack(side='left')
    ttk.Entry(xf, textvariable=teslimv, width=11).pack(side='left', padx=(2, 10))
    ttk.Label(xf, text='Not:').pack(side='left')
    ttk.Entry(xf, textvariable=notev, width=28).pack(side='left', padx=(2, 4), fill='x', expand=True)

    def _num(v):
        try:
            return float(str(v).replace(',', '.').strip() or 0)
        except ValueError:
            return 0.0

    def opt():
        mode = next((m[0] for m in DISC_MODES if m[1] == discm.get()), 'yok')
        return {'disc': {'mode': mode, 'val': _num(discval.get())},
                'extra': {'kapora': _num(kapv.get()), 'teslim': teslimv.get().strip(), 'not': notev.get().strip()}}

    def set_opt(o):
        o = o or {}
        d = o.get('disc') or {}
        discm.set(next((m[1] for m in DISC_MODES if m[0] == d.get('mode')), DISC_MODES[0][1]))
        discval.set('' if not d.get('val') else f"{d['val']:g}")
        e_ = o.get('extra') or {}
        kapv.set('' if not e_.get('kapora') else f"{e_['kapora']:g}")
        teslimv.set(e_.get('teslim') or '')
        notev.set(e_.get('not') or '')
    tf = ttk.Frame(w, padding=(10, 2))
    tf.pack(fill='x')
    tbtn = {}

    def set_tier(t):
        state['tier'] = t
        for r in state['rows']:
            if r.get('status') != 'seçildi':
                r['stock'] = tier_pick(r, t)          # durum (sarı 'kontrol' uyarısı dahil) KORUNUR
        refresh()
    kindv = tk.StringVar(value='Perakende')
    ttk.Label(tf, text='Fiyat türü:').pack(side='right', padx=(0, 4))

    def kind_changed(*_):
        apply_price_kind(stock, {'Toptan': 'toptan', 'Özel': 'ozel'}.get(kindv.get(), 'perakende'))
        for r in state['rows']:
            r['pool'].sort(key=lambda z: (z['retail'], z['name']))
        set_tier(state['tier'])
    kc = ttk.Combobox(tf, textvariable=kindv, values=('Perakende', 'Toptan', 'Özel'), state='readonly', width=11)
    kc.pack(side='right')
    kc.bind('<<ComboboxSelected>>', kind_changed)
    for key, label in (('uygun', 'UYGUN FİYATLI'), ('orta', 'ORTA FİYATLI'), ('pahali', 'PAHALI / MARKALI'), ('eslesen', 'EN İYİ EŞLEŞEN')):
        b = tk.Button(tf, text=label, font=('Segoe UI', 11, 'bold'), relief='raised', bd=2, padx=10, pady=6, cursor='hand2', command=keep(lambda k=key: set_tier(k)))
        b.pack(side='left', padx=(0, 8))
        tbtn[key] = (b, label)
    info = ttk.Label(w, text='', foreground='#555')
    info.pack(fill='x', padx=10)
    # Çıktı/yazdırma/kaydetme düğmeleri ana ekranda görünmez; hepsi 📝 LİSTEYİ DÜZENLE (TAM EKRAN) penceresinin altındadır (bb/bb2 yalnızca gizli kap).
    bb = ttk.Frame(w, padding=(8, 8, 8, 2))
    bb2 = ttk.Frame(w, padding=(8, 2, 8, 8))

    def fallback_text():
        _pv = paperv.get()
        _tx = preview_text(state['rows'], state['tier'], opt(), 'a4' if _pv.startswith('A4') else '80', modev.get().startswith('Haz'), custv.get().strip(), kindv.get())
        if not PV:
            return
        PV['cv'].pack_forget()
        PV['sb'].pack_forget()
        PV['t'].pack(fill='both', expand=True)
        PV['t'].config(state='normal')
        PV['t'].delete('1.0', 'end')
        PV['t'].insert('1.0', _tx)
        PV['t'].config(state='disabled')
        pv_state['mode'] = 'text'

    def show_png(res, seq):
        if seq != pv_state['seq'] or not pv_on['on'] or not PV:
            return
        try:
            ph = tk.PhotoImage(file=res) if res else None
        except Exception:
            ph = None
        if ph is None:
            return fallback_text()
        pv_state['ph'] = ph
        cv = PV['cv']
        cv.delete('all')
        x0 = max(0, (cv.winfo_width() - ph.width()) // 2)
        cv.create_image(x0, 6, image=ph, anchor='nw')
        cv.configure(scrollregion=(0, 0, max(ph.width(), cv.winfo_width()), ph.height() + 12))
        cv.yview_moveto(0)
        PV['t'].pack_forget()
        PV['sb'].pack(side='right', fill='y')
        cv.pack(side='left', fill='both', expand=True)
        pv_state['mode'] = 'image'

    def preview_now():
        pv_state['job'] = None
        if not pv_on['on'] or not PV:
            return
        if not (render and render_png and state['rows']):
            return fallback_text()
        import tempfile
        import threading
        try:
            h = out_html(False, modev.get().startswith('Haz'))
            pap = cur_pap()
        except Exception:
            return fallback_text()
        wpx, hpx = pap['w'] * 96 / 25.4, pap['h'] * 96 / 25.4
        avail = max(300, PV['cv'].winfo_width() - 30) if PV['cv'].winfo_width() > 50 else 720
        tw = min(float(avail), wpx * 2.2)
        out = os.path.join(tempfile.gettempdir(), f'alfabe_lf_pv_{os.getpid()}.png')
        pv_state['seq'] += 1
        seq = pv_state['seq']
        box = {'res': None, 'done': False}

        def work():
            try:
                if os.path.exists(out):
                    os.remove(out)
                box['res'] = render_png(h, wpx, hpx, tw / wpx, out)
            except Exception:
                box['res'] = None
            box['done'] = True
        if state.get('sync'):
            work()
            return show_png(box['res'], seq)
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not box['done']:
                try:
                    w.after(120, poll)
                except Exception:
                    pass
                return
            show_png(box['res'], seq)
        w.after(120, poll)

    def schedule_preview():
        if state.get('sync'):
            return preview_now()
        try:
            if pv_state['job']:
                w.after_cancel(pv_state['job'])
            pv_state['job'] = w.after(450, preview_now)
        except Exception:
            preview_now()

    def refresh():
        tv.delete(*tv.get_children())
        nm = 0
        nlow = 0
        for i, r in enumerate(state['rows']):
            s = r['stock']
            if s:
                lab = r['status'] if r['status'] in ('seçildi', 'hatırlanan', 'kesin', 'kontrol') else (r.get('status0') or 'kontrol')
                tag = 'kontrol' if lab == 'kontrol' else ''
                if lab == 'kontrol' and r.get('ocr_fix'):
                    lab = 'kontrol (OCR)'                # OCR harf düzeltmesiyle bulunan tahmin: satıcı mutlaka kontrol etsin
                if unit(r, s) <= 0:
                    lab, tag = 'FİYAT 0', 'yok'
                    nm += 1
                sk_ = f"{s.get('qty') or 0:g}"
                if short_of(r) > 0:
                    nlow += 1
                    if tag != 'yok':
                        tag = 'az'
                vals = (i + 1, r['raw'], f"{r['qty']:g}", s['name'], f"{unit(r, s):,.2f}", f"{r['qty'] * unit(r, s):,.2f}", sk_, lab + (' *' if r.get('unit') is not None else ''))
            else:
                vals = (i + 1, r['raw'], f"{r['qty']:g}", '— ürün seçilmedi —', '', '', '', 'yok')
                tag = 'yok'
                nm += 1
            tv.insert('', 'end', iid=str(i), values=vals, tags=(tag,))
        if pv_on['on']:
            schedule_preview()
        fr_ = footer_rows(state['rows'], state['tier'], False, opt())
        tot.config(text='   '.join(f"{lb}: {v[0]:,.2f} ₺" for lb, v in fr_ if lb in ('TOPLAM', 'İSKONTO', 'ÖDENECEK', 'KAPORA', 'KALAN')))
        for key, (b, label) in tbtn.items():
            b.config(text=f"{label}\n{apply_discount(tier_total(state['rows'], key), opt()['disc'])[1]:,.2f} ₺", bg='#c2570c' if key == state['tier'] else '#e6e6e6', fg='white' if key == state['tier'] else 'black')
        chk = sum(1 for r in state['rows'] if r.get('stock') and tv.set(str(state['rows'].index(r)), 'd').startswith('kontrol'))
        info.config(text=f"{len(state['rows'])} satır   •   eşleşmeyen: {nm}   •   kontrol edilecek (sarı): {chk}   •   turuncu (stok yetersiz): {nlow}" + (f"   •   ⚠ {len(find_duplicates(state['rows']))} ürün birden fazla satırda (AYNILARI BİRLEŞTİR)" if find_duplicates(state['rows']) else '') + "   •   kırmızı satırlar toplama girmez")
        if state.get('ed_redraw'):
            state['ed_redraw']()

    for _v in (discm, discval, kapv, teslimv, notev):
        _v.trace_add('write', lambda *_: refresh())

    def calc():
        raw = txt.get('1.0', 'end')
        raw_n, tbl_notes = normalize_table(raw)
        if not raw_n.strip() and tbl_notes:                 # yalnızca numaralar yapıştırılmış: kutuya dokunma, uyar
            return messagebox.showwarning('Liste', ' '.join(tbl_notes), parent=dlg_parent())
        if raw_n != raw:                                    # tablodan yapışmış liste: düzgün satırlara çevrilmiş halini kutuda göster
            txt.delete('1.0', 'end')
            txt.insert('1.0', raw_n)
            raw = raw_n

        def work(report):
            report('Liste satırlara ayrılıyor…')
            con = connection_factory()
            try:
                return match_all(con, raw, stock, progress=lambda i, n: report(f'Stoktan ürünler aranıyor…  {i}/{n} satır', (i / n) if n else None), ocr_text=state.get('ocr_text'))
            finally:
                con.close()

        def done(res, err):
            if err is not None:
                return messagebox.showwarning('Liste', 'Hesaplanamadı: ' + str(err), parent=dlg_parent())
            snap()
            state['rows'] = res
            state['tier'] = 'eslesen'
            refresh()
            if tbl_notes:
                info.config(text='ℹ ' + ' '.join(tbl_notes) + '  ' + info.cget('text'))
            front()
            if not state['rows']:
                messagebox.showinfo('Liste', 'Listede ürün satırı bulunamadı.', parent=dlg_parent())
        run_with_progress(w, 'Liste hesaplanıyor', work, done, sync=state.get('sync', False))

    def cur():
        s = tv.selection()
        return int(s[0]) if s else None

    def snap():
        state.setdefault('undo', []).append([dict(r) for r in state['rows']])
        del state['undo'][:-40]

    def undo():
        if not state.get('undo'):
            return messagebox.showinfo('Liste', 'Geri alınacak işlem yok.', parent=dlg_parent())
        state['rows'][:] = state['undo'].pop()
        refresh()
        info.config(text='Son işlem geri alındı.')

    def pick(i=None):
        i = cur() if i is None else i
        if i is None:
            return messagebox.showinfo('Liste', 'Önce bir satır seçin.', parent=dlg_parent())
        r = state['rows'][i]
        d = tk.Toplevel(w)
        d.title('Ürün seç — ' + r['raw'][:60])
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        d.geometry('%dx%d+%d+%d' % (min(1100, sw - 80), min(780, sh - 100), max(0, (sw - min(1100, sw - 80)) // 2), 30))
        d.transient(dlg_parent())
        d.grab_set()
        sv = tk.StringVar(value=r['query'])
        ttk.Label(d, text='Yazarak arayın  •  ↑ ↓ ile ürüne gidin  •  ENTER = seç ve listeye at  •  ESC = vazgeç', foreground='#555').pack(anchor='w', padx=8, pady=(6, 0))
        e = ttk.Entry(d, textvariable=sv)
        e.pack(fill='x', padx=8, pady=6)
        lb = ttk.Treeview(d, columns=('n', 'g', 'p', 's'), show='headings', selectmode='browse', height=24)
        HEAD = {'n': 'Ürün', 'g': 'Stok grubu', 'p': 'Fiyat', 's': 'Stok'}
        for k, wd, an in (('n', 520, 'w'), ('g', 190, 'w'), ('p', 100, 'e'), ('s', 80, 'e')):
            lb.heading(k, text=HEAD[k] + '  ⇅', command=lambda k=k: sort_by(k))
            lb.column(k, width=wd, anchor=an)
        lb.pack(fill='both', expand=True, padx=8)
        lbsb = ttk.Scrollbar(d, orient='vertical', command=lb.yview)      # v470: on binlerce ürün arasında hızlı gezinmek için kaydırma çubuğu (listenin sağ kenarında)
        lb.configure(yscrollcommand=lbsb.set)
        lbsb.place(in_=lb, relx=1.0, rely=0.0, relheight=1.0, anchor='ne')
        shown = {}
        cur_list = []
        sortst = {'k': 'g', 'rev': False}
        bestid = {'id': None}
        KEYF = {'n': lambda s_: fold(s_['name']), 'g': lambda s_: (fold(s_['grp']), fold(s_['name'])), 'p': lambda s_: s_['retail'], 's': lambda s_: s_['qty']}

        def draw():
            lb.delete(*lb.get_children())
            shown.clear()
            items = list(cur_list)
            if sortst['k']:
                items.sort(key=KEYF[sortst['k']], reverse=sortst['rev'])
            for s in items:
                shown[str(s['id'])] = s
                lb.insert('', 'end', iid=str(s['id']), values=(s['name'], s['grp'], f"{s['retail']:,.2f}", f"{s['qty']:g}"))
            ch = lb.get_children()
            if ch:
                pick_id = str(bestid['id']) if str(bestid['id']) in shown else ch[0]     # en uygun ürün seçili gelir (ENTER onu seçer)
                lb.selection_set(pick_id)
                lb.focus(pick_id)
                try:
                    lb.update_idletasks()
                except Exception:
                    pass
                lb.see(pick_id)
                lb.after_idle(lambda: lb.see(pick_id) if lb.exists(pick_id) else None)
                lb.after(120, lambda: lb.see(pick_id) if lb.exists(pick_id) else None)
            for k_ in HEAD:
                arrow = ('  ▲' if not sortst['rev'] else '  ▼') if sortst['k'] == k_ else '  ⇅'
                lb.heading(k_, text=HEAD[k_] + arrow)

        def sort_by(k):
            sortst['rev'] = (not sortst['rev']) if sortst['k'] == k else False
            sortst['k'] = k
            draw()

        def fill(*_):
            if query_tokens(sv.get()):
                res = candidates(stock, sv.get(), None)          # sınır yok: eşleşen HEPSİ görünür
                cur_list[:] = [s for sc, s in res]
                bestid['id'] = res[0][1]['id'] if res else None
                if not res:
                    cur_list[:] = list(stock)                     # hiç aday yoksa boş ekran yerine tüm stok (elle seçim)
            else:
                cur_list[:] = list(stock)                         # arama boş: tüm stok, grup sırasıyla
                bestid['id'] = None
            draw()
            d.title(f"Ürün seç — {r['raw'][:50]}   ({len(cur_list)} ürün)")

        def fill_later(*_):
            try:
                if getattr(d, '_lf_job', None):
                    d.after_cancel(d._lf_job)
                d._lf_job = d.after(180, fill)
            except Exception:
                fill()

        def ok(*_):
            sel = lb.selection()
            if not sel:
                return
            s = shown[sel[0]]
            snap()
            r['stock'], r['status'] = s, 'seçildi'
            db(lambda con: remember(con, r['query'], s['id']))
            d.destroy()
            refresh()
            front()

        def none():
            snap()
            r['stock'], r['status'] = None, 'yok'
            db(lambda con: remember(con, r['query'], None))
            d.destroy()
            refresh()
            front()
        sv.trace_add('write', fill_later)
        lb.bind('<Double-1>', ok)
        e.bind('<Return>', ok)
        def mv(delta):
            ch = lb.get_children()
            if not ch:
                return 'break'
            sel = lb.selection()
            k = ch.index(sel[0]) + delta if sel else 0
            k = max(0, min(len(ch) - 1, k))
            lb.selection_set(ch[k])
            lb.focus(ch[k])
            lb.see(ch[k])
            return 'break'
        e.bind('<Down>', lambda ev=None: mv(1))
        e.bind('<Up>', lambda ev=None: mv(-1))
        e.bind('<Next>', lambda ev=None: mv(8))
        e.bind('<Prior>', lambda ev=None: mv(-8))
        lb.bind('<Return>', ok)
        f = ttk.Frame(d)
        f.pack(fill='x', padx=8, pady=6)
        ttk.Button(f, text='SEÇ', command=ok).pack(side='left')
        ttk.Button(f, text='BU SATIRDA ÜRÜN YOK', command=none).pack(side='left', padx=6)
        fill()
        e.focus_set()
        d.bind('<Escape>', lambda ev=None: d.destroy())

    def set_qty():
        i = cur()
        if i is None:
            return messagebox.showinfo('Liste', 'Önce bir satır seçin.', parent=dlg_parent())
        v = simpledialog.askstring('Adet', 'Adet:', initialvalue=f"{state['rows'][i]['qty']:g}", parent=dlg_parent())
        try:
            q = float(str(v).replace(',', '.'))
            if q <= 0:
                raise ValueError
        except Exception:
            return
        snap()
        state['rows'][i]['qty'] = q
        refresh()
        tv.selection_set(str(i))

    def dlg_parent():
        ew = state.get('edit_win')
        try:
            return ew if ew is not None and ew.winfo_exists() else w
        except Exception:
            return w

    def split_row(i=None):
        """Seçili satırı elle birden çok ürüne böler (parçalar ';' ile ayrılır); her parça stoktan ayrı eşleşir."""
        i = cur() if i is None else i
        if i is None:
            return messagebox.showinfo('Liste', 'Önce bir satır seçin.', parent=dlg_parent())
        r = state['rows'][i]
        init = re.sub(r'\s+ve\s+', '; ', re.sub(r'\([^)]*\)', ' ', r['query'] or r['raw'])).strip()
        v = simpledialog.askstring('Satırı böl', 'Parçaları ; ile ayırarak yazın (her parça ayrı ürün olur):', initialvalue=init, parent=dlg_parent())
        parts = [x.strip() for x in str(v or '').split(';') if x.strip()]
        if len(parts) < 2:
            return
        new = []
        for p_ in parts:
            for nr in db(lambda con, p_=p_: match_all(con, p_, stock)):
                nr['qty'] = r['qty']
                nr['raw'] = r['raw'] + ' — ' + p_
                new.append(nr)
        if not new:
            return
        snap()
        state['rows'][i:i + 1] = new
        refresh()
        try:
            tv.selection_set(str(i))
        except Exception:
            pass

    def edit_window():
        ew = state.get('edit_win')
        try:
            if ew is not None and ew.winfo_exists():
                ew.lift()
                ew.focus_force()
                return ew
        except Exception:
            pass
        d = tk.Toplevel(w)
        d.title('Listeyi düzenle — tam ekran')
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        d.geometry('%dx%d+0+0' % (sw, sh - 40))
        for fn in (lambda: d.state('zoomed'), lambda: d.attributes('-zoomed', True)):
            try:
                fn()
                break
            except Exception:
                pass
        bar = ttk.Frame(d, padding=(8, 6))
        bar.pack(fill='x')
        cust = ttk.Frame(d, padding=(8, 0, 8, 6))   # Müşteri/Telefon + kayıt düğmeleri görünmez (kodu korunur, gerekirse cust.pack(fill='x') ile geri açılır)
        # İskonto / Kapora / Teslim tarihi / Not: ana ekranda değil, burada (aynı değişkenler; değişince fiyat yeniden hesaplanır)
        xf2 = ttk.Frame(d, padding=(8, 0, 8, 6))
        xf2.pack(fill='x')
        ttk.Label(xf2, text='İskonto:').pack(side='left')
        ttk.Combobox(xf2, textvariable=discm, values=[m[1] for m in DISC_MODES], state='readonly', width=20).pack(side='left', padx=(2, 4))
        ttk.Entry(xf2, textvariable=discval, width=7).pack(side='left', padx=(0, 10))
        # Kapora / Teslim tarihi / Not alanları kaldırıldı (değişkenleri boş kalır, hesaba etkisi yok)
        ttk.Label(cust, text='Müşteri:').pack(side='left')
        ttk.Entry(cust, textvariable=custv, width=32).pack(side='left', padx=(4, 12))
        ttk.Label(cust, text='Telefon:').pack(side='left')
        ttk.Entry(cust, textvariable=telv, width=16).pack(side='left', padx=(4, 18))
        for tx, fn in (('💾 MÜŞTERİYE KAYDET', lambda: cur_job_save(False)), ('📂 KAYITLI LİSTELER', jobs_window), ('🗑 KAYDI SİL', del_current), ('📊 SEZON RAPORU', season_window)):
            ttk.Button(cust, text=tx, command=keep(fn)).pack(side='left', padx=(0, 6))
        bar2 = ttk.Frame(d, padding=(8, 0, 8, 4))
        bar2.pack(fill='x')
        fv = tk.StringVar(value='Tümü')
        FILT = ('Tümü', 'Sadece eşleşmeyenler (yok)', 'Sadece kontrol edilecekler', 'Stoğu yetmeyenler', 'Elle eklenenler')
        etv = ttk.Treeview(d, columns=cols, show='headings', selectmode='browse')
        for k, h, wd, an in (('n', '#', 40, 'e'), ('raw', 'Listedeki satır', 520, 'w'), ('q', 'Adet (tıkla: değiştir)', 120, 'e'), ('ad', 'Seçilen ürün (çift tıkla: değiştir)', 520, 'w'),
                             ('p', 'Birim (tıkla: değiştir)', 130, 'e'), ('t', 'Tutar', 100, 'e'), ('sk', 'Stok', 70, 'e'), ('d', 'Durum', 90, 'w')):
            etv.heading(k, text=h)
            etv.column(k, width=wd, anchor=an)
        etv.tag_configure('yok', background='#fde2e2')
        etv.tag_configure('kontrol', background='#fff3cd')
        etv.tag_configure('az', background='#ffd9b3')
        esb = ttk.Scrollbar(d, orient='vertical', command=etv.yview)
        etv.configure(yscrollcommand=esb.set)
        foot = ttk.Frame(d, padding=(8, 4))
        foot.pack(side='bottom', fill='x')
        out2 = ttk.Frame(d, padding=(8, 2))
        out2.pack(side='bottom', fill='x')
        out1 = ttk.Frame(d, padding=(8, 4, 8, 2))
        out1.pack(side='bottom', fill='x')
        if on_add:
            tk.Button(out1, text='SEPETE EKLE', command=keep(to_cart), bg='#0f8a5f', fg='white', font=('Segoe UI', 11, 'bold'), padx=18, pady=4, cursor='hand2').pack(side='right')
        for tx, fn, pad in (('👁 ÖN İZLEME', toggle_pv, (0, 6)), ('🎨 RAPOR TASARIMI', open_designer, (0, 6)), ('YAZDIR', lambda: do_print(False), (12, 6))):
            ttk.Button(out2, text=tx, command=keep(fn)).pack(side='left', padx=pad)
        ttk.Label(out2, text='Çıktı:').pack(side='left', padx=(14, 2))
        ttk.Combobox(out2, textvariable=modev, state='readonly', width=16, values=('Fiyatlı liste', 'Hazırlık listesi')).pack(side='left', padx=(0, 10))
        ttk.Label(out2, text='Kâğıt/Yazıcı:').pack(side='left', padx=(6, 2))
        ttk.Combobox(out2, textvariable=paperv, state='readonly', width=26, values=('A4 (normal yazıcı)', '80 mm Fiş yazıcı', '80 mm Barkod/Etiket yazıcı', 'Özel ebat (mm)')).pack(side='left')
        ttk.Label(out2, text='Özel mm (en x boy):').pack(side='left', padx=(8, 2))
        ttk.Entry(out2, textvariable=cwv, width=5).pack(side='left')
        ttk.Label(out2, text='x').pack(side='left')
        ttk.Entry(out2, textvariable=chv, width=5).pack(side='left')
        esb.pack(side='right', fill='y')
        etv.pack(fill='both', expand=True, side='left')
        tl = tk.Label(foot, text='', font=('Segoe UI', 13, 'bold'), fg='#0a6b2d', anchor='w')
        tl.pack(side='left')
        cnt = tk.Label(foot, text='', anchor='e', fg='#444')
        cnt.pack(side='right')
        sync = {'on': False}

        def passes(i, r):
            f = fv.get()
            s_ = r.get('stock')
            if f == FILT[1]:
                return s_ is None or unit(r, s_) <= 0
            if f == FILT[2]:
                return bool(s_) and (r['status'] if r['status'] in ('seçildi', 'hatırlanan', 'kesin', 'kontrol') else (r.get('status0') or 'kontrol')) == 'kontrol'
            if f == FILT[3]:
                return bool(s_) and short_of(r) > 0
            if f == FILT[4]:
                return str(r.get('raw', '')).startswith('(elle eklendi)')
            return True

        def redraw(*_):
            if not d.winfo_exists():
                return
            sel = etv.selection()
            sync['on'] = True
            etv.delete(*etv.get_children())
            shown = 0
            for i, r in enumerate(state['rows']):
                if not passes(i, r):
                    continue
                iid = str(i)
                if tv.exists(iid):
                    vals = list(tv.item(iid, 'values'))
                    tg = tv.item(iid, 'tags')
                else:
                    continue
                etv.insert('', 'end', iid=iid, values=vals, tags=tg)
                shown += 1
            if sel and etv.exists(sel[0]):
                etv.selection_set(sel[0])
            sync['on'] = False
            tl.config(text=tot.cget('text'))
            cnt.config(text=f"{shown} / {len(state['rows'])} satır gösteriliyor")

        def on_sel(_e=None):
            if sync['on']:
                return
            s_ = etv.selection()
            if s_ and tv.exists(s_[0]):
                tv.selection_set(s_[0])

        def estart(ev):
            end_edit(True)
            if etv.identify_region(ev.x, ev.y) != 'cell':
                return
            col = etv.identify_column(ev.x)
            key = {'#3': 'q', '#5': 'p'}.get(col)
            iid = etv.identify_row(ev.y)
            if not key or not iid:
                return
            i = int(iid)
            r = state['rows'][i]
            if key == 'p' and r.get('stock') is None:
                return
            bx = etv.bbox(iid, col)
            if not bx:
                return
            cv_ = r['qty'] if key == 'q' else unit(r, r['stock'])
            e = tk.Entry(etv, justify='right', font=('Segoe UI', 11))
            e._i, e._col = i, key
            e.insert(0, f'{cv_:g}' if key == 'q' else f'{cv_:.2f}')
            e.place(x=bx[0], y=bx[1], width=bx[2], height=bx[3])
            e.focus_set()
            e.select_range(0, 'end')
            e.bind('<Return>', lambda _e=None: end_edit(True))
            e.bind('<KP_Enter>', lambda _e=None: end_edit(True))
            e.bind('<Escape>', lambda _e=None: end_edit(False))
            e.bind('<FocusOut>', lambda _e=None: end_edit(True))
            ed['e'] = e
            etv.selection_set(iid)
            return 'break'

        def edbl(ev):
            if etv.identify_column(ev.x) in ('#3', '#5'):
                return 'break'
            on_sel()
            pick()

        def do(fn):
            def run(*_):
                end_edit(True)
                on_sel()
                fn()
            return run

        def delete_key(_e=None):
            if ed['e'] is not None:
                return
            on_sel()
            drop()
            return 'break'
        def del_sel():
            sel = etv.selection()
            if not sel:
                return messagebox.showinfo('Liste', 'Önce silinecek satırı seçin.', parent=dlg_parent())
            i0 = int(sel[0])
            drop()
            n_ = len(state['rows'])
            if n_ and etv.exists(str(min(i0, n_ - 1))):                 # silinenin yerindeki satır seçili kalsın: peş peşe silmek kolay olsun
                etv.selection_set(str(min(i0, n_ - 1)))
                etv.focus(str(min(i0, n_ - 1)))
        for tx, fn in (('➕ ÜRÜN EKLE', add_product), ('ÜRÜN SEÇ / DEĞİŞTİR', pick), ('🗑 SEÇİLİ SATIRI SİL', del_sel)):
            ttk.Button(bar, text=tx, command=do(fn)).pack(side='left', padx=(0, 6))
        ttk.Button(bar, text='KAPAT (ESC)', command=lambda: close()).pack(side='right')
        ttk.Label(bar2, text='Göster:').pack(side='left')
        cbf = ttk.Combobox(bar2, textvariable=fv, values=FILT, state='readonly', width=30)
        cbf.pack(side='left', padx=(4, 12))
        ttk.Label(bar2, text='Adet/Birim hücresine tıklayıp yazın  •  çift tık = ürün değiştir  •  ENTER = ürün seç  •  DELETE = satırı çıkar  •  ↑ ↓ = satır değiştir', foreground='#555').pack(side='left')
        fv.trace_add('write', redraw)
        etv.bind('<<TreeviewSelect>>', on_sel)
        etv.bind('<Button-1>', estart, add='+')
        etv.bind('<Double-1>', edbl)
        etv.bind('<Return>', lambda _e=None: (on_sel(), pick()))
        etv.bind('<Delete>', delete_key)

        def close(*_):
            end_edit(False)
            state['ed_redraw'] = None
            state['edit_win'] = None
            try:
                d.destroy()
            except Exception:
                pass
            front()
        d.bind('<Escape>', close)
        d.protocol('WM_DELETE_WINDOW', close)
        state['edit_win'] = d
        state['ed_redraw'] = redraw
        d._lf = dict(etv=etv, fv=fv, redraw=redraw, close=close, cnt=cnt, tl=tl)
        redraw()
        ch = etv.get_children()
        if ch:
            etv.selection_set(ch[0])
            etv.focus(ch[0])
        etv.focus_set()
        return d

    def add_row(s_, qty=1.0):
        """Listeye elle ürün satırı ekler (sona). Satır 'seçildi' sayılır; kademe düğmeleri ona dokunmaz."""
        try:
            ex = candidates_ex(stock, s_['name'], None, paren_hints(s_['name']))
            pool = make_pool(ex)
        except Exception:
            pool = []
        snap()
        state['rows'].append(dict(qty=float(qty), raw='(elle eklendi) ' + s_['name'], query=s_['name'], stock=s_, status='seçildi', cands=[s_], pool=pool or [s_], best=s_, status0='seçildi'))
        refresh()
        k = str(len(state['rows']) - 1)
        try:
            tv.selection_set(k)
            tv.see(k)
        except Exception:
            pass
        return len(state['rows']) - 1

    def add_product():
        d = tk.Toplevel(w)
        d.title('Ürün ekle — listenin sonuna eklenir')
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = min(1000, sw - 80), min(720, sh - 100)
        d.geometry('%dx%d+%d+%d' % (ww, wh, (sw - ww) // 2, max(0, (sh - wh) // 2 - 20)))
        d.transient(dlg_parent())
        ttk.Label(d, text='Ürün adı / barkod / raf yazın  •  ↑ ↓ ile gidin  •  ENTER veya çift tık = listeye ekle (pencere açık kalır)  •  ESC = kapat', foreground='#555').pack(anchor='w', padx=8, pady=(6, 0))
        top = ttk.Frame(d)
        top.pack(fill='x', padx=8, pady=6)
        sv, qv = tk.StringVar(), tk.StringVar(value='1')
        e = ttk.Entry(top, textvariable=sv)
        e.pack(side='left', fill='x', expand=True)
        ttk.Label(top, text='Adet:').pack(side='left', padx=(10, 2))
        qe = ttk.Entry(top, textvariable=qv, width=6)
        qe.pack(side='left')
        lb = ttk.Treeview(d, columns=('n', 'g', 'p', 's', 'r'), show='headings', selectmode='browse', height=20)
        for k, tx, wd, an in (('n', 'Ürün', 440, 'w'), ('g', 'Stok grubu', 170, 'w'), ('p', 'Fiyat', 90, 'e'), ('s', 'Stok', 70, 'e'), ('r', 'Raf', 110, 'w')):
            lb.heading(k, text=tx)
            lb.column(k, width=wd, anchor=an)
        lb.pack(fill='both', expand=True, padx=8)
        msg = ttk.Label(d, text='', foreground='#0a6b2d')
        msg.pack(anchor='w', padx=8, pady=(4, 0))
        shown = {}

        def fill(*_):
            lb.delete(*lb.get_children())
            shown.clear()
            q = sv.get()
            if query_tokens(q):
                items = [x for sc, x in candidates(stock, q, None)][:300]
            else:
                items = sorted(stock, key=lambda z: (fold(z['grp']), fold(z['name'])))[:300]
            for x in items:
                shown[str(x['id'])] = x
                lb.insert('', 'end', iid=str(x['id']), values=(x['name'], x['grp'], f"{x['retail']:,.2f}", f"{x['qty']:g}", x.get('loc', '')))
            ch = lb.get_children()
            if ch:
                lb.selection_set(ch[0])
                lb.focus(ch[0])

        def fill_later(*_):
            try:
                if getattr(d, '_lf_job', None):
                    d.after_cancel(d._lf_job)
                d._lf_job = d.after(180, fill)
            except Exception:
                fill()

        def add(*_):
            sel = lb.selection()
            if not sel:
                return
            try:
                q = float(str(qv.get()).replace(',', '.'))
                if q <= 0:
                    raise ValueError
            except Exception:
                return messagebox.showwarning('Liste', 'Adet geçerli bir sayı olmalı.', parent=d)
            x = shown[sel[0]]
            add_row(x, q)
            msg.config(text=f"✔ Eklendi: {q:g} × {x['name']}   (listede {len(state['rows'])} satır)")
            sv.set('')
            qv.set('1')
            e.focus_set()

        def mv(delta):
            ch = lb.get_children()
            if not ch:
                return 'break'
            sel = lb.selection()
            k = max(0, min(len(ch) - 1, (ch.index(sel[0]) if sel else 0) + delta))
            lb.selection_set(ch[k])
            lb.focus(ch[k])
            lb.see(ch[k])
            return 'break'
        sv.trace_add('write', fill_later)
        lb.bind('<Double-1>', add)
        lb.bind('<Return>', add)
        e.bind('<Return>', add)
        qe.bind('<Return>', add)
        e.bind('<Down>', lambda ev=None: mv(1))
        e.bind('<Up>', lambda ev=None: mv(-1))
        d.bind('<Escape>', lambda ev=None: d.destroy())
        f = ttk.Frame(d)
        f.pack(fill='x', padx=8, pady=6)
        ttk.Button(f, text='LİSTEYE EKLE', command=add).pack(side='left')
        ttk.Button(f, text='KAPAT', command=d.destroy).pack(side='right')
        fill()
        e.focus_set()
        state['add_win'] = d
        d._lf = dict(sv=sv, qv=qv, lb=lb, add=add, fill=fill)

    def drop():
        i = cur()
        if i is None:
            return
        snap()
        del state['rows'][i]
        refresh()

    def to_cart():
        items = [(r['stock']['id'], r['qty'], r.get('unit')) if r.get('unit') is not None else (r['stock']['id'], r['qty']) for r in state['rows'] if r.get('stock')]
        if not items:
            return messagebox.showinfo('Liste', 'Sepete eklenecek ürün yok.', parent=dlg_parent())
        miss = sum(1 for r in state['rows'] if not r.get('stock'))
        if miss and not messagebox.askyesno('Liste', f'{miss} satırda ürün seçilmedi, bunlar eklenmeyecek. Devam edilsin mi?', parent=dlg_parent()):
            return
        if on_add:
            on_add(items)
        w.destroy()

    def copy_txt():
        lines = [f"{r['qty']:g} x {r['stock']['name']}  {r['qty'] * unit(r, r['stock']):,.2f}" if r.get('stock') else f"{r['qty']:g} x {r['raw']}  (yok)" for r in state['rows']]
        for lb, v in footer_rows(state['rows'], state['tier'], False, opt()):
            lines.append(f"{lb}: {v[0]:,.2f} TL")
        hx_, nt_ = head_extra(opt())
        if hx_:
            lines.append(' • '.join(hx_))
        if nt_:
            lines.append('Not: ' + nt_)
        w.clipboard_clear()
        w.clipboard_append('\n'.join(lines))
        info.config(text='Liste panoya kopyalandı (WhatsApp / mesaja yapıştırabilirsiniz).')

    def save_list():
        items = [(r['stock']['id'], r['qty']) for r in state['rows'] if r.get('stock')]
        if not items:
            return messagebox.showinfo('Liste', 'Kaydedilecek ürün yok.', parent=dlg_parent())
        n = simpledialog.askstring('Okul listesi olarak kaydet', 'Liste adı (örn. Atatürk İlkokulu 1. Sınıf):', parent=dlg_parent())
        if not n:
            return
        import ALFABE_OKUL_LISTE as OL

        def f(con):
            lid = OL.create_list(con, n)
            for sid, q in items:
                OL.set_item(con, lid, sid, q)
        try:
            db(f)
        except Exception as ex:
            return messagebox.showwarning('Liste', str(ex), parent=dlg_parent())
        messagebox.showinfo('Liste', 'Kaydedildi. Bir sonraki sefer 🎒 OKUL LİSTESİ ekranından tek tıkla sepete eklenir.', parent=dlg_parent())

    def photo():
        from tkinter import filedialog
        fp = filedialog.askopenfilename(parent=w, title='Liste fotoğrafını veya PDF dosyasını seçin', filetypes=[('Fotoğraf ve PDF', '*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.pdf'), ('PDF', '*.pdf'), ('Fotoğraf', '*.jpg *.jpeg *.png *.bmp *.tif *.tiff'), ('Tüm dosyalar', '*.*')])
        if not fp:
            return
        is_pdf = str(fp).lower().endswith('.pdf')

        def work(report):
            if is_pdf:
                report('PDF okunuyor…')
                text, err, scanned = pdf_list_text(fp)
                if text:
                    return text, '', 'dijital'                      # yazı katmanından birebir: OCR hatası olmaz
                if not scanned:
                    return '', err, ''
                report('PDF taranmış / fotoğraf: Windows okuma motoru okuyor…\n(sayfa sayısına göre 10-60 saniye sürebilir)')
                try:
                    import ALFABE_BELGE_ALIS as BA
                    words, err2 = BA.ocr_words(fp)
                    text = BA.words_to_text(words) if words else ''
                except Exception as ex:
                    text, err2 = '', str(ex)
                if text:
                    report('Yazı okundu, satırlar düzenleniyor…')
                    return join_ocr_lines(text), '', 'taranmis'
                return '', (err2 or 'PDF içinde okunabilir yazı bulunamadı.'), ''
            report('Windows okuma motoru fotoğraftaki yazıyı okuyor…\n(büyük fotoğraflarda 10-20 saniye sürebilir)')
            try:
                import ALFABE_FIS_OKU as FO
                text, err = FO.ocr_image(fp, upscale=True)
            except Exception as ex:
                text, err = '', str(ex)
            if text:
                report('Yazı okundu, satırlar düzenleniyor…')
                text = join_ocr_lines(text)
            elif err:
                err = err.replace('Fişte', 'Fotoğrafta')
            return text, err, 'foto'

        def done(res, err):
            text, e2, kind = (tuple(res) + ('', '', ''))[:3] if res else ('', str(err or ''), '')
            if not text:
                front()
                return messagebox.showwarning('Liste', (e2 or ('PDF\'ten yazı okunamadı.' if is_pdf else 'Fotoğraftan yazı okunamadı.')) + '\n\nListeyi elle yazabilir ya da yapıştırabilirsiniz.', parent=dlg_parent())
            txt.delete('1.0', 'end')
            txt.insert('1.0', text)
            state['ocr_text'] = '' if kind == 'dijital' else fold(text)   # OCR harf düzeltmesi yalnız fotoğraf / taranmış PDF satırlarında denenir
            calc()
            if kind == 'dijital':
                msg = 'PDF okundu (yazı katmanından birebir; okuma hatası olmaz). Yine de metni ve tablodaki satırları kontrol edin; gerekirse düzeltip tekrar HESAPLA\'ya basın.'
            elif kind == 'taranmis':
                msg = 'Taranmış PDF okundu. Okuma hatası olabilir; yukarıdaki metni ve tablodaki satırları kontrol edin, gerekirse düzeltip tekrar HESAPLA\'ya basın.'
            else:
                msg = 'Fotoğraf okundu. Okuma hatası olabilir; yukarıdaki metni ve tablodaki satırları kontrol edin, gerekirse düzeltip tekrar HESAPLA\'ya basın.'
            if state.get('sync'):
                messagebox.showinfo('Liste', msg, parent=dlg_parent())
            else:
                w.after(300, lambda: messagebox.showinfo('Liste', msg, parent=dlg_parent()))
        run_with_progress(w, 'PDF okunuyor' if is_pdf else 'Fotoğraf okunuyor', work, done, sync=state.get('sync', False))

    def clear_all():
        """Kayıttan sonra ekranı boşaltır: liste kutusu, satırlar, müşteri/telefon, iskonto-kapora-teslim-not, geri al geçmişi."""
        txt.delete('1.0', 'end')
        state['rows'].clear()
        state.pop('job_id', None)
        state['undo'] = []
        state['tier'] = 'eslesen'
        custv.set('')
        telv.set('')
        try:
            set_opt(None)
        except Exception:
            pass
        refresh()

    def cur_job_save(new=False):
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce listeyi HESAPLA ile fiyatlayın.', parent=dlg_parent())
        if not custv.get().strip():
            return messagebox.showinfo('Liste', 'Müşteri adını yazın (üstteki Müşteri kutusu).', parent=dlg_parent())
        import time as _time
        nm, tel, raw = custv.get(), telv.get(), txt.get('1.0', 'end').strip()
        rows_, tier_, kind_, opt_ = list(state['rows']), state['tier'], kindv.get(), opt()
        jid0 = None if new else state.get('job_id')

        def work(report):
            report('1/3  Liste hazırlanıyor…', 0.1)
            _time.sleep(0.3)
            con = connection_factory()
            try:
                report('2/3  Veritabanına kaydediliyor…', 0.45)
                jid = save_job(con, nm, tel, '', raw, rows_, tier_, kind_, jid0, opt_)
                _time.sleep(0.3)
                report('3/3  Kayıt doğrulanıyor…', 0.8)
                ok_ = [j for j in list_jobs(con, '', False) if j['id'] == jid]
                if not ok_ or ok_[0]['n'] != len(rows_):
                    raise RuntimeError('Kayıt doğrulanamadı.')
                _time.sleep(0.25)
                report('✔ Tamam', 1.0)
                return jid
            finally:
                con.close()

        def done(jid, err):
            front()
            if err is not None:
                return messagebox.showwarning('Liste', 'Kaydedilemedi: ' + str(err), parent=dlg_parent())
            state['job_id'] = jid
            info.config(text=f"'{nm.strip()}' listesi kaydedildi. 'KAYITLI LİSTELER' düğmesinden tekrar açabilirsiniz.")
            messagebox.showinfo('Liste', f"✔ KAYDEDİLDİ\n\nMüşteri: {nm.strip()}\nKalem: {len(rows_)}\n\n'KAYITLI LİSTELER' düğmesinden tekrar açabilirsiniz.\nListe temizlendi; yeni müşteriye geçebilirsiniz.", parent=dlg_parent())
            clear_all()
            info.config(text=f"'{nm.strip()}' listesi kaydedildi ve ekran temizlendi. 'KAYITLI LİSTELER' düğmesinden tekrar açabilirsiniz.")
        run_with_progress(w, 'Müşteri listesi kaydediliyor', work, done, min_seconds=1.3, sync=state.get('sync', False))

    def open_job(jid):
        stock[:] = db(load_stock)
        j = db(lambda con: load_job(con, jid, stock))
        kindv.set(j['kind'])
        apply_price_kind(stock, {'Toptan': 'toptan', 'Özel': 'ozel'}.get(j['kind'], 'perakende'))
        j = db(lambda con: load_job(con, jid, stock))
        custv.set(j['customer'])
        telv.set(j['phone'])
        txt.delete('1.0', 'end')
        txt.insert('1.0', j['raw_text'])
        state['rows'] = j['rows']
        state['tier'] = j['tier']
        state['job_id'] = jid
        set_opt(j.get('opt'))
        refresh()
        info.config(text=f"'{j['customer']}' listesi açıldı ({'TESLİM EDİLDİ' if j['status'] != 'bekliyor' else 'bekliyor'}). Fiyatlar güncel stoktan alındı.")

    def jobs_window():
        d = tk.Toplevel(w)
        d.title('Kayıtlı müşteri listeleri')
        d.geometry('900x560')
        try:
            d.transient(dlg_parent())
            d.lift()
            d.focus_force()
        except Exception:
            pass
        sv, ov = tk.StringVar(), tk.IntVar(value=1)
        f0 = ttk.Frame(d, padding=8)
        f0.pack(fill='x')
        ttk.Label(f0, text='Ara (ad / telefon):').pack(side='left')
        ttk.Entry(f0, textvariable=sv, width=28).pack(side='left', padx=6)
        ttk.Checkbutton(f0, text='Sadece bekleyenler', variable=ov, command=lambda: fill()).pack(side='left', padx=10)
        jt = ttk.Treeview(d, columns=('c', 't', 'd', 'n', 's', 'tz', 'kp'), show='headings', selectmode='browse')
        for k, h, wd, an in (('c', 'Müşteri', 300, 'w'), ('t', 'Telefon', 120, 'w'), ('d', 'Son işlem', 140, 'w'), ('n', 'Kalem', 60, 'e'), ('s', 'Durum', 110, 'w'), ('tz', 'Teslim tarihi', 100, 'w'), ('kp', 'Kapora', 80, 'e')):
            jt.heading(k, text=h)
            jt.column(k, width=wd, anchor=an)
        jt.pack(fill='both', expand=True, padx=8)

        def fill(*_):
            jt.delete(*jt.get_children())
            for j in db(lambda con: list_jobs(con, sv.get(), bool(ov.get()))):
                jt.insert('', 'end', iid=str(j['id']), values=(j['customer'], j['phone'], j['updated'], j['n'], 'bekliyor' if j['status'] == 'bekliyor' else 'TESLİM EDİLDİ', j.get('teslim') or '', f"{j['kapora']:g}" if j.get('kapora') else ''))

        def sel():
            s_ = jt.selection()
            return int(s_[0]) if s_ else None

        def op(*_):
            i = sel()
            if i is None:
                return
            try:
                open_job(i)
            except Exception as ex:
                return messagebox.showwarning('Liste', 'Liste açılamadı: ' + str(ex), parent=d)
            d.destroy()
            front()

        def done():
            i = sel()
            if i is None:
                return
            cur_ = jt.item(str(i), 'values')[4]
            db(lambda con: set_job_status(con, i, 'teslim' if cur_ == 'bekliyor' else 'bekliyor'))
            fill()

        def rm():
            i = sel()
            if i is None or not messagebox.askyesno('Liste', 'Seçili müşteri ve listesi programdan KALICI olarak silinsin mi?', parent=d):
                return
            db(lambda con: delete_job(con, i))
            fill()
        def clean_done():
            n = len([1 for j in db(lambda con: list_jobs(con, '', False)) if j['status'] != 'bekliyor'])
            if not n:
                return messagebox.showinfo('Liste', 'Teslim edilmiş kayıt yok.', parent=d)
            if not messagebox.askyesno('Liste', f'{n} teslim edilmiş müşteri listesi kalıcı olarak silinsin mi?', parent=d):
                return
            db(lambda con: [delete_job(con, j['id']) for j in list_jobs(con, '', False) if j['status'] != 'bekliyor'])
            fill()

        def click_open(ev):
            if jt.identify_region(ev.x, ev.y) == 'cell' and jt.identify_row(ev.y):
                jt.selection_set(jt.identify_row(ev.y))
                d.after(60, op)

        def print_it():
            i = sel()
            if i is None:
                return messagebox.showinfo('Liste', 'Önce bir müşteri seçin.', parent=d)
            try:
                open_job(i)
            except Exception as ex:
                return messagebox.showwarning('Liste', 'Liste açılamadı: ' + str(ex), parent=d)
            d.destroy()
            front()
            do_print(False)
        sv.trace_add('write', fill)
        jt.bind('<ButtonRelease-1>', click_open)
        f1 = ttk.Frame(d, padding=8)
        f1.pack(fill='x')
        ttk.Label(f1, text='Müşteriye tıklayınca liste açılır.').pack(side='left', padx=(0, 12))
        ttk.Button(f1, text='AÇ', command=op).pack(side='left')
        ttk.Button(f1, text='ÇIKTI AL', command=print_it).pack(side='left', padx=6)
        ttk.Button(f1, text='TESLİM EDİLDİ / BEKLİYOR', command=done).pack(side='left')
        ttk.Button(f1, text='SİL', command=rm).pack(side='left', padx=6)
        ttk.Button(f1, text='TESLİM EDİLENLERİ TEMİZLE', command=clean_done).pack(side='left')
        fill()
        d.w_ctl = dict(fill=fill, jt=jt, sv=sv, ov=ov, rm=rm, clean_done=clean_done, op=op, sel=sel)
        return d

    ttk.Button(bt, text='📷 FOTOĞRAF / PDF OKU', command=keep(photo)).pack(side='left', padx=(0, 12))
    ttk.Button(bt, text='HESAPLA', command=keep(calc)).pack(side='left')
    ttk.Button(bt, text='TEMİZLE', command=keep(lambda: (txt.delete('1.0', 'end'), state['rows'].clear(), state.pop('job_id', None), custv.set(''), telv.set(''), refresh()))).pack(side='left', padx=6)
    ttk.Button(bt_gizli, text='💾 MÜŞTERİYE KAYDET', command=keep(lambda: cur_job_save(False))).pack(side='left', padx=(18, 6))
    ttk.Button(bt_gizli, text='📂 KAYITLI LİSTELER', command=keep(jobs_window)).pack(side='left')

    def del_current():
        jid = state.get('job_id')
        if not jid:
            return messagebox.showinfo('Liste', 'Silinecek kayıtlı liste yok (bu liste henüz kaydedilmemiş).', parent=dlg_parent())
        if not messagebox.askyesno('Liste', f"'{custv.get().strip()}' müşterisi ve listesi programdan KALICI olarak silinsin mi?", parent=dlg_parent()):
            return
        db(lambda con: delete_job(con, jid))
        state.pop('job_id', None)
        txt.delete('1.0', 'end')
        state['rows'].clear()
        custv.set('')
        telv.set('')
        refresh()
        info.config(text='Kayıt silindi.')
    ttk.Button(bt_gizli, text='🗑 KAYDI SİL', command=keep(del_current)).pack(side='left', padx=6)
    def step(delta):
        i = cur()
        if i is None:
            return messagebox.showinfo('Liste', 'Önce bir satır seçin.', parent=dlg_parent())
        snap()
        if not step_row(state['rows'][i], delta):
            state['undo'].pop()
            info.config(text='Bu satır için daha ' + ('pahalı' if delta > 0 else 'ucuz') + ' bir seçenek yok.')
            return
        refresh()
        tv.selection_set(str(i))

    def merge_dups():
        n = len(find_duplicates(state['rows']))
        if not n:
            return messagebox.showinfo('Liste', 'Aynı ürünü gösteren birden fazla satır yok.', parent=dlg_parent())
        snap()
        k = merge_duplicates(state['rows'])
        refresh()
        info.config(text=f'{k} tekrar eden satır birleştirildi (adetler toplandı). Geri almak için ↶ GERİ AL.')

    def miss_window():
        ms = missing_rows(state['rows'])
        if not ms:
            return messagebox.showinfo('Liste', 'Eksik ürün yok — tüm satırlar eşleşti.', parent=dlg_parent())
        d = tk.Toplevel(w)
        d.title(f'Eksik ürünler ({len(ms)})')
        d.geometry('560x420')
        d.transient(dlg_parent())
        ttk.Label(d, text='Stokta bulunamayan satırlar (tedarikçiye sipariş / müşteriye bilgi için):', font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=8, pady=6)
        t = tk.Text(d, font=('Segoe UI', 11), height=14)
        t.pack(fill='both', expand=True, padx=8)
        t.insert('1.0', missing_text(state['rows']))
        t.config(state='disabled')
        f = ttk.Frame(d, padding=8)
        f.pack(fill='x')

        def cp():
            w.clipboard_clear()
            w.clipboard_append(missing_text(state['rows']))
            info.config(text='Eksik ürün listesi panoya kopyalandı.')

        def pr():
            try:
                print_html(w, build_missing_html(state['rows'], custv.get().strip(), telv.get().strip()))
            except Exception as ex:
                messagebox.showwarning('Liste', 'Yazdırma açılamadı: ' + str(ex), parent=d)
        ttk.Button(f, text='YAZDIR', command=pr).pack(side='left')
        ttk.Button(f, text='PANOYA KOPYALA', command=cp).pack(side='left', padx=6)
        if on_missing:
            ttk.Button(f, text='SİPARİŞ LİSTESİNE AKTAR', command=lambda: (on_missing([(r['raw'], r['qty']) for r in ms]), info.config(text='Eksik ürünler sipariş listesine aktarıldı.'))).pack(side='left')
        ttk.Button(f, text='KAPAT', command=d.destroy).pack(side='right')
        state['miss_win'] = d

    def ready_msg():
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce listeyi hesaplayın.', parent=dlg_parent())
        m = ready_message(custv.get(), state['rows'], state['tier'], opt())
        w.clipboard_clear()
        w.clipboard_append(m)
        info.config(text='Mesaj panoya kopyalandı (WhatsApp\'a yapıştırın): ' + m)

    def season_window():
        top_, mm_ = db(lambda con: season_stats(con, stock))
        d = tk.Toplevel(w)
        d.title('Sezon raporu — kayıtlı müşteri listelerinden')
        d.geometry('820x560')
        d.transient(dlg_parent())
        ttk.Label(d, text='Programda kayıtlı (silinmemiş) müşteri listelerine göre:', foreground='#555').pack(anchor='w', padx=8, pady=6)
        nb = ttk.Notebook(d)
        nb.pack(fill='both', expand=True, padx=8)
        t1 = ttk.Treeview(nb, columns=('a', 'q', 'n'), show='headings')
        t2 = ttk.Treeview(nb, columns=('a', 'q', 'n'), show='headings')
        for tr_, hd in ((t1, 'Ürün'), (t2, 'Listedeki satır (bulunamadı)')):
            for k, h, wd, an in (('a', hd, 460, 'w'), ('q', 'Toplam adet', 110, 'e'), ('n', 'Kaç listede', 100, 'e')):
                tr_.heading(k, text=h)
                tr_.column(k, width=wd, anchor=an)
        for a_, q_, n_ in top_:
            t1.insert('', 'end', values=(a_, f'{q_:g}', n_))
        for a_, q_, n_ in mm_:
            t2.insert('', 'end', values=(a_, f'{q_:g}', n_))
        nb.add(t1, text=f'En çok istenenler ({len(top_)})')
        nb.add(t2, text=f'Bulunamayanlar ({len(mm_)})')
        f = ttk.Frame(d, padding=8)
        f.pack(fill='x')
        if on_missing:
            ttk.Button(f, text='BULUNAMAYANLARI SATIN ALMA LİSTESİNE AKTAR', command=lambda: (on_missing([(a_, q_) for a_, q_, n_ in mm_]), info.config(text='Aktarıldı.'))).pack(side='left')
        ttk.Button(f, text='KAPAT', command=d.destroy).pack(side='right')
        state['season'] = dict(top=top_, miss=mm_, win=d, t1=t1, t2=t2)

    # Düzenleme düğmeleri (ürün ekle/seç/ucuz/pahalı/adet/çıkar/geri al/birleştir/eksik) ana ekranda değil, 📝 LİSTEYİ DÜZENLE (TAM EKRAN) penceresindedir.
    paperv = tk.StringVar(value='A4 (normal yazıcı)')
    modev = tk.StringVar(value='Fiyatlı liste')
    ttk.Label(bb2, text='Kâğıt/Yazıcı:').pack(side='right', padx=(6, 2))
    ttk.Combobox(bb2, textvariable=paperv, state='readonly', width=26, values=('A4 (normal yazıcı)', '80 mm Fiş yazıcı', '80 mm Barkod/Etiket yazıcı', 'Özel ebat (mm)')).pack(side='right')
    cwv, chv = tk.StringVar(value='100'), tk.StringVar(value='150')
    ttk.Entry(bb2, textvariable=chv, width=5).pack(side='right')
    ttk.Label(bb2, text='x').pack(side='right')
    ttk.Entry(bb2, textvariable=cwv, width=5).pack(side='right')
    ttk.Label(bb2, text='Özel mm (en x boy):').pack(side='right', padx=(8, 2))
    ttk.Combobox(bb2, textvariable=modev, state='readonly', width=16, values=('Fiyatlı liste', 'Hazırlık listesi')).pack(side='right', padx=(0, 10))
    ttk.Label(bb2, text='Çıktı:').pack(side='right', padx=(6, 2))
    paperv.trace_add('write', lambda *_: refresh())
    cwv.trace_add('write', lambda *_: refresh())
    chv.trace_add('write', lambda *_: refresh())
    modev.trace_add('write', lambda *_: refresh())

    def close_pv():
        pv_on['on'] = False
        w_ = PV.get('win')
        PV.clear()
        try:
            if w_ is not None and w_.winfo_exists():
                w_.destroy()
        except Exception:
            pass

    def open_pv():
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = min(sw - 120, 860), min(sh - 110, 940)
        d = tk.Toplevel(w)
        d.title('Ön izleme — gerçek sayfa')
        d.geometry('%dx%d+%d+%d' % (ww, wh, (sw - ww) // 2, max(0, (sh - wh) // 2 - 20)))     # ekranın tam ortasında
        d.configure(bg='#8b97a6')
        d.transient(dlg_parent())
        bar = ttk.Frame(d, padding=6)
        bar.pack(fill='x', side='top')
        ttk.Button(bar, text='🖨 YAZDIR', command=lambda: do_print(False)).pack(side='left')
        ttk.Button(bar, text='KAPAT', command=close_pv).pack(side='right')
        ttk.Label(bar, text='Ebat / çıktı türünü ana ekrandan değiştirebilirsiniz; bu pencere kendini yeniler.', foreground='#555').pack(side='left', padx=12)
        body = tk.Frame(d, bg='#8b97a6')
        body.pack(fill='both', expand=True)
        cv = tk.Canvas(body, bg='#8b97a6', highlightthickness=0)
        sbv = ttk.Scrollbar(body, orient='vertical', command=cv.yview)
        cv.configure(yscrollcommand=sbv.set)
        t = tk.Text(body, font=('Consolas', 10), bg='#fffef6', wrap='none', state='disabled')
        cv.bind('<MouseWheel>', lambda e=None: cv.yview_scroll(int(-e.delta / 120) * 2, 'units') if e is not None else None)
        d.bind('<Escape>', lambda e=None: close_pv())
        d.protocol('WM_DELETE_WINDOW', close_pv)
        PV.update(win=d, cv=cv, sb=sbv, t=t)
        pv_on['on'] = True
        t.pack(fill='both', expand=True)
        d.update_idletasks()
        try:
            d.lift()
        except Exception:
            pass

    def toggle_pv():
        if pv_on['on'] and PV:
            close_pv()
            return
        open_pv()
        refresh()
    ttk.Button(bb2, text='👁 ÖN İZLEME', command=keep(toggle_pv)).pack(side='left', padx=(0, 6))

    def open_designer():
        if not designer:
            return messagebox.showinfo('Liste', 'Rapor Tasarımcısı bu ortamda kullanılamıyor.', parent=dlg_parent())
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce listeyi hesaplayın; tasarım tablo sütunlarına göre açılır.', parent=dlg_parent())
        pap = cur_pap()
        key, heads, out, total, period = report_spec(state['rows'], state['tier'], False, opt(), modev.get().startswith('Haz'), pap['kind'] != 'a4', custv.get().strip(), telv.get().strip(), kindv.get())
        designer(key, heads, out, lambda: (schedule_preview() if pv_on['on'] else None))
    ttk.Button(bb2, text='🎨 RAPOR TASARIMI', command=keep(open_designer)).pack(side='left', padx=(0, 6))

    def cur_pap(force_a4=False):
        if force_a4:
            return paper_of('A4', 0)
        return paper_of(paperv.get(), len(state['rows']), _num(cwv.get()) or 100, _num(chv.get()) or 150, state['rows'])

    def out_html(compare, prep, force_a4=False):
        return final_html(render, state['rows'], state['tier'], compare, opt(), prep, cur_pap(force_a4), custv.get().strip(), telv.get().strip(), kindv.get())

    def do_print(compare):
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce HESAPLA ile listeyi fiyatlayın.', parent=dlg_parent())
        pv = paperv.get()
        prep = modev.get().startswith('Haz')
        h = out_html(compare, prep)
        close_pv()                      # yazdırırken program içi yazdırma ön izlemesi açılır; ikisi üst üste durmasın
        state['last_html'] = h
        try:
            if printer_suggest:
                printer_suggest('Fiş Yazıcı' if 'Fiş' in pv else ('Etiket Yazıcı' if 'Barkod' in pv else 'A4/Rapor Yazıcı'))
            print_html(w, h)
        except Exception as ex:
            messagebox.showwarning('Liste', 'Yazdırma açılamadı: ' + str(ex), parent=dlg_parent())
    ttk.Button(bb2, text='YAZDIR', command=lambda: do_print(False)).pack(side='left', padx=(12, 6))
    ttk.Button(bb2, text='3 SEÇENEĞİ YAZDIR', command=lambda: do_print(True)).pack(side='left', padx=(0, 6))
    def _fname(base, compare, sep):
        nm = re.sub(r'[^0-9A-Za-zÇĞİÖŞÜçğıöşü _-]+', '', custv.get()).strip().replace(' ', sep)
        return base + (sep + nm if nm else '') + ((sep + '3' + sep + 'Secenek') if compare else '')

    def do_excel(compare):
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce HESAPLA ile listeyi fiyatlayın.', parent=dlg_parent())
        if not export_excel:
            return messagebox.showinfo('Liste', 'Excel çıktısı bu ortamda kullanılamıyor.', parent=dlg_parent())
        h, rr, tt = export_table(state['rows'], state['tier'], compare, opt())
        try:
            p = export_excel(_fname('Fiyat Teklifi', compare, ' '), h, rr, tt)
        except Exception as ex:
            return messagebox.showwarning('Liste', 'Excel oluşturulamadı: ' + str(ex), parent=dlg_parent())
        if p:
            info.config(text='Excel kaydedildi: ' + str(p) + '  (müşteriye WhatsApp/e-posta ile gönderebilirsiniz)')

    def do_pdf(compare):
        if not state['rows']:
            return messagebox.showinfo('Liste', 'Önce HESAPLA ile listeyi fiyatlayın.', parent=dlg_parent())
        if not export_pdf:
            return messagebox.showinfo('Liste', 'PDF çıktısı bu ortamda kullanılamıyor.', parent=dlg_parent())
        h = out_html(compare, False, force_a4=True)
        try:
            p = export_pdf(h, _fname('Fiyat_Teklifi', compare, '_'))
        except Exception as ex:
            return messagebox.showwarning('Liste', 'PDF oluşturulamadı: ' + str(ex), parent=dlg_parent())
        if p:
            info.config(text='PDF kaydedildi: ' + str(p) + '  (müşteriye WhatsApp/e-posta ile gönderebilirsiniz)')
    ttk.Button(bb2, text='PDF KAYDET', command=keep(lambda: do_pdf(False))).pack(side='left', padx=(6, 6))
    ttk.Button(bb2, text='EXCEL KAYDET', command=keep(lambda: do_excel(False))).pack(side='left', padx=(0, 6))
    ttk.Button(bb2, text='PDF (3 SEÇENEK)', command=keep(lambda: do_pdf(True))).pack(side='left', padx=(0, 6))
    ttk.Button(bb2, text='EXCEL (3 SEÇENEK)', command=keep(lambda: do_excel(True))).pack(side='left', padx=(0, 6))
    ttk.Button(bb, text='PANOYA KOPYALA', command=keep(copy_txt)).pack(side='left', padx=(6, 6))
    ttk.Button(bb, text='✔ "HAZIR" MESAJI', command=keep(ready_msg)).pack(side='left', padx=(0, 6))
    ttk.Button(bt_gizli, text='📊 SEZON RAPORU', command=keep(season_window)).pack(side='left', padx=(12, 0))
    tk.Button(bt, text='📝 LİSTEYİ DÜZENLE (TAM EKRAN)', font=('Segoe UI', 10, 'bold'), bg='#0a6b2d', fg='white', relief='raised', padx=10, command=keep(edit_window)).pack(side='left', padx=(12, 0))
    ttk.Button(bb, text='OKUL LİSTESİ OLARAK KAYDET', command=keep(save_list)).pack(side='left', padx=6)
    if on_add:
        tk.Button(bb, text='SEPETE EKLE', command=keep(to_cart), bg='#0f8a5f', fg='white', font=('Segoe UI', 11, 'bold'), padx=18, pady=4, cursor='hand2').pack(side='right')
    ed = {'e': None}

    def end_edit(save=True):
        e = ed['e']
        if e is None:
            return
        ed['e'] = None
        i, col = e._i, e._col
        v = e.get()
        e.destroy()
        if not save or i >= len(state['rows']):
            return
        r = state['rows'][i]
        try:
            x = float(str(v).replace('₺', '').replace(' ', '').replace(',', '.'))
        except ValueError:
            return
        snap()
        if col == 'q':
            if x > 0:
                r['qty'] = x
        elif r.get('stock') is not None or True:
            if x >= 0:
                r['unit'] = x
        refresh()
        tv.selection_set(str(i))
        front()

    def start_edit(ev):
        end_edit(True)
        if tv.identify_region(ev.x, ev.y) != 'cell':
            return
        col = tv.identify_column(ev.x)
        key = {'#3': 'q', '#5': 'p'}.get(col)
        iid = tv.identify_row(ev.y)
        if not key or not iid:
            return
        i = int(iid)
        r = state['rows'][i]
        if key == 'p' and r.get('stock') is None:
            return
        bx = tv.bbox(iid, col)
        if not bx:
            return
        cur = r['qty'] if key == 'q' else unit(r, r['stock'])
        e = tk.Entry(tv, justify='right', font=('Segoe UI', 10))
        e._i, e._col = i, key
        e.insert(0, f'{cur:g}' if key == 'q' else f'{cur:.2f}')
        e.place(x=bx[0], y=bx[1], width=bx[2], height=bx[3])
        e.focus_set()
        e.select_range(0, 'end')
        e.bind('<Return>', lambda _e: end_edit(True))
        e.bind('<KP_Enter>', lambda _e: end_edit(True))
        e.bind('<Escape>', lambda _e: end_edit(False))
        e.bind('<FocusOut>', lambda _e: end_edit(True))
        ed['e'] = e
        tv.selection_set(iid)
        return 'break'

    def dbl(ev):
        if tv.identify_column(ev.x) in ('#3', '#5'):
            return 'break'
        pick()
    tv.bind('<Button-1>', start_edit, add='+')
    tv.bind('<Double-1>', dbl)
    txt.focus_set()
    w.ctl = dict(edit_window=edit_window, split_row=split_row, add_product=add_product, add_row=add_row, preview_now=preview_now, PV=PV, pv_state=pv_state, close_pv=close_pv, open_designer=open_designer, cwv=cwv, chv=chv, out_html=out_html, cur_pap=cur_pap, undo=undo, step=step, merge_dups=merge_dups, miss_window=miss_window, ready_msg=ready_msg, season_window=season_window, toggle_pv=toggle_pv, pv_on=pv_on, discm=discm, discval=discval, kapv=kapv, teslimv=teslimv, notev=notev, opt=opt, set_opt=set_opt, tot=tot, set_sync=lambda v=True: state.__setitem__('sync', v), front=front, del_current=del_current, paperv=paperv, modev=modev, custv=custv, telv=telv, photo=photo, cur_job_save=cur_job_save, open_job=open_job, jobs_window=jobs_window, start_edit=start_edit, end_edit=end_edit, ed=ed, do_pdf=do_pdf, do_excel=do_excel, do_print=do_print, set_tier=set_tier, kindv=kindv, kind_changed=kind_changed, txt=txt, tv=tv, calc=calc, state=state, pick=pick, to_cart=to_cart, refresh=refresh)
    return w
