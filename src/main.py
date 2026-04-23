"""Оркестратор: collect → digest → send."""
import argparse
import asyncio

from . import config
from .collector import collect_all
from .digest import generate_digest
from .sender import send_digest


async def run_pipeline(hours_back: int = 24, model: str | None = None,
                       skip_send: bool = False):
    model = model or config.DIGEST_MODEL
    print("=" * 60)
    print(f"AI Digest Pipeline | model={model} | hours={hours_back}")
    print("=" * 60)

    # 1. Collect
    stats = await collect_all(hours_back=hours_back)
    if stats["total"] == 0:
        print("Постов нет — выхожу")
        return

    # 2. Generate
    result = generate_digest(model=model, hours_back=hours_back)
    if not result["content"]:
        print("Дайджест пустой")
        return

    # 3. Send
    if skip_send:
        print("\nОтправка пропущена (--skip-send)")
    else:
        await send_digest(
            result["digest_id"],
            result["content"],
            config.DIGEST_RECIPIENT_ID,
        )

    print("\nПайплайн завершён.")


def main():
    p = argparse.ArgumentParser(description="AI Digest pipeline")
    p.add_argument("--hours", type=int, default=24, help="Окно сбора постов")
    p.add_argument("--model", choices=["opus", "sonnet", "haiku"],
                   help="Модель Claude (по умолчанию из .env)")
    p.add_argument("--skip-send", action="store_true", help="Не отправлять")
    p.add_argument("--only-digest", action="store_true",
                   help="Не собирать новые посты, только сгенерировать дайджест")
    p.add_argument("--only-send", type=int, metavar="DIGEST_ID",
                   help="Отправить существующий дайджест по ID")
    args = p.parse_args()

    if args.only_send:
        from .db import connect
        conn = connect()
        row = conn.execute(
            "SELECT content, recipient_id FROM digests WHERE id = ?",
            (args.only_send,)
        ).fetchone()
        conn.close()
        if not row:
            print(f"Дайджест ID={args.only_send} не найден")
            return
        asyncio.run(send_digest(args.only_send, row["content"],
                                row["recipient_id"] or config.DIGEST_RECIPIENT_ID))
        return

    if args.only_digest:
        result = generate_digest(model=args.model or config.DIGEST_MODEL,
                                 hours_back=args.hours)
        if not args.skip_send and result["content"]:
            asyncio.run(send_digest(result["digest_id"], result["content"],
                                    config.DIGEST_RECIPIENT_ID))
        return

    asyncio.run(run_pipeline(
        hours_back=args.hours,
        model=args.model,
        skip_send=args.skip_send,
    ))


if __name__ == "__main__":
    main()
