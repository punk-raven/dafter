from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys

from dafter_core.errors import DafterError

from .control import ControlPlane
from .run import transcribe_session

SESSION = re.compile(r"^s_[0-9a-f]{8}$")


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-batch",
        description=(
            "Transcribe a finished session from each participant's recorded track and store "
            "the result as a new transcript version. Spends batch provider credits."
        ),
    )
    p.add_argument("session", help="the session id, s_ and eight hex digits")
    args = p.parse_args(argv)
    if not SESSION.fullmatch(args.session):
        p.error("not a session id")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = arguments(argv)
    try:
        stored = asyncio.run(transcribe_session(args.session, ControlPlane.from_env()))
    except DafterError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 1
    print(json.dumps(stored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
