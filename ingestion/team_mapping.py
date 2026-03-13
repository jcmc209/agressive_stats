"""
Normalización de nombres de equipo entre fuentes de datos.

Nombre canónico = el que usa football-data.co.uk (nuestro estándar en Supabase).
"""

from __future__ import annotations

FBREF_TO_DB: dict[str, str] = {
    # --- Nombres oficiales largos ---
    "Athletic Club": "Ath Bilbao",
    "Atlético Madrid": "Ath Madrid",
    "Atletico Madrid": "Ath Madrid",
    "Atlético de Madrid": "Ath Madrid",
    "Rayo Vallecano": "Vallecano",
    "Real Sociedad": "Sociedad",
    "Real Betis": "Betis",
    "RCD Espanyol": "Espanol",
    "Espanyol": "Espanol",
    "Deportivo Alavés": "Alaves",
    "Deportivo Alaves": "Alaves",
    "Alavés": "Alaves",
    "Cádiz": "Cadiz",
    "Cadiz CF": "Cadiz",
    "UD Las Palmas": "Las Palmas",
    "RCD Mallorca": "Mallorca",
    "Celta Vigo": "Celta",
    "Celta de Vigo": "Celta",
    "RC Celta": "Celta",
    "Real Valladolid": "Valladolid",
    "CD Leganés": "Leganes",
    "CD Leganes": "Leganes",
    "Leganés": "Leganes",
    "Getafe CF": "Getafe",
    "Girona FC": "Girona",
    "Sevilla FC": "Sevilla",
    "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal",
    "CA Osasuna": "Osasuna",
    "FC Barcelona": "Barcelona",
    "Granada CF": "Granada",
    "UD Almería": "Almeria",
    "Almería": "Almeria",
    "Real Oviedo": "Oviedo",
    "Elche CF": "Elche",
    "Levante UD": "Levante",
    "Racing de Santander": "Racing Santander",
}


def normalize(name: str) -> str:
    """Normaliza un nombre de equipo al formato canónico de la BD."""
    return FBREF_TO_DB.get(name.strip(), name.strip())
