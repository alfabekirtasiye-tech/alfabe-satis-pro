# -*- coding: utf-8 -*-
"""ALFABE Yardım Merkezi (kullanım kılavuzu).

Sol ağaçta konular, sağda içerik. Konuların çoğu Asistan bilgi tabanından (ALFABE_ASISTAN_BILGI.KB) gelir; böylece
tek yerde yazılır, iki yerde aynı anlatım görünür. Burada yalnız grup girişleri ve KB'de olmayan konular yazılıdır.

Metin işaretleri (satır başı):  '# ' başlık   '- ' madde   '! ' not   diğer satırlar düz yazı.
"""
import os
import re
import html as _html

TR = str.maketrans('İIıŞşĞğÜüÖöÇç', 'iiissgguuoocc')


def fold(s):
    return str(s or '').translate(TR).lower()


# (grup adı, giriş metni, [bu gruba giren modüller])
GRUPLAR = [
    ('Başlarken', 'Programın genel düzeni, yetkiler ve yardım pencereleri.', []),
    ('Satış', 'Kasada hızlı satış, veresiye (cari) satış, hizmet satışı ve müşteri listelerini fiyatlama.',
     ['Hızlı Satış', 'Cari Satış', 'Hizmetler / Fotokopi']),
    ('Stok ve Fiyat', 'Ürün kartları, sayım, toplu fiyat, etiket ve kampanyalar.',
     ['Stok Merkezi', 'Fiyat Yönetimi', 'Etiket & Barkod', 'Fiyat Gör', 'Kampanya Merkezi']),
    ('Alış ve Sipariş', 'Tedarikçiden mal alma, belgeden alış, sipariş ve teklif.',
     ['Alış & Mal Kabul', 'Sipariş & Teklif']),
    ('Cari', 'Müşteri ve tedarikçi kartları, tahsilat, ekstre, vade.', ['Cari Merkezi']),
    ('Kasa, Banka ve Finans', 'Kasa, banka, gider, personel, e-fatura ve muhasebe.',
     ['Finans Merkezi', 'Kasa Merkezi', 'Banka Merkezi', 'Personel Takibi', 'E-Fatura / E-Arşiv', 'Muhasebe & Beyan']),
    ('Raporlar ve Uyarılar', 'Raporlar, yazdırma, günlük özet, uyarı ve hatırlatmalar.',
     ['Rapor & Analiz', 'Yazdırma Merkezi', 'Uyarılar', 'Duyuru & Hatırlatma']),
    ('Sistem ve Ayarlar', 'Yedek, kullanıcı yetkileri, ayarlar, veri aktarımı ve asistan.',
     ['Sistem Merkezi', 'Kullanıcı & Güvenlik', 'Ayarlar', 'Veri Aktarım', 'AI Asistan', 'Cihazlar & Entegrasyonlar']),
]

