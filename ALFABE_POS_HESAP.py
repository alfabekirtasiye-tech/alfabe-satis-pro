# -*- coding: utf-8 -*-
"""ALFABE POS Hesapları (v434): POS tanımı + komisyon + valör (kaç gün sonra hesaba geçer) + aylık ücret tek ekranda, canlı örnek hesaplamayla."""
import datetime
import sqlite3

import ALFABE_POS_TAKIP as PT

BANKS = ['Ziraat Bankası', 'Halkbank', 'VakıfBank', 'İş Bankası', 'Garanti BBVA', 'Yapı Kredi', 'Akbank', 'QNB', 'DenizBank', 'TEB', 'ING',
         'Kuveyt Türk', 'Albaraka Türk', 'Türkiye Finans', 'Vakıf Katılım', 'Ziraat Katılım', 'Emlak Katılım', 'Param', 'İyzico', 'PayTR', 'Diğer']
VALOR_PRESETS = (('Ertesi iş günü (1)', 1), ('2 gün', 2), ('7 gün', 7), ('15 gün', 15), ('30 gün', 30), ('Aynı gün (0)', 0))


def ensure(c):
    PT.ensure_schema(c)
    have = {r[1] for r in c.execute('PRAGMA table_info(pos_terminals)').fetchall()}
    for name, typ in (('merchant_no', 'TEXT'), ('note', 'TEXT'), ('skip_weekend', 'INTEGER DEFAULT 1')):
        if name not in have:
            c.execute('ALTER TABLE pos_terminals ADD COLUMN %s %s' % (name, typ))
    c.commit()


def _num(s, default=0.0):
    t = str(s if s is not None else '').strip().replace('₺', '').replace(' ', '')
    if not t:
        return default
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    return float(t)


def due_date(day, valor, skip_weekend=True):
    d = day + datetime.timedelta(days=max(int(valor), 0))
    while skip_weekend and d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d


def preview(amount, rate, valor, day=None, skip_weekend=True):
    """Örnek: satış tutarı → komisyon, net, hesaba geçiş tarihi."""
    day = day or datetime.date.today()
    comm = round(amount * rate / 100.0, 2)
    return dict(commission=comm, net=round(amount - comm, 2), due=due_date(day, valor, skip_weekend))


def validate(f):
    name = ' '.join(str(f.get('name') or '').split())
    if len(name) < 2:
        raise ValueError('POS adı yazılmalı (örn. Ziraat POS).')
    rate = _num(f.get('rate'))
    if not 0 <= rate <= 50:
        raise ValueError('Komisyon oranı 0 ile 50 arasında olmalı.')
    try:
        valor = int(_num(f.get('valor'), 1))
    except ValueError:
        raise ValueError('Valör günü sayı olmalı.')
    if not 0 <= valor <= 60:
        raise ValueError('Valör günü 0 ile 60 arasında olmalı.')
    fee = _num(f.get('fee'))
    if not 0 <= fee <= 100000:
        raise ValueError('Aylık POS ücreti geçersiz.')
    return dict(name=name, bank=' '.join(str(f.get('bank') or '').split()), rate=rate, valor=valor, fee=fee,
                merchant_no=str(f.get('merchant_no') or '').strip(), note=str(f.get('note') or '').strip(),
                skip=1 if f.get('skip', 1) in (1, True, '1') else 0)


def save_terminal(c, f, tid=None):
    """Yeni POS ekler ya da (tid verilirse) günceller; bekleyen yatışların tarih/komisyonunu yeniden hesaplar."""
    ensure(c)
    d = validate(f)
    if tid is None:
        try:
            c.execute('INSERT INTO pos_terminals(name,bank,commission_rate,active,created,valor_days,monthly_fee,merchant_no,note,skip_weekend) VALUES(?,?,?,1,?,?,?,?,?,?)',
                      (d['name'], d['bank'], d['rate'], datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), d['valor'], d['fee'], d['merchant_no'], d['note'], d['skip']))
        except sqlite3.IntegrityError:
            raise ValueError('Bu adla bir POS zaten kayıtlı.')
        tid = c.execute('SELECT id FROM pos_terminals WHERE name=?', (d['name'],)).fetchone()[0]
    else:
        c.execute('UPDATE pos_terminals SET bank=?,commission_rate=?,valor_days=?,monthly_fee=?,merchant_no=?,note=?,skip_weekend=? WHERE id=?',
                  (d['bank'], d['rate'], d['valor'], d['fee'], d['merchant_no'], d['note'], d['skip'], tid))
    c.commit()
    recalc_pending(c)
    return tid


