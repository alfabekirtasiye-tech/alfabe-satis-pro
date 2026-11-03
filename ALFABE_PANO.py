# -*- coding: utf-8 -*-
"""ALFABE kopyala / yapıştır desteği (program genelinde).
 * Metin kutularında (Entry, Text, açılır liste, sayı kutusu) sağ tık menüsü: Kes, Kopyala, Yapıştır, Tümünü Seç; Ctrl+A tümünü seçer.
 * Tablolarda (Treeview): Ctrl+C seçili satırları, Ctrl+Shift+C başlıklarla birlikte kopyalar; sağ tık menüsü: Satırı / Hücreyi /
   Seçilenleri / Tüm Tabloyu Kopyala. Kopyalanan metin sekmeyle ayrılır; Excel'e doğrudan yapıştırılır.
 * Tablonun kendi sağ tık menüsü ya da Ctrl+C kısayolu olan ekranlara dokunulmaz.
Veriyi değiştirmez; yalnızca panoya yazar / metin kutusuna yapıştırır."""
import tkinter as tk
from tkinter import ttk


def clean(v):
    return ' '.join(str(v if v is not None else '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').split())


def to_text(headers, rows, with_headers=False):
    lines = []
    if with_headers and headers:
        lines.append('\t'.join(clean(h) for h in headers))
    lines += ['\t'.join(clean(c) for c in r) for r in rows]
    return '\n'.join(lines)


def shown_columns(tree):
    cols = list(tree['columns'])
    disp = tree['displaycolumns']
    if isinstance(disp, str):
        disp = (disp,)
    disp = list(disp)
    if not disp or disp == ['#all']:
        return cols
    out = []
    for d in disp:
        if d in cols:
            out.append(d)
        elif str(d).isdigit() and int(d) < len(cols):
            out.append(cols[int(d)])
    return out or cols


def grab(tree, which='selected'):
    """Tablodan (başlıklar, satırlar) alır. which: selected | all."""
    cols = list(tree['columns'])
    show = shown_columns(tree)
    idx = [cols.index(c) for c in show]
    heads = []
    for c in show:
        try:
            heads.append(tree.heading(c, 'text'))
        except Exception:
            heads.append(c)
    ids = tree.get_children('') if which == 'all' else tree.selection()
    rows = []
    for iid in ids:
        vals = list(tree.item(iid, 'values'))
        rows.append([vals[i] if i < len(vals) else '' for i in idx])
    return heads, rows


def put_clipboard(widget, text):
    widget.clipboard_clear()
    widget.clipboard_append(text)


def copy_tree(tree, which='selected', with_headers=False):
    heads, rows = grab(tree, which)
    if not rows:
        return 0
    put_clipboard(tree, to_text(heads, rows, with_headers))
    return len(rows)


def copy_cell(tree, event):
    col = tree.identify_column(event.x)
    iid = tree.identify_row(event.y)
    if not iid:
        return 0
    show = shown_columns(tree)
    try:
        c = show[int(str(col)[1:]) - 1]
    except Exception:
        return 0
    vals = list(tree.item(iid, 'values'))
    i = list(tree['columns']).index(c)
    put_clipboard(tree, clean(vals[i] if i < len(vals) else ''))
    return 1


# ------------------------------------------------------------------ olay bağlayıcıları
def _has_own(widget, seq):
    try:
        return bool(widget.bind(seq))
    except Exception:
        return False


def _tree_ctrl_c(e, headers=False):
    t = e.widget
    if _has_own(t, '<Control-c>') or _has_own(t, '<Control-C>'):
        return None
    copy_tree(t, 'selected', headers)
    return 'break'


def _tree_menu(e):
    t = e.widget
    if not isinstance(t, ttk.Treeview) or _has_own(t, '<Button-3>'):
        return None
    if t.identify_region(e.x, e.y) == 'heading':
        return None          # sütun başlığı menüsü mevcut genel menüde
    iid = t.identify_row(e.y)
    if iid and iid not in t.selection():
        t.selection_set(iid)
    m = tk.Menu(t, tearoff=0)
    has_sel = bool(t.selection())
    st = 'normal' if has_sel else 'disabled'
    m.add_command(label='Satırı Kopyala', state=st, command=lambda: copy_tree(t, 'selected', False))
    m.add_command(label='Hücreyi Kopyala', state='normal' if iid else 'disabled', command=lambda: copy_cell(t, e))
    m.add_command(label='Seçilenleri Başlıklarla Kopyala', state=st, command=lambda: copy_tree(t, 'selected', True))
    m.add_separator()
    m.add_command(label='Tüm Tabloyu Kopyala (Excel\'e yapıştırılabilir)', command=lambda: copy_tree(t, 'all', True))
    m.tk_popup(e.x_root, e.y_root)
    t._alfabe_pano_menu = m
    return 'break'


def _text_state(w):
    try:
        return str(w.cget('state'))
    except Exception:
        return 'normal'


def _sel_exists(w):
    try:
        if isinstance(w, tk.Text):
            return bool(w.tag_ranges('sel'))
        return bool(w.selection_present())
    except Exception:
        return False


def _select_all(w):
    try:
        if isinstance(w, tk.Text):
            w.tag_add('sel', '1.0', 'end-1c')
        else:
            w.select_range(0, 'end')
            w.icursor('end')
    except Exception:
        pass


def _entry_menu(e):
    w = e.widget
    try:
        w.focus_set()
    except Exception:
        pass
    editable = _text_state(w) == 'normal'
    m = tk.Menu(w, tearoff=0)
    sel = _sel_exists(w)
    m.add_command(label='Kes', state='normal' if sel and editable else 'disabled', command=lambda: w.event_generate('<<Cut>>'))
    m.add_command(label='Kopyala', state='normal' if sel else 'disabled', command=lambda: w.event_generate('<<Copy>>'))
    m.add_command(label='Yapıştır', state='normal' if editable else 'disabled', command=lambda: w.event_generate('<<Paste>>'))
    m.add_separator()
    m.add_command(label='Tümünü Seç', command=lambda: _select_all(w))
    m.tk_popup(e.x_root, e.y_root)
    w._alfabe_pano_menu = m
    return 'break'


def _entry_select_all(e):
    _select_all(e.widget)
    return 'break'


def install(root):
    """Uygulama penceresi açılırken bir kez çağrılır."""
    for seq in ('<Control-c>', '<Control-C>', '<Control-Insert>'):
        root.bind_class('Treeview', seq, _tree_ctrl_c, add='+')
    for seq in ('<Control-Shift-c>', '<Control-Shift-C>'):
        root.bind_class('Treeview', seq, lambda e: _tree_ctrl_c(e, True), add='+')
    root.bind_class('Treeview', '<Button-3>', _tree_menu, add='+')
    for cls in ('Entry', 'TEntry', 'Text', 'TCombobox', 'Spinbox', 'TSpinbox'):
        root.bind_class(cls, '<Button-3>', _entry_menu, add='+')
        for seq in ('<Control-a>', '<Control-A>'):
            root.bind_class(cls, seq, _entry_select_all, add='+')
