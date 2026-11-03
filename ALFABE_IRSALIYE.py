# -*- coding: utf-8 -*-
"""ALFABE sevk irsaliyesi (satış ve alış). Belge satış/alış kayıtlarından kopyalanır; stok, cari ve kasa etkilenmez.
Bu belge yazdırılabilir kontrol/sevk belgesidir; resmî e-İrsaliye GİB/entegratör üzerinden düzenlenir."""
import datetime
import html as _html
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_notes(
 id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE, dt TEXT, kind TEXT NOT NULL, ref_id INTEGER, ref_doc TEXT, party_id INTEGER, party_name TEXT,
 plate TEXT DEFAULT '', driver TEXT DEFAULT '', note TEXT DEFAULT '', user TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS delivery_note_lines(
 id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL, line_no INTEGER, stock_id INTEGER, name TEXT, qty REAL, unit TEXT DEFAULT 'Adet');
"""
KINDS = ('Satış', 'Alış')


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _next_no(c):
    y = datetime.date.today().year
    r = c.execute("SELECT MAX(CAST(substr(doc_no,-6) AS INTEGER)) FROM delivery_notes WHERE doc_no LIKE ?", ('IRS-%d-%%' % y,)).fetchone()[0]
    return 'IRS-%d-%06d' % (y, (r or 0) + 1)


def create(c, kind, ref_id, plate='', driver='', note='', user=''):
    """Satış (sales.id) veya alış (purchases.id) kaydından irsaliye oluşturur. Aynı kayıt için ikinci irsaliye açılmaz. id döndürür."""
    ensure_schema(c)
    if kind not in KINDS:
        raise ValueError('Tür Satış veya Alış olmalıdır.')
    if c.execute('SELECT 1 FROM delivery_notes WHERE kind=? AND ref_id=?', (kind, ref_id)).fetchone():
        raise ValueError('Bu belge için zaten irsaliye düzenlenmiş.')
    if kind == 'Satış':
        h = c.execute("SELECT s.id,s.doc_no,s.status,s.customer_id pid,COALESCE(ca.title,'Perakende Müşteri') pname FROM sales s LEFT JOIN caris ca ON ca.id=s.customer_id WHERE s.id=?", (ref_id,)).fetchone()
        if h is None:
            raise ValueError('Satış bulunamadı.')
        if h['status'] not in ('FINAL', 'PARTIAL_RETURN'):
            raise ValueError('Bu satış için irsaliye düzenlenemez (durum: %s).' % h['status'])
        ls = c.execute("SELECT sl.stock_id,sl.name,sl.qty,COALESCE(st.unit,'Adet') unit FROM sale_lines sl LEFT JOIN stock st ON st.id=sl.stock_id WHERE sl.sale_id=? AND sl.qty>0 ORDER BY sl.id", (ref_id,)).fetchall()
    else:
        h = c.execute("SELECT p.id,p.doc_no,p.supplier_id pid,COALESCE(ca.title,'') pname FROM purchases p LEFT JOIN caris ca ON ca.id=p.supplier_id WHERE p.id=?", (ref_id,)).fetchone()
        if h is None:
            raise ValueError('Alış bulunamadı.')
        ls = c.execute("SELECT pl.stock_id,st.name,pl.qty,COALESCE(st.unit,'Adet') unit FROM purchase_lines pl LEFT JOIN stock st ON st.id=pl.stock_id WHERE pl.purchase_id=? AND pl.qty>0 ORDER BY pl.id", (ref_id,)).fetchall()
    if not ls:
        raise ValueError('Belgede irsaliyeye alınacak kalem yok.')
    try:
        if not c.in_transaction:
            c.execute('BEGIN IMMEDIATE')
        doc = _next_no(c)
        nid = c.execute('INSERT INTO delivery_notes(doc_no,dt,kind,ref_id,ref_doc,party_id,party_name,plate,driver,note,user) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (doc, _now(), kind, ref_id, h['doc_no'], h['pid'], h['pname'], plate, driver, note, user)).lastrowid
        for i, l in enumerate(ls, 1):
            c.execute('INSERT INTO delivery_note_lines(note_id,line_no,stock_id,name,qty,unit) VALUES(?,?,?,?,?,?)', (nid, i, l['stock_id'], l['name'] or '', float(l['qty']), l['unit']))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return nid


def render_html(c, note_id):
    n = c.execute('SELECT * FROM delivery_notes WHERE id=?', (note_id,)).fetchone()
    if n is None:
        raise ValueError('İrsaliye bulunamadı.')
    L = c.execute('SELECT * FROM delivery_note_lines WHERE note_id=? ORDER BY line_no', (note_id,)).fetchall()
    try:
        import ALFABE_EFATURA as E
        sel = E.seller_info(c)
    except Exception:
        sel = {'title': '', 'tax_id': '', 'tax_office': '', 'address': '', 'city': '', 'district': ''}
    e = _html.escape
    rows = ''.join('<tr><td>%s</td><td>%s</td><td class=r>%g</td><td>%s</td></tr>' % (l['line_no'], e(str(l['name'])), l['qty'], e(str(l['unit']))) for l in L)
    tot = sum(l['qty'] for l in L)
    who = 'ALICI' if n['kind'] == 'Satış' else 'GÖNDEREN (TEDARİKÇİ)'
    return ('<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>%s</title><style>body{font:13px Segoe UI,Arial;margin:28px;color:#102a4c}'
            'table{border-collapse:collapse;width:100%%}td,th{border:1px solid #b8c6d6;padding:6px 8px}th{background:#e6f1ff}.r{text-align:center}'
            '.box{border:1px solid #b8c6d6;padding:10px;margin:8px 0}h1{font-size:20px}.sg{display:flex;justify-content:space-between;margin-top:60px}.sg div{width:30%%;border-top:1px solid #444;text-align:center;padding-top:6px}</style></head><body>'
            '<h1>SEVK İRSALİYESİ</h1><div class=box><b>İrsaliye No:</b> %s &nbsp; <b>Tarih:</b> %s &nbsp; <b>İlgili Belge:</b> %s (%s)</div>'
            '<div class=box><b>DÜZENLEYEN:</b> %s<br>VKN: %s &nbsp; V.D.: %s<br>%s %s/%s</div>'
            '<div class=box><b>%s:</b> %s<br><b>Araç Plaka:</b> %s &nbsp; <b>Şoför:</b> %s</div>'
            '<table><tr><th>#</th><th>Mal Cinsi</th><th>Miktar</th><th>Birim</th></tr>%s<tr><th colspan=2 class=r>TOPLAM</th><th class=r>%g</th><th></th></tr></table>'
            '<p>%s</p><div class=sg><div>Teslim Eden</div><div>Taşıyıcı</div><div>Teslim Alan</div></div></body></html>') % (
        e(n['doc_no']), e(n['doc_no']), e(str(n['dt'])[:16]), e(str(n['ref_doc'] or '')), e(n['kind']),
        e(sel['title']), e(sel['tax_id']), e(sel['tax_office']), e(sel['address']), e(sel['district']), e(sel['city']),
        who, e(str(n['party_name'] or '')), e(str(n['plate'] or '')), e(str(n['driver'] or '')), rows, tot, e(str(n['note'] or '')))


def open_dialog(parent, connection_factory):
    import tempfile
    import webbrowser
    from pathlib import Path
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Sevk İrsaliyesi')
    w.geometry('980x620')
    kind = tk.StringVar(value='Satış')
    top = ttk.Frame(w, padding=10)
    top.pack(fill='x')
    ttk.Label(top, text='Belge türü:').pack(side='left')
    for k in KINDS:
        ttk.Radiobutton(top, text=k, value=k, variable=kind, command=lambda: load_src()).pack(side='left', padx=6)
    src = ttk.Treeview(w, columns=('no', 'dt', 'party', 'tot'), show='headings', height=9)
    for k, h, wd in (('no', 'Belge', 150), ('dt', 'Tarih', 140), ('party', 'Cari', 380), ('tot', 'Tutar', 110)):
        src.heading(k, text=h)
        src.column(k, width=wd, anchor='e' if k == 'tot' else 'w')
    src.pack(fill='x', padx=10)
    ttk.Label(w, text='Düzenlenmiş irsaliyeler', font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(10, 0))
    nt = ttk.Treeview(w, columns=('no', 'dt', 'kind', 'party', 'ref'), show='headings', height=8)
    for k, h, wd in (('no', 'İrsaliye', 150), ('dt', 'Tarih', 140), ('kind', 'Tür', 70), ('party', 'Cari', 330), ('ref', 'İlgili Belge', 150)):
        nt.heading(k, text=h)
        nt.column(k, width=wd)
    nt.pack(fill='both', expand=True, padx=10, pady=6)

    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()

    def load_src():
        src.delete(*src.get_children())
        if kind.get() == 'Satış':
            q = ("SELECT s.id,s.doc_no,s.dt,COALESCE(ca.title,'Perakende') p,s.total FROM sales s LEFT JOIN caris ca ON ca.id=s.customer_id "
                 "WHERE s.status IN ('FINAL','PARTIAL_RETURN') AND NOT EXISTS(SELECT 1 FROM delivery_notes d WHERE d.kind='Satış' AND d.ref_id=s.id) ORDER BY s.id DESC LIMIT 300")
        else:
            q = ("SELECT p.id,p.doc_no,p.dt,COALESCE(ca.title,'') p,p.total FROM purchases p LEFT JOIN caris ca ON ca.id=p.supplier_id "
                 "WHERE NOT EXISTS(SELECT 1 FROM delivery_notes d WHERE d.kind='Alış' AND d.ref_id=p.id) ORDER BY p.id DESC LIMIT 300")
        for r in db(lambda con: con.execute(q).fetchall()):
            src.insert('', 'end', iid=str(r['id']), values=(r['doc_no'], str(r['dt'])[:16], r['p'], f"{float(r['total'] or 0):,.2f}"))

    def load_notes():
        nt.delete(*nt.get_children())
        for r in db(lambda con: con.execute('SELECT * FROM delivery_notes ORDER BY id DESC LIMIT 300').fetchall()):
            nt.insert('', 'end', iid=str(r['id']), values=(r['doc_no'], str(r['dt'])[:16], r['kind'], r['party_name'] or '', r['ref_doc'] or ''))

    def make():
        s = src.selection()
        if not s:
            return messagebox.showinfo('İrsaliye', 'Listeden bir belge seçin.', parent=w)
        plate = simpledialog.askstring('İrsaliye', 'Araç plakası (isteğe bağlı):', parent=w) or ''
        driver = simpledialog.askstring('İrsaliye', 'Şoför / teslim eden (isteğe bağlı):', parent=w) or ''
        try:
            nid = db(lambda con: create(con, kind.get(), int(s[0]), plate.strip(), driver.strip()))
        except Exception as ex:
            return messagebox.showwarning('İrsaliye', str(ex), parent=w)
        load_src()
        load_notes()
        nt.selection_set(str(nid))
        show()

    def show():
        s = nt.selection()
        if not s:
            return messagebox.showinfo('İrsaliye', 'Önizlenecek irsaliyeyi seçin.', parent=w)
        doc = db(lambda con: render_html(con, int(s[0])))
        p = Path(tempfile.gettempdir()) / 'alfabe_irsaliye.html'
        p.write_text(doc, encoding='utf-8')
        webbrowser.open(p.resolve().as_uri())
    bf = ttk.Frame(w, padding=10)
    bf.pack(fill='x')
    ttk.Button(bf, text='SEÇİLİ BELGEDEN İRSALİYE OLUŞTUR', command=make).pack(side='left', padx=(0, 8))
    ttk.Button(bf, text='İRSALİYEYİ ÖNİZLE / YAZDIR', command=show).pack(side='left')
    load_src()
    load_notes()
