# -*- coding: utf-8 -*-
"""ALFABE - Özel Rapor Oluşturucu (v419).

Stok / Satış / Cari / Finans veri alanlarından kullanıcının kendi raporunu kurmasını sağlar:
alan seçimi ve sırası, özetleme (gruplama), tarih ve kategori filtreleri, hesaplanan alanlar,
önceki dönem / geçen yıl karşılaştırması ve doğru toplamlar.

Sonuç (başlıklar, satırlar, toplam satırı) programın mevcut tek rapor altyapısına verilir:
ön izleme, A4 baskı, PDF, Excel ve görsel tasarım aynı motordan çıkar. Bu modül veritabanına
yalnızca kendi tablosunu (custom_reports) yazar; satış/stok/cari/finans kayıtlarını değiştirmez.
"""
import ast
import datetime
import json
import sqlite3

TABLE = 'custom_reports'
MAX_ROWS = 50000

# ----------------------------------------------------------------------------------------------
# Veri alanları (tek tanım kaynağı)
# ----------------------------------------------------------------------------------------------
_COST = "COALESCE(NULLIF(sl.cost_snapshot,0),NULLIF(st.avg_cost,0),st.last_cost,0)"
_SALE_TYPE = ("CASE WHEN s.doc_no LIKE 'CS-%' OR EXISTS(SELECT 1 FROM sales os WHERE os.id=s.original_sale_id AND os.doc_no LIKE 'CS-%') "
              "THEN 'Cari Satış' ELSE 'Perakende' END")

DOMAINS = {
    'Satış': dict(
        base="FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id LEFT JOIN caris ca ON ca.id=s.customer_id",
        date="substr(s.dt,1,10)",
        where_net="s.status IN ('FINAL','PARTIAL_RETURN','RETURNED','RETURN')",
        where_gross="s.status IN ('FINAL','PARTIAL_RETURN','RETURNED')",
        rowid="sl.id",
        dims={
            'tarih': ('Tarih', "substr(s.dt,1,10)"),
            'ay': ('Ay', "substr(s.dt,1,7)"),
            'belge': ('Belge No', "s.doc_no"),
            'tur': ('Satış Türü', _SALE_TYPE),
            'musteri': ('Müşteri', "COALESCE(ca.title,'Perakende')"),
            'kullanici': ('Kullanıcı', "COALESCE(s.user,'')"),
            'odeme': ('Ödeme Türü', "COALESCE(s.payment,'')"),
            'kod': ('Stok Kodu', "COALESCE(st.code,'')"),
            'barkod': ('Barkod', "COALESCE(NULLIF(sl.barcode,''),st.barcode,'')"),
            'urun': ('Ürün', "COALESCE(NULLIF(sl.name,''),st.name,'')"),
            'marka': ('Marka', "COALESCE(NULLIF(trim(st.brand),''),'(Markasız)')"),
            'grup': ('Ürün Grubu', "COALESCE(NULLIF(trim(st.grp),''),'(Grupsuz)')"),
        },
        measures={
            'adet': ('Adet', "SUM(sl.qty)", 3),
            'ciro': ('Net Satış Tutarı', "SUM(sl.total)", 2),
            'iskonto': ('İskonto', "SUM(COALESCE(sl.discount,0))", 2),
            'maliyet': ('Maliyet', "SUM(sl.qty*" + _COST + ")", 2),
            'kar': ('Brüt Kâr', "SUM(sl.total-sl.qty*" + _COST + ")", 2),
            'kdv': ('KDV Tutarı', "SUM(COALESCE(sl.vat_amount,0))", 2),
            'fis': ('Fiş Sayısı', "COUNT(DISTINCT CASE WHEN s.status<>'RETURN' THEN s.id END)", 0),
        },
    ),
    'Stok': dict(
        base="FROM stock st",
        date=None,
        where_net="1=1", where_gross="1=1",
        rowid="st.id",
        dims={
            'kod': ('Stok Kodu', "COALESCE(st.code,'')"),
            'barkod': ('Barkod', "COALESCE(st.barcode,'')"),
            'urun': ('Ürün', "COALESCE(st.name,'')"),
            'marka': ('Marka', "COALESCE(NULLIF(trim(st.brand),''),'(Markasız)')"),
            'grup': ('Ürün Grubu', "COALESCE(NULLIF(trim(st.grp),''),'(Grupsuz)')"),
            'altgrup': ('Alt Grup', "COALESCE(NULLIF(trim(st.subgrp),''),'(Yok)')"),
            'birim': ('Birim', "COALESCE(st.unit,'')"),
            'depo': ('Depo', "COALESCE(NULLIF(trim(st.warehouse),''),'(Belirsiz)')"),
            'durum': ('Durum', "CASE WHEN COALESCE(st.active,1)=1 THEN 'Aktif' ELSE 'Pasif' END"),
        },
        measures={
            'adet': ('Stok Adedi', "SUM(COALESCE(st.qty,0))", 3),
            'maliyet_deger': ('Stok Maliyet Değeri', "SUM(COALESCE(st.qty,0)*COALESCE(NULLIF(st.avg_cost,0),st.last_cost,0))", 2),
            'satis_deger': ('Stok Satış Değeri', "SUM(COALESCE(st.qty,0)*COALESCE(st.retail,0))", 2),
            'cesit': ('Ürün Çeşidi', "COUNT(st.id)", 0),
            'min_alti': ('Min. Altı Ürün', "SUM(CASE WHEN COALESCE(st.min_qty,0)>0 AND COALESCE(st.qty,0)<st.min_qty THEN 1 ELSE 0 END)", 0),
        },
    ),
    'Cari': dict(
        base="FROM caris ca LEFT JOIN cari_moves m ON m.cari_id=ca.id",
        date="substr(m.dt,1,10)",
        where_net="COALESCE(ca.active,1)=1", where_gross="COALESCE(ca.active,1)=1",
        rowid="COALESCE(m.id,-ca.id)",
        dims={
            'unvan': ('Cari', "COALESCE(ca.title,'')"),
            'kod': ('Cari Kodu', "COALESCE(ca.cari_code,'')"),
            'rol': ('Cari Türü', "COALESCE(ca.role,'')"),
            'grup': ('Cari Grubu', "COALESCE(NULLIF(trim(ca.grp),''),'(Grupsuz)')"),
            'sehir': ('Şehir', "COALESCE(NULLIF(trim(ca.city),''),'(Belirsiz)')"),
            'hareket': ('Hareket Türü', "COALESCE(m.kind,'')"),
            'tarih': ('Tarih', "substr(m.dt,1,10)"),
            'ay': ('Ay', "substr(m.dt,1,7)"),
        },
        measures={
            'borc': ('Borç', "SUM(COALESCE(m.debit,0))", 2),
            'alacak': ('Alacak', "SUM(COALESCE(m.credit,0))", 2),
            'bakiye': ('Bakiye', "SUM(COALESCE(m.debit,0)-COALESCE(m.credit,0))", 2),
            'hareket_sayi': ('Hareket Sayısı', "COUNT(m.id)", 0),
        },
    ),
    'Finans': dict(
        base="FROM finance f",
        date="substr(f.dt,1,10)",
        where_net="1=1", where_gross="1=1",
        rowid="f.id",
        dims={
            'tarih': ('Tarih', "substr(f.dt,1,10)"),
            'ay': ('Ay', "substr(f.dt,1,7)"),
            'hesap': ('Hesap', "COALESCE(f.account,'')"),
            'tur': ('Hareket Türü', "COALESCE(f.kind,'')"),
            'kaynak': ('Kaynak', "COALESCE(f.source,'')"),
            'pos': ('POS', "COALESCE(f.pos_name,'')"),
            'aciklama': ('Açıklama', "COALESCE(f.note,'')"),
        },
        measures={
            'giris': ('Giriş Tutarı', "SUM(CASE WHEN f.amount>0 THEN f.amount ELSE 0 END)", 2),
            'cikis': ('Çıkış Tutarı', "SUM(CASE WHEN f.amount<0 THEN -f.amount ELSE 0 END)", 2),
            'net': ('Net Tutar', "SUM(f.amount)", 2),
            'islem': ('İşlem Sayısı', "COUNT(f.id)", 0),
        },
    ),
}

