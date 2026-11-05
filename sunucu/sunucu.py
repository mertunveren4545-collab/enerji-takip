#!/usr/bin/env python3
"""
Enerji Takip — ev sunucusu (Termux / Android, Linux, Windows)

Tapo L530E ampulü yerel ağdan (python-kasa) yönetir, durumunu
her 30 saniyede kaydeder ve Enerji Takip uygulamasına anahtarlı
bir API sunar. TP-Link bulutunu kullanmaz.

İlk çalıştırmada ayarları sorar ve ayarlar.json'a yazar.
Çalıştırma:  python sunucu.py
"""
import asyncio
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from getpass import getpass

from flask import Flask, jsonify, request

KLASOR = os.path.dirname(os.path.abspath(__file__))
AYAR_DOSYASI = os.path.join(KLASOR, "ayarlar.json")
VERITABANI = os.path.join(KLASOR, "enerji.db")

ORNEK_ARALIGI = 30      # saniye — ampul durumu bu sıklıkla okunur
ORNEK_TAVAN = 90        # saniye — sunucu kapalı kaldıysa aradaki boşluk sayılmaz
VARSAYILAN = {
    "port": 5000,
    "watt": 8.7,        # L530E en yüksek parlaklıkta
    "bekleme_watt": 0.4,  # kapalıyken çektiği
    "kwh_fiyat": 2.95,  # TL / kWh — uygulamadaki fiyatla aynı
}

SAHTE = "--sahte" in sys.argv  # gerçek ampul olmadan test için
UYGULAMA_ADRESI = "https://mertunveren4545-collab.github.io/enerji-takip/"


# ---------------------------------------------------------------- ayarlar
def ayarlari_yukle():
    if os.path.exists(AYAR_DOSYASI):
        with open(AYAR_DOSYASI, encoding="utf-8") as f:
            a = json.load(f)
        for k, v in VARSAYILAN.items():
            a.setdefault(k, v)
        return a
    if SAHTE:
        a = dict(VARSAYILAN, eposta="", sifre="", ip="", mac="",
                 anahtar=secrets.token_urlsafe(18))
        ayarlari_kaydet(a)
        return a
    print("\n=== Enerji Takip sunucusu — ilk kurulum ===\n")
    a = dict(VARSAYILAN)
    a["eposta"] = input("Tapo e-posta: ").strip()
    a["sifre"] = getpass("Tapo şifre (yazarken görünmez): ")
    a["ip"] = input("Ampulün IP adresi (Tapo > ampul > ayarlar > Cihaz Bilgisi): ").strip()
    a["mac"] = ""
    a["anahtar"] = secrets.token_urlsafe(18)
    ayarlari_kaydet(a)
    print("\nAyarlar kaydedildi.")
    return a


