# -*- coding: utf-8 -*-
"""ALFABE Uyarı Motoru (v421): Akıllı Uyarı & İş Takip Merkezi, AI Asistan ("dikkat edilecekler") ve
testler AYNI hesabı kullanır. Yalnızca SELECT çalıştırır (kural tohumlama ayrı: ensure_rules).

collect(c, today=None, back_dir=None) -> [uyarı sözlükleri]
  uyarı: key, level (KRİTİK|ÖNEMLİ|NORMAL), category, title, detail, due (ISO ya da ''), due_note,
         module (git düğmesi için), heads/rows (hangi kayıtlar), count
"""
import datetime
import os

LEVELS = ('KRİTİK', 'ÖNEMLİ', 'NORMAL')

# kind: (ad, eşik, seviye)   — eşiği olmayanlarda eşik 0'dır ve değiştirilmesi etkisizdir.
RULES = {
    'a_neg_stock': ('Negatif stok', 0, 'KRİTİK'),
    'a_crit_stock': ('Kritik stok (min. altı)', 0, 'ÖNEMLİ'),
    'a_over_stock': ('Fazla stok (max. üstü)', 0, 'NORMAL'),
    'no_sale_days': ('Ölü stok: satmayan ürün (gün)', 90, 'ÖNEMLİ'),
    'a_no_price': ('Satış fiyatı eksik', 0, 'KRİTİK'),
    'a_no_cost': ('Maliyet eksik', 0, 'ÖNEMLİ'),
    'a_loss_risk': ('Zararına satış riski', 0, 'KRİTİK'),
    'profit_pct': ('Düşük kâr oranı (%)', 20, 'ÖNEMLİ'),
    'a_cost_up': ('Maliyet arttı / fiyat riski', 0, 'ÖNEMLİ'),
    'sales_drop_pct': ('Satış düşüşü / artışı (%)', 25, 'ÖNEMLİ'),
    'discount_pct': ('Yüksek iskonto (%)', 25, 'ÖNEMLİ'),
    'a_below_cost': ('Maliyet altında satış', 0, 'KRİTİK'),
    'high_sale_amount': ('Olağan dışı yüksek satış (TL)', 5000, 'ÖNEMLİ'),
    'high_item_qty': ('Aynı üründe yüksek adet (adet)', 20, 'ÖNEMLİ'),
    'return_count': ('İade yoğunluğu (işlem)', 3, 'ÖNEMLİ'),
    'a_cancel': ('Satış iptali', 0, 'KRİTİK'),
    'stockout_days': ('Stok bitişi: kalan gün', 7, 'KRİTİK'),
    'a_late_inst': ('Vadesi geçmiş taksit', 0, 'KRİTİK'),
    'a_risk': ('Cari risk limiti aşıldı', 0, 'KRİTİK'),
    'cari_no_pay_days': ('Ödeme yapmayan cari (gün)', 60, 'ÖNEMLİ'),
    'due_soon_days': ('Yaklaşan taksit / çek (gün)', 7, 'ÖNEMLİ'),
    'a_late_check': ('Vadesi geçmiş çek/senet', 0, 'KRİTİK'),
    'a_iban_pending': ('Bankada doğrulanmamış havale', 0, 'ÖNEMLİ'),
    'a_pos_late': ('POS yatışı gecikmesi', 0, 'ÖNEMLİ'),
    'order_wait_days': ('Geciken açık sipariş (gün)', 7, 'ÖNEMLİ'),
    'campaign_days': ('Kampanya bitişi (gün)', 3, 'NORMAL'),
    'a_no_barcode': ('Barkodsuz stok kartı', 0, 'NORMAL'),
    'a_price_changed': ('Bugün fiyatı değişen ürün', 0, 'NORMAL'),
    'backup_days': ('Yedek yaşı (gün)', 1, 'ÖNEMLİ'),
}
OLD_NAMES = {'profit_pct': 'Düşük kâr oranı', 'no_sale_days': 'Satmayan ürün', 'discount_pct': 'Yüksek iskonto', 'sales_drop_pct': 'Satış düşüşü',
             'high_sale_amount': 'Olağan dışı yüksek satış', 'high_item_qty': 'Aynı üründe yüksek adet', 'return_count': 'İade yoğunluğu'}

