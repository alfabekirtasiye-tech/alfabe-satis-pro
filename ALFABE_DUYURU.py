# -*- coding: utf-8 -*-
"""ALFABE Duyuru & Hatırlatma Merkezi (v435).
 - Tarih GG.AA.YYYY + takvim seçici, saat SS:DD
 - Tekrarlayan hatırlatma (Günlük/Haftalık/Aylık/Yıllık): tamamlanınca bir sonraki otomatik oluşur
 - Ertele: 5 dk / 1 saat / yarın / 1 hafta
 - Durumlar: Gecikmiş / Bugün / Yaklaşan / Bekliyor / Ertelendi / Tamamlandı (duyuru: Yayında / Okundu / Süresi doldu)
 - Süzgeçler, renkler, özet düğmeleri, ayrıntı paneli, kullanıcı listesinden atama, denetim kaydı
 - Zamanı gelen hatırlatma/duyuru açılışta ve dakikada bir kontrol edilip küçük pencerede gösterilir."""
import calendar
import datetime
import re

REM_LEVELS = ('KRİTİK', 'ÖNEMLİ', 'NORMAL')
ANN_LEVELS = ('Acil', 'Önemli', 'Normal')
REPEATS = ('Yok', 'Günlük', 'Haftalık', 'Aylık', 'Yıllık')
RANK = {'KRİTİK': 0, 'Acil': 0, 'ÖNEMLİ': 1, 'Önemli': 1, 'NORMAL': 2, 'Normal': 2}
GUN = ('Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz')


# ---------------------------------------------------------------- tarih/saat
def parse_date(s):
    t = str(s or '').strip()[:10]
    if not t:
        return None
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    raise ValueError('Tarih anlaşılamadı: %s (GG.AA.YYYY yazın, ör. 25.10.2026)' % t)


def parse_time(s, default='09:00'):
    t = str(s or '').strip().replace('.', ':')
    if not t:
        return default
    m = re.fullmatch(r'(\d{1,2})(?::(\d{1,2}))?', t)
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError('Saat anlaşılamadı: %s (SS:DD yazın, ör. 14:30)' % s)
    return '%02d:%02d' % (int(m.group(1)), int(m.group(2) or 0))


def tr_date(d):
    if isinstance(d, str):
        d = parse_date(d) if d else None
    return d.strftime('%d.%m.%Y') if d else ''


def add_period(dt, rule):
    if rule == 'Günlük':
        return dt + datetime.timedelta(days=1)
    if rule == 'Haftalık':
        return dt + datetime.timedelta(days=7)
    if rule in ('Aylık', 'Yıllık'):
        add = 1 if rule == 'Aylık' else 12
        y, m = divmod(dt.year * 12 + dt.month - 1 + add, 12)
        y, m = y, m + 1
        return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))
    return None


def _now(now=None):
    return now or datetime.datetime.now().replace(second=0, microsecond=0)


def _ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M')


def _pdt(s):
    s = str(s or '').strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:16] if len(s) > 10 else s[:10] + ' 00:00', '%Y-%m-%d %H:%M')
    except ValueError:
        return None


# ---------------------------------------------------------------- veritabanı
def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY,title TEXT NOT NULL,description TEXT DEFAULT '',start_date TEXT DEFAULT '',end_date TEXT DEFAULT '',alert_time TEXT DEFAULT '',priority TEXT DEFAULT 'Normal',show_on_start INTEGER DEFAULT 1,is_read INTEGER DEFAULT 0,created_at TEXT DEFAULT '',created_by TEXT DEFAULT '')")
    c.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY,dt TEXT,title TEXT,detail TEXT,status TEXT DEFAULT 'Yeni')")
    have = {r[1] for r in c.execute('PRAGMA table_info(reminders)').fetchall()}
    for name, typ in (('due', "TEXT DEFAULT ''"), ('level', "TEXT DEFAULT 'NORMAL'"), ('category', "TEXT DEFAULT 'Hatırlatma'"), ('repeat_rule', "TEXT DEFAULT 'Yok'"),
                      ('snooze_until', "TEXT DEFAULT ''"), ('completed', 'INTEGER DEFAULT 0'), ('assigned_to', "TEXT DEFAULT ''"), ('snooze_count', 'INTEGER DEFAULT 0'),
                      ('link_type', "TEXT DEFAULT ''"), ('link_id', 'INTEGER DEFAULT 0'), ('notified', 'INTEGER DEFAULT 0'), ('created_by', "TEXT DEFAULT ''"),
                      ('completed_at', "TEXT DEFAULT ''"), ('end_note', "TEXT DEFAULT ''")):
        if name not in have:
            c.execute('ALTER TABLE reminders ADD COLUMN %s %s' % (name, typ))
    c.commit()


def _audit(audit, *a, **k):
    if audit:
        try:
            audit(*a, **k)
        except Exception:
            pass


def status_of_reminder(r, now):
    if r['completed']:
        return 'Tamamlandı'
    sn = _pdt(r['snooze_until'])
    if sn and sn > now:
        return 'Ertelendi'
    due = _pdt(r['due'])
    if not due:
        return 'Bekliyor'
    if due < now:
        return 'Gecikmiş'
    if due.date() == now.date():
        return 'Bugün'
    if due.date() <= now.date() + datetime.timedelta(days=7):
        return 'Yaklaşan'
    return 'Bekliyor'


def status_of_announcement(a, now):
    if a['is_read']:
        return 'Okundu'
    sd, ed = _pdt(a['start_date']), _pdt(a['end_date'])
    if ed and ed.date() < now.date():
        return 'Süresi doldu'
    if sd and sd.date() > now.date():
        return 'Zamanı gelmedi'
    return 'Yayında'


