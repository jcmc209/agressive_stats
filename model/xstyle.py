"""
xStyle — Perfil de estilo de juego de cada equipo.

Dimensiones: tiros, precisión, corners, goles, eficiencia,
             físico, riesgo tarjeta, faltas provocadas.
"""

from __future__ import annotations

from datetime import date

from config import DECAY_LAMBDA
from model.helpers import parse_date, decay_weight, safe

STYLE_DIMS = [
    ("tiros", "Tiros/P"),
    ("precision", "Precisión tiro"),
    ("corners", "Corners/P"),
    ("goles", "Goles/P"),
    ("eficiencia", "Eficiencia gol"),
    ("fisico", "Físico"),
    ("riesgo_tarj", "Riesgo tarjeta"),
    ("faltas_prov", "Faltas provoca"),
]


def _clasificar_estilo(
    fouls: float, shots: float, shot_acc: float,
    set_piece_r: float, physical_r: float,
    goals: float, goals_conc: float,
) -> tuple[str, str]:
    """Clasifica el estilo según métricas combinadas."""
    high_physical = fouls > 13.5
    low_physical = fouls < 11.0
    high_shots = shots > 12.0
    low_shots = shots < 9.5
    high_acc = shot_acc > 0.40
    high_setpiece = set_piece_r > 0.33
    high_goals = goals > 1.4

    if high_physical and low_shots:
        return "FÍSICO-DEFENSIVO", "Alta presión, bajo volumen ofensivo"
    if high_physical and high_shots:
        return "INTENSO", "Máxima intensidad en ambas fases"
    if low_physical and high_shots and high_acc:
        return "TÉCNICO-OFENSIVO", "Juego de posesión con alto aprovechamiento"
    if low_physical and high_shots:
        return "POSESIÓN", "Control del juego con volumen ofensivo"
    if high_setpiece and high_physical:
        return "DIRECTO-FÍSICO", "Balón parado y contacto físico constante"
    if high_setpiece:
        return "ESTRATÉGICO", "Dependencia de estrategia y balones parados"
    if low_physical and low_shots:
        return "CONSERVADOR", "Bajo riesgo, contención defensiva"
    if high_goals and high_shots:
        return "OFENSIVO", "Alto volumen goleador"
    return "EQUILIBRADO", "Sin tendencia dominante marcada"


def calcular_xstyle(partidos: list[dict]) -> dict:
    """Calcula el perfil de estilo de juego de cada equipo con decay temporal."""
    hoy = date.today()
    acum: dict = {}

    for p in partidos:
        fecha = parse_date(p["date"])
        peso = decay_weight(fecha, hoy, DECAY_LAMBDA)

        for rol, opp_rol in [("home", "away"), ("away", "home")]:
            team = p[rol]
            nombre = team["name"]
            opp = p[opp_rol]

            if nombre not in acum:
                acum[nombre] = {k: 0.0 for k in [
                    "f_w", "y_w", "r_w", "sh_w", "sh_ot_w", "co_w",
                    "gf_w", "ga_w", "drawn_w", "w",
                ]}
                acum[nombre]["n"] = 0

            a = acum[nombre]
            a["f_w"] += safe(team.get("fouls")) * peso
            a["y_w"] += safe(team.get("yellow_cards")) * peso
            a["r_w"] += safe(team.get("red_cards")) * peso
            a["sh_w"] += safe(team.get("shots")) * peso
            a["sh_ot_w"] += safe(team.get("shots_on_target")) * peso
            a["co_w"] += safe(team.get("corners")) * peso
            a["gf_w"] += safe(team.get("goals")) * peso
            a["ga_w"] += safe(opp.get("goals")) * peso
            a["drawn_w"] += safe(opp.get("fouls")) * peso
            a["w"] += peso
            a["n"] += 1

    raw: dict = {}
    for nombre, a in acum.items():
        w = a["w"]
        if w == 0:
            continue

        fouls = a["f_w"] / w
        yellows = a["y_w"] / w
        shots = a["sh_w"] / w
        shots_ot = a["sh_ot_w"] / w
        corners = a["co_w"] / w
        goals = a["gf_w"] / w
        goals_c = a["ga_w"] / w
        drawn = a["drawn_w"] / w

        precision = shots_ot / shots if shots > 0 else 0.0
        eficiencia = goals / shots_ot if shots_ot > 0 else 0.0
        set_piece_r = corners / (shots + corners) if (shots + corners) > 0 else 0.0
        cards_foul = yellows / fouls if fouls > 0 else 0.0
        ratio_fis = fouls / (fouls + shots) if (fouls + shots) > 0 else 0.5
        tempo = fouls + shots + corners * 0.5

        estilo, desc = _clasificar_estilo(
            fouls, shots, precision, set_piece_r, ratio_fis, goals, goals_c,
        )

        raw[nombre] = {
            "tiros": round(shots, 1),
            "tiros_a_puerta": round(shots_ot, 1),
            "corners": round(corners, 1),
            "goles": round(goals, 2),
            "goles_conc": round(goals_c, 2),
            "fouls": round(fouls, 1),
            "amarillas": round(yellows, 2),
            "rojas": round(a["r_w"] / w, 3),
            "faltas_prov": round(drawn, 1),
            "precision": round(precision, 3),
            "eficiencia": round(eficiencia, 3),
            "set_piece_ratio": round(set_piece_r, 3),
            "cards_per_foul": round(cards_foul, 3),
            "ratio_fisico": round(ratio_fis, 3),
            "tempo": round(tempo, 1),
            "estilo": estilo,
            "estilo_desc": desc,
            "n_partidos": a["n"],
        }

    _normalizar_dims(raw)
    return raw


def _normalizar_dims(raw: dict) -> None:
    """Normaliza dimensiones de estilo a escala 1-10 relativa a la liga."""
    dims = {
        "tiros": ("tiros", True),
        "precision": ("precision", True),
        "corners": ("corners", True),
        "goles": ("goles", True),
        "eficiencia": ("eficiencia", True),
        "fisico": ("fouls", True),
        "riesgo_tarj": ("cards_per_foul", True),
        "faltas_prov": ("faltas_prov", True),
    }
    for dim_key, (campo, higher_is_more) in dims.items():
        valores = [raw[n][campo] for n in raw]
        min_v, max_v = min(valores), max(valores)
        rng = max_v - min_v if max_v != min_v else 1.0
        for nombre in raw:
            v = raw[nombre][campo]
            norm = 1 + ((v - min_v) / rng) * 9 if higher_is_more else 1 + ((max_v - v) / rng) * 9
            if "dim_norm" not in raw[nombre]:
                raw[nombre]["dim_norm"] = {}
            raw[nombre]["dim_norm"][dim_key] = round(norm, 1)
