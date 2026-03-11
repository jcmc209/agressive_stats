# =============================================================================
# CONFIGURACIÓN DEL MODELO DE AGRESIVIDAD - LA LIGA
# =============================================================================
# Copia este archivo como config.py y rellena tus credenciales.
# config.py está en .gitignore y NO se sube al repositorio.

# --- Supabase (base de datos en la nube) ---
SUPABASE_URL = "https://tu-proyecto.supabase.co"
SUPABASE_KEY = "tu_supabase_service_role_key"

# --- API-Football (enrich: xG, posesión, offsides) ---
# Dashboard: https://dashboard.api-football.com/
# Límite plan Free: 100 llamadas/día.
API_FOOTBALL_KEY = "tu_api_football_key"

# --- Fuente de datos CSV: football-data.co.uk ---
SEASONS = [2023, 2024, 2025]

# --- Decay temporal ---
DECAY_LAMBDA = 0.003

# --- Pesos del índice ---
PESO_FALTAS = 1.0
PESO_AMARILLAS = 2.5
PESO_ROJAS = 6.0

# --- Caché local ---
CACHE_FILE = "cache_partidos.json"

# --- Umbrales de riesgo disciplinario ---
UMBRAL_ALTO_RIESGO = 7.0
UMBRAL_CRITICO = 8.5

# --- xFouls ---
ALPHA_CARD_PRESSURE = 0.40
