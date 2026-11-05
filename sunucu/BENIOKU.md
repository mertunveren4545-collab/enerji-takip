# Enerji Takip — ev sunucusu

Tapo L530E ampulü evdeki yedek Android telefondan (Termux) yönetir ve
Enerji Takip uygulamasına canlı kontrol + gerçek tüketim verisi sağlar.
TP-Link bulutunu kullanmaz; ampulle yerel ağdan konuşur.

## Kurulum (yedek telefonda, Termux'ta, bir kere)

    curl -sL https://raw.githubusercontent.com/mertunveren4545-collab/enerji-takip/main/sunucu/kurulum.sh | bash

## Başlatma

    bash ~/enerji/baslat.sh

İlk seferde Tapo e-posta/şifre ve ampulün IP'sini sorar. Sonunda bir QR kod
gösterir: ana telefonla okutunca uygulama kendini bağlar.

## Dosyalar
- `sunucu.py` — Flask API + python-kasa + SQLite kayıt (30 sn'de bir)
- `baslat.sh` — sunucuyu ve Cloudflare tünelini açar, QR'ı gösterir
- `kurulum.sh` — Termux paketleri ve kütüphaneler

Ayarlar `~/enerji/ayarlar.json`, kayıtlar `~/enerji/enerji.db` içinde durur.
