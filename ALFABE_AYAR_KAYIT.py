# -*- coding: utf-8 -*-
"""ALFABE SATIŞ PRO - AYAR KAYIT DEFTERİ (v1.2.291.452)

Programdaki TÜM ayarların tek kaynağıdır. Bir ayar buraya yazılmadan Ayarlar Merkezi'nde görünmez; buraya yazılan her ayar
programın ilgili yerinde gerçekten okunur (tests/ayar_denetimi.py bunu kontrol eder).

Değerler veritabanında metin olarak tutulur:  Evet/Hayır, sayılar (nokta ondalıklı), seçenek metinleri, yollar.
Okuma:  ayar('anahtar')  -> metin,  ayar_evet('anahtar') -> bool,  ayar_sayi('anahtar') -> float,  ayar_tam('anahtar') -> int
Okuma sırası: Bu Bilgisayar > Kullanıcı > Dönem > Firma > Sistem > eski genel ayar > bu dosyadaki varsayılan.
"""
import time as _time

# ----------------------------------------------------------------------------- tanımlar
KINDS = ('bool', 'choice', 'int', 'float', 'text', 'dir', 'file', 'printer', 'time')


class Spec:
    __slots__ = ('key', 'label', 'kind', 'default', 'options', 'lo', 'hi', 'unit', 'help', 'live', 'depends', 'placeholder', 'secret', 'dyn', 'firma')

    def __init__(self, key, label, kind, default, help='', options=None, lo=None, hi=None, unit='', live=True, depends=None, placeholder='', firma=False):
        self.key = key
        self.label = label
        self.kind = kind
        self.default = str(default)
        self.options = list(options) if options else None
        self.lo = lo
        self.hi = hi
        self.unit = unit
        self.help = help
        self.live = live          # True: kaydedince hemen etkili; False: yeni pencere / yeniden başlatma gerekir
        self.depends = depends    # (başka_anahtar, [değerler]) -> o ayar bu değerlerden biri değilse bu satır pasif görünür
        self.placeholder = placeholder
        self.secret = False
        self.firma = firma         # True: yalnız Firma kapsamında tutulur (güvenlik, yedek, belge no gibi tüm firmayı bağlayan ayarlar)
        self.dyn = None            # 'units' | 'vat' | 'pos' : seçenekler çalışma anında veritabanından gelir


def B(key, label, default, help='', **kw):
    """Evet / Hayır ayarı."""
    return Spec(key, label, 'bool', default, help, options=['Evet', 'Hayır'], **kw)


def C(key, label, default, options, help='', **kw):
    return Spec(key, label, 'choice', default, help, options=options, **kw)


def I(key, label, default, lo, hi, help='', unit='', **kw):
    return Spec(key, label, 'int', default, help, lo=lo, hi=hi, unit=unit, **kw)


def F(key, label, default, lo, hi, help='', unit='', **kw):
    return Spec(key, label, 'float', default, help, lo=lo, hi=hi, unit=unit, **kw)


def T(key, label, default, help='', **kw):
    return Spec(key, label, 'text', default, help, **kw)


def D(key, label, default, help='', **kw):
    return Spec(key, label, 'dir', default, help, **kw)


def P(key, label, default, help='', **kw):
    return Spec(key, label, 'printer', default, help, **kw)


class Cat:
    """Ayar kategorisi. special: özel sayfa kimliği (None = yalnız ayar satırları)."""

    def __init__(self, cid, title, icon, desc, groups=None, special=None, extra_after=None):
        self.id = cid
        self.title = title
        self.icon = icon
        self.desc = desc
        self.groups = groups or []          # [(grup_başlığı, [Spec,...]), ...]
        self.special = special
        self.extra_after = extra_after      # satırlardan sonra gösterilecek özel blok kimliği

    def specs(self):
        for _g, items in self.groups:
            for s in items:
                yield s


YOK = 'Windows Varsayılan Yazıcı'
IZIN = ['İzin Ver', 'Uyar', 'Engelle']

CATEGORIES = []     # aşağıda doldurulur (registry_*.py bölümleri)


# ----------------------------------------------------------------------------- çözümleyici (programdan bağlanır)
_RESOLVER = {'fn': None}
_CACHE = {'t': 0.0, 'm': {}}
_TTL = 2.0


def bind(resolver):
    """resolver(anahtar) -> metin | None  (kapsam sırasını uygulayan program işlevi)."""
    _RESOLVER['fn'] = resolver
    invalidate()


def invalidate():
    _CACHE['t'] = 0.0
    _CACHE['m'] = {}


_INDEX = {}


def _index():
    if not _INDEX:
        for c in CATEGORIES:
            for s in c.specs():
                _INDEX[s.key] = s
    return _INDEX


