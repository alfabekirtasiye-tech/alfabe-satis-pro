# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - TOPLU STOK SAYIMI altyapısı (v1.2.291.454)

Ekrandan bağımsız, hızlı ve sınanabilir sayım mantığı. Ekran (ana dosya) yalnız bu sınıfı çağırır.

HIZ İÇİN TASARIM
- Katalog açılışta TEK sorguyla belleğe alınır; barkod / kod / ek barkod arama sözlükten O(1) yapılır (okutma başına veritabanı sorgusu yok).
- Her okutma TEK SQL deyimidir (INSERT .. ON CONFLICT DO UPDATE actual_qty=actual_qty+?), kalıcı bağlantıyla ve hemen commit edilir:
  elektrik kesilse bile okutulan son ürün kayıtlıdır.
- Kesinleştirme tek işlemde, toplu (executemany) çalışır; on binlerce satır saniyeler içinde biter.
Sayım, "fiilî miktar = fiziksel gerçek" ilkesiyle kesinleşir: stok miktarı sayılan miktara eşitlenir, fark ayrı hareket olarak yazılır.
"""
import re
import sqlite3
import time

SCOPES = ['Tüm Stok', 'Bölüm', 'Raf', 'Ürün Grubu', 'Depo']
_SCOPE_COL = {'Bölüm': 's.section', 'Raf': 's.shelf', 'Ürün Grubu': 's.grp', 'Depo': 's.warehouse'}
SCAN_MODES = ['Ekle (+1 / Adet kutusu)', 'Miktar sor', 'Üzerine yaz']
EPS = 0.000001


def ensure_schema(con):
    """Eski veritabanlarına yeni sayım sütunlarını / tablosunu güvenle ekler."""
    def cols(t):
        return {r[1] for r in con.execute('PRAGMA table_info(%s)' % t).fetchall()}
    c = cols('stock_counts')
    for name, typ in (('scope_kind', 'TEXT'), ('scope_value', 'TEXT'), ('blind', 'INTEGER DEFAULT 0'),
                      ('zero_uncounted', 'INTEGER DEFAULT 0'), ('summary', 'TEXT')):
        if name not in c:
            con.execute('ALTER TABLE stock_counts ADD COLUMN %s %s' % (name, typ))
    l = cols('stock_count_lines')
    for name, typ in (('recount', 'INTEGER DEFAULT 0'), ('scans', 'INTEGER DEFAULT 0'), ('last_scan', 'TEXT')):
        if name not in l:
            con.execute('ALTER TABLE stock_count_lines ADD COLUMN %s %s' % (name, typ))
    con.execute('CREATE TABLE IF NOT EXISTS stock_count_unknown(id INTEGER PRIMARY KEY,count_id INTEGER,barcode TEXT,qty REAL DEFAULT 0,dt TEXT,UNIQUE(count_id,barcode))')
    con.execute('CREATE INDEX IF NOT EXISTS ix_stock_count_unknown_count ON stock_count_unknown(count_id)')
    con.execute('CREATE INDEX IF NOT EXISTS ix_stock_section_shelf ON stock(section,shelf)')
    con.commit()


def parse_scan(text, default_qty=1.0):
    """'5*8690..' -> (5.0,'8690..') ; '8690..*5' -> (5.0,'8690..') ; düz barkod -> (default_qty, barkod).
    Barkodun kendisi sayısal olabileceğinden yalnız KISA (<=6 hane) taraf miktar sayılır."""
    t = str(text or '').strip()
    if '*' in t:
        a, _, b = t.partition('*')
        a, b = a.strip(), b.strip()

        def isq(x):
            return bool(re.fullmatch(r'-?\d{1,6}([.,]\d{1,3})?', x))
        if a and b and isq(a) and not (isq(b) and len(b) <= 6):
            return float(a.replace(',', '.')), b
        if a and b and isq(b):
            return float(b.replace(',', '.')), a
    return float(default_qty), t


def fmt(n):
    n = float(n or 0)
    return ('%g' % round(n, 6)).replace('.', ',') if abs(n - round(n)) > EPS else str(int(round(n)))


class Session:
    """Tek bir sayım belgesinin canlı oturumu."""

    def __init__(self, connect, cid, now):
        self.connect = connect
        self.cid = int(cid)
        self.now = now
        self.con = connect()
        ensure_schema(self.con)
        self.count = self.con.execute('SELECT * FROM stock_counts WHERE id=?', (self.cid,)).fetchone()
        self.blind = bool(self.count['blind']) if self.count else False
        self.editable = bool(self.count and self.count['status'] == 'Taslak')
        self.history = []                 # [(stock_id, eski_fiili | None, eski_tarama)]
        self.catalog = {}                 # stock_id -> dict
        self.keys = {}                    # arama anahtarı -> [stock_id,...]
        self.lines = {}                   # stock_id -> dict
        self.order = []                   # son okutulan en başta
        self.load()

    # ------------------------------------------------------------------ yükleme
    def load(self):
        t0 = time.time()
        self.catalog.clear()
        self.keys.clear()
        for r in self.con.execute('SELECT id,barcode,code,name,unit,qty,avg_cost,section,shelf,grp,warehouse,active,retail FROM stock'):
            d = {k: r[k] for k in r.keys()}
            self.catalog[d['id']] = d
            for k in (d['barcode'], d['code']):
                k = str(k or '').strip()
                if k:
                    self.keys.setdefault(k, []).append(d['id'])
                    self.keys.setdefault(k.casefold(), []).append(d['id'])
        for r in self.con.execute('SELECT stock_id,barcode FROM stock_barcodes WHERE active=1'):
            k = str(r['barcode'] or '').strip()
            if k:
                self.keys.setdefault(k, []).append(r['stock_id'])
        for k, v in list(self.keys.items()):
            self.keys[k] = list(dict.fromkeys(v))
        # v461.56: kutu/koli barkodu okutulunca sayım miktarı içerdiği adet kadar artar (stok adet cinsinden tutulur).
        self.unit_f = {}
        try:
            for r in self.con.execute("SELECT barcode,base_quantity FROM stock_unit_conversions WHERE TRIM(COALESCE(barcode,''))<>''"):
                if float(r['base_quantity'] or 0) > 0:
                    self.unit_f[str(r['barcode']).strip()] = float(r['base_quantity'])
        except sqlite3.Error:
            self.unit_f = {}
        self.lines.clear()
        self.order = []
        for r in self.con.execute('SELECT * FROM stock_count_lines WHERE count_id=? ORDER BY COALESCE(last_scan,\'\') DESC,id DESC', (self.cid,)):
            d = {k: r[k] for k in r.keys()}
            self.lines[d['stock_id']] = d
            self.order.append(d['stock_id'])
        self._scope_n = sum(1 for i in self.catalog if self.catalog[i]['active'] and self.in_scope(i))
        self._rebuild_agg()
        self.load_ms = (time.time() - t0) * 1000.0
        return self.load_ms

    # --- artımlı toplamlar: her okutmada O(1)
    def _rebuild_agg(self):
        self.agg = {'qty': 0.0, 'diff_lines': 0, 'over': 0.0, 'short': 0.0, 'recount': 0, 'in_scope': 0}
        for l in self.lines.values():
            self._contrib(l, 1)

    def _contrib(self, l, sign):
        a = self.agg
        a['qty'] += sign * float(l['actual_qty'] or 0)
        d = float(l['difference'] or 0)
        v = float(l['value_diff'] or 0)
        if abs(d) > EPS:
            a['diff_lines'] += sign
            if v >= 0:
                a['over'] += sign * v
            else:
                a['short'] += sign * v
        if l.get('recount'):
            a['recount'] += sign
        if self.in_scope(l['stock_id']):
            a['in_scope'] += sign

    def reload_catalog_qty(self):
        """Sayım sürerken satış yapıldıysa güncel sistem miktarlarını yeniler (satırların anlık görüntüsü değişmez)."""
        for r in self.con.execute('SELECT id,qty,avg_cost FROM stock'):
            if r['id'] in self.catalog:
                self.catalog[r['id']]['qty'] = r['qty']
                self.catalog[r['id']]['avg_cost'] = r['avg_cost']

    # ------------------------------------------------------------------ kapsam
    def in_scope(self, sid):
        k, v = (self.count['scope_kind'] if self.count else None), (self.count['scope_value'] if self.count else None)
        if not k or k == 'Tüm Stok':
            return True
        col = {'Bölüm': 'section', 'Raf': 'shelf', 'Ürün Grubu': 'grp', 'Depo': 'warehouse'}.get(k)
        return bool(col) and str(self.catalog.get(sid, {}).get(col) or '').strip().casefold() == str(v or '').strip().casefold()

    def scope_ids(self, only_active=True):
        return [i for i, d in self.catalog.items() if (d['active'] or not only_active) and self.in_scope(i)]

    def uncounted(self):
        return [i for i in self.scope_ids() if i not in self.lines]

    # ------------------------------------------------------------------ arama
    def unit_factor(self, term):
        """Okutulan barkod bir kutu/koli (birim) barkoduysa içerdiği adet, değilse 1."""
        return self.unit_f.get(str(term or '').strip(), 1.0)

    def resolve(self, term):
        """('ok', stock_id) | ('multi', [ids]) | ('none', None)"""
        t = str(term or '').strip()
        if not t:
            return 'none', None
        ids = self.keys.get(t) or self.keys.get(t.casefold())
        if not ids:
            return 'none', None
        act = [i for i in ids if self.catalog[i]['active']] or ids
        if len(act) == 1:
            return 'ok', act[0]
        return 'multi', act

    def search_name(self, text, limit=40):
        """Ada göre arama (barkod bulunamayınca seçim listesi için)."""
        terms = [x.casefold() for x in str(text or '').split() if x]
        if not terms:
            return []
        out = []
        for i, d in self.catalog.items():
            if not d['active']:
                continue
            h = (str(d['name'] or '') + ' ' + str(d['code'] or '')).casefold()
            if all(t in h for t in terms):
                out.append(i)
                if len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------ okutma
    def add(self, sid, qty, mode='add', commit=True):
        """mode: 'add' mevcut sayıma ekler, 'set' üzerine yazar. Tek SQL + commit. Satır sözlüğünü döndürür."""
        if not self.editable:
            raise ValueError('Kesinleşmiş sayım değiştirilemez.')
        s = self.catalog[sid]
        old = self.lines.get(sid)
        self.history.append((sid, old['actual_qty'] if old else None, old['scans'] if old else 0))
        if len(self.history) > 5000:
            del self.history[:1000]
        sysq = float(s['qty'] or 0)
        cost = float(s['avg_cost'] or 0)
        ts = self.now()
        qty = float(qty)
        if old is None or mode == 'set':
            actual = qty
        else:
            actual = float(old['actual_qty'] or 0) + qty
        if old is not None:
            sysq = float(old['system_qty'] or 0)       # ilk okutmadaki anlık görüntü korunur
        diff = round(actual - sysq, 6)
        self.con.execute(
            'INSERT INTO stock_count_lines(count_id,stock_id,barcode,system_qty,actual_qty,difference,unit_cost,value_diff,scans,last_scan) '
            'VALUES(?,?,?,?,?,?,?,?,1,?) ON CONFLICT(count_id,stock_id) DO UPDATE SET actual_qty=excluded.actual_qty,'
            'difference=excluded.difference,value_diff=excluded.value_diff,scans=scans+1,last_scan=excluded.last_scan',
            (self.cid, sid, s['barcode'] or '', sysq, actual, diff, cost, diff * cost, ts))
        self.con.execute('UPDATE stock_counts SET updated=? WHERE id=?', (ts, self.cid))
        if commit:
            self.con.commit()
        ln = {'stock_id': sid, 'system_qty': sysq, 'actual_qty': actual, 'difference': diff, 'unit_cost': cost, 'value_diff': diff * cost,
              'note': old['note'] if old else '', 'recount': old['recount'] if old else 0, 'scans': (old['scans'] if old else 0) + 1, 'last_scan': ts,
              'barcode': s['barcode'] or ''}
        if old is not None:
            self._contrib(old, -1)
        self.lines[sid] = ln
        self._contrib(ln, 1)
        if old is not None and self.order and self.order[0] == sid:
            pass
        else:
            if sid in self.lines and old is not None:
                try:
                    self.order.remove(sid)
                except ValueError:
                    pass
            self.order.insert(0, sid)
        return ln

    def undo(self):
        """Son okutmayı geri alır. Geri alınan stock_id döner (yoksa None)."""
        if not self.editable or not self.history:
            return None
        sid, old_actual, old_scans = self.history.pop()
        if old_actual is None:
            self.con.execute('DELETE FROM stock_count_lines WHERE count_id=? AND stock_id=?', (self.cid, sid))
            if sid in self.lines:
                self._contrib(self.lines[sid], -1)
            self.lines.pop(sid, None)
            if sid in self.order:
                self.order.remove(sid)
        else:
            ln = self.lines[sid]
            self._contrib(ln, -1)
            diff = round(old_actual - float(ln['system_qty'] or 0), 6)
            cost = float(ln['unit_cost'] or 0)
            self.con.execute('UPDATE stock_count_lines SET actual_qty=?,difference=?,value_diff=?,scans=? WHERE count_id=? AND stock_id=?',
                             (old_actual, diff, diff * cost, old_scans, self.cid, sid))
            ln.update(actual_qty=old_actual, difference=diff, value_diff=diff * cost, scans=old_scans)
            self._contrib(ln, 1)
        self.con.commit()
        return sid

    def delete_line(self, sid):
        if not self.editable or sid not in self.lines:
            return False
        self.con.execute('DELETE FROM stock_count_lines WHERE count_id=? AND stock_id=?', (self.cid, sid))
        self.con.commit()
        self._contrib(self.lines[sid], -1)
        self.lines.pop(sid, None)
        if sid in self.order:
            self.order.remove(sid)
        self.history = [h for h in self.history if h[0] != sid]
        return True

    def set_note(self, sid, note):
        if sid in self.lines and self.editable:
            self.con.execute('UPDATE stock_count_lines SET note=? WHERE count_id=? AND stock_id=?', (note, self.cid, sid))
            self.con.commit()
            self.lines[sid]['note'] = note

    def toggle_recount(self, sid):
        if sid in self.lines and self.editable:
            v = 0 if self.lines[sid].get('recount') else 1
            self.con.execute('UPDATE stock_count_lines SET recount=? WHERE count_id=? AND stock_id=?', (v, self.cid, sid))
            self.con.commit()
            self.agg['recount'] += 1 if v else -1
            self.lines[sid]['recount'] = v
            return v
        return None

    def add_unknown(self, barcode, qty=1.0):
        self.con.execute('INSERT INTO stock_count_unknown(count_id,barcode,qty,dt) VALUES(?,?,?,?) ON CONFLICT(count_id,barcode) DO UPDATE SET qty=qty+excluded.qty,dt=excluded.dt',
                         (self.cid, str(barcode), float(qty), self.now()))
        self.con.commit()

    def unknowns(self):
        return self.con.execute('SELECT * FROM stock_count_unknown WHERE count_id=? ORDER BY id', (self.cid,)).fetchall()

    def register_new_stock(self, d):
        """Okutma sırasında açılan yeni stok kartını kataloğa ekler."""
        d = dict(d)
        self.catalog[d['id']] = d
        for k in (d.get('barcode'), d.get('code')):
            k = str(k or '').strip()
            if k:
                self.keys.setdefault(k, []).append(d['id'])
                self.keys.setdefault(k.casefold(), []).append(d['id'])

    # ------------------------------------------------------------------ özet
    def totals(self):
        a = self.agg
        return {'lines': len(self.lines), 'qty': a['qty'], 'diff_lines': a['diff_lines'], 'over': a['over'], 'short': a['short'],
                'net': a['over'] + a['short'], 'recount': a['recount'], 'uncounted': max(0, self._scope_n - a['in_scope'])}

    def rows(self, mode='Tümü', text=''):
        """Ekranda gösterilecek stock_id listesi (en son okutulan en üstte)."""
        if mode == 'Sayılmayanlar':
            ids = sorted(self.uncounted(), key=lambda i: str(self.catalog[i]['name'] or '').casefold())
        else:
            ids = list(self.order)
            if mode == 'Sadece Farklı':
                ids = [i for i in ids if abs(float(self.lines[i]['difference'] or 0)) > EPS]
            elif mode == 'Fazla':
                ids = [i for i in ids if float(self.lines[i]['difference'] or 0) > EPS]
            elif mode == 'Eksik':
                ids = [i for i in ids if float(self.lines[i]['difference'] or 0) < -EPS]
            elif mode == 'Yeniden Say':
                ids = [i for i in ids if self.lines[i].get('recount')]
        terms = [x.casefold() for x in str(text or '').split() if x]
        if terms:
            ids = [i for i in ids if all(t in (str(self.catalog[i]['name'] or '') + ' ' + str(self.catalog[i]['code'] or '') + ' ' + str(self.catalog[i]['barcode'] or '')).casefold() for t in terms)]
        return ids

    # ------------------------------------------------------------------ içe aktarma (el terminali dosyası)
    def import_text(self, text, mode='add'):
        """Satırlar 'barkod;adet' veya yalnız 'barkod' (her satır 1 adet). Aynı barkodlar toplanır.
        mode 'add' mevcut sayıma ekler, 'set' dosyadaki toplamla üzerine yazar. (okunan, eşleşen, bilinmeyen, toplam_adet) döner."""
        agg = {}
        read = 0
        for raw in str(text or '').splitlines():
            ln = raw.strip().lstrip('﻿')
            if not ln:
                continue
            parts = re.split(r'[;\t,]', ln)
            code = parts[0].strip().strip('"')
            if not code or code.casefold() in ('barkod', 'barcode', 'kod'):
                continue
            q = 1.0
            if len(parts) > 1 and parts[1].strip():
                try:
                    q = float(parts[1].strip().strip('"').replace(',', '.'))
                except ValueError:
                    q = 1.0
            agg[code] = agg.get(code, 0.0) + q
            read += 1
        matched = unknown = 0
        total = 0.0
        for code, q in agg.items():
            st, sid = self.resolve(code)
            if st == 'ok':
                q = q * self.unit_factor(code)
                self.add(sid, q, mode, commit=False)
                matched += 1
                total += q
            else:
                self.add_unknown(code, q)
                unknown += 1
        self.con.commit()
        return read, matched, unknown, total

    # ------------------------------------------------------------------ kesinleştirme
    def finalize(self, reason, user, doc_no, zero_uncounted=False, audit=None):
        """Tek işlemde, toplu. Özet sözlüğü döner. Hata olursa işlem geri alınır."""
        if not self.editable:
            raise ValueError('Sayım artık taslak değil.')
        con = self.con
        ts = self.now()
        t0 = time.time()
        try:
            con.execute('BEGIN IMMEDIATE')
            live = con.execute('SELECT status FROM stock_counts WHERE id=?', (self.cid,)).fetchone()
            if not live or live['status'] != 'Taslak':
                raise ValueError('Sayım artık taslak değil. Listeyi yenileyin.')
            cur = {r['id']: (float(r['qty'] or 0), float(r['avg_cost'] or 0), r['active']) for r in con.execute('SELECT id,qty,avg_cost,active FROM stock')}
            upd_lines, upd_stock, moves = [], [], []
            moved = changed = 0
            for ln in con.execute('SELECT id,stock_id,actual_qty FROM stock_count_lines WHERE count_id=?', (self.cid,)).fetchall():
                if ln['stock_id'] not in cur:
                    continue
                sysq, cost, _a = cur[ln['stock_id']]
                actual = float(ln['actual_qty'] or 0)
                diff = round(actual - sysq, 6)
                upd_lines.append((sysq, diff, cost, diff * cost, ln['id']))
                upd_stock.append((actual, ts, ln['stock_id']))
                if abs(diff) > EPS:
                    moves.append((ln['stock_id'], ts, 'Toplu Sayım Farkı', diff, cost, 'STOCK_COUNT', self.cid, doc_no + ' / ' + reason))
                    moved += 1
                orig = self.lines.get(ln['stock_id'])
                if orig is not None and abs(sysq - float(orig['system_qty'] or 0)) > EPS:
                    changed += 1
            zeroed = 0
            if zero_uncounted:
                counted = set(self.lines)
                for sid in self.scope_ids():
                    if sid in counted or sid not in cur:
                        continue
                    sysq, cost, _a = cur[sid]
                    if abs(sysq) < EPS:
                        continue
                    diff = round(0 - sysq, 6)
                    con.execute('INSERT INTO stock_count_lines(count_id,stock_id,barcode,system_qty,actual_qty,difference,unit_cost,value_diff,note,scans,last_scan) VALUES(?,?,?,?,?,?,?,?,?,0,?)',
                                (self.cid, sid, self.catalog[sid]['barcode'] or '', sysq, 0, diff, cost, diff * cost, 'Sayılmadı: sıfırlandı', ts))
                    upd_stock.append((0.0, ts, sid))
                    moves.append((sid, ts, 'Toplu Sayım Farkı', diff, cost, 'STOCK_COUNT', self.cid, doc_no + ' / sayılmayan sıfırlandı / ' + reason))
                    zeroed += 1
            con.executemany('UPDATE stock_count_lines SET system_qty=?,difference=?,unit_cost=?,value_diff=? WHERE id=?', upd_lines)
            con.executemany('UPDATE stock SET qty=?,updated=? WHERE id=?', upd_stock)
            con.executemany('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)', moves)
            summ = {'lines': len(upd_lines), 'moved': moved, 'zeroed': zeroed, 'changed_during': changed}
            con.execute("UPDATE stock_counts SET status='Kesinleşti',note=?,updated=?,finalized_at=?,zero_uncounted=?,summary=? WHERE id=?",
                        (reason, ts, ts, 1 if zero_uncounted else 0, repr(summ), self.cid))
            if audit:
                audit(con, 'Toplu Sayım Kesinleştir', self.cid, 'Taslak', 'Kesinleşti', reason)
            con.commit()
        except Exception:
            con.rollback()
            raise
        self.editable = False
        summ['ms'] = int((time.time() - t0) * 1000)
        self.load()
        return summ

    def finalize_preview(self, zero_uncounted=False):
        """Kesinleştirmeden önce etkiyi hesaplar (veritabanına yazmaz)."""
        cur = {r['id']: (float(r['qty'] or 0), float(r['avg_cost'] or 0)) for r in self.con.execute('SELECT id,qty,avg_cost FROM stock')}
        changed = moved = zeroed = 0
        over = short = 0.0
        for sid, l in self.lines.items():
            if sid not in cur:
                continue
            sysq, cost = cur[sid]
            diff = round(float(l['actual_qty'] or 0) - sysq, 6)
            if abs(diff) > EPS:
                moved += 1
                v = diff * cost
                if v >= 0:
                    over += v
                else:
                    short += v
            if abs(sysq - float(l['system_qty'] or 0)) > EPS:
                changed += 1
        if zero_uncounted:
            for sid in self.uncounted():
                if sid in cur and abs(cur[sid][0]) > EPS:
                    zeroed += 1
                    short += -cur[sid][0] * cur[sid][1] if cur[sid][0] > 0 else 0.0
        return {'lines': len(self.lines), 'moved': moved, 'zeroed': zeroed, 'changed_during': changed, 'over': over, 'short': short, 'net': over + short}

    # ------------------------------------------------------------------ sayım föyü (kör liste)
    def sheet_rows(self, only_scope=True):
        ids = self.scope_ids() if only_scope else [i for i, d in self.catalog.items() if d['active']]
        ids.sort(key=lambda i: (str(self.catalog[i]['section'] or ''), str(self.catalog[i]['shelf'] or ''), str(self.catalog[i]['name'] or '').casefold()))
        return [self.catalog[i] for i in ids]

    def sheet_html(self, title):
        import html
        rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="b"></td></tr>' % (
            html.escape(str(d['section'] or '')), html.escape(str(d['shelf'] or '')), html.escape(str(d['barcode'] or d['code'] or '')),
            html.escape(str(d['code'] or '')), html.escape(str(d['name'] or '')[:60])) for d in self.sheet_rows())
        return ('<!doctype html><html><head><meta charset="utf-8"><title>%s</title><style>body{font-family:Segoe UI,Arial;margin:14px}'
                'h1{font-size:18px;color:#c00000}table{border-collapse:collapse;width:100%%;font-size:12px}td,th{border:1px solid #000;padding:4px 6px}'
                'th{background:#eee}.b{width:90px}</style></head><body><h1>%s</h1><p>Sayan: ____________ &nbsp; Tarih: ____________ &nbsp; Bu föyde sistem miktarı yoktur (kör sayım).</p>'
                '<table><tr><th>Bölüm</th><th>Raf</th><th>Barkod</th><th>Kod</th><th>Stok Adı</th><th>Sayılan</th></tr>%s</table></body></html>') % (html.escape(title), html.escape(title), rows)

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass
