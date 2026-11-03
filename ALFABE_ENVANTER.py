# -*- coding: utf-8 -*-
"""ALFABE STOK ENVANTERİ: stok miktarını seçilen fiyatla değerler; toplam değeri ve giriş/çıkış toplamlarını verir.
Yalnız OKUR: stok, alış ve hareket verisine hiçbir şey yazmaz.

Fiyat tabanları (programdaki alanlar):
  - Ortalama Maliyet / Son Alış : stok kartındaki iskontolu NET maliyet (KDV HARİÇ)
  - Ortalama Alış (Ağırlıklı/Basit): seçilen iki tarih arasındaki alış satırlarından (iade belgeleri hariç)
  - Perakende / Toptan / Özel fiyat : stok kartında KDV DAHİL tutulur
İskontolu seçeneği KAPALIYSA Son Alış ve Ortalama Alış, net maliyet alış belgesindeki cari ve satır iskontosu geri açılarak
(net / ((1-cari%)*(1-satır%))) hesaplanır; böylece koli/birim çarpanı ve KDV'den etkilenmez. Ortalama Maliyet her zaman net maliyettir.
"""
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

FIYATLAR = [('ort_maliyet', 'Ortalama Maliyet'), ('son_alis', 'Son Alış'),
            ('ort_alis_agirlikli', 'Ortalama Alış (Ağırlıklı)'), ('ort_alis_basit', 'Ortalama Alış (Basit)'),
            ('perakende', 'Perakende Fiyatı'), ('toptan', 'Toptan Fiyatı'), ('ozel', 'Özel Fiyat')]
YEDEKLER = [('yok', 'Hiçbiri (sıfır olsun)'), ('perakende', 'Perakende Fiyatı'), ('toptan', 'Toptan Fiyatı'), ('ozel', 'Özel Fiyat')]
_DAHIL = {'perakende': 'retail', 'toptan': 'wholesale', 'ozel': 'special_price'}   # KDV DAHİL tutulan alanlar


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _purchase_lines(rows, t1, t2):
    """Alış satırları: (stock_id, qty, net_maliyet, iskontosuz_maliyet_KDV_hariç, tarih). İade belgeleri hariç."""
    sql = ("SELECT pl.stock_id sid, pl.qty qty, pl.cost net, t.cd cd, t.ld ld, p.dt dt "
           "FROM purchase_lines pl JOIN purchases p ON p.id=pl.purchase_id "
           "LEFT JOIN (SELECT purchase_id,stock_id,AVG(cari_discount) cd,AVG(line_discount) ld "
           "FROM purchase_line_tax GROUP BY purchase_id,stock_id) t ON t.purchase_id=pl.purchase_id AND t.stock_id=pl.stock_id "
           "WHERE COALESCE(p.status,'Kesinleşti')!='İade' ORDER BY p.dt,p.id,pl.id")
    try:
        kayitlar = rows(sql, ())
    except Exception:
        # purchase_line_tax tablosu henüz yoksa (hiç alış girilmemiş / eski veri): iskonto bilgisi yok sayılır, net maliyet kullanılır.
        kayitlar = rows("SELECT pl.stock_id sid, pl.qty qty, pl.cost net, 0 cd, 0 ld, p.dt dt FROM purchase_lines pl "
                        "JOIN purchases p ON p.id=pl.purchase_id WHERE COALESCE(p.status,'Kesinleşti')!='İade' ORDER BY p.dt,p.id,pl.id", ())
    out = []
    for r in kayitlar:
        d = str(r['dt'] or '')[:10]
        net = _f(r['net'])
        k = (1 - min(max(_f(r['cd']), 0), 99.99) / 100.0) * (1 - min(max(_f(r['ld']), 0), 99.99) / 100.0)
        out.append((int(r['sid']), _f(r['qty']), net, net / k if k > 0 else net, d))
    return out


