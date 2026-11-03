# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - POS Takip (v415): kart satışlarının komisyonu, bankaya geçiş tarihi ve hesaba geçiş takibi.

Akış
 1) Kart satışları (Hızlı Satış, Cari Satış, sipariş, iade) POS hesabına yazılmıştır (finance: 'Kart/POS' ve 'POS' hesapları).
 2) HESAPLA: her POS için her satış gününe bir kesim satırı çıkarılır: brüt, komisyon (POS'un oranı), net, beklenen yatış günü
    (satış günü + valör günü, hafta sonu ise pazartesi). Henüz yatmamış satırlar her hesaplamada güncellenir.
 3) HESABA GEÇTİ: banka hesabını ve bankaya YATAN tutarı yazarsınız. Program tek işlemde:
      POS hesabından yatan net tutarı düşer, banka hesabına ekler (Banka Merkezi'nde "POS Hesaba Geçiş" olarak görünür),
      brüt ile yatan arasındaki farkı "POS Komisyonu" giderine yazar.
    Birden fazla günü seçip toplu yatışı tek tutarla da işleyebilirsiniz (fark komisyona dağıtılır).
 4) GERİ AL: yanlış işlenen yatış, ters kayıtlarla iptal edilir; satır yeniden "Bekliyor" olur.
