#!/usr/bin/env python3
"""
main.py — CLI del Analizador de Agresividad de La Liga.

Uso:
  python main.py                                    # Modo interactivo (selector numerico)
  python main.py 2 15                               # Por numero de la lista (--equipos)
  python main.py "Athletic" "Atletico"              # Por nombre (busqueda aproximada)
  python main.py --ranking                          # Ranking completo
  python main.py --equipos                          # Listar equipos numerados
  python main.py --arbitros                         # Listar arbitros
  python main.py --ingest stats                     # CSVs -> Supabase
  python main.py --ingest possession                # Scrape fbref -> Supabase
  python main.py --ingest all                       # stats + possession
"""

from __future__ import annotations

import sys
import re
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from ingestion import fetch_all, ingest_stats, ingest_possession, ingest_all
from model import (
    calcular_scores, calcular_rankings, buscar_equipo, nivel_riesgo,
    calcular_xfouls, calcular_xstyle,
    calcular_perfiles_arbitros, buscar_arbitro,
    nivel_intensidad, STYLE_DIMS,
    ensamblar_knowledge_pack, ajustar_knowledge_pack,
)
from model.evaluate_knowledge import evaluar as evaluar_knowledge
from model.calibrate_agresividad import calibrar_agresividad_volumen

# ── Colores ANSI ─────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[96m"
YELLOW  = "\033[93m"
GREEN   = "\033[92m"
RED     = "\033[91m"
MAGENTA = "\033[95m"
GRAY    = "\033[90m"
WHITE   = "\033[97m"
BLUE    = "\033[94m"


def barra_agresividad(score: float, ancho: int = 20) -> str:
    filled = int(round((score - 1) / 9 * ancho))
    filled = max(0, min(ancho, filled))
    empty = ancho - filled

    if score >= 8:
        color = RED
    elif score >= 6:
        color = YELLOW
    elif score >= 4:
        color = GREEN
    else:
        color = CYAN

    return f"{color}{'█' * filled}{'░' * empty}{RESET}"


def imprimir_cabecera():
    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}{CYAN}  ⚽  ANALIZADOR DE AGRESIVIDAD - LA LIGA ESPAÑOLA  ⚽{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{GRAY}  Basado en faltas, tarjetas amarillas y rojas{RESET}")
    print(f"{GRAY}  Con ponderación temporal (partidos recientes > históricos){RESET}\n")


def imprimir_equipo(nombre: str, scores: dict, rankings: dict, n_equipos: int):
    s = scores[nombre]
    r = rankings[nombre]

    g  = s["general_norm"]
    lo = s["local_norm"]
    vi = s["visitante_norm"]

    rg  = r["rank_general"]
    rlo = r["rank_local"]
    rvi = r["rank_visitante"]

    print(f"{BOLD}{WHITE}  {nombre}{RESET}")
    print(f"  {GRAY}{'─' * 50}{RESET}")

    print(f"  {BOLD}General  {RESET}  {barra_agresividad(g)}  "
          f"{BOLD}{g:4.1f}/10{RESET}  "
          f"{GRAY}(#{rg} de {n_equipos} en liga){RESET}")

    print(f"  {BLUE}Local    {RESET}  {barra_agresividad(lo)}  "
          f"{BOLD}{lo:4.1f}/10{RESET}  "
          f"{GRAY}(#{rlo} de {n_equipos}){RESET}")

    print(f"  {MAGENTA}Visitante{RESET}  {barra_agresividad(vi)}  "
          f"{BOLD}{vi:4.1f}/10{RESET}  "
          f"{GRAY}(#{rvi} de {n_equipos}){RESET}")

    sr = s["stats_raw"]
    print(f"\n  {GRAY}Medias por partido: "
          f"{sr['faltas_media']} faltas · "
          f"{sr['amarillas_media']} amarillas · "
          f"{sr['rojas_media']} rojas{RESET}")
    print(f"  {GRAY}Partidos analizados: {s['n_partidos']} "
          f"({s['n_local']} local / {s['n_visitante']} visitante){RESET}")


def barra_dim(valor_norm: float, ancho: int = 14) -> str:
    filled = int(round((valor_norm - 1) / 9 * ancho))
    filled = max(0, min(ancho, filled))
    if valor_norm >= 7.5:
        color = RED
    elif valor_norm >= 5.5:
        color = YELLOW
    else:
        color = CYAN
    return f"{color}{'█' * filled}{'░' * (ancho - filled)}{RESET}"


def imprimir_xstyle(nombre: str, profile: dict) -> None:
    estilo = profile.get("estilo", "—")
    desc   = profile.get("estilo_desc", "")
    norms  = profile.get("dim_norm", {})

    print(f"\n  {GRAY}  ── Estilo de juego ──────────────────────────────{RESET}")
    print(f"  {BOLD}  {estilo}{RESET}  {GRAY}{desc}{RESET}")

    dim_labels = [
        ("tiros",       f"Tiros/P       {profile.get('tiros', 0):4.1f}"),
        ("precision",   f"Precisión tiro {profile.get('precision', 0)*100:4.1f}%"),
        ("corners",     f"Corners/P     {profile.get('corners', 0):4.1f}"),
        ("goles",       f"Goles/P        {profile.get('goles', 0):4.2f}"),
        ("eficiencia",  f"Efic. gol      {profile.get('eficiencia', 0)*100:4.1f}%"),
        ("fisico",      f"Faltas/P      {profile.get('fouls', 0):4.1f}"),
        ("riesgo_tarj", f"T./Falta       {profile.get('cards_per_foul', 0):4.3f}"),
        ("faltas_prov", f"Faltas prov.  {profile.get('faltas_prov', 0):4.1f}"),
    ]
    for dim_key, label in dim_labels:
        n = norms.get(dim_key, 5.0)
        print(f"    {barra_dim(n)}  {GRAY}{label}{RESET}")


def imprimir_xfouls(xf: dict, equipo_a: str, equipo_b: str) -> None:
    sep = f"{GRAY}  {'─' * 56}{RESET}"
    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}{CYAN}  PREDICCIÓN DEL PARTIDO — xFouls{RESET}")
    print(f"{sep}")

    arb = xf.get("arbitro")
    if arb:
        tipo_color = RED if "muy" in arb["tipo"] else (YELLOW if "estricto" in arb["tipo"] else GREEN)
        print(f"\n  {BOLD}Árbitro:{RESET} {arb['nombre']}")
        print(f"  {tipo_color}{BOLD}{arb['tipo'].upper()}{RESET}  "
              f"{GRAY}— {arb['fouls_partido']:.1f} F/P  ·  "
              f"{arb['amarillas_partido']:.2f} T/P  ·  "
              f"{arb['partidos']} partidos{RESET}")
    else:
        print(f"\n  {GRAY}Árbitro no especificado (usa --arbitro \"Nombre\"){RESET}")

    print(f"\n  {BOLD}Faltas esperadas:{RESET}")

    def fmt_factor(f: float) -> str:
        if f > 1.04:
            return f"{RED}×{f:.2f}↑{RESET}"
        if f < 0.96:
            return f"{GREEN}×{f:.2f}↓{RESET}"
        return f"{GRAY}×{f:.2f}{RESET}"

    print(f"  {BLUE}{equipo_a:<24}{RESET}  "
          f"base {xf['base_local']:4.1f}  "
          f"rival {fmt_factor(xf['draw_factor_visitante'])}  "
          f"árb {fmt_factor(xf['ref_factor'])}  "
          f"tarj {fmt_factor(xf['card_pressure_local'])}  "
          f"→ {BOLD}{xf['xfouls_local']:4.1f}{RESET}")

    print(f"  {MAGENTA}{equipo_b:<24}{RESET}  "
          f"base {xf['base_visitante']:4.1f}  "
          f"rival {fmt_factor(xf['draw_factor_local'])}  "
          f"árb {fmt_factor(xf['ref_factor'])}  "
          f"tarj {fmt_factor(xf['card_pressure_visitante'])}  "
          f"→ {BOLD}{xf['xfouls_visitante']:4.1f}{RESET}")

    avg = xf["avg_liga"]
    total = xf["xfouls_total"]
    diff_pct = (total - avg) / avg * 100
    signo = "+" if diff_pct >= 0 else ""
    color_total = RED if diff_pct > 15 else (YELLOW if diff_pct > 5 else GREEN)

    print(f"\n  {BOLD}Total esperado:{RESET}  "
          f"{color_total}{BOLD}{total:.1f} faltas{RESET}  "
          f"{GRAY}(media liga: {avg:.1f}, {signo}{diff_pct:.0f}%){RESET}")

    etiqueta_int, color_int = nivel_intensidad(total, avg)
    print(f"  Intensidad:     {color_int}{BOLD}{etiqueta_int}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 58}{RESET}\n")


