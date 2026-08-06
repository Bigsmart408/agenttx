#!/usr/bin/env python3
"""AgentTX CLI — trajectory-level effect transactions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime import AgentTXRuntime


def cmd_begin(args: argparse.Namespace) -> int:
    tx = AgentTXRuntime.begin(
        workdir=Path(args.workdir).resolve() if args.workdir else Path.cwd(),
        hide_network=args.no_network,
        trace_reads=not args.no_trace_reads,
    )
    print(json.dumps(tx.status(), indent=2))
    print(tx.pool.session_dir)
    return 0


def _load(args: argparse.Namespace) -> AgentTXRuntime:
    if not args.session:
        raise SystemExit("--session DIR is required")
    return AgentTXRuntime.load(Path(args.session))


def cmd_run(args: argparse.Namespace) -> int:
    tx = _load(args)
    if not args.argv:
        raise SystemExit("missing command after --")
    result = tx.run_tool(args.tool or args.argv[0], args.argv)
    print(
        json.dumps(
            {
                "step_id": result.step_id,
                "tool_name": result.tool_name,
                "exit_code": result.exit_code,
                "effect_count": result.effect_count,
                "parents": result.parents,
            },
            indent=2,
        )
    )
    if args.verbose:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
    return result.exit_code


def cmd_rollback(args: argparse.Namespace) -> int:
    tx = _load(args)
    targets = tx.rollback_from(args.step)
    print(json.dumps({"aborted": targets}, indent=2))
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    tx = _load(args)
    frontier = tx.commit(args.up_to)
    print(json.dumps({"committed_frontier": frontier}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    tx = _load(args)
    st = tx.status()
    st["ledger"] = tx.ledger.to_dict()
    print(json.dumps(st, indent=2))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    tx = _load(args)
    tx.close(destroy=args.destroy)
    print(json.dumps({"closed": True, "destroyed": args.destroy}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agenttx", description="Agent effect transactions")
    sp = p.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("begin", help="start a trajectory session")
    b.add_argument("--workdir", default=None)
    b.add_argument("--no-network", action="store_true")
    b.add_argument(
        "--no-trace-reads",
        action="store_true",
        help="disable automatic workspace read/negative-lookup tracing",
    )
    b.set_defaults(func=cmd_begin)

    r = sp.add_parser("run", help="run one tool call inside the session")
    r.add_argument("--session", required=True)
    r.add_argument("--tool", default=None)
    r.add_argument("-v", "--verbose", action="store_true")
    r.add_argument("argv", nargs=argparse.REMAINDER)
    r.set_defaults(func=cmd_run)

    rb = sp.add_parser("rollback", help="cascade rollback from a step")
    rb.add_argument("--session", required=True)
    rb.add_argument("--step", type=int, default=None)
    rb.set_defaults(func=cmd_rollback)

    c = sp.add_parser("commit", help="commit up to frontier")
    c.add_argument("--session", required=True)
    c.add_argument("--up-to", dest="up_to", type=int, default=None)
    c.set_defaults(func=cmd_commit)

    s = sp.add_parser("status", help="show session + ledger")
    s.add_argument("--session", required=True)
    s.set_defaults(func=cmd_status)

    cl = sp.add_parser("close", help="persist and optionally destroy session dirs")
    cl.add_argument("--session", required=True)
    cl.add_argument("--destroy", action="store_true")
    cl.set_defaults(func=cmd_close)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # allow: agenttx run --session X -- cmd args
    if "run" in argv and "--" in argv:
        idx = argv.index("--")
        # keep -- out of remainder confusion: argparse REMAINDER keeps leading --
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "run" and args.argv and args.argv[0] == "--":
        args.argv = args.argv[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
