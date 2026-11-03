# -*- coding: utf-8 -*-
"""ALFABE konsinye alış: mal tedarikçiye ait olarak teslim alınır (stok artar, cari borç doğmaz);
satılan kısım için hesap kesilir (tedarikçi cari alacağı), satılmayan kısım sezon sonu iade edilir (stok düşer)."""
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

SCHEMA = """
CREATE TABLE IF NOT EXISTS consignments(
 id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE, dt TEXT, supplier_id INTEGER NOT NULL, status TEXT DEFAULT 'Açık', note TEXT DEFAULT '', user TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS consignment_lines(
 id INTEGER PRIMARY KEY, cons_id INTEGER NOT NULL, stock_id INTEGER NOT NULL, qty REAL NOT NULL CHECK(qty>0), cost REAL NOT NULL CHECK(cost>=0),
 returned REAL DEFAULT 0, settled REAL DEFAULT 0, UNIQUE(cons_id,stock_id));
"""


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def _begin(c):
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')


def receive(c, supplier_id, items, note='', user=''):
    """items: [(stock_id, adet, birim_maliyet)]. Stok artar, cari etkilenmez. Belge id döndürür."""
    ensure_schema(c)
    if not items:
        raise ValueError('En az bir ürün girin.')
    if not c.execute('SELECT 1 FROM caris WHERE id=?', (supplier_id,)).fetchone():
        raise ValueError('Tedarikçi bulunamadı.')
    for sid, q, cost in items:
        if float(q) <= 0 or float(cost) < 0:
            raise ValueError('Adet sıfırdan büyük, maliyet negatif olmayan sayı olmalı.')
        if not c.execute('SELECT 1 FROM stock WHERE id=? AND active=1', (sid,)).fetchone():
            raise ValueError('Aktif olmayan veya bulunamayan ürün: %s' % sid)
    try:
        _begin(c)
        n = (c.execute('SELECT COALESCE(MAX(id),0) FROM consignments').fetchone()[0] or 0) + 1
        doc = 'KN-%s-%06d' % (datetime.date.today().year, n)
        cid = c.execute('INSERT INTO consignments(doc_no,dt,supplier_id,status,note,user) VALUES(?,?,?,?,?,?)', (doc, _now(), supplier_id, 'Açık', note, user)).lastrowid
        for sid, q, cost in items:
            q, cost = float(q), float(cost)
            c.execute('INSERT INTO consignment_lines(cons_id,stock_id,qty,cost) VALUES(?,?,?,?) ON CONFLICT(cons_id,stock_id) DO UPDATE SET qty=qty+excluded.qty,cost=excluded.cost', (cid, sid, q, cost))
            c.execute('UPDATE stock SET qty=qty+?,updated=? WHERE id=?', (q, _now(), sid))
            c.execute('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)', (sid, _now(), 'Konsinye Giriş', q, cost, 'CONSIGNMENT', cid, doc))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return cid


def outstanding(row):
    return float(row['qty']) - float(row['returned']) - float(row['settled'])


def lines(c, cons_id):
    """Satır özeti: kalan (tedarikçiye ait, rafta) ve satış tahmini."""
    ensure_schema(c)
    cons = c.execute('SELECT * FROM consignments WHERE id=?', (cons_id,)).fetchone()
    out = []
    for r in c.execute('SELECT l.*,s.name,s.qty stock_qty FROM consignment_lines l JOIN stock s ON s.id=l.stock_id WHERE l.cons_id=? ORDER BY s.name', (cons_id,)).fetchall():
        sold = c.execute("""SELECT COALESCE(SUM(sl.qty),0) FROM sale_lines sl JOIN sales sa ON sa.id=sl.sale_id
                            WHERE sl.stock_id=? AND sa.status IN ('FINAL','PARTIAL_RETURN','RETURN') AND sa.dt>=?""", (r['stock_id'], cons['dt'])).fetchone()[0]
        left = outstanding(r)
        out.append({'id': r['id'], 'stock_id': r['stock_id'], 'name': r['name'], 'qty': float(r['qty']), 'cost': float(r['cost']),
                    'returned': float(r['returned']), 'settled': float(r['settled']), 'left': left, 'stock_qty': float(r['stock_qty'] or 0),
                    'suggest': max(0.0, min(left, float(sold or 0) - float(r['settled'])))})
    return out


