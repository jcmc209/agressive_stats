"""
xmodel.py
---------
Modelos extendidos de análisis de partidos:

  xFouls  — Faltas esperadas en un partido concreto, ajustadas por:
              · Tasa de faltas histórica de cada equipo (con decay temporal)
              · Factor víctima: ¿cuánto provoca el rival?
              · Factor árbitro: ¿es estricto o permisivo?
              · Presión de tarjetas: equipos «enchufados» se autorregulan

  xStyle  — Perfil de estilo de juego de cada equipo calculado a partir de:
              · Tiros / tiros a portería / corners / goles
              · Faltas, tarjetas
              · Faltas provocadas (las que cometen los rivales)
              · Métricas derivadas: precisión de tiro, eficiencia gol,
                dependencia de set piece, ratio físico/técnico

  Árbitros — Perfil estadístico de cada árbitro:
              · Faltas / tarjetas por partido
              · Factor de leniencia respecto a la media de la liga
"""

import math
from datetime import date, datetime
from typing import Optional

from config import DECAY_LAMBDA

# ---------------------------------------------------------------------------
# Parámetros del modelo xFouls
# ---------------------------------------------------------------------------
# Cuánto influye la presión de tarjetas sobre la tasa de faltas de un equipo.
# 0 = ignorar ; 0.4 = efecto moderado
ALPHA_CARD_PRESSURE = 0.40

# Promedio histórico de La Liga (calibrado con los datos de la BD):
# ~25.2 faltas/partido → ~12.6 por equipo
AVG_FOULS_EQUIPO = 12.6
AVG_AMARILLAS_EQUIPO = 2.3   # ~4.6 totales / 2
RATIO_AMARILLAS_FALTA_AVG = AVG_AMARILLAS_EQUIPO / AVG_FOULS_EQUIPO  # ≈ 0.183


# ===========================================================================
# Helpers internos
# ===========================================================================

def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _decay(match_date: date, ref: date, lam: float) -> float:
    days = max(0, (ref - match_date).days)
    return math.exp(-lam * days)


def _safe(val, default=0):
    return val if val is not None else default


# ===========================================================================
# Árbitros
# ===========================================================================

def calcular_perfiles_arbitros(partidos: list) -> dict:
    """
    Calcula estadísticas históricas de cada árbitro.

    Devuelve {
        nombre: {
            "partidos": int,
            "fouls_partido": float,
            "amarillas_partido": float,
            "rojas_partido": float,
            "factor_fouls": float,    # vs media liga (1.0 = media)
            "factor_amarillas": float,
            "tipo": str               # "muy estricto" / "estricto" / "normal" / "permisivo"
        }
    }
    """
    acum: dict = {}
    total_fouls = total_amarillas = total_rojas = total_partidos = 0

    for p in partidos:
        ref = p.get("referee", "")
        if not ref:
            continue

        f = _safe(p["home"].get("fouls")) + _safe(p["away"].get("fouls"))
        a = _safe(p["home"].get("yellow_cards")) + _safe(p["away"].get("yellow_cards"))
        r = _safe(p["home"].get("red_cards")) + _safe(p["away"].get("red_cards"))

        if ref not in acum:
            acum[ref] = {"f": 0, "a": 0, "r": 0, "n": 0}
        acum[ref]["f"] += f
        acum[ref]["a"] += a
        acum[ref]["r"] += r
        acum[ref]["n"] += 1

        total_fouls += f
        total_amarillas += a
        total_rojas += r
        total_partidos += 1

    if total_partidos == 0:
        return {}

    avg_f = total_fouls / total_partidos
    avg_a = total_amarillas / total_partidos

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
    """Búsqueda case-insensitive del nombre del árbitro (parcial o completo)."""
    q = nombre_input.lower().strip()
    arbitros = list(perfiles.keys())

    for a in arbitros:
        if a.lower() == q:
            return a

    candidatos = [a for a in arbitros if q in a.lower()]
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        return min(candidatos, key=len)

    candidatos = [a for a in arbitros if a.lower() in q]
    if candidatos:
        return max(candidatos, key=len)

    return None


