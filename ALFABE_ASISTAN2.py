# -*- coding: utf-8 -*-
"""ALFABE Asistan 2 (v420): bilgi + analiz + işlem asistanı.

ALFABE_ASISTAN.py'deki mevcut motorun ÜZERİNE katman olarak eklenir (install()). Eski analizler aynen çalışır.

1) BİLGİ ASİSTANI : "X nasıl yapılır / nerede" sorularını ALFABE_ASISTAN_BILGI.py bilgi tabanından yanıtlar,
                    adımları ve "Ekranı aç" düğmesini verir.
2) ANALİZ ASİSTANI: Eski analizlere ek olarak kasa/banka, cari bakiye, gider, ödeme dağılımı, bekleyen havale/POS,
                    "dikkat edilecekler" ve Özel Rapor motoru üzerinden serbest rapor (alan/grup/dönem/karşılaştırma).
3) İŞLEM ASİSTANI : Gider kaydı, hatırlatma, havale onayı, sipariş taslağı gibi işlemleri HAZIRLAR. Hiçbiri kendiliğinden
                    yapılmaz: kullanıcı formu görür, düzeltir ve açıkça onaylar; yetki (role_permissions) kontrol edilir,
                    denetim kaydı yazılır.

Güvenlik: analiz sorguları yalnız SELECT'tir (Engine.rows/one SELECT dışını reddeder).
"""
import datetime
import difflib
import json
import math
import re

D = datetime.date
_A = None          # ALFABE_ASISTAN modülü (install ile verilir)

# ----------------------------------------------------------------------------------------------
# Metin yardımcıları
# ----------------------------------------------------------------------------------------------
_TR = str.maketrans({'İ': 'i', 'I': 'ı'})


def fold(t):
    t = str(t or '').translate(_TR).lower()
    for a, b in (('ı', 'i'), ('ş', 's'), ('ğ', 'g'), ('ü', 'u'), ('ö', 'o'), ('ç', 'c'), ('â', 'a'), ('î', 'i'), ('û', 'u'), ('̇', '')):
        t = t.replace(a, b)
    return t


def words(t):
    return re.findall(r'[a-z0-9]+', fold(t))


STOP = set('bir bu su o ve ile de da ki mi mu icin ne nasil nerede nereden nereye hangi kac gibi daha cok var yok olan olarak bana benim '
           'bize lutfen acaba misin musun mu mi miyim miyiz bunu sunu onu ben biz sen siz yapilir yapabilirim yapabilir yapmaliyim '
           'icinde uzerinde ise ya ama veya hem yani artik simdi sadece tum butun herhangi'.split())


def _wmatch(tok, kw):
    """Soru sözcüğü (tok) bilgi tabanı sözcüğüne (kw) uyuyor mu? Ek tolere edilir, küçük yazım hatası tolere edilir."""
    if tok == kw:
        return True
    if len(kw) <= 3:
        return tok.startswith(kw) and len(tok) <= len(kw) + (5 if len(kw) == 3 else 3)
    if tok.startswith(kw):
        return True
    if len(tok) >= 4 and kw.startswith(tok) and len(tok) >= len(kw) - 3:
        return True
    if len(tok) >= 5 and len(kw) >= 5 and abs(len(tok) - len(kw)) <= 3:
        return difflib.SequenceMatcher(None, tok[:len(kw) + 2], kw).ratio() >= 0.86
    return False


class KBIndex:
    def __init__(self, kb):
        self.kb = kb
        self.entries = []
        df = {}
        for e in kb:
            phrases = [fold(p) for p in e['q']] + [fold(e['title'].rstrip('?'))]
            pw = []
            for p in phrases:
                ws = [w for w in re.findall(r'[a-z0-9]+', p) if w not in STOP]
                if ws:
                    pw.append(ws)
            self.entries.append((e, pw))
            for w in {w for ws in pw for w in ws}:
                df[w] = df.get(w, 0) + 1
        self.n = len(kb)
        self.df = df

    def weight(self, w):
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 0.6

    def score(self, tokens):
        out = []
        for e, pw in self.entries:
            best, extra = 0.0, 0.0
            for ws in pw:
                if all(any(_wmatch(t, w) for t in tokens) for w in ws):
                    sc = sum(self.weight(w) for w in ws) * (1.0 + 0.15 * (len(ws) - 1))
                    if sc > best:
                        extra += best * 0.15
                        best = sc
                    else:
                        extra += sc * 0.15
            if best:
                out.append((best + min(extra, 1.0), e))
        out.sort(key=lambda x: -x[0])
        return out


_IDX = {}


def kb_index():
    if 'i' not in _IDX:
        from ALFABE_ASISTAN_BILGI import KB
        _IDX['i'] = KBIndex(KB)
    return _IDX['i']


HOW_STRONG = re.compile(
    r'nasil (yapilir|yapar|yaparim|yapabilirim|eklenir|ekler|ekleyebilirim|alinir|girilir|acilir|kaydedilir|olusturulur|iptal|silinir|silerim|'
    r'degistirilir|guncellenir|basilir|yazdirilir|tanimlanir|kullanilir|kullanirim|calisir|ayarlanir|gonderilir|alirim|girerim|acarim|'
    r'kaydederim|olustururum|iptal ederim|degistirebilirim|bulurum|bulabilirim|gorurum|gorebilirim)|'
    r'nasil .{0,30}\b(yap|ekle|al|gir|ac|kaydet|olustur|sil|iptal|degistir|guncelle|bas|yazdir|tanimla|kullan|gor|bul|ayarla|gonder|sat|ode|cikar|yaz|duzelt|kapat)\w*|'
    r'\bnerede\b|\bnereden\b|\bnereye\b|\bnerde\b|hangi (menu|ekran|buton|tus|bolum|modul|sayfa)|ne yapmali|ne yapacagim|ne ise yarar|'
    r'(yapabilir|edebilir|alabilir|verebilir|girebilir|kullanabilir|degistirebilir)\w* miyim|nasil (cikar|cikarilir|hesaplanir|gorunur|calisir)|\bnedir\b|\bne demek\b|anlat|adim adim|rehber|ogret|kisayol|yardim')

DATA_CUE = re.compile(
    r'bugun|\bdun\b|bu (hafta|ay|yil)|gecen (hafta|ay|yil)|son \d+|ne kadar|kac (tl|lira|adet|tane|kisi)|toplam|en cok|en az|en fazla|'
    r'listele|kimler|hangileri|karsilastir|ortalama|dagilim|sayisi|\d{4}-\d{2}-\d{2}')

AMOUNT_RX = re.compile(r'(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(bin|k)?\s*(tl|lira|₺|try)?', re.I)


def parse_amount(text):
    """'5.000 TL', '1250,50', '5 bin lira' -> float; bulunamazsa None. Tarih/saat gibi değerleri atlar."""
    s = str(text or '')
    s = re.sub(r'\d{1,2}[./:]\d{1,2}[./:]\d{2,4}', ' ', s)          # tarihler
    s = re.sub(r'\d{1,2}[:.]\d{2}\b(?!\s*(tl|lira))', ' ', s)           # saat
    best = None
    for m in AMOUNT_RX.finditer(s):
        raw, mult, cur = m.group(1), m.group(2), m.group(3)
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?', raw):
            val = float(raw.replace('.', '').replace(',', '.'))
        else:
            val = float(raw.replace(',', '.'))
        if mult:
            val *= 1000
        if val <= 0:
            continue
        if cur or mult:
            return val
        if best is None:
            best = val
    return best


# ----------------------------------------------------------------------------------------------
# Yetki ve yardımcı
# ----------------------------------------------------------------------------------------------
def _can(eng, module, what='view'):
    f = getattr(eng, 'can_do', None)
    if f:
        return f(module, what)
    return eng.can_view(module) if what == 'view' else False


def _fm(v):
    return _A.fmt_money(v)


def _fn(v, d=2):
    return _A.fmt_num(v, d)


def _fd(v):
    return _A.fmt_date(v)


def _money_cols(heads):
    kw = ('tutar', 'ciro', 'maliyet', 'kar', 'bakiye', 'borc', 'alacak', 'deger', 'iskonto', 'kdv', 'net', 'giris', 'cikis')
    return {i for i, h in enumerate(heads) if any(k in fold(h) for k in kw) and 'sayi' not in fold(h) and '%' not in h}


