"""Write operations. Every function returns a list of planned actions and only
performs them when execute=True, so a plan can always be reviewed first.
"""

from . import analyze


class Action:
    def __init__(self, description, method=None, path=None, body=None):
        self.description = description
        self.method = method
        self.path = path
        self.body = body

    def run(self, client):
        if self.method:
            client.request(self.method, self.path, body=self.body)

    def __repr__(self):
        return self.description


def _profile(client, profile_id):
    profile = client.get(f"qualityprofile/{profile_id}")
    if not profile:
        raise ValueError(f"{client.name}: no quality profile with id {profile_id}")
    return profile


def stop_upgrades_at_cutoff(client, profile_id, cutoff_quality=None, cutoff_format_score=None,
                            min_format_score=None, disable_upgrades=False, execute=False):
    """Make a profile stop grabbing once its cutoff is reached.

    cutoff_quality        move the quality cutoff to this named quality
    cutoff_format_score   clamp the custom-format cutoff to a reachable value
    min_format_score      clamp the minimum accepted score; an unreachable value
                          here rejects every release, so the profile grabs nothing
    disable_upgrades      turn off upgrades entirely (grab once, never replace)
    """
    profile = _profile(client, profile_id)
    changes = {}

    if disable_upgrades and profile.get("upgradeAllowed"):
        changes["upgradeAllowed"] = False
    if min_format_score is not None and profile.get("minFormatScore") != min_format_score:
        changes["minFormatScore"] = min_format_score
    resolved_note = ""
    if cutoff_quality:
        target_id, target_name, was_group = analyze.cutoff_target(profile, cutoff_quality)
        if target_id is None:
            choices = "; ".join(analyze.cutoff_choices(profile))
            raise ValueError(
                f"{client.name}: {cutoff_quality!r} cannot be the cutoff for "
                f"{profile['name']!r}. Valid cutoffs: {choices}"
            )
        if was_group and target_name.lower() != cutoff_quality.strip().lower():
            resolved_note = f" (resolved to the {target_name!r} group that contains it)"
        if profile.get("cutoff") != target_id:
            changes["cutoff"] = target_id
    if cutoff_format_score is not None and profile.get("cutoffFormatScore") != cutoff_format_score:
        changes["cutoffFormatScore"] = cutoff_format_score

    if not changes:
        return [Action(f"{client.name}: {profile['name']!r} already matches — nothing to do")]

    updated = dict(profile)
    updated.update(changes)
    summary = ", ".join(f"{key}={value}" for key, value in changes.items())
    action = Action(
        f"{client.name}: update profile {profile['id']} {profile['name']!r} → {summary}"
        f"{resolved_note}",
        "PUT",
        f"qualityprofile/{profile['id']}",
        updated,
    )
    if execute:
        action.run(client)
    return [action]


def merge_profiles(client, source_id, target_id, delete_source=True, execute=False):
    """Move everything off source_id onto target_id, then remove the source."""
    source = _profile(client, source_id)
    target = _profile(client, target_id)
    actions = []

    media = client.get(client.media_path) or []
    moving = [item["id"] for item in media if item.get("qualityProfileId") == source_id]
    if moving:
        body = {client.media_id_field: moving, "qualityProfileId": target_id, "moveFiles": False}
        actions.append(
            Action(
                f"{client.name}: reassign {len(moving)} item(s) from {source['name']!r} "
                f"to {target['name']!r}",
                "PUT",
                f"{client.media_path}/editor",
                body,
            )
        )

    # Anything else holding a qualityProfileId blocks the delete with a bare
    # "profile is in use". Radarr collections are the easy one to miss: a library
    # can have hundreds, and they keep a profile even while unmonitored.
    referrers = [("importlist", "name")]
    if client.app == "radarr":
        referrers.append(("collection", "title"))

    for endpoint, label_key in referrers:
        try:
            entries = client.get(endpoint) or []
        except Exception:
            continue
        for entry in entries:
            if entry.get("qualityProfileId") != source_id:
                continue
            updated = dict(entry)
            updated["qualityProfileId"] = target_id
            actions.append(
                Action(
                    f"{client.name}: point {endpoint} "
                    f"{entry.get(label_key) or entry['id']!r} at {target['name']!r}",
                    "PUT",
                    f"{endpoint}/{entry['id']}",
                    updated,
                )
            )

    if delete_source:
        actions.append(
            Action(
                f"{client.name}: delete now-empty profile {source_id} {source['name']!r}",
                "DELETE",
                f"qualityprofile/{source_id}",
                None,
            )
        )

    if execute:
        for action in actions:
            action.run(client)
    return actions