def mostrar_arbitros(perfiles: dict) -> None:
    print(f"\n{BOLD}Árbitros en la base de datos:{RESET}\n")
    print(f"  {BOLD}{'Árbitro':<30} {'P':>4} {'F/P':>5} {'A/P':>5} {'R/P':>5} {'Tipo':<15}{RESET}")
    print(f"  {GRAY}{'─' * 65}{RESET}")
    orden = sorted(perfiles.items(), key=lambda x: x[1]["amarillas_partido"], reverse=True)
    for nombre, d in orden:
        tipo_color = RED if "muy" in d["tipo"] else (YELLOW if "estricto" in d["tipo"] else GREEN)
        print(f"  {nombre:<30} {d['partidos']:>4} "
              f"{d['fouls_partido']:>5.1f} "
              f"{d['amarillas_partido']:>5.2f} "
              f"{d['rojas_partido']:>5.2f}  "
              f"{tipo_color}{d['tipo']:<15}{RESET}")
    print()


_RESULTS_DIR = Path(__file__).resolve().parent / "results"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _construir_resultado(
    equipo_a: str, equipo_b: str,
    scores: dict, rankings: dict,
    xstyles: dict, partidos: list,
    arbitro: Optional[str],
    jornada: Optional[int] = None,
    cuotas: Optional[dict] = None,
    contexto_competitivo: Optional[dict] = None,
    market_input: Optional[dict] = None,
) -> dict:
    sa = scores[equipo_a]
    sb = scores[equipo_b]
    ra = rankings[equipo_a]
    rb = rankings[equipo_b]
    n  = len(scores)

    xf = calcular_xfouls(partidos, equipo_a, equipo_b, arbitro)
    perfiles_refs = calcular_perfiles_arbitros(partidos)
    etiqueta_riesgo, _ = nivel_riesgo(sa["general_norm"], sb["general_norm"])
    etiqueta_int, _    = nivel_intensidad(xf["xfouls_total"], xf["avg_liga"])

    def equipo_dict(nombre: str, s: dict, r: dict) -> dict:
        sr = s["stats_raw"]
        return {
            "nombre":       nombre,
            "iap_general":  round(s["general_norm"], 2),
            "iap_local":    round(s["local_norm"], 2),
            "iap_visitante":round(s["visitante_norm"], 2),
            "rank_general": r["rank_general"],
            "rank_local":   r["rank_local"],
            "rank_visitante":r["rank_visitante"],
            "n_equipos":    n,
            "faltas_media":   sr["faltas_media"],
            "amarillas_media":sr["amarillas_media"],
            "rojas_media":    sr["rojas_media"],
            "n_partidos":   s["n_partidos"],
            "n_local":      s["n_local"],
            "n_visitante":  s["n_visitante"],
        }

    def style_dict(nombre: str) -> dict:
        p = xstyles.get(nombre, {})
        poss = p.get("posesion")
        return {
            "estilo":      p.get("estilo", "—"),
            "estilo_desc": p.get("estilo_desc", ""),
            "posesion":    round(poss, 1) if poss is not None else None,
            "tiros":       round(p.get("tiros", 0), 1),
            "precision":   round(p.get("precision", 0) * 100, 1),
            "corners":     round(p.get("corners", 0), 1),
            "goles":       round(p.get("goles", 0), 2),
            "eficiencia":  round(p.get("eficiencia", 0) * 100, 1),
            "faltas":      round(p.get("fouls", 0), 1),
            "tarj_falta":  round(p.get("cards_per_foul", 0), 3),
            "faltas_prov": round(p.get("faltas_prov", 0), 1),
        }

    diff_pct = (xf["xfouls_total"] - xf["avg_liga"]) / xf["avg_liga"] * 100
    xf_arb = xf.get("arbitro")

    knowledge_pack = ensamblar_knowledge_pack(
        equipo_local=equipo_a,
        equipo_visitante=equipo_b,
        partidos=partidos,
        xstyles=xstyles,
        xfouls_result=xf,
        ref_perfiles=perfiles_refs,
        arbitro=arbitro,
        jornada=jornada,
        contexto_competitivo_input=contexto_competitivo,
    )
    if cuotas or market_input:
        knowledge_pack = ajustar_knowledge_pack(
            knowledge_pack,
            odds_h=cuotas.get("local") if cuotas else None,
            odds_d=cuotas.get("empate") if cuotas else None,
            odds_a=cuotas.get("visitante") if cuotas else None,
            odds_over25=cuotas.get("over25") if cuotas else None,
            odds_under25=cuotas.get("under25") if cuotas else None,
            market_input=market_input,
        )

    return {
        "generado":          datetime.now().isoformat(timespec="seconds"),
        "equipo_local":      equipo_a,
        "equipo_visitante":  equipo_b,
        "arbitro":           arbitro,
        "jornada":           jornada,
        "iap": {
            "local":       equipo_dict(equipo_a, sa, ra),
            "visitante":   equipo_dict(equipo_b, sb, rb),
            "mas_agresivo":equipo_a if sa["general_norm"] >= sb["general_norm"] else equipo_b,
            "diferencia":  round(abs(sa["general_norm"] - sb["general_norm"]), 2),
            "nivel_riesgo":_strip_ansi(etiqueta_riesgo),
        },
        "xfouls": {
            "base_local":          round(xf["base_local"], 1),
            "base_visitante":      round(xf["base_visitante"], 1),
            "xfouls_local":        round(xf["xfouls_local"], 1),
            "xfouls_visitante":    round(xf["xfouls_visitante"], 1),
            "total":               round(xf["xfouls_total"], 1),
            "avg_liga":            round(xf["avg_liga"], 1),
            "diff_pct":            round(diff_pct, 1),
            "intensidad":          _strip_ansi(etiqueta_int),
            "arbitro": {
                "nombre":          xf_arb["nombre"],
                "tipo":            xf_arb["tipo"],
                "fouls_partido":   round(xf_arb["fouls_partido"], 2),
                "amarillas_partido":round(xf_arb["amarillas_partido"], 2),
                "partidos":        xf_arb["partidos"],
            } if xf_arb else None,
        },
        "xstyle": {
            "local":     style_dict(equipo_a),
            "visitante": style_dict(equipo_b),
        },
        "knowledge_pack": knowledge_pack,
    }


