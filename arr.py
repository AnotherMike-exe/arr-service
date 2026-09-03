#!/usr/bin/env python3
"""Sonarr/Radarr profile cleanup CLI.

./arr.py check                 verify connectivity to every configured instance
./arr.py pull                  snapshot full state to snapshots/<stamp>/
./arr.py report                pull, then print findings (read-only)
./arr.py report --from DIR     re-run the report against an existing snapshot
./arr.py set-cutoff ...        stop upgrades at a cutoff (dry-run by default)
./arr.py merge ...             fold one profile into another (dry-run by default)
./arr.py reshape ...           replace a profile's allowed qualities and cutoff
./arr.py sort ...              route media by the resolution already on disk
./arr.py restore DIR           push snapshotted profiles back
"""

import argparse
import sys

from arr_service import analyze, apply, config, reshape, snapshot
from arr_service import client as client_mod

SEVERITY_MARK = {"critical": "!!", "warning": " !", "info": "  ", "ok": " +"}


def build_clients(only=None):
    instances, problems = config.discover()
    for problem in problems:
        print(f"  config warning: {problem}", file=sys.stderr)
    if only:
        wanted = {name.lower() for name in only}
        instances = [entry for entry in instances if entry["name"] in wanted]
    if not instances:
        print(
            "No instances configured. Copy .env.example to .env and fill in the URL and "
            "API key for each Sonarr/Radarr instance.",
            file=sys.stderr,
        )
        sys.exit(2)
    return [client_mod.from_config(entry) for entry in instances]


def cmd_check(args):
    failed = False
    for client in build_clients(args.instance):
        try:
            status = client.get("system/status") or {}
            print(f"  ok  {client.name:12} {client.app:7} v{status.get('version', '?'):12} {client.base}")
        except client_mod.ArrError as exc:
            failed = True
            print(f"  FAIL {client.name:12} {exc}")
    return 1 if failed else 0


def collect(args, label=None):
    states = {}
    for client in build_clients(args.instance):
        print(f"  pulling {client.name} ({client.base}) ...", file=sys.stderr)
        states[client.name] = snapshot.pull(client)
    path = snapshot.write(states, label=label)
    print(f"  snapshot written to {path}", file=sys.stderr)
    return states, path


def cmd_pull(args):
    collect(args)
    return 0


def fmt_score(row):
    cutoff, reach = row["cutoffFormatScore"], row["maxReachableScore"]
    text = f"{cutoff} (theoretical max {reach}"
    if row["observedFiles"]:
        text += f", best of {row['observedFiles']} file(s) on disk {row['observedMaxScore']}"
    text += ")"
    never = (
        row["upgradeAllowed"]
        and cutoff > 0
        and (cutoff > reach or (row["observedMaxScore"] is not None and row["observedMaxScore"] < cutoff))
    )
    return text + ("  <-- never satisfied" if never else "")


def print_report(result):
    header = f"{result['app'].upper()}  {result['instance']}  v{result['version']}"
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))
    print(
        f"{result['mediaTotal']} item(s) across {len(result['profiles'])} profile(s); "
        f"{result['cutoffUnmetTotal']} awaiting upgrade (cutoff unmet)\n"
    )

    for severity, message in result.get("instanceFindings", []):
        print(f"  {SEVERITY_MARK[severity]} {message}")
    if result.get("instanceFindings"):
        print()

    print("-- Profiles " + "-" * 58)
    for row in result["profiles"]:
        used = f"{row['mediaCount']} item(s)"
        if row["importLists"]:
            used += f", import lists: {', '.join(row['importLists'])}"
        if row["mediaCount"] == 0 and not row["importLists"]:
            used = "UNUSED"
        print(f"\n  [{row['id']}] {row['name']}  —  {used}")
        print(f"      qualities        : {', '.join(row['qualities']) or '(none allowed)'}")
        print(f"      cutoff           : {row['cutoff']}")
        print(f"      upgrades allowed : {row['upgradeAllowed']}")
        print(f"      cutoff fmt score : {fmt_score(row)}")
        for severity, message in row["findings"]:
            print(f"      {SEVERITY_MARK[severity]} {message}")

    if result["exactDuplicates"]:
        print("\n-- Exact duplicates (identical behaviour, safe to merge) " + "-" * 14)
        for group in result["exactDuplicates"]:
            keep = max(group, key=lambda r: r["mediaCount"])
            print()
            for row in group:
                print(f"      [{row['id']}] {row['name']:30} {row['mediaCount']} item(s)")
            print(f"      suggested: keep [{keep['id']}] {keep['name']!r}, merge the rest into it")

    if result["nearDuplicates"]:
        print("\n-- Near duplicates (same qualities and cutoff, differing formats) " + "-" * 5)
        for group in result["nearDuplicates"]:
            print()
            for row in group:
                print(
                    f"      [{row['id']}] {row['name']:30} {row['mediaCount']:>4} item(s)  "
                    f"minFmt={row['minFormatScore']} cutoffFmt={row['cutoffFormatScore']}"
                )

    if result["unused"]:
        print("\n-- Unused profiles (nothing references these) " + "-" * 25)
        for row in result["unused"]:
            print(f"      [{row['id']}] {row['name']}")


