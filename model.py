"""
model.py
--------
Calcula el Índice de Agresividad Ponderado (IAP) para cada equipo.

Fórmula por partido:
  IAP_raw = (faltas × W_FALTAS) + (amarillas × W_AMARILLAS) + (rojas × W_ROJAS)

Decay temporal (los partidos recientes pesan más):
  peso = e^(-λ × días_desde_partido)

Score final ponderado:
  IAP_equipo = Σ(IAP_raw × peso) / Σ(pesos)

Normalización:
  Escala relativa 1-10 respecto al resto de equipos de la liga.
"""

import math
from datetime import date, datetime
from typing import Optional
from config import DECAY_LAMBDA, PESO_FALTAS, PESO_AMARILLAS, PESO_ROJAS


def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _decay_weight(match_date: date, reference_date: date, lam: float) -> float:
    """Calcula el peso exponencial de un partido según su antigüedad."""
    days_ago = (reference_date - match_date).days
    days_ago = max(0, days_ago)
    return math.exp(-lam * days_ago)


def _iap_raw(fouls: int, yellow_cards: int, red_cards: int) -> float:
    """Calcula el IAP sin ponderar de un partido individual."""
    return (
        fouls * PESO_FALTAS +
        yellow_cards * PESO_AMARILLAS +
        red_cards * PESO_ROJAS
    )


def calcular_scores(partidos: list[dict]) -> dict:
    """
    Calcula los scores de agresividad para todos los equipos.

    Devuelve un dict con estructura:
    {
        "nombre_equipo": {
            "general": float,       # IAP ponderado global (sin normalizar)
            "local": float,         # IAP ponderado solo como local
            "visitante": float,     # IAP ponderado solo como visitante
            "n_partidos": int,
            "n_local": int,
            "n_visitante": int,
            "stats_raw": {          # Medias sin ponderar (informativo)
                "faltas_media": float,
                "amarillas_media": float,
                "rojas_media": float,
            }
        }
    }
    """
    hoy = date.today()

    # Acumuladores: {team_name: {"general": [numerador, denominador], ...}}
    acum = {}

    for partido in partidos:
        fecha = _parse_date(partido["date"])
        peso = _decay_weight(fecha, hoy, DECAY_LAMBDA)

        for rol, key in [("home", "local"), ("away", "visitante")]:
            team = partido[rol]
            nombre = team["name"]

            iap = _iap_raw(team["fouls"], team["yellow_cards"], team["red_cards"])

            if nombre not in acum:
                acum[nombre] = {
                    "general_num": 0.0, "general_den": 0.0,
                    "local_num": 0.0,   "local_den": 0.0,
                    "visitante_num": 0.0, "visitante_den": 0.0,
                    "n_partidos": 0, "n_local": 0, "n_visitante": 0,
                    # Para stats_raw (sin ponderar)
                    "faltas_sum": 0, "amarillas_sum": 0, "rojas_sum": 0,
                }

            a = acum[nombre]
            a["general_num"] += iap * peso
            a["general_den"] += peso
            a["n_partidos"] += 1
            a["faltas_sum"] += team["fouls"]
            a["amarillas_sum"] += team["yellow_cards"]
            a["rojas_sum"] += team["red_cards"]

            if rol == "home":
                a["local_num"] += iap * peso
                a["local_den"] += peso
                a["n_local"] += 1
            else:
                a["visitante_num"] += iap * peso
                a["visitante_den"] += peso
                a["n_visitante"] += 1

    # Calcular scores ponderados por equipo
    scores_raw = {}
    for nombre, a in acum.items():
        n = a["n_partidos"]
        scores_raw[nombre] = {
            "general":   a["general_num"] / a["general_den"] if a["general_den"] > 0 else 0,
            "local":     a["local_num"] / a["local_den"]     if a["local_den"] > 0 else 0,
            "visitante": a["visitante_num"] / a["visitante_den"] if a["visitante_den"] > 0 else 0,
            "n_partidos":  n,
            "n_local":     a["n_local"],
            "n_visitante": a["n_visitante"],
            "stats_raw": {
                "faltas_media":    round(a["faltas_sum"] / n, 2) if n > 0 else 0,
                "amarillas_media": round(a["amarillas_sum"] / n, 2) if n > 0 else 0,
                "rojas_media":     round(a["rojas_sum"] / n, 2) if n > 0 else 0,
            }
        }

    # Normalizar a escala 1-10 para cada dimensión
    scores_normalizados = _normalizar(scores_raw)
    return scores_normalizados


