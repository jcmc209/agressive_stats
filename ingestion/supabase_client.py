"""Conexión y operaciones CRUD contra Supabase."""

from __future__ import annotations

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY


def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _row_to_partido(row: dict) -> dict:
    """Convierte una fila de Supabase al formato interno del modelo."""
    return {
        "fixture_id": row["match_id"],
        "date": row["match_date"],
        "season": int(row["season"].split("-")[0]),
        "referee": row.get("referee") or "",
        "home": {
            "name": row["home_team"],
            "fouls": row.get("fouls_home") or 0,
            "yellow_cards": row.get("yellows_home") or 0,
            "red_cards": row.get("reds_home") or 0,
            "shots": row.get("shots_home") or 0,
            "shots_on_target": row.get("shots_on_target_home") or 0,
            "corners": row.get("corners_home") or 0,
            "goals": row.get("home_goals") or 0,
            "possession": row.get("possession_home"),
        },
        "away": {
            "name": row["away_team"],
            "fouls": row.get("fouls_away") or 0,
            "yellow_cards": row.get("yellows_away") or 0,
            "red_cards": row.get("reds_away") or 0,
            "shots": row.get("shots_away") or 0,
            "shots_on_target": row.get("shots_on_target_away") or 0,
            "corners": row.get("corners_away") or 0,
            "goals": row.get("away_goals") or 0,
            "possession": row.get("possession_away"),
        },
    }


def _paginate_select(sb: Client, table: str, columns: str, **kwargs) -> list[dict]:
    """Descarga todas las filas de una tabla con paginación automática."""
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    order_col = kwargs.get("order")
    order_desc = kwargs.get("desc", False)

    while True:
        q = sb.table(table).select(columns)
        if order_col:
            q = q.order(order_col, desc=order_desc)
        r = q.range(offset, offset + page_size - 1).execute()
        all_rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    return all_rows


def fetch_all_matches() -> list[dict]:
    """Descarga todos los partidos de Supabase → formato modelo."""
    sb = get_client()
    cols = (
        "match_id,match_date,season,referee,"
        "home_team,away_team,home_goals,away_goals,"
        "fouls_home,fouls_away,yellows_home,yellows_away,"
        "reds_home,reds_away,shots_home,shots_away,"
        "shots_on_target_home,shots_on_target_away,"
        "corners_home,corners_away,"
        "possession_home,possession_away"
    )
    rows = _paginate_select(sb, "matches", cols, order="match_date")
    return [_row_to_partido(r) for r in rows]


def get_existing_keys(sb: Client) -> set[tuple[str, str, str]]:
    """Devuelve conjunto de (date, home, away) ya existentes."""
    rows = _paginate_select(sb, "matches", "match_date,home_team,away_team")
    return {(r["match_date"], r["home_team"], r["away_team"]) for r in rows}


def sync_new_matches(records: list[dict]) -> tuple[int, int]:
    """Sube partidos nuevos a Supabase. Devuelve (insertados, omitidos)."""
    sb = get_client()

    print("  → Consultando partidos existentes en Supabase...")
    existing = get_existing_keys(sb)
    print(f"     {len(existing)} partidos ya en Supabase.")

    nuevos = [
        r for r in records
        if (r["match_date"], r["home_team"], r["away_team"]) not in existing
    ]

    if not nuevos:
        return 0, len(records)

    batch_size = 500
    insertados = 0
    for i in range(0, len(nuevos), batch_size):
        batch = nuevos[i : i + batch_size]
        sb.table("matches").insert(batch).execute()
        insertados += len(batch)

    return insertados, len(records) - insertados


def get_matches_with_possession() -> set[tuple[str, str, str]]:
    """Devuelve conjunto de (date, home, away) que ya tienen posesión."""
    sb = get_client()
    rows = _paginate_select(sb, "matches", "match_date,home_team,away_team")
    result: set[tuple[str, str, str]] = set()
    for r in rows:
        result.add((r["match_date"], r["home_team"], r["away_team"]))
    # Solo filtramos los que SÍ tienen posesión con una segunda query
    rows_poss: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        r2 = (
            sb.table("matches")
            .select("match_date,home_team,away_team")
            .not_.is_("possession_home", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows_poss.extend(r2.data)
        if len(r2.data) < page_size:
            break
        offset += page_size
    return {(r["match_date"], r["home_team"], r["away_team"]) for r in rows_poss}


def update_possession_single(
    match_date: str, home_team: str, away_team: str,
    possession_home: int, possession_away: int,
) -> bool:
    """Actualiza posesión de un partido concreto. Devuelve True si se actualizó."""
    sb = get_client()
    try:
        result = (
            sb.table("matches")
            .update({
                "possession_home": possession_home,
                "possession_away": possession_away,
            })
            .eq("match_date", match_date)
            .eq("home_team", home_team)
            .eq("away_team", away_team)
            .execute()
        )
        return bool(result.data)
    except Exception:
        return False
