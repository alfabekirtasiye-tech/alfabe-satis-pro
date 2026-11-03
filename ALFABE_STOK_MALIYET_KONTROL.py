# -*- coding: utf-8 -*-
"""ALFABE ortalama maliyet kontrolü / onarımı.

Ortalama maliyeti (avg_cost) sıfır ya da boş kalmış stokları bulur, alış geçmişinden bir maliyet ÖNERİSİ üretir
ve kullanıcı onayıyla toplu düzeltir.

Güvence:
  * Yalnız stock.avg_cost değişir; stok miktarı, hareketler, son alış ve fiyatlara dokunulmaz.
  * Önce liste ve öneri gösterilir; kullanıcı satırları seçer, isterse öneriyi elle değiştirir.
  * Her düzeltme stock_corrections + audit tablolarına eski/yeni değerle yazılır (Stok Sağlık > Düzeltme Geçmişi'nde görünür).
  * Düzeltme anında ortalama maliyet hâlâ sıfırsa uygulanır (başka işlem arada değiştirdiyse atlanır).
Öneri kaynağı (öncelik sırası): 1) kesinleşmiş alışların miktar ağırlıklı ortalaması, 2) maliyetli stok girişi hareketlerinin
ağırlıklı ortalaması, 3) son alış maliyeti. Kaynak bulunamazsa satır "Kaynak yok" olur ve elle değer girilmeden düzeltilmez.
"""
import datetime
import json

SOURCE_PURCHASE = 'Alış geçmişi (ağırlıklı ort.)'
SOURCE_MOVES = 'Stok giriş hareketleri (ağırlıklı ort.)'
SOURCE_LAST = 'Son alış maliyeti'
SOURCE_NONE = 'Kaynak yok'


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def count_missing(c):
    """Ana ekran uyarısı için hızlı sayım: ortalama maliyeti sıfır olup hareketi/stoğu/alışı olan aktif stok sayısı."""
    r = c.execute("""SELECT COUNT(*) FROM stock s WHERE s.active=1 AND COALESCE(s.avg_cost,0)<=0 AND
                     (ABS(COALESCE(s.qty,0))>0.000001 OR COALESCE(s.last_cost,0)>0 OR
                      EXISTS(SELECT 1 FROM stock_moves m WHERE m.stock_id=s.id AND m.qty>0))""").fetchone()
    return int(r[0] or 0)


def find_issues(c):
    """avg_cost<=0 olan ürünler ve öneriler. Satır: dict(sid,name,barcode,qty,avg,last,suggested,source,moves)."""
    purchase = {int(r[0]): (_f(r[1]), _f(r[2])) for r in c.execute(
        """SELECT pl.stock_id, SUM(pl.qty*pl.cost), SUM(pl.qty) FROM purchase_lines pl JOIN purchases p ON p.id=pl.purchase_id
           WHERE p.status='Kesinleşti' AND pl.qty>0 AND pl.cost>0 AND pl.stock_id IS NOT NULL GROUP BY pl.stock_id""").fetchall()}
    moves = {int(r[0]): (_f(r[1]), _f(r[2]), int(r[3] or 0)) for r in c.execute(
        """SELECT stock_id, SUM(CASE WHEN qty>0 AND unit_cost>0 THEN qty*unit_cost ELSE 0 END),
                  SUM(CASE WHEN qty>0 AND unit_cost>0 THEN qty ELSE 0 END), SUM(CASE WHEN qty>0 THEN 1 ELSE 0 END)
           FROM stock_moves WHERE stock_id IS NOT NULL GROUP BY stock_id""").fetchall()}
    out = []
    for s in c.execute("SELECT id,name,barcode,code,qty,avg_cost,last_cost FROM stock WHERE active=1 AND COALESCE(avg_cost,0)<=0 ORDER BY name").fetchall():
        sid = int(s[0])
        qty, last = _f(s[4]), _f(s[6])
        pv, pq = purchase.get(sid, (0.0, 0.0))
        mv, mq, mn = moves.get(sid, (0.0, 0.0, 0))
        if abs(qty) < 0.000001 and last <= 0 and mn == 0 and pq == 0:
            continue                                    # hiç hareketi/alışı olmayan yeni kart: sorun değil
        if pq > 0:
            sug, src = pv / pq, SOURCE_PURCHASE
        elif mq > 0:
            sug, src = mv / mq, SOURCE_MOVES
        elif last > 0:
            sug, src = last, SOURCE_LAST
        else:
            sug, src = 0.0, SOURCE_NONE
        out.append({'sid': sid, 'name': s[1] or '', 'barcode': s[2] or '', 'code': s[3] or '', 'qty': qty, 'avg': _f(s[5]), 'last': last,
                    'suggested': round(sug, 4), 'source': src, 'moves': mn})
    return out


