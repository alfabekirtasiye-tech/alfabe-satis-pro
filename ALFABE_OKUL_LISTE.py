# -*- coding: utf-8 -*-
"""ALFABE okul ihtiyaç listesi: okul/sınıf bazlı hazır ürün listeleri; tek tıkla sepete eklenir.
Yalnız kendi tablolarına yazar; stok ve satış verisi değişmez."""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

SCHEMA = """
CREATE TABLE IF NOT EXISTS school_lists(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, school TEXT DEFAULT '', grade TEXT DEFAULT '', note TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS school_list_items(
 id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL, stock_id INTEGER NOT NULL, qty REAL NOT NULL CHECK(qty>0), UNIQUE(list_id,stock_id));
"""


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def create_list(c, name, school='', grade='', note=''):
    ensure_schema(c)
    name = ' '.join(str(name or '').split())
    if not name:
        raise ValueError('Liste adı boş olamaz.')
    if c.execute('SELECT 1 FROM school_lists WHERE name=?', (name,)).fetchone():
        raise ValueError('Bu adla bir liste zaten var.')
    cur = c.execute('INSERT INTO school_lists(name,school,grade,note,active) VALUES(?,?,?,?,1)', (name, school, grade, note))
    c.commit()
    return cur.lastrowid


def find_stock(c, key):
    key = str(key or '').strip()
    if not key:
        raise ValueError('Barkod veya stok kodu girin.')
    rows = c.execute("""SELECT DISTINCT s.id,s.name FROM stock s LEFT JOIN stock_barcodes b ON b.stock_id=s.id AND b.active=1
                        WHERE s.active=1 AND (s.barcode=? OR s.code=? OR b.barcode=?)""", (key, key, key)).fetchall()
    if len(rows) != 1:
        raise ValueError('Tek bir aktif ürün bulunamadı: ' + key)
    return rows[0]


def set_item(c, list_id, stock_id, qty):
    ensure_schema(c)
    qty = float(qty)
    if qty <= 0:
        raise ValueError('Adet sıfırdan büyük olmalı.')
    c.execute('INSERT INTO school_list_items(list_id,stock_id,qty) VALUES(?,?,?) ON CONFLICT(list_id,stock_id) DO UPDATE SET qty=excluded.qty',
              (list_id, stock_id, qty))
    c.commit()


def list_items(c, list_id):
    """[(stock_id, ad, adet, birim_fiyat, stok)] — pasif ürünler dahil edilmez."""
    ensure_schema(c)
    return [(r['stock_id'], r['name'], float(r['qty']), float(r['retail'] or 0), float(r['stock'] or 0)) for r in c.execute(
        """SELECT i.stock_id,s.name,i.qty,s.retail,s.qty stock FROM school_list_items i JOIN stock s ON s.id=i.stock_id
           WHERE i.list_id=? AND s.active=1 ORDER BY s.name""", (list_id,)).fetchall()]


def list_total(c, list_id):
    return round(sum(q * p for _s, _n, q, p, _st in list_items(c, list_id)), 2)


