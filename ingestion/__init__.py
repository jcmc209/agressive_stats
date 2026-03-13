"""
Pipeline de ingesta de datos de La Liga.

Fuentes:
  1. Supabase (nube)           — fuente primaria y única
  2. football-data.co.uk       — CSVs con faltas, tarjetas, goles, tiros
  3. fbref.com (scraper)       — posesión de balón → directo a Supabase

Modos de ingesta (--ingest):
  stats       — descarga CSVs de football-data → Supabase
  possession  — scrape fbref → Supabase
  all         — stats + possession
"""

from __future__ import annotations

from config import SEASONS
from ingestion import supabase_client, csv_source


def fetch_all() -> list[dict]:
    """Descarga todos los partidos de Supabase."""
    print("📡 Consultando Supabase...")
    partidos = supabase_client.fetch_all_matches()
    if partidos:
        print(f"  ✓ {len(partidos)} partidos desde Supabase.")
    else:
        print("❌ Supabase no devolvió datos. Ejecuta --ingest stats para cargar partidos.")
    return partidos


def ingest_stats() -> list[dict]:
    """Descarga CSVs de football-data.co.uk → sube nuevos a Supabase."""
    print("🌐 Descargando CSVs desde football-data.co.uk...")

    all_csv: list[dict] = []
    for season in SEASONS:
        print(f"\n📅 Temporada {season}/{season + 1}:")
        try:
            rows = csv_source.download_season(season)
            all_csv.extend(rows)
        except Exception as e:
            print(f"  ⚠  Error en temporada {season}: {e}")

    if not all_csv:
        print("⚠  No se descargaron datos del CSV.")
        return []

    print(f"\n☁  Sincronizando {len(all_csv)} partidos con Supabase...")
    try:
        inserted, skipped = supabase_client.sync_new_matches(all_csv)
        print(f"  ✓ {inserted} partidos nuevos subidos, {skipped} ya existían.")
    except Exception as e:
        print(f"  ⚠  Error al sincronizar con Supabase: {e}")

    print()
    return fetch_all()


def ingest_possession() -> None:
    """Scrape fbref → escribe posesión directamente a Supabase."""
    from ingestion.scraper import scrape_possession
    scrape_possession()


def ingest_all() -> list[dict]:
    """Ejecuta stats + possession."""
    partidos = ingest_stats()
    print()
    ingest_possession()
    return partidos


def get_all_team_names(partidos: list[dict]) -> list[str]:
    """Devuelve lista ordenada de todos los equipos en los datos."""
    nombres: set[str] = set()
    for p in partidos:
        nombres.add(p["home"]["name"])
        nombres.add(p["away"]["name"])
    return sorted(nombres)
