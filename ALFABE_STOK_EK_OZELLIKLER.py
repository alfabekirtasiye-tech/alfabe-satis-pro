# -*- coding: utf-8 -*-
"""ALFABE stok ek özellikleri. Mevcut stok, hareket ve fiyat tablolarını değiştirmez."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import sqlite3, shutil, hashlib, os, subprocess, tempfile

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_variant_links(
 id INTEGER PRIMARY KEY, family_stock_id INTEGER NOT NULL, variant_stock_id INTEGER NOT NULL UNIQUE,
 description TEXT NOT NULL DEFAULT '', UNIQUE(family_stock_id,variant_stock_id),
 CHECK(family_stock_id<>variant_stock_id));
CREATE TABLE IF NOT EXISTS stock_unit_conversions(
 id INTEGER PRIMARY KEY,stock_id INTEGER NOT NULL,unit_name TEXT NOT NULL,
 base_quantity REAL NOT NULL CHECK(base_quantity>0),UNIQUE(stock_id,unit_name));
CREATE TABLE IF NOT EXISTS stock_alternatives(
 id INTEGER PRIMARY KEY,stock_id INTEGER NOT NULL,alternative_stock_id INTEGER NOT NULL,
 note TEXT NOT NULL DEFAULT '',UNIQUE(stock_id,alternative_stock_id),
 CHECK(stock_id<>alternative_stock_id));
CREATE TABLE IF NOT EXISTS stock_images(
 id INTEGER PRIMARY KEY,stock_id INTEGER NOT NULL,image_file TEXT NOT NULL,
 title TEXT NOT NULL DEFAULT '',UNIQUE(stock_id,image_file));
CREATE TABLE IF NOT EXISTS stock_components(
 id INTEGER PRIMARY KEY,set_id INTEGER NOT NULL,component_id INTEGER NOT NULL,qty REAL NOT NULL CHECK(qty>0),UNIQUE(set_id,component_id),CHECK(set_id<>component_id));
CREATE TABLE IF NOT EXISTS stock_book_info(
 stock_id INTEGER PRIMARY KEY,author TEXT NOT NULL DEFAULT '',publisher TEXT NOT NULL DEFAULT '',isbn TEXT NOT NULL DEFAULT '');
"""

def _gen_ean13(c, used=()):
    """290 önekli, benzersiz işletme içi EAN-13 (ana programdaki üretimle aynı kural)."""
    import random
    for _ in range(5000):
        base='290'+''.join(str(random.randint(0,9)) for _ in range(9))
        code=base+_isbn13_check(base)
        if code in used:continue
        if not c.execute("SELECT 1 FROM stock WHERE barcode=? UNION ALL SELECT 1 FROM stock_barcodes WHERE barcode=? AND active=1 LIMIT 1",(code,code)).fetchone():
            return code
    raise RuntimeError('Benzersiz otomatik barkod üretilemedi.')

def _next_numeric_code(c):
    mx=0
    sqls=["SELECT code FROM stock WHERE trim(COALESCE(code,''))<>''","SELECT code FROM deleted_stock_codes WHERE trim(COALESCE(code,''))<>''"]
    for q in sqls:
        try:
            for r in c.execute(q).fetchall():
                x=str(r[0] or '').strip()
                if x.isdigit():mx=max(mx,int(x))
        except Exception:pass
    return mx

_VARIANT_COPY=('brand','model','grp','subgrp','unit','vat','warehouse','section','shelf','min_qty','max_qty','last_cost','avg_cost','retail','wholesale','special_price')

def create_variants(c, base_id, values1, values2=(), now_text=''):
    """Ana ürün kartından renk/beden gibi özellik kombinasyonları için stok kartları üretir ve çeşit olarak bağlar.
    Her kart ana kartın fiyat/KDV/grup bilgilerini kopyalar, stok 0 başlar. Aynı açıklamalı çeşit varsa atlanır.
    [(stok_id, kod, ad, açıklama)] döndürür. Hata olursa hiçbir kart oluşmaz."""
    import datetime
    c.executescript(SCHEMA)
    base=c.execute('SELECT * FROM stock WHERE id=?',(base_id,)).fetchone()
    if base is None:raise ValueError('Ana ürün bulunamadı.')
    v1=[x.strip() for x in values1 if x and x.strip()]
    v2=[x.strip() for x in values2 if x and x.strip()]
    if not v1:raise ValueError('En az bir özellik değeri girin (örn. renk).')
    if len({x.casefold() for x in v1})!=len(v1) or len({x.casefold() for x in v2})!=len(v2):raise ValueError('Aynı değer birden fazla girilmiş.')
    combos=[(a,b) for a in v1 for b in (v2 or [''])]
    if len(combos)>200:raise ValueError('Tek seferde en fazla 200 çeşit oluşturulabilir.')
    now_text=now_text or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exist={str(r[0]).casefold() for r in c.execute('SELECT description FROM stock_variant_links WHERE family_stock_id=?',(base_id,)).fetchall()}
    out=[];used=set();code_no=_next_numeric_code(c)
    try:
        c.execute('BEGIN IMMEDIATE') if not c.in_transaction else None
        for a,b in combos:
            desc=(a+' / '+b) if b else a
            if desc.casefold() in exist:continue
            code_no+=1;code=str(code_no)
            bar=_gen_ean13(c,used);used.add(bar)
            vals=[base[k] for k in _VARIANT_COPY]
            name=str(base['name'])+' - '+desc
            cur=c.execute('INSERT INTO stock(barcode,code,name,'+','.join(_VARIANT_COPY)+',qty,active,created,updated) VALUES(?,?,?,'+','.join('?'*len(_VARIANT_COPY))+',0,1,?,?)',
                          (bar,code,name,*vals,now_text,now_text))
            sid=cur.lastrowid
            c.execute('INSERT INTO stock_variant_links(family_stock_id,variant_stock_id,description) VALUES(?,?,?)',(base_id,sid,desc))
            out.append((sid,code,name,desc))
        c.commit()
    except Exception:
        c.rollback();raise
    return out