# ===========================================================================
# xFouls
# ===========================================================================

def calcular_xfouls(
    partidos: list,
    equipo_local: str,
    equipo_visitante: str,
    arbitro: Optional[str] = None,
) -> dict:
    """
    Calcula las faltas esperadas en un partido concreto.

    Fórmula por equipo:
      xFouls_A = base_A × draw_factor_B × ref_factor × card_pressure_A

    · base_A:          tasa histórica de faltas de A (con decay temporal)
    · draw_factor_B:   ¿cuánto provoca B al rival? (fouls drawn / avg liga)
    · ref_factor:      factor de leniencia del árbitro vs media liga
    · card_pressure_A: reducción si A se enchufó de amarillas históricamente

    Devuelve dict con xFouls por equipo, total, componentes y datos del árbitro.
    """
    hoy = date.today()

    # Acumuladores por equipo (con decay)
    t: dict = {}

    total_fouls = total_matches = 0

    for p in partidos:
        fecha = _parse_date(p["date"])
        peso = _decay(fecha, hoy, DECAY_LAMBDA)

        for rol, opp_rol in [("home", "away"), ("away", "home")]:
            team = p[rol]
            nombre = team["name"]
            opp = p[opp_rol]

            f_com = _safe(team.get("fouls"))      # faltas cometidas
            f_drawn = _safe(opp.get("fouls"))      # faltas provocadas = fouls del rival
            yellows = _safe(team.get("yellow_cards"))

            if nombre not in t:
                t[nombre] = {
                    "f_num": 0.0, "f_den": 0.0,     # faltas cometidas
                    "d_num": 0.0, "d_den": 0.0,     # faltas provocadas
                    "y_sum": 0.0, "n": 0,
                }

            t[nombre]["f_num"] += f_com * peso
            t[nombre]["f_den"] += peso
            t[nombre]["d_num"] += f_drawn * peso
            t[nombre]["d_den"] += peso
            t[nombre]["y_sum"] += yellows
            t[nombre]["n"] += 1

        total_fouls += (
            _safe(p["home"].get("fouls")) + _safe(p["away"].get("fouls"))
        )
        total_matches += 1

    avg_total = total_fouls / total_matches if total_matches else 25.2
    avg_equipo = avg_total / 2

    def fouls_rate(nombre: str) -> float:
        d = t.get(nombre, {})
        den = d.get("f_den", 0)
        return d["f_num"] / den if den > 0 else avg_equipo

    def drawn_rate(nombre: str) -> float:
        d = t.get(nombre, {})
        den = d.get("d_den", 0)
        return d["d_num"] / den if den > 0 else avg_equipo

    def card_pressure(nombre: str) -> float:
        d = t.get(nombre, {})
        n = d.get("n", 0)
        if n == 0:
            return 1.0
        yr = d["y_sum"] / n          # amarillas/partido
        fr = fouls_rate(nombre)       # faltas/partido
        ratio = yr / fr if fr > 0 else RATIO_AMARILLAS_FALTA_AVG
        exceso = max(0.0, ratio - RATIO_AMARILLAS_FALTA_AVG)
        return max(0.80, 1.0 - ALPHA_CARD_PRESSURE * exceso)

    # Factor árbitro
    ref_factor = 1.0
    arbitro_info: Optional[dict] = None

    perfiles_refs = calcular_perfiles_arbitros(partidos)
    if arbitro and arbitro in perfiles_refs:
        ref = perfiles_refs[arbitro]
        ref_factor = ref["factor_fouls"]
        arbitro_info = {
            "nombre": arbitro,
            **ref,
        }

    # Cálculo xFouls por equipo
    base_loc = fouls_rate(equipo_local)
    draw_vis = drawn_rate(equipo_visitante)
    draw_factor_vis = draw_vis / avg_equipo
    cp_loc = card_pressure(equipo_local)
    xf_local = base_loc * draw_factor_vis * ref_factor * cp_loc

    base_vis = fouls_rate(equipo_visitante)
    draw_loc = drawn_rate(equipo_local)
    draw_factor_loc = draw_loc / avg_equipo
    cp_vis = card_pressure(equipo_visitante)
    xf_vis = base_vis * draw_factor_loc * ref_factor * cp_vis

    return {
        "xfouls_local": round(xf_local, 1),
        "xfouls_visitante": round(xf_vis, 1),
        "xfouls_total": round(xf_local + xf_vis, 1),
        "avg_liga": round(avg_total, 1),
        "base_local": round(base_loc, 1),
        "base_visitante": round(base_vis, 1),
        "draw_factor_local": round(draw_factor_loc, 2),
        "draw_factor_visitante": round(draw_factor_vis, 2),
        "card_pressure_local": round(cp_loc, 2),
        "card_pressure_visitante": round(cp_vis, 2),
        "ref_factor": round(ref_factor, 2),
        "arbitro": arbitro_info,
        "fouls_drawn_local": round(draw_loc, 1),
        "fouls_drawn_visitante": round(draw_vis, 1),
    }


