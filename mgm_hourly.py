#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mgm_hourly.py - MGM Turkiye geneli anlik sondurum + Islak Termometre Sicakligi
+ interpolasyonlu harita + e-posta ile JPG gonderimi.

Akis:
  1. mgm_sondurum.py --tum-turkiye ile tum 81 ilin anlik verisini ceker
  2. Her istasyon icin Islak Termometre Sicakligi (Stull 2011) hesaplar
  3. turkiye_sondurum_wetbulb_latest.csv ve ...log.csv dosyalarini gunceller
  4. istasyon_koordinatlari.csv ile birlestirip IDW enterpolasyonu yapar
  5. turkey_boundary_fine.geojson ile Turkiye siniri disini maskeler
  6. Saglik bantli renk skalasiyla haritayi cizer, JPG olarak kaydeder
  7. JPG'yi e-posta ekinde gonderir (SMTP bilgileri ortam degiskenlerinden)

Gerekli ortam degiskenleri (GitHub Secrets uzerinden saglanir):
  SMTP_USERNAME   - gonderen e-posta adresi (orn. Gmail adresi)
  SMTP_PASSWORD   - Gmail "Uygulama Sifresi" (normal sifre degil!)
  EMAIL_TO        - alici e-posta adresi (virgulle birden fazla verilebilir)
  SMTP_SERVER     - varsayilan: smtp.gmail.com
  SMTP_PORT       - varsayilan: 587