def _generar_markdown(r: dict) -> str:
    ea   = r["equipo_local"]
    eb   = r["equipo_visitante"]
    iap  = r["iap"]
    xf   = r["xfouls"]
    arb  = r["arbitro"] or "—"
    fecha = r["generado"][:10]

    ia  = iap["local"]
    ib  = iap["visitante"]
    sa  = r["xstyle"]["local"]
    sb  = r["xstyle"]["visitante"]

    signo = "+" if xf["diff_pct"] >= 0 else ""

    lines = [
        f"# {ea} vs {eb}  —  {fecha}",
        "",
        f"> Árbitro: **{arb}**",
        "",
        "---",
        "",
        "## Índice IAP (histórico, señal secundaria)",
        "",
        f"| | **{ea}** | **{eb}** |",
        "|---|---:|---:|",
        f"| General | {ia['iap_general']}/10 (#{ia['rank_general']}/{ia['n_equipos']}) | {ib['iap_general']}/10 (#{ib['rank_general']}/{ib['n_equipos']}) |",
        f"| Local | {ia['iap_local']}/10 (#{ia['rank_local']}) | {ib['iap_local']}/10 (#{ib['rank_local']}) |",
        f"| Visitante | {ia['iap_visitante']}/10 (#{ia['rank_visitante']}) | {ib['iap_visitante']}/10 (#{ib['rank_visitante']}) |",
        f"| Faltas/P | {ia['faltas_media']} | {ib['faltas_media']} |",
        f"| Amarillas/P | {ia['amarillas_media']} | {ib['amarillas_media']} |",
        f"| Rojas/P | {ia['rojas_media']} | {ib['rojas_media']} |",
        f"| Partidos | {ia['n_partidos']} | {ib['n_partidos']} |",
        "",
        f"**{iap['mas_agresivo']}** es el equipo más agresivo "
        f"({iap['diferencia']} puntos de diferencia)",
        "",
        f"### {_strip_ansi(iap['nivel_riesgo'])}",
        "",
        "---",
        "",
        "## Predicción de faltas (xFouls)",
        "",
        f"| | |",
        "|---|---:|",
        f"| {ea} (local) | **{xf['xfouls_local']}** faltas |",
        f"| {eb} (visitante) | **{xf['xfouls_visitante']}** faltas |",
        f"| **Total esperado** | **{xf['total']} faltas** |",
        f"| Media liga | {xf['avg_liga']} ({signo}{xf['diff_pct']:.1f}%) |",
        f"| Intensidad | {xf['intensidad']} |",
    ]

    if xf.get("arbitro"):
        xa = xf["arbitro"]
        lines += [
            "",
            f"**Árbitro:** {xa['nombre']} — {xa['tipo'].upper()}  "
            f"({xa['fouls_partido']} F/P · {xa['amarillas_partido']} A/P · {xa['partidos']} partidos)",
        ]

    lines += [
        "",
        "---",
        "",
        "## Estilo de juego",
        "",
        f"### {ea} — {sa['estilo']}",
        f"> {sa['estilo_desc']}",
        "",
        f"| Dimensión | Valor |",
        "|---|---:|",
        f"| Posesión | {sa['posesion']}% |" if sa['posesion'] is not None else "| Posesión | — |",
        f"| Tiros/P | {sa['tiros']} |",
        f"| Precisión tiro | {sa['precision']}% |",
        f"| Corners/P | {sa['corners']} |",
        f"| Goles/P | {sa['goles']} |",
        f"| Eficiencia gol | {sa['eficiencia']}% |",
        f"| Faltas/P | {sa['faltas']} |",
        f"| Tarjetas/Falta | {sa['tarj_falta']} |",
        f"| Faltas provocadas | {sa['faltas_prov']} |",
        "",
        f"### {eb} — {sb['estilo']}",
        f"> {sb['estilo_desc']}",
        "",
        f"| Dimensión | Valor |",
        "|---|---:|",
        f"| Posesión | {sb['posesion']}% |" if sb['posesion'] is not None else "| Posesión | — |",
        f"| Tiros/P | {sb['tiros']} |",
        f"| Precisión tiro | {sb['precision']}% |",
        f"| Corners/P | {sb['corners']} |",
        f"| Goles/P | {sb['goles']} |",
        f"| Eficiencia gol | {sb['eficiencia']}% |",
        f"| Faltas/P | {sb['faltas']} |",
        f"| Tarjetas/Falta | {sb['tarj_falta']} |",
        f"| Faltas provocadas | {sb['faltas_prov']} |",
    ]

    # ── Knowledge Pack ────────────────────────────────────────────────────────
    kp = r.get("knowledge_pack")
    if kp:
        forma_l = kp["forma"]["local"]
        forma_v = kp["forma"]["visitante"]
        ctx = kp["contexto_temporada"]
        xg = kp["expected_metrics"]["xgoals"]
        xposs = kp["expected_metrics"]["xposesion"]
        xtarj = kp["expected_metrics"]["xtarjetas"]
        aggv = kp["expected_metrics"]["agresividad_volumen"]
        compat = kp["compatibilidad_estilos"]
        ccomp = kp.get("contexto_competitivo", {})
        narrative = kp.get("narrative", [])
        jornada_val = r.get("jornada")

        def _racha_forma(forma: dict) -> str:
            if forma["partidos_analizados"] == 0:
                return "—"
            return (
                f"{forma['racha_str']} "
                f"({forma['victorias']}V {forma['empates']}E {forma['derrotas']}D, "
                f"{forma['puntos']} pts, {forma['tendencia']})"
            )

        lines += [
            "",
            "---",
            "",
            "## Knowledge Pack — Análisis Prepartido",
            "",
            f"### Contexto de temporada",
            "",
            f"| | |",
            "|---|---:|",
            f"| Jornada estimada | ~{ctx['jornada_estimada']}/{ctx['jornadas_totales']} "
            f"({ctx['porcentaje_temporada']}%) |" if jornada_val is None
            else f"| Jornada | {ctx['jornada_estimada']}/{ctx['jornadas_totales']} "
            f"({ctx['porcentaje_temporada']}%) |",
            f"| Tramo | {ctx['tramo_desc']} |",
            f"| Jornadas restantes | {ctx['jornadas_restantes']} |",
            f"| Presión de cierre | **{ctx['presion_final'].upper()}** |",
            "",
            "### Forma reciente",
            "",
            f"| | **{ea}** | **{eb}** |",
            "|---|:---:|:---:|",
            f"| Últimos partidos | {forma_l['partidos_analizados']} | {forma_v['partidos_analizados']} |",
            f"| Racha (reciente→antigua) | {_racha_forma(forma_l)} | {_racha_forma(forma_v)} |",
            f"| Puntos/partido | {forma_l['puntos_media']} | {forma_v['puntos_media']} |",
            f"| Goles anotados/P | {forma_l['goles_anotados_media']} | {forma_v['goles_anotados_media']} |",
            f"| Goles recibidos/P | {forma_l['goles_recibidos_media']} | {forma_v['goles_recibidos_media']} |",
            f"| Faltas/P (forma) | {forma_l['faltas_media']} | {forma_v['faltas_media']} |",
            f"| Tarjetas/P (forma) | {forma_l['tarjetas_media']} | {forma_v['tarjetas_media']} |",
            "",
            "### Tipo de partido esperado",
            "",
            f"**{compat['tipo_partido']}** — {compat['tipo_partido_desc']}",
            "",
            f"| | |",
            "|---|---:|",
            f"| Intensidad física | {compat['fisico_total']} F/P combinadas ({compat['fisico_label']}) |",
            f"| Ritmo ofensivo | {compat['ritmo_ofensivo']} tiros/P combinados ({compat['ofensivo_label']}) |",
            f"| Control del juego | {compat['control_juego']} |",
            f"| Estilo {ea} | {compat['estilos']['local']} |",
            f"| Estilo {eb} | {compat['estilos']['visitante']} |",
        ]

        if compat["derived_angles"]:
            lines += [
                "",
                f"**Ángulos detectados:** {' · '.join(compat['derived_angles'])}",
            ]

        if ccomp:
            ccl = ccomp.get("local", {})
            ccv = ccomp.get("visitante", {})
            lines += [
                "",
                "### Contexto competitivo",
                "",
                f"| Factor | {ea} | {eb} |",
                "|---|:---:|:---:|",
                f"| ICC competitivo | {ccl.get('scores', {}).get('icc', 0)} | {ccv.get('scores', {}).get('icc', 0)} |",
                f"| Última competición | {ccl.get('last_competition', 'none')} | {ccv.get('last_competition', 'none')} |",
                f"| Próxima competición | {ccl.get('next_competition', 'none')} | {ccv.get('next_competition', 'none')} |",
                f"| Objetivo liga | {ccl.get('objetivo_liga', 'none')} | {ccv.get('objetivo_liga', 'none')} |",
                f"| Lectura | {ccl.get('lectura', '—')} | {ccv.get('lectura', '—')} |",
            ]

        lines += [
            "",
            "### Métricas esperadas",
            "",
            f"| Métrica | {ea} | {eb} | Total |",
            "|---|:---:|:---:|:---:|",
            f"| xGoals | {xg['xg_local']} | {xg['xg_visitante']} | {xg['xg_total']} |",
            f"| Posesión esperada | {xposs['posesion_local']}% | {xposs['posesion_visitante']}% | — |",
            f"| xFaltas | {kp['expected_metrics']['xfouls']['local']} | "
            f"{kp['expected_metrics']['xfouls']['visitante']} | "
            f"{kp['expected_metrics']['xfouls']['total']} |",
            f"| Agresividad volumen | {aggv['local']} | {aggv['visitante']} | {aggv['total']} |",
            f"| xTarjetas amarillas | {xtarj['xtarjetas_local']} | "
            f"{xtarj['xtarjetas_visitante']} | {xtarj['xtarjetas_total']:.2f} |",
            f"| xRojas (estimadas) | — | — | {xtarj['xrojas_total']:.3f} |",
            "",
            "### Probabilidades derivadas (Poisson)",
            "",
            f"| Mercado | Probabilidad modelo |",
            "|---|:---:|",
            f"| {ea} gana | **{xg['prob_local_win']*100:.1f}%** |",
            f"| Empate | **{xg['prob_draw']*100:.1f}%** |",
            f"| {eb} gana | **{xg['prob_visitante_win']*100:.1f}%** |",
            f"| Over 2.5 goles | **{xg['prob_over25']*100:.1f}%** |",
            f"| Under 2.5 goles | **{xg['prob_under25']*100:.1f}%** |",
            f"| BTTS (ambos marcan) | **{xg['prob_btts']*100:.1f}%** |",
        ]

        # Market signal
        ms = kp.get("market_signal")
        if ms:
            lines += ["", "### Señal de mercado", ""]
            if ms.get("alignment"):
                al = ms["alignment"]
                cuotas_md = ms["cuotas_1x2"]
                pm = ms["probabilidades_mercado_1x2"]
                pmod = ms["probabilidades_modelo_1x2"]
                lines += [
                    f"| | Cuota | P. mercado | P. modelo | Diferencia |",
                    "|---|:---:|:---:|:---:|:---:|",
                    f"| {ea} gana | {cuotas_md['local']} | {pm['prob_local']*100:.1f}% | "
                    f"{pmod['prob_local']*100:.1f}% | {(pmod['prob_local']-pm['prob_local'])*100:+.1f}pp |",
                    f"| Empate | {cuotas_md['empate']} | {pm['prob_draw']*100:.1f}% | "
                    f"{pmod['prob_draw']*100:.1f}% | {(pmod['prob_draw']-pm['prob_draw'])*100:+.1f}pp |",
                    f"| {eb} gana | {cuotas_md['visitante']} | {pm['prob_visitante']*100:.1f}% | "
                    f"{pmod['prob_visitante']*100:.1f}% | {(pmod['prob_visitante']-pm['prob_visitante'])*100:+.1f}pp |",
                    "",
                    f"**Alignment 1X2:** {al['alignment_score']} — {al['interpretacion']}",
                ]
            if "over25" in ms:
                ov = ms["over25"]
                lines += [
                    f"**Over 2.5:** cuota {ov['cuota_over']} · "
                    f"P.mercado {ov['prob_mercado']*100:.1f}% · "
                    f"P.modelo {ov['prob_modelo']*100:.1f}% · "
                    f"diferencia {ov['diferencia']*100:+.1f}pp — {ov['interpretacion']}",
                ]
            if ms.get("markets"):
                lines += ["", "**Mercados disponibles:** " + ", ".join(ms.get("available_markets", []))]
                gal = ms.get("global_alignment")
                if gal:
                    lines += [f"**Alignment global:** {gal['score']} ({gal['n_markets']} mercados) — {gal['interpretacion']}"]

        # Narrative
        if narrative:
            lines += [
                "",
                "### Narrative (knowledge bullets)",
                "",
            ]
            for bullet in narrative:
                lines.append(f"- {bullet}")

    lines += [
        "",
        "---",
        f"*Generado: {r['generado']}*",
    ]
    return "\n".join(lines)


