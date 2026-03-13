"""Descarga y parseo de datos de partidos desde football-data.co.uk."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

import requests

CSV_URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/{code}/SP1.csv"


def _season_code(season: int) -> str:
    """2023 → '2324', 2025 → '2526'."""
    return f"{season % 100}{(season + 1) % 100:02d}"


def _season_str(season: int) -> str:
    """2023 → '2023-24', 2025 → '2025-26'."""
    return f"{season}-{(season + 1) % 100:02d}"


def _parse_date_dmy(date_str: str) -> str:
    """DD/MM/YYYY → YYYY-MM-DD."""
    return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")


def _row_to_supabase(row: dict, season: int) -> Optional[dict]:
    """Convierte una fila del CSV de football-data.co.uk al formato Supabase."""
    home = row.get("HomeTeam", "").strip()
    away = row.get("AwayTeam", "").strip()
    date_raw = row.get("Date", "").strip()

    if not home or not away or not date_raw:
        return None

    hf = row.get("HF", "")
    af = row.get("AF", "")
    hy = row.get("HY", "")
    ay = row.get("AY", "")
    hr = row.get("HR", "")
    ar = row.get("AR", "")

    if not hf or not hy:
        return None

    try:
        fecha = _parse_date_dmy(date_raw)
    except ValueError:
        return None

    season_s = _season_str(season)
    match_id = f"fdc_{season_s}_{fecha}_{home}_{away}"

    return {
        "match_id": match_id,
        "season": season_s,
        "match_date": fecha,
        "home_team": home,
        "away_team": away,
        "fouls_home": int(hf),
        "fouls_away": int(af),
        "fouls_total": int(hf) + int(af),
        "yellows_home": int(hy),
        "yellows_away": int(ay),
        "reds_home": int(hr) if hr else 0,
        "reds_away": int(ar) if ar else 0,
        "home_goals": int(row.get("FTHG", 0) or 0),
        "away_goals": int(row.get("FTAG", 0) or 0),
        "result": row.get("FTR", "").strip() or None,
        "shots_home": int(row.get("HS", 0) or 0) or None,
        "shots_away": int(row.get("AS", 0) or 0) or None,
        "shots_on_target_home": int(row.get("HST", 0) or 0) or None,
        "shots_on_target_away": int(row.get("AST", 0) or 0) or None,
        "corners_home": int(row.get("HC", 0) or 0) or None,
        "corners_away": int(row.get("AC", 0) or 0) or None,
        "data_source": "football-data.co.uk",
    }


def download_season(season: int) -> list[dict]:
    """Descarga el CSV de una temporada y devuelve filas en formato Supabase."""
    code = _season_code(season)
    url = CSV_URL_TEMPLATE.format(code=code)
    print(f"  → Descargando temporada {season}/{season + 1}...")

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    text = response.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for row in reader:
        sb_row = _row_to_supabase(row, season)
        if sb_row:
            rows.append(sb_row)

    print(f"     {len(rows)} partidos en el CSV.")
    return rows
