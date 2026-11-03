# -*- coding: utf-8 -*-
"""ALFABE fotokopi sayacı ve kağıt tüketimi: fotokopi makinesi sayaç okumaları, program satışlarıyla karşılaştırma
(kaçak/fire tespiti) ve hizmet bazında tüketim raporu. Satış/stok kayıtlarını değiştirmez."""
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

SCHEMA = """
CREATE TABLE IF NOT EXISTS copier_counters(
 id INTEGER PRIMARY KEY, dt TEXT NOT NULL, device TEXT NOT NULL DEFAULT 'Fotokopi', counter REAL NOT NULL, note TEXT DEFAULT '', user TEXT DEFAULT '');
"""
# Sayaç sayfa/yüz sayar; bu anahtar sözcükleri içeren hizmetler makinede sayfa harcar (tarama, laminasyon vb. harcamaz).
PAGE_WORDS = ('fotokopi', 'fotokopı', 'çıktı', 'cikti', 'baskı', 'baski', 'print', 'a4', 'a3', 'a5')
NO_PAGE_WORDS = ('tarama', 'lamin', 'cilt', 'spiral', 'kaplama', 'kimlik')


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def _lower(s):
    return str(s or '').replace('İ', 'i').replace('I', 'ı').lower()


def uses_machine(name, variant=''):
    t = _lower(name) + ' ' + _lower(variant)
    if any(w in t for w in NO_PAGE_WORDS) and not any(w in _lower(name) for w in ('fotokopi', 'çıktı', 'cikti', 'baskı', 'baski')):
        return False
    return any(w in t for w in PAGE_WORDS)


def add_reading(c, device, counter, dt=None, note='', user=''):
    ensure_schema(c)
    counter = float(counter)
    if counter < 0:
        raise ValueError('Sayaç negatif olamaz.')
    device = ' '.join(str(device or '').split()) or 'Fotokopi'
    dt = dt or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    last = c.execute('SELECT counter,dt FROM copier_counters WHERE device=? ORDER BY dt DESC,id DESC LIMIT 1', (device,)).fetchone()
    if last and dt >= last['dt'] and counter < float(last['counter']):
        raise ValueError('Sayaç önceki okumadan (%g) küçük olamaz. Sayaç sıfırlandıysa cihaz adını değiştirin (örn. Fotokopi-2).' % last['counter'])
    cur = c.execute('INSERT INTO copier_counters(dt,device,counter,note,user) VALUES(?,?,?,?,?)', (dt, device, counter, note, user))
    c.commit()
    return cur.lastrowid


def sold_pages(c, start_dt, end_dt):
    """İki zaman arasındaki makine sayfası harcayan hizmet satış adedi."""
    tot = 0.0
    for r in c.execute("""SELECT name,variant,SUM(qty) q FROM service_sales WHERE dt>? AND dt<=? GROUP BY name,variant""", (start_dt, end_dt)).fetchall():
        if uses_machine(r['name'], r['variant']):
            tot += float(r['q'] or 0)
    return tot


def analysis(c, device):
    """Ardışık okumalar arasında makine sayacı farkı ile program satışı karşılaştırması.
    [(başlangıç, bitiş, makine, satış, fark)] ; fark = makine - satış (pozitif = satılmadan basılan sayfa)."""
    ensure_schema(c)
    rs = c.execute('SELECT * FROM copier_counters WHERE device=? ORDER BY dt,id', (device,)).fetchall()
    out = []
    for a, b in zip(rs, rs[1:]):
        machine = float(b['counter']) - float(a['counter'])
        sold = sold_pages(c, a['dt'], b['dt'])
        out.append((a['dt'], b['dt'], machine, sold, machine - sold))
    return out


def consumption(c, d1, d2):
    """Tarih aralığında hizmet bazında adet ve ciro. [(hizmet, varyant, adet, ciro, makine_sayfası_mı)]"""
    rows = c.execute("""SELECT name,variant,SUM(qty) q,SUM(total) t FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ? GROUP BY name,variant ORDER BY q DESC""", (d1, d2)).fetchall()
    return [(r['name'], r['variant'] or '', float(r['q'] or 0), float(r['t'] or 0), uses_machine(r['name'], r['variant'])) for r in rows]