# KB'de olmayan, elle yazılmış konular: (id, başlık, grup, modül, metin)
EK_KONULAR = [
    ('baslarken_duzen', 'Ekran düzeni ve çalışma biçimi', 'Başlarken', '', '''
# Ana Sayfa ve bölümler
Program açıldığında Ana Sayfa görünür. Sol tarafta menü vardır; menü gruplara ayrılmıştır (SATIŞ, STOK & ALIŞ, MÜŞTERİ & SİPARİŞ, FİNANS, ARAÇLAR, ASİSTAN, YÖNETİM). Grup başlığına tıklayınca grup açılır veya kapanır.
Bir bölüm açıldığında (örneğin Hızlı Satış) bölüm tam ekran çalışır; sol menü ve alt çubuk gizlenir. Pencerenin sağ üstündeki — / □ / X düğmeleri her bölümde vardır.
# Tek bölüm kuralı
Bir bölüm açıkken başka bir bölüm açılmaz; önce açık bölümü kapatıp Ana Sayfa'ya dönün. Bu sırada uyarı penceresi çıkmaz, tıklama sessizce yok sayılır.
# Yetkiler
Yönetici dışındaki kullanıcılarda yetkisi olmayan bölümler sol menüde görünmez. Yetkiler "Kullanıcı & Güvenlik" bölümünden verilir.
'''),
    ('baslarken_yardim', 'Yardım nasıl açılır?', 'Başlarken', '', '''
# Bu kılavuz
- Ana Sayfa'da sol menünün en altındaki "Yardım / Kılavuz" düğmesi.
- Alt çubuktaki "Kılavuz" düğmesi.
- Hızlı Satış dışındaki ekranlarda F1 tuşu: o ekranın konusunu doğrudan açar. (Hızlı Satış'ta F1 "nakit öde" tuşudur; orada kılavuza Ana Sayfa'dan ulaşılır.)
- F12 (Yardım & Kısayollar penceresi) içindeki "TAM KILAVUZ" düğmesi.
# Pencere içinde
- Sol ağaçtan konu seçin; "Ara" kutusuna yazınca yalnız eşleşen konular kalır.
- Geri düğmesi önceki konuya döner. Gizle düğmesi ağacı gizleyip okuma alanını genişletir.
- Yazdır düğmesi açık konuyu tarayıcıda yazdırma ekranıyla açar.
- PDF düğmesi tüm kılavuzu PDF olarak açar (program klasöründeki ALFABE_KULLANIM_KILAVUZU.pdf).
'''),
    ('liste_fiyatla', 'Liste Fiyatla: müşterinin listesini fiyatlama', 'Satış', 'Hızlı Satış', '''
# Ne işe yarar?
Müşterinin getirdiği malzeme listesini (okul listesi, ofis listesi) yazarak, yapıştırarak, fotoğraftan veya PDF'ten okuyup satırları stoktaki ürünlerle eşleştirir, fiyatlandırır; sepete ekler, yazdırır veya mesaj olarak kopyalar.
# Açılış
Sol menüde "Fiyat Gör" altındaki "Liste Fiyatla" (Hızlı Satış yetkisi gerekir). Seçilen ürünler satış sepetine gittiği için Hızlı Satış açılır, araç onun üstünde çalışır.
# Adımlar
1. Listeyi kutuya yapıştırın (Ctrl+V) ya da "FOTOĞRAF / PDF OKU" düğmesiyle bir fotoğraf veya PDF seçin.
2. HESAPLA'ya basın. Her satır "adet + ürün" olarak ayrılır, stokla eşleşir ve fiyatlanır.
3. Eşleşmeyen veya yanlış eşleşen satırda çift tıklayıp ürünü değiştirin. Ürün arama penceresinde ENTER veya çift tık ürünü listeye ekler, pencere açık kalır, ESC kapatır.
4. Adet veya Birim hücresine tıklayıp doğrudan yazarak düzeltin. Son işlem geri alınabilir.
5. Müşteri, telefon, iskonto, kapora, teslim tarihi, not ve fiyat türü alanlarını gerekirse doldurun.
6. Sonucu kullanın: SEPETE EKLE, YAZDIR, "3 SEÇENEĞİ YAZDIR", PDF KAYDET, EXCEL KAYDET, PANOYA KOPYALA (WhatsApp veya mesaja yapıştırmak için).
7. Stokta bulunamayan satırlar ayrı gösterilir; bunları panoya kopyalayabilir veya sipariş listesine aktarabilirsiniz.
# PDF okuma
- Yazı katmanlı (dijital) PDF: metin doğrudan alınır, OCR kullanılmaz. En çok 60 sayfa. Tablo başlığı ("Sıra Malzeme Adet") varsa başlık ve sıra numaraları atılır, sondaki sayı adet sayılır.
- Taranmış veya fotoğraf PDF: sayfalar Windows okuma özelliğiyle okunur, en çok 20 sayfa. Okuma hatası olabilir; tablodaki satırları kontrol edin.
- Şifreli, bozuk veya yazısız PDF'te ekranda açık bir uyarı çıkar.
! Fotoğraftan veya taranmış PDF'ten okunan sayıları (adet) mutlaka gözle kontrol edin.
# Okul listesi olarak kaydetme
"OKUL LİSTESİ OLARAK KAYDET" düğmesi fiyatlanan listeyi Okul İhtiyaç Listesi'ne ekler.
'''),
    ('okul_listesi', 'Okul İhtiyaç Listesi', 'Satış', 'Hızlı Satış', '''
# Ne işe yarar?
Sınıf bazında kayıtlı ihtiyaç listelerini tek seferde sepete ekler.
# Adımlar
1. Sol menüde "Fiyat Gör" altındaki "Okul İhtiyaç Listesi"ni açın (Hızlı Satış yetkisi gerekir).
2. Soldaki LİSTELER bölümünden listeyi, sınıfı seçin.
3. Ürünleri kontrol edip SEPETE EKLE'ye basın.
! Yeni liste eklemenin en kolay yolu Liste Fiyatla ekranındaki "OKUL LİSTESİ OLARAK KAYDET" düğmesidir.
'''),
    ('belgeden_alis', 'Belgeden alış (PDF / Excel / fotoğraf)', 'Alış ve Sipariş', 'Alış & Mal Kabul', '''
# Ne işe yarar?
Tedarikçinin gönderdiği fatura veya irsaliyeden ürün satırlarını okuyup alış belgesine aktarır; elle yazma işini azaltır.
# Adımlar
1. Alış & Mal Kabul ekranında "BELGEDEN ALIŞ AL (PDF / EXCEL)" düğmesine basın ve dosyayı seçin.
2. Tedarikçiyi seçin. Belgedeki fiyatlar KDV dahilse "Belgedeki fiyatlar KDV dahil" kutusunu işaretleyin.
3. Satırları stok kartlarıyla eşleştirin. Yeni ürünlerde satış kârı yüzdesini (varsa) girin.
4. "Bu tedarikçinin ürün kodlarını hatırla" işaretliyse bir sonraki belgede aynı kodlar otomatik eşleşir.
5. ALIŞ BELGESİNE AKTAR'a basın.
! Belge fotoğraftan veya taramadan okunduysa miktar, fiyat ve KDV sayılarını özellikle kontrol edin. ⚠ işaretli satırlarda miktar × fiyat belgedeki tutarla tutmuyor demektir.
'''),
    ('sorun_giderme', 'Sık karşılaşılan durumlar', 'Başlarken', '', '''
# Başka bölüm açılmıyor
Tek bölüm kuralı: açık bölümü kapatıp Ana Sayfa'ya dönün, sonra yeni bölümü açın.
# Bir menü görünmüyor
Kullanıcınızın o bölüme yetkisi yoktur. Yönetici "Kullanıcı & Güvenlik" bölümünden yetki verebilir.
# Fotoğraftan veya PDF'ten okuma çalışmıyor
Okuma Windows'un yazı tanıma (OCR) özelliğini kullanır. "Okuma dili yok" uyarısı çıkarsa Windows'a Türkçe dil paketini ekleyin; olmazsa listeyi elle yazın veya yapıştırın. Yazı katmanlı PDF'lerde Windows OCR gerekmez.
# Veri güvenliği
Düzenli yedek alın (konu: Yedek alma ve geri yükleme). Yedek, program kapanırsa veya bilgisayar bozulursa verinizi geri getirmenin tek yoludur.
'''),
]


