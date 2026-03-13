"""
Scraper de posesión de La Liga desde fbref.com.

Usa undetected-chromedriver para pasar Cloudflare.
Proceso lento (~2-4h para una temporada completa) por rate limiting.

Escribe directamente a Supabase. En re-ejecuciones salta partidos
que ya tienen posesión, así que es seguro interrumpir y retomar.

Uso:
  python main.py --scrape
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from ingestion.team_mapping import normalize

FBREF_BASE = "https://fbref.com"
_CURRENT_CALENDAR = f"{FBREF_BASE}/en/comps/12/schedule/La-Liga-Scores-and-Fixtures"


def _calendar_url(season_start: int) -> str:
    """
    Devuelve la URL del calendario de fbref para una temporada dada.
    season_start=2025 → temporada actual (URL sin año).
    season_start=2024 → 2024-2025-La-Liga-Scores-and-Fixtures.
    season_start=2023 → 2023-2024-La-Liga-Scores-and-Fixtures.
    """
    from datetime import date
    current_year = date.today().year
    if season_start >= current_year:
        return _CURRENT_CALENDAR
    slug = f"{season_start}-{season_start + 1}"
    return f"{FBREF_BASE}/en/comps/12/{slug}/schedule/{slug}-La-Liga-Scores-and-Fixtures"


def _create_driver():
    import undetected_chromedriver as uc

    opts = uc.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    return uc.Chrome(options=opts)


def _wait_for_page(driver, timeout: int = 20) -> bool:
    for _ in range(timeout):
        title = driver.title.lower()
        if "moment" not in title and "momento" not in title and len(driver.title) > 3:
            return True
        time.sleep(1)
    return False


def _extract_date(soup, url: str = "") -> str:
    """Extrae la fecha del partido (YYYY-MM-DD) de un match report de fbref."""
    el = soup.find(attrs={"data-venue-date": True})
    if el:
        return el["data-venue-date"]

    meta = soup.find("div", class_="scorebox_meta")
    if meta:
        for a in meta.find_all("a"):
            href = a.get("href", "")
            for part in href.split("/"):
                if len(part) == 10 and part[4:5] == "-" and part[7:8] == "-":
                    try:
                        datetime.strptime(part, "%Y-%m-%d")
                        return part
                    except ValueError:
                        pass

    if url:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", url)
        if m:
            return m.group(1)

    return ""


def _scrape_season(driver, season_start: int, already_done: set) -> tuple[int, int]:
    """
    Scrapea posesión de una temporada concreta.
    Devuelve (escritos, saltados).
    """
    from bs4 import BeautifulSoup
    from ingestion.supabase_client import update_possession_single

    url = _calendar_url(season_start)
    season_label = f"{season_start}-{season_start + 1}"
    print(f"\n--- Temporada {season_label} ---")
    print(f"    {url}")

    driver.get(url)
    if not _wait_for_page(driver):
        print("x No se pudo pasar Cloudflare en calendario")
        return 0, 0

    time.sleep(3)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    match_links = []
    for td in soup.select('td[data-stat="match_report"]'):
        a = td.find("a")
        if a and a.get("href"):
            match_links.append(FBREF_BASE + a["href"])

    total = len(match_links)
    print(f"    {total} partidos encontrados\n")

    written = 0
    skipped = 0

    for i, link in enumerate(match_links):
        print(f"  [{i + 1:>3}/{total}] ", end="", flush=True)
        try:
            driver.get(link)
            if not _wait_for_page(driver):
                print("x Cloudflare bloqueado")
                continue

            time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")

            match_date = _extract_date(soup, link)
            scorebox = soup.find("div", class_="scorebox")
            teams = (
                [a.text.strip() for a in scorebox.select('strong > a[href*="/squads/"]')]
                if scorebox
                else []
            )

            home_name = normalize(teams[0]) if teams else ""
            away_name = normalize(teams[1]) if len(teams) > 1 else ""

            if (match_date, home_name, away_name) in already_done:
                print(f"skip  {home_name} vs {away_name}")
                skipped += 1
                continue

            team_stats = soup.find("div", id="team_stats")
            if not team_stats:
                print("x  Sin team_stats")
                continue

            rows_ts = team_stats.find_all("tr")
            found = False
            for j, row in enumerate(rows_ts):
                th = row.find("th")
                if th and "Possession" in th.get_text():
                    if j + 1 < len(rows_ts):
                        strongs = rows_ts[j + 1].find_all("strong")
                        if len(strongs) >= 2:
                            home_poss = int(strongs[0].text.strip().replace("%", ""))
                            away_poss = int(strongs[1].text.strip().replace("%", ""))

                            ok = update_possession_single(
                                match_date, home_name, away_name,
                                home_poss, away_poss,
                            )
                            if ok:
                                print(f"ok    {home_name} {home_poss}% vs {away_poss}% {away_name}")
                                written += 1
                                already_done.add((match_date, home_name, away_name))
                            else:
                                print(f"x  No en BD: {home_name} vs {away_name} ({match_date})")
                            found = True
                    break

            if not found:
                print("x  Sin posesion en pagina")

        except Exception as e:
            print(f"x  Error: {e}")

        time.sleep(4)

    return written, skipped


def scrape_possession(seasons: list[int] | None = None) -> int:
    """
    Scrapea posesion de todos los partidos de La Liga desde fbref
    y escribe cada resultado directamente a Supabase.

    Itera sobre todas las temporadas en config.SEASONS (o las que se pasen).
    Salta partidos que ya tienen posesion. Es seguro interrumpir y retomar.
    Devuelve el total de partidos nuevos escritos.
    """
    from ingestion.supabase_client import get_matches_with_possession
    from config import SEASONS

    if seasons is None:
        seasons = SEASONS

    print("Consultando partidos con posesion ya en Supabase...")
    already_done = get_matches_with_possession()
    print(f"   {len(already_done)} partidos ya tienen posesion.\n")

    print("Iniciando Chrome...")
    driver = _create_driver()
    total_written = 0
    total_skipped = 0

    try:
        for season_start in seasons:
            w, s = _scrape_season(driver, season_start, already_done)
            total_written  += w
            total_skipped  += s
            if season_start != seasons[-1]:
                print("  Pausa entre temporadas...")
                time.sleep(10)

    finally:
        driver.quit()

    print(f"\nTotal: {total_written} partidos actualizados, {total_skipped} ya tenian posesion.")
    return total_written
