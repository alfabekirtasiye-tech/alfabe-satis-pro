# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - Fiş fotoğrafından gider kaydı (v414)
Fiş/fatura fotoğrafını seçersiniz; tarih, toplam tutar ve işyeri adı okunur, sizin onayınızla GİDER olarak kaydedilir.
 * Okuma: Windows 10/11'in kendi OCR motoru (internet gerekmez, ek kurulum yok). Türkçe dil paketi varsa Türkçe okur.
 * Okuma başarısız olursa ya da yanlışsa alanlar elle düzeltilir; hiçbir şey onayınız olmadan kaydedilmez.
 * Kayıt, ana programdaki "Yeni Gider" ile aynı tablolara aynı kurallarla yazılır (finance + expense_records, tek işlem).
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE_TYPES = [('Fiş fotoğrafı', '*.jpg *.jpeg *.png *.bmp *.tif *.tiff'), ('Tüm dosyalar', '*.*')]

_PS = r"""
param([string]$Path,[string]$Lang,[int]$Up=0)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op,$type){ $t=$asTask.MakeGenericMethod($type).Invoke($null,@($op)); $t.Wait(-1) | Out-Null; $t.Result }
if($Up -eq 1){
 try{
  Add-Type -AssemblyName System.Drawing
  $img=[System.Drawing.Image]::FromFile($Path)
  $mx=[Math]::Max($img.Width,$img.Height)
  $f=[Math]::Min(4.0,2400.0/$mx)
  if($f -ge 1.3){
   $nw=[int]($img.Width*$f);$nh=[int]($img.Height*$f)
   $nb=New-Object System.Drawing.Bitmap($nw,$nh)
   $gr=[System.Drawing.Graphics]::FromImage($nb)
   $gr.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
   $gr.DrawImage($img,0,0,$nw,$nh);$gr.Dispose()
   $tp=Join-Path $PSScriptRoot 'buyuk.png'
   $nb.Save($tp,[System.Drawing.Imaging.ImageFormat]::Png);$nb.Dispose();$img.Dispose()
   $Path=$tp
  } else { $img.Dispose() }
 }catch{}
}
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp=Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$eng=$null
if($Lang){ try{ $eng=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang)) }catch{} }
if(-not $eng){ $eng=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if(-not $eng){ Write-Output '##NOLANG'; exit 3 }
$res=Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
foreach($l in $res.Lines){ Write-Output $l.Text }
"""


def ocr_image(path, timeout=60, upscale=False):
    """upscale=True: küçük fotoğrafı okutmadan önce büyütür (v470, yalnız Liste Fiyatla kullanır; varsayılan kapalı = eski davranış).
    (metin, hata) döndürür. Windows dışında ya da motor yoksa metin boş, hata açıklamalıdır."""
    if not sys.platform.startswith('win'):
        return '', 'Otomatik okuma yalnız Windows 10/11 üzerinde çalışır; bilgileri elle yazabilirsiniz.'
    p = Path(path)
    if not p.exists():
        return '', 'Dosya bulunamadı.'
    tmp = Path(tempfile.mkdtemp(prefix='alfabe_ocr_'))
    try:
        ps1 = tmp / 'ocr.ps1'
        ps1.write_text(_PS, encoding='utf-8-sig')
        flags = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        for lang in ('tr-TR', ''):
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ps1), '-Path', str(p.resolve()), '-Lang', lang] + (['-Up', '1'] if upscale else []),
                               capture_output=True, timeout=timeout, creationflags=flags)
            out = r.stdout.decode('utf-8', 'replace').strip()
            if r.returncode == 0 and out:
                return out, ''
            if r.returncode == 3 or '##NOLANG' in out:
                return '', 'Windows\'ta okuma dili bulunamadı (Ayarlar > Zaman ve Dil > Dil bölümünden Türkçe ekleyin).'
        err = r.stderr.decode('utf-8', 'replace').strip().splitlines()
        return '', 'Fişte okunabilir yazı bulunamadı' + (' (' + err[0][:120] + ')' if err else '') + '. Fotoğrafı daha net/aydınlık çekip tekrar deneyin ya da elle yazın.'
    except subprocess.TimeoutExpired:
        return '', 'Okuma çok uzun sürdü; elle yazabilirsiniz.'
    except Exception as ex:
        return '', 'Okuma yapılamadı: %s' % ex
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------------------------
# Metin çözümleme (platformdan bağımsız, test edilebilir)
# ----------------------------------------------------------------------------------------------
_NUM = re.compile(r'(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})+[.,]\d{2}|\d+[.,]\d{2})(?![\d])')
_DATE = re.compile(r'(?<!\d)(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4}|\d{2})(?!\d)')
_TOTAL_WORDS = ('genel toplam', 'toplam', 'top.', 'tutar', 'odenecek', 'ödenecek', 'nakit', 'kredi', 'kart', 'tahsil', 'odeme', 'ödeme')
_SKIP_WORDS = ('kdv', 'ara toplam', 'aratoplam', 'indirim', 'iskonto', 'para üstü', 'para ustu', 'topkdv', 'matrah', 'puan')