ST_SALE = "('FINAL','PARTIAL_RETURN')"
CHECK_CLOSED = "('Tahsil','İptal','Ödendi','Kapandı')"


def ensure_rules(c):
    """Eksik kural satırlarını ekler (eski satırların eşik/aktif/seviyesine dokunmaz)."""
    c.execute("CREATE TABLE IF NOT EXISTS alert_rules(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,threshold REAL DEFAULT 0,level TEXT DEFAULT 'ÖNEMLİ',active INTEGER DEFAULT 1)")
    have = {r[0] for r in c.execute('SELECT kind FROM alert_rules').fetchall()}
    for kind, (name, thr, lvl) in RULES.items():
        if kind not in have:
            c.execute('INSERT INTO alert_rules(name,kind,threshold,level,active) VALUES(?,?,?,?,1)', (name, kind, thr, lvl))
        elif kind in OLD_NAMES:
            c.execute('UPDATE alert_rules SET name=? WHERE kind=? AND name=?', (name, kind, OLD_NAMES[kind]))
    # eski sürümde 'Satmayan ürün' kuralı hiç kullanılmıyordu; eşik artık geçerlidir
    c.commit()


def load_rules(c):
    out = {k: [v[1], v[2], 1] for k, v in RULES.items()}
    try:
        for r in c.execute('SELECT kind,threshold,level,active FROM alert_rules ORDER BY id').fetchall():
            if r[0] in out:
                out[r[0]] = [float(r[1] or 0), (r[2] if r[2] in LEVELS else RULES[r[0]][2]), int(r[3] if r[3] is not None else 1)]
    except Exception:
        pass
    return out


def _money(v):
    s = '{:,.2f}'.format(float(v or 0)).replace(',', 'X').replace('.', ',').replace('X', '.')
    return s + ' ₺'


