"""Seed script — charge le référentiel curaté métiers dans career_references.

Idempotent : upsert par (title, country). Sur une entrée déjà présente, met à
jour le contenu mais NE TOUCHE JAMAIS reviewed_by/reviewed_at — une relecture
humaine déjà faite ne doit jamais être silencieusement effacée par un rafraîchissement
de contenu.

Usage : python -m scripts.seed_career_references
"""

import json
from pathlib import Path

from app.core.database import SessionLocal
from app.models.career_reference import CareerReference

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "career_references_ci.json"


def main() -> None:
    entries = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    inserted = 0
    updated = 0

    with SessionLocal() as db:
        for entry in entries:
            existing = (
                db.query(CareerReference)
                .filter(
                    CareerReference.title == entry["title"],
                    CareerReference.country == entry.get("country"),
                )
                .one_or_none()
            )
            if existing:
                existing.category = entry.get("category")
                existing.description = entry.get("description")
                existing.key_skills = entry.get("key_skills")
                existing.example_formations = entry.get("example_formations")
                existing.source_note = entry.get("source_note")
                updated += 1
            else:
                db.add(CareerReference(
                    title=entry["title"],
                    category=entry.get("category"),
                    description=entry.get("description"),
                    key_skills=entry.get("key_skills"),
                    example_formations=entry.get("example_formations"),
                    country=entry.get("country"),
                    source_note=entry.get("source_note"),
                ))
                inserted += 1

        db.commit()

    print(f"{inserted} insérée(s), {updated} mise(s) à jour.")


if __name__ == "__main__":
    main()