# ----------------------------------------------------------------------------------------------
# ANALİZ: yeni araçlar (Engine metotları)
# ----------------------------------------------------------------------------------------------
class T:
    """Engine'e eklenen t_* araçları (self = Engine)."""

    def cash_status(self):
        rows = self.rows("SELECT COALESCE(account,'') a, COALESCE(SUM(amount),0) b, COUNT(*) n FROM finance GROUP BY COALESCE(account,'') ORDER BY 1")
        kinds = {}
        try:
            for r in self.rows("SELECT name,kind,COALESCE(bank,'') bank FROM finance_accounts WHERE active=1"):
                kinds[r['name']] = (r['kind'], r['bank'])
        except Exception:
            pass
        res = self.new('Kasa ve banka durumu', 'Şu anki bakiyeler', sensitive=[])
        cash = bank = pos = 0.0
        out = []
        for r in rows:
            a, b = r['a'], float(r['b'])
            if a in ('Nakit', 'Kasa', 'Ana Kasa'):
                kind = 'Kasa'
            elif a in ('Kart/POS', 'POS', 'Kart / POS'):
                kind = 'POS (bekleyen)'
            elif kinds.get(a, ('', ''))[0] == 'Banka' or 'banka' in fold(a):
                kind = 'Banka'
            else:
                kind = kinds.get(a, ('Diğer', ''))[0] or 'Diğer'
            if kind == 'Kasa':
                cash += b
            elif kind == 'Banka':
                bank += b
            elif kind.startswith('POS'):
                pos += b
            out.append((kind, a, b, r['n']))
        order = {'Kasa': 0, 'Banka': 1, 'POS (bekleyen)': 2}
        out.sort(key=lambda x: (order.get(x[0], 9), x[1]))
        # kasa hesapları tek satırda birleşsin
        merged = []
        kasa_done = False
        for kind, a, b, n in out:
            if kind == 'Kasa':
                if kasa_done:
                    continue
                kasa_done = True
                merged.append(('Kasa', 'Kasa (Nakit / Ana Kasa)', cash, sum(x[3] for x in out if x[0] == 'Kasa')))
            else:
                merged.append((kind, a, b, n))
        res['columns'] = ['Tür', 'Hesap', 'Bakiye', 'Hareket sayısı']
        res['rows'] = [[k, a, _fm(b), n] for k, a, b, n in merged]
        res['num_cols'] = [2, 3]
        try:
            pend = self.one("SELECT COALESCE(SUM(net),0) t FROM pos_settlements WHERE status='Bekliyor'")
            pend = float(pend['t'] or 0) if pend else 0.0
        except Exception:
            pend = 0.0
        res['summary'] = [f'Kasada: {_fm(cash)}', f'Bankada: {_fm(bank)}', f'Toplam (kasa + banka): {_fm(cash + bank)}']
        if pend:
            res['summary'].append(f'Bankaya geçmesi beklenen kart (POS) tutarı: {_fm(pend)}')
        td = datetime.date.today().isoformat()
        src = self.rows("SELECT dt,account,kind,amount,note FROM finance WHERE substr(dt,1,10)=? ORDER BY id DESC LIMIT 100", (td,))
        res['src_title'] = 'Bugünkü finans hareketleri'
        res['src_columns'] = ['Saat', 'Hesap', 'İşlem', 'Tutar', 'Not']
        res['src_rows'] = [[str(x['dt'])[11:16], x['account'] or '', x['kind'] or '', _fm(x['amount']), x['note'] or ''] for x in src]
        res['src_num_cols'] = [3]
        res['notes'].append('Bakiye = hesabın tüm finans hareketlerinin toplamı (giriş − çıkış). Banka satırları Banka Merkezi\'ndeki hesaplardır.')
        return res

    def _find_cari(self, text):
        """Soru metnindeki cari unvanını bulur: [(id,title,score)]."""
        t = fold(text)
        tw = set(re.findall(r'[a-z0-9]+', t))
        generic = {'ltd', 'sti', 'san', 'tic', 'as', 'a', 's', 've', 'limited', 'sirketi', 'ticaret', 'sanayi', 'kirtasiye', 'market', 'oto', 'gida', 'insaat', 'bey', 'hanim'}
        out = []
        for r in self.rows("SELECT id,title FROM caris WHERE COALESCE(active,1)=1"):
            ws = [w for w in re.findall(r'[a-z0-9]+', fold(r['title'])) if w not in generic and len(w) >= 2]
            if not ws:
                continue
            hit = [w for w in ws if any(_wmatch(x, w) and len(x) >= 3 for x in tw if x not in STOP)]
            if len(ws) == 1 and hit and len(ws[0]) >= 4:
                out.append((r['id'], r['title'], 1.0))
            elif len(hit) >= 2:
                out.append((r['id'], r['title'], len(hit) / len(ws)))
            elif len(ws) >= 2 and len(hit) == 1 and len(hit[0]) >= 5 and len(ws) == 2:
                out.append((r['id'], r['title'], 0.5))
        out.sort(key=lambda x: -x[2])
        return out

    def cari_balance(self, name=''):
        res = self.new('Cari bakiye', name, sensitive=['Cari', 'Telefon'])
        if not str(name).strip():
            res['summary'] = ['Hangi carinin bakiyesini görmek istediğinizi yazın (ör. "Ahmet Yılmaz\'ın borcu ne kadar?").']
            return res
        found = self._find_cari(name)
        if not found:
            res['summary'] = [f"'{name}' ile eşleşen cari bulunamadı. Unvanı biraz daha eksiksiz yazmayı deneyin."]
            return res
        top = found[0][2]
        cands = [f for f in found if f[2] >= top - 0.01][:6]
        if len(cands) > 1:
            ph = ','.join('?' * len(cands))
            rs = self.rows(f"SELECT c.id,c.title,COALESCE(SUM(m.debit)-SUM(m.credit),0) bal FROM caris c LEFT JOIN cari_moves m ON m.cari_id=c.id WHERE c.id IN ({ph}) GROUP BY c.id ORDER BY c.title", tuple(x[0] for x in cands))
            res['title'] = 'Birden fazla cari eşleşti'
            res['summary'] = ['Hangisini kastettiniz? Soruyu cari unvanıyla tekrar yazın:'] + [f"• {r['title']} — bakiye {_fm(r['bal'])}" for r in rs]
            res['columns'] = ['Cari', 'Bakiye']
            res['rows'] = [[r['title'], _fm(r['bal'])] for r in rs]
            res['num_cols'] = [1]
            return res
        cid, title = found[0][0], found[0][1]
        b = self.one("SELECT COALESCE(SUM(debit),0) d,COALESCE(SUM(credit),0) c,COUNT(*) n,MAX(substr(dt,1,10)) last FROM cari_moves WHERE cari_id=?", (cid,))
        d, c_, n = float(b['d']), float(b['c']), int(b['n'])
        bal = d - c_
        res['title'] = f'Cari bakiye — {title}'
        res['period'] = 'Güncel'
        if abs(bal) < 0.005:
            res['summary'] = [f'{title} carisinin bakiyesi sıfır.']
        elif bal > 0:
            res['summary'] = [f'{title} carisinin BORCU (bizim alacağımız): {_fm(bal)}']
        else:
            res['summary'] = [f'{title} carisine BORCUMUZ: {_fm(-bal)}']
        res['summary'].append(f'Toplam borç hareketi {_fm(d)}, toplam alacak (tahsilat/ödeme) {_fm(c_)}; {n} hareket' + (f', son hareket {_fd(b["last"])}.' if b['last'] else '.'))
        try:
            ins = self.one("SELECT COALESCE(SUM(amount-paid),0) o, COUNT(*) n FROM installments i JOIN sales s ON s.id=i.sale_id WHERE s.customer_id=? AND COALESCE(i.status,'') NOT IN ('Ödendi','İptal','İade') AND i.amount-i.paid>0.0001", (cid,))
            if ins and int(ins['n']) > 0:
                res['summary'].append(f"Açık taksit/vade: {int(ins['n'])} kayıt, {_fm(ins['o'])}.")
        except Exception:
            pass
        rows = self.rows("SELECT dt,kind,debit,credit,note FROM cari_moves WHERE cari_id=? ORDER BY dt DESC,id DESC LIMIT 30", (cid,))
        res['columns'] = ['Tarih', 'Tür', 'Borç', 'Alacak', 'Not']
        res['rows'] = [[_fd(r['dt']), r['kind'] or '', _fm(r['debit']), _fm(r['credit']), r['note'] or ''] for r in rows]
        res['num_cols'] = [2, 3]
        res['src_title'] = 'Kaynak: cari hareketleri (son 30)'
        res['src_columns'], res['src_rows'], res['src_num_cols'] = res['columns'], res['rows'], [2, 3]
        res['notes'].append('Bakiye = borç hareketleri − alacak hareketleri. Tam döküm için Cari Merkezi > CARİ EKSTRE.')
        res['actions'] = [dict(type='goto', label='Cari Merkezi\'ni aç', module='Cari Merkezi')]
        return res

    def expenses(self, period='bu ay', category=''):
        p = _A.parse_period(period)
        a, b = p.iso()
        res = self.new('Giderler', p.label)
        cond = "status='Aktif' AND substr(dt,1,10) BETWEEN ? AND ?"
        params = [a, b]
        if category:
            cond += " AND lower(category) LIKE ?"
            params.append('%' + str(category).lower() + '%')
        rows = self.rows(f"SELECT category,COUNT(*) n,SUM(amount) t FROM expense_records WHERE {cond} GROUP BY category ORDER BY t DESC", tuple(params))
        tot = sum(float(r['t'] or 0) for r in rows)
        if not rows:
            res['summary'] = ['Bu dönemde kayıtlı gider bulunmuyor.']
            return res
        res['summary'] = [f'Toplam gider: {_fm(tot)} ({sum(int(r["n"]) for r in rows)} kayıt).',
                          f"En yüksek kategori: {rows[0]['category']} — {_fm(rows[0]['t'])} (%{_fn(float(rows[0]['t']) / tot * 100 if tot else 0, 1)})."]
        res['columns'] = ['Kategori', 'Kayıt', 'Tutar', 'Pay %']
        res['rows'] = [[r['category'], r['n'], _fm(r['t']), _fn(float(r['t']) / tot * 100 if tot else 0, 1)] for r in rows]
        res['num_cols'] = [1, 2, 3]
        src = self.rows(f"SELECT dt,category,account,amount,note FROM expense_records WHERE {cond} ORDER BY dt DESC,id DESC LIMIT 300", tuple(params))
        res['src_title'] = 'Kaynak gider kayıtları'
        res['src_columns'] = ['Tarih', 'Kategori', 'Hesap', 'Tutar', 'Not']
        res['src_rows'] = [[_fd(x['dt']), x['category'], x['account'] or '', _fm(x['amount']), x['note'] or ''] for x in src]
        res['src_num_cols'] = [3]
        res['notes'].append('İptal edilmiş giderler dahil değildir.')
        return res

    def payment_mix(self, period='bu ay'):
        p = _A.parse_period(period)
        a, b = p.iso()
        res = self.new('Ödeme türü dağılımı', p.label)
        rows = self.rows("""SELECT sp.kind k,COALESCE(SUM(sp.amount),0) t,COUNT(DISTINCT sp.sale_id) n FROM sale_payments sp JOIN sales s ON s.id=sp.sale_id
                            WHERE s.status IN ('FINAL','PARTIAL_RETURN','RETURNED','RETURN') AND substr(s.dt,1,10) BETWEEN ? AND ? GROUP BY sp.kind ORDER BY t DESC""", (a, b))
        if not rows:
            res['summary'] = ['Bu dönemde ödeme kaydı yok.']
            return res
        tot = sum(float(r['t']) for r in rows)
        res['summary'] = [f"Net tahsilat: {_fm(tot)}"] + [f"• {r['k']}: {_fm(r['t'])} (%{_fn(float(r['t']) / tot * 100 if tot else 0, 1)})" for r in rows]
        res['columns'] = ['Ödeme türü', 'Belge', 'Net tutar', 'Pay %']
        res['rows'] = [[r['k'], r['n'], _fm(r['t']), _fn(float(r['t']) / tot * 100 if tot else 0, 1)] for r in rows]
        res['num_cols'] = [1, 2, 3]
        try:
            pend = self.one("SELECT COUNT(*) n,COALESCE(SUM(amount),0) t FROM sale_payments WHERE kind='Havale/IBAN' AND amount>0 AND COALESCE(verified,0)=0")
            if pend and int(pend['n']):
                res['notes'].append(f"Havale/IBAN'ın {int(pend['n'])} kaydı ({_fm(pend['t'])}) henüz bankada görüldü olarak işaretlenmemiş (Banka Merkezi > IBAN KONTROL).")
        except Exception:
            pass
        res['notes'].append('İadeler ilgili ödeme türünden düşülmüştür. Tutarlar sale_payments kayıtlarından gelir.')
        return res

    def iban_pending(self):
        res = self.new('Bankada doğrulanmamış havaleler', 'Güncel', sensitive=['Müşteri'])
        rows = self.rows("""SELECT s.doc_no,substr(s.dt,1,10) d,COALESCE(ca.title,'Perakende') cu,p.amount,COALESCE(p.bank_account,'') acc,COALESCE(p.ref,'') ref
                            FROM sale_payments p JOIN sales s ON s.id=p.sale_id LEFT JOIN caris ca ON ca.id=s.customer_id
                            WHERE p.kind='Havale/IBAN' AND p.amount>0 AND COALESCE(p.verified,0)=0 AND COALESCE(s.status,'FINAL')<>'CANCEL' ORDER BY s.dt""")
        if not rows:
            res['summary'] = ['Doğrulanmamış havale yok: tüm Havale/IBAN tahsilatları bankada görüldü olarak işaretli.']
            return res
        tot = sum(float(r['amount']) for r in rows)
        res['summary'] = [f'{len(rows)} havale henüz bankada doğrulanmadı: toplam {_fm(tot)}.', 'Banka uygulamanızda parayı görünce Banka Merkezi > IBAN KONTROL\'den onaylayın.']
        res['columns'] = ['Belge', 'Tarih', 'Müşteri', 'Tutar', 'Banka hesabı', 'Açıklama']
        res['rows'] = [[r['doc_no'], _fd(r['d']), r['cu'], _fm(r['amount']), r['acc'], r['ref']] for r in rows]
        res['num_cols'] = [3]
        res['actions'] = [dict(type='iban_verify', label='Bunları onayla…', ids=None, module='Banka Merkezi'), dict(type='goto', label='IBAN KONTROL\'e git', module='Banka Merkezi')]
        return res

    def pos_pending(self):
        res = self.new('Bekleyen POS (kart) yatışları', 'Güncel')
        try:
            rows = self.rows("SELECT dt,pos_name,gross,commission,net,due_date FROM pos_settlements WHERE status='Bekliyor' ORDER BY due_date,pos_name")
        except Exception:
            rows = []
        if not rows:
            res['summary'] = ['Bankaya geçmesi beklenen POS satışı yok.']
            return res
        td = datetime.date.today().isoformat()
        due = [r for r in rows if str(r['due_date']) <= td]
        res['summary'] = [f"Bekleyen toplam: {_fm(sum(float(r['net']) for r in rows))} ({len(rows)} günlük kesim).",
                          (f"Yatış günü gelmiş/geçmiş: {len(due)} kesim, {_fm(sum(float(r['net']) for r in due))} — bankanızda kontrol edin." if due else 'Yatış günü gelen kesim yok.')]
        res['columns'] = ['Satış günü', 'POS', 'Brüt', 'Komisyon', 'Net', 'Beklenen yatış']
        res['rows'] = [[_fd(r['dt']), r['pos_name'] or '', _fm(r['gross']), _fm(r['commission']), _fm(r['net']), _fd(r['due_date'])] for r in rows]
        res['num_cols'] = [2, 3, 4]
        res['actions'] = [dict(type='goto', label='POS TAKİP için Banka Merkezi\'ni aç', module='Banka Merkezi')]
        return res

    def attention(self):
        """Bugün dikkat edilecekler: Uyarı Merkezi ile AYNI kaynak (ALFABE_UYARI.collect)."""
        res = self.new('Bugün dikkat edilecekler', 'Güncel')
        try:
            import ALFABE_UYARI as U
        except Exception:
            res['summary'] = ['Uyarı modülü yüklenemedi.']
            return res
        al = [a for a in U.collect(_Conn(self), back_dir=getattr(self, 'back_dir', None)) if not a.get('module') or self.can_view(a['module'])]
        bad = [a for a in al if a['level'] in ('KRİTİK', 'ÖNEMLİ')]
        if not bad:
            res['summary'] = ['Şu an dikkat gerektiren bir durum görünmüyor.']
            return res
        nk = sum(1 for a in bad if a['level'] == 'KRİTİK')
        res['summary'] = [f'{nk} kritik, {len(bad) - nk} önemli uyarı var. Önce kritik olanlara bakın:'] + [f"• [{a['level']}] {a['title']}: {a['detail']}" for a in bad[:8]]
        res['columns'] = ['Seviye', 'Uyarı', 'Detay', 'Vade', 'Bölüm']
        res['rows'] = [[a['level'], a['title'], a['detail'], a.get('due_note', ''), a.get('module', '')] for a in bad]
        res['notes'].append('Bu liste Akıllı Uyarı & İş Takip Merkezi ile aynı kaynaktan gelir; ayrıntı için Uyarılar ekranına bakın.')
        acts, seen = [], set()
        for a in bad:
            m = a.get('module')
            if m and m not in seen and len(acts) < 3:
                seen.add(m)
                acts.append(dict(type='goto', label=m + ' ekranını aç', module=m))
        res['actions'] = acts
        return res

    # ---- bilgi / işlem hazırlama (LLM ve yerel akış ortak kullanır)
    def howto(self, query=''):
        return knowledge_result(self, query, fold(query))

    def prepare_action(self, kind='', text=''):
        return prepare_action(self, kind, text)

    # ---- serbest rapor (Özel Rapor motoru)
    def custom_report(self, domain='Satış', group_by='', measures='', period='', compare='', filter='', limit=0, sort='', calc=''):
        return custom_report(self, dict(domain=domain, group_by=group_by, measures=measures, period=period, compare=compare, filter=filter, limit=limit, sort=sort, calc=calc))


