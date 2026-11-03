# -*- coding: utf-8 -*-
"""ALFABE döviz kurları: TCMB günlük kur bülteni (today.xml) internetten çekilir ve yerelde saklanır.
İnternet yoksa son kaydedilen kurlar gösterilir. Satış/cari/kasa kayıtlarını değiştirmez."""
import datetime
import urllib.request
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, messagebox

URL = 'https://www.tcmb.gov.tr/kurlar/today.xml'
SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_rates(
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, code TEXT NOT NULL, name TEXT DEFAULT '', unit REAL DEFAULT 1,
 fx_buy REAL DEFAULT 0, fx_sell REAL DEFAULT 0, note_buy REAL DEFAULT 0, note_sell REAL DEFAULT 0, fetched TEXT, UNIQUE(day,code));
"""
MAIN = ('USD', 'EUR', 'GBP', 'CHF', 'SAR', 'JPY')


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def _num(t):
    try:
        return float((t or '').strip().replace(',', '.'))
    except ValueError:
        return 0.0


def parse_bulletin(xml_bytes):
    """TCMB bülteni -> (gün 'YYYY-AA-GG', [{code,name,unit,fx_buy,fx_sell,note_buy,note_sell}])."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as ex:
        raise ValueError('Kur bülteni okunamadı: %s' % ex)
    if root.tag != 'Tarih_Date':
        raise ValueError('Beklenmeyen kur dosyası biçimi.')
    day = datetime.datetime.strptime(root.get('Tarih'), '%d.%m.%Y').date().isoformat()
    out = []
    for cur in root.findall('Currency'):
        code = cur.get('Kod') or cur.get('CurrencyCode') or ''
        g = lambda tag: _num(cur.findtext(tag))
        row = {'code': code, 'name': (cur.findtext('Isim') or '').strip(), 'unit': g('Unit') or 1.0,
               'fx_buy': g('ForexBuying'), 'fx_sell': g('ForexSelling'), 'note_buy': g('BanknoteBuying'), 'note_sell': g('BanknoteSelling')}
        if code and (row['fx_buy'] > 0 or row['fx_sell'] > 0):
            out.append(row)
    if not out:
        raise ValueError('Kur bülteninde kur bulunamadı.')
    return day, out


def store(c, day, rows):
    ensure_schema(c)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        c.execute('INSERT INTO exchange_rates(day,code,name,unit,fx_buy,fx_sell,note_buy,note_sell,fetched) VALUES(?,?,?,?,?,?,?,?,?) '
                  'ON CONFLICT(day,code) DO UPDATE SET name=excluded.name,unit=excluded.unit,fx_buy=excluded.fx_buy,fx_sell=excluded.fx_sell,'
                  'note_buy=excluded.note_buy,note_sell=excluded.note_sell,fetched=excluded.fetched',
                  (day, r['code'], r['name'], r['unit'], r['fx_buy'], r['fx_sell'], r['note_buy'], r['note_sell'], now))
    c.commit()
    return len(rows)


