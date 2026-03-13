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
)

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
) -> dict:
    sa = scores[equipo_a]
    sb = scores[equipo_b]
    ra = rankings[equipo_a]
    rb = rankings[equipo_b]
    n  = len(scores)

    xf = calcular_xfouls(partidos, equipo_a, equipo_b, arbitro)
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
        return {
            "estilo":      p.get("estilo", "—"),
            "estilo_desc": p.get("estilo_desc", ""),
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

    return {
        "generado":          datetime.now().isoformat(timespec="seconds"),
        "equipo_local":      equipo_a,
        "equipo_visitante":  equipo_b,
        "arbitro":           arbitro,
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
        "## Índice de Agresividad (IAP)",
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
        f"| Tiros/P | {sb['tiros']} |",
        f"| Precisión tiro | {sb['precision']}% |",
        f"| Corners/P | {sb['corners']} |",
        f"| Goles/P | {sb['goles']} |",
        f"| Eficiencia gol | {sb['eficiencia']}% |",
        f"| Faltas/P | {sb['faltas']} |",
        f"| Tarjetas/Falta | {sb['tarj_falta']} |",
        f"| Faltas provocadas | {sb['faltas_prov']} |",
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


def mostrar_enfrentamiento(
    equipo_a: str, equipo_b: str,
    scores: dict, rankings: dict,
    xstyles: dict, partidos: list,
    arbitro: Optional[str] = None,
) -> None:
    resultado = _construir_resultado(equipo_a, equipo_b, scores, rankings, xstyles, partidos, arbitro)
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

    print(f"  {BOLD}IAP (Agresividad){RESET}")
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
        description="Analizador de Agresividad + xFouls + xStyle — La Liga Española",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                                    # Modo interactivo (selector numerico)
  python main.py 2 15                               # Por numero de la lista (--equipos)
  python main.py "Athletic" "Atletico"              # Por nombre (busqueda aproximada)
  python main.py "Getafe" "Barca" --arbitro "Gil"   # Con arbitro
  python main.py --ranking                          # Ranking de agresividad
  python main.py --equipos                          # Listar equipos numerados
  python main.py --arbitros                         # Listar arbitros con estadisticas
  python main.py --ingest stats                     # CSVs -> Supabase
  python main.py --ingest possession                # Scrape fbref -> Supabase
  python main.py --ingest all                       # stats + possession
        """
    )
    parser.add_argument("equipo_a", nargs="?", help="Primer equipo")
    parser.add_argument("equipo_b", nargs="?", help="Segundo equipo")
    parser.add_argument("--ranking",   action="store_true", help="Mostrar ranking completo")
    parser.add_argument("--equipos",   action="store_true", help="Listar equipos disponibles")
    parser.add_argument("--arbitros",  action="store_true", help="Listar árbitros con estadísticas")
    parser.add_argument("--arbitro",   type=str, default=None,
                        help="Nombre del árbitro para ajustar xFouls")
    parser.add_argument("--ingest",    choices=["stats", "possession", "all"],
                        help="Ingesta de datos: stats | possession | all")

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

    # ── Routing ───────────────────────────────────────────────────────────────

    if args.arbitros:
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

        mostrar_enfrentamiento(eq_a, eq_b, scores, rankings, xstyles, partidos, arbitro_resuelto)

    elif args.equipo_a and not args.equipo_b:
        print(f"\n{RED}Debes indicar dos equipos.{RESET}")
        parser.print_help()
        sys.exit(1)

    else:
        modo_interactivo(scores, rankings, xstyles, partidos)


if __name__ == "__main__":
    main()