def settle(c, line_id, qty, user=''):
    """Satılan adet için hesap keser: tedarikçi cari alacağı (borcumuz) oluşur. Tutarı döndürür."""
    ensure_schema(c)
    qty = float(qty)
    r = c.execute('SELECT l.*,k.supplier_id,k.doc_no FROM consignment_lines l JOIN consignments k ON k.id=l.cons_id WHERE l.id=?', (line_id,)).fetchone()
    if r is None:
        raise ValueError('Satır bulunamadı.')
    if qty <= 0 or qty > outstanding(r) + 1e-9:
        raise ValueError('Adet 0 ile kalan miktar (%g) arasında olmalı.' % outstanding(r))
    amount = round(qty * float(r['cost']), 2)
    try:
        _begin(c)
        c.execute('UPDATE consignment_lines SET settled=settled+? WHERE id=?', (qty, line_id))
        c.execute('INSERT INTO cari_moves(cari_id,dt,kind,credit,source,source_id,note) VALUES(?,?,?,?,?,?,?)',
                  (r['supplier_id'], _now(), 'Konsinye Alış', amount, 'CONSIGNMENT', r['cons_id'], '%s / %g adet' % (r['doc_no'], qty)))
        _close_if_done(c, r['cons_id'])
        c.commit()
    except Exception:
        c.rollback()
        raise
    return amount


def give_back(c, line_id, qty, user=''):
    """Satılmayan konsinye malı tedarikçiye iade eder: stok düşer, cari etkilenmez."""
    ensure_schema(c)
    qty = float(qty)
    r = c.execute('SELECT l.*,k.doc_no FROM consignment_lines l JOIN consignments k ON k.id=l.cons_id WHERE l.id=?', (line_id,)).fetchone()
    if r is None:
        raise ValueError('Satır bulunamadı.')
    if qty <= 0 or qty > outstanding(r) + 1e-9:
        raise ValueError('Adet 0 ile kalan miktar (%g) arasında olmalı.' % outstanding(r))
    have = float(c.execute('SELECT qty FROM stock WHERE id=?', (r['stock_id'],)).fetchone()[0] or 0)
    if qty > have + 1e-9:
        raise ValueError('Stokta yeterli ürün yok (stok: %g). Önce stok sayımını kontrol edin.' % have)
    try:
        _begin(c)
        c.execute('UPDATE consignment_lines SET returned=returned+? WHERE id=?', (qty, line_id))
        c.execute('UPDATE stock SET qty=qty-?,updated=? WHERE id=?', (qty, _now(), r['stock_id']))
        c.execute('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)',
                  (r['stock_id'], _now(), 'Konsinye İade', -qty, float(r['cost']), 'CONSIGNMENT', r['cons_id'], r['doc_no']))
        _close_if_done(c, r['cons_id'])
        c.commit()
    except Exception:
        c.rollback()
        raise


def _close_if_done(c, cons_id):
    left = c.execute('SELECT COALESCE(SUM(qty-returned-settled),0) FROM consignment_lines WHERE cons_id=?', (cons_id,)).fetchone()[0]
    c.execute('UPDATE consignments SET status=? WHERE id=?', ('Kapandı' if left <= 1e-9 else 'Açık', cons_id))


def open_value(c):
    """Rafta duran, tedarikçiye ait konsinye mal değeri (maliyetle)."""
    ensure_schema(c)
    return round(float(c.execute('SELECT COALESCE(SUM((qty-returned-settled)*cost),0) FROM consignment_lines').fetchone()[0] or 0), 2)