PERIODS = ['Tümü', 'Bugün', 'Dün', 'Bu Hafta', 'Son 30 Gün', 'Bu Ay', 'Geçen Ay', 'Bu Yıl', 'Özel Tarih']
COMPARE = [('', 'Karşılaştırma yok'), ('onceki', 'Önceki dönem'), ('yil', 'Geçen yıl aynı dönem')]


# ----------------------------------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------------------------------
def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def parse_date(s):
    """GG.AA.YYYY veya YYYY-AA-GG -> date; boş -> None."""
    t = str(s or '').strip()[:10]
    if not t:
        return None
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    raise ValueError('Tarih anlaşılamadı: ' + t + ' (GG.AA.YYYY yazın)')


def tr_date(d):
    return d.strftime('%d.%m.%Y') if d else ''


def _month_end(y, m):
    nxt = datetime.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return nxt - datetime.timedelta(days=1)


def resolve_period(preset, start='', end='', today=None):
    """-> (a, b) date nesneleri; 'Tümü' için (None, None)."""
    t = today or datetime.date.today()
    if preset == 'Tümü' or not preset:
        return None, None
    if preset == 'Bugün':
        return t, t
    if preset == 'Dün':
        d = t - datetime.timedelta(days=1)
        return d, d
    if preset == 'Bu Hafta':
        a = t - datetime.timedelta(days=t.weekday())
        return a, t
    if preset == 'Son 30 Gün':
        return t - datetime.timedelta(days=29), t
    if preset == 'Bu Ay':
        return t.replace(day=1), t
    if preset == 'Geçen Ay':
        last = t.replace(day=1) - datetime.timedelta(days=1)
        return last.replace(day=1), last
    if preset == 'Bu Yıl':
        return datetime.date(t.year, 1, 1), t
    if preset == 'Özel Tarih':
        a, b = parse_date(start), parse_date(end)
        if a is None or b is None:
            raise ValueError('Özel tarih için başlangıç ve bitiş tarihini yazın.')
        if a > b:
            raise ValueError('Başlangıç tarihi bitiş tarihinden sonra olamaz.')
        return a, b
    raise ValueError('Bilinmeyen dönem: ' + str(preset))


def compare_period(preset, a, b, mode):
    """Karşılaştırma dönemi (a2, b2). mode: 'onceki' | 'yil'."""
    if a is None or b is None:
        return None, None
    if mode == 'yil':
        def back(d):
            try:
                return d.replace(year=d.year - 1)
            except ValueError:          # 29 Şubat
                return d.replace(year=d.year - 1, day=28)
        return back(a), back(b)
    if preset in ('Bu Ay', 'Geçen Ay'):
        pm_last = a - datetime.timedelta(days=1)
        pa = pm_last.replace(day=1)
        span = (b - a).days
        pb = min(pa + datetime.timedelta(days=span), _month_end(pa.year, pa.month))
        return pa, pb
    if preset == 'Bu Yıl':
        return datetime.date(a.year - 1, 1, 1), (lambda d: d)(datetime.date(a.year - 1, b.month, min(b.day, _month_end(a.year - 1, b.month).day)))
    n = (b - a).days + 1
    return a - datetime.timedelta(days=n), a - datetime.timedelta(days=1)


def _eval_formula(expr, env):
    """Güvenli aritmetik: + - * / ve parantez; alan adları env'den. Sıfıra bölme -> None."""
    tree = ast.parse(str(expr).strip(), mode='eval')

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise ValueError('Formülde bilinmeyen alan: ' + n.id)
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return None if v is None else (-v if isinstance(n.op, ast.USub) else v)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return None if abs(b) < 1e-12 else a / b
        raise ValueError('Formülde yalnızca + - * / ve parantez kullanılabilir.')
    return ev(tree)


