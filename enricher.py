"""
enricher.py
-----------
Enriquece la tabla matches de Supabase con datos de API-Football:
  - xG (expected goals)
  - Posesión (%)
  - Offsides

Respetando el límite de 100 llamadas/día del plan gratuito:
  - 3-6 llamadas para mapear fixtures (detección automática de temporada actual)
  - Hasta ~90 partidos enriquecidos por ejecución (--enrich)
"""

import json
import os
import re
import time
from datetime import date
from typing import Optional

import requests

from config import SEASONS, SUPABASE_URL, SUPABASE_KEY, API_FOOTBALL_KEY
from data_fetcher import _get_sb


FIXTURE_MAPPING_FILE = "fixture_mapping.json"
SKIP_FIXTURES_FILE = "enricher_skips.json"
API_BASE = "https://v3.football.api-sports.io"
LA_LIGA_ID = 140
MAX_STATS_CALLS_PER_RUN = 90  # deja margen a las 100/día (3 para mapeo)

# Nombres API-Football → nombres en nuestra BD (Supabase / football-data.co.uk)
API_TEAM_TO_DB = {
    "Athletic Club": "Ath Bilbao",
    "Atlético Madrid": "Ath Madrid",
    "Atletico Madrid": "Ath Madrid",
    "Rayo Vallecano": "Vallecano",
    "Real Sociedad": "Sociedad",
    "Real Betis": "Betis",
    "RCD Espanyol": "Espanol",
    "Espanyol": "Espanol",
    "Deportivo Alavés": "Alaves",
    "Deportivo Alaves": "Alaves",
    "Cádiz": "Cadiz",
    "Cadiz": "Cadiz",
    "UD Las Palmas": "Las Palmas",
    "RCD Mallorca": "Mallorca",
    "Celta Vigo": "Celta",
    "Real Valladolid": "Valladolid",
    "CD Leganés": "Leganes",
    "CD Leganes": "Leganes",
    "Getafe CF": "Getafe",
    "Girona FC": "Girona",
    "Sevilla FC": "Sevilla",
    "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal",
    "CA Osasuna": "Osasuna",
    "Real Madrid": "Real Madrid",
    "FC Barcelona": "Barcelona",
    "Granada CF": "Granada",
    "Deportivo Alaves": "Alaves",
    # Ascensos / Segunda (nombres que puede devolver la API)
    "Real Oviedo": "Oviedo",
    "Oviedo": "Oviedo",
    "Elche CF": "Elche",
    "Elche": "Elche",
    "Levante UD": "Levante",
    "Levante": "Levante",
}


def load_skip_fixtures() -> set:
    """Carga fixture_ids que ya se intentaron y no tenían stats."""
    if not os.path.exists(SKIP_FIXTURES_FILE):
        return set()
    with open(SKIP_FIXTURES_FILE, encoding="utf-8") as f:
        return set(json.load(f))


def save_skip_fixtures(skips: set) -> None:
    """Guarda fixture_ids sin stats para no reintentarlos."""
    with open(SKIP_FIXTURES_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(skips), f)


def clear_skip_fixtures() -> int:
    """Borra la lista de skips. Devuelve cuántos había."""
    skips = load_skip_fixtures()
    count = len(skips)
    if os.path.exists(SKIP_FIXTURES_FILE):
        os.remove(SKIP_FIXTURES_FILE)
    return count


RATE_LIMIT_WAITS = [30, 60, 60]


def _api_get(path: str, params: dict) -> dict:
    for attempt in range(len(RATE_LIMIT_WAITS) + 1):
        r = requests.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"x-apisports-key": API_FOOTBALL_KEY},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        errors = data.get("errors")
        if isinstance(errors, dict) and "rateLimit" in errors:
            if attempt < len(RATE_LIMIT_WAITS):
                wait = RATE_LIMIT_WAITS[attempt]
                print(f"  [rate limit] Esperando {wait}s antes de reintentar...")
                time.sleep(wait)
                continue
        return data
    return data


def _parse_stat(team_stats: list, stat_type: str) -> Optional[str]:
    for s in team_stats:
        if s.get("type") == stat_type:
            v = s.get("value")
            if v is None:
                return None
            return str(v).strip()
    return None


