# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - KİTAP HIZLI GİRİŞ (v413)
Elle yazarak yeni kitap / kitap stoğu girişi için tek ekranlık, klavye odaklı form.
 - ISBN-10 yazılırsa ISBN-13'e çevrilir ve barkod olarak kullanılır; ISBN yoksa 290'lı iç barkod üretilir.
 - Kitap zaten kayıtlıysa (ISBN/barkod ya da aynı ad + yazar) yeni kart AÇILMAZ, adet eklenir.
 - Yeni kart: stok kartı + açılış stok hareketi + kitap bilgisi (yazar/yayınevi/ISBN) tek işlemde yazılır.
 - Yayınevi, grup, KDV, raf bir sonraki kitap için hatırlanır (KAYDET VE YENİ).
"""
import datetime
import json
import sqlite3

import ALFABE_STOK_EXCEL as XL
import ALFABE_STOK_EK_OZELLIKLER as EK

DEFAULT_GROUP = 'KİTAP'
DEFAULT_VAT = 0.0          # basılı kitapta KDV %0; ekranda değiştirilebilir


def _num(v, default=None):
    t = str(v if v is not None else '').strip().replace(' ', '')
    if t == '':
        return default
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
    else:
        t = t.replace(',', '.')
    return float(t)


def _fold(s):
    return XL.norm(s)


TPL_KEYS = ('author', 'publisher', 'vat', 'shelf', 'grp', 'cost', 'retail')


def ensure_tables(c):
    c.execute("CREATE TABLE IF NOT EXISTS stock_book_info(stock_id INTEGER PRIMARY KEY,author TEXT NOT NULL DEFAULT '',publisher TEXT NOT NULL DEFAULT '',isbn TEXT NOT NULL DEFAULT '')")
    if 'year' not in {r[1] for r in c.execute('PRAGMA table_info(stock_book_info)').fetchall()}:
        c.execute("ALTER TABLE stock_book_info ADD COLUMN year TEXT NOT NULL DEFAULT ''")
    if 'source' not in {r[1] for r in c.execute('PRAGMA table_info(stock_book_info)').fetchall()}:
        c.execute("ALTER TABLE stock_book_info ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE TABLE IF NOT EXISTS book_templates(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,data TEXT NOT NULL DEFAULT '{}',updated TEXT)")


def save_template(c, name, vals):
    """Şablon Kart: yayınevi, yazar, KDV, raf, grup, alış/satış fiyatı gibi sabit bilgileri adıyla kaydeder (aynı ad güncellenir)."""
    ensure_tables(c)
    name = ' '.join(str(name or '').split())
    if len(name) < 2:
        raise ValueError('Şablon adı en az 2 karakter olmalı.')
    data = {k: str(vals.get(k, '') or '').strip() for k in TPL_KEYS}
    if not any(data[k] for k in ('author', 'publisher', 'shelf', 'cost', 'retail')):
        raise ValueError('Şablona kaydedilecek sabit bilgi yok: yayınevi, yazar, raf veya fiyat yazın.')
    c.execute('INSERT INTO book_templates(name,data,updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data,updated=excluded.updated',
              (name, json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    c.commit()
    return name


def list_templates(c):
    ensure_tables(c)
    return [r[0] for r in c.execute('SELECT name FROM book_templates ORDER BY name COLLATE NOCASE').fetchall()]


def get_template(c, name):
    ensure_tables(c)
    r = c.execute('SELECT data FROM book_templates WHERE name=?', (name,)).fetchone()
    if not r:
        return None
    try:
        d = json.loads(r[0])
    except Exception:
        d = {}
    return {k: str(d.get(k, '') or '') for k in TPL_KEYS}


def delete_template(c, name):
    ensure_tables(c)
    c.execute('DELETE FROM book_templates WHERE name=?', (name,))
    c.commit()


def normalize_code(text):
    """Girilen ISBN/barkod -> (barkod, isbn13, uyarı). Geçerli ISBN-10/13 ise 13'lüye çevrilir."""
    raw = ''.join(str(text or '').split())
    if not raw:
        return '', '', ''
    i13 = EK.isbn_to13(raw)
    if i13:
        return i13, i13, ('ISBN-10, ISBN-13 olarak çevrildi: ' + i13) if EK.isbn_clean(raw) != i13 else ''
    return raw, '', 'ISBN kontrol hanesi tutmuyor; yazdığınız değer düz barkod olarak kullanılır.'


def find_existing(c, code='', name='', author=''):
    """Kayıtlı kart ara: önce barkod/ISBN, sonra ad+yazar. Dönüş: stock satırı dict ya da None."""
    ensure_tables(c)
    bc, i13, _ = normalize_code(code)
    cands = [x for x in {bc, i13, EK.isbn_clean(code)} if x]
    for x in cands:
        r = c.execute("SELECT * FROM stock WHERE barcode=? AND active=1 LIMIT 1", (x,)).fetchone()
        if r:
            return dict(r)
        r = c.execute("SELECT s.* FROM stock_barcodes b JOIN stock s ON s.id=b.stock_id WHERE b.barcode=? AND b.active=1 AND s.active=1 LIMIT 1", (x,)).fetchone()
        if r:
            return dict(r)
        r = c.execute("SELECT s.* FROM stock_book_info i JOIN stock s ON s.id=i.stock_id WHERE i.isbn=? AND s.active=1 LIMIT 1", (x,)).fetchone()
        if r:
            return dict(r)
    if name.strip():
        fn, fa = _fold(name), _fold(author)
        for r in c.execute("SELECT s.*,COALESCE(i.author,'') author FROM stock s LEFT JOIN stock_book_info i ON i.stock_id=s.id WHERE s.active=1").fetchall():
            if _fold(r['name']) == fn and _fold(r['author']) == fa:
                d = dict(r)
                d.pop('author', None)
                return d
    return None


