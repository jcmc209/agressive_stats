"""Paquete de modelos — cálculo puro, sin I/O."""

from model.iap import calcular_scores, calcular_rankings, buscar_equipo, nivel_riesgo
from model.xfouls import calcular_xfouls, nivel_intensidad
from model.xstyle import calcular_xstyle, STYLE_DIMS
from model.referees import calcular_perfiles as calcular_perfiles_arbitros, buscar_arbitro
from model.match_knowledge import ensamblar_knowledge_pack
from model.market_adjust import ajustar_knowledge_pack