def _grup_of(module):
    for ad, _i, mods in GRUPLAR:
        if module in mods:
            return ad
    return 'Sistem ve Ayarlar'


def konular():
    """[(grup, giriş, [konu dict, ...]), ...]. Konu: id, title, module, text (işaretli metin)."""
    out = {ad: [] for ad, _i, _m in GRUPLAR}
    for tid, title, grup, mod, body in EK_KONULAR:
        out[grup].append(dict(id=tid, title=title, module=mod, text=body.strip('\n')))
    try:
        import ALFABE_ASISTAN_BILGI as AB
        kb = list(AB.KB)
    except Exception:
        kb = []
    for e in kb:
        lines = []
        if e.get('module'):
            lines.append('# Ekran')
            lines.append('Sol menüde / ilgili bölümde: %s' % e['module'])
        lines.append('# Adımlar')
        for i, s in enumerate(e.get('steps') or [], 1):
            lines.append('%d. %s' % (i, s))
        if e.get('tip'):
            lines.append('! ' + e['tip'])
        out[_grup_of(e.get('module', ''))].append(dict(id=e['id'], title=e['title'], module=e.get('module', ''), text='\n'.join(lines)))
    # kısayol konusu: gerçek tablodan
    try:
        import ALFABE_GUNLUK as G
        ls = ['# Genel']
        ls += ['- %s: %s' % (k, d) for k, d in G.GENERAL_KEYS]
        for scr, ks in G.SCREEN_KEYS.items():
            ls.append('# %s' % scr)
            ls += ['- %s: %s' % (k, d) for k, d in ks]
        out['Başlarken'].append(dict(id='kisayollar_tam', title='Klavye kısayolları (tam liste)', module='', text='\n'.join(ls)))
    except Exception:
        pass
    return [(ad, intro, out[ad]) for ad, intro, _m in GRUPLAR]


