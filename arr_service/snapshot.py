"""Pull full state from each instance and write it to a timestamped snapshot.

Every write operation takes a snapshot first, so a snapshot doubles as the
rollback source for `arr.py restore`.
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .client import ArrError

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"


def file_scores(client, media, profiles):
    """Custom format scores actually achieved by files on disk, per profile id.

    The theoretical maximum from summing positive format scores is a loose upper
    bound. What files really score is the honest test of whether a format cutoff
    is ever satisfiable, so this measures it. Only profiles with a format cutoff
    to prove are queried, which keeps the per-series Sonarr case cheap.
    """
    relevant = {p["id"] for p in profiles if p.get("cutoffFormatScore")}
    if not relevant:
        return {}

    scores = defaultdict(list)
    if client.app == "radarr":
        wanted = [m for m in media if m.get("hasFile") and m.get("qualityProfileId") in relevant]
        profile_of = {m["id"]: m["qualityProfileId"] for m in wanted}
        for record in client.get_repeated("moviefile", "movieId", [m["id"] for m in wanted]):
            if record.get("customFormatScore") is not None:
                scores[profile_of.get(record.get("movieId"))].append(record["customFormatScore"])
    else:
        wanted = [
            s
            for s in media
            if s.get("qualityProfileId") in relevant and (s.get("statistics") or {}).get("episodeFileCount")
        ]
        for series in wanted:
            try:
                records = client.get(f"episodefile?seriesId={series['id']}") or []
            except ArrError:
                continue
            for record in records:
                if record.get("customFormatScore") is not None:
                    scores[series["qualityProfileId"]].append(record["customFormatScore"])

    return {pid: sorted(values) for pid, values in scores.items() if pid is not None}


def pull(client):
    """Collect everything relevant to profile cleanup from one instance."""
    status = client.get("system/status") or {}
    profiles = client.get("qualityprofile") or []
    custom_formats = client.get("customformat") or []
    import_lists = client.get("importlist") or []

    media = client.get(client.media_path) or []
    media_summary = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "qualityProfileId": item.get("qualityProfileId"),
            "monitored": item.get("monitored"),
        }
        for item in media
    ]

    # Cutoff-unmet is the direct measure of pending upgrade work. Sonarr returns
    # episodes, which only carry their series (and its profile id) when asked.
    unmet_params = {"monitored": "true"}
    if client.app == "sonarr":
        unmet_params["includeSeries"] = "true"
    try:
        cutoff_unmet = client.paged("wanted/cutoff", params=unmet_params)
    except ArrError:
        cutoff_unmet = []

    return {
        "instance": client.name,
        "fileScores": {str(k): v for k, v in file_scores(client, media, profiles).items()},
        "app": client.app,
        "url": client.base,
        "version": status.get("version"),
        "pulledAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mediaManagement": client.get("config/mediamanagement") or {},
        "qualityProfiles": profiles,
        "customFormats": custom_formats,
        "importLists": import_lists,
        "media": media_summary,
        "cutoffUnmet": [
            {
                "id": row.get("id"),
                "title": row.get("title") or (row.get("series") or {}).get("title"),
                "qualityProfileId": row.get("qualityProfileId")
                or (row.get("series") or {}).get("qualityProfileId"),
            }
            for row in cutoff_unmet
        ],
    }


def write(snapshots, label=None):
    """Persist a {instance_name: state} mapping to snapshots/<stamp>/."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if label:
        stamp = f"{stamp}-{label}"
    target = SNAPSHOT_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)

    for name, state in snapshots.items():
        (target / f"{name}.json").write_text(json.dumps(state, indent=2, sort_keys=True))
    return target


def load(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"no such snapshot directory: {directory}")
    return {path.stem: json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))}


def latest():
    candidates = sorted((p for p in SNAPSHOT_DIR.glob("*") if p.is_dir()), reverse=True)
    return candidates[0] if candidates else None