def formula_names(expr):
    return [n.id for n in ast.walk(ast.parse(str(expr).strip(), mode='eval')) if isinstance(n, ast.Name)]


def validate_formula(domain, expr, known_calcs=()):
    dom = DOMAINS[domain]
    env = {k: 1.0 for k in dom['measures']}
    env.update({k: 1.0 for k in known_calcs})
    try:
        _eval_formula(expr, env)
    except SyntaxError:
        raise ValueError('Formül yazımı hatalı. Örnek: kar / ciro * 100')
    return True


def ensure_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS ' + TABLE + '(id INTEGER PRIMARY KEY, name TEXT UNIQUE, domain TEXT, definition TEXT, created TEXT, updated TEXT, active INTEGER DEFAULT 1)')
    c.commit()


def list_saved(c):
    ensure_schema(c)
    return [dict(id=r[0], name=r[1], domain=r[2]) for r in c.execute('SELECT id,name,domain FROM ' + TABLE + ' WHERE active=1 ORDER BY name COLLATE NOCASE').fetchall()]


def load_saved(c, name):
    ensure_schema(c)
    r = c.execute('SELECT definition FROM ' + TABLE + ' WHERE name=? AND active=1', (name,)).fetchone()
    return json.loads(r[0]) if r else None


def save_definition(c, name, defn):
    name = str(name or '').strip()
    if len(name) < 2:
        raise ValueError('Rapor adı en az 2 karakter olmalıdır.')
    if defn.get('domain') not in DOMAINS:
        raise ValueError('Veri alanı seçilmemiş.')
    ensure_schema(c)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data = json.dumps(defn, ensure_ascii=False)
    r = c.execute('SELECT id FROM ' + TABLE + ' WHERE name=?', (name,)).fetchone()
    if r:
        c.execute('UPDATE ' + TABLE + ' SET domain=?,definition=?,updated=?,active=1 WHERE id=?', (defn['domain'], data, now, r[0]))
    else:
        c.execute('INSERT INTO ' + TABLE + '(name,domain,definition,created,updated,active) VALUES(?,?,?,?,?,1)', (name, defn['domain'], data, now, now))
    c.commit()


def delete_saved(c, name):
    ensure_schema(c)
    c.execute('UPDATE ' + TABLE + ' SET active=0,updated=? WHERE name=?', (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), name))
    c.commit()


def new_definition(domain='Satış'):
    dom = DOMAINS[domain]
    first_dim = list(dom['dims'])[0]
    m1 = list(dom['measures'])[:2]
    return dict(domain=domain, fields=[first_dim] + m1, summarize=True, period='Bu Ay', start='', end='', compare='', filters=[], calcs=[],
                sort='', desc=False, returns='net')


def field_catalog(defn):
    """[(key, etiket, tür)] tür: dim | measure | calc."""
    dom = DOMAINS[defn['domain']]
    out = [(k, v[0], 'dim') for k, v in dom['dims'].items()]
    out += [(k, v[0], 'measure') for k, v in dom['measures'].items()]
    out += [(c['key'], c['label'], 'calc') for c in defn.get('calcs', [])]
    return out


def distinct_values(c, domain, dim_key, limit=500):
    dom = DOMAINS[domain]
    expr = dom['dims'][dim_key][1]
    try:
        rows = c.execute('SELECT DISTINCT ' + expr + ' v ' + dom['base'] + ' WHERE ' + dom['where_net'] + ' ORDER BY 1 LIMIT ' + str(int(limit))).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if r[0] not in (None, '')]


# ----------------------------------------------------------------------------------------------
# Sorgu
# ----------------------------------------------------------------------------------------------
def _fetch(c, dom, dim_keys, meas_keys, a, b, filters, grouped, returns):
    dims, meas = dom['dims'], dom['measures']
    cols = ['%s AS d_%s' % (dims[k][1], k) for k in dim_keys] + ['%s AS m_%s' % (meas[k][1], k) for k in meas_keys]
    if not cols:
        cols = ['1 AS one']
    where = [dom['where_gross'] if returns == 'excl' else dom['where_net']]
    params = []
    if a is not None and dom['date']:
        where.append(dom['date'] + ' BETWEEN ? AND ?')
        params += [a.isoformat(), b.isoformat()]
    for k, v in filters:
        if k in dims and str(v) != '':
            where.append('(' + dims[k][1] + ')=?')
            params.append(str(v))
    sql = 'SELECT ' + ','.join(cols) + ' ' + dom['base'] + ' WHERE ' + ' AND '.join(where)
    if grouped is True:
        if dim_keys:
            sql += ' GROUP BY ' + ','.join(dims[k][1] for k in dim_keys) + ' ORDER BY ' + ','.join(dims[k][1] for k in dim_keys)
    elif grouped == 'detail':
        sql += ' GROUP BY ' + dom['rowid'] + ' ORDER BY ' + dom['rowid']
    sql += ' LIMIT ' + str(MAX_ROWS + 1)
    rows = c.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = {}
        for i, k in enumerate(dim_keys):
            v = r[i]
            d['d_' + k] = '' if v is None else v
        for j, k in enumerate(meas_keys):
            d['m_' + k] = _f(r[len(dim_keys) + j])
        out.append(d)
    return out


def _totals(c, dom, meas_keys, a, b, filters, returns):
    rows = _fetch(c, dom, [], meas_keys, a, b, filters, False, returns)
    return rows[0] if rows else {('m_' + k): 0.0 for k in meas_keys}


def _apply_calcs(values, calcs):
    """values: {'ciro':..} -> calc anahtarlarını da ekler (sırayla)."""
    env = dict(values)
    for cd in calcs:
        try:
            env[cd['key']] = _eval_formula(cd['formula'], env)
        except ZeroDivisionError:
            env[cd['key']] = None
    return env