def recalc_pending(c):
    """Bekleyen (henüz yatmamış) satırların komisyonunu ve yatış tarihini POS ayarına göre yeniler."""
    try:
        PT.generate(c)
        for r in c.execute("SELECT s.id,s.dt,s.gross,t.commission_rate,t.valor_days,COALESCE(t.skip_weekend,1) FROM pos_settlements s JOIN pos_terminals t ON t.name=s.pos_name WHERE s.status='Bekliyor'").fetchall():
            comm = round(float(r[2] or 0) * float(r[3] or 0) / 100.0, 2) if float(r[2] or 0) > 0 else 0.0
            due = due_date(datetime.date.fromisoformat(r[1]), int(r[4] if r[4] is not None else 1), bool(r[5])).isoformat()
            c.execute('UPDATE pos_settlements SET commission=?,net=?,due_date=? WHERE id=?', (comm, round(float(r[2] or 0) - comm, 2), due, r[0]))
        c.commit()
    except Exception as e:
        _log('POS: bekleyen komisyon/valör yenilenemedi', e)


def toggle(c, tid):
    c.execute('UPDATE pos_terminals SET active=1-COALESCE(active,0) WHERE id=?', (tid,))
    c.commit()


def usage(c, name):
    n = 0
    for sql in ("SELECT COUNT(*) FROM sale_payments WHERE pos_name=?", "SELECT COUNT(*) FROM pos_settlements WHERE pos_name=?", "SELECT COUNT(*) FROM finance WHERE pos_name=?"):
        try:
            n += c.execute(sql, (name,)).fetchone()[0]
        except sqlite3.Error:
            pass
    return n


def delete_unused(c, tid):
    r = c.execute('SELECT name FROM pos_terminals WHERE id=?', (tid,)).fetchone()
    if not r:
        return
    if usage(c, r[0]):
        raise ValueError('Bu POS ile işlem yapılmış; silinemez. Bunun yerine PASİFE alın.')
    c.execute('DELETE FROM pos_terminals WHERE id=?', (tid,))
    c.commit()


def listing(c, today=None):
    ensure(c)
    today = today or datetime.date.today()
    lim = (today - datetime.timedelta(days=30)).isoformat()
    out = []
    for r in c.execute('SELECT id,name,bank,commission_rate,valor_days,monthly_fee,active,COALESCE(merchant_no,\'\'),COALESCE(note,\'\'),COALESCE(skip_weekend,1) FROM pos_terminals ORDER BY name').fetchall():
        pend = c.execute("SELECT COALESCE(SUM(net),0),MIN(due_date) FROM pos_settlements WHERE pos_name=? AND status='Bekliyor'", (r[1],)).fetchone()
        try:
            s30 = c.execute("SELECT COALESCE(SUM(p.amount),0) FROM sale_payments p JOIN sales s ON s.id=p.sale_id WHERE p.kind LIKE 'Kart%' AND p.pos_name=? AND substr(s.dt,1,10)>=?", (r[1], lim)).fetchone()[0]
        except sqlite3.Error:
            s30 = 0
        out.append(dict(id=r[0], name=r[1], bank=r[2] or '', rate=float(r[3] or 0), valor=int(r[4] if r[4] is not None else 1), fee=float(r[5] or 0), active=int(r[6] or 0),
                        merchant_no=r[7], note=r[8], skip=int(r[9]), pending=float(pend[0] or 0), next_due=pend[1] or '', s30=float(s30 or 0)))
    return out