def list_items(c, now=None):
    ensure(c)
    now = _now(now)
    out = []
    cur = c.execute('SELECT id,title,description,start_date,end_date,alert_time,priority,show_on_start,is_read,created_at,created_by FROM announcements')
    for r in cur.fetchall():
        a = dict(zip([d[0] for d in cur.description], r))
        st = status_of_announcement(a, now)
        sd = _pdt(a['start_date'])
        out.append(dict(key='A:%d' % a['id'], id=a['id'], kind='Duyuru', level=a['priority'] or 'Normal', rank=RANK.get(a['priority'] or 'Normal', 2), title=a['title'] or '',
                        detail=a['description'] or '', date=a['start_date'] or '', time=a['alert_time'] or '', end=a['end_date'] or '', repeat='', status=st,
                        assigned='', creator=a['created_by'] or '', sort=(sd or now).strftime('%Y-%m-%d') + ' ' + (a['alert_time'] or '00:00'),
                        done=st in ('Okundu', 'Süresi doldu'), overdue=False, today=(st == 'Yayında')))
    cur = c.execute('SELECT id,title,detail,due,level,repeat_rule,snooze_until,completed,assigned_to,created_by FROM reminders')
    for r in cur.fetchall():
        a = dict(zip([d[0] for d in cur.description], r))
        st = status_of_reminder(a, now)
        due = _pdt(a['due'])
        out.append(dict(key='R:%d' % a['id'], id=a['id'], kind='Hatırlatma', level=(a['level'] or 'NORMAL').upper(), rank=RANK.get((a['level'] or 'NORMAL').upper(), 2),
                        title=a['title'] or '', detail=a['detail'] or '', date=(a['due'] or '')[:10], time=(a['due'] or '')[11:16], end='', repeat=a['repeat_rule'] or 'Yok',
                        status=st, assigned=a['assigned_to'] or '', creator=a['created_by'] or '', sort=_ts(due) if due else '9999-12-31 00:00',
                        done=(st == 'Tamamlandı'), overdue=(st == 'Gecikmiş'), today=(st == 'Bugün')))
    out.sort(key=lambda x: (x['done'], x['sort'] if x['kind'] == 'Hatırlatma' else '0', x['rank'], x['id']))
    return out


def counts(items):
    return dict(gecikmis=sum(1 for x in items if x['status'] == 'Gecikmiş'),
                bugun=sum(1 for x in items if x['status'] in ('Bugün', 'Yayında') and not x['done']),
                yaklasan=sum(1 for x in items if x['status'] == 'Yaklaşan'),
                bitti=sum(1 for x in items if x['done']))


def save_reminder(c, f, rid=None, user='', audit=None, now=None):
    ensure(c)
    title = ' '.join(str(f.get('title') or '').split())
    if not title:
        raise ValueError('Başlık yazılmalı.')
    d = parse_date(f.get('date'))
    if not d:
        raise ValueError('Tarih yazılmalı (GG.AA.YYYY).')
    tm = parse_time(f.get('time'))
    level = str(f.get('level') or 'NORMAL').upper()
    if level not in REM_LEVELS:
        raise ValueError('Öncelik geçersiz.')
    rep = f.get('repeat') or 'Yok'
    if rep not in REPEATS:
        raise ValueError('Tekrar türü geçersiz.')
    assigned = '' if str(f.get('assigned') or '').strip() in ('', '(Herkes)') else str(f['assigned']).strip()
    due = '%s %s' % (d.isoformat(), tm)
    detail = str(f.get('detail') or '').strip()
    if rid:
        c.execute('UPDATE reminders SET title=?,detail=?,due=?,level=?,repeat_rule=?,assigned_to=?,notified=0,snooze_until=\'\' WHERE id=?', (title, detail, due, level, rep, assigned, rid))
        c.commit()
        _audit(audit, 'Hatırlatma', 'Düzenlendi', rid, new=title)
    else:
        cur = c.execute("INSERT INTO reminders(dt,title,detail,status,due,level,category,repeat_rule,completed,assigned_to,created_by,notified) VALUES(?,?,?,?,?,?,?,?,0,?,?,0)",
                        (_ts(_now(now)), title, detail, 'Yeni', due, level, 'Hatırlatma', rep, assigned, user))
        rid = cur.lastrowid
        c.commit()
        _audit(audit, 'Hatırlatma', 'Eklendi', rid, new=title)
    c.commit()
    return rid


def save_announcement(c, f, aid=None, user='', audit=None, now=None):
    ensure(c)
    title = ' '.join(str(f.get('title') or '').split())
    if not title:
        raise ValueError('Başlık yazılmalı.')
    sd, ed = parse_date(f.get('start')), parse_date(f.get('end'))
    if sd and ed and ed < sd:
        raise ValueError('Bitiş tarihi başlangıçtan önce olamaz.')
    tm = parse_time(f.get('time'), '')
    pr = f.get('priority') or 'Normal'
    if pr not in ANN_LEVELS:
        raise ValueError('Öncelik geçersiz.')
    vals = (title, str(f.get('description') or '').strip(), sd.isoformat() if sd else '', ed.isoformat() if ed else '', tm, pr, 1 if f.get('show', 1) in (1, True, '1') else 0)
    if aid:
        c.execute('UPDATE announcements SET title=?,description=?,start_date=?,end_date=?,alert_time=?,priority=?,show_on_start=?,is_read=0 WHERE id=?', vals + (aid,))
        c.commit()
        _audit(audit, 'Duyuru', 'Düzenlendi', aid, new=title)
    else:
        cur = c.execute('INSERT INTO announcements(title,description,start_date,end_date,alert_time,priority,show_on_start,is_read,created_at,created_by) VALUES(?,?,?,?,?,?,?,0,?,?)',
                        vals + (_ts(_now(now)), user))
        aid = cur.lastrowid
        c.commit()
        _audit(audit, 'Duyuru', 'Eklendi', aid, new=title)
    c.commit()
    return aid