def run(c, defn, today=None):
    """-> dict(heads, rows, total, period_text, note, key, compare_text)."""
    if defn.get('domain') not in DOMAINS:
        raise ValueError('Veri alanı seçilmemiş.')
    domain = defn['domain']
    dom = DOMAINS[domain]
    fields = list(defn.get('fields') or [])
    if not fields:
        raise ValueError('Rapora en az bir alan ekleyin.')
    calcs = list(defn.get('calcs') or [])
    calc_keys = {cd['key'] for cd in calcs}
    dim_sel = [k for k in fields if k in dom['dims']]
    meas_shown = [k for k in fields if k in dom['measures']]
    calc_shown = [k for k in fields if k in calc_keys]
    for k in fields:
        if k not in dom['dims'] and k not in dom['measures'] and k not in calc_keys:
            raise ValueError('Bu veri alanında olmayan alan: ' + k)
    # Hesaplanan alanların ihtiyaç duyduğu ölçüler ekranda olmasa da sorguya girer.
    need = list(meas_shown)
    for cd in calcs:
        for nm in formula_names(cd['formula']):
            if nm in dom['measures'] and nm not in need:
                need.append(nm)
    for cd in calcs:
        validate_formula(domain, cd['formula'], [x['key'] for x in calcs])
    summarize = bool(defn.get('summarize', True))
    note = []
    a, b = resolve_period(defn.get('period', 'Tümü'), defn.get('start', ''), defn.get('end', ''), today)
    if dom['date'] is None and a is not None:
        note.append('Stok raporu anlık durumdur; tarih filtresi uygulanmadı.')
        a = b = None
    filters = [(k, v) for k, v in (defn.get('filters') or []) if k in dom['dims'] and str(v) != '']
    returns = defn.get('returns', 'net')
    grouped = True if summarize else 'detail'
    if not summarize and not dim_sel:
        raise ValueError('Ayrıntılı listede en az bir tanımlayıcı alan (ör. Belge No, Ürün) seçin ya da "Özetle" seçeneğini açın.')
    cur = _fetch(c, dom, dim_sel, need, a, b, filters, grouped, returns)
    if len(cur) > MAX_ROWS:
        raise ValueError('Sonuç %d satırdan fazla. Dönemi daraltın veya "Özetle" seçeneğini kullanın.' % MAX_ROWS)
    tot = _totals(c, dom, need, a, b, filters, returns)
    # karşılaştırma
    cmp_mode = defn.get('compare', '') if summarize else ''
    if defn.get('compare') and not summarize:
        note.append('Karşılaştırma yalnızca özetlenmiş raporlarda yapılır.')
    prev = prev_tot = None
    pa = pb = None
    if cmp_mode:
        if a is None:
            note.append('Karşılaştırma için bir dönem seçin (Tümü seçiliyken yapılamaz).')
            cmp_mode = ''
        else:
            pa, pb = compare_period(defn.get('period'), a, b, cmp_mode)
            prev = _fetch(c, dom, dim_sel, need, pa, pb, filters, grouped, returns)
            prev_tot = _totals(c, dom, need, pa, pb, filters, returns)

    def vals_of(row):
        return _apply_calcs({k: row.get('m_' + k, 0.0) for k in need}, calcs)

    # sütun başlıkları
    cat = {k: (lbl, kind) for k, lbl, kind in field_catalog(defn)}
    heads = [cat[k][0] for k in fields]
    num_fields = [k for k in fields if k not in dom['dims']]
    if cmp_mode:
        for k in num_fields:
            heads += [cat[k][0] + ' (Önceki)', cat[k][0] + ' Değişim %']
    dec = {k: dom['measures'][k][2] for k in dom['measures']}

    def rnd(k, v):
        if v is None:
            return ''
        return round(v, dec.get(k, 2))

    def build_row(row, prow):
        v = vals_of(row) if row is not None else None
        pv = vals_of(prow) if prow is not None else None
        out = []
        for k in fields:
            if k in dom['dims']:
                src = row if row is not None else prow
                out.append(src.get('d_' + k, ''))
            else:
                out.append(rnd(k, v.get(k)) if v is not None else rnd(k, 0.0))
        if cmp_mode:
            for k in num_fields:
                cv = v.get(k) if v is not None else 0.0
                pvv = pv.get(k) if pv is not None else 0.0
                out.append(rnd(k, pvv) if pvv is not None else '')
                if cv is None or pvv is None or abs(pvv) < 1e-12:
                    out.append('')
                else:
                    out.append(round((cv - pvv) / abs(pvv) * 100.0, 2))
        return out

    rows_out = []
    keyed = []
    if summarize:
        pmap = {}
        if cmp_mode:
            for r in prev:
                pmap[tuple(r['d_' + k] for k in dim_sel)] = r
        seen = set()
        for r in cur:
            kk = tuple(r['d_' + k] for k in dim_sel)
            seen.add(kk)
            keyed.append((r, pmap.get(kk)))
        if cmp_mode:
            for kk, r in pmap.items():
                if kk not in seen:
                    keyed.append((None, r))
    else:
        keyed = [(r, None) for r in cur]
    for r, pr in keyed:
        rows_out.append(build_row(r, pr))
    # sıralama
    sort_key = defn.get('sort') or ''
    if sort_key in fields:
        idx = fields.index(sort_key)
        rev = bool(defn.get('desc'))
        has = [r for r in rows_out if r[idx] != '']
        non = [r for r in rows_out if r[idx] == '']
        has.sort(key=lambda r: (r[idx] if isinstance(r[idx], (int, float)) else str(r[idx]).casefold()), reverse=rev)
        rows_out = has + non
    elif summarize and dim_sel:
        first = fields.index(dim_sel[0])
        rows_out.sort(key=lambda r: str(r[first]).casefold())
    # toplam satırı: tüm sorgunun doğrudan toplamı (satır ortalamalarından değil)
    tv = vals_of(tot)
    pt = vals_of(prev_tot) if prev_tot is not None else None
    total = []
    first_dim_done = False
    for k in fields:
        if k in dom['dims']:
            total.append('TOPLAM' if not first_dim_done else '')
            first_dim_done = True
        else:
            total.append(rnd(k, tv.get(k)))
    if cmp_mode:
        for k in num_fields:
            cv, pvv = tv.get(k), (pt.get(k) if pt else None)
            total.append(rnd(k, pvv) if pvv is not None else '')
            total.append('' if (cv is None or pvv is None or abs(pvv) < 1e-12) else round((cv - pvv) / abs(pvv) * 100.0, 2))
    if not first_dim_done and total:
        pass
    period_text = ''
    if a is not None:
        period_text = '%s - %s' % (tr_date(a), tr_date(b))
        if cmp_mode:
            period_text += '   |   Karşılaştırılan: %s - %s' % (tr_date(pa), tr_date(pb))
    elif dom['date'] is not None:
        period_text = 'Tüm tarihler'
    if filters:
        period_text += ('   |   ' if period_text else '') + ', '.join('%s: %s' % (dom['dims'][k][0], v) for k, v in filters)
    return dict(heads=heads, rows=[tuple(r) for r in rows_out], total=tuple(total), period_text=period_text, note=' '.join(note),
                key='Özel Rapor / ' + str(defn.get('name') or 'Adsız'), count=len(rows_out))


