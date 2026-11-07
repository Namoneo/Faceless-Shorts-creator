"""Offline foundation commands.

python3 -m tools.foundation.cli run --manifest productions/fixtures/foundation/manifest.json --out productions/fixtures/foundation/out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.foundation.manifest import ManifestError, load_manifest
from tools.foundation.narration import prepare_narration
from tools.foundation.render import RenderFailure, run_episode
from tools.foundation.timing import TimingError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline episode foundation runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "import", "render", "qa"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--manifest", required=True)
        cmd.add_argument("--out", required=True)
    sub.add_parser("generate", help="Always refused. This runner does not call generation providers.")
    args = parser.parse_args(argv)
    if args.command == "generate":
        print("refusing generation request; this runner is offline and does not call providers", file=sys.stderr)
        return 2
    manifest = Path(args.manifest)
    out = Path(args.out)
    try:
        if args.command == "import":
            doc = load_manifest(manifest)
            records = prepare_narration(doc, manifest.parent, out)
            print(json.dumps(records, ensure_ascii=False, indent=2))
            return 0
        report = run_episode(manifest, out)
        if args.command == "qa" and not report["qaPass"]:
            return 1
        print(json.dumps({"episodeId": report["episodeId"], "qaPass": report["qaPass"], "scenes": report["scenes"], "assembled": report["assembled"]}, ensure_ascii=False, indent=2))
        return 0 if report["qaPass"] else 1
    except (ManifestError, TimingError, RenderFailure, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
