# -*- coding: utf-8 -*-
"""Yazarkasa (ÖKC / yeni nesil yazarkasa POS) gün sonu mutabakatı: cihazın Z raporundaki nakit ve kart toplamları,
programdaki aynı günün satış ödemeleriyle karşılaştırılır. Cihazla bağlantı kurmaz; satış/kasa kaydı değiştirmez."""
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

SCHEMA = """
CREATE TABLE IF NOT EXISTS okc_reconciliations(
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, device TEXT DEFAULT '', z_no TEXT DEFAULT '',
 z_cash REAL DEFAULT 0, z_card REAL DEFAULT 0, prog_cash REAL DEFAULT 0, prog_card REAL DEFAULT 0,
 diff_cash REAL DEFAULT 0, diff_card REAL DEFAULT 0, note TEXT DEFAULT '', created TEXT, user TEXT DEFAULT '');
"""


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def program_totals(c, day):
    """Gün içindeki net (iadeler düşülmüş) nakit ve kart satış ödemeleri. (nakit, kart) döndürür."""
    r = c.execute("""SELECT COALESCE(SUM(CASE WHEN p.kind='Nakit' THEN p.amount ELSE 0 END),0) cash,
                            COALESCE(SUM(CASE WHEN p.kind LIKE 'Kart%' THEN p.amount ELSE 0 END),0) card
                     FROM sale_payments p JOIN sales s ON s.id=p.sale_id
                     WHERE s.status IN ('FINAL','PARTIAL_RETURN','RETURN') AND substr(s.dt,1,10)=?""", (day,)).fetchone()
    return round(float(r[0] or 0), 2), round(float(r[1] or 0), 2)


def compare(c, day, z_cash, z_card):
    """Z raporu ile program toplamlarını karşılaştırır. fark = Z - program."""
    prog_cash, prog_card = program_totals(c, day)
    z_cash, z_card = round(float(z_cash), 2), round(float(z_card), 2)
    return {'prog_cash': prog_cash, 'prog_card': prog_card, 'z_cash': z_cash, 'z_card': z_card,
            'diff_cash': round(z_cash - prog_cash, 2), 'diff_card': round(z_card - prog_card, 2)}


def save(c, day, z_no, z_cash, z_card, device='', note='', user=''):
    ensure_schema(c)
    datetime.date.fromisoformat(day)
    if float(z_cash) < 0 or float(z_card) < 0:
        raise ValueError('Z raporu tutarları negatif olamaz.')
    r = compare(c, day, z_cash, z_card)
    cur = c.execute('INSERT INTO okc_reconciliations(day,device,z_no,z_cash,z_card,prog_cash,prog_card,diff_cash,diff_card,note,created,user) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (day, device, z_no, r['z_cash'], r['z_card'], r['prog_cash'], r['prog_card'], r['diff_cash'], r['diff_card'], note,
                     datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user))
    c.commit()
    return cur.lastrowid


def open_dialog(parent, connection_factory, user=''):
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Yazarkasa (ÖKC) Gün Sonu Mutabakatı')
    w.geometry('820x600')
    v = {k: tk.StringVar() for k in ('day', 'device', 'z_no', 'z_cash', 'z_card', 'note')}
    v['day'].set(datetime.date.today().isoformat())
    v['device'].set('Ingenico')
    f = ttk.Frame(w, padding=12)
    f.pack(fill='x')
    for i, (k, lab) in enumerate((('day', 'Tarih (YYYY-AA-GG)'), ('device', 'Cihaz'), ('z_no', 'Z No'), ('z_cash', 'Z Raporu NAKİT toplamı'), ('z_card', 'Z Raporu KART toplamı'), ('note', 'Not'))):
        ttk.Label(f, text=lab).grid(row=i, column=0, sticky='w', pady=4)
        ttk.Entry(f, textvariable=v[k], width=34).grid(row=i, column=1, sticky='w', padx=8)
    res = ttk.Label(w, font=('Segoe UI', 11, 'bold'), justify='left')
    res.pack(anchor='w', padx=14, pady=8)

    def num(k):
        t = v[k].get().strip().replace('.', '').replace(',', '.') if ',' in v[k].get() else v[k].get().strip()
        return float(t) if t else 0.0

    def check():
        try:
            datetime.date.fromisoformat(v['day'].get().strip())
            zc, zk = num('z_cash'), num('z_card')
        except ValueError:
            messagebox.showwarning('Mutabakat', 'Tarih YYYY-AA-GG, tutarlar sayı olmalıdır.', parent=w)
            return None
        con = connection_factory()
        try:
            r = compare(con, v['day'].get().strip(), zc, zk)
        finally:
            con.close()

        def line(n, z, p, d):
            return f"{n}:  Z raporu {z:,.2f}  |  Program {p:,.2f}  |  Fark {d:+,.2f}  " + ('✔ tutuyor' if abs(d) < 0.005 else '✖ FARK VAR')
        res.config(text=line('Nakit', r['z_cash'], r['prog_cash'], r['diff_cash']) + '\n' + line('Kart ', r['z_card'], r['prog_card'], r['diff_card']))
        return r

    def do_save():
        r = check()
        if r is None:
            return
        con = connection_factory()
        try:
            save(con, v['day'].get().strip(), v['z_no'].get().strip(), r['z_cash'], r['z_card'], v['device'].get().strip(), v['note'].get().strip(), user)
        except Exception as ex:
            return messagebox.showwarning('Mutabakat', str(ex), parent=w)
        finally:
            con.close()
        load()
    bf = ttk.Frame(w, padding=(12, 0))
    bf.pack(fill='x')
    ttk.Button(bf, text='KARŞILAŞTIR', command=check).pack(side='left', padx=(0, 8))
    ttk.Button(bf, text='KARŞILAŞTIR VE KAYDET', command=do_save).pack(side='left')
    t = ttk.Treeview(w, columns=('d', 'z', 'zn', 'pn', 'fn', 'zk', 'pk', 'fk'), show='headings', height=10)
    for k, h, wd in (('d', 'Gün', 90), ('z', 'Z No', 70), ('zn', 'Z Nakit', 90), ('pn', 'Prg Nakit', 90), ('fn', 'Fark', 80), ('zk', 'Z Kart', 90), ('pk', 'Prg Kart', 90), ('fk', 'Fark', 80)):
        t.heading(k, text=h)
        t.column(k, width=wd, anchor='w' if k in ('d', 'z') else 'e')
    t.pack(fill='both', expand=True, padx=12, pady=12)
    t.tag_configure('bad', foreground='#B00020')

    def load():
        t.delete(*t.get_children())
        con = connection_factory()
        try:
            for r in con.execute('SELECT * FROM okc_reconciliations ORDER BY day DESC,id DESC LIMIT 200').fetchall():
                bad = abs(r['diff_cash']) >= 0.005 or abs(r['diff_card']) >= 0.005
                t.insert('', 'end', values=(r['day'], r['z_no'], f"{r['z_cash']:,.2f}", f"{r['prog_cash']:,.2f}", f"{r['diff_cash']:+,.2f}", f"{r['z_card']:,.2f}", f"{r['prog_card']:,.2f}", f"{r['diff_card']:+,.2f}"), tags=(('bad',) if bad else ()))
        finally:
            con.close()
    load()