class _Conn:
    """Özel Rapor / Günlük Özet fonksiyonlarına Engine'in SELECT-only erişimini sqlite bağlantısı gibi gösterir."""
    class _Cur:
        def __init__(self, rows):
            self._r = rows

        def fetchall(self):
            return self._r

        def fetchone(self):
            return self._r[0] if self._r else None

        def __iter__(self):
            return iter(self._r)

    def __init__(self, eng):
        self.eng = eng

    def execute(self, sql, params=()):
        return _Conn._Cur(self.eng.rows(sql, tuple(params)))

    def commit(self):
        pass

    def close(self):
        pass


# ----------------------------------------------------------------------------------------------
# BİLGİ yanıtı
# ----------------------------------------------------------------------------------------------
def kb_find(question, limit=3):
    toks = [w for w in words(question) if w not in STOP]
    if not toks:
        return []
    return kb_index().score(toks)[:limit]


KB_MIN = 1.6          # tek başına yeterli güven
KB_OK = 2.4           # nasıl-sorusunda yeterli


def knowledge_result(eng, question, t=None, scored=None):
    scored = scored if scored is not None else kb_find(question)
    res = eng.new('Nasıl yapılır', '')
    if not scored:
        res['title'] = 'Bu konuda bilgim yok'
        res['summary'] = ['Bu konuyu bilgi tabanımda bulamadım. Konuyu başka sözcüklerle yazın ya da F12 (Yardım) tuşuna basın.']
        res['suggest'] = suggestions(question)
        return res
    sc, e = scored[0]
    res['title'] = e['title']
    res['summary'] = [f'{i}. {s}' for i, s in enumerate(e['steps'], 1)]
    if e.get('tip'):
        res['notes'].append(e['tip'])
    can_open = eng.can_view(e['module']) if e.get('module') else True
    acts = []
    if e.get('module'):
        if can_open:
            acts.append(dict(type='goto', label=f"{e['module']} ekranını aç", module=e['module']))
        else:
            res['notes'].append(f"{e['module']} bölümünü görme yetkiniz yok; yönetici yetki verebilir.")
    res['actions'] = acts
    others = [x for x in scored[1:3] if x[0] >= sc * 0.8 and x[1]['id'] != e['id']]
    if others:
        res['suggest'] = [x[1]['title'] for x in others]
        res['notes'].append('Başka bir konuyu kastettiyseniz aşağıdaki önerilere bakın.')
    res['columns'], res['rows'] = [], []
    res['kb'] = e['id']
    return res


def suggestions(question, n=4):
    """Anlaşılamayan soru için en yakın bilgi başlıkları ve örnek sorular."""
    out = []
    for sc, e in kb_find(question, 3):
        out.append(e['title'])
    from ALFABE_ASISTAN_BILGI import EXAMPLES_ANALIZ, EXAMPLES_BILGI, EXAMPLES_ISLEM
    pool = EXAMPLES_ANALIZ + EXAMPLES_BILGI + EXAMPLES_ISLEM
    qw = set(words(question))
    ranked = sorted(pool, key=lambda s: -len(qw & set(words(s))))
    for s in ranked:
        if s not in out:
            out.append(s)
        if len(out) >= n:
            break
    return out[:n]


# ----------------------------------------------------------------------------------------------
# SERBEST RAPOR: soru -> Özel Rapor tanımı
# ----------------------------------------------------------------------------------------------
DOMAIN_NEEDS = {
    'Satış': ['Rapor & Analiz', 'Hızlı Satış', 'Cari Satış'],
    'Stok': ['Stok Merkezi', 'Alış & Mal Kabul', 'Rapor & Analiz'],
    'Cari': ['Cari Merkezi', 'Finans Merkezi', 'Rapor & Analiz'],
    'Finans': ['Finans Merkezi', 'Kasa Merkezi', 'Banka Merkezi', 'Rapor & Analiz'],
}

DOMAIN_WORDS = {
    'Satış': r'satis|satti|satilan|satan|aldi\b|sattim|ciro|hasilat|iskonto|\bfis\b|kasiyer|kar\b|karim|kazanc|marj|musteri sayisi',
    'Stok': r'stok|envanter|depo|sermaye|\braf\b|urun cesidi|stok degeri|eldeki|elimdeki',
    'Cari': r'cari|bakiye|borc|alacak|musteri bakiye|tedarikci borc',
    'Finans': r'kasa|banka|giris|cikis|gelir|finans|hareket|nakit akis',
}

MEASURE_WORDS = {
    'Satış': [('kar', r'\bkar\b|karim|kari\b|kazanc|brut kar'), ('ciro', r'ciro|hasilat|satis tutar|satis|sattim|satilan|satan|aldi\b|tutar'), ('adet', r'adet|kac tane|miktar|kac urun'),
              ('maliyet', r'maliyet'), ('iskonto', r'iskonto|indirim'), ('fis', r'fis sayi|kac fis|islem sayi|musteri sayi|kac musteri'), ('kdv', r'\bkdv')],
    'Stok': [('maliyet_deger', r'stok deger|sermaye|maliyet deger|elimdeki mal|stok maliyet'), ('satis_deger', r'satis deger'), ('adet', r'adet|miktar|kac tane|stok adedi'),
             ('cesit', r'cesit|kac urun|kac kalem'), ('min_alti', r'kritik|min altinda|minimum')],
    'Cari': [('bakiye', r'bakiye'), ('borc', r'\bborc'), ('alacak', r'alacak'), ('hareket_sayi', r'hareket sayi')],
    'Finans': [('giris', r'giris|gelir|tahsilat'), ('cikis', r'cikis|gider|odeme'), ('net', r'\bnet\b|fark'), ('islem', r'islem sayi')],
}

DIM_WORDS = {
    'Satış': [('grup', r'grup|grub|kategori'), ('marka', r'marka'), ('urun', r'urun(?!\s*grub)|kalem(?!\s*grub)|stok adi'), ('musteri', r'musteri|cari'),
              ('kullanici', r'kasiyer|kullanici|personel|calisan'), ('tarih', r'\bgun\w*\s*(bazinda|gore|bazli)|gunluk|her gun|tarih'), ('ay', r'\bay\w*\s*(bazinda|gore|bazli)|aylik|her ay'),
              ('odeme', r'odeme (turu|tipi|sekli)|odeme\w* gore|nakit.*kart'), ('tur', r'satis turu|perakende.*cari')],
    'Stok': [('grup', r'grup|grub|kategori'), ('marka', r'marka'), ('urun', r'urun(?!\s*grub)|stok adi|kalem'), ('depo', r'depo'), ('birim', r'birim')],
    'Cari': [('unvan', r'cari|musteri|tedarikci'), ('grup', r'grup|grub'), ('sehir', r'sehir|\bil\b'), ('hareket', r'hareket turu'), ('ay', r'\bay\w*\s*(bazinda|gore|bazli)|aylik')],
    'Finans': [('hesap', r'hesap'), ('tur', r'hareket turu|islem turu|tur\w* gore'), ('kaynak', r'kaynak'), ('tarih', r'\bgun\w*\s*(bazinda|gore|bazli)|gunluk|her gun'), ('ay', r'\bay\w*\s*(bazinda|gore|bazli)|aylik')],
}
GROUP_CUE = re.compile(r'aylik|gunluk|\bgore\b|bazinda|bazli|dagilim|kirilim|\bher\b|ayri ayri|en cok|en az|en fazla|en iyi|en kotu|hangi|siralama|liste|\w+lar\b|\w+ler\b')

