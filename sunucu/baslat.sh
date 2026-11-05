#!/data/data/com.termux/files/usr/bin/bash
# Enerji Takip — sunucuyu ve tüneli başlatır
cd "$HOME/enerji" || exit 1

# Telefon uyusa bile Termux çalışmaya devam etsin
termux-wake-lock 2>/dev/null

# Eski kopyaları kapat
pkill -f "python sunucu.py" 2>/dev/null
pkill -f "cloudflared tunnel" 2>/dev/null
sleep 1

# İlk kurulum: ayarları sor ve ampule bağlanmayı dene
if [ ! -f ayarlar.json ]; then
  if ! python sunucu.py --kurulum; then
    echo
    echo "Ampule bağlanılamadı. IP'yi kontrol edip tekrar dene:"
    echo "    rm ~/enerji/ayarlar.json && bash ~/enerji/baslat.sh"
    exit 1
  fi
fi

echo ">> Sunucu başlatılıyor..."
python sunucu.py > sunucu.log 2>&1 &
sleep 4
if ! curl -s http://localhost:5000/ >/dev/null; then
  echo "Sunucu açılmadı. Hata:"
  tail -n 20 sunucu.log
  exit 1
fi
grep -E "Bağlandı|ulaşılamadı" sunucu.log

echo ">> Dış bağlantı (Cloudflare) açılıyor..."
cloudflared tunnel --no-autoupdate --url http://localhost:5000 > tunel.log 2>&1 &
ADRES=""
for i in $(seq 1 30); do
  ADRES=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunel.log | head -n 1)
  [ -n "$ADRES" ] && break
  sleep 1
done
if [ -z "$ADRES" ]; then
  echo "Tünel açılamadı. İnternet bağlantısını kontrol et:"
  tail -n 15 tunel.log
  exit 1
fi

python sunucu.py --baglanti "$ADRES"
echo "Sunucu çalışıyor. Bu ekranı kapatma; Termux'u arka plana alabilirsin."
echo "Durdurmak için: Ctrl+C"
wait
