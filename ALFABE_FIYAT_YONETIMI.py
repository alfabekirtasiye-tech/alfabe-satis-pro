# -*- coding: utf-8 -*-
"""ALFABE Fiyat Yönetimi yardımcıları (v1.2.291.295).

Arayüzden bağımsız, sqlite3 bağlantısıyla çalışan iş mantığı:
  * satır sınıflandırma (maliyet altı / kârsız / maliyeti yok / fiyatı yok)
  * son alış tarihleri
  * Excel/CSV'den fiyat yükleme planı ve uygulaması (fiyat geçmişi kaydeder)
  * değişiklik özeti
Hiçbir fonksiyon kullanıcı onayı olmadan veri yazmaz; yazan tek fonksiyon apply_changes'tır.
"""
import datetime
import sqlite3

import ALFABE_STOK_EXCEL as XL

PRICE_FIELDS = (('retail', 'Perakende'), ('wholesale', 'Toptan'), ('special_price', 'Özel'))
PRICE_LABEL = dict(PRICE_FIELDS)

# Excel başlığı eşanlamlıları (XL.norm ile normalleştirilmiş)
_SYN = {
    'barcode': {'barkod', 'barkodno', 'barcode', 'ean'},
    'code': {'stokkodu', 'kod', 'urunkodu', 'stokkod', 'code'},
    'retail': {'perakende', 'perakendefiyat', 'perakendesatis', 'satisfiyati', 'satisfiyat', 'fiyat'},
    'wholesale': {'toptan', 'toptanfiyat', 'toptansatis'},
    'special_price': {'ozel', 'ozelfiyat', 'ozelsatis'},
}


def _dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def classify(retail, avg_cost, last_cost=0.0):
    """Satır durumu: 'none' (fiyat yok) / 'below' (maliyet altı) / 'nopro' (kârsız) / 'nocost' (maliyet yok) / ''."""
    retail = float(retail or 0)
    cost = float(avg_cost or 0)
    if retail <= 0:
        return 'none'
    if cost <= 0:
        return 'nocost'
    if retail < cost:
        return 'below'
    if abs(retail - cost) < 0.005:
        return 'nopro'
    return ''


def last_purchase_dates(c):
    """{stock_id: son kesinleşmiş alış tarihi (ISO)}"""
    try:
        cur = c.execute("SELECT h.stock_id AS sid, MAX(h.dt) AS dt FROM supplier_price_history h "
                        "JOIN purchases p ON p.id=h.purchase_id WHERE p.status='Kesinleşti' GROUP BY h.stock_id")
        return {r['sid']: r['dt'] for r in _dicts(cur)}
    except Exception:
        return {}


def match_columns(headers):
    """{alan: sütun indeksi}"""
    hn = [XL.norm(h) for h in headers]
    out = {}
    for key, syns in _SYN.items():
        for i, h in enumerate(hn):
            if h and h in syns and i not in out.values():
                out[key] = i
                break
    return out


def build_import_plan(c, headers, rows):
    """rows: XL.read_table çıktısı [(excel_satir_no, [hücreler])].
    Döner: {'changes':[...], 'errors':[(satir,mesaj)], 'unchanged':n, 'matched':n, 'cols':[alan..]}"""
    m = match_columns(headers)
    price_cols = [k for k, _ in PRICE_FIELDS if k in m]
    plan = {'changes': [], 'errors': [], 'unchanged': 0, 'matched': 0, 'cols': price_cols}
    if 'barcode' not in m and 'code' not in m:
        plan['errors'].append((0, 'Barkod veya Stok Kodu sütunu bulunamadı.'))
        return plan
    if not price_cols:
        plan['errors'].append((0, 'Perakende / Toptan / Özel fiyat sütunu bulunamadı.'))
        return plan
    stocks = _dicts(c.execute('SELECT id,barcode,code,name,retail,wholesale,special_price FROM stock WHERE active=1'))
    by_bar = {XL.id_text(s['barcode']): s for s in stocks if XL.id_text(s['barcode'])}
    by_code = {XL.id_text(s['code']).casefold(): s for s in stocks if XL.id_text(s['code'])}
    seen = set()
    for rno, cells in rows:
        def cell(k):
            i = m.get(k)
            return cells[i] if i is not None and i < len(cells) else None
        bar = XL.id_text(cell('barcode')) if 'barcode' in m else ''
        code = XL.id_text(cell('code')).casefold() if 'code' in m else ''
        s = by_bar.get(bar) if bar else None
        if s is None and code:
            s = by_code.get(code)
        if s is None:
            plan['errors'].append((rno, 'Ürün bulunamadı: ' + (bar or code or '(boş)')))
            continue
        if s['id'] in seen:
            plan['errors'].append((rno, 'Aynı ürün dosyada birden fazla: ' + str(s['name'])))
            continue
        seen.add(s['id'])
        plan['matched'] += 1
        changed = False
        for k in price_cols:
            raw = cell(k)
            try:
                v = XL.parse_num(raw)
            except Exception:
                plan['errors'].append((rno, '%s fiyatı sayı değil: %r' % (PRICE_LABEL[k], raw)))
                continue
            if v is None:
                continue
            if v < 0:
                plan['errors'].append((rno, '%s fiyatı negatif olamaz.' % PRICE_LABEL[k]))
                continue
            v = round(v, 2)
            old = round(float(s[k] or 0), 2)
            if abs(v - old) < 0.005:
                continue
            plan['changes'].append({'id': s['id'], 'name': s['name'], 'key': k, 'old': old, 'new': v})
            changed = True
        if not changed:
            plan['unchanged'] += 1
    return plan