def variant_summary(c, family_id, d1, d2):
    """Ana kart + çeşitler: [(açıklama, stok, satılan adet, ciro)] ve en sonda 'TOPLAM'. Satış iadeleri düşer."""
    rows=[];ids=[(family_id,'(Ana ürün)')]
    for r in c.execute('SELECT variant_stock_id,description FROM stock_variant_links WHERE family_stock_id=? ORDER BY description',(family_id,)).fetchall():
        ids.append((r[0],r[1]))
    tq=tt=0.0
    for sid,desc in ids:
        st=c.execute('SELECT qty FROM stock WHERE id=?',(sid,)).fetchone()
        sl=c.execute("""SELECT COALESCE(SUM(sl.qty),0),COALESCE(SUM(sl.total),0) FROM sale_lines sl JOIN sales sa ON sa.id=sl.sale_id
                        WHERE sl.stock_id=? AND sa.status IN ('FINAL','PARTIAL_RETURN','RETURN') AND substr(sa.dt,1,10) BETWEEN ? AND ?""",(sid,d1,d2)).fetchone()
        q=float(st[0] or 0) if st else 0.0
        rows.append((desc,q,float(sl[0] or 0),float(sl[1] or 0)))
    tot=('TOPLAM',sum(r[1] for r in rows),sum(r[2] for r in rows),sum(r[3] for r in rows))
    return rows+[tot]

