"""Perfilado estadístico de árbitros."""

from __future__ import annotations

from typing import Optional

from model.helpers import safe


def calcular_perfiles(partidos: list[dict]) -> dict:
    """
    Estadísticas históricas de cada árbitro.

    Devuelve dict[nombre] con faltas/partido, amarillas/partido,
    factor vs media de la liga y clasificación (estricto/permisivo).
    """
    acum: dict = {}
    total_fouls = total_yellows = total_matches = 0

    for p in partidos:
        ref = p.get("referee", "")
        if not ref:
            continue

        f = safe(p["home"].get("fouls")) + safe(p["away"].get("fouls"))
        a = safe(p["home"].get("yellow_cards")) + safe(p["away"].get("yellow_cards"))
        r = safe(p["home"].get("red_cards")) + safe(p["away"].get("red_cards"))

        if ref not in acum:
            acum[ref] = {"f": 0, "a": 0, "r": 0, "n": 0}
        acum[ref]["f"] += f
        acum[ref]["a"] += a
        acum[ref]["r"] += r
        acum[ref]["n"] += 1

        total_fouls += f
        total_yellows += a
        total_matches += 1

    if total_matches == 0:
        return {}

    avg_f = total_fouls / total_matches
    avg_a = total_yellows / total_matches

    perfiles: dict = {}
    for ref, d in acum.items():
        n = d["n"]
        fp = d["f"] / n
        ap = d["a"] / n
        rp = d["r"] / n
        factor_f = fp / avg_f if avg_f > 0 else 1.0
        factor_a = ap / avg_a if avg_a > 0 else 1.0

        if factor_a >= 1.20:
            tipo = "muy estricto"
        elif factor_a >= 1.05:
            tipo = "estricto"
        elif factor_a >= 0.95:
            tipo = "normal"
        else:
            tipo = "permisivo"

        perfiles[ref] = {
            "partidos": n,
            "fouls_partido": round(fp, 1),
            "amarillas_partido": round(ap, 2),
            "rojas_partido": round(rp, 2),
            "factor_fouls": round(factor_f, 3),
            "factor_amarillas": round(factor_a, 3),
            "tipo": tipo,
            "avg_liga_fouls": round(avg_f, 1),
            "avg_liga_amarillas": round(avg_a, 2),
        }

    return perfiles


def buscar_arbitro(nombre_input: str, perfiles: dict) -> Optional[str]:
    """Búsqueda case-insensitive del nombre del árbitro."""
    q = nombre_input.lower().strip()
    arbitros = list(perfiles.keys())

    for a in arbitros:
        if a.lower() == q:
            return a

    candidatos = [a for a in arbitros if q in a.lower()]
    if len(candidatos) == 1:
        return candidatos[0]
    if candidatos:
        return min(candidatos, key=len)

    candidatos = [a for a in arbitros if a.lower() in q]
    if candidatos:
        return max(candidatos, key=len)

    return None
