# ⚽ Aggressivity Stats — Analizador de Agresividad de La Liga

Herramienta de análisis estadístico para **La Liga Española** que calcula índices de agresividad, predice faltas esperadas (xFouls) y perfila el estilo de juego de cada equipo. Pensada para apostadores, analistas y aficionados que quieren evaluar el riesgo disciplinario de los partidos.

## 📋 ¿Qué hace este proyecto?

- **IAP (Índice de Agresividad Ponderado)**: Puntúa cada equipo (1–10) según faltas, tarjetas amarillas y rojas, con ponderación temporal (partidos recientes pesan más).
- **xFouls**: Predice las faltas esperadas en un partido concreto, teniendo en cuenta equipos, árbitro y presión de tarjetas.
- **xStyle**: Perfil de estilo de juego (tiros, precisión, corners, eficiencia, faltas provocadas, etc.).
- **Rankings**: Clasificación de agresividad general, local y visitante.
- **Árbitros**: Estadísticas por árbitro (faltas/partido, tarjetas/partido) y clasificación (permisivo / estricto / muy estricto).

Los datos se obtienen de **Supabase** (base de datos en la nube) y se pueden actualizar desde **football-data.co.uk** o enriquecer con **API-Football** (xG, posesión, offsides).

---

## 🚀 Instalación

```bash
pip install -r requirements.txt
```

### Configuración

1. Copia `config.example.py` como `config.py`:
   ```bash
   cp config.example.py config.py
   ```
2. Edita `config.py` y añade tus credenciales:
   - **Supabase**: URL y service role key (para leer/escribir partidos)
   - **API-Football** (opcional): Para enriquecer con xG, posesión y offsides (~90 llamadas/día en plan gratuito)

---

## 📖 Uso

### Comparativa entre dos equipos
```bash
python main.py "Athletic" "Atletico"
python main.py "Getafe" "Barca" --arbitro "Gil"
```

### Modo interactivo
```bash
python main.py
```

### Comandos útiles
```bash
python main.py --ranking      # Ranking completo de agresividad
python main.py --equipos      # Listar equipos disponibles
python main.py --arbitros     # Listar árbitros con estadísticas
python main.py --pull         # Sincronizar Supabase → caché local
python main.py --refresh      # CSVs → Supabase → caché local
python main.py --enrich       # Enriquecer con xG, posesión (API-Football)
python main.py --enriched     # Ver partidos ya enriquecidos
```

---

## 📊 Ejemplo de salida

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

## 🧮 Modelo IAP

```
IAP_partido = (faltas × 1.0) + (amarillas × 2.5) + (rojas × 6.0)
peso_temporal = e^(-λ × días_desde_partido)
IAP_equipo = Σ(IAP_partido × peso) / Σ(pesos)
Score_final = normalización min-max → escala 1–10 (relativa a la liga)
```

---

## 📁 Estructura del proyecto

```
agressivity_stats/
├── main.py              # CLI principal
├── model.py             # IAP y rankings
├── xmodel.py            # xFouls, xStyle, perfiles de árbitros
├── data_fetcher.py      # Supabase, CSVs, caché local
├── enricher.py          # Enriquecimiento con API-Football
├── config.py            # Credenciales (no se sube a git)
├── config.example.py    # Plantilla de configuración
├── requirements.txt
├── fixture_mapping.json # Mapeo de IDs API-Football
├── supabase_migration_offsides.sql
└── README.md
```

---

## ⚙️ Parámetros (config.py)

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `DECAY_LAMBDA` | `0.003` | Velocidad de decay temporal |
| `PESO_FALTAS` | `1.0` | Peso de faltas en el IAP |
| `PESO_AMARILLAS` | `2.5` | Peso de amarillas |
| `PESO_ROJAS` | `6.0` | Peso de rojas |
| `SEASONS` | `[2023, 2024, 2025]` | Temporadas a incluir |
| `ALPHA_CARD_PRESSURE` | `0.40` | Efecto de presión de tarjetas en xFouls |

---

## 📄 Licencia

Proyecto de uso personal/educativo.