_MEAS_FIELD = {'ciro': 'Net Satış Tutarı', 'kar': 'Brüt Kâr', 'adet': 'Adet'}


def _plan_custom(eng, question, t):
    """Soru -> custom_report argümanları (dict) ya da None."""
    scores = {d: len(re.findall(rx, t)) for d, rx in DOMAIN_WORDS.items()}
    dom = max(scores, key=lambda d: (scores[d], d == 'Satış'))
    # ölçü sözcüğü bu alanda yoksa diğer alanlara bak
    def meas_in(domain):
        out = []
        for key, rx in MEASURE_WORDS[domain]:
            if re.search(rx, t):
                out.append(key)
        return out
    meas = meas_in(dom)
    if not meas:
        for d2 in DOMAIN_WORDS:
            if d2 != dom and scores[d2] and meas_in(d2):
                dom, meas = d2, meas_in(d2)
                break
    if scores[dom] == 0 and not meas:
        return None
    # ölçü önceliği: "kar" varsa ciro sözcüğü ('satış kârı') ayrıca eklenmesin
    if dom == 'Satış':
        if 'kar' in meas and 'ciro' in meas and not re.search(r'ciro|hasilat', t):
            meas.remove('ciro')
        if 'ciro' in meas and 'adet' in meas and not re.search(r'ciro|hasilat|tutar|tl|lira', t):
            meas.remove('ciro')
    dims = []
    cue = bool(GROUP_CUE.search(t))
    if cue:
        for key, rx in DIM_WORDS[dom]:
            if re.search(rx, t):
                dims.append(key)
    # "bu ay" / "geçen ay" ifadelerindeki 'ay' sözcüğü ay-bazlı gruplama sayılmasın
    if 'ay' in dims and not re.search(r'\bay\w*\s*(bazinda|gore|bazli)|aylik|her ay', t):
        dims.remove('ay')
    if 'tarih' in dims and not re.search(r'\bgun\w*\s*(bazinda|gore|bazli)|gunluk|her gun', t):
        dims.remove('tarih')
    # "kasa" ile "musteri/cari" çakışması: Cari alanında 'cari' dim, Satış'ta müşteri
    if dom == 'Satış' and 'musteri' in dims and re.search(r'musteri sayi|kac musteri', t):
        dims.remove('musteri')
    # çok genel sözcükler yüzünden yanlış gruplama olmasın: 'urun' yalnızca ürün odaklı ifadede
    if len(dims) > 2:
        dims = dims[:2]
    if not meas:
        if dom == 'Satış':
            meas = ['ciro']
        elif dom == 'Stok':
            meas = ['adet', 'maliyet_deger']
        elif dom == 'Cari':
            meas = ['bakiye']
        else:
            meas = ['giris', 'cikis', 'net']
    if dom == 'Satış' and len(meas) == 1 and meas[0] in ('ciro', 'kar') and dims and re.search(r'adet|kac tane', t) is None and 'urun' in dims:
        meas = meas + ['adet']
    args = dict(domain=dom, group_by=','.join(dims), measures=','.join(meas))
    if re.search(r'marj|kar orani|karlilik', t) and dom == 'Satış':
        args['calc'] = 'Kâr Marjı %=kar / ciro * 100'
        if 'kar' not in meas:
            args['measures'] += ',ciro,kar' if 'ciro' not in meas else ',kar'
    # dönem
    try:
        hp = _A.Engine._has_period(t)
    except Exception:
        hp = False
    if hp:
        args['period'] = question
    elif dom in ('Satış', 'Finans'):
        args['period'] = 'bu ay'
    elif dom == 'Cari' and dims and 'ay' in dims:
        args['period'] = 'bu yıl'
    else:
        args['period'] = 'tümü'
    # karşılaştırma
    if re.search(r'karsilastir|\bgore (nasil|fark|ne)|onceki donem|artti|azaldi|degisim|ayni donem|gecen (ay|yil|hafta)\w* gore|dune gore', t):
        args['compare'] = 'yil' if re.search(r'gecen yil|ayni donem|yilin|yillik', t) else 'onceki'
        if not hp:
            args['period'] = 'bu ay'
    # sıralama / limit
    m = re.search(r'(?:ilk|en cok|en fazla|en iyi|top)\s*(\d{1,3})\b', t)
    if m:
        args['limit'] = int(m.group(1))
    elif re.search(r'en cok|en fazla|en iyi|en yuksek|en karli', t):
        args['limit'] = 10
    if re.search(r'en az|en dusuk|en kotu', t):
        args['sort'] = 'asc'
        args.setdefault('limit', 10)
    return args


def _dim_key(domain_defs, token):
    return token


def _resolve_period(txt):
    f = fold(txt or '')
    if not f or f in ('tumu', 'hepsi', 'tum zamanlar'):
        return 'Tümü', '', ''
    p = _A.parse_period(txt)
    return 'Özel Tarih', p.start.strftime('%d.%m.%Y'), p.end.strftime('%d.%m.%Y')


def _value_filters(eng, domain, t):
    """Sorudaki ürün grubu / marka / müşteri / kullanıcı adlarını (veritabanındaki gerçek değerlerle) bulur."""
    import ALFABE_OZEL_RAPOR as O
    dom = O.DOMAINS[domain]
    out = []
    cands = {'Satış': ['grup', 'marka', 'musteri', 'kullanici', 'odeme'], 'Stok': ['grup', 'marka', 'depo'], 'Cari': ['grup', 'sehir'], 'Finans': ['hesap']}.get(domain, [])
    conn = _Conn(eng)
    tw = set(re.findall(r'[a-z0-9]+', t))
    for k in cands:
        if k not in dom['dims']:
            continue
        try:
            vals = O.distinct_values(conn, domain, k, 400)
        except Exception:
            vals = []
        for v in vals:
            fv = fold(v)
            vw = [w for w in re.findall(r'[a-z0-9]+', fv) if w not in STOP]
            if not vw or fv.startswith('('):
                continue
            if k == 'odeme' and fv in ('parcali odeme',):
                continue
            if all(any(_wmatch(x, w) and len(x) >= 3 for x in tw) for w in vw) and len(fv) >= 3:
                out.append((k, v))
                break
    return out


def custom_report(eng, a):
    import ALFABE_OZEL_RAPOR as O
    domain = a.get('domain') or 'Satış'
    if domain not in O.DOMAINS:
        domain = next((d for d in O.DOMAINS if fold(d) == fold(domain)), 'Satış')
    res = eng.new('Rapor', '')
    if not any(eng.can_view(m) for m in DOMAIN_NEEDS[domain]):
        return eng.denied('custom_report')
    dom = O.DOMAINS[domain]
    lab2key = {}
    for k, v in dom['dims'].items():
        lab2key[fold(v[0])] = k
        lab2key[k] = k
    for k, v in dom['measures'].items():
        lab2key[fold(v[0])] = k
        lab2key[k] = k

    def keys(s):
        out = []
        for part in re.split(r'[,;]', str(s or '')):
            p = fold(part).strip()
            if p in lab2key and lab2key[p] not in out:
                out.append(lab2key[p])
        return out
    dims = [k for k in keys(a.get('group_by')) if k in dom['dims']]
    meas = [k for k in keys(a.get('measures')) if k in dom['measures']]
    if not meas:
        meas = [list(dom['measures'])[0]]
    calcs, fields_extra = [], []
    if a.get('calc'):
        for i, part in enumerate(str(a['calc']).split(';')):
            if '=' in part:
                lab, fm = part.split('=', 1)
                try:
                    O.validate_formula(domain, fm.strip(), [c['key'] for c in calcs])
                except Exception as ex:
                    res['summary'] = ['Hesaplanan alan formülü geçersiz: ' + str(ex)]
                    return res
                calcs.append(dict(key='h%d' % (i + 1), label=lab.strip(), formula=fm.strip()))
                fields_extra.append('h%d' % (i + 1))
    per, s, e = _resolve_period(a.get('period') or '')
    if dom['date'] is None:
        per, s, e = 'Tümü', '', ''
    cmpm = fold(a.get('compare') or '')
    cmpm = 'yil' if cmpm.startswith('yil') else ('onceki' if cmpm else '')
    if per == 'Tümü':
        cmpm = ''
    filters = []
    for part in str(a.get('filter') or '').split(';'):
        if '=' in part:
            fk, fv = part.split('=', 1)
            k = lab2key.get(fold(fk).strip())
            if k in dom['dims'] and fv.strip():
                filters.append([k, fv.strip()])
    defn = O.new_definition(domain)
    defn.update(fields=dims + meas + fields_extra, summarize=True, period=per, start=s, end=e, compare=cmpm, filters=filters, calcs=calcs, name='Asistan Raporu')
    srt = fold(a.get('sort') or '')
    lim = int(a.get('limit') or 0)
    first_meas = (meas + fields_extra)[0] if (meas + fields_extra) else ''
    if dims and first_meas:
        defn['sort'] = first_meas
        defn['desc'] = srt != 'asc'
    try:
        r = O.run(_Conn(eng), defn)
    except ValueError as ex:
        res['summary'] = [str(ex)]
        return res
    heads, rows, total = r['heads'], list(r['rows']), r['total']
    dom_title = {'Satış': 'Satış', 'Stok': 'Stok', 'Cari': 'Cari', 'Finans': 'Finans'}[domain]
    cat = {k: l for k, l, _kd in O.field_catalog(defn)}
    title = (', '.join(cat[k] for k in meas) + (' — ' + ' / '.join(cat[k] for k in dims) + ' bazında' if dims else ''))
    out = eng.new(title, r['period_text'])
    shown = rows[:lim] if lim else rows
    mc = _money_cols(heads)
    ratio_cols = {i for i, h in enumerate(heads) if '%' in h}

    def f(i, v):
        if v == '' or v is None:
            return ''
        if isinstance(v, (int, float)):
            if i in ratio_cols:
                return _fn(v, 2)
            if i in mc:
                return _fm(v)
            return _fn(v, 2)
        return _A.fmt_date(v) if (isinstance(v, str) and len(v) == 10 and v[4] == '-' and v[7] == '-') else str(v)
    out['columns'] = list(heads)
    out['rows'] = [[f(i, v) for i, v in enumerate(row)] for row in shown]
    out['num_cols'] = [i for i in range(len(heads)) if i >= len(dims)]
    # özet cümleleri
    sm = []
    nd = len(dims)
    first_m_idx = nd
    if total:
        parts = [f"{heads[i]}: {f(i, total[i])}" for i in range(nd, min(len(total), nd + len(meas) + len(fields_extra))) if total[i] != '']
        sm.append('Toplam — ' + '; '.join(parts))
    if dims and rows:
        top = rows[0]
        sm.append(f"En yüksek: {' / '.join(str(x) for x in top[:nd])} — {heads[first_m_idx]} {f(first_m_idx, top[first_m_idx])}")
        if len(rows) > 1:
            lo = rows[-1]
            sm.append(f"En düşük: {' / '.join(str(x) for x in lo[:nd])} — {f(first_m_idx, lo[first_m_idx])}")
    if cmpm and total:
        ci = [i for i, h in enumerate(heads) if h.endswith('Değişim %')]
        if ci and total[ci[0]] != '':
            sm.append(f"Karşılaştırma ({heads[ci[0]].replace(' Değişim %', '')}): {'+' if total[ci[0]] > 0 else ''}{_fn(total[ci[0]], 1)}% değişim.")
    if not rows:
        sm = ['Bu koşullarda kayıt bulunamadı.']
    out['summary'] = sm
    if lim and len(rows) > lim:
        out['notes'].append(f'İlk {lim} satır gösterildi (toplam {len(rows)}); toplam satırı tüm kayıtları kapsar.')
    if filters:
        out['notes'].append('Uygulanan filtre: ' + ', '.join(f'{dom["dims"][k][0]} = {v}' for k, v in filters))
    if r.get('note'):
        out['notes'].append(r['note'])
    out['src_title'] = 'Tüm satırlar'
    out['src_columns'] = list(heads)
    out['src_rows'] = [[f(i, v) for i, v in enumerate(row)] for row in rows]
    out['src_num_cols'] = out['num_cols']
    out['notes'].append('Bu rapor Özel Rapor motoruyla gerçek kayıtlardan üretildi; iadeler satıştan düşülür, iptaller sayılmaz.')
    out['actions'] = [dict(type='open_custom', label='Özel Rapor ekranında aç (PDF / Excel / yazdır)', defn=defn)]
    out['tool'] = 'custom_report'
    return out


