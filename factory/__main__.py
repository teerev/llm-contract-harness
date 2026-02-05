from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # Route `run` directly so `python -m factory run --help` shows the run help.
    if argv[:1] == ["run"]:
        from factory.run import run_cli

        return run_cli(argv[1:])

    p = argparse.ArgumentParser(prog="python -m factory")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", help="Run the factory harness")
    try:
        p.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return int(code)

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