def book_of(c, sid):
    r = c.execute('SELECT author,publisher,isbn,year FROM stock_book_info WHERE stock_id=?', (sid,)).fetchone()
    return dict(author=r[0], publisher=r[1], isbn=r[2], year=r[3]) if r else dict(author='', publisher='', isbn='', year='')


def suggestions(c, column):
    """Yayınevi / yazar açılır listesi için daha önce girilmiş değerler."""
    ensure_tables(c)
    col = 'publisher' if column == 'publisher' else 'author'
    return [r[0] for r in c.execute("SELECT %s FROM stock_book_info WHERE TRIM(%s)<>'' GROUP BY %s ORDER BY COUNT(*) DESC, %s LIMIT 400" % (col, col, col, col)).fetchall()]


def validate(e):
    """Form değerlerini kontrol eder, temizlenmiş dict döndürür; hata olursa ValueError."""
    d = dict(e)
    d['name'] = ' '.join(str(d.get('name') or '').split())
    if not d['name']:
        raise ValueError('Kitap adı yazılmalı.')
    d['author'] = ' '.join(str(d.get('author') or '').split())
    d['publisher'] = ' '.join(str(d.get('publisher') or '').split())
    d['grp'] = ' '.join(str(d.get('grp') or '').split()) or DEFAULT_GROUP
    d['shelf'] = str(d.get('shelf') or '').strip()
    d['year'] = str(d.get('year') or '').strip()
    if d['year'] and not (d['year'].isdigit() and len(d['year']) == 4 and 1000 <= int(d['year']) <= 2100):
        raise ValueError('Basım yılı 4 haneli bir yıl olmalı (ör. 2019).')
    try:
        d['qty'] = _num(d.get('qty'), 1.0)
        d['cost'] = _num(d.get('cost'))
        d['retail'] = _num(d.get('retail'))
        d['vat'] = _num(d.get('vat'), DEFAULT_VAT)
    except ValueError:
        raise ValueError('Adet / fiyat / KDV alanlarında sayı olmayan bir değer var.')
    if d['qty'] is None or d['qty'] <= 0 or d['qty'] > 1000000:
        raise ValueError('Adet 0\'dan büyük olmalı.')
    if d['qty'] != int(d['qty']):
        raise ValueError('Kitap adedi tam sayı olmalı.')
    for k, lab in (('cost', 'Alış fiyatı'), ('retail', 'Satış fiyatı')):
        if d[k] is not None and d[k] < 0:
            raise ValueError(lab + ' negatif olamaz.')
    if d['vat'] < 0 or d['vat'] > 100:
        raise ValueError('KDV oranı 0-100 arasında olmalı.')
    return d


def save_entry(c, e, user='', db_backup_folder=None):
    """Tek kitabı kaydeder.
    Dönüş: dict(action='YENİ'|'ADET EKLENDİ', sid, barcode, code, name, qty_total, msg)"""
    d = validate(e)
    ensure_tables(c)
    bc, i13, warn = normalize_code(d.get('code'))
    ex = find_existing(c, d.get('code'), d['name'], d['author'])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msgs = [warn] if warn else []
    if ex:
        sid = int(ex['id'])
        old_q = float(ex['qty'] or 0)
        add = d['qty']
        c.execute('BEGIN IMMEDIATE')
        try:
            new_avg = float(ex['avg_cost'] or 0)
            unit_cost = new_avg
            sets = {'qty': old_q + add, 'updated': now}
            if d['cost'] is not None:
                unit_cost = d['cost']
                base = max(old_q, 0)
                new_avg = round((base * new_avg + add * d['cost']) / (base + add), 4) if (base + add) > 0 else d['cost']
                sets['last_cost'] = d['cost']
                sets['avg_cost'] = new_avg
            if d['retail'] is not None and abs(float(ex['retail'] or 0) - d['retail']) > 0.00005:
                c.execute('INSERT INTO price_history(stock_id,dt,price_type,old_price,new_price,user,reason,batch_id) VALUES(?,?,?,?,?,?,?,?)',
                          (sid, now, 'Perakende', float(ex['retail'] or 0), d['retail'], user, 'Kitap hızlı giriş', 'KHG'))
                sets['retail'] = d['retail']
            if d['shelf'] and not (ex.get('shelf') or '').strip():
                sets['shelf'] = d['shelf']
            c.execute('UPDATE stock SET %s WHERE id=?' % ','.join(k + '=?' for k in sets), list(sets.values()) + [sid])
            c.execute('INSERT INTO stock_moves(stock_id,dt,kind,qty,unit_cost,source,source_id,note) VALUES(?,?,?,?,?,?,?,?)',
                      (sid, now, 'Giriş', add, unit_cost, 'KITAP_HIZLI', sid, 'Kitap hızlı giriş: adet eklendi'))
            bi = book_of(c, sid)
            if not bi['author'] and not bi['publisher'] and not bi['isbn']:
                c.execute('INSERT OR REPLACE INTO stock_book_info(stock_id,author,publisher,isbn,year) VALUES(?,?,?,?,?)', (sid, d['author'], d['publisher'], i13, d['year']))
                c.execute('UPDATE stock_book_info SET source=? WHERE stock_id=?', (str(d.get('source') or '').strip(), sid))
            else:
                c.execute('UPDATE stock_book_info SET author=CASE WHEN author=\'\' THEN ? ELSE author END,publisher=CASE WHEN publisher=\'\' THEN ? ELSE publisher END,'
                          'isbn=CASE WHEN isbn=\'\' THEN ? ELSE isbn END,year=CASE WHEN year=\'\' THEN ? ELSE year END WHERE stock_id=?', (d['author'], d['publisher'], i13, d['year'], sid))
            c.execute("UPDATE stock_book_info SET source=CASE WHEN source='' THEN ? ELSE source END WHERE stock_id=?", (str(d.get('source') or '').strip(), sid))
            c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                      (now, user, 'Stok', 'Kitap Hızlı Giriş (adet)', sid, json.dumps({'qty': old_q}), json.dumps({'qty': old_q + add}), ex['name']))
            c.commit()
        except Exception:
            c.rollback()
            raise
        msgs.append('Kitap zaten kayıtlı: yeni kart açılmadı, %g adet eklendi.' % add)
        return dict(action='ADET EKLENDİ', sid=sid, barcode=ex.get('barcode') or '', code=ex.get('code') or '', name=ex['name'], qty_total=old_q + add, msg=msgs)

    # --- yeni kart ---
    if not bc:
        bc = EK._gen_ean13(c)
        msgs.append('ISBN/barkod yazılmadı: iç barkod üretildi ' + bc)
    headers = ['barcode', 'name', 'brand', 'grp', 'unit', 'vat', 'shelf', 'qty', 'last_cost', 'avg_cost', 'retail']
    cells = [bc, d['name'], d['publisher'], d['grp'], 'Adet', d['vat'], d['shelf'], d['qty'],
             '' if d['cost'] is None else d['cost'], '' if d['cost'] is None else d['cost'], '' if d['retail'] is None else d['retail']]
    mapping = {k: i for i, k in enumerate(headers)}
    plan, summ = XL.build_plan(c, headers, [(1, cells)], mapping, mode='new', default_vat=d['vat'])
    p = plan[0]
    if p['action'] != 'YENİ':
        raise ValueError('Kayıt oluşturulamadı: ' + '; '.join(p['msg'] or [p['action']]))
    # stok kartı + açılış hareketi tek işlemde (kitap bilgisi ayrı ama hemen ardından; hata olursa kart geri alınır)
    res = XL.apply_plan(c, plan, user=user, update_qty=False, backup_folder=None, source_name='Kitap hızlı giriş')
    sid = c.execute('SELECT id FROM stock WHERE barcode=?', (bc,)).fetchone()[0]
    try:
        c.execute('UPDATE stock_moves SET note=? WHERE stock_id=? AND kind=? AND source=?', ('Kitap hızlı giriş (açılış)', sid, 'Açılış', 'STOCK'))
        c.execute('INSERT OR REPLACE INTO stock_book_info(stock_id,author,publisher,isbn,year) VALUES(?,?,?,?,?)', (sid, d['author'], d['publisher'], i13, d['year']))
        c.execute('UPDATE stock_book_info SET source=? WHERE stock_id=?', (str(d.get('source') or '').strip(), sid))
        c.commit()
    except Exception as ex2:
        c.rollback()
        raise RuntimeError('Kitap bilgisi yazılamadı: %s' % ex2)
    code = c.execute('SELECT code FROM stock WHERE id=?', (sid,)).fetchone()[0]
    return dict(action='YENİ', sid=sid, barcode=bc, code=code, name=d['name'], qty_total=d['qty'], msg=msgs)


