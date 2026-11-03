# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Kullanıcı & Güvenlik çekirdeği (v1.2.291.448).

Firma bazında TEK kullanıcı veritabanı (alfabe_kullanicilar.db) kullanılır; roller ve yetkiler de artık buradadır
(eskiden her dönem DB'sinde ayrı tutuluyordu, dönem değişince yetkiler farklılaşabiliyordu).

Sağlananlar:
  * Hatalı girişte hesap kilidi (ayarlanabilir deneme sayısı / süre), giriş geçmişi (başarılı + hatalı).
  * Şifre kuralı (en az uzunluk, kullanıcı adıyla aynı olamaz), şifre süresi, "ilk girişte şifre değiştir".
  * Son yöneticiyi koruma (pasife alınamaz / rolü düşürülemez).
  * Rol yetkileri: Görüntüle / Ekle / Değiştir / Pasife Al / Yazdır / Kritik İşlem; rol kopyalama, hazır rol şablonları.
  * Yetkili onayı: yetkisi olmayan kullanıcı kritik işlemde (iade, yüksek iskonto, geri yükleme, dönem silme...)
    yetkili bir kullanıcının adı + şifresiyle onay alır; onay işlem geçmişine yazılır.
  * Kullanıcı bazlı en yüksek iskonto oranı.
  * Boşta kalınca ekran kilidi (Ayarlar > Oturum Zaman Aşımı dk; yalnız açılışta şifre soruluyorsa).
"""
import datetime
import socket
import tkinter as tk
from tkinter import ttk, messagebox

MODULES = ['Ana Sayfa', 'Hızlı Satış', 'Stok Merkezi', 'Fiyat Yönetimi', 'Alış & Mal Kabul', 'Cari Merkezi', 'Personel Takibi',
           'Sipariş & Teklif', 'Finans Merkezi', 'Kasa Merkezi', 'Banka Merkezi', 'E-Fatura / E-Arşiv', 'Muhasebe & Beyan',
           'Kampanya Merkezi', 'Hizmetler / Fotokopi', 'Etiket & Barkod', 'Fiyat Gör', 'Rapor & Analiz', 'AI Asistan', 'Uyarılar',
           'Duyuru & Hatırlatma', 'Veri Aktarım', 'Sistem Merkezi', 'Kullanıcı & Güvenlik', 'Ayarlar', 'Cihazlar & Entegrasyonlar']
KINDS = [('view', 'Görüntüle'), ('add_rec', 'Ekle'), ('edit', 'Değiştir'), ('passive', 'Pasife Al'), ('print', 'Yazdır'), ('critical', 'Kritik İşlem')]
KIND_COLS = [k for k, _ in KINDS]
ADMIN = 'Yönetici'
DEFAULTS = {'min_pw_len': '6', 'max_failed': '5', 'lock_minutes': '5', 'pw_expire_days': '0'}

_ALL = set(KIND_COLS)
_VAP = {'view', 'add_rec', 'print'}
_V = {'view'}
_VP = {'view', 'print'}
TEMPLATES = {
    'Kasiyer': {'Ana Sayfa': _V, 'Hızlı Satış': _VAP, 'Fiyat Gör': _V, 'Hizmetler / Fotokopi': _VAP, 'Uyarılar': _V,
                'Duyuru & Hatırlatma': _V, 'Kasa Merkezi': _VP, 'Cari Merkezi': _V},
    'Depo': {'Ana Sayfa': _V, 'Stok Merkezi': {'view', 'add_rec', 'edit', 'print'}, 'Alış & Mal Kabul': {'view', 'add_rec', 'edit', 'print'},
             'Etiket & Barkod': _VAP, 'Fiyat Gör': _V, 'Sipariş & Teklif': _VAP, 'Uyarılar': _V, 'Duyuru & Hatırlatma': _V},
    'Muhasebe': {'Ana Sayfa': _V, 'Cari Merkezi': {'view', 'add_rec', 'edit', 'print'}, 'Finans Merkezi': {'view', 'add_rec', 'edit', 'print'},
                 'Kasa Merkezi': {'view', 'add_rec', 'edit', 'print'}, 'Banka Merkezi': {'view', 'add_rec', 'edit', 'print'},
                 'E-Fatura / E-Arşiv': {'view', 'add_rec', 'edit', 'print'}, 'Muhasebe & Beyan': {'view', 'add_rec', 'edit', 'print'},
                 'Rapor & Analiz': _VP, 'Alış & Mal Kabul': _VP, 'Uyarılar': _V, 'Duyuru & Hatırlatma': _V},
    'Personel': {'Ana Sayfa': _V, 'Hızlı Satış': _VAP, 'Fiyat Gör': _V, 'Duyuru & Hatırlatma': _V},
}
ROLE_DESC = {ADMIN: 'Tam yetkili yönetici', 'Kasiyer': 'Satış ve tahsilat', 'Muhasebe': 'Cari ve finans', 'Depo': 'Stok ve mal kabul', 'Personel': 'Sınırlı personel'}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _pc():
    try:
        return socket.gethostname()
    except Exception:
        return ''


# ----------------------------------------------------------------------------- şema
def ensure(c, period_rows=None):
    """Kullanıcı DB'sini yükseltir. period_rows: ilk kurulumda dönem DB'sinden taşınacak (roles, role_permissions) satırları."""
    cols = {r[1] for r in c.execute('PRAGMA table_info(users)')}
    for name, typ, default in [('phone', 'TEXT', "''"), ('email', 'TEXT', "''"), ('note', 'TEXT', "''"), ('max_discount', 'REAL', '-1'),
                               ('must_change_pw', 'INTEGER', '0'), ('failed_count', 'INTEGER', '0'), ('locked_until', 'TEXT', "''"),
                               ('pw_changed_at', 'TEXT', "''")]:
        if name not in cols:
            c.execute(f'ALTER TABLE users ADD COLUMN {name} {typ} DEFAULT {default}')
    c.execute('CREATE TABLE IF NOT EXISTS login_log(id INTEGER PRIMARY KEY AUTOINCREMENT, dt TEXT, username TEXT, pc TEXT, success INTEGER, detail TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS sec_settings(k TEXT PRIMARY KEY, v TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS sec_roles(name TEXT PRIMARY KEY, description TEXT DEFAULT \'\', active INTEGER DEFAULT 1)')
    c.execute('CREATE TABLE IF NOT EXISTS sec_role_permissions(role TEXT, module TEXT, view INTEGER DEFAULT 0, add_rec INTEGER DEFAULT 0, edit INTEGER DEFAULT 0, '
              'passive INTEGER DEFAULT 0, print INTEGER DEFAULT 0, critical INTEGER DEFAULT 0, PRIMARY KEY(role, module))')
    for k, v in DEFAULTS.items():
        c.execute('INSERT OR IGNORE INTO sec_settings(k, v) VALUES(?, ?)', (k, v))
    fresh = c.execute('SELECT COUNT(*) FROM sec_roles').fetchone()[0] == 0
    if fresh:
        roles, perms = period_rows or ([], [])
        for r in roles:
            c.execute('INSERT OR IGNORE INTO sec_roles(name, description, active) VALUES(?, ?, ?)', (r[0], r[1] or '', int(r[2] if r[2] is not None else 1)))
        for p in perms:
            c.execute('INSERT OR IGNORE INTO sec_role_permissions(role, module, view, add_rec, edit, passive, print, critical) VALUES(?,?,?,?,?,?,?,?)', tuple(p[:8]))
        for name, desc in ROLE_DESC.items():
            c.execute('INSERT OR IGNORE INTO sec_roles(name, description, active) VALUES(?, ?, 1)', (name, desc))
        for role, mods in TEMPLATES.items():
            if c.execute('SELECT COUNT(*) FROM sec_role_permissions WHERE role=?', (role,)).fetchone()[0] == 0:
                apply_template(c, role, role)
    for mod in MODULES:
        c.execute('INSERT OR REPLACE INTO sec_role_permissions(role, module, view, add_rec, edit, passive, print, critical) VALUES(?,?,1,1,1,1,1,1)', (ADMIN, mod))
    c.commit()


def apply_template(c, role, template):
    mods = TEMPLATES.get(template, {})
    c.execute('DELETE FROM sec_role_permissions WHERE role=?', (role,))
    for mod in MODULES:
        ks = mods.get(mod, set())
        c.execute('INSERT INTO sec_role_permissions(role, module, view, add_rec, edit, passive, print, critical) VALUES(?,?,?,?,?,?,?,?)',
                  (role, mod) + tuple(1 if k in ks else 0 for k in KIND_COLS))


def setting(c, k):
    # v1.2.291.452: Ayarlar Merkezi (kapsamlı ayar) önceliklidir; yoksa eski sec_settings kaydı, o da yoksa varsayılan.
    try:
        v = CTX.opt_raw(k) if (CTX is not None and getattr(CTX, 'opt_raw', None)) else None
        if v not in (None, ''):
            return v
    except Exception:
        pass
    r = c.execute('SELECT v FROM sec_settings WHERE k=?', (k,)).fetchone()
    return r[0] if r and r[0] is not None else DEFAULTS.get(k, '')


def setting_int(c, k):
    try:
        return int(float(setting(c, k)))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(k, '0') or 0)


def set_setting(c, k, v):
    c.execute('INSERT INTO sec_settings(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', (k, str(v)))
    c.commit()


# ----------------------------------------------------------------------------- yetki
def role_can(c, role, module, kind='view'):
    if role == ADMIN:
        return True
    if kind not in KIND_COLS:
        return False
    r = c.execute(f'SELECT {kind} FROM sec_role_permissions WHERE role=? AND module=?', (role, module)).fetchone()
    return bool(r and int(r[0] or 0))


def set_perm(c, role, module, kind, value):
    if role == ADMIN:
        raise ValueError('Yönetici rolünün yetkileri değiştirilemez (her zaman tam yetkili).')
    c.execute('INSERT OR IGNORE INTO sec_role_permissions(role, module) VALUES(?, ?)', (role, module))
    c.execute(f'UPDATE sec_role_permissions SET {kind}=? WHERE role=? AND module=?', (1 if value else 0, role, module))
    c.commit()


def copy_role(c, src, dst, desc=''):
    dst = dst.strip()
    if not dst:
        raise ValueError('Yeni rol adı boş olamaz.')
    if c.execute('SELECT 1 FROM sec_roles WHERE name=?', (dst,)).fetchone():
        raise ValueError('Bu adda bir rol zaten var.')
    c.execute('INSERT INTO sec_roles(name, description, active) VALUES(?, ?, 1)', (dst, desc))
    for r in c.execute('SELECT module, view, add_rec, edit, passive, print, critical FROM sec_role_permissions WHERE role=?', (src,)).fetchall():
        c.execute('INSERT INTO sec_role_permissions(role, module, view, add_rec, edit, passive, print, critical) VALUES(?,?,?,?,?,?,?,?)', (dst,) + tuple(r))
    c.commit()


def delete_role(c, name):
    if name in ROLE_DESC:
        raise ValueError('Hazır roller silinemez (pasife alınabilir).')
    n = c.execute('SELECT COUNT(*) FROM users WHERE role=?', (name,)).fetchone()[0]
    if n:
        raise ValueError(f'Bu role bağlı {n} kullanıcı var; önce kullanıcıların rolünü değiştirin.')
    c.execute('DELETE FROM sec_role_permissions WHERE role=?', (name,))
    c.execute('DELETE FROM sec_roles WHERE name=?', (name,))
    c.commit()


def roles(c, only_active=True):
    q = 'SELECT name FROM sec_roles' + (' WHERE active=1' if only_active else '') + ' ORDER BY CASE name WHEN ? THEN 0 ELSE 1 END, name'
    return [r[0] for r in c.execute(q, (ADMIN,))]


def active_admins(c, exclude_id=None):
    q = "SELECT COUNT(*) FROM users WHERE active=1 AND role=? AND COALESCE(password_hash,'')<>''"
    args = [ADMIN]
    if exclude_id is not None:
        q += ' AND id<>?'
        args.append(exclude_id)
    return c.execute(q, args).fetchone()[0]


def guard_last_admin(c, user_id, new_role=None, new_active=None):
    """Son aktif (şifreli) yöneticinin pasife alınmasını veya rolünün düşürülmesini engeller. Hata metni veya '' döner."""
    r = c.execute('SELECT role, active FROM users WHERE id=?', (user_id,)).fetchone()
    if not r or r[0] != ADMIN or not r[1]:
        return ''
    losing = (new_active is not None and not new_active) or (new_role is not None and new_role != ADMIN)
    if losing and active_admins(c, exclude_id=user_id) == 0:
        return ('Bu kullanıcı şifresi tanımlı tek aktif Yönetici. Pasife alınır ya da rolü değiştirilirse programa yönetici olarak '
                'kimse giremez.\n\nÖnce başka bir kullanıcıyı Yönetici yapıp şifresini belirleyin.')
    return ''


# ----------------------------------------------------------------------------- şifre
def validate_password(c, pw, username=''):
    n = max(4, setting_int(c, 'min_pw_len'))
    if len(pw or '') < n:
        return f'Şifre en az {n} karakter olmalı.'
    if username and pw.strip().lower() == str(username).strip().lower():
        return 'Şifre kullanıcı adıyla aynı olamaz.'
    if len(set(pw)) == 1:
        return 'Şifre tek bir karakterin tekrarı olamaz (ör. 111111).'
    if pw in ('123456', '1234567', '12345678', '123456789', 'qwerty', 'password', 'sifre', 'şifre', 'abc123', '654321'):
        return 'Bu şifre çok yaygın; başka bir şifre seçin.'
    return ''


def set_password(c, user_id, pw, hasher, must_change=False):
    c.execute('UPDATE users SET password_hash=?, pw_changed_at=?, must_change_pw=?, failed_count=0, locked_until=\'\' WHERE id=?',
              (hasher(pw), _now(), 1 if must_change else 0, user_id))
    c.commit()


def password_expired(c, row):
    days = setting_int(c, 'pw_expire_days')
    if days <= 0:
        return False
    t = str(row['pw_changed_at'] or row['created_at'] or '')[:19]
    try:
        return (datetime.datetime.now() - datetime.datetime.strptime(t, '%Y-%m-%d %H:%M:%S')).days >= days
    except ValueError:
        return False


# ----------------------------------------------------------------------------- giriş
def log_login(c, username, ok, detail='', pc=None):
    c.execute('INSERT INTO login_log(dt, username, pc, success, detail) VALUES(?,?,?,?,?)', (_now(), username, pc if pc is not None else _pc(), 1 if ok else 0, detail))
    c.execute('DELETE FROM login_log WHERE id NOT IN (SELECT id FROM login_log ORDER BY id DESC LIMIT 20000)')
    c.commit()


def lock_remaining(row):
    """Kilit varsa kalan dakika (yukarı yuvarlanmış), yoksa 0."""
    t = str(row['locked_until'] or '')[:19]
    if not t:
        return 0
    try:
        sec = (datetime.datetime.strptime(t, '%Y-%m-%d %H:%M:%S') - datetime.datetime.now()).total_seconds()
    except ValueError:
        return 0
    return int(sec // 60 + (1 if sec % 60 else 0)) if sec > 0 else 0


def authenticate(c, username, password, verify_fn, hasher, purpose='Giriş'):
    """(satır | None, mesaj). verify_fn(username, password) -> satır | None (eski kayıt taşıma dahil).
    Hatalı denemeler sayılır; sınır aşılınca hesap ayarlanan süre kilitlenir. Her deneme giriş geçmişine yazılır."""
    username = str(username or '').strip()
    row = c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE ORDER BY (username=?) DESC, id LIMIT 1', (username, username)).fetchone()
    if row:
        username = row['username']      # kayıttaki yazım biçimi (Ali / ali fark etmez); geçmiş ve doğrulama bununla yapılır
    if not row or not row['active']:
        log_login(c, username, False, purpose + ': kullanıcı yok veya pasif')
        return None, 'Kullanıcı bulunamadı veya pasif.'
    rem = lock_remaining(row)
    if rem:
        log_login(c, username, False, purpose + ': hesap kilitli')
        return None, f'Bu hesap çok sayıda hatalı deneme nedeniyle kilitli. {rem} dakika sonra tekrar deneyin veya yöneticiye kilidi açtırın.'
    if not str(row['password_hash'] or ''):
        # Merkezî kayıtta şifre yoksa yalnız eski dönem kaydındaki gerçek şifre ile (taşınarak) girilebilir.
        if not password or not verify_fn(username, password):
            log_login(c, username, False, purpose + ': şifre tanımlı değil')
            return None, 'Bu kullanıcı için şifre tanımlanmamış. Yönetici, Kullanıcı & Güvenlik bölümünden şifre belirlemeli.'
        row = c.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
    ok_row = verify_fn(username, password)
    if not ok_row:
        mx = max(1, setting_int(c, 'max_failed'))
        n = int(row['failed_count'] or 0) + 1
        if n >= mx:
            until = (datetime.datetime.now() + datetime.timedelta(minutes=max(1, setting_int(c, 'lock_minutes')))).strftime('%Y-%m-%d %H:%M:%S')
            c.execute('UPDATE users SET failed_count=0, locked_until=? WHERE id=?', (until, row['id']))
            c.commit()
            log_login(c, username, False, purpose + f': hatalı şifre ({n}. deneme) - hesap kilitlendi')
            return None, f'Şifre {n} kez hatalı girildi. Hesap {setting_int(c, "lock_minutes")} dakika kilitlendi.'
        c.execute('UPDATE users SET failed_count=? WHERE id=?', (n, row['id']))
        c.commit()
        log_login(c, username, False, purpose + f': hatalı şifre ({n}. deneme)')
        return None, f'Kullanıcı adı veya şifre yanlış. (Kalan deneme: {mx - n})'
    row = c.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
    upd = 'UPDATE users SET failed_count=0, locked_until=\'\''
    args = []
    if purpose == 'Giriş':
        upd += ', last_login=?'
        args.append(_now())
    if not str(row['password_hash'] or '').startswith('pbkdf2_sha256$'):
        upd += ', password_hash=?'          # eski SHA-256 / düz metin kaydı güvenli biçime yükseltilir
        args.append(hasher(password))
    c.execute(upd + ' WHERE id=?', args + [row['id']])
    c.commit()
    log_login(c, username, True, purpose)
    return c.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone(), ''


def unlock(c, user_id):
    c.execute("UPDATE users SET failed_count=0, locked_until='' WHERE id=?", (user_id,))
    c.commit()


# ----------------------------------------------------------------------------- arayüz yardımcıları
class Ctx:
    """Ana programdan gelen bağlam: connect() -> kullanıcı DB bağlantısı; verify_fn; hasher; audit(module, action, old, new);
    user() -> geçerli kullanıcı adı; role() -> geçerli rol; opt(k, default) -> program ayarı; new_toplevel; app adı."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


CTX = None


def init(**kw):
    global CTX
    CTX = Ctx(**kw)
    return CTX


def _yes(v):
    return str(v or '').strip().lower() in ('evet', 'yes', '1', 'true', 'açık', 'acik')


_ENSURED = set()


def _con():
    c = CTX.connect()
    try:
        key = c.execute('PRAGMA database_list').fetchone()[2]
    except Exception:
        key = None
    if key not in _ENSURED:
        ensure(c, CTX.period_rows() if getattr(CTX, 'period_rows', None) else None)
        _ENSURED.add(key)
    return c


def can(module, kind='view', role=None):
    c = _con()
    try:
        return role_can(c, role or CTX.role(), module, kind)
    finally:
        c.close()


def approve_dialog(parent, reason, module, kind='critical'):
    """Yetkili onayı penceresi. Onaylayanın kullanıcı adını, vazgeçilirse None döner."""
    c = _con()
    try:
        eligible = [r['username'] for r in c.execute("SELECT username, role FROM users WHERE active=1 AND COALESCE(password_hash,'')<>'' ORDER BY username")
                    if role_can(c, r['role'], module, kind)]
    finally:
        c.close()
    if not eligible:
        messagebox.showerror(CTX.app, 'Bu işlemi onaylayabilecek (şifresi tanımlı) yetkili kullanıcı yok.\n\nKullanıcı & Güvenlik bölümünden bir Yönetici tanımlayın.', parent=parent)
        return None
    w = CTX.new_toplevel(parent)
    w.title('Yetkili Onayı')
    w.geometry('500x330')
    w.resizable(False, False)
    try:
        w.attributes('-topmost', True)
    except Exception:
        pass
    try:
        w.transient(parent.winfo_toplevel())
        w.grab_set()
    except Exception:
        pass
    tk.Label(w, text='🔒  YETKİLİ ONAYI GEREKİYOR', font=('Segoe UI', 13, 'bold'), fg='#9a3c00').pack(pady=(18, 6))
    tk.Label(w, text=reason, font=('Segoe UI', 10), wraplength=450, justify='center').pack(padx=20, pady=(0, 12))
    f = tk.Frame(w)
    f.pack(fill='x', padx=30)
    uv = tk.StringVar(value=eligible[0])
    pv = tk.StringVar()
    tk.Label(f, text='Yetkili', font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky='w', pady=6)
    ttk.Combobox(f, textvariable=uv, values=eligible, state='readonly', width=26).grid(row=0, column=1, sticky='ew', pady=6)
    tk.Label(f, text='Şifre', font=('Segoe UI', 10, 'bold')).grid(row=1, column=0, sticky='w', pady=6)
    pe = tk.Entry(f, textvariable=pv, show='*', font=('Segoe UI', 11))
    pe.grid(row=1, column=1, sticky='ew', pady=6, ipady=3)
    f.columnconfigure(1, weight=1)
    msg = tk.Label(w, text='', fg='#b00020', font=('Segoe UI', 9, 'bold'), wraplength=450)
    msg.pack(pady=4)
    res = {'u': None}

    def ok(event=None):
        c2 = _con()
        try:
            row, err = authenticate(c2, uv.get(), pv.get(), CTX.verify_fn, CTX.hasher, purpose='Yetkili onayı')
            if row and not role_can(c2, row['role'], module, kind):
                row, err = None, 'Bu kullanıcının bu işlem için yetkisi yok.'
        finally:
            c2.close()
        if not row:
            pv.set('')
            msg.configure(text=err)
            return
        res['u'] = row['username']
        try:
            CTX.audit('Güvenlik', 'Yetkili Onayı', '', f'{row["username"]} onayladı: {reason[:180]} (isteyen: {CTX.user()})')
        except Exception:
            pass
        w.destroy()
    b = tk.Frame(w)
    b.pack(pady=10)
    tk.Button(b, text='ONAYLA', command=ok, bg='#0aa12b', fg='white', font=('Segoe UI', 10, 'bold'), relief='flat', padx=24, pady=6).pack(side='left', padx=6)
    tk.Button(b, text='VAZGEÇ', command=w.destroy, font=('Segoe UI', 10, 'bold'), relief='flat', padx=18, pady=6).pack(side='left', padx=6)
    pe.bind('<Return>', ok)
    w.after_idle(pe.focus_set)
    w.wait_window()
    return res['u']


_APPROVED = {}


def require(parent, module, action, kind='critical', setting_key='critical_password', cache_minutes=0):
    """Kritik işlem kapısı. Geçerli rol yetkiliyse veya ilgili ayar kapalıysa hemen True; değilse yetkili onayı ister.
    cache_minutes>0: aynı kullanıcı + bölüm + yetki için verilen onay bu süre boyunca tekrar sorulmaz (ekle / değiştir / yazdır düğmeleri)."""
    try:
        if setting_key and not _yes(CTX.opt(setting_key, 'Evet')):
            return True
        if can(module, kind):
            return True
        key = (CTX.user(), module, kind)
        if cache_minutes and _APPROVED.get(key, datetime.datetime.min) > datetime.datetime.now():
            return True
        ok = approve_dialog(parent, f'"{action}" işlemi için yetkiniz yok ({CTX.user()} / {CTX.role()}).\nDevam etmek için yetkili bir kullanıcı onaylamalı.', module, kind) is not None
        if ok and cache_minutes:
            _APPROVED[key] = datetime.datetime.now() + datetime.timedelta(minutes=cache_minutes)
        return ok
    except Exception as e:
        try:
            CTX.log_error('Yetki kontrolü: ' + action, e)
        except Exception:
            pass
        messagebox.showerror(CTX.app, 'Yetki kontrolü yapılamadı; işlem güvenlik için durduruldu.\n\n' + str(e), parent=parent)
        return False


def check_discount(parent, gross, discount):
    """Kullanıcının en yüksek iskonto oranını aşan satışta yetkili onayı ister. True: devam."""
    try:
        if gross <= 0 or discount <= 0.004 or not _yes(CTX.opt('discount_approval', 'Evet')):
            return True
        c = _con()
        try:
            r = c.execute('SELECT max_discount FROM users WHERE username=?', (CTX.user(),)).fetchone()
            role = CTX.role()
        finally:
            c.close()
        limit = float(r[0]) if r and r[0] is not None else -1.0
        if role == ADMIN:
            return True
        if limit < 0:   # kullanıcıya özel limit yok: Ayarlar > Hızlı Satış > "Genel azami iskonto" (0 = sınırsız)
            try:
                limit = float(str(CTX.opt('sale_discount_limit', '0')).replace(',', '.') or 0)
            except ValueError:
                limit = 0.0
            if limit <= 0:
                return True
        pct = discount * 100.0 / gross
        if pct <= limit + 1e-6:
            return True
        return approve_dialog(parent, f'Bu satıştaki iskonto %{pct:.1f}; kullanıcınızın en yüksek iskonto oranı %{limit:g}.\nDevam etmek için yetkili onayı gerekiyor.',
                              'Hızlı Satış', 'critical') is not None
    except Exception as e:
        try:
            CTX.log_error('İskonto limiti kontrolü', e)
        except Exception:
            pass
        return True


def change_password_dialog(parent, user_row, reason=''):
    """Kullanıcının kendi şifresini değiştirmesi (zorunlu değişim dahil). True: değişti."""
    w = CTX.new_toplevel(parent)
    w.title('Şifre Değiştir')
    w.geometry('480x320')
    w.resizable(False, False)
    try:
        w.grab_set()
    except Exception:
        pass
    tk.Label(w, text='ŞİFRENİZİ DEĞİŞTİRİN', font=('Segoe UI', 13, 'bold'), fg='#0b376d').pack(pady=(18, 4))
    tk.Label(w, text=reason or f'Kullanıcı: {user_row["username"]}', font=('Segoe UI', 9), wraplength=430).pack(pady=(0, 10))
    p1, p2 = tk.StringVar(), tk.StringVar()
    f = tk.Frame(w)
    f.pack(fill='x', padx=30)
    for i, (lab, v) in enumerate((('Yeni Şifre', p1), ('Yeni Şifre (Tekrar)', p2))):
        tk.Label(f, text=lab, font=('Segoe UI', 10, 'bold')).grid(row=i, column=0, sticky='w', pady=6)
        tk.Entry(f, textvariable=v, show='*', font=('Segoe UI', 11)).grid(row=i, column=1, sticky='ew', pady=6)
    f.columnconfigure(1, weight=1)
    msg = tk.Label(w, text='', fg='#b00020', font=('Segoe UI', 9, 'bold'), wraplength=430)
    msg.pack()
    res = {'ok': False}

    def save():
        c = _con()
        try:
            err = validate_password(c, p1.get(), user_row['username'])
            if not err and p1.get() != p2.get():
                err = 'Şifreler eşleşmiyor.'
            if not err and CTX.verify_raw(p1.get(), str(user_row['password_hash'] or '')):
                err = 'Yeni şifre eskisiyle aynı olamaz.'
            if err:
                msg.configure(text=err)
                return
            set_password(c, user_row['id'], p1.get(), CTX.hasher, must_change=False)
        finally:
            c.close()
        try:
            CTX.audit('Güvenlik', 'Şifre Değiştir (kendi)', '', user_row['username'])
        except Exception:
            pass
        res['ok'] = True
        w.destroy()
    tk.Button(w, text='KAYDET', command=save, bg='#0aa12b', fg='white', font=('Segoe UI', 10, 'bold'), relief='flat', padx=24, pady=6).pack(pady=12)
    w.wait_window()
    return res['ok']


# ----------------------------------------------------------------------------- boşta kilit
def install_idle_lock(root):
    """Ayarlar > Oturum Zaman Aşımı dk süresince klavye/fare kullanılmazsa ekranı kilitler (yalnız açılışta şifre soruluyorsa)."""
    state = {'last': datetime.datetime.now(), 'locked': False}

    def touch(_e=None):
        state['last'] = datetime.datetime.now()
    for seq in ('<Any-KeyPress>', '<Any-ButtonPress>', '<MouseWheel>'):
        try:
            root.bind_all(seq, touch, add='+')
        except Exception:
            pass

    def tick():
        try:
            if not state['locked'] and CTX.login_on():
                mins = int(float(CTX.opt('session_timeout', '30') or 0))
                if mins > 0 and (datetime.datetime.now() - state['last']).total_seconds() >= mins * 60:
                    state['locked'] = True
                    lock_screen(root, f'{mins} dakika işlem yapılmadığı için ekran kilitlendi.')
                    state['locked'] = False
                    touch()
        except Exception as e:
            try:
                CTX.log_error('Boşta kilit', e)
            except Exception:
                pass
        try:
            root.after(20000, tick)
        except tk.TclError:
            pass
    root.after(20000, tick)
    root.bind_all('<Control-l>', lambda e: lock_screen(root, 'Ekran kilitlendi (Ctrl+L).') if CTX.login_on() else None, add='+')
    return state


def lock_screen(root, reason):
    user = CTX.user()
    w = tk.Toplevel(root)
    w.title('Ekran Kilitli')
    try:
        w.attributes('-topmost', True)
    except Exception:
        pass
    w.geometry('%dx%d+0+0' % (root.winfo_screenwidth(), root.winfo_screenheight()))
    w.configure(bg='#0b1d33')
    w.protocol('WM_DELETE_WINDOW', lambda: None)
    try:
        w.grab_set()
    except Exception:
        pass
    box = tk.Frame(w, bg='white', padx=30, pady=24)
    box.place(relx=0.5, rely=0.42, anchor='center')
    tk.Label(box, text='🔒  EKRAN KİLİTLİ', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='white').pack(pady=(0, 6))
    tk.Label(box, text=reason + f'\nKullanıcı: {user}', font=('Segoe UI', 10), bg='white').pack(pady=(0, 12))
    pv = tk.StringVar()
    pe = tk.Entry(box, textvariable=pv, show='*', font=('Segoe UI', 13), width=24)
    pe.pack(ipady=4)
    msg = tk.Label(box, text='', fg='#b00020', bg='white', font=('Segoe UI', 9, 'bold'), wraplength=360)
    msg.pack(pady=6)

    def ok(event=None):
        c = _con()
        try:
            row, err = authenticate(c, user, pv.get(), CTX.verify_fn, CTX.hasher, purpose='Kilit açma')
        finally:
            c.close()
        if row:
            w.destroy()
        else:
            pv.set('')
            msg.configure(text=err)
    tk.Button(box, text='KİLİDİ AÇ', command=ok, bg='#0aa12b', fg='white', font=('Segoe UI', 11, 'bold'), relief='flat', padx=26, pady=7).pack(pady=(4, 0))

    def by_admin():
        if approve_dialog(w, f'{user} kullanıcısının ekran kilidini açmak için yetkili onayı.', 'Kullanıcı & Güvenlik', 'edit'):
            w.destroy()
    tk.Button(box, text='Yetkili ile aç', command=by_admin, relief='flat', fg='#0b376d', bg='white', font=('Segoe UI', 9, 'underline')).pack(pady=(8, 0))
    pe.bind('<Return>', ok)
    w.after(100, pe.focus_set)
    w.wait_window()


# ============================================================================= Kullanıcı & Güvenlik ekranı
_STATUS_FG = {'ok': '#0f5d2f', 'warn': '#9a5b00', 'bad': '#b00020', 'idle': '#111111'}


def _fmt_dt(v):
    v = str(v or '')
    if len(v) >= 16 and v[4] == '-':
        return f'{v[8:10]}.{v[5:7]}.{v[0:4]} {v[11:16]}'
    return v or '-'


def build_center(base):
    """base: ana programın Base çerçevesi (card(), btn(parent, text, cmd, red))."""
    S = base
    bg = S.cget('bg')
    info = S.card()
    tk.Label(info, text='KULLANICI & YETKİ YÖNETİMİ', bg='white', fg='#123a63', font=('Segoe UI', 13, 'bold'), anchor='w').pack(fill='x', padx=14, pady=(10, 2))
    tk.Label(info, text='Kullanıcıları tanımlayın, rol yetkilerini verin, giriş güvenliğini ayarlayın. Yetkisi olmayan kullanıcı kritik işlemlerde '
             '(iade, yüksek iskonto, geri yükleme, dönem silme) yetkili onayı ister; her giriş denemesi ve onay kayıt altına alınır.',
             bg='white', fg='#31506f', font=('Segoe UI', 9), anchor='w', justify='left', wraplength=1500).pack(fill='x', padx=14, pady=(0, 10))
    b = S.card()
    r1 = tk.Frame(b, bg='white'); r1.pack(fill='x')
    r2 = tk.Frame(b, bg='white'); r2.pack(fill='x')
    acts = [('YENİ KULLANICI', lambda: user_form(S), True, r1), ('DÜZENLE', lambda: user_form(S, _sel(S)), False, r1),
            ('AKTİF / PASİF', lambda: toggle_active(S), False, r1), ('ŞİFRE BELİRLE / SIFIRLA', lambda: reset_password(S), False, r1),
            ('KİLİDİ AÇ', lambda: unlock_user(S), False, r1), ('ROL & YETKİLER', lambda: roles_window(S), False, r1),
            ('GİRİŞ GEÇMİŞİ', lambda: login_history(S), False, r2), ('İŞLEM GEÇMİŞİ', lambda: audit_window(S), False, r2),
            ('GÜVENLİK AYARLARI', lambda: settings_window(S), False, r2), ('EKRANI KİLİTLE', lambda: _lock_now(S), False, r2),
            ('YENİLE', lambda: refresh(S), False, r2)]
    for tx, fn, red, row in acts:
        S.btn(row, tx, fn, red).pack(side='left', padx=5, pady=(10, 2) if row is r1 else (2, 10))
    cards = tk.Frame(S, bg=bg); cards.pack(fill='x', padx=24, pady=(2, 4))
    S._sv, S._sl = {}, {}
    for k in ('KULLANICILAR', 'YÖNETİCİ', 'KİLİTLİ HESAP', 'AÇILIŞTA ŞİFRE', 'SON 24 SAAT HATALI GİRİŞ'):
        f = tk.Frame(cards, bg='white', bd=1, relief='solid'); f.pack(side='left', fill='x', expand=True, padx=4)
        tk.Label(f, text=k, bg='white', font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=12, pady=(8, 0))
        v = tk.StringVar(value='-'); lb = tk.Label(f, textvariable=v, bg='white', font=('Segoe UI', 11, 'bold'), wraplength=260, justify='left')
        lb.pack(anchor='w', padx=12, pady=(2, 10)); S._sv[k] = v; S._sl[k] = lb
    S._warn = tk.Label(S, text='', bg=bg, fg='#b00020', font=('Segoe UI', 10, 'bold'), anchor='w', justify='left', wraplength=1500)
    S._warn.pack(fill='x', padx=28)
    body = tk.Frame(S, bg=bg); body.pack(fill='both', expand=True, padx=24, pady=8)
    body.grid_columnconfigure(0, weight=3); body.grid_columnconfigure(1, weight=2); body.grid_rowconfigure(0, weight=1)
    left = tk.LabelFrame(body, text=' Kullanıcı Listesi ', bg='white', fg='#123a63', font=('Segoe UI', 10, 'bold'))
    left.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
    S.t = ttk.Treeview(left, columns=('no', 'user', 'name', 'role', 'state', 'last', 'disc'), show='headings', selectmode='browse')
    for c, h, w in [('no', 'No', 45), ('user', 'Kullanıcı', 130), ('name', 'Ad Soyad', 170), ('role', 'Rol', 100), ('state', 'Durum', 110),
                    ('last', 'Son Giriş', 130), ('disc', 'İskonto Limiti %', 110)]:
        S.t.heading(c, text=h); S.t.column(c, width=w, anchor='w')
    S.t.tag_configure('pasif', foreground='#8a94a3'); S.t.tag_configure('kilit', foreground='#b00020'); S.t.tag_configure('sifresiz', foreground='#9a5b00')
    S.t.pack(fill='both', expand=True, padx=8, pady=8)
    S.t.bind('<Double-1>', lambda e: user_form(S, _sel(S))); S.t.bind('<<TreeviewSelect>>', lambda e: show_details(S))
    right = tk.LabelFrame(body, text=' Seçili Kullanıcı ', bg='white', fg='#123a63', font=('Segoe UI', 10, 'bold'))
    right.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
    S._det = tk.Text(right, bg='white', relief='flat', font=('Segoe UI', 10), wrap='word', padx=12, pady=10)
    S._det.pack(fill='both', expand=True)
    refresh(S)


def _sel(S):
    x = S.t.selection()
    return int(x[0]) if x else None


def _edit_ok(S, what):
    return require(S, 'Kullanıcı & Güvenlik', what, kind='edit', setting_key=None)


def _audit(action, old='', new=''):
    try:
        CTX.audit('Güvenlik', action, old, new)
    except Exception:
        pass


def _set_card(S, k, text, level='idle'):
    S._sv[k].set(text); S._sl[k].configure(fg=_STATUS_FG.get(level, '#111111'))


def refresh(S):
    c = _con()
    try:
        rows = c.execute('SELECT * FROM users ORDER BY active DESC, username').fetchall()
        admins = active_admins(c)
        since = (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        fails = c.execute('SELECT COUNT(*) FROM login_log WHERE success=0 AND dt>=?', (since,)).fetchone()[0]
    finally:
        c.close()
    keep = S.t.selection()
    S.t.delete(*S.t.get_children())
    locked = nopw = 0
    for r in rows:
        rem = lock_remaining(r)
        has_pw = bool(str(r['password_hash'] or ''))
        state = 'Pasif' if not r['active'] else (f'KİLİTLİ ({rem} dk)' if rem else ('Şifresiz' if not has_pw else 'Aktif'))
        tag = 'pasif' if not r['active'] else ('kilit' if rem else ('sifresiz' if not has_pw else ''))
        locked += 1 if (r['active'] and rem) else 0
        nopw += 1 if (r['active'] and not has_pw) else 0
        md = r['max_discount']
        S.t.insert('', 'end', iid=str(r['id']), values=(r['id'], r['username'], r['full_name'] or '', r['role'], state, _fmt_dt(r['last_login']),
                                                       'Sınırsız' if md is None or float(md) < 0 else '%g' % float(md)), tags=(tag,) if tag else ())
    if keep and S.t.exists(keep[0]):
        S.t.selection_set(keep[0])
    act = sum(1 for r in rows if r['active'])
    login = bool(CTX.login_on())
    _set_card(S, 'KULLANICILAR', f'{act} aktif / {len(rows)} toplam', 'idle')
    _set_card(S, 'YÖNETİCİ', f'{admins} şifreli aktif yönetici', 'ok' if admins >= 1 else 'bad')
    _set_card(S, 'KİLİTLİ HESAP', str(locked), 'bad' if locked else 'ok')
    _set_card(S, 'AÇILIŞTA ŞİFRE', 'AÇIK' if login else 'KAPALI', 'ok' if login else 'warn')
    _set_card(S, 'SON 24 SAAT HATALI GİRİŞ', str(fails), 'warn' if fails >= 3 else ('idle' if fails else 'ok'))
    warns = []
    if not login:
        warns.append('Program açılışında şifre sorulmuyor: programı açan herkes Yönetici olarak çalışır; rol yetkileri, iade / iskonto onayları ve ekran kilidi devreye girmez. "GÜVENLİK AYARLARI" ile açın.')
    if admins == 0:
        warns.append('Şifresi tanımlı aktif Yönetici yok. Bir kullanıcıya Yönetici rolü verip şifresini belirleyin.')
    elif admins == 1:
        warns.append('Tek yönetici var: şifresi unutulursa yönetici işlemleri yapılamaz. İkinci bir Yönetici tanımlamanız önerilir.')
    if nopw:
        warns.append(f'{nopw} aktif kullanıcının şifresi yok; bu kullanıcılar giriş yapamaz. "ŞİFRE BELİRLE / SIFIRLA" ile şifre verin.')
    if locked:
        warns.append(f'{locked} hesap hatalı denemeler nedeniyle kilitli. Gerekirse "KİLİDİ AÇ".')
    S._warn.configure(text='\n'.join('⚠ ' + w for w in warns))
    show_details(S)


def show_details(S):
    t = S._det
    t.configure(state='normal'); t.delete('1.0', 'end')
    uid = _sel(S)
    if not uid:
        t.insert('end', 'Listeden bir kullanıcı seçin.\n\nÇift tıklayarak düzenleyebilirsiniz.')
        t.configure(state='disabled'); return
    c = _con()
    try:
        r = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not r:
            return
        perms = c.execute('SELECT * FROM sec_role_permissions WHERE role=?', (r['role'],)).fetchall()
        logs = c.execute('SELECT dt, pc, success, detail FROM login_log WHERE username=? ORDER BY id DESC LIMIT 8', (r['username'],)).fetchall()
        expired = password_expired(c, r)
    finally:
        c.close()
    see = [p['module'] for p in perms if p['view']] if r['role'] != ADMIN else MODULES
    crit = [p['module'] for p in perms if p['critical']] if r['role'] != ADMIN else MODULES
    md = r['max_discount']
    lines = [f"Kullanıcı Adı   : {r['username']}", f"Ad Soyad        : {r['full_name'] or '-'}", f"Rol             : {r['role']}",
             f"Durum           : {'Aktif' if r['active'] else 'Pasif'}" + (f'  •  KİLİTLİ ({lock_remaining(r)} dk)' if lock_remaining(r) else ''),
             f"Telefon / E-posta: {r['phone'] or '-'} / {r['email'] or '-'}",
             f"İskonto limiti  : {'Sınırsız' if md is None or float(md) < 0 else '%' + format(float(md), 'g')}",
             f"Şifre           : {'tanımlı' if r['password_hash'] else 'YOK'}" + (' • ilk girişte değiştirecek' if r['must_change_pw'] else '') + (' • SÜRESİ DOLDU' if expired else ''),
             f"Şifre değişimi  : {_fmt_dt(r['pw_changed_at'])}", f"Oluşturulma     : {_fmt_dt(r['created_at'])}", f"Son giriş       : {_fmt_dt(r['last_login'])}",
             '', f'Görebildiği bölümler ({len(see)}/{len(MODULES)}): ' + (', '.join(see) if len(see) < len(MODULES) else 'Tümü'),
             f'Kritik işlem yetkisi: ' + (', '.join(crit) if crit else 'Yok (kritik işlemlerde yetkili onayı ister)'), '']
    if r['note']:
        lines += ['Not: ' + r['note'], '']
    lines.append('Son girişler:')
    lines += [f"  {_fmt_dt(x['dt'])}  {'✓' if x['success'] else '✗'}  {x['detail'] or ''}  {('(' + x['pc'] + ')') if x['pc'] else ''}" for x in logs] or ['  (kayıt yok)']
    t.insert('end', '\n'.join(lines)); t.configure(state='disabled')


def user_form(S, uid=None):
    if not _edit_ok(S, 'Kullanıcı ekleme / düzenleme'):
        return
    c = _con()
    try:
        r = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() if uid else None
        role_list = roles(c)
    finally:
        c.close()
    w = CTX.new_toplevel(S); w.title('Kullanıcı Düzenle' if r else 'Yeni Kullanıcı'); w.geometry('560x560'); w.resizable(False, False)
    try:
        w.grab_set()
    except Exception:
        pass
    V = {k: tk.StringVar(value=str(r[k] or '') if r else '') for k in ('username', 'full_name', 'phone', 'email', 'note')}
    V['role'] = tk.StringVar(value=r['role'] if r else ('Kasiyer' if 'Kasiyer' in role_list else role_list[0]))
    md = r['max_discount'] if r else -1
    V['disc'] = tk.StringVar(value='' if md is None or float(md) < 0 else '%g' % float(md))
    V['p1'], V['p2'] = tk.StringVar(), tk.StringVar()
    must = tk.BooleanVar(value=True if not r else bool(r['must_change_pw']))
    act = tk.BooleanVar(value=True if not r else bool(r['active']))
    f = tk.Frame(w); f.pack(fill='both', expand=True, padx=20, pady=14)
    rows = [('Kullanıcı Adı *', 'username'), ('Ad Soyad', 'full_name'), ('Rol *', 'role'), ('Telefon', 'phone'), ('E-posta', 'email'),
            ('En Yüksek İskonto %', 'disc'), ('Not', 'note')]
    if not r:
        rows += [('Şifre *', 'p1'), ('Şifre (Tekrar) *', 'p2')]
    for i, (lab, k) in enumerate(rows):
        tk.Label(f, text=lab, font=('Segoe UI', 10, 'bold'), anchor='w').grid(row=i, column=0, sticky='w', pady=5)
        if k == 'role':
            e = ttk.Combobox(f, textvariable=V[k], values=role_list, state='readonly', width=30)
        else:
            e = tk.Entry(f, textvariable=V[k], font=('Segoe UI', 10), show='*' if k in ('p1', 'p2') else '', width=32)
            if k == 'username' and r:
                e.configure(state='readonly')
        e.grid(row=i, column=1, sticky='ew', pady=5)
    f.columnconfigure(1, weight=1)
    n = len(rows)
    tk.Label(f, text='İskonto boş bırakılırsa sınır yoktur. Sınırı aşan satış yetkili onayı ister (Yönetici rolü hariç).',
             fg='#667788', font=('Segoe UI', 8), wraplength=480, justify='left').grid(row=n, column=0, columnspan=2, sticky='w', pady=(2, 6))
    tk.Checkbutton(f, text='İlk girişte kendi şifresini belirlesin', variable=must).grid(row=n + 1, column=0, columnspan=2, sticky='w')
    tk.Checkbutton(f, text='Aktif', variable=act).grid(row=n + 2, column=0, columnspan=2, sticky='w')
    msg = tk.Label(w, text='', fg='#b00020', font=('Segoe UI', 9, 'bold'), wraplength=500); msg.pack()

    def save():
        u = V['username'].get().strip(); role = V['role'].get().strip()
        if not u or not role:
            return msg.configure(text='Kullanıcı adı ve rol zorunlu.')
        try:
            disc = -1.0 if not V['disc'].get().strip() else float(V['disc'].get().replace(',', '.'))
            if disc > 100 or (disc < 0 and V['disc'].get().strip()):
                raise ValueError
        except ValueError:
            return msg.configure(text='İskonto oranı 0 ile 100 arasında olmalı (boş = sınırsız).')
        c2 = _con()
        try:
            if r:
                err = guard_last_admin(c2, r['id'], new_role=role, new_active=act.get())
                if err:
                    return msg.configure(text=err)
                c2.execute('UPDATE users SET full_name=?, role=?, phone=?, email=?, note=?, max_discount=?, must_change_pw=?, active=? WHERE id=?',
                           (V['full_name'].get().strip(), role, V['phone'].get().strip(), V['email'].get().strip(), V['note'].get().strip(), disc,
                            1 if must.get() else 0, 1 if act.get() else 0, r['id']))
                c2.commit()
                _audit('Kullanıcı Düzenle', f"{r['username']} / {r['role']}", f'{u} / {role}')
            else:
                if c2.execute('SELECT 1 FROM users WHERE username=? COLLATE NOCASE', (u,)).fetchone():
                    return msg.configure(text='Bu kullanıcı adı zaten var.')
                err = validate_password(c2, V['p1'].get(), u)
                if not err and V['p1'].get() != V['p2'].get():
                    err = 'Şifreler eşleşmiyor.'
                if err:
                    return msg.configure(text=err)
                c2.execute('INSERT INTO users(username, full_name, role, active, password_hash, created_at, phone, email, note, max_discount, must_change_pw, pw_changed_at) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (u, V['full_name'].get().strip(), role, 1 if act.get() else 0, CTX.hasher(V['p1'].get()), _now(), V['phone'].get().strip(),
                            V['email'].get().strip(), V['note'].get().strip(), disc, 1 if must.get() else 0, _now()))
                c2.commit()
                _audit('Kullanıcı Ekle', '', f'{u} / {role}')
        finally:
            c2.close()
        w.destroy(); refresh(S)
    bar = tk.Frame(w); bar.pack(fill='x', padx=20, pady=12)
    S.btn(bar, 'KAYDET', save, True).pack(side='left'); S.btn(bar, 'VAZGEÇ', w.destroy).pack(side='right')


def toggle_active(S):
    uid = _sel(S)
    if not uid:
        return messagebox.showinfo(CTX.app, 'Önce bir kullanıcı seçin.', parent=S)
    if not _edit_ok(S, 'Kullanıcı aktif / pasif'):
        return
    c = _con()
    try:
        r = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        err = guard_last_admin(c, uid, new_active=not r['active'])
        if err:
            return messagebox.showwarning(CTX.app, err, parent=S)
        if r['username'] == CTX.user() and r['active']:
            return messagebox.showwarning(CTX.app, 'Kendi hesabınızı pasife alamazsınız.', parent=S)
        c.execute('UPDATE users SET active=? WHERE id=?', (0 if r['active'] else 1, uid)); c.commit()
    finally:
        c.close()
    _audit('Aktif/Pasif', r['username'], 'Pasif' if r['active'] else 'Aktif'); refresh(S)


def reset_password(S):
    uid = _sel(S)
    if not uid:
        return messagebox.showinfo(CTX.app, 'Önce bir kullanıcı seçin.', parent=S)
    if not _edit_ok(S, 'Şifre belirleme / sıfırlama'):
        return
    c = _con()
    try:
        r = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        n = max(4, setting_int(c, 'min_pw_len'))
    finally:
        c.close()
    w = CTX.new_toplevel(S); w.title('Şifre Belirle / Sıfırla'); w.geometry('500x340'); w.resizable(False, False)
    try:
        w.grab_set()
    except Exception:
        pass
    tk.Label(w, text=f"Kullanıcı: {r['username']}  ({r['full_name'] or '-'})", font=('Segoe UI', 11, 'bold'), fg='#123a63').pack(pady=(18, 4))
    tk.Label(w, text=f'Yönetici yeni şifre belirler; eski şifre istenmez. Şifre en az {n} karakter olmalı, kullanıcı adıyla aynı olamaz.',
             fg='#667788', font=('Segoe UI', 9), wraplength=450).pack(pady=(0, 8))
    p1, p2 = tk.StringVar(), tk.StringVar(); must = tk.BooleanVar(value=r['username'] != CTX.user())
    f = tk.Frame(w); f.pack(fill='x', padx=24)
    for i, (lab, v) in enumerate((('Yeni Şifre', p1), ('Yeni Şifre (Tekrar)', p2))):
        tk.Label(f, text=lab, font=('Segoe UI', 10, 'bold')).grid(row=i, column=0, sticky='w', pady=6)
        tk.Entry(f, textvariable=v, show='*', font=('Segoe UI', 11)).grid(row=i, column=1, sticky='ew', pady=6)
    f.columnconfigure(1, weight=1)
    tk.Checkbutton(w, text='Kullanıcı ilk girişte kendi şifresini belirlesin', variable=must).pack(anchor='w', padx=24)
    msg = tk.Label(w, text='', fg='#b00020', font=('Segoe UI', 9, 'bold'), wraplength=450); msg.pack()

    def save():
        c2 = _con()
        try:
            err = validate_password(c2, p1.get(), r['username'])
            if not err and p1.get() != p2.get():
                err = 'Şifreler eşleşmiyor.'
            if err:
                return msg.configure(text=err)
            set_password(c2, uid, p1.get(), CTX.hasher, must_change=must.get())
        finally:
            c2.close()
        _audit('Şifre Belirle', r['username'], 'Şifre güncellendi' + (' (ilk girişte değiştirecek)' if must.get() else ''))
        messagebox.showinfo(CTX.app, 'Şifre kaydedildi.', parent=w); w.destroy(); refresh(S)
    bar = tk.Frame(w); bar.pack(fill='x', padx=24, pady=12)
    S.btn(bar, 'KAYDET', save, True).pack(side='left'); S.btn(bar, 'VAZGEÇ', w.destroy).pack(side='right')


def unlock_user(S):
    uid = _sel(S)
    if not uid:
        return messagebox.showinfo(CTX.app, 'Önce bir kullanıcı seçin.', parent=S)
    if not _edit_ok(S, 'Hesap kilidi açma'):
        return
    c = _con()
    try:
        r = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone(); unlock(c, uid)
    finally:
        c.close()
    _audit('Kilit Aç', r['username'], 'Hatalı deneme sayacı sıfırlandı'); refresh(S)


def _lock_now(S):
    if not CTX.login_on():
        return messagebox.showinfo(CTX.app, 'Ekran kilidi yalnız "Program açılışında şifre sor" açıkken kullanılabilir (GÜVENLİK AYARLARI).', parent=S)
    lock_screen(S.winfo_toplevel(), 'Ekran kilitlendi.')


# ----------------------------------------------------------------------------- roller
def roles_window(S):
    w = CTX.new_toplevel(S); w.title('Rol & Yetkiler'); w.geometry('1180x700')
    left = tk.Frame(w); left.pack(side='left', fill='y', padx=(14, 6), pady=12)
    tk.Label(left, text='ROLLER', font=('Segoe UI', 10, 'bold'), fg='#123a63').pack(anchor='w')
    lb = tk.Listbox(left, width=24, height=20, font=('Segoe UI', 10), exportselection=False); lb.pack(fill='y', expand=True, pady=6)
    desc = tk.Label(left, text='', font=('Segoe UI', 9), fg='#31506f', wraplength=200, justify='left'); desc.pack(anchor='w', pady=(0, 6))
    right = tk.Frame(w); right.pack(side='left', fill='both', expand=True, padx=(6, 14), pady=12)
    head = tk.Label(right, text='', font=('Segoe UI', 11, 'bold'), fg='#123a63', anchor='w'); head.pack(fill='x')
    tk.Label(right, text='Hücreye tıklayarak yetkiyi aç / kapat (✔ = var). Değişiklik hemen kaydedilir ve işlem geçmişine yazılır.',
             font=('Segoe UI', 9), fg='#667788', anchor='w').pack(fill='x')
    cols = ('module',) + tuple(KIND_COLS)
    t = ttk.Treeview(right, columns=cols, show='headings', selectmode='browse')
    t.heading('module', text='Bölüm'); t.column('module', width=240, anchor='w')
    for k, lab in KINDS:
        t.heading(k, text=lab); t.column(k, width=100, anchor='center')
    t.pack(fill='both', expand=True, pady=6)
    bar = tk.Frame(right); bar.pack(fill='x')
    state = {'role': None}

    def load_roles(select=None):
        c = _con()
        try:
            names = roles(c, only_active=False)
        finally:
            c.close()
        lb.delete(0, 'end')
        for n in names:
            lb.insert('end', n)
        sel = select or state['role'] or (names[1] if len(names) > 1 else names[0])
        if sel in names:
            lb.selection_clear(0, 'end'); lb.selection_set(names.index(sel)); lb.see(names.index(sel))
        load_perms()

    def load_perms(*_):
        cur = lb.curselection()
        if not cur:
            return
        role = lb.get(cur[0]); state['role'] = role
        c = _con()
        try:
            rr = c.execute('SELECT description, active FROM sec_roles WHERE name=?', (role,)).fetchone()
            data = {p['module']: p for p in c.execute('SELECT * FROM sec_role_permissions WHERE role=?', (role,))}
            n_users = c.execute('SELECT COUNT(*) FROM users WHERE role=?', (role,)).fetchone()[0]
        finally:
            c.close()
        head.configure(text=f'Rol: {role}' + ('   (her zaman tam yetkili, değiştirilemez)' if role == ADMIN else ''))
        desc.configure(text=(rr['description'] if rr else '') + f'\n{n_users} kullanıcı' + ('' if not rr or rr['active'] else '\n(PASİF)'))
        t.delete(*t.get_children())
        for mod in MODULES:
            p = data.get(mod)
            t.insert('', 'end', iid=mod, values=(mod,) + tuple('✔' if (role == ADMIN or (p and p[k])) else '—' for k in KIND_COLS))

    def click(e):
        role = state['role']
        if not role or t.identify_region(e.x, e.y) != 'cell':
            return
        col = t.identify_column(e.x); mod = t.identify_row(e.y)
        idx = int(col[1:]) - 1
        if idx < 1 or not mod:
            return
        if role == ADMIN:
            return messagebox.showinfo(CTX.app, 'Yönetici rolü her zaman tam yetkilidir.', parent=w)
        if not _edit_ok(w, 'Rol yetkisi değiştirme'):
            return
        kind = KIND_COLS[idx - 1]
        c = _con()
        try:
            cur = role_can(c, role, mod, kind); set_perm(c, role, mod, kind, not cur)
            if kind == 'view' and cur:
                for k in KIND_COLS:
                    set_perm(c, role, mod, k, False)      # görmeyen bölümde başka yetki anlamsız
            elif kind != 'view' and not cur:
                set_perm(c, role, mod, 'view', True)
        finally:
            c.close()
        _audit('Yetki Değiştir', f'{role} / {mod} / {dict(KINDS)[kind]}', 'Verildi' if not cur else 'Kaldırıldı')
        _after_perm_change(); load_perms()
    t.bind('<Button-1>', click)
    lb.bind('<<ListboxSelect>>', load_perms)

    def bulk(value, row_only):
        role = state['role']
        if not role or role == ADMIN:
            return
        mods = [t.selection()[0]] if row_only and t.selection() else ([] if row_only else MODULES)
        if not mods:
            return messagebox.showinfo(CTX.app, 'Önce tablodan bir bölüm seçin.', parent=w)
        if not row_only and not messagebox.askyesno(CTX.app, f'{role} rolünün TÜM yetkileri ' + ('verilsin' if value else 'kaldırılsın') + ' mi?', parent=w):
            return
        if not _edit_ok(w, 'Toplu yetki değişikliği'):
            return
        c = _con()
        try:
            for m in mods:
                for k in KIND_COLS:
                    set_perm(c, role, m, k, value)
        finally:
            c.close()
        _audit('Toplu Yetki', f'{role} / {len(mods)} bölüm', 'Verildi' if value else 'Kaldırıldı'); _after_perm_change(); load_perms()

    def new_role():
        from tkinter import simpledialog
        if not _edit_ok(w, 'Yeni rol'):
            return
        name = simpledialog.askstring('Yeni Rol', 'Rol adı:', parent=w)
        if not name:
            return
        tmpl = simpledialog.askstring('Yeni Rol', 'Başlangıç şablonu (Kasiyer / Depo / Muhasebe / Personel ya da boş):', parent=w) or ''
        c = _con()
        try:
            if c.execute('SELECT 1 FROM sec_roles WHERE name=?', (name.strip(),)).fetchone():
                return messagebox.showwarning(CTX.app, 'Bu adda bir rol zaten var.', parent=w)
            c.execute('INSERT INTO sec_roles(name, description, active) VALUES(?, ?, 1)', (name.strip(), 'Özel rol')); c.commit()
            apply_template(c, name.strip(), tmpl.strip().title() if tmpl.strip().title() in TEMPLATES else ''); c.commit()
        finally:
            c.close()
        _audit('Rol Ekle', '', name.strip()); _after_perm_change(); load_roles(name.strip())

    def copy_cur():
        from tkinter import simpledialog
        role = state['role']
        if not role or not _edit_ok(w, 'Rol kopyalama'):
            return
        name = simpledialog.askstring('Rolü Kopyala', f'"{role}" rolünün kopyası için yeni ad:', parent=w)
        if not name:
            return
        c = _con()
        try:
            copy_role(c, role, name, f'{role} rolünden kopyalandı')
        except ValueError as e:
            return messagebox.showwarning(CTX.app, str(e), parent=w)
        finally:
            c.close()
        _audit('Rol Kopyala', role, name.strip()); _after_perm_change(); load_roles(name.strip())

    def del_cur():
        role = state['role']
        if not role or not _edit_ok(w, 'Rol silme'):
            return
        if not messagebox.askyesno(CTX.app, f'"{role}" rolü silinsin mi?', parent=w):
            return
        c = _con()
        try:
            delete_role(c, role)
        except ValueError as e:
            return messagebox.showwarning(CTX.app, str(e), parent=w)
        finally:
            c.close()
        _audit('Rol Sil', role, ''); state['role'] = None; _after_perm_change(); load_roles()

    def template_cur():
        role = state['role']
        if not role or role == ADMIN:
            return
        from tkinter import simpledialog
        tmpl = simpledialog.askstring('Şablon Uygula', 'Şablon (Kasiyer / Depo / Muhasebe / Personel):', parent=w)
        if not tmpl or tmpl.strip().title() not in TEMPLATES:
            return
        if not _edit_ok(w, 'Rol şablonu uygulama') or not messagebox.askyesno(CTX.app, f'{role} rolünün yetkileri "{tmpl.strip().title()}" şablonuyla değiştirilsin mi?', parent=w):
            return
        c = _con()
        try:
            apply_template(c, role, tmpl.strip().title()); c.commit()
        finally:
            c.close()
        _audit('Rol Şablonu', role, tmpl.strip().title()); _after_perm_change(); load_perms()
    lbar = tk.Frame(left); lbar.pack(fill='x')
    for tx, fn in (('YENİ ROL', new_role), ('KOPYALA', copy_cur), ('ŞABLON UYGULA', template_cur), ('SİL', del_cur)):
        S.btn(lbar, tx, fn).pack(fill='x', pady=2)
    for tx, fn in (('SEÇİLİ SATIRA TÜM YETKİ', lambda: bulk(True, True)), ('SEÇİLİ SATIRDAN KALDIR', lambda: bulk(False, True)),
                   ('TÜM YETKİLERİ VER', lambda: bulk(True, False)), ('TÜM YETKİLERİ KALDIR', lambda: bulk(False, False))):
        S.btn(bar, tx, fn).pack(side='left', padx=3)
    load_roles()


def _after_perm_change():
    try:
        CTX.on_perm_change()
    except Exception:
        pass


# ----------------------------------------------------------------------------- geçmiş pencereleri
def login_history(S):
    w = CTX.new_toplevel(S); w.title('Giriş Geçmişi'); w.geometry('1050x640')
    top = tk.Frame(w); top.pack(fill='x', padx=14, pady=10)
    c = _con()
    try:
        users = ['(Tümü)'] + [r[0] for r in c.execute('SELECT DISTINCT username FROM login_log ORDER BY 1')]
    finally:
        c.close()
    uv = tk.StringVar(value='(Tümü)'); only_bad = tk.BooleanVar(value=False)
    tk.Label(top, text='Kullanıcı:').pack(side='left'); ttk.Combobox(top, textvariable=uv, values=users, state='readonly', width=22).pack(side='left', padx=6)
    tk.Checkbutton(top, text='Yalnız hatalı denemeler', variable=only_bad).pack(side='left', padx=10)
    info = tk.Label(top, text='', fg='#31506f'); info.pack(side='right')
    t = ttk.Treeview(w, columns=('dt', 'user', 'pc', 'res', 'det'), show='headings')
    for col, h, ww in (('dt', 'Tarih', 150), ('user', 'Kullanıcı', 140), ('pc', 'Bilgisayar', 150), ('res', 'Sonuç', 90), ('det', 'Ayrıntı', 460)):
        t.heading(col, text=h); t.column(col, width=ww, anchor='w')
    t.tag_configure('bad', foreground='#b00020')
    t.pack(fill='both', expand=True, padx=14, pady=(0, 14))

    def load(*_):
        q = 'SELECT * FROM login_log WHERE 1=1'; a = []
        if uv.get() != '(Tümü)':
            q += ' AND username=?'; a.append(uv.get())
        if only_bad.get():
            q += ' AND success=0'
        c2 = _con()
        try:
            rows = c2.execute(q + ' ORDER BY id DESC LIMIT 3000', a).fetchall()
        finally:
            c2.close()
        t.delete(*t.get_children())
        for r in rows:
            t.insert('', 'end', values=(r['dt'], r['username'], r['pc'] or '', 'Başarılı' if r['success'] else 'HATALI', r['detail'] or ''), tags=() if r['success'] else ('bad',))
        info.configure(text=f'{len(rows)} kayıt (en yeni 3000)')
    S.btn(top, 'LİSTELE', load, True).pack(side='left', padx=6)
    load()


def audit_window(S):
    w = CTX.new_toplevel(S); w.title('İşlem Geçmişi'); w.geometry('1180x680')
    top = tk.Frame(w); top.pack(fill='x', padx=14, pady=10)
    sv = tk.StringVar(); mv = tk.StringVar(value='(Tümü)')
    tk.Label(top, text='Ara:').pack(side='left'); tk.Entry(top, textvariable=sv, width=28).pack(side='left', padx=6)
    try:
        mods = ['(Tümü)'] + [r[0] for r in CTX.audit_rows('SELECT DISTINCT module FROM audit ORDER BY 1', ())]
    except Exception:
        mods = ['(Tümü)']
    tk.Label(top, text='Bölüm:').pack(side='left', padx=(10, 0)); ttk.Combobox(top, textvariable=mv, values=mods, state='readonly', width=20).pack(side='left', padx=6)
    info = tk.Label(top, text='', fg='#31506f'); info.pack(side='right')
    t = ttk.Treeview(w, columns=('dt', 'user', 'module', 'action', 'old', 'new', 'reason'), show='headings')
    for col, h, ww in (('dt', 'Tarih', 145), ('user', 'Kullanıcı', 110), ('module', 'Bölüm', 120), ('action', 'İşlem', 160), ('old', 'Eski Değer', 200),
                       ('new', 'Yeni Değer', 260), ('reason', 'Açıklama', 160)):
        t.heading(col, text=h); t.column(col, width=ww, anchor='w')
    t.pack(fill='both', expand=True, padx=14, pady=(0, 14))

    def load(*_):
        q = 'SELECT * FROM audit WHERE 1=1'; a = []
        if mv.get() != '(Tümü)':
            q += ' AND module=?'; a.append(mv.get())
        s = sv.get().strip()
        if s:
            q += ' AND (user LIKE ? OR action LIKE ? OR old_value LIKE ? OR new_value LIKE ? OR reason LIKE ?)'; a += ['%' + s + '%'] * 5
        rows = CTX.audit_rows(q + ' ORDER BY id DESC LIMIT 3000', tuple(a))
        t.delete(*t.get_children())
        for r in rows:
            t.insert('', 'end', values=(r['dt'], r['user'], r['module'], r['action'], r['old_value'], r['new_value'], r['reason']))
        info.configure(text=f'{len(rows)} kayıt (en yeni 3000)')
    S.btn(top, 'LİSTELE', load, True).pack(side='left', padx=6)
    load()


# ----------------------------------------------------------------------------- ayarlar
def settings_window(S):
    # v1.2.291.452: Güvenlik ayarları tek yerden (Ayarlar Merkezi > Kullanıcı & Güvenlik) yönetilir.
    if not _edit_ok(S, 'Güvenlik ayarları'):
        return
    try:
        CTX.open_settings('guvenlik')
        return
    except Exception:
        pass
    c = _con()
    try:
        cur = {k: setting(c, k) for k in DEFAULTS}
    finally:
        c.close()
    w = CTX.new_toplevel(S); w.title('Güvenlik Ayarları'); w.geometry('660x600'); w.resizable(False, False)
    try:
        w.grab_set()
    except Exception:
        pass
    f = tk.Frame(w); f.pack(fill='both', expand=True, padx=22, pady=16)
    V = {}
    items = [('login_required', 'Program açılışında kullanıcı adı / şifre sor', ['Evet', 'Hayır'], CTX.opt('login_required', 'Hayır')),
             ('session_timeout', 'Boşta kalınca ekranı kilitle (dakika, 0 = kapalı)', None, CTX.opt('session_timeout', '30')),
             ('min_pw_len', 'En az şifre uzunluğu', None, cur['min_pw_len']),
             ('max_failed', 'Kaç hatalı denemede hesap kilitlensin', None, cur['max_failed']),
             ('lock_minutes', 'Kilit süresi (dakika)', None, cur['lock_minutes']),
             ('pw_expire_days', 'Şifre geçerlilik süresi (gün, 0 = süresiz)', None, cur['pw_expire_days']),
             ('critical_password', 'Kritik işlemde yetkili onayı (geri yükleme, dönem silme)', ['Evet', 'Hayır'], CTX.opt('critical_password', 'Evet')),
             ('return_approval', 'İadede yetkili onayı (yetkisi olmayan kullanıcı için)', ['Evet', 'Hayır'], CTX.opt('return_approval', 'Evet')),
             ('discount_approval', 'İskonto limiti aşılınca yetkili onayı', ['Evet', 'Hayır'], CTX.opt('discount_approval', 'Evet')),
             ('cancel_approval', 'Kaydedilmemiş satışta satır / fiş iptalinde yetkili onayı', ['Evet', 'Hayır'], CTX.opt('cancel_approval', 'Evet'))]
    for i, (k, lab, choices, val) in enumerate(items):
        tk.Label(f, text=lab, font=('Segoe UI', 10), anchor='w').grid(row=i, column=0, sticky='w', pady=6)
        V[k] = tk.StringVar(value=str(val))
        e = ttk.Combobox(f, textvariable=V[k], values=choices, state='readonly', width=10) if choices else tk.Entry(f, textvariable=V[k], width=12)
        e.grid(row=i, column=1, sticky='w', padx=10, pady=6)
    tk.Label(f, text='Açılışta şifre sorulmuyorsa rol yetkileri, onaylar ve ekran kilidi uygulanmaz; programı açan herkes Yönetici olur.\n'
             'Kilitlenen hesabı Yönetici "KİLİDİ AÇ" ile hemen açabilir. Ctrl+L ekranı anında kilitler.',
             fg='#667788', font=('Segoe UI', 8), justify='left', wraplength=580).grid(row=len(items), column=0, columnspan=2, sticky='w', pady=(10, 0))
    msg = tk.Label(w, text='', fg='#b00020', font=('Segoe UI', 9, 'bold'), wraplength=580); msg.pack()

    def save():
        nums = {'session_timeout': (0, 1440), 'min_pw_len': (4, 64), 'max_failed': (1, 50), 'lock_minutes': (1, 1440), 'pw_expire_days': (0, 3650)}
        vals = {}
        for k, (lo, hi) in nums.items():
            try:
                v = int(float(V[k].get().replace(',', '.')))
                if not lo <= v <= hi:
                    raise ValueError
                vals[k] = v
            except ValueError:
                return msg.configure(text=f'"{dict((i[0], i[1]) for i in items)[k]}" {lo} ile {hi} arasında olmalı.')
        c2 = _con()
        try:
            if V['login_required'].get() == 'Evet' and active_admins(c2) == 0:
                return msg.configure(text='Açılışta şifre sorulabilmesi için önce şifresi tanımlı aktif bir Yönetici olmalı; yoksa kimse giriş yapamaz.')
            for k in ('min_pw_len', 'max_failed', 'lock_minutes', 'pw_expire_days'):
                set_setting(c2, k, vals[k])
        finally:
            c2.close()
        for k in ('login_required', 'session_timeout', 'critical_password', 'return_approval', 'discount_approval', 'cancel_approval'):
            CTX.save_opt(k, str(vals.get(k, V[k].get())))
        _audit('Güvenlik Ayarları', '', ', '.join(f'{k}={vals.get(k, V[k].get())}' for k, *_ in items))
        w.destroy(); refresh(S)
    bar = tk.Frame(w); bar.pack(fill='x', padx=22, pady=12)
    S.btn(bar, 'KAYDET', save, True).pack(side='left'); S.btn(bar, 'VAZGEÇ', w.destroy).pack(side='right')


# ----------------------------------------------------------------------------- düğme yetkileri
import re as _re
_EXCLUDE = ('YENİLE', 'SEPET', 'FİLTRE', 'VAZGEÇ', 'KAPAT', 'ARA', 'LİSTELE', 'GETİR', 'TEMİZLE')
_KIND_RULES = [('print', _re.compile(r'YAZDIR|\bPDF\b|ÖN ?İZLEME|EXCEL|DIŞA AKTAR|DÖKÜM')),
               ('passive', _re.compile(r'PASİF|(?<![\wÇĞİÖŞÜ])SİL(?![\wÇĞİÖŞÜ])|KALDIR')),
               ('edit', _re.compile(r'DEĞİŞTİR|DÜZENLE|GÜNCELLE')),
               ('add_rec', _re.compile(r'^YENİ\b|\bYENİ |EKLE\b|^\+'))]


def button_kind(label):
    """Düğme yazısından yetki türünü tahmin eder (ekle / değiştir / pasife al / yazdır). Belirsizse None."""
    t = str(label or '').replace('i', 'İ').replace('ı', 'I').upper().replace('\n', ' ').strip()
    t = _re.sub(r'^[^\wÇĞİÖŞÜ+]+', '', t)
    if not t or any(x in t for x in _EXCLUDE):
        return None
    for kind, rx in _KIND_RULES:
        if rx.search(t):
            return kind
    return None


def gate_command(widget_getter, module, label, cmd):
    """Düğme komutunu yetki kontrolüyle sarar. Yetki yoksa yetkili onayı ister (onay 5 dk geçerli)."""
    kind = button_kind(label)
    if not kind or not module or module == 'Kullanıcı & Güvenlik' or cmd is None:
        return cmd

    def run(*a, **k):
        try:
            if CTX.role() != ADMIN and not can(module, kind):
                if not require(widget_getter(), module, str(label).replace('\n', ' '), kind=kind, setting_key=None, cache_minutes=5):
                    return None
        except Exception as e:
            try:
                CTX.log_error('Düğme yetkisi', e)
            except Exception:
                pass
        return cmd(*a, **k)
    run._alf_gate = (module, kind)
    return run


# Bölüm içinde ortak düğme fonksiyonu dışında (doğrudan tk.Button ile) oluşturulmuş düğmeler.
_SKIP_IN = {'Hızlı Satış': ('KAYDET', 'SATIR SİL', 'ÖDE')}       # satış tamamlama / satır iptali ayrı kurallarla korunur
_REDIRECT = {('Ana Sayfa', 'add_rec'): 'Stok Merkezi'}             # Ana Sayfa "Stok Ekle" -> Stok Merkezi yetkisi


def gate_widget_tree(top, module):
    """top altındaki henüz bağlanmamış ekle / değiştir / pasife al / yazdır düğmelerini yetki kontrolüne bağlar. Bağlanan sayısını döner."""
    n = 0
    stack = [top]
    while stack:
        w = stack.pop()
        try:
            stack.extend(w.winfo_children())
        except Exception:
            continue
        if getattr(w, '_alf_gated', False):
            continue
        try:
            if w.winfo_class() not in ('Button', 'TButton'):
                continue
            label = str(w.cget('text') or '').replace('\n', ' ').strip()
            kind = button_kind(label)
            if not kind or len(label) <= 2:
                continue
            up = label.replace('i', 'İ').upper()
            if any(x in up for x in _SKIP_IN.get(module, ())):
                continue
            mod = _REDIRECT.get((module, kind), module)
            tcl = str(w.cget('command') or '')
            if not tcl:
                continue
            w._alf_gated = True

            def run(_w=w, _tcl=tcl, _mod=mod, _kind=kind, _lab=label):
                try:
                    if CTX.role() != ADMIN and not can(_mod, _kind):
                        if not require(_w.winfo_toplevel(), _mod, _lab, kind=_kind, setting_key=None, cache_minutes=5):
                            return None
                except Exception as e:
                    try:
                        CTX.log_error('Düğme yetkisi', e)
                    except Exception:
                        pass
                return _w.tk.call(_tcl)
            w.configure(command=run)
            n += 1
        except Exception:
            continue
    return n
