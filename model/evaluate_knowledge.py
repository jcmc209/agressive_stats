"""
evaluate_knowledge.py — Evaluación de utilidad y calibración del Knowledge Pack.

Mide en retrospectiva (backtesting) la calidad predictiva de las métricas
esperadas generadas por el knowledge pack, comparándolas con los resultados
reales presentes en el histórico de LaLiga.

Uso:
    python -c "from model.evaluate_knowledge import evaluar; evaluar(partidos, xstyles)"

O desde main.py con --evaluar (pendiente de añadir al CLI si se requiere).
"""

from __future__ import annotations

import math
from typing import Optional

from model.helpers import parse_date, safe
from model.match_knowledge import (
    calcular_xgoals,
    calcular_xposesion,
    calcular_xtarjetas,
    calcular_forma_reciente,
)
from model.xfouls import calcular_xfouls


# ── Métricas de calibración ───────────────────────────────────────────────────

def _mae(predichos: list[float], reales: list[float]) -> float:
    """Mean Absolute Error."""
    if not predichos:
        return float("nan")
    return sum(abs(p - r) for p, r in zip(predichos, reales)) / len(predichos)


def _rmse(predichos: list[float], reales: list[float]) -> float:
    """Root Mean Squared Error."""
    if not predichos:
        return float("nan")
    return math.sqrt(sum((p - r) ** 2 for p, r in zip(predichos, reales)) / len(predichos))


def _log_loss(prob_predicha: list[float], ocurrido: list[int]) -> float:
    """Log-loss para probabilidades binarias."""
    if not prob_predicha:
        return float("nan")
    eps = 1e-9
    return -sum(
        y * math.log(max(eps, p)) + (1 - y) * math.log(max(eps, 1 - p))
        for p, y in zip(prob_predicha, ocurrido)
    ) / len(prob_predicha)


def _brier(prob_predicha: list[float], ocurrido: list[int]) -> float:
    """Brier score (0=perfecto, 0.25=sin información)."""
    if not prob_predicha:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(prob_predicha, ocurrido)) / len(prob_predicha)


# ── Backtesting principal ─────────────────────────────────────────────────────

def evaluar(
    partidos: list[dict],
    xstyles: dict,
    n_ultimos: int = 100,
    verbose: bool = True,
) -> dict:
    """
    Evalúa la calibración del knowledge pack sobre los últimos N partidos.

    Para cada partido de la muestra:
      - usa los partidos anteriores como contexto de entrenamiento
      - predice con calcular_xgoals, calcular_xposesion, calcular_xfouls
      - compara con el resultado real

    Métricas retornadas:
      xGoals: MAE, RMSE (vs goles reales por equipo)
      Over 2.5: Log-loss, Brier score
      BTTS: Log-loss, Brier score
      1X2: Log-loss, Brier score
      xPosesion: MAE (solo cuando posesión real está disponible)
    """
    partidos_sorted = sorted(partidos, key=lambda p: p["date"])
    muestra = partidos_sorted[-n_ultimos:]

    errores_xg_local: list[float] = []
    errores_xg_vis: list[float] = []
    prob_over25: list[float] = []
    real_over25: list[int] = []
    prob_btts: list[float] = []
    real_btts: list[int] = []
    prob_local_win: list[float] = []
    real_local_win: list[int] = []
    prob_draw: list[float] = []
    real_draw: list[int] = []
    prob_vis_win: list[float] = []
    real_vis_win: list[int] = []
    errores_poss: list[float] = []

    for i, partido in enumerate(muestra):
        ea = partido["home"]["name"]
        eb = partido["away"]["name"]

        contexto = [p for p in partidos_sorted if p["date"] < partido["date"]]
        if len(contexto) < 20:
            continue

        if ea not in xstyles or eb not in xstyles:
            continue

        xg = calcular_xgoals(xstyles, ea, eb)
        xf = calcular_xfouls(contexto, ea, eb)

        goles_l_real = safe(partido["home"].get("goals"))
        goles_v_real = safe(partido["away"].get("goals"))
        goles_total_real = goles_l_real + goles_v_real

        errores_xg_local.append(xg["xg_local"] - goles_l_real)
        errores_xg_vis.append(xg["xg_visitante"] - goles_v_real)

        prob_over25.append(xg["prob_over25"])
        real_over25.append(1 if goles_total_real > 2 else 0)

        prob_btts.append(xg["prob_btts"])
        real_btts.append(1 if goles_l_real > 0 and goles_v_real > 0 else 0)

        prob_local_win.append(xg["prob_local_win"])
        real_local_win.append(1 if goles_l_real > goles_v_real else 0)

        prob_draw.append(xg["prob_draw"])
        real_draw.append(1 if goles_l_real == goles_v_real else 0)

        prob_vis_win.append(xg["prob_visitante_win"])
        real_vis_win.append(1 if goles_v_real > goles_l_real else 0)

        poss_l_real = partido["home"].get("possession")
        poss_v_real = partido["away"].get("possession")
        if poss_l_real is not None and poss_v_real is not None:
            xposs = calcular_xposesion(xstyles, ea, eb)
            errores_poss.append(xposs["posesion_local"] - float(poss_l_real))

    n = len(prob_over25)
    if n == 0:
        if verbose:
            print("No hay suficientes partidos para evaluar.")
        return {}

    resultados = {
        "n_partidos_evaluados": n,
        "xgoals": {
            "mae_local": round(_mae([abs(e) for e in errores_xg_local], [0.0] * len(errores_xg_local)), 3),
            "mae_visitante": round(_mae([abs(e) for e in errores_xg_vis], [0.0] * len(errores_xg_vis)), 3),
            "rmse_local": round(_rmse(errores_xg_local, [0.0] * len(errores_xg_local)), 3),
            "rmse_visitante": round(_rmse(errores_xg_vis, [0.0] * len(errores_xg_vis)), 3),
            "bias_local": round(sum(errores_xg_local) / len(errores_xg_local), 3),
            "bias_visitante": round(sum(errores_xg_vis) / len(errores_xg_vis), 3),
        },
        "over25": {
            "log_loss": round(_log_loss(prob_over25, real_over25), 4),
            "brier": round(_brier(prob_over25, real_over25), 4),
            "tasa_real": round(sum(real_over25) / n, 3),
            "prob_media_modelo": round(sum(prob_over25) / n, 3),
        },
        "btts": {
            "log_loss": round(_log_loss(prob_btts, real_btts), 4),
            "brier": round(_brier(prob_btts, real_btts), 4),
            "tasa_real": round(sum(real_btts) / n, 3),
            "prob_media_modelo": round(sum(prob_btts) / n, 3),
        },
        "resultado_1x2": {
            "log_loss_local": round(_log_loss(prob_local_win, real_local_win), 4),
            "brier_local": round(_brier(prob_local_win, real_local_win), 4),
            "log_loss_draw": round(_log_loss(prob_draw, real_draw), 4),
            "brier_draw": round(_brier(prob_draw, real_draw), 4),
            "log_loss_visitante": round(_log_loss(prob_vis_win, real_vis_win), 4),
            "brier_visitante": round(_brier(prob_vis_win, real_vis_win), 4),
            "tasas_reales": {
                "local": round(sum(real_local_win) / n, 3),
                "draw": round(sum(real_draw) / n, 3),
                "visitante": round(sum(real_vis_win) / n, 3),
            },
            "prob_medias_modelo": {
                "local": round(sum(prob_local_win) / n, 3),
                "draw": round(sum(prob_draw) / n, 3),
                "visitante": round(sum(prob_vis_win) / n, 3),
            },
        },
    }

    if errores_poss:
        resultados["xposesion"] = {
            "n": len(errores_poss),
            "mae": round(_mae([abs(e) for e in errores_poss], [0.0] * len(errores_poss)), 2),
            "bias": round(sum(errores_poss) / len(errores_poss), 2),
        }

    if verbose:
        _imprimir_evaluacion(resultados)

    return resultados


