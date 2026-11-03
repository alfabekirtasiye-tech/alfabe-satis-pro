# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - ÜRÜN BİLGİ PENCERESİ (v1.2.291.456)

Satış ekranında ürün satırına sağ tıklayınca açılır: stok durumu, fiyatlar, satış / alış özeti,
son hareketler ve (yetkiliyse) stok kartı fiyatını değiştirme.
Veri toplama (collect) ve fiyat yazma (save_prices) ekrandan bağımsızdır; sınanabilir.
"""
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

CTX = {}
SALE_STATUS = "('FINAL','PARTIAL_RETURN')"
RED = '#b3131b'
INK = '#101820'
LINE = '#c9d6e6'
BLUE = '#1f6fd6'


def bind(**kw):
    CTX.update(kw)


def _con():
    return CTX['con']()


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _q(v):
    f = _f(v)
    return str(int(f)) if f == int(f) else (f'{f:,.3f}'.replace(',', 'X').replace('.', ',').replace('X', '.').rstrip('0').rstrip(','))


def collect(stock_id):
    """Ürünle ilgili tüm bilgileri tek sözlükte toplar."""
    c = _con()
    try:
        s = c.execute('SELECT * FROM stock WHERE id=?', (stock_id,)).fetchone()
        if not s:
            return None
        s = dict(s)
        today = datetime.date.today()
        d7 = (today - datetime.timedelta(days=6)).isoformat()
        d30 = (today - datetime.timedelta(days=29)).isoformat()
        d90 = (today - datetime.timedelta(days=89)).isoformat()
        d365 = (today - datetime.timedelta(days=364)).isoformat()
        t = today.isoformat()
        r = c.execute(
            "SELECT SUM(CASE WHEN s.dt>=? THEN sl.qty END),SUM(CASE WHEN s.dt>=? THEN sl.total END),"
            "SUM(CASE WHEN s.dt>=? THEN sl.qty END),SUM(CASE WHEN s.dt>=? THEN sl.total END),"
            "SUM(CASE WHEN s.dt>=? THEN sl.qty END),SUM(CASE WHEN s.dt>=? THEN sl.total END),"
            "SUM(CASE WHEN s.dt>=? THEN sl.qty END),SUM(CASE WHEN s.dt>=? THEN sl.total END),"
            "SUM(CASE WHEN s.dt>=? THEN sl.qty END),SUM(CASE WHEN s.dt>=? THEN sl.total END),"
            "SUM(sl.qty),SUM(sl.total),MIN(s.dt),MAX(s.dt),COUNT(*) "
            "FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE sl.stock_id=? AND s.status IN " + SALE_STATUS,
            (t, t, d7, d7, d30, d30, d90, d90, d365, d365, stock_id)).fetchone()
        names = ['today', 'd7', 'd30', 'd90', 'd365']
        sales = {}
        for i, n in enumerate(names):
            sales[n] = (_f(r[i * 2]), _f(r[i * 2 + 1]))
        sales['all'] = (_f(r[10]), _f(r[11]))
        sales['first'] = r[12] or ''
        sales['last'] = r[13] or ''
        sales['lines'] = int(r[14] or 0)
        last_sales = [dict(x) for x in c.execute(
            "SELECT s.dt,s.doc_no,sl.qty,sl.price,sl.total,COALESCE(ca.title,'Perakende') cari FROM sale_lines sl "
            "JOIN sales s ON s.id=sl.sale_id LEFT JOIN caris ca ON ca.id=s.customer_id "
            "WHERE sl.stock_id=? AND s.status IN " + SALE_STATUS + " ORDER BY s.id DESC LIMIT 12", (stock_id,)).fetchall()]
        try:
            pr = c.execute(
                "SELECT SUM(pl.qty),SUM(pl.total) FROM purchase_lines pl JOIN purchases p ON p.id=pl.purchase_id "
                "WHERE pl.stock_id=? AND COALESCE(p.status,'') NOT LIKE '%İptal%'", (stock_id,)).fetchone()
            bought = (_f(pr[0]), _f(pr[1]))
            lp = c.execute(
                "SELECT p.dt,pl.qty,pl.cost,COALESCE(ca.title,'') sup FROM purchase_lines pl JOIN purchases p ON p.id=pl.purchase_id "
                "LEFT JOIN caris ca ON ca.id=p.supplier_id WHERE pl.stock_id=? AND COALESCE(p.status,'') NOT LIKE '%İptal%' "
                "ORDER BY p.id DESC LIMIT 1", (stock_id,)).fetchone()
            last_purchase = dict(lp) if lp else None
        except Exception:
            bought = (0.0, 0.0)
            last_purchase = None
        moves = [dict(x) for x in c.execute(
            'SELECT dt,kind,qty,note FROM stock_moves WHERE stock_id=? ORDER BY id DESC LIMIT 12', (stock_id,)).fetchall()]
        prices = [dict(x) for x in c.execute(
            'SELECT dt,price_type,old_price,new_price,user,reason FROM price_history WHERE stock_id=? ORDER BY id DESC LIMIT 12',
            (stock_id,)).fetchall()]
        extra = []
        try:
            extra = [x[0] for x in c.execute('SELECT barcode FROM stock_barcodes WHERE stock_id=? AND active=1', (stock_id,)).fetchall()]
        except Exception:
            pass
        qty = _f(s.get('qty'))
        daily = sales['d30'][0] / 30.0
        s['_days_left'] = (qty / daily) if (daily > 0 and qty > 0) else None
        return dict(stock=s, sales=sales, last_sales=last_sales, bought=bought, last_purchase=last_purchase,
                    moves=moves, prices=prices, extra_barcodes=extra)
    finally:
        c.close()


def margin(retail_incl, vat, cost_excl):
    """KDV dahil satış fiyatı ve KDV hariç maliyetten (birim kâr, kâr %)."""
    net = _f(retail_incl) / (1 + _f(vat) / 100.0)
    profit = net - _f(cost_excl)
    pct = (profit / net * 100.0) if net > 0 else 0.0
    return profit, pct


def save_prices(stock_id, new, reason='Satış ekranından fiyat değişikliği'):
    """new: {'retail':x,'wholesale':y,'special_price':z} (yalnız değişenler). Geçmişe yazar. Dönüş: değişen alan listesi."""
    labels = {'retail': 'Perakende', 'wholesale': 'Toptan', 'special_price': 'Özel Fiyat'}
    c = _con()
    changed = []
    try:
        c.execute('BEGIN')
        old = c.execute('SELECT retail,wholesale,special_price,previous_price FROM stock WHERE id=?', (stock_id,)).fetchone()
        if not old:
            raise RuntimeError('Stok kartı bulunamadı.')
        sets = {}
        for k, v in new.items():
            if k not in labels:
                continue
            v = round(float(v), 6)
            if v < 0:
                raise ValueError('Fiyat negatif olamaz.')
            o = _f(old[k])
            if abs(v - o) > 0.000001:
                changed.append((k, o, v))
                sets[k] = v
                c.execute('INSERT INTO price_history(stock_id,dt,price_type,old_price,new_price,user,reason,batch_id) VALUES(?,?,?,?,?,?,?,?)',
                          (stock_id, CTX['now'](), labels[k], o, v, CTX['user'](), reason, None))
        if 'retail' in sets:
            sets['previous_price'] = _f(old['retail'])
        if sets:
            sets['updated'] = CTX['now']()
            c.execute('UPDATE stock SET ' + ','.join(f'{k}=?' for k in sets) + ' WHERE id=?', tuple(sets.values()) + (stock_id,))
            c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                      (CTX['now'](), CTX['user'](), 'Stok', 'Satış Ekranı Fiyat Değişikliği', stock_id,
                       '; '.join(f'{labels[k]}={o:g}' for k, o, v in changed), '; '.join(f'{labels[k]}={v:g}' for k, o, v in changed), reason))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return changed


# ------------------------------------------------------------------ arayüz
def open_dialog(parent, stock_id, cart_item=None, cart_qty=0.0, on_price=None):
    """Ürün bilgi penceresi. on_price(new_retail): fiyat değişince ve 'sepete de uygula' seçiliyse çağrılır."""
    money = CTX['money']
    disp = CTX.get('display_dt', lambda x: str(x or '')[:16])
    info = collect(stock_id)
    if not info:
        messagebox.showwarning(CTX['app'], 'Bu satırın stok kartı bulunamadı (hizmet / serbest satır olabilir).', parent=parent)
        return None
    s = info['stock']
    sl = info['sales']
    w = CTX['new_toplevel'](parent)
    w.title('Ürün Bilgisi - ' + str(s['name']))
    w.geometry('1020x700')
    w.configure(bg='white')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass

    def head(frame, text, row, col, span=1):
        tk.Label(frame, text=text, bg='white', fg=RED, font=('Segoe UI', 10, 'bold'), anchor='w').grid(row=row, column=col, columnspan=span, sticky='w', padx=8, pady=(6, 2))

    def kv(frame, row, col, k, v, bold=False, color=INK):
        tk.Label(frame, text=k, bg='white', fg=INK, font=('Segoe UI', 9), anchor='w').grid(row=row, column=col * 2, sticky='w', padx=(10, 6), pady=1)
        tk.Label(frame, text=v, bg='white', fg=color, font=('Segoe UI', 10, 'bold' if bold else 'normal'), anchor='e').grid(row=row, column=col * 2 + 1, sticky='e', padx=(0, 10), pady=1)

    # ---- üst bant
    top = tk.Frame(w, bg='#eaf3fc', highlightthickness=1, highlightbackground=LINE)
    top.pack(fill='x', padx=10, pady=(10, 4))
    tk.Label(top, text=str(s['name']), bg='#eaf3fc', fg=INK, font=('Segoe UI', 16, 'bold'), anchor='w').pack(fill='x', padx=12, pady=(8, 0))
    sub = '   •   '.join(x for x in (
        'Barkod: ' + str(s.get('barcode') or '-'), 'Kod: ' + str(s.get('code') or '-'),
        'Marka: ' + str(s.get('brand') or '-'), 'Grup: ' + str(s.get('grp') or '-'),
        'Raf: ' + str(s.get('shelf') or '-'), 'Birim: ' + str(s.get('unit') or 'Adet'),
        '' if s.get('active') else 'PASİF KART') if x)
    tk.Label(top, text=sub, bg='#eaf3fc', fg=INK, font=('Segoe UI', 9), anchor='w').pack(fill='x', padx=12, pady=(0, 8))

    # ---- kartlar
    cards = tk.Frame(w, bg='white')
    cards.pack(fill='x', padx=10)
    for i in range(3):
        cards.columnconfigure(i, weight=1, uniform='c')

    def card(col):
        f = tk.Frame(cards, bg='white', highlightthickness=1, highlightbackground=LINE)
        f.grid(row=0, column=col, sticky='nsew', padx=3, pady=3)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        return f

    qty = _f(s.get('qty'))
    mn = _f(s.get('min_qty'))
    after = qty - _f(cart_qty)
    if qty < 0:
        state, col_state = 'NEGATİF STOK', RED
    elif qty == 0:
        state, col_state = 'STOKTA YOK', RED
    elif mn > 0 and qty <= mn:
        state, col_state = 'KRİTİK (minimum ' + _q(mn) + ')', '#b35a00'
    else:
        state, col_state = 'Yeterli', '#0b6b2e'
    c1 = card(0)
    head(c1, 'STOK DURUMU', 0, 0, 2)
    kv(c1, 1, 0, 'Mevcut stok', _q(qty) + ' ' + str(s.get('unit') or ''), True, col_state)
    kv(c1, 2, 0, 'Durum', state, True, col_state)
    kv(c1, 3, 0, 'Minimum / Maksimum', _q(mn) + ' / ' + _q(s.get('max_qty')))
    if cart_qty:
        kv(c1, 4, 0, 'Sepetteki miktar', _q(cart_qty))
        kv(c1, 5, 0, 'Satış sonrası kalan', _q(after), True, RED if after < 0 else INK)
    dl = s.get('_days_left')
    kv(c1, 6, 0, 'Tahmini yetme süresi', ('%d gün (son 30 gün hızıyla)' % round(dl)) if dl else '-')

    pr, pc = margin(s.get('retail'), s.get('vat'), s.get('avg_cost') or s.get('last_cost'))
    c2 = card(1)
    head(c2, 'FİYATLAR (KDV dahil)', 0, 0, 2)
    kv(c2, 1, 0, 'Perakende', money(s.get('retail')), True)
    kv(c2, 2, 0, 'Toptan', money(s.get('wholesale')))
    kv(c2, 3, 0, 'Özel fiyat', money(s.get('special_price')))
    kv(c2, 4, 0, 'Önceki perakende', money(s.get('previous_price')))
    kv(c2, 5, 0, 'KDV oranı', '%' + _q(s.get('vat')))
    lastchg = info['prices'][0]['dt'] if info['prices'] else ''
    kv(c2, 6, 0, 'Son fiyat değişimi', disp(lastchg) if lastchg else '-')

    c3 = card(2)
    head(c3, 'MALİYET / KÂR (KDV hariç)', 0, 0, 2)
    kv(c3, 1, 0, 'Son alış fiyatı', money(s.get('last_cost')))
    kv(c3, 2, 0, 'Ortalama maliyet', money(s.get('avg_cost')))
    kv(c3, 3, 0, 'Birim kâr', money(pr), True, RED if pr < 0 else INK)
    kv(c3, 4, 0, 'Kâr oranı', '%' + CTX['number'](pc, 1), True, RED if pc < 0 else INK)
    lp = info['last_purchase']
    kv(c3, 5, 0, 'Son alış', (disp(lp['dt'])[:10] + ' • ' + _q(lp['qty']) + ' adet') if lp else '-')
    kv(c3, 6, 0, 'Tedarikçi', (lp['sup'] or '-') if lp else '-')

    # ---- satış özeti
    sm = tk.Frame(w, bg='white', highlightthickness=1, highlightbackground=LINE)
    sm.pack(fill='x', padx=13, pady=(2, 4))
    tk.Label(sm, text='SATIŞ ÖZETİ', bg='white', fg=RED, font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky='w', padx=8, pady=(6, 2))
    cols = [('Bugün', 'today'), ('Son 7 gün', 'd7'), ('Son 30 gün', 'd30'), ('Son 90 gün', 'd90'), ('Son 365 gün', 'd365'), ('Toplam', 'all')]
    for i, (lab, key) in enumerate(cols):
        sm.columnconfigure(i + 1, weight=1)
        tk.Label(sm, text=lab, bg='white', fg=INK, font=('Segoe UI', 9, 'bold')).grid(row=0, column=i + 1, pady=(6, 0))
        tk.Label(sm, text=_q(sl[key][0]) + ' ' + str(s.get('unit') or ''), bg='white', fg=INK, font=('Segoe UI', 11, 'bold')).grid(row=1, column=i + 1)
        tk.Label(sm, text=money(sl[key][1]), bg='white', fg=INK, font=('Segoe UI', 9)).grid(row=2, column=i + 1, pady=(0, 4))
    tk.Label(sm, text='Adet', bg='white', fg=INK, font=('Segoe UI', 9)).grid(row=1, column=0, sticky='w', padx=8)
    tk.Label(sm, text='Ciro', bg='white', fg=INK, font=('Segoe UI', 9)).grid(row=2, column=0, sticky='w', padx=8)
    foot = ('İlk satış: ' + (disp(sl['first'])[:10] if sl['first'] else '-') + '   •   Son satış: ' + (disp(sl['last'])[:10] if sl['last'] else '-') +
            '   •   Toplam alınan: ' + _q(info['bought'][0]) + '   •   Toplam satılan: ' + _q(sl['all'][0]))
    tk.Label(sm, text=foot, bg='white', fg=INK, font=('Segoe UI', 9), anchor='w').grid(row=3, column=0, columnspan=7, sticky='w', padx=8, pady=(0, 6))

    # ---- alt: listeler + fiyat değiştir
    body = tk.Frame(w, bg='white')
    body.pack(fill='both', expand=True, padx=10, pady=(0, 6))
    nb = ttk.Notebook(body)
    nb.pack(side='left', fill='both', expand=True)

    def tab(title, cols, widths, rows, anchors=None):
        f = tk.Frame(nb, bg='white')
        nb.add(f, text=title)
        t = ttk.Treeview(f, columns=[c[0] for c in cols], show='headings', height=6)
        for (cid, h), wd in zip(cols, widths):
            t.heading(cid, text=h)
            t.column(cid, width=wd, anchor=('e' if cid in ('qty', 'price', 'total', 'old', 'new') else 'w'))
        t.pack(fill='both', expand=True)
        for r in rows:
            t.insert('', 'end', values=r)
        return t

    tab('Son Satışlar', [('dt', 'Tarih'), ('doc', 'Fiş No'), ('cari', 'Müşteri'), ('qty', 'Miktar'), ('price', 'Fiyat'), ('total', 'Tutar')],
        [130, 110, 200, 70, 90, 100],
        [(disp(x['dt']), x['doc_no'], x['cari'], _q(x['qty']), money(x['price']), money(x['total'])) for x in info['last_sales']])
    tab('Stok Hareketleri', [('dt', 'Tarih'), ('kind', 'İşlem'), ('qty', 'Miktar'), ('note', 'Açıklama')], [130, 190, 80, 300],
        [(disp(x['dt']), x['kind'], _q(x['qty']), x['note'] or '') for x in info['moves']])
    tab('Fiyat Geçmişi', [('dt', 'Tarih'), ('type', 'Tür'), ('old', 'Eski'), ('new', 'Yeni'), ('user', 'Kullanıcı'), ('why', 'Neden')], [130, 100, 90, 90, 100, 220],
        [(disp(x['dt']), x['price_type'], money(x['old_price']), money(x['new_price']), x['user'] or '', x['reason'] or '') for x in info['prices']])

    # fiyat değiştirme paneli
    pf = tk.Frame(body, bg='#f6faff', highlightthickness=1, highlightbackground=LINE)
    pf.pack(side='right', fill='y', padx=(8, 0))
    tk.Label(pf, text='FİYAT DEĞİŞTİR', bg='#f6faff', fg=RED, font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(8, 2))
    tk.Label(pf, text='Stok kartındaki fiyatı günceller;\ngeçmişe kullanıcı ve zamanla yazılır.', bg='#f6faff', fg=INK, font=('Segoe UI', 8), justify='left').pack(anchor='w', padx=10)
    ents = {}
    for key, lab in (('retail', 'Perakende (KDV dahil)'), ('wholesale', 'Toptan'), ('special_price', 'Özel fiyat')):
        tk.Label(pf, text=lab, bg='#f6faff', fg=INK, font=('Segoe UI', 9)).pack(anchor='w', padx=10, pady=(6, 0))
        v = tk.StringVar(value=('%.2f' % _f(s.get(key))).replace('.', ','))
        e = tk.Entry(pf, textvariable=v, justify='right', font=('Segoe UI', 12), width=14)
        e.pack(padx=10, pady=(0, 0), ipady=2)
        ents[key] = v
    live = tk.Label(pf, text='', bg='#f6faff', fg=INK, font=('Segoe UI', 9, 'bold'), justify='left')
    live.pack(anchor='w', padx=10, pady=(6, 0))
    apply_cart = tk.BooleanVar(value=bool(cart_item))
    if cart_item is not None:
        tk.Checkbutton(pf, text='Sepetteki satıra da uygula', variable=apply_cart, bg='#f6faff', font=('Segoe UI', 9)).pack(anchor='w', padx=8, pady=(4, 0))
    tk.Label(pf, text='Neden (isteğe bağlı)', bg='#f6faff', fg=INK, font=('Segoe UI', 9)).pack(anchor='w', padx=10, pady=(6, 0))
    why = tk.StringVar()
    tk.Entry(pf, textvariable=why, font=('Segoe UI', 10), width=22).pack(padx=10, ipady=2)

    def num(v):
        return CTX['fnum'](v.get())

    def upd(*_):
        p, pc2 = margin(num(ents['retail']), s.get('vat'), s.get('avg_cost') or s.get('last_cost'))
        live.config(text='Birim kâr: ' + money(p) + '\nKâr oranı: %' + CTX['number'](pc2, 1), fg=(RED if p < 0 else INK))
    for v in ents.values():
        v.trace_add('write', upd)
    upd()

    def do_save():
        new = {k: num(v) for k, v in ents.items()}
        cur = {k: _f(s.get(k)) for k in new}
        diff = {k: v for k, v in new.items() if abs(v - cur[k]) > 0.000001}
        if not diff:
            return messagebox.showinfo(CTX['app'], 'Fiyatta değişiklik yok.', parent=w)
        if not CTX['require'](w, 'Stok', 'Stok kartı fiyatı değiştirme'):
            return
        cost = _f(s.get('avg_cost') or s.get('last_cost'))
        if 'retail' in diff and cost > 0 and diff['retail'] / (1 + _f(s.get('vat')) / 100.0) < cost:
            if not messagebox.askyesno(CTX['app'], 'Yeni perakende fiyat KDV hariç maliyetin altında kalıyor.\nYine de kaydedilsin mi?', parent=w):
                return
        try:
            ch = save_prices(stock_id, diff, (why.get().strip() or 'Satış ekranından fiyat değişikliği'))
        except Exception as e:
            return messagebox.showerror(CTX['app'], 'Fiyat kaydedilemedi:\n' + str(e), parent=w)
        if on_price and 'retail' in diff and cart_item is not None and apply_cart.get():
            try:
                on_price(diff['retail'])
            except Exception:
                pass
        messagebox.showinfo(CTX['app'], 'Fiyat güncellendi: ' + ', '.join(f"{ {'retail':'Perakende','wholesale':'Toptan','special_price':'Özel'}[k] } {money(n)}" for k, o, n in ch), parent=w)
        w.destroy()

    tk.Button(pf, text='FİYATI KAYDET', command=do_save, bg='#1f6fd6', fg='white', font=('Segoe UI', 10, 'bold'), relief='flat', padx=10, pady=6).pack(fill='x', padx=10, pady=(10, 4))
    tk.Button(w, text='KAPAT (Esc)', command=w.destroy, bg='white', fg=INK, relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), padx=14, pady=5).pack(side='bottom', anchor='e', padx=12, pady=(0, 10))
    w.bind('<Escape>', lambda e: w.destroy())
    try:
        w.focus_force()
    except Exception:
        pass
    return w
