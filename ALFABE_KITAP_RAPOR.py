# -*- coding: utf-8 -*-
"""ALFABE kitap satış raporları (v432): satış özeti, yayınevine göre, yazara göre / en çok satanlar, stok ve yeniden sipariş.
Kitap = grubu KİTAP olan kartlar + kitap bilgisi (ISBN/yazar/yayınevi) kaydı olan kartlar. İptal satışlar sayılmaz, iadeler net satıştan düşer."""
import csv
import datetime

import ALFABE_ISBN as IS
import ALFABE_OZEL_RAPOR as OR

OK_STATUS = "('FINAL','RETURN','PARTIAL_RETURN','RETURNED')"


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS stock_book_info(stock_id INTEGER PRIMARY KEY,author TEXT NOT NULL DEFAULT '',publisher TEXT NOT NULL DEFAULT '',isbn TEXT NOT NULL DEFAULT '')")


def book_ids(c):
    _ensure(c)
    ids = {r[0] for r in c.execute('SELECT stock_id FROM stock_book_info').fetchall()}
    for r in c.execute('SELECT id,grp FROM stock').fetchall():
        if IS.tr_upper(r[1] or '').strip() == 'KİTAP':
            ids.add(r[0])
    return ids


def _meta(c):
    _ensure(c)
    out = {}
    for r in c.execute("SELECT s.id,s.name,s.barcode,s.qty,s.min_qty,s.avg_cost,s.retail,s.vat,COALESCE(NULLIF(i.author,''),'(Yazarsız)'),"
                       "COALESCE(NULLIF(i.publisher,''),NULLIF(s.brand,''),'(Yayınevsiz)'),s.active FROM stock s LEFT JOIN stock_book_info i ON i.stock_id=s.id").fetchall():
        out[r[0]] = dict(id=r[0], name=r[1] or '', barcode=r[2] or '', qty=float(r[3] or 0), min=float(r[4] or 0), cost=float(r[5] or 0),
                         retail=float(r[6] or 0), vat=float(r[7] if r[7] is not None else 20), author=r[8], publisher=r[9], active=r[10])
    return out


def lines(c, d1, d2):
    """Tarih aralığındaki kitap satış satırları (iade = eksi)."""
    ids = book_ids(c)
    meta = _meta(c)
    out = []
    for r in c.execute("SELECT s.id,substr(s.dt,1,10),sl.stock_id,sl.qty,sl.total,COALESCE(sl.cost_snapshot,0) FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id "
                       "WHERE substr(s.dt,1,10) BETWEEN ? AND ? AND COALESCE(s.doc_no,'') NOT LIKE 'CS-%' AND COALESCE(s.status,'FINAL') IN " + OK_STATUS,
                       (d1.isoformat(), d2.isoformat())).fetchall():
        if r[2] not in ids:
            continue
        m = meta.get(r[2], dict(vat=20, name='', author='(Yazarsız)', publisher='(Yayınevsiz)', qty=0))
        net = float(r[4] or 0) / (1 + (m['vat'] or 0) / 100.0)
        out.append(dict(sid=r[0], day=r[1], stock_id=r[2], qty=float(r[3] or 0), total=float(r[4] or 0), kar=net - float(r[3] or 0) * float(r[5] or 0), m=m))
    return out


def _agg(rows, keyf):
    d = {}
    for x in rows:
        a = d.setdefault(keyf(x), dict(qty=0.0, total=0.0, kar=0.0, docs=set()))
        a['qty'] += x['qty']; a['total'] += x['total']; a['kar'] += x['kar']; a['docs'].add(x['sid'])
    return d


def summary(c, d1, d2):
    rows = lines(c, d1, d2)
    by = (lambda x: x['day']) if (d2 - d1).days <= 62 else (lambda x: x['day'][:7])
    g = _agg(rows, by)
    detail = [(k, a['qty'], len(a['docs']), a['total'], a['kar']) for k, a in sorted(g.items())]
    tot = _agg(rows, lambda x: 'T').get('T', dict(qty=0, total=0, kar=0, docs=set()))
    return dict(heads=('Gün' if (d2 - d1).days <= 62 else 'Ay', 'Adet', 'Fiş', 'Ciro (KDV dahil)', 'Kâr (KDV hariç)'), rows=detail,
                total=('TOPLAM', tot['qty'], len(tot['docs']), tot['total'], tot['kar']))


def by_publisher(c, d1, d2):
    rows = lines(c, d1, d2)
    g = _agg(rows, lambda x: x['m']['publisher'])
    stock = {}
    meta = _meta(c)
    for i in book_ids(c):
        m = meta.get(i)
        if m and m['active']:
            stock[m['publisher']] = stock.get(m['publisher'], 0) + m['qty']
    names = set(g) | set(stock)
    res = []
    for n in names:
        a = g.get(n, dict(qty=0, total=0, kar=0))
        res.append((n, a['qty'], a['total'], a['kar'], stock.get(n, 0.0)))
    res.sort(key=lambda r: (-r[2], r[0]))
    return dict(heads=('Yayınevi', 'Satılan adet', 'Ciro (KDV dahil)', 'Kâr (KDV hariç)', 'Kalan stok'), rows=res,
                total=('TOPLAM', sum(r[1] for r in res), sum(r[2] for r in res), sum(r[3] for r in res), sum(r[4] for r in res)))