# ===========================================================================
# xStyle
# ===========================================================================

# Dimensiones de estilo y sus etiquetas para display
STYLE_DIMS = [
    ("tiros",         "Tiros/P"),
    ("precision",     "Precisión tiro"),
    ("corners",       "Corners/P"),
    ("goles",         "Goles/P"),
    ("eficiencia",    "Eficiencia gol"),
    ("fisico",        "Físico"),
    ("riesgo_tarj",   "Riesgo tarjeta"),
    ("faltas_prov",   "Faltas provoca"),
]

# Arquetipos de estilo basados en combinaciones de dimensiones
def _clasificar_estilo(
    fouls: float,
    shots: float,
    shot_acc: float,
    set_piece_r: float,
    physical_r: float,
    goals: float,
    goals_conc: float,
) -> tuple:
    """
    Clasifica el estilo en una etiqueta y una descripción corta.
    Umbrales calibrados con datos reales de La Liga (3 temporadas).
    """
    # Promedios aproximados La Liga:
    # fouls ~12.6 | shots ~11 | shot_acc ~0.37 | set_piece ~0.29 | goals ~1.3
    high_physical = fouls > 13.5
    low_physical  = fouls < 11.0
    high_shots    = shots > 12.0
    low_shots     = shots < 9.5
    high_acc      = shot_acc > 0.40
    high_setpiece = set_piece_r > 0.33
    high_goals    = goals > 1.4

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