def _guardar_resultado(r: dict) -> tuple[Path, Path]:
    _RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    slug  = f"{r['equipo_local']}_vs_{r['equipo_visitante']}_{stamp}".replace(" ", "_")

    path_json = _RESULTS_DIR / f"{slug}.json"
    path_md   = _RESULTS_DIR / f"{slug}.md"

    path_json.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    path_md.write_text(_generar_markdown(r), encoding="utf-8")

    return path_json, path_md


def _imprimir_knowledge_pack(kp: dict, equipo_a: str, equipo_b: str) -> None:
    """Muestra en consola el knowledge pack de forma legible."""
    sep = f"{GRAY}  {'─' * 56}{RESET}"

    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}{CYAN}  KNOWLEDGE PACK — ANÁLISIS PREPARTIDO{RESET}")
    print(f"{sep}")

    # Contexto temporada
    ctx = kp["contexto_temporada"]
    print(f"\n  {BOLD}Contexto temporada:{RESET}  "
          f"Jornada ~{ctx['jornada_estimada']}/38  "
          f"{GRAY}({ctx['tramo_desc']}){RESET}")
    print(f"  Quedan {ctx['jornadas_restantes']} jornadas  "
          f"· Presión de cierre: {BOLD}{ctx['presion_final'].upper()}{RESET}")

    # Forma reciente
    print(f"\n  {BOLD}Forma reciente:{RESET}")

    def _forma_row(nombre: str, forma: dict) -> None:
        if forma["partidos_analizados"] == 0:
            print(f"  {nombre:<22}  {GRAY}sin datos{RESET}")
            return
        tend_color = GREEN if forma["tendencia"] == "mejorando" else (
            RED if forma["tendencia"] == "empeorando" else GRAY)
        print(f"  {nombre:<22}  {BOLD}{forma['racha_str']}{RESET}  "
              f"{GRAY}({forma['victorias']}V {forma['empates']}E {forma['derrotas']}D · "
              f"{forma['puntos']} pts){RESET}  "
              f"{tend_color}{forma['tendencia']}{RESET}")

    _forma_row(equipo_a, kp["forma"]["local"])
    _forma_row(equipo_b, kp["forma"]["visitante"])

    # Tipo de partido
    compat = kp["compatibilidad_estilos"]
    print(f"\n  {BOLD}Tipo de partido esperado:{RESET}")
    print(f"  {CYAN}{BOLD}{compat['tipo_partido']}{RESET}  {GRAY}— {compat['tipo_partido_desc']}{RESET}")
    print(f"  Físico: {compat['fisico_label']}  "
          f"· Ofensivo: {compat['ofensivo_label']}  "
          f"· {compat['control_juego']}")

    if compat["derived_angles"]:
        angles = " · ".join(compat["derived_angles"])
        print(f"  {GRAY}Ángulos: {angles}{RESET}")

    # Métricas esperadas
    em = kp["expected_metrics"]
    xg = em["xgoals"]
    xposs = em["xposesion"]
    xtarj = em["xtarjetas"]
    aggv = em["agresividad_volumen"]
    xfl = em["xfouls"]
    ccomp = kp.get("contexto_competitivo", {})

    print(f"\n  {BOLD}Métricas esperadas:{RESET}")
    print(f"  {'':24}  {BLUE}{equipo_a:<20}{RESET}  {MAGENTA}{equipo_b}{RESET}")
    print(f"  {GRAY}{'─' * 56}{RESET}")
    print(f"  {'xGoals':<24}  {BLUE}{xg['xg_local']:>6.2f}{RESET}              "
          f"{MAGENTA}{xg['xg_visitante']:>6.2f}{RESET}")
    print(f"  {'Posesión esperada':<24}  {BLUE}{xposs['posesion_local']:>5.1f}%{RESET}             "
          f"{MAGENTA}{xposs['posesion_visitante']:>5.1f}%{RESET}")
    print(f"  {'xFaltas':<24}  {BLUE}{xfl['local']:>6.1f}{RESET}              "
          f"{MAGENTA}{xfl['visitante']:>6.1f}{RESET}   total {BOLD}{xfl['total']:.1f}{RESET}")
    print(f"  {'Agresividad volumen':<24}  {BLUE}{aggv['local']:>6.2f}{RESET}              "
          f"{MAGENTA}{aggv['visitante']:>6.2f}{RESET}   total {BOLD}{aggv['total']:.2f}{RESET}")
    print(f"  {'xTarjetas amarillas':<24}  {BLUE}{xtarj['xtarjetas_local']:>6.2f}{RESET}              "
          f"{MAGENTA}{xtarj['xtarjetas_visitante']:>6.2f}{RESET}   total {BOLD}{xtarj['xtarjetas_total']:.2f}{RESET}")
    print(f"  {'xRojas (estimadas)':<24}  {'total':>26}  {BOLD}{xtarj['xrojas_total']:.3f}{RESET}")
    if ccomp:
        ccl = ccomp.get("local", {})
        ccv = ccomp.get("visitante", {})
        print(f"  {'ICC competitivo':<24}  {BLUE}{ccl.get('scores', {}).get('icc', 0):>6.2f}{RESET}              "
              f"{MAGENTA}{ccv.get('scores', {}).get('icc', 0):>6.2f}{RESET}")
        print(f"  {'Comp. siguiente':<24}  {BLUE}{ccl.get('next_competition', 'none'):>6}{RESET}              "
              f"{MAGENTA}{ccv.get('next_competition', 'none'):>6}{RESET}")
        print(f"  {'Objetivo liga':<24}  {BLUE}{ccl.get('objetivo_liga', 'none'):>6}{RESET}              "
              f"{MAGENTA}{ccv.get('objetivo_liga', 'none'):>6}{RESET}")

    # Probabilidades
    print(f"\n  {BOLD}Probabilidades derivadas (Poisson):{RESET}")
    print(f"  Local {BOLD}{xg['prob_local_win']*100:.0f}%{RESET}  "
          f"· Empate {BOLD}{xg['prob_draw']*100:.0f}%{RESET}  "
          f"· Visitante {BOLD}{xg['prob_visitante_win']*100:.0f}%{RESET}")
    over_color = GREEN if xg["prob_over25"] >= 0.5 else YELLOW
    print(f"  Over 2.5: {over_color}{BOLD}{xg['prob_over25']*100:.0f}%{RESET}  "
          f"· Under 2.5: {BOLD}{xg['prob_under25']*100:.0f}%{RESET}  "
          f"· BTTS: {BOLD}{xg['prob_btts']*100:.0f}%{RESET}")

    # Market signal
    ms = kp.get("market_signal")
    if ms:
        al = ms.get("alignment")
        if al:
            al_score = al["alignment_score"]
            al_color = GREEN if al_score >= 0.80 else (YELLOW if al_score >= 0.60 else RED)
            print(f"\n  {BOLD}Señal de mercado:{RESET}  "
                  f"{al_color}Alignment {al_score:.2f}{RESET}  "
                  f"{GRAY}— {al['interpretacion']}{RESET}")
        gal = ms.get("global_alignment")
        if gal:
            print(f"  {GRAY}Global ({gal['n_markets']} mercados): "
                  f"{gal['score']} — {gal['interpretacion']}{RESET}")

    # Narrative
    narrative = kp.get("narrative", [])
    if narrative:
        print(f"\n  {BOLD}Narrative (knowledge bullets):{RESET}")
        for bullet in narrative:
            print(f"    {GRAY}{bullet}{RESET}")

    print(f"{BOLD}{CYAN}{'═' * 58}{RESET}\n")