def reset_index():
    _INDEX.clear()


def spec(key):
    return _index().get(key)


def all_specs():
    return list(_index().values())


def category_of(key):
    for c in CATEGORIES:
        for s in c.specs():
            if s.key == key:
                return c
    return None


def default(key):
    s = spec(key)
    return s.default if s else ''


def ayar(key, varsayilan=None):
    """Ayarın etkin değeri (metin). Kayıt yoksa/boşsa kayıt defterindeki varsayılan."""
    now = _time.time()
    if now - _CACHE['t'] > _TTL:
        _CACHE['m'] = {}
        _CACHE['t'] = now
    m = _CACHE['m']
    if key in m:
        return m[key]
    s = spec(key)
    v = None
    fn = _RESOLVER['fn']
    if fn is not None:
        try:
            v = fn(key)
        except Exception:
            v = None
    if v is None or (str(v).strip() == '' and not (s and s.kind in ('text', 'dir', 'file', 'printer'))):
        v = varsayilan if varsayilan is not None else (s.default if s else '')
    v = str(v)
    m[key] = v
    return v


def ayar_evet(key, varsayilan=None):
    return ayar(key, varsayilan).strip().casefold() in ('evet', '1', 'true', 'açık', 'acik', 'e')


def ayar_sayi(key, varsayilan=None):
    s = spec(key)
    try:
        n = float(str(ayar(key, varsayilan)).replace(',', '.'))
    except (TypeError, ValueError):
        try:
            n = float(str(s.default).replace(',', '.'))
        except Exception:
            n = 0.0
    if s is not None:
        if s.lo is not None:
            n = max(float(s.lo), n)
        if s.hi is not None:
            n = min(float(s.hi), n)
    return n


def ayar_tam(key, varsayilan=None):
    return int(round(ayar_sayi(key, varsayilan)))


def izin(key):
    """İzin Ver / Uyar / Engelle ayarını 'izin' | 'uyar' | 'engelle' olarak döndürür."""
    v = ayar(key).strip().casefold()
    if v.startswith('engel'):
        return 'engelle'
    if v.startswith('uyar'):
        return 'uyar'
    return 'izin'


# ----------------------------------------------------------------------------- doğrulama
def normalize(s, value):
    """(True, normal değer) | (False, hata metni). Hiçbir şey yazmaz."""
    v = '' if value is None else str(value).strip()
    if s.kind == 'choice' and s.options is None:
        if not v:
            return False, f'{s.label}: bir değer seçin.'
        return True, v
    if s.kind in ('bool', 'choice'):
        if v not in s.options:
            return False, f'{s.label}: listeden geçerli bir seçenek seçin.'
        return True, v
    if s.kind in ('int', 'float'):
        if v == '':
            return False, f'{s.label}: bir sayı girin.'
        try:
            n = float(v.replace(',', '.'))
        except ValueError:
            return False, f'{s.label}: sayısal bir değer girin.'
        if (s.lo is not None and n < s.lo) or (s.hi is not None and n > s.hi):
            return False, f'{s.label}: değer {s.lo:g} ile {s.hi:g} arasında olmalıdır.'
        if s.kind == 'int':
            if abs(n - round(n)) > 1e-9:
                return False, f'{s.label}: tam sayı olmalıdır.'
            return True, str(int(round(n)))
        return True, f'{n:.6f}'.rstrip('0').rstrip('.') if n != int(n) else str(int(n))
    if s.kind == 'time':
        import re
        if not re.fullmatch(r'([01]?\d|2[0-3]):[0-5]\d', v):
            return False, f'{s.label}: saat SS:DD biçiminde olmalıdır (örn. 21:30).'
        h, m = v.split(':')
        return True, f'{int(h):02d}:{m}'
    if s.kind == 'text' and s.key.startswith('prefix_'):
        import re
        v = v.upper()
        if not re.fullmatch(r'[A-Z0-9]{1,4}', v):
            return False, f'{s.label}: 1-4 karakter olmalı; yalnız A-Z harf ve rakam kullanın (Türkçe harf / boşluk olmaz).'
        return True, v
    if s.kind == 'text' and len(v) > 200:
        return False, f'{s.label}: en çok 200 karakter olabilir.'
    return True, v


def limits_text(s):
    if s.kind in ('int', 'float') and s.lo is not None and s.hi is not None:
        return f'{s.lo:g} – {s.hi:g}' + (f' {s.unit}' if s.unit else '')
    return ''


# ----------------------------------------------------------------------------- kayıt defterini yükle
def _yukle():
    import sys
    import ALFABE_AYAR_LISTE as _L
    CATEGORIES[:] = _L.build(sys.modules[__name__])
    reset_index()


_yukle()