def open_dialog(parent, connection_factory, on_add=None):
    """Liste seçme/düzenleme penceresi. on_add([(stock_id, adet), ...]) sepete ekleme için çağrılır."""
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Okul İhtiyaç Listeleri')
    w.geometry('900x560')
    try:
        w.transient(parent)
    except Exception:
        pass
    left = ttk.Frame(w, padding=10)
    left.pack(side='left', fill='y')
    ttk.Label(left, text='LİSTELER', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
    lt = ttk.Treeview(left, columns=('n', 'g'), show='headings', height=16, selectmode='browse')
    lt.heading('n', text='Liste')
    lt.heading('g', text='Sınıf')
    lt.column('n', width=190)
    lt.column('g', width=70)
    lt.pack(pady=6)
    right = ttk.Frame(w, padding=10)
    right.pack(side='left', fill='both', expand=True)
    head = ttk.Label(right, text='Soldan liste seçin', font=('Segoe UI', 11, 'bold'))
    head.pack(anchor='w')
    it = ttk.Treeview(right, columns=('n', 'q', 'p', 's'), show='headings', selectmode='browse')
    for k, h, wd in (('n', 'Ürün', 330), ('q', 'Adet', 60), ('p', 'Birim Fiyat', 90), ('s', 'Stok', 70)):
        it.heading(k, text=h)
        it.column(k, width=wd, anchor='w' if k == 'n' else 'e')
    it.pack(fill='both', expand=True, pady=6)
    tot = ttk.Label(right, text='', font=('Segoe UI', 10, 'bold'))
    tot.pack(anchor='w')
    ent = ttk.Entry(right)
    ent.pack(fill='x', pady=(8, 2))
    ent.insert(0, 'Ürün barkodu / stok kodu')
    qv = ttk.Entry(right, width=8)
    qv.pack(anchor='w')
    qv.insert(0, '1')

    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()

    def cur_id():
        s = lt.selection()
        return int(s[0]) if s else None

    def load_lists():
        lt.delete(*lt.get_children())
        for r in db(lambda con: con.execute('SELECT id,name,grade FROM school_lists WHERE active=1 ORDER BY name').fetchall()):
            lt.insert('', 'end', iid=str(r['id']), values=(r['name'], r['grade']))

    def load_items(*_):
        it.delete(*it.get_children())
        lid = cur_id()
        if not lid:
            return
        rows = db(lambda con: list_items(con, lid))
        for sid, n, q, p, st in rows:
            it.insert('', 'end', iid=str(sid), values=(n, f'{q:g}', f'{p:,.2f}', f'{st:g}'))
        t = round(sum(q * p for _s, _n, q, p, _st in rows), 2)
        head.config(text=lt.item(str(lid), 'values')[0])
        tot.config(text=f'{len(rows)} kalem   •   Liste tutarı: {t:,.2f} ₺')

    def new_list():
        n = simpledialog.askstring('Yeni Liste', 'Liste adı (örn. Atatürk İlkokulu 3. Sınıf):', parent=w)
        if not n:
            return
        g = simpledialog.askstring('Yeni Liste', 'Sınıf (isteğe bağlı):', parent=w) or ''
        try:
            db(lambda con: create_list(con, n, grade=g))
        except Exception as ex:
            return messagebox.showwarning('Liste', str(ex), parent=w)
        load_lists()

    def add_item():
        lid = cur_id()
        if not lid:
            return messagebox.showinfo('Liste', 'Önce bir liste seçin.', parent=w)
        try:
            q = float(qv.get().replace(',', '.'))

            def f(con):
                r = find_stock(con, ent.get())
                set_item(con, lid, r['id'], q)
            db(f)
        except Exception as ex:
            return messagebox.showwarning('Liste', str(ex), parent=w)
        ent.delete(0, 'end')
        load_items()

    def del_item():
        lid, s = cur_id(), it.selection()
        if not lid or not s:
            return messagebox.showinfo('Liste', 'Silinecek ürünü seçin.', parent=w)

        def f(con):
            con.execute('DELETE FROM school_list_items WHERE list_id=? AND stock_id=?', (lid, int(s[0])))
            con.commit()
        db(f)
        load_items()

    def del_list():
        lid = cur_id()
        if not lid or not messagebox.askyesno('Liste', 'Seçili liste silinsin mi?', parent=w):
            return

        def f(con):
            con.execute('DELETE FROM school_list_items WHERE list_id=?', (lid,))
            con.execute('DELETE FROM school_lists WHERE id=?', (lid,))
            con.commit()
        db(f)
        load_lists()
        load_items()

    def to_cart():
        lid = cur_id()
        if not lid:
            return messagebox.showinfo('Liste', 'Önce bir liste seçin.', parent=w)
        rows = db(lambda con: list_items(con, lid))
        if not rows:
            return messagebox.showinfo('Liste', 'Listede ürün yok.', parent=w)
        if on_add:
            on_add([(sid, q) for sid, _n, q, _p, _st in rows])
        w.destroy()

    bf = ttk.Frame(right)
    bf.pack(fill='x', pady=8)
    for tx, cmd in (('ÜRÜN EKLE / GÜNCELLE', add_item), ('ÜRÜNÜ ÇIKAR', del_item), ('YENİ LİSTE', new_list), ('LİSTEYİ SİL', del_list)):
        ttk.Button(bf, text=tx, command=cmd).pack(side='left', padx=(0, 6))
    if on_add:
        ttk.Button(bf, text='SEPETE EKLE', command=to_cart).pack(side='right')
    lt.bind('<<TreeviewSelect>>', load_items)
    load_lists()