def apply_fixes(c, items, reason, user=''):
    """items: [(stock_id, yeni_ortalama_maliyet)]. (uygulanan, atlanan[(sid, neden)]) döndürür. Tek işlemde yazılır."""
    reason = str(reason or '').strip()
    if len(reason) < 3:
        raise ValueError('Düzeltme gerekçesi en az 3 karakter olmalıdır.')
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    done, skipped = 0, []
    c.execute('BEGIN IMMEDIATE')
    try:
        for sid, val in items:
            val = round(_f(val), 4)
            if val <= 0:
                skipped.append((sid, 'Maliyet sıfır veya geçersiz'))
                continue
            cur = c.execute('SELECT name,avg_cost FROM stock WHERE id=?', (sid,)).fetchone()
            if cur is None:
                skipped.append((sid, 'Stok bulunamadı'))
                continue
            if _f(cur[1]) > 0:
                skipped.append((sid, 'Ortalama maliyet bu arada girilmiş (%.4f)' % _f(cur[1])))
                continue
            old, new = {'avg_cost': _f(cur[1])}, {'avg_cost': val}
            c.execute('UPDATE stock SET avg_cost=?,updated=? WHERE id=?', (val, now, sid))
            c.execute('INSERT INTO stock_corrections(stock_id,dt,issue,action,old_value,new_value,reason,user) VALUES(?,?,?,?,?,?,?,?)',
                      (sid, now, 'Maliyet Eksik', 'Toplu Ortalama Maliyet Onarımı', json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), reason, user))
            c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                      (now, user, 'Stok', 'Ortalama Maliyet Onarımı', sid, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), reason))
            done += 1
        c.commit()
    except Exception:
        c.rollback()
        raise
    return done, skipped