def top_books(c, d1, d2, limit=30):
    rows = lines(c, d1, d2)
    g = _agg(rows, lambda x: x['stock_id'])
    res = [(g_k, a['qty'], a['total'], a['kar']) for g_k, a in g.items()]
    meta = _meta(c)
    res = [(meta.get(k, {}).get('name', '?'), meta.get(k, {}).get('author', ''), q, t, kr) for k, q, t, kr in res]
    res.sort(key=lambda r: (-r[2], r[0]))
    return dict(heads=('Kitap', 'Yazar', 'Satılan adet', 'Ciro (KDV dahil)', 'Kâr (KDV hariç)'), rows=res[:limit], total=None)


def top_authors(c, d1, d2, limit=30):
    g = _agg(lines(c, d1, d2), lambda x: x['m']['author'])
    res = sorted(((k, a['qty'], a['total'], a['kar']) for k, a in g.items()), key=lambda r: (-r[1], r[0]))
    return dict(heads=('Yazar', 'Satılan adet', 'Ciro (KDV dahil)', 'Kâr (KDV hariç)'), rows=res[:limit], total=None)


def stock_report(c, today=None, threshold=2, dead_days=90):
    """Azalan stok + yayınevine göre sipariş önerisi + hareketsiz (satmayan) kitaplar."""
    today = today or datetime.date.today()
    meta = _meta(c)
    ids = [i for i in book_ids(c) if i in meta and meta[i]['active']]
    s30 = {}
    last = {}
    for r in c.execute("SELECT sl.stock_id,sl.qty,substr(s.dt,1,10) FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id "
                       "WHERE COALESCE(s.doc_no,'') NOT LIKE 'CS-%' AND COALESCE(s.status,'FINAL') IN " + OK_STATUS).fetchall():
        if r[2] and r[2] >= (today - datetime.timedelta(days=30)).isoformat():
            s30[r[0]] = s30.get(r[0], 0) + float(r[1] or 0)
        if float(r[1] or 0) > 0 and r[2] and (r[0] not in last or r[2] > last[r[0]]):
            last[r[0]] = r[2]
    low, order, dead = [], [], []
    for i in ids:
        m = meta[i]
        sold = s30.get(i, 0)
        lim = max(threshold, m['min'])
        if m['qty'] <= lim:
            low.append((m['publisher'], m['name'], m['author'], m['qty'], sold))
            sug = max(0.0, max(sold, lim + 1) - m['qty'])
            if sug > 0:
                order.append((m['publisher'], m['name'], m['author'], m['qty'], sold, sug))
        cut = (today - datetime.timedelta(days=dead_days)).isoformat()
        if m['qty'] > 0 and last.get(i, '') < cut:
            dead.append((m['publisher'], m['name'], m['author'], m['qty'], last.get(i, '') or 'hiç satılmadı', m['qty'] * m['cost']))
    low.sort(); order.sort(); dead.sort(key=lambda r: -r[5])
    return dict(
        low=dict(heads=('Yayınevi', 'Kitap', 'Yazar', 'Stok', 'Son 30 gün satış'), rows=low, total=None),
        order=dict(heads=('Yayınevi', 'Kitap', 'Yazar', 'Stok', 'Son 30 gün satış', 'Önerilen sipariş'), rows=order, total=None),
        dead=dict(heads=('Yayınevi', 'Kitap', 'Yazar', 'Stok', 'Son satış', 'Bağlı maliyet'), rows=dead, total=None))


def export_csv(path, rep):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(rep['heads'])
        for r in rep['rows']:
            w.writerow([('%.2f' % x).replace('.', ',') if isinstance(x, float) else x for x in r])
        if rep.get('total'):
            w.writerow([('%.2f' % x).replace('.', ',') if isinstance(x, float) else x for x in rep['total']])


def _fmt(v, money, head=''):
    if isinstance(v, float):
        if any(k in str(head) for k in ('Ciro', 'Kâr', 'maliyet')):
            return money(v) if money else '%.2f' % v
        return '%g' % v
    return v