def open_dialog(parent, connection_factory, on_change=None, money=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    w = tk.Toplevel(parent)
    w.title('POS Hesapları')
    w.geometry('1040x760')
    w.minsize(900, 640)
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    con = connection_factory()
    ensure(con)
    fm = money or (lambda v: ('%.2f' % float(v)).replace('.', ',') + ' ₺')
    BG = '#f4f7fb'
    tk.Label(w, text='POS HESAPLARI', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg=BG).pack(anchor='w', padx=18, pady=(14, 0))
    tk.Label(w, text='Her POS için komisyon ve "kaç gün sonra hesaba geçer" (valör) bilgisini girin. Kart satışları buna göre POS Takip\'te beklenen yatış olarak görünür; yatış günü geldiğinde uyarı verir.',
             font=('Segoe UI', 10), fg='#475569', bg=BG, wraplength=980, justify='left').pack(anchor='w', padx=18, pady=(0, 8))
    cols = ('name', 'bank', 'rate', 'valor', 'fee', 'pending', 'due', 's30', 'active')
    tv = ttk.Treeview(w, columns=cols, show='headings', height=6)
    for k, t, wd, a in (('name', 'POS Adı', 190, 'w'), ('bank', 'Banka', 130, 'w'), ('rate', 'Komisyon %', 85, 'e'), ('valor', 'Hesaba geçiş', 100, 'e'), ('fee', 'Aylık ücret', 90, 'e'),
                        ('pending', 'Bekleyen net', 110, 'e'), ('due', 'En yakın yatış', 100, 'e'), ('s30', 'Son 30 gün kart', 110, 'e'), ('active', 'Durum', 60, 'w')):
        tv.heading(k, text=t)
        tv.column(k, width=wd, anchor=a)
    tv.pack(fill='x', padx=18)

    box = tk.LabelFrame(w, text=' POS TANIMI ', font=('Segoe UI', 10, 'bold'), fg='#0b376d', bg='white', padx=12, pady=8)
    box.pack(fill='x', padx=18, pady=10)
    v = {k: tk.StringVar() for k in ('name', 'bank', 'merchant_no', 'rate', 'valor', 'fee', 'note', 'test')}
    v['skip'] = tk.IntVar(value=1)
    state = {'tid': None}

    def lab(r, c_, t):
        tk.Label(box, text=t, font=('Segoe UI', 9, 'bold'), fg='#334155', bg='white').grid(row=r, column=c_, sticky='w', padx=6, pady=(4, 0))

    lab(0, 0, 'POS ADI *'); e_name = tk.Entry(box, textvariable=v['name'], font=('Segoe UI', 12), width=26); e_name.grid(row=1, column=0, sticky='we', padx=6)
    lab(0, 1, 'BANKA'); ttk.Combobox(box, textvariable=v['bank'], values=BANKS, font=('Segoe UI', 12), width=22).grid(row=1, column=1, sticky='we', padx=6)
    lab(0, 2, 'ÜYE İŞYERİ / TERMİNAL NO'); tk.Entry(box, textvariable=v['merchant_no'], font=('Segoe UI', 12), width=22).grid(row=1, column=2, sticky='we', padx=6)
    lab(2, 0, 'KOMİSYON %'); tk.Entry(box, textvariable=v['rate'], font=('Segoe UI', 12), width=10).grid(row=3, column=0, sticky='w', padx=6)
    lab(2, 1, 'KAÇ GÜN SONRA HESABA GEÇER? (VALÖR)')
    vf = tk.Frame(box, bg='white'); vf.grid(row=3, column=1, columnspan=2, sticky='w', padx=6)
    tk.Entry(vf, textvariable=v['valor'], font=('Segoe UI', 12), width=6).pack(side='left')
    tk.Label(vf, text='gün', bg='white').pack(side='left', padx=(4, 10))
    for t, d in VALOR_PRESETS:
        tk.Button(vf, text=t, font=('Segoe UI', 8), command=lambda d=d: (v['valor'].set(str(d)), upd())).pack(side='left', padx=2)
    tk.Checkbutton(box, text='Yatış günü Cumartesi/Pazar ise bir sonraki iş gününe ertele', variable=v['skip'], bg='white', font=('Segoe UI', 9), command=lambda: upd()).grid(row=4, column=0, columnspan=3, sticky='w', padx=6, pady=(4, 0))
    lab(5, 0, 'AYLIK POS ÜCRETİ (TL)'); tk.Entry(box, textvariable=v['fee'], font=('Segoe UI', 12), width=12).grid(row=6, column=0, sticky='w', padx=6)
    lab(5, 1, 'NOT'); tk.Entry(box, textvariable=v['note'], font=('Segoe UI', 11), width=50).grid(row=6, column=1, columnspan=2, sticky='we', padx=6)
    box.grid_columnconfigure(1, weight=1); box.grid_columnconfigure(2, weight=1)

    pv = tk.LabelFrame(w, text=' ÖRNEK HESAPLAMA ', font=('Segoe UI', 10, 'bold'), fg='#0b376d', bg='#eaf2ff', padx=12, pady=6)
    pv.pack(fill='x', padx=18)
    tk.Label(pv, text='Bugün kartla', bg='#eaf2ff').pack(side='left')
    v['test'].set('1000')
    tk.Entry(pv, textvariable=v['test'], width=9, font=('Segoe UI', 11), justify='right').pack(side='left', padx=4)
    tk.Label(pv, text='TL satış yapılırsa:', bg='#eaf2ff').pack(side='left')
    pl = tk.Label(pv, text='', bg='#eaf2ff', fg='#0b376d', font=('Segoe UI', 11, 'bold'), anchor='w', justify='left')
    pl.pack(side='left', padx=10)
    msg = tk.Label(w, text='', font=('Segoe UI', 10, 'bold'), bg=BG, anchor='w')
    msg.pack(fill='x', padx=18, pady=(6, 0))

    def upd(*_):
        try:
            amt = _num(v['test'].get(), 0)
            r = _num(v['rate'].get())
            d = int(_num(v['valor'].get(), 1))
            p = preview(amt, r, d, skip_weekend=bool(v['skip'].get()))
            gun = ('Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar')[p['due'].weekday()]
            pl.config(text='komisyon %s  →  hesaba net %s  →  %s %s günü yatar' % (fm(p['commission']), fm(p['net']), p['due'].strftime('%d.%m.%Y'), gun))
        except Exception:
            pl.config(text='(komisyon, gün ve tutarı sayı olarak yazın)')
    for k in ('rate', 'valor', 'test'):
        v[k].trace_add('write', upd)

    def load():
        tv.delete(*tv.get_children())
        for x in listing(con):
            due = datetime.date.fromisoformat(x['next_due']).strftime('%d.%m.%Y') if x['next_due'] else '-'
            tv.insert('', 'end', iid=str(x['id']), values=(x['name'], x['bank'], ('%g' % x['rate']).replace('.', ','),
                      'Aynı gün' if x['valor'] == 0 else ('%d gün sonra' % x['valor']), fm(x['fee']), fm(x['pending']), due, fm(x['s30']), 'Aktif' if x['active'] else 'Pasif'))

    def new():
        state['tid'] = None
        for k in ('name', 'bank', 'merchant_no', 'rate', 'fee', 'note'):
            v[k].set('')
        v['valor'].set('1'); v['skip'].set(1)
        e_name.config(state='normal'); e_name.focus_set()
        tv.selection_set(()); msg.config(text='Yeni POS: bilgileri yazıp KAYDET\'e basın.', fg='#475569'); upd()

    def pick(_=None):
        s = tv.selection()
        if not s:
            return
        x = [y for y in listing(con) if y['id'] == int(s[0])][0]
        state['tid'] = x['id']
        v['name'].set(x['name']); v['bank'].set(x['bank']); v['merchant_no'].set(x['merchant_no']); v['rate'].set(('%g' % x['rate']).replace('.', ','))
        v['valor'].set(str(x['valor'])); v['fee'].set(('%.2f' % x['fee']).replace('.', ',')); v['note'].set(x['note']); v['skip'].set(x['skip'])
        e_name.config(state='disabled')
        msg.config(text='Düzenleniyor: %s (ad değiştirilemez; geçmiş kayıtlar bu adla bağlı).' % x['name'], fg='#475569'); upd()
    tv.bind('<<TreeviewSelect>>', pick)

    def save():
        f = {k: v[k].get() for k in ('name', 'bank', 'merchant_no', 'rate', 'valor', 'fee', 'note')}
        f['skip'] = v['skip'].get()
        if state['tid'] is not None:
            f['name'] = [y for y in listing(con) if y['id'] == state['tid']][0]['name']
        try:
            tid = save_terminal(con, f, state['tid'])
        except ValueError as ex:
            msg.config(text='✗ ' + str(ex), fg='#b91c1c'); return
        load()
        tv.selection_set(str(tid))
        msg.config(text='✓ Kaydedildi. Bekleyen yatışların tarihi ve komisyonu yeni ayara göre güncellendi.', fg='#15803d')
        if on_change:
            try:
                on_change()
            except Exception as e:
                _log('POS hesapları: değişiklik bildirimi', e)

    def do_toggle():
        s = tv.selection()
        if not s:
            msg.config(text='Önce listeden bir POS seçin.', fg='#b91c1c'); return
        toggle(con, int(s[0])); load(); tv.selection_set(s[0])
        if on_change:
            on_change()

    def do_delete():
        s = tv.selection()
        if not s:
            msg.config(text='Önce listeden bir POS seçin.', fg='#b91c1c'); return
        if not messagebox.askyesno('POS Hesapları', 'Seçili POS silinsin mi?', parent=w):
            return
        try:
            delete_unused(con, int(s[0]))
        except ValueError as ex:
            msg.config(text='✗ ' + str(ex), fg='#b91c1c'); return
        load(); new()
        if on_change:
            on_change()
    bf = tk.Frame(w, bg=BG)
    bf.pack(fill='x', padx=18, pady=8)
    tk.Button(bf, text='KAYDET', command=save, font=('Segoe UI', 11, 'bold'), bg='#0b6bcb', fg='white', padx=18, pady=6).pack(side='left')
    tk.Button(bf, text='YENİ POS', command=new, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left', padx=6)
    tk.Button(bf, text='PASİFE AL / AKTİFE AL', command=do_toggle, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left')
    tk.Button(bf, text='SİL', command=do_delete, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left', padx=6)
    tk.Button(bf, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='right')
    load(); new()
    w._poshesap = dict(vars=v, save=save, tree=tv, new=new, pick=pick, msg=msg, preview=pl, toggle=do_toggle, delete=do_delete)
    return w


def _log(ctx, e):
    # Sessiz yutulan hatayı ana programın hata günlüğüne yazar (yoksa sessizce geçer; ana işlemi bozmaz).
    try:
        import sys
        for m in list(sys.modules.values()):
            try:
                f = getattr(m, 'log_error', None)
                if callable(f) and hasattr(m, 'ERROR_LOG'):
                    f(ctx, e)
                    return
            except Exception:
                continue
    except Exception:
        pass