def open_dialog(parent, connection_factory):
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Konsinye Alış / Sezon Sonu İade')
    w.geometry('1000x600')
    top = ttk.Frame(w, padding=10)
    top.pack(fill='x')
    info = ttk.Label(top, font=('Segoe UI', 10, 'bold'))
    info.pack(side='left')
    dt_ = ttk.Treeview(w, columns=('no', 'dt', 'sup', 'st'), show='headings', height=6)
    for k, h, wd in (('no', 'Belge', 140), ('dt', 'Tarih', 140), ('sup', 'Tedarikçi', 300), ('st', 'Durum', 90)):
        dt_.heading(k, text=h)
        dt_.column(k, width=wd)
    dt_.pack(fill='x', padx=10)
    lt = ttk.Treeview(w, columns=('n', 'q', 'c', 'r', 's', 'l', 'g'), show='headings', selectmode='browse')
    for k, h, wd in (('n', 'Ürün', 300), ('q', 'Gelen', 70), ('c', 'Maliyet', 80), ('r', 'İade', 70), ('s', 'Hesabı Kesilen', 110), ('l', 'Kalan (Rafta)', 100), ('g', 'Önerilen Satış', 110)):
        lt.heading(k, text=h)
        lt.column(k, width=wd, anchor='w' if k == 'n' else 'e')
    lt.pack(fill='both', expand=True, padx=10, pady=8)

    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()

    def load_docs():
        dt_.delete(*dt_.get_children())
        for r in db(lambda con: con.execute('SELECT k.*,c.title FROM consignments k LEFT JOIN caris c ON c.id=k.supplier_id ORDER BY k.id DESC').fetchall()):
            dt_.insert('', 'end', iid=str(r['id']), values=(r['doc_no'], str(r['dt'])[:16], r['title'] or '', r['status']))
        info.config(text='Rafta duran konsinye mal değeri (maliyet): %s ₺' % f"{db(open_value):,.2f}")

    def load_lines(*_):
        lt.delete(*lt.get_children())
        s = dt_.selection()
        if not s:
            return
        for r in db(lambda con: lines(con, int(s[0]))):
            lt.insert('', 'end', iid=str(r['id']), values=(r['name'], f"{r['qty']:g}", f"{r['cost']:.2f}", f"{r['returned']:g}", f"{r['settled']:g}", f"{r['left']:g}", f"{r['suggest']:g}"))

    def ask_qty(title, default):
        from tkinter import simpledialog
        v = simpledialog.askstring(title, 'Adet:', initialvalue=f'{default:g}', parent=w)
        if v is None:
            return None
        try:
            return float(v.replace(',', '.'))
        except ValueError:
            messagebox.showwarning(title, 'Adet sayı olmalıdır.', parent=w)
            return None

    def act(kind):
        s = lt.selection()
        if not s:
            return messagebox.showinfo('Konsinye', 'Listeden bir ürün satırı seçin.', parent=w)
        vals = lt.item(s[0], 'values')
        default = float(vals[6]) if kind == 'settle' else float(vals[5])
        q = ask_qty('Hesap Kes' if kind == 'settle' else 'Tedarikçiye İade', default)
        if q is None:
            return
        try:
            if kind == 'settle':
                amt = db(lambda con: settle(con, int(s[0]), q))
                messagebox.showinfo('Hesap Kes', f'Tedarikçi cari hesabına {amt:,.2f} ₺ alacak yazıldı.', parent=w)
            else:
                db(lambda con: give_back(con, int(s[0]), q))
        except Exception as ex:
            return messagebox.showwarning('Konsinye', str(ex), parent=w)
        load_docs()
        load_lines()
    bf = ttk.Frame(w, padding=10)
    bf.pack(fill='x')
    ttk.Button(bf, text='SATILAN İÇİN HESAP KES', command=lambda: act('settle')).pack(side='left', padx=(0, 8))
    ttk.Button(bf, text='TEDARİKÇİYE İADE (SEZON SONU)', command=lambda: act('return')).pack(side='left')
    ttk.Label(bf, text='Yeni konsinye girişi için Konsinye Giriş düğmesini kullanın.', foreground='#526477').pack(side='right')
    dt_.bind('<<TreeviewSelect>>', load_lines)
    load_docs()
    return w


