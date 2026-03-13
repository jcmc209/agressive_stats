# ⚽ Aggressivity Stats — Knowledge Pack Prepartido · La Liga

Herramienta de **knowledge engineering** para La Liga Española que, dado un partido futuro, genera un `match_knowledge_pack` con métricas esperadas, contexto competitivo e interpretación narrativa para alimentar otro modelo estadístico aguas abajo.

## ¿Qué genera el Knowledge Pack?

- **Forma reciente**: últimos 8 partidos (puntos, goles, faltas, tarjetas, racha W/D/L, tendencia mejorando/estable/empeorando).
- **Contexto de temporada**: jornada, tramo (inicio/medio/final), presión de cierre.
- **Tipo de partido esperado**: cruce de estilos (TÉCNICO / FÍSICO / MIXTO / OFENSIVO…) con ángulos derivados.
- **xGoals**: goles esperados local y visitante con Poisson, P(over 2.5), P(BTTS), P(1X2).
- **xPosesión**: posesión esperada basada en histórico o proxy de tempo.
- **xFouls**: faltas esperadas por equipo (ajustadas por árbitro y presión de tarjetas).
- **xTarjetas**: tarjetas amarillas y rojas esperadas.
- **Señal de mercado** (opcional): probabilidades implícitas sin overround, alignment-score modelo vs mercado, comparativa Over/Under.
- **Narrative**: 6–7 bullets accionables listos para otro modelo.

### Módulos base que se siguen usando

- **IAP (Índice de Agresividad Ponderado)**: score 1–10 con decay temporal.
- **xStyle**: perfil de estilo de juego por equipo (tiros, corners, eficiencia, físico…).
- **Árbitros**: estadísticas y factor de influencia sobre faltas y tarjetas.

Datos en **Supabase** · ingesta desde **football-data.co.uk** + **fbref.com**.

---

## Instalación

```bash
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y rellena tus credenciales de Supabase:

```bash
cp .env.example .env
```

---

## Uso

### Knowledge Pack — análisis completo de un partido

```bash
# Básico (forma + contexto estimado + métricas + narrative)
python main.py "Barcelona" "Real Madrid"

# Con árbitro (ajusta xFouls y xTarjetas)
python main.py "Getafe" "Barca" --arbitro "Gil Manzano"

# Con jornada exacta (contexto de temporada preciso)
python main.py "Atletico" "Athletic" --jornada 28

# Con cuotas 1X2 (activa capa de market signal)
python main.py "Real Madrid" "Barca" \
  --cuota-local 2.10 --cuota-empate 3.40 --cuota-vis 3.20

# Completo: árbitro + jornada + cuotas 1X2 + Over/Under
python main.py "Real Madrid" "Barca" \
  --arbitro "Mateu Lahoz" --jornada 32 \
  --cuota-local 2.10 --cuota-empate 3.40 --cuota-vis 3.20 \
  --cuota-over25 1.85 --cuota-under25 1.95

# Calibración automática de agresividad por volumen (walk-forward histórico)
python main.py --calibrar-agresividad
```

### Modo interactivo

```bash
python main.py
```

### Consultas

```bash
python main.py --ranking      # Ranking completo de agresividad
python main.py --equipos      # Listar equipos disponibles
python main.py --arbitros     # Listar árbitros con estadísticas
```

### Ingesta de datos

```bash
python main.py --ingest stats       # football-data.co.uk → Supabase
python main.py --ingest possession  # Scrape fbref.com → Supabase
python main.py --ingest all         # Ambos en secuencia
```

### API HTTP (solo JSON)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `GET /health`
- `GET /teams`
- `GET /referees`
- `POST /refresh`
- `POST /ingest/{mode}` (`mode`: `stats` | `possession` | `all`)
- `POST /predict`

Ejemplo `POST /predict`:

```json
{
  "equipo_local": "Atletico",
  "equipo_visitante": "Getafe",
  "arbitro": "Ortiz Arias",
  "jornada": 28,
  "cuotas": {
    "local": 1.57,
    "empate": 3.8,
    "visitante": 6.5,
    "over25": 2.37,
    "under25": 1.57
  },
  "market_input": {
    "fouls_ou": { "line": 24.5, "over": 1.95, "under": 1.85 },
    "cards_ou": { "line": 4.5, "over": 2.0, "under": 1.8 },
    "corners_ou": { "line": 9.5, "over": 1.9, "under": 1.9 }
  },
  "contexto_competitivo": {
    "local": {
      "days_since_last": 3,
      "days_to_next": 4,
      "last_competition": "ucl",
      "next_competition": "ucl",
      "liga_urgencia": "media",
      "objetivo_liga": "top4",
      "riesgo_rotacion": "alto"
    },
    "visitante": {
      "days_since_last": 7,
      "days_to_next": 7,
      "last_competition": "liga",
      "next_competition": "liga",
      "liga_urgencia": "alta",
      "objetivo_liga": "descenso",
      "riesgo_rotacion": "bajo"
    }
  }
}
```

---

## Ejemplo de salida

```
══════════════════════════════════════════════════════════
  ⚽  ANALIZADOR DE AGRESIVIDAD - LA LIGA ESPAÑOLA  ⚽