def cmd_report(args):
    if args.from_dir:
        states = snapshot.load(args.from_dir)
    else:
        states, _ = collect(args)
    for state in states.values():
        print_report(analyze.analyze(state))
    print("\nRead-only: nothing was changed.")
    return 0


def run_actions(actions, execute):
    for action in actions:
        print(f"  {'APPLIED ' if execute else 'dry-run '} {action.description}")
    if not execute:
        print("\n  Nothing was changed. Re-run with --execute to apply.")
    return 0


def cmd_set_cutoff(args):
    clients = build_clients([args.instance])
    if args.execute:
        collect(argparse.Namespace(instance=[args.instance]), label="pre-set-cutoff")
    actions = []
    for client in clients:
        actions += apply.stop_upgrades_at_cutoff(
            client,
            args.profile,
            cutoff_quality=args.quality,
            cutoff_format_score=args.format_score,
            min_format_score=args.min_score,
            disable_upgrades=args.disable_upgrades,
            execute=args.execute,
        )
    return run_actions(actions, args.execute)


def cmd_merge(args):
    clients = build_clients([args.instance])
    if args.execute:
        collect(argparse.Namespace(instance=[args.instance]), label="pre-merge")
    actions = []
    for client in clients:
        actions += apply.merge_profiles(
            client,
            args.source,
            args.target,
            delete_source=not args.keep_source,
            execute=args.execute,
        )
    return run_actions(actions, args.execute)


def cmd_reshape(args):
    clients = build_clients([args.instance])
    if args.execute:
        collect(argparse.Namespace(instance=[args.instance]), label="pre-reshape")
    upgrades = None
    if args.upgrades:
        upgrades = args.upgrades == "on"
    actions = []
    for client in clients:
        actions += reshape.set_shape(
            client,
            args.profile,
            allow=[q.strip() for q in args.allow.split(",")] if args.allow else None,
            cutoff=args.cutoff,
            name=args.name,
            upgrade_allowed=upgrades,
            execute=args.execute,
        )
    return run_actions(actions, args.execute)


def cmd_sort(args):
    clients = build_clients([args.instance])
    if args.execute:
        collect(argparse.Namespace(instance=[args.instance]), label="pre-sort")
    sources = {}
    for spec in args.source:
        pid, _, tier = spec.partition(":")
        if tier not in ("uhd", "hd"):
            print(f"error: --source expects <profileId>:uhd|hd, got {spec!r}", file=sys.stderr)
            return 1
        sources[int(pid)] = tier
    actions = []
    for client in clients:
        acts, reasons = reshape.sort_by_resolution(client, args.uhd, args.hd, sources, execute=args.execute)
        actions += acts
        print("  classified by:")
        for reason, count in reasons.most_common():
            print(f"      {count:>4}  {reason}")
    return run_actions(actions, args.execute)


def cmd_config(args):
    clients = build_clients([args.instance_arg] if args.instance_arg else None)
    if args.execute:
        collect(argparse.Namespace(instance=[c.name for c in clients]), label="pre-config")
    actions = []
    for client in clients:
        actions += apply.set_media_management(
            client,
            propers=args.propers,
            recycle_bin=args.recycle_bin,
            recycle_cleanup_days=args.recycle_cleanup_days,
            execute=args.execute,
        )
    return run_actions(actions, args.execute)