def mostrar_enfrentamiento(
    equipo_a: str, equipo_b: str,
    scores: dict, rankings: dict,
    xstyles: dict, partidos: list,
    arbitro: Optional[str] = None,
    jornada: Optional[int] = None,
    cuotas: Optional[dict] = None,
    contexto_competitivo: Optional[dict] = None,
    market_input: Optional[dict] = None,
) -> None:
    resultado = _construir_resultado(
        equipo_a, equipo_b, scores, rankings, xstyles, partidos, arbitro,
        jornada=jornada, cuotas=cuotas, contexto_competitivo=contexto_competitivo,
        market_input=market_input,
    )
    path_json, path_md = _guardar_resultado(resultado)

    iap = resultado["iap"]
    xf  = resultado["xfouls"]
    ia  = iap["local"]
    ib  = iap["visitante"]

    signo = "+" if xf["diff_pct"] >= 0 else ""
    _, color_riesgo = nivel_riesgo(ia["iap_general"], ib["iap_general"])

    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}{CYAN}  {equipo_a}  vs  {equipo_b}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 58}{RESET}\n")

    print(f"  {BOLD}IAP (histórico de contacto, secundario){RESET}")
    print(f"  {BLUE}{equipo_a:<22}{RESET}  "
          f"{BOLD}{ia['iap_general']:4.1f}/10{RESET}  "
          f"L {ia['iap_local']:.1f}  V {ia['iap_visitante']:.1f}  "
          f"{GRAY}(#{ia['rank_general']}/{ia['n_equipos']}){RESET}")
    print(f"  {MAGENTA}{equipo_b:<22}{RESET}  "
          f"{BOLD}{ib['iap_general']:4.1f}/10{RESET}  "
          f"L {ib['iap_local']:.1f}  V {ib['iap_visitante']:.1f}  "
          f"{GRAY}(#{ib['rank_general']}/{ib['n_equipos']}){RESET}")

    print(f"\n  {color_riesgo}{BOLD}{_strip_ansi(iap['nivel_riesgo'])}{RESET}")
    print(f"  {GRAY}{iap['mas_agresivo']} más agresivo (+{iap['diferencia']:.1f} pts){RESET}")

    print(f"\n  {BOLD}xFouls{RESET}")
    print(f"  {BLUE}{equipo_a:<22}{RESET}  {xf['xfouls_local']:.1f} faltas esperadas")
    print(f"  {MAGENTA}{equipo_b:<22}{RESET}  {xf['xfouls_visitante']:.1f} faltas esperadas")
    print(f"  {BOLD}Total:{RESET} {xf['total']:.1f}  "
          f"{GRAY}(liga avg {xf['avg_liga']:.1f}, {signo}{xf['diff_pct']:.0f}%){RESET}")
    print(f"  Intensidad: {BOLD}{xf['intensidad']}{RESET}")

    if xf.get("arbitro"):
        xa = xf["arbitro"]
        print(f"\n  {BOLD}Árbitro:{RESET} {xa['nombre']} — {xa['tipo'].upper()}")

    kp = resultado.get("knowledge_pack")
    if kp:
        _imprimir_knowledge_pack(kp, equipo_a, equipo_b)
    else:
        print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")

    print(f"  {GREEN}✓ Guardado en:{RESET}")
    print(f"    {path_json.relative_to(Path.cwd()) if path_json.is_relative_to(Path.cwd()) else path_json}")
    print(f"    {path_md.relative_to(Path.cwd()) if path_md.is_relative_to(Path.cwd()) else path_md}")
    print(f"{BOLD}{CYAN}{'═' * 58}{RESET}\n")