def _fold(s):
    return str(s).replace('İ', 'i').replace('I', 'ı').lower().replace('ı', 'i')


def to_amount(tok):
    t = re.sub(r'\s', '', tok)
    m = re.search(r'[.,](\d{2})$', t)
    if not m:
        return None
    ip = re.sub(r'[.,]', '', t[:m.start()])
    try:
        return round(float(ip + '.' + m.group(1)), 2)
    except ValueError:
        return None


def parse_receipt(text, today=None):
    """OCR metninden dict(date, total, vat, merchant, candidates) çıkarır. Bulunamayan alan '' / None olur."""
    today = today or datetime.date.today()
    lines = [ln.strip() for ln in str(text or '').splitlines() if ln.strip()]
    res = dict(date='', total=None, vat=None, merchant='', candidates=[])
    # tarih
    for ln in lines:
        for m in _DATE.finditer(ln):
            d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
            y = int(y) + (2000 if len(y) == 2 else 0)
            try:
                dt = datetime.date(y, mo, d)
            except ValueError:
                continue
            if dt > today + datetime.timedelta(days=1) or dt < today - datetime.timedelta(days=366 * 3):
                continue
            res['date'] = dt.isoformat()
            break
        if res['date']:
            break
    # tutar
    scored, allv = [], []
    for i, ln in enumerate(lines):
        f = _fold(ln)
        vals = [v for v in (to_amount(x) for x in _NUM.findall(ln)) if v is not None and v > 0]
        if not vals:
            continue
        allv += vals
        if 'kdv' in f and not any(w in f for w in ('genel toplam',)):
            if 'toplam' in f or 'top' in f:
                res['vat'] = vals[-1]
            continue
        if any(w in f for w in _SKIP_WORDS):
            continue
        score = 0
        if 'genel toplam' in f:
            score = 100
        elif 'toplam' in f or 'odenecek' in f or 'ödenecek' in _fold(ln):
            score = 90
        elif '*' in ln[:3] and re.search(r'top', f):
            score = 85
        elif any(w in f for w in _TOTAL_WORDS):
            score = 60
        if score:
            scored.append((score, i, vals[-1]))
    if scored:
        best = max(scored, key=lambda x: (x[0], -x[1]))
        res['total'] = best[2]
    elif allv:
        res['total'] = max(allv)
    res['candidates'] = sorted(set(allv), reverse=True)[:6]
    # işyeri: ilk anlamlı harfli satır
    for ln in lines[:6]:
        letters = sum(ch.isalpha() for ch in ln)
        if letters >= 4 and not _DATE.search(ln) and letters >= len(ln) * 0.5:
            res['merchant'] = ' '.join(ln.split())[:60]
            break
    return res