def set_available(c, set_id):
    """Set/paket için satılabilir adet = en kısıtlı bileşene göre tam set sayısı; bileşen yoksa None."""
    rows=c.execute('SELECT sc.qty need,s.qty have FROM stock_components sc JOIN stock s ON s.id=sc.component_id WHERE sc.set_id=?',(set_id,)).fetchall()
    if not rows:return None
    return min(int(max(float(r[1] or 0),0)//float(r[0])) for r in rows)

def set_cost(c, set_id):
    r=c.execute('SELECT COALESCE(SUM(sc.qty*s.avg_cost),0) FROM stock_components sc JOIN stock s ON s.id=sc.component_id WHERE sc.set_id=?',(set_id,)).fetchone()
    return float(r[0] or 0)

def add_component(c, set_id, comp_id, qty):
    c.executescript(SCHEMA)
    qty=float(qty)
    if qty<=0:raise ValueError('Miktar sıfırdan büyük olmalı.')
    if set_id==comp_id:raise ValueError('Ürün kendi bileşeni olamaz.')
    if c.execute('SELECT 1 FROM stock_components WHERE set_id=?',(comp_id,)).fetchone():raise ValueError('Seçilen ürün kendisi bir set; iç içe set desteklenmez.')
    if c.execute('SELECT 1 FROM stock_components WHERE component_id=?',(set_id,)).fetchone():raise ValueError('Bu ürün başka bir setin bileşeni; set yapılamaz.')
    c.execute('INSERT INTO stock_components(set_id,component_id,qty) VALUES(?,?,?) ON CONFLICT(set_id,component_id) DO UPDATE SET qty=excluded.qty',(set_id,comp_id,qty))
    c.execute('UPDATE stock SET avg_cost=?,last_cost=? WHERE id=?',(set_cost(c,set_id),set_cost(c,set_id),set_id))
    c.commit()

def isbn_clean(v):
    return ''.join(ch for ch in str(v or '').upper() if ch.isdigit() or ch=='X')

def _isbn13_check(first12):
    return str((10-sum(int(ch)*(1 if i%2==0 else 3) for i,ch in enumerate(first12))%10)%10)

def isbn13_ok(v):
    d=isbn_clean(v)
    return len(d)==13 and d.isdigit() and _isbn13_check(d[:12])==d[12]

def isbn10_ok(v):
    d=isbn_clean(v)
    if len(d)!=10 or not d[:9].isdigit() or not (d[9].isdigit() or d[9]=='X'):return False
    tot=sum((10-i)*int(ch) for i,ch in enumerate(d[:9]))+(10 if d[9]=='X' else int(d[9]))
    return tot%11==0

def isbn_to13(v):
    """Geçerli ISBN-10/ISBN-13 değerini 13 haneli ISBN'e çevirir; geçersizse ''."""
    d=isbn_clean(v)
    if isbn13_ok(d):return d
    if isbn10_ok(d):
        b='978'+d[:9]
        return b+_isbn13_check(b)
    return ''

# ---------------------------------------------------------------------------------------------
# Stok kartı içinde ürün görseli gösterimi (v1.2.291.245)
# ---------------------------------------------------------------------------------------------
_PS_THUMB = r"""
param([string]$Src,[string]$Dst,[int]$MW,[int]$MH)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$img=[System.Drawing.Image]::FromFile($Src)
try{
 $r=[math]::Min($MW/$img.Width,$MH/$img.Height)
 $w=[math]::Max(1,[int]($img.Width*$r)); $h=[math]::Max(1,[int]($img.Height*$r))
 $bmp=New-Object System.Drawing.Bitmap($w,$h)
 $g=[System.Drawing.Graphics]::FromImage($bmp)
 $g.Clear([System.Drawing.Color]::White)
 $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
 $g.DrawImage($img,0,0,$w,$h)
 $bmp.Save($Dst,[System.Drawing.Imaging.ImageFormat]::Png)
 $g.Dispose();$bmp.Dispose()
}finally{$img.Dispose()}
"""

def _image_dir(db_path):
    d=Path(db_path).parent/'stok_gorselleri'
    d.mkdir(parents=True,exist_ok=True)
    return d

def _list_images(connection_factory, stock_id):
    c=connection_factory()
    try:
        c.executescript(SCHEMA);c.commit()
        return c.execute('SELECT id,title,image_file FROM stock_images WHERE stock_id=? ORDER BY id',(stock_id,)).fetchall()
    finally:c.close()

def _edge_path():
    for base in (os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('PROGRAMFILES',''),os.environ.get('LOCALAPPDATA','')):
        if base:
            c=Path(base)/'Microsoft/Edge/Application/msedge.exe'
            if c.exists():return str(c)
    for base in (os.environ.get('PROGRAMFILES',''),os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('LOCALAPPDATA','')):
        if base:
            c=Path(base)/'Google/Chrome/Application/chrome.exe'
            if c.exists():return str(c)
    return None

def _thumbnail(path, max_w, max_h):
    """Görseli Tk'nin gösterebildiği, kutuya sığan bir PNG önizlemeye çevirir (jpg/webp dahil). Önbelleğe alınır."""
    path=Path(path)
    tdir=path.parent/'_onizleme'
    try:tdir.mkdir(exist_ok=True)
    except Exception:tdir=Path(tempfile.gettempdir())
    out=tdir/f'{path.stem}_{max_w}x{max_h}.png'
    try:
        if out.exists() and out.stat().st_size>200 and out.stat().st_mtime>=path.stat().st_mtime:return out
    except Exception:pass
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    if os.name=='nt':
        try:
            ps=Path(tempfile.gettempdir())/'alf_gorsel_kucult.ps1';ps.write_text(_PS_THUMB,encoding='utf-8-sig')
            subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ps),'-Src',str(path),'-Dst',str(out),'-MW',str(max_w),'-MH',str(max_h)],
                           capture_output=True,timeout=40,creationflags=flags)
            if out.exists() and out.stat().st_size>200:return out
        except Exception:pass
        try:  # GDI+ açamazsa (ör. webp) Edge ile çiz
            edge=_edge_path()
            if edge:
                html=Path(tempfile.gettempdir())/'alf_gorsel_edge.html'
                html.write_text('<html><body style="margin:0;background:#fff"><img src="'+path.resolve().as_uri()+'" style="display:block;margin:auto;max-width:'+str(max_w)+'px;max-height:'+str(max_h)+'px"></body></html>',encoding='utf-8')
                prof=Path(tempfile.gettempdir())/'alfabe_edge_gorsel';prof.mkdir(exist_ok=True)
                subprocess.run([edge,'--headless','--disable-gpu','--hide-scrollbars','--no-first-run','--user-data-dir='+str(prof),
                                '--default-background-color=FFFFFFFF',f'--window-size={max_w},{max_h}','--screenshot='+str(out),html.as_uri()],
                               capture_output=True,timeout=40,creationflags=flags)
                if out.exists() and out.stat().st_size>200:return out
        except Exception:pass
    if path.suffix.lower() in ('.png','.gif'):return path
    return None

def add_image_dialog(parent, connection_factory, stock_id, db_path):
    """Dosya seçtirir, görseli stok_gorselleri klasörüne kopyalar ve stoğa bağlar. Eklendiyse True döner."""
    src=filedialog.askopenfilename(parent=parent,title='Ürün görseli seç',filetypes=[('Görsel','*.png *.jpg *.jpeg *.webp *.gif')])
    if not src:return False
    try:
        src=Path(src)
        if src.stat().st_size>10*1024*1024:raise ValueError('Görsel 10 MB üzerinde olamaz.')
        if src.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif'):raise ValueError('Desteklenmeyen dosya türü.')
        digest=hashlib.sha256(src.read_bytes()).hexdigest()[:24]
        name=f'{stock_id}_{digest}{src.suffix.lower()}';dst=_image_dir(db_path)/name
        if not dst.exists():shutil.copyfile(src,dst)
        c=connection_factory()
        try:
            c.executescript(SCHEMA);c.commit()
            c.execute('BEGIN IMMEDIATE');c.execute('INSERT OR IGNORE INTO stock_images(stock_id,image_file,title) VALUES(?,?,?)',(stock_id,name,src.stem));c.commit()
        except Exception:
            c.rollback();raise
        finally:c.close()
        return True
    except Exception as ex:
        messagebox.showerror('İşlem başarısız',str(ex),parent=parent);return False

