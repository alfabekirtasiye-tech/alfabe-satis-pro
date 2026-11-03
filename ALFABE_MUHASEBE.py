# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Muhasebe & Beyan hazırlığı.

Üç iş yapar (hiçbir kayıt DEĞİŞTİRİLMEZ, yalnız okunur):
  1) Ba-Bs formu listesi   (cari bazında aylık alış / satış bildirimi)
  2) KDV özeti             (oran bazında hesaplanan / indirilecek KDV)
  3) Muhasebe aktarımı     (fatura listesi + yevmiye fişi taslağı, CSV / XLSX)

Veri kaynakları
  Satış : sales + sale_lines   (KDV satırda saklı: vat_pct / vat_amount, KDV dahil total)
          iade belgeleri (status='RETURN') negatif satırlarla zaten ters kaydedilmiştir.
  Alış  : purchase_line_tax    (doğrudan alış ekranı)  +
          supplier_invoices / supplier_invoice_lines (onaylı tedarikçi faturaları)
          Mal kabul (goods_receipts) tek başına mali belge sayılmaz, fatura gelmeden KDV'ye girmez.

Bu çıktılar muhasebeci için HAZIRLIK listesidir; resmi beyanname değildir.
"""
import csv
import datetime
import re
import sqlite3

BABS_ESIK_VARSAYILAN = 5000.0   # KDV hariç, cari başına aylık (mevzuat değişirse ekrandan değiştirilebilir)
KDV_ORANLARI = (0.0, 1.0, 10.0, 20.0)

CANCELLED_SALE = ('CANCELLED', 'CANCELED', 'VOID', 'İPTAL', 'İptal', 'IPTAL', 'Taslak', 'DRAFT')
# Uygulama iptal edilen alışı purchases.status='İptal' (küçük harfli) olarak yazar; büyük/küçük yazımların hepsi dışlanır.
CANCELLED_PURCHASE = ('İPTAL', 'İptal', 'IPTAL', 'Iptal', 'CANCELLED', 'Taslak', 'DRAFT')
CANCELLED_INVOICE = ('Taslak', 'İptal', 'İPTAL', 'Reddedildi', 'CANCELLED', 'DRAFT')

HESAPLAR_VARSAYILAN = {
    'kasa': ('100', 'KASA'),
    'cek': ('101', 'ALINAN ÇEKLER'),
    'banka': ('102', 'BANKALAR'),
    'pos': ('108', 'DİĞER HAZIR DEĞERLER (POS)'),
    'alici': ('120', 'ALICILAR'),
    'senet': ('121', 'ALACAK SENETLERİ'),
    'mal': ('153', 'TİCARİ MALLAR'),
    'indirilecek_kdv': ('191', 'İNDİRİLECEK KDV'),
    'satici': ('320', 'SATICILAR'),
    'hesaplanan_kdv': ('391', 'HESAPLANAN KDV'),
    'satis': ('600', 'YURTİÇİ SATIŞLAR'),
    'satis_iade': ('610', 'SATIŞTAN İADELER (-)'),
}


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def r2(x):
    return round(_f(x) + 0.0, 2)


def donem(yil, ay):
    """(başlangıç, bitiş) 'YYYY-MM-DD' kapsayıcı."""
    yil, ay = int(yil), int(ay)
    d1 = datetime.date(yil, ay, 1)
    d2 = (datetime.date(yil + (ay == 12), (ay % 12) + 1, 1) - datetime.timedelta(days=1))
    return d1.isoformat(), d2.isoformat()


def vkn_ok(v):
    s = re.sub(r'\D', '', str(v or ''))
    return len(s) in (10, 11)


def _has_table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _ph(seq):
    return ','.join('?' * len(seq))


# --------------------------------------------------------------------------- toplama
def collect_sales(c, d1, d2):
    """Satış / iade belgeleri. Dönüş: (belgeler, uyarılar)."""
    c.row_factory = sqlite3.Row
    sql = ("SELECT s.id sid,s.doc_no,s.dt,s.customer_id,s.payment,s.status,s.total stotal,"
           "ca.title,ca.tax_no,l.vat_pct,l.total lt,l.vat_amount,st.vat stock_vat "
           "FROM sales s JOIN sale_lines l ON l.sale_id=s.id "
           "LEFT JOIN caris ca ON ca.id=s.customer_id LEFT JOIN stock st ON st.id=l.stock_id "
           "WHERE substr(s.dt,1,10) BETWEEN ? AND ? AND COALESCE(s.status,'FINAL') NOT IN (" + _ph(CANCELLED_SALE) + ") "
           "ORDER BY s.dt,s.id,l.id")
    docs = {}
    filled = 0
    for r in c.execute(sql, (d1, d2) + CANCELLED_SALE):
        d = docs.get(r['sid'])
        if d is None:
            d = docs[r['sid']] = {'tip': 'IADE' if str(r['status'] or '') == 'RETURN' else 'SATIS', 'id': r['sid'],
                                  'no': r['doc_no'] or '', 'dt': str(r['dt'] or '')[:10], 'cari_id': r['customer_id'],
                                  'unvan': (r['title'] or '') if r['customer_id'] else '', 'vkn': re.sub(r'\D', '', str(r['tax_no'] or '')),
                                  'odeme': r['payment'] or '', 'stotal': _f(r['stotal']), 'oran': {}}
        lt = _f(r['lt'])
        vp = _f(r['vat_pct'])
        va = _f(r['vat_amount'])
        if vp == 0 and va == 0 and _f(r['stock_vat']) > 0:
            # Eski kayıtlarda KDV kolonları boş olabilir: stok kartındaki orandan tamamla.
            vp = _f(r['stock_vat'])
            va = lt * vp / (100.0 + vp)
            filled += 1
        elif vp > 0 and va == 0 and lt != 0:
            va = lt * vp / (100.0 + vp)
        net = lt - va
        o = d['oran'].setdefault(round(vp, 2), [0.0, 0.0])
        o[0] += net
        o[1] += va
    for d in docs.values():
        for k in d['oran']:
            d['oran'][k] = [r2(d['oran'][k][0]), r2(d['oran'][k][1])]
        d['net'] = r2(sum(v[0] for v in d['oran'].values()))
        d['kdv'] = r2(sum(v[1] for v in d['oran'].values()))
        d['toplam'] = r2(d['net'] + d['kdv'])
    # ödeme kırılımı (karma ödemeler)
    if _has_table(c, 'sale_payments') and docs:
        ids = list(docs)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for p in c.execute('SELECT sale_id,kind,amount FROM sale_payments WHERE sale_id IN (' + _ph(part) + ')', part):
                docs[p['sale_id']].setdefault('odemeler', []).append((p['kind'] or '', _f(p['amount'])))
    uy = []
    if filled:
        uy.append('%d satış satırında KDV bilgisi boştu; stok kartındaki orandan tamamlandı.' % filled)
    farkli = [d for d in docs.values() if abs(d['toplam'] - d['stotal']) > 0.05]
    if farkli:
        uy.append('%d belgede satır toplamı ile belge toplamı uyuşmuyor (ilk: %s). Lütfen kontrol edin.' % (len(farkli), farkli[0]['no']))
    return list(docs.values()), uy


def collect_purchases(c, d1, d2):
    c.row_factory = sqlite3.Row
    docs = {}
    uy = []
    if _has_table(c, 'purchase_line_tax'):
        sql = ("SELECT p.id pid,p.doc_no,p.dt,p.supplier_id,ca.title,ca.tax_no,t.vat_rate,t.net_amount,t.vat_amount "
               "FROM purchases p JOIN purchase_line_tax t ON t.purchase_id=p.id LEFT JOIN caris ca ON ca.id=p.supplier_id "
               "WHERE substr(p.dt,1,10) BETWEEN ? AND ? AND COALESCE(p.status,'') NOT IN (" + _ph(CANCELLED_PURCHASE) + ") ORDER BY p.dt,p.id")
        for r in c.execute(sql, (d1, d2) + CANCELLED_PURCHASE):
            key = ('A', r['pid'])
            d = docs.get(key)
            if d is None:
                d = docs[key] = {'tip': 'ALIS', 'id': r['pid'], 'no': r['doc_no'] or '', 'dt': str(r['dt'] or '')[:10], 'cari_id': r['supplier_id'],
                                 'unvan': r['title'] or '', 'vkn': re.sub(r'\D', '', str(r['tax_no'] or '')), 'odeme': '', 'oran': {}}
            o = d['oran'].setdefault(round(_f(r['vat_rate']), 2), [0.0, 0.0])
            o[0] += _f(r['net_amount'])
            o[1] += _f(r['vat_amount'])
    if _has_table(c, 'purchase_return_lines') and _has_table(c, 'purchase_line_tax'):
        # Alış iadeleri iade tarihinde negatif belge olarak girer: indirilecek KDV ve Ba tutarı düşer.
        sql = ("SELECT r.id rid,r.purchase_id pid,r.stock_id,r.dt,r.net,r.gross,p.doc_no,p.supplier_id,ca.title,ca.tax_no,"
               "(SELECT t.vat_rate FROM purchase_line_tax t WHERE t.purchase_id=r.purchase_id AND t.stock_id=r.stock_id LIMIT 1) vr "
               "FROM purchase_return_lines r JOIN purchases p ON p.id=r.purchase_id LEFT JOIN caris ca ON ca.id=p.supplier_id "
               "WHERE substr(r.dt,1,10) BETWEEN ? AND ? AND EXISTS(SELECT 1 FROM purchase_line_tax x WHERE x.purchase_id=r.purchase_id) ORDER BY r.dt,r.id")
        for r in c.execute(sql, (d1, d2)):
            key = ('R', r['pid'], str(r['dt'] or '')[:10])
            d = docs.get(key)
            if d is None:
                d = docs[key] = {'tip': 'ALIS_IADE', 'id': r['pid'], 'no': (r['doc_no'] or '') + ' (İADE)', 'dt': str(r['dt'] or '')[:10], 'cari_id': r['supplier_id'],
                                 'unvan': r['title'] or '', 'vkn': re.sub(r'\D', '', str(r['tax_no'] or '')), 'odeme': '', 'oran': {}}
            net = _f(r['net']); vat = _f(r['gross']) - net
            rate = round(_f(r['vr']), 2) if r['vr'] is not None else (round(vat / net * 100.0) if net else 0.0)
            o = d['oran'].setdefault(rate, [0.0, 0.0])
            o[0] -= net
            o[1] -= vat
    if _has_table(c, 'supplier_invoices') and _has_table(c, 'supplier_invoice_lines'):
        sql = ("SELECT i.id iid,i.invoice_no,i.dt,i.supplier_id,ca.title,ca.tax_no,l.vat_pct,l.net,l.vat,i.subtotal,i.vat hvat,i.total htotal "
               "FROM supplier_invoices i JOIN supplier_invoice_lines l ON l.invoice_id=i.id LEFT JOIN caris ca ON ca.id=i.supplier_id "
               "WHERE substr(i.dt,1,10) BETWEEN ? AND ? AND COALESCE(i.status,'Onaylandı') NOT IN (" + _ph(CANCELLED_INVOICE) + ") "
               "AND COALESCE(i.reversed_at,'')='' ORDER BY i.dt,i.id")
        htot = {}
        for r in c.execute(sql, (d1, d2) + CANCELLED_INVOICE):
            key = ('B', r['iid'])
            d = docs.get(key)
            if d is None:
                d = docs[key] = {'tip': 'ALIS', 'id': r['iid'], 'no': r['invoice_no'] or '', 'dt': str(r['dt'] or '')[:10], 'cari_id': r['supplier_id'],
                                 'unvan': r['title'] or '', 'vkn': re.sub(r'\D', '', str(r['tax_no'] or '')), 'odeme': '', 'oran': {}}
                htot[key] = _f(r['htotal'])
            o = d['oran'].setdefault(round(_f(r['vat_pct']), 2), [0.0, 0.0])
            o[0] += _f(r['net'])
            o[1] += _f(r['vat'])
        farkli = 0
        for key, ht in htot.items():
            d = docs[key]
            if ht and abs(sum(v[0] + v[1] for v in d['oran'].values()) - ht) > 0.05:
                farkli += 1
        if farkli:
            uy.append('%d tedarikçi faturasında başlık toplamı ile satır toplamı farklı (nakliye/ek masraf olabilir); satır toplamı esas alındı.' % farkli)
    for d in docs.values():
        for k in d['oran']:
            d['oran'][k] = [r2(d['oran'][k][0]), r2(d['oran'][k][1])]
        d['net'] = r2(sum(v[0] for v in d['oran'].values()))
        d['kdv'] = r2(sum(v[1] for v in d['oran'].values()))
        d['toplam'] = r2(d['net'] + d['kdv'])
    out = sorted(docs.values(), key=lambda x: (x['dt'], str(x['no'])))
    return out, uy


# --------------------------------------------------------------------------- 1) Ba-Bs
def ba_bs(docs, esik=BABS_ESIK_VARSAYILAN):
    """Cari bazında aylık toplam. Eşiği (KDV hariç, iade hariç faturalar toplamı) aşanlar 'bildirilecek'.
    Dönüş: (satırlar, uyarılar, cari_kartsiz_toplam)"""
    grup = {}
    kartsiz = 0.0
    kartsiz_adet = 0
    for d in docs:
        if not d['cari_id']:
            if d['tip'] not in ('IADE', 'ALIS_IADE'):
                kartsiz += d['net']
                kartsiz_adet += 1
            continue
        # Bildirim VKN/TCKN bazındadır: aynı VKN'li birden çok cari kartı (mükerrer kart) tek satırda toplanır; VKN yoksa cari bazında kalır.
        gkey = ('v', re.sub(r'\D', '', str(d['vkn'] or ''))) if vkn_ok(d['vkn']) else ('c', d['cari_id'])
        g = grup.setdefault(gkey, {'unvan': d['unvan'], 'vkn': d['vkn'], 'adet': 0, 'net': 0.0, 'iade': 0.0, 'kdv': 0.0, 'iade_adet': 0, 'cari_id': d['cari_id']})
        if d['tip'] in ('IADE', 'ALIS_IADE'):
            g['iade'] += -d['net']
            g['iade_adet'] += 1
        else:
            g['adet'] += 1
            g['net'] += d['net']
            g['kdv'] += d['kdv']
    rows = []
    uy = []
    for gkey, g in grup.items():
        cid = g['cari_id']
        g['net'] = r2(g['net'])
        g['iade'] = r2(g['iade'])
        g['kdv'] = r2(g['kdv'])
        g['bildirilir'] = g['net'] >= esik
        g['vkn_gecerli'] = vkn_ok(g['vkn'])
        if g['bildirilir']:
            rows.append(g)
            if not g['vkn_gecerli']:
                uy.append('%s: VKN/TCKN eksik veya hatalı (%s). Bildirimde zorunludur; cari kartından düzeltin.' % (g['unvan'] or ('Cari #%s' % cid), g['vkn'] or 'boş'))
    rows.sort(key=lambda x: -x['net'])
    for i, g in enumerate(rows, 1):
        g['sira'] = i
    if kartsiz_adet:
        uy.append('Cari kartı seçilmeden yapılan %d belge (%.2f TL matrah) Ba-Bs dışında kaldı. Perakende fişleri bildirime girmez; '
                  'cari adına fatura kestiyseniz satışta cari seçilmelidir.' % (kartsiz_adet, kartsiz))
    return rows, uy, r2(kartsiz)


# --------------------------------------------------------------------------- 2) KDV özeti
def kdv_ozeti(satis, alis):
    oranlar = set()
    for d in list(satis) + list(alis):
        oranlar.update(d['oran'].keys())
    oranlar = sorted(oranlar)
    rows = []
    for o in oranlar:
        sm = sum(d['oran'].get(o, [0, 0])[0] for d in satis)
        sk = sum(d['oran'].get(o, [0, 0])[1] for d in satis)
        am = sum(d['oran'].get(o, [0, 0])[0] for d in alis)
        ak = sum(d['oran'].get(o, [0, 0])[1] for d in alis)
        rows.append({'oran': o, 'satis_matrah': r2(sm), 'hesaplanan': r2(sk), 'alis_matrah': r2(am), 'indirilecek': r2(ak)})
    tot = {'oran': None}
    for k in ('satis_matrah', 'hesaplanan', 'alis_matrah', 'indirilecek'):
        tot[k] = r2(sum(r[k] for r in rows))
    tot['odenecek'] = r2(tot['hesaplanan'] - tot['indirilecek'])   # + ise ödenecek, - ise devreden
    return rows, tot


# --------------------------------------------------------------------------- 3) muhasebe aktarımı
def _odeme_hesabi(kind, H):
    k = str(kind or '').lower()
    if 'nakit' in k or 'kasa' in k:
        return H['kasa']
    if 'kart' in k or 'pos' in k:
        return H['pos']
    if 'havale' in k or 'eft' in k or 'banka' in k:
        return H['banka']
    if 'çek' in k or 'cek' in k:
        return H['cek']
    if 'senet' in k:
        return H['senet']
    return H['alici']


def fatura_listesi(docs):
    """Düz liste: oran sütunları ayrı. Dönüş: (başlıklar, satırlar)."""
    oranlar = sorted({o for d in docs for o in d['oran']} | {1.0, 10.0, 20.0})
    h = ['Tarih', 'Belge No', 'Tür', 'Cari Ünvan', 'VKN/TCKN', 'Ödeme']
    for o in oranlar:
        h += ['Matrah %%%g' % o, 'KDV %%%g' % o]
    h += ['Toplam Matrah', 'Toplam KDV', 'Genel Toplam']
    rows = []
    for d in docs:
        row = [d['dt'], d['no'], {'SATIS': 'Satış', 'IADE': 'Satış İadesi', 'ALIS': 'Alış', 'ALIS_IADE': 'Alış İadesi'}.get(d['tip'], d['tip']), d['unvan'] or 'Perakende', d['vkn'], d['odeme']]
        for o in oranlar:
            m, k = d['oran'].get(o, [0.0, 0.0])
            row += [m, k]
        row += [d['net'], d['kdv'], d['toplam']]
        rows.append(row)
    return h, rows


def yevmiye(satis, alis, hesaplar=None):
    """Yevmiye fişi taslağı. Her belge = bir fiş; borç = alacak garantilidir."""
    H = dict(HESAPLAR_VARSAYILAN)
    if hesaplar:
        H.update(hesaplar)
    h = ['Fiş No', 'Tarih', 'Hesap Kodu', 'Hesap Adı', 'Açıklama', 'Borç', 'Alacak', 'Belge No']
    rows = []
    fis = 0
    for d in sorted(list(satis) + list(alis), key=lambda x: (x['dt'], x['tip'], str(x['no']))):
        if not d['oran']:
            continue
        fis += 1
        acik = {'SATIS': 'Satış', 'IADE': 'Satış iadesi', 'ALIS': 'Alış', 'ALIS_IADE': 'Alış iadesi'}[d['tip']] + ' ' + str(d['no']) + (' / ' + d['unvan'] if d['unvan'] else '')
        satirlar = []   # (kod, ad, borc, alacak)
        if d['tip'] in ('SATIS', 'IADE'):
            ters = d['tip'] == 'IADE'
            net = {o: v[0] for o, v in d['oran'].items()}
            kdv = {o: v[1] for o, v in d['oran'].items()}
            toplam = r2(sum(net.values()) + sum(kdv.values()))
            # Alacak tarafı (satış) ; iade ise 610 borç / 391 borç / ödeme alacak
            gelir_kod = H['satis_iade'] if ters else H['satis']
            tutar_gelir = abs(r2(sum(net.values())))
            tutar_kdv = abs(r2(sum(kdv.values())))
            toplam = abs(toplam)
            if not ters:
                satirlar.append((gelir_kod[0], gelir_kod[1], 0.0, tutar_gelir))
                if tutar_kdv:
                    satirlar.append((H['hesaplanan_kdv'][0], H['hesaplanan_kdv'][1] + ' ' + '/'.join('%%%g' % o for o in sorted(kdv) if kdv[o]), 0.0, tutar_kdv))
            else:
                satirlar.append((gelir_kod[0], gelir_kod[1], tutar_gelir, 0.0))
                if tutar_kdv:
                    satirlar.append((H['hesaplanan_kdv'][0], H['hesaplanan_kdv'][1], tutar_kdv, 0.0))
            # Tahsilat tarafı
            od = d.get('odemeler') or [(d['odeme'], toplam)]
            toplam_od = r2(sum(abs(a) for _, a in od))
            parcalar = []
            for kind, amt in od:
                parcalar.append([_odeme_hesabi(kind, H), abs(_f(amt))])
            if not parcalar or abs(toplam_od - toplam) > 0.01:
                parcalar = [[_odeme_hesabi(d['odeme'], H), toplam]]
            fark = r2(toplam - sum(p[1] for p in parcalar))
            parcalar[0][1] = r2(parcalar[0][1] + fark)
            for kod, amt in parcalar:
                satirlar.append((kod[0], kod[1], 0.0 if ters else r2(amt), r2(amt) if ters else 0.0))
        elif d['tip'] == 'ALIS_IADE':
            net = abs(r2(sum(v[0] for v in d['oran'].values())))
            kdv = abs(r2(sum(v[1] for v in d['oran'].values())))
            satirlar.append((H['satici'][0], H['satici'][1], r2(net + kdv), 0.0))
            satirlar.append((H['mal'][0], H['mal'][1], 0.0, net))
            if kdv:
                satirlar.append((H['indirilecek_kdv'][0], H['indirilecek_kdv'][1], 0.0, kdv))
        else:
            net = r2(sum(v[0] for v in d['oran'].values()))
            kdv = r2(sum(v[1] for v in d['oran'].values()))
            satirlar.append((H['mal'][0], H['mal'][1], net, 0.0))
            if kdv:
                satirlar.append((H['indirilecek_kdv'][0], H['indirilecek_kdv'][1], kdv, 0.0))
            satirlar.append((H['satici'][0], H['satici'][1], 0.0, r2(net + kdv)))
        for kod, ad, b, a in satirlar:
            rows.append([fis, d['dt'], kod, ad, acik, r2(b), r2(a), d['no']])
    return h, rows


# --------------------------------------------------------------------------- dışa aktarım
def _tr(v):
    if isinstance(v, float):
        return ('%.2f' % v).replace('.', ',')
    return v


def write_csv(path, headers, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(headers)
        for r in rows:
            w.writerow([_tr(x) for x in r])


def write_xlsx_any(path, headers, rows, sheet='Liste'):
    import ALFABE_STOK_EXCEL as X
    X.write_xlsx(path, headers, rows, sheet_name=sheet)


def babs_tablo(rows):
    h = ['Sıra', 'Ünvan', 'VKN/TCKN', 'Ülke', 'Belge Adedi', 'Matrah (KDV Hariç)', 'İade Matrahı', 'KDV']
    return h, [[g['sira'], g['unvan'], g['vkn'], 'TR', g['adet'], g['net'], g['iade'], g['kdv']] for g in rows]


def kdv_tablo(rows, tot):
    h = ['KDV Oranı', 'Satış Matrahı', 'Hesaplanan KDV', 'Alış Matrahı', 'İndirilecek KDV']
    out = [['%%%g' % r['oran'], r['satis_matrah'], r['hesaplanan'], r['alis_matrah'], r['indirilecek']] for r in rows]
    out.append(['TOPLAM', tot['satis_matrah'], tot['hesaplanan'], tot['alis_matrah'], tot['indirilecek']])
    out.append(['ÖDENECEK (+) / DEVREDEN (-) KDV', '', tot['odenecek'], '', ''])
    return h, out