Satış kayıtlarına dokunulmaz; yalnız pos_settlements tablosu ve yatış anındaki finans kayıtları yazılır.
"""
import datetime
import sqlite3

POS_ACCOUNTS = ('Kart/POS', 'POS')
SALE_SOURCES = ('SALE', 'CARI_SALE', 'SALE_RETURN', 'ORDER_SALE', 'REVERSAL')
CATEGORY = 'POS Komisyonu'
NONAME = '(POS belirtilmemiş)'

_NEW_COLS = [('pos_account', 'TEXT'), ('bank_account', 'TEXT'), ('received', 'REAL'), ('paid_dt', 'TEXT'), ('user', 'TEXT'), ('note', 'TEXT'),
             ('n', 'INTEGER DEFAULT 0'), ('finance_out', 'INTEGER'), ('finance_bank', 'INTEGER'), ('finance_fee', 'INTEGER'), ('expense_id', 'INTEGER'),
             ('op_key', 'TEXT'), ('batch', 'TEXT')]


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def money(v):
    s = '{:,.2f}'.format(_f(v))
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def tr_date(s):
    t = str(s or '')[:10]
    try:
        y, m, d = t.split('-')
        return '%s.%s.%s' % (d, m, y)
    except Exception:
        return t


def ensure_schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS pos_terminals(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,bank TEXT,commission_rate REAL DEFAULT 0,active INTEGER DEFAULT 1,created TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS pos_settlements(id INTEGER PRIMARY KEY,dt TEXT,pos_name TEXT,gross REAL DEFAULT 0,commission REAL DEFAULT 0,net REAL DEFAULT 0,"
              "due_date TEXT,status TEXT DEFAULT 'Bekliyor',source_id INTEGER)")
    have = {r[1] for r in c.execute('PRAGMA table_info(pos_terminals)').fetchall()}
    if 'valor_days' not in have:
        c.execute('ALTER TABLE pos_terminals ADD COLUMN valor_days INTEGER DEFAULT 1')
    if 'monthly_fee' not in have:
        c.execute('ALTER TABLE pos_terminals ADD COLUMN monthly_fee REAL DEFAULT 0')
    if 'skip_weekend' not in have:
        c.execute('ALTER TABLE pos_terminals ADD COLUMN skip_weekend INTEGER DEFAULT 1')
    c.execute("CREATE TABLE IF NOT EXISTS pos_fees(id INTEGER PRIMARY KEY,pos_name TEXT,month TEXT,amount REAL DEFAULT 0,bank_account TEXT,dt TEXT,"
              "finance_id INTEGER,expense_id INTEGER,status TEXT DEFAULT 'Aktif',user TEXT,note TEXT)")
    have = {r[1] for r in c.execute('PRAGMA table_info(pos_settlements)').fetchall()}
    for name, typ in _NEW_COLS:
        if name not in have:
            c.execute('ALTER TABLE pos_settlements ADD COLUMN %s %s' % (name, typ))
    try:
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_pos_settle_day ON pos_settlements(dt,pos_name)')
    except sqlite3.Error:
        pass
    c.commit()


def due_date(day_iso, valor, skip_weekend=True):
    d = datetime.date.fromisoformat(day_iso) + datetime.timedelta(days=max(int(valor or 0), 0))
    while skip_weekend and d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def terminals(c):
    ensure_schema(c)
    return [dict(id=r[0], name=r[1], bank=r[2] or '', rate=_f(r[3]), active=int(r[4] or 0), valor=int(r[5] if r[5] is not None else 1), fee=_f(r[6]),
                 skip=bool(1 if r[7] is None else r[7]))
            for r in c.execute('SELECT id,name,bank,commission_rate,active,valor_days,monthly_fee,skip_weekend FROM pos_terminals ORDER BY name').fetchall()]


def set_terminal(c, tid, rate, valor, fee=None):
    rate, valor = float(rate), int(valor)
    if fee is not None and not 0 <= float(fee) <= 100000:
        raise ValueError('Aylık POS ücreti geçersiz.')
    if not 0 <= rate <= 50:
        raise ValueError('Komisyon oranı 0-50 arasında olmalı.')
    if not 0 <= valor <= 60:
        raise ValueError('Valör günü 0-60 arasında olmalı.')
    c.execute('UPDATE pos_terminals SET commission_rate=?,valor_days=? WHERE id=?', (rate, valor, tid))
    if fee is not None:
        c.execute('UPDATE pos_terminals SET monthly_fee=? WHERE id=?', (float(fee), tid))
    c.commit()


def generate(c, since_days=120, today=None):
    """Kart satışlarından kesim satırlarını üretir/günceller. Yatmış satırlara dokunmaz. Dönüş: (yeni, güncellenen)."""
    ensure_schema(c)
    today = today or datetime.date.today()
    start = (today - datetime.timedelta(days=since_days)).isoformat() if since_days else '0000-00-00'
    terms = terminals(c)
    active = [t for t in terms if t['active']]
    default_pos = active[0]['name'] if len(active) == 1 else ''
    byname = {t['name']: t for t in terms}
    rows = c.execute(
        "SELECT substr(dt,1,10) d, COALESCE(pos_name,'') pos, account, COUNT(*) n, SUM(amount) amt FROM finance "
        "WHERE account IN (%s) AND source IN (%s) AND substr(dt,1,10)>=? AND substr(dt,1,10)<=? "
        "GROUP BY 1,2,3" % (','.join('?' * len(POS_ACCOUNTS)), ','.join('?' * len(SALE_SOURCES))),
        POS_ACCOUNTS + SALE_SOURCES + (start, today.isoformat())).fetchall()
    groups = {}
    for r in rows:
        pos = r[1] or default_pos or NONAME
        g = groups.setdefault((r[0], pos), dict(n=0, gross=0.0, acc={}))
        g['n'] += int(r[3])
        g['gross'] += _f(r[4])
        g['acc'][r[2]] = g['acc'].get(r[2], 0.0) + abs(_f(r[4]))
    new = upd = 0
    for (d, pos), g in sorted(groups.items()):
        gross = round(g['gross'], 2)
        if abs(gross) < 0.005:
            continue
        t = byname.get(pos)
        rate = t['rate'] if t else 0.0
        valor = t['valor'] if t else 1
        comm = round(gross * rate / 100.0, 2) if gross > 0 else 0.0
        net = round(gross - comm, 2)
        acct = max(g['acc'], key=g['acc'].get)
        due = due_date(d, valor, t['skip'] if t else True)
        ex = c.execute('SELECT id,status,gross FROM pos_settlements WHERE dt=? AND pos_name=?', (d, pos)).fetchone()
        if ex is None:
            c.execute("INSERT INTO pos_settlements(dt,pos_name,gross,commission,net,due_date,status,pos_account,n) VALUES(?,?,?,?,?,?,'Bekliyor',?,?)",
                      (d, pos, gross, comm, net, due, acct, g['n']))
            new += 1
        elif ex[1] == 'Bekliyor':
            c.execute('UPDATE pos_settlements SET gross=?,commission=?,net=?,due_date=?,pos_account=?,n=? WHERE id=?', (gross, comm, net, due, acct, g['n'], ex[0]))
            upd += 1
    # satışı kalmayan (iptal edilmiş vb.) bekleyen satırlar sıfırlanır
    live = {k for k, g in groups.items() if abs(g['gross']) >= 0.005}
    for r in c.execute("SELECT id,dt,pos_name FROM pos_settlements WHERE status='Bekliyor' AND dt>=?", (start,)).fetchall():
        if (r[1], r[2]) not in live:
            c.execute('DELETE FROM pos_settlements WHERE id=?', (r[0],))
    c.commit()
    return new, upd


def listing(c, pos='', status='', date_from='', date_to=''):
    ensure_schema(c)
    sql = ("SELECT id,dt,pos_name,n,gross,commission,net,due_date,status,bank_account,paid_dt,received,note FROM pos_settlements WHERE 1=1")
    a = []
    if pos:
        sql += ' AND pos_name=?'
        a.append(pos)
    if status:
        sql += ' AND status=?'
        a.append(status)
    if date_from:
        sql += ' AND dt>=?'
        a.append(date_from)
    if date_to:
        sql += ' AND dt<=?'
        a.append(date_to)
    sql += ' ORDER BY dt DESC, pos_name'
    keys = ['id', 'dt', 'pos', 'n', 'gross', 'commission', 'net', 'due', 'status', 'bank', 'paid_dt', 'received', 'note']
    return [dict(zip(keys, r)) for r in c.execute(sql, a).fetchall()]


def kpis(c, today=None):
    ensure_schema(c)
    t = (today or datetime.date.today()).isoformat()
    q = lambda s, a=(): (c.execute(s, a).fetchone() or (0, 0))
    wait = q("SELECT COALESCE(SUM(net),0),COUNT(*) FROM pos_settlements WHERE status='Bekliyor'")
    due = q("SELECT COALESCE(SUM(net),0),COUNT(*) FROM pos_settlements WHERE status='Bekliyor' AND due_date<=?", (t,))
    month = t[:7]
    fee = q("SELECT COALESCE(SUM(commission),0),COUNT(*) FROM pos_settlements WHERE substr(dt,1,7)=? AND gross>0", (month,))
    return dict(wait=_f(wait[0]), wait_n=int(wait[1]), due=_f(due[0]), due_n=int(due[1]), fee_month=_f(fee[0]))


# ----------------------------------------------------------------------------------------------
# Hesaba geçiş / geri alma
# ----------------------------------------------------------------------------------------------
def settle(c, ids, bank_account, received_total=None, paid_date=None, user='', note='', audit=None):
    """Seçili kesimleri 'Yattı' yapar. received_total=None ise beklenen net toplam. Dönüş: dict(rows, received, fee)."""
    ids = [int(i) for i in ids]
    if not ids:
        raise ValueError('Satır seçilmedi.')
    if not str(bank_account or '').strip():
        raise ValueError('Banka hesabı seçin.')
    paid_date = paid_date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(paid_date)
    except ValueError:
        raise ValueError('Yatış tarihi geçersiz (YYYY-AA-GG).')
    rows = [c.execute('SELECT id,dt,pos_name,gross,commission,net,status,pos_account FROM pos_settlements WHERE id=?', (i,)).fetchone() for i in ids]
    if any(r is None for r in rows):
        raise ValueError('Seçilen kesim bulunamadı; listeyi yenileyin.')
    if any(r[6] != 'Bekliyor' for r in rows):
        raise ValueError('Seçilenlerden biri zaten yatmış görünüyor; listeyi yenileyin.')
    if len({r[2] for r in rows}) > 1:
        raise ValueError('Tek seferde yalnız aynı POS\'un günleri işlenebilir.')
    exp_net = round(sum(_f(r[5]) for r in rows), 2)
    got = exp_net if received_total is None else round(float(received_total), 2)
    gross_sum = round(sum(_f(r[3]) for r in rows), 2)
    if got < 0 or got > gross_sum + 0.005:
        raise ValueError('Yatan tutar 0 ile brüt toplam (%s) arasında olmalı.' % money(gross_sum))
    # fark (beklenenden az yatan) komisyona dağıtılır — yalnız brütü pozitif satırlara, brüt oranında
    delta = round(exp_net - got, 2)
    pos_rows = [r for r in rows if _f(r[3]) > 0]
    alloc = {r[0]: 0.0 for r in rows}
    if abs(delta) > 0.004:
        if not pos_rows:
            raise ValueError('Bu satırlarda komisyon farkı dağıtılamaz.')
        tot = sum(_f(r[3]) for r in pos_rows)
        left = delta
        for r in pos_rows[:-1]:
            share = round(delta * _f(r[3]) / tot, 2)
            alloc[r[0]] = share
            left = round(left - share, 2)
        alloc[pos_rows[-1][0]] = left
    ensure_cat = "INSERT OR IGNORE INTO expense_categories(name,active) VALUES(?,1)"
    stamp = paid_date + ' ' + datetime.datetime.now().strftime('%H:%M:%S')
    batch = 'POS-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    total_fee = 0.0
    total_recv = 0.0
    c.execute('BEGIN IMMEDIATE')
    try:
        try:
            c.execute(ensure_cat, (CATEGORY,))
        except sqlite3.Error:
            pass
        for r in rows:
            sid, d, pos, gross, comm, net, _st, acct = r
            gross, net = _f(gross), _f(net)
            recv = round(net - alloc[sid], 2)
            fee = round(gross - recv, 2) if gross > 0 else 0.0
            if gross <= 0:
                recv = round(gross, 2)
            acct = acct or POS_ACCOUNTS[0]
            lab = '%s %s POS yatışı (%s)' % (tr_date(d), pos, batch)
            f_out = c.execute("INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)",
                              (stamp, acct, 'POS Hesaba Geçiş Çıkış', -recv, 'POS_SETTLE', sid, lab + ((' ' + note) if note else ''))).lastrowid
            f_bank = c.execute("INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)",
                               (stamp, bank_account, 'POS Hesaba Geçiş', recv, 'POS_SETTLE', sid, lab + ((' ' + note) if note else ''))).lastrowid
            key = batch + '-%d' % sid
            c.execute("INSERT INTO bank_operations(op_key,dt,kind,account,amount,finance_id,note,user) VALUES(?,?,?,?,?,?,?,?)",
                      (key, stamp, 'POS Hesaba Geçiş', bank_account, recv, f_bank, lab, user))
            f_fee = exp_id = None
            if fee > 0.004:
                f_fee = c.execute("INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)",
                                  (stamp, acct, 'Gider', -fee, 'EXPENSE_PRO', sid, CATEGORY + ' - ' + lab)).lastrowid
                exp_id = c.execute("INSERT INTO expense_records(finance_id,dt,category,account,amount,payment_type,note,user,status) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (f_fee, stamp, CATEGORY, acct, fee, 'Kart', lab, user, 'Aktif')).lastrowid
            c.execute("UPDATE pos_settlements SET status='Yattı',commission=?,net=?,received=?,paid_dt=?,bank_account=?,user=?,note=?,finance_out=?,finance_bank=?,"
                      "finance_fee=?,expense_id=?,op_key=?,batch=? WHERE id=?",
                      (fee, recv, recv, paid_date, bank_account, user, note, f_out, f_bank, f_fee, exp_id, key, batch, sid))
            total_fee += fee
            total_recv += recv
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (stamp, user, 'POS Takip', 'Hesaba Geçiş', ids[0], '', 'satır=%d yatan=%.2f komisyon=%.2f banka=%s' % (len(rows), total_recv, total_fee, bank_account), batch))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return dict(rows=len(rows), received=round(total_recv, 2), fee=round(total_fee, 2), batch=batch)


def undo(c, sid, reason, user=''):
    """Yatmış bir kesimi ters kayıtlarla geri alır (kayıt silinmez)."""
    if not str(reason or '').strip():
        raise ValueError('Geri alma nedeni yazılmalı.')
    r = c.execute("SELECT id,status,pos_account,bank_account,received,finance_out,finance_bank,finance_fee,expense_id,op_key,dt,pos_name FROM pos_settlements WHERE id=?", (sid,)).fetchone()
    if r is None or r[1] != 'Yattı':
        raise ValueError('Bu satır "Yattı" durumunda değil.')
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    key = 'REV-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    recv = _f(r[4])
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute("INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)",
                  (stamp, r[2], 'POS Geçiş İptali', recv, 'POS_SETTLE_REV', sid, '%s: %s' % (key, reason)))
        rb = c.execute("INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)",
                       (stamp, r[3], 'İptal / Ters Kayıt', -recv, 'BANK_REVERSAL', sid, '%s: %s' % (key, reason))).lastrowid
        op = c.execute("SELECT id FROM bank_operations WHERE op_key=? AND status='Aktif'", (r[9],)).fetchone()
        if op:
            c.execute("UPDATE bank_operations SET status='İptal' WHERE id=?", (op[0],))
            c.execute("INSERT INTO bank_operations(op_key,dt,kind,account,amount,finance_id,reverse_of,status,note,user) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (key, stamp, 'İptal / Ters Kayıt', r[3], -recv, rb, op[0], 'Aktif', reason, user))
        if r[8]:
            e = c.execute("SELECT id,amount,account,status FROM expense_records WHERE id=?", (r[8],)).fetchone()
            if e and e[3] == 'Aktif':
                c.execute("UPDATE expense_records SET status='İptal' WHERE id=?", (e[0],))
                c.execute("INSERT INTO finance(dt,account,kind,amount,source,note) VALUES(?,?,?,?,?,?)",
                          (stamp, e[2], 'Gider İptal', _f(e[1]), 'EXPENSE_REVERSAL', 'Gider #%s - %s' % (e[0], reason)))
        # brüt/komisyon bir sonraki HESAPLA'da yeniden hesaplanır
        c.execute("UPDATE pos_settlements SET status='Bekliyor',received=NULL,paid_dt=NULL,bank_account=NULL,finance_out=NULL,finance_bank=NULL,finance_fee=NULL,expense_id=NULL,"
                  "op_key=NULL,batch=NULL,note=? WHERE id=?", ('Geri alındı: ' + reason, sid))
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (stamp, user, 'POS Takip', 'Hesaba Geçiş Geri Alma', sid, 'Yattı', 'Bekliyor', reason))
        c.commit()
    except Exception:
        c.rollback()
        raise
    generate(c)         # brüt/komisyon yeniden
    return True


# ----------------------------------------------------------------------------------------------
# Aylık POS ücreti (komisyonsuz / blokesiz çalışan işletmeler için asıl kesinti)
# ----------------------------------------------------------------------------------------------
FEE_CATEGORY = 'POS Ücreti'


def fee_months(c, pos=''):
    ensure_schema(c)
    sql = "SELECT id,pos_name,month,amount,bank_account,dt,status FROM pos_fees"
    a = []
    if pos:
        sql += ' WHERE pos_name=?'
        a.append(pos)
    sql += ' ORDER BY month DESC, pos_name'
    return [dict(id=r[0], pos=r[1], month=r[2], amount=_f(r[3]), bank=r[4], dt=r[5], status=r[6]) for r in c.execute(sql, a).fetchall()]


def fee_status(c, month=None):
    """Aylık ücreti tanımlı her aktif POS için bu ay işlendi mi? [(pos, tutar, işlendi_mi)]"""
    ensure_schema(c)
    month = month or datetime.date.today().isoformat()[:7]
    done = {r[0] for r in c.execute("SELECT pos_name FROM pos_fees WHERE month=? AND status='Aktif'", (month,)).fetchall()}
    return [(t['name'], t['fee'], t['name'] in done) for t in terminals(c) if t['active'] and t['fee'] > 0]


def post_fee(c, pos, month, amount, bank_account, paid_date=None, user='', note='', audit=None):
    """Aylık POS ücretini banka hesabından gider olarak işler (aynı POS + ay ikinci kez işlenemez)."""
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError('Tutar sıfırdan büyük olmalı.')
    if not str(bank_account or '').strip():
        raise ValueError('Banka hesabı seçin.')
    if not (len(month) == 7 and month[4] == '-' and month[:4].isdigit() and month[5:].isdigit() and 1 <= int(month[5:]) <= 12):
        raise ValueError('Ay YYYY-AA biçiminde olmalı.')
    paid_date = paid_date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(paid_date)
    except ValueError:
        raise ValueError('Tarih geçersiz (YYYY-AA-GG).')
    ensure_schema(c)
    if c.execute("SELECT 1 FROM pos_fees WHERE pos_name=? AND month=? AND status='Aktif'", (pos, month)).fetchone():
        raise ValueError('%s için %s ayı ücreti zaten işlenmiş.' % (pos, month))
    stamp = paid_date + ' ' + datetime.datetime.now().strftime('%H:%M:%S')
    lab = '%s aylık POS ücreti (%s)' % (pos, month)
    c.execute('BEGIN IMMEDIATE')
    try:
        try:
            c.execute("INSERT OR IGNORE INTO expense_categories(name,active) VALUES(?,1)", (FEE_CATEGORY,))
        except sqlite3.Error:
            pass
        fid = c.execute("INSERT INTO finance(dt,account,kind,amount,source,note) VALUES(?,?,?,?,?,?)",
                        (stamp, bank_account, 'Gider', -amount, 'EXPENSE_PRO', FEE_CATEGORY + ' - ' + lab + ((' ' + note) if note else ''))).lastrowid
        eid = c.execute("INSERT INTO expense_records(finance_id,dt,category,account,amount,payment_type,note,user,status) VALUES(?,?,?,?,?,?,?,?,?)",
                        (fid, stamp, FEE_CATEGORY, bank_account, amount, 'Banka', lab + ((' ' + note) if note else ''), user, 'Aktif')).lastrowid
        rid = c.execute("INSERT INTO pos_fees(pos_name,month,amount,bank_account,dt,finance_id,expense_id,status,user,note) VALUES(?,?,?,?,?,?,?,'Aktif',?,?)",
                        (pos, month, amount, bank_account, stamp, fid, eid, user, note)).lastrowid
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (stamp, user, 'POS Takip', 'Aylık POS Ücreti', rid, '', 'pos=%s ay=%s tutar=%.2f banka=%s' % (pos, month, amount, bank_account), lab))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return rid


def undo_fee(c, rid, reason, user=''):
    if not str(reason or '').strip():
        raise ValueError('Geri alma nedeni yazılmalı.')
    r = c.execute("SELECT id,pos_name,month,amount,bank_account,expense_id,status FROM pos_fees WHERE id=?", (rid,)).fetchone()
    if r is None or r[6] != 'Aktif':
        raise ValueError('Bu ücret kaydı aktif değil.')
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute("UPDATE pos_fees SET status='İptal' WHERE id=?", (rid,))
        e = c.execute("SELECT id,amount,account,status FROM expense_records WHERE id=?", (r[5],)).fetchone()
        if e and e[3] == 'Aktif':
            c.execute("UPDATE expense_records SET status='İptal' WHERE id=?", (e[0],))
            c.execute("INSERT INTO finance(dt,account,kind,amount,source,note) VALUES(?,?,?,?,?,?)",
                      (stamp, e[2], 'Gider İptal', _f(e[1]), 'EXPENSE_REVERSAL', 'Gider #%s - %s' % (e[0], reason)))
        c.execute('INSERT INTO audit(dt,user,module,action,record_id,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?,?)',
                  (stamp, user, 'POS Takip', 'Aylık POS Ücreti İptali', rid, 'Aktif', 'İptal', reason))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return True


# ----------------------------------------------------------------------------------------------
# Ekran
# ----------------------------------------------------------------------------------------------
def open_dialog(parent, connection_factory, bank_accounts, user='', on_done=None, audit=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    con = connection_factory()
    ensure_schema(con)
    w = tk.Toplevel(parent)
    w.title('POS Takip')
    w.geometry('1240x760')
    w.minsize(1060, 620)
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    tk.Label(w, text='POS TAKİP — KART SATIŞLARI, KOMİSYON, BANKAYA GEÇİŞ', font=('Segoe UI', 17, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    tk.Label(w, text='Kart satışları her POS için günlük gruplanır; komisyon ve beklenen yatış günü hesaplanır. Para bankaya geçince HESABA GEÇTİ ile işleyin. Aylık POS ücreti için AYLIK POS ÜCRETİ düğmesini kullanın.',
             font=('Segoe UI', 10), fg='#475569', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(0, 6))
    cards = tk.Frame(w, bg='#f4f7fb')
    cards.pack(fill='x', padx=14)
    klabels = {}
    for i, (k, t) in enumerate((('wait', 'BANKAYA GEÇMEYİ BEKLEYEN (NET)'), ('due', 'YATMASI GELMİŞ / GEÇMİŞ'), ('fee', 'BU AY POS ÜCRETİ'))):
        f = tk.Frame(cards, bg='white', highlightbackground='#cbd5e1', highlightthickness=1)
        f.grid(row=0, column=i, sticky='nsew', padx=4, pady=2)
        cards.grid_columnconfigure(i, weight=1, uniform='k')
        tk.Label(f, text=t, font=('Segoe UI', 8, 'bold'), fg='#64748b', bg='white').pack(anchor='w', padx=10, pady=(7, 0))
        klabels[k] = tk.Label(f, text='0,00', font=('Segoe UI', 14, 'bold'), fg='#0f172a', bg='white')
        klabels[k].pack(anchor='w', padx=10, pady=(0, 7))
    fl = tk.Frame(w, bg='#f4f7fb')
    fl.pack(fill='x', padx=18, pady=(8, 2))
    v_pos = tk.StringVar(value='Tümü')
    v_st = tk.StringVar(value='Bekliyor')
    tk.Label(fl, text='POS:', bg='#f4f7fb').pack(side='left')
    cb_pos = ttk.Combobox(fl, textvariable=v_pos, state='readonly', width=24)
    cb_pos.pack(side='left', padx=(4, 12))
    tk.Label(fl, text='Durum:', bg='#f4f7fb').pack(side='left')
    ttk.Combobox(fl, textvariable=v_st, values=['Tümü', 'Bekliyor', 'Yattı'], state='readonly', width=10).pack(side='left', padx=4)
    cols = ('dt', 'pos', 'n', 'gross', 'fee', 'net', 'due', 'status', 'bank', 'paid')
    heads = (('dt', 'Satış Günü', 95), ('pos', 'POS', 190), ('n', 'İşlem', 55), ('gross', 'Brüt', 105), ('fee', 'Komisyon', 90), ('net', 'Net', 105),
             ('due', 'Beklenen Yatış', 105), ('status', 'Durum', 110), ('bank', 'Banka Hesabı', 160), ('paid', 'Yatış Tarihi', 95))
    fr = tk.Frame(w)
    fr.pack(fill='both', expand=True, padx=18, pady=6)
    t = ttk.Treeview(fr, columns=cols, show='headings', selectmode='extended')
    for c, h, wd in heads:
        t.heading(c, text=h)
        t.column(c, width=wd, anchor='e' if c in ('n', 'gross', 'fee', 'net') else 'w', stretch=(c in ('pos', 'bank')))
    sb = ttk.Scrollbar(fr, orient='vertical', command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    t.tag_configure('late', foreground='#b91c1c')
    t.tag_configure('paid', foreground='#15803d')
    t.tag_configure('neg', foreground='#b45309')
    info = tk.Label(w, text='', font=('Segoe UI', 10, 'bold'), bg='#f4f7fb', anchor='w')
    info.pack(fill='x', padx=18)
    state = {'rows': {}}

    def refresh(recalc=False):
        if recalc:
            try:
                generate(con)
            except Exception as ex:
                messagebox.showerror('POS Takip', 'Hesaplanamadı:\n%s' % ex, parent=w)
        names = ['Tümü'] + sorted({r[0] for r in con.execute('SELECT DISTINCT pos_name FROM pos_settlements').fetchall() if r[0]} | {x['name'] for x in terminals(con)})
        cb_pos['values'] = names
        if v_pos.get() not in names:
            v_pos.set('Tümü')
        t.delete(*t.get_children())
        state['rows'].clear()
        today = datetime.date.today().isoformat()
        for r in listing(con, '' if v_pos.get() == 'Tümü' else v_pos.get(), '' if v_st.get() == 'Tümü' else v_st.get()):
            tag = 'paid' if r['status'] == 'Yattı' else ('late' if r['due'] and r['due'] < today else ('neg' if _f(r['gross']) < 0 else ''))
            stt = r['status'] if r['status'] == 'Yattı' else ('GECİKTİ' if r['due'] and r['due'] < today else ('BUGÜN' if r['due'] == today else 'Bekliyor'))
            t.insert('', 'end', iid=str(r['id']), values=(tr_date(r['dt']), r['pos'], r['n'], money(r['gross']), money(r['commission']), money(r['net']),
                                                          tr_date(r['due']), stt, r['bank'] or '', tr_date(r['paid_dt'])), tags=(tag,))
            state['rows'][str(r['id'])] = r
        k = kpis(con)
        klabels['wait'].config(text='%s TL  (%d gün)' % (money(k['wait']), k['wait_n']))
        klabels['due'].config(text='%s TL  (%d gün)' % (money(k['due']), k['due_n']), fg='#b91c1c' if k['due_n'] else '#0f172a')
        fs = fee_status(con)
        miss = [x for x in fs if not x[2]]
        if miss:
            klabels['fee'].config(text='İŞLENMEDİ: ' + ', '.join('%s %s TL' % (x[0], money(x[1])) for x in miss), fg='#b45309', font=('Segoe UI', 11, 'bold'))
        elif fs:
            klabels['fee'].config(text='İşlendi ✓', fg='#15803d', font=('Segoe UI', 14, 'bold'))
        else:
            klabels['fee'].config(text=('%s TL komisyon' % money(k['fee_month'])) if k['fee_month'] else 'Tanımlı değil', fg='#64748b', font=('Segoe UI', 11, 'bold'))
        info.config(text='')

    def on_select(_=None):
        sel = [state['rows'][i] for i in t.selection() if i in state['rows'] and state['rows'][i]['status'] == 'Bekliyor']
        if sel:
            info.config(text='Seçili %d gün: brüt %s, beklenen net %s TL' % (len(sel), money(sum(x['gross'] for x in sel)), money(sum(x['net'] for x in sel))), fg='#0b376d')
    t.bind('<<TreeviewSelect>>', on_select)

    def do_settle():
        sel = [state['rows'][i] for i in t.selection() if i in state['rows'] and state['rows'][i]['status'] == 'Bekliyor']
        if not sel:
            return messagebox.showwarning('POS Takip', 'Bekleyen (Bekliyor) bir veya daha fazla satır seçin.', parent=w)
        if len({x['pos'] for x in sel}) > 1:
            return messagebox.showwarning('POS Takip', 'Aynı anda yalnız tek POS\'un günlerini seçin.', parent=w)
        if not bank_accounts:
            return messagebox.showwarning('POS Takip', 'Önce Banka Merkezi\'nde bir banka hesabı tanımlayın.', parent=w)
        d = tk.Toplevel(w)
        d.title('Hesaba Geçti')
        d.geometry('520x430')
        d.transient(w)
        d.grab_set()
        net = round(sum(x['net'] for x in sel), 2)
        gross = round(sum(x['gross'] for x in sel), 2)
        tk.Label(d, text='BANKAYA GEÇİŞİ İŞLE', font=('Segoe UI', 15, 'bold'), fg='#0b376d').pack(anchor='w', padx=18, pady=(14, 4))
        tk.Label(d, text='%s — %d gün, brüt %s TL, beklenen net %s TL' % (sel[0]['pos'], len(sel), money(gross), money(net)), wraplength=480, justify='left').pack(anchor='w', padx=18)
        vb = tk.StringVar(value=bank_accounts[0])
        vr = tk.StringVar(value=('%.2f' % net).replace('.', ','))
        vd = tk.StringVar(value=datetime.date.today().isoformat())
        vn = tk.StringVar()

        def rowf(lab, wd):
            f = tk.Frame(d)
            f.pack(fill='x', padx=18, pady=7)
            tk.Label(f, text=lab, width=22, anchor='w', font=('Segoe UI', 10, 'bold')).pack(side='left')
            wd(f).pack(side='left', fill='x', expand=True)
        rowf('Banka hesabı', lambda f: ttk.Combobox(f, textvariable=vb, values=bank_accounts, state='readonly'))
        rowf('Bankaya YATAN tutar', lambda f: tk.Entry(f, textvariable=vr, font=('Segoe UI', 12, 'bold')))
        rowf('Yatış tarihi (YYYY-AA-GG)', lambda f: tk.Entry(f, textvariable=vd))
        rowf('Not (isteğe bağlı)', lambda f: tk.Entry(f, textvariable=vn))
        lb = tk.Label(d, text='Bankanın size gönderdiği tutarı yazın. Komisyon kesilmiyorsa tutarı olduğu gibi bırakın. Beklenenden az yatmışsa fark "POS Komisyonu" gideri olarak kaydedilir.', fg='#64748b', wraplength=480, justify='left')
        lb.pack(anchor='w', padx=18, pady=6)

        def ok():
            try:
                x = vr.get().strip().replace(' ', '')
                x = x.replace('.', '').replace(',', '.') if (',' in x and x.rfind(',') > x.rfind('.')) else x.replace(',', '.')
                res = settle(con, [s['id'] for s in sel], vb.get(), float(x), vd.get().strip(), user, vn.get().strip(), audit)
            except ValueError as ex:
                return messagebox.showwarning('POS Takip', str(ex), parent=d)
            except Exception as ex:
                return messagebox.showerror('POS Takip', 'İşlenemedi, hiçbir değişiklik yapılmadı:\n%s' % ex, parent=d)
            d.destroy()
            refresh()
            info.config(text='✓ %d gün işlendi: bankaya %s TL, komisyon %s TL.' % (res['rows'], money(res['received']), money(res['fee'])), fg='#15803d')
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    _log('POS takip: tamamlandı bildirimi', e)
        tk.Button(d, text='HESABA GEÇTİ — KAYDET', command=ok, bg='#15803d', fg='white', font=('Segoe UI', 11, 'bold'), relief='flat', padx=16, pady=7).pack(pady=12)
        d._pos = dict(vars=dict(bank=vb, recv=vr, date=vd, note=vn), ok=ok)
        w._pos_dialog = d

    def do_undo():
        sel = [state['rows'][i] for i in t.selection() if i in state['rows'] and state['rows'][i]['status'] == 'Yattı']
        if len(sel) != 1:
            return messagebox.showwarning('POS Takip', 'Geri alınacak tek bir "Yattı" satırı seçin.', parent=w)
        reason = simpledialog.askstring('Geri Al', 'Geri alma nedeni:', parent=w)
        if not reason:
            return
        if not messagebox.askyesno('POS Takip', '%s %s yatışı ters kayıtla geri alınsın mı?' % (tr_date(sel[0]['dt']), sel[0]['pos']), parent=w):
            return
        try:
            undo(con, sel[0]['id'], reason, user)
        except Exception as ex:
            return messagebox.showerror('POS Takip', str(ex), parent=w)
        refresh()
        if on_done:
            try:
                on_done()
            except Exception as e:
                _log('POS takip: tamamlandı bildirimi', e)

    def do_settings():
        d = tk.Toplevel(w)
        d.title('POS Komisyon ve Valör')
        d.geometry('700x440')
        d.transient(w)
        d.grab_set()
        tk.Label(d, text='Her POS için komisyon oranı (komisyon yoksa 0), bankaya geçiş süresi (valör günü; örn. 15) ve aylık POS ücreti. Yatış günü hafta sonuna denk gelirse pazartesiye kaydırılır.', wraplength=600, justify='left').pack(anchor='w', padx=14, pady=10)
        tt = ttk.Treeview(d, columns=('name', 'bank', 'rate', 'valor', 'fee'), show='headings', height=8)
        for c, h, wd in (('name', 'POS', 170), ('bank', 'Banka', 130), ('rate', 'Komisyon %', 90), ('valor', 'Valör (gün)', 90), ('fee', 'Aylık ücret TL', 110)):
            tt.heading(c, text=h)
            tt.column(c, width=wd, anchor='w')
        tt.pack(fill='x', padx=14)

        def load():
            tt.delete(*tt.get_children())
            for x in terminals(con):
                tt.insert('', 'end', iid=str(x['id']), values=(x['name'], x['bank'], ('%g' % x['rate']).replace('.', ','), x['valor'], ('%.2f' % x['fee']).replace('.', ',')))
        load()
        vr, vv, vf = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ef = tk.Frame(d)
        ef.pack(fill='x', padx=14, pady=10)
        tk.Label(ef, text='Komisyon %').pack(side='left')
        tk.Entry(ef, textvariable=vr, width=8).pack(side='left', padx=6)
        tk.Label(ef, text='Valör gün').pack(side='left')
        tk.Entry(ef, textvariable=vv, width=6).pack(side='left', padx=6)
        tk.Label(ef, text='Aylık ücret').pack(side='left')
        tk.Entry(ef, textvariable=vf, width=9).pack(side='left', padx=6)

        def pick(_=None):
            s = tt.selection()
            if s:
                x = tt.item(s[0], 'values')
                vr.set(x[2])
                vv.set(x[3])
                vf.set(x[4])
        tt.bind('<<TreeviewSelect>>', pick)

        def save():
            s = tt.selection()
            if not s:
                return messagebox.showwarning('POS Takip', 'Bir POS seçin.', parent=d)
            try:
                set_terminal(con, int(s[0]), float(vr.get().replace(',', '.')), int(vv.get()), float(vf.get().replace('.', '').replace(',', '.') if ',' in vf.get() else (vf.get() or 0)))
            except ValueError as ex:
                return messagebox.showwarning('POS Takip', str(ex) or 'Sayıları kontrol edin.', parent=d)
            load()
            refresh(True)
        tk.Button(ef, text='KAYDET', command=save, padx=10).pack(side='left', padx=10)
        tk.Button(d, text='KAPAT', command=d.destroy, padx=12).pack(pady=8)
        d._pos = dict(tree=tt, vars=(vr, vv, vf), save=save)
        w._pos_settings = d

    def do_fee():
        terms = [x for x in terminals(con) if x['active']]
        if not terms:
            return messagebox.showwarning('POS Takip', 'Tanımlı aktif POS yok (Banka Merkezi > POS HESAPLARI).', parent=w)
        if not bank_accounts:
            return messagebox.showwarning('POS Takip', 'Önce Banka Merkezi\'nde bir banka hesabı tanımlayın.', parent=w)
        d = tk.Toplevel(w)
        d.title('Aylık POS Ücreti')
        d.geometry('600x560')
        d.transient(w)
        d.grab_set()
        tk.Label(d, text='AYLIK POS ÜCRETİ', font=('Segoe UI', 15, 'bold'), fg='#0b376d').pack(anchor='w', padx=18, pady=(14, 2))
        tk.Label(d, text='Banka POS kullanım ücretini hesabınızdan kestiğinde buradan işleyin; seçtiğiniz banka hesabından düşer ve "POS Ücreti" gideri olarak görünür.', wraplength=560, justify='left', fg='#475569').pack(anchor='w', padx=18)
        names = [x['name'] for x in terms]
        vp, vm, va, vb, vd, vn = (tk.StringVar(value=names[0]), tk.StringVar(value=datetime.date.today().isoformat()[:7]), tk.StringVar(), tk.StringVar(value=bank_accounts[0]),
                                  tk.StringVar(value=datetime.date.today().isoformat()), tk.StringVar())

        def setamt(*_):
            x = next((y for y in terms if y['name'] == vp.get()), None)
            if x and x['fee'] > 0:
                va.set(('%.2f' % x['fee']).replace('.', ','))
        setamt()

        def rowf(lab, wd):
            f = tk.Frame(d)
            f.pack(fill='x', padx=18, pady=5)
            tk.Label(f, text=lab, width=20, anchor='w', font=('Segoe UI', 10, 'bold')).pack(side='left')
            wd(f).pack(side='left', fill='x', expand=True)
        cbp = ttk.Combobox
        rowf('POS', lambda f: cbp(f, textvariable=vp, values=names, state='readonly'))
        rowf('Ay (YYYY-AA)', lambda f: tk.Entry(f, textvariable=vm))
        rowf('Tutar (TL)', lambda f: tk.Entry(f, textvariable=va, font=('Segoe UI', 12, 'bold')))
        rowf('Kesilen banka hesabı', lambda f: cbp(f, textvariable=vb, values=bank_accounts, state='readonly'))
        rowf('Tarih (YYYY-AA-GG)', lambda f: tk.Entry(f, textvariable=vd))
        rowf('Not', lambda f: tk.Entry(f, textvariable=vn))
        vp.trace_add('write', setamt)
        hist = ttk.Treeview(d, columns=('m', 'p', 'a', 'b', 's'), show='headings', height=5)
        for c, h, wd in (('m', 'Ay', 70), ('p', 'POS', 150), ('a', 'Tutar', 90), ('b', 'Banka', 150), ('s', 'Durum', 70)):
            hist.heading(c, text=h)
            hist.column(c, width=wd, anchor='w')

        def reload():
            hist.delete(*hist.get_children())
            for x in fee_months(con):
                hist.insert('', 'end', iid=str(x['id']), values=(x['month'], x['pos'], money(x['amount']), x['bank'] or '', x['status']))
        reload()

        def ok():
            try:
                x = va.get().strip().replace(' ', '')
                x = x.replace('.', '').replace(',', '.') if (',' in x and x.rfind(',') > x.rfind('.')) else x.replace(',', '.')
                post_fee(con, vp.get(), vm.get().strip(), float(x), vb.get(), vd.get().strip(), user, vn.get().strip(), audit)
            except ValueError as ex:
                return messagebox.showwarning('POS Takip', str(ex) or 'Tutarı kontrol edin.', parent=d)
            except Exception as ex:
                return messagebox.showerror('POS Takip', 'İşlenemedi, hiçbir değişiklik yapılmadı:\n%s' % ex, parent=d)
            reload()
            refresh()
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    _log('POS takip: tamamlandı bildirimi', e)

        def cancel():
            s = hist.selection()
            if not s:
                return messagebox.showwarning('POS Takip', 'İptal edilecek kaydı listeden seçin.', parent=d)
            reason = simpledialog.askstring('Aylık Ücret İptali', 'İptal nedeni:', parent=d)
            if not reason:
                return
            try:
                undo_fee(con, int(s[0]), reason, user)
            except ValueError as ex:
                return messagebox.showwarning('POS Takip', str(ex), parent=d)
            reload()
            refresh()
        bf = tk.Frame(d)
        bf.pack(fill='x', padx=18, pady=8)
        tk.Button(bf, text='ÜCRETİ İŞLE', command=ok, bg='#15803d', fg='white', font=('Segoe UI', 11, 'bold'), relief='flat', padx=14, pady=5).pack(side='left')
        tk.Button(bf, text='SEÇİLİ KAYDI İPTAL ET', command=cancel, padx=10, pady=5).pack(side='left', padx=8)
        tk.Button(bf, text='KAPAT', command=d.destroy, padx=10, pady=5).pack(side='right')
        hist.pack(fill='both', expand=True, padx=18, pady=(0, 10))
        d._pos = dict(vars=dict(pos=vp, month=vm, amount=va, bank=vb, date=vd, note=vn), ok=ok, cancel=cancel, tree=hist)
        w._pos_fee = d
    bar = tk.Frame(w, bg='#f4f7fb')
    bar.pack(fill='x', padx=18, pady=10)
    tk.Button(bar, text='HESAPLA / YENİLE', command=lambda: refresh(True), font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left')
    tk.Button(bar, text='✔ HESABA GEÇTİ', command=do_settle, font=('Segoe UI', 11, 'bold'), bg='#15803d', fg='white', relief='flat', padx=16, pady=6).pack(side='left', padx=8)
    tk.Button(bar, text='GERİ AL', command=do_undo, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left')
    tk.Button(bar, text='AYLIK POS ÜCRETİ', command=do_fee, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left', padx=(8, 0))
    tk.Button(bar, text='POS AYARI (VALÖR / ÜCRET)', command=do_settings, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='left', padx=8)
    tk.Button(bar, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=6).pack(side='right')
    v_pos.trace_add('write', lambda *_: refresh())
    v_st.trace_add('write', lambda *_: refresh())
    refresh(True)
    w._pos = dict(tree=t, state=state, refresh=refresh, settle=do_settle, undo=do_undo, settings=do_settings, fee=do_fee, info=info, klabels=klabels)
    return w


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
