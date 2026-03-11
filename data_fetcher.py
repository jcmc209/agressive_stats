"""
data_fetcher.py
---------------
Gestiona los datos de partidos de La Liga desde dos fuentes:

  1. Supabase (nube) — fuente primaria y de verdad.
     Tabla: matches  |  columnas: match_id, match_date, home_team, away_team,
                                  fouls_home/away, yellows_home/away, reds_home/away,
                                  season, data_source, ...

  2. football-data.co.uk — fuente de actualización.
     CSVs gratuitos con datos por partido. Se usan para añadir nuevos partidos
     a Supabase y mantener la base de datos al día.

  3. Caché local (cache_partidos.json) — copia offline de Supabase.
     Permite ejecutar el CLI sin conexión a internet.

Flujo habitual:
  python main.py              → carga caché local (instantáneo)
  python main.py --pull       → descarga todo de Supabase → actualiza caché local
  python main.py --refresh    → descarga CSVs → sube nuevos partidos a Supabase
                                → actualiza caché local
"""

import csv
import io
import json
import os
from datetime import datetime
from typing import Optional

import requests
from supabase import create_client, Client

from config import (
    CACHE_FILE, SEASONS,
    SUPABASE_URL, SUPABASE_KEY,
)

CSV_URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/{code}/SP1.csv"


# =============================================================================
# Helpers de conversión entre formatos
# =============================================================================

def _season_code(season: int) -> str:
    """Convierte 2023 → '2324', 2025 → '2526'."""
    return f"{season % 100}{(season + 1) % 100:02d}"


def _season_str(season: int) -> str:
    """Convierte 2023 → '2023-24', 2025 → '2025-26'."""
    return f"{season}-{(season + 1) % 100:02d}"


def _parse_date_dmy(date_str: str) -> str:
    """Convierte DD/MM/YYYY → YYYY-MM-DD."""
    return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")


def _row_to_partido(row: dict) -> dict:
    """
    Convierte una fila de la tabla Supabase al formato interno del modelo.
    Incluye: faltas, tarjetas, tiros, corners, goles y árbitro.
    """
    return {
        "fixture_id": row["match_id"],
        "date": row["match_date"],
        "season": int(row["season"].split("-")[0]),   # "2023-24" → 2023
        "referee": row.get("referee") or "",
        "home": {
            "name": row["home_team"],
            "fouls": row.get("fouls_home") or 0,
            "yellow_cards": row.get("yellows_home") or 0,
            "red_cards": row.get("reds_home") or 0,
            "shots": row.get("shots_home") or 0,
            "shots_on_target": row.get("shots_on_target_home") or 0,
            "corners": row.get("corners_home") or 0,
            "goals": row.get("home_goals") or 0,
        },
        "away": {
            "name": row["away_team"],
            "fouls": row.get("fouls_away") or 0,
            "yellow_cards": row.get("yellows_away") or 0,
            "red_cards": row.get("reds_away") or 0,
            "shots": row.get("shots_away") or 0,
            "shots_on_target": row.get("shots_on_target_away") or 0,
            "corners": row.get("corners_away") or 0,
            "goals": row.get("away_goals") or 0,
        },
    }


def _csv_row_to_supabase(row: dict, season: int) -> Optional[dict]:
    """
    Convierte una fila del CSV de football-data.co.uk al formato de la tabla
    matches en Supabase.
    """
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

    # ID único: prefijo "fdc" (football-data.co.uk) + fecha + equipos
    # Formato compatible con el resto de la tabla pero claramente diferenciado
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


# =============================================================================
# Supabase: lectura y escritura
# =============================================================================

