# -*- coding: utf-8 -*-
"""ALFABE - Hızlı belge görüntüleme servisi (Edge/Chrome, DevTools protokolü).

Yazdırma ön izleme ve Liste Fiyatla ön izlemesi her seferinde yeni bir Edge işlemi başlatıyordu (her açılış 2-5 sn).
Bu modül tek bir görünmez (headless) Edge işlemini arka planda hazır tutar; sayfalar aynı işleme DevTools
protokolüyle yaptırılır: açılış beklemesi yoktur, sayfalar tek tek (ilk sayfa hemen) gelir.

Harici kütüphane gerekmez (WebSocket istemcisi burada yazılmıştır). Herhangi bir adımda hata olursa çağıran taraf
eski yönteme (her seferinde ayrı Edge işlemi) döner. Servis yalnızca kendi geçici profil klasörünü kullanır,
kullanıcının açık Edge penceresine/profiline dokunmaz.
"""
import atexit
import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

IDLE_KAPAT_SN = 15 * 60
_HAFIF = ['--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-component-update',
          '--disable-sync', '--disable-default-apps', '--metrics-recording-only', '--mute-audio', '--disable-breakpad',
          '--disable-features=Translate,OptimizationHints,MediaRouter']


class _WS:
    """Gereken en küçük WebSocket istemcisi (RFC 6455): metin çerçevesi gönder / al."""

    def __init__(self, host, port, path, timeout=20):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            ch = self.s.recv(4096)
            if not ch:
                raise ConnectionError('WebSocket el sıkışması başarısız')
            buf += ch
        head, rest = buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0] + b' ':
            raise ConnectionError('WebSocket reddedildi: ' + head.split(b'\r\n')[0].decode('latin1'))
        self.buf = rest

    def _read(self, n):
        while len(self.buf) < n:
            ch = self.s.recv(max(65536, n - len(self.buf)))
            if not ch:
                raise ConnectionError('bağlantı koptu')
            self.buf += ch
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text, op=1):
        data = text.encode('utf-8') if isinstance(text, str) else text
        n = len(data)
        hdr = bytes([0x80 | op])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(data)) if n < 4096 else _xor(data, mask)
        self.s.sendall(hdr + mask + masked)

    def recv(self):
        msg = b''
        while True:
            b0, b1 = self._read(2)
            fin, op = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            if b1 & 0x80:
                mk = self._read(4)
                data = _xor(self._read(n), mk)
            else:
                data = self._read(n)
            if op == 8:
                raise ConnectionError('bağlantı kapandı')
            if op == 9:
                self.send(data, 10)
                continue
            if op == 10:
                continue
            msg += data
            if fin:
                return msg.decode('utf-8', 'replace')

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _xor(data, mask):
    # büyük veri için hızlı XOR (int dönüşümüyle)
    n = len(data)
    if not n:
        return b''
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(m, 'big')).to_bytes(n, 'big')


