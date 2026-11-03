# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - AYAR LİSTESİ (v1.2.291.452)

Kategori ve ayar tanımları. Burada yazan HER ayar programda gerçekten okunur.
Ayar eklemek için: buraya tanım ekle + programda ayar()/ayar_evet()/ayar_sayi() ile oku (tests/ayar_denetimi.py denetler).
"""


def build(K):
    B, C, I, F, T, D, P, Cat, IZIN, YOK = K.B, K.C, K.I, K.F, K.T, K.D, K.P, K.Cat, K.IZIN, K.YOK
    cats = []

    # ======================================================================== GENEL
    cats.append(Cat('firma', 'Firma Bilgileri', '🏢',
                    'Ünvan, vergi dairesi, adres ve iletişim bilgileri. Fatura, teklif, rapor ve fişlerin üst bilgisinde kullanılır.',
                    special='firma'))
    cats.append(Cat('logo', 'Firma Logosu', '🖼',
                    'Raporlarda, tekliflerde, faturalarda, ekstrelerde ve fişlerde görünen logo.', special='logo'))
    cats.append(Cat('kdv', 'KDV Oranları', '％',
                    'Kullanılacak KDV oranlarını yönetin, varsayılan oranı seçin, toplu KDV değişikliği yapın.', special='kdv'))

    # ======================================================================== SATIŞ
    cats.append(Cat('satis', 'Hızlı Satış', '🛒',
                    'Kasa / perakende satış ekranının davranışı: ürün ekleme, kontroller, iskonto, müşteri ve satış sonrası.', groups=[
        ('Ürün Ekleme ve Sepet', [
            B('sale_merge_lines', 'Aynı ürünü tek satırda topla', 'Evet',
              'Evet: aynı ürün + birim + fiyat tekrar okutulunca miktar artar. Hayır: her okutma ayrı satır olur.'),
            B('enter_nav', 'Enter ile sonraki alana geç', 'Evet',
              'Giriş formlarında Enter tuşu bir sonraki alana atlatır.'),
            B('barcode_auto_advance', 'Barkod tamamlanınca otomatik geç', 'Evet',
              'Barkod okuyucudan gelen tam barkod, Enter beklenmeden bir sonraki alana geçirir.'),
            B('click_add', 'Arama listesinde tek tıkla ekle', 'Evet',
              'Hayır: listeden ürün eklemek için çift tıklama / Enter gerekir.'),
            I('search_delay', 'Canlı arama gecikmesi', 120, 0, 2000, 'Yazmayı bıraktıktan kaç milisaniye sonra arama listesi açılsın. Yavaş bilgisayarda artırın.', unit='ms'),
            B('sale_favorites', 'Favori ürün panelini göster', 'Evet',
              'Hızlı Satış ekranının sağındaki favori / çok satan ürün kutuları. Kapatırsanız sepet alanı genişler.', live=False),
            B('sale_customer_btn', 'Müşteri ekranı düğmesini Hızlı Satış panelinde göster', 'Hayır',
              'Evet: sağ paneldeki favori ürünlerin altında "MÜŞTERİ EKRANI (2. MONİTÖR)" düğmesi görünür. Hayır: düğme yoktur, favori ürünlere daha çok yer kalır.', live=False),
        ]),
        ('Kontroller', [
            C('stock_negative', 'Stokta olmayan ürün satışı', 'Uyar', IZIN,
              'Stok yetersizken ürün eklenirse: İzin Ver = sessiz, Uyar = uyarı ve onay sorar, Engelle = eklemez.'),
            C('sale_zero_price', 'Fiyatı 0 olan ürün satışı', 'Uyar', IZIN,
              'Satış fiyatı tanımsız (0) ürün sepete eklenirken uygulanır.'),
            C('sale_below_cost', 'Maliyetin altında satış', 'Uyar', IZIN,
              'Satır net fiyatı (KDV hariç, iskonto sonrası) alış maliyetinin altına düşerse ödeme sırasında uygulanır.'),
            F('min_profit_pct', 'Asgari kâr oranı', 0, 0, 1000,
              'Maliyetin üzerine en az bu kadar kâr olmayan satırlar "maliyetin altında satış" kuralına takılır. 0 = yalnız maliyet altı.',
              unit='%'),
        ]),
        ('İskonto ve Fiyat', [
            F('sale_discount_limit', 'Genel azami iskonto', 0, 0, 100,
              'Kullanıcıya özel iskonto limiti yoksa bu oran geçerlidir; aşılırsa yetkili onayı istenir. 0 = sınırsız.', unit='%'),
            C('sale_price_edit', 'Satışta birim fiyat değiştirme', 'Serbest', ['Serbest', 'Yetkili Onayı', 'Kapalı'],
              'F7 / satır fiyatı değiştirme. Yetkili Onayı: yetkisi olmayan kullanıcı için yönetici onayı ister.'),
            B('use_wholesale', 'Toptan fiyatı kullan', 'Evet',
              'Evet: fiyat tipi "Toptan" olan cariye satışta toptan fiyat uygulanır.'),
            B('use_special_price', 'Özel fiyatı kullan', 'Evet',
              'Evet: fiyat tipi "Özel" olan cariye satışta özel fiyat uygulanır.'),
        ]),
        ('Müşteri ve Ödeme', [
            C('sale_customer', 'Perakende satışta müşteri', 'İsteğe Bağlı', ['İsteğe Bağlı', 'Zorunlu'],
              'Zorunlu: müşteri seçilmeden ödeme alınamaz.'),
            Spec_dyn(K, 'default_pos', 'Kart ödemesinde POS seçimi', '(Her seferinde sor)', 'pos',
                     'Birden fazla POS tanımlıysa: sabit bir POS seçerseniz kart ödemesinde sorulmaz.'),
        ]),
        ('Satış Sonrası', [
            B('sale_done_message', '"Satış tamamlandı" mesajı', 'Evet',
              'Hayır: F1 / F2 ile ödeme alındığında bilgi kutusu çıkmaz, doğrudan yeni satışa geçilir (hızlı kasa).'),
            B('sale_print_after', 'Fişi otomatik önizle / yazdır', 'Hayır',
              'Evet: F1 / F2 ile tamamlanan her satışın fişi baskı ön izlemesinde otomatik açılır.'),
        ]),
        ('İade / Değişim', [
            I('sale_return_days', 'İade süresi', 0, 0, 3650,
              'Satış tarihinden bu kadar gün sonra iade kabul edilmez; yetkili onayıyla geçilebilir. 0 = sınırsız.', unit='gün'),
        ]),
        ('Terazi (Tartılı Ürün) Barkodu', [
            B('scale_enabled', 'Terazi barkodunu çöz', 'Hayır',
              'Etiketli terazi barkodundan ürünü ve gramajı otomatik okur (örn. 2 + ürün kodu + ağırlık + kontrol).'),
            T('scale_prefix', 'Barkod ön eki', '2', 'Terazi barkodunun ilk rakam(lar)ı. Çoğu terazide 2 veya 28 / 29.',
              depends=('scale_enabled', ['Evet']), placeholder='2'),
            I('scale_code_len', 'Ürün kodu hane sayısı', 5, 1, 8, 'Ön ekten sonra gelen ürün kodu (PLU / stok kodu) kaç haneli.',
              depends=('scale_enabled', ['Evet'])),
            I('scale_weight_len', 'Ağırlık hane sayısı', 5, 1, 8, 'Ürün kodundan sonra gelen ağırlık / miktar kaç haneli.',
              depends=('scale_enabled', ['Evet'])),
            I('scale_divisor', 'Ağırlık bölücü', 1000, 1, 1000000,
              'Okunan sayı bu değere bölünür. 1000: gram → kilogram. Adetli ürünler için 1.',
              depends=('scale_enabled', ['Evet'])),
        ]),
    ]))

    cats.append(Cat('carisatis', 'Cari Satış', '👤',
                    'Müşteriye özel (veresiye / açık hesap / senet) satış ekranı.', groups=[
        ('Ödeme ve Belge', [
            C('cari_sale_payment', 'Varsayılan ödeme şekli', 'Açık Hesap',
              ['Açık Hesap', 'Nakit', 'Kart / POS', 'Havale/IBAN', 'Senet / Taksitli', 'Parçalı Ödeme'],
              'Cari Satış ekranı açıldığında seçili gelen ödeme şekli.'),
            B('cari_sale_auto_print', 'Kaydedince belgeyi yazdır', 'Evet',
              'Satış kaydedilince belge baskı ön izlemesinde otomatik açılır.'),
        ]),
    ]))

    cats.append(Cat('kampanya', 'Kampanya', '🏷',
                    'Birden fazla kampanya aynı ürüne uyduğunda ve zararına kampanyalarda programın davranışı.', groups=[
        ('Kampanya Uygulama', [
            C('campaign_conflict', 'Kampanya çakışırsa', 'İlk Kampanya', ['İlk Kampanya', 'Son Kampanya', 'En Avantajlı'],
              'İlk/Son: öncelik sırasına göre. En Avantajlı: müşteriye en çok indirim sağlayan kampanya uygulanır.'),
            C('campaign_loss', 'Maliyetin altına düşüren kampanya', 'İzin Ver', IZIN,
              'Kampanya sonrası satır maliyetin altına düşerse: İzin Ver / Uyar (satışta sorar) / Engelle (indirimi uygulamaz).'),
        ]),
    ]))

    cats.append(Cat('hizmet', 'Hizmetler / Fotokopi', '🖨',
                    'F6 Hızlı Hizmet penceresinde seçili gelen değerler.', groups=[
        ('Varsayılan Seçimler', [
            C('service_cat', 'Varsayılan hizmet', 'Fotokopi',
              ['Fotokopi', 'Çıktı', 'Tarama', 'Laminasyon', 'Spiral', 'Ciltleme', 'Kimlik Kaplama'],
              'F6 açıldığında seçili gelen hizmet türü.'),
            C('service_copy', 'Varsayılan renk', 'Siyah-Beyaz', ['Siyah-Beyaz', 'Renkli'], 'Fotokopi / çıktı için.'),
            C('service_size', 'Varsayılan ebat', 'A4', ['A4', 'A3', 'A5', 'A6'], 'Fotokopi / çıktı / tarama için.'),
            C('service_sides', 'Varsayılan yüz', 'Tek Taraf', ['Tek Taraf', 'Çift Taraf']),
            C('lamination_size', 'Laminasyon varsayılan ebadı', 'A4', ['A4', 'A5', 'A6', 'Kimlik'],
              'Laminasyon seçildiğinde gelen ebat.'),
        ]),
    ]))

    # ======================================================================== STOK & ALIŞ
    cats.append(Cat('stok', 'Stok & Fiyat', '📦',
                    'Yeni stok kartı varsayılanları, barkod kuralları, maliyet ve fiyat yuvarlama.', groups=[
        ('Yeni Stok Kartı Varsayılanları', [
            Spec_dyn(K, 'stock_unit', 'Varsayılan birim', 'Adet', 'units', 'Yeni stok kartında seçili gelen birim.'),
            Spec_dyn(K, 'stock_vat', 'Varsayılan KDV oranı', '20', 'vat', 'Yeni stok kartında seçili gelen KDV oranı. Oranları "KDV Oranları" bölümünden yönetin.'),
            I('stock_min_default', 'Varsayılan kritik stok', 0, 0, 1000000,
              'Yeni stok kartında "minimum stok" olarak yazılır; altına düşünce uyarı çıkar. 0 = boş.', unit='adet'),
        ]),
        ('Barkod', [
            B('stock_barcode_required', 'Barkod zorunlu', 'Evet',
              'Evet: barkodsuz stok kartı kaydedilemez (otomatik barkod açıksa üretilir).'),
            B('stock_auto_barcode', 'Boş barkodu otomatik üret', 'Evet',
              'Barkod girilmeden kaydedilen kartta programın iç barkodu üretilir.'),
            C('stock_barcode_type', 'Varsayılan barkod türü', 'EAN13', ['EAN13', 'EAN8'],
              'Yeni stok kartında seçili gelen barkod türü.'),
        ]),
        ('Toplu Stok Sayımı', [
            C('count_scan_mode', 'Sayımda okutma biçimi', 'Ekle (+1 / Adet kutusu)', ['Ekle (+1 / Adet kutusu)', 'Miktar sor', 'Üzerine yaz'],
              'Ekle: her okutma sayıma 1 (ya da Adet kutusundaki / "12*barkod" ile yazdığınız) adet ekler, en hızlı yöntemdir. Miktar sor: her ürün için toplam miktar sorulur. Üzerine yaz: okutulan miktar toplamın yerine geçer.'),
            B('count_sound', 'Sayımda sesli uyarı', 'Evet', 'Başarılı okutmada kısa, bulunamayan barkodda uzun ses çalar.'),
            B('count_blind_default', 'Yeni sayım varsayılan olarak KÖR olsun', 'Hayır',
              'Kör sayımda sayan kişi sistem miktarını ve farkı görmez (hile ve etkilenme riskini azaltır). Fark görmek için yetkili onayı gerekir.'),
            C('count_unknown_action', 'Bilinmeyen barkod', 'Sor', ['Sor', 'Bilinmeyenlere kaydet', 'Yoksay'],
              'Sayımda stok kartı olmayan barkod okutulursa: Sor (yeni kart aç / listeye kaydet / atla), doğrudan listeye kaydet ya da yoksay.'),
            B('count_approval', 'Sayımı kesinleştirirken yönetici onayı', 'Evet',
              'Stok miktarlarını toplu değiştirdiği için kesinleştirmede yönetici onayı istenir.'),
            F('count_warn_value', 'Büyük sayım farkı uyarısı', 5000, 0, 100000000,
              'Net sayım farkının (fazla - eksik) mutlak değeri bu tutarı aşarsa kesinleştirmeden önce ek uyarı verilir. 0 = kapalı.', unit='₺'),
        ]),
        ('Maliyet ve Fiyat', [
            C('cost_method', 'Satışta maliyet yöntemi', 'Ağırlıklı Ortalama', ['Ağırlıklı Ortalama', 'Son Alış'],
              'Satış satırına yazılan maliyet (kâr raporlarının temeli). Ağırlıklı ortalama: stok maliyet ortalaması. Son alış: en son alış fiyatı.'),
            C('price_rounding', 'Toplu fiyat yuvarlama adımı', '0,50', ['0,01', '0,05', '0,10', '0,50', '1', '5', '10'],
              'Fiyat Yönetimi\'nde toplu fiyat hesaplarken kullanılan varsayılan yuvarlama adımı.'),
        ]),
    ]))

    cats.append(Cat('alis', 'Alış & Mal Kabul', '📥',
                    'Tedarikçi alışı girişinde varsayılanlar ve kontroller.', groups=[
        ('Alış Girişi', [
            C('purchase_payment', 'Varsayılan ödeme', 'Vadeli', ['Peşin', 'Vadeli', 'İşletme Kredi Kartı'],
              'Yeni alış belgesinde seçili gelen ödeme türü.'),
            I('purchase_due_days', 'Varsayılan vade', 0, 0, 3650,
              'Yeni alış belgesinde vade tarihi, bugünden bu kadar gün sonra gelir. 0 = boş bırak.', unit='gün'),
            B('purchase_cost_update', 'Alışta stok maliyetini güncelle', 'Evet',
              'Evet: kesinleşen alış son alış ve ortalama maliyeti günceller. Hayır: yalnız stok miktarı artar.'),
            F('purchase_price_warn_pct', 'Alış fiyatı artış uyarısı', 0, 0, 1000,
              'Alış fiyatı önceki alıştan bu orandan fazla yükselmişse kayıtta uyarır. 0 = kapalı.', unit='%'),
        ]),
        ('Alış Sonrası', [
            C('purchase_label', 'Alınan ürünler için etiket', 'Gönderme', ['Gönderme', 'Sor', 'Her Zaman'],
              'Alış kesinleşince ürünleri Etiket Merkezi kuyruğuna gönderir (adet = alınan miktar).'),
        ]),
    ]))

    # ======================================================================== CARİ & FİNANS
    cats.append(Cat('cari', 'Cari & Risk', '👥',
                    'Yeni cari kartı varsayılanları ve risk limiti davranışı.', groups=[
        ('Yeni Cari Kartı', [
            C('cari_role', 'Varsayılan cari türü', 'Müşteri', ['Müşteri', 'Tedarikçi', 'Ortak'],
              'Yeni cari kartında seçili gelen tür.'),
            I('cari_due_days', 'Varsayılan vade günü', 30, 0, 3650,
              'Vadesi tanımlanmamış carilerde açık hesap satışın vadesi ve yeni cari kartındaki vade günü.', unit='gün'),
            F('cari_default_limit', 'Varsayılan risk limiti', 0, 0, 1000000000,
              'Yeni cari kartında risk limiti olarak yazılır. 0 = limitsiz.', unit='₺'),
        ]),
        ('Risk Kontrolü', [
            C('cari_risk_action', 'Risk limiti aşılırsa', 'Uyar', ['İzin Ver', 'Uyar', 'Yönetici Onayı', 'Engelle'],
              'Cari satışta bakiye + yeni borç, cari kartındaki risk limitini aşarsa uygulanır.'),
        ]),
    ]))

    # ======================================================================== BELGE & YAZDIRMA
    cats.append(Cat('belge', 'Belge Numaraları & Klasörler', '🧾',
                    'Belge numarası ön ekleri ve belge / PDF klasörleri. Yeni ön ek yeni belgelerde geçerlidir; eski belgeler değişmez.', groups=[
        ('Belge Ön Ekleri', [
            T('prefix_purchase', 'Alış belgesi ön eki', 'AL', 'Örn. AL-2026-000001. En çok 4 harf/rakam.', placeholder='AL'),
            T('prefix_receipt', 'Mal kabul belgesi ön eki', 'MK', 'Örn. MK-2026-000001.', placeholder='MK'),
            T('prefix_offer', 'Teklif ön eki', 'TF', 'Örn. TF-2026-000001.', placeholder='TF'),
            T('prefix_order', 'Sipariş ön eki', 'SP', 'Örn. SP-2026-000001.', placeholder='SP'),
        ]),
        ('Klasörler', [
            D('report_pdf_dir', 'Rapor / PDF kayıt klasörü', '', '"Kaydet" pencereleri (PDF, Excel, rapor dışa aktarma) bu klasörde açılır. Boşsa Windows son kullanılan klasörü gösterir.'),
            D('document_dir', 'Belge / içe aktarma klasörü', '', '"Aç" pencereleri (dosya seç, içe aktar, belge ekle) bu klasörde açılır. Boşsa Windows son kullanılan klasörü gösterir.'),
        ]),
    ]))

    cats.append(Cat('yazici', 'Yazıcı & Baskı', '🖨',
                    'Hangi belge hangi yazıcıdan çıksın; kağıt boyutu, yön ve kopya sayısı.', groups=[
        ('Yazıcılar', [
            P('receipt_printer', 'Fiş yazıcısı', YOK, 'Satış fişleri bu yazıcıyla önerilir.'),
            P('report_printer', 'Rapor / belge yazıcısı', YOK, 'Rapor, teklif, ekstre gibi A4 belgeler bu yazıcıyla önerilir.'),
            P('label_printer', 'Etiket yazıcısı', YOK, 'Etiket Merkezi\'nde seçili gelen yazıcı.'),
        ]),
        ('Baskı Varsayılanları', [
            C('paper_size', 'Varsayılan kâğıt', 'A4', ['A4', 'A3', 'A5'], 'Rapor ve belge tasarımlarında kayıtlı tasarımı olmayanların kâğıt boyutu. (Fiş genişliği Fiş & Yazıcı ayarlarındadır.)'),
            C('paper_orientation', 'Varsayılan yön', 'Otomatik', ['Otomatik', 'Dikey', 'Yatay'], 'Otomatik: raporlar kendi uygun yönüyle, belgeler dikey açılır. Dikey / Yatay seçerseniz kayıtlı tasarımı olmayan raporlar buna göre açılır.'),
            I('print_copies', 'Varsayılan kopya sayısı', 1, 1, 20, 'Baskı ön izlemesi açılırken kopya sayısı.'),
        ]),
    ], extra_after='yazici_araclari'))

    cats.append(Cat('etiket', 'Etiket & Barkod', '🏷',
                    'Etiket kuyruğu ve tasarımları Etiket Merkezi\'nde yönetilir; buradakiler yeni açılışta seçili gelir.', groups=[
        ('Etiket Varsayılanları', [
            T('label_template', 'Varsayılan etiket şablonu', 'Normal Fiyat',
              'Etiket Merkezi açılınca seçili şablon adı. Şablonlar Etiket Merkezi → Şablonlar bölümünden seçilir.'),
        ]),
    ], extra_after='etiket_araclari'))

    cats.append(Cat('cihaz', 'Cihazlar & Entegrasyonlar', '🔌',
                    'Yazıcı, barkod okuyucu, para çekmecesi, NAS ve ağ cihazlarının kontrolü. Cihaz tanımları Cihazlar & Entegrasyonlar ekranında yapılır.', groups=[
        ('Cihaz Kullanımı', [
            B('device_use_defaults', 'Cihaz tanımındaki varsayılanları kullan', 'Evet',
              'Evet: Yazıcı & Baskı ayarında yazıcı seçilmemişse fiş / etiket / rapor yazıcısı ve ikinci yedek (NAS) konumu, Cihazlar ekranında "varsayılan" işaretli cihazdan alınır.'),
            B('drawer_on_cash', 'Nakit satışta para çekmecesini aç', 'Hayır',
              'Evet: nakit içeren satış kesinleşince bu bilgisayarın varsayılan para çekmecesine açma komutu gönderilir. Çekmece yoksa ya da hata olursa satış etkilenmez.'),
        ]),
        ('Kontrol', [
            B('device_test_on_start', 'Açılışta cihazları kontrol et', 'Evet',
              'Program açıldıktan kısa süre sonra etkin cihazlar arka planda sınanır; sonuç cihaz listesine ve olay geçmişine yazılır.'),
            B('device_warn_on_start', 'Sorun varsa açılışta uyar', 'Evet',
              'Açılış kontrolünde erişilemeyen cihaz bulunursa bir kez bilgi verilir. Program kilitlenmez.',
              depends=('device_test_on_start', ['Evet'])),
            I('device_test_timeout', 'Bağlantı zaman aşımı', 3, 1, 30,
              'Ağ / TCP ve klasör testlerinde bir cihaz için en çok beklenecek süre.', unit='sn'),
            I('device_event_keep_days', 'Olay geçmişini sakla', 180, 0, 3650,
              'Cihaz olay geçmişi bu günden eski kayıtları açılışta siler. 0 = hiç silme.', unit='gün'),
        ]),
    ], extra_after='cihaz_araclari'))

    # ======================================================================== GÖRÜNÜM
    cats.append(Cat('gorunum', 'Görünüm & Ekran', '🎨',
                    'Ekran yazı büyüklüğü, menü, açılış ekranı ve sayı biçimi.', groups=[
        ('Açılış', [
            C('start_screen', 'Açılışta ilk ekran', 'Ana Sayfa',
              ['Ana Sayfa', 'Hızlı Satış', 'Cari Satış', 'Stok Merkezi', 'Cari Merkezi'],
              'Program açılınca (ve kullanıcı girişinden sonra) bu ekran açılır.', live=False),
            B('status_bar', 'Alt durum çubuğunu göster', 'Evet',
              'Ana sayfa altındaki hızlı işlem ve durum çubuğu.'),
        ]),
        ('Yazı ve Menü', [
            C('ui_text_mode', 'Ekran yazı büyüklüğü', 'Büyük', ['Normal', 'Büyük', 'Çok Büyük'],
              'Menü, düğme ve form yazılarının genel boyutu. Açık pencerelerde yeniden açınca tam uygulanır.'),
            I('nav_width', 'Sol menü genişliği', 270, 180, 420, 'Ana sayfadaki sol menünün genişliği.', unit='px'),
            I('font_nav', 'Sol menü yazı boyutu', 0, 0, 16, '0 = otomatik (ekran küçükse sıkışık, değilse normal). Menü yazısını büyütmek için 8-16 arası girin.', unit='pt'),
            I('font_sale_price', 'Hızlı Satış toplam yazısı', 27, 14, 60, 'Satış tutarı kutusundaki büyük rakamın boyutu.', unit='pt', live=False),
            I('font_pricecheck', 'Fiyat Gör rakamı', 100, 24, 200, 'Fiyat Gör ekranındaki dev fiyat yazısının boyutu.', unit='pt', live=False),
        ]),
        ('Sayı Biçimi', [
            I('number_decimal_places', 'Ondalık basamak sayısı', 2, 0, 4, 'Tutarlarda virgülden sonra kaç basamak gösterilsin.'),
        ]),
    ]))

    cats.append(Cat('tablo', 'Tablolar', '▦',
                    'Listelerin (tabloların) yazı boyutu, satır yüksekliği ve hizalama davranışı.', groups=[
        ('Boyut', [
            I('table_font_size', 'Tablo yazı boyutu', 10, 8, 18, 'Tüm listelerin yazı boyutu.', unit='pt'),
            C('table_row_mode', 'Satır yüksekliği', 'Otomatik', ['Otomatik', 'Elle'], 'Otomatik: yazı boyutuna göre.'),
            I('table_row_height', 'Elle satır yüksekliği', 24, 18, 60, 'Yalnız "Elle" seçiliyken geçerlidir.', unit='px',
              depends=('table_row_mode', ['Elle'])),
            B('table_auto_width', 'Sütun genişliğini içeriğe uydur', 'Evet', 'Tablo açılırken sütunlar içeriğe göre ayarlanır.'),
        ]),
        ('Hizalama', [
            C('table_text_align', 'Metin hizalama', 'Sola', ['Sola', 'Ortala', 'Sağa']),
            C('table_number_align', 'Rakam hizalama', 'Ortala', ['Sola', 'Ortala', 'Sağa']),
            C('table_header_align2', 'Başlık hizalama', 'Sütunla Aynı', ['Sütunla Aynı', 'Sola', 'Ortala', 'Sağa']),
        ]),
        ('Hatırlama', [
            B('table_layout_memory', 'Sütun düzenini hatırla', 'Evet',
              'Sütun genişliği, sırası, gizli sütunlar ve yazı boyutu her tablo için kaydedilir ve sonraki açılışta geri gelir.', live=False),
        ]),
    ], extra_after='tablo_araclari'))

    cats.append(Cat('rapor', 'Rapor & Analiz', '📊',
                    'Rapor ekranı varsayılanları.', groups=[
        ('Varsayılanlar', [
            C('report_period', 'Rapor açılınca tarih aralığı', 'Bu Ay', ['Bugün', 'Dün', 'Bu Hafta', 'Geçen Hafta', 'Bu Ay', 'Geçen Ay'],
              'Rapor & Analiz ekranı açılınca seçili gelen dönem.'),
        ]),
    ]))

    # ======================================================================== SİSTEM
    cats.append(Cat('yedek', 'Yedekleme', '💾',
                    'Otomatik yedekler, saklama süresi ve ikinci kopya (USB / NAS / ağ klasörü).', groups=[
        ('Otomatik Yedek', [
            B('backup_on_close', 'Program kapanırken yedek al', 'Evet', 'Programdan çıkarken doğrulanmış tam yedek alınır.'),
            B('backup_daily', 'Günlük otomatik yedek', 'Evet', 'Program açıkken günde bir kez, gün içinde yedek alınmamışsa yedek alır.'),
            I('backup_keep_days', 'Yedekleri saklama süresi', 30, 1, 36500, 'Bu günden eski otomatik yedekler temizlenir.', unit='gün'),
            B('backup_single', 'Kapanış yedeğini tek dosyada tut', 'Evet', 'Evet: kapanış yedeği her seferinde öncekinin yerine geçer (disk dolmaz).'),
            I('alert_backup_hours', 'Yedek eskirse uyar', 36, 1, 87600, 'Son yedek bu süreden eskiyse ana sayfada uyarı gösterilir.', unit='saat'),
        ]),
        ('İkinci Kopya', [
            D('nas_backup_path', '2. yedek klasörü (USB / NAS / ağ)', '', 'Her yedekten sonra dosya bu klasöre de kopyalanır ve SHA256 ile doğrulanır. Boşsa ikinci kopya alınmaz.'),
        ]),
    ], extra_after='yedek_araclari'))

    cats.append(Cat('guvenlik', 'Kullanıcı & Güvenlik', '🔒',
                    'Giriş, şifre politikası, onaylar ve kullanıcı / rol yetkileri.', special='guvenlik', groups=[
        ('Giriş ve Oturum', [
            B('login_required', 'Açılışta kullanıcı adı / şifre sor', 'Hayır',
              'Kapalıyken programı açan herkes Yönetici olur; yetki ve onaylar uygulanmaz. Açmadan önce şifreli bir Yönetici tanımlayın.'),
            I('session_timeout', 'Boşta kalınca ekranı kilitle', 30, 0, 1440, 'Belirtilen dakika işlem yapılmazsa şifre ister. 0 = kapalı. Ctrl+L anında kilitler.', unit='dk'),
        ]),
        ('Şifre Politikası', [
            I('min_pw_len', 'En az şifre uzunluğu', 6, 4, 64, '', unit='karakter'),
            I('max_failed', 'Hesabı kilitleyen hatalı deneme', 5, 1, 50, 'Bu kadar art arda hatalı şifrede hesap kilitlenir.', unit='deneme'),
            I('lock_minutes', 'Hesap kilit süresi', 5, 1, 1440, '', unit='dk'),
            I('pw_expire_days', 'Şifre geçerlilik süresi', 0, 0, 3650, 'Süre dolunca kullanıcı şifresini değiştirmek zorundadır. 0 = süresiz.', unit='gün'),
        ]),
        ('Onaylar', [
            B('critical_password', 'Kritik işlemde yetkili onayı', 'Evet', 'Geri yükleme, dönem silme gibi işlemlerde yetkisi olmayana yönetici onayı sorulur.'),
            B('discount_approval', 'İskonto limiti aşılınca onay', 'Evet', 'Kullanıcının / genel iskonto limitini aşan satışta yetkili onayı istenir.'),
            B('return_approval', 'İadede yetkili onayı', 'Evet', 'İade yetkisi olmayan kullanıcı için yönetici onayı sorulur.'),
            B('cancel_approval', 'Satış / satır iptalinde onay', 'Evet', 'Kaydedilmemiş satırın veya fişin iptalinde yetkili onayı sorulur.'),
        ]),
    ]))

    cats.append(Cat('sistem', 'Açılış, Kapanış & Bakım', '🛠',
                    'Açılışta ve kapanışta çalışan kontroller; büyük veride performans.', groups=[
        ('Açılış ve Kapanış', [
            B('startup_health', 'Açılışta günlük özet / veri sağlık taraması', 'Evet',
              'Program açıldıktan kısa süre sonra yedek, negatif stok, vadesi geçen vb. kontroller yapılır; sorun varsa Günlük Özet penceresi açılır. Sorun yoksa sizi rahatsız etmez.'),
            B('close_open_check', 'Kapanışta açık işlem kontrolü', 'Evet',
              'Kaydedilmemiş satış / belge varken programdan çıkılırsa uyarır.'),
        ]),
        ('Liste ve Yenileme', [
            I('list_limit', 'Liste kayıt limiti', 500, 10, 1000000, 'Liste ve arama pencerelerinde aynı anda yüklenecek en çok kayıt. Düşük değer büyük veride programı hızlandırır; aradığınız kayıt görünmüyorsa aramayı daraltın.'),
            I('refresh_seconds', 'Ana sayfa yenileme', 0, 0, 86400, 'Başka bilgisayar / pencere veri değiştirdiğinde Ana Sayfa en az bu kadar saniyede bir kendini yeniler. 0 = otomatik yenileme kapalı (yalnız sayfa her açıldığında güncellenir).', unit='sn'),
        ]),
    ]))

    cats.append(Cat('kisayol', 'Klavye Kısayolları', '⌨',
                    'Programdaki tüm kısayol tuşlarının rehberi.', special='kisayol'))
    cats.append(Cat('yonetim', 'Ayar Yönetimi', '⚙',
                    'Hazır ayar profilleri, değişiklik geçmişi, yedek / geri yükleme ve ayar sağlık kontrolü.', special='yonetim'))
    for c in cats:
        for s in c.specs():
            if c.id in ('guvenlik', 'yedek', 'sistem') or s.key.startswith('prefix_'):
                s.firma = True
    return cats


def Spec_dyn(K, key, label, default, dyn, help=''):
    """Seçenekleri çalışma anında veritabanından gelen ayar (birim, KDV, POS vb.)."""
    s = K.Spec(key, label, 'choice', default, help, options=None)
    s.dyn = dyn
    return s