def calcular_xstyle(partidos: list) -> dict:
    """
    Calcula el perfil de estilo de juego para cada equipo con decay temporal.

    Devuelve {
        team_name: {
            "tiros", "shots_on_target", "corners", "goles", "goles_conc",
            "fouls", "amarillas", "faltas_prov",        # medias por partido
            "precision",  "eficiencia", "set_piece_ratio",
            "cards_per_foul", "ratio_fisico",           # métricas derivadas
            "tempo",                                    # intensidad del partido
            "estilo", "estilo_desc",                    # clasificación
            "dim_norm": {dim: float 1-10},              # normalizado para display
        }
    }
    """
    hoy = date.today()
    acum: dict = {}

    for p in partidos:
        fecha = _parse_date(p["date"])
        peso = _decay(fecha, hoy, DECAY_LAMBDA)

        for rol, opp_rol in [("home", "away"), ("away", "home")]:
            team = p[rol]
            nombre = team["name"]
            opp = p[opp_rol]

            if nombre not in acum:
                acum[nombre] = {k: 0.0 for k in [
                    "f_w", "y_w", "r_w",
                    "sh_w", "sh_ot_w", "co_w",
                    "gf_w", "ga_w",
                    "drawn_w",          # fouls provocadas al rival
                    "w",
                ]}
                acum[nombre]["n"] = 0

            a = acum[nombre]
            a["f_w"]      += _safe(team.get("fouls")) * peso
            a["y_w"]      += _safe(team.get("yellow_cards")) * peso
            a["r_w"]      += _safe(team.get("red_cards")) * peso
            a["sh_w"]     += _safe(team.get("shots")) * peso
            a["sh_ot_w"]  += _safe(team.get("shots_on_target")) * peso
            a["co_w"]     += _safe(team.get("corners")) * peso
            a["gf_w"]     += _safe(team.get("goals")) * peso
            a["ga_w"]     += _safe(opp.get("goals")) * peso
            a["drawn_w"]  += _safe(opp.get("fouls")) * peso   # fouls del rival = faltas provocadas
            a["w"]        += peso
            a["n"]        += 1

    # Calcular medias y métricas derivadas
    raw: dict = {}
    for nombre, a in acum.items():
        w = a["w"]
        if w == 0:
            continue

        fouls     = a["f_w"] / w
        yellows   = a["y_w"] / w
        shots     = a["sh_w"] / w
        shots_ot  = a["sh_ot_w"] / w
        corners   = a["co_w"] / w
        goals     = a["gf_w"] / w
        goals_c   = a["ga_w"] / w
        drawn     = a["drawn_w"] / w

        precision   = shots_ot / shots if shots > 0 else 0.0
        eficiencia  = goals / shots_ot if shots_ot > 0 else 0.0
        set_piece_r = corners / (shots + corners) if (shots + corners) > 0 else 0.0
        cards_foul  = yellows / fouls if fouls > 0 else 0.0
        ratio_fis   = fouls / (fouls + shots) if (fouls + shots) > 0 else 0.5
        tempo       = (fouls + shots + corners * 0.5)  # intensidad bruta por partido

        estilo, desc = _clasificar_estilo(fouls, shots, precision, set_piece_r, ratio_fis, goals, goals_c)

        raw[nombre] = {
            "tiros":          round(shots, 1),
            "tiros_a_puerta": round(shots_ot, 1),
            "corners":        round(corners, 1),
            "goles":          round(goals, 2),
            "goles_conc":     round(goals_c, 2),
            "fouls":          round(fouls, 1),
            "amarillas":      round(yellows, 2),
            "rojas":          round(a["r_w"] / w, 3),
            "faltas_prov":    round(drawn, 1),
            "precision":      round(precision, 3),
            "eficiencia":     round(eficiencia, 3),
            "set_piece_ratio":round(set_piece_r, 3),
            "cards_per_foul": round(cards_foul, 3),
            "ratio_fisico":   round(ratio_fis, 3),
            "tempo":          round(tempo, 1),
            "estilo":         estilo,
            "estilo_desc":    desc,
            "n_partidos":     a["n"],
        }

    # Normalizar dimensiones a escala 1-10 para display
    _normalizar_dims(raw)
    return raw


def _normalizar_dims(raw: dict) -> None:
    """Añade dim_norm a cada perfil con escala 1-10 relativa a la liga."""
    # Qué dimensiones normalizar y si mayor = más agresivo (True) o menor (True también)
    dims = {
        "tiros":       ("tiros", True),
        "precision":   ("precision", True),
        "corners":     ("corners", True),
        "goles":       ("goles", True),
        "eficiencia":  ("eficiencia", True),
        "fisico":      ("fouls", True),
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


def nivel_intensidad(xfouls_total: float, avg_liga: float = 25.2) -> tuple:
    """
    Clasifica la intensidad esperada del partido según xFouls.
    Devuelve (etiqueta, color_ansi).
    """
    ratio = xfouls_total / avg_liga
    if ratio >= 1.20:
        return "MUY ALTA — partido físico y trabado", "\033[91m"
    if ratio >= 1.08:
        return "ALTA — partido de contacto frecuente", "\033[33m"
    if ratio >= 0.92:
        return "NORMAL — partido dentro de la media", "\033[93m"
    return "BAJA — partido fluido y técnico", "\033[92m"
