# -*- coding: utf-8 -*-
"""ALFABE pencere kuralı (v424): programda açılan her alt pencere (Toplevel) ana programın TAM ORTASINDA açılır.

install() bir kez çağrılır; tk.Toplevel ve Wm.geometry yamalanır, böylece mevcut ve gelecekteki bütün pencereler kurala uyar.
Atlananlar: kodu açıkça konum veren pencere (geometry '...+x+y'), tam ekran/büyütülmüş, başlıksız (menü/ipucu) pencereler,
ve `_ab_nocenter = True` işaretli pencereler (ör. ikinci ekran müşteri ekranı).
"""
import re
import tkinter as tk

_RX_SIZE = re.compile(r'^(\d+)x(\d+)')
_RX_POS = re.compile(r'[+-]-?\d+[+-]-?\d+$')


def _ref_box(w):
    root = getattr(tk, '_default_root', None)
    try:
        if root is not None and root is not w and root.state() == 'normal' and root.winfo_viewable() and root.winfo_width() > 200:
            return root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    except Exception:
        pass
    return 0, 0, w.winfo_screenwidth(), w.winfo_screenheight()


def fit(w):
    """İçeriği pencereden büyükse (düğme/alan kesiliyorsa) pencereyi içeriğe göre büyütür; ekranı aşmaz."""
    try:
        if getattr(w, '_ab_nofit', False) or w.state() != 'normal' or w.overrideredirect():
            return False
        w.update_idletasks()
        wh = getattr(w, '_ab_wh', None)
        if not wh:
            return False
        rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        cw = w.winfo_width() if w.winfo_viewable() else wh[0]
        ch = w.winfo_height() if w.winfo_viewable() else wh[1]
        cw, ch = max(cw, 1) if w.winfo_viewable() else wh[0], max(ch, 1) if w.winfo_viewable() else wh[1]
        nw = min(max(cw, rw + 4), sw - 40)
        nh = min(max(ch, rh + 4), sh - 90)
        if nw > cw + 2 or nh > ch + 2:
            _orig_geom(w, '%dx%d' % (nw, nh))
            w._ab_wh = (nw, nh)
            return True
    except Exception:
        pass
    return False


def center(w, force=False):
    """Pencereyi ana programın ortasına alır. Başarıda True."""
    try:
        if not w.winfo_exists():
            return False
        if getattr(w, '_ab_nocenter', False):
            return False
        if not force and getattr(w, '_ab_pos', False) and not isinstance(w, _dialog_cls()):
            return False
        if w.state() in ('withdrawn', 'iconic', 'zoomed') or w.overrideredirect():
            return False
        try:
            if w.attributes('-fullscreen'):
                return False
        except Exception:
            pass
        fit(w)
        w.update_idletasks()
        wh = getattr(w, '_ab_wh', None)
        ww, hh = wh if wh else (w.winfo_reqwidth(), w.winfo_reqheight())
        if w.winfo_viewable():
            ww, hh = max(ww, w.winfo_width()), max(hh, w.winfo_height())
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        if ww >= sw - 60 and hh >= sh - 120:
            return False
        rx, ry, rw, rh = _ref_box(w)
        x = rx + (rw - ww) // 2
        y = ry + (rh - hh) // 2
        x = max(0, min(x, sw - ww))
        y = max(0, min(y, sh - hh - 40))
        _orig_geom(w, '+%d+%d' % (x, y))
        w._ab_centered = (x, y)
        return True
    except Exception:
        return False


def _dialog_cls():
    try:
        from tkinter import simpledialog
        return (simpledialog.Dialog,)
    except Exception:
        return ()


_BTN = (tk.Button, tk.Radiobutton, tk.Checkbutton, tk.Menubutton)


def _is_btn(x):
    try:
        from tkinter import ttk
        return isinstance(x, _BTN + (ttk.Button, ttk.Radiobutton, ttk.Checkbutton, ttk.Menubutton))
    except Exception:
        return isinstance(x, _BTN)


def _pad(v):
    try:
        if isinstance(v, (tuple, list)):
            return int(sum(int(float(str(a))) for a in v))
        return int(float(str(v))) * 2
    except Exception:
        return 8