def restore_profiles(client, state, execute=False):
    """Return the instance to a snapshot: profile settings, deleted profiles, and
    the media assignments that pointed at them.

    A deleted profile cannot be recreated with its original id, so recreating one
    yields a new id and every media item the snapshot had on the old id is moved
    to it. That makes a merge undoable, not just a settings change.
    """
    actions = []
    live = {p["id"]: p for p in (client.get("qualityprofile") or [])}
    id_map = {}

    for profile in state.get("qualityProfiles", []):
        old_id = profile["id"]
        if old_id in live:
            id_map[old_id] = old_id
            if live[old_id] != profile:
                action = Action(
                    f"{client.name}: restore settings on profile {old_id} {profile['name']!r}",
                    "PUT",
                    f"qualityprofile/{old_id}",
                    profile,
                )
                actions.append(action)
                if execute:
                    action.run(client)
            continue

        body = {key: value for key, value in profile.items() if key != "id"}
        actions.append(
            Action(
                f"{client.name}: recreate deleted profile {profile['name']!r} (gets a new id)",
                "POST",
                "qualityprofile",
                body,
            )
        )
        if execute:
            created = client.post("qualityprofile", body) or {}
            id_map[old_id] = created.get("id")
        else:
            id_map[old_id] = None

    # Move media back to whatever profile the snapshot had it on.
    live_media = {item["id"]: item.get("qualityProfileId") for item in (client.get(client.media_path) or [])}
    regroup = {}
    for item in state.get("media", []):
        old_id = item.get("qualityProfileId")
        target = id_map.get(old_id, old_id)
        if item["id"] not in live_media:
            continue
        if target is not None and live_media[item["id"]] == target:
            continue
        regroup.setdefault((old_id, target), []).append(item["id"])

    for (old_id, target), ids in sorted(regroup.items(), key=lambda kv: kv[0][0] or 0):
        label = target if target is not None else f"the recreated stand-in for {old_id}"
        action = Action(
            f"{client.name}: move {len(ids)} item(s) back to profile {label}",
            "PUT" if target is not None else None,
            f"{client.media_path}/editor",
            {client.media_id_field: ids, "qualityProfileId": target, "moveFiles": False},
        )
        actions.append(action)
        if execute and target is not None:
            action.run(client)

    return actions


def set_media_management(client, propers=None, recycle_bin=None,
                         recycle_cleanup_days=None, execute=False):
    """Adjust instance-wide media management settings.

    `propers` matters more than it looks: "preferAndUpgrade" replaces a file when a
    PROPER or REPACK appears even though the quality cutoff is already met, so it
    bypasses every cutoff the profiles set. TRaSH-style setups score repacks with a
    custom format instead and leave this on "doNotPrefer".
    """
    current = client.get("config/mediamanagement") or {}
    if not current:
        raise ValueError(f"{client.name}: could not read config/mediamanagement")

    wanted = {
        "downloadPropersAndRepacks": propers,
        "recycleBin": recycle_bin,
        "recycleBinCleanupDays": recycle_cleanup_days,
    }
    changes = {
        key: value
        for key, value in wanted.items()
        if value is not None and current.get(key) != value
    }
    if not changes:
        return [Action(f"{client.name}: media management already matches — nothing to do")]

    updated = dict(current)
    updated.update(changes)
    summary = ", ".join(f"{key}={value!r}" for key, value in changes.items())
    action = Action(
        f"{client.name}: media management → {summary}",
        "PUT",
        f"config/mediamanagement/{current['id']}",
        updated,
    )
    if execute:
        action.run(client)
    return [action]
