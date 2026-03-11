#!/usr/bin/env python3
"""
main.py
-------
Script principal del Analizador de Agresividad de La Liga.

Uso:
  python main.py                          # Modo interactivo
  python main.py "Athletic" "Atletico"    # Modo directo
  python main.py --ranking                # Ver ranking completo
  python main.py --equipos                # Listar todos los equipos disponibles
  python main.py --arbitros               # Listar árbitros con estadísticas
  python main.py --pull                   # Sincronizar Supabase → caché local
  python main.py --refresh               # CSVs → Supabase → caché local
  python main.py --enrich                # Enriquecer partidos con xG, posesión y offsides (API-Football, ~90/día)
  python main.py --enrich-reset          # Reiniciar skips y enriquecer (reintenta fixtures fallidos)
  python main.py --enriched              # Listar partidos que ya tienen xG/posesión rellenados

Requisitos:
  pip install requests
"""

import sys
import argparse
from typing import Optional

# Forzar UTF-8 en stdout/stderr para que los emojis funcionen en CMD de Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from data_fetcher import fetch_all_data, pull_from_supabase, refresh_from_csv, get_all_team_names
from model import calcular_scores, calcular_rankings, buscar_equipo, nivel_riesgo
from xmodel import (
    calcular_xfouls, calcular_xstyle,
    calcular_perfiles_arbitros, buscar_arbitro,
    nivel_intensidad, STYLE_DIMS,
)

# ── Colores ANSI ──────────────────────────────────────────────────────────────
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
    """Genera una barra visual para el score (1-10)."""
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
    """Imprime el bloque de información de un equipo."""
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

    # General
    print(f"  {BOLD}General  {RESET}  {barra_agresividad(g)}  "
          f"{BOLD}{g:4.1f}/10{RESET}  "
          f"{GRAY}(#{rg} de {n_equipos} en liga){RESET}")

    # Local
    print(f"  {BLUE}Local    {RESET}  {barra_agresividad(lo)}  "
          f"{BOLD}{lo:4.1f}/10{RESET}  "
          f"{GRAY}(#{rlo} de {n_equipos}){RESET}")

    # Visitante
    print(f"  {MAGENTA}Visitante{RESET}  {barra_agresividad(vi)}  "
          f"{BOLD}{vi:4.1f}/10{RESET}  "
          f"{GRAY}(#{rvi} de {n_equipos}){RESET}")

    # Stats detalladas
    sr = s["stats_raw"]
    print(f"\n  {GRAY}Medias por partido: "
          f"{sr['faltas_media']} faltas · "
          f"{sr['amarillas_media']} amarillas · "
          f"{sr['rojas_media']} rojas{RESET}")
    print(f"  {GRAY}Partidos analizados: {s['n_partidos']} "
          f"({s['n_local']} local / {s['n_visitante']} visitante){RESET}")


def barra_dim(valor_norm: float, ancho: int = 14) -> str:
    """Barra de progreso 1-10 para dimensiones de estilo."""
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
    """Imprime el bloque de estilo de juego de un equipo."""
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
    """Imprime el bloque de predicción de faltas esperadas."""
    sep = f"{GRAY}  {'─' * 56}{RESET}"
    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}{CYAN}  PREDICCIÓN DEL PARTIDO — xFouls{RESET}")
    print(f"{sep}")

    # Árbitro
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
    """Lista todos los árbitros con sus estadísticas."""
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


def mostrar_enfrentamiento(
    equipo_a: str, equipo_b: str,
    scores: dict, rankings: dict,
    xstyles: dict, partidos: list,
    arbitro: Optional[str] = None,
) -> None:
    """Muestra la comparativa completa: IAP + xStyle + xFouls."""
    n_equipos = len(scores)
    imprimir_cabecera()

    # ── Equipo A ──────────────────────────────────────────────────────────────
    print(f"{BOLD}{CYAN}  EQUIPO 1{RESET}")
    imprimir_equipo(equipo_a, scores, rankings, n_equipos)
    if equipo_a in xstyles:
        imprimir_xstyle(equipo_a, xstyles[equipo_a])

    print(f"\n{GRAY}  {'─' * 56}{RESET}\n")

    # ── Equipo B ──────────────────────────────────────────────────────────────
    print(f"{BOLD}{CYAN}  EQUIPO 2{RESET}")
    imprimir_equipo(equipo_b, scores, rankings, n_equipos)
    if equipo_b in xstyles:
        imprimir_xstyle(equipo_b, xstyles[equipo_b])

    print(f"\n{BOLD}{CYAN}{'═' * 58}{RESET}")

    # ── Veredicto IAP ─────────────────────────────────────────────────────────
    sa = scores[equipo_a]["general_norm"]
    sb = scores[equipo_b]["general_norm"]
    diff = abs(sa - sb)
    mas_agresivo = equipo_a if sa >= sb else equipo_b

    print(f"\n{BOLD}  VEREDICTO IAP{RESET}")
    print(f"  {BOLD}{mas_agresivo}{RESET} es el equipo más agresivo "
          f"({diff:.1f} puntos de diferencia)")
    etiqueta, color = nivel_riesgo(sa, sb)
    print(f"  {color}{BOLD}{etiqueta}{RESET}")

    # ── xFouls ────────────────────────────────────────────────────────────────
    xf = calcular_xfouls(partidos, equipo_a, equipo_b, arbitro)
    imprimir_xfouls(xf, equipo_a, equipo_b)