def mostrar_ranking_completo(scores: dict, rankings: dict):
    imprimir_cabecera()
    print(f"{BOLD}  RANKING COMPLETO DE AGRESIVIDAD - LA LIGA{RESET}\n")

    orden = sorted(scores.keys(), key=lambda n: scores[n]["general_norm"], reverse=True)

    print(f"  {BOLD}{'#':<4} {'Equipo':<30} {'General':>8} {'Local':>8} {'Visitante':>10}{RESET}")
    print(f"  {GRAY}{'─' * 64}{RESET}")

    for pos, nombre in enumerate(orden, 1):
        s = scores[nombre]
        g  = s["general_norm"]
        lo = s["local_norm"]
        vi = s["visitante_norm"]

        color_pos = RED if pos <= 5 else (YELLOW if pos <= 10 else GREEN)
        print(f"  {color_pos}{pos:<4}{RESET} {nombre:<30} "
              f"{BOLD}{g:>7.1f}{RESET}   {BLUE}{lo:>6.1f}{RESET}   {MAGENTA}{vi:>8.1f}{RESET}")

    print(f"\n  {GRAY}General = score ponderado con decay temporal (λ=0.003){RESET}")
    print(f"  {GRAY}Escala 1-10 relativa a los equipos de la liga\n{RESET}")


def _imprimir_lista_equipos(equipos: list[str]) -> None:
    """Muestra los equipos en columnas numeradas."""
    n = len(equipos)
    cols = 3
    rows = (n + cols - 1) // cols
    col_width = 20

    print(f"\n{BOLD}  Equipos disponibles:{RESET}\n")
    for row in range(rows):
        line = "  "
        for col in range(cols):
            idx = col * rows + row
            if idx < n:
                label = f"{idx + 1:>2}. {equipos[idx]}"
                line += f"{CYAN}{label:<{col_width + 4}}{RESET}"
        print(line)
    print()


def _seleccionar_equipo(equipos: list[str], rol: str) -> str:
    """Pide al usuario un numero y devuelve el nombre del equipo."""
    n = len(equipos)
    while True:
        raw = input(f"{BOLD}  Equipo {rol} [{1}-{n}]: {RESET}").strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < n:
                equipo = equipos[idx]
                print(f"  {GREEN}✓ {equipo}{RESET}\n")
                return equipo
        print(f"  {RED}Introduce un numero entre 1 y {n}.{RESET}")


def mostrar_equipos_disponibles(scores: dict):
    equipos = sorted(scores.keys())
    _imprimir_lista_equipos(equipos)


def modo_interactivo(scores: dict, rankings: dict, xstyles: dict, partidos: list):
    equipos = sorted(scores.keys())
    _imprimir_lista_equipos(equipos)
    equipo_a = _seleccionar_equipo(equipos, "local")
    equipo_b = _seleccionar_equipo(equipos, "visitante")
    mostrar_enfrentamiento(equipo_a, equipo_b, scores, rankings, xstyles, partidos)