# ----------------------------------------------------------------------------------------------
# Ekran
# ----------------------------------------------------------------------------------------------
def open_builder(parent, connection_factory, engine, user='', on_done=None, initial=None):
    """engine: html(key,heads,rows,period,total)->Path, pdf(key,heads,rows,target,period,total),
    export(parent,title,heads,rows,total,key), designer(parent,key,heads,preview_rows=rows),
    printer(key,heads,rows,period,total)"""
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog, filedialog
    import webbrowser

    w = tk.Toplevel(parent)
    w.title('Özel Rapor Oluşturucu')
    w.geometry('1280x860')
    w.minsize(1100, 740)
    st = dict(defn=new_definition(), result=None)

    def conn():
        c = connection_factory()
        ensure_schema(c)
        return c

    top = tk.Frame(w)
    top.pack(fill='x', padx=12, pady=(10, 4))
    tk.Label(top, text='ÖZEL RAPOR OLUŞTURUCU', font=('Segoe UI', 15, 'bold'), fg='#0b376d').pack(side='left')
    saved_var = tk.StringVar()
    cb_saved = ttk.Combobox(top, textvariable=saved_var, state='readonly', width=30)
    cb_saved.pack(side='left', padx=(24, 4))
    name_var = tk.StringVar()

    body = tk.Frame(w)
    body.pack(fill='x', padx=12, pady=4)
    left = tk.LabelFrame(body, text=' 1. Veri alanı ve dönem ')
    left.pack(side='left', fill='y', padx=(0, 6))
    mid = tk.LabelFrame(body, text=' 2. Rapor alanları (sıra = sütun sırası) ')
    mid.pack(side='left', fill='both', expand=True, padx=6)
    right = tk.LabelFrame(body, text=' 3. Filtre / hesaplanan alan / karşılaştırma ')
    right.pack(side='left', fill='y', padx=(6, 0))

    dom_var = tk.StringVar(value='Satış')
    tk.Label(left, text='Veri alanı:').grid(row=0, column=0, sticky='w', padx=6, pady=4)
    cb_dom = ttk.Combobox(left, textvariable=dom_var, values=list(DOMAINS), state='readonly', width=16)
    cb_dom.grid(row=0, column=1, padx=6)
    per_var = tk.StringVar(value='Bu Ay')
    tk.Label(left, text='Dönem:').grid(row=1, column=0, sticky='w', padx=6, pady=4)
    cb_per = ttk.Combobox(left, textvariable=per_var, values=PERIODS, state='readonly', width=16)
    cb_per.grid(row=1, column=1, padx=6)
    s_var, e_var = tk.StringVar(), tk.StringVar()
    tk.Label(left, text='Başlangıç:').grid(row=2, column=0, sticky='w', padx=6)
    tk.Entry(left, textvariable=s_var, width=14).grid(row=2, column=1, padx=6, pady=2)
    tk.Label(left, text='Bitiş:').grid(row=3, column=0, sticky='w', padx=6)
    tk.Entry(left, textvariable=e_var, width=14).grid(row=3, column=1, padx=6, pady=2)
    tk.Label(left, text='(Özel Tarih için GG.AA.YYYY)', fg='#64748b').grid(row=4, column=0, columnspan=2, sticky='w', padx=6)
    sum_var = tk.BooleanVar(value=True)
    tk.Checkbutton(left, text='Özetle (seçili tanımlayıcı\nalanlara göre grupla)', variable=sum_var, justify='left').grid(row=5, column=0, columnspan=2, sticky='w', padx=6, pady=(8, 2))
    ret_var = tk.StringVar(value='Net (iadeler düşülür)')
    tk.Label(left, text='İadeler (Satış):').grid(row=6, column=0, sticky='w', padx=6, pady=(6, 0))
    cb_ret = ttk.Combobox(left, textvariable=ret_var, values=['Net (iadeler düşülür)', 'İadeler hariç'], state='readonly', width=20)
    cb_ret.grid(row=6, column=1, padx=6, pady=(6, 0))
    tk.Label(left, text='Sırala:').grid(row=7, column=0, sticky='w', padx=6, pady=(6, 0))
    sort_var = tk.StringVar()
    cb_sort = ttk.Combobox(left, textvariable=sort_var, state='readonly', width=20)
    cb_sort.grid(row=7, column=1, padx=6, pady=(6, 0))
    desc_var = tk.BooleanVar(value=True)
    tk.Checkbutton(left, text='Büyükten küçüğe', variable=desc_var).grid(row=8, column=1, sticky='w', padx=6)

    # alan seçimi
    tk.Label(mid, text='Kullanılabilir alanlar').grid(row=0, column=0)
    tk.Label(mid, text='Rapordaki sütunlar').grid(row=0, column=2)
    lb_av = tk.Listbox(mid, height=11, width=30, exportselection=False)
    lb_av.grid(row=1, column=0, padx=6, pady=4)
    lb_sel = tk.Listbox(mid, height=11, width=30, exportselection=False)
    lb_sel.grid(row=1, column=2, padx=6, pady=4)
    mbtn = tk.Frame(mid)
    mbtn.grid(row=1, column=1, padx=4)
    keys_av, keys_sel = [], []

    def refresh_lists():
        d = st['defn']
        cat = field_catalog(d)
        label_of = {k: (l, kd) for k, l, kd in cat}
        keys_av[:] = [k for k, _l, _kd in cat if k not in d['fields']]
        lb_av.delete(0, 'end')
        for k in keys_av:
            l, kd = label_of[k]
            lb_av.insert('end', ('▭ ' if kd == 'dim' else '∑ ' if kd == 'measure' else 'ƒ ') + l)
        keys_sel[:] = [k for k in d['fields'] if k in label_of]
        d['fields'] = list(keys_sel)
        lb_sel.delete(0, 'end')
        for k in keys_sel:
            l, kd = label_of[k]
            lb_sel.insert('end', ('▭ ' if kd == 'dim' else '∑ ' if kd == 'measure' else 'ƒ ') + l)
        cb_sort.configure(values=[''] + [label_of[k][0] for k in keys_sel])
        cur = d.get('sort', '')
        sort_var.set(label_of[cur][0] if cur in label_of and cur in keys_sel else '')

    def add_field():
        for i in reversed(lb_av.curselection()):
            st['defn']['fields'].append(keys_av[i])
        refresh_lists()

    def del_field():
        for i in reversed(lb_sel.curselection()):
            st['defn']['fields'].remove(keys_sel[i])
        refresh_lists()

    def move(delta):
        s = lb_sel.curselection()
        if not s:
            return
        i = s[0]
        j = i + delta
        f = st['defn']['fields']
        if 0 <= j < len(f):
            f[i], f[j] = f[j], f[i]
            refresh_lists()
            lb_sel.selection_set(j)

    tk.Button(mbtn, text='Ekle →', command=add_field, width=9).pack(pady=2)
    tk.Button(mbtn, text='← Çıkar', command=del_field, width=9).pack(pady=2)
    tk.Button(mbtn, text='▲ Yukarı', command=lambda: move(-1), width=9).pack(pady=(10, 2))
    tk.Button(mbtn, text='▼ Aşağı', command=lambda: move(1), width=9).pack(pady=2)
    lb_av.bind('<Double-1>', lambda e: add_field())
    lb_sel.bind('<Double-1>', lambda e: del_field())
    tk.Label(mid, text='▭ tanımlayıcı   ∑ ölçü   ƒ hesaplanan', fg='#64748b').grid(row=2, column=0, columnspan=3)

    # filtreler
    flt_vars = []
    tk.Label(right, text='Filtre (kategori):').grid(row=0, column=0, columnspan=2, sticky='w', padx=6)
    flt_rows = []
    for i in range(3):
        dv, vv = tk.StringVar(), tk.StringVar()
        cd = ttk.Combobox(right, textvariable=dv, state='readonly', width=16)
        cd.grid(row=1 + i, column=0, padx=6, pady=2)
        cv = ttk.Combobox(right, textvariable=vv, width=22)
        cv.grid(row=1 + i, column=1, padx=6, pady=2)
        flt_rows.append((dv, vv, cd, cv))

    def dim_label_map():
        dom = DOMAINS[st['defn']['domain']]
        return {v[0]: k for k, v in dom['dims'].items()}

    def fill_filter_values(i):
        dv, vv, cd, cv = flt_rows[i]
        m = dim_label_map()
        k = m.get(dv.get())
        if k:
            c = conn()
            try:
                cv.configure(values=[''] + distinct_values(c, st['defn']['domain'], k))
            finally:
                c.close()
        else:
            cv.configure(values=[''])

    for i, (dv, vv, cd, cv) in enumerate(flt_rows):
        cd.bind('<<ComboboxSelected>>', lambda e, i=i: (flt_rows[i][1].set(''), fill_filter_values(i)))

    tk.Label(right, text='Karşılaştırma:').grid(row=4, column=0, sticky='w', padx=6, pady=(10, 0))
    cmp_var = tk.StringVar(value=COMPARE[0][1])
    cb_cmp = ttk.Combobox(right, textvariable=cmp_var, values=[x[1] for x in COMPARE], state='readonly', width=22)
    cb_cmp.grid(row=4, column=1, padx=6, pady=(10, 0))

    tk.Label(right, text='Hesaplanan alanlar:').grid(row=5, column=0, columnspan=2, sticky='w', padx=6, pady=(10, 0))
    lb_calc = tk.Listbox(right, height=4, width=44, exportselection=False)
    lb_calc.grid(row=6, column=0, columnspan=2, padx=6)
    cbtn = tk.Frame(right)
    cbtn.grid(row=7, column=0, columnspan=2, pady=2)

    def refresh_calcs():
        lb_calc.delete(0, 'end')
        for cd in st['defn']['calcs']:
            lb_calc.insert('end', '%s  =  %s' % (cd['label'], cd['formula']))

    def add_calc():
        d = st['defn']
        dom = DOMAINS[d['domain']]
        hint = 'Kullanılabilir adlar: ' + ', '.join(dom['measures']) + ((', ' + ', '.join(c['key'] for c in d['calcs'])) if d['calcs'] else '')
        label = simpledialog.askstring('Hesaplanan alan', 'Sütun adı (ör. Kâr Marjı %):', parent=w)
        if not label:
            return
        formula = simpledialog.askstring('Hesaplanan alan', 'Formül (+ - * / ve parantez)\n' + hint + '\nÖrnek: kar / ciro * 100', parent=w)
        if not formula:
            return
        try:
            validate_formula(d['domain'], formula, [c['key'] for c in d['calcs']])
        except Exception as ex:
            return messagebox.showwarning('Özel Rapor', str(ex), parent=w)
        n = 1
        while any(c['key'] == 'h%d' % n for c in d['calcs']):
            n += 1
        d['calcs'].append(dict(key='h%d' % n, label=label.strip(), formula=formula.strip()))
        d['fields'].append('h%d' % n)
        refresh_calcs()
        refresh_lists()

    def del_calc():
        s = lb_calc.curselection()
        if not s:
            return
        cd = st['defn']['calcs'].pop(s[0])
        if cd['key'] in st['defn']['fields']:
            st['defn']['fields'].remove(cd['key'])
        refresh_calcs()
        refresh_lists()

    tk.Button(cbtn, text='+ Hesaplanan alan', command=add_calc).pack(side='left', padx=4)
    tk.Button(cbtn, text='Sil', command=del_calc).pack(side='left', padx=4)
    tk.Label(right, text='Toplam satırı, hesaplanan alanlarda satır toplamlarından değil\ngenel toplamlardan yeniden hesaplanır (doğru oran).', fg='#64748b', justify='left').grid(row=8, column=0, columnspan=2, padx=6, pady=4, sticky='w')

    # sonuç
    bar = tk.Frame(w)
    bar.pack(fill='x', padx=12, pady=4)
    info = tk.Label(w, text='', anchor='w', fg='#9a3c00', font=('Segoe UI', 9, 'bold'))
    grid = tk.Frame(w)
    tree = ttk.Treeview(grid, show='headings')
    ys = ttk.Scrollbar(grid, orient='vertical', command=tree.yview)
    xs = ttk.Scrollbar(grid, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    totl = tk.Label(w, text='', anchor='w', bg='#e8f1fb', fg='#0b376d', font=('Segoe UI', 10, 'bold'), justify='left')

    def fmt(h, v, report_value_text=None):
        if v == '' or v is None:
            return ''
        if isinstance(v, float):
            s = '{:,.2f}'.format(v).replace(',', 'X').replace('.', ',').replace('X', '.')
            return s[:-3] if s.endswith(',00') and 'adet' in h.lower() else s
        if isinstance(v, str) and len(v) == 10 and v[4] == '-' and v[7] == '-':
            return '%s.%s.%s' % (v[8:], v[5:7], v[:4])
        return str(v)

    def collect():
        d = st['defn']
        d['domain'] = dom_var.get()
        d['period'] = per_var.get()
        d['start'], d['end'] = s_var.get().strip(), e_var.get().strip()
        d['summarize'] = bool(sum_var.get())
        d['returns'] = 'excl' if ret_var.get().startswith('İadeler hariç') else 'net'
        d['compare'] = next((k for k, l in COMPARE if l == cmp_var.get()), '')
        m = dim_label_map()
        d['filters'] = [[m[dv.get()], vv.get().strip()] for dv, vv, _c1, _c2 in flt_rows if dv.get() in m and vv.get().strip()]
        lab = {k: l for k, l, _kd in field_catalog(d)}
        d['sort'] = next((k for k, l in lab.items() if l == sort_var.get() and k in d['fields']), '')
        d['desc'] = bool(desc_var.get())
        d['name'] = name_var.get().strip() or saved_var.get() or 'Adsız'
        return d

    def do_run():
        try:
            d = collect()
            c = conn()
            try:
                res = run(c, d)
            finally:
                c.close()
        except Exception as ex:
            return messagebox.showwarning('Özel Rapor', str(ex), parent=w)
        st['result'] = res
        tree.delete(*tree.get_children())
        tree.configure(columns=['c%d' % i for i in range(len(res['heads']))])
        for i, h in enumerate(res['heads']):
            tree.heading('c%d' % i, text=h)
            tree.column('c%d' % i, width=max(90, min(260, 11 * len(h) + 20)), anchor='w')
        for r in res['rows'][:5000]:
            tree.insert('', 'end', values=tuple(fmt(h, v) for h, v in zip(res['heads'], r)))
        parts = ['%s: %s' % (h, fmt(h, v)) for h, v in zip(res['heads'], res['total']) if isinstance(v, (int, float)) and v != '']
        totl.config(text='GENEL TOPLAM  |  %d satır  |  ' % res['count'] + '   •   '.join(parts))
        info.config(text=(res['period_text'] + '   ' + res['note']).strip())
        w._ozel = dict(result=res, tree=tree, run=do_run)

    def need_result():
        if st['result'] is None:
            do_run()
        return st['result']

    def key_of(res):
        return res['key']

    def do_preview():
        res = need_result()
        if not res:
            return
        try:
            p = engine['html'](key_of(res), res['heads'], res['rows'], res['period_text'], res['total'])
            webbrowser.open(p.resolve().as_uri(), new=1)
        except Exception as ex:
            messagebox.showerror('Özel Rapor', 'Ön izleme açılamadı:\n' + str(ex), parent=w)

    def do_print():
        res = need_result()
        if not res:
            return
        try:
            engine['printer'](key_of(res), res['heads'], res['rows'], res['period_text'], res['total'])
        except Exception as ex:
            messagebox.showerror('Özel Rapor', 'Yazdırma açılamadı:\n' + str(ex), parent=w)

    def do_pdf():
        res = need_result()
        if not res:
            return
        f = filedialog.asksaveasfilename(parent=w, defaultextension='.pdf', filetypes=[('PDF Belgesi', '*.pdf')], initialfile=(st['defn'].get('name') or 'Ozel_Rapor') + '.pdf')
        if not f:
            return
        try:
            engine['pdf'](key_of(res), res['heads'], res['rows'], f, res['period_text'], res['total'])
            messagebox.showinfo('Özel Rapor', 'PDF kaydedildi.', parent=w)
        except Exception as ex:
            messagebox.showerror('Özel Rapor', 'PDF oluşturulamadı:\n' + str(ex), parent=w)

    def do_excel():
        res = need_result()
        if not res:
            return
        try:
            f = engine['export'](w, st['defn'].get('name') or 'Özel Rapor', res['heads'], res['rows'], res['total'], key_of(res))
            if f:
                messagebox.showinfo('Özel Rapor', 'Excel / CSV olarak kaydedildi.', parent=w)
        except Exception as ex:
            messagebox.showerror('Özel Rapor', 'Dışa aktarılamadı:\n' + str(ex), parent=w)

    def do_design():
        res = need_result()
        if not res:
            return
        try:
            engine['designer'](w, key_of(res), res['heads'], preview_rows=res['rows'])
        except Exception as ex:
            messagebox.showerror('Özel Rapor', 'Tasarımcı açılamadı:\n' + str(ex), parent=w)

    def to_ui():
        d = st['defn']
        dom_var.set(d['domain'])
        per_var.set(d.get('period', 'Tümü'))
        s_var.set(d.get('start', ''))
        e_var.set(d.get('end', ''))
        sum_var.set(bool(d.get('summarize', True)))
        ret_var.set('İadeler hariç' if d.get('returns') == 'excl' else 'Net (iadeler düşülür)')
        cmp_var.set(next((l for k, l in COMPARE if k == d.get('compare', '')), COMPARE[0][1]))
        desc_var.set(bool(d.get('desc')))
        m = {k: v[0] for k, v in DOMAINS[d['domain']]['dims'].items()}
        for dv, vv, cd, cv in flt_rows:
            cd.configure(values=[''] + list(m.values()))
            dv.set('')
            vv.set('')
        for i, (k, v) in enumerate((d.get('filters') or [])[:3]):
            if k in m:
                flt_rows[i][0].set(m[k])
                flt_rows[i][1].set(v)
        refresh_calcs()
        refresh_lists()

    def on_domain(*_):
        if dom_var.get() != st['defn']['domain']:
            keep_name = name_var.get()
            st['defn'] = new_definition(dom_var.get())
            name_var.set(keep_name)
            st['result'] = None
            to_ui()

    cb_dom.bind('<<ComboboxSelected>>', on_domain)

    def reload_saved():
        c = conn()
        try:
            cb_saved.configure(values=[x['name'] for x in list_saved(c)])
        finally:
            c.close()

    def pick_saved(*_):
        c = conn()
        try:
            d = load_saved(c, saved_var.get())
        finally:
            c.close()
        if d:
            st['defn'] = d
            name_var.set(saved_var.get())
            st['result'] = None
            to_ui()

    cb_saved.bind('<<ComboboxSelected>>', pick_saved)

    def do_save():
        d = collect()
        nm = simpledialog.askstring('Raporu kaydet', 'Rapor adı:', initialvalue=name_var.get() or saved_var.get(), parent=w)
        if not nm:
            return
        try:
            c = conn()
            try:
                d['name'] = nm.strip()
                save_definition(c, nm, d)
            finally:
                c.close()
        except Exception as ex:
            return messagebox.showwarning('Özel Rapor', str(ex), parent=w)
        name_var.set(nm.strip())
        reload_saved()
        saved_var.set(nm.strip())
        messagebox.showinfo('Özel Rapor', 'Rapor tanımı kaydedildi: ' + nm.strip(), parent=w)

    def do_delete():
        nm = saved_var.get()
        if not nm:
            return
        if not messagebox.askyesno('Özel Rapor', '"%s" rapor tanımı silinsin mi?\n(Verileriniz etkilenmez.)' % nm, parent=w):
            return
        c = conn()
        try:
            delete_saved(c, nm)
        finally:
            c.close()
        saved_var.set('')
        reload_saved()

    def do_new():
        st['defn'] = new_definition('Satış')
        st['result'] = None
        saved_var.set('')
        name_var.set('')
        to_ui()

    tk.Button(top, text='Yeni', command=do_new).pack(side='left', padx=2)
    tk.Button(top, text='Kaydet', command=do_save).pack(side='left', padx=2)
    tk.Button(top, text='Sil', command=do_delete).pack(side='left', padx=2)
    run_btn = tk.Button(bar, text='▶ RAPORU ÇALIŞTIR', command=do_run, bg='#15803d', fg='white', font=('Segoe UI', 11, 'bold'), padx=14, pady=4)
    run_btn.pack(side='left', padx=4)
    for tx, cmd in [('🖨 BASKI ÖN İZLEME', do_preview), ('🖨 YAZDIR', do_print), ('📄 PDF', do_pdf), ('EXCEL / CSV', do_excel), ('TASARIMI DÜZENLE', do_design)]:
        tk.Button(bar, text=tx, command=cmd, padx=8, pady=4).pack(side='left', padx=3)
    info.pack(fill='x', padx=12)
    grid.pack(fill='both', expand=True, padx=12, pady=4)
    ys.pack(side='right', fill='y')
    xs.pack(side='bottom', fill='x')
    tree.pack(fill='both', expand=True)
    totl.pack(fill='x', padx=12, pady=(2, 10), ipady=6)

    if initial:
        st['defn'] = dict(new_definition(initial.get('domain', 'Satış')), **initial)
    to_ui()
    reload_saved()
    w._ozel = dict(state=st, run=do_run, collect=collect, save=do_save, vars=dict(domain=dom_var, period=per_var, summarize=sum_var, compare=cmp_var, saved=saved_var, name=name_var, start=s_var, end=e_var),
                   fields=lambda: st['defn']['fields'], add=add_field, flt=flt_rows, tree=tree, lb_av=lb_av, lb_sel=lb_sel, to_ui=to_ui, refresh=refresh_lists,
                   buttons=dict(preview=do_preview, print=do_print, pdf=do_pdf, excel=do_excel, design=do_design), on_domain=on_domain)
    if getattr(parent, '_ozel_auto', None):          # test/otomasyon kancası
        parent._ozel_auto(w)
    return w