# ---------------------------------------------------------------------------------------------
# Stok Merkezi listesi için küçük resimler (v1.2.291.246)
# ---------------------------------------------------------------------------------------------
_PS_LIST_THUMB = r"""
param([string]$Manifest,[int]$S)
Add-Type -AssemblyName System.Drawing
Get-Content -LiteralPath $Manifest -Encoding UTF8 | ForEach-Object {
 if($_.Trim() -eq ''){return}
 $a=$_ -split "`t"
 try{
  $img=[System.Drawing.Image]::FromFile($a[0])
  $r=[math]::Min($S/$img.Width,$S/$img.Height)
  $w=[math]::Max(1,[int]($img.Width*$r));$h=[math]::Max(1,[int]($img.Height*$r))
  $bmp=New-Object System.Drawing.Bitmap($S,$S)
  $g=[System.Drawing.Graphics]::FromImage($bmp);$g.Clear([System.Drawing.Color]::White)
  $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.DrawImage($img,[int](($S-$w)/2),[int](($S-$h)/2),$w,$h)
  $bmp.Save($a[1],[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose();$bmp.Dispose();$img.Dispose()
 }catch{}
}
"""

def primary_images(connection_factory):
    """{stok_id: görsel_dosyası} - her stoğun ilk görseli (tek sorgu)."""
    c=connection_factory()
    try:
        c.executescript(SCHEMA);c.commit()
        return {r[0]:r[1] for r in c.execute('SELECT stock_id,image_file FROM stock_images WHERE id IN (SELECT MIN(id) FROM stock_images GROUP BY stock_id)')}
    finally:c.close()

def list_thumb_file(db_path, image_file, size=28):
    return Path(db_path).parent/'stok_gorselleri'/'_liste'/f'{Path(image_file).stem}_{size}.png'