def _g(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    return ('%d' % v) if v == int(v) else ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')


def _date(s):
    s = str(s or '')[:10]
    return '%s.%s.%s' % (s[8:10], s[5:7], s[:4]) if len(s) == 10 and s[4] == '-' else s


def due_text(due, today=None):
    """'2026-10-12' -> '12.10.2026 (13 gün gecikti)'."""
    d = str(due or '')[:10]
    if not d:
        return ''
    try:
        x = (datetime.date.fromisoformat(d) - (today or datetime.date.today())).days
    except ValueError:
        return d
    if x < 0:
        return '%s (%d gün gecikti)' % (_date(d), -x)
    if x == 0:
        return '%s (bugün)' % _date(d)
    return '%s (%d gün sonra)' % (_date(d), x)


def collect(c, today=None, back_dir=None, now=None):
    today = today or datetime.date.today()
    t_iso = today.isoformat()
    now = now or datetime.datetime.now()
    rules = load_rules(c)

    def q(sql, p=()):
        try:
            return c.execute(sql, tuple(p)).fetchall()
        except Exception:
            return []

    def one(sql, p=(), d=0):
        r = q(sql, p)
        return (r[0][0] if r and r[0][0] is not None else d)

    def R(k):
        return rules[k][0]

    def on(k):
        return bool(rules[k][2])

    def plus(n):
        return (today + datetime.timedelta(days=int(n))).isoformat()

    def minus(n):
        return (today - datetime.timedelta(days=int(n))).isoformat()
    out = []

    def add(kind, cat, title, detail, module, due='', heads=None, rows=None, count=None, level=None, due_note=''):
        if not on(kind):
            return
        out.append(dict(key=kind, level=level or rules[kind][1], category=cat, title=title, detail=detail, due=due, due_note=due_note or (due_text(due, today) if due else ''),
                        module=module, heads=heads or [], rows=rows or [], count=count if count is not None else len(rows or [])))

    # ---------------- Stok
    stock_h = ['Kod', 'Ürün', 'Mevcut', 'Minimum']
    rs = q("SELECT code,name,qty,min_qty FROM stock WHERE active=1 AND qty<0 ORDER BY qty LIMIT 300")
    n = one("SELECT COUNT(*) FROM stock WHERE active=1 AND qty<0")
    if n:
        add('a_neg_stock', 'Stok', 'Negatif stok', f'{n} ürünün stok miktarı negatif.', 'Stok Merkezi', heads=stock_h, rows=[[r[0] or '', r[1], _g(r[2]), _g(r[3])] for r in rs], count=n)
    rs = q("SELECT code,name,qty,min_qty FROM stock WHERE active=1 AND min_qty>0 AND qty>=0 AND qty<=min_qty ORDER BY qty-min_qty LIMIT 300")
    n = one("SELECT COUNT(*) FROM stock WHERE active=1 AND min_qty>0 AND qty>=0 AND qty<=min_qty")
    if n:
        add('a_crit_stock', 'Stok', 'Kritik stok', f'{n} ürün minimum seviyede veya altında.', 'Stok Merkezi', heads=stock_h, rows=[[r[0] or '', r[1], _g(r[2]), _g(r[3])] for r in rs], count=n)
    rs = q("SELECT code,name,qty,max_qty FROM stock WHERE active=1 AND max_qty>0 AND qty>max_qty ORDER BY qty-max_qty DESC LIMIT 300")
    n = one("SELECT COUNT(*) FROM stock WHERE active=1 AND max_qty>0 AND qty>max_qty")
    if n:
        add('a_over_stock', 'Stok', 'Fazla stok', f'{n} ürün tanımlı maksimum stok seviyesinin üzerinde.', 'Stok Merkezi', heads=['Kod', 'Ürün', 'Mevcut', 'Maksimum'], rows=[[r[0] or '', r[1], _g(r[2]), _g(r[3])] for r in rs], count=n)
    nd = int(R('no_sale_days'))
    dead_where = ("active=1 AND qty>0 AND id NOT IN(SELECT DISTINCT sl.stock_id FROM sale_lines sl JOIN sales sa ON sa.id=sl.sale_id "
                  "WHERE sl.stock_id IS NOT NULL AND substr(sa.dt,1,10)>=?)")
    cap = one("SELECT COALESCE(SUM(qty*avg_cost),0) FROM stock WHERE " + dead_where, (minus(nd),))
    if cap:
        rs = q("SELECT code,name,qty,qty*avg_cost FROM stock WHERE " + dead_where + " ORDER BY qty*avg_cost DESC LIMIT 300", (minus(nd),))
        add('no_sale_days', 'Stok', 'Ölü stok / bağlı sermaye', f'{nd} gündür satmayan stoklarda yaklaşık {_money(cap)} bağlı sermaye var.', 'Stok Merkezi',
            heads=['Kod', 'Ürün', 'Mevcut', 'Bağlı sermaye'], rows=[[r[0] or '', r[1], _g(r[2]), _money(r[3])] for r in rs],
            count=int(one("SELECT COUNT(*) FROM stock WHERE " + dead_where, (minus(nd),))))
    # ---------------- Fiyat / Kâr
    def simple(kind, cat, title, where, text, module='Stok Merkezi', params=(), extra_h=None):
        n = one("SELECT COUNT(*) FROM stock WHERE active=1 AND (" + where + ")", params)
        if n:
            rs = q("SELECT code,name,retail,avg_cost FROM stock WHERE active=1 AND (" + where + ") ORDER BY name LIMIT 300", params)
            add(kind, cat, title, text.format(n=n), module, heads=['Kod', 'Ürün', 'Satış fiyatı', 'Ort. maliyet'], rows=[[r[0] or '', r[1], _money(r[2]), _money(r[3])] for r in rs], count=n)
    simple('a_no_price', 'Fiyat/Kâr', 'Satış fiyatı eksik', 'retail<=0', '{n} aktif üründe perakende fiyatı yok.', 'Fiyat Yönetimi')
    simple('a_no_cost', 'Fiyat/Kâr', 'Maliyet eksik', 'avg_cost<=0', '{n} üründe ortalama maliyet yok.', 'Fiyat Yönetimi')
    simple('a_loss_risk', 'Fiyat/Kâr', 'Zararına satış riski', 'avg_cost>0 AND retail<avg_cost', '{n} ürünün satış fiyatı ortalama maliyetin altında.', 'Fiyat Yönetimi')
    pct = R('profit_pct')
    simple('profit_pct', 'Fiyat/Kâr', 'Düşük kâr oranı', 'retail>0 AND avg_cost>0 AND ((retail-avg_cost)*100.0/retail)<?',
           '{n} ürünün brüt kâr oranı %' + ('%g' % pct) + ' eşiğinin altında.', 'Fiyat Yönetimi', (pct,))
    n = one("SELECT COUNT(DISTINCT ph.stock_id) FROM price_history ph JOIN stock st ON st.id=ph.stock_id WHERE ph.price_type IN ('Alış','Maliyet','Son Alış') AND ph.new_price>ph.old_price AND st.retail<=ph.new_price")
    if n:
        rs = q("SELECT st.code,st.name,st.retail,MAX(ph.new_price) FROM price_history ph JOIN stock st ON st.id=ph.stock_id WHERE ph.price_type IN ('Alış','Maliyet','Son Alış') AND ph.new_price>ph.old_price AND st.retail<=ph.new_price GROUP BY st.id ORDER BY st.name LIMIT 300")
        add('a_cost_up', 'Fiyat/Kâr', 'Maliyet arttı / fiyat riski', f'{n} üründe maliyet artışı sonrası satış fiyatı kontrol edilmeli.', 'Fiyat Yönetimi',
            heads=['Kod', 'Ürün', 'Satış fiyatı', 'Yeni maliyet'], rows=[[r[0] or '', r[1], _money(r[2]), _money(r[3])] for r in rs], count=n)
    # ---------------- Satış (bugünkü)
    cut = now.strftime('%H:%M:%S')
    yd = (today - datetime.timedelta(days=1)).isoformat()
    s_today = float(one(f"SELECT COALESCE(SUM(total),0) FROM sales WHERE status IN {ST_SALE} AND substr(dt,1,10)=? AND substr(dt,12,8)<=?", (t_iso, cut)))
    s_yday = float(one(f"SELECT COALESCE(SUM(total),0) FROM sales WHERE status IN {ST_SALE} AND substr(dt,1,10)=? AND substr(dt,12,8)<=?", (yd, cut)))
    drop = R('sales_drop_pct')
    if s_yday > 0:
        diff = (s_today - s_yday) * 100 / s_yday
        info = [['Bugün (bu saate kadar)', _money(s_today)], ['Dün (aynı saate kadar)', _money(s_yday)]]
        if diff <= -drop:
            add('sales_drop_pct', 'Satış', 'Satış düşüşü', f'Bugün bu saate kadar satış dün aynı saate göre %{abs(diff):.1f} düşük.', 'Rapor & Analiz', heads=['Dönem', 'Satış'], rows=info)
        elif diff >= drop:
            add('sales_drop_pct', 'Satış', 'Satış artışı', f'Bugün bu saate kadar satış dün aynı saate göre %{diff:.1f} yüksek.', 'Rapor & Analiz', heads=['Dönem', 'Satış'], rows=info, level='NORMAL')
    disc = R('discount_pct')
    n = one(f"SELECT COUNT(*) FROM sales WHERE status IN {ST_SALE} AND substr(dt,1,10)=? AND gross>0 AND discount*100.0/gross>=?", (t_iso, disc))
    if n:
        add('discount_pct', 'Satış', 'Yüksek iskonto', f'{n} satışta iskonto oranı %{disc:g} veya üzerinde.', 'Rapor & Analiz', count=n)
    n = one(f"""SELECT COUNT(DISTINCT s.id) FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id WHERE s.status IN {ST_SALE} AND substr(s.dt,1,10)=? AND sl.qty>0 AND sl.total<(sl.qty*CASE WHEN COALESCE(sl.cost_snapshot,0)>0 THEN sl.cost_snapshot ELSE COALESCE((SELECT avg_cost FROM stock WHERE id=sl.stock_id),0) END)""", (t_iso,))
    if n:
        add('a_below_cost', 'Satış', 'Maliyet altında satış', f'{n} satışta en az bir ürün maliyetinin altında satılmış.', 'Rapor & Analiz', count=n)
    hi = R('high_sale_amount')
    n = one(f"SELECT COUNT(*) FROM sales WHERE status IN {ST_SALE} AND substr(dt,1,10)=? AND total>=?", (t_iso, hi))
    if n:
        add('high_sale_amount', 'Satış', 'Olağan dışı yüksek satış', f'{n} satış {_money(hi)} eşiğinde veya üzerinde.', 'Rapor & Analiz', count=n)
    hq = R('high_item_qty')
    n = one(f"SELECT COUNT(*) FROM (SELECT sl.stock_id FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status IN {ST_SALE} AND substr(s.dt,1,10)=? AND sl.stock_id IS NOT NULL GROUP BY sl.stock_id HAVING SUM(sl.qty)>=?)", (t_iso, hq))
    if n:
        add('high_item_qty', 'Satış', 'Aynı üründe olağan dışı satış', f'{n} ürün bugün toplam {hq:g} adet veya daha fazla satılmış.', 'Rapor & Analiz', count=n)
    rl = R('return_count')
    n = one("SELECT COUNT(*) FROM sales WHERE status='RETURN' AND substr(dt,1,10)=?", (t_iso,))
    if n and n >= rl:
        add('return_count', 'Satış', 'İade yoğunluğu', f'Bugün {n} iade işlemi var; uyarı eşiği {rl:g}.', 'Rapor & Analiz', count=n)
    n = one("SELECT COUNT(*) FROM sales WHERE status='CANCEL' AND substr(dt,1,10)=?", (t_iso,))
    if n:
        rs = q("SELECT doc_no,substr(dt,1,16),total,COALESCE(user,'') FROM sales WHERE status='CANCEL' AND substr(dt,1,10)=? ORDER BY dt DESC LIMIT 100", (t_iso,))
        add('a_cancel', 'Satış', 'Satış iptali yoğunluğu', f'Bugün {n} satış iptal edilmiş.', 'Rapor & Analiz', heads=['Belge', 'Tarih', 'Tutar', 'Kullanıcı'],
            rows=[[r[0], r[1], _money(r[2]), r[3]] for r in rs], count=n)
    # ---------------- Stok bitişi (son 7 günlük hız)
    sd = max(1, int(R('stockout_days')))
    fast = []
    for r in q("""SELECT st.code,st.name,st.qty,COALESCE(SUM(sl.qty),0) q7 FROM stock st JOIN sale_lines sl ON sl.stock_id=st.id JOIN sales sa ON sa.id=sl.sale_id
                  WHERE st.active=1 AND st.qty>0 AND sa.status IN """ + ST_SALE + """ AND substr(sa.dt,1,10)>=? GROUP BY st.id HAVING q7>0""", (minus(7),)):
        days = float(r[2]) / (float(r[3]) / 7.0)
        if days <= sd:
            fast.append((days, r))
    if fast:
        fast.sort(key=lambda x: x[0])
        add('stockout_days', 'Stok', 'Stok bitişi yaklaşıyor', f'{len(fast)} ürün son 7 günlük satış hızına göre yaklaşık {sd} gün içinde bitebilir.', 'Stok Merkezi',
            heads=['Kod', 'Ürün', 'Mevcut', 'Son 7 gün satış', 'Kalan gün'], rows=[[r[0] or '', r[1], _g(r[2]), _g(r[3]), '%.1f' % d_] for d_, r in fast[:300]], count=len(fast))
    # ---------------- Cari / Finans vadeleri
    soon = int(R('due_soon_days'))
    inst_open = "COALESCE(i.status,'')!='Ödendi' AND i.amount-i.paid>0.0001 AND COALESCE(i.due_date,'')<>''"
    rs = q(f"SELECT i.due_date,COALESCE(c.title,''),i.amount-i.paid FROM installments i LEFT JOIN caris c ON c.id=i.cari_id WHERE {inst_open} AND substr(i.due_date,1,10)<? ORDER BY i.due_date", (t_iso,))
    if rs:
        add('a_late_inst', 'Cari', 'Vadesi geçmiş taksit', f'{len(rs)} açık taksidin vadesi geçmiş (toplam {_money(sum(float(r[2]) for r in rs))}); en eski vade {_date(rs[0][0])}.', 'Cari Merkezi',
            due=str(rs[0][0])[:10], heads=['Vade', 'Cari', 'Kalan tutar', 'Gecikme'], rows=[[_date(r[0]), r[1], _money(r[2]), due_text(r[0], today)] for r in rs[:300]], count=len(rs))
    rs = q(f"SELECT i.due_date,COALESCE(c.title,''),i.amount-i.paid FROM installments i LEFT JOIN caris c ON c.id=i.cari_id WHERE {inst_open} AND substr(i.due_date,1,10) BETWEEN ? AND ? ORDER BY i.due_date", (t_iso, plus(soon)))
    if rs:
        add('due_soon_days', 'Finans', 'Yaklaşan taksit', f'{len(rs)} taksit {soon} gün içinde vadeli (toplam {_money(sum(float(r[2]) for r in rs))}).', 'Cari Merkezi',
            due=str(rs[0][0])[:10], heads=['Vade', 'Cari', 'Kalan tutar', 'Kalan süre'], rows=[[_date(r[0]), r[1], _money(r[2]), due_text(r[0], today)] for r in rs[:300]], count=len(rs))
    rs = q("SELECT c.title,SUM(m.debit-m.credit),c.risk FROM caris c JOIN cari_moves m ON m.cari_id=c.id WHERE c.active=1 AND c.risk>0 GROUP BY c.id HAVING SUM(m.debit-m.credit)>c.risk ORDER BY SUM(m.debit-m.credit)-c.risk DESC")
    if rs:
        add('a_risk', 'Cari', 'Risk limiti aşıldı', f'{len(rs)} cari risk limitini aşmış.', 'Cari Merkezi', heads=['Cari', 'Bakiye', 'Risk limiti', 'Aşım'],
            rows=[[r[0], _money(r[1]), _money(r[2]), _money(float(r[1]) - float(r[2]))] for r in rs[:300]], count=len(rs))
    nop = int(R('cari_no_pay_days'))
    rs = q("""SELECT c.title,SUM(m.debit-m.credit) bal,MAX(CASE WHEN m.credit>0 THEN substr(m.dt,1,10) END) lastpay FROM caris c JOIN cari_moves m ON m.cari_id=c.id
              WHERE c.active=1 GROUP BY c.id HAVING bal>0.005 AND (lastpay IS NULL OR lastpay<?) ORDER BY bal DESC""", (minus(nop),))
    if rs:
        add('cari_no_pay_days', 'Cari', 'Uzun süredir ödeme yapmayan cari', f'{len(rs)} borçlu cari {nop} günden uzun süredir ödeme yapmamış (toplam {_money(sum(float(r[1]) for r in rs))}).', 'Cari Merkezi',
            heads=['Cari', 'Bakiye (borç)', 'Son ödeme'], rows=[[r[0], _money(r[1]), _date(r[2]) if r[2] else 'hiç ödeme yok'] for r in rs[:300]], count=len(rs))
    chk = f"status NOT IN {CHECK_CLOSED} AND COALESCE(due_date,'')<>''"
    rs = q(f"SELECT kind,number,COALESCE(customer,''),due_date,amount FROM checks_notes WHERE {chk} AND substr(due_date,1,10)<? ORDER BY due_date", (t_iso,))
    if rs:
        add('a_late_check', 'Finans', 'Vadesi geçmiş çek/senet', f'{len(rs)} çek/senet kaydının vadesi geçmiş (toplam {_money(sum(float(r[4]) for r in rs))}); en eski vade {_date(rs[0][3])}.', 'Finans Merkezi',
            due=str(rs[0][3])[:10], heads=['Vade', 'Tür / No', 'Cari', 'Tutar', 'Gecikme'], rows=[[_date(r[3]), ('%s %s' % (r[0] or '', r[1] or '')).strip(), r[2], _money(r[4]), due_text(r[3], today)] for r in rs[:300]], count=len(rs))
    rs = q(f"SELECT kind,number,COALESCE(customer,''),due_date,amount FROM checks_notes WHERE {chk} AND substr(due_date,1,10) BETWEEN ? AND ? ORDER BY due_date", (t_iso, plus(soon)))
    if rs:
        add('due_soon_days', 'Finans', 'Yaklaşan çek/senet', f'{len(rs)} çek/senet {soon} gün içinde vadeli (toplam {_money(sum(float(r[4]) for r in rs))}).', 'Finans Merkezi',
            due=str(rs[0][3])[:10], heads=['Vade', 'Tür / No', 'Cari', 'Tutar', 'Kalan süre'], rows=[[_date(r[3]), ('%s %s' % (r[0] or '', r[1] or '')).strip(), r[2], _money(r[4]), due_text(r[3], today)] for r in rs[:300]], count=len(rs))
    # ---------------- Banka: bekleyen havale / POS
    rs = q(f"""SELECT s.doc_no,substr(s.dt,1,10),COALESCE(ca.title,'Perakende'),p.amount,COALESCE(p.bank_account,'') FROM sale_payments p JOIN sales s ON s.id=p.sale_id LEFT JOIN caris ca ON ca.id=s.customer_id
               WHERE p.kind='Havale/IBAN' AND p.amount>0 AND COALESCE(p.verified,0)=0 AND COALESCE(s.status,'FINAL')<>'CANCEL' ORDER BY s.dt""")
    if rs:
        add('a_iban_pending', 'Finans', 'Bankada doğrulanmamış havale', f'{len(rs)} havale henüz bankada doğrulanmadı (toplam {_money(sum(float(r[3]) for r in rs))}); en eskisi {_date(rs[0][1])}.', 'Banka Merkezi',
            due=str(rs[0][1])[:10], heads=['Belge', 'Tarih', 'Müşteri', 'Tutar', 'Banka hesabı'], rows=[[r[0], _date(r[1]), r[2], _money(r[3]), r[4]] for r in rs[:300]], count=len(rs))
    rs = q("SELECT dt,pos_name,net,due_date FROM pos_settlements WHERE status='Bekliyor' AND COALESCE(due_date,'')<>'' AND substr(due_date,1,10)<=? ORDER BY due_date", (t_iso,))
    if rs:
        add('a_pos_late', 'Finans', 'POS ödemesi gecikmesi', f'{len(rs)} günlük kart satışının bankaya geçmiş olması gerekiyor (toplam {_money(sum(float(r[2]) for r in rs))}).', 'Banka Merkezi',
            due=str(rs[0][3])[:10], heads=['Satış günü', 'POS', 'Net', 'Beklenen yatış'], rows=[[_date(r[0]), r[1] or '', _money(r[2]), due_text(r[3], today)] for r in rs[:300]], count=len(rs))
    # ---------------- Sipariş / kampanya / sistem
    ow = int(R('order_wait_days'))
    rs = q("SELECT doc_no,substr(dt,1,10),COALESCE(party,''),total FROM orders WHERE status='Açık' AND substr(dt,1,10)<? ORDER BY dt", (minus(ow),))
    if rs:
        add('order_wait_days', 'Sipariş', 'Geciken açık sipariş', f'{len(rs)} açık sipariş {ow} günden uzun süredir bekliyor; en eskisi {_date(rs[0][1])}.', 'Sipariş & Teklif',
            due=str(rs[0][1])[:10], due_note='sipariş tarihi ' + _date(rs[0][1]), heads=['Belge', 'Tarih', 'Taraf', 'Tutar', 'Bekleme'],
            rows=[[r[0], _date(r[1]), r[2], _money(r[3]), '%d gün' % (today - datetime.date.fromisoformat(str(r[1])[:10])).days] for r in rs[:300]], count=len(rs))
    cd = int(R('campaign_days'))
    rs = q("SELECT name,start_date,end_date FROM campaigns WHERE active=1 AND substr(end_date,1,10) BETWEEN ? AND ? ORDER BY end_date", (t_iso, plus(cd)))
    if rs:
        add('campaign_days', 'Kampanya', 'Kampanya bitişi yaklaşıyor', f'{len(rs)} kampanya {cd} gün içinde bitecek.', 'Kampanya Merkezi',
            due=str(rs[0][2])[:10], heads=['Kampanya', 'Başlangıç', 'Bitiş', 'Kalan süre'], rows=[[r[0], _date(r[1]), _date(r[2]), due_text(r[2], today)] for r in rs], count=len(rs))
    n = one("SELECT COUNT(*) FROM stock WHERE active=1 AND COALESCE(barcode,'')=''")
    if n:
        rs = q("SELECT code,name FROM stock WHERE active=1 AND COALESCE(barcode,'')='' ORDER BY name LIMIT 300")
        add('a_no_barcode', 'Sistem', 'Barkodsuz stok kartı', f'{n} aktif stok kartında barkod yok.', 'Etiket & Barkod', heads=['Kod', 'Ürün'], rows=[[r[0] or '', r[1]] for r in rs], count=n)
    n = one("SELECT COUNT(DISTINCT stock_id) FROM price_history WHERE substr(dt,1,10)=?", (t_iso,))
    if n:
        rs = q("SELECT st.code,st.name,ph.price_type,ph.old_price,ph.new_price FROM price_history ph JOIN stock st ON st.id=ph.stock_id WHERE substr(ph.dt,1,10)=? ORDER BY ph.id DESC LIMIT 300", (t_iso,))
        add('a_price_changed', 'Etiket', 'Bugün fiyatı değişen ürün', f'{n} ürünün fiyatı bugün değişti; raf etiketi kontrol edilmeli.', 'Etiket & Barkod',
            heads=['Kod', 'Ürün', 'Fiyat türü', 'Eski', 'Yeni'], rows=[[r[0] or '', r[1], r[2], _money(r[3]), _money(r[4])] for r in rs], count=n)
    if back_dir is not None and on('backup_days'):
        try:
            files = [os.path.join(str(back_dir), f) for f in os.listdir(str(back_dir))]
            latest = max((os.path.getmtime(f) for f in files if os.path.isfile(f)), default=0)
        except Exception:
            latest = 0
        age = (now.timestamp() - latest) / 86400 if latest else 999
        lim = R('backup_days')
        if age > lim:
            txt = 'Hiç yedek bulunamadı. Ayarlar > Yedekleme bölümünden yedek alın.' if not latest else 'Son yedek %s (%d gün önce); eşik %g gün.' % (datetime.datetime.fromtimestamp(latest).strftime('%d.%m.%Y %H:%M'), int(age), lim)
            add('backup_days', 'Sistem', 'Yedek kontrolü', txt, 'Ayarlar', level=('KRİTİK' if age > 3 else rules['backup_days'][1]), heads=['Bilgi'], rows=[[txt]], count=1)
    order = {'KRİTİK': 0, 'ÖNEMLİ': 1, 'NORMAL': 2}
    out.sort(key=lambda a: (order.get(a['level'], 3)))
    return out