def open_dialog(parent, connection_factory, money=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    w = tk.Toplevel(parent)
    w.title('Kitap Satış Raporları')
    w.geometry('1100x720')
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    con = connection_factory()
    fm = money or (lambda v: ('%.2f' % float(v)).replace('.', ','))
    tk.Label(w, text='KİTAP SATIŞ RAPORLARI', font=('Segoe UI', 16, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=16, pady=(12, 2))
    top = tk.Frame(w, bg='#f4f7fb'); top.pack(fill='x', padx=16)
    today = datetime.date.today()
    a = tk.StringVar(value=OR.tr_date(today.replace(day=1))); b = tk.StringVar(value=OR.tr_date(today))
    tk.Label(top, text='Başlangıç', bg='#f4f7fb').pack(side='left'); tk.Entry(top, textvariable=a, width=12).pack(side='left', padx=4)
    tk.Label(top, text='Bitiş', bg='#f4f7fb').pack(side='left'); tk.Entry(top, textvariable=b, width=12).pack(side='left', padx=4)

    def quick(kind):
        t = datetime.date.today()
        if kind == 'bugun':
            s, e = t, t
        elif kind == 'ay':
            s, e = t.replace(day=1), t
        elif kind == 'gecen':
            e = t.replace(day=1) - datetime.timedelta(days=1); s = e.replace(day=1)
        else:
            s, e = t.replace(month=1, day=1), t
        a.set(OR.tr_date(s)); b.set(OR.tr_date(e)); run()
    for t, k in (('BUGÜN', 'bugun'), ('BU AY', 'ay'), ('GEÇEN AY', 'gecen'), ('BU YIL', 'yil')):
        tk.Button(top, text=t, command=lambda k=k: quick(k), font=('Segoe UI', 9, 'bold')).pack(side='left', padx=2)
    nb = ttk.Notebook(w); nb.pack(fill='both', expand=True, padx=16, pady=8)
    tabs = {}
    for key, title in (('ozet', 'Satış özeti'), ('yayin', 'Yayınevine göre'), ('kitap', 'En çok satan kitaplar'), ('yazar', 'En çok satan yazarlar'),
                       ('azalan', 'Azalan stok'), ('siparis', 'Sipariş önerisi'), ('olu', 'Satmayan kitaplar')):
        fr = tk.Frame(nb, bg='white'); nb.add(fr, text=title)
        tv = ttk.Treeview(fr, show='headings'); sb = ttk.Scrollbar(fr, orient='vertical', command=tv.yview); tv.configure(yscrollcommand=sb.set)
        tv.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        tabs[key] = dict(tv=tv, rep=None)
    foot = tk.Label(w, text='', bg='#e8f1fb', fg='#0b376d', font=('Segoe UI', 10, 'bold'), anchor='w'); foot.pack(fill='x', padx=16, ipady=6)

    def show(key, rep):
        t = tabs[key]; tv = t['tv']; t['rep'] = rep
        tv.delete(*tv.get_children()); tv['columns'] = list(range(len(rep['heads'])))
        for i, h in enumerate(rep['heads']):
            tv.heading(i, text=h); tv.column(i, width=130 if i else 260, anchor='w' if isinstance(h, str) and h in ('Gün', 'Ay', 'Yayınevi', 'Kitap', 'Yazar', 'Son satış') else 'e')
        for r in rep['rows']:
            tv.insert('', 'end', values=[_fmt(x, fm, rep['heads'][j]) for j, x in enumerate(r)])
        if rep.get('total'):
            tv.insert('', 'end', values=[_fmt(x, fm, rep['heads'][j]) for j, x in enumerate(rep['total'])], tags=('t',))
            tv.tag_configure('t', background='#dbeafe', font=('Segoe UI', 10, 'bold'))

    def run(_=None):
        try:
            d1, d2 = OR.parse_date(a.get()), OR.parse_date(b.get())
            if not d1 or not d2 or d1 > d2:
                raise ValueError('Tarih aralığını kontrol edin.')
        except ValueError as ex:
            messagebox.showwarning('Kitap raporları', str(ex), parent=w); return
        s = summary(con, d1, d2)
        show('ozet', s); show('yayin', by_publisher(con, d1, d2)); show('kitap', top_books(con, d1, d2)); show('yazar', top_authors(con, d1, d2))
        st = stock_report(con)
        show('azalan', st['low']); show('siparis', st['order']); show('olu', st['dead'])
        t = s['total']
        foot.config(text='%s – %s   |   Satılan: %g adet   Ciro: %s TL   Kâr (KDV hariç): %s TL   (Azalan stok, sipariş ve satmayan kitaplar bugünün durumudur.)' % (
            OR.tr_date(d1), OR.tr_date(d2), t[1], fm(t[3]), fm(t[4])))

    def export():
        keys = [k for k in tabs]; k = keys[nb.index(nb.select())]; rep = tabs[k]['rep']
        if not rep:
            return
        p = filedialog.asksaveasfilename(parent=w, defaultextension='.csv', filetypes=[('Excel için CSV', '*.csv')], initialfile='kitap_raporu_%s.csv' % k)
        if p:
            export_csv(p, rep); messagebox.showinfo('Kitap raporları', 'Kaydedildi (Excel ile açılır):\n' + p, parent=w)
    tk.Button(top, text='LİSTELE', command=run, font=('Segoe UI', 10, 'bold'), bg='#0b6bcb', fg='white', padx=12).pack(side='left', padx=10)
    tk.Button(top, text='EXCEL\'E AKTAR (CSV)', command=export, font=('Segoe UI', 9, 'bold')).pack(side='left')
    tk.Button(top, text='KAPAT', command=w.destroy, font=('Segoe UI', 9, 'bold')).pack(side='right')
    run()
    w._rapor = dict(run=run, tabs=tabs, foot=foot, dates=(a, b), nb=nb)
    return w