def hesapla(rows, opts):
    """rows(sql,args) -> satır listesi (r['kolon'] ile erişilir). opts anahtarları:
    fiyat, kdv ('haric'|'dahil'), yedek, sadece_mevcut (bool), iskontolu (bool), tarih1, tarih2 (YYYY-AA-GG).
    Dönüş: (satırlar, toplamlar)."""
    fiyat = opts.get('fiyat', 'ort_maliyet')
    dahil = opts.get('kdv', 'haric') == 'dahil'
    yedek = opts.get('yedek', 'yok')
    iskontolu = bool(opts.get('iskontolu', True))
    t1, t2 = str(opts.get('tarih1') or '0000-01-01'), str(opts.get('tarih2') or '9999-12-31')

    stocks = rows("SELECT id,code,special,name,brand,grp,unit,vat,qty,last_cost,avg_cost,retail,wholesale,special_price "
                  "FROM stock WHERE active=1 ORDER BY name", ())

    lines = _purchase_lines(rows, t1, t2) if (fiyat.startswith('ort_alis') or (fiyat == 'son_alis' and not iskontolu)) else []
    agg = {}      # stock_id -> [toplam_adet, toplam_tutar, toplam_basit, satır_sayısı]  (seçilen tarih aralığı)
    last = {}     # stock_id -> son alışın maliyeti (tüm zamanlar, iskontosuz seçenek için)
    for sid, q, net, gross, d in lines:
        c = net if iskontolu else gross
        last[sid] = gross
        if t1 <= d <= t2:
            a = agg.setdefault(sid, [0.0, 0.0, 0.0, 0])
            a[0] += q; a[1] += q * c; a[2] += c; a[3] += 1

    try:
        t2_son = (datetime.date.fromisoformat(t2) + datetime.timedelta(days=1)).isoformat()
    except ValueError:
        t2_son = '9999-12-31'
    moves = {}
    for r in rows("SELECT stock_id sid, SUM(CASE WHEN qty>0 AND kind NOT LIKE '%Açılış%' THEN qty ELSE 0 END) i, "
                  "SUM(CASE WHEN qty<0 AND kind NOT LIKE '%Açılış%' THEN -qty ELSE 0 END) o FROM stock_moves WHERE dt>=? AND dt<? GROUP BY stock_id",
                  (t1, t2_son)):
        moves[int(r['sid'])] = (_f(r['i']), _f(r['o']))

    def kdv_donustur(deger, vat, dahil_alan):
        """Alanın KDV durumundan (dahil_alan) istenen duruma çevirir."""
        k = 1 + _f(vat) / 100.0
        if dahil_alan and not dahil:
            return deger / k
        if not dahil_alan and dahil:
            return deger * k
        return deger

    def taban(s, kind):
        """(fiyat, KDV_dahil_mi) ; fiyat yoksa 0."""
        sid = int(s['id'])
        if kind in _DAHIL:
            return _f(s[_DAHIL[kind]]), True
        if kind == 'ort_maliyet':
            return _f(s['avg_cost']), False
        if kind == 'son_alis':
            return (_f(s['last_cost']) if iskontolu else last.get(sid, 0.0)), False
        a = agg.get(sid)
        if not a or a[3] == 0:
            return 0.0, False
        if kind == 'ort_alis_agirlikli':
            return (a[1] / a[0] if a[0] > 0 else 0.0), False
        return a[2] / a[3], False

    out = []
    tot = {'kayit': 0, 'qty': 0.0, 'deger': 0.0, 'giris': 0.0, 'cikis': 0.0, 'yedek': 0}
    for s in stocks:
        qty = _f(s['qty'])
        if opts.get('sadece_mevcut', True) and qty <= 0:
            continue
        p, p_dahil = taban(s, fiyat)
        kullanildi = False
        if p <= 0 and yedek != 'yok':
            p, p_dahil = taban(s, yedek)
            kullanildi = p > 0
        p = kdv_donustur(p, s['vat'], p_dahil) if p > 0 else 0.0
        gi, ci = moves.get(int(s['id']), (0.0, 0.0))
        deger = round(qty * p, 2)
        p = round(p, 4)
        out.append({'id': int(s['id']), 'code': s['code'] or '', 'special': s['special'] or '', 'name': s['name'] or '',
                    'grp': s['grp'] or '', 'brand': s['brand'] or '', 'qty': qty, 'unit': s['unit'] or '',
                    'price': p, 'value': deger, 'vat': _f(s['vat']), 'giris': gi, 'cikis': ci, 'yedek': kullanildi})
        tot['kayit'] += 1; tot['qty'] += qty; tot['deger'] += deger; tot['giris'] += gi; tot['cikis'] += ci
        tot['yedek'] += 1 if kullanildi else 0
    tot['deger'] = round(tot['deger'], 2)
    return out, tot


