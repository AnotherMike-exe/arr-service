"""Turn pulled state into findings: upgrade churn causes and duplicate profiles."""

import json
from collections import Counter, defaultdict

# Radarr ships this as the default cutoff format score. It is far above what any
# real release scores, so the cutoff is never satisfied and upgrades never stop.
UNREACHABLE_DEFAULT = 10000

# A lone positive format worth less than this is noise (a repack tiebreaker, say),
# not a ladder worth chasing across a re-download.
GRADIENT_FLOOR = 100


def quality_names(profile):
    """Allowed qualities in profile order, worst to best."""
    names = []
    for item in profile.get("items", []):
        if item.get("quality"):
            if item.get("allowed"):
                names.append(item["quality"]["name"])
        elif item.get("allowed"):
            for sub in item.get("items", []):
                if sub.get("allowed"):
                    names.append(sub["quality"]["name"])
    return names


def quality_index(profile):
    """Map every selectable id (individual qualities and groups) to its name."""
    index = {}
    for item in profile.get("items", []):
        if item.get("quality"):
            index[item["quality"]["id"]] = item["quality"]["name"]
        else:
            if item.get("id") is not None:
                index[item["id"]] = item.get("name") or f"group {item['id']}"
            for sub in item.get("items", []):
                if sub.get("quality"):
                    index[sub["quality"]["id"]] = sub["quality"]["name"]
    return index


def cutoff_name(profile):
    return quality_index(profile).get(profile.get("cutoff"), f"unknown ({profile.get('cutoff')})")


def cutoff_target(profile, name):
    """Resolve a quality or group name to the id usable as this profile's cutoff.

    A quality nested inside a group cannot be a cutoff on its own — the app wants
    the group id — so naming a member resolves to the group that contains it.
    Returns (id, resolved_name, was_group) or (None, None, False) if not allowed here.
    """
    wanted = name.strip().lower()
    for item in profile.get("items", []):
        if not item.get("allowed"):
            continue
        if item.get("quality"):
            if item["quality"]["name"].lower() == wanted:
                return item["quality"]["id"], item["quality"]["name"], False
            continue
        group_name = item.get("name") or f"group {item.get('id')}"
        if group_name.lower() == wanted:
            return item["id"], group_name, True
        for sub in item.get("items", []):
            if (sub.get("quality") or {}).get("name", "").lower() == wanted:
                return item["id"], group_name, True
    return None, None, False


def cutoff_choices(profile):
    """Names accepted as a cutoff: standalone qualities and group names."""
    names = []
    for item in profile.get("items", []):
        if not item.get("allowed"):
            continue
        if item.get("quality"):
            names.append(item["quality"]["name"])
        else:
            members = ", ".join(s["quality"]["name"] for s in item.get("items", []) if s.get("quality"))
            names.append(f"{item.get('name')} [{members}]")
    return names


def format_scores(profile):
    """Non-zero custom format scores, keyed by format name."""
    return {
        item.get("name"): item.get("score") for item in profile.get("formatItems", []) if item.get("score")
    }


def max_reachable_score(profile):
    """Upper bound on the score any single release could earn in this profile.

    Sums every positive score, which assumes all positive formats match at once.
    Real releases score well below this, so exceeding it is a hard guarantee that
    the format cutoff can never be met.
    """
    return sum(score for score in format_scores(profile).values() if score > 0)


def signature(profile):
    """Identity of a profile's behaviour, ignoring its name and id."""
    return json.dumps(
        {
            "qualities": quality_names(profile),
            "cutoff": cutoff_name(profile),
            "upgradeAllowed": profile.get("upgradeAllowed"),
            "minFormatScore": profile.get("minFormatScore", 0),
            "cutoffFormatScore": profile.get("cutoffFormatScore", 0),
            "minUpgradeFormatScore": profile.get("minUpgradeFormatScore", 1),
            "formats": format_scores(profile),
            "language": (profile.get("language") or {}).get("name"),
        },
        sort_keys=True,
    )


def shape_signature(profile):
    """Weaker identity: same allowed qualities and cutoff, formats ignored."""
    return json.dumps({"qualities": quality_names(profile), "cutoff": cutoff_name(profile)}, sort_keys=True)


def usage(state):
    """Count what references each profile id: media items and import lists."""
    media_counts = Counter(
        item["qualityProfileId"] for item in state.get("media", []) if item.get("qualityProfileId")
    )
    monitored_counts = Counter(
        item["qualityProfileId"]
        for item in state.get("media", [])
        if item.get("qualityProfileId") and item.get("monitored")
    )
    list_names = defaultdict(list)
    for entry in state.get("importLists", []):
        if entry.get("qualityProfileId"):
            list_names[entry["qualityProfileId"]].append(entry.get("name") or f"list {entry['id']}")

    unmet_counts = Counter(
        row["qualityProfileId"] for row in state.get("cutoffUnmet", []) if row.get("qualityProfileId")
    )
    return {
        "media": media_counts,
        "monitored": monitored_counts,
        "importLists": list_names,
        "cutoffUnmet": unmet_counts,
    }


