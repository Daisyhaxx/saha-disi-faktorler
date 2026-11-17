import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS_API = "https://site.api.espn.com/apis/v2/sports/soccer"

# ESPN lig kodu -> lig bilgisi. calendar_year: sezon tek takvim yılında oynanıyor
LEAGUES = {
    "eng.1": {"name": "Premier League", "country": "England"},
    "eng.2": {"name": "Championship", "country": "England"},
    "ger.1": {"name": "Bundesliga", "country": "Germany"},
    "esp.1": {"name": "La Liga", "country": "Spain"},
    "ita.1": {"name": "Serie A", "country": "Italy"},
    "fra.1": {"name": "Ligue 1", "country": "France"},
    "ned.1": {"name": "Eredivisie", "country": "Netherlands"},
    "por.1": {"name": "Primeira Liga", "country": "Portugal"},
    "bra.1": {"name": "Brasileirão Série A", "country": "Brazil", "calendar_year": True},
}
SEASONS = list(range(2015, 2026))  # sezon başlangıç yılı

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "espn"
SLEEP_SECONDS = 1.0  # istekler arasında kısa bekleme

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0"
# 429 ve 5xx yanıtlarında ve bağlantı hatalarında artan aralıklarla yeniden dener
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=5, status_forcelist=[429, 500, 502, 503, 504])))

request_count = 0
failures = []


def save_json(data, path):
    # Önce geçici dosyaya yazılır, yarıda kesilen bir yazma "indirilmiş" sayılmaması için
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)


def load_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def fetch_cached(path, url, params):
    # Dosya diskte varsa onu okur, yoksa API'den çekip ham yanıtı kaydeder.
    global request_count
    if path.exists():
        return load_json(path)
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()  # hatalı yanıtta durur
    data = r.json()
    save_json(data, path)
    request_count += 1
    time.sleep(SLEEP_SECONDS)
    return data


def collect_standings(slug, season):
    # Puan tablosunu kaydeder ve o sezonki takımların ESPN id'lerini döndürür.
    path = RAW_DIR / slug / "standings" / f"{season}.json.gz"
    data = fetch_cached(path, f"{STANDINGS_API}/{slug}/standings", {"season": season})
    children = data.get("children", [])  # olmayan sezonda bu alan hiç gelmiyor
    if not children:
        print(f"  ! {slug} {season}: puan tablosu boş")
        return []
    returned = children[0]["standings"].get("season")
    if returned != season:
        print(f"  ! {slug} {season}: ESPN {returned} sezonunu döndürdü")
    return [e["team"]["id"] for child in children for e in child["standings"]["entries"]]


def collect_scorers(slug, season):
    # Yanıt gol ve asist liderlerini birlikte içerir. Üst seviye "season" alanı
    # her zaman mevcut sezonu gösterir, sezon bilgisi dosya adından alınmalı.
    path = RAW_DIR / slug / "scorers" / f"{season}.json.gz"
    fetch_cached(path, f"{SITE_API}/{slug}/statistics", {"season": season})


def collect_rosters(slug, season, team_ids):
    # Hata takım bazında yakalanır tek bir takım sezonun geri kalanını engellemesin
    for team_id in team_ids:
        path = RAW_DIR / slug / "rosters" / str(season) / f"{team_id}.json.gz"
        try:
            data = fetch_cached(path, f"{SITE_API}/{slug}/teams/{team_id}/roster", {"season": season})
        except requests.RequestException as e:
            failures.append(f"{slug} {season} takım {team_id}: {e}")
            print(f"  ! {slug} {season} takım {team_id}: {e}")
            continue
        returned = data.get("season", {}).get("year")
        if returned != season:
            print(f"  ! {slug} {season} takım {team_id}: ESPN {returned} sezonunu döndürdü")


def collect_matches(slug, year):
    path = RAW_DIR / slug / "matches" / f"{year}.json.gz"
    data = fetch_cached(path, f"{SITE_API}/{slug}/scoreboard", {"dates": year, "limit": 1000})
    if len(data.get("events", [])) >= 1000:
        print(f"  ! {slug} {year}: 1000 maç sınırına ulaşıldı, veri eksik olabilir")


def match_years(slug, seasons):
    if LEAGUES[slug].get("calendar_year"):
        return sorted(set(seasons))
    return sorted({year for s in seasons for year in (s, s + 1)})


def main():
    parser = argparse.ArgumentParser(description="ESPN'den ham futbol verisi toplar.")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=list(LEAGUES))
    parser.add_argument("--seasons", nargs="+", type=int, default=SEASONS)
    args = parser.parse_args()

    started = time.time()
    for slug in args.leagues:
        print(f"\n== {LEAGUES[slug]['name']} ({slug})")
        for season in args.seasons:
            try:
                team_ids = collect_standings(slug, season)
                collect_scorers(slug, season)
                collect_rosters(slug, season, team_ids)
                print(f"  {season}: puan tablosu, gol krallığı ve {len(team_ids)} kadro tamam")
            except requests.RequestException as e:
                failures.append(f"{slug} {season}: {e}")
                print(f"  ! {slug} {season}: {e}")
        for year in match_years(slug, args.seasons):
            try:
                collect_matches(slug, year)
            except requests.RequestException as e:
                failures.append(f"{slug} maçlar {year}: {e}")
                print(f"  ! {slug} maçlar {year}: {e}")
        print(f"  maçlar tamam ({len(match_years(slug, args.seasons))} takvim yılı)")

    # README'deki "verinin çekildiği tarih" bilgisi için, yalnızca yeni veri indiyse güncellenir
    if request_count:
        meta = {
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "leagues": args.leagues,
            "seasons": args.seasons,
            "new_requests": request_count,
            "failures": failures,
        }
        (RAW_DIR / "_last_fetch.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    minutes = (time.time() - started) / 60
    print(f"\nBitti: {request_count} yeni istek, {minutes:.1f} dk, {len(failures)} hata")
    for f in failures:
        print("  -", f)


if __name__ == "__main__":
    main()
