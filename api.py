#!/usr/bin/env python3
"""
API HTTP para predicciones del knowledge pack (solo JSON).

Uso:
  uvicorn api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
from contextlib import redirect_stdout, redirect_stderr
from threading import Lock
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ingestion import ingest_all, ingest_possession, ingest_stats, supabase_client
from model import (
    ajustar_knowledge_pack,
    buscar_arbitro,
    buscar_equipo,
    calcular_perfiles_arbitros,
    calcular_rankings,
    calcular_scores,
    calcular_xfouls,
    calcular_xstyle,
    ensamblar_knowledge_pack,
)


@dataclass
class ModelState:
    partidos: list[dict]
    scores: dict
    rankings: dict
    xstyles: dict
    updated_at: str


_state_lock = Lock()
_state: Optional[ModelState] = None
_ingest_lock = Lock()


def _load_state() -> ModelState:
    partidos = supabase_client.fetch_all_matches()
    if not partidos:
        raise RuntimeError("No hay partidos disponibles en Supabase.")
    scores = calcular_scores(partidos)
    rankings = calcular_rankings(scores)
    xstyles = calcular_xstyle(partidos)
    return ModelState(
        partidos=partidos,
        scores=scores,
        rankings=rankings,
        xstyles=xstyles,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )


def get_state(refresh: bool = False) -> ModelState:
    global _state
    with _state_lock:
        if _state is None or refresh:
            _state = _load_state()
        return _state


class CuotasInput(BaseModel):
    local: Optional[float] = None
    empate: Optional[float] = None
    visitante: Optional[float] = None
    over25: Optional[float] = None
    under25: Optional[float] = None


class PredictRequest(BaseModel):
    equipo_local: str = Field(..., min_length=2)
    equipo_visitante: str = Field(..., min_length=2)
    arbitro: Optional[str] = None
    jornada: Optional[int] = None
    refresh_data: bool = False

    # Compatibilidad con CLI actual.
    cuotas: Optional[CuotasInput] = None

    # Estructura unificada de mercados.
    market_input: Optional[dict[str, Any]] = None

    # Contexto competitivo libre (local/visitante con días, compes, objetivos...).
    contexto_competitivo: Optional[dict[str, Any]] = None


class IngestResponse(BaseModel):
    ok: bool
    mode: str
    updated_at: str
    n_partidos: int
    n_equipos: int
    message: str


app = FastAPI(title="Agressive Stats API", version="1.0.0")


def _equipo_dict(nombre: str, s: dict, r: dict, n: int) -> dict:
    sr = s["stats_raw"]
    return {
        "nombre": nombre,
        "iap_general": round(s["general_norm"], 2),
        "iap_local": round(s["local_norm"], 2),
        "iap_visitante": round(s["visitante_norm"], 2),
        "rank_general": r["rank_general"],
        "rank_local": r["rank_local"],
        "rank_visitante": r["rank_visitante"],
        "n_equipos": n,
        "faltas_media": sr["faltas_media"],
        "amarillas_media": sr["amarillas_media"],
        "rojas_media": sr["rojas_media"],
        "n_partidos": s["n_partidos"],
        "n_local": s["n_local"],
        "n_visitante": s["n_visitante"],
    }


def _style_dict(xstyles: dict, nombre: str) -> dict:
    p = xstyles.get(nombre, {})
    poss = p.get("posesion")
    return {
        "estilo": p.get("estilo", "—"),
        "estilo_desc": p.get("estilo_desc", ""),
        "posesion": round(poss, 1) if poss is not None else None,
        "tiros": round(p.get("tiros", 0), 1),
        "precision": round(p.get("precision", 0) * 100, 1),
        "corners": round(p.get("corners", 0), 1),
        "goles": round(p.get("goles", 0), 2),
        "eficiencia": round(p.get("eficiencia", 0) * 100, 1),
        "faltas": round(p.get("fouls", 0), 1),
        "tarj_falta": round(p.get("cards_per_foul", 0), 3),
        "faltas_prov": round(p.get("faltas_prov", 0), 1),
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/refresh")
def refresh() -> dict:
    st = get_state(refresh=True)
    return {
        "ok": True,
        "updated_at": st.updated_at,
        "n_partidos": len(st.partidos),
        "n_equipos": len(st.scores),
    }


def _run_ingest_mode(mode: str) -> None:
    """
    Ejecuta ingesta silenciando stdout/stderr para evitar problemas de encoding
    en entornos Windows cp1252.
    """
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        if mode == "stats":
            ingest_stats()
        elif mode == "possession":
            ingest_possession()
        elif mode == "all":
            ingest_all()
        else:
            raise ValueError(f"Modo de ingesta no soportado: {mode}")


@app.post("/ingest/{mode}", response_model=IngestResponse)
def ingest_endpoint(mode: str, refresh_after: bool = True) -> IngestResponse:
    """
    Lanza ingestas:
      - /ingest/stats
      - /ingest/possession
      - /ingest/all
    """
    mode = mode.strip().lower()
    if mode not in {"stats", "possession", "all"}:
        raise HTTPException(status_code=400, detail="mode debe ser stats | possession | all")

    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una ingesta en ejecución.")
    try:
        _run_ingest_mode(mode)
        st = get_state(refresh=refresh_after)
        return IngestResponse(
            ok=True,
            mode=mode,
            updated_at=st.updated_at,
            n_partidos=len(st.partidos),
            n_equipos=len(st.scores),
            message=f"Ingesta '{mode}' completada correctamente.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en ingesta {mode}: {e}") from e
    finally:
        _ingest_lock.release()


@app.get("/teams")
def teams() -> dict:
    st = get_state()
    return {"teams": sorted(st.scores.keys()), "count": len(st.scores)}


@app.get("/referees")
def referees() -> dict:
    st = get_state()
    refs = calcular_perfiles_arbitros(st.partidos)
    return {"referees": sorted(refs.keys()), "count": len(refs)}


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    st = get_state(refresh=req.refresh_data)
    warnings: list[str] = []

    eq_local = buscar_equipo(req.equipo_local, st.scores)
    eq_visit = buscar_equipo(req.equipo_visitante, st.scores)
    if not eq_local:
        raise HTTPException(status_code=404, detail=f"Equipo local no encontrado: {req.equipo_local}")
    if not eq_visit:
        raise HTTPException(status_code=404, detail=f"Equipo visitante no encontrado: {req.equipo_visitante}")
    if eq_local == eq_visit:
        raise HTTPException(status_code=400, detail="Equipo local y visitante no pueden ser el mismo.")

    arbitro_resuelto: Optional[str] = None
    if req.arbitro:
        refs = calcular_perfiles_arbitros(st.partidos)
        arbitro_resuelto = buscar_arbitro(req.arbitro, refs)
        if not arbitro_resuelto:
            warnings.append(f"Árbitro no encontrado: {req.arbitro}. Se calcula sin ajuste arbitral.")

    xf = calcular_xfouls(st.partidos, eq_local, eq_visit, arbitro_resuelto)
    perfiles_refs = calcular_perfiles_arbitros(st.partidos)

    knowledge_pack = ensamblar_knowledge_pack(
        equipo_local=eq_local,
        equipo_visitante=eq_visit,
        partidos=st.partidos,
        xstyles=st.xstyles,
        xfouls_result=xf,
        ref_perfiles=perfiles_refs,
        arbitro=arbitro_resuelto,
        jornada=req.jornada,
        contexto_competitivo_input=req.contexto_competitivo,
    )

    cuotas_dict = req.cuotas.model_dump() if req.cuotas else None
    if cuotas_dict or req.market_input:
        knowledge_pack = ajustar_knowledge_pack(
            knowledge_pack,
            odds_h=cuotas_dict.get("local") if cuotas_dict else None,
            odds_d=cuotas_dict.get("empate") if cuotas_dict else None,
            odds_a=cuotas_dict.get("visitante") if cuotas_dict else None,
            odds_over25=cuotas_dict.get("over25") if cuotas_dict else None,
            odds_under25=cuotas_dict.get("under25") if cuotas_dict else None,
            market_input=req.market_input,
        )

    sa = st.scores[eq_local]
    sb = st.scores[eq_visit]
    ra = st.rankings[eq_local]
    rb = st.rankings[eq_visit]
    n = len(st.scores)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_updated_at": st.updated_at,
        "equipo_local": eq_local,
        "equipo_visitante": eq_visit,
        "arbitro": arbitro_resuelto,
        "jornada": req.jornada,
        "warnings": warnings,
        "iap": {
            "local": _equipo_dict(eq_local, sa, ra, n),
            "visitante": _equipo_dict(eq_visit, sb, rb, n),
            "mas_agresivo": eq_local if sa["general_norm"] >= sb["general_norm"] else eq_visit,
            "diferencia": round(abs(sa["general_norm"] - sb["general_norm"]), 2),
        },
        "xfouls": {
            "base_local": round(xf["base_local"], 1),
            "base_visitante": round(xf["base_visitante"], 1),
            "xfouls_local": round(xf["xfouls_local"], 1),
            "xfouls_visitante": round(xf["xfouls_visitante"], 1),
            "total": round(xf["xfouls_total"], 1),
            "avg_liga": round(xf["avg_liga"], 1),
            "diff_pct": round((xf["xfouls_total"] - xf["avg_liga"]) / xf["avg_liga"] * 100, 1),
            "arbitro": xf.get("arbitro"),
        },
        "xstyle": {
            "local": _style_dict(st.xstyles, eq_local),
            "visitante": _style_dict(st.xstyles, eq_visit),
        },
        "knowledge_pack": knowledge_pack,
    }
    return result

