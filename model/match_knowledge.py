"""
Match Knowledge Pack — capa de knowledge engineering para análisis prepartido.

Integra: forma reciente, contexto de temporada, compatibilidad de estilos,
métricas esperadas (goles, posesión, faltas, tarjetas) y narrative accionable.
El output alimenta otro modelo estadístico aguas abajo.
"""

from __future__ import annotations

import math
from typing import Optional

from config import (
    FORMA_VENTANA,
    HOME_GOALS_FACTOR,
    JORNADAS_LALIGA,
    FISICO_MUY_ALTO,
    FISICO_ALTO,
    FISICO_NORMAL,
    OFENSIVO_MUY_ABIERTO,
    OFENSIVO_ABIERTO,
    OFENSIVO_EQUILIBRADO,
    AGG_VOL_PESO_FALTAS,
    AGG_VOL_PESO_AMARILLAS,
    AGG_VOL_PESO_ROJAS,
)
from model.helpers import safe

AWAY_GOALS_FACTOR = round(2.0 - HOME_GOALS_FACTOR, 4)


# ── Forma reciente ────────────────────────────────────────────────────────────

def _resultado_para_equipo(partido: dict, equipo: str) -> str:
    """Devuelve W/D/L para el equipo en ese partido a partir de los goles."""
    es_local = partido["home"]["name"] == equipo
    gf = safe(partido["home" if es_local else "away"].get("goals"))
    gc = safe(partido["away" if es_local else "home"].get("goals"))
    if gf > gc:
        return "W"
    if gf == gc:
        return "D"
    return "L"


def calcular_forma_reciente(
    partidos: list[dict],
    equipo: str,
    n: int = FORMA_VENTANA,
) -> dict:
    """
    Analiza los últimos N partidos del equipo.

    Retorna: puntos, goles, faltas, tarjetas, racha y tendencia comparando
    la mitad más reciente vs la más antigua de la ventana.
    """
    mis_partidos = [
        p for p in partidos
        if p["home"]["name"] == equipo or p["away"]["name"] == equipo
    ]
    mis_partidos.sort(key=lambda p: p["date"], reverse=True)
    ultimos = mis_partidos[:n]

    if not ultimos:
        return {
            "partidos_analizados": 0,
            "puntos": 0,
            "puntos_media": 0.0,
            "victorias": 0,
            "empates": 0,
            "derrotas": 0,
            "goles_anotados_media": 0.0,
            "goles_recibidos_media": 0.0,
            "faltas_media": 0.0,
            "tarjetas_media": 0.0,
            "racha": [],
            "racha_str": "—",
            "tendencia": "sin_datos",
        }

    puntos = goles_a = goles_c = faltas = tarjetas = 0
    victorias = empates = derrotas = 0
    racha: list[str] = []

    for p in ultimos:
        es_local = p["home"]["name"] == equipo
        team = p["home" if es_local else "away"]
        opp = p["away" if es_local else "home"]

        res = _resultado_para_equipo(p, equipo)
        racha.append(res)
        if res == "W":
            puntos += 3
            victorias += 1
        elif res == "D":
            puntos += 1
            empates += 1
        else:
            derrotas += 1

        goles_a += safe(team.get("goals"))
        goles_c += safe(opp.get("goals"))
        faltas += safe(team.get("fouls"))
        tarjetas += safe(team.get("yellow_cards")) + safe(team.get("red_cards")) * 2

    n_real = len(ultimos)

    # Tendencia: primera mitad (más reciente) vs segunda mitad (más antigua)
    tendencia = "estable"
    if n_real >= 4:
        mid = n_real // 2
        pts_rec = sum({"W": 3, "D": 1, "L": 0}[r] for r in racha[:mid])
        pts_ant = sum({"W": 3, "D": 1, "L": 0}[r] for r in racha[mid:])
        max_pts = mid * 3
        if max_pts > 0:
            diff = (pts_rec - pts_ant) / max_pts
            if diff > 0.20:
                tendencia = "mejorando"
            elif diff < -0.20:
                tendencia = "empeorando"

    return {
        "partidos_analizados": n_real,
        "puntos": puntos,
        "puntos_media": round(puntos / n_real, 2),
        "victorias": victorias,
        "empates": empates,
        "derrotas": derrotas,
        "goles_anotados_media": round(goles_a / n_real, 2),
        "goles_recibidos_media": round(goles_c / n_real, 2),
        "faltas_media": round(faltas / n_real, 1),
        "tarjetas_media": round(tarjetas / n_real, 2),
        "racha": racha,
        "racha_str": "".join(racha),
        "tendencia": tendencia,
    }


# ── Contexto de temporada ─────────────────────────────────────────────────────

