import asyncio

from app.db.session import SessionLocal, engine
from app.services.idempotency import prune_expired


async def _run() -> None:
    try:
        async with SessionLocal() as session:
            deleted = await prune_expired(session)
            await session.commit()
        print(f"Pruned {deleted} expired idempotency key(s).")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