# ----------------------------------------------------------------------------------------------
# Kayıt
# ----------------------------------------------------------------------------------------------
def save_expense(con, dt_text, category, account, payment, amount, note, user='', audit=None):
    """Ana programdaki 'Yeni Gider' ile aynı kayıt. Dönüş: finance_id."""
    a = float(amount)
    if a <= 0:
        raise ValueError('Tutar sıfırdan büyük olmalı.')
    try:
        datetime.datetime.strptime(str(dt_text).strip()[:10], '%Y-%m-%d')
    except Exception:
        raise ValueError('Tarih geçersiz.')
    if not str(category).strip():
        raise ValueError('Kategori seçin.')
    note = str(note or '').strip()
    con.execute('BEGIN IMMEDIATE')
    try:
        cur = con.execute('INSERT INTO finance(dt,account,kind,amount,source,note) VALUES(?,?,?,?,?,?)',
                          (dt_text, account, 'Gider', -a, 'EXPENSE_PRO', (str(category) + ' - ' + note).strip(' -')))
        fid = cur.lastrowid
        con.execute('INSERT INTO expense_records(finance_id,dt,category,account,amount,payment_type,note,user,status) VALUES(?,?,?,?,?,?,?,?,?)',
                    (fid, dt_text, category, account, a, payment, note, user, 'Aktif'))
        con.commit()
    except Exception:
        con.rollback()
        raise
    if audit:
        try:
            audit('Finans', 'Gider (fiş fotoğrafı)', new={'kategori': category, 'tutar': a, 'hesap': account})
        except Exception:
            pass
    return fid


def keep_image(src, store_dir, when=None):
    """Fiş görselini veri klasörüne kopyalar; kopya yolunu döndürür (başarısızsa '')."""
    try:
        when = when or datetime.date.today()
        d = Path(store_dir) / when.strftime('%Y%m')
        d.mkdir(parents=True, exist_ok=True)
        s = Path(src)
        dst = d / (when.strftime('%Y%m%d') + '_' + datetime.datetime.now().strftime('%H%M%S') + s.suffix.lower())
        shutil.copy2(s, dst)
        return str(dst)
    except Exception:
        return ''


