# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Havale / IBAN ile satış tahsilatı (v417)
Müşteri ödemeyi mağazanın banka hesabına (IBAN) gönderdiğinde:
 * Satış 'Havale/IBAN' ödeme türüyle kaydedilir (Hızlı Satış F3, parçalı ödeme, Cari Satış, siparişten satış, iade).
 * Tutar doğrudan seçilen BANKA HESABINA işlenir (kasa/POS'a değil) ve Banka Merkezi'nde görünür.
 * Gönderen adı / dekont açıklaması kaydedilir; "banka hesabında gördüm" onayı verilmediyse tahsilat
   'Kontrol bekliyor' listesine düşer (IBAN KONTROL ekranı) ve Günlük Özet'te hatırlatılır.
"""
import datetime
import sqlite3

KIND = 'Havale/IBAN'
FIN_KIND = 'Havale/IBAN Satış'
LAST_SETTING = 'last_iban_account'


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def money(v):
    s = '{:,.2f}'.format(_f(v))
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def tr_date(s):
    t = str(s or '')[:10]
    try:
        y, m, d = t.split('-')
        return '%s.%s.%s' % (d, m, y)
    except Exception:
        return t


def ensure_columns(c):
    have = {r[1] for r in c.execute('PRAGMA table_info(sale_payments)').fetchall()}
    for name, typ in (('bank_account', 'TEXT'), ('ref', 'TEXT'), ('verified', 'INTEGER DEFAULT 0')):
        if name not in have:
            c.execute('ALTER TABLE sale_payments ADD COLUMN %s %s' % (name, typ))
    c.commit()


def bank_accounts(c):
    try:
        return [r[0] for r in c.execute("SELECT name FROM finance_accounts WHERE active=1 AND kind='Banka' ORDER BY bank,name").fetchall()]
    except sqlite3.Error:
        return []


def get_last(c):
    try:
        r = c.execute('SELECT v FROM settings WHERE k=?', (LAST_SETTING,)).fetchone()
        return r[0] if r else ''
    except sqlite3.Error:
        return ''


def set_last(c, name):
    try:
        c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)', (LAST_SETTING, name))
        c.commit()
    except sqlite3.Error:
        pass


def post_payment(c, sid, doc, amount, account, ref='', verified=0, stamp=None, source='SALE', fin_kind=FIN_KIND, note=''):
    """Açık bir işlem (BEGIN yapılmış bağlantı) içinde çağrılır: ödeme satırı + banka hesabına finans hareketi.
    Dönüş: finance_id."""
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError('Havale/IBAN tutarı sıfırdan büyük olmalı.')
    if not str(account or '').strip():
        raise ValueError('Havale/IBAN için banka hesabı seçilmedi.')
    stamp = stamp or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO sale_payments(sale_id,kind,amount,bank_account,ref,verified) VALUES(?,?,?,?,?,?)',
              (sid, KIND, amount, account, str(ref or '').strip(), 1 if verified else 0))
    n = (doc + ((' | ' + str(ref).strip()) if str(ref or '').strip() else '') + ((' | ' + note) if note else '')).strip()
    return c.execute('INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)',
                     (stamp, account, fin_kind, amount, source, sid, n)).lastrowid


def post_refund(c, rid, doc, amount, account, stamp=None, note=''):
    """İade: banka hesabından çıkış + negatif ödeme satırı."""
    amount = round(float(amount), 2)
    stamp = stamp or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO sale_payments(sale_id,kind,amount,bank_account,ref,verified) VALUES(?,?,?,?,?,1)', (rid, KIND, -amount, account, '', ))
    return c.execute('INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)',
                     (stamp, account, 'Satış İadesi', -amount, 'SALE_RETURN', rid, note or doc)).lastrowid


def pending(c):
    """Kontrol bekleyen (banka hesabında görüldü işaretlenmemiş) tahsilatlar."""
    try:
        return [dict(id=r[0], sale_id=r[1], doc=r[2], dt=r[3], amount=_f(r[4]), account=r[5], ref=r[6])
                for r in c.execute("SELECT p.id,p.sale_id,s.doc_no,s.dt,p.amount,p.bank_account,p.ref FROM sale_payments p JOIN sales s ON s.id=p.sale_id "
                                   "WHERE p.kind=? AND p.amount>0 AND COALESCE(p.verified,0)=0 AND COALESCE(s.status,'FINAL')<>'CANCEL' ORDER BY s.dt", (KIND,)).fetchall()]
    except sqlite3.Error:
        return []


def listing(c, only_pending=False, date_from='', date_to=''):
    sql = ("SELECT p.id,s.dt,s.doc_no,COALESCE(ca.title,'Perakende'),p.amount,COALESCE(p.bank_account,''),COALESCE(p.ref,''),COALESCE(p.verified,0) "
           "FROM sale_payments p JOIN sales s ON s.id=p.sale_id LEFT JOIN caris ca ON ca.id=s.customer_id WHERE p.kind=?")
    a = [KIND]
    if only_pending:
        sql += ' AND p.amount>0 AND COALESCE(p.verified,0)=0'
    if date_from:
        sql += ' AND substr(s.dt,1,10)>=?'
        a.append(date_from)
    if date_to:
        sql += ' AND substr(s.dt,1,10)<=?'
        a.append(date_to)
    sql += ' ORDER BY s.dt DESC, p.id DESC'
    keys = ['id', 'dt', 'doc', 'customer', 'amount', 'account', 'ref', 'verified']
    return [dict(zip(keys, r)) for r in c.execute(sql, a).fetchall()]


def set_verified(c, pid, value=True, user=''):
    r = c.execute("SELECT amount FROM sale_payments WHERE id=? AND kind=?", (pid, KIND)).fetchone()
    if r is None:
        raise ValueError('Kayıt bulunamadı.')
    c.execute('UPDATE sale_payments SET verified=? WHERE id=?', (1 if value else 0, pid))
    try:
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user, 'Satış', 'IBAN tahsilat kontrolü', pid, '0' if value else '1', '1' if value else '0', ''))
    except sqlite3.Error:
        pass
    c.commit()


# ----------------------------------------------------------------------------------------------
# Ekranlar
# ----------------------------------------------------------------------------------------------
def ask(parent, connection_factory, amount, title=None, refund=False):
    """Satış anında hesabı ve gönderen bilgisini sorar. Dönüş: dict(account, ref, verified) | None (vazgeçildi)."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    con = connection_factory()
    try:
        ensure_columns(con)
        accs = bank_accounts(con)
        last = get_last(con)
    finally:
        try:
            con.close()
        except Exception:
            pass
    if not accs:
        messagebox.showwarning('Havale / IBAN', 'Tanımlı banka hesabı yok.\nÖnce Banka Merkezi > BANKA HESABI ile mağaza hesabınızı ekleyin.', parent=parent)
        return None
    w = tk.Toplevel(parent)
    w.title(title or ('Havale / IBAN İadesi' if refund else 'Havale / IBAN Tahsilatı'))
    w.geometry('520x430')
    w.resizable(False, False)
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    w.grab_set()
    tk.Label(w, text='HAVALE / IBAN İADESİ' if refund else 'HAVALE / IBAN TAHSİLATI', font=('Segoe UI', 15, 'bold'), fg='#0b376d').pack(pady=(16, 2))
    tk.Label(w, text=money(amount) + ' TL', font=('Segoe UI', 24, 'bold'), fg='#b91c1c').pack()
    va = tk.StringVar(value=last if last in accs else accs[0])
    vr = tk.StringVar()
    vv = tk.BooleanVar(value=False)
    f1 = tk.Frame(w)
    f1.pack(fill='x', padx=26, pady=(14, 4))
    tk.Label(f1, text='Hangi banka hesabından gönderilecek?' if refund else 'Hangi banka hesabına geldi?', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
    cb = ttk.Combobox(f1, textvariable=va, values=accs, state='readonly', font=('Segoe UI', 12))
    cb.pack(fill='x', pady=3, ipady=3)
    f2 = tk.Frame(w)
    f2.pack(fill='x', padx=26, pady=4)
    tk.Label(f2, text='Gönderen adı / dekont açıklaması (isteğe bağlı)', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
    er = tk.Entry(f2, textvariable=vr, font=('Segoe UI', 12))
    er.pack(fill='x', pady=3, ipady=3)
    if refund:
        vv.set(True)
        tk.Label(w, text='İade tutarı bu banka hesabından çıkış olarak işlenir. Havaleyi bankadan ayrıca siz göndereceksiniz.',
                 fg='#64748b', wraplength=460, justify='left').pack(anchor='w', padx=26, pady=(12, 8))
    else:
        ck = tk.Checkbutton(w, text='Parayı banka uygulamamdan / ekstremden gördüm, hesabıma geçti', variable=vv, font=('Segoe UI', 10), wraplength=450, justify='left')
        ck.pack(anchor='w', padx=22, pady=(10, 0))
        tk.Label(w, text='İşaretlemezseniz tahsilat "Kontrol bekliyor" listesinde kalır (Banka Merkezi > IBAN KONTROL); satış yine kaydedilir.',
                 fg='#64748b', wraplength=460, justify='left').pack(anchor='w', padx=26, pady=(2, 8))
    res = {'v': None}

    def ok(_=None):
        res['v'] = dict(account=va.get(), ref=vr.get().strip(), verified=bool(vv.get()))
        w.destroy()

    def cancel(_=None):
        w.destroy()
    bf = tk.Frame(w)
    bf.pack(pady=8)
    tk.Button(bf, text='TAMAM — KAYDET', command=ok, bg='#15803d', fg='white', font=('Segoe UI', 12, 'bold'), relief='flat', padx=18, pady=7).pack(side='left', padx=6)
    tk.Button(bf, text='VAZGEÇ', command=cancel, font=('Segoe UI', 11), padx=14, pady=7).pack(side='left', padx=6)
    w.bind('<Escape>', cancel)
    w.bind('<Return>', ok)
    w._iban = dict(vars=dict(account=va, ref=vr, verified=vv), ok=ok, cancel=cancel)
    er.focus_set()
    if getattr(parent, '_iban_auto', None):         # test/otomasyon kancası
        parent._iban_auto(w)
    w.wait_window()
    try:
        parent.grab_set()
    except Exception:
        pass
    if res['v']:
        c2 = connection_factory()
        try:
            set_last(c2, res['v']['account'])
        finally:
            try:
                c2.close()
            except Exception:
                pass
    return res['v']


def open_list(parent, connection_factory, user='', on_done=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    con = connection_factory()
    ensure_columns(con)
    w = tk.Toplevel(parent)
    w.title('IBAN Kontrol')
    w.geometry('1120x640')
    w.minsize(960, 520)
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    tk.Label(w, text='HAVALE / IBAN TAHSİLATLARI', font=('Segoe UI', 17, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    tk.Label(w, text='Bankanıza gelen havaleleri ekstrenizle karşılaştırın; gördüğünüz kayıtları "KONTROL EDİLDİ" yapın. Kontrol bekleyenler turuncu görünür.',
             bg='#f4f7fb', fg='#475569').pack(anchor='w', padx=18, pady=(0, 6))
    fl = tk.Frame(w, bg='#f4f7fb')
    fl.pack(fill='x', padx=18)
    vf = tk.StringVar(value='Kontrol bekleyenler')
    ttk.Combobox(fl, textvariable=vf, values=['Kontrol bekleyenler', 'Tümü (son 60 gün)'], state='readonly', width=24).pack(side='left')
    sumlab = tk.Label(fl, text='', bg='#f4f7fb', font=('Segoe UI', 10, 'bold'))
    sumlab.pack(side='left', padx=14)
    cols = ('dt', 'doc', 'customer', 'amount', 'account', 'ref', 'state')
    fr = tk.Frame(w)
    fr.pack(fill='both', expand=True, padx=18, pady=8)
    t = ttk.Treeview(fr, columns=cols, show='headings', selectmode='extended')
    for c, h, wd, an in (('dt', 'Tarih', 130, 'w'), ('doc', 'Belge', 120, 'w'), ('customer', 'Müşteri', 190, 'w'), ('amount', 'Tutar', 110, 'e'),
                         ('account', 'Banka Hesabı', 170, 'w'), ('ref', 'Gönderen / Açıklama', 260, 'w'), ('state', 'Durum', 110, 'w')):
        t.heading(c, text=h)
        t.column(c, width=wd, anchor=an, stretch=(c in ('customer', 'ref')))
    sb = ttk.Scrollbar(fr, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    t.tag_configure('wait', foreground='#b45309')
    t.tag_configure('ok', foreground='#15803d')
    t.tag_configure('ret', foreground='#64748b')

    def refresh():
        t.delete(*t.get_children())
        only = vf.get().startswith('Kontrol')
        d1 = '' if only else (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
        tot = 0.0
        n = 0
        for r in listing(con, only, d1):
            if r['amount'] < 0:
                st, tag = 'İade', 'ret'
            elif r['verified']:
                st, tag = 'Kontrol edildi', 'ok'
            else:
                st, tag = 'Kontrol bekliyor', 'wait'
                tot += r['amount']
                n += 1
            t.insert('', 'end', iid=str(r['id']), values=(r['dt'][:16], r['doc'], r['customer'], money(r['amount']), r['account'], r['ref'], st), tags=(tag,))
        sumlab.config(text=('Kontrol bekleyen: %d kayıt, %s TL' % (n, money(tot))) if n else 'Kontrol bekleyen tahsilat yok', fg='#b45309' if n else '#15803d')

    def mark(value):
        sel = t.selection()
        if not sel:
            return messagebox.showwarning('IBAN Kontrol', 'Listeden kayıt seçin.', parent=w)
        try:
            for i in sel:
                set_verified(con, int(i), value, user)
        except Exception as ex:
            return messagebox.showerror('IBAN Kontrol', str(ex), parent=w)
        refresh()
        if on_done:
            try:
                on_done()
            except Exception:
                pass
    vf.trace_add('write', lambda *_: refresh())
    bar = tk.Frame(w, bg='#f4f7fb')
    bar.pack(fill='x', padx=18, pady=10)
    tk.Button(bar, text='✔ KONTROL EDİLDİ', command=lambda: mark(True), bg='#15803d', fg='white', font=('Segoe UI', 11, 'bold'), relief='flat', padx=16, pady=6).pack(side='left')
    tk.Button(bar, text='KONTROLÜ GERİ AL', command=lambda: mark(False), font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left', padx=8)
    tk.Button(bar, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='right')
    refresh()
    w._iban = dict(tree=t, refresh=refresh, mark=mark, filter=vf)
    return w
