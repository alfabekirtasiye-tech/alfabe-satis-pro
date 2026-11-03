# -*- coding: utf-8 -*-
"""ALFABE - Etiket işini kaydet / yükle.

Bir etiket işi = etiket listesi (ürün + adet + şablon + birim) ve ekran ayarları
(ölçü, şablon, kağıt, yazıcı, satır/sütun, boşluklar). İsimle kaydedilir; tek tıkla geri yüklenir.
Fiyatlar kaydedilmez: yüklenince ürünlerin GÜNCEL satış fiyatı kullanılır.
"""
import datetime
import json
import tkinter as tk
from tkinter import ttk

SETTING_KEYS = ('label_width', 'label_height', 'label_template', 'label_paper', 'label_printer', 'label_rows', 'label_cols', 'label_hgap', 'label_vgap')


def ensure_table(c):
    c.execute('CREATE TABLE IF NOT EXISTS label_jobs(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,dt TEXT,payload TEXT NOT NULL)')


def build_payload(items, settings):
    """items: [{stock_id, qty, template, unit_name}]; settings: {anahtar: değer}."""
    return {'v': 1, 'items': [{'stock_id': int(i['stock_id']), 'qty': max(1, int(i.get('qty') or 1)), 'template': i.get('template') or '',
                               'unit_name': i.get('unit_name') or ''} for i in items],
            'settings': {k: str(settings.get(k, '')) for k in SETTING_KEYS if k in settings}}


def save(c, name, payload, overwrite=False):
    """(durum, mesaj): durum 'ok' | 'exists' | 'bad'."""
    name = str(name or '').strip()[:80]
    if not name:
        return 'bad', 'İsim boş olamaz.'
    if not payload.get('items'):
        return 'bad', 'Kaydedilecek etiket listesi boş.'
    ensure_table(c)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    r = c.execute('SELECT id FROM label_jobs WHERE lower(name)=lower(?)', (name,)).fetchone()
    if r and not overwrite:
        return 'exists', name
    data = json.dumps(payload, ensure_ascii=False)
    if r:
        c.execute('UPDATE label_jobs SET name=?,dt=?,payload=? WHERE id=?', (name, now, data, r[0]))
    else:
        c.execute('INSERT INTO label_jobs(name,dt,payload) VALUES(?,?,?)', (name, now, data))
    return 'ok', name


def list_jobs(c):
    ensure_table(c)
    out = []
    for r in c.execute('SELECT id,name,dt,payload FROM label_jobs ORDER BY dt DESC,id DESC'):
        try:
            p = json.loads(r[3])
        except Exception:
            p = {'items': []}
        its = p.get('items') or []
        out.append({'id': r[0], 'name': r[1], 'dt': r[2] or '', 'products': len(its), 'labels': sum(int(i.get('qty') or 0) for i in its), 'payload': p})
    return out


def delete(c, job_id):
    ensure_table(c)
    c.execute('DELETE FROM label_jobs WHERE id=?', (int(job_id),))


def rename(c, job_id, new_name):
    new_name = str(new_name or '').strip()[:80]
    if not new_name:
        return False
    ensure_table(c)
    if c.execute('SELECT 1 FROM label_jobs WHERE lower(name)=lower(?) AND id<>?', (new_name, int(job_id))).fetchone():
        return False
    c.execute('UPDATE label_jobs SET name=? WHERE id=?', (new_name, int(job_id)))
    return True


def existing_items(c, payload):
    """Hâlâ var olan (silinmemiş) ürünlerin kalemleri ve eksik kalem sayısı."""
    ok, missing = [], 0
    for i in payload.get('items') or []:
        if c.execute('SELECT 1 FROM stock WHERE id=? AND COALESCE(active,1)=1', (int(i['stock_id']),)).fetchone():
            ok.append(i)
        else:
            missing += 1
    return ok, missing


def open_jobs_dialog(parent, con_factory, on_load, title='Kayıtlı Etiket İşleri'):
    """on_load(payload, mode) çağrılır; mode: 'replace' | 'append'."""
    d = tk.Toplevel(parent)
    d.title(title)
    d.geometry('760x460')
    d.transient(parent)
    tk.Label(d, text='KAYITLI ETİKET İŞLERİ', font=('Segoe UI', 14, 'bold'), fg='#123a63').pack(anchor='w', padx=14, pady=(12, 2))
    tk.Label(d, text='Yüklenince ürünlerin güncel satış fiyatı kullanılır. Silinmiş ürünler atlanır.', fg='#5c7188').pack(anchor='w', padx=14)
    cols = ('name', 'dt', 'products', 'labels')
    t = ttk.Treeview(d, columns=cols, show='headings', selectmode='browse')
    for k, h, w in (('name', 'İş Adı', 330), ('dt', 'Kayıt Tarihi', 140), ('products', 'Ürün', 70), ('labels', 'Etiket', 70)):
        t.heading(k, text=h)
        t.column(k, width=w, anchor='w' if k in ('name', 'dt') else 'e')
    t.pack(fill='both', expand=True, padx=14, pady=8)
    data = {}

    def reload():
        t.delete(*t.get_children())
        data.clear()
        c = con_factory()
        try:
            for j in list_jobs(c):
                data[str(j['id'])] = j
                t.insert('', 'end', iid=str(j['id']), values=(j['name'], j['dt'], j['products'], j['labels']))
        finally:
            c.close()
    info = tk.Label(d, text='', fg='#123a63', anchor='w')
    info.pack(fill='x', padx=14)

    def sel():
        s = t.selection()
        return data.get(s[0]) if s else None

    def do(mode):
        j = sel()
        if not j:
            info.config(text='Önce listeden bir iş seçin.')
            return
        on_load(j['payload'], mode)
        d.destroy()

    def do_delete():
        j = sel()
        if not j:
            return
        c = con_factory()
        try:
            delete(c, j['id'])
            c.commit()
        finally:
            c.close()
        reload()
        info.config(text='“%s” silindi.' % j['name'])

    def do_rename():
        j = sel()
        if not j:
            return
        from tkinter import simpledialog
        nn = simpledialog.askstring('Yeniden adlandır', 'Yeni ad:', initialvalue=j['name'], parent=d)
        if not nn:
            return
        c = con_factory()
        try:
            ok = rename(c, j['id'], nn)
            c.commit()
        finally:
            c.close()
        reload()
        info.config(text='Yeniden adlandırıldı.' if ok else 'Bu ad başka bir işte kullanılıyor.')
    bar = tk.Frame(d)
    bar.pack(fill='x', padx=14, pady=(2, 12))
    tk.Button(bar, text='YÜKLE (listeyi değiştir)', command=lambda: do('replace'), bg='#0b6b2b', fg='white', font=('Segoe UI', 9, 'bold'), padx=10).pack(side='left')
    tk.Button(bar, text='LİSTEYE EKLE', command=lambda: do('append'), padx=10).pack(side='left', padx=6)
    tk.Button(bar, text='YENİDEN ADLANDIR', command=do_rename, padx=8).pack(side='left', padx=6)
    tk.Button(bar, text='SİL', command=do_delete, padx=10).pack(side='left')
    tk.Button(bar, text='KAPAT', command=d.destroy, padx=10).pack(side='right')
    t.bind('<Double-1>', lambda e=None: do('replace'))
    reload()
    d._jobs_tree = t
    d._do = do
    d._reload = reload
    return d
