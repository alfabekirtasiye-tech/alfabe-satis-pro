# -*- coding: utf-8 -*-
"""ALFABE yedekleme yardımcıları (Sistem Merkezi).

Diğer satış / ön muhasebe programlarının yedekleme ekranlarındaki yaygın uygulamalar:
  * Yedek öncesi disk alanı denetimi (yarım kalan, diski dolduran yedek olmasın).
  * İkinci kopya (NAS / USB / bulut klasörü): önce geçici ad (.part) ile kopyalanır, SHA-256 ile doğrulanır, sonra adı verilir;
    yarım dosya asla "yedek" görünmez. Büyük dosyalar parça parça okunur (bellek şişmez).
  * Saklama politikası: gün sınırı + EN AZ N yedek her zaman korunur (uzun süre kullanılmayan bilgisayarda hepsi silinmesin);
    ikinci kopya klasörüne de uygulanır; güvenlik kopyaları (geri yükleme / aktarım öncesi .db) ayrıca temizlenir.
  * Durum özeti: kaç yedek, toplam boyut, en yeni yedeğin yaşı, disk boşluğu, ikinci kopya durumu.
  * Günlük tablolarının sınırsız büyümesini önleyen budama.
Saf Python; arayüz içermez, kolayca sınanabilir.
"""
import datetime
import hashlib
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

MIN_KEEP_DEFAULT = 5
CHUNK = 4 * 1024 * 1024
PACKAGE_PATTERNS = ('ALFABE_TAM_YEDEK_*.zip',)
CLOSE_PATTERNS = ('ALFABE_2*.bck', 'ALFABE_KAPANIS_YEDEGI_*.zip')
SAFETY_PATTERNS = ('GERI_YUKLEME_ONCESI_*.db', 'BCK_YUKLEME_ONCESI_*.db', 'AKTARIM_ONCESI_*.db')
MAINT_PATTERNS = ('BAKIM_ONCESI_*.db',)


def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_copy(source, target):
    """(tamam_mı, ileti). Boyut + SHA-256 (parça parça) + ZIP bütünlüğü."""
    source, target = Path(source), Path(target)
    if not target.exists():
        return False, 'İkinci yedek dosyası bulunamadı.'
    if target.stat().st_size != source.stat().st_size:
        return False, 'Dosya boyutu eşleşmiyor.'
    if sha256_file(source) != sha256_file(target):
        return False, 'SHA256 doğrulaması eşleşmiyor.'
    try:
        with zipfile.ZipFile(str(target), 'r') as z:
            bad = z.testzip()
            if bad:
                return False, 'ZIP doğrulama hatası: ' + bad
    except Exception as e:
        return False, 'Yedek paketi açılamadı: ' + str(e)
    return True, 'OK'


def copy_atomic(source, folder):
    """source -> folder/<ad>. Önce .part, doğrulama, sonra yeniden adlandırma. Hata olursa hedefte artık bırakmaz. Hedef yolu döner."""
    source, folder = Path(source), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / source.name
    part = folder / (source.name + '.part')
    try:
        shutil.copyfile(str(source), str(part))
        ok, msg = verify_copy(source, part)
        if not ok:
            raise RuntimeError(msg)
        os.replace(str(part), str(final))
    finally:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
    return final


def free_space_check(folder, db_path, factor=2.5, extra_mb=50):
    """Yedek için yeterli boş alan var mı? (tamam_mı, boş_bayt, gereken_bayt). Okunamazsa tamam sayılır."""
    try:
        need = int(Path(db_path).stat().st_size * factor + extra_mb * 1024 * 1024)
        free = shutil.disk_usage(str(folder)).free
        return free >= need, free, need
    except Exception:
        return True, 0, 0