# ----------------------------------------------------------------------------------------------
# İŞLEM HAZIRLAMA (yalnız öneri üretir; kayıt yapmaz)
# ----------------------------------------------------------------------------------------------
MONTHS = {'ocak': 1, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayis': 5, 'haziran': 6, 'temmuz': 7, 'agustos': 8, 'eylul': 9, 'ekim': 10, 'kasim': 11, 'aralik': 12}
WEEKDAYS = {'pazartesi': 0, 'sali': 1, 'carsamba': 2, 'persembe': 3, 'cuma': 4, 'cumartesi': 5, 'pazar': 6}


def _dmy(d):
    return '%02d.%02d.%04d' % (d.day, d.month, d.year)


def parse_when(text, today=None):
    """-> (date|None, 'HH:MM'|'' , span-listesi). Metinden tarih ve saat çıkarır."""
    today = today or D.today()
    t = fold(text)
    spans = []
    d = None
    m = re.search(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b', t)
    if m:
        try:
            d = D(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            spans.append(m.span())
        except ValueError:
            pass
    if d is None:
        m = re.search(r'\b(\d{1,2})\s+(ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik)\w*(?:\s+(\d{4}))?', t)
        if m:
            y = int(m.group(3)) if m.group(3) else today.year
            try:
                d = D(y, MONTHS[m.group(2)], int(m.group(1)))
                if not m.group(3) and d < today:
                    d = D(y + 1, MONTHS[m.group(2)], int(m.group(1)))
                spans.append(m.span())
            except ValueError:
                pass
    if d is None:
        m = re.search(r'\b(\d{1,3})\s*gun sonra', t)
        if m:
            d = today + datetime.timedelta(days=int(m.group(1)))
            spans.append(m.span())
    if d is None:
        m = re.search(r'\b(\d{1,2})\s*hafta sonra', t)
        if m:
            d = today + datetime.timedelta(days=7 * int(m.group(1)))
            spans.append(m.span())
    if d is None:
        for w, off in (('obur gun', 2), ('yarin', 1), ('bugun', 0)):
            m = re.search(r'\b' + w + r'\w*', t)
            if m:
                d = today + datetime.timedelta(days=off)
                spans.append(m.span())
                break
    if d is None:
        m = re.search(r'\b(pazartesi|sali|carsamba|persembe|cuma|cumartesi|pazar)\w*', t)
        if m:
            wd = WEEKDAYS[m.group(1)]
            diff = (wd - today.weekday()) % 7 or 7
            d = today + datetime.timedelta(days=diff)
            spans.append(m.span())
    hh = ''
    m = re.search(r'\b(?:saat\s*)?(\d{1,2})[:.](\d{2})\b', t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hh = '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
        spans.append(m.span())
    else:
        m = re.search(r'\bsaat\s*(\d{1,2})\b|\b(\d{1,2})\s*(?:\'|’)?\s*(?:de|da|te|ta)\b', t)
        if m:
            h = int(m.group(1) or m.group(2))
            if h < 24:
                hh = '%02d:00' % h
                spans.append(m.span())
    return d, hh, spans


EXPENSE_HINTS = {
    'kira': 'Kira', 'elektrik': 'Elektrik', 'su': 'Su', 'dogalgaz': 'Doğalgaz', 'internet': 'İnternet / Telefon', 'telefon': 'İnternet / Telefon',
    'personel': 'Personel', 'maas': 'Personel', 'kargo': 'Kargo / Nakliye', 'nakliye': 'Kargo / Nakliye', 'bakim': 'Bakım / Onarım', 'onarim': 'Bakım / Onarım',
    'vergi': 'Vergi / Harç', 'harc': 'Vergi / Harç', 'temizlik': 'Temizlik', 'muhasebe': 'Muhasebe',
}


def _expense_categories(eng):
    try:
        return [r['name'] for r in eng.rows("SELECT name FROM expense_categories WHERE active=1 ORDER BY name")]
    except Exception:
        return []


def _accounts(eng):
    try:
        return [(r['name'], r['kind']) for r in eng.rows("SELECT name,kind FROM finance_accounts WHERE active=1 ORDER BY kind,name")]
    except Exception:
        return []


def prepare_action(eng, kind, text):
    kind = fold(kind)
    if kind in ('gider', 'expense', 'masraf'):
        return _prep_expense(eng, text)
    if kind in ('hatirlatma', 'reminder', 'hatirlat'):
        return _prep_reminder(eng, text)
    if kind in ('iban', 'iban_verify', 'havale', 'iban_onay'):
        return _prep_iban(eng, text)
    if kind in ('siparis', 'pool_order'):
        return eng.run('reorder')
    res = eng.new('İşlem hazırlanamadı', summary=['Desteklenen işlemler: gider kaydı, hatırlatma, havale (IBAN) onayı, sipariş taslağı.'])
    return res


def _no_perm(eng, what, module, kind):
    r = eng.new('İşlem için yetkiniz yok', summary=[f'{what} için {module} bölümünde "{kind}" yetkisi gerekir. Yöneticiden yetki isteyebilirsiniz; ben hiçbir kayıt yapmadım.'])
    return r


def _prep_expense(eng, text):
    if not _can(eng, 'Finans Merkezi', 'add'):
        return _no_perm(eng, 'Gider kaydı', 'Finans Merkezi', 'Ekle')
    t = fold(text)
    amount = parse_amount(text)
    cats = _expense_categories(eng)
    cat = ''
    for w, c in EXPENSE_HINTS.items():
        if re.search(r'\b' + w + r'\w*', t):
            cat = next((x for x in cats if fold(x).startswith(fold(c)[:4])), c if c in cats else '')
            if cat:
                break
    if not cat:
        for c in cats:
            cw = [w for w in re.findall(r'[a-z0-9]+', fold(c)) if len(w) >= 3]
            if cw and any(re.search(r'\b' + w, t) for w in cw):
                cat = c
                break
    accs = _accounts(eng)
    acc = ''
    for n, k in accs:
        nw = [w for w in re.findall(r'[a-z0-9]+', fold(n)) if len(w) >= 4 and w not in ('banka', 'vadesiz', 'hesap', 'hesabi')]
        if nw and all(re.search(r'\b' + w, t) for w in nw):
            acc = n
            break
    if not acc:
        if re.search(r'banka|havale|eft|iban|karttan|kartla|hesaptan', t):
            acc = next((n for n, k in accs if k == 'Banka'), '')
        if not acc:
            acc = next((n for n, k in accs if k == 'Kasa'), 'Ana Kasa')
    when, _hh, spans = parse_when(text)
    when = when or D.today()
    note = re.sub(r'\s+', ' ', re.sub(r'(\d[\d.,]*)\s*(bin|k)?\s*(tl|lira|₺)?', ' ', text, flags=re.I)).strip()
    note = re.sub(r'\b(gider|gideri|masraf|masrafi|ekle|ekleyin|kaydet|gir|yaz|olustur|isle)\w*', ' ', note, flags=re.I)
    note = re.sub(r'\s+', ' ', note).strip(' -,.')
    res = eng.new('Hazırlanan işlem: Gider kaydı', summary=[
        'Aşağıdaki gideri kaydetmeye hazırım. HENÜZ HİÇBİR KAYIT YAPILMADI.',
        f"Tutar: {_fm(amount) if amount else '— (formda girin)'}", f"Kategori: {cat or '— (formda seçin)'}", f"Hesap: {acc}", f"Tarih: {_dmy(when)}",
        '"Formu aç ve kontrol et" düğmesine basın; bilgileri düzeltip "ONAYLA VE KAYDET" dediğinizde kaydedilir.'])
    res['actions'] = [dict(type='form', kind='expense', label='Formu aç ve kontrol et',
                           values=dict(amount=('%.2f' % amount).replace('.', ',') if amount else '', category=cat, account=acc, date=_dmy(when), note=note),
                           cats=cats, accounts=[n for n, k in accs])]
    res['tool'] = 'prepare_action'
    return res


def _prep_reminder(eng, text):
    if not _can(eng, 'Duyuru & Hatırlatma', 'add'):
        return _no_perm(eng, 'Hatırlatma', 'Duyuru & Hatırlatma', 'Ekle')
    when, hh, spans = parse_when(text)
    clean = text
    for a_, b_ in sorted(spans, reverse=True):
        pass
    title = re.sub(r'\b(bana|beni|bir|diye)\b', ' ', text, flags=re.I)
    title = re.sub(r'\b(hatirla\w*|hatırla\w*|hatirlat\w*|hatırlat\w*|ekle|olustur|oluştur|kaydet|yaz)\b', ' ', title, flags=re.I)
    title = re.sub(r'\b(yarın|yarin|bugün|bugun|öbür gün|obur gun)\b', ' ', title, flags=re.I)
    title = re.sub(r'\b(pazartesi|salı|sali|çarşamba|carsamba|perşembe|persembe|cuma|cumartesi|pazar)\w*', ' ', title, flags=re.I)
    title = re.sub(r'\b(saat\s*)?\d{1,2}[:.]\d{2}\b|\bsaat\s*\d{1,2}\b|\b\d{1,2}\s*[\'’]?\s*(de|da|te|ta)\b', ' ', title, flags=re.I)
    title = re.sub(r'\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\b\d{1,3}\s*(gün|gun|hafta)\s*sonra\b|\b\d{1,2}\s+(ocak|şubat|subat|mart|nisan|mayıs|mayis|haziran|temmuz|ağustos|agustos|eylül|eylul|ekim|kasım|kasim|aralık|aralik)\w*', ' ', title, flags=re.I)
    title = re.sub(r'\s+', ' ', title).strip(' -,.:')
    res = eng.new('Hazırlanan işlem: Hatırlatma', summary=[
        'Şu hatırlatmayı oluşturmaya hazırım. HENÜZ HİÇBİR KAYIT YAPILMADI.',
        f"Konu: {title or '— (formda yazın)'}", f"Zaman: {(_dmy(when) if when else '— (formda seçin)')} {hh}".strip(),
        '"Formu aç ve kontrol et" düğmesine basın; onaylayınca kaydedilir.'])
    res['actions'] = [dict(type='form', kind='reminder', label='Formu aç ve kontrol et',
                           values=dict(title=title, date=_dmy(when or D.today()), time=hh or '09:00', level='NORMAL', detail=''))]
    res['tool'] = 'prepare_action'
    return res


def _prep_iban(eng, text):
    if not _can(eng, 'Banka Merkezi', 'edit'):
        return _no_perm(eng, 'Havale onayı', 'Banka Merkezi', 'Düzenle')
    rows = eng.rows("""SELECT p.id,s.doc_no,substr(s.dt,1,10) d,p.amount,COALESCE(p.bank_account,'') acc,COALESCE(p.ref,'') ref,COALESCE(ca.title,'Perakende') cu
                       FROM sale_payments p JOIN sales s ON s.id=p.sale_id LEFT JOIN caris ca ON ca.id=s.customer_id
                       WHERE p.kind='Havale/IBAN' AND p.amount>0 AND COALESCE(p.verified,0)=0 AND COALESCE(s.status,'FINAL')<>'CANCEL' ORDER BY s.dt""")
    res = eng.new('Hazırlanan işlem: Havale onayı', sensitive=['Müşteri'])
    if not rows:
        res['summary'] = ['Onay bekleyen Havale/IBAN tahsilatı yok.']
        return res
    t = text or ''
    docs = re.findall(r'[A-Za-z]{1,3}-?\d{4}-\d{3,}|[A-Za-z]{1,3}-\d{3,}', t)
    amount = parse_amount(re.sub(r'[A-Za-z]{1,3}-?\d{4}-\d{3,}|[A-Za-z]{1,3}-\d{3,}', ' ', t))
    ft = fold(t)
    pick = []
    if docs:
        pick = [r for r in rows if any(fold(d) == fold(r['doc_no']) for d in docs)]
    elif amount:
        pick = [r for r in rows if abs(float(r['amount']) - amount) < 0.01]
    elif re.search(r'hepsi|tumu|butun|hepsini', ft):
        pick = list(rows)
    if not pick:
        res['summary'] = ['Hangi havaleyi onaylayacağımı belirleyemedim. Belge no (ör. PS-2026-000012), tutar ya da "hepsini" yazın. Bekleyenler:'] + \
            [f"• {r['doc_no']} — {_fm(r['amount'])} ({r['cu']})" for r in rows[:12]]
        res['columns'] = ['Belge', 'Tutar', 'Banka hesabı']
        res['rows'] = [[r['doc_no'], _fm(r['amount']), r['acc']] for r in rows]
        res['num_cols'] = [1]
        return res
    tot = sum(float(r['amount']) for r in pick)
    res['summary'] = [f'{len(pick)} havale "bankada görüldü" olarak işaretlenecek (toplam {_fm(tot)}). HENÜZ İŞARETLENMEDİ.'] + \
        [f"• {r['doc_no']} — {_fm(r['amount'])} — {r['acc']}" for r in pick[:12]] + \
        ['Yalnızca parayı banka hesabınızda GÖRDÜYSENİZ onaylayın.']
    res['columns'] = ['Belge', 'Tarih', 'Tutar', 'Banka hesabı']
    res['rows'] = [[r['doc_no'], _fd(r['d']), _fm(r['amount']), r['acc']] for r in pick]
    res['num_cols'] = [2]
    res['actions'] = [dict(type='iban_verify', label='Bankada gördüm — onayla…', ids=[int(r['id']) for r in pick], module='Banka Merkezi')]
    res['tool'] = 'prepare_action'
    return res


# ----------------------------------------------------------------------------------------------
# Eylemi çalıştırma (kullanıcı onayından SONRA; UI tarafından çağrılır)
# ----------------------------------------------------------------------------------------------
def _perm(ctx, module, what):
    """role_permissions.add_rec / edit kontrolü (Yönetici hep serbest)."""
    if ctx.get('role') == 'Yönetici':
        return True
    DX = ctx['DX']
    col = {'add': 'add_rec', 'edit': 'edit', 'view': 'view'}[what]
    try:
        r = DX.one(f'SELECT {col} FROM role_permissions WHERE role=? AND module=?', (ctx.get('role', ''), module))
        return bool(r and int(r[col] or 0))
    except Exception:
        return False


def _to_iso(d):
    s = str(d or '').strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    raise ValueError('Tarih anlaşılamadı (GG.AA.YYYY yazın).')


def execute(ctx, kind, vals):
    """kind: expense | reminder | iban_verify. Başarıda kullanıcıya gösterilecek metni döndürür; hata ValueError."""
    DX = ctx['DX']
    user = ctx.get('user', '')
    audit = ctx.get('audit')
    if kind == 'expense':
        if not _perm(ctx, 'Finans Merkezi', 'add'):
            raise ValueError('Gider kaydetme yetkiniz yok (Finans Merkezi > Ekle).')
        a = parse_amount(str(vals.get('amount', '')) + ' tl') if str(vals.get('amount', '')).strip() else None
        if not a or a <= 0:
            raise ValueError('Geçerli bir tutar girin.')
        cat = str(vals.get('category', '')).strip()
        if not cat:
            raise ValueError('Kategori seçin.')
        acc = str(vals.get('account', '')).strip()
        if not acc:
            raise ValueError('Ödeme hesabı seçin.')
        dt = _to_iso(vals.get('date'))
        stamp = dt + ' ' + datetime.datetime.now().strftime('%H:%M:%S')
        pay = 'Banka' if 'banka' in fold(acc) else 'Nakit'
        import ALFABE_FIS_OKU as FO
        con = DX.con()
        try:
            fid = FO.save_expense(con, stamp, cat, acc, pay, a, str(vals.get('note', '')).strip(), user)
        finally:
            con.close()
        try:
            audit('Finans', 'Gider (AI Asistan)', fid, new=f'{cat} {a:.2f} / {acc}')
        except Exception:
            pass
        return f'Gider kaydedildi: {cat} — {_fm(a)} ({acc}, {_to_iso(vals.get("date"))}). Geri almak için Gider / Masraf Merkezi\'nden iptal edebilirsiniz.'
    if kind == 'reminder':
        if not _perm(ctx, 'Duyuru & Hatırlatma', 'add'):
            raise ValueError('Hatırlatma ekleme yetkiniz yok (Duyuru & Hatırlatma > Ekle).')
        title = str(vals.get('title', '')).strip()
        if len(title) < 2:
            raise ValueError('Hatırlatma konusunu yazın.')
        d = _to_iso(vals.get('date'))
        tm = str(vals.get('time', '')).strip() or '09:00'
        if not re.fullmatch(r'\d{1,2}:\d{2}', tm):
            raise ValueError('Saat SS:DD biçiminde olmalı.')
        due = '%s %02d:%s:00' % (d, int(tm.split(':')[0]), tm.split(':')[1])
        rid = DX.exec("INSERT INTO reminders(dt,title,detail,status,due,level,category,repeat_rule,completed,assigned_to) VALUES(?,?,?,?,?,?,?,?,0,?)",
                      (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), title, str(vals.get('detail', '')).strip(), 'Yeni', due,
                       str(vals.get('level') or 'NORMAL'), 'Hatırlatma', '', user))
        try:
            audit('Hatırlatma', 'Hatırlatma (AI Asistan)', rid, new=f'{title} / {due}')
        except Exception:
            pass
        return f'Hatırlatma oluşturuldu: "{title}" — {_fd(d)} {tm}. Duyuru & Hatırlatma bölümünde görünür.'
    if kind == 'iban_verify':
        if not _perm(ctx, 'Banka Merkezi', 'edit'):
            raise ValueError('Havale onaylama yetkiniz yok (Banka Merkezi > Düzenle).')
        import ALFABE_IBAN as IB
        ids = [int(x) for x in vals.get('ids') or []]
        if not ids:
            raise ValueError('Onaylanacak havale seçilmedi.')
        con = DX.con()
        try:
            for pid in ids:
                IB.set_verified(con, pid, True, user)
        finally:
            con.close()
        return f'{len(ids)} havale "bankada görüldü" olarak işaretlendi.'
    raise ValueError('Bilinmeyen işlem: ' + str(kind))


# ----------------------------------------------------------------------------------------------
# Onay formu (Tk)
# ----------------------------------------------------------------------------------------------
def confirm_form(host, kind, values, cats=(), accounts=()):
    """Kullanıcıya düzenlenebilir onay formu gösterir. Onaylanırsa değerler sözlüğü, aksi halde None döner."""
    import tkinter as tk
    from tkinter import ttk
    w = tk.Toplevel(host)
    w.title('İşlemi Kontrol Et ve Onayla')
    w.geometry('560x430')
    try:
        w.transient(host.winfo_toplevel())
        w.grab_set()
    except Exception:
        pass
    spec = {
        'expense': ('GİDER KAYDI', [('amount', 'Tutar (TL)', 'entry', None), ('category', 'Kategori', 'combo', list(cats)), ('account', 'Ödeme hesabı', 'combo', list(accounts)),
                                    ('date', 'Tarih (GG.AA.YYYY)', 'entry', None), ('note', 'Açıklama', 'entry', None)]),
        'reminder': ('HATIRLATMA', [('title', 'Konu', 'entry', None), ('date', 'Tarih (GG.AA.YYYY)', 'entry', None), ('time', 'Saat (SS:DD)', 'entry', None),
                                    ('level', 'Öncelik', 'combo', ['NORMAL', 'ÖNEMLİ', 'ACİL']), ('detail', 'Not', 'entry', None)]),
    }[kind]
    tk.Label(w, text=spec[0], font=('Segoe UI', 15, 'bold'), fg='#0b376d').pack(pady=(14, 2))
    tk.Label(w, text='Bilgileri kontrol edin, gerekirse düzeltin. "ONAYLA VE KAYDET" demeden hiçbir kayıt yapılmaz.', fg='#64748b', wraplength=500).pack()
    vars_ = {}
    frm = tk.Frame(w)
    frm.pack(fill='x', padx=24, pady=10)
    for i, (k, lab, typ, vals) in enumerate(spec[1]):
        tk.Label(frm, text=lab + ':', anchor='w', width=20).grid(row=i, column=0, sticky='w', pady=5)
        v = tk.StringVar(value=str(values.get(k, '')))
        vars_[k] = v
        if typ == 'combo':
            ttk.Combobox(frm, textvariable=v, values=vals, width=34).grid(row=i, column=1, sticky='w')
        else:
            tk.Entry(frm, textvariable=v, width=37).grid(row=i, column=1, sticky='w')
    out = {'v': None}

    def ok(*_):
        out['v'] = {k: v.get().strip() for k, v in vars_.items()}
        w.destroy()

    def cancel(*_):
        w.destroy()
    bf = tk.Frame(w)
    bf.pack(pady=10)
    tk.Button(bf, text='ONAYLA VE KAYDET', command=ok, bg='#15803d', fg='white', font=('Segoe UI', 11, 'bold'), padx=16, pady=6).pack(side='left', padx=8)
    tk.Button(bf, text='VAZGEÇ', command=cancel, font=('Segoe UI', 11), padx=14, pady=6).pack(side='left', padx=8)
    w._asistan = dict(vars=vars_, ok=ok, cancel=cancel)
    if getattr(host, '_asistan_auto', None):         # test/otomasyon kancası
        host._asistan_auto(w)
    try:
        if w.winfo_exists():
            w.wait_window()
    except Exception:
        pass
    return out['v']


def run_action(host, ctx, eng, act, put, messagebox, APP='ALFABE'):
    """UI düğmesi: eylemi yürütür. Dönüş: True=yapıldı."""
    typ = act.get('type')
    if typ == 'goto':
        go = ctx.get('go')
        if not go:
            messagebox.showinfo(APP, 'Ekran açma bu kurulumda kapalı.', parent=host)
            return False
        if not eng.can_view(act['module']):
            messagebox.showwarning(APP, f"{act['module']} bölümünü açma yetkiniz yok.", parent=host)
            return False
        go(act['module'])
        return True
    if typ == 'open_custom':
        opener = ctx.get('open_custom')
        if not opener:
            messagebox.showinfo(APP, 'Özel Rapor ekranı bu kurulumda açılamıyor.', parent=host)
            return False
        opener(act['defn'])
        return True
    if typ == 'form':
        mod = {'expense': 'Finans Merkezi', 'reminder': 'Duyuru & Hatırlatma'}[act['kind']]
        if not _perm(ctx, mod, 'add'):
            messagebox.showwarning(APP, f'Bu işlemi yapma yetkiniz yok ({mod} > Ekle). Yöneticiden yetki isteyin.', parent=host)
            return False
        vals = confirm_form(host, act['kind'], act['values'], act.get('cats') or (), act.get('accounts') or ())
        if vals is None:
            put('meta', 'İşlem iptal edildi; hiçbir kayıt değiştirilmedi.')
            return False
        try:
            msg = execute(ctx, act['kind'], vals)
        except ValueError as ex:
            messagebox.showwarning(APP, str(ex), parent=host)
            return False
        except Exception as ex:
            try:
                ctx['log_error']('AI Asistan işlem', ex)
            except Exception:
                pass
            messagebox.showerror(APP, 'İşlem kaydedilemedi:\n' + str(ex), parent=host)
            return False
        put('bot', msg)
        return True
    if typ == 'iban_verify':
        ids = act.get('ids')
        if ids is None:
            return run_action(host, ctx, eng, dict(type='goto', module='Banka Merkezi'), put, messagebox, APP)
        if not _perm(ctx, 'Banka Merkezi', 'edit'):
            messagebox.showwarning(APP, 'Havale onaylama yetkiniz yok (Banka Merkezi > Düzenle).', parent=host)
            return False
        if not messagebox.askyesno(APP, f'{len(ids)} havale tahsilatı "bankada görüldü" olarak işaretlensin mi?\n\nYalnızca parayı banka hesabınızda gördüyseniz onaylayın.', parent=host):
            put('meta', 'İşlem iptal edildi; hiçbir kayıt değiştirilmedi.')
            return False
        try:
            put('bot', execute(ctx, 'iban_verify', dict(ids=ids)))
        except Exception as ex:
            messagebox.showwarning(APP, str(ex), parent=host)
            return False
        return True
    return False


# ----------------------------------------------------------------------------------------------
# Yönlendirme: yeni answer()
# ----------------------------------------------------------------------------------------------
MODULE_ALIASES = [
    ('hizli satis', 'Hızlı Satış'), ('cari satis', 'Cari Satış'), ('stok merkezi', 'Stok Merkezi'), ('stok', 'Stok Merkezi'), ('fiyat yonetimi', 'Fiyat Yönetimi'),
    ('mal kabul', 'Alış & Mal Kabul'), ('cari merkezi', 'Cari Merkezi'), ('cari', 'Cari Merkezi'), ('personel', 'Personel Takibi'), ('siparis', 'Sipariş & Teklif'),
    ('teklif', 'Sipariş & Teklif'), ('finans', 'Finans Merkezi'), ('gider', 'Finans Merkezi'), ('kasa', 'Kasa Merkezi'), ('banka', 'Banka Merkezi'), ('iban kontrol', 'Banka Merkezi'),
    ('pos takip', 'Banka Merkezi'), ('pos', 'Banka Merkezi'), ('e fatura', 'E-Fatura / E-Arşiv'), ('muhasebe', 'Muhasebe & Beyan'), ('kampanya', 'Kampanya Merkezi'),
    ('hizmet', 'Hizmetler / Fotokopi'), ('fotokopi', 'Hizmetler / Fotokopi'), ('etiket', 'Etiket & Barkod'), ('fiyat gor', 'Fiyat Gör'), ('rapor', 'Rapor & Analiz'),
    ('yazdirma', 'Yazdırma Merkezi'), ('uyari', 'Uyarılar'), ('hatirlatma', 'Duyuru & Hatırlatma'), ('duyuru', 'Duyuru & Hatırlatma'), ('veri aktar', 'Veri Aktarım'),
    ('sistem', 'Sistem Merkezi'), ('yedek', 'Sistem Merkezi'), ('kullanici', 'Kullanıcı & Güvenlik'), ('ayarlar', 'Ayarlar'), ('ana sayfa', 'Ana Sayfa'),
]


def _answer2(self, question):
    q = str(question or '').strip()
    t = re.sub(r'\bnasl\b|\bnasul\b|\bnsl\b', 'nasil', fold(q))
    if not t:
        return self.new('Sorunuzu yazın', summary=['Örnekler: "Bu ay ürün grubuna göre satışlar", "Havale ile ödeme nasıl alınır?", "5000 TL kira gideri ekle".'])
    howto = bool(HOW_STRONG.search(t))
    scored = kb_find(t)
    top = scored[0][0] if scored else 0.0
    # 1) açık "nasıl / nerede" sorusu ve bilgi tabanında karşılığı varsa -> bilgi asistanı
    #    (cari adı geçen veri soruları hariç)
    cari_hit = None
    if re.search(r'borc|bakiye|alacak|ekstre|ne kadar|hareket|odedi|aldim|verdim', t):
        try:
            found = self._find_cari(q)
            cari_hit = found[0] if found and found[0][2] >= 0.99 else None
        except Exception:
            cari_hit = None
    if howto and top >= KB_MIN and not cari_hit:
        return knowledge_result(self, q, t, scored)
    # 2) işlem istekleri (ekle/kaydet/hatırlat/onayla)
    act = _detect_action(self, q, t, scored)
    if act is not None:
        return act
    # 3) cari adı + bakiye sorusu
    if cari_hit:
        return self.run('cari_balance', name=q)
    # 4) eski kural tabanlı analizler (+ yeni araçların kuralları)
    old = self._answer_v1(q)
    unknown = old.get('title') == 'Sorunuzu anlayamadım'
    if not unknown and old.get('tool') == 'product_lookup' and re.search(r'stok deger|stok maliyet|stok tutar|sermaye|elimdeki mal|envanter deger', t):
        r0 = self.run('custom_report', **(_plan_custom(self, q, t) or dict(domain='Stok', measures='maliyet_deger,adet')))
        if r0.get('rows'):
            return r0
    if not unknown and old.get('tool') in ('sales_summary', 'profit', 'top_products', 'service_income', 'product_lookup') and \
            (re.search(r'\bgore\b|bazinda|bazli|dagilim|kirilim|ayri ayri|aylik|gunluk|her gun|her ay', t) or
             (old.get('tool') == 'top_products' and re.search(r'en cok|en fazla|en iyi|en az', t) and re.search(r'\b(grub|grup|marka|kasiyer|musteri|cari)\w*', t))):
        plan0 = _plan_custom(self, q, t)
        if plan0 and plan0.get('group_by'):
            r0 = self.run('custom_report', **plan0)
            if r0.get('rows'):
                return r0
    if not unknown and old.get('tool') == 'expenses' and not self._has_period(t) and top >= KB_MIN and not re.search(r'ne kadar|toplam|kac|listele|harcad', t):
        return knowledge_result(self, q, t, scored)
    if not unknown:
        # eski yönlendirme zayıf ve bilgi tabanı çok daha güçlüyse bilgi yanıtı ver
        if not DATA_CUE.search(t) and top >= KB_OK + 1.2 and old.get('tool') in ('product_lookup', 'sales_summary'):
            return knowledge_result(self, q, t, scored)
        return old
    # 5) serbest rapor (veri ipucu yoksa ve bilgi tabanı güçlüyse önce bilgi)
    if not DATA_CUE.search(t) and top >= KB_OK:
        return knowledge_result(self, q, t, scored)
    plan = None if (howto and top < KB_MIN) else _plan_custom(self, q, t)
    if plan:
        try:
            r = self.run('custom_report', **plan)
            if r.get('rows') or r.get('title') == 'Yetki yok':
                try:
                    self.last = ('custom_report', dict(plan))
                except Exception:
                    pass
                return r
        except Exception:
            pass
    # 6) bilgi tabanı (nasıl-kalıbı olmasa da)
    if top >= KB_MIN:
        return knowledge_result(self, q, t, scored)
    # 7) modül aç
    # 8) hiçbiri: yakın öneriler
    res = self.new('Sorunuzu tam anlayamadım', summary=[
        'Bu soruyu net bir işleme ya da konuya bağlayamadım. Şunlardan biri mi demek istediniz?',
        'İsterseniz soruyu şu kalıplarla yazın: "<dönem> <ürün grubuna/markaya/müşteriye göre> satışlar", "<konu> nasıl yapılır?", "<tutar> TL <kira> gideri ekle".'])
    res['suggest'] = suggestions(q, 5)
    return res


def _detect_action(eng, q, t, scored):
    top_id = scored[0][1]['id'] if scored else ''
    verb_add = re.search(r'\b(ekle|ekleyin|kaydet|gir|yaz|olustur|isle|aç|ac)\w*|odedim|verdim|cikti\b|hazirla|yap\b', t)
    # gider
    if re.search(r'gider|masraf|\b(kira|elektrik|dogalgaz|kargo|temizlik)\w*.*(odedim|ekle|kaydet|gir|yaz)', t) and \
            not (top_id == 'fis_oku' and parse_amount(q) is None) and re.search(r'\b(ekle|ekleyin|kaydet|gir|yaz|isle|olustur)\w*|odedim|odendi', t) \
            and not re.search(r'ne kadar|toplam|raporu|listele|kac|nasil|nerede|nereden', t):
        return prepare_action(eng, 'gider', q)
    # hatırlatma
    if re.search(r'hatirlat', t) and not re.search(r'nasil|nerede', t):
        return prepare_action(eng, 'hatirlatma', q)
    # havale onayı
    if re.search(r'havale|iban|\beft|bankada gordum|bankaya (gecti|geldi|girdi)', t) and re.search(r'onayla|dogrula|gordum|geldi|gelmis|gecti|girdi|teyit|isaretle|tamam', t) and not re.search(r'nasil|nerede|kac|hangi|bekleyen', t):
        return prepare_action(eng, 'iban', q)
    # sipariş taslağı
    if re.search(r'siparis', t) and re.search(r'olustur|hazirla|tasla|\bver\b|verelim|vermeliyim|etmeliyim|yapmaliyim|vermem', t) and not re.search(r'nasil|nerede', t):
        r = eng.run('reorder')
        r['notes'].insert(0, 'Siparişi ben vermiyorum: aşağıdaki öneri listesinden "Sipariş taslağı oluştur" düğmesiyle taslak hazırlanır, onayınızı ister.')
        return r
    # ekran aç
    if re.search(r'\b(ac|acar misin|acin|git|gotur|goster|gec)\b', t) and not DATA_CUE.search(t):
        for key, mod in MODULE_ALIASES:
            if re.search(r'\b' + key, t):
                res = eng.new(f'{mod} ekranı', summary=[f'{mod} ekranını açabilirim.'])
                res['actions'] = [dict(type='goto', label=f'{mod} ekranını aç', module=mod)]
                return res
    return None


# ----------------------------------------------------------------------------------------------
# Kayıt
# ----------------------------------------------------------------------------------------------
NEW_TOOLS = {
    'cash_status': dict(title='Kasa ve banka durumu', needs=['Finans Merkezi', 'Kasa Merkezi', 'Banka Merkezi'],
                        desc='Kasadaki ve banka hesaplarındaki güncel bakiyeler, bekleyen POS tutarı.', params={}),
    'cari_balance': dict(title='Cari bakiye', needs=['Cari Merkezi', 'Finans Merkezi', 'Rapor & Analiz'],
                         desc='Bir carinin (müşteri/tedarikçi) güncel borç/alacak bakiyesi ve son hareketleri. name: cari unvanı veya soru metni.',
                         params={'name': {'type': 'string', 'description': 'Cari unvanı (ör. "Ahmet Yılmaz").'}}),
    'expenses': dict(title='Giderler', needs=['Finans Merkezi', 'Rapor & Analiz'],
                     desc='Dönemin giderleri kategoriye göre (iptaller hariç).',
                     params={'period': {'type': 'string', 'description': 'Dönem (bu ay, geçen ay ...).'}, 'category': {'type': 'string', 'description': 'İsteğe bağlı kategori (kira, elektrik ...).'}}),
    'payment_mix': dict(title='Ödeme türü dağılımı', needs=['Rapor & Analiz', 'Hızlı Satış', 'Cari Satış'],
                        desc='Dönemin net tahsilatı Nakit / Kart / Havale-IBAN / Açık hesap gibi ödeme türlerine göre.',
                        params={'period': {'type': 'string', 'description': 'Dönem.'}}),
    'iban_pending': dict(title='Bankada doğrulanmamış havaleler', needs=['Banka Merkezi', 'Finans Merkezi'],
                         desc='Havale/IBAN ile alınıp banka hesabında henüz görüldü olarak işaretlenmemiş tahsilatlar.', params={}),
    'pos_pending': dict(title='Bekleyen POS yatışları', needs=['Banka Merkezi', 'Finans Merkezi'],
                        desc='Kart satışlarından bankaya geçmesi beklenen tutarlar ve yatış tarihleri.', params={}),
    'attention': dict(title='Bugün dikkat edilecekler', needs=['Stok Merkezi', 'Finans Merkezi', 'Cari Merkezi', 'Banka Merkezi', 'Rapor & Analiz', 'Sistem Merkezi'],
                      desc='Kritik stok, vadesi gelen/geçen kayıtlar, borçlu cariler, bekleyen POS/havale ve yedek durumu gibi dikkat gerektiren konular.', params={}),
    'custom_report': dict(title='Serbest rapor', needs=sum(DOMAIN_NEEDS.values(), []),
                          desc=("Gerçek kayıtlardan ölçü + gruplama + dönem + karşılaştırma ile rapor üretir. domain: Satış|Stok|Cari|Finans. "
                                "group_by (virgülle): Satış: tarih, ay, belge, tur, musteri, kullanici, odeme, kod, barkod, urun, marka, grup; Stok: kod, barkod, urun, marka, grup, altgrup, depo; "
                                "Cari: unvan, grup, sehir, hareket, ay; Finans: tarih, ay, hesap, tur, kaynak. "
                                "measures (virgülle): Satış: adet, ciro, iskonto, maliyet, kar, kdv, fis; Stok: adet, maliyet_deger, satis_deger, cesit, min_alti; Cari: borc, alacak, bakiye; Finans: giris, cikis, net, islem. "
                                "compare: 'onceki' veya 'yil'. filter: 'grup=KALEM;marka=X'. calc: 'Kâr Marjı %=kar / ciro * 100'."),
                          params={'domain': {'type': 'string', 'description': 'Satış, Stok, Cari veya Finans.'}, 'group_by': {'type': 'string', 'description': 'Gruplama alanları (virgülle).'},
                                  'measures': {'type': 'string', 'description': 'Ölçüler (virgülle).'}, 'period': {'type': 'string', 'description': 'Dönem.'},
                                  'compare': {'type': 'string', 'description': "'onceki' veya 'yil'."}, 'filter': {'type': 'string', 'description': "alan=değer;alan=değer"},
                                  'limit': {'type': 'integer', 'description': 'İlk N satır.'}, 'sort': {'type': 'string', 'description': "'asc' küçükten büyüğe."},
                                  'calc': {'type': 'string', 'description': "Hesaplanan alan: 'Ad=formül'."}}),
    'howto': dict(title='Programı kullanma bilgisi', needs=[], open=True,
                  desc='"X nasıl yapılır / nerede" tipi sorular için program kullanım adımlarını ve ilgili ekranı verir.',
                  params={'query': {'type': 'string', 'description': 'Kullanıcının sorusu.'}}),
    'prepare_action': dict(title='İşlem hazırla', needs=[], open=True,
                           desc=("Gider kaydı (kind=gider), hatırlatma (kind=hatirlatma) veya havale onayı (kind=iban) için ONAY FORMU hazırlar. "
                                 "Asla kayıt yapmaz; kullanıcı ekrandaki düğmeyle kendisi onaylar. text: kullanıcının cümlesi."),
                           params={'kind': {'type': 'string', 'description': 'gider | hatirlatma | iban'}, 'text': {'type': 'string', 'description': 'Kullanıcının isteği.'}}),
}
OPEN_TOOLS = {k for k, v in NEW_TOOLS.items() if v.get('open')}

NEW_RULES = {
    'cash_status': [(r'kasa.{0,25}(ne kadar|kac|bakiye|durum|para)', 6), (r'banka.{0,25}(ne kadar|kac|bakiye|durum|para)', 6), (r'kasada|bankada', 4), (r'kasa ve banka', 8),
                    (r'ne kadar param', 6), (r'nakit durum|nakit varlik|elimde.{0,10}para', 5)],
    'expenses': [(r'gider', 5), (r'masraf', 5), (r'ne kadar harcad', 6), (r'harcama', 4)],
    'payment_mix': [(r'odeme (tur|tip|sekil)', 6), (r'nakit.{0,15}(kart|havale|iban)', 7), (r'(kart|havale|iban).{0,15}nakit', 7), (r'ne kadar.{0,12}(havale|iban|kart|nakit)', 6),
                    (r'(havale|iban|kart).{0,8}(ile|ten|dan|den)? ?(gelen|tahsil|odeme)', 5), (r'tahsilat dagilim', 7), (r'odeme dagilim', 7)],
    'iban_pending': [(r'(bekleyen|dogrulanmam|onaylanmam|kontrol edilmem|gorulmem).{0,18}(havale|iban)', 9), (r'(havale|iban).{0,18}(bekleyen|dogrulanmam|onaylanmam|kontrol bekle)', 9),
                     (r'havale.{0,10}(geldi mi|gelmis mi)', 6)],
    'pos_pending': [(r'bekleyen pos', 9), (r'(kart|pos).{0,45}ne zaman', 10), (r'pos.{0,20}(bekleyen|ne zaman|yatacak|gecmedi|valor)', 8), (r'kart.{0,12}(ne zaman|yatacak|bankaya)', 7), (r'bankaya gecmesi', 8),
                    (r'pos ucreti|aylik pos', 6)],
    'slow_stock': [(r'yavas sat|az sat|satisi dur|satilmaz', 8)],
    'attention': [(r'dikkat', 7), (r'neye bakmaliyim|ne yapmaliyim|bugun ne|acil', 7), (r'yapmam gereken', 8), (r'sorun var mi|problem var mi', 7), (r'uyari', 3)],
}


def install(A):
    """ALFABE_ASISTAN modülüne yeni araçları ve yeni answer() katmanını ekler."""
    global _A
    _A = A
    E = A.Engine
    if getattr(E, '_a2_installed', False):
        return
    for name, spec in NEW_TOOLS.items():
        A.TOOLS[name] = {k: v for k, v in spec.items() if k != 'open'}
        fn = getattr(T, name)
        setattr(E, 't_' + name, fn)
    setattr(E, '_find_cari', T._find_cari)
    for name, rules in NEW_RULES.items():
        A.RULES.setdefault(name, [])
        A.RULES[name] = list(A.RULES[name]) + list(rules)
    for name in ('iban_pending', 'pos_pending', 'cash_status', 'payment_mix', 'expenses', 'attention'):
        if name not in A.PRIORITY:
            A.PRIORITY.insert(0, name)
    # çakışmaları çöz: "gider" kelimesi eski kurallarla yarışırken yeni ağırlıklar yeterli; 'debtors' 'alacak' kuralı korunur
    E._answer_v1 = E.answer
    E.answer = _answer2
    orig_run = E.run

    def run2(self, tool, **args):
        if tool in OPEN_TOOLS:
            res = getattr(self, 't_' + tool)(**self._clean(tool, args))
            res['tool'] = tool
            return res
        return orig_run(self, tool, **args)
    E.run = run2
    orig_clean = E._clean

    def clean2(self, tool, args):
        out = orig_clean(self, tool, args)
        if tool in ('howto', 'prepare_action', 'cari_balance'):
            for k, spec in A.TOOLS[tool]['params'].items():
                if k in args and args[k] not in (None, ''):
                    out[k] = str(args[k])[:400]
        return out
    E._clean = clean2
    A.SYSTEM_PROMPT = (A.SYSTEM_PROMPT + " Programın nasıl kullanılacağı sorulursa howto aracını çağır ve adımları olduğu gibi aktar. "
                       "Rapor istenirse uygun analiz aracını ya da custom_report'u kullan. Gider ekleme, hatırlatma veya havale onayı gibi işlem istenirse "
                       "prepare_action aracını çağır: bu araç yalnızca onay formu hazırlar, kaydı kullanıcı kendisi onaylar; kayıt yaptığını asla söyleme.")
    E._a2_installed = True


def chip_examples():
    return ['Bu ay ürün grubuna göre satışlar', 'Kasada ne kadar var?', 'Havale ile ödeme nasıl alınır?', 'Bugün dikkat etmem gerekenler']