def complete_reminder(c, rid, user='', audit=None, now=None):
    """Tamamlar; tekrarlıysa bir sonraki hatırlatmayı (gelecekteki ilk tarihe) oluşturur. Dönüş: yeni id veya None."""
    ensure(c)
    now = _now(now)
    cur = c.execute('SELECT title,detail,due,level,repeat_rule,assigned_to,created_by,completed FROM reminders WHERE id=?', (rid,))
    r = cur.fetchone()
    if not r or r[7]:
        return None
    c.execute("UPDATE reminders SET completed=1,status='Çözüldü',completed_at=? WHERE id=?", (_ts(now), rid))
    c.commit()
    _audit(audit, 'Hatırlatma', 'Tamamlandı', rid, new=r[0])
    new = None
    rule = r[4] or 'Yok'
    due = _pdt(r[2])
    if rule != 'Yok' and due:
        nxt = add_period(due, rule)
        n = 0
        while nxt and nxt <= now and n < 4000:
            nxt = add_period(nxt, rule)
            n += 1
        if nxt:
            cur = c.execute("INSERT INTO reminders(dt,title,detail,status,due,level,category,repeat_rule,completed,assigned_to,created_by,notified) VALUES(?,?,?,?,?,?,?,?,0,?,?,0)",
                            (_ts(now), r[0], r[1], 'Yeni', _ts(nxt), r[3], 'Hatırlatma', rule, r[5], r[6]))
            new = cur.lastrowid
            c.commit()
            _audit(audit, 'Hatırlatma', 'Tekrar oluşturuldu', new, new=_ts(nxt))
    c.commit()
    return new


def snooze_until(kind, now=None):
    now = _now(now)
    if kind == '5dk':
        return now + datetime.timedelta(minutes=5)
    if kind == '1sa':
        return now + datetime.timedelta(hours=1)
    if kind == 'yarin':
        return (now + datetime.timedelta(days=1)).replace(hour=9, minute=0)
    if kind == 'hafta':
        return (now + datetime.timedelta(days=7)).replace(hour=9, minute=0)
    raise ValueError('Erteleme türü geçersiz.')


def snooze_reminder(c, rid, kind, audit=None, now=None):
    u = snooze_until(kind, now)
    c.execute('UPDATE reminders SET snooze_until=?,snooze_count=COALESCE(snooze_count,0)+1,notified=0 WHERE id=? AND COALESCE(completed,0)=0', (_ts(u), rid))
    c.commit()
    _audit(audit, 'Hatırlatma', 'Ertelendi', rid, new=_ts(u))
    return u


def create_test(c, user='', audit=None, now=None):
    """Deneme: 1 dakika sonrasına bir hatırlatma kurar (pencere, ses ve yanıp sönmenin çalıştığını görmek için)."""
    now = _now(now)
    due = now + datetime.timedelta(minutes=1)
    return save_reminder(c, dict(title='DENEME: Hatırlatma çalışıyor', detail='Bu pencereyi görüyorsanız hatırlatma sistemi çalışıyor. Bu kaydı TAMAMLANDI yapabilirsiniz.',
                                 date=tr_date(due.date()), time=due.strftime('%H:%M'), level='NORMAL', repeat='Yok', assigned=user or '(Herkes)'), None, user, audit, now)


def mark_read(c, aid, audit=None):
    c.execute('UPDATE announcements SET is_read=1 WHERE id=?', (aid,))
    c.commit()
    _audit(audit, 'Duyuru', 'Okundu', aid)


def delete_item(c, key, audit=None):
    typ, i = key.split(':')
    i = int(i)
    t = 'announcements' if typ == 'A' else 'reminders'
    r = c.execute('SELECT title FROM %s WHERE id=?' % t, (i,)).fetchone()
    c.execute('DELETE FROM %s WHERE id=?' % t, (i,))
    c.commit()
    _audit(audit, 'Duyuru' if typ == 'A' else 'Hatırlatma', 'Silindi', i, old=(r[0] if r else ''))


SHOWN_ANN = set()   # bu program oturumunda penceresi gösterilmiş duyurular (kapatılınca dakikada bir tekrar çıkmasın)


def due_now(c, user='', now=None):
    """Şimdi gösterilecekler: zamanı gelmiş, tamamlanmamış, ertelemesi bitmiş, daha önce haber verilmemiş hatırlatmalar + yayındaki duyurular."""
    ensure(c)
    now = _now(now)
    rems, anns = [], []
    for x in list_items(c, now):
        if x['kind'] == 'Hatırlatma':
            if x['done'] or x['status'] not in ('Gecikmiş', 'Bugün'):
                continue
            due = _pdt(x['date'] + ' ' + (x['time'] or '00:00'))
            if not due or due > now:
                continue
            if x['assigned'] and x['assigned'] != user:
                continue
            n = c.execute('SELECT COALESCE(notified,0) FROM reminders WHERE id=?', (x['id'],)).fetchone()
            if n and n[0]:
                continue
            rems.append(x)
        else:
            if x['status'] != 'Yayında' or x['id'] in SHOWN_ANN:
                continue
            r = c.execute('SELECT show_on_start FROM announcements WHERE id=?', (x['id'],)).fetchone()
            if not r or not r[0]:
                continue
            if x['time'] and x['time'] > now.strftime('%H:%M'):
                continue
            anns.append(x)
    return rems, anns