def fmt_size(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return ('%d %s' % (n, u)) if u == 'B' else ('%.1f %s' % (n, u)).replace('.', ',')
        n /= 1024.0


def _files(folder, patterns):
    folder = Path(folder)
    seen, out = set(), []
    if not folder.exists():
        return out
    for pat in patterns:
        for p in folder.glob(pat):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def prune(folder, patterns, keep_days, min_keep=MIN_KEEP_DEFAULT, protect=()):
    """keep_days'ten eski dosyaları siler AMA en yeni min_keep dosyayı (ve protect içindekileri) hiç silmez. Silinenlerin adlarını döner."""
    removed = []
    try:
        files = sorted(_files(folder, patterns), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return removed
    keep = set(files[:max(0, int(min_keep))]) | {Path(x) for x in protect}
    cut = time.time() - max(1, int(keep_days)) * 86400
    for p in files:
        if p in keep:
            continue
        try:
            if p.stat().st_mtime < cut:
                p.unlink()
                removed.append(p.name)
        except OSError as e:
            _log('Yedek temizliği: eski yedek silinemedi: ' + str(p), e)
    return removed


def prune_all(back_dir, keep_days, nas_dir='', protect=(), single_close=True):
    """Ana klasör + ikinci kopya + güvenlik kopyaları. Kapanış (.bck) yedekleri 'tek yedek' kapalıysa gün sınırıyla budanır."""
    removed = {'main': [], 'second': [], 'safety': []}
    removed['main'] += prune(back_dir, PACKAGE_PATTERNS, keep_days, MIN_KEEP_DEFAULT, protect)
    if not single_close:
        removed['main'] += prune(back_dir, CLOSE_PATTERNS, keep_days, MIN_KEEP_DEFAULT, protect)
    if nas_dir:
        try:
            if Path(nas_dir).resolve() != Path(back_dir).resolve():
                removed['second'] += prune(nas_dir, PACKAGE_PATTERNS + CLOSE_PATTERNS, max(int(keep_days), 7), MIN_KEEP_DEFAULT, protect)
        except OSError as e:
            _log('Yedek temizliği: ikinci yedek konumu temizlenemedi', e)
    removed['safety'] += prune(back_dir, SAFETY_PATTERNS, max(60, int(keep_days) * 2), 5, protect)
    removed['safety'] += prune(Path(back_dir) / 'bakim_oncesi', MAINT_PATTERNS, 30, 3, protect)
    return removed


def prune_tables(c, events_keep=2000, history_keep=3000):
    """system_events / backup_history tablolarının sınırsız büyümesini önler."""
    for table, keep in (('system_events', events_keep), ('backup_history', history_keep)):
        try:
            c.execute('DELETE FROM %s WHERE id NOT IN (SELECT id FROM %s ORDER BY id DESC LIMIT ?)' % (table, table), (keep,))
        except sqlite3.Error as e:
            _log('Bakım: ' + table + ' kayıtları kısaltılamadı', e)
    try:
        c.commit()
    except sqlite3.Error:
        pass


def storage_summary(back_dir, nas_dir='', db_path=None):
    """Salt okunur özet: yedek sayısı/boyutu, en yeni yedek (yol, saat önce), boş disk, ikinci kopyada en yeni yedek."""
    out = {'count': 0, 'size': 0, 'newest': None, 'age_hours': None, 'free': None, 'db_size': 0, 'second_newest': None, 'second_age_hours': None, 'same_disk': False}
    files = _files(back_dir, PACKAGE_PATTERNS + CLOSE_PATTERNS)
    if files:
        out['count'] = len(files)
        out['size'] = sum(p.stat().st_size for p in files)
        newest = max(files, key=lambda p: p.stat().st_mtime)
        out['newest'] = str(newest)
        out['age_hours'] = (time.time() - newest.stat().st_mtime) / 3600.0
    try:
        out['free'] = shutil.disk_usage(str(back_dir)).free
    except Exception:
        pass
    try:
        if db_path:
            out['db_size'] = Path(db_path).stat().st_size
            out['same_disk'] = os.path.splitdrive(str(Path(db_path).resolve()))[0].lower() == os.path.splitdrive(str(Path(back_dir).resolve()))[0].lower() if os.name == 'nt' \
                else Path(db_path).stat().st_dev == Path(back_dir).stat().st_dev
    except Exception:
        pass
    if nas_dir:
        sf = _files(nas_dir, PACKAGE_PATTERNS + CLOSE_PATTERNS)
        if sf:
            nn = max(sf, key=lambda p: p.stat().st_mtime)
            out['second_newest'] = str(nn)
            out['second_age_hours'] = (time.time() - nn.stat().st_mtime) / 3600.0
    return out


def quick_zip_ok(path):
    """Hızlı sağlık: ZIP merkezi dizini okunur + gerekli dosyalar var mı (tüm veriyi açmaz; testzip değildir)."""
    try:
        with zipfile.ZipFile(str(path), 'r') as z:
            names = set(z.namelist())
            return 'alfabe_veritabani.db' in names and 'manifest.json' in names
    except Exception:
        return False


def status_level(age_hours, limit_hours):
    """'ok' / 'warn' / 'bad' — yedek yaşına göre."""
    if age_hours is None:
        return 'bad'
    if age_hours <= limit_hours:
        return 'ok'
    return 'warn' if age_hours <= limit_hours * 3 else 'bad'


# ======================================================================================================================
# Bakım & Onarım (gerçek bakım)
# ======================================================================================================================
CONSISTENCY_CHECKS = [
    ('Satış satırı - ana satışı yok', "SELECT COUNT(*) FROM sale_lines WHERE sale_id NOT IN (SELECT id FROM sales)", 'hata'),
    ('Ödeme kaydı - ana satışı yok', "SELECT COUNT(*) FROM sale_payments WHERE sale_id NOT IN (SELECT id FROM sales)", 'hata'),
    ('Cari hareketi - cari kartı yok', "SELECT COUNT(*) FROM cari_moves WHERE cari_id IS NOT NULL AND cari_id NOT IN (SELECT id FROM caris)", 'hata'),
    ('Stok hareketi - stok kartı yok', "SELECT COUNT(*) FROM stock_moves WHERE stock_id IS NOT NULL AND stock_id NOT IN (SELECT id FROM stock)", 'hata'),
    ('Alış satırı - alış belgesi yok', "SELECT COUNT(*) FROM purchase_lines WHERE purchase_id NOT IN (SELECT id FROM purchases)", 'hata'),
    ('Ek barkod - stok kartı yok', "SELECT COUNT(*) FROM stock_barcodes WHERE stock_id NOT IN (SELECT id FROM stock)", 'hata'),
    ('Satış - cari kartı yok', "SELECT COUNT(*) FROM sales WHERE customer_id IS NOT NULL AND customer_id<>0 AND customer_id NOT IN (SELECT id FROM caris)", 'hata'),
    ('Satış satırı - stok kartı yok', "SELECT COUNT(*) FROM sale_lines WHERE stock_id IS NOT NULL AND stock_id<>0 AND stock_id NOT IN (SELECT id FROM stock)", 'uyarı'),
    ('Adsız stok kartı', "SELECT COUNT(*) FROM stock WHERE trim(COALESCE(name,''))=''", 'uyarı'),
    ('Adsız cari kartı', "SELECT COUNT(*) FROM caris WHERE trim(COALESCE(title,''))=''", 'uyarı'),
    ('Aynı belge numarası (satış)', "SELECT COUNT(*) FROM (SELECT doc_no FROM sales GROUP BY doc_no HAVING COUNT(*)>1)", 'hata'),
    ('Eksi (negatif) stoklu kart', "SELECT COUNT(*) FROM stock WHERE qty<0 AND COALESCE(active,1)=1", 'bilgi'),
    ('Stok miktarı ile stok hareketleri toplamı farklı kart', "SELECT COUNT(*) FROM stock s WHERE ABS(s.qty-COALESCE((SELECT SUM(qty) FROM stock_moves m WHERE m.stock_id=s.id),0))>0.0005", 'bilgi'),
]


def _scalar(c, sql):
    try:
        r = c.execute(sql).fetchone()
        return None if r is None else r[0]
    except sqlite3.Error:
        return None


def consistency_scan(c):
    """Salt okunur mantıksal tutarlılık taraması. [(ad, adet, önem)] — tablo yoksa atlanır. HİÇBİR KAYDI DEĞİŞTİRMEZ."""
    out = []
    for name, sql, sev in CONSISTENCY_CHECKS:
        n = _scalar(c, sql)
        if n is not None:
            out.append((name, int(n), sev))
    return out


def clean_temp_dirs(back_dir, older_hours=24):
    """Yarım kalmış geçici klasörleri (_restore_*, _safe_*) siler. Silinen adedi döner."""
    n = 0
    back_dir = Path(back_dir)
    if not back_dir.exists():
        return 0
    for pat in ('_restore_*', '_safe_*'):
        for p in back_dir.glob(pat):
            try:
                if p.is_dir() and (time.time() - p.stat().st_mtime) > older_hours * 3600:
                    shutil.rmtree(str(p), ignore_errors=True)
                    n += 1
            except OSError:
                pass
    return n


def run_maintenance(db_path, back_dir, progress=None, vacuum=True, nas_dir='', keep_days=30, single_close=True, protect=()):
    """Gerçek bakım. progress(adım_no, toplam, başlık, durum, ayrıntı) çağrılır. Rapor sözlüğü döner:
    ok(bool), steps[(başlık, durum, ayrıntı, saniye)], findings[(ad, adet, önem)], size_before/after, backup, lines(metin).
    Adımlar: güvenlik yedeği → tam bütünlük → yabancı anahtar → tablo okuma → tutarlılık taraması → REINDEX → ANALYZE → WAL → VACUUM →
    son bütünlük → temizlik. Veri satırı silinmez/değiştirilmez; tutarsızlıklar yalnız raporlanır."""
    db_path, back_dir = Path(db_path), Path(back_dir)
    rep = {'ok': True, 'db': str(db_path), 'steps': [], 'findings': [], 'size_before': 0, 'size_after': 0, 'backup': '', 'started': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    total = 11

    def step(no, title):
        t0 = time.time()

        def done(status, detail=''):
            rep['steps'].append((title, status, detail, round(time.time() - t0, 1)))
            if status == 'HATA':
                rep['ok'] = False
            if progress:
                progress(no, total, title, status, detail)
        if progress:
            progress(no, total, title, 'ÇALIŞIYOR', '')
        return done
    rep['size_before'] = db_path.stat().st_size if db_path.exists() else 0

    # 1 güvenlik yedeği
    d = step(1, 'Güvenlik yedeği alınıyor')
    try:
        folder = back_dir / 'bakim_oncesi'
        folder.mkdir(parents=True, exist_ok=True)
        free_ok, free, need = free_space_check(folder, db_path, 2.2)
        if not free_ok:
            raise RuntimeError('Diskte yeterli boş alan yok (boş %s, gereken en az %s). Bakım başlatılmadı.' % (fmt_size(free), fmt_size(need)))
        dest = folder / ('BAKIM_ONCESI_%s.db' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        s_ = sqlite3.connect(str(db_path), timeout=30)
        d_ = sqlite3.connect(str(dest))
        try:
            s_.backup(d_)
        finally:
            d_.close()
            s_.close()
        c_ = sqlite3.connect(str(dest))
        try:
            ok = str(c_.execute('PRAGMA integrity_check').fetchone()[0]) == 'ok'
        finally:
            c_.close()
        if not ok:
            raise RuntimeError('Güvenlik yedeği doğrulanamadı.')
        rep['backup'] = str(dest)
        d('TAMAM', '%s (%s)' % (dest.name, fmt_size(dest.stat().st_size)))
    except Exception as ex:
        d('HATA', str(ex))
        return _finish(rep)

    c = sqlite3.connect(str(db_path), timeout=60)
    c.row_factory = sqlite3.Row
    try:
        # 2 tam bütünlük
        d = step(2, 'Veritabanı bütünlüğü (tam tarama)')
        rows = [str(r[0]) for r in c.execute('PRAGMA integrity_check').fetchall()]
        if rows == ['ok']:
            d('TAMAM', 'integrity_check = ok')
        else:
            d('HATA', '; '.join(rows[:5]) + (' ...' if len(rows) > 5 else ''))
            rep['findings'].append(('Veritabanı bütünlük hatası', len(rows), 'hata'))
        # 3 yabancı anahtar
        d = step(3, 'Yabancı anahtar / ilişki kontrolü')
        try:
            fk = c.execute('PRAGMA foreign_key_check').fetchall()
            d('TAMAM' if not fk else 'UYARI', 'sorun yok' if not fk else '%d kayıtta ilişki sorunu' % len(fk))
            if fk:
                rep['findings'].append(('Yabancı anahtar ihlali', len(fk), 'hata'))
        except sqlite3.Error as ex:
            d('UYARI', str(ex))
        # 4 tablo okuma
        d = step(4, 'Tüm tablolar okunuyor')
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        bad, tot_rows = [], 0
        for t in tables:
            try:
                tot_rows += int(c.execute('SELECT COUNT(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0])
            except sqlite3.Error as ex:
                bad.append('%s: %s' % (t, ex))
        d('TAMAM' if not bad else 'HATA', '%d tablo, %s kayıt okundu' % (len(tables), format(tot_rows, ',').replace(',', '.')) + ('' if not bad else ' | ' + '; '.join(bad[:3])))
        # 5 tutarlılık
        d = step(5, 'Kayıt tutarlılığı taranıyor (salt okunur)')
        rep['findings'] += [f for f in consistency_scan(c) if f[1] > 0]
        errs = [f for f in rep['findings'] if f[2] == 'hata']
        d('TAMAM' if not errs else 'UYARI', 'tutarsızlık yok' if not rep['findings'] else '%d madde bulundu (rapora bakın)' % len(rep['findings']))
        # 6 reindex
        d = step(6, 'İndeksler yeniden kuruluyor (REINDEX)')
        c.execute('REINDEX')
        c.commit()
        d('TAMAM')
        # 7 analyze
        d = step(7, 'Sorgu istatistikleri güncelleniyor (ANALYZE)')
        c.execute('ANALYZE')
        c.commit()
        d('TAMAM')
        # 8 wal
        d = step(8, 'Günlük dosyası birleştiriliyor (WAL checkpoint)')
        try:
            r = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            d('TAMAM', 'tamam' if not r or r[0] == 0 else 'meşgul (sonra yeniden denenir)')
        except sqlite3.Error as ex:
            d('UYARI', str(ex))
        c.close()
        # 9 vacuum
        d = step(9, 'Veritabanı sıkıştırılıyor (VACUUM)')
        if not vacuum:
            d('ATLANDI', 'ağdaki diğer bilgisayarlar açık olduğu için atlandı')
        else:
            ok_free, free, need = free_space_check(db_path.parent, db_path, 1.3, 20)
            if not ok_free:
                d('ATLANDI', 'boş alan yetersiz (boş %s, gereken %s)' % (fmt_size(free), fmt_size(need)))
            else:
                try:
                    v = sqlite3.connect(str(db_path), timeout=60, isolation_level=None)
                    try:
                        v.execute('VACUUM')
                    finally:
                        v.close()
                    d('TAMAM')
                except sqlite3.Error as ex:
                    d('UYARI', 'yapılamadı: %s' % ex)
        # 10 son bütünlük
        d = step(10, 'Bakım sonrası bütünlük doğrulaması')
        c = sqlite3.connect(str(db_path), timeout=60)
        try:
            r = str(c.execute('PRAGMA integrity_check').fetchone()[0])
            c.execute('PRAGMA optimize')
        finally:
            c.close()
        if r == 'ok':
            d('TAMAM', 'integrity_check = ok')
        else:
            d('HATA', r[:200] + ' | Güvenlik yedeği: ' + rep['backup'])
        # 11 temizlik
        d = step(11, 'Eski geçici dosyalar ve yedekler temizleniyor')
        n_tmp = clean_temp_dirs(back_dir)
        rem = prune_all(back_dir, keep_days, nas_dir, protect, single_close)
        d('TAMAM', 'geçici klasör: %d, eski yedek: %d, ikinci kopya: %d, güvenlik kopyası: %d' % (n_tmp, len(rem['main']), len(rem['second']), len(rem['safety'])))
    except Exception as ex:
        rep['steps'].append(('Beklenmeyen hata', 'HATA', str(ex), 0))
        rep['ok'] = False
    finally:
        try:
            c.close()
        except Exception:
            pass
    return _finish(rep)


def _finish(rep):
    try:
        rep['size_after'] = Path(rep.get('db', '')).stat().st_size if rep.get('db') else rep['size_after']
    except OSError:
        pass
    rep['lines'] = report_lines(rep)
    return rep


def report_lines(rep):
    L = ['BAKIM & ONARIM RAPORU', '=' * 64, 'Başlangıç: ' + rep.get('started', '')]
    if rep.get('backup'):
        L.append('Güvenlik yedeği: ' + rep['backup'])
    L.append('')
    for title, status, detail, secs in rep['steps']:
        L.append('[%-8s] %-46s %5.1f sn %s' % (status, title, secs, ('- ' + detail) if detail else ''))
    L.append('')
    if rep['findings']:
        L.append('TARAMA BULGULARI (hiçbir kayıt silinmedi / değiştirilmedi):')
        for name, n, sev in rep['findings']:
            L.append('  [%s] %s: %d' % (sev.upper(), name, n))
        L.append('')
        L.append('  HATA/UYARI maddeleri için ilgili ekranlardan kayıtları kontrol edin; gerekirse güvenlik yedeği ile destek alın.')
        L.append('  BİLGİ maddeleri hata değildir (ör. satış sonrası eksiye düşen stok, elle düzeltilmiş miktarlar).')
    else:
        L.append('Tarama bulgusu yok: kayıtlar tutarlı.')
    if rep.get('size_before') is not None:
        L.append('')
        L.append('Veritabanı boyutu: %s → %s' % (fmt_size(rep.get('size_before')), fmt_size(rep.get('size_after'))))
    return '\n'.join(L)


def _log(ctx, e):
    # Sessiz yutulan hatayı ana programın hata günlüğüne yazar (yoksa sessizce geçer; ana işlemi bozmaz).
    try:
        import sys
        for m in list(sys.modules.values()):
            try:
                f = getattr(m, 'log_error', None)
                if callable(f) and hasattr(m, 'ERROR_LOG'):
                    f(ctx, e)
                    return
            except Exception:
                continue
    except Exception:
        pass
