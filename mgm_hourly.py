#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mgm_hourly.py - MGM Türkiye geneli anlık sondurum + Yaş Termometre Sıcaklığı
işini saat başı (veya istenen sıklıkta) çalıştırmak için tasarlanmış script.

Yapılanlar:
  1. mgm_sondurum.py --tum-turkiye ile tüm 81 ilin anlık verisini çeker
  2. Her istasyon için Wet Bulb Temperature (Stull 2011) hesaplar
  3. İki dosya üretir:
       - turkiye_sondurum_wetbulb_latest.csv  (her çalıştırmada üzerine yazılır,
         en güncel anlık görüntü)
       - turkiye_sondurum_wetbulb_log.csv     (append-only zaman serisi;
         her satırda hangi çalıştırmaya ait olduğunu gösteren 'calismaZamani'
         sütunu vardır -- büyür, zamanla kırpma/arşivleme gerekebilir)

Kullanım (elle test için):
    python3 mgm_hourly.py

Cron ile saat başı otomatik çalıştırma için kurulum talimatları scriptin
sonundaki mesajda / sohbet cevabında verilmiştir.
"""

import csv
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Ayarlar -----------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
MGM_SONDURUM_SCRIPT = SCRIPT_DIR / "mgm_sondurum.py"
REFERENCE_CSV = SCRIPT_DIR / "mgm_tum_istasyonlar.csv"
RAW_TMP_CSV = SCRIPT_DIR / "_tmp_turkiye_sondurum.csv"
LATEST_CSV = SCRIPT_DIR / "turkiye_sondurum_wetbulb_latest.csv"
LOG_CSV = SCRIPT_DIR / "turkiye_sondurum_wetbulb_log.csv"
DELAY = "3"  # mgm_sondurum.py --delay değeri (iller arası ortalama bekleme, sn)
# ------------------------------------------------------------------------


def wet_bulb_stull(T: float, RH: float) -> float:
    """Stull (2011) yaklaşık ıslak termometre sıcaklığı formülü (°C, %RH)."""
    return (
        T * math.atan(0.151977 * math.sqrt(RH + 8.313659))
        + math.atan(T + RH)
        - math.atan(RH - 1.676331)
        + 0.00391838 * (RH ** 1.5) * math.atan(0.023101 * RH)
        - 4.686035
    )


def fetch_all_turkey():
    """mgm_sondurum.py --tum-turkiye çalıştırır, geçici CSV üretir."""
    cmd = [
        sys.executable, str(MGM_SONDURUM_SCRIPT),
        "--tum-turkiye",
        "--reference", str(REFERENCE_CSV),
        "--csv", str(RAW_TMP_CSV),
        "--delay", DELAY,
    ]
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Çekiliyor: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    # mgm_sondurum.py ilerleme/uyarı mesajlarını stderr'e yazıyor, log'a düşsün
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"mgm_sondurum.py başarısız (kod {result.returncode})")
    if not RAW_TMP_CSV.exists():
        raise RuntimeError("Beklenen çıktı dosyası oluşmadı.")


def add_wetbulb_and_save():
    """Geçici CSV'yi okur, yaş termometre sütunu ekler, latest+log dosyalarını yazar."""
    calisma_zamani = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(RAW_TMP_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    out_rows = []
    for row in rows:
        tw = ""
        try:
            T = float(row.get("sicaklik_C", ""))
            RH = float(row.get("nem_%", ""))
            if -20 <= T <= 50 and 5 <= RH <= 99:
                tw = round(wet_bulb_stull(T, RH), 2)
        except (ValueError, TypeError):
            pass
        new_row = dict(row)
        new_row["yasTermometreSicakligi_C"] = tw
        new_row["calismaZamani_UTC"] = calisma_zamani
        out_rows.append(new_row)

    if not out_rows:
        raise RuntimeError("İşlenecek satır yok, dosyalar güncellenmedi.")

    fieldnames = list(out_rows[0].keys())

    # 1) En güncel anlık görüntü -- her seferinde üzerine yazılır
    with open(LATEST_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    # 2) Zaman serisi log -- var olan dosyaya EKLENİR (header sadece ilk seferde)
    log_exists = LOG_CSV.exists()
    with open(LOG_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not log_exists:
            writer.writeheader()
        writer.writerows(out_rows)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] Tamamlandı: "
          f"{len(out_rows)} istasyon -> {LATEST_CSV.name} (güncellendi), "
          f"{LOG_CSV.name} (eklendi)")


def main():
    try:
        fetch_all_turkey()
        add_wetbulb_and_save()
    finally:
        if RAW_TMP_CSV.exists():
            RAW_TMP_CSV.unlink()


if __name__ == "__main__":
    main()
