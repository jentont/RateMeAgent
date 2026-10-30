"""Нарисовать пример карточки без Telegram: python preview.py → data/preview."""

from __future__ import annotations

import sys
from pathlib import Path

import card
import config
import photo
import usernames
from scoring import Profile, evaluate


def main() -> None:
    out = config.DATA_DIR / "preview"
    out.mkdir(parents=True, exist_ok=True)
    raw = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else None
    rich = Profile(
        user_id=184_000_000, username="storm", name="Ваня Solutions", photos=6,
        bio="делаю ботов и аватарки, канал в профиле", gifts=48, nft=5, stars=21_000,
        level=27, nft_name=True, name_usd=2400, rare_name="Onyx Black (фон)", rarity=0.78,
        avatar=raw, avatar_quality=photo.quality(raw),
    )
    empty = Profile(user_id=8_300_000_000, username=None, name="новый акк 🙂", guessed=("gifts", "value"))
    for name, profile in (("rich", rich), ("empty", empty)):
        (out / f"{name}.png").write_bytes(card.render(evaluate(profile), "ocenka_profile_bot"))
        print(name, evaluate(profile).total)
    for nick in ("storm", "kwjpz_7"):
        rating = usernames.rate_offline(nick)
        (out / f"user_{nick}.png").write_bytes(card.render_username(rating, "свободен", "ocenka_profile_bot"))
        print(nick, rating.total)


if __name__ == "__main__":
    main()
