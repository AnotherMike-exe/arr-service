"""Rebuild a profile's allowed qualities, and re-sort media by what is on disk.

Two operations that `set_cutoff` cannot express:

`set_shape` replaces a profile's whole quality list. It rebuilds from the app's
own schema so every known quality is present, and orders by resolution first.
Both apps ship orderings that are not monotonic in resolution — Sonarr ranks
HDTV-1080p *below* Bluray-720p — which makes "cutoff at the bottom of the 1080p
tier" behave wrongly. Normalising by resolution fixes that.

`sort_by_resolution` moves media between profiles based on the resolution of the
file already on disk, so nothing lands below its new cutoff and no download is
triggered by the move itself.
"""

from collections import Counter

from .apply import Action

# Never allowed: unrankable junk, disc images, and untranscoded raw captures.
JUNK_QUALITIES = {
    "unknown",
    "workprint",
    "cam",
    "telesync",
    "telecine",
    "dvdscr",
    "regional",
    "br-disk",
    "raw-hd",
}


def schema_qualities(client):
    """Every quality the app knows, ordered by resolution then the app's own rank."""
    schema = client.get("qualityprofile/schema") or {}
    flat = []
    for item in schema.get("items", []):
        if item.get("quality"):
            flat.append(item["quality"])
        else:
            for sub in item.get("items", []):
                if sub.get("quality"):
                    flat.append(sub["quality"])
    ranked = sorted(enumerate(flat), key=lambda pair: (pair[1].get("resolution") or 0, pair[0]))
    return [quality for _, quality in ranked]


def build_items(client, allow_names):
    """Flat items array with `allowed` set for the named qualities.

    Groups are flattened away: with the cutoff pinned to the bottom of a tier,
    grouping buys nothing and explicit per-quality ranking is easier to reason about.
    """
    qualities = schema_qualities(client)
    wanted = {name.strip().lower() for name in allow_names}
    known = {q["name"].lower() for q in qualities}

    unknown = wanted - known
    if unknown:
        raise ValueError(
            f"{client.name}: unknown quality name(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(q['name'] for q in qualities)}"
        )
    junk = wanted & JUNK_QUALITIES
    if junk:
        raise ValueError(f"{client.name}: refusing to allow junk quality: {', '.join(sorted(junk))}")

    return [
        {"quality": quality, "items": [], "allowed": quality["name"].lower() in wanted}
        for quality in qualities
    ]


def set_shape(client, profile_id, allow=None, cutoff=None, name=None, upgrade_allowed=None, execute=False):
    """Replace a profile's allowed qualities, cutoff, name and upgrade flag."""
    profile = client.get(f"qualityprofile/{profile_id}")
    if not profile:
        raise ValueError(f"{client.name}: no quality profile with id {profile_id}")

    updated = dict(profile)
    changes = []

    if name and name != profile.get("name"):
        updated["name"] = name
        changes.append(f"name={name!r}")

    if upgrade_allowed is not None and upgrade_allowed != profile.get("upgradeAllowed"):
        updated["upgradeAllowed"] = upgrade_allowed
        changes.append(f"upgradeAllowed={upgrade_allowed}")

    if allow:
        items = build_items(client, allow)
        allowed_now = [i["quality"]["name"] for i in items if i["allowed"]]
        before = [
            i["quality"]["name"] for i in profile.get("items", []) if i.get("quality") and i.get("allowed")
        ]
        updated["items"] = items
        if allowed_now != before:
            changes.append(f"allowed={len(allowed_now)} qualities ({', '.join(allowed_now)})")

    if cutoff:
        candidates = [
            i["quality"]
            for i in updated.get("items", [])
            if i.get("allowed") and i["quality"]["name"].lower() == cutoff.strip().lower()
        ]
        if not candidates:
            allowed_now = [i["quality"]["name"] for i in updated.get("items", []) if i.get("allowed")]
            raise ValueError(
                f"{client.name}: cutoff {cutoff!r} is not among the allowed qualities "
                f"({', '.join(allowed_now)})"
            )
        if updated.get("cutoff") != candidates[0]["id"]:
            updated["cutoff"] = candidates[0]["id"]
            changes.append(f"cutoff={cutoff}")

    if not changes:
        return [Action(f"{client.name}: profile {profile_id} {profile['name']!r} already matches")]

    action = Action(
        f"{client.name}: reshape [{profile_id}] {profile['name']!r} → " + "; ".join(changes),
        "PUT",
        f"qualityprofile/{profile_id}",
        updated,
    )
    if execute:
        action.run(client)
    return [action]


def _resolution_of(item):
    """Resolution of the file on disk, or None when there is no file."""
    handle = item.get("movieFile") or {}
    quality = (handle.get("quality") or {}).get("quality") or {}
    return quality.get("resolution")


def series_resolution(client, series_id):
    """Highest resolution present across a series' episode files, or None if empty.

    Highest, not predominant: a mixed series routed by its majority would land on
    a profile that disallows its best episodes, and the app replaces a file whose
    quality the profile does not list. The higher tier allows both, so routing up
    strands nothing.
    """
    try:
        files = client.get(f"episodefile?seriesId={series_id}") or []
    except Exception:
        return None
    resolutions = [((f.get("quality") or {}).get("quality") or {}).get("resolution") for f in files]
    resolutions = [r for r in resolutions if r is not None]
    return max(resolutions) if resolutions else None


def sort_by_resolution(client, uhd_profile, hd_profile, sources, execute=False):
    """Route media into the UHD or HD profile by the resolution already on disk.

    `sources` maps a source profile id to the tier its file-less items should
    follow ("uhd" or "hd"), since those have nothing on disk to classify.
    """
    media = client.get(client.media_path) or []
    moves = {uhd_profile: [], hd_profile: []}
    reasons = Counter()

    for item in media:
        current = item.get("qualityProfileId")
        if current not in sources:
            continue

        if client.app == "radarr":
            resolution = _resolution_of(item) if item.get("hasFile") else None
        else:
            resolution = (
                series_resolution(client, item["id"])
                if (item.get("statistics") or {}).get("episodeFileCount")
                else None
            )

        if resolution is None:
            target = uhd_profile if sources[current] == "uhd" else hd_profile
            reasons["no file — followed source profile's tier"] += 1
        elif resolution >= 2160:
            target = uhd_profile
            reasons["2160p file"] += 1
        else:
            target = hd_profile
            reasons[f"{resolution}p file"] += 1

        if target != current:
            moves[target].append(item["id"])

    actions = []
    for target, ids in moves.items():
        if not ids:
            continue
        actions.append(
            Action(
                f"{client.name}: move {len(ids)} item(s) to profile {target}",
                "PUT",
                f"{client.media_path}/editor",
                {client.media_id_field: ids, "qualityProfileId": target, "moveFiles": False},
            )
        )

    if execute:
        for action in actions:
            action.run(client)
    return actions, reasons