def calcular_contexto_temporada(
    partidos: list[dict],
    equipo_local: str,
    jornada: Optional[int] = None,
) -> dict:
    """
    Determina el tramo de temporada y la presión de cierre.

    Si se provee jornada, la usa directamente. Si no, estima la jornada
    contando partidos jugados por el equipo local en la temporada más reciente.
    """
    if jornada is None:
        season_actual = max((p.get("season", 0) for p in partidos), default=2025)
        partidos_temporada = [
            p for p in partidos
            if p.get("season") == season_actual
            and (p["home"]["name"] == equipo_local or p["away"]["name"] == equipo_local)
        ]
        jornada = len(partidos_temporada) + 1

    jornadas_restantes = max(0, JORNADAS_LALIGA - jornada)

    if jornada <= 10:
        tramo = "inicio"
        tramo_desc = "inicio de temporada (rodaje)"
        presion_final = "baja"
    elif jornada <= 26:
        tramo = "medio"
        tramo_desc = "tramo central de la temporada"
        presion_final = "media"
    else:
        tramo = "final"
        tramo_desc = "recta final de la temporada"
        presion_final = "alta"

    return {
        "jornada_estimada": jornada,
        "jornadas_totales": JORNADAS_LALIGA,
        "jornadas_restantes": jornadas_restantes,
        "tramo": tramo,
        "tramo_desc": tramo_desc,
        "presion_final": presion_final,
        "porcentaje_temporada": round(jornada / JORNADAS_LALIGA * 100, 1),
    }


# ── Goles esperados (xGoals) ──────────────────────────────────────────────────