def upgrade_findings(profile, refs, observed=None):
    """Reasons this profile keeps pulling upgrades."""
    findings = []
    allowed = quality_names(profile)
    cutoff = cutoff_name(profile)
    cutoff_score = profile.get("cutoffFormatScore", 0)
    reachable = max_reachable_score(profile)
    observed = observed or []

    min_score = profile.get("minFormatScore", 0)
    if min_score > reachable:
        findings.append(
            (
                "critical",
                f"Profile can never grab anything: minFormatScore is {min_score} but the best "
                f"score any release could reach is {reachable}. Every release is rejected.",
            )
        )

    if not profile.get("upgradeAllowed"):
        findings.append(("ok", "Upgrades are already disabled — this profile grabs once and stops."))
        return findings

    # An unsatisfiable format cutoff only costs bandwidth if there is a ladder of
    # higher-scoring releases to climb. With no gradient the setting is wrong but
    # inert, because no release ever scores better than what is already on disk.
    rungs = sorted({score for score in format_scores(profile).values() if score > 0})
    gradient = len(rungs) > 1 or (rungs and rungs[-1] >= GRADIENT_FLOOR)

    unsatisfiable = cutoff_score > reachable
    never_in_practice = not unsatisfiable and observed and max(observed) < cutoff_score

    if unsatisfiable or never_in_practice:
        if unsatisfiable:
            detail = (
                f"cutoffFormatScore is {cutoff_score} but the highest score any release "
                f"could possibly reach is {reachable}"
            )
            if cutoff_score == UNREACHABLE_DEFAULT and reachable == 0:
                detail += " (this is the untouched default)"
        else:
            detail = (
                f"cutoffFormatScore is {cutoff_score} but the best of {len(observed)} "
                f"file(s) on disk scores {max(observed)}"
            )

        if gradient:
            top = ", ".join(
                f"{name} {score}"
                for name, score in sorted(format_scores(profile).items(), key=lambda kv: -kv[1])[:3]
                if score > 0
            )
            findings.append(
                (
                    "critical",
                    f"Format cutoff is never satisfied and there is a score ladder to climb: "
                    f"{detail}. Every better-scoring release is grabbed as an upgrade, "
                    f"indefinitely (top rungs: {top}).",
                )
            )
        else:
            findings.append(
                (
                    "warning",
                    f"Format cutoff is never satisfied — {detail} — but no meaningful "
                    f"higher-scoring release exists to upgrade to, so it costs little in "
                    f"practice. Worth correcting, not urgent.",
                )
            )

    if allowed and cutoff == allowed[-1]:
        findings.append(
            (
                "warning",
                f"Cutoff is set to the top allowed quality ({cutoff}), so it keeps hunting "
                f"until it finds the single best release available.",
            )
        )

    unmet = refs["cutoffUnmet"].get(profile["id"], 0)
    if unmet:
        findings.append(("info", f"{unmet} item(s) on this profile are currently flagged as cutoff-unmet."))
    return findings


def instance_findings(state):
    """Settings that affect every profile at once."""
    findings = []
    mm = state.get("mediaManagement") or {}
    if mm.get("downloadPropersAndRepacks") == "preferAndUpgrade":
        findings.append(
            (
                "critical",
                "Propers/repacks is set to 'Prefer and Upgrade', which replaces files that "
                "have already met their cutoff. It bypasses every cutoff set below. Score "
                "repacks with a custom format and set this to 'Do Not Prefer' instead.",
            )
        )
    if mm and not (mm.get("recycleBin") or "").strip():
        findings.append(
            (
                "warning",
                "No Recycle Bin path is set, so a file replaced by an upgrade is deleted "
                "outright with no way back.",
            )
        )
    return findings


def analyze(state):
    """Produce the full finding set for one instance."""
    profiles = state.get("qualityProfiles", [])
    refs = usage(state)

    scores_by_profile = state.get("fileScores") or {}

    rows = []
    for profile in profiles:
        pid = profile["id"]
        observed = scores_by_profile.get(str(pid)) or []
        rows.append(
            {
                "id": pid,
                "name": profile.get("name"),
                "observedFiles": len(observed),
                "observedMaxScore": max(observed) if observed else None,
                "observedMedianScore": observed[len(observed) // 2] if observed else None,
                "qualities": quality_names(profile),
                "cutoff": cutoff_name(profile),
                "upgradeAllowed": profile.get("upgradeAllowed"),
                "minFormatScore": profile.get("minFormatScore", 0),
                "cutoffFormatScore": profile.get("cutoffFormatScore", 0),
                "maxReachableScore": max_reachable_score(profile),
                "formats": format_scores(profile),
                "mediaCount": refs["media"].get(pid, 0),
                "monitoredCount": refs["monitored"].get(pid, 0),
                "importLists": refs["importLists"].get(pid, []),
                "cutoffUnmet": refs["cutoffUnmet"].get(pid, 0),
                "signature": signature(profile),
                "shape": shape_signature(profile),
                "findings": upgrade_findings(profile, refs, observed),
            }
        )

    by_signature = defaultdict(list)
    by_shape = defaultdict(list)
    for row in rows:
        by_signature[row["signature"]].append(row)
        by_shape[row["shape"]].append(row)

    exact = [group for group in by_signature.values() if len(group) > 1]
    exact_ids = {row["id"] for group in exact for row in group}
    near = [
        group
        for group in by_shape.values()
        if len(group) > 1 and not {row["id"] for row in group} <= exact_ids
    ]

    unused = [row for row in rows if row["mediaCount"] == 0 and not row["importLists"]]

    return {
        "instance": state.get("instance"),
        "app": state.get("app"),
        "version": state.get("version"),
        "mediaTotal": len(state.get("media", [])),
        "cutoffUnmetTotal": len(state.get("cutoffUnmet", [])),
        "instanceFindings": instance_findings(state),
        "profiles": rows,
        "exactDuplicates": exact,
        "nearDuplicates": near,
        "unused": unused,
    }