def ensure_list_thumbs(pairs, size=28):
    """pairs: [(kaynak_görsel, hedef_png)] - eksik küçük resimleri tek PowerShell çağrısıyla üretir."""
    pairs=[(Path(a),Path(b)) for a,b in pairs if Path(a).is_file()]
    if not pairs or os.name!='nt':return
    for _,dst in pairs:dst.parent.mkdir(parents=True,exist_ok=True)
    man=Path(tempfile.gettempdir())/'alf_liste_kucuk.txt'
    man.write_text('\n'.join(f'{a}\t{b}' for a,b in pairs),encoding='utf-8')
    ps=Path(tempfile.gettempdir())/'alf_liste_kucuk.ps1';ps.write_text(_PS_LIST_THUMB,encoding='utf-8-sig')
    subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ps),'-Manifest',str(man),'-S',str(size)],
                   capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def stock_image_panel(parent, connection_factory, stock_id, db_path, bg='#ffffff', border='#c9d5e3', on_change=None, **grid):
    """Stok kartında ürün görselini gösteren kutu. Dönen fonksiyon çağrılınca görsel yenilenir."""
    MW,MH=360,200
    box=tk.Frame(parent,bg=bg,highlightbackground=border,highlightthickness=1)
    box.grid(sticky='nsew',padx=(0,34),pady=(6,14),**grid)
    tk.Label(box,text='ÜRÜN GÖRSELİ',bg=bg,fg='#102a4c',font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(8,2))
    holder=tk.Label(box,bg='#f4f7fb',fg='#6b7f95',font=('Segoe UI',10),justify='center')
    holder.pack(fill='both',expand=True,padx=12,pady=4)
    nav=tk.Frame(box,bg=bg);nav.pack(fill='x',padx=12,pady=(0,10))
    st={'i':0,'photo':None,'items':[]}
    info=tk.Label(nav,bg=bg,fg='#55708f',font=('Segoe UI',9),anchor='w')
    def show():
        items=st['items']
        if not stock_id:
            holder.config(image='',text='Görsel eklemek için\nönce stok kartını kaydedin.');st['photo']=None;info.config(text='');return
        if not items:
            holder.config(image='',text='Bu ürüne henüz görsel eklenmedi.\n"GÖRSEL EKLE" ile ekleyebilirsiniz.');st['photo']=None;info.config(text='');return
        st['i']=max(0,min(st['i'],len(items)-1));it=items[st['i']]
        path=_image_dir(db_path)/it['image_file']
        photo=None
        if path.is_file():
            try:
                holder.config(text='Görsel hazırlanıyor...');holder.update_idletasks()
                th=_thumbnail(path,MW,MH)
                if th:
                    photo=tk.PhotoImage(file=str(th))
                    f=max(1,-(-photo.width()//MW),-(-photo.height()//MH))
                    if f>1:photo=photo.subsample(f,f)
            except Exception:photo=None
        if photo:holder.config(image=photo,text='');st['photo']=photo
        else:holder.config(image='',text='Görsel gösterilemedi:\n'+str(it['title'] or it['image_file']));st['photo']=None
        info.config(text=f"{st['i']+1}/{len(items)}  {it['title']}")
    def refresh(goto_last=False):
        try:
            st['items']=_list_images(connection_factory,stock_id) if stock_id else []
            if goto_last and st['items']:st['i']=len(st['items'])-1
            show()
        except Exception:pass
    def step(d):
        if st['items']:st['i']=(st['i']+d)%len(st['items']);show()
    def add():
        if not stock_id:return
        if add_image_dialog(box.winfo_toplevel(),connection_factory,stock_id,db_path):
            refresh(True)
            if on_change:
                try:on_change()
                except Exception:pass
    def open_big():
        try:
            it=st['items'][st['i']];path=_image_dir(db_path)/it['image_file']
            if path.is_file() and os.name=='nt':os.startfile(str(path))
        except Exception:pass
    tk.Button(nav,text='◀',command=lambda:step(-1),width=3).pack(side='left')
    tk.Button(nav,text='▶',command=lambda:step(1),width=3).pack(side='left',padx=(4,8))
    info.pack(side='left',fill='x',expand=True)
    if stock_id:
        tk.Button(nav,text='GÖRSEL EKLE',command=add,font=('Segoe UI',9,'bold')).pack(side='right')
        tk.Button(nav,text='BÜYÜT',command=open_big,font=('Segoe UI',9)).pack(side='right',padx=6)
    refresh()
    return refresh

def open_stock_extras(owner, connection_factory, stock_id, db_path, on_change=None):
    db_path=Path(db_path)
    c=connection_factory()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:c.close()
    c=connection_factory()
    item=c.execute('SELECT id,barcode,name FROM stock WHERE id=?',(stock_id,)).fetchone()
    c.close()
    if not item:
        messagebox.showerror('Stok','Stok kartı bulunamadı.',parent=owner);return
    w=tk.Toplevel(owner);w.title('Stok Kartı Ek Özellikler – '+str(item['name']));w.geometry('900x610');w.minsize(720,480)
    try:
        w.transient(owner);w.lift();w.grab_set();w.focus_force()
    except Exception:pass
    def notify():
        if on_change:
            try:on_change()
            except Exception:pass
    def kapat():
        try:w.grab_release()
        except Exception:pass
        try:w.destroy()
        except Exception:pass
        try:
            if owner is not None and owner.winfo_exists():owner.grab_set();owner.focus_force()
        except Exception:pass
        notify()
    w.protocol('WM_DELETE_WINDOW',kapat)
    ttk.Label(w,text='STOK KARTI EK ÖZELLİKLERİ  |  '+str(item['name']),font=('Segoe UI',14,'bold')).pack(anchor='w',padx=14,pady=(14,6))
    ttk.Label(w,text='Mevcut stok miktarı, barkod, fiyat ve satış kayıtları değiştirilmez.',foreground='#526477').pack(anchor='w',padx=14)
    book=ttk.Notebook(w);book.pack(fill='both',expand=True,padx=14,pady=12)
    def sql(query,args=(),fetch=False):
        con=connection_factory()
        try:
            if fetch:return con.execute(query,args).fetchall()
            con.execute('BEGIN IMMEDIATE');con.execute(query,args);con.commit()
        except Exception:
            con.rollback();raise
        finally:con.close()
    def error(ex):messagebox.showerror('İşlem başarısız',str(ex),parent=w)
    def lookup(value):
        value=value.strip()
        if not value:raise ValueError('Barkod veya stok kodu girin.')
        rows=sql("""SELECT DISTINCT s.id,s.name FROM stock s LEFT JOIN stock_barcodes b ON b.stock_id=s.id AND b.active=1
            WHERE s.active=1 AND (s.barcode=? OR s.code=? OR b.barcode=?)""",(value,value,value),True)
        if len(rows)!=1:raise ValueError('Tek bir aktif ürün bulunamadı. Barkod/stok kodunu kontrol edin.')
        if rows[0]['id']==stock_id:raise ValueError('Ürün kendisiyle ilişkilendirilemez.')
        return rows[0]
    def tab(name,heads):
        frame=ttk.Frame(book,padding=12);book.add(frame,text=name)
        tree=ttk.Treeview(frame,columns=tuple(range(len(heads))),show='headings',height=12)
        for i,head in enumerate(heads):tree.heading(i,text=head);tree.column(i,width=170 if i==0 else 240,anchor='w')
        tree.pack(fill='both',expand=True,pady=(0,10));return frame,tree
    def buttons(frame,actions):
        line=ttk.Frame(frame);line.pack(fill='x')
        for label,fn in actions:ttk.Button(line,text=label,command=fn).pack(side='left',padx=(0,8))
    def selected_id(tree):
        sel=tree.selection()
        if not sel:raise ValueError('Önce listeden kayıt seçin.')
        return int(sel[0])
    def redraw(tree,query,args=()):
        tree.delete(*tree.get_children())
        for row in sql(query,args,True):tree.insert('','end',iid=str(row[0]),values=tuple(row)[1:])
    # Çeşitler: var olan bağımsız stok kartlarını bir aileye bağlar.
    frame,t=tab('Ürün Çeşitleri',['Stok Kodu','Çeşit Ürün','Açıklama'])
    e=ttk.Entry(frame);e.pack(fill='x');e.insert(0,'Çeşidin barkodunu veya stok kodunu buraya yazın')
    note=ttk.Entry(frame);note.pack(fill='x',pady=5)
    def refresh_variants():redraw(t,"""SELECT v.id,s.code,s.name,v.description FROM stock_variant_links v JOIN stock s ON s.id=v.variant_stock_id WHERE v.family_stock_id=? ORDER BY s.name""",(stock_id,))
    def matrix_dialog():
        d=tk.Toplevel(w);d.title('Çeşit Matrisi Oluştur');d.geometry('520x330');d.transient(w);d.grab_set()
        ttk.Label(d,text='Ana ürün: '+str(item['name']),font=('Segoe UI',11,'bold')).pack(anchor='w',padx=14,pady=(14,2))
        ttk.Label(d,text='Değerleri virgülle ayırın. İkinci özellik boş bırakılabilir. Her kombinasyon için ayrı stok kartı açılır\n(fiyat/KDV/grup ana karttan kopyalanır, stok 0 başlar, barkod otomatik üretilir).',foreground='#526477').pack(anchor='w',padx=14)
        v1=tk.StringVar();v2=tk.StringVar()
        ttk.Label(d,text='1. özellik (örn. Renk): Kırmızı, Mavi, Siyah').pack(anchor='w',padx=14,pady=(14,2));ttk.Entry(d,textvariable=v1,width=60).pack(padx=14,anchor='w')
        ttk.Label(d,text='2. özellik (örn. Beden/Ebat): S, M, L  (isteğe bağlı)').pack(anchor='w',padx=14,pady=(10,2));ttk.Entry(d,textvariable=v2,width=60).pack(padx=14,anchor='w')
        def make():
            try:
                l1=[x for x in v1.get().split(',') if x.strip()];l2=[x for x in v2.get().split(',') if x.strip()]
                n=len(l1)*(len(l2) or 1)
                if n==0:raise ValueError('En az bir değer girin.')
                if not messagebox.askyesno('Çeşit Matrisi',str(n)+' yeni stok kartı oluşturulacak. Devam edilsin mi?',parent=d):return
                con=connection_factory()
                try:created=create_variants(con,stock_id,l1,l2)
                finally:con.close()
                refresh_variants();notify()
                messagebox.showinfo('Çeşit Matrisi',str(len(created))+' çeşit kartı oluşturuldu.'+('\n(Mevcut olanlar atlandı.)' if len(created)<n else ''),parent=d);d.destroy()
            except Exception as ex:messagebox.showerror('İşlem başarısız',str(ex),parent=d)
        ttk.Button(d,text='OLUŞTUR',command=make).pack(pady=18)
    def summary_dialog():
        import datetime as _dt
        d=tk.Toplevel(w);d.title('Çeşit Stok / Satış Özeti');d.geometry('640x430');d.transient(w)
        top=ttk.Frame(d);top.pack(fill='x',padx=12,pady=10)
        a1=tk.StringVar(value=_dt.date.today().replace(day=1).isoformat());a2=tk.StringVar(value=_dt.date.today().isoformat())
        ttk.Label(top,text='Başlangıç').pack(side='left');ttk.Entry(top,textvariable=a1,width=11).pack(side='left',padx=4)
        ttk.Label(top,text='Bitiş').pack(side='left',padx=(8,0));ttk.Entry(top,textvariable=a2,width=11).pack(side='left',padx=4)
        tv=ttk.Treeview(d,columns=('c','s','q','t'),show='headings')
        for k,h,wd in (('c','Çeşit',250),('s','Stok',90),('q','Satılan Adet',110),('t','Ciro',120)):tv.heading(k,text=h);tv.column(k,width=wd,anchor='w' if k=='c' else 'e')
        tv.pack(fill='both',expand=True,padx=12,pady=(0,12))
        def load():
            try:
                _dt.date.fromisoformat(a1.get().strip());_dt.date.fromisoformat(a2.get().strip())
                con=connection_factory()
                try:rows=variant_summary(con,stock_id,a1.get().strip(),a2.get().strip())
                finally:con.close()
            except ValueError:
                return messagebox.showwarning('Tarih','Tarihleri YYYY-AA-GG biçiminde girin.',parent=d)
            tv.delete(*tv.get_children())
            for r in rows:tv.insert('','end',values=(r[0],f'{r[1]:g}',f'{r[2]:g}',f'{r[3]:,.2f}'.replace(',','X').replace('.',',').replace('X','.')))
        ttk.Button(top,text='LİSTELE',command=load).pack(side='left',padx=8);load()
    def add_variant():
        try:
            target=lookup(e.get());sql('INSERT INTO stock_variant_links(family_stock_id,variant_stock_id,description) VALUES(?,?,?)',(stock_id,target['id'],note.get().strip()));refresh_variants()
        except Exception as ex:error(ex)
    def del_variant():
        try:sql('DELETE FROM stock_variant_links WHERE id=? AND family_stock_id=?',(selected_id(t),stock_id));refresh_variants()
        except Exception as ex:error(ex)
    buttons(frame,[('ÇEŞİT BAĞLA',add_variant),('MATRİSTEN ÇEŞİT OLUŞTUR',matrix_dialog),('STOK / SATIŞ ÖZETİ',summary_dialog),('SEÇİLİ BAĞI KALDIR',del_variant)]);refresh_variants()
    # Birim dönüşümleri tanım amaçlıdır; hareketlere otomatik uygulanmaz.
    frame,u=tab('Birim Dönüşümü',['Birim','1 birimdeki ana adet'])
    line=ttk.Frame(frame);line.pack(fill='x')
    unit=ttk.Entry(line,width=25);unit.pack(side='left',padx=(0,8));unit.insert(0,'Kutu')
    quantity=ttk.Entry(line,width=12);quantity.pack(side='left');quantity.insert(0,'12')
    ttk.Label(frame,text='Birim barkodu ve birim fiyatları stok kartındaki "Birimler / Barkodlar" sekmesinden girilir; stok her zaman ana birim (adet) cinsinden tutulur.',foreground='#9b5200').pack(anchor='w',pady=8)
    def refresh_units():redraw(u,'SELECT id,unit_name,base_quantity FROM stock_unit_conversions WHERE stock_id=? ORDER BY unit_name',(stock_id,))
    def add_unit():
        try:
            name=unit.get().strip();val=float(quantity.get().replace(',','.'))
            if not name or not 0<val<100000000:raise ValueError('Geçerli birim adı ve pozitif dönüşüm miktarı girin.')
            sql('INSERT INTO stock_unit_conversions(stock_id,unit_name,base_quantity) VALUES(?,?,?) ON CONFLICT(stock_id,unit_name) DO UPDATE SET base_quantity=excluded.base_quantity',(stock_id,name,val));refresh_units()
        except Exception as ex:error(ex)
    def del_unit():
        try:
            uid=selected_id(u)
            sql("DELETE FROM stock_barcodes WHERE stock_id=? AND barcode IN (SELECT barcode FROM stock_unit_conversions WHERE id=? AND stock_id=? AND barcode<>'')",(stock_id,uid,stock_id))
            sql('DELETE FROM stock_unit_conversions WHERE id=? AND stock_id=?',(uid,stock_id));refresh_units()
        except Exception as ex:error(ex)
    buttons(frame,[('DÖNÜŞÜM KAYDET',add_unit),('SEÇİLİ TANIMI KALDIR',del_unit)]);refresh_units()
    # Muadil ürünler
    frame,a=tab('Muadil Ürünler',['Stok Kodu','Muadil Ürün','Not'])
    alt=ttk.Entry(frame);alt.pack(fill='x');alt.insert(0,'Muadil ürün barkodu / stok kodu')
    alt_note=ttk.Entry(frame);alt_note.pack(fill='x',pady=5)
    def refresh_alts():redraw(a,'SELECT x.id,s.code,s.name,x.note FROM stock_alternatives x JOIN stock s ON s.id=x.alternative_stock_id WHERE x.stock_id=? ORDER BY s.name',(stock_id,))
    def add_alt():
        try:
            target=lookup(alt.get());sql('INSERT INTO stock_alternatives(stock_id,alternative_stock_id,note) VALUES(?,?,?)',(stock_id,target['id'],alt_note.get().strip()));refresh_alts()
        except Exception as ex:error(ex)
    def del_alt():
        try:sql('DELETE FROM stock_alternatives WHERE id=? AND stock_id=?',(selected_id(a),stock_id));refresh_alts()
        except Exception as ex:error(ex)
    buttons(frame,[('MUADİL EKLE',add_alt),('SEÇİLİ BAĞI KALDIR',del_alt)]);refresh_alts()
    # Görseller: DB ile aynı dönem klasöründe saklanır; kaynak dosyaya dokunulmaz.
    frame,img=tab('Ürün Görselleri',['Başlık','Dosya'])
    img_dir=db_path.parent/'stok_gorselleri'; img_dir.mkdir(parents=True,exist_ok=True)
    def refresh_imgs():redraw(img,'SELECT id,title,image_file FROM stock_images WHERE stock_id=? ORDER BY id',(stock_id,))
    def add_img():
        src=filedialog.askopenfilename(parent=w,title='Ürün görseli seç',filetypes=[('Görsel','*.png *.jpg *.jpeg *.webp *.gif')])
        if not src:return
        try:
            src=Path(src)
            if src.stat().st_size>10*1024*1024:raise ValueError('Görsel 10 MB üzerinde olamaz.')
            if src.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif'):raise ValueError('Desteklenmeyen dosya türü.')
            digest=hashlib.sha256(src.read_bytes()).hexdigest()[:24]
            name=f'{stock_id}_{digest}{src.suffix.lower()}';dst=img_dir/name
            if not dst.exists():shutil.copyfile(src,dst)
            sql('INSERT OR IGNORE INTO stock_images(stock_id,image_file,title) VALUES(?,?,?)',(stock_id,name,src.stem));refresh_imgs();notify()
        except Exception as ex:error(ex)
    def del_img():
        try:
            # Kayıt kaldırılır, dosya güvenlik için otomatik silinmez.
            sql('DELETE FROM stock_images WHERE id=? AND stock_id=?',(selected_id(img),stock_id));refresh_imgs();notify()
        except Exception as ex:error(ex)
    def view_img():
        try:
            rows=sql('SELECT image_file FROM stock_images WHERE id=? AND stock_id=?',(selected_id(img),stock_id),True)
            if not rows:raise ValueError('Görsel bulunamadı.')
            path=img_dir/rows[0]['image_file']
            if not path.is_file():raise ValueError('Görsel dosyası bulunamadı: '+str(path))
            import os
            if os.name=='nt':os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(['xdg-open',str(path)])
        except Exception as ex:error(ex)
    buttons(frame,[('GÖRSEL EKLE',add_img),('GÖRSELİ AÇ',view_img),('SEÇİLİ KAYDI KALDIR',del_img)]);refresh_imgs()
    # Set / Paket: bileşenler satışta stoktan düşer, set kartının kendi stoğu kullanılmaz.
    frame,tc=tab('Set / Paket',['Stok Kodu','Bileşen','Adet'])
    ce=ttk.Entry(frame);ce.pack(fill='x');ce.insert(0,'Bileşenin barkodunu veya stok kodunu yazın')
    cq=ttk.Entry(frame);cq.pack(fill='x',pady=5);cq.insert(0,'1')
    info=ttk.Label(frame,foreground='#526477');info.pack(anchor='w',pady=(0,6))
    def refresh_comps():
        redraw(tc,"SELECT sc.id,s.code,s.name,sc.qty FROM stock_components sc JOIN stock s ON s.id=sc.component_id WHERE sc.set_id=? ORDER BY s.name",(stock_id,))
        con=connection_factory()
        try:
            av=set_available(con,stock_id)
            info.config(text=('Bu ürün set değil.' if av is None else 'Satılabilir set sayısı: '+str(av)+'   |   Set maliyeti: '+f'{set_cost(con,stock_id):,.2f}'))
        finally:con.close()
    def add_comp():
        try:
            row=lookup(ce.get());q=float(cq.get().replace(',','.'))
            con=connection_factory()
            try:add_component(con,stock_id,row['id'],q)
            finally:con.close()
            refresh_comps();notify()
        except Exception as ex:error(ex)
    def del_comp():
        try:
            sql('DELETE FROM stock_components WHERE id=? AND set_id=?',(selected_id(tc),stock_id));refresh_comps();notify()
        except Exception as ex:error(ex)
    buttons(frame,[('BİLEŞEN EKLE / GÜNCELLE',add_comp),('SEÇİLİ BİLEŞENİ KALDIR',del_comp)]);refresh_comps()
    # Kitap / ISBN: yazar, yayınevi, ISBN. ISBN-10 girilirse ISBN-13'e çevrilir ve barkod olarak da taranabilir.
    bk=ttk.Frame(book,padding=14);book.add(bk,text='Kitap / ISBN')
    bvars={k:tk.StringVar() for k in ('author','publisher','isbn')}
    rows0=sql('SELECT author,publisher,isbn FROM stock_book_info WHERE stock_id=?',(stock_id,),True)
    if rows0:
        for k in bvars:bvars[k].set(rows0[0][k])
    for i,(k,lab) in enumerate((('author','Yazar'),('publisher','Yayınevi'),('isbn','ISBN (10 veya 13 hane)'))):
        ttk.Label(bk,text=lab).grid(row=i,column=0,sticky='w',pady=6,padx=(0,12))
        ttk.Entry(bk,textvariable=bvars[k],width=44).grid(row=i,column=1,sticky='w',pady=6)
    def save_book():
        try:
            isbn_in=bvars['isbn'].get().strip();i13=''
            if isbn_in:
                i13=isbn_to13(isbn_in)
                if not i13:raise ValueError('ISBN geçerli değil (kontrol hanesi hatalı olabilir).')
            con=connection_factory()
            try:
                con.execute('BEGIN IMMEDIATE')
                if i13:
                    dup=con.execute("SELECT 1 FROM stock WHERE barcode=? AND id<>? UNION SELECT 1 FROM stock_barcodes WHERE barcode=? AND stock_id<>? AND active=1",(i13,stock_id,i13,stock_id)).fetchone()
                    if dup:raise ValueError('Bu ISBN başka bir üründe barkod olarak kayıtlı.')
                    own=con.execute("SELECT 1 FROM stock WHERE barcode=? AND id=? UNION SELECT 1 FROM stock_barcodes WHERE barcode=? AND stock_id=?",(i13,stock_id,i13,stock_id)).fetchone()
                    if not own:
                        con.execute('INSERT INTO stock_barcodes(stock_id,barcode,active) VALUES(?,?,1)',(stock_id,i13))
                con.execute('INSERT INTO stock_book_info(stock_id,author,publisher,isbn) VALUES(?,?,?,?) ON CONFLICT(stock_id) DO UPDATE SET author=excluded.author,publisher=excluded.publisher,isbn=excluded.isbn',(stock_id,bvars['author'].get().strip(),bvars['publisher'].get().strip(),i13))
                con.commit()
            except Exception:
                con.rollback();raise
            finally:con.close()
            bvars['isbn'].set(i13)
            messagebox.showinfo('Kitap / ISBN','Kaydedildi.'+(' ISBN-13 barkod olarak da tanımlandı: '+i13 if i13 else ''),parent=w)
        except Exception as ex:error(ex)
    ttk.Button(bk,text='KAYDET',command=save_book).grid(row=3,column=1,sticky='w',pady=14)
    ttk.Label(bk,text='ISBN, stokta ek barkod olarak tanımlanır; satış ekranında ISBN-10 okutulsa da ürün bulunur.',foreground='#526477').grid(row=4,column=0,columnspan=2,sticky='w')
    ttk.Button(w,text='KAPAT',command=kapat).pack(anchor='e',padx=14,pady=(0,12))