def recent(c, limit=12):
    ensure_tables(c)
    return [dict(r) for r in c.execute(
        "SELECT s.id,s.barcode,s.name,COALESCE(i.author,'') author,COALESCE(i.year,'') year,COALESCE(i.source,'') source,s.qty,s.retail,s.updated FROM stock s LEFT JOIN stock_book_info i ON i.stock_id=s.id "
        "WHERE s.id IN (SELECT stock_id FROM stock_moves WHERE source='KITAP_HIZLI' OR note LIKE 'Kitap hızlı giriş%') ORDER BY s.updated DESC, s.id DESC LIMIT ?", (limit,)).fetchall()]


# ----------------------------------------------------------------------------------------------
# Ekran
# ----------------------------------------------------------------------------------------------
def _setting(c, k, default=''):
    try:
        r = c.execute('SELECT v FROM settings WHERE k=?', (k,)).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _set_setting(c, k, val):
    try:
        c.execute('CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)')
        c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)', (k, val))
        c.commit()
    except Exception:
        pass


def open_dialog(parent, connection_factory, user='', default_vat=None, on_done=None, money=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    w = tk.Toplevel(parent)
    w.title('Kitap Hızlı Giriş  -  sürüm 460.10')
    w.geometry('1020x800')
    w.minsize(900, 700)
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    w.configure(bg='#f4f7fb')
    con = connection_factory()
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass
    ensure_tables(con)
    fmt = money or (lambda v: ('%.2f' % float(v or 0)).replace('.', ','))
    sticky = {'publisher': '', 'grp': DEFAULT_GROUP, 'vat': ('%g' % DEFAULT_VAT).replace('.', ','), 'shelf': ''}
    v = {k: tk.StringVar() for k in ('code', 'name', 'author', 'publisher', 'qty', 'cost', 'retail', 'vat', 'grp', 'shelf', 'year', 'source')}
    v['tpl'] = tk.StringVar()
    v['kitapi'] = tk.IntVar(value=1 if _setting(con, 'kitap_kitapi_on', '0') == '1' else 0)
    v['online'] = tk.IntVar(value=0 if _setting(con, 'kitap_api_on', '1') == '0' else 1)
    state = {'dup': None, 'saved': 0, 'tpl': '', 'auto': False, 'last': None, 'busy': False}

    tk.Label(w, text='KİTAP HIZLI GİRİŞ', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    tk.Label(w, text='Kitabı yazın, Enter ile alanlar arasında ilerleyin, son alanda Enter = KAYDET VE YENİ. Kayıtlı kitap ise yeni kart açılmaz, adet eklenir.',
             font=('Segoe UI', 10), fg='#475569', bg='#f4f7fb', wraplength=960, justify='left').pack(anchor='w', padx=18, pady=(0, 8))

    box = tk.Frame(w, bg='white', highlightbackground='#cbd5e1', highlightthickness=1)
    box.pack(fill='x', padx=18)
    status = tk.Label(w, text='', font=('Segoe UI', 11, 'bold'), anchor='w', justify='left', bg='#f4f7fb', wraplength=960)

    order = []

    def field(row, col, label, key, width, colspan=1, combo=None):
        tk.Label(box, text=label, font=('Segoe UI', 9, 'bold'), fg='#334155', bg='white').grid(row=row * 2, column=col, sticky='w', padx=10, pady=(8, 0), columnspan=colspan)
        if combo is not None:
            e = ttk.Combobox(box, textvariable=v[key], values=combo, width=width, font=('Segoe UI', 12))
        else:
            e = tk.Entry(box, textvariable=v[key], width=width, font=('Segoe UI', 12), relief='solid', bd=1)
        e.grid(row=row * 2 + 1, column=col, sticky='we', padx=10, pady=(0, 6), columnspan=colspan)
        order.append(e)
        return e

    for i in range(4):
        box.grid_columnconfigure(i, weight=1)
    e_code = field(0, 0, 'ISBN / BARKOD (boş bırakılırsa otomatik üretilir)', 'code', 24)
    e_name = field(0, 1, 'KİTAP ADI *', 'name', 40, colspan=3)
    e_author = field(1, 0, 'YAZAR', 'author', 24, combo=suggestions(con, 'author'))
    e_pub = field(1, 1, 'YAYINEVİ', 'publisher', 24, combo=suggestions(con, 'publisher'))
    e_qty = field(1, 2, 'ADET *', 'qty', 8)
    e_shelf = field(1, 3, 'RAF', 'shelf', 12)
    e_cost = field(2, 0, 'ALIŞ FİYATI (KDV HARİÇ)', 'cost', 14)
    e_retail = field(2, 1, 'SATIŞ FİYATI (KDV DAHİL)', 'retail', 14)
    e_vat = field(2, 2, 'KDV %', 'vat', 8)
    e_grp = field(2, 3, 'GRUP', 'grp', 16)
    e_year = field(3, 0, 'BASKI YILI', 'year', 8)
    e_source = field(4, 0, 'KAYNAK FİRMA (bilgi / fiyat hangi firmadan alındı)', 'source', 24, colspan=2)
    tk.Label(box, text='ŞABLON KART (sabit bilgiler)', font=('Segoe UI', 9, 'bold'), fg='#334155', bg='white').grid(row=6, column=1, sticky='w', padx=10, pady=(8, 0), columnspan=2)
    cb_tpl = ttk.Combobox(box, textvariable=v['tpl'], values=list_templates(con), width=30, font=('Segoe UI', 11), state='readonly')
    cb_tpl.grid(row=7, column=1, sticky='we', padx=10, pady=(0, 8), columnspan=2)
    tfr = tk.Frame(box, bg='white')
    tfr.grid(row=7, column=3, sticky='w', padx=10, pady=(0, 8))
    tk.Button(tfr, text='ŞABLONU KAYDET', command=lambda: tpl_save(), font=('Segoe UI', 9, 'bold')).pack(side='left')
    tk.Button(tfr, text='SİL', command=lambda: tpl_delete(), font=('Segoe UI', 9, 'bold')).pack(side='left', padx=4)
    tk.Button(tfr, text='KAPAT', command=lambda: tpl_off(), font=('Segoe UI', 9, 'bold')).pack(side='left')

    chk = tk.Checkbutton(box, text='İnternetten kitap bilgisi getir', variable=v['online'], bg='white', font=('Segoe UI', 9),
                         command=lambda: _set_setting(con, 'kitap_api_on', '1' if v['online'].get() else '0'))
    chk.grid(row=10, column=0, columnspan=4, sticky='w', padx=10, pady=(0, 8))
    chk2 = tk.Checkbutton(box, text='Türkçe kitap kaynağını da dene (topluluk sunucusu, güvenilirliği garanti değil)', variable=v['kitapi'], bg='white', font=('Segoe UI', 9),
                          command=lambda: _set_setting(con, 'kitap_kitapi_on', '1' if v['kitapi'].get() else '0'))
    chk2.grid(row=11, column=0, columnspan=4, sticky='w', padx=10, pady=(0, 8))
    info = tk.Label(w, text='', font=('Segoe UI', 10), fg='#92400e', bg='#fffbeb', anchor='w', justify='left', wraplength=960)
    status.pack(fill='x', padx=18, pady=(8, 0))
    info.pack(fill='x', padx=18, pady=(4, 0))

    def reset_form(keep_sticky=True):
        for k in v:
            if k not in ('tpl', 'online', 'kitapi'):
                v[k].set('')
        if keep_sticky:
            for k, val in sticky.items():
                v[k].set(val)
        v['qty'].set('1')
        if not v['vat'].get():
            v['vat'].set(sticky['vat'])
        if not v['grp'].get():
            v['grp'].set(DEFAULT_GROUP)
        state['dup'] = None
        state['auto'] = False
        state['auto_vals'] = {}
        state['auto_code'] = ''
        state['manual'] = set()
        if state['tpl']:
            apply_tpl(state['tpl'], quiet=True)
        info.config(text='', bg='#f4f7fb')

    def apply_tpl(name, quiet=False):
        d = get_template(con, name)
        if not d:
            return False
        for k in TPL_KEYS:
            if d[k] != '' or k in ('author',):
                v[k].set(d[k])
        v['qty'].set('1')
        state['tpl'] = name
        v['tpl'].set(name)
        if not quiet:
            status.config(text='Şablon uygulandı: %s. Sadece kitap adı/ISBN ve adet yazın.' % name, fg='#15803d')
        return True

    def on_tpl(_=None):
        n = v['tpl'].get()
        if n:
            apply_tpl(n)
            e_code.focus_set()

    cb_tpl.bind('<<ComboboxSelected>>', on_tpl)

    def tpl_save():
        import tkinter.simpledialog as sd
        name = sd.askstring('Şablon Kart', 'Şablon adı (örn. İş Bankası Kültür Yayınları):', initialvalue=v['tpl'].get() or v['publisher'].get(), parent=w)
        if not name:
            return
        try:
            nm = save_template(con, name, {k: v[k].get() for k in TPL_KEYS})
        except ValueError as ex:
            messagebox.showwarning('Şablon Kart', str(ex), parent=w)
            return
        cb_tpl.config(values=list_templates(con))
        state['tpl'] = nm
        v['tpl'].set(nm)
        status.config(text='✓ Şablon kaydedildi: %s' % nm, fg='#15803d')

    def tpl_delete():
        n = v['tpl'].get()
        if not n:
            return
        if not messagebox.askyesno('Şablon Kart', '"%s" şablonu silinsin mi? (Kitaplara dokunulmaz.)' % n, parent=w):
            return
        delete_template(con, n)
        state['tpl'] = ''
        v['tpl'].set('')
        cb_tpl.config(values=list_templates(con))
        status.config(text='Şablon silindi: %s' % n, fg='#475569')

    def tpl_off():
        state['tpl'] = ''
        v['tpl'].set('')
        status.config(text='Şablon kapatıldı.', fg='#475569')

    def copy_prev():
        d = state['last']
        if not d:
            status.config(text='Kopyalanacak önceki kitap yok; önce bir kitap kaydedin.', fg='#b91c1c')
            return
        for k in TPL_KEYS:
            v[k].set(d[k])
        status.config(text='Önceki kitabın sabit bilgileri kopyalandı (yazar, yayınevi, KDV, raf, grup, fiyat). Kitap adı ve ISBN yazın.', fg='#15803d')
        e_code.focus_set()

    def fill_from(d):
        """İnternet/önbellek sonucunu YALNIZ boş alanlara yazar. Dönüş: doldurulan alan sayısı."""
        n = 0
        filled = state.setdefault('auto_vals', {})
        for k, kk in (('name', 'title'), ('author', 'author'), ('publisher', 'publisher'), ('year', 'year')):
            if d.get(kk) and not v[k].get().strip():
                import ALFABE_ISBN as _IU
                v[k].set(d[kk] if k == 'year' else _IU.tr_upper(d[kk]))
                filled[k] = v[k].get()
                n += 1
        try:
            src_name = str(d.get('source') or '').replace('(kayıtlı)', '').strip()
            if src_name and not v['source'].get().strip():
                v['source'].set(src_name)
                filled['source'] = src_name
        except Exception:
            pass
        # Ürün sayfası kaynağı fiyat da veriyorsa (yayıncı/dağıtıcı fiyatı) boş SATIŞ FİYATINA yazılır.
        try:
            if d.get('price') and not v['retail'].get().strip():
                v['retail'].set(('%.2f' % float(d['price'])).replace('.', ','))
                filled['retail'] = v['retail'].get()
                n += 1
        except Exception:
            pass
        return n

    def do_lookup(i13):
        import ALFABE_ISBN as IS
        c0 = None
        try:
            c0 = IS.cache_get(con, i13)
        except Exception:
            c0 = None
        if c0 and not c0.get('price'):
            # Kayıtlı bilgide fiyat yok: ürün sayfası kaynağı (URUN) tanımlıysa fiyat için yeniden aranır; bulunamazsa kayıtlı bilgi kullanılır.
            try:
                if v['online'].get() and any(x['aktif'] and x['tur'] == 'URUN' for x in IS.sources_load(con)):
                    state['cache_fb'] = c0
                    c0 = None
            except Exception:
                pass
        if c0:
            return done_lookup(i13, c0)
        if not v['online'].get():
            return
        if state['busy']:
            info.config(text='Önceki internet araması sürüyor; bu barkod için bekleyin ya da bilgileri elle yazın.', bg='#fffbeb')
            return
        state['busy'] = True
        info.config(text='İnternetten kitap bilgisi aranıyor…', bg='#eff6ff')
        import threading, queue
        q = queue.Queue()
        sru = _setting(con, 'kitap_sru_url', '')
        kitapi_on = bool(v['kitapi'].get())
        srcs = IS.sources_load(con)

        def work():
            try:
                q.put(IS.lookup(i13, sru_url=sru, kitapi=kitapi_on, sources=srcs))
            except Exception:
                q.put(None)
        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                r = q.get_nowait()
            except Exception:
                try:
                    w.after(120, poll)
                except Exception:
                    pass
                return
            state['busy'] = False
            try:
                if not w.winfo_exists():
                    return
            except Exception:
                return
            if not r and state.get('cache_fb'):
                r = state['cache_fb']
            state['cache_fb'] = None
            _dbg('lookup', isbn=i13, result=(r or {}).get('title'), source=(r or {}).get('source'))
            if r:
                try:
                    IS.cache_put(con, i13, r)
                except Exception:
                    pass
            if v['code'].get().strip() != i13:
                return
            done_lookup(i13, r)
        w.after(120, poll)

    def done_lookup(i13, r):
        if state['dup']:
            return
        if not r:
            try:
                import ALFABE_ISBN as _I
                st, er = _I.last_status, _I.last_error
            except Exception:
                st, er = '', ''
            if st == 'ag':
                msg = 'İnternet kaynaklarına ulaşılamadı (bağlantı/güvenlik duvarı?). Kitap adını elle yazabilirsiniz.' + (' [%s]' % er if er else '')
            else:
                msg = 'Bu ISBN ücretsiz kitap kaynaklarında (Google Books, Open Library) kayıtlı değil; Türkçe kitaplarda sık olur. Kitap adını elle yazın.'
            info.config(text=msg, bg='#fffbeb')
            return
        was_empty = not v['name'].get().strip()
        n = fill_from(r)
        if n:
            state['auto'] = was_empty and bool(v['name'].get().strip())
            info.config(text='✓ Bilgiler otomatik dolduruldu (%s, ISBN %s). Kontrol edin; yalnız ADET yazıp Enter ile kaydedebilirsiniz.' % (r.get('source', ''), i13), bg='#ecfdf5')
            if was_empty:
                e_qty.focus_set()
                e_qty.select_range(0, 'end')

    def open_catalog():
        import webbrowser
        import ALFABE_ISBN as IS
        code = normalize_code(v['code'].get())[1] or v['code'].get().strip()
        allsrc = [x for x in IS.sources_load(con) if x['aktif']]
        pages = [x for x in allsrc if x['tur'] in ('SAYFA', 'URUN')]
        if not pages:
            # Tür yanlış seçilmiş olabilir (ör. bir web sitesi SRU olarak eklenmiş): adresi yine de tarayıcıda aç.
            pages = [x for x in allsrc if str(x['url']).lower().startswith(('http://', 'https://'))]
        if not pages:
            messagebox.showinfo('Katalogda ara', 'Kayıtlı arama sayfası yok. "KİTAP KAYNAKLARI…" düğmesinden bir site adresi ekleyin (Tür = SAYFA; arama adresinde ISBN yerine {isbn} yazın).', parent=w)
            return
        if not code:
            messagebox.showinfo('Katalogda ara', 'Önce ISBN / barkod yazın.', parent=w)
            return
        src = pages[0]
        if '{isbn}' not in str(src['url']):
            # Arama kalıbı yoksa siteyi aç, ISBN'i panoya kopyala (sitenin arama kutusuna Ctrl+V ile yapıştırılır).
            try:
                w.clipboard_clear()
                w.clipboard_append(code)
            except Exception:
                pass
            webbrowser.open(src['url'])
            messagebox.showinfo('Katalogda ara', '%s sitesi açıldı.\n\nISBN / barkod panoya kopyalandı: %s\nSitenin arama kutusuna Ctrl+V ile yapıştırın.' % (src.get('ad', ''), code), parent=w)
            return
        webbrowser.open(IS.page_url(src, code))

    def sources_dialog():
        import ALFABE_ISBN as IS
        d = tk.Toplevel(w)
        d.title('Kitap Arama Adresleri')
        d.geometry('820x520')
        d.transient(w)
        d.configure(bg='#f4f7fb')
        tk.Label(d, text='KİTAP ARAMA ADRESLERİ', font=('Segoe UI', 15, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=14, pady=(12, 0))
        tk.Label(d, text='Tür SRU: kütüphane SRU adresi (Koha vb.). Tür JSON: ISBN\'in yazılacağı yere {isbn} koyun, yanıt JSON olmalı. '
                         'Tür SAYFA: sitenin arama adresi, {isbn} yerine ISBN yazılır ve tarayıcıda açılır (KATALOGDA ARA). Tür URUN: ürün sayfası adresi ({isbn} yerine ISBN); kitap adı, yazar, yayınevi ve fiyat sayfadan otomatik okunur.',
                 font=('Segoe UI', 9), fg='#475569', bg='#f4f7fb', wraplength=780, justify='left').pack(anchor='w', padx=14, pady=(0, 8))
        lst = IS.sources_load(con)
        tv2 = ttk.Treeview(d, columns=('ad', 'tur', 'url', 'aktif'), show='headings', height=8)
        for k, t, wd in (('ad', 'Ad', 150), ('tur', 'Tür', 60), ('url', 'Adres', 480), ('aktif', 'Açık', 50)):
            tv2.heading(k, text=t)
            tv2.column(k, width=wd, anchor='w')
        tv2.pack(fill='x', padx=14)

        def draw():
            tv2.delete(*tv2.get_children())
            for x in lst:
                tv2.insert('', 'end', values=(x['ad'], x['tur'], x['url'], 'Evet' if x['aktif'] else 'Hayır'))
        draw()
        sv = {k: tk.StringVar() for k in ('ad', 'tur', 'url')}
        sv['tur'].set('SRU')
        fr = tk.Frame(d, bg='#f4f7fb')
        fr.pack(fill='x', padx=14, pady=10)
        tk.Label(fr, text='Ad', bg='#f4f7fb', font=('Segoe UI', 9, 'bold')).grid(row=0, column=0, sticky='w')
        tk.Entry(fr, textvariable=sv['ad'], width=24, font=('Segoe UI', 11)).grid(row=1, column=0, padx=(0, 8))
        tk.Label(fr, text='Tür', bg='#f4f7fb', font=('Segoe UI', 9, 'bold')).grid(row=0, column=1, sticky='w')
        ttk.Combobox(fr, textvariable=sv['tur'], values=('SRU', 'JSON', 'SAYFA', 'URUN'), width=8, state='readonly').grid(row=1, column=1, padx=(0, 8))
        tk.Label(fr, text='Adres (http:// veya https:// ile başlamalı)', bg='#f4f7fb', font=('Segoe UI', 9, 'bold')).grid(row=0, column=2, sticky='w')
        tk.Entry(fr, textvariable=sv['url'], width=52, font=('Segoe UI', 11)).grid(row=1, column=2)
        msg = tk.Label(d, text='', bg='#f4f7fb', fg='#b91c1c', font=('Segoe UI', 10, 'bold'))
        msg.pack(anchor='w', padx=14)

        def persist():
            IS.sources_save(con, lst)
            draw()

        def add():
            u = sv['url'].get().strip()
            if not u.lower().startswith(('http://', 'https://')):
                msg.config(text='Adres http:// veya https:// ile başlamalı.')
                return
            lst.append(dict(ad=sv['ad'].get().strip() or 'Kaynak', tur=sv['tur'].get(), url=u, aktif=1))
            persist()
            msg.config(text='')
            sv['ad'].set('')
            sv['url'].set('')

        def sel():
            x = tv2.selection()
            return tv2.index(x[0]) if x else None

        def delete():
            i = sel()
            if i is not None:
                del lst[i]
                persist()

        def toggle():
            i = sel()
            if i is not None:
                lst[i]['aktif'] = 0 if lst[i]['aktif'] else 1
                persist()
        bf = tk.Frame(d, bg='#f4f7fb')
        bf.pack(fill='x', padx=14)
        tk.Button(bf, text='EKLE', command=add, font=('Segoe UI', 10, 'bold'), bg='#0b6bcb', fg='white', padx=14, pady=6).pack(side='left')
        tk.Button(bf, text='AÇ / KAPAT', command=toggle, font=('Segoe UI', 10, 'bold'), padx=10, pady=6).pack(side='left', padx=6)
        tk.Button(bf, text='SİL', command=delete, font=('Segoe UI', 10, 'bold'), padx=10, pady=6).pack(side='left')
        tk.Button(bf, text='KAPAT', command=d.destroy, font=('Segoe UI', 10, 'bold'), padx=10, pady=6).pack(side='right')
        d._src = dict(vars=sv, add=add, lst=lst)
        w._src_dialog = d

    def _dbg(*a, **kw):
        # Hata ayıklama günlüğü: %TEMP%\alfabe_kitap_giris_log.txt (son olaylar)
        try:
            import os as _o, tempfile as _t, datetime as _d
            with open(_o.path.join(_t.gettempdir(), 'alfabe_kitap_giris_log.txt'), 'a', encoding='utf-8') as fh:
                fh.write('%s %s %s\n' % (_d.datetime.now().strftime('%H:%M:%S'), a[0] if a else '', kw))
        except Exception:
            pass

    def _mark_manual(key):
        def f(ev=None):
            if ev is not None and getattr(ev, 'keysym', '') in ('Return', 'Tab', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'ISO_Left_Tab'):
                return
            state.setdefault('manual', set()).add(key)
        return f

    def check_code(_=None):
        """ISBN/barkod alanından çıkınca: kayıtlı mı bak, bilgileri getir."""
        code = v['code'].get().strip()
        if not code:
            return
        bc, i13, warn = normalize_code(code)
        if i13 and v['code'].get().strip() != i13:
            v['code'].set(i13)
        # Barkod değişti: önceki barkoddan OTOMATİK gelen bilgiler (kitap adı, yazar, yayınevi, yıl, fiyat) silinir; elle yazılanlara dokunulmaz.
        _cur = i13 or bc
        _dbg('check_code', code=code, cur=_cur, prev=state.get('auto_code'), name=v['name'].get(), manual=sorted(state.get('manual', set())))
        if state.get('auto_code') != _cur:
            # Barkod değişti (ya da ilk okutma): elle YAZILMAMIŞ kitap adı / yıl, nereden gelirse gelsin silinir.
            for k in ('name', 'year'):
                if k not in state.get('manual', set()):
                    v[k].set('')
            state['dup'] = None
        if state.get('auto_code') and state['auto_code'] != _cur:
            for k, val in list(state.get('auto_vals', {}).items()):
                if v[k].get().strip() == val:
                    v[k].set('')
            state['auto_vals'] = {}
            state['auto'] = False
            state['dup'] = None
        state['auto_code'] = _cur
        ex = find_existing(con, code)
        if ex:
            state['dup'] = ex['id']
            bi = book_of(con, int(ex['id']))
            _av = state.setdefault('auto_vals', {})
            if not v['name'].get().strip():
                v['name'].set(ex['name']);_av['name'] = v['name'].get()
            if not v['author'].get().strip():
                v['author'].set(bi['author']);_av['author'] = v['author'].get()
            if not v['publisher'].get().strip():
                v['publisher'].set(bi['publisher'] or (ex.get('brand') or ''));_av['publisher'] = v['publisher'].get()
            info.config(text='KAYITLI KİTAP: %s  |  Stokta: %g adet  |  Satış: %s TL.  Kaydedince yeni kart açılmaz, yazdığınız adet mevcut stoğa eklenir.' % (
                ex['name'], float(ex['qty'] or 0), fmt(ex['retail'])), bg='#fffbeb')
        else:
            state['dup'] = None
            info.config(text=warn, bg='#fffbeb' if warn else '#f4f7fb')
            if i13 and not v['name'].get().strip():
                do_lookup(i13)

    def check_name(_=None):
        if v['code'].get().strip() or not v['name'].get().strip():
            return
        ex = find_existing(con, '', v['name'].get(), v['author'].get())
        if ex:
            state['dup'] = ex['id']
            info.config(text='Bu ad ve yazarla KAYITLI KİTAP var: %s (stokta %g adet). Kaydedince adet eklenir.' % (ex['name'], float(ex['qty'] or 0)), bg='#fffbeb')

    e_name.bind('<KeyPress>', _mark_manual('name'), add='+')
    e_code.bind('<FocusOut>', check_code)
    e_name.bind('<FocusOut>', check_name)
    e_author.bind('<FocusOut>', check_name)

    def save(_=None):
        check_code()
        try:
            r = save_entry(con, {k: v[k].get() for k in v if k not in ('tpl', 'online', 'kitapi')}, user=user)
        except ValueError as ex:
            status.config(text='✗ ' + str(ex), fg='#b91c1c')
            return 'break'
        except Exception as ex:
            messagebox.showerror('Kitap Hızlı Giriş', 'Kayıt yapılamadı, hiçbir değişiklik yapılmadı:\n%s' % ex, parent=w)
            return 'break'
        state['saved'] += 1
        for k in ('publisher', 'grp', 'vat', 'shelf'):
            sticky[k] = v[k].get().strip() or sticky[k]
        state['last'] = {k: v[k].get().strip() for k in TPL_KEYS}
        tag = '✓ YENİ KART: ' if r['action'] == 'YENİ' else '✓ ADET EKLENDİ: '
        status.config(text='%s%s  (barkod %s, stok %g)%s' % (tag, r['name'], r['barcode'], r['qty_total'], ('\n' + ' '.join(r['msg'])) if r['msg'] else ''), fg='#15803d')
        reset_form()
        refresh_list()
        e_code.focus_set()
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return 'break'

    # Enter ile ilerle; son alanda kaydet
    def after_code(ev):
        check_code()
        if state['dup']:
            e_qty.focus_set()
        elif not state['auto']:
            e_name.focus_set()
        return 'break'

    def after_qty(ev):
        # Bilgiler otomatik geldiyse ve fiyat girilmişse (veya kayıtlı kitapsa) yalnız adetle kaydet
        if (state['auto'] or state['dup']) and (state['dup'] or v['retail'].get().strip()):
            return save()
        e_shelf.focus_set()
        return 'break'

    for i, e in enumerate(order):
        nxt = order[i + 1] if i + 1 < len(order) else None
        e.bind('<Return>', (lambda n: (lambda ev: (n.focus_set(), 'break')[1]))(nxt) if nxt else save)
    e_code.bind('<Return>', after_code)
    e_qty.bind('<Return>', after_qty)
    w.bind('<F2>', lambda ev: (copy_prev(), 'break')[1])
    w.bind('<Control-s>', save)
    w.bind('<Control-S>', save)

    btns = tk.Frame(w, bg='#f4f7fb')
    btns.pack(fill='x', padx=18, pady=8)
    tk.Button(btns, text='KAYDET VE YENİ  (Ctrl+S)', command=save, font=('Segoe UI', 12, 'bold'), bg='#0b6bcb', fg='white', activebackground='#0a58a8', activeforeground='white', relief='flat', padx=18, pady=8).pack(side='left')
    tk.Button(btns, text='ÖNCEKİ KİTAPTAN KOPYALA (F2)', command=copy_prev, font=('Segoe UI', 10, 'bold'), padx=10, pady=8).pack(side='left', padx=(8, 0))
    tk.Button(btns, text='KİTAP KAYNAKLARI…', command=lambda: sources_dialog(), font=('Segoe UI', 10, 'bold'), padx=10, pady=8).pack(side='left', padx=(8, 0))
    def _rapor():
        import ALFABE_KITAP_RAPOR as KR
        KR.open_dialog(w, connection_factory, money=money)
    tk.Button(btns, text='KİTAP RAPORLARI', command=_rapor, font=('Segoe UI', 10, 'bold'), padx=10, pady=8).pack(side='left', padx=(8, 0))
    tk.Button(btns, text='KATALOGDA ARA', command=lambda: open_catalog(), font=('Segoe UI', 10, 'bold'), padx=10, pady=8).pack(side='left', padx=(8, 0))
    lst = {'cut': ''}

    def iptal():
        # İPTAL: formu temizler ve "Son girilen kitaplar" listesini boşaltır (kaydedilmiş kitap kartları SİLİNMEZ; yalnız bu listeden kalkar).
        reset_form(False)
        status.config(text='')
        try:
            rows = recent(con, 200)
            if rows:
                lst['cut'] = max(str(r['updated'] or '') for r in rows)
        except Exception:
            pass
        refresh_list()
        e_code.focus_set()
    tk.Button(btns, text='İPTAL', command=iptal, bg='#dc2626', fg='white', activebackground='#b91c1c', activeforeground='white', font=('Segoe UI', 10, 'bold'), padx=18, pady=8).pack(side='left', padx=8)
    tk.Button(btns, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=8).pack(side='right')

    tk.Label(w, text='SON GİRİLEN KİTAPLAR', font=('Segoe UI', 10, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(6, 2))
    cols = ('barcode', 'name', 'author', 'source', 'qty', 'retail')
    tv = ttk.Treeview(w, columns=cols, show='headings', height=9)
    for k, t, wd, a in (('barcode', 'Barkod / ISBN', 130, 'w'), ('name', 'Kitap Adı', 360, 'w'), ('author', 'Yazar', 200, 'w'), ('source', 'Kaynak firma', 130, 'w'), ('qty', 'Stok', 70, 'e'), ('retail', 'Satış (KDV dahil)', 120, 'e')):
        tv.heading(k, text=t)
        tv.column(k, width=wd, anchor=a, stretch=(k in ('name', 'author')))
    tv.pack(fill='both', expand=True, padx=18, pady=(0, 14))

    def refresh_list():
        tv.delete(*tv.get_children())
        for r in recent(con):
            if lst['cut'] and str(r['updated'] or '') <= lst['cut']:
                continue
            tv.insert('', 'end', values=(r['barcode'] or '', r['name'], r['author'], r['source'], '%g' % float(r['qty'] or 0), fmt(r['retail'])))

    def on_close():
        try:
            con.close()
        except Exception:
            pass
        w.destroy()
    w.protocol('WM_DELETE_WINDOW', on_close)
    reset_form()
    refresh_list()
    e_code.focus_set()
    w._kitap = dict(vars=v, save=save, tree=tv, status=status, info=info, entries=order, apply_tpl=apply_tpl, copy_prev=copy_prev, sources_dialog=sources_dialog, open_catalog=open_catalog, check_code=check_code, state=state, tpl_cb=cb_tpl)
    return w