def _get_sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_from_supabase() -> list:
    """
    Descarga todos los partidos de la tabla matches en Supabase.
    Maneja paginación automática (PostgREST devuelve máximo 1000 filas/petición).
    Devuelve lista en formato interno del modelo.
    """
    sb = _get_sb()
    all_rows = []
    page_size = 1000
    offset = 0

    while True:
        r = (
            sb.table("matches")
            .select("match_id,match_date,season,referee,"
                    "home_team,away_team,home_goals,away_goals,"
                    "fouls_home,fouls_away,yellows_home,yellows_away,"
                    "reds_home,reds_away,shots_home,shots_away,"
                    "shots_on_target_home,shots_on_target_away,"
                    "corners_home,corners_away")
            .order("match_date")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = r.data
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return [_row_to_partido(row) for row in all_rows]


def sync_to_supabase(csv_partidos: list) -> tuple:
    """
    Sube partidos nuevos (descargados de football-data.co.uk) a Supabase.
    Evita duplicados comparando por (match_date, home_team, away_team).

    Devuelve (insertados, omitidos).
    """
    sb = _get_sb()

    # Obtener conjunto de (date, home, away) ya existentes en Supabase
    print("  → Consultando partidos existentes en Supabase...")
    existing = set()
    offset = 0
    page_size = 1000
    while True:
        r = (
            sb.table("matches")
            .select("match_date,home_team,away_team")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        for row in r.data:
            existing.add((row["match_date"], row["home_team"], row["away_team"]))
        if len(r.data) < page_size:
            break
        offset += page_size

    print(f"     {len(existing)} partidos ya en Supabase.")

    # Filtrar solo los que no existen
    nuevos = [
        p for p in csv_partidos
        if (p["match_date"], p["home_team"], p["away_team"]) not in existing
    ]

    if not nuevos:
        return 0, len(csv_partidos)

    # Insertar en lotes de 500
    batch_size = 500
    insertados = 0
    for i in range(0, len(nuevos), batch_size):
        batch = nuevos[i:i + batch_size]
        sb.table("matches").insert(batch).execute()
        insertados += len(batch)

    return insertados, len(csv_partidos) - insertados


# =============================================================================
# Descarga desde football-data.co.uk
# =============================================================================

def _download_csv_season(season: int) -> list:
    """
    Descarga el CSV de una temporada y devuelve filas en formato Supabase.
    """
    code = _season_code(season)
    url = CSV_URL_TEMPLATE.format(code=code)
    print(f"  → Descargando temporada {season}/{season + 1}...")

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    text = response.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for row in reader:
        sb_row = _csv_row_to_supabase(row, season)
        if sb_row:
            rows.append(sb_row)

    print(f"     {len(rows)} partidos en el CSV.")
    return rows


# =============================================================================
# Caché local
# =============================================================================

def _load_cache() -> list:
    if not os.path.exists(CACHE_FILE):
        return []
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(partidos: list) -> None:
    partidos.sort(key=lambda p: p["date"])
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(partidos, f, ensure_ascii=False, indent=2)


# =============================================================================
# Punto de entrada principal
# =============================================================================

def fetch_all_data() -> list:
    """
    Devuelve lista de partidos consultando Supabase directamente.
    Si Supabase falla, usa la caché local como fallback offline.

    --refresh → descarga CSVs nuevos → los sube a Supabase → recarga desde Supabase.
    """
    print("📡 Consultando Supabase...")
    try:
        partidos = fetch_from_supabase()
        if partidos:
            _save_cache(partidos)   # actualiza caché local por si acaso
            print(f"  ✓ {len(partidos)} partidos desde Supabase.")
            return partidos
        print("⚠  Supabase no devolvió datos, probando caché local...")
    except Exception as e:
        print(f"⚠  Error al conectar con Supabase: {e}")
        print("   Intentando caché local como fallback...")

    if os.path.exists(CACHE_FILE):
        partidos = _load_cache()
        if partidos:
            print(f"  ✓ {len(partidos)} partidos desde caché local (modo offline).")
            return partidos

    print("❌ No hay datos disponibles. Comprueba la conexión o ejecuta --refresh.")
    return []


def pull_from_supabase() -> list:
    """
    Descarga todos los partidos de Supabase y actualiza la caché local.
    """
    print("📥 Descargando datos desde Supabase...")
    try:
        partidos = fetch_from_supabase()
        if partidos:
            _save_cache(partidos)
            print(f"✓ {len(partidos)} partidos descargados y guardados en caché local.")
        else:
            print("⚠  Supabase no devolvió datos.")
        return partidos
    except Exception as e:
        print(f"⚠  Error al conectar con Supabase: {e}")
        return []


def refresh_from_csv() -> list:
    """
    Descarga CSVs de football-data.co.uk para todas las temporadas configuradas,
    sube los partidos nuevos a Supabase y actualiza la caché local.
    """
    print("🌐 Descargando CSVs desde football-data.co.uk...")

    todos_csv = []
    for season in SEASONS:
        print(f"\n📅 Temporada {season}/{season + 1}:")
        try:
            filas = _download_csv_season(season)
            todos_csv.extend(filas)
        except Exception as e:
            print(f"  ⚠  Error en temporada {season}: {e}")

    if not todos_csv:
        print("⚠  No se descargaron datos del CSV.")
        return []

    print(f"\n☁  Sincronizando {len(todos_csv)} partidos con Supabase...")
    try:
        insertados, omitidos = sync_to_supabase(todos_csv)
        print(f"  ✓ {insertados} partidos nuevos subidos, {omitidos} ya existían.")
    except Exception as e:
        print(f"  ⚠  Error al sincronizar con Supabase: {e}")

    # Siempre hacemos pull para tener la caché local actualizada con TODO Supabase
    print()
    return pull_from_supabase()


def get_all_team_names(partidos: list) -> list:
    """Devuelve lista ordenada de todos los equipos encontrados en los datos."""
    nombres = set()
    for p in partidos:
        nombres.add(p["home"]["name"])
        nombres.add(p["away"]["name"])
    return sorted(nombres)
