"""
IAP — Índice de Agresividad Ponderado.

  IAP_raw  = faltas × W_F + amarillas × W_A + rojas × W_R
  peso     = e^(-λ × días)
  IAP_team = Σ(IAP_raw × peso) / Σ(peso)
  Escala normalizada 1-10 relativa a la liga.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from config import DECAY_LAMBDA, PESO_FALTAS, PESO_AMARILLAS, PESO_ROJAS
from model.helpers import parse_date, decay_weight


def _iap_raw(fouls: int, yellows: int, reds: int) -> float:
    return fouls * PESO_FALTAS + yellows * PESO_AMARILLAS + reds * PESO_ROJAS


def calcular_scores(partidos: list[dict]) -> dict:
    """
    Calcula scores de agresividad para todos los equipos.

    Devuelve dict[equipo] con general/local/visitante + stats_raw.
    """
    hoy = date.today()
    acum: dict = {}

    for partido in partidos:
        fecha = parse_date(partido["date"])
        peso = decay_weight(fecha, hoy, DECAY_LAMBDA)

        for rol, key in [("home", "local"), ("away", "visitante")]:
            team = partido[rol]
            nombre = team["name"]
            iap = _iap_raw(team["fouls"], team["yellow_cards"], team["red_cards"])

            if nombre not in acum:
                acum[nombre] = {
                    "general_num": 0.0, "general_den": 0.0,
                    "local_num": 0.0, "local_den": 0.0,
                    "visitante_num": 0.0, "visitante_den": 0.0,
                    "n_partidos": 0, "n_local": 0, "n_visitante": 0,
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

    scores: dict = {}
    for nombre, a in acum.items():
        n = a["n_partidos"]
        scores[nombre] = {
            "general": a["general_num"] / a["general_den"] if a["general_den"] > 0 else 0,
            "local": a["local_num"] / a["local_den"] if a["local_den"] > 0 else 0,
            "visitante": a["visitante_num"] / a["visitante_den"] if a["visitante_den"] > 0 else 0,
            "n_partidos": n,
            "n_local": a["n_local"],
            "n_visitante": a["n_visitante"],
            "stats_raw": {
                "faltas_media": round(a["faltas_sum"] / n, 2) if n > 0 else 0,
                "amarillas_media": round(a["amarillas_sum"] / n, 2) if n > 0 else 0,
                "rojas_media": round(a["rojas_sum"] / n, 2) if n > 0 else 0,
            },
        }

    _normalizar(scores)
    return scores


def _normalizar(scores: dict) -> None:
    """Normaliza a escala 1-10 con min-max relativo a la liga."""
    for dim in ("general", "local", "visitante"):
        valores = [s[dim] for s in scores.values()]
        min_v, max_v = min(valores), max(valores)
        rango = max_v - min_v if max_v != min_v else 1.0
        for nombre in scores:
            raw = scores[nombre][dim]
            scores[nombre][f"{dim}_norm"] = round(1 + ((raw - min_v) / rango) * 9, 1)


def calcular_rankings(scores: dict) -> dict:
    """Posición de cada equipo en el ranking por dimensión."""
    rankings = {nombre: {} for nombre in scores}
    for dim in ("general", "local", "visitante"):
        orden = sorted(scores, key=lambda n: scores[n][f"{dim}_norm"], reverse=True)
        for pos, nombre in enumerate(orden, 1):
            rankings[nombre][f"rank_{dim}"] = pos
    return rankings


# -- Búsqueda fuzzy de equipo -------------------------------------------------

ALIASES: dict[str, str] = {
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
    """Búsqueda fuzzy del nombre de equipo. Devuelve nombre oficial o None."""
    q = nombre_input.lower().strip()
    equipos = list(scores.keys())

    alias = ALIASES.get(q)
    if alias and alias in scores:
        return alias

    for e in equipos:
        if e.lower() == q:
            return e

    candidatos = [e for e in equipos if q in e.lower()]
    if len(candidatos) == 1:
        return candidatos[0]
    if candidatos:
        return min(candidatos, key=len)

    candidatos = [e for e in equipos if e.lower() in q]
    if candidatos:
        return max(candidatos, key=len)

    for alias_key, nombre_oficial in ALIASES.items():
        if q in alias_key or alias_key in q:
            if nombre_oficial in scores:
                return nombre_oficial

    return None


def nivel_riesgo(score_a: float, score_b: float) -> tuple[str, str]:
    """Evalúa el nivel de riesgo disciplinario del enfrentamiento."""
    media = (score_a + score_b) / 2
    if media >= 8.5:
        return "🔴 PARTIDO CRÍTICO - Riesgo disciplinario muy alto", "\033[91m"
    if media >= 7.0:
        return "🟠 PARTIDO DE ALTO RIESGO DISCIPLINARIO", "\033[33m"
    if media >= 5.0:
        return "🟡 Riesgo disciplinario moderado", "\033[93m"
    return "🟢 Partido de bajo riesgo disciplinario", "\033[92m"