def entry_dialog(parent, connection_factory, on_done=None):
    """Konsinye giriş: tedarikçi seç, barkodla ürün/adet/maliyet ekle, kaydet."""
    c = connection_factory()
    try:
        ensure_schema(c)
        sups = [(r['id'], r['title']) for r in c.execute("SELECT id,title FROM caris WHERE active=1 ORDER BY title").fetchall()]
    finally:
        c.close()
    if not sups:
        return messagebox.showwarning('Konsinye', 'Önce bir tedarikçi cari kartı açın.', parent=parent)
    w = tk.Toplevel(parent)
    w.title('Konsinye Giriş')
    w.geometry('760x520')
    sv = tk.StringVar()
    ttk.Label(w, text='Tedarikçi').pack(anchor='w', padx=12, pady=(12, 0))
    ttk.Combobox(w, textvariable=sv, values=[t for _i, t in sups], state='readonly', width=50).pack(anchor='w', padx=12)
    items = []
    tv = ttk.Treeview(w, columns=('n', 'q', 'c'), show='headings', height=10)
    for k, h, wd in (('n', 'Ürün', 380), ('q', 'Adet', 80), ('c', 'Birim Maliyet', 110)):
        tv.heading(k, text=h)
        tv.column(k, width=wd, anchor='w' if k == 'n' else 'e')
    tv.pack(fill='both', expand=True, padx=12, pady=10)
    row = ttk.Frame(w)
    row.pack(fill='x', padx=12)
    bk, qv, cv = tk.StringVar(), tk.StringVar(value='1'), tk.StringVar()
    ttk.Label(row, text='Barkod / Kod').pack(side='left')
    be = ttk.Entry(row, textvariable=bk, width=20)
    be.pack(side='left', padx=4)
    ttk.Label(row, text='Adet').pack(side='left')
    ttk.Entry(row, textvariable=qv, width=7).pack(side='left', padx=4)
    ttk.Label(row, text='Maliyet').pack(side='left')
    ttk.Entry(row, textvariable=cv, width=9).pack(side='left', padx=4)

    def add():
        con = connection_factory()
        try:
            key = bk.get().strip()
            r = con.execute("""SELECT DISTINCT s.id,s.name,s.avg_cost,s.last_cost FROM stock s LEFT JOIN stock_barcodes b ON b.stock_id=s.id AND b.active=1
                               WHERE s.active=1 AND (s.barcode=? OR s.code=? OR b.barcode=?)""", (key, key, key)).fetchall()
        finally:
            con.close()
        if len(r) != 1:
            return messagebox.showwarning('Konsinye', 'Tek bir aktif ürün bulunamadı.', parent=w)
        try:
            q = float(qv.get().replace(',', '.'))
            cost = float(cv.get().replace(',', '.')) if cv.get().strip() else float(r[0]['last_cost'] or r[0]['avg_cost'] or 0)
        except ValueError:
            return messagebox.showwarning('Konsinye', 'Adet ve maliyet sayı olmalıdır.', parent=w)
        if q <= 0 or cost < 0:
            return messagebox.showwarning('Konsinye', 'Adet sıfırdan büyük olmalıdır.', parent=w)
        items.append((r[0]['id'], q, cost))
        tv.insert('', 'end', values=(r[0]['name'], f'{q:g}', f'{cost:.2f}'))
        bk.set('')
        be.focus_set()

    def save():
        if not sv.get():
            return messagebox.showwarning('Konsinye', 'Tedarikçi seçin.', parent=w)
        sid = next(i for i, t in sups if t == sv.get())
        con = connection_factory()
        try:
            receive(con, sid, items)
        except Exception as ex:
            return messagebox.showwarning('Konsinye', str(ex), parent=w)
        finally:
            con.close()
        messagebox.showinfo('Konsinye', 'Konsinye giriş kaydedildi. Stok arttı; cari borç oluşmadı.', parent=w)
        w.destroy()
        if on_done:
            on_done()
    ttk.Button(row, text='EKLE', command=add).pack(side='left', padx=6)
    w.bind('<Return>', lambda e: add())
    ttk.Button(w, text='KAYDET', command=save).pack(pady=10)
    be.focus_set()