def _tum():
    return [t for _g, _i, ts in konular() for t in ts]


def konu_bul(module='', topic_id=''):
    """(grup_adı, konu) — topic_id veya modüle ait ilk konu; yoksa (None, None)."""
    for g, _i, ts in konular():
        for t in ts:
            if topic_id and t['id'] == topic_id:
                return g, t
    if module:
        for g, _i, ts in konular():
            for t in ts:
                if t.get('module') == module:
                    return g, t
    return None, None


def ara(q):
    """Aranan sözcüklerin hepsini içeren konular (başlık + metin)."""
    ws = [w for w in fold(q).split() if w]
    if not ws:
        return _tum()
    return [t for t in _tum() if all(w in fold(t['title'] + ' ' + t['text']) for w in ws)]


def konu_html(t, tam=False):
    h = ['<h2>%s</h2>' % _html.escape(t['title'])]
    ul = False
    for ln in t['text'].split('\n'):
        if ln.startswith('- ') and not ul:
            h.append('<ul>'); ul = True
        elif not ln.startswith('- ') and ul:
            h.append('</ul>'); ul = False
        if ln.startswith('# '):
            h.append('<h3>%s</h3>' % _html.escape(ln[2:]))
        elif ln.startswith('- '):
            h.append('<li>%s</li>' % _html.escape(ln[2:]))
        elif ln.startswith('! '):
            h.append('<p class="not"><b>Not:</b> %s</p>' % _html.escape(ln[2:]))
        elif ln.strip():
            h.append('<p>%s</p>' % _html.escape(ln))
    if ul:
        h.append('</ul>')
    return '\n'.join(h)


def sayfa_html(konular_, baslik='ALFABE SATIŞ PRO Kullanım Kılavuzu', yazdir=True):
    css = ('body{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:24px auto;color:#101827;line-height:1.5}'
           'h1{color:#0b376d}h2{color:#0b376d;border-bottom:1px solid #cbd5e1;padding-bottom:4px;margin-top:28px}'
           'h3{margin-bottom:2px}.not{background:#fffbeb;border-left:4px solid #f59e0b;padding:6px 10px}'
           '@media print{h2{page-break-after:avoid}}')
    body = '\n'.join(konu_html(t) for t in konular_)
    js = '<script>window.onload=function(){window.print()}</script>' if yazdir else ''
    return '<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head><body><h1>%s</h1>%s%s</body></html>' % (
        _html.escape(baslik), css, _html.escape(baslik), body, js)


PDF_AD = 'ALFABE_KULLANIM_KILAVUZU.pdf'


def _klasor():
    return os.path.dirname(os.path.abspath(__file__))


def _dosya_ac(p):
    try:
        if hasattr(os, 'startfile'):
            os.startfile(p)       # Windows
            return True
    except Exception:
        pass
    try:
        import webbrowser
        return bool(webbrowser.open('file://' + os.path.abspath(p)))
    except Exception:
        return False