def _imprimir_evaluacion(r: dict) -> None:
    """Imprime el resumen de evaluación en consola."""
    print(f"\n{'═' * 58}")
    print(f"  EVALUACIÓN DEL KNOWLEDGE PACK — BACKTESTING")
    print(f"  N partidos evaluados: {r['n_partidos_evaluados']}")
    print(f"{'─' * 58}")

    xg = r["xgoals"]
    print(f"\n  xGoals (error vs goles reales):")
    print(f"    Local    MAE={xg['mae_local']}  RMSE={xg['rmse_local']}  Bias={xg['bias_local']:+.3f}")
    print(f"    Visitante MAE={xg['mae_visitante']}  RMSE={xg['rmse_visitante']}  Bias={xg['bias_visitante']:+.3f}")

    ov = r["over25"]
    print(f"\n  Over 2.5 goles:")
    print(f"    Log-loss={ov['log_loss']}  Brier={ov['brier']}")
    print(f"    Tasa real={ov['tasa_real']*100:.0f}%  P.modelo media={ov['prob_media_modelo']*100:.0f}%")

    bt = r["btts"]
    print(f"\n  BTTS (ambos marcan):")
    print(f"    Log-loss={bt['log_loss']}  Brier={bt['brier']}")
    print(f"    Tasa real={bt['tasa_real']*100:.0f}%  P.modelo media={bt['prob_media_modelo']*100:.0f}%")

    res = r["resultado_1x2"]
    print(f"\n  1X2 — Resultado:")
    print(f"    Local     Log-loss={res['log_loss_local']}  Brier={res['brier_local']}  "
          f"real={res['tasas_reales']['local']*100:.0f}%  modelo={res['prob_medias_modelo']['local']*100:.0f}%")
    print(f"    Empate    Log-loss={res['log_loss_draw']}  Brier={res['brier_draw']}  "
          f"real={res['tasas_reales']['draw']*100:.0f}%  modelo={res['prob_medias_modelo']['draw']*100:.0f}%")
    print(f"    Visitante Log-loss={res['log_loss_visitante']}  Brier={res['brier_visitante']}  "
          f"real={res['tasas_reales']['visitante']*100:.0f}%  modelo={res['prob_medias_modelo']['visitante']*100:.0f}%")

    if "xposesion" in r:
        xp = r["xposesion"]
        print(f"\n  xPosesión (N={xp['n']} partidos con dato real):")
        print(f"    MAE={xp['mae']}pp  Bias={xp['bias']:+.2f}pp")

    print(f"\n  Referencia (Brier score): 0.0=perfecto · 0.25=sin info")
    print(f"{'═' * 58}\n")