def mark_notified(c, rem_ids):
    for i in rem_ids:
        c.execute('UPDATE reminders SET notified=1 WHERE id=?', (i,))
    c.commit()


def users_of(c):
    try:
        return [r[0] for r in c.execute("SELECT username FROM users WHERE COALESCE(active,1)=1 ORDER BY username").fetchall()]
    except Exception:
        return []


def apply_filters(items, kind='Tümü', status='Tümü', prio='Tümü', who='Tümü', span='Tümü', q='', user='', now=None):
    now = _now(now)
    q = (q or '').strip().lower()
    out = []
    for x in items:
        if kind != 'Tümü' and x['kind'] != kind:
            continue
        if status == 'Açık':
            if x['done']:
                continue
        elif status != 'Tümü' and x['status'] != status:
            continue
        if prio != 'Tümü' and x['level'].upper() != prio.upper():
            continue
        if who == 'Benim' and x['assigned'] not in ('', user):
            continue
        if span != 'Tümü':
            try:
                d = parse_date(x['date'])
            except ValueError:
                d = None
            if not d:
                continue
            t = now.date()
            if span == 'Bugün' and d != t:
                continue
            if span == 'Bu hafta' and not (t <= d <= t + datetime.timedelta(days=7)):
                continue
            if span == 'Bu ay' and (d.year, d.month) != (t.year, t.month):
                continue
        if q and q not in (x['title'] + ' ' + x['detail']).lower():
            continue
        out.append(x)
    return out


# ---------------------------------------------------------------- ses / dikkat çekme
def sound_on(c):
    try:
        r = c.execute("SELECT v FROM settings WHERE k='duyuru_ses'").fetchone()
        return not (r and str(r[0]) == '0')
    except Exception:
        return True


def set_sound(c, on):
    c.execute('CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)')
    c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('duyuru_ses',?)", ('1' if on else '0',))
    c.commit()


