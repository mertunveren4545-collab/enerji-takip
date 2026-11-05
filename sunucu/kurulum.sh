#!/data/data/com.termux/files/usr/bin/bash
# Enerji Takip — Termux kurulumu (bir kere çalıştırılır)
# Kullanım:
#   curl -sL https://raw.githubusercontent.com/mertunveren4545-collab/enerji-takip/main/sunucu/kurulum.sh | bash
set -e
KAYNAK="https://raw.githubusercontent.com/mertunveren4545-collab/enerji-takip/main/sunucu"
HEDEF="$HOME/enerji"

echo ">> Paketler kuruluyor (birkaç dakika sürebilir)..."
yes | pkg update -y >/dev/null 2>&1 || true
pkg install -y python python-cryptography cloudflared >/dev/null

echo ">> Python kütüphaneleri kuruluyor..."
# C derleyicisi gerekmesin diye saf-Python sürümleri kullan
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 \
       FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
pip install --no-cache-dir -q flask python-kasa qrcode

echo ">> Dosyalar indiriliyor..."
mkdir -p "$HEDEF"
curl -sL "$KAYNAK/sunucu.py" -o "$HEDEF/sunucu.py"
curl -sL "$KAYNAK/baslat.sh" -o "$HEDEF/baslat.sh"
chmod +x "$HEDEF/baslat.sh"

echo
echo "✔ Kurulum bitti. Başlatmak için:"
echo "    bash ~/enerji/baslat.sh"