def ayarlari_kaydet(a):
    with open(AYAR_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(AYAR_DOSYASI, 0o600)
    except OSError:
        pass


AYAR = ayarlari_yukle()


# ---------------------------------------------------------------- veritabanı
def db():
    c = sqlite3.connect(VERITABANI, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def db_hazirla():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS ornek (ts INTEGER, acik INTEGER, parlaklik INTEGER)")
        c.execute("CREATE INDEX IF NOT EXISTS ornek_ts ON ornek(ts)")
        c.execute("CREATE TABLE IF NOT EXISTS olay (ts INTEGER, tur TEXT, kaynak TEXT, deger INTEGER)")


def ornek_ekle(acik, parlaklik):
    with db() as c:
        c.execute("INSERT INTO ornek VALUES (?,?,?)", (int(time.time()), int(acik), int(parlaklik)))


def olay_ekle(tur, kaynak, deger=None):
    with db() as c:
        c.execute("INSERT INTO olay VALUES (?,?,?,?)", (int(time.time()), tur, kaynak, deger))


def guc_w(acik, parlaklik):
    """Tahmini anlık güç. LED gücü parlaklıkla yaklaşık doğrusal değişir."""
    if not acik:
        return AYAR["bekleme_watt"]
    return max(1.0, AYAR["watt"] * max(1, parlaklik) / 100.0)


def enerji(bas, bit):
    """[bas, bit) aralığında harcanan enerji (kWh) ve açık kalınan süre (saat)."""
    with db() as c:
        satirlar = c.execute(
            "SELECT ts, acik, parlaklik FROM ornek WHERE ts >= ? AND ts < ? ORDER BY ts",
            (bas - ORNEK_TAVAN, bit)).fetchall()
    wh, acik_sn = 0.0, 0
    simdi = min(bit, int(time.time()))
    for i, (ts, acik, par) in enumerate(satirlar):
        son = satirlar[i + 1][0] if i + 1 < len(satirlar) else simdi
        son = min(son, ts + ORNEK_TAVAN)
        a, b = max(ts, bas), min(son, bit)
        if b <= a:
            continue
        wh += guc_w(acik, par) * (b - a) / 3600.0
        if acik:
            acik_sn += b - a
    kwh = wh / 1000.0
    return {"kwh": round(kwh, 4), "saat": round(acik_sn / 3600.0, 2),
            "tl": round(kwh * AYAR["kwh_fiyat"], 2)}


def gun_basi(gun_once=0):
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=gun_once)
    return int(d.timestamp())


# ---------------------------------------------------------------- ampul
class SahteAmpul:
    """Gerçek ampul yokken API'yi denemek için."""
    def __init__(self):
        self.is_on, self.brightness, self.alias = True, 80, "Salon"

    async def update(self):
        pass

    async def turn_on(self):
        self.is_on = True

    async def turn_off(self):
        self.is_on = False


class Ampul:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.cihaz = None
        self.kilit = None
        self.durum = {"cevrimici": False, "acik": False, "parlaklik": 0, "ad": "", "hata": "", "zaman": 0}
        self.calistir(self._kilit_kur())

    async def _kilit_kur(self):
        self.kilit = asyncio.Lock()

    def calistir(self, coro, zaman_asimi=25):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(zaman_asimi)

    # --- bağlantı
    async def _baglan(self):
        if SAHTE:
            self.cihaz = SahteAmpul()
            return
        from kasa import Credentials, Discover
        kimlik = Credentials(AYAR["eposta"], AYAR["sifre"])
        cihaz = None
        if AYAR.get("ip"):
            try:
                cihaz = await Discover.discover_single(AYAR["ip"], credentials=kimlik, timeout=8)
            except Exception:
                cihaz = None
        if cihaz is None:
            # IP değişmiş olabilir: ağı tara, bilinen MAC'i ya da ilk Tapo ışığını bul
            bulunan = await Discover.discover(credentials=kimlik, discovery_timeout=6)
            for ip, d in bulunan.items():
                try:
                    await d.update()
                except Exception:
                    continue
                mac = (getattr(d, "mac", "") or "").upper()
                if (AYAR.get("mac") and mac == AYAR["mac"]) or (not AYAR.get("mac") and self._isik_mi(d)):
                    cihaz = d
                    AYAR["ip"] = ip
                    break
        if cihaz is None:
            raise RuntimeError("Ampul bulunamadı. Aynı WiFi'de mi, IP doğru mu?")
        await cihaz.update()
        mac = (getattr(cihaz, "mac", "") or "").upper()
        if mac and mac != AYAR.get("mac"):
            AYAR["mac"] = mac
            ayarlari_kaydet(AYAR)
        self.cihaz = cihaz

    @staticmethod
    def _isik_mi(d):
        try:
            from kasa import Module
            return Module.Light in d.modules
        except Exception:
            return False

    def _isik(self):
        if SAHTE:
            return self.cihaz
        from kasa import Module
        return self.cihaz.modules.get(Module.Light) if self.cihaz else None

    async def _guncelle(self):
        async with self.kilit:
            for deneme in (1, 2):
                try:
                    if self.cihaz is None:
                        await self._baglan()
                    await self.cihaz.update()
                    isik = self._isik()
                    par = int(getattr(isik, "brightness", 100) or 0) if isik else 100
                    self.durum = {"cevrimici": True, "acik": bool(self.cihaz.is_on),
                                  "parlaklik": par, "ad": getattr(self.cihaz, "alias", "") or "Ampul",
                                  "hata": "", "zaman": int(time.time())}
                    return self.durum
                except Exception as e:
                    await self._kapat_baglanti()
                    if deneme == 2:
                        self.durum = dict(self.durum, cevrimici=False, hata=str(e)[:200])
                        return self.durum

    async def _kapat_baglanti(self):
        try:
            if self.cihaz is not None and hasattr(self.cihaz, "disconnect"):
                await self.cihaz.disconnect()
        except Exception:
            pass
        self.cihaz = None

    async def _guc(self, acik):
        await self._guncelle()
        async with self.kilit:
            if self.cihaz is None:
                raise RuntimeError(self.durum.get("hata") or "Ampule ulaşılamıyor")
            await (self.cihaz.turn_on() if acik else self.cihaz.turn_off())
        return await self._guncelle()

    async def _parlaklik(self, deger):
        await self._guncelle()
        async with self.kilit:
            isik = self._isik()
            if isik is None:
                raise RuntimeError(self.durum.get("hata") or "Ampule ulaşılamıyor")
            if SAHTE:
                isik.brightness, isik.is_on = deger, True
            else:
                await isik.set_brightness(deger)
        return await self._guncelle()

    # --- dışa açık
    def guncelle(self):
        return self.calistir(self._guncelle())

    def guc(self, acik):
        return self.calistir(self._guc(acik))

    def parlaklik(self, deger):
        return self.calistir(self._parlaklik(deger))


AMPUL = Ampul()


SON_BILINEN = {"acik": None}


def ornekleyici():
    """Ampul nereden açılıp kapanırsa kapansın (anahtar, Tapo, bu uygulama) kaydeder."""
    while True:
        try:
            d = AMPUL.guncelle()
            if d["cevrimici"]:
                ornek_ekle(d["acik"], d["parlaklik"])
                onceki = SON_BILINEN["acik"]
                if onceki is not None and onceki != d["acik"]:
                    olay_ekle("ac" if d["acik"] else "kapat", "dis")
                SON_BILINEN["acik"] = d["acik"]
        except Exception as e:
            print("Örnekleme hatası:", e)
        time.sleep(ORNEK_ARALIGI)


# ---------------------------------------------------------------- API
app = Flask(__name__)


@app.after_request
def cors(yanit):
    yanit.headers["Access-Control-Allow-Origin"] = "*"
    yanit.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Anahtar"
    yanit.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    yanit.headers["Access-Control-Max-Age"] = "86400"
    return yanit


@app.before_request
def yetki():
    if request.method == "OPTIONS":
        return ("", 204)
    if request.path.startswith("/api/"):
        gelen = request.headers.get("X-Anahtar", "")
        if not hmac.compare_digest(gelen, AYAR["anahtar"]):
            return jsonify(hata="Anahtar yanlış"), 401


@app.get("/")
def kok():
    return "Enerji Takip sunucusu çalışıyor."


def durum_paketi(d):
    ay_basi = int(datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    simdi = int(time.time()) + 1
    return {
        **d,
        "watt": round(guc_w(d["acik"], d["parlaklik"]), 1) if d["cevrimici"] else 0,
        "bugun": enerji(gun_basi(0), simdi),
        "ay": enerji(ay_basi, simdi),
        "fiyat": AYAR["kwh_fiyat"],
    }


@app.get("/api/durum")
def api_durum():
    return jsonify(durum_paketi(AMPUL.guncelle()))


@app.post("/api/guc")
def api_guc():
    acik = bool((request.get_json(silent=True) or {}).get("acik"))
    try:
        d = AMPUL.guc(acik)
    except Exception as e:
        return jsonify(hata=str(e)[:200]), 502
    olay_ekle("ac" if acik else "kapat", "uygulama")
    ornek_ekle(d["acik"], d["parlaklik"])
    SON_BILINEN["acik"] = d["acik"]
    return jsonify(durum_paketi(d))


@app.post("/api/parlaklik")
def api_parlaklik():
    try:
        deger = int((request.get_json(silent=True) or {}).get("deger"))
    except (TypeError, ValueError):
        return jsonify(hata="deger 1-100 arası olmalı"), 400
    deger = min(100, max(1, deger))
    try:
        d = AMPUL.parlaklik(deger)
    except Exception as e:
        return jsonify(hata=str(e)[:200]), 502
    olay_ekle("parlaklik", "uygulama", deger)
    ornek_ekle(d["acik"], d["parlaklik"])
    SON_BILINEN["acik"] = d["acik"]
    return jsonify(durum_paketi(d))


@app.get("/api/gecmis")
def api_gecmis():
    gun = min(60, max(1, request.args.get("gun", 14, type=int)))
    sonuc = []
    for i in range(gun - 1, -1, -1):
        bas, bit = gun_basi(i), gun_basi(i - 1)
        sonuc.append({"tarih": datetime.fromtimestamp(bas).strftime("%Y-%m-%d"), **enerji(bas, bit)})
    return jsonify(sonuc)


@app.get("/api/olaylar")
def api_olaylar():
    limit = min(100, max(1, request.args.get("limit", 20, type=int)))
    with db() as c:
        satirlar = c.execute("SELECT ts, tur, kaynak, deger FROM olay ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([{"ts": t, "tur": tu, "kaynak": k, "deger": v} for t, tu, k, v in satirlar])


# ---------------------------------------------------------------- başlat
def baglanti_goster(tunel_adresi):
    """Ana telefonda okutulacak kurulum bağlantısı + QR."""
    from urllib.parse import quote
    link = f"{UYGULAMA_ADRESI}#sunucu={quote(tunel_adresi, safe='')}&anahtar={quote(AYAR['anahtar'], safe='')}"
    print("\n" + "=" * 48)
    print("  ANA TELEFONDA BU QR'I OKUT (ya da linki aç):\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(link)
        qr.print_ascii(invert=True)
    except Exception:
        print("  (QR için: pip install qrcode)")
    print("\n  " + link)
    print("=" * 48 + "\n")


if __name__ == "__main__":
    if "--baglanti" in sys.argv:
        baglanti_goster(sys.argv[sys.argv.index("--baglanti") + 1])
        sys.exit(0)
    db_hazirla()
    print("Ampule bağlanılıyor...")
    d = AMPUL.guncelle()
    if d["cevrimici"]:
        print(f"✔ Bağlandı: {d['ad']} — {'AÇIK' if d['acik'] else 'KAPALI'}, parlaklık %{d['parlaklik']}")
    else:
        print("✘ Ampule şimdilik ulaşılamadı:", d["hata"])
        print("  Sunucu yine de açılıyor; her 30 sn'de tekrar denenecek.")
    if "--kurulum" in sys.argv:
        sys.exit(0 if d["cevrimici"] else 1)
    threading.Thread(target=ornekleyici, daemon=True).start()
    print("\n" + "=" * 48)
    print("  UYGULAMA ANAHTARI:", AYAR["anahtar"])
    print("  (Enerji Takip > Canlı ampul > Ayarlar kısmına yapıştır)")
    print("=" * 48 + "\n")
    app.run(host="0.0.0.0", port=int(AYAR["port"]), threaded=True)