def main():
    parser = argparse.ArgumentParser(
        description="Analizador de Agresividad + Knowledge Pack — La Liga Española",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                                          # Modo interactivo
  python main.py "Getafe" "Barca"                        # Por nombre
  python main.py "Getafe" "Barca" --arbitro "Gil"        # Con árbitro
  python main.py "Getafe" "Barca" --jornada 28           # Con jornada exacta
  python main.py "Real Madrid" "Barca" \\
    --cuota-local 2.10 --cuota-empate 3.40 --cuota-vis 3.20  # Con cuotas 1X2
  python main.py "Real Madrid" "Barca" \\
    --cuota-local 2.10 --cuota-empate 3.40 --cuota-vis 3.20 \\
    --cuota-over25 1.85 --cuota-under25 1.95              # Con cuotas O/U
  python main.py --ranking                               # Ranking de agresividad
  python main.py --equipos                               # Listar equipos numerados
  python main.py --arbitros                              # Listar árbitros
  python main.py --ingest stats                          # CSVs -> Supabase
  python main.py --ingest possession                     # Scrape fbref -> Supabase
  python main.py --ingest all                            # stats + possession
        """
    )
    parser.add_argument("equipo_a", nargs="?", help="Primer equipo (local)")
    parser.add_argument("equipo_b", nargs="?", help="Segundo equipo (visitante)")
    parser.add_argument("--ranking",   action="store_true", help="Mostrar ranking completo")
    parser.add_argument("--equipos",   action="store_true", help="Listar equipos disponibles")
    parser.add_argument("--arbitros",  action="store_true", help="Listar árbitros con estadísticas")
    parser.add_argument("--arbitro",   type=str, default=None,
                        help="Nombre del árbitro para ajustar xFouls y xTarjetas")
    parser.add_argument("--jornada",   type=int, default=None,
                        help="Jornada del partido (1-38). Si no se indica, se estima automáticamente.")
    parser.add_argument("--cuota-local",   dest="cuota_local",   type=float, default=None,
                        help="Cuota decimal para victoria local (ej. 2.10)")
    parser.add_argument("--cuota-empate",  dest="cuota_empate",  type=float, default=None,
                        help="Cuota decimal para empate (ej. 3.40)")
    parser.add_argument("--cuota-vis",     dest="cuota_vis",     type=float, default=None,
                        help="Cuota decimal para victoria visitante (ej. 3.20)")
    parser.add_argument("--cuota-over25",  dest="cuota_over25",  type=float, default=None,
                        help="Cuota decimal Over 2.5 goles (ej. 1.85)")
    parser.add_argument("--cuota-under25", dest="cuota_under25", type=float, default=None,
                        help="Cuota decimal Under 2.5 goles (ej. 1.95)")
    parser.add_argument("--market-file", type=str, default=None,
                        help="Ruta a JSON unificado de mercados (todo en un sitio)")
    parser.add_argument("--cuota-over-faltas", type=float, default=None)
    parser.add_argument("--cuota-under-faltas", type=float, default=None)
    parser.add_argument("--linea-faltas", type=float, default=None)
    parser.add_argument("--cuota-over-tarjetas", type=float, default=None)
    parser.add_argument("--cuota-under-tarjetas", type=float, default=None)
    parser.add_argument("--linea-tarjetas", type=float, default=None)
    parser.add_argument("--cuota-over-corners", type=float, default=None)
    parser.add_argument("--cuota-under-corners", type=float, default=None)
    parser.add_argument("--linea-corners", type=float, default=None)
    parser.add_argument("--cuota-over-tiros", type=float, default=None)
    parser.add_argument("--cuota-under-tiros", type=float, default=None)
    parser.add_argument("--linea-tiros", type=float, default=None)
    parser.add_argument("--cuota-over-tiros-puerta", type=float, default=None)
    parser.add_argument("--cuota-under-tiros-puerta", type=float, default=None)
    parser.add_argument("--linea-tiros-puerta", type=float, default=None)
    parser.add_argument("--cuota-over-fueras-juego", type=float, default=None)
    parser.add_argument("--cuota-under-fueras-juego", type=float, default=None)
    parser.add_argument("--linea-fueras-juego", type=float, default=None)
    parser.add_argument("--local-days-since-last", type=int, default=None,
                        help="Días desde el último partido del local")
    parser.add_argument("--visitante-days-since-last", type=int, default=None,
                        help="Días desde el último partido del visitante")
    parser.add_argument("--local-days-to-next", type=int, default=None,
                        help="Días hasta el próximo partido del local")
    parser.add_argument("--visitante-days-to-next", type=int, default=None,
                        help="Días hasta el próximo partido del visitante")
    parser.add_argument("--local-last-ucl", action="store_true",
                        help="El último partido del local fue de Champions")
    parser.add_argument("--visitante-last-ucl", action="store_true",
                        help="El último partido del visitante fue de Champions")
    parser.add_argument("--local-next-ucl", action="store_true",
                        help="El próximo partido del local es de Champions")
    parser.add_argument("--visitante-next-ucl", action="store_true",
                        help="El próximo partido del visitante es de Champions")
    parser.add_argument("--local-last-competition",
                        choices=["none", "liga", "copa", "ucl", "uel", "uecl"], default="none",
                        help="Competición del último partido del local")
    parser.add_argument("--visitante-last-competition",
                        choices=["none", "liga", "copa", "ucl", "uel", "uecl"], default="none",
                        help="Competición del último partido del visitante")
    parser.add_argument("--local-next-competition",
                        choices=["none", "liga", "copa", "ucl", "uel", "uecl"], default="none",
                        help="Competición del próximo partido del local")
    parser.add_argument("--visitante-next-competition",
                        choices=["none", "liga", "copa", "ucl", "uel", "uecl"], default="none",
                        help="Competición del próximo partido del visitante")
    parser.add_argument("--local-liga-urgencia", choices=["baja", "media", "alta"], default="media",
                        help="Urgencia competitiva liguera del local")
    parser.add_argument("--visitante-liga-urgencia", choices=["baja", "media", "alta"], default="media",
                        help="Urgencia competitiva liguera del visitante")
    parser.add_argument("--local-objetivo-liga",
                        choices=["none", "titulo", "top4", "ucl", "uel", "descenso", "salvacion", "media_tabla"],
                        default="none",
                        help="Objetivo real de tabla del local")
    parser.add_argument("--visitante-objetivo-liga",
                        choices=["none", "titulo", "top4", "ucl", "uel", "descenso", "salvacion", "media_tabla"],
                        default="none",
                        help="Objetivo real de tabla del visitante")
    parser.add_argument("--local-riesgo-rotacion", choices=["bajo", "medio", "alto"], default="medio",
                        help="Riesgo de rotación del local")
    parser.add_argument("--visitante-riesgo-rotacion", choices=["bajo", "medio", "alto"], default="medio",
                        help="Riesgo de rotación del visitante")
    parser.add_argument("--ingest",    choices=["stats", "possession", "all"],
                        help="Ingesta de datos: stats | possession | all")
    parser.add_argument("--evaluar",   action="store_true",
                        help="Backtesting del knowledge pack sobre el histórico")
    parser.add_argument("--calibrar-agresividad", action="store_true",
                        help="Calibra alpha_card_pressure y pesos de agresividad por volumen")

    args = parser.parse_args()

    # ── Ingesta de datos ─────────────────────────────────────────────────────

    if args.ingest:
        try:
            if args.ingest == "stats":
                partidos = ingest_stats()
                if partidos:
                    print(f"{GREEN}✓ {len(partidos)} partidos en Supabase.{RESET}")
            elif args.ingest == "possession":
                ingest_possession()
            elif args.ingest == "all":
                partidos = ingest_all()
                if partidos:
                    print(f"{GREEN}✓ {len(partidos)} partidos en Supabase.{RESET}")
        except ImportError as e:
            print(f"\n{RED}Dependencia faltante para scraping: {e}{RESET}")
            print(f"Instala: pip install undetected-chromedriver beautifulsoup4{RESET}\n")
            sys.exit(1)
        except Exception as e:
            print(f"\n{RED}Error en ingesta: {e}{RESET}\n")
            sys.exit(1)
        return

    # ── Carga de datos para el modelo ─────────────────────────────────────────

    try:
        partidos = fetch_all()
    except Exception as e:
        print(f"\n{RED}Error al cargar datos: {e}{RESET}\n")
        sys.exit(1)

    if not partidos:
        print(f"{YELLOW}No hay datos disponibles.{RESET}")
        print(f"Ejecuta {BOLD}python main.py --ingest stats{RESET} para cargar datos.")
        sys.exit(1)

    # ── Cálculo del modelo ────────────────────────────────────────────────────

    print(f"{GRAY}Calculando modelos...{RESET}", end="\r")
    scores   = calcular_scores(partidos)
    rankings = calcular_rankings(scores)
    xstyles  = calcular_xstyle(partidos)
    print(" " * 50, end="\r")

    n_equipos = len(scores)
    print(f"{GREEN}✓ {n_equipos} equipos · {len(partidos)} partidos · xStyle calculado{RESET}")

    # ── Árbitro ───────────────────────────────────────────────────────────────

    arbitro_resuelto: Optional[str] = None
    if args.arbitro:
        perfiles_refs = calcular_perfiles_arbitros(partidos)
        arbitro_resuelto = buscar_arbitro(args.arbitro, perfiles_refs)
        if not arbitro_resuelto:
            print(f"{YELLOW}Árbitro '{args.arbitro}' no encontrado. "
                  f"Usa --arbitros para ver los disponibles.{RESET}")
        else:
            print(f"{GREEN}✓ Árbitro: {arbitro_resuelto}{RESET}")

    # ── Cuotas ────────────────────────────────────────────────────────────────

    cuotas: Optional[dict] = None
    if args.cuota_local or args.cuota_empate or args.cuota_vis:
        cuotas = {
            "local":    args.cuota_local,
            "empate":   args.cuota_empate,
            "visitante": args.cuota_vis,
            "over25":   args.cuota_over25,
            "under25":  args.cuota_under25,
        }
        if all(v is not None for v in (args.cuota_local, args.cuota_empate, args.cuota_vis)):
            print(f"{GREEN}✓ Cuotas 1X2: {args.cuota_local} / {args.cuota_empate} / {args.cuota_vis}{RESET}")
        else:
            print(f"{YELLOW}Cuotas incompletas: se necesitan las tres (local, empate, visitante).{RESET}")
            cuotas = None

    if args.jornada:
        print(f"{GREEN}✓ Jornada: {args.jornada}{RESET}")

    market_input: dict = {}
    if args.market_file:
        try:
            market_input = json.loads(Path(args.market_file).read_text(encoding="utf-8"))
            print(f"{GREEN}✓ Mercado unificado cargado desde: {args.market_file}{RESET}")
        except Exception as e:
            print(f"{YELLOW}No se pudo leer market-file ({e}). Se ignora.{RESET}")
            market_input = {}

    # Compat: mapear argumentos sueltos al mercado unificado.
    if args.cuota_local and args.cuota_empate and args.cuota_vis:
        market_input.setdefault("1x2", {})
        market_input["1x2"].update(
            {"local": args.cuota_local, "empate": args.cuota_empate, "visitante": args.cuota_vis}
        )
    if args.cuota_over25 is not None:
        market_input.setdefault("goals_ou", {})
        market_input["goals_ou"].update(
            {"line": 2.5, "over": args.cuota_over25, "under": args.cuota_under25}
        )

    def _set_ou(key: str, line, over, under):
        if over is None:
            return
        market_input.setdefault(key, {})
        market_input[key].update({"line": line, "over": over, "under": under})

    _set_ou("fouls_ou", args.linea_faltas, args.cuota_over_faltas, args.cuota_under_faltas)
    _set_ou("cards_ou", args.linea_tarjetas, args.cuota_over_tarjetas, args.cuota_under_tarjetas)
    _set_ou("corners_ou", args.linea_corners, args.cuota_over_corners, args.cuota_under_corners)
    _set_ou("shots_ou", args.linea_tiros, args.cuota_over_tiros, args.cuota_under_tiros)
    _set_ou(
        "shots_on_target_ou",
        args.linea_tiros_puerta,
        args.cuota_over_tiros_puerta,
        args.cuota_under_tiros_puerta,
    )
    _set_ou(
        "offsides_ou",
        args.linea_fueras_juego,
        args.cuota_over_fueras_juego,
        args.cuota_under_fueras_juego,
    )

    contexto_competitivo = {
        "local": {
            "days_since_last": args.local_days_since_last,
            "days_to_next": args.local_days_to_next,
            "last_ucl": args.local_last_ucl,
            "next_ucl": args.local_next_ucl,
            "last_competition": args.local_last_competition,
            "next_competition": args.local_next_competition,
            "liga_urgencia": args.local_liga_urgencia,
            "objetivo_liga": args.local_objetivo_liga,
            "riesgo_rotacion": args.local_riesgo_rotacion,
        },
        "visitante": {
            "days_since_last": args.visitante_days_since_last,
            "days_to_next": args.visitante_days_to_next,
            "last_ucl": args.visitante_last_ucl,
            "next_ucl": args.visitante_next_ucl,
            "last_competition": args.visitante_last_competition,
            "next_competition": args.visitante_next_competition,
            "liga_urgencia": args.visitante_liga_urgencia,
            "objetivo_liga": args.visitante_objetivo_liga,
            "riesgo_rotacion": args.visitante_riesgo_rotacion,
        },
    }

    # ── Routing ───────────────────────────────────────────────────────────────

    if args.calibrar_agresividad:
        print(f"{GRAY}Calibrando agresividad por volumen con histórico...{RESET}")
        calib = calibrar_agresividad_volumen(partidos, verbose=True)
        if calib.get("ok"):
            print(f"{GREEN}✓ Recomendación de parámetros:{RESET}")
            print(f"  alpha_card_pressure: {calib['best_alpha_card_pressure']}")
            print("  agresividad_volumen:")
            print(f"    peso_faltas: 1.0")
            print(f"    peso_amarillas: {calib['best_peso_amarillas']}")
            print(f"    peso_rojas: {calib['best_peso_rojas']}")
            print(f"{GRAY}Mejora MAE: {calib['improvement_pct']}%{RESET}")
        else:
            print(f"{YELLOW}No fue posible calibrar: {calib.get('reason', 'sin detalle')}{RESET}")

    elif args.evaluar:
        print(f"{GRAY}Ejecutando backtesting del knowledge pack...{RESET}")
        evaluar_knowledge(partidos, xstyles, n_ultimos=100, verbose=True)

    elif args.arbitros:
        perfiles_refs = calcular_perfiles_arbitros(partidos)
        mostrar_arbitros(perfiles_refs)

    elif args.ranking:
        mostrar_ranking_completo(scores, rankings)

    elif args.equipos:
        mostrar_equipos_disponibles(scores)

    elif args.equipo_a and args.equipo_b:
        equipos_ordenados = sorted(scores.keys())

        def resolver(arg: str) -> Optional[str]:
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(equipos_ordenados):
                    return equipos_ordenados[idx]
                return None
            return buscar_equipo(arg, scores)

        eq_a = resolver(args.equipo_a)
        eq_b = resolver(args.equipo_b)

        errores = []
        if not eq_a:
            errores.append(f"'{args.equipo_a}'")
        if not eq_b:
            errores.append(f"'{args.equipo_b}'")

        if errores:
            print(f"\n{RED}Equipo(s) no encontrado(s): {', '.join(errores)}{RESET}")
            print(f"Usa {BOLD}python main.py --equipos{RESET} para ver la lista numerada.\n")
            sys.exit(1)

        mostrar_enfrentamiento(
            eq_a, eq_b, scores, rankings, xstyles, partidos,
            arbitro=arbitro_resuelto, jornada=args.jornada, cuotas=cuotas,
            contexto_competitivo=contexto_competitivo,
            market_input=market_input if market_input else None,
        )

    elif args.equipo_a and not args.equipo_b:
        print(f"\n{RED}Debes indicar dos equipos.{RESET}")
        parser.print_help()
        sys.exit(1)

    else:
        modo_interactivo(scores, rankings, xstyles, partidos)


if __name__ == "__main__":
    main()
