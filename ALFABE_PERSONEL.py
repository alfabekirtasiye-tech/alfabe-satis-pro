# -*- coding: utf-8 -*-
"""ALFABE personel takibi: maaş / yevmiye / avans / prim / kesinti hareketleri ve personel satış kârı.
Yalnız kendi tablolarına (staff, staff_moves) yazar; ödeme istenirse kasa hareketi (finance) ekler.
Satış ve stok verisi okunur, değiştirilmez."""
import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, title TEXT DEFAULT '', phone TEXT DEFAULT '',
 pay_type TEXT DEFAULT 'Maaş', pay_amount REAL DEFAULT 0, start_date TEXT DEFAULT '',
 login_user TEXT DEFAULT '', active INTEGER DEFAULT 1, note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS staff_moves(
 id INTEGER PRIMARY KEY, staff_id INTEGER NOT NULL, dt TEXT, kind TEXT NOT NULL, amount REAL NOT NULL,
 period TEXT DEFAULT '', note TEXT DEFAULT '', finance_id INTEGER, user TEXT DEFAULT '');
"""

PAY_TYPES = ('Maaş', 'Yevmiye')
# +: personele borçlanılan tutar (hakediş), -: personele yapılan ödeme / düşülen tutar
KINDS_PLUS = ('Maaş Hakediş', 'Yevmiye', 'Prim')
KINDS_MINUS = ('Avans', 'Maaş Ödemesi', 'Kesinti')
KINDS = KINDS_PLUS + KINDS_MINUS
KINDS_CASH = ('Avans', 'Maaş Ödemesi')   # kasadan para çıkışı oluşturabilen türler


def ensure_schema(c):
    c.executescript(SCHEMA)
    c.commit()


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def add_staff(c, name, title='', phone='', pay_type='Maaş', pay_amount=0.0, start_date='', login_user='', note=''):
    ensure_schema(c)
    name = ' '.join(str(name or '').split())
    if not name:
        raise ValueError('Personel adı boş olamaz.')
    if pay_type not in PAY_TYPES:
        raise ValueError('Ücret türü Maaş veya Yevmiye olmalıdır.')
    amt = float(pay_amount or 0)
    if amt < 0:
        raise ValueError('Ücret negatif olamaz.')
    if c.execute('SELECT 1 FROM staff WHERE name=?', (name,)).fetchone():
        raise ValueError('Bu adla bir personel zaten kayıtlı.')
    cur = c.execute('INSERT INTO staff(name,title,phone,pay_type,pay_amount,start_date,login_user,active,note,created) VALUES(?,?,?,?,?,?,?,1,?,?)',
                    (name, title, phone, pay_type, amt, start_date, login_user, note, _now()))
    c.commit()
    return cur.lastrowid


def add_move(c, staff_id, kind, amount, note='', period='', user='', pay_from_cash=False, dt=None):
    """Personel hareketi ekler. pay_from_cash=True ve tür Avans/Maaş Ödemesi ise kasadan çıkış (finance) da yazılır."""
    ensure_schema(c)
    if kind not in KINDS:
        raise ValueError('Geçersiz hareket türü.')
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError('Tutar sıfırdan büyük olmalıdır.')
    st = c.execute('SELECT id,name,active FROM staff WHERE id=?', (staff_id,)).fetchone()
    if st is None:
        raise ValueError('Personel bulunamadı.')
    dt = dt or _now()
    fid = None
    try:
        c.execute('BEGIN IMMEDIATE') if not c.in_transaction else None
        if pay_from_cash and kind in KINDS_CASH:
            cur = c.execute('INSERT INTO finance(dt,account,kind,amount,source,source_id,note) VALUES(?,?,?,?,?,?,?)',
                            (dt, 'Kasa', 'Personel ' + kind, -amount, 'STAFF', staff_id, '%s - %s' % (st['name'], note)))
            fid = cur.lastrowid
        cur = c.execute('INSERT INTO staff_moves(staff_id,dt,kind,amount,period,note,finance_id,user) VALUES(?,?,?,?,?,?,?,?)',
                        (staff_id, dt, kind, amount, period, note, fid, user))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return cur.lastrowid


def delete_move(c, move_id):
    """Hareketi siler; bağlı kasa hareketi varsa onu da geri alır."""
    m = c.execute('SELECT * FROM staff_moves WHERE id=?', (move_id,)).fetchone()
    if m is None:
        raise ValueError('Hareket bulunamadı.')
    try:
        c.execute('BEGIN IMMEDIATE') if not c.in_transaction else None
        if m['finance_id']:
            c.execute('DELETE FROM finance WHERE id=? AND source=?', (m['finance_id'], 'STAFF'))
        c.execute('DELETE FROM staff_moves WHERE id=?', (move_id,))
        c.commit()
    except Exception:
        c.rollback()
        raise


def balance_rows(c, only_active=True):
    """Her personel için: hakediş, avans, ödeme, kesinti, kalan (personele borç; negatif = personel borçlu)."""
    ensure_schema(c)
    out = []
    q = 'SELECT * FROM staff' + (' WHERE active=1' if only_active else '') + ' ORDER BY name'
    for s in c.execute(q).fetchall():
        tot = {k: 0.0 for k in KINDS}
        for r in c.execute('SELECT kind,SUM(amount) a FROM staff_moves WHERE staff_id=? GROUP BY kind', (s['id'],)).fetchall():
            if r['kind'] in tot:
                tot[r['kind']] = float(r['a'] or 0)
        hak = sum(tot[k] for k in KINDS_PLUS)
        dus = sum(tot[k] for k in KINDS_MINUS)
        out.append({'id': s['id'], 'name': s['name'], 'title': s['title'], 'pay_type': s['pay_type'], 'pay_amount': float(s['pay_amount'] or 0),
                    'hakedis': round(hak, 2), 'avans': round(tot['Avans'], 2), 'odeme': round(tot['Maaş Ödemesi'], 2),
                    'kesinti': round(tot['Kesinti'], 2), 'kalan': round(hak - dus, 2), 'active': int(s['active'] or 0)})
    return out


def accrue_monthly(c, period, user=''):
    """Aktif 'Maaş' türündeki personel için o ay (YYYY-AA) hakedişini bir kez yazar. Yazılanların sayısını döndürür."""
    ensure_schema(c)
    n = 0
    for s in c.execute("SELECT * FROM staff WHERE active=1 AND pay_type='Maaş' AND pay_amount>0").fetchall():
        if c.execute("SELECT 1 FROM staff_moves WHERE staff_id=? AND kind='Maaş Hakediş' AND period=?", (s['id'], period)).fetchone():
            continue
        add_move(c, s['id'], 'Maaş Hakediş', s['pay_amount'], 'Aylık maaş hakedişi', period, user)
        n += 1
    return n


def sales_profit(c, d1, d2):
    """Tarih aralığında (YYYY-AA-GG) satış yapan kullanıcı bazında ciro, maliyet, kâr. İadeler (negatif satırlar) düşer.
    Personel kaydında login_user eşleşirse personel adı gösterilir."""
    ensure_schema(c)
    cost = "sl.qty*CASE WHEN COALESCE(sl.cost_snapshot,0)>0 THEN sl.cost_snapshot ELSE COALESCE(NULLIF(st.avg_cost,0),st.last_cost,0) END"
    rows = c.execute("""SELECT COALESCE(NULLIF(sa.user,''),'(Bilinmiyor)') usr, COUNT(DISTINCT CASE WHEN sa.status<>'RETURN' THEN sa.id END) n,
                        SUM(sl.total) ciro, SUM(%s) maliyet
                        FROM sale_lines sl JOIN sales sa ON sa.id=sl.sale_id LEFT JOIN stock st ON st.id=sl.stock_id
                        WHERE sa.status IN ('FINAL','PARTIAL_RETURN','RETURN') AND substr(sa.dt,1,10) BETWEEN ? AND ?
                        GROUP BY 1 ORDER BY 3 DESC""" % cost, (d1, d2)).fetchall()
    names = {}
    for s in c.execute("SELECT name,login_user FROM staff WHERE COALESCE(login_user,'')<>''").fetchall():
        names[s['login_user'].strip().casefold()] = s['name']
    out = []
    for r in rows:
        ciro, mal = float(r['ciro'] or 0), float(r['maliyet'] or 0)
        out.append({'user': r['usr'], 'staff': names.get(str(r['usr']).strip().casefold(), ''), 'fis': int(r['n'] or 0),
                    'ciro': round(ciro, 2), 'maliyet': round(mal, 2), 'kar': round(ciro - mal, 2)})
    return out
