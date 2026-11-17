import re

import pandas as pd

from collect import LEAGUES, RAW_DIR, load_json


def _file_number(path):
    # "2019.json.gz" -> 2019 (sezon) veya "359.json.gz" -> 359 (takım id)
    return int(path.name.split(".")[0])


def matches():
    rows = []
    for slug in LEAGUES:
        for path in sorted((RAW_DIR / slug / "matches").glob("*.json.gz")):
            for event in load_json(path)["events"]:
                comp = event["competitions"][0]
                side = {c["homeAway"]: c for c in comp["competitors"]}
                rows.append({
                    "league": slug,
                    "season": event["season"]["year"],
                    "season_type": event["season"]["type"],
                    "season_slug": event["season"].get("slug"),
                    "match_id": event["id"],
                    "date": event["date"],
                    "status": event["status"]["type"]["name"],
                    "completed": event["status"]["type"]["completed"],
                    "home_team_id": side["home"]["team"]["id"],
                    "home_team": side["home"]["team"]["displayName"],
                    "away_team_id": side["away"]["team"]["id"],
                    "away_team": side["away"]["team"]["displayName"],
                    "home_goals": side["home"].get("score"),
                    "away_goals": side["away"].get("score"),
                    "attendance": comp.get("attendance"),
                    "has_details": bool(comp.get("details")),
                })
    return pd.DataFrame(rows)


def goals():
    rows = []
    for slug in LEAGUES:
        for path in sorted((RAW_DIR / slug / "matches").glob("*.json.gz")):
            for event in load_json(path)["events"]:
                for detail in event["competitions"][0].get("details", []):
                    if not detail.get("scoringPlay"):
                        continue
                    scorer = (detail.get("athletesInvolved") or [{}])[0]  # ilk oyuncu golü atan
                    rows.append({
                        "league": slug,
                        "season": event["season"]["year"],
                        "match_id": event["id"],
                        "team_id": detail.get("team", {}).get("id"),
                        "player_id": scorer.get("id"),
                        "player": scorer.get("displayName"),
                        "minute": detail.get("clock", {}).get("displayValue"),
                        "penalty": detail.get("penaltyKick"),
                        "own_goal": detail.get("ownGoal"),
                        "shootout": detail.get("shootout"),
                    })
    return pd.DataFrame(rows)



STANDINGS_STATS = {
    "rank": "rank",
    "gamesPlayed": "played",
    "wins": "wins",
    "ties": "draws",
    "losses": "losses",
    "pointsFor": "goals_for",
    "pointsAgainst": "goals_against",
    "pointDifferential": "goal_diff",
    "deductions": "deductions",
    "points": "points",
}


def standings():
    rows = []
    for slug in LEAGUES:
        for path in sorted((RAW_DIR / slug / "standings").glob("*.json.gz")):
            for child in load_json(path).get("children", []):
                for entry in child["standings"]["entries"]:
                    stats = {s["name"]: s.get("value") for s in entry["stats"]}
                    rows.append({
                        "league": slug,
                        "season": _file_number(path),
                        "team_id": entry["team"]["id"],
                        "team": entry["team"]["displayName"],
                        **{col: stats.get(name) for name, col in STANDINGS_STATS.items()},
                    })
    return pd.DataFrame(rows)


def scorers():
    rows = []
    for slug in LEAGUES:
        for path in sorted((RAW_DIR / slug / "scorers").glob("*.json.gz")):
            # Üst seviye "season" alanı hep mevcut sezonu gösterir
            stats = load_json(path).get("stats", [])
            goals = next((s for s in stats if s["name"] == "goalsLeaders"), {"leaders": []})
            for order, leader in enumerate(goals["leaders"], start=1):
                athlete = leader["athlete"]
                played = re.search(r"Matches: (\d+)", leader.get("displayValue", ""))
                rows.append({
                    "league": slug,
                    "season": _file_number(path),
                    "list_order": order,
                    "player_id": athlete["id"],
                    "player": athlete["displayName"],
                    "team_id": athlete.get("team", {}).get("id"),
                    "team": athlete.get("team", {}).get("displayName"),
                    "goals": leader.get("value"),
                    "matches": played.group(1) if played else None,
                })
    return pd.DataFrame(rows)


def _appearances(athlete):
    stats = athlete.get("statistics")
    if not stats:
        return None
    for category in stats["splits"]["categories"]:
        for stat in category["stats"]:
            if stat["name"] == "appearances":
                return stat.get("value")
    return None


def players():
    rows = []
    for slug in LEAGUES:
        for path in sorted((RAW_DIR / slug / "rosters").glob("*/*.json.gz")):
            data = load_json(path)
            for athlete in data.get("athletes", []):
                rows.append({
                    "league": slug,
                    "season": int(path.parent.name),
                    "team_id": _file_number(path),
                    "team": data.get("team", {}).get("displayName"),
                    "player_id": athlete["id"],
                    "player": athlete["displayName"],
                    "date_of_birth": athlete.get("dateOfBirth"),
                    "citizenship": athlete.get("citizenship"),
                    "position": athlete.get("position", {}).get("name"),
                    "appearances": _appearances(athlete),
                })
    return pd.DataFrame(rows)
