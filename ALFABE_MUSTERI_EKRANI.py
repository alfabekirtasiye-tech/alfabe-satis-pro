# -*- coding: utf-8 -*-
"""ALFABE müşteri ekranı: satış sepetini müşteriye dönük, büyük yazıyla gösteren ayrı pencere (2. monitör için).
Yalnız gösterim yapar; satış/stok kaydına dokunmaz."""
import tkinter as tk
from tkinter import ttk


def fmt_money(v):
    return f'{float(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' ₺'


def summarize(cart, customer=''):
    """Sepetten gösterilecek satırlar ve toplamlar. Fiyat birim fiyatı, tutar iskonto düşülmüş satır tutarıdır."""
    rows, total, qty = [], 0.0, 0.0
    for x in cart:
        q = float(x.get('qty', 0) or 0)
        line = max(0.0, q * float(x.get('price', 0) or 0) - float(x.get('disc', 0) or 0) - float(x.get('camp_disc', 0) or 0))
        rows.append((str(x.get('name', '')), q, float(x.get('price', 0) or 0), line))
        total += line
        qty += q
    return {'rows': rows, 'total': round(total, 2), 'qty': qty, 'customer': customer}


class CustomerDisplay(tk.Toplevel):
    _ab_nocenter = True      # ikinci ekrana sürüklenen pencere: ortalanmaz
    def __init__(self, parent, title='Müşteri Ekranı'):
        super().__init__(parent)
        self.title(title)
        self.geometry('900x620')
        self.configure(bg='#0e2a52')
        tk.Label(self, text='HOŞ GELDİNİZ', bg='#0e2a52', fg='white', font=('Segoe UI', 22, 'bold')).pack(pady=(14, 4))
        self.cust = tk.Label(self, text='', bg='#0e2a52', fg='#9ec9ff', font=('Segoe UI', 14))
        self.cust.pack()
        st = ttk.Style(self)
        try:
            st.configure('Musteri.Treeview', rowheight=40, font=('Segoe UI', 16))
            st.configure('Musteri.Treeview.Heading', font=('Segoe UI', 13, 'bold'))
        except Exception:
            pass
        self.t = ttk.Treeview(self, columns=('n', 'q', 'p', 't'), show='headings', style='Musteri.Treeview')
        for k, h, wd in (('n', 'Ürün', 400), ('q', 'Adet', 80), ('p', 'Fiyat', 130), ('t', 'Tutar', 150)):
            self.t.heading(k, text=h)
            self.t.column(k, width=wd, anchor='w' if k == 'n' else 'e', stretch=(k == 'n'))
        self.t.pack(fill='both', expand=True, padx=20, pady=12)
        self.total = tk.Label(self, text='0,00 ₺', bg='#0e2a52', fg='#7CFC9A', font=('Segoe UI', 44, 'bold'))
        self.total.pack(pady=(0, 6))
        tk.Label(self, text='Bu pencereyi ikinci ekrana sürükleyin. Tam ekran: F11', bg='#0e2a52', fg='#6f8fb8', font=('Segoe UI', 9)).pack(pady=(0, 8))
        self._full = False
        self.bind('<F11>', self._toggle_full)
        self.bind('<Escape>', lambda e: self._set_full(False))

    def _set_full(self, on):
        self._full = bool(on)
        try:
            self.attributes('-fullscreen', self._full)
        except Exception:
            pass

    def _toggle_full(self, _e=None):
        self._set_full(not self._full)

    def show(self, cart, customer=''):
        s = summarize(cart, customer)
        self.t.delete(*self.t.get_children())
        for n, q, p, t in s['rows']:
            self.t.insert('', 'end', values=(n, f'{q:g}', fmt_money(p), fmt_money(t)))
        kids = self.t.get_children()
        if kids:
            self.t.see(kids[-1])
        self.total.config(text=fmt_money(s['total']))
        self.cust.config(text=customer or '')
