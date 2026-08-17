#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MGM (Meteoroloji Genel Müdürlüğü) - il geneli anlık sondurum verisi çekici.

Tek bir istekle (servis.mgm.gov.tr/web/sondurumlar/ilTumSondurum?ilPlaka=N)
o ildeki TÜM aktif istasyonların anlık verisini alır ve yerel bir referans
listesiyle (il, ilçe, istasyon adı) birleştirip okunabilir bir tabloya
dönüştürür.

Kullanım:
    pip install requests
    python3 mgm_sondurum.py
    python3 mgm_sondurum.py --il Ankara --csv ankara_sondurum.csv
    python3 mgm_sondurum.py --ilplaka 34 --il Istanbul --csv istanbul_sondurum.csv

Referans dosya (varsayılan: mgm_tum_istasyonlar.csv) il, ilce, istasyon,
merkezId (=istNo) sütunlarını içermeli. Bu dosya olmadan da çalışır ama
istasyon adı/ilçe bilgisi olmadan sadece istNo ile listelenir.
"""

import argparse
import csv
import random
import sys
import time

import requests

HEADERS = {
    "Host": "servis.mgm.gov.tr",
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.mgm.gov.tr",
    "Referer": "https://www.mgm.gov.tr/",
}

CONDITION_CODES = {
    "PB": "Parçalı Bulutlu", "GSY": "Gökgürültülü Sağanak Yağışlı",
    "HSY": "Hafif Sağanak Yağışlı", "SY": "Sağanak Yağışlı", "A": "Açık",
    "AB": "Az Bulutlu", "CB": "Çok Bulutlu", "D": "Duman",
    "HY": "Hafif Yağmurlu", "HKY": "Hafif Kar Yağışlı",
    "MSY": "Yer Yer Sağanak Yağışlı", "KKY": "Karla Karışık Yağmurlu",
    "GKR": "Güneyli Kuvvetli Rüzgar", "SCK": "Sıcak", "PUS": "Pus",
    "Y": "Yağmurlu", "K": "Kar Yağışlı", "DY": "Dolu", "R": "Rüzgarlı",
    "KKR": "Kuzeyli Kuvvetli Rüzgar", "SGK": "Soğuk", "SIS": "Sis",
    "KY": "Kuvvetli Yağmurlu", "KSY": "Kuvvetli Sağanak Yağışlı",
    "YKY": "Yoğun Kar Yağışlı", "KF": "Toz veya Kum Fırtınası",
    "KGY": "Kuvvetli Gökgürültülü Sağanak Yağışlı",
}

# Standart il plaka kodları (81 il)
IL_PLAKA = {
    "Adana": 1, "Adıyaman": 2, "Afyonkarahisar": 3, "Ağrı": 4, "Amasya": 5,
    "Ankara": 6, "Antalya": 7, "Artvin": 8, "Aydın": 9, "Balıkesir": 10,
    "Bilecik": 11, "Bingöl": 12, "Bitlis": 13, "Bolu": 14, "Burdur": 15,
    "Bursa": 16, "Çanakkale": 17, "Çankırı": 18, "Çorum": 19, "Denizli": 20,
    "Diyarbakır": 21, "Edirne": 22, "Elazığ": 23, "Erzincan": 24,
    "Erzurum": 25, "Eskişehir": 26, "Gaziantep": 27, "Giresun": 28,
    "Gümüşhane": 29, "Hakkari": 30, "Hatay": 31, "Isparta": 32,
    "Mersin": 33, "İstanbul": 34, "İzmir": 35, "Kars": 36, "Kastamonu": 37,
    "Kayseri": 38, "Kırklareli": 39, "Kırşehir": 40, "Kocaeli": 41,
    "Konya": 42, "Kütahya": 43, "Malatya": 44, "Manisa": 45,
    "Kahramanmaraş": 46, "Mardin": 47, "Muğla": 48, "Muş": 49,
    "Nevşehir": 50, "Niğde": 51, "Ordu": 52, "Rize": 53, "Sakarya": 54,
    "Samsun": 55, "Siirt": 56, "Sinop": 57, "Sivas": 58, "Tekirdağ": 59,
    "Tokat": 60, "Trabzon": 61, "Tunceli": 62, "Şanlıurfa": 63, "Uşak": 64,
    "Van": 65, "Yozgat": 66, "Zonguldak": 67, "Aksaray": 68, "Bayburt": 69,
    "Karaman": 70, "Kırıkkale": 71, "Batman": 72, "Şırnak": 73,
    "Bartın": 74, "Ardahan": 75, "Iğdır": 76, "Yalova": 77, "Karabük": 78,
    "Kilis": 79, "Osmaniye": 80, "Düzce": 81,
}


def clean(value):
    """MGM'nin 'veri yok' kodu -9999'u okunabilir boşluğa çevirir."""
    if value in (-9999, "-9999", -9999.0):
        return ""
    return value


def get_il_tum_sondurum(plaka: int, retries: int = 3) -> dict:
    """{istNo: kayit} sözlüğü döner - o ildeki TÜM aktif istasyonlar, tek istek.
    Zaman aşımı/geçici ağ hatalarında artan bekleme ile yeniden dener."""
    url = f"https://servis.mgm.gov.tr/web/sondurumlar/ilTumSondurum?ilPlaka={plaka}"
    last_error = None
    for attempt in range(1, retries + 1):
        timeout = 20 * attempt  # 20s, 40s, 60s
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return {int(rec["istNo"]): rec for rec in data if rec.get("istNo") is not None}
        except requests.RequestException as e:
            last_error = e
            if attempt < retries:
                wait = 5 * attempt + random.uniform(0, 3)
                print(f"    [uyarı] deneme {attempt}/{retries} başarısız ({e}), {wait:.1f}sn sonra tekrar denenecek...", file=sys.stderr)
                time.sleep(wait)
    raise last_error


def process_il(il: str, plaka: int, reference_all: dict) -> list:
    """Bir il için sondurum verisini çeker ve satır listesi döner (yazdırma yapmaz)."""
    try:
        sondurum_by_istno = get_il_tum_sondurum(plaka)
    except requests.RequestException as e:
        print(f"  [HATA] {il}: veri çekilemedi: {e}", file=sys.stderr)
        return []

    ref_by_istno = reference_all.get(il, {})
    ist_nos = list(ref_by_istno.keys()) if ref_by_istno else list(sondurum_by_istno.keys())

    rows = []
    for ist_no in ist_nos:
        sd = sondurum_by_istno.get(ist_no, {})
        ref = ref_by_istno.get(ist_no, {})
        if not sd:
            continue

        kod = sd.get("hadiseKodu")
        label = ref.get("istasyon") or str(ist_no)
        ilce = ref.get("ilce") or ""

        rows.append({
            "il": il,
            "ilce": ilce,
            "istasyon": label,
            "istNo": ist_no,
            "veriZamani_UTC": sd.get("veriZamani"),
            "hadise": CONDITION_CODES.get(kod, kod),
            "sicaklik_C": clean(sd.get("sicaklik")),
            "hissedilenSicaklik_C": clean(sd.get("hissedilenSicaklik")),
            "nem_%": clean(sd.get("nem")),
            "ruzgarYonu_derece": clean(sd.get("ruzgarYon")),
            "ruzgarHiz_kmh": clean(sd.get("ruzgarHiz")),
            "aktuelBasinc_hPa": clean(sd.get("aktuelBasinc")),
            "denizeIndirgenmisBasinc_hPa": clean(sd.get("denizeIndirgenmisBasinc")),
            "gorus_m": clean(sd.get("gorus")),
            "kapalilik_okta": clean(sd.get("kapalilik")),
            "yagis10dk_mm": clean(sd.get("yagis10Dk")),
            "yagis00danSuana_mm": clean(sd.get("yagis00Now")),
            "yagis1saat_mm": clean(sd.get("yagis1Saat")),
            "yagis6saat_mm": clean(sd.get("yagis6Saat")),
            "yagis12saat_mm": clean(sd.get("yagis12Saat")),
            "yagis24saat_mm": clean(sd.get("yagis24Saat")),
            "karYukseklik_cm": clean(sd.get("karYukseklik")),
            "denizSicaklik_C": clean(sd.get("denizSicaklik")),
        })
    return rows


def load_reference_all(path: str) -> dict:
    """{il: {istNo: satir}} şeklinde TÜM illeri tek seferde yükler."""
    result = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                il = row.get("il")
                try:
                    ist_no = int(row["merkezId"])
                except (KeyError, ValueError):
                    continue
                result.setdefault(il, {})[ist_no] = row
    except FileNotFoundError:
        print(f"  [uyarı] Referans dosya bulunamadı: {path}", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description="MGM il geneli anlık sondurum çekici (tek istek/il)")
    parser.add_argument("--il", default="Ankara", help="İl adı (varsayılan: Ankara)")
    parser.add_argument("--ilplaka", type=int, default=None, help="İl plaka kodu (verilmezse --il'den bulunur)")
    parser.add_argument(
        "--tum-turkiye", action="store_true",
        help="Tüm 81 ili sırayla çeker ve tek CSV'de birleştirir (--il yok sayılır)"
    )
    parser.add_argument(
        "--reference", default="mgm_tum_istasyonlar.csv",
        help="İstasyon adı/ilçe referans CSV dosyası (varsayılan: mgm_tum_istasyonlar.csv)"
    )
    parser.add_argument("--csv", default=None, help="Sonuçları CSV dosyasına yaz")
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="İller arası ORTALAMA bekleme (sn), sadece --tum-turkiye ile. "
             "Gerçek bekleme ±%%40 rastgele değişir. Varsayılan: 3.0"
    )
    args = parser.parse_args()

    def bekle(ortalama: float):
        """Sabit aralık yerine hafif rastgele (jitter'lı) bekleme yapar."""
        sure = ortalama * random.uniform(0.6, 1.4)
        time.sleep(sure)

    reference_all = load_reference_all(args.reference)

    all_rows = []

    if args.tum_turkiye:
        print(f"Tüm 81 il sırayla çekiliyor (aralarda {args.delay}sn bekleme)...\n", file=sys.stderr)
        basarisiz = []
        for il, plaka in IL_PLAKA.items():
            print(f"[{plaka:2d}/81] {il}...", file=sys.stderr)
            rows = process_il(il, plaka, reference_all)
            print(f"        {len(rows)} istasyon alındı.", file=sys.stderr)
            if not rows:
                basarisiz.append((il, plaka))
            all_rows.extend(rows)
            bekle(args.delay)

        if basarisiz:
            print(f"\n{len(basarisiz)} il için veri alınamadı, tekrar deneniyor: {[b[0] for b in basarisiz]}", file=sys.stderr)
            hala_basarisiz = []
            for il, plaka in basarisiz:
                print(f"  [tekrar] {il}...", file=sys.stderr)
                rows = process_il(il, plaka, reference_all)
                print(f"           {len(rows)} istasyon alındı.", file=sys.stderr)
                if not rows:
                    hala_basarisiz.append(il)
                all_rows.extend(rows)
                bekle(args.delay)
            if hala_basarisiz:
                print(f"\n[uyarı] Bu iller hâlâ alınamadı: {hala_basarisiz}", file=sys.stderr)

        print(f"\nToplam: {len(all_rows)} istasyon, 81 il.", file=sys.stderr)
    else:
        plaka = args.ilplaka if args.ilplaka is not None else IL_PLAKA.get(args.il)
        if plaka is None:
            print(f"HATA: '{args.il}' için plaka kodu bilinmiyor, --ilplaka ile elle verin.", file=sys.stderr)
            sys.exit(1)
        print(f"{args.il} (plaka {plaka}) için tüm istasyonlar tek istekte çekiliyor...", file=sys.stderr)
        all_rows = process_il(args.il, plaka, reference_all)
        for row in all_rows:
            ilce, label = row["ilce"], row["istasyon"]
            label_disp = f"{ilce}/{label}" if ilce and ilce != label else label
            yagis = row["yagis00danSuana_mm"]
            yagis_str = f"{yagis}mm" if yagis != "" else "-"
            print(
                f"  {label_disp:40s} {row['sicaklik_C']!s:>6} °C  "
                f"nem:{row['nem_%']!s:>4}%  rüzgar:{row['ruzgarHiz_kmh']!s:>6} km/sa  "
                f"yağış:{yagis_str:>7}"
            )
        print(f"\n{len(all_rows)} istasyon alındı.", file=sys.stderr)

    if args.csv and all_rows:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"'{args.csv}' dosyasına yazıldı.", file=sys.stderr)


if __name__ == "__main__":
    main()