def open_cost_check(parent, connection_factory, user='', on_done=None, money=None):
    """Tk penceresi. Burada tkinter yalnız çağrı anında içe aktarılır (mantık kısmı arayüzsüz test edilebilsin diye)."""
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    money = money or (lambda v: '%.2f' % v)
    c = connection_factory()
    try:
        rows = find_issues(c)
    finally:
        c.close()
    w = tk.Toplevel(parent)
    w.title('Ortalama Maliyet Kontrolü')
    w.geometry('1250x700')
    w.minsize(980, 520)
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    tk.Label(w, text='ORTALAMA MALİYET KONTROLÜ', font=('Segoe UI', 17, 'bold'), fg='#0b376d').pack(anchor='w', padx=16, pady=(14, 2))
    tk.Label(w, text='Ortalama maliyeti sıfır olan ürünler ve alış geçmişinden bulunan öneri. Önerileri inceleyin, gerekirse çift tıklayıp değiştirin, '
                     'düzeltilecek satırları seçin. Yalnız ortalama maliyet değişir; miktar ve hareketlere dokunulmaz, her işlem geçmişe yazılır.',
             fg='#34516f', wraplength=1200, justify='left').pack(anchor='w', padx=16)
    vals = {r['sid']: r['suggested'] for r in rows}
    byid = {r['sid']: r for r in rows}
    cols = ('name', 'barcode', 'qty', 'last', 'sug', 'source')
    heads = {'name': ('Stok Adı', 330), 'barcode': ('Barkod', 130), 'qty': ('Mevcut', 70), 'last': ('Son Alış', 100), 'sug': ('Önerilen Ort. Maliyet', 140), 'source': ('Öneri Kaynağı', 280)}
    t = ttk.Treeview(w, columns=cols, show='headings', selectmode='extended')
    for k in cols:
        t.heading(k, text=heads[k][0])
        t.column(k, width=heads[k][1], anchor='e' if k in ('qty', 'last', 'sug') else 'w')
    vs = ttk.Scrollbar(w, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=vs.set)
    info = tk.Label(w, text='', fg='#0b376d', font=('Segoe UI', 10, 'bold'))
    info.pack(side='top', anchor='w', padx=16, pady=(8, 0))
    vs.pack(side='right', fill='y', padx=(0, 8), pady=8)
    t.pack(fill='both', expand=True, padx=(16, 0), pady=8)
    t.tag_configure('none', foreground='#9aa4b2')

    def fill():
        t.delete(*t.get_children())
        for r in rows:
            v = vals[r['sid']]
            t.insert('', 'end', iid=str(r['sid']), tags=(('none',) if v <= 0 else ()),
                     values=(r['name'], r['barcode'], '%g' % r['qty'], money(r['last']), money(v) if v > 0 else '—', r['source'] if v > 0 else SOURCE_NONE))
        info.config(text='Bulunan ürün: %d   •   Önerisi olan: %d   •   Kaynak yok (elle girilmeli): %d' % (len(rows), sum(1 for v in vals.values() if v > 0), sum(1 for v in vals.values() if v <= 0)))

    def edit_value(_e=None):
        s = t.selection()
        if not s:
            return
        sid = int(s[0])
        raw = simpledialog.askstring('Ortalama maliyet', byid[sid]['name'] + '\n\nOrtalama maliyet (KDV hariç):', initialvalue=('%.4f' % vals[sid]).replace('.', ',') if vals[sid] > 0 else '', parent=w)
        if raw is None:
            return
        try:
            v = float(raw.strip().replace('₺', '').replace(' ', '').replace('.', '').replace(',', '.')) if ',' in raw else float(raw.strip().replace(' ', ''))
        except ValueError:
            return messagebox.showwarning('ALFABE', 'Geçerli bir sayı girin.', parent=w)
        if v < 0:
            return messagebox.showwarning('ALFABE', 'Maliyet negatif olamaz.', parent=w)
        vals[sid] = round(v, 4)
        byid[sid]['source'] = 'Elle girildi'
        fill()
        t.selection_set(str(sid))

    def select_all_suggested():
        t.selection_set([str(r['sid']) for r in rows if vals[r['sid']] > 0])

    def apply():
        sel = [int(i) for i in t.selection() if vals.get(int(i), 0) > 0]
        if not sel:
            return messagebox.showwarning('ALFABE', 'Önerisi olan satırları seçin (TÜMÜNÜ SEÇ düğmesi önerisi olanları seçer).', parent=w)
        total = sum(vals[i] * max(byid[i]['qty'], 0) for i in sel)
        why = simpledialog.askstring('Gerekçe', '%d ürünün ortalama maliyeti güncellenecek.\nStok değerine etkisi yaklaşık +%s\n\nDüzeltme gerekçesi:' % (len(sel), money(total)),
                                     initialvalue='Ortalama maliyet sıfırdı; alış geçmişinden onarıldı', parent=w)
        if why is None:
            return
        c2 = connection_factory()
        try:
            done, skipped = apply_fixes(c2, [(i, vals[i]) for i in sel], why, user)
        except Exception as ex:
            return messagebox.showerror('ALFABE', 'Düzeltme yapılamadı (hiçbir kayıt değişmedi):\n' + str(ex), parent=w)
        finally:
            c2.close()
        msg = '%d ürünün ortalama maliyeti düzeltildi.' % done
        if skipped:
            msg += '\n\nAtlananlar (%d):\n' % len(skipped) + '\n'.join('%s: %s' % (byid.get(s, {}).get('name', s), why_) for s, why_ in skipped[:10])
        messagebox.showinfo('ALFABE', msg + '\n\nGeçmiş: Stok Sağlık > DÜZELTME GEÇMİŞİ', parent=w)
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        w.destroy()

    t.bind('<Double-1>', edit_value)
    bar = tk.Frame(w)
    bar.pack(side='bottom', fill='x', padx=16, pady=(0, 12))
    for text, cmd, primary in (('SEÇİLİLERİ DÜZELT', apply, True), ('TÜMÜNÜ SEÇ (ÖNERİSİ OLAN)', select_all_suggested, False), ('DEĞERİ DEĞİŞTİR (ÇİFT TIK)', edit_value, False)):
        tk.Button(bar, text=text, command=cmd, bg='#c8102e' if primary else '#ffffff', fg='#ffffff' if primary else '#111111', relief='solid', bd=1,
                  font=('Segoe UI', 10, 'bold'), padx=12, pady=7, cursor='hand2').pack(side='left', padx=(0, 6))
    tk.Button(bar, text='KAPAT', command=w.destroy, relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), padx=12, pady=7, cursor='hand2').pack(side='right')
    fill()
    if not rows:
        messagebox.showinfo('ALFABE', 'Ortalama maliyeti sıfır olan ürün bulunmadı.', parent=w)