def _parse_possession(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    m = re.match(r"^(\d+)%?$", str(value).strip())
    return int(m.group(1)) if m else None


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "" or (isinstance(value, str) and value.lower() in ("none", "n/a")):
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "" or (isinstance(value, str) and value.lower() in ("none", "n/a")):
        return None
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except ValueError:
        return None


def _api_to_db_name(api_name: str) -> str:
    """Normaliza nombre de API-Football al de nuestra BD."""
    if not api_name:
        return ""
    return API_TEAM_TO_DB.get(api_name.strip(), api_name.strip())


def _team_names_match(db_name: str, api_name: str) -> bool:
    """True si el nombre en nuestra BD coincide con el de la API (flexible)."""
    if not db_name or not api_name:
        return False
    d = db_name.lower().strip()
    a = api_name.lower().strip()
    a_db = _api_to_db_name(api_name).lower()
    if d == a or d == a_db:
        return True
    if d in a or a in d or d in a_db or a_db in d:
        return True
    d_words = set(d.split())
    a_words = set(a.split())
    return bool(d_words & a_words)


def load_fixture_mapping() -> dict:
    """Carga el mapeo (date -> lista de {fixture_id, home, away})."""
    if not os.path.exists(FIXTURE_MAPPING_FILE):
        return {}
    with open(FIXTURE_MAPPING_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_fixture_mapping(mapping: dict) -> None:
    with open(FIXTURE_MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def fetch_fixtures_for_season(season: int) -> list:
    """Devuelve lista de {fixture_id, date, home, away} para una temporada."""
    data = _api_get("/fixtures", {"league": LA_LIGA_ID, "season": season})
    response = data.get("response", [])
    out = []
    for f in response:
        fixture = f.get("fixture", {})
        teams = f.get("teams", {})
        date_str = (fixture.get("date") or "")[:10]
        if not date_str:
            continue
        out.append({
            "fixture_id": fixture.get("id"),
            "date": date_str,
            "home": (teams.get("home") or {}).get("name") or "",
            "away": (teams.get("away") or {}).get("name") or "",
        })
    return out


def build_fixture_mapping() -> dict:
    """Obtiene todos los fixtures de La Liga (SEASONS de config) y construye el mapeo por fecha."""
    by_date = {}
    for season in SEASONS:
        fixtures = fetch_fixtures_for_season(season)
        print(f"  season={season}: {len(fixtures)} fixtures")
        if not fixtures:
            print(f"    ⚠ 0 fixtures — revisa que la API key sea válida y tenga cuota disponible.")
        for item in fixtures:
            date_str = item["date"]
            if date_str not in by_date:
                by_date[date_str] = []
            by_date[date_str].append({
                "fixture_id": item["fixture_id"],
                "home": item["home"],
                "away": item["away"],
            })
    return by_date


def find_fixture_id(mapping: dict, match_date: str, home_team: str, away_team: str) -> Optional[int]:
    """Busca el fixture_id de API-Football para un partido de nuestra BD."""
    candidates = mapping.get(match_date, [])
    for c in candidates:
        if _team_names_match(home_team, c["home"]) and _team_names_match(away_team, c["away"]):
            return c["fixture_id"]
    return None


def fetch_fixture_statistics(fixture_id: int) -> Optional[dict]:
    """
    Devuelve { home: {xg, possession, offsides}, away: {xg, possession, offsides} }
    o None si falla.
    """
    data = _api_get("/fixtures/statistics", {"fixture": fixture_id})
    response = data.get("response", [])
    if len(response) != 2:
        return None
    home_stats = response[0].get("statistics", [])
    away_stats = response[1].get("statistics", [])
    home_team_name = (response[0].get("team") or {}).get("name") or ""
    away_team_name = (response[1].get("team") or {}).get("name") or ""

    def extract(stat_list):
        xg = _parse_float(_parse_stat(stat_list, "expected_goals"))
        poss = _parse_possession(_parse_stat(stat_list, "Ball Possession"))
        off = _parse_int(_parse_stat(stat_list, "Offsides"))
        return {"xg": xg, "possession": poss, "offsides": off}

    home_data = extract(home_stats)
    away_data = extract(away_stats)
    # Si no hay xG ni posesión en ninguno, la API no devolvió stats útiles
    if (home_data["xg"] is None and away_data["xg"] is None
            and home_data["possession"] is None and away_data["possession"] is None):
        return None
    return {
        "home": home_data,
        "away": away_data,
        "home_team": home_team_name,
        "away_team": away_team_name,
    }


def get_matches_to_enrich(sb, limit: int) -> list:
    """Partidos de Supabase que aún no tienen xg_home rellenado.
    Orden: temporada actual hacia atrás (más recientes primero).
    """
    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        r = (
            sb.table("matches")
            .select("match_id,match_date,home_team,away_team,xg_home,possession_home")
            .order("match_date", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        all_rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    to_enrich = [
        row for row in all_rows
        if row.get("xg_home") is None
        or (isinstance(row.get("xg_home"), (int, float)) and row.get("possession_home") is None)
    ]
    # Orden "más recientes primero": si la API devolvió asc, invertir y tomar los N primeros
    if to_enrich and to_enrich[0]["match_date"] < to_enrich[-1]["match_date"]:
        to_enrich = list(reversed(to_enrich))
    return to_enrich[:limit]


def update_match_enrichment(sb, match_id: str, home: dict, away: dict) -> None:
    """Actualiza una fila de matches con xG, posesión y offsides."""
    payload = {
        "xg_home": home.get("xg"),
        "xg_away": away.get("xg"),
        "possession_home": home.get("possession"),
        "possession_away": away.get("possession"),
        "offsides_home": home.get("offsides"),
        "offsides_away": away.get("offsides"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        sb.table("matches").update(payload).eq("match_id", match_id).execute()
    except Exception as e:
        err = str(e).lower()
        if "offsides" in err or "column" in err or "unknown" in err or "does not exist" in err:
            # Intentar solo xG y posesión por si las columnas offsides no existen aún
            fallback = {k: v for k, v in payload.items() if "offsides" not in k}
            if fallback:
                sb.table("matches").update(fallback).eq("match_id", match_id).execute()
                return
            raise RuntimeError(
                "Faltan columnas offsides_home/offsides_away en Supabase. "
                "En el dashboard: SQL Editor → New query → ejecuta el contenido de supabase_migration_offsides.sql"
            ) from e
        raise


def run_enrich(reset_skips: bool = False) -> None:
    """
    Flujo principal:
    1. Cargar o construir mapeo fecha/equipos -> fixture_id (3 llamadas si hay que construir).
    2. Obtener de Supabase los partidos sin enriquecer (xg_home null).
    3. Para cada uno (máx MAX_STATS_CALLS_PER_RUN): buscar fixture_id, llamar statistics, actualizar Supabase.

    Antes de la primera ejecución: en Supabase SQL Editor ejecuta supabase_migration_offsides.sql
    para añadir las columnas offsides_home y offsides_away (si no existen).
    """
    sb = _get_sb()

    if reset_skips and os.path.exists(SKIP_FIXTURES_FILE):
        os.remove(SKIP_FIXTURES_FILE)
        print("  Skip list eliminada.")

    # ── Fixture mapping ───────────────────────────────────────────────────────
    mapping = load_fixture_mapping()

    if not mapping:
        print("Construyendo mapeo de partidos (API-Football fixtures)...")
        mapping = build_fixture_mapping()
        save_fixture_mapping(mapping)
        total_fixtures = sum(len(v) for v in mapping.values())
        print(f"  {total_fixtures} partidos mapeados en {FIXTURE_MAPPING_FILE}")

    to_enrich = get_matches_to_enrich(sb, 99999)
    if not to_enrich:
        print("No hay partidos pendientes de enriquecer (xg/posesión/offsides ya rellenados).")
        return

    if mapping and to_enrich:
        max_date_map = max(mapping.keys())
        max_date_pending = max(r["match_date"] for r in to_enrich)
        if max_date_pending > max_date_map:
            print("El mapeo no incluye fechas tan recientes. Reconstruyendo...")
            mapping = build_fixture_mapping()
            save_fixture_mapping(mapping)
            print(f"  {sum(len(v) for v in mapping.values())} partidos en el mapeo.")

    # ── Filtrar partidos con fecha futura ──────────────────────────────────────
    today_str = date.today().isoformat()
    future_count = sum(1 for r in to_enrich if r["match_date"] > today_str)
    if future_count:
        to_enrich = [r for r in to_enrich if r["match_date"] <= today_str]
        print(f"  {future_count} partidos con fecha futura, se omiten.")

    # ── Construir lista de partidos a procesar ────────────────────────────────
    to_process = []
    skipped_no_fixture = 0
    for row in to_enrich:
        fid = find_fixture_id(mapping, row["match_date"], row["home_team"], row["away_team"])
        if fid is None:
            skipped_no_fixture += 1
            continue
        to_process.append((row, fid))
        if len(to_process) >= MAX_STATS_CALLS_PER_RUN:
            break

    if skipped_no_fixture:
        print(f"  {skipped_no_fixture} partidos sin fixture en API-Football (fechas/liga no disponibles), se omiten.")

    if not to_process:
        print("Ningún partido procesable en este lote.")
        print("  Prueba borrar fixture_mapping.json y ejecutar de nuevo.")
        return

    fechas = [r["match_date"] for r, _ in to_process]
    primera, ultima = min(fechas), max(fechas)
    est_min = len(to_process) * 7 / 60
    print(f"Enriqueciendo {len(to_process)} partidos (límite {MAX_STATS_CALLS_PER_RUN}/día).")
    print(f"  Rango de este lote: {primera} → {ultima}.")
    print(f"  Tiempo estimado: ~{est_min:.0f} minutos (rate limit API: 10 req/min).")

    done = 0
    no_stats = 0
    errors = 0
    for i, (row, fixture_id) in enumerate(to_process):
        if i > 0:
            time.sleep(7)
        match_id = row["match_id"]
        match_date = row["match_date"]
        home_team = row["home_team"]
        away_team = row["away_team"]
        try:
            stats = fetch_fixture_statistics(fixture_id)
            if not stats:
                print(f"  [sin respuesta] {home_team} vs {away_team} ({match_date}) — se reintentará en próxima ejecución")
                no_stats += 1
                continue
            update_match_enrichment(sb, match_id, stats["home"], stats["away"])
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(to_process)}...")
        except Exception as e:
            print(f"  [error] {home_team} vs {away_team} ({match_date}): {e}")
            errors += 1

    print(f"Listo: {done} partidos enriquecidos.")
    if no_stats:
        print(f"  {no_stats} sin respuesta de la API (se reintentarán en la próxima ejecución).")
    if errors:
        print(f"  {errors} con error.")
    remaining = len(get_matches_to_enrich(sb, 99999))
    if remaining > 0:
        print(f"Quedan {remaining} partidos por enriquecer. Ejecuta de nuevo mañana (límite 100 llamadas/día).")


def get_enrich_counts(sb) -> tuple:
    """Devuelve (total_partidos, enriquecidos, pendientes)."""
    enriched = 0
    offset = 0
    page_size = 1000
    while True:
        r = (
            sb.table("matches")
            .select("match_id")
            .not_.is_("xg_home", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        enriched += len(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
    pending = len(get_matches_to_enrich(sb, 99999))
    total = enriched + pending
    return total, enriched, pending


def list_enriched() -> None:
    """Lista los partidos que ya tienen xG/posesión/offsides rellenados en Supabase."""
    sb = _get_sb()
    total, enriched, pending = get_enrich_counts(sb)
    print(f"Total partidos: {total}  |  Con xG/posesión: {enriched}  |  Pendientes: {pending}\n")

    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        r = (
            sb.table("matches")
            .select("match_id,match_date,home_team,away_team,xg_home,xg_away,possession_home,possession_away")
            .not_.is_("xg_home", "null")
            .order("match_date", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        all_rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    if not all_rows:
        print("Ningún partido enriquecido aún (xg_home/posesión vacíos). Ejecuta: python main.py --enrich")
        return

    print(f"Partidos con xG/posesión rellenados: {len(all_rows)}\n")
    print(f"  {'Fecha':<12} {'Local':<20} {'Visitante':<20} {'xG':<10} {'Posesión':<12}")
    print("  " + "-" * 78)
    for row in all_rows:
        xg = f"{row.get('xg_home') or '-'}-{row.get('xg_away') or '-'}"
        poss = f"{row.get('possession_home') or '-'}%-{row.get('possession_away') or '-'}%"
        print(f"  {row['match_date']:<12} {str(row.get('home_team', ''))[:19]:<20} {str(row.get('away_team', ''))[:19]:<20} {xg:<10} {poss:<12}")
    print()