_UNIT_PRICE_COL = {'retail': 'retail', 'wholesale': 'wholesale', 'special_price': 'special_price'}


def scale_unit_prices(c, stock_id, key, old, new):
    """v461.57: adet fiyatı değişince, o ürünün kutu/koli gibi birimlerinde elle girilmiş (0'dan büyük) aynı tür fiyatlar da
    aynı oranda değişir (kutu fiyatı adet fiyatından kopmasın). Fiyatı boş olan birimler zaten adet fiyatı x adetten hesaplanır."""
    col = _UNIT_PRICE_COL.get(key)
    try:
        old = float(old or 0)
        new = float(new or 0)
        if not col or old <= 0 or new < 0 or abs(new - old) < 1e-9:
            return 0
        f = new / old
        cur = c.execute('UPDATE stock_unit_conversions SET %s=ROUND(%s*?,2) WHERE stock_id=? AND %s>0' % (col, col, col), (f, stock_id))
        return cur.rowcount
    except sqlite3.Error:
        return 0


def apply_changes(c, changes, reason, user='Yönetici', batch_prefix='FX-'):
    """changes: [{'id','key','old','new',...}] — tek işlemde, eski fiyat hâlâ geçerliyse uygular.
    Hata olursa hiçbir şey yazılmaz (ValueError). Döner: batch_id"""
    batch = batch_prefix + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    keys = {k for k, _ in PRICE_FIELDS}
    try:
        c.execute('BEGIN IMMEDIATE')
        for ch in changes:
            k = ch['key']
            if k not in keys:
                raise ValueError('Geçersiz fiyat alanı: ' + str(k))
            row = c.execute('SELECT %s FROM stock WHERE id=? AND active=1' % k, (ch['id'],)).fetchone()
            if row is None or abs(float(row[0] or 0) - float(ch['old'])) > 0.000001:
                raise ValueError('Fiyatlar önizlemeden sonra değişmiş (%s). İşlem iptal edildi.' % ch.get('name', ch['id']))
            if abs(float(ch['new']) - float(ch['old'])) < 1e-9:
                continue
            c.execute('INSERT INTO price_history(stock_id,dt,price_type,old_price,new_price,user,reason,batch_id) VALUES(?,?,?,?,?,?,?,?)',
                      (ch['id'], ts, PRICE_LABEL[k], ch['old'], ch['new'], user, reason, batch))
            if k == 'retail':
                c.execute('UPDATE stock SET previous_price=retail,retail=?,updated=? WHERE id=?', (ch['new'], ts, ch['id']))
            else:
                c.execute('UPDATE stock SET %s=?,updated=? WHERE id=?' % k, (ch['new'], ts, ch['id']))
            scale_unit_prices(c, ch['id'], k, ch['old'], ch['new'])
        c.commit()
    except Exception:
        c.rollback()
        raise
    return batch


def summarize(changes):
    """İnsan okunur değişiklik özeti (Türkçe) ve sayısal sonuçlar."""
    if not changes:
        return {'products': 0, 'text': 'Değişiklik yok.'}
    prods = {ch['id'] for ch in changes}
    lines = ['%d ürün, %d fiyat alanı güncellendi.' % (len(prods), len(changes))]
    res = {'products': len(prods), 'fields': len(changes)}
    for k, lab in PRICE_FIELDS:
        sub = [ch for ch in changes if ch['key'] == k]
        if not sub:
            continue
        pcts = [(ch['new'] - ch['old']) / ch['old'] * 100 for ch in sub if ch['old'] > 0]
        up = sum(1 for ch in sub if ch['new'] > ch['old'])
        down = sum(1 for ch in sub if ch['new'] < ch['old'])
        txt = '%s: %d ürün (%d artan, %d azalan)' % (lab, len(sub), up, down)
        if pcts:
            avg = sum(pcts) / len(pcts)
            txt += ', ortalama %s%%%s' % (('%+.1f' % avg).replace('.', ','), '')
            res[k + '_avg_pct'] = avg
        lines.append(txt)
    res['text'] = '\n'.join(lines)
    return res