def mostrar_ranking_completo(scores: dict, rankings: dict):
    """Imprime la tabla completa de agresividad de todos los equipos."""
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

    print(f"\n  {GRAY}General = score ponderado con decay temporal (λ={0.003}){RESET}")
    print(f"  {GRAY}Escala 1-10 relativa a los equipos de la liga\n{RESET}")


def mostrar_equipos_disponibles(scores: dict):
    """Lista todos los equipos con datos disponibles."""
    print(f"\n{BOLD}Equipos disponibles en la base de datos:{RESET}\n")
    equipos = sorted(scores.keys())
    for i, equipo in enumerate(equipos, 1):
        print(f"  {i:>2}. {equipo}")
    print()


def modo_interactivo(scores: dict, rankings: dict, xstyles: dict, partidos: list):
    """Solicita los dos equipos por teclado."""
    equipos_disponibles = list(scores.keys())

    print(f"\n{GRAY}Escribe parte del nombre del equipo (ej: 'Athletic', 'Madrid', 'Barca'){RESET}")

    for numero in ["primer", "segundo"]:
        while True:
            nombre_input = input(f"\n{BOLD}Introduce el {numero} equipo: {RESET}").strip()
            if not nombre_input:
                continue
            encontrado = buscar_equipo(nombre_input, scores)
            if encontrado:
                print(f"  {GREEN}✓ Equipo encontrado: {BOLD}{encontrado}{RESET}")
                if numero == "primer":
                    equipo_a = encontrado
                else:
                    equipo_b = encontrado
                break
            else:
                print(f"  {RED}✗ Equipo no encontrado. Equipos disponibles:{RESET}")
                for e in sorted(equipos_disponibles):
                    print(f"    - {e}")

    mostrar_enfrentamiento(equipo_a, equipo_b, scores, rankings, xstyles, partidos)