# ----------------------------------------------------------------------------------------------
# Ekran
# ----------------------------------------------------------------------------------------------
def open_dialog(parent, connection_factory, categories, accounts, user='', store_dir=None, audit=None, on_done=None, ocr=ocr_image, image_path=None):
    import threading
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    w = tk.Toplevel(parent)
    w.title('Fişten Gider Kaydı')
    w.geometry('980x700')
    w.minsize(900, 640)
    w.configure(bg='#f4f7fb')
    try:
        w.transient(parent.winfo_toplevel())
    except Exception:
        pass
    v = dict(date=tk.StringVar(value=datetime.date.today().isoformat()), amount=tk.StringVar(), category=tk.StringVar(), account=tk.StringVar(value=(accounts[0] if accounts else 'Ana Kasa')),
             payment=tk.StringVar(value='Nakit'), note=tk.StringVar())
    st = {'path': '', 'busy': False, 'photo': None}
    tk.Label(w, text='FİŞTEN GİDER KAYDI', font=('Segoe UI', 18, 'bold'), fg='#0b376d', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(14, 0))
    tk.Label(w, text='1) Fiş fotoğrafını seçin  2) Okunan bilgileri kontrol edip düzeltin  3) KAYDET. Onayınız olmadan hiçbir kayıt yapılmaz.',
             font=('Segoe UI', 10), fg='#475569', bg='#f4f7fb').pack(anchor='w', padx=18, pady=(0, 8))
    body = tk.Frame(w, bg='#f4f7fb')
    body.pack(fill='both', expand=True, padx=18)
    left = tk.Frame(body, bg='white', highlightbackground='#cbd5e1', highlightthickness=1, width=360)
    left.pack(side='left', fill='y')
    left.pack_propagate(False)
    pic = tk.Label(left, text='Fiş fotoğrafı burada görünür', bg='white', fg='#94a3b8', font=('Segoe UI', 11))
    pic.pack(fill='both', expand=True, padx=8, pady=8)
    right = tk.Frame(body, bg='#f4f7fb')
    right.pack(side='left', fill='both', expand=True, padx=(14, 0))
    status = tk.Label(right, text='', font=('Segoe UI', 10, 'bold'), bg='#f4f7fb', fg='#475569', wraplength=520, justify='left', anchor='w')
    status.pack(fill='x')
    form = tk.Frame(right, bg='white', highlightbackground='#cbd5e1', highlightthickness=1)
    form.pack(fill='x', pady=8)

    def row(r, lab, widget):
        tk.Label(form, text=lab, font=('Segoe UI', 10, 'bold'), bg='white', width=14, anchor='w').grid(row=r, column=0, padx=10, pady=6, sticky='w')
        widget.grid(row=r, column=1, padx=10, pady=6, sticky='we')
    form.grid_columnconfigure(1, weight=1)
    row(0, 'Tarih (YYYY-AA-GG)', tk.Entry(form, textvariable=v['date'], font=('Segoe UI', 12)))
    row(1, 'Tutar (TL)', tk.Entry(form, textvariable=v['amount'], font=('Segoe UI', 12, 'bold')))
    row(2, 'Kategori', ttk.Combobox(form, textvariable=v['category'], values=list(categories), font=('Segoe UI', 11)))
    row(3, 'Hesap', ttk.Combobox(form, textvariable=v['account'], values=list(accounts) or ['Ana Kasa'], state='readonly', font=('Segoe UI', 11)))
    row(4, 'Ödeme türü', ttk.Combobox(form, textvariable=v['payment'], values=['Nakit', 'Banka', 'Kart', 'Diğer'], state='readonly', font=('Segoe UI', 11)))
    row(5, 'Açıklama', tk.Entry(form, textvariable=v['note'], font=('Segoe UI', 11)))
    cand = tk.Frame(right, bg='#f4f7fb')
    cand.pack(fill='x')
    tk.Label(right, text='Fişten okunan yazı (yanlışsa yukarıdaki alanları elle düzeltin):', font=('Segoe UI', 9, 'bold'), bg='#f4f7fb', fg='#475569').pack(anchor='w', pady=(6, 2))
    raw = tk.Text(right, height=9, font=('Consolas', 10), bg='white', relief='solid', bd=1)
    raw.pack(fill='both', expand=True)

    def show_image(p):
        try:
            from PIL import Image, ImageTk
            im = Image.open(p)
            im.thumbnail((340, 560))
            st['photo'] = ImageTk.PhotoImage(im)
            pic.config(image=st['photo'], text='')
        except Exception:
            pic.config(image='', text=Path(p).name + '\n(önizleme gösterilemedi)')

    def apply_text(text, err):
        st['busy'] = False
        pick_btn.config(state='normal')
        raw.delete('1.0', 'end')
        raw.insert('1.0', text)
        for ch in cand.winfo_children():
            ch.destroy()
        if err:
            status.config(text='⚠ ' + err, fg='#b45309')
            return
        r = parse_receipt(text)
        got = []
        if r['date']:
            v['date'].set(r['date'])
            got.append('tarih')
        if r['total']:
            v['amount'].set(('%.2f' % r['total']).replace('.', ','))
            got.append('tutar')
        if r['merchant'] and not v['note'].get():
            v['note'].set(r['merchant'])
            got.append('işyeri')
        status.config(text=('✓ Okundu: ' + ', '.join(got) + '. Lütfen kontrol edin.') if got else '⚠ Yazı okundu ama tarih/tutar seçilemedi; elle girin.', fg='#15803d' if got else '#b45309')
        if len(r['candidates']) > 1:
            tk.Label(cand, text='Fişteki diğer tutarlar:', bg='#f4f7fb', font=('Segoe UI', 9)).pack(side='left')
            for c in r['candidates']:
                tk.Button(cand, text=('%.2f' % c).replace('.', ','), command=lambda x=c: v['amount'].set(('%.2f' % x).replace('.', ',')), font=('Segoe UI', 9), padx=6).pack(side='left', padx=2)

    def load(p):
        if not p:
            return
        st['path'] = p
        show_image(p)
        status.config(text='Fiş okunuyor, lütfen bekleyin...', fg='#475569')
        st['busy'] = True
        pick_btn.config(state='disabled')
        box = {}

        def work():
            box['r'] = ocr(p)
        th = threading.Thread(target=work, daemon=True)
        th.start()

        def poll():
            if th.is_alive():
                w.after(150, poll)
            else:
                try:
                    apply_text(*box.get('r', ('', 'Okuma sonuç vermedi.')))
                except tk.TclError:
                    pass
        w.after(150, poll)

    def pick():
        p = filedialog.askopenfilename(parent=w, title='Fiş fotoğrafını seçin', filetypes=IMAGE_TYPES)
        if p:
            load(p)

    def amount_value():
        t = v['amount'].get().strip().replace(' ', '').replace('₺', '').replace('TL', '')
        if ',' in t and '.' in t:
            t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
        else:
            t = t.replace(',', '.')
        return float(t)

    def save():
        try:
            a = amount_value()
        except ValueError:
            return messagebox.showwarning('Fişten Gider', 'Geçerli bir tutar yazın.', parent=w)
        d = v['date'].get().strip()
        note = v['note'].get().strip()
        if st['path'] and store_dir:
            k = keep_image(st['path'], store_dir)
            if k:
                note = (note + ' [Fiş: ' + os.path.basename(k) + ']').strip()
        con = connection_factory()
        try:
            save_expense(con, d + (' ' + datetime.datetime.now().strftime('%H:%M:%S') if len(d) == 10 else ''), v['category'].get().strip(), v['account'].get(),
                         v['payment'].get(), a, note, user, audit)
        except ValueError as ex:
            return messagebox.showwarning('Fişten Gider', str(ex), parent=w)
        except Exception as ex:
            return messagebox.showerror('Fişten Gider', 'Kaydedilemedi, hiçbir değişiklik yapılmadı:\n%s' % ex, parent=w)
        status.config(text='✓ Gider kaydedildi: %s TL (%s)' % (v['amount'].get(), v['category'].get()), fg='#15803d')
        v['amount'].set('')
        v['note'].set('')
        raw.delete('1.0', 'end')
        pic.config(image='', text='Fiş fotoğrafı burada görünür')
        st['path'] = ''
        if on_done:
            try:
                on_done()
            except Exception:
                pass

    bar = tk.Frame(w, bg='#f4f7fb')
    bar.pack(fill='x', padx=18, pady=10)
    pick_btn = tk.Button(bar, text='📷 FİŞ FOTOĞRAFI SEÇ', command=pick, font=('Segoe UI', 11, 'bold'), bg='#0b6bcb', fg='white', relief='flat', padx=16, pady=7)
    pick_btn.pack(side='left')
    tk.Button(bar, text='KAYDET', command=save, font=('Segoe UI', 11, 'bold'), bg='#15803d', fg='white', relief='flat', padx=22, pady=7).pack(side='left', padx=10)
    tk.Button(bar, text='KAPAT', command=w.destroy, font=('Segoe UI', 10, 'bold'), padx=12, pady=7).pack(side='right')
    w._fis = dict(vars=v, status=status, raw=raw, load=load, save=save, state=st, apply_text=apply_text)
    if image_path:
        w.after(100, lambda: load(image_path))
    return w