def cmd_restore(args):
    states = snapshot.load(args.directory)
    actions = []
    for client in build_clients(args.instance):
        state = states.get(client.name)
        if not state:
            print(f"  skipping {client.name}: not present in snapshot")
            continue
        actions += apply.restore_profiles(client, state, execute=args.execute)
    if not actions:
        print("  live profiles already match the snapshot — nothing to restore")
        return 0
    return run_actions(actions, args.execute)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--instance", action="append", help="limit to this instance (repeatable)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="verify connectivity").set_defaults(func=cmd_check)
    subparsers.add_parser("pull", help="snapshot state").set_defaults(func=cmd_pull)

    report = subparsers.add_parser("report", help="analyze profiles (read-only)")
    report.add_argument("--from", dest="from_dir", help="use an existing snapshot directory")
    report.set_defaults(func=cmd_report)

    cutoff = subparsers.add_parser("set-cutoff", help="stop upgrades at a cutoff")
    cutoff.add_argument("instance")
    cutoff.add_argument("profile", type=int, help="quality profile id")
    cutoff.add_argument("--quality", help="name of the quality to stop upgrading at")
    cutoff.add_argument("--format-score", type=int, help="clamp cutoffFormatScore to this value")
    cutoff.add_argument("--min-score", type=int, help="clamp minFormatScore to this value")
    cutoff.add_argument("--disable-upgrades", action="store_true", help="never upgrade at all")
    cutoff.add_argument("--execute", action="store_true")
    cutoff.set_defaults(func=cmd_set_cutoff)

    merge = subparsers.add_parser("merge", help="fold one profile into another")
    merge.add_argument("instance")
    merge.add_argument("source", type=int, help="profile id to retire")
    merge.add_argument("target", type=int, help="profile id to keep")
    merge.add_argument("--keep-source", action="store_true", help="reassign but do not delete")
    merge.add_argument("--execute", action="store_true")
    merge.set_defaults(func=cmd_merge)

    shape = subparsers.add_parser("reshape", help="replace a profile's allowed qualities")
    shape.add_argument("instance")
    shape.add_argument("profile", type=int)
    shape.add_argument("--name", help="rename the profile")
    shape.add_argument("--allow", help="comma-separated quality names to allow")
    shape.add_argument("--cutoff", help="quality to stop upgrading at")
    shape.add_argument("--upgrades", choices=["on", "off"], help="set upgradeAllowed")
    shape.add_argument("--execute", action="store_true")
    shape.set_defaults(func=cmd_reshape)

    sort = subparsers.add_parser("sort", help="route media by the resolution on disk")
    sort.add_argument("instance")
    sort.add_argument("--uhd", type=int, required=True, help="profile id for 2160p content")
    sort.add_argument("--hd", type=int, required=True, help="profile id for everything below")
    sort.add_argument(
        "--source",
        action="append",
        required=True,
        help="<profileId>:uhd|hd — a profile to sort, and the tier its file-less items follow (repeatable)",
    )
    sort.add_argument("--execute", action="store_true")
    sort.set_defaults(func=cmd_sort)

    cfg = subparsers.add_parser("config", help="instance-wide media management settings")
    cfg.add_argument("instance_arg", nargs="?", metavar="instance")
    cfg.add_argument("--propers", choices=["preferAndUpgrade", "doNotPrefer", "doNotUpgrade"])
    cfg.add_argument("--recycle-bin", help="path for replaced files")
    cfg.add_argument("--recycle-cleanup-days", type=int)
    cfg.add_argument("--execute", action="store_true")
    cfg.set_defaults(func=cmd_config)

    restore = subparsers.add_parser("restore", help="push snapshotted profiles back")
    restore.add_argument("directory")
    restore.add_argument("--execute", action="store_true")
    restore.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    if not hasattr(args, "instance"):
        args.instance = None
    try:
        sys.exit(args.func(args))
    except (client_mod.ArrError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