══════════════════════════════════════════════════════════

  EQUIPO 1
  Athletic Club
  ──────────────────────────────────────────────────
  General    ████████████░░░░░░░░   7.4/10  (#5 de 20 en liga)
  Local      ██████████████░░░░░░   8.1/10  (#3 de 20)
  Visitante  █████████░░░░░░░░░░░   6.2/10  (#9 de 20)

  PREDICCIÓN DEL PARTIDO — xFouls
  Árbitro: Gil Manzano (ESTRICTO)
  Faltas esperadas: Athletic 14.2 · Atlético 13.8
  Total esperado: 28.0 faltas (media liga: 24.5, +14%)
  Intensidad: PARTIDO DE ALTA INTENSIDAD
══════════════════════════════════════════════════════════
```

---

## Modelo IAP

```
IAP_partido = (faltas × 1.0) + (amarillas × 2.5) + (rojas × 6.0)
peso_temporal = e^(-λ × días_desde_partido)
IAP_equipo = Σ(IAP_partido × peso) / Σ(pesos)
Score_final = normalización min-max → escala 1–10 (relativa a la liga)
```

---

## Estructura del proyecto

```
agressive_stats/
├── main.py                       # CLI (presentación + routing + knowledge pack display)
├── config.py                     # Lee .env + config.yaml
├── config.yaml                   # Parámetros del modelo y knowledge pack
├── .env                          # Secretos (Supabase) — no se sube
├── .env.example                  # Plantilla de credenciales
│
├── ingestion/                    # Capa de datos
│   ├── __init__.py               # Orquestación (fetch_all, ingest_*)
│   ├── supabase_client.py        # Conexión y CRUD Supabase
│   ├── csv_source.py             # Descarga CSVs (+ referee desde football-data)
│   ├── scraper.py                # Scrape posesión desde fbref.com
│   └── team_mapping.py           # Normalización de nombres entre fuentes
│
├── model/                        # Capa de modelo (cálculo puro, sin I/O)
│   ├── __init__.py               # Reexporta API pública
│   ├── helpers.py                # parse_date, decay_weight, safe
│   ├── iap.py                    # Índice de Agresividad Ponderado + rankings
│   ├── xfouls.py                 # Predicción de faltas esperadas
│   ├── xstyle.py                 # Perfil de estilo de juego
│   ├── referees.py               # Perfilado estadístico de árbitros
│   ├── match_knowledge.py        # ★ Knowledge Pack: forma, xGoals, xPosesion,
│   │                             #   xTarjetas, compatibilidad estilos, narrative
│   └── market_adjust.py          # ★ Señal de mercado: probabilidades implícitas,
│                                 #   alignment score, ajuste O/U
│
├── results/                      # JSON + MD generados por partido
├── requirements.txt
├── liga.bat                      # Lanzador Windows
└── README.md
```

---

## Parámetros (config.yaml)

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `decay_lambda` | `0.003` | Velocidad de decay temporal |
| `pesos.faltas` | `1.0` | Peso de faltas en el IAP |
| `pesos.amarillas` | `2.5` | Peso de amarillas |
| `pesos.rojas` | `6.0` | Peso de rojas |
| `seasons` | `[2023, 2024, 2025]` | Temporadas a incluir |
| `alpha_card_pressure` | `0.40` | Efecto presión de tarjetas en xFouls |
| `umbrales.alto_riesgo` | `7.0` | Umbral IAP para riesgo alto |
| `umbrales.critico` | `8.5` | Umbral IAP para riesgo crítico |
| `forma_ventana` | `8` | Nº de partidos para calcular forma reciente |
| `home_goals_factor` | `1.07` | Factor de ventaja local en xGoals |
| `jornadas_laliga` | `38` | Total de jornadas en la temporada |
| `market_alignment_threshold` | `0.60` | Umbral de alineación modelo-mercado |
| `umbrales_match.fisico_*` | varios | Umbrales para clasificar intensidad física |
| `umbrales_match.ofensivo_*` | varios | Umbrales para clasificar ritmo ofensivo |

### Argumentos CLI del knowledge pack

| Argumento | Descripción |
|-----------|-------------|
| `--jornada N` | Jornada exacta del partido (1–38). Si no se da, se estima. |
| `--arbitro "Nombre"` | Árbitro designado (ajusta xFouls y xTarjetas). |
| `--cuota-local F` | Cuota decimal victoria local (activa market signal). |
| `--cuota-empate F` | Cuota decimal empate. |
| `--cuota-vis F` | Cuota decimal victoria visitante. |
| `--cuota-over25 F` | Cuota decimal Over 2.5 goles (opcional). |
| `--cuota-under25 F` | Cuota decimal Under 2.5 goles (opcional). |

---

## Licencia

Proyecto de uso personal/educativo.
