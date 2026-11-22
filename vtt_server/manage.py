"""Command-line admin helpers.

    python -m vtt_server.manage promote USERNAME     # grant administrator rights
    python -m vtt_server.manage demote USERNAME
    python -m vtt_server.manage list
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from vtt_server.database import AsyncSessionLocal, init_db
from vtt_server.models import User


async def _run(args: argparse.Namespace) -> int:
    await init_db()
    async with AsyncSessionLocal() as db:
        if args.command == "list":
            for user in (await db.execute(select(User).order_by(User.id))).scalars():
                flags = ("admin " if user.is_superuser else "") + ("" if user.is_active else "inactive ")
                print(f"{user.id}\t{user.username}\t{user.email}\t{flags.strip()}")
            return 0
        user = (await db.execute(select(User).where(User.username == args.username))).scalar_one_or_none()
        if user is None:
            print(f"No such user: {args.username}", file=sys.stderr)
            return 1
        user.is_superuser = args.command == "promote"
        await db.commit()
        print(f"{user.username}: administrator = {user.is_superuser}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vtt_server.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("promote", "demote"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