def _poisson_prob(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def calcular_xgoals(
    xstyles: dict,
    equipo_local: str,
    equipo_visitante: str,
) -> dict:
    """
    Estima goles esperados combinando tasa ofensiva del equipo y
    tasa defensiva del rival. Aplica factores de localía.

    Usa distribución de Poisson para derivar probabilidades de resultado,
    over/under 2.5 y BTTS.
    """
    sl = xstyles.get(equipo_local, {})
    sv = xstyles.get(equipo_visitante, {})

    gl_hist = safe(sl.get("goles"), 1.2)
    gc_vis_hist = safe(sv.get("goles_conc"), 1.2)
    gv_hist = safe(sv.get("goles"), 1.0)
    gc_loc_hist = safe(sl.get("goles_conc"), 1.2)

    xg_local = ((gl_hist + gc_vis_hist) / 2) * HOME_GOALS_FACTOR
    xg_visitante = ((gv_hist + gc_loc_hist) / 2) * AWAY_GOALS_FACTOR
    xg_total = xg_local + xg_visitante

    # Poisson: over 2.5 goles
    p_over25 = 1.0 - sum(_poisson_prob(xg_total, k) for k in range(3))

    # BTTS: P(local ≥ 1) × P(visitante ≥ 1)
    p_btts = (1.0 - _poisson_prob(xg_local, 0)) * (1.0 - _poisson_prob(xg_visitante, 0))

    # Probabilidades de resultado 1X2 (convolution Poisson)
    p_local_win = p_draw = p_vis_win = 0.0
    for i in range(9):
        for j in range(9):
            p = _poisson_prob(xg_local, i) * _poisson_prob(xg_visitante, j)
            if i > j:
                p_local_win += p
            elif i == j:
                p_draw += p
            else:
                p_vis_win += p

    # Normalizar a 1.0 (las colas infinitas aportan muy poco)
    total_prob = p_local_win + p_draw + p_vis_win
    if total_prob > 0:
        p_local_win /= total_prob
        p_draw /= total_prob
        p_vis_win /= total_prob

    return {
        "xg_local": round(xg_local, 2),
        "xg_visitante": round(xg_visitante, 2),
        "xg_total": round(xg_total, 2),
        "prob_over25": round(p_over25, 3),
        "prob_under25": round(1.0 - p_over25, 3),
        "prob_btts": round(p_btts, 3),
        "prob_local_win": round(p_local_win, 3),
        "prob_draw": round(p_draw, 3),
        "prob_visitante_win": round(p_vis_win, 3),
    }


# ── Posesión esperada ─────────────────────────────────────────────────────────

def calcular_xposesion(
    xstyles: dict,
    equipo_local: str,
    equipo_visitante: str,
) -> dict:
    """
    Posesión esperada. Usa promedio histórico de posesión si disponible,
    si no aplica proxy por ratio de tempo (tiros + corners × 0.5 + faltas × 0.3).

    La ventaja local añade ~1.5 puntos porcentuales.
    """
    sl = xstyles.get(equipo_local, {})
    sv = xstyles.get(equipo_visitante, {})

    poss_l_hist = sl.get("posesion")
    poss_v_hist = sv.get("posesion")

    if poss_l_hist is not None and poss_v_hist is not None:
        poss_local = (poss_l_hist + (100.0 - poss_v_hist)) / 2.0
        fuente = "historico_posesion"
    else:
        tempo_l = safe(sl.get("tempo"), 0)
        tempo_v = safe(sv.get("tempo"), 0)
        total = tempo_l + tempo_v
        if total > 0:
            poss_local = 100.0 * tempo_l / total
            poss_local = max(35.0, min(65.0, poss_local))
        else:
            poss_local = 50.0
        fuente = "proxy_tempo"

    # Ligera ventaja local de posesión (~1.5pp)
    poss_local = min(70.0, poss_local + 1.5)
    poss_visitante = 100.0 - poss_local

    return {
        "posesion_local": round(poss_local, 1),
        "posesion_visitante": round(poss_visitante, 1),
        "fuente": fuente,
    }


# ── Tarjetas esperadas (xTarjetas) ────────────────────────────────────────────

def calcular_xtarjetas(
    xfouls_result: dict,
    xstyles: dict,
    equipo_local: str,
    equipo_visitante: str,
    ref_perfiles: Optional[dict] = None,
    arbitro: Optional[str] = None,
) -> dict:
    """
    Tarjetas esperadas = xFouls × tasa_tarjeta_por_falta, ajustado
    por el factor del árbitro sobre amarillas.
    """
    sl = xstyles.get(equipo_local, {})
    sv = xstyles.get(equipo_visitante, {})

    cpf_local = safe(sl.get("cards_per_foul"), 0.18)
    cpf_visitante = safe(sv.get("cards_per_foul"), 0.18)

    ref_cards_factor = 1.0
    if ref_perfiles and arbitro and arbitro in ref_perfiles:
        ref_cards_factor = safe(ref_perfiles[arbitro].get("factor_amarillas"), 1.0)

    xa_local = xfouls_result["xfouls_local"] * cpf_local * ref_cards_factor
    xa_visitante = xfouls_result["xfouls_visitante"] * cpf_visitante * ref_cards_factor
    xa_total = xa_local + xa_visitante

    rojas_l = safe(sl.get("rojas"), 0.04)
    rojas_v = safe(sv.get("rojas"), 0.04)

    return {
        # Compat backward-compatible: xtarjetas_* se interpreta como amarillas esperadas.
        "xtarjetas_local": round(xa_local, 2),
        "xtarjetas_visitante": round(xa_visitante, 2),
        "xtarjetas_total": round(xa_total, 2),
        "xamarillas_local": round(xa_local, 2),
        "xamarillas_visitante": round(xa_visitante, 2),
        "xamarillas_total": round(xa_total, 2),
        "xrojas_total": round(rojas_l + rojas_v, 3),
        "ref_factor_tarjetas": round(ref_cards_factor, 3),
        "cpf_local": round(cpf_local, 3),
        "cpf_visitante": round(cpf_visitante, 3),
    }


def calcular_agresividad_volumen(xfouls_result: dict, xtarjetas: dict) -> dict:
    """
    Agresividad por volumen:
      base principal = xFouls
      ajuste secundario = amarillas/rojas esperadas como proxy de fricción.
    """
    xf_l = safe(xfouls_result.get("xfouls_local"), 0.0)
    xf_v = safe(xfouls_result.get("xfouls_visitante"), 0.0)
    xf_t = xf_l + xf_v

    ya_l = safe(xtarjetas.get("xamarillas_local"), safe(xtarjetas.get("xtarjetas_local"), 0.0))
    ya_v = safe(xtarjetas.get("xamarillas_visitante"), safe(xtarjetas.get("xtarjetas_visitante"), 0.0))
    rr_t = safe(xtarjetas.get("xrojas_total"), 0.0)

    # Reparto de rojas esperadas proporcional al volumen de faltas previsto.
    if xf_t > 0:
        share_l = xf_l / xf_t
    else:
        share_l = 0.5
    rr_l = rr_t * share_l
    rr_v = rr_t - rr_l

    vol_l = (
        AGG_VOL_PESO_FALTAS * xf_l
        + AGG_VOL_PESO_AMARILLAS * ya_l
        + AGG_VOL_PESO_ROJAS * rr_l
    )
    vol_v = (
        AGG_VOL_PESO_FALTAS * xf_v
        + AGG_VOL_PESO_AMARILLAS * ya_v
        + AGG_VOL_PESO_ROJAS * rr_v
    )

    return {
        "local": round(vol_l, 2),
        "visitante": round(vol_v, 2),
        "total": round(vol_l + vol_v, 2),
        "pesos": {
            "faltas": AGG_VOL_PESO_FALTAS,
            "amarillas": AGG_VOL_PESO_AMARILLAS,
            "rojas": AGG_VOL_PESO_ROJAS,
        },
        "componentes": {
            "xfouls_local": round(xf_l, 2),
            "xfouls_visitante": round(xf_v, 2),
            "xamarillas_local": round(ya_l, 2),
            "xamarillas_visitante": round(ya_v, 2),
            "xrojas_local": round(rr_l, 3),
            "xrojas_visitante": round(rr_v, 3),
        },
    }


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _map_level(level: str, low: float, mid: float, high: float, default: float) -> float:
    m = {
        "baja": low,
        "bajo": low,
        "media": mid,
        "medio": mid,
        "alta": high,
        "alto": high,
    }
    return m.get((level or "").strip().lower(), default)


def _norm_comp(comp: Optional[str]) -> str:
    c = (comp or "").strip().lower()
    aliases = {
        "champions": "ucl",
        "champions_league": "ucl",
        "europa": "uel",
        "europa_league": "uel",
        "conference": "uecl",
        "conference_league": "uecl",
        "none": "none",
        "ninguna": "none",
        "": "none",
    }
    c = aliases.get(c, c)
    if c in {"liga", "copa", "ucl", "uel", "uecl", "none"}:
        return c
    return "none"


def _priority_by_comp(next_competition: str, days_to_next: Optional[int]) -> float:
    # Cuanto más alta la competición externa y más cerca en días, más resta foco liguero.
    base = {
        "none": 0.15,
        "liga": 0.10,
        "copa": 0.35,
        "uecl": 0.45,
        "uel": 0.60,
        "ucl": 0.78,
    }.get(next_competition, 0.15)
    if days_to_next is None:
        return base
    if days_to_next <= 3:
        return _clip(base + 0.20, 0.0, 1.0)
    if days_to_next <= 5:
        return _clip(base + 0.10, 0.0, 1.0)
    if days_to_next <= 7:
        return base
    return max(0.05, base - 0.10)


def _objetivo_liga_score(objetivo: Optional[str], urg_default: float) -> float:
    key = (objetivo or "").strip().lower()
    m = {
        "titulo": 0.95,
        "top4": 0.75,
        "ucl": 0.75,
        "uel": 0.65,
        "descenso": 0.95,
        "salvacion": 0.90,
        "media_tabla": 0.45,
        "none": urg_default,
        "": urg_default,
    }
    return m.get(key, urg_default)


def _fatiga_por_descanso(days_since_last: Optional[int]) -> float:
    if days_since_last is None:
        return 0.35
    if days_since_last <= 3:
        return 0.80
    if days_since_last <= 5:
        return 0.55
    if days_since_last <= 7:
        return 0.35
    return 0.15


def _build_team_context(raw: dict) -> dict:
    urg_base = _map_level(raw.get("liga_urgencia", "media"), 0.30, 0.55, 0.85, 0.55)
    urg_obj = _objetivo_liga_score(raw.get("objetivo_liga"), urg_base)
    urg = round((urg_base * 0.5 + urg_obj * 0.5), 3)
    rot = _map_level(raw.get("riesgo_rotacion", "medio"), 0.25, 0.45, 0.75, 0.45)
    fat = _fatiga_por_descanso(raw.get("days_since_last"))

    last_comp = _norm_comp(raw.get("last_competition"))
    next_comp = _norm_comp(raw.get("next_competition"))

    # Compatibilidad con flags antiguos (solo UCL).
    if last_comp == "none" and raw.get("last_ucl", False):
        last_comp = "ucl"
    if next_comp == "none" and raw.get("next_ucl", False):
        next_comp = "ucl"

    prx = _priority_by_comp(next_comp, raw.get("days_to_next"))

    if last_comp in {"ucl", "uel", "uecl"}:
        extra_fat = {"ucl": 0.15, "uel": 0.10, "uecl": 0.08}[last_comp]
        fat = _clip(fat + extra_fat, 0.0, 1.0)
    if next_comp in {"ucl", "uel", "uecl"} and raw.get("days_to_next") is not None and raw.get("days_to_next") <= 4:
        rot = _clip(rot + 0.15, 0.0, 1.0)

    # Índice de contexto competitivo (ICC):
    # + urgencia liga, - fatiga, - prioridad de competición externa, - rotación.
    icc = 0.35 * urg - 0.25 * fat - 0.20 * prx - 0.20 * rot
    icc = _clip(icc, -1.0, 1.0)

    if icc >= 0.20:
        lectura = "foco competitivo alto en liga"
    elif icc <= -0.20:
        lectura = "foco de liga condicionado por calendario/otras prioridades"
    else:
        lectura = "foco liguero neutro"

    return {
        "days_since_last": raw.get("days_since_last"),
        "days_to_next": raw.get("days_to_next"),
        "last_competition": last_comp,
        "next_competition": next_comp,
        "last_ucl": last_comp == "ucl",
        "next_ucl": next_comp == "ucl",
        "liga_urgencia": raw.get("liga_urgencia", "media"),
        "objetivo_liga": raw.get("objetivo_liga", "none"),
        "riesgo_rotacion": raw.get("riesgo_rotacion", "medio"),
        "scores": {
            "urgencia_liga": round(urg, 3),
            "fatiga": round(fat, 3),
            "prioridad_extra": round(prx, 3),
            "rotacion": round(rot, 3),
            "icc": round(icc, 3),
        },
        "lectura": lectura,
        "factors": {
            "xg_factor": round(1.0 + 0.10 * icc, 4),
            "xfouls_factor": round(1.0 + 0.12 * icc, 4),
            "posesion_delta_pp": round(2.0 * icc, 3),
        },
    }


def calcular_contexto_competitivo(contexto_input: Optional[dict] = None) -> dict:
    """
    Construye el contexto competitivo de local/visitante a partir de señales:
    descanso, proximidad UCL, urgencia liguera y riesgo de rotación.
    """
    if contexto_input is None:
        contexto_input = {}
    local_raw = contexto_input.get("local", {})
    visitante_raw = contexto_input.get("visitante", {})
    return {
        "local": _build_team_context(local_raw),
        "visitante": _build_team_context(visitante_raw),
    }


def _recalcular_probs_desde_xg(xg_local: float, xg_visitante: float) -> dict:
    p_over25 = 1.0 - sum(_poisson_prob(xg_local + xg_visitante, k) for k in range(3))
    p_btts = (1.0 - _poisson_prob(xg_local, 0)) * (1.0 - _poisson_prob(xg_visitante, 0))

    p_local_win = p_draw = p_vis_win = 0.0
    for i in range(9):
        for j in range(9):
            p = _poisson_prob(xg_local, i) * _poisson_prob(xg_visitante, j)
            if i > j:
                p_local_win += p
            elif i == j:
                p_draw += p
            else:
                p_vis_win += p
    total_prob = p_local_win + p_draw + p_vis_win
    if total_prob > 0:
        p_local_win /= total_prob
        p_draw /= total_prob
        p_vis_win /= total_prob
    return {
        "prob_over25": round(p_over25, 3),
        "prob_under25": round(1.0 - p_over25, 3),
        "prob_btts": round(p_btts, 3),
        "prob_local_win": round(p_local_win, 3),
        "prob_draw": round(p_draw, 3),
        "prob_visitante_win": round(p_vis_win, 3),
    }


def calcular_xvolumen_eventos(xstyles: dict, equipo_local: str, equipo_visitante: str, contexto_comp: dict) -> dict:
    """
    Estima volúmenes de eventos (tiros/corners/tiros a puerta) y los ajusta
    con factores de contexto competitivo para mantener coherencia.
    """
    sl = xstyles.get(equipo_local, {})
    sv = xstyles.get(equipo_visitante, {})

    f_l = contexto_comp["local"]["factors"]["xg_factor"]
    f_v = contexto_comp["visitante"]["factors"]["xg_factor"]

    sh_l = round(safe(sl.get("tiros"), 11.0) * f_l, 1)
    sh_v = round(safe(sv.get("tiros"), 11.0) * f_v, 1)
    sot_l = round(safe(sl.get("tiros_a_puerta"), 4.0) * f_l, 1)
    sot_v = round(safe(sv.get("tiros_a_puerta"), 4.0) * f_v, 1)
    co_l = round(safe(sl.get("corners"), 4.8) * f_l, 1)
    co_v = round(safe(sv.get("corners"), 4.8) * f_v, 1)

    return {
        "shots_local": sh_l,
        "shots_visitante": sh_v,
        "shots_total": round(sh_l + sh_v, 1),
        "shots_on_target_local": sot_l,
        "shots_on_target_visitante": sot_v,
        "shots_on_target_total": round(sot_l + sot_v, 1),
        "corners_local": co_l,
        "corners_visitante": co_v,
        "corners_total": round(co_l + co_v, 1),
        # Placeholder por si se integra más adelante dato real de offsides.
        "offsides_total": None,
    }


# ── Compatibilidad de estilos ─────────────────────────────────────────────────

_FISICO_STYLES = {"FÍSICO-DEFENSIVO", "INTENSO", "DIRECTO-FÍSICO"}
_TECH_STYLES = {"TÉCNICO-OFENSIVO", "POSESIÓN"}


def calcular_compatibilidad_estilos(
    xstyles: dict,
    equipo_local: str,
    equipo_visitante: str,
) -> dict:
    """
    Clasifica el tipo de partido esperado según el cruce de perfiles de estilo,
    volumen físico y ritmo ofensivo.
    """
    sl = xstyles.get(equipo_local, {})
    sv = xstyles.get(equipo_visitante, {})

    estilo_l = sl.get("estilo", "EQUILIBRADO")
    estilo_v = sv.get("estilo", "EQUILIBRADO")

    fouls_l = safe(sl.get("fouls"), 12.0)
    fouls_v = safe(sv.get("fouls"), 12.0)
    shots_l = safe(sl.get("tiros"), 11.0)
    shots_v = safe(sv.get("tiros"), 11.0)
    poss_l = sl.get("posesion") or 50.0
    poss_v = sv.get("posesion") or 50.0

    fisico_total = fouls_l + fouls_v
    ritmo_ofensivo = shots_l + shots_v
    dif_posesion = abs(poss_l - poss_v)

    if fisico_total >= FISICO_MUY_ALTO:
        fisico_label = "muy alto"
    elif fisico_total >= FISICO_ALTO:
        fisico_label = "alto"
    elif fisico_total >= FISICO_NORMAL:
        fisico_label = "normal"
    else:
        fisico_label = "bajo"

    if ritmo_ofensivo >= OFENSIVO_MUY_ABIERTO:
        ofensivo_label = "muy abierto"
    elif ritmo_ofensivo >= OFENSIVO_ABIERTO:
        ofensivo_label = "abierto"
    elif ritmo_ofensivo >= OFENSIVO_EQUILIBRADO:
        ofensivo_label = "equilibrado"
    else:
        ofensivo_label = "cerrado"

    if dif_posesion > 12:
        control_juego = "dominio claro de un equipo"
    elif dif_posesion > 6:
        control_juego = "ligero control de un equipo"
    else:
        control_juego = "equilibrio en posesión"

    es_fisico_l = estilo_l in _FISICO_STYLES
    es_fisico_v = estilo_v in _FISICO_STYLES
    es_tec_l = estilo_l in _TECH_STYLES
    es_tec_v = estilo_v in _TECH_STYLES

    physical = es_fisico_l or es_fisico_v
    technical = es_tec_l or es_tec_v

    if physical and technical:
        tipo_partido = "PARTIDO MIXTO"
        tipo_desc = "Choque de estilos opuestos, partido imprevisible con transiciones"
    elif physical:
        tipo_partido = "PARTIDO FÍSICO"
        tipo_desc = "Alta intensidad y contacto, bajo ritmo técnico"
    elif technical:
        tipo_partido = "PARTIDO TÉCNICO"
        tipo_desc = "Predominio del juego elaborado y la posesión"
    elif fisico_total >= FISICO_ALTO and ritmo_ofensivo >= OFENSIVO_ABIERTO:
        tipo_partido = "PARTIDO ABIERTO E INTENSO"
        tipo_desc = "Alto ritmo en ambas fases con contacto frecuente"
    elif ritmo_ofensivo >= OFENSIVO_MUY_ABIERTO:
        tipo_partido = "PARTIDO OFENSIVO"
        tipo_desc = "Alto volumen de remates de ambos equipos"
    elif fisico_total < FISICO_NORMAL and ritmo_ofensivo < OFENSIVO_EQUILIBRADO:
        tipo_partido = "PARTIDO CONTROLADO"
        tipo_desc = "Bajo ritmo, búsqueda de la solidez defensiva"
    else:
        tipo_partido = "PARTIDO EQUILIBRADO"
        tipo_desc = "Sin tendencias dominantes claras entre ambos equipos"

    derived_angles: list[str] = []
    if fouls_l > 14.0 or fouls_v > 14.0:
        derived_angles.append("alto_riesgo_disciplinario")
    if shots_l > 13.0 and shots_v > 13.0:
        derived_angles.append("partido_abierto_bilateral")
    if dif_posesion > 10.0:
        derived_angles.append("asimetria_posesion")
    if fisico_total >= FISICO_MUY_ALTO:
        derived_angles.append("duelo_fisico_extremo")
    if shots_l < 9.0 and shots_v < 9.0:
        derived_angles.append("partido_cerrado_bajo_ritmo")
    if es_fisico_l and es_tec_v:
        derived_angles.append("presion_vs_posesion_visitante")
    if es_tec_l and es_fisico_v:
        derived_angles.append("posesion_local_vs_presion_visitante")

    return {
        "tipo_partido": tipo_partido,
        "tipo_partido_desc": tipo_desc,
        "estilos": {"local": estilo_l, "visitante": estilo_v},
        "fisico_total": round(fisico_total, 1),
        "fisico_label": fisico_label,
        "ritmo_ofensivo": round(ritmo_ofensivo, 1),
        "ofensivo_label": ofensivo_label,
        "control_juego": control_juego,
        "derived_angles": derived_angles,
    }


# ── Narrative ─────────────────────────────────────────────────────────────────

def _generar_narrative(
    equipo_local: str,
    equipo_visitante: str,
    forma_local: dict,
    forma_visitante: dict,
    xgoals: dict,
    xposesion: dict,
    xtarjetas: dict,
    xfouls_result: dict,
    agresividad_vol: dict,
    contexto_comp: dict,
    compat: dict,
    contexto: dict,
) -> list[str]:
    xf_local = xfouls_result.get("xfouls_local", xfouls_result.get("local", 0.0))
    xf_visit = xfouls_result.get("xfouls_visitante", xfouls_result.get("visitante", 0.0))
    xf_total = xfouls_result.get("xfouls_total", xfouls_result.get("total", 0.0))

    """Genera bullets narrativos accionables para el knowledge pack."""
    bullets: list[str] = []

    # Forma reciente
    def _forma_texto(equipo: str, forma: dict) -> str:
        if forma["partidos_analizados"] == 0:
            return f"{equipo}: sin datos de forma reciente."
        return (
            f"{equipo} acumula {forma['puntos']} pts en sus últimos "
            f"{forma['partidos_analizados']} partidos "
            f"({forma['victorias']}V {forma['empates']}E {forma['derrotas']}D) "
            f"— racha: {forma['racha_str']} — tendencia: {forma['tendencia']}."
        )

    bullets.append(f"[FORMA LOCAL] {_forma_texto(equipo_local, forma_local)}")
    bullets.append(f"[FORMA VISITANTE] {_forma_texto(equipo_visitante, forma_visitante)}")

    # Tipo de partido
    bullets.append(
        f"[GUION] Se espera un {compat['tipo_partido']}: {compat['tipo_partido_desc']}. "
        f"Intensidad física {compat['fisico_label']} · ritmo ofensivo {compat['ofensivo_label']} · "
        f"{compat['control_juego']}."
    )

    # Métricas esperadas
    bullets.append(
        f"[MÉTRICAS] xG {xgoals['xg_local']}/{xgoals['xg_visitante']} (total {xgoals['xg_total']}) · "
        f"Posesión {xposesion['posesion_local']}%/{xposesion['posesion_visitante']}% · "
        f"xFaltas {xf_local}/{xf_visit} "
        f"(total {xf_total}) · "
        f"Agresividad-volumen {agresividad_vol['local']:.1f}/{agresividad_vol['visitante']:.1f} "
        f"(total {agresividad_vol['total']:.1f}) · "
        f"xTarjetas {xtarjetas['xtarjetas_local']:.1f}/{xtarjetas['xtarjetas_visitante']:.1f} "
        f"(total {xtarjetas['xtarjetas_total']:.1f})."
    )

    # Probabilidades
    bullets.append(
        f"[PROBABILIDADES] Local {xgoals['prob_local_win']*100:.0f}% · "
        f"Empate {xgoals['prob_draw']*100:.0f}% · "
        f"Visitante {xgoals['prob_visitante_win']*100:.0f}% · "
        f"Over 2.5 {xgoals['prob_over25']*100:.0f}% · "
        f"BTTS {xgoals['prob_btts']*100:.0f}%."
    )

    # Contexto
    bullets.append(
        f"[CONTEXTO] Jornada ~{contexto['jornada_estimada']}/{contexto['jornadas_totales']} "
        f"({contexto['tramo_desc']}) · "
        f"{contexto['jornadas_restantes']} jornadas restantes · "
        f"presión de cierre: {contexto['presion_final']}."
    )
    ccl = contexto_comp["local"]
    ccv = contexto_comp["visitante"]
    bullets.append(
        f"[CONTEXTO COMPETITIVO] {equipo_local}: ICC {ccl['scores']['icc']} ({ccl['lectura']}) · "
        f"{equipo_visitante}: ICC {ccv['scores']['icc']} ({ccv['lectura']})."
    )

    # Ángulos derivados
    if compat["derived_angles"]:
        bullets.append(
            f"[ÁNGULOS] Señales identificadas: {', '.join(compat['derived_angles'])}."
        )

    return bullets


# ── Ensamblador principal ─────────────────────────────────────────────────────

def ensamblar_knowledge_pack(
    equipo_local: str,
    equipo_visitante: str,
    partidos: list[dict],
    xstyles: dict,
    xfouls_result: dict,
    ref_perfiles: Optional[dict] = None,
    arbitro: Optional[str] = None,
    jornada: Optional[int] = None,
    contexto_competitivo_input: Optional[dict] = None,
) -> dict:
    """
    Ensambla el knowledge pack completo para el partido solicitado.

    El pack integra: forma reciente, contexto temporal, métricas esperadas
    (xGoals, xPosesion, xTarjetas), compatibilidad de estilos y narrative.
    """
    forma_local = calcular_forma_reciente(partidos, equipo_local)
    forma_visitante = calcular_forma_reciente(partidos, equipo_visitante)
    contexto = calcular_contexto_temporada(partidos, equipo_local, jornada)
    xgoals = calcular_xgoals(xstyles, equipo_local, equipo_visitante)
    xposesion = calcular_xposesion(xstyles, equipo_local, equipo_visitante)
    xtarjetas = calcular_xtarjetas(
        xfouls_result, xstyles, equipo_local, equipo_visitante, ref_perfiles, arbitro,
    )
    contexto_comp = calcular_contexto_competitivo(contexto_competitivo_input)
    xvol = calcular_xvolumen_eventos(xstyles, equipo_local, equipo_visitante, contexto_comp)

    # Ajustes contextuales sobre métricas esperadas.
    f_xg_l = contexto_comp["local"]["factors"]["xg_factor"]
    f_xg_v = contexto_comp["visitante"]["factors"]["xg_factor"]
    xg_local_adj = round(xgoals["xg_local"] * f_xg_l, 2)
    xg_visitante_adj = round(xgoals["xg_visitante"] * f_xg_v, 2)
    xgoals_adj = {
        "xg_local": xg_local_adj,
        "xg_visitante": xg_visitante_adj,
        "xg_total": round(xg_local_adj + xg_visitante_adj, 2),
    }
    xgoals_adj.update(_recalcular_probs_desde_xg(xg_local_adj, xg_visitante_adj))

    f_xf_l = contexto_comp["local"]["factors"]["xfouls_factor"]
    f_xf_v = contexto_comp["visitante"]["factors"]["xfouls_factor"]
    xf_local_adj = round(xfouls_result["xfouls_local"] * f_xf_l, 1)
    xf_visitante_adj = round(xfouls_result["xfouls_visitante"] * f_xf_v, 1)
    xf_total_adj = round(xf_local_adj + xf_visitante_adj, 1)

    xfouls_adj = {
        "local": xf_local_adj,
        "visitante": xf_visitante_adj,
        "total": xf_total_adj,
        "avg_liga": xfouls_result["avg_liga"],
    }

    delta_poss_local = contexto_comp["local"]["factors"]["posesion_delta_pp"]
    delta_poss_visit = contexto_comp["visitante"]["factors"]["posesion_delta_pp"]
    poss_local_adj = _clip(xposesion["posesion_local"] + delta_poss_local - delta_poss_visit, 35.0, 65.0)
    xposesion_adj = {
        "posesion_local": round(poss_local_adj, 1),
        "posesion_visitante": round(100.0 - poss_local_adj, 1),
        "fuente": xposesion["fuente"],
    }

    # Tarjetas ajustadas proporcionalmente al cambio en xFouls.
    ratio_l = xf_local_adj / max(0.1, xfouls_result["xfouls_local"])
    ratio_v = xf_visitante_adj / max(0.1, xfouls_result["xfouls_visitante"])
    xtarjetas_adj = {
        **xtarjetas,
        "xtarjetas_local": round(xtarjetas["xtarjetas_local"] * ratio_l, 2),
        "xtarjetas_visitante": round(xtarjetas["xtarjetas_visitante"] * ratio_v, 2),
        "xtarjetas_total": round(
            xtarjetas["xtarjetas_local"] * ratio_l + xtarjetas["xtarjetas_visitante"] * ratio_v, 2
        ),
        "xamarillas_local": round(xtarjetas["xamarillas_local"] * ratio_l, 2),
        "xamarillas_visitante": round(xtarjetas["xamarillas_visitante"] * ratio_v, 2),
        "xamarillas_total": round(
            xtarjetas["xamarillas_local"] * ratio_l + xtarjetas["xamarillas_visitante"] * ratio_v, 2
        ),
    }

    agresividad_vol = calcular_agresividad_volumen(
        {
            "xfouls_local": xf_local_adj,
            "xfouls_visitante": xf_visitante_adj,
        },
        xtarjetas_adj,
    )
    compat = calcular_compatibilidad_estilos(xstyles, equipo_local, equipo_visitante)
    narrative = _generar_narrative(
        equipo_local, equipo_visitante,
        forma_local, forma_visitante,
        xgoals_adj, xposesion_adj, xtarjetas_adj,
        xfouls_adj, agresividad_vol, contexto_comp, compat, contexto,
    )

    return {
        "forma": {
            "local": forma_local,
            "visitante": forma_visitante,
        },
        "contexto_temporada": contexto,
        "contexto_competitivo": contexto_comp,
        "expected_metrics": {
            "xgoals": xgoals_adj,
            "xposesion": xposesion_adj,
            "xtarjetas": xtarjetas_adj,
            "agresividad_volumen": agresividad_vol,
            "xfouls": xfouls_adj,
            "xvolumen_eventos": xvol,
        },
        "base_metrics": {
            "xgoals": xgoals,
            "xposesion": xposesion,
            "xtarjetas": xtarjetas,
            "xfouls": {
                "local": xfouls_result["xfouls_local"],
                "visitante": xfouls_result["xfouls_visitante"],
                "total": xfouls_result["xfouls_total"],
                "avg_liga": xfouls_result["avg_liga"],
            },
        },
        "compatibilidad_estilos": compat,
        "narrative": narrative,
        "market_signal": None,
    }
