ALFABE SATIŞ PRO — PROGRAMIN AMACI VE DEĞİŞTİRME KURALLARI  (ÇOK ÖNEMLİ — HER DEĞİŞİKLİKTEN ÖNCE OKU)
==============================================================================================

1) PROGRAMIN AMACI
   Küçük/orta işletme için Windows'ta çalışan tek dosyalı (Python / Tkinter / SQLite) satış, stok, cari, kasa ve
   raporlama programıdır. Günlük işi hızlı, hatasız ve veri kaybı olmadan yürütmek içindir:
   - Hızlı Satış / Cari Satış, barkodlu ve hızlı ürün arama
   - Stok kartları, toplu sayım, kitap hızlı giriş (ISBN / dağıtıcı sitesinden bilgi + fiyat)
   - Cari hesaplar, tahsilat/ödeme, kasa-banka, personel (avans/maaş), gider
   - Çok firmalı / çok dönemli çalışma, ağ üzerinde birden fazla bilgisayar
   - Yedekleme ve YEDEKTEN GERİ YÜKLEME (en kritik bölüm), eski KTS yedeği aktarımı
   - Büyük veriyle (yüzbinlerce satış) yavaşlamadan çalışmak

2) ALTIN KURAL
   Yeni bölüm / özellik eklerken ÇALIŞAN KISIMLAR BOZULMAMALIDIR.
   - Çalışan bir davranışı değiştirmeden önce neden değiştirildiği açık olmalı; kullanıcı istemediyse DOKUNMA.
   - Yeni özellik, mevcut ekranların varsayılan davranışını sessizce değiştirmemeli (yeni seçenekler varsayılan KAPALI/eski davranış).
   - Veri silen / değiştiren her işlemde: önce güvenlik kopyası, onay penceresi, hata olursa geri alma.
   - Her değişiklikten sonra TÜM testler çalıştırılır (tests/hepsini_calistir.py). Kırmızı test varken paket verilmez.
   - Yeni özelliğe test eklenir. Hata düzeltilince hatayı yakalayan test eklenir (aynı hata geri gelmesin).
   - Sürüm numarası ve SURUM_NOTU dosyası her pakette güncellenir; kullanıcıya ne değiştiği ve neyin DEĞİŞMEDİĞİ yazılır.

3) ÖZEL DİKKAT İSTEYEN BÖLÜMLER
   - Yedekten geri yükleme (ALFABE_SATIS_PRO.py: RestoreCenterWindow, _rc_restore_*): seçeneklerin (Tüm / Seçilen dosyalar /
     Sadece stok / Sadece cari / Stok+cari, miktar-bakiye seçenekleri, "seçilmeyen verileri temizle") her biri test edilmiştir.
   - Çoklu bilgisayar oturum denetimi (_presence_*): aynı bilgisayarın kendi kaydı "başka bilgisayar" sayılmamalı.
   - Tablo yerleşimi kaydı (tablo_yerlesimleri.json): tüm tablolarda kalıcı olmalı.
   - Hızlı Satış performansı: büyük veride sorgu planı (CROSS JOIN sıralaması, ifade indeksleri) bozulmamalı.
   - Kitap Hızlı Giriş + ALFABE_ISBN.py: yanlış kitap bilgisi ASLA forma yazılmamalı (sayfada barkod yoksa doldurma).
   - Sayı kutuları: tıklayınca içerik seçili gelir (genel kural, _install_numeric_select).
   - Yazı/arama kutuları: fareyle İLK tıklamada içerik seçili gelir (yeni yazı eskisini değiştirir; 2. tık imleç koyar). Parola/salt-okunur kutulara dokunmaz. (_alf_click_*, test_arama_kutusu_secim.py)

4) ÇALIŞTIRMA / TEST
   python tests/hepsini_calistir.py     (tests klasöründeki tüm testleri sırayla, her biri temiz veriyle çalıştırır; özet verir)
   Tablo yerleşimi testleri (test_tablo_yerlesim*.py) iki aşamalıdır: önce "1", programı kapatıp "2" ile elle çalıştırılır.
   Arayüz testleri ekran gerektirir (Windows'ta doğrudan çalışır; Linux'ta xvfb-run).

5) KULLANICI TERCİHLERİ (özet)
   - Türkçe arayüz, büyük ve okunaklı ekranlar, kırmızı = iptal/silme türü işlemler.
   - Sayı kutularında yeni değeri doğrudan yazmak ister (tıklayınca seçili).
   - Arama kutularında da aynı: tıklayınca eski yazıyı silmeden yenisini yazabilmek ister.
   - ÇALIŞMA ŞEKLİ: istekleri önce yazar; "hazırla/yap" demeden değişiklik YAPILMAZ (not alınır, soru varsa sorulur).
   - Tarih alanı olan işlemlerde geçmiş tarihle kayıt girebilmek ister.
   - "Programın genelinde uygula" denildiğinde ilgili tüm ekranlar taranır, tek ekranla yetinilmez.