def _normalizar(scores_raw: dict) -> dict:
    """
    Normaliza los scores a escala 1-10 usando min-max relativo a la liga.
    El equipo más agresivo obtiene 10, el menos agresivo obtiene 1.
    """
    for dimension in ["general", "local", "visitante"]:
        valores = [s[dimension] for s in scores_raw.values()]
        min_v = min(valores)
        max_v = max(valores)
        rango = max_v - min_v if max_v != min_v else 1.0

        for nombre in scores_raw:
            raw = scores_raw[nombre][dimension]
            normalizado = 1 + ((raw - min_v) / rango) * 9  # escala 1-10
            scores_raw[nombre][f"{dimension}_norm"] = round(normalizado, 1)

    return scores_raw


def calcular_rankings(scores: dict) -> dict:
    """
    Calcula la posición de cada equipo en el ranking de agresividad
    para cada dimensión (general, local, visitante).

    Devuelve: {"equipo": {"rank_general": N, "rank_local": N, "rank_visitante": N}}
    """
    rankings = {nombre: {} for nombre in scores}

    for dimension in ["general", "local", "visitante"]:
        orden = sorted(scores.keys(), key=lambda n: scores[n][f"{dimension}_norm"], reverse=True)
        for pos, nombre in enumerate(orden, 1):
            rankings[nombre][f"rank_{dimension}"] = pos

    return rankings


ALIASES = {
    "athletic": "Ath Bilbao",
    "athletic bilbao": "Ath Bilbao",
    "athletic club": "Ath Bilbao",
    "bilbao": "Ath Bilbao",
    "atletico": "Ath Madrid",
    "atletico madrid": "Ath Madrid",
    "atletico de madrid": "Ath Madrid",
    "atleti": "Ath Madrid",
    "barca": "Barcelona",
    "barça": "Barcelona",
    "fcb": "Barcelona",
    "madrid": "Real Madrid",
    "rmadrid": "Real Madrid",
    "rayo": "Vallecano",
    "rayo vallecano": "Vallecano",
    "real sociedad": "Sociedad",
    "la real": "Sociedad",
    "real betis": "Betis",
    "espanyol": "Espanol",
    "rcd espanyol": "Espanol",
    "deportivo alaves": "Alaves",
    "alavés": "Alaves",
    "cadiz": "Cadiz",
    "cádiz": "Cadiz",
    "ud las palmas": "Las Palmas",
    "rcd mallorca": "Mallorca",
    "rc celta": "Celta",
    "celta vigo": "Celta",
    "pucela": "Valladolid",
    "real valladolid": "Valladolid",
    "leganés": "Leganes",
    "cd leganes": "Leganes",
}


def buscar_equipo(nombre_input: str, scores: dict) -> Optional[str]:
    """
    Búsqueda fuzzy del nombre del equipo.
    Acepta nombres parciales, aliases comunes y errores menores (case-insensitive).
    Devuelve el nombre oficial o None si no se encuentra.
    """
    nombre_input = nombre_input.lower().strip()
    equipos = list(scores.keys())

    # 0. Alias conocidos (Athletic → Ath Bilbao, etc.)
    alias_match = ALIASES.get(nombre_input)
    if alias_match and alias_match in scores:
        return alias_match

    # 1. Coincidencia exacta (case-insensitive)
    for equipo in equipos:
        if equipo.lower() == nombre_input:
            return equipo

    # 2. Coincidencia parcial (el input está contenido en el nombre oficial)
    candidatos = [e for e in equipos if nombre_input in e.lower()]
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        return min(candidatos, key=len)

    # 3. Búsqueda inversa (el nombre oficial está contenido en el input)
    candidatos = [e for e in equipos if e.lower() in nombre_input]
    if candidatos:
        return max(candidatos, key=len)

    # 4. Coincidencia parcial en aliases
    for alias, nombre_oficial in ALIASES.items():
        if nombre_input in alias or alias in nombre_input:
            if nombre_oficial in scores:
                return nombre_oficial

    return None


def nivel_riesgo(score_a: float, score_b: float) -> tuple:
    """
    Dada la puntuación general de dos equipos, evalúa el nivel de riesgo
    disciplinario del partido.
    Devuelve (etiqueta, color_ansi).
    """
    media = (score_a + score_b) / 2
    if media >= 8.5:
        return "🔴 PARTIDO CRÍTICO - Riesgo disciplinario muy alto", "\033[91m"
    elif media >= 7.0:
        return "🟠 PARTIDO DE ALTO RIESGO DISCIPLINARIO", "\033[33m"
    elif media >= 5.0:
        return "🟡 Riesgo disciplinario moderado", "\033[93m"
    else:
        return "🟢 Partido de bajo riesgo disciplinario", "\033[92m"
