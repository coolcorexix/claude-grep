"""ccfind-convert — CLI entry to drive cross-agent session conversion
through the canonical IR.

Usage:
    python3 -m ccfind_core.cli list <agent>
    python3 -m ccfind_core.cli convert --from <agent> --to <agent> \
        --session <id> [--dry-run]
    python3 -m ccfind_core.cli dump --from <agent> --session <id>
        # prints the canonical JSON to stdout (no target write)
    python3 -m ccfind_core.cli capabilities
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import adapters, CANONICAL_VERSION


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ccfind-convert",
        description="Move agent conversations through a canonical IR.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_caps = sub.add_parser("capabilities", help="show per-agent capabilities")

    p_list = sub.add_parser("list", help="list sessions for one agent")
    p_list.add_argument("agent", choices=adapters.supported_agents())
    p_list.add_argument("--limit", type=int, default=20)

    p_dump = sub.add_parser("dump", help="read a session into canonical JSON")
    p_dump.add_argument("--from", dest="src", required=True,
                        choices=adapters.supported_agents())
    p_dump.add_argument("--session", required=True)
    p_dump.add_argument("--pretty", action="store_true")

    p_conv = sub.add_parser("convert", help="convert source session → target")
    p_conv.add_argument("--from", dest="src", required=True,
                        choices=adapters.supported_agents())
    p_conv.add_argument("--to", dest="dst", required=True,
                        choices=adapters.supported_agents())
    p_conv.add_argument("--session", required=True)
    p_conv.add_argument("--dry-run", action="store_true",
                        help="parse to canonical but skip target write")
    p_conv.add_argument("--target-id",
                        help="provide a specific target session id "
                             "(default: generate fresh UUID)")
    p_conv.add_argument("--target-cwd",
                        help="override cwd written into the target session")

    args = p.parse_args(argv)

    if args.cmd == "capabilities":
        out = {a: adapters.capabilities(a) for a in adapters.supported_agents()}
        print(json.dumps({"canonical_version": CANONICAL_VERSION,
                          "agents": out}, indent=2))
        return 0

    if args.cmd == "list":
        if not adapters.can(args.agent, "list"):
            print(f"agent {args.agent!r} does not support listing",
                  file=sys.stderr)
            return 2
        rows = adapters.list_sessions(args.agent)[:args.limit]
        for r in rows:
            print(json.dumps(r))
        return 0

    if args.cmd == "dump":
        if not adapters.can(args.src, "read"):
            print(f"agent {args.src!r} does not support reading",
                  file=sys.stderr)
            return 2
        conv = adapters.read(args.src, args.session)
        if args.pretty:
            print(conv.to_json(indent=2))
        else:
            print(conv.to_json(indent=None))
        return 0

    if args.cmd == "convert":
        if not adapters.can(args.src, "read"):
            print(f"source {args.src!r} cannot read", file=sys.stderr)
            return 2
        if not args.dry_run and not adapters.can(args.dst, "write"):
            caps = adapters.capabilities(args.dst)
            print(
                f"target {args.dst!r} cannot write. capabilities: {caps}",
                file=sys.stderr,
            )
            return 2

        conv = adapters.read(args.src, args.session)
        # Stamp the original IDs onto metadata so the new transcript can
        # cite its origin (Claude-style forkedFrom convention).
        conv.metadata.setdefault("forkedFrom", {
            "source_agent": args.src,
            "source_session_id": args.session,
        })

        if args.dry_run:
            summary = {
                "source_agent": conv.source_agent,
                "source_session_id": conv.source_session_id,
                "event_count": len(conv.events),
                "message_count": len(conv.message_events()),
                "cwd": conv.cwd,
                "title": conv.title,
            }
            print(json.dumps(summary, indent=2))
            return 0

        result = adapters.write(
            args.dst,
            conv,
            target_session_id=args.target_id,
            cwd=args.target_cwd,
        )
        print(json.dumps(result, indent=2))
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