def open_dialog(parent, connection_factory, user=''):
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Fotokopi Sayacı ve Kağıt Tüketimi')
    w.geometry('900x620')
    nb = ttk.Notebook(w)
    nb.pack(fill='both', expand=True, padx=10, pady=10)

    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()
    # --- Sayaç ---
    f1 = ttk.Frame(nb, padding=10)
    nb.add(f1, text='Makine Sayacı')
    dev, cnt, note = tk.StringVar(value='Fotokopi'), tk.StringVar(), tk.StringVar()
    row = ttk.Frame(f1)
    row.pack(fill='x')
    for lab, var, wd in (('Cihaz', dev, 14), ('Sayaç', cnt, 12), ('Not', note, 24)):
        ttk.Label(row, text=lab).pack(side='left')
        ttk.Entry(row, textvariable=var, width=wd).pack(side='left', padx=(4, 10))
    t1 = ttk.Treeview(f1, columns=('a', 'b', 'm', 's', 'f'), show='headings', height=14)
    for k, h, wd in (('a', 'Başlangıç', 140), ('b', 'Bitiş', 140), ('m', 'Makine Sayfası', 110), ('s', 'Programda Satılan', 130), ('f', 'Fark (Satılmayan)', 130)):
        t1.heading(k, text=h)
        t1.column(k, width=wd, anchor='w' if k in ('a', 'b') else 'e')
    t1.tag_configure('bad', foreground='#B00020')
    info = ttk.Label(f1, foreground='#526477', wraplength=820)

    def load1():
        t1.delete(*t1.get_children())
        for a, b, m, s, d in reversed(db(lambda con: analysis(con, dev.get().strip() or 'Fotokopi'))):
            t1.insert('', 'end', values=(a[:16], b[:16], f'{m:g}', f'{s:g}', f'{d:+g}'), tags=(('bad',) if d > 0.5 else ()))

    def add():
        try:
            v = float(cnt.get().replace(',', '.'))
            db(lambda con: add_reading(con, dev.get(), v, note=note.get().strip(), user=user))
        except Exception as ex:
            return messagebox.showwarning('Sayaç', str(ex), parent=w)
        cnt.set('')
        load1()
    ttk.Button(row, text='SAYAÇ OKUMASI EKLE', command=add).pack(side='left')
    t1.pack(fill='both', expand=True, pady=10)
    info.config(text='Her gün açılış/kapanışta makinenin sayacını girin. Makine sayfası, aynı aralıkta programa girilen fotokopi/çıktı satışından fazlaysa fark kırmızı görünür (satılmadan basılan sayfa).')
    info.pack(anchor='w')
    # --- Tüketim ---
    f2 = ttk.Frame(nb, padding=10)
    nb.add(f2, text='Hizmet Tüketimi')
    a1 = tk.StringVar(value=datetime.date.today().replace(day=1).isoformat())
    a2 = tk.StringVar(value=datetime.date.today().isoformat())
    r2 = ttk.Frame(f2)
    r2.pack(fill='x')
    ttk.Label(r2, text='Başlangıç').pack(side='left')
    ttk.Entry(r2, textvariable=a1, width=11).pack(side='left', padx=4)
    ttk.Label(r2, text='Bitiş').pack(side='left', padx=(8, 0))
    ttk.Entry(r2, textvariable=a2, width=11).pack(side='left', padx=4)
    t2 = ttk.Treeview(f2, columns=('n', 'v', 'q', 't', 'm'), show='headings', height=15)
    for k, h, wd in (('n', 'Hizmet', 230), ('v', 'Varyant', 200), ('q', 'Adet', 90), ('t', 'Ciro', 110), ('m', 'Makine Sayfası', 100)):
        t2.heading(k, text=h)
        t2.column(k, width=wd, anchor='w' if k in ('n', 'v') else 'e')
    tot = ttk.Label(f2, font=('Segoe UI', 10, 'bold'))

    def load2():
        try:
            datetime.date.fromisoformat(a1.get().strip())
            datetime.date.fromisoformat(a2.get().strip())
        except ValueError:
            return messagebox.showwarning('Tarih', 'Tarihleri YYYY-AA-GG biçiminde girin.', parent=w)
        rows = db(lambda con: consumption(con, a1.get().strip(), a2.get().strip()))
        t2.delete(*t2.get_children())
        for n, v, q, t, m in rows:
            t2.insert('', 'end', values=(n, v, f'{q:g}', f'{t:,.2f}', 'Evet' if m else '-'))
        tot.config(text=f"Makine sayfası harcayan toplam: {sum(q for _n, _v, q, _t, m in rows if m):g} sayfa   •   Toplam ciro: {sum(t for _n, _v, _q, t, _m in rows):,.2f} ₺")
    ttk.Button(r2, text='LİSTELE', command=load2).pack(side='left', padx=8)
    t2.pack(fill='both', expand=True, pady=10)
    tot.pack(anchor='w')
    load1()
    load2()