class EdgeServis:
    def __init__(self, edge_yolu, profil_adi='alfabe_edge_servis'):
        self.edge = str(edge_yolu)
        self.profil = Path(tempfile.gettempdir()) / profil_adi
        self.proc = None
        self.ws = None
        self.sid = None
        self.id = 0
        self.olaylar = []
        self.kilit = threading.RLock()
        self.son = time.time()
        self.hazir = False
        self._bekci = None

    # ---------- yaşam döngüsü ----------
    def baslat(self):
        with self.kilit:
            if self.hazir and self.proc is not None and self.proc.poll() is None:
                self.son = time.time()
                return True
            self._temizle()
            self.profil.mkdir(exist_ok=True)
            pf = self.profil / 'DevToolsActivePort'
            try:
                pf.unlink()
            except OSError:
                pass
            args = [self.edge, '--headless', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--remote-debugging-port=0',
                    f'--user-data-dir={self.profil}', '--default-background-color=FFFFFFFF'] + _HAFIF
            if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0:
                args.append('--no-sandbox')
            self.proc = subprocess.Popen(args + ['about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            port = path = None
            t0 = time.time()
            while time.time() - t0 < 15:
                if self.proc.poll() is not None:
                    raise RuntimeError('Edge servisi başlamadı')
                try:
                    ln = pf.read_text(encoding='utf-8').split('\n')
                    if len(ln) >= 2 and ln[0].strip().isdigit() and ln[1].strip():
                        port, path = int(ln[0]), ln[1].strip()
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            if not port:
                raise RuntimeError('Edge servis portu bulunamadı')
            self.ws = _WS('127.0.0.1', port, path)
            self.olaylar = []
            r = self.cmd('Target.createTarget', {'url': 'about:blank'})
            tid = r['targetId']
            r = self.cmd('Target.attachToTarget', {'targetId': tid, 'flatten': True})
            self.sid = r['sessionId']
            self.cmd('Page.enable', sid=self.sid)
            self.hazir = True
            self.son = time.time()
            if self._bekci is None:
                self._bekci = threading.Thread(target=self._idle_bekle, daemon=True)
                self._bekci.start()
            return True

    def _idle_bekle(self):
        while True:
            time.sleep(60)
            if self.hazir and time.time() - self.son > IDLE_KAPAT_SN and self.kilit.acquire(False):
                try:
                    self._temizle()
                finally:
                    self.kilit.release()

    def _temizle(self):
        self.hazir = False
        self.sid = None
        if self.ws:
            self.ws.close()
            self.ws = None
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except Exception:
                        self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def kapat(self):
        with self.kilit:
            self._temizle()

    # ---------- DevTools çağrıları ----------
    def cmd(self, method, params=None, sid=None, timeout=30):
        self.id += 1
        i = self.id
        m = {'id': i, 'method': method, 'params': params or {}}
        if sid:
            m['sessionId'] = sid
        self.ws.send(json.dumps(m))
        t0 = time.time()
        while True:
            if time.time() - t0 > timeout:
                raise TimeoutError(method)
            d = json.loads(self.ws.recv())
            if d.get('id') == i:
                if 'error' in d:
                    raise RuntimeError(f"{method}: {d['error'].get('message')}")
                return d.get('result', {})
            if 'method' in d:
                self.olaylar.append(d)

    def _yukle(self, url, timeout=30):
        self.olaylar = []
        self.cmd('Page.navigate', {'url': url}, sid=self.sid)
        t0 = time.time()
        while True:
            for e in self.olaylar:
                if e.get('method') == 'Page.loadEventFired' and e.get('sessionId') == self.sid:
                    return
            if time.time() - t0 > timeout:
                raise TimeoutError('sayfa yüklenemedi')
            d = json.loads(self.ws.recv())           # bağlantı zaman aşımı (20 sn) aşılırsa hata verir, çağıran eski yönteme döner
            if 'method' in d:
                self.olaylar.append(d)

    def _olc(self, w_px, h_px, scale):
        self.cmd('Emulation.setDeviceMetricsOverride', {'width': int(w_px), 'height': int(h_px), 'deviceScaleFactor': float(scale), 'mobile': False}, sid=self.sid)

    def _yuk_cm(self):
        r = self.cmd('Runtime.evaluate', {'expression': 'Math.max(document.documentElement.scrollHeight,document.body?document.body.scrollHeight:0)', 'returnByValue': True}, sid=self.sid)
        return int(r.get('result', {}).get('value') or 0)

    def _kare(self, x, y, w, h, out):
        r = self.cmd('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True, 'fromSurface': True,
                                                'clip': {'x': float(x), 'y': float(y), 'width': float(w), 'height': float(h), 'scale': 1}}, sid=self.sid, timeout=60)
        Path(out).write_bytes(base64.b64decode(r['data']))
        return str(out)

    # ---------- kullanım ----------
    def sayfalar(self, html_yolu, win_w, page_h, scale, klasor, her_sayfa):
        """html (zorla sayfa boyutlandırılmış) belgeyi yükler; sayfa sayısını döndürür ve her sayfa için
        her_sayfa(k, n, png_yolu) çağırır (ilk sayfa hemen). Dönüş: sayfa sayısı."""
        with self.kilit:
            self.baslat()
            self.son = time.time()
            self._olc(win_w + 1, page_h, scale)
            self._yukle(Path(html_yolu).resolve().as_uri())
            h = self._yuk_cm()
            n = max(1, min(300, int((h - 2) // page_h) + (1 if (h - 2) % page_h > 1 else 0)))
            for k in range(n):
                png = self._kare(0, k * page_h, win_w + 1, page_h, Path(klasor) / f'p{k}.png')
                her_sayfa(k, n, png)
            self.son = time.time()
            return n

    def ekran(self, html_yolu, w_px, h_px, scale, out):
        with self.kilit:
            self.baslat()
            self.son = time.time()
            self._olc(w_px, h_px, scale)
            self._yukle(Path(html_yolu).resolve().as_uri())
            p = self._kare(0, 0, w_px, h_px, out)
            self.son = time.time()
            return p

    def pdf(self, html_yolu, out, landscape=False):
        with self.kilit:
            self.baslat()
            self._yukle(Path(html_yolu).resolve().as_uri())
            r = self.cmd('Page.printToPDF', {'landscape': bool(landscape), 'printBackground': True, 'preferCSSPageSize': True,
                                             'displayHeaderFooter': False}, sid=self.sid, timeout=90)
            Path(out).write_bytes(base64.b64decode(r['data']))
            self.son = time.time()
            return str(out)


_SERVIS = {}


def servis(edge_yolu):
    s = _SERVIS.get(str(edge_yolu))
    if s is None:
        s = _SERVIS[str(edge_yolu)] = EdgeServis(edge_yolu)
    return s


def hepsini_kapat():
    for s in list(_SERVIS.values()):
        try:
            s.kapat()
        except Exception:
            pass


atexit.register(hepsini_kapat)