def fetch(timeout=10):
    req = urllib.request.Request(URL, headers={'User-Agent': 'ALFABE-SATIS-PRO'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_bulletin(resp.read())


def update(c, timeout=10):
    """İnternetten kurları çekip kaydeder. (gün, adet) döndürür; ağ hatasında ConnectionError."""
    try:
        day, rows = fetch(timeout)
    except ValueError:
        raise
    except Exception as ex:
        raise ConnectionError('TCMB kur sunucusuna ulaşılamadı: %s' % ex)
    return day, store(c, day, rows)


def latest(c):
    """Kayıtlı en son bülten: (gün, {kod: satır}). Kayıt yoksa (None, {})."""
    ensure_schema(c)
    d = c.execute('SELECT MAX(day) FROM exchange_rates').fetchone()[0]
    if not d:
        return None, {}
    return d, {r['code']: r for r in c.execute('SELECT * FROM exchange_rates WHERE day=?', (d,)).fetchall()}


def convert(amount, rate_row, to_try=True, basis='fx_sell'):
    """Döviz<->TL çevirir. basis: 'fx_sell' (satış), 'fx_buy' (alış), 'note_sell', 'note_buy'. Birim (örn. JPY 100) hesaba katılır."""
    rate = float(rate_row[basis]) / float(rate_row['unit'] or 1)
    if rate <= 0:
        raise ValueError('Bu kur için %s değeri yok.' % basis)
    return round(float(amount) * rate, 2) if to_try else round(float(amount) / rate, 2)


def open_dialog(parent, connection_factory):
    c = connection_factory()
    try:
        ensure_schema(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Döviz Kurları (TCMB)')
    w.geometry('760x560')
    st = ttk.Label(w, font=('Segoe UI', 10, 'bold'))
    st.pack(anchor='w', padx=12, pady=(12, 4))
    t = ttk.Treeview(w, columns=('c', 'n', 'fb', 'fs', 'nb', 'ns'), show='headings', height=14)
    for k, h, wd in (('c', 'Kod', 60), ('n', 'Para Birimi', 230), ('fb', 'Döviz Alış', 100), ('fs', 'Döviz Satış', 100), ('nb', 'Efektif Alış', 100), ('ns', 'Efektif Satış', 100)):
        t.heading(k, text=h)
        t.column(k, width=wd, anchor='w' if k in ('c', 'n') else 'e')
    t.pack(fill='both', expand=True, padx=12, pady=6)
    rates = {}

    def db(fn):
        con = connection_factory()
        try:
            return fn(con)
        finally:
            con.close()

    def show():
        day, rs = db(latest)
        rates.clear()
        rates.update({k: dict(v) for k, v in rs.items()})
        t.delete(*t.get_children())
        order = [k for k in MAIN if k in rs] + sorted(k for k in rs if k not in MAIN)
        for k in order:
            r = rs[k]
            t.insert('', 'end', values=(k, r['name'], f"{r['fx_buy']:.4f}", f"{r['fx_sell']:.4f}", f"{r['note_buy']:.4f}", f"{r['note_sell']:.4f}"))
        st.config(text=('Bülten tarihi: ' + datetime.date.fromisoformat(day).strftime('%d.%m.%Y') if day else 'Kayıtlı kur yok. "KURLARI GÜNCELLE" ile internetten çekin.'))

    def refresh():
        try:
            day, n = db(update)
        except Exception as ex:
            return messagebox.showwarning('Döviz Kurları', str(ex) + '\n\nSon kaydedilen kurlar gösteriliyor.', parent=w)
        show()
        messagebox.showinfo('Döviz Kurları', f'{n} kur güncellendi ({day}).', parent=w)
    cv = ttk.Frame(w, padding=12)
    cv.pack(fill='x')
    amt, cur, direction, out = tk.StringVar(value='1'), tk.StringVar(value='USD'), tk.StringVar(value='TL'), tk.StringVar()
    ttk.Label(cv, text='Tutar').pack(side='left')
    ttk.Entry(cv, textvariable=amt, width=12).pack(side='left', padx=4)
    ttk.Combobox(cv, textvariable=cur, values=list(MAIN), width=6).pack(side='left')
    ttk.Combobox(cv, textvariable=direction, values=['TL', 'Döviz'], state='readonly', width=7).pack(side='left', padx=6)

    def calc():
        r = rates.get(cur.get().strip().upper())
        if not r:
            return out.set('Önce kurları güncelleyin / geçerli kod girin.')
        try:
            a = float(amt.get().replace(',', '.'))
            if direction.get() == 'TL':
                out.set(f"{a:g} {r['code']} = {convert(a, r, True):,.2f} TL  (döviz satış kuru)")
            else:
                out.set(f"{a:,.2f} TL = {convert(a, r, False):,.2f} {r['code']}  (döviz satış kuru)")
        except ValueError as ex:
            out.set(str(ex) if 'kur' in str(ex) else 'Tutar sayı olmalıdır.')
    ttk.Button(cv, text='ÇEVİR', command=calc).pack(side='left')
    ttk.Label(cv, textvariable=out, font=('Segoe UI', 10, 'bold')).pack(side='left', padx=10)
    ttk.Label(w, text='"TL" seçiliyse tutar dövizdir ve TL karşılığı gösterilir; "Döviz" seçiliyse tutar TL\'dir.', foreground='#526477').pack(anchor='w', padx=12)
    bf = ttk.Frame(w, padding=12)
    bf.pack(fill='x')
    ttk.Button(bf, text='KURLARI GÜNCELLE (İNTERNET)', command=refresh).pack(side='left')
    show()
