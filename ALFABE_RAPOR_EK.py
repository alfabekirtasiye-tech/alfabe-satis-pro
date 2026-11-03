# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Ek raporlar (v1.2.291.445).

Türkiye'de yaygın ticari / perakende programlarının (Wolvox, Mikro, Logo, Netsis, ETA, Paraşüt, Zirve, Vega, Tamsoft vb.) rapor menülerinde
bulunup programda eksik olan raporlar. Hepsi YALNIZ OKUR (SELECT); hiçbir kayıt değiştirilmez.

Kullanım: build(kind, a, b, DX) -> (başlıklar, satırlar) veya None (bu modülün raporu değilse).
  a, b : 'YYYY-AA-GG' başlangıç / bitiş (dahil).
Sözleşmeler (program genelindeki kayıt biçimi):
  * stock_moves.qty işaretlidir (satış -, alış +, iade işaretli).
  * cari_moves: borç (debit) = müşteri bize borçlanır, alacak (credit) = tahsilat / tedarikçi alacağı.
  * sale_lines iade satırlarında qty / total / vat_amount negatiftir; net satış = FINAL + RETURN toplamı.
  * finance.amount işaretlidir (giriş +, çıkış -).
"""
import datetime

NEW_REPORTS = [
    # (rapor adı, hangi mevcut rapordan SONRA eklenecek)
    ('Cari Hesap Ekstresi (Hareket Dökümü)', 'Cari Bakiyeler'),
    ('Cari Yaşlandırma (Alacak - Müşteri)', 'Cari Hesap Ekstresi (Hareket Dökümü)'),
    ('Cari Yaşlandırma (Borç - Tedarikçi)', 'Cari Yaşlandırma (Alacak - Müşteri)'),
    ('Cari Bazında Satış ve Kâr', 'Cari Yaşlandırma (Borç - Tedarikçi)'),
    ('Stok Hareket Dökümü', 'Stok Listesi'),
    ('Stok Hareket Özeti (Giriş / Çıkış / Bakiye)', 'Stok Hareket Dökümü'),
    ('Stok Değer Raporu (Maliyet / Satış / Potansiyel Kâr)', 'Stok Hareket Özeti (Giriş / Çıkış / Bakiye)'),
    ('Aylık Satış Özeti', 'Günlük Satış & Kâr'),
    ('Satış Fişi Bazında Kâr', 'Aylık Satış Özeti'),
    ('Hesaplanan KDV Özeti (İadeler Düşülmüş)', 'KDV Oranına Göre Satış'),
    ('Alış İadeleri', 'Alışlar'),
    ('Kasa / Banka Özeti', 'Finans Hareketleri'),
    ('Gelir - Gider Dağılımı', 'Kasa / Banka Özeti'),
]
NAMES = [n for n, _ in NEW_REPORTS]

_COST = ("(sl.qty*CASE WHEN COALESCE(sl.cost_snapshot,0)>0 THEN sl.cost_snapshot "
         "ELSE COALESCE(NULLIF(st.avg_cost,0),st.last_cost,0) END)")


def merged_list(existing):
    """Mevcut rapor listesine yeni raporları ilgili raporların yanına ekler (tekrar etmez)."""
    out = list(existing)
    for name, after in NEW_REPORTS:
        if name in out:
            continue
        if after in out:
            out.insert(out.index(after) + 1, name)
        else:
            out.append(name)
    return out


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v):
    return round(_f(v), 2)


def _d10(s):
    return str(s or '')[:10]


def _today():
    return datetime.date.today()


def _age_days(dt_text, today):
    try:
        return max(0, (today - datetime.date.fromisoformat(_d10(dt_text))).days)
    except ValueError:
        return 0


# ----------------------------------------------------------------------------- Cari
def cari_ekstre(DX, a, b):
    heads = ['Cari', 'Tarih', 'İşlem', 'Açıklama', 'Borç Tutarı', 'Alacak Tutarı', 'Bakiye']
    caris = DX.rows("SELECT id,title FROM caris WHERE active=1 ORDER BY title")
    rows = []
    for c in caris:
        cid = c['id']
        dev = DX.one("SELECT COALESCE(SUM(debit-credit),0) b FROM cari_moves WHERE cari_id=? AND substr(dt,1,10)<?", (cid, a))
        bal = _f(dev['b'] if dev else 0)
        mv = DX.rows("SELECT dt,kind,debit,credit,note FROM cari_moves WHERE cari_id=? AND substr(dt,1,10) BETWEEN ? AND ? ORDER BY dt,id", (cid, a, b))
        if not mv and abs(bal) < 0.005:
            continue
        rows.append((c['title'], a, 'Devir', 'Önceki dönemden devreden bakiye', 0.0, 0.0, _r2(bal)))
        for m in mv:
            bal += _f(m['debit']) - _f(m['credit'])
            rows.append((c['title'], _d10(m['dt']), m['kind'] or '', m['note'] or '', _r2(m['debit']), _r2(m['credit']), _r2(bal)))
    return heads, rows


def _aging(DX, receivable):
    """FIFO yaşlandırma. receivable=True: müşteri (borç satırları, tahsilat düşer); False: tedarikçi (alacak satırları, ödeme düşer)."""
    today = _today()
    heads = ['Cari', 'Güncel Bakiye', 'Bakiye 0-30 Gün', 'Bakiye 31-60 Gün', 'Bakiye 61-90 Gün', 'Bakiye 91-180 Gün', 'Bakiye 181+ Gün', 'En Eski Belge Tarihi']
    rows = []
    for c in DX.rows("SELECT id,title FROM caris WHERE active=1 ORDER BY title"):
        open_d, open_c = [], []      # açık borç (alacağımız) ve açık alacak (borcumuz) kalemleri: [tarih, kalan]
        for m in DX.rows("SELECT dt,debit,credit FROM cari_moves WHERE cari_id=? ORDER BY dt,id", (c['id'],)):
            for amt, mine, other in ((_f(m['debit']), open_d, open_c), (_f(m['credit']), open_c, open_d)):
                left = amt
                while left > 1e-9 and other:          # karşı taraftaki en eski açık kalemi önce kapat
                    take = min(left, other[0][1])
                    other[0][1] -= take
                    left -= take
                    if other[0][1] <= 1e-9:
                        other.pop(0)
                if left > 1e-9:
                    mine.append([_d10(m['dt']), left])
        open_items = open_d if receivable else open_c
        total = sum(x[1] for x in open_items)
        if total < 0.005:
            continue
        bk = [0.0] * 5
        for dt, amt in open_items:
            d = _age_days(dt, today)
            i = 0 if d <= 30 else 1 if d <= 60 else 2 if d <= 90 else 3 if d <= 180 else 4
            bk[i] += amt
        rows.append((c['title'], _r2(total), _r2(bk[0]), _r2(bk[1]), _r2(bk[2]), _r2(bk[3]), _r2(bk[4]), open_items[0][0]))
    rows.sort(key=lambda r: -r[1])
    return heads, rows


def cari_satis_kar(DX, a, b):
    heads = ['Cari', 'Fiş Sayısı', 'Net Satış Tutarı', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Kâr %', 'İade Tutarı']
    q = f"""SELECT COALESCE(c.title,'(Cari seçilmemiş - perakende)') t,
        COUNT(DISTINCT CASE WHEN s.status='FINAL' THEN s.id END) n,
        SUM(sl.total) net, SUM({_COST}) cost,
        SUM(CASE WHEN s.status='RETURN' THEN -sl.total ELSE 0 END) iade
        FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id
        LEFT JOIN stock st ON st.id=sl.stock_id LEFT JOIN caris c ON c.id=s.customer_id
        WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ?
        GROUP BY s.customer_id ORDER BY net DESC"""
    rows = []
    for r in DX.rows(q, (a, b)):
        net, cost = _f(r['net']), _f(r['cost'])
        kar = net - cost
        rows.append((r['t'], int(r['n'] or 0), _r2(net), _r2(cost), _r2(kar), _r2(kar * 100.0 / net) if abs(net) > 0.005 else 0.0, _r2(r['iade'])))
    return heads, rows


# ----------------------------------------------------------------------------- Stok
def stok_hareket_dokumu(DX, a, b):
    heads = ['Tarih', 'Barkod', 'Stok Kodu', 'Ürün', 'İşlem', 'Miktar', 'Birim Maliyet', 'Kaynak', 'Açıklama']
    rows = [(m['dt'], m['barcode'] or '', m['code'] or '', m['name'] or '', m['kind'] or '', _f(m['qty']), _r2(m['unit_cost']), m['source'] or '', m['note'] or '')
            for m in DX.rows("""SELECT m.dt,st.barcode,st.code,st.name,m.kind,m.qty,m.unit_cost,m.source,m.note
                FROM stock_moves m LEFT JOIN stock st ON st.id=m.stock_id
                WHERE substr(m.dt,1,10) BETWEEN ? AND ? ORDER BY m.dt DESC,m.id DESC""", (a, b))]
    return heads, rows


def stok_hareket_ozeti(DX, a, b):
    heads = ['Barkod', 'Stok Kodu', 'Ürün', 'Açılış', 'Giriş', 'Çıkış', 'Kapanış', 'Kart Stok']
    q = """SELECT st.barcode,st.code,st.name,st.qty,
        COALESCE(SUM(CASE WHEN substr(m.dt,1,10)<? THEN m.qty END),0) acilis,
        COALESCE(SUM(CASE WHEN substr(m.dt,1,10) BETWEEN ? AND ? AND m.qty>0 THEN m.qty END),0) giris,
        COALESCE(SUM(CASE WHEN substr(m.dt,1,10) BETWEEN ? AND ? AND m.qty<0 THEN -m.qty END),0) cikis
        FROM stock st LEFT JOIN stock_moves m ON m.stock_id=st.id
        WHERE st.active=1 GROUP BY st.id
        HAVING ABS(acilis)>0.0001 OR giris>0.0001 OR cikis>0.0001
        ORDER BY st.name"""
    rows = []
    for r in DX.rows(q, (a, a, b, a, b)):
        ac, gi, ci = _f(r['acilis']), _f(r['giris']), _f(r['cikis'])
        rows.append((r['barcode'] or '', r['code'] or '', r['name'] or '', round(ac, 3), round(gi, 3), round(ci, 3), round(ac + gi - ci, 3), round(_f(r['qty']), 3)))
    return heads, rows


def stok_deger(DX, a=None, b=None):
    heads = ['Barkod', 'Stok Kodu', 'Ürün', 'Stok', 'Ort. Maliyet', 'Maliyet Değeri', 'Satış Fiyatı', 'Satış Değeri', 'Potansiyel Kâr', 'Kâr %']
    rows = []
    for r in DX.rows("SELECT barcode,code,name,qty,avg_cost,last_cost,retail FROM stock WHERE active=1 AND qty>0 ORDER BY name"):
        qty = _f(r['qty'])
        cost = _f(r['avg_cost']) or _f(r['last_cost'])
        sell = _f(r['retail'])
        cv, sv = qty * cost, qty * sell
        rows.append((r['barcode'] or '', r['code'] or '', r['name'] or '', round(qty, 3), _r2(cost), _r2(cv), _r2(sell), _r2(sv), _r2(sv - cv), _r2((sv - cv) * 100.0 / sv) if sv > 0.005 else 0.0))
    return heads, rows


# ----------------------------------------------------------------------------- Satış
def aylik_satis(DX, a, b):
    heads = ['Ay', 'Fiş Sayısı', 'Satış Tutarı', 'İade Tutarı', 'Net Satış Tutarı', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Kâr %']
    q = f"""SELECT substr(s.dt,1,7) ay,
        COUNT(DISTINCT CASE WHEN s.status='FINAL' THEN s.id END) n,
        SUM(CASE WHEN s.status='FINAL' THEN sl.total ELSE 0 END) satis,
        SUM(CASE WHEN s.status='RETURN' THEN -sl.total ELSE 0 END) iade,
        SUM(sl.total) net, SUM({_COST}) cost
        FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id LEFT JOIN stock st ON st.id=sl.stock_id
        WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ?
        GROUP BY substr(s.dt,1,7) ORDER BY 1"""
    rows = []
    for r in DX.rows(q, (a, b)):
        net, cost = _f(r['net']), _f(r['cost'])
        rows.append((r['ay'], int(r['n'] or 0), _r2(r['satis']), _r2(r['iade']), _r2(net), _r2(cost), _r2(net - cost), _r2((net - cost) * 100.0 / net) if abs(net) > 0.005 else 0.0))
    return heads, rows


def fis_kar(DX, a, b):
    heads = ['Belge', 'Tarih', 'Cari', 'Ödeme', 'Tutar', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Kâr %', 'Kullanıcı']
    q = f"""SELECT s.doc_no,s.dt,COALESCE(c.title,'Perakende') cari,s.payment,s.user,
        SUM(sl.total) tutar, SUM({_COST}) cost
        FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id LEFT JOIN stock st ON st.id=sl.stock_id LEFT JOIN caris c ON c.id=s.customer_id
        WHERE s.status='FINAL' AND substr(s.dt,1,10) BETWEEN ? AND ?
        GROUP BY s.id ORDER BY s.dt DESC"""
    rows = []
    for r in DX.rows(q, (a, b)):
        t, cst = _f(r['tutar']), _f(r['cost'])
        rows.append((r['doc_no'], r['dt'], r['cari'], r['payment'] or '', _r2(t), _r2(cst), _r2(t - cst), _r2((t - cst) * 100.0 / t) if abs(t) > 0.005 else 0.0, r['user'] or ''))
    return heads, rows


def kdv_ozeti(DX, a, b):
    heads = ['KDV %', 'Matrah Tutarı (KDV Hariç)', 'KDV Tutarı', 'Toplam Tutar (KDV Dahil)', 'Kaynak']
    out = []
    # Hizmet satışı hem sale_lines hem service_sales tablosuna yazılır: burada atlanır, aşağıda service_sales'ten bir kez sayılır.
    # Satırda KDV oranı da tutarı da 0 ise (eski / içe aktarılmış satış) kayıt eksiktir: ürün kartındaki oran kullanılır ve satır "tahmini" diye ayrı gösterilir.
    _eksik = "(COALESCE(sl.vat_pct,0)=0 AND COALESCE(sl.vat_amount,0)=0 AND COALESCE(st.vat,0)>0)"
    for r in DX.rows(f"""SELECT CASE WHEN {_eksik} THEN st.vat ELSE COALESCE(sl.vat_pct,0) END v, {_eksik} tah,
        SUM(sl.total-CASE WHEN {_eksik} THEN sl.total-sl.total/(1+st.vat/100.0) ELSE COALESCE(sl.vat_amount,0) END) m,
        SUM(CASE WHEN {_eksik} THEN sl.total-sl.total/(1+st.vat/100.0) ELSE COALESCE(sl.vat_amount,0) END) k, SUM(sl.total) t
        FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id LEFT JOIN stock st ON st.id=sl.stock_id
        WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ?
          AND NOT (sl.stock_id IS NULL AND EXISTS(SELECT 1 FROM service_sales ss WHERE ss.doc_no=s.doc_no))
        GROUP BY 1,2 ORDER BY 2,1""", (a, b)):
        out.append((_f(r['v']), _r2(r['m']), _r2(r['k']), _r2(r['t']),
                    'Mal satışı - satırda KDV kaydı yok, ürün kartındaki oran kullanıldı (tahmini)' if r['tah'] else 'Mal satışı (iade düşülmüş)'))
    try:
        for r in DX.rows("""SELECT COALESCE(vat_pct,0) v, SUM(total-COALESCE(vat_amount,0)) m, SUM(COALESCE(vat_amount,0)) k, SUM(total) t
            FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ? GROUP BY COALESCE(vat_pct,0) ORDER BY 1""", (a, b)):
            out.append((_f(r['v']), _r2(r['m']), _r2(r['k']), _r2(r['t']), 'Hizmet satışı'))
    except Exception:
        pass
    if out:
        out.append(('TOPLAM', _r2(sum(x[1] for x in out)), _r2(sum(x[2] for x in out)), _r2(sum(x[3] for x in out)), ''))
    return heads, out


def alis_iadeleri(DX, a, b):
    heads = ['Belge', 'Tarih', 'Tedarikçi', 'Toplam', 'Durum']
    rows = [(r['doc_no'], r['dt'], r['t'] or '(Tanımsız)', _r2(r['total']), r['status'] or '')
            for r in DX.rows("""SELECT p.doc_no,p.dt,c.title t,p.total,p.status FROM purchases p LEFT JOIN caris c ON c.id=p.supplier_id
                WHERE COALESCE(p.status,'')='İade' AND substr(p.dt,1,10) BETWEEN ? AND ? ORDER BY p.dt DESC""", (a, b))]
    return heads, rows


# ----------------------------------------------------------------------------- Finans
def kasa_banka_ozeti(DX, a, b):
    heads = ['Hesap', 'Devir Bakiye', 'Giriş Tutarı', 'Çıkış Tutarı', 'Dönem Sonu Bakiye']
    q = """SELECT COALESCE(NULLIF(account,''),'Ana Kasa') h,
        COALESCE(SUM(CASE WHEN substr(dt,1,10)<? THEN amount END),0) devir,
        COALESCE(SUM(CASE WHEN substr(dt,1,10) BETWEEN ? AND ? AND amount>0 THEN amount END),0) gi,
        COALESCE(SUM(CASE WHEN substr(dt,1,10) BETWEEN ? AND ? AND amount<0 THEN -amount END),0) ci
        FROM finance WHERE substr(dt,1,10)<=? GROUP BY COALESCE(NULLIF(account,''),'Ana Kasa') ORDER BY 1"""
    rows = []
    for r in DX.rows(q, (a, a, b, a, b, b)):
        d, g, c = _f(r['devir']), _f(r['gi']), _f(r['ci'])
        if abs(d) < 0.005 and g < 0.005 and c < 0.005:
            continue
        rows.append((r['h'], _r2(d), _r2(g), _r2(c), _r2(d + g - c)))
    if rows:
        rows.append(('TOPLAM', _r2(sum(x[1] for x in rows)), _r2(sum(x[2] for x in rows)), _r2(sum(x[3] for x in rows)), _r2(sum(x[4] for x in rows))))
    return heads, rows


def gelir_gider(DX, a, b):
    heads = ['İşlem Türü', 'Hareket Sayısı', 'Giriş Tutarı', 'Çıkış Tutarı', 'Net Tutar']
    q = """SELECT COALESCE(NULLIF(kind,''),'(Türsüz)') k, COUNT(*) n,
        COALESCE(SUM(CASE WHEN amount>0 THEN amount END),0) gi, COALESCE(SUM(CASE WHEN amount<0 THEN -amount END),0) ci, SUM(amount) net
        FROM finance WHERE substr(dt,1,10) BETWEEN ? AND ? GROUP BY COALESCE(NULLIF(kind,''),'(Türsüz)') ORDER BY ABS(SUM(amount)) DESC"""
    rows = [(r['k'], int(r['n']), _r2(r['gi']), _r2(r['ci']), _r2(r['net'])) for r in DX.rows(q, (a, b))]
    if rows:
        rows.append(('TOPLAM', sum(x[1] for x in rows), _r2(sum(x[2] for x in rows)), _r2(sum(x[3] for x in rows)), _r2(sum(x[4] for x in rows))))
    return heads, rows


_DISPATCH = {
    'Cari Hesap Ekstresi (Hareket Dökümü)': cari_ekstre,
    'Cari Yaşlandırma (Alacak - Müşteri)': lambda DX, a, b: _aging(DX, True),
    'Cari Yaşlandırma (Borç - Tedarikçi)': lambda DX, a, b: _aging(DX, False),
    'Cari Bazında Satış ve Kâr': cari_satis_kar,
    'Stok Hareket Dökümü': stok_hareket_dokumu,
    'Stok Hareket Özeti (Giriş / Çıkış / Bakiye)': stok_hareket_ozeti,
    'Stok Değer Raporu (Maliyet / Satış / Potansiyel Kâr)': stok_deger,
    'Aylık Satış Özeti': aylik_satis,
    'Satış Fişi Bazında Kâr': fis_kar,
    'Hesaplanan KDV Özeti (İadeler Düşülmüş)': kdv_ozeti,
    'Alış İadeleri': alis_iadeleri,
    'Kasa / Banka Özeti': kasa_banka_ozeti,
    'Gelir - Gider Dağılımı': gelir_gider,
}


# ----------------------------------------------------------------------------- Katalog eklemeleri (kırtasiye rapor kataloğu)
def _veresiye_sql(col='s.payment'):
    return "(LOWER(COALESCE(%s,'')) LIKE '%%cari%%' OR LOWER(COALESCE(%s,'')) LIKE '%%açık%%' OR LOWER(COALESCE(%s,'')) LIKE '%%veresiye%%')" % (col, col, col)


def x_raporu(DX, a, b):
    """Anlık ara rapor: BUGÜN, hiçbir şeyi kapatmaz / değiştirmez."""
    today = _today().isoformat()
    heads = ['Kalem', 'Değer']
    r = DX.one("SELECT COUNT(*) n, COALESCE(SUM(total),0) t, MIN(substr(dt,12,5)) ilk, MAX(substr(dt,12,5)) son FROM sales WHERE status='FINAL' AND substr(dt,1,10)=?", (today,))
    ret = DX.one("SELECT COUNT(*) n, COALESCE(SUM(-total),0) t FROM sales WHERE status='RETURN' AND substr(dt,1,10)=?", (today,))
    n, t = int(r['n'] or 0), _f(r['t'])
    rows = [('Rapor tarihi / saati', datetime.datetime.now().strftime('%Y-%m-%d %H:%M')), ('Satış fişi sayısı', n), ('Brüt satış tutarı', _r2(t)),
            ('İade fişi sayısı', int(ret['n'] or 0)), ('İade tutarı', _r2(ret['t'])), ('Net satış tutarı', _r2(t - _f(ret['t']))),
            ('Ortalama sepet tutarı', _r2(t / n) if n else 0.0), ('İlk işlem saati', r['ilk'] or '-'), ('Son işlem saati', r['son'] or '-')]
    for p in DX.rows("SELECT COALESCE(sp.kind,s.payment,'-') k, COALESCE(SUM(COALESCE(sp.amount,s.total)),0) a FROM sales s LEFT JOIN sale_payments sp ON sp.sale_id=s.id WHERE s.status='FINAL' AND substr(s.dt,1,10)=? GROUP BY COALESCE(sp.kind,s.payment,'-') ORDER BY 2 DESC", (today,)):
        rows.append(('Ödeme: ' + str(p['k']) + ' tutarı', _r2(p['a'])))
    return heads, rows


def iade_urunleri(DX, a, b):
    heads = ['Belge', 'Tarih', 'Barkod', 'Ürün', 'İade Adedi', 'İade Tutarı', 'İade Nedeni', 'Asıl Fiş', 'Kullanıcı']
    rows = [(r['doc_no'], r['dt'], r['barcode'] or '', r['name'] or '', _f(-_f(r['qty'])), _r2(-_f(r['total'])), r['return_note'] or '', r['orig'] or '', r['user'] or '')
            for r in DX.rows("""SELECT s.doc_no,s.dt,sl.barcode,sl.name,sl.qty,sl.total,s.return_note,o.doc_no orig,s.user
                FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id LEFT JOIN sales o ON o.id=s.original_sale_id
                WHERE s.status='RETURN' AND substr(s.dt,1,10) BETWEEN ? AND ? ORDER BY s.dt DESC""", (a, b))]
    return heads, rows


def iade_nedenleri(DX, a, b):
    heads = ['İade Nedeni', 'İade Fişi Sayısı', 'İade Tutarı', 'Pay %']
    data = DX.rows("""SELECT COALESCE(NULLIF(TRIM(return_note),''),'(Neden yazılmamış)') n, COUNT(*) c, SUM(-total) t
        FROM sales WHERE status='RETURN' AND substr(dt,1,10) BETWEEN ? AND ? GROUP BY COALESCE(NULLIF(TRIM(return_note),''),'(Neden yazılmamış)') ORDER BY t DESC""", (a, b))
    tot = sum(_f(x['t']) for x in data)
    return heads, [(x['n'], int(x['c']), _r2(x['t']), _r2(_f(x['t']) * 100.0 / tot) if tot else 0.0) for x in data]


def kasiyer_tahsilat(DX, a, b):
    heads = ['Kasiyer', 'Ödeme Türü', 'Fiş Sayısı', 'Tahsilat Tutarı']
    rows = [(r['u'], r['k'], int(r['n']), _r2(r['t'])) for r in DX.rows("""SELECT COALESCE(NULLIF(s.user,''),'(Bilinmiyor)') u, COALESCE(sp.kind,s.payment,'-') k, COUNT(DISTINCT s.id) n,
        SUM(COALESCE(sp.amount,s.total)) t FROM sales s LEFT JOIN sale_payments sp ON sp.sale_id=s.id
        WHERE s.status='FINAL' AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY 1,2 ORDER BY 1, t DESC""", (a, b))]
    return heads, rows


def kasiyer_islem(DX, a, b):
    heads = ['Kasiyer', 'Fiş Sayısı', 'Satış Tutarı', 'Ort. Sepet Tutarı', 'Aktif Gün', 'Günlük Ort. Fiş', 'İlk İşlem', 'Son İşlem']
    rows = []
    for r in DX.rows("""SELECT COALESCE(NULLIF(user,''),'(Bilinmiyor)') u, COUNT(*) n, SUM(total) t, COUNT(DISTINCT substr(dt,1,10)) g, MIN(dt) ilk, MAX(dt) son
        FROM sales WHERE status='FINAL' AND substr(dt,1,10) BETWEEN ? AND ? GROUP BY 1 ORDER BY n DESC""", (a, b)):
        n, g = int(r['n']), int(r['g'] or 1)
        rows.append((r['u'], n, _r2(r['t']), _r2(_f(r['t']) / n) if n else 0.0, g, round(n / g, 1) if g else 0.0, r['ilk'], r['son']))
    return heads, rows


def aylik_karsilastirma(DX, a, b):
    heads = ['Ay', 'Bu Yıl Net Satış Tutarı', 'Geçen Yıl Net Satış Tutarı', 'Fark Tutarı', 'Değişim %']
    try:
        da, db = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    except ValueError:
        return heads, []
    cur = {r['ay']: _f(r['t']) for r in DX.rows("SELECT substr(s.dt,1,7) ay, SUM(sl.total) t FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY 1", (a, b))}
    try:
        pa, pb = da.replace(year=da.year - 1), db.replace(year=db.year - 1)
    except ValueError:
        pa, pb = da - datetime.timedelta(days=365), db - datetime.timedelta(days=365)
    prev = {r['ay']: _f(r['t']) for r in DX.rows("SELECT substr(s.dt,1,7) ay, SUM(sl.total) t FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY 1", (pa.isoformat(), pb.isoformat()))}
    rows = []
    for ay in sorted(cur):
        y, m = int(ay[:4]), ay[5:]
        p = prev.get('%04d-%s' % (y - 1, m), 0.0)
        rows.append((ay, _r2(cur[ay]), _r2(p), _r2(cur[ay] - p), _r2((cur[ay] - p) * 100.0 / p) if abs(p) > 0.005 else 0.0))
    return heads, rows


def barkod_orani(DX, a, b):
    heads = ['Satır Türü', 'Satır Sayısı', 'Satış Tutarı', 'Pay %']
    q = """SELECT CASE WHEN TRIM(COALESCE(sl.barcode,''))='' THEN 'Barkodsuz (elle giriş)' ELSE 'Barkodlu' END k, COUNT(*) c, SUM(sl.total) t
        FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status='FINAL' AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY 1 ORDER BY 1"""
    data = DX.rows(q, (a, b))
    tot = sum(_f(x['t']) for x in data)
    return heads, [(x['k'], int(x['c']), _r2(x['t']), _r2(_f(x['t']) * 100.0 / tot) if tot else 0.0) for x in data]


def tukenen(DX, a, b):
    heads = ['Barkod', 'Stok Kodu', 'Ürün', 'Marka', 'Grup', 'Stok', 'Minimum Stok', 'Son Alış Maliyeti']
    return heads, [(r['barcode'] or '', r['code'] or '', r['name'] or '', r['brand'] or '', r['grp'] or '', _f(r['qty']), _f(r['min_qty']), _r2(r['last_cost']))
                   for r in DX.rows("SELECT barcode,code,name,brand,grp,qty,min_qty,last_cost FROM stock WHERE active=1 AND qty<=0 ORDER BY name")]


def tedarikci_satis(DX, a, b):
    heads = ['Tedarikçi (Son Alış)', 'Ürün Sayısı', 'Satış Adedi', 'Net Satış Tutarı', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Kâr %']
    q = f"""WITH ls AS (SELECT stock_id, supplier_id FROM supplier_price_history WHERE id IN (SELECT MAX(id) FROM supplier_price_history GROUP BY stock_id))
        SELECT COALESCE(c.title,'(Tedarikçi bilinmiyor)') t, COUNT(DISTINCT sl.stock_id) u, SUM(sl.qty) q, SUM(sl.total) tt, SUM({_COST}) cost
        FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id LEFT JOIN ls ON ls.stock_id=sl.stock_id LEFT JOIN caris c ON c.id=ls.supplier_id
        WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY ls.supplier_id ORDER BY tt DESC"""
    rows = []
    for r in DX.rows(q, (a, b)):
        t, cst = _f(r['tt']), _f(r['cost'])
        rows.append((r['t'], int(r['u'] or 0), _f(r['q']), _r2(t), _r2(cst), _r2(t - cst), _r2((t - cst) * 100.0 / t) if abs(t) > 0.005 else 0.0))
    return heads, rows


def altgrup_satis(DX, a, b):
    heads = ['Grup', 'Alt Grup', 'Satış Adedi', 'Net Satış Tutarı', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Kâr %']
    q = f"""SELECT COALESCE(NULLIF(st.grp,''),'(Grupsuz)') g, COALESCE(NULLIF(st.subgrp,''),'(Alt grupsuz)') sg, SUM(sl.qty) q, SUM(sl.total) t, SUM({_COST}) cost
        FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id
        WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY 1,2 ORDER BY t DESC"""
    rows = []
    for r in DX.rows(q, (a, b)):
        t, cst = _f(r['t']), _f(r['cost'])
        rows.append((r['g'], r['sg'], _f(r['q']), _r2(t), _r2(cst), _r2(t - cst), _r2((t - cst) * 100.0 / t) if abs(t) > 0.005 else 0.0))
    return heads, rows


def grup_stok_degeri(DX, a, b):
    heads = ['Grup', 'Ürün Sayısı', 'Toplam Stok', 'Maliyet Değeri', 'Satış Değeri', 'Potansiyel Kâr']
    rows = []
    for r in DX.rows("""SELECT COALESCE(NULLIF(grp,''),'(Grupsuz)') g, COUNT(*) n, SUM(qty) q,
        SUM(qty*CASE WHEN avg_cost>0 THEN avg_cost ELSE last_cost END) cv, SUM(qty*retail) sv FROM stock WHERE active=1 AND qty>0 GROUP BY 1 ORDER BY cv DESC"""):
        rows.append((r['g'], int(r['n']), round(_f(r['q']), 3), _r2(r['cv']), _r2(r['sv']), _r2(_f(r['sv']) - _f(r['cv']))))
    return heads, rows


def yeni_urunler(DX, a, b):
    heads = ['Eklenme Tarihi', 'Barkod', 'Stok Kodu', 'Ürün', 'Marka', 'Grup', 'Stok', 'Dönemde Satılan Adet', 'Satış Tutarı']
    rows = [(r['created'], r['barcode'] or '', r['code'] or '', r['name'] or '', r['brand'] or '', r['grp'] or '', _f(r['qty']), _f(r['sq']), _r2(r['st']))
            for r in DX.rows("""SELECT st.created,st.barcode,st.code,st.name,st.brand,st.grp,st.qty,
                COALESCE((SELECT SUM(sl.qty) FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE sl.stock_id=st.id AND s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10)>=substr(st.created,1,10)),0) sq,
                COALESCE((SELECT SUM(sl.total) FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE sl.stock_id=st.id AND s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10)>=substr(st.created,1,10)),0) st
                FROM stock st WHERE st.active=1 AND substr(st.created,1,10) BETWEEN ? AND ? ORDER BY st.created DESC""", (a, b))]
    return heads, rows


def hatali_barkod(DX, a, b):
    heads = ['Sorun', 'Barkod', 'Stok Kodu', 'Ürün']
    rows = []
    for r in DX.rows("""SELECT bc, GROUP_CONCAT(name,' | ') names, GROUP_CONCAT(code,' | ') codes, COUNT(*) n FROM (
        SELECT barcode bc,name,code FROM stock WHERE active=1 AND TRIM(COALESCE(barcode,''))<>''
        UNION ALL SELECT sb.barcode,st.name,st.code FROM stock_barcodes sb JOIN stock st ON st.id=sb.stock_id WHERE sb.active=1 AND st.active=1 AND TRIM(COALESCE(sb.barcode,''))<>'')
        GROUP BY bc HAVING COUNT(DISTINCT code)>1 OR COUNT(*)>1 ORDER BY bc"""):
        rows.append(('Mükerrer barkod (%d kayıt)' % int(r['n']), r['bc'], r['codes'] or '', r['names'] or ''))
    for r in DX.rows("SELECT barcode,code,name FROM stock WHERE active=1 AND TRIM(COALESCE(barcode,''))<>'' ORDER BY name"):
        bc = str(r['barcode']).strip()
        if bc.isdigit() and len(bc) in (8, 12, 13):
            digits = [int(x) for x in bc]
            body, chk = digits[:-1], digits[-1]
            s_ = sum(d * (3 if (len(body) - i) % 2 else 1) for i, d in enumerate(body))
            if (10 - s_ % 10) % 10 != chk:
                rows.append(('Kontrol hanesi hatalı (EAN)', bc, r['code'] or '', r['name'] or ''))
        elif ' ' in bc or any(ch in bc for ch in "'\"") or len(bc) < 3:
            rows.append(('Şüpheli barkod biçimi', bc, r['code'] or '', r['name'] or ''))
    return heads, rows


def stok_cikis_zayi(DX, a, b):
    heads = ['Tarih', 'Barkod', 'Ürün', 'İşlem', 'Miktar', 'Birim Maliyet', 'Zarar Tutarı', 'Kaynak', 'Açıklama']
    rows = [(m['dt'], m['barcode'] or '', m['name'] or '', m['kind'] or '', _f(m['qty']), _r2(m['unit_cost']), _r2(-_f(m['qty']) * _f(m['unit_cost'])), m['source'] or '', m['note'] or '')
            for m in DX.rows("""SELECT m.dt,st.barcode,st.name,m.kind,m.qty,m.unit_cost,m.source,m.note FROM stock_moves m LEFT JOIN stock st ON st.id=m.stock_id
                WHERE m.qty<0 AND substr(m.dt,1,10) BETWEEN ? AND ? AND COALESCE(m.kind,'') NOT LIKE 'Satış%' AND COALESCE(m.kind,'') NOT LIKE 'Alış%'
                AND COALESCE(m.source,'') NOT IN ('SALE','PURCHASE_RETURN','PURCHASE_CANCEL','PERIOD_OPEN') ORDER BY m.dt DESC""", (a, b))]
    return heads, rows


def talep_tahmini(DX, a, b):
    heads = ['Barkod', 'Ürün', 'Geçen Yıl Aynı Dönem Satış Adedi', 'Bu Dönem Satış Adedi', 'Mevcut Stok', 'Önerilen Sipariş Adedi']
    try:
        da, db = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
        pa, pb = da.replace(year=da.year - 1), db.replace(year=db.year - 1)
    except ValueError:
        return heads, []
    q = """SELECT st.barcode, st.name, st.qty,
        COALESCE(SUM(CASE WHEN substr(s.dt,1,10) BETWEEN ? AND ? THEN sl.qty END),0) gy,
        COALESCE(SUM(CASE WHEN substr(s.dt,1,10) BETWEEN ? AND ? THEN sl.qty END),0) by_
        FROM stock st JOIN sale_lines sl ON sl.stock_id=st.id JOIN sales s ON s.id=sl.sale_id AND s.status IN ('FINAL','RETURN')
        WHERE st.active=1 GROUP BY st.id HAVING gy>0 OR by_>0 ORDER BY gy DESC"""
    rows = []
    for r in DX.rows(q, (pa.isoformat(), pb.isoformat(), a, b)):
        gy, by_, qty = _f(r['gy']), _f(r['by_']), _f(r['qty'])
        need = max(0.0, round(max(gy, by_) - qty, 3))
        rows.append((r['barcode'] or '', r['name'] or '', round(gy, 3), round(by_, 3), round(qty, 3), need))
    rows.sort(key=lambda x: -x[5])
    return heads, rows


def hizmet_ozeti(DX, a, b):
    heads = ['Hizmet', 'Varyant', 'İşlem Sayısı', 'Adet', 'Satış Tutarı', 'KDV Tutarı', 'Ort. Birim Fiyat']
    rows = []
    for r in DX.rows("""SELECT name, COALESCE(variant,'') v, COUNT(*) n, SUM(qty) q, SUM(total) t, SUM(COALESCE(vat_amount,0)) k
        FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ? GROUP BY name, COALESCE(variant,'') ORDER BY t DESC""", (a, b)):
        q = _f(r['q'])
        rows.append((r['name'] or '', r['v'], int(r['n']), q, _r2(r['t']), _r2(r['k']), _r2(_f(r['t']) / q) if q else 0.0))
    return heads, rows


def _giderler(DX, a, b):
    return DX.rows("SELECT dt,account,kind,amount,note FROM finance WHERE kind LIKE '%Gider%' AND substr(dt,1,10) BETWEEN ? AND ? ORDER BY dt", (a, b))


def gunluk_giderler(DX, a, b):
    heads = ['Tarih', 'Hesap', 'Gider Türü', 'Gider Tutarı', 'Açıklama']
    return heads, [(g['dt'], g['account'] or '', g['kind'] or '', _r2(-_f(g['amount'])), g['note'] or '') for g in reversed(_giderler(DX, a, b))]


def aylik_kar_zarar(DX, a, b):
    heads = ['Ay', 'Net Satış Tutarı', 'Maliyet Tutarı', 'Brüt Kâr Tutarı', 'Gider Tutarı (Finans)', 'Net Kâr / Zarar Tutarı']
    _, ay = aylik_satis(DX, a, b)
    gid = {}
    for g in _giderler(DX, a, b):
        k = _d10(g['dt'])[:7]
        gid[k] = gid.get(k, 0.0) - _f(g['amount'])
    months = sorted({r[0] for r in ay} | set(gid))
    by = {r[0]: r for r in ay}
    rows = []
    for m in months:
        r = by.get(m)
        net, cost, kar = (r[4], r[5], r[6]) if r else (0.0, 0.0, 0.0)
        g = gid.get(m, 0.0)
        rows.append((m, net, cost, kar, _r2(g), _r2(kar - g)))
    if rows:
        rows.append(('TOPLAM',) + tuple(_r2(sum(x[i] for x in rows)) for i in range(1, 6)))
    return heads, rows


def nakit_akis(DX, a, b):
    heads = ['Vade Tarihi', 'Beklenen Tahsilat Tutarı', 'Beklenen Ödeme Tutarı', 'Net Tutar', 'Kümülatif Tutar', 'Kaynak']
    ev = {}
    def add(d, tah, ode, src):
        d = _d10(d)
        if not d:
            return
        x = ev.setdefault(d, [0.0, 0.0, set()])
        x[0] += tah; x[1] += ode; x[2].add(src)
    for r in DX.rows("SELECT due_date,amount-paid k FROM installments WHERE amount-paid>0.005 AND due_date BETWEEN ? AND ?", (a, b)):
        add(r['due_date'], _f(r['k']), 0.0, 'Taksit')
    for r in DX.rows("SELECT kind,due_date,amount FROM checks_notes WHERE status IN ('Portföyde','Bekliyor') AND due_date BETWEEN ? AND ?", (a, b)):
        verilen = str(r['kind'] or '').startswith('Verilen')
        add(r['due_date'], 0.0 if verilen else _f(r['amount']), _f(r['amount']) if verilen else 0.0, 'Çek / Senet')
    rows, cum = [], 0.0
    for d in sorted(ev):
        t, o, src = ev[d]
        cum += t - o
        rows.append((d, _r2(t), _r2(o), _r2(t - o), _r2(cum), ' + '.join(sorted(src))))
    return heads, rows


def pos_komisyon(DX, a, b):
    heads = ['Tarih', 'POS', 'Brüt Tutar', 'Komisyon Tutarı', 'Net Tutar', 'Vade Tarihi', 'Durum']
    rows = [(r['dt'], r['pos_name'] or '', _r2(r['gross']), _r2(r['commission']), _r2(r['net']), r['due_date'] or '', r['status'] or '')
            for r in DX.rows("SELECT dt,pos_name,gross,commission,net,due_date,status FROM pos_settlements WHERE substr(dt,1,10) BETWEEN ? AND ? ORDER BY dt DESC", (a, b))]
    if rows:
        rows.append(('TOPLAM', '', _r2(sum(x[2] for x in rows)), _r2(sum(x[3] for x in rows)), _r2(sum(x[4] for x in rows)), '', ''))
    return heads, rows


def alacak_borc_denge(DX, a, b):
    heads = ['Kalem', 'Varlık Tutarı', 'Borç Tutarı']
    cari = DX.one("""SELECT COALESCE(SUM(CASE WHEN b>0 THEN b END),0) alacak, COALESCE(SUM(CASE WHEN b<0 THEN -b END),0) borc FROM (SELECT SUM(debit-credit) b FROM cari_moves GROUP BY cari_id)""")
    fin = DX.one("SELECT COALESCE(SUM(amount),0) t FROM finance")
    stk = DX.one("SELECT COALESCE(SUM(qty*CASE WHEN avg_cost>0 THEN avg_cost ELSE last_cost END),0) t FROM stock WHERE active=1 AND qty>0")
    cek_a = DX.one("SELECT COALESCE(SUM(amount),0) t FROM checks_notes WHERE status IN ('Portföyde','Bekliyor') AND kind NOT LIKE 'Verilen%'")
    cek_v = DX.one("SELECT COALESCE(SUM(amount),0) t FROM checks_notes WHERE status IN ('Portföyde','Bekliyor') AND kind LIKE 'Verilen%'")
    rows = [('Kasa / Banka bakiyesi', _r2(fin['t']), 0.0), ('Stok değeri (maliyet)', _r2(stk['t']), 0.0), ('Müşterilerden alacaklar (cari)', _r2(cari['alacak']), 0.0),
            ('Borçlu olduğumuz cariler (tedarikçi borcu / alınan avans)', 0.0, _r2(cari['borc'])), ('Portföydeki alınan çek / senet', _r2(cek_a['t']), 0.0), ('Verilen çek / senet', 0.0, _r2(cek_v['t']))]
    va, bo = sum(x[1] for x in rows), sum(x[2] for x in rows)
    rows += [('TOPLAM', _r2(va), _r2(bo)), ('NET DURUM (Varlık - Borç)', _r2(va - bo), 0.0)]
    return heads, rows


def yuksek_karli(DX, a, b):
    heads = ['Barkod', 'Stok Kodu', 'Ürün', 'Ort. Maliyet', 'Perakende', 'Kâr Tutarı', 'Kâr %', 'Dönem Satış Adedi']
    rows = []
    for r in DX.rows("""SELECT st.barcode,st.code,st.name,CASE WHEN st.avg_cost>0 THEN st.avg_cost ELSE st.last_cost END c, st.retail,
        COALESCE((SELECT SUM(sl.qty) FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE sl.stock_id=st.id AND s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ?),0) q
        FROM stock st WHERE st.active=1 AND st.retail>0 AND (st.avg_cost>0 OR st.last_cost>0) ORDER BY (st.retail-CASE WHEN st.avg_cost>0 THEN st.avg_cost ELSE st.last_cost END)*1.0/st.retail DESC LIMIT 300""", (a, b)):
        c, p = _f(r['c']), _f(r['retail'])
        rows.append((r['barcode'] or '', r['code'] or '', r['name'] or '', _r2(c), _r2(p), _r2(p - c), _r2((p - c) * 100.0 / p), _f(r['q'])))
    return heads, rows


def mali_musavir(DX, a, b):
    heads = ['Kalem', 'Tutar']
    sat = DX.one("SELECT COALESCE(SUM(sl.total),0) t, COALESCE(SUM(sl.vat_amount),0) k FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ?", (a, b))
    hiz = DX.one("SELECT COALESCE(SUM(total),0) t, COALESCE(SUM(vat_amount),0) k FROM service_sales WHERE substr(dt,1,10) BETWEEN ? AND ?", (a, b))
    alis = DX.one("SELECT COALESCE(SUM(total),0) t FROM purchases WHERE COALESCE(status,'') NOT IN ('İptal','İade') AND substr(dt,1,10) BETWEEN ? AND ?", (a, b))
    aiade = DX.one("SELECT COALESCE(SUM(total),0) t FROM purchases WHERE COALESCE(status,'')='İade' AND substr(dt,1,10) BETWEEN ? AND ?", (a, b))
    gid = sum(-_f(g['amount']) for g in _giderler(DX, a, b))
    _, kz = aylik_kar_zarar(DX, a, b)
    brut = kz[-1][3] if kz else 0.0
    fin = DX.one("SELECT COALESCE(SUM(amount),0) t FROM finance WHERE substr(dt,1,10)<=?", (b,))
    stk = DX.one("SELECT COALESCE(SUM(qty*CASE WHEN avg_cost>0 THEN avg_cost ELSE last_cost END),0) t FROM stock WHERE active=1 AND qty>0")
    cari = DX.one("SELECT COALESCE(SUM(debit-credit),0) t FROM cari_moves WHERE substr(dt,1,10)<=?", (b,))
    rows = [('Dönem', '%s / %s' % (a, b)), ('Mal satışı (KDV dahil, iade düşülmüş)', _r2(sat['t'])), ('Hizmet satışı (KDV dahil)', _r2(hiz['t'])),
            ('Hesaplanan KDV (mal + hizmet)', _r2(_f(sat['k']) + _f(hiz['k']))), ('Alışlar (iptal / iade hariç)', _r2(alis['t'])), ('Alış iadeleri', _r2(aiade['t'])),
            ('Brüt kâr (satış - maliyet)', _r2(brut)), ('Gider kayıtları (finans)', _r2(gid)), ('Kasa / banka bakiyesi (dönem sonu)', _r2(fin['t'])),
            ('Stok değeri (maliyet, bugünkü)', _r2(stk['t'])), ('Cari net bakiye (+ alacak / - borç)', _r2(cari['t']))]
    return heads, rows


def kapanmis_cariler(DX, a, b):
    heads = ['Cari', 'Rol', 'Hareket Sayısı', 'Toplam Borç Tutarı', 'Toplam Alacak Tutarı', 'Son Hareket']
    return heads, [(r['title'], r['role'] or '', int(r['n']), _r2(r['d']), _r2(r['c']), _d10(r['son'])) for r in DX.rows(
        """SELECT c.title,c.role,COUNT(m.id) n,SUM(m.debit) d,SUM(m.credit) c,MAX(m.dt) son FROM caris c JOIN cari_moves m ON m.cari_id=c.id
        WHERE c.active=1 GROUP BY c.id HAVING ABS(SUM(m.debit-m.credit))<0.005 AND SUM(m.debit)>0 ORDER BY son DESC""")]


def supheli_alacak(DX, a, b):
    heads, rows = _aging(DX, True)
    out = [(r[0], r[1], r[5], r[6], r[7]) for r in rows if _f(r[5]) + _f(r[6]) > 0.005]
    return ['Cari', 'Güncel Bakiye', 'Bakiye 91-180 Gün', 'Bakiye 181+ Gün', 'En Eski Belge Tarihi'], out


def avans_alinan(DX, a, b):
    heads = ['Cari', 'Rol', 'Telefon', 'Avans / Kapora Bakiye Tutarı']
    return heads, [(r['title'], r['role'] or '', r['phone'] or '', _r2(-_f(r['b']))) for r in DX.rows(
        """SELECT c.title,c.role,c.phone,SUM(m.debit-m.credit) b FROM caris c JOIN cari_moves m ON m.cari_id=c.id
        WHERE c.active=1 AND COALESCE(c.role,'') NOT LIKE 'Tedarik%' GROUP BY c.id HAVING b<-0.005 ORDER BY b""")]


def cari_iletisim(DX, a, b):
    heads = ['Cari', 'Rol', 'Bakiye', 'Telefon', 'E-posta', 'İl', 'İlçe', 'Adres', 'Yetkili', 'Yetkili Telefon']
    return heads, [(r['title'], r['role'] or '', _r2(r['b']), r['phone'] or '', r['email'] or '', r['city'] or '', r['district'] or '', r['address'] or '', r['contact_name'] or '', r['contact_phone'] or '')
                   for r in DX.rows("""SELECT c.title,c.role,c.phone,c.email,c.city,c.district,c.address,c.contact_name,c.contact_phone,COALESCE(SUM(m.debit-m.credit),0) b
        FROM caris c LEFT JOIN cari_moves m ON m.cari_id=c.id WHERE c.active=1 GROUP BY c.id HAVING b>0.005 ORDER BY b DESC""")]


def veresiye_orani(DX, a, b):
    heads = ['Ay', 'Toplam Ciro Tutarı', 'Veresiye (Cari) Ciro Tutarı', 'Veresiye Oranı %', 'Fiş Sayısı', 'Veresiye Fiş Sayısı']
    q = f"""SELECT substr(dt,1,7) ay, SUM(total) t, SUM(CASE WHEN {_veresiye_sql('payment')} THEN total ELSE 0 END) v,
        COUNT(*) n, SUM(CASE WHEN {_veresiye_sql('payment')} THEN 1 ELSE 0 END) vn FROM sales WHERE status='FINAL' AND substr(dt,1,10) BETWEEN ? AND ? GROUP BY 1 ORDER BY 1"""
    rows = [(r['ay'], _r2(r['t']), _r2(r['v']), _r2(_f(r['v']) * 100.0 / _f(r['t'])) if _f(r['t']) else 0.0, int(r['n']), int(r['vn'] or 0)) for r in DX.rows(q, (a, b))]
    if rows:
        t, v = sum(x[1] for x in rows), sum(x[2] for x in rows)
        rows.append(('TOPLAM', _r2(t), _r2(v), _r2(v * 100.0 / t) if t else 0.0, sum(x[4] for x in rows), sum(x[5] for x in rows)))
    return heads, rows


_DISPATCH.update({
    'X Raporu (Ara Rapor - Anlık)': x_raporu,
    'İade Edilen Ürünler (Satır Bazında)': iade_urunleri,
    'İade Nedenleri Analizi': iade_nedenleri,
    'Kasiyer Bazında Tahsilat (Ödeme Türüne Göre)': kasiyer_tahsilat,
    'Kasiyer İşlem Adedi ve Hızı': kasiyer_islem,
    'Aylık Ciro Karşılaştırma (Geçen Yıl)': aylik_karsilastirma,
    'Barkodlu / Barkodsuz Satış Oranı': barkod_orani,
    'Tükenen Ürünler': tukenen,
    'Tedarikçi Bazlı Satış (Son Alış Tedarikçisi)': tedarikci_satis,
    'Alt Grup Bazında Satış': altgrup_satis,
    'Grup Bazında Stok Değeri': grup_stok_degeri,
    'Yeni Eklenen Ürünler': yeni_urunler,
    'Hatalı / Mükerrer Barkodlar': hatali_barkod,
    'Stok Çıkış / Zayi / Düzeltme Hareketleri': stok_cikis_zayi,
    'Sezonluk Talep Tahmini (Geçen Yıl Esaslı)': talep_tahmini,
    'Hizmet Gelir Özeti (Hizmet Bazında)': hizmet_ozeti,
    'Günlük Giderler': gunluk_giderler,
    'Aylık Kâr / Zarar (Gider Dahil)': aylik_kar_zarar,
    'Nakit Akış Takvimi': nakit_akis,
    'POS Komisyon Kesinti Raporu': pos_komisyon,
    'Alacak / Borç Denge Raporu': alacak_borc_denge,
    'Yüksek Kârlı Ürünler': yuksek_karli,
    'Dönem Sonu Özet (Mali Müşavir)': mali_musavir,
    'Kapanmış Cari Hesaplar': kapanmis_cariler,
    'Şüpheli / Gecikmiş Alacaklar (91+ Gün)': supheli_alacak,
    'Avans / Kapora Alınan Cariler': avans_alinan,
    'Cari İletişim ve Adres Listesi (Borçlular)': cari_iletisim,
    'Veresiye Ciro Oranı': veresiye_orani,
})
NEW_REPORTS += [
    ('X Raporu (Ara Rapor - Anlık)', 'Gün Sonu Özeti (Z Raporu)'),
    ('İade Edilen Ürünler (Satır Bazında)', 'İade Fişleri'),
    ('İade Nedenleri Analizi', 'İade Edilen Ürünler (Satır Bazında)'),
    ('Kasiyer Bazında Tahsilat (Ödeme Türüne Göre)', 'Personel / İskonto Performansı'),
    ('Kasiyer İşlem Adedi ve Hızı', 'Kasiyer Bazında Tahsilat (Ödeme Türüne Göre)'),
    ('Aylık Ciro Karşılaştırma (Geçen Yıl)', 'Aylık Satış Özeti'),
    ('Barkodlu / Barkodsuz Satış Oranı', 'Satış Fişi Bazında Kâr'),
    ('Tükenen Ürünler', 'Kritik Stok'),
    ('Tedarikçi Bazlı Satış (Son Alış Tedarikçisi)', 'Tedarikçi Analizi'),
    ('Alt Grup Bazında Satış', 'Grup Analizi'),
    ('Grup Bazında Stok Değeri', 'Stok Değer Raporu (Maliyet / Satış / Potansiyel Kâr)'),
    ('Yeni Eklenen Ürünler', 'Barkodsuz Stoklar'),
    ('Hatalı / Mükerrer Barkodlar', 'Yeni Eklenen Ürünler'),
    ('Stok Çıkış / Zayi / Düzeltme Hareketleri', 'Stok Hareket Özeti (Giriş / Çıkış / Bakiye)'),
    ('Sezonluk Talep Tahmini (Geçen Yıl Esaslı)', 'Sipariş Öneri Raporu'),
    ('Hizmet Gelir Özeti (Hizmet Bazında)', 'Hizmet Satışları'),
    ('Günlük Giderler', 'Gelir - Gider Dağılımı'),
    ('Aylık Kâr / Zarar (Gider Dahil)', 'Günlük Giderler'),
    ('Nakit Akış Takvimi', 'Yaklaşan Vadeler'),
    ('POS Komisyon Kesinti Raporu', 'Kartlı Ödeme Hareketleri'),
    ('Alacak / Borç Denge Raporu', 'Aylık Kâr / Zarar (Gider Dahil)'),
    ('Yüksek Kârlı Ürünler', 'Düşük Kârlı Ürünler'),
    ('Dönem Sonu Özet (Mali Müşavir)', 'Alacak / Borç Denge Raporu'),
    ('Kapanmış Cari Hesaplar', 'Alacaklı Cariler'),
    ('Şüpheli / Gecikmiş Alacaklar (91+ Gün)', 'Cari Yaşlandırma (Borç - Tedarikçi)'),
    ('Avans / Kapora Alınan Cariler', 'Kapanmış Cari Hesaplar'),
    ('Cari İletişim ve Adres Listesi (Borçlular)', 'Borçlu Cariler'),
    ('Veresiye Ciro Oranı', 'Cari Bazında Satış ve Kâr'),
]
NAMES = [n for n, _ in NEW_REPORTS]



def _iptal_tablo(DX):
    con = DX.con()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS sale_cancels(id INTEGER PRIMARY KEY,dt TEXT,user TEXT,kind TEXT,item_count INTEGER,total REAL,approver TEXT,note TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS sale_cancel_lines(id INTEGER PRIMARY KEY,cancel_id INTEGER,stock_id INTEGER,barcode TEXT,name TEXT,qty REAL,price REAL,total REAL)")
        con.commit()
    finally:
        con.close()


def iptal_fisleri(DX, a, b):
    _iptal_tablo(DX)
    heads = ['Tarih', 'Kasiyer', 'İptal Türü', 'Satır Sayısı', 'İptal Tutarı', 'Onaylayan']
    rows = [(r['dt'], r['user'] or '', r['kind'] or '', int(r['item_count'] or 0), _r2(r['total']), r['approver'] or '(onay gerekmedi)')
            for r in DX.rows("SELECT * FROM sale_cancels WHERE substr(dt,1,10) BETWEEN ? AND ? ORDER BY dt DESC", (a, b))]
    if rows:
        rows.append(('TOPLAM', '', '', sum(x[3] for x in rows), _r2(sum(x[4] for x in rows)), ''))
    return heads, rows


def iptal_urunleri(DX, a, b):
    _iptal_tablo(DX)
    heads = ['Barkod', 'Ürün', 'İptal Sayısı', 'İptal Adedi', 'İptal Tutarı', 'Son İptal']
    return heads, [(r['barcode'] or '', r['name'] or '', int(r['n']), _f(r['q']), _r2(r['t']), r['son'])
                   for r in DX.rows("""SELECT l.barcode, l.name, COUNT(*) n, SUM(l.qty) q, SUM(l.total) t, MAX(c.dt) son
        FROM sale_cancel_lines l JOIN sale_cancels c ON c.id=l.cancel_id WHERE substr(c.dt,1,10) BETWEEN ? AND ?
        GROUP BY COALESCE(l.stock_id, l.name) ORDER BY t DESC""", (a, b))]


_DISPATCH.update({'İptal Edilen Fişler (Kaydedilmeden)': iptal_fisleri, 'İptal Edilen Ürünler': iptal_urunleri})
NEW_REPORTS += [('İptal Edilen Fişler (Kaydedilmeden)', 'İade Nedenleri Analizi'), ('İptal Edilen Ürünler', 'İptal Edilen Fişler (Kaydedilmeden)')]
NAMES = [n for n, _ in NEW_REPORTS]


# ----------------------------------------------------------------------------- Sıcak satış (v1.2.291.470)
def _sicak_donem(DX, b, gun):
    """Seçilen bitiş gününe (b) kadar 'gun' günlük ürün satışı: {stock_id: (ad, barkod, adet, ciro, stok)}."""
    try:
        end = datetime.date.fromisoformat(_d10(b))
    except ValueError:
        end = _today()
    return end, DX


def _sicak_satis(DX, bas, bit):
    out = {}
    for r in DX.rows(
        "SELECT st.id sid,st.barcode bc,st.name nm,st.qty stk,SUM(sl.qty) q,SUM(sl.total) t "
        "FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id JOIN stock st ON st.id=sl.stock_id "
        "WHERE s.status IN ('FINAL','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? AND st.active=1 GROUP BY st.id",
        (bas.isoformat(), bit.isoformat())):
        out[r['sid']] = (r['nm'] or '', r['bc'] or '', _f(r['q']), _f(r['t']), _f(r['stk']))
    return out


def sicak_yukselen(DX, a, b):
    """Son 7 gün ile ondan önceki 7 günün karşılaştırması: satışı hızla artan ürünler."""
    end, _ = _sicak_donem(DX, b, 7)
    d1 = datetime.timedelta(days=1)
    now = _sicak_satis(DX, end - datetime.timedelta(days=6), end)
    old = _sicak_satis(DX, end - datetime.timedelta(days=13), end - datetime.timedelta(days=7))
    rows = []
    for sid, (nm, bc, q, t, stk) in now.items():
        if q <= 0:
            continue
        oq = old.get(sid, ('', '', 0.0, 0.0, 0.0))[2]
        if oq > 0:
            ch = round((q - oq) * 100.0 / oq, 1)
            if q <= oq:
                continue
        else:
            ch = 'Yeni satış'
        rows.append((bc, nm, round(oq, 2), round(q, 2), round(q - oq, 2), ch, round(t, 2), round(stk, 2)))
    rows.sort(key=lambda r: (-(r[4]), r[1]))
    heads = ['Barkod', 'Ürün', 'Önceki 7 Gün Adet', 'Son 7 Gün Adet', 'Artış Adedi', 'Artış %', 'Son 7 Gün Ciro', 'Mevcut Stok']
    return heads, rows[:200]


def sicak_hizli(DX, a, b):
    """Son 7 günde en hızlı dönen ürünler ve mevcut stoğun kaç güne yeteceği."""
    end, _ = _sicak_donem(DX, b, 7)
    now = _sicak_satis(DX, end - datetime.timedelta(days=6), end)
    rows = []
    for sid, (nm, bc, q, t, stk) in now.items():
        if q <= 0:
            continue
        gunluk = q / 7.0
        gun = round(stk / gunluk, 1) if stk > 0 else 0
        durum = 'STOK BİTTİ' if stk <= 0 else ('ACİL' if gun < 3 else ('YAKINDA' if gun < 7 else 'YETERLİ'))
        rows.append((bc, nm, round(q, 2), round(gunluk, 2), round(stk, 2), gun, durum, round(t, 2)))
    rows.sort(key=lambda r: (-(r[2]), r[1]))
    heads = ['Barkod', 'Ürün', 'Son 7 Gün Adet', 'Günlük Hız', 'Mevcut Stok', 'Stok Kaç Güne Yeter', 'Durum', 'Son 7 Gün Ciro']
    return heads, rows[:200]


_DISPATCH.update({'Sıcak Satış - Yükselen Ürünler': sicak_yukselen, 'Sıcak Satış - Hızlı Satanlar ve Stok Yeterliliği': sicak_hizli})
NEW_REPORTS += [('Sıcak Satış - Yükselen Ürünler', 'En Çok Satan 200'), ('Sıcak Satış - Hızlı Satanlar ve Stok Yeterliliği', 'Sıcak Satış - Yükselen Ürünler')]
NAMES = [n for n, _ in NEW_REPORTS]


def build(kind, a, b, DX):
    fn = _DISPATCH.get(kind)
    if fn is None:
        return None
    heads, rows = fn(DX, a, b)
    if not rows:
        return heads, [('Seçilen koşullarda kayıt bulunamadı.',) + ('',) * (len(heads) - 1)]
    return heads, rows