def ac(parent, module='', topic_id=''):
    """Yardım Merkezi penceresini açar (zaten açıksa öne getirip konuyu seçer). Pencereyi döndürür."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    old = getattr(parent, '_yardim_merkezi', None)
    try:
        if old is not None and old.winfo_exists():
            old.deiconify(); old.lift()
            old._git(module, topic_id)
            return old
    except Exception:
        pass
    w = tk.Toplevel(parent)
    w.title('Yardım')
    try:
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, hh = min(1100, sw - 60), min(720, sh - 90)
        w.geometry('%dx%d+%d+%d' % (ww, hh, max(0, (sw - ww) // 2), max(0, (sh - hh) // 2 - 20)))
    except Exception:
        w.geometry('1000x680')
    w.configure(bg='#f4f7fb')
    parent._yardim_merkezi = w
    st = dict(gecmis=[], simdiki=None, agac=True, ids={})
    bar = tk.Frame(w, bg='#eef2f7'); bar.pack(fill='x')

    def bt(txt, cmd):
        b = tk.Button(bar, text=txt, command=cmd, relief='flat', bg='#eef2f7', font=('Segoe UI', 9), padx=10, pady=4, cursor='hand2')
        b.pack(side='left', padx=2, pady=2); return b
    b_gizle = bt('▯ Gizle', lambda: gizle())
    b_geri = bt('◀ Geri', lambda: geri())
    bt('🖨 Yazdır', lambda: yazdir())
    bt('📄 PDF', lambda: pdf_ac())
    tk.Label(bar, text='Ara:', bg='#eef2f7', font=('Segoe UI', 9)).pack(side='left', padx=(18, 4))
    q = tk.StringVar()
    e = tk.Entry(bar, textvariable=q, width=28, font=('Segoe UI', 10)); e.pack(side='left', pady=4)
    body = tk.PanedWindow(w, orient='horizontal', sashwidth=5, bg='#cbd5e1'); body.pack(fill='both', expand=True)
    left = tk.Frame(body, bg='white'); body.add(left, width=300)
    tv = ttk.Treeview(left, show='tree', selectmode='browse'); tv.pack(side='left', fill='both', expand=True)
    sb = ttk.Scrollbar(left, command=tv.yview); sb.pack(side='right', fill='y'); tv.configure(yscrollcommand=sb.set)
    right = tk.Frame(body, bg='white'); body.add(right)
    box = tk.Text(right, wrap='word', font=('Segoe UI', 11), bg='white', relief='flat', padx=22, pady=14, spacing3=4, cursor='arrow')
    sb2 = ttk.Scrollbar(right, command=box.yview); sb2.pack(side='right', fill='y'); box.pack(side='left', fill='both', expand=True)
    box.configure(yscrollcommand=sb2.set)
    box.tag_configure('t', font=('Segoe UI', 18, 'bold'), foreground='#0b376d', spacing3=10)
    box.tag_configure('h', font=('Segoe UI', 12, 'bold'), foreground='#0b376d', spacing1=12, spacing3=4)
    box.tag_configure('b', lmargin1=18, lmargin2=34)
    box.tag_configure('n', lmargin1=10, lmargin2=34)
    box.tag_configure('note', background='#fffbeb', foreground='#92400e', lmargin1=10, lmargin2=10, spacing1=6, spacing3=6)
    box.tag_configure('g', foreground='#475569', spacing3=8)
    box.tag_configure('link', foreground='#1d4ed8', underline=True, lmargin1=18, lmargin2=18)

    def doldur(liste):
        tv.delete(*tv.get_children()); st['ids'] = {}
        idset = {t['id'] for t in liste}
        for g, intro, ts in konular():
            ts = [t for t in ts if t['id'] in idset]
            if not ts and q.get().strip():
                continue
            gi = tv.insert('', 'end', text='📘 ' + g, open=bool(q.get().strip()), values=())
            st['ids'][gi] = ('g', g, intro, ts)
            for t in ts:
                ti = tv.insert(gi, 'end', text=t['title'])
                st['ids'][ti] = ('t', t)

    def goster(kayit, geri_ekle=True):
        box.configure(state='normal'); box.delete('1.0', 'end')
        if kayit[0] == 'g':
            _k, g, intro, ts = kayit
            box.insert('end', g + '\n', 't'); box.insert('end', intro + '\n\n', 'g')
            for t in ts:
                box.insert('end', '•  ' + t['title'] + '\n', 'b')
            cur = ('g', g)
        else:
            t = kayit[1]
            box.insert('end', t['title'] + '\n', 't')
            for ln in t['text'].split('\n'):
                if ln.startswith('# '):
                    box.insert('end', ln[2:] + '\n', 'h')
                elif ln.startswith('- '):
                    box.insert('end', '•  ' + ln[2:] + '\n', 'b')
                elif ln.startswith('! '):
                    box.insert('end', 'Not: ' + ln[2:] + '\n', 'note')
                elif re.match(r'^\d+\. ', ln):
                    box.insert('end', ln + '\n', 'n')
                elif ln.strip():
                    box.insert('end', ln + '\n')
            cur = ('t', t['id'])
        box.configure(state='disabled'); box.yview_moveto(0)
        if geri_ekle and st['simdiki'] and st['simdiki'] != cur:
            st['gecmis'].append(st['simdiki'])
        st['simdiki'] = cur
        w._acik_konu = cur

    def sec(_e=None):
        s = tv.selection()
        if s and s[0] in st['ids']:
            goster(st['ids'][s[0]])
    tv.bind('<<TreeviewSelect>>', sec)

    def geri():
        if not st['gecmis']:
            return
        onceki = st['gecmis'].pop()
        for iid, k in st['ids'].items():
            if (k[0] == 'g' and onceki == ('g', k[1])) or (k[0] == 't' and onceki == ('t', k[1]['id'])):
                tv.selection_set(iid); tv.see(iid)
                goster(k, geri_ekle=False); return

    def gizle():
        if st['agac']:
            body.forget(left); st['agac'] = False; b_gizle.configure(text='▯ Göster')
        else:
            body.add(left, before=right, width=300); st['agac'] = True; b_gizle.configure(text='▯ Gizle')

    def yazdir():
        cur = getattr(w, '_acik_konu', None)
        ts = [t for t in _tum() if cur and cur[0] == 't' and t['id'] == cur[1]] or _tum()
        try:
            import tempfile
            p = os.path.join(tempfile.gettempdir(), 'alfabe_kilavuz.html')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(sayfa_html(ts))
            _dosya_ac(p)
        except Exception as ex:
            messagebox.showwarning('Yardım', 'Yazdırma sayfası açılamadı: %s' % ex, parent=w)

    def pdf_ac():
        p = os.path.join(_klasor(), PDF_AD)
        if not os.path.exists(p):
            return messagebox.showwarning('Yardım', 'PDF kılavuz dosyası bulunamadı:\n%s\n\nProgram klasöründe %s olmalıdır.' % (p, PDF_AD), parent=w)
        if not _dosya_ac(p):
            messagebox.showwarning('Yardım', 'PDF açılamadı. Dosyayı elle açın:\n%s' % p, parent=w)

    def ara_(*_a):
        doldur(ara(q.get()))
        if q.get().strip():
            for iid in tv.get_children():
                for c in tv.get_children(iid):
                    tv.selection_set(c); tv.see(c); return
    q.trace_add('write', lambda *a: w.after(150, ara_))

    def git(module='', topic_id=''):
        if q.get():
            q.set('')
            doldur(_tum())
        g, t = konu_bul(module, topic_id)
        if t:
            for iid, k in st['ids'].items():
                if k[0] == 't' and k[1]['id'] == t['id']:
                    tv.selection_set(iid); tv.see(iid); return
        for iid, k in st['ids'].items():
            if k[0] == 'g' and k[1] == 'Başlarken':
                tv.item(iid, open=True); tv.selection_set(iid); return
    w._git = git
    w._yardim = dict(tv=tv, box=box, q=q, st=st, geri=geri, gizle=gizle, ara=ara_, doldur=doldur)
    doldur(_tum())
    git(module, topic_id)
    return w