def main():
    parser = argparse.ArgumentParser(
        description="Analizador de Agresividad + xFouls + xStyle — La Liga Española",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                                   # Modo interactivo
  python main.py "Athletic" "Atletico"             # Comparativa directa
  python main.py "Getafe" "Barca" --arbitro "Gil"  # Con árbitro → xFouls ajustado
  python main.py --ranking                         # Ranking de agresividad
  python main.py --equipos                         # Listar equipos
  python main.py --arbitros                        # Listar árbitros con estadísticas
  python main.py --pull                            # Supabase → caché local
  python main.py --refresh                         # CSVs → Supabase → caché local
  python main.py --enrich                          # API-Football: xG, posesión, offsides (90/día)
  python main.py --enrich-reset                    # Reiniciar skips y enriquecer de nuevo
  python main.py --enriched                        # Ver partidos ya enriquecidos
        """
    )
    parser.add_argument("equipo_a", nargs="?", help="Primer equipo")
    parser.add_argument("equipo_b", nargs="?", help="Segundo equipo")
    parser.add_argument("--ranking",   action="store_true", help="Mostrar ranking completo")
    parser.add_argument("--equipos",   action="store_true", help="Listar equipos disponibles")
    parser.add_argument("--arbitros",  action="store_true", help="Listar árbitros con estadísticas")
    parser.add_argument("--arbitro",   type=str, default=None,
                        help="Nombre del árbitro para ajustar xFouls")
    parser.add_argument("--pull",      action="store_true",
                        help="Descargar datos de Supabase a caché local")
    parser.add_argument("--refresh",   action="store_true",
                        help="Descargar CSVs, subir nuevos a Supabase y actualizar caché")
    parser.add_argument("--enrich",    action="store_true",
                        help="Enriquecer partidos con xG, posesión y offsides vía API-Football (límite ~90/día)")
    parser.add_argument("--enrich-reset", action="store_true",
                        help="Reiniciar lista de fixtures sin stats y enriquecer de nuevo")
    parser.add_argument("--enriched", action="store_true",
                        help="Listar partidos que ya tienen xG/posesión rellenados en Supabase")

    args = parser.parse_args()

    # ── Operaciones de datos que no necesitan el modelo ───────────────────────
    if args.enriched:
        try:
            from enricher import list_enriched
            list_enriched()
        except Exception as e:
            print(f"\n{RED}Error: {e}{RESET}\n")
            sys.exit(1)
        return

    if args.enrich or args.enrich_reset:
        try:
            from enricher import run_enrich
            run_enrich(reset_skips=args.enrich_reset)
        except RuntimeError as e:
            print(f"\n{RED}{e}{RESET}\n")
            sys.exit(1)
        except Exception as e:
            print(f"\n{RED}Error en enricher: {e}{RESET}\n")
            sys.exit(1)
        return

    if args.pull:
        # --pull ahora es equivalente a correr el script normal (siempre va a Supabase)
        # Se mantiene por compatibilidad y para forzar solo la sincronización
        partidos = pull_from_supabase()
        if not partidos:
            print(f"{RED}No se pudieron obtener datos de Supabase.{RESET}")
            sys.exit(1)
        print(f"{GREEN}✓ {len(partidos)} partidos sincronizados desde Supabase.{RESET}")
        return

    if args.refresh:
        partidos = refresh_from_csv()
        if not partidos:
            print(f"{RED}No se pudieron obtener datos.{RESET}")
            sys.exit(1)
        print(f"{GREEN}✓ Datos actualizados: {len(partidos)} partidos en caché local.{RESET}")
        return

    # ── Carga de datos para el modelo ─────────────────────────────────────────
    try:
        partidos = fetch_all_data()
    except Exception as e:
        print(f"\n{RED}Error al cargar datos: {e}{RESET}\n")
        sys.exit(1)

    if not partidos:
        print(f"{YELLOW}No hay datos en caché local.{RESET}")
        print(f"Ejecuta {BOLD}python main.py --pull{RESET} para descargar desde Supabase.")
        sys.exit(1)

    # ── Cálculo del modelo ────────────────────────────────────────────────────
    print(f"{GRAY}Calculando modelos...{RESET}", end="\r")
    scores   = calcular_scores(partidos)
    rankings = calcular_rankings(scores)
    xstyles  = calcular_xstyle(partidos)
    print(" " * 50, end="\r")

    n_equipos = len(scores)
    print(f"{GREEN}✓ {n_equipos} equipos · {len(partidos)} partidos · xStyle calculado{RESET}")

    # ── Árbitro (resolución de nombre) ────────────────────────────────────────
    arbitro_resuelto: Optional[str] = None
    if args.arbitro:
        perfiles_refs = calcular_perfiles_arbitros(partidos)
        arbitro_resuelto = buscar_arbitro(args.arbitro, perfiles_refs)
        if not arbitro_resuelto:
            print(f"{YELLOW}Árbitro '{args.arbitro}' no encontrado. "
                  f"Usa --arbitros para ver los disponibles.{RESET}")
        else:
            print(f"{GREEN}✓ Árbitro: {arbitro_resuelto}{RESET}")

    # ── Routing de comandos ───────────────────────────────────────────────────
    if args.arbitros:
        perfiles_refs = calcular_perfiles_arbitros(partidos)
        mostrar_arbitros(perfiles_refs)

    elif args.ranking:
        mostrar_ranking_completo(scores, rankings)

    elif args.equipos:
        mostrar_equipos_disponibles(scores)

    elif args.equipo_a and args.equipo_b:
        eq_a = buscar_equipo(args.equipo_a, scores)
        eq_b = buscar_equipo(args.equipo_b, scores)

        errores = []
        if not eq_a:
            errores.append(f"'{args.equipo_a}'")
        if not eq_b:
            errores.append(f"'{args.equipo_b}'")

        if errores:
            print(f"\n{RED}Equipo(s) no encontrado(s): {', '.join(errores)}{RESET}")
            print(f"Usa {BOLD}python main.py --equipos{RESET} para ver los nombres disponibles.\n")
            sys.exit(1)

        mostrar_enfrentamiento(eq_a, eq_b, scores, rankings, xstyles, partidos, arbitro_resuelto)

    elif args.equipo_a and not args.equipo_b:
        print(f"\n{RED}Debes indicar dos equipos.{RESET}")
        parser.print_help()
        sys.exit(1)

    else:
        # Modo interactivo por defecto
        modo_interactivo(scores, rankings, xstyles, partidos)


if __name__ == "__main__":
    main()