def beep(widget=None):
    """Uyarı sesi: Windows'ta sistem 'ünlem' sesi, diğerlerinde zil. Hata vermez; çalındıysa True."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return True
    except Exception:
        pass
    try:
        if widget is not None:
            widget.bell()
            return True
    except Exception:
        pass
    return False


def flash_taskbar(widget):
    """Windows görev çubuğundaki program simgesini yanıp söndürür (program arkada/küçükse dikkat çeker)."""
    import os
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD), ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]
        top = widget.winfo_toplevel()
        hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
        fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3 | 12, 5, 0)   # tümü + ön plana gelene kadar
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(fi))
        return True
    except Exception:
        return False


def switch_bar(screen, current):
    """Akıllı Uyarılar ile Duyuru & Hatırlatma ekranlarının üstüne ortak geçiş çubuğu koyar (fark kullanıcıya açıkça anlatılır)."""
    import tkinter as tk
    bar = tk.Frame(screen, bg=screen['bg'])
    bar.pack(fill='x', padx=24, pady=(0, 2))
    for key, text, mod in (('uyari', 'AKILLI UYARILAR', 'Uyarılar'), ('duyuru', 'DUYURU & HATIRLATMA', 'Duyuru & Hatırlatma')):
        on = (key == current)
        tk.Button(bar, text=text, font=('Segoe UI', 10, 'bold'), relief='flat', padx=16, pady=6,
                  bg='#0b6bcb' if on else '#e2e8f0', fg='white' if on else '#0f172a', state='disabled' if on else 'normal',
                  disabledforeground='white', command=lambda m=mod: screen.winfo_toplevel().show(m)).pack(side='left', padx=(0, 6))
    tk.Label(bar, text=('Programın kendi ürettiği uyarılar: stok, vade, POS yatışı, kâr...  Sizin yazdığınız görevler de burada görünür.' if current == 'uyari'
                        else 'Sizin yazdığınız duyuru ve hatırlatmalar.  Stok, vade gibi otomatik uyarılar için AKILLI UYARILAR\'a geçin.'),
             bg=screen['bg'], fg='#64748b', font=('Segoe UI', 9)).pack(side='left', padx=10)
    screen._switch_bar = bar
    return bar


# ---------------------------------------------------------------- arayüz
def pick_date(parent, current=None):
    """Küçük takvim penceresi. Seçilen date döner, vazgeçilirse None."""
    import tkinter as tk
    d = tk.Toplevel(parent)
    d.title('Tarih seç')
    d.resizable(False, False)
    d.transient(parent.winfo_toplevel())
    cur = current or datetime.date.today()
    st = {'y': cur.year, 'm': cur.month, 'res': None}
    head = tk.Frame(d)
    head.pack(fill='x', padx=8, pady=6)
    lab = tk.Label(head, font=('Segoe UI', 11, 'bold'), width=16)
    body = tk.Frame(d)
    body.pack(padx=8, pady=(0, 8))

    def draw():
        for w in body.winfo_children():
            w.destroy()
        lab.config(text='%s %d' % (('Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık')[st['m'] - 1], st['y']))
        for i, g in enumerate(GUN):
            tk.Label(body, text=g, width=4, fg='#64748b').grid(row=0, column=i)
        for r, week in enumerate(calendar.Calendar(0).monthdayscalendar(st['y'], st['m']), 1):
            for i, dd in enumerate(week):
                if dd:
                    isnow = datetime.date(st['y'], st['m'], dd) == datetime.date.today()
                    tk.Button(body, text=str(dd), width=4, relief='flat', bg='#dbeafe' if isnow else None, command=lambda dd=dd: pick(dd)).grid(row=r, column=i, padx=1, pady=1)

    def pick(dd):
        st['res'] = datetime.date(st['y'], st['m'], dd)
        d.destroy()

    def move(k):
        m = st['m'] + k
        st['y'] += (m - 1) // 12
        st['m'] = (m - 1) % 12 + 1
        draw()
    tk.Button(head, text='◀', command=lambda: move(-1), width=3).pack(side='left')
    lab.pack(side='left', expand=True)
    tk.Button(head, text='▶', command=lambda: move(1), width=3).pack(side='left')
    draw()
    d._cal = dict(pick=pick, move=move, state=st)
    parent._cal_dialog = d
    d.grab_set()
    d.wait_window()
    return st['res']


def _date_field(w, row, var, label='Tarih'):
    import tkinter as tk
    f = tk.Frame(w)
    e = tk.Entry(f, textvariable=var, width=14, font=('Segoe UI', 11))
    e.pack(side='left')

    def cal():
        try:
            cur = parse_date(var.get())
        except ValueError:
            cur = None
        r = pick_date(w, cur)
        if r:
            var.set(tr_date(r))
    tk.Button(f, text='📅', command=cal).pack(side='left', padx=4)
    tk.Button(f, text='Bugün', command=lambda: var.set(tr_date(datetime.date.today())), font=('Segoe UI', 8)).pack(side='left')
    tk.Button(f, text='Yarın', command=lambda: var.set(tr_date(datetime.date.today() + datetime.timedelta(days=1))), font=('Segoe UI', 8)).pack(side='left', padx=2)
    return f


def build(screen, DX, user_fn, audit_fn):
    """Mevcut Base ekranının (screen) içine yeni Duyuru & Hatırlatma Merkezi'ni kurar."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    con = DX.con
    ensure(con())
    try:
        switch_bar(screen, 'duyuru')
    except Exception:
        pass
    V = {k: tk.StringVar(value=v) for k, v in (('kind', 'Tümü'), ('status', 'Açık'), ('prio', 'Tümü'), ('who', 'Tümü'), ('span', 'Tümü'), ('q', ''))}
    screen.q = V['q']
    state = {'items': []}
    top = screen.card()
    bar = tk.Frame(top, bg='white')
    bar.pack(fill='x', padx=6, pady=8)
    for text, cmd, red in (('YENİ DUYURU', lambda: announcement_form(), True), ('YENİ HATIRLATMA', lambda: reminder_form(), True), ('DÜZENLE', lambda: edit(), False),
                           ('TAMAMLANDI / OKUNDU', lambda: finish(), False), ('SİL', lambda: delete(), False), ('YENİLE', lambda: refresh(), False)):
        screen.btn(bar, text, cmd, red).pack(side='left', padx=3)
    def _test():
        create_test(con(), user_fn(), audit_fn)
        refresh()
        messagebox.showinfo('Duyuru & Hatırlatma', 'Deneme hatırlatması 1 dakika sonrasına kuruldu.\nProgramı açık bırakın; en geç 2 dakika içinde pencere açılmalı (ses açıksa uyarı sesi de çalar).', parent=screen.winfo_toplevel())
    screen.btn(bar, '1 DK SONRAYA DENEME', _test).pack(side='left', padx=3)
    mb = tk.Menubutton(bar, text='ERTELE ▾', relief='solid', bd=1, font=('Segoe UI', 10, 'bold'), bg='white', padx=12, pady=6)
    menu = tk.Menu(mb, tearoff=0)
    for t, k in (('5 dakika sonra', '5dk'), ('1 saat sonra', '1sa'), ('Yarın 09:00', 'yarin'), ('1 hafta sonra', 'hafta')):
        menu.add_command(label=t, command=lambda k=k: snooze(k))
    mb.config(menu=menu)
    mb.pack(side='left', padx=3)
    # özet düğmeleri
    chips = tk.Frame(screen, bg=screen['bg'])
    chips.pack(fill='x', padx=24)
    chip = {}
    for key, text, color, status in (('gecikmis', 'GECİKMİŞ', '#c2410c', 'Gecikmiş'), ('bugun', 'BUGÜN', '#1d4ed8', 'Bugün'), ('yaklasan', 'YAKLAŞAN (7 gün)', '#0f766e', 'Yaklaşan'), ('bitti', 'TAMAMLANAN / OKUNAN', '#64748b', 'Tamamlandı')):
        b = tk.Button(chips, text=text + ': 0', font=('Segoe UI', 10, 'bold'), fg='white', bg=color, relief='flat', padx=14, pady=6,
                      command=lambda s=status: (V['status'].set(s if s != 'Tamamlandı' else 'Tümü'), refresh()))
        b.pack(side='left', padx=(0, 8), pady=4)
        chip[key] = (b, text)
    # süzgeçler
    fl = tk.Frame(screen, bg='white', highlightthickness=1, highlightbackground='#cbd5e1')
    fl.pack(fill='x', padx=24, pady=(6, 4))

    def combo(label, key, values, width):
        tk.Label(fl, text=label, bg='white', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=(10, 3), pady=8)
        cb = ttk.Combobox(fl, textvariable=V[key], values=values, state='readonly', width=width)
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', lambda e: refresh())
        return cb
    combo('Tür', 'kind', ('Tümü', 'Duyuru', 'Hatırlatma'), 11)
    combo('Durum', 'status', ('Açık', 'Tümü', 'Gecikmiş', 'Bugün', 'Yaklaşan', 'Bekliyor', 'Ertelendi', 'Yayında', 'Tamamlandı', 'Okundu', 'Süresi doldu'), 12)
    combo('Öncelik', 'prio', ('Tümü', 'KRİTİK', 'ÖNEMLİ', 'NORMAL'), 10)
    combo('Kimin', 'who', ('Tümü', 'Benim'), 8)
    combo('Tarih', 'span', ('Tümü', 'Bugün', 'Bu hafta', 'Bu ay'), 10)
    tk.Label(fl, text='Ara', bg='white', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=(12, 3))
    e = tk.Entry(fl, textvariable=V['q'], width=22, bg='#fff59d')
    e.pack(side='left')
    e.bind('<KeyRelease>', lambda ev: refresh())
    tk.Button(fl, text='Süzgeçleri temizle', command=lambda: ([V[k].set(v) for k, v in (('kind', 'Tümü'), ('status', 'Açık'), ('prio', 'Tümü'), ('who', 'Tümü'), ('span', 'Tümü'), ('q', ''))], refresh()),
              font=('Segoe UI', 9)).pack(side='left', padx=10)
    # liste
    box = tk.Frame(screen, bg=screen['bg'])
    box.pack(fill='both', expand=True, padx=24, pady=(4, 0))
    cols = (('type', 'Tür', 90, 'w', False), ('prio', 'Öncelik', 80, 'w', False), ('title', 'Başlık', 360, 'w', True), ('date', 'Tarih', 100, 'w', False), ('time', 'Saat', 60, 'w', False),
            ('end', 'Bitiş', 100, 'w', False), ('repeat', 'Tekrar', 80, 'w', False), ('status', 'Durum', 105, 'w', False), ('who', 'Atanan', 110, 'w', False), ('by', 'Oluşturan', 110, 'w', False))
    t = ttk.Treeview(box, columns=[c[0] for c in cols], show='headings', selectmode='browse', height=12)
    for k, h, wd, an, st in cols:
        t.heading(k, text=h)
        t.column(k, width=wd, anchor=an, stretch=st)
    sb = ttk.Scrollbar(box, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    t.tag_configure('kritik', background='#fee2e2')
    t.tag_configure('gec', background='#ffedd5', foreground='#9a3412')
    t.tag_configure('bugun', background='#dbeafe')
    t.tag_configure('bitti', foreground='#94a3b8')
    det = tk.Text(screen, height=5, wrap='word', font=('Segoe UI', 10), bg='#f8fafc', relief='solid', bd=1, state='disabled')
    det.pack(fill='x', padx=24, pady=(6, 4))
    foot = tk.Frame(screen, bg=screen['bg'])
    foot.pack(fill='x', padx=24, pady=(0, 8))
    sv = tk.IntVar(value=1 if sound_on(con()) else 0)

    def _snd():
        set_sound(con(), bool(sv.get()))
        if sv.get():
            beep(screen)
    tk.Checkbutton(foot, text='Uyarı sesi çal ve görev çubuğunu yanıp söndür', variable=sv, command=_snd, bg=screen['bg']).pack(side='left')
    tk.Label(foot, text='Hatırlatma saati gelince küçük pencerede haber verilir (program açıkken, dakikada bir kontrol). Silme ve tamamlama işlem geçmişine yazılır.',
             bg=screen['bg'], fg='#64748b', font=('Segoe UI', 9)).pack(side='left', padx=12)
    screen._sound_var = sv

    def refresh(_=None):
        now = _now()
        items = list_items(con(), now)
        c = counts(items)
        for k, (b, text) in chip.items():
            b.config(text='%s: %d' % (text, c[k]))
        shown = apply_filters(items, V['kind'].get(), V['status'].get(), V['prio'].get(), V['who'].get(), V['span'].get(), V['q'].get(), user_fn(), now)
        state['items'] = {x['key']: x for x in shown}
        t.delete(*t.get_children())
        for x in shown:
            tag = ('bitti',) if x['done'] else (('gec',) if x['overdue'] else (('kritik',) if x['rank'] == 0 else (('bugun',) if x['today'] else ())))
            dd = tr_date(x['date']) if x['date'] else ''
            t.insert('', 'end', iid=x['key'], tags=tag, values=(x['kind'], x['level'], x['title'], dd, x['time'], tr_date(x['end']) if x['end'] else '', x['repeat'] if x['repeat'] != 'Yok' else '',
                                                              x['status'], x['assigned'] or ('(Herkes)' if x['kind'] == 'Hatırlatma' else ''), x['creator']))
        show_detail()

    def show_detail(_=None):
        det.config(state='normal')
        det.delete('1.0', 'end')
        x = state['items'].get(sel()) if sel() else None
        if x:
            det.insert('end', '%s — %s\n' % (x['title'], x['status']))
            det.insert('end', x['detail'] or '(açıklama yok)')
        det.config(state='disabled')
    t.bind('<<TreeviewSelect>>', show_detail)

    def sel():
        s = t.selection()
        return s[0] if s else ''

    def need():
        k = sel()
        if not k:
            messagebox.showinfo('Duyuru & Hatırlatma', 'Önce listeden bir kayıt seçin.', parent=screen.winfo_toplevel())
        return k

    def form_shell(title, size):
        from ALFABE_PENCERE import center  # noqa
        w = tk.Toplevel(screen.winfo_toplevel())
        w.title(title)
        w.geometry(size)
        w.transient(screen.winfo_toplevel())
        w.configure(padx=14, pady=10)
        w.grid_columnconfigure(1, weight=1)
        return w

    def row_label(w, r, text):
        tk.Label(w, text=text, font=('Segoe UI', 10, 'bold')).grid(row=r, column=0, sticky='nw', padx=(0, 12), pady=7)

    def reminder_form(rid=None):
        d = con().execute('SELECT title,detail,due,level,repeat_rule,assigned_to FROM reminders WHERE id=?', (rid,)).fetchone() if rid else None
        w = form_shell('Hatırlatma Düzenle' if rid else 'Yeni Hatırlatma', '640x560')
        dd = _pdt(d[2]) if d else None
        v = dict(title=tk.StringVar(value=d[0] if d else ''), date=tk.StringVar(value=tr_date(dd.date()) if dd else tr_date(datetime.date.today())),
                 time=tk.StringVar(value=dd.strftime('%H:%M') if dd else '09:00'), level=tk.StringVar(value=(d[3] or 'NORMAL') if d else 'NORMAL'),
                 repeat=tk.StringVar(value=(d[4] or 'Yok') if d else 'Yok'), assigned=tk.StringVar(value=(d[5] or '(Herkes)') if d else '(Herkes)'))
        row_label(w, 0, 'Başlık *'); tk.Entry(w, textvariable=v['title'], font=('Segoe UI', 11)).grid(row=0, column=1, sticky='we')
        row_label(w, 1, 'Detay / Not'); txt = tk.Text(w, height=5, font=('Segoe UI', 10), wrap='word'); txt.grid(row=1, column=1, sticky='we'); txt.insert('1.0', d[1] if d and d[1] else '')
        row_label(w, 2, 'Tarih *'); _date_field(w, 2, v['date']).grid(row=2, column=1, sticky='w')
        row_label(w, 3, 'Saat'); ttk.Combobox(w, textvariable=v['time'], values=['%02d:%02d' % (h, m) for h in range(6, 23) for m in (0, 30)], width=8).grid(row=3, column=1, sticky='w')
        row_label(w, 4, 'Öncelik'); ttk.Combobox(w, textvariable=v['level'], values=REM_LEVELS, state='readonly', width=12).grid(row=4, column=1, sticky='w')
        row_label(w, 5, 'Tekrar'); ttk.Combobox(w, textvariable=v['repeat'], values=REPEATS, state='readonly', width=12).grid(row=5, column=1, sticky='w')
        tk.Label(w, text='Tamamlayınca bir sonraki tarih için yenisi otomatik oluşur.', fg='#64748b').grid(row=6, column=1, sticky='w')
        row_label(w, 7, 'Atanan kişi'); ttk.Combobox(w, textvariable=v['assigned'], values=['(Herkes)'] + users_of(con()), width=22).grid(row=7, column=1, sticky='w')
        msg = tk.Label(w, text='', fg='#b91c1c', font=('Segoe UI', 10, 'bold'))
        msg.grid(row=8, column=0, columnspan=2, sticky='w', pady=(6, 0))

        def save():
            f = {k: x.get() for k, x in v.items()}
            f['detail'] = txt.get('1.0', 'end').strip()
            try:
                save_reminder(con(), f, rid, user_fn(), audit_fn)
            except ValueError as ex:
                msg.config(text='✗ ' + str(ex)); return
            w.destroy(); refresh()
        screen.btn(w, 'KAYDET', save, True).grid(row=9, column=1, sticky='e', pady=14)
        w._f = dict(vars=v, text=txt, save=save, msg=msg)
        screen._last_form = w
        return w

    def announcement_form(aid=None):
        d = con().execute('SELECT title,description,start_date,end_date,alert_time,priority,show_on_start FROM announcements WHERE id=?', (aid,)).fetchone() if aid else None
        w = form_shell('Duyuru Düzenle' if aid else 'Yeni Duyuru', '640x590')
        v = dict(title=tk.StringVar(value=d[0] if d else ''), start=tk.StringVar(value=tr_date(d[2]) if d and d[2] else tr_date(datetime.date.today())),
                 end=tk.StringVar(value=tr_date(d[3]) if d and d[3] else ''), time=tk.StringVar(value=(d[4] if d else '09:00') or ''),
                 priority=tk.StringVar(value=(d[5] or 'Normal') if d else 'Normal'), show=tk.IntVar(value=int(d[6]) if d else 1))
        row_label(w, 0, 'Başlık *'); tk.Entry(w, textvariable=v['title'], font=('Segoe UI', 11)).grid(row=0, column=1, sticky='we')
        row_label(w, 1, 'Açıklama'); txt = tk.Text(w, height=6, font=('Segoe UI', 10), wrap='word'); txt.grid(row=1, column=1, sticky='we'); txt.insert('1.0', d[1] if d and d[1] else '')
        row_label(w, 2, 'Başlangıç'); _date_field(w, 2, v['start']).grid(row=2, column=1, sticky='w')
        row_label(w, 3, 'Bitiş (boşsa süresiz)'); _date_field(w, 3, v['end']).grid(row=3, column=1, sticky='w')
        row_label(w, 4, 'Uyarı saati'); ttk.Combobox(w, textvariable=v['time'], values=[''] + ['%02d:%02d' % (h, m) for h in range(6, 23) for m in (0, 30)], width=8).grid(row=4, column=1, sticky='w')
        row_label(w, 5, 'Öncelik'); ttk.Combobox(w, textvariable=v['priority'], values=ANN_LEVELS, state='readonly', width=12).grid(row=5, column=1, sticky='w')
        tk.Checkbutton(w, text='Program açılışında zamanı gelince göster', variable=v['show']).grid(row=6, column=1, sticky='w')
        msg = tk.Label(w, text='', fg='#b91c1c', font=('Segoe UI', 10, 'bold'))
        msg.grid(row=7, column=0, columnspan=2, sticky='w', pady=(6, 0))

        def save():
            f = {k: x.get() for k, x in v.items()}
            f['description'] = txt.get('1.0', 'end').strip()
            try:
                save_announcement(con(), f, aid, user_fn(), audit_fn)
            except ValueError as ex:
                msg.config(text='✗ ' + str(ex)); return
            w.destroy(); refresh()
        screen.btn(w, 'KAYDET', save, True).grid(row=8, column=1, sticky='e', pady=14)
        w._f = dict(vars=v, text=txt, save=save, msg=msg)
        screen._last_form = w
        return w

    def edit(_=None):
        k = need()
        if k:
            typ, i = k.split(':')
            (announcement_form if typ == 'A' else reminder_form)(int(i))

    def finish():
        k = need()
        if not k:
            return
        typ, i = k.split(':')
        if typ == 'A':
            mark_read(con(), int(i), audit_fn)
        else:
            n = complete_reminder(con(), int(i), user_fn(), audit_fn)
            if n:
                x = state['items'].get(k)
                messagebox.showinfo('Duyuru & Hatırlatma', 'Tamamlandı. Bir sonraki hatırlatma otomatik oluşturuldu.', parent=screen.winfo_toplevel())
        refresh()

    def snooze(kind):
        k = need()
        if not k:
            return
        if not k.startswith('R:'):
            messagebox.showinfo('Duyuru & Hatırlatma', 'Erteleme yalnız hatırlatmalarda kullanılır.', parent=screen.winfo_toplevel()); return
        snooze_reminder(con(), int(k[2:]), kind, audit_fn)
        refresh()

    def delete():
        k = need()
        if not k:
            return
        if not messagebox.askyesno('Duyuru & Hatırlatma', 'Seçili kayıt silinsin mi? (İşlem geçmişine yazılır.)', parent=screen.winfo_toplevel()):
            return
        delete_item(con(), k, audit_fn)
        refresh()
    t.bind('<Double-1>', edit)
    t.bind('<Return>', edit)
    t.bind('<Delete>', lambda e: delete())
    screen._duyuru = dict(refresh=refresh, tree=t, filters=V, reminder_form=reminder_form, announcement_form=announcement_form, finish=finish, snooze=snooze,
                          delete=delete, state=state, chips=chip, detail=det, select=lambda k: (t.selection_set(k), show_detail()))
    screen.refresh = refresh
    refresh()


def popup(parent, DX, user_fn, audit_fn, on_change=None):
    """Zamanı gelen hatırlatma/duyuruları küçük pencerede gösterir. Gösterilecek yoksa None."""
    import tkinter as tk
    from tkinter import ttk
    c = DX.con()
    rems, anns = due_now(c, user_fn())
    if not rems and not anns:
        return None
    mark_notified(c, [x['id'] for x in rems])
    SHOWN_ANN.update(x['id'] for x in anns)
    w = tk.Toplevel(parent)
    w.title('Hatırlatma / Duyuru')
    w.geometry('720x420')
    w.configure(bg='#fff7ed')   # bağımsız pencere: ana program küçültülmüş olsa da görünür
    tk.Label(w, text='ZAMANI GELEN HATIRLATMA VE DUYURULAR', font=('Segoe UI', 14, 'bold'), bg='#fff7ed', fg='#9a3412').pack(anchor='w', padx=14, pady=(12, 6))
    tv = ttk.Treeview(w, columns=('k', 'p', 't', 'd'), show='headings', height=8)
    for k, h, wd in (('k', 'Tür', 90), ('p', 'Öncelik', 80), ('t', 'Başlık', 360), ('d', 'Zaman', 140)):
        tv.heading(k, text=h)
        tv.column(k, width=wd, anchor='w', stretch=(k == 't'))
    tv.pack(fill='both', expand=True, padx=14)
    items = {}
    for x in rems + anns:
        items[x['key']] = x
        tv.insert('', 'end', iid=x['key'], values=(x['kind'], x['level'], x['title'], (tr_date(x['date']) + ' ' + x['time']).strip()))
    det = tk.Label(w, text='', bg='#fff7ed', anchor='w', justify='left', wraplength=680, fg='#475569')
    det.pack(fill='x', padx=14, pady=4)
    tv.bind('<<TreeviewSelect>>', lambda e: det.config(text=(items[tv.selection()[0]]['detail'] or '') if tv.selection() else ''))

    def after():
        if tv.get_children() == ():
            w.destroy()
        if on_change:
            try:
                on_change()
            except Exception:
                pass

    def act(fn):
        s = tv.selection()
        if not s:
            return
        fn(s[0])
        tv.delete(s[0])
        after()

    def done(k):
        if k.startswith('R:'):
            complete_reminder(c, int(k[2:]), user_fn(), audit_fn)
        else:
            mark_read(c, int(k[2:]), audit_fn)

    def sn(kind):
        def f(k):
            if k.startswith('R:'):
                snooze_reminder(c, int(k[2:]), kind, audit_fn)
        act(f)
    bf = tk.Frame(w, bg='#fff7ed')
    bf.pack(fill='x', padx=14, pady=10)
    tk.Button(bf, text='TAMAMLANDI / OKUNDU', command=lambda: act(done), font=('Segoe UI', 10, 'bold'), bg='#0b6bcb', fg='white', padx=12, pady=6).pack(side='left')
    for t_, k in (('5 DK ERTELE', '5dk'), ('1 SAAT', '1sa'), ('YARIN', 'yarin')):
        tk.Button(bf, text=t_, command=lambda k=k: sn(k), font=('Segoe UI', 9, 'bold'), padx=8, pady=6).pack(side='left', padx=4)
    tk.Button(bf, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='right')
    w._popup = dict(tree=tv, done=lambda k: (tv.selection_set(k), act(done)), snooze=lambda k, kind: (tv.selection_set(k), sn(kind)))
    try:
        if sound_on(c):
            beep(w)
            flash_taskbar(parent)
    except Exception:
        pass
    try:
        w.deiconify()
        w.lift()
        w.focus_force()
        w.attributes('-topmost', True)
        w.after(600, lambda: w.attributes('-topmost', False))
    except Exception:
        pass
    return w
