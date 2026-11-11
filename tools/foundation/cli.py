"""Offline foundation commands.

python3 -m tools.foundation.cli run --manifest productions/fixtures/foundation/manifest.json --out productions/fixtures/foundation/out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.foundation.flow import FlowError
from tools.foundation.longform import plan_long, run_longform
from tools.foundation.pilot import PilotError, make_pilot
from tools.foundation.manifest import ManifestError, load_manifest
from tools.foundation.narration import prepare_narration
from tools.foundation.render import RenderFailure, run_episode
from tools.foundation.repurpose import run_repurpose
from tools.foundation.timing import TimingError
from tools.foundation.voicestudio import VoiceStudioBlocked, narration_door


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline episode foundation runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "import", "render", "qa"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--manifest", required=True)
        cmd.add_argument("--out", required=True)
    sub.add_parser("generate", help="Always refused. This runner does not call generation providers.")
    sub.add_parser("flow-generate", help="Always refused. Flow stays a manual export.")
    door = sub.add_parser("narration-door")
    door.add_argument("--door", required=True)
    door.add_argument("--language", default="uz-Latn")
    planned = sub.add_parser("plan-long")
    planned.add_argument("--topic", required=True)
    planned.add_argument("--language", required=True)
    planned.add_argument("--duration", required=True, type=float)
    planned.add_argument("--audience", required=True)
    planned.add_argument("--out", required=True)
    planned.add_argument("--pilot", action="store_true", help="Allow a 5 to 7 minute plan. Still renders nothing.")
    longer = sub.add_parser("run-long")
    longer.add_argument("--manifest", required=True)
    longer.add_argument("--out", required=True)
    longer.add_argument("--packet", required=True)
    shorts = sub.add_parser("repurpose")
    shorts.add_argument("--manifest", required=True)
    shorts.add_argument("--selection", required=True)
    shorts.add_argument("--out", required=True)
    shorts.add_argument("--packet", required=True)
    pilot = sub.add_parser("make-pilot", help="Assemble episode 001 from a narration folder and a stills folder.")
    pilot.add_argument("--episode", required=True)
    pilot.add_argument("--narration", required=True)
    pilot.add_argument("--stills", required=True)
    pilot.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command in {"generate", "flow-generate"}:
        print("refusing generation request; this runner is offline and does not call providers", file=sys.stderr)
        return 2
    try:
        if args.command == "narration-door":
            print(narration_door(args.door, args.language))
            return 0
        if args.command == "make-pilot":
            report = make_pilot(Path(args.episode), Path(args.narration), Path(args.stills), Path(args.out))
            print(json.dumps({"placeholder": report["placeholder"], "final": report["final"], "qaPass": report["qaPass"], "episode": report["episode"], "shorts": report["shorts"], "cost": report["cost"]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "plan-long":
            doc = plan_long(args.topic, args.language, args.duration, args.audience, Path(args.out), pilot=args.pilot)
            print(json.dumps({"stopped": doc["stopped"], "manualSteps": doc["manualSteps"]}, indent=2))
            return 0
        if args.command == "run-long":
            report = run_longform(Path(args.manifest), Path(args.out), Path(args.packet))
            print(json.dumps({"episodeId": report["episodeId"], "qaPass": report["qaPass"]}, indent=2))
            return 0 if report["qaPass"] else 1
        if args.command == "repurpose":
            report = run_repurpose(Path(args.manifest), Path(args.selection), Path(args.out), Path(args.packet))
            print(json.dumps({"qaPass": report["qaPass"], "shorts": [item["id"] for item in report["shorts"]]}, indent=2))
            return 0 if report["qaPass"] else 1
        manifest = Path(args.manifest)
        out = Path(args.out)
        if args.command == "import":
            doc = load_manifest(manifest)
            records = prepare_narration(doc, manifest.parent, out)
            print(json.dumps(records, ensure_ascii=False, indent=2))
            return 0
        report = run_episode(manifest, out)
    except (ManifestError, TimingError, RenderFailure, FlowError, VoiceStudioBlocked, PilotError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.command == "qa" and not report["qaPass"]:
        return 1
    print(json.dumps({"episodeId": report["episodeId"], "qaPass": report["qaPass"], "scenes": report["scenes"], "assembled": report["assembled"]}, ensure_ascii=False, indent=2))
    return 0 if report["qaPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