Bu degiskenler tanimli degilse script haritayi uretir ama e-posta adimini
sessizce atlar (yerel test icin).
"""

import csv
import json
import math
import os
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import shape

# --- Ayarlar -----------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
MGM_SONDURUM_SCRIPT = SCRIPT_DIR / "mgm_sondurum.py"
REFERENCE_CSV = SCRIPT_DIR / "mgm_tum_istasyonlar.csv"
COORDS_CSV = SCRIPT_DIR / "istasyon_koordinatlari.csv"
BOUNDARY_GEOJSON = SCRIPT_DIR / "turkey_boundary_fine.geojson"

RAW_TMP_CSV = SCRIPT_DIR / "_tmp_turkiye_sondurum.csv"
LATEST_CSV = SCRIPT_DIR / "turkiye_sondurum_wetbulb_latest.csv"
LOG_CSV = SCRIPT_DIR / "turkiye_sondurum_wetbulb_log.csv"
MAP_JPG = SCRIPT_DIR / "turkiye_islak_termometre_latest.jpg"

DELAY = "3"
# ------------------------------------------------------------------------


def wet_bulb_stull(T: float, RH: float) -> float:
    return (
        T * math.atan(0.151977 * math.sqrt(RH + 8.313659))
        + math.atan(T + RH)
        - math.atan(RH - 1.676331)
        + 0.00391838 * (RH ** 1.5) * math.atan(0.023101 * RH)
        - 4.686035
    )


def clean(value):
    if value in (-9999, "-9999", -9999.0):
        return ""
    return value


def fetch_all_turkey():
    cmd = [
        sys.executable, str(MGM_SONDURUM_SCRIPT),
        "--tum-turkiye",
        "--reference", str(REFERENCE_CSV),
        "--csv", str(RAW_TMP_CSV),
        "--delay", DELAY,
    ]
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Cekiliyor...")
    result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"mgm_sondurum.py basarisiz (kod {result.returncode})")
    if not RAW_TMP_CSV.exists():
        raise RuntimeError("Beklenen cikti dosyasi olusmadi.")


def add_wetbulb_and_save():
    calisma_zamani = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(RAW_TMP_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

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
        new_row["islakTermometreSicakligi_C"] = tw
        new_row["calismaZamani_UTC"] = calisma_zamani
        out_rows.append(new_row)

    if not out_rows:
        raise RuntimeError("Islenecek satir yok.")

    fieldnames = list(out_rows[0].keys())
    with open(LATEST_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    log_exists = LOG_CSV.exists()
    with open(LOG_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not log_exists:
            writer.writeheader()
        writer.writerows(out_rows)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] "
          f"{len(out_rows)} istasyon -> latest + log guncellendi")
    return out_rows, calisma_zamani


def build_map(out_rows, calisma_zamani: str) -> Path:
    """Islak termometre verisini koordinatlarla birlestirir, IDW enterpolasyonu
    yapar, Turkiye siniriyla maskeler, JPG olarak kaydeder."""
    import plotly.graph_objects as go

    coords_by_id = {}
    with open(COORDS_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            coords_by_id[int(row["istNo"])] = row

    lats, lons, vals = [], [], []
    for row in out_rows:
        try:
            ist_no = int(row["istNo"])
        except (KeyError, ValueError):
            continue
        coord = coords_by_id.get(ist_no)
        if not coord:
            continue
        tw = row.get("islakTermometreSicakligi_C", "")
        if tw in ("", None):
            continue
        try:
            lats.append(float(coord["lat"]))
            lons.append(float(coord["lon"]))
            vals.append(float(tw))
        except ValueError:
            continue

    if len(vals) < 20:
        raise RuntimeError(f"Harita icin yeterli istasyon yok ({len(vals)}).")

    lats = np.array(lats); lons = np.array(lons); vals = np.array(vals)
    print(f"Harita icin {len(vals)} istasyon kullanilacak "
          f"(T_islak: {vals.min():.1f} - {vals.max():.1f})")

    with open(BOUNDARY_GEOJSON, encoding="utf-8") as f:
        boundary = shape(json.load(f)["geometry"])

    lon_min, lon_max = 25.5, 45.0
    lat_min, lat_max = 35.7, 42.3
    res = 0.05  # CI'da hizli calissin diye biraz daha kaba (yerelde 0.04 kullanmistik)
    grid_lon = np.arange(lon_min, lon_max, res)
    grid_lat = np.arange(lat_min, lat_max, res)
    glon, glat = np.meshgrid(grid_lon, grid_lat)

    tree = cKDTree(np.column_stack([lons, lats]))
    flat_lon = glon.ravel(); flat_lat = glat.ravel()
    grid_points = np.column_stack([flat_lon, flat_lat])

    k = 10
    dist, idx = tree.query(grid_points, k=k)
    dist = np.where(dist == 0, 1e-6, dist)
    weights = 1.0 / (dist ** 2)
    weights_sum = weights.sum(axis=1, keepdims=True)
    interp_vals = (weights * vals[idx]).sum(axis=1) / weights_sum.ravel()
    grid_z = interp_vals.reshape(glon.shape)

    inside = contains_xy(boundary, flat_lon, flat_lat).reshape(glon.shape)
    grid_z_masked = np.where(inside, grid_z, np.nan)

    zmin, zmax = 8, 36
    span = zmax - zmin
    def pos(v):
        return (v - zmin) / span

    colorscale = [
        [pos(zmin), "#1a3a6b"], [pos(15), "#2c6b8f"], [pos(22), "#4fae94"],
        [pos(24), "#f2d24a"], [pos(26), "#e8862e"], [pos(28), "#cc5a1e"],
        [pos(30), "#c02d22"], [pos(32), "#7a1414"], [pos(35), "#3d0a0a"],
        [pos(zmax), "#3d0a0a"],
    ]

    fig = go.Figure()
    fig.add_trace(go.Contour(
        x=glon[0, :], y=glat[:, 0], z=grid_z_masked,
        colorscale=colorscale, zmin=zmin, zmax=zmax,
        contours=dict(coloring="fill", showlines=True),
        line=dict(width=0.3, color="rgba(255,255,255,0.2)"),
        colorbar=dict(
            title=dict(text="Islak Termometre<br>Sicakligi (\u00b0C)", side="right"),
            thickness=20, len=0.8,
            tickvals=[10, 20, 24, 26, 28, 30, 32, 35],
            ticktext=["10", "20", "24", "26", "28", "30", "32", "35+"],
        ),
        connectgaps=False,
    ))
    for poly in boundary.geoms:
        if poly.area < 0.0005:
            continue
        x, y = poly.exterior.xy
        fig.add_trace(go.Scatter(
            x=list(x), y=list(y), mode="lines",
            line=dict(color="rgba(30,30,30,0.75)", width=1.1),
            showlegend=False, hoverinfo="skip",
        ))

    dt_local = datetime.fromisoformat(calisma_zamani)
    zaman_str = dt_local.strftime("%d %B %Y, %H:%M UTC")
    fig.update_layout(
        title=dict(
            text=(f"Turkiye \u2014 Islak Termometre Sicakligi<br>"
                  f"<sub>{len(vals)} istasyondan IDW enterpolasyonu \u00b7 {zaman_str}<br>"
                  f"&lt;24\u00b0C sorunsuz \u00b7 24-26 hafif stres \u00b7 26-28 riskli aktivite \u00b7 "
                  f"28-30 tehlikeli \u00b7 30-32 ciddi tehlike \u00b7 32-35 \u00e7ok ciddi/\u00f6l\u00fcmc\u00fcl risk</sub>"),
            x=0.5, xanchor="center", font=dict(size=17, family="Georgia, serif"),
        ),
        font=dict(family="Helvetica, Arial, sans-serif"),
        xaxis=dict(range=[25.5, 45], showgrid=False, zeroline=False,
                   scaleanchor="y", scaleratio=1),
        yaxis=dict(range=[35.7, 42.3], showgrid=False, zeroline=False),
        margin=dict(l=60, r=20, t=110, b=50),
        height=900, width=1200,
        plot_bgcolor="white", paper_bgcolor="white",
    )

    fig.write_image(str(MAP_JPG), scale=2)
    print(f"Harita kaydedildi: {MAP_JPG.name}")
    return MAP_JPG


def build_hot_stations_text(out_rows, threshold: float = 28.0) -> str:
    """Islak termometresi esigin ustunde olan istasyonlari, yuksekten dusuge
    siralanmis okunabilir bir metin listesi olarak dondurur."""
    hot = []
    for row in out_rows:
        tw = row.get("islakTermometreSicakligi_C", "")
        if tw in ("", None):
            continue
        try:
            tw_f = float(tw)
        except ValueError:
            continue
        if tw_f > threshold:
            hot.append((tw_f, row.get("il", ""), row.get("ilce", ""), row.get("istasyon", "")))

    hot.sort(key=lambda x: x[0], reverse=True)

    if not hot:
        return f"\n{threshold:.0f}\u00b0C \u00fczerinde islak termometre sicakligi olan istasyon yok.\n"

    lines = [f"\n{threshold:.0f}\u00b0C \u00fczerindeki istasyonlar ({len(hot)} adet), y\u00fcksekten d\u00fc\u015f\u00fc\u011fe:\n"]
    for tw_f, il, ilce, istasyon in hot:
        etiket = f"{ilce}/{istasyon}" if ilce and ilce != istasyon else istasyon
        lines.append(f"  {tw_f:5.1f}\u00b0C  \u2014  {il}, {etiket}")
    return "\n".join(lines) + "\n"


def send_email(jpg_path: Path, calisma_zamani: str, extra_text: str = ""):
    smtp_user = os.environ.get("SMTP_USERNAME")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    email_to = os.environ.get("EMAIL_TO")

    if not (smtp_user and smtp_pass and email_to):
        print("SMTP bilgileri tanimli degil, e-posta gonderimi atlaniyor.")
        return

    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    recipients = [addr.strip() for addr in email_to.split(",") if addr.strip()]

    dt_local = datetime.fromisoformat(calisma_zamani)
    zaman_str = dt_local.strftime("%d %B %Y, %H:%M UTC")

    msg = MIMEMultipart()
    msg["Subject"] = f"Turkiye Islak Termometre Haritasi \u2014 {zaman_str}"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    body = (
        f"Ekte {zaman_str} itibariyla guncellenen Turkiye islak termometre "
        f"sicakligi haritasi bulunmaktadir.\n"
        f"{extra_text}\n"
        f"Bu e-posta otomatik olarak GitHub Actions uzerinden gonderilmistir."
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(jpg_path, "rb") as f:
        img = MIMEImage(f.read(), _subtype="jpeg")
        img.add_header("Content-Disposition", "attachment", filename=jpg_path.name)
        msg.attach(img)

    print(f"E-posta gonderiliyor: {recipients}")
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())
    print("E-posta gonderildi.")


def main():
    try:
        fetch_all_turkey()
        out_rows, calisma_zamani = add_wetbulb_and_save()
        jpg_path = build_map(out_rows, calisma_zamani)
        hot_text = build_hot_stations_text(out_rows, threshold=28.0)
        send_email(jpg_path, calisma_zamani, extra_text=hot_text)
    finally:
        if RAW_TMP_CSV.exists():
            RAW_TMP_CSV.unlink()


if __name__ == "__main__":
    main()