HEADS = ['Stok Kodu', 'Özel Kod', 'Ürün Adı', 'Grup', 'Marka', 'Mevcut', 'Birim', 'Envanter Fiyatı', 'Toplam Değer', 'KDV %',
         'Toplam Giriş', 'Toplam Çıkış']


def open_dialog(parent, rows, h):
    """Stok Envanteri penceresi. h: ana programdan gelen yardımcılar (new_toplevel, date_entry, money, btn, preview, export, theme)."""
    th = h['theme']
    BG, TXT, MUT, BORDER, RED = th['BG'], th['TXT'], th['MUT'], th['BORDER'], th['RED']
    money = h['money']
    w = h['new_toplevel'](parent)
    w.title('Stok Envanteri'); w.geometry('1380x780'); w.minsize(1000, 600); w.configure(bg=BG)
    tk.Label(w, text='Stok Envanteri', bg=BG, fg=TXT, font=('Segoe UI', 22, 'bold')).pack(anchor='w', padx=18, pady=(12, 2))

    today = datetime.date.today()
    v_fiyat = tk.StringVar(value=dict(FIYATLAR)['ort_maliyet']); v_kdv = tk.StringVar(value='KDV Hariç')
    v_yedek = tk.StringVar(value=dict(YEDEKLER)['yok']); v_isk = tk.BooleanVar(value=True); v_mevcut = tk.BooleanVar(value=True)
    v_t1 = tk.StringVar(value=datetime.date(today.year, 1, 1).isoformat()); v_t2 = tk.StringVar(value=today.isoformat())
    state = {'rows': [], 'tot': None, 'opts': None}

    bar = tk.Frame(w, bg=BG); bar.pack(fill='x', padx=18, pady=(6, 6))
    box = tk.Frame(w, bg='#e8f0fb', highlightbackground=BORDER, highlightthickness=1); box.pack(fill='x', padx=18, pady=(0, 6))

    def col(parent_, label, widget_factory, c):
        f = tk.Frame(parent_, bg='#e8f0fb'); f.grid(row=0, column=c, padx=10, pady=8, sticky='sw')
        tk.Label(f, text=label, bg='#e8f0fb', fg=MUT, font=('Segoe UI', 9)).pack(anchor='w'); widget_factory(f).pack(anchor='w')
    def combo(var, values, wd):
        return lambda f: ttk.Combobox(f, textvariable=var, values=values, state='readonly', width=wd)
    col(box, 'Hesaplama fiyatı', combo(v_fiyat, [n for _, n in FIYATLAR], 26), 0)
    col(box, 'KDV', combo(v_kdv, ['KDV Hariç', 'KDV Dahil'], 12), 1)
    col(box, 'Fiyatı yoksa (0 ise)', combo(v_yedek, [n for _, n in YEDEKLER], 22), 2)
    col(box, 'Başlangıç (ortalama alış, giriş/çıkış)', lambda f: h['date_entry'](f, v_t1, width=12), 3)
    col(box, 'Bitiş', lambda f: h['date_entry'](f, v_t2, width=12), 4)
    chk = tk.Frame(box, bg='#e8f0fb'); chk.grid(row=0, column=5, padx=14, pady=8, sticky='sw')
    tk.Checkbutton(chk, text='İskontolu fiyattan hesaplansın', variable=v_isk, bg='#e8f0fb', anchor='w').pack(anchor='w')
    tk.Checkbutton(chk, text='Sadece mevcut olanlar', variable=v_mevcut, bg='#e8f0fb', anchor='w').pack(anchor='w')

    title = tk.Label(w, text='STOK ENVANTERİ (henüz hesaplama yapılmadı)', bg=BG, fg=TXT, font=('Segoe UI', 10, 'bold'), anchor='w')
    title.pack(fill='x', padx=18)
    foot = tk.Label(w, text='', bg='#E8F1FB', fg=TXT, anchor='w', font=('Segoe UI', 10, 'bold'), padx=12, pady=6); foot.pack(side='bottom', fill='x', padx=18, pady=(4, 10))
    cols = ['code', 'special', 'name', 'grp', 'brand', 'qty', 'unit', 'price', 'value', 'vat', 'giris', 'cikis']
    wid = [100, 90, 300, 120, 110, 80, 60, 115, 125, 55, 95, 95]
    tb = tk.Frame(w, bg=BG); tb.pack(fill='both', expand=True, padx=18, pady=(2, 0))
    tree = ttk.Treeview(tb, columns=cols, show='headings', selectmode='browse')
    for c, hd, wd in zip(cols, HEADS, wid):
        tree.heading(c, text=hd); tree.column(c, width=wd, anchor='e' if c in ('qty', 'price', 'value', 'vat', 'giris', 'cikis') else 'w')
    sb = tk.Scrollbar(tb, orient='vertical', command=tree.yview); tree.configure(yscrollcommand=sb.set)
    tree.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
    tree.tag_configure('yedek', background='#fff7cc')

    def secenekler():
        kf = {n: k for k, n in FIYATLAR}; ky = {n: k for k, n in YEDEKLER}
        t1, t2 = v_t1.get().strip(), v_t2.get().strip()
        if t1 and t2 and t1 > t2:
            raise ValueError('Başlangıç tarihi bitiş tarihinden sonra olamaz.')
        return {'fiyat': kf[v_fiyat.get()], 'kdv': 'dahil' if v_kdv.get() == 'KDV Dahil' else 'haric', 'yedek': ky[v_yedek.get()],
                'sadece_mevcut': v_mevcut.get(), 'iskontolu': v_isk.get(), 'tarih1': t1, 'tarih2': t2}

    def hesapla_btn():
        try:
            o = secenekler()
            data, tot = hesapla(rows, o)
        except Exception as e:
            return messagebox.showerror('Stok Envanteri', 'Envanter hesaplanamadı:\n' + str(e), parent=w)
        state.update(rows=data, tot=tot, opts=o)
        tree.delete(*tree.get_children())
        for r in data:
            tree.insert('', 'end', tags=('yedek',) if r['yedek'] else (),
                        values=(r['code'], r['special'], r['name'], r['grp'], r['brand'], f"{r['qty']:g}", r['unit'], money(r['price']),
                                money(r['value']), f"{r['vat']:g}", f"{r['giris']:g}", f"{r['cikis']:g}"))
        title.config(text=f"STOK ENVANTERİ — {today.strftime('%d.%m.%Y')} itibarıyla, {v_fiyat.get()}, {v_kdv.get()}"
                          + ('' if v_isk.get() else ', iskontosuz'))
        ek = f"   |   Fiyatı olmadığı için yedek fiyat kullanılan: {tot['yedek']} (sarı satırlar)" if tot['yedek'] else ''
        foot.config(text=f"{tot['kayit']} Kayıt   |   Toplam Mevcut: {tot['qty']:g}   |   TOPLAM DEĞER: {money(tot['deger'])}   |   "
                         f"Toplam Giriş: {tot['giris']:g}   |   Toplam Çıkış: {tot['cikis']:g}{ek}")

    def payload():
        if not state['tot']:
            messagebox.showinfo('Stok Envanteri', 'Önce ENVANTERİ HESAPLA düğmesine basın.', parent=w); return None
        return True

    def hatali(e):
        messagebox.showerror('Stok Envanteri', 'İşlem tamamlanamadı:\n' + str(e), parent=w)

    def preview(pdf=False):
        if payload():
            try:
                h['preview'](tree, 'Stok Envanteri', pdf)
            except Exception as e:
                hatali(e)

    def excel():
        if not payload():
            return
        t = state['tot']
        rws = [list(tree.item(i, 'values')) for i in tree.get_children()]
        total = ['', '', f"TOPLAM ({t['kayit']} kayıt)", '', '', f"{t['qty']:g}", '', '', money(t['deger']), '', f"{t['giris']:g}", f"{t['cikis']:g}"]
        try:
            h['export'](w, 'Stok Envanteri', HEADS, rws, total, 'STOK / Stok Envanteri')
        except Exception as e:
            hatali(e)

    for text, cmd, prim in [('ENVANTERİ HESAPLA', hesapla_btn, True), ('ÖN İZLE / YAZDIR', preview, False),
                            ('PDF', lambda: preview(True), False), ('EXCEL', excel, False)]:
        h['btn'](bar, text, cmd, prim).pack(side='left', padx=(0, 6))
    h['btn'](bar, 'KAPAT', w.destroy).pack(side='right')
    w._envanter = {'tree': tree, 'hesapla': hesapla_btn, 'vars': dict(fiyat=v_fiyat, kdv=v_kdv, yedek=v_yedek, isk=v_isk, mevcut=v_mevcut, t1=v_t1, t2=v_t2),
                   'state': state, 'foot': foot}
    return w