def _flow(f):
    """Kayıtlı düğmeleri çerçeve genişliğine göre satırlara dizer (sığmayan alt satıra geçer)."""
    try:
        items = f._ab_items
        W = f.winfo_width()
        if W < 120:
            return
        rows, cur, used = [], [], 0
        for w_, padx, pady in items:
            need = w_.winfo_reqwidth() + padx
            if cur and used + need > W - 4:
                rows.append(cur); cur, used = [], 0
            cur.append((w_, padx, pady)); used += need
        if cur:
            rows.append(cur)
        # Son satırda 1-2 düğme yalnız kalmasın: önceki satırdan düğme alıp iki satırı dengele (genişlik aşılmaz).
        if len(rows) > 1 and len(rows[-1]) <= 2:
            def _w(r):
                return sum(x[0].winfo_reqwidth() + x[1] for x in r)
            while len(rows[-2]) > len(rows[-1]) + 1:
                mv = rows[-2][-1]
                if _w(rows[-1]) + mv[0].winfo_reqwidth() + mv[1] > W - 4:
                    break
                rows[-1].insert(0, rows[-2].pop())
        key = tuple(len(r) for r in rows)
        if key == getattr(f, '_ab_key', None):
            return
        f._ab_key = key
        for r, row in enumerate(rows):
            for c, (w_, padx, pady) in enumerate(row):
                w_.grid(row=r, column=c, padx=(0, max(0, padx // 2)) if padx else 0, pady=(0, 3), sticky='w')
    except Exception:
        pass


def wrap_row(f):
    """Yalnız yan yana (side=left) düğmelerden oluşan ve genişliğe sığmayan çerçeveyi çok satıra çevirir. Dönüş: yapıldı mı."""
    try:
        if getattr(f, '_ab_items', None) is not None:
            return False
        kids = f.pack_slaves()
        if len(kids) < 3 or f.grid_slaves():
            return False
        info = []
        for k in kids:
            pi = k.pack_info()
            if str(pi.get('side')) != 'left' or str(pi.get('expand')) not in ('0', 'False') or not _is_btn(k):
                return False
            info.append((k, _pad(pi.get('padx', 0)), 0))
        W = f.winfo_width()
        if W < 300 or not f.winfo_viewable():
            return False
        if sum(k.winfo_reqwidth() + p for k, p, _ in info) <= W:
            return False
        for k, _, _ in info:
            k.pack_forget()
        f._ab_items = info
        f.bind('<Configure>', lambda e, f=f: _flow(f), add='+')
        _flow(f)
        return True
    except Exception:
        return False


def _sweep(root):
    try:
        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, tk.Toplevel):
                    continue
                if type(ch) is tk.Frame:
                    wrap_row(ch)
                walk(ch)
        walk(root)
        for t in root.winfo_children():
            if isinstance(t, tk.Toplevel) and t.winfo_viewable():
                walk(t)
    except Exception:
        pass


def _hook_root(root):
    if getattr(root, '_ab_sweep_hooked', False):
        return
    root._ab_sweep_hooked = True
    st = {'t': None}

    def later(_e=None):
        if st['t'] is not None:
            return
        def run():
            st['t'] = None
            _sweep(root)
        try:
            st['t'] = root.after(350, run)
        except Exception:
            st['t'] = None
    try:
        root.bind_all('<Map>', later, add='+')
        root.bind('<Configure>', later, add='+')
    except Exception:
        pass
    later()


_orig_tk_init = tk.Tk.__init__


def _tk_init(self, *a, **k):
    _orig_tk_init(self, *a, **k)
    try:
        self.after_idle(lambda: _hook_root(self))
    except Exception:
        pass


_orig_init = tk.Toplevel.__init__
_orig_geom = tk.Wm.wm_geometry
_installed = [False]


def _geom(self, newGeometry=None):
    if newGeometry:
        m = _RX_SIZE.match(str(newGeometry))
        if m:
            self._ab_wh = (int(m.group(1)), int(m.group(2)))
        if _RX_POS.search(str(newGeometry)):
            self._ab_pos = True
    return _orig_geom(self, newGeometry)


def _init(self, *a, **k):
    _orig_init(self, *a, **k)
    try:
        self.after_idle(lambda: center(self))
        self.after(90, lambda: center(self))
        self.after(450, lambda: fit(self) and center(self, True))
    except Exception:
        pass


def install():
    if _installed[0]:
        return
    tk.Toplevel.__init__ = _init
    tk.Wm.wm_geometry = _geom
    tk.Wm.geometry = _geom
    tk.Toplevel._ab_center = True
    tk.Tk.__init__ = _tk_init
    if getattr(tk, '_default_root', None) is not None:
        _hook_root(tk._default_root)
    _installed[0] = True
