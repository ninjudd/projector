from __future__ import annotations

import argparse

from . import runtime, watch
from .core import ProjectorError


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="projector", description="Run and supervise your agents in a terminal")
    command.add_argument("command", nargs="?", choices=("setup",), help="install the optional agent environment")
    return command


def main(argv=None) -> int:
    command = parser()
    args = command.parse_args(argv)
    if args.command == "setup":
        try:
            path = runtime.setup(watch.state_root())
            print(f"Agent environment ready: {path}")
            return 0
        except ProjectorError as error:
            command.exit(error.exit_code, str(error) + "\n")
    from .cli import main as project_main
    return project_main(["console"])


if __name__ == "__main__":
    raise SystemExit(main())
