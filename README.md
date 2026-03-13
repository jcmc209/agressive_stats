# ⚽ Aggressivity Stats — Analizador de Agresividad de La Liga

Herramienta de análisis estadístico para **La Liga Española** que calcula índices de agresividad, predice faltas esperadas (xFouls) y perfila el estilo de juego de cada equipo.

## ¿Qué hace este proyecto?

- **IAP (Índice de Agresividad Ponderado)**: Puntúa cada equipo (1–10) según faltas, tarjetas amarillas y rojas, con ponderación temporal (partidos recientes pesan más).
- **xFouls**: Predice las faltas esperadas en un partido concreto, teniendo en cuenta equipos, árbitro y presión de tarjetas.
- **xStyle**: Perfil de estilo de juego (tiros, precisión, corners, eficiencia, faltas provocadas, etc.).
- **Rankings**: Clasificación de agresividad general, local y visitante.
- **Árbitros**: Estadísticas por árbitro (faltas/partido, tarjetas/partido) y clasificación (permisivo / estricto / muy estricto).

Datos almacenados en **Supabase**. La ingesta se alimenta de **football-data.co.uk** (partidos, faltas, tarjetas, tiros) y **fbref.com** (posesión vía scraping).

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

### Comparativa entre dos equipos

```bash
python main.py "Athletic" "Atletico"
python main.py "Getafe" "Barca" --arbitro "Gil"
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
├── main.py                       # CLI (presentación + routing)
├── config.py                     # Lee .env + config.yaml
├── config.yaml                   # Parámetros del modelo
├── .env                          # Secretos (Supabase) — no se sube
├── .env.example                  # Plantilla de credenciales
│
├── ingestion/                    # Capa de datos
│   ├── __init__.py               # Orquestación (fetch_all, ingest_*)
│   ├── supabase_client.py        # Conexión y CRUD Supabase
│   ├── csv_source.py             # Descarga CSVs football-data.co.uk
│   ├── scraper.py                # Scrape posesión desde fbref.com
│   └── team_mapping.py           # Normalización de nombres entre fuentes
│
├── model/                        # Capa de modelo (cálculo puro, sin I/O)
│   ├── __init__.py               # Reexporta API pública
│   ├── helpers.py                # parse_date, decay_weight
│   ├── iap.py                    # Índice de Agresividad + rankings
│   ├── xfouls.py                 # Predicción de faltas esperadas
│   ├── xstyle.py                 # Perfil de estilo de juego
│   └── referees.py               # Perfilado estadístico de árbitros
│
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
| `alpha_card_pressure` | `0.40` | Efecto de presión de tarjetas en xFouls |
| `umbrales.alto_riesgo` | `7.0` | Umbral IAP para riesgo alto |
| `umbrales.critico` | `8.5` | Umbral IAP para riesgo crítico |

---

## Licencia

Proyecto de uso personal/educativo.
