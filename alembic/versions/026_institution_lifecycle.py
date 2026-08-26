"""Cycle de vie Malayka Institution — archivage et retrait de membres.

Rien ne pouvait etre supprime ni retire : ni une salle, ni un cours, ni un
exercice envoye par erreur, ni un eleve parti, ni un enseignant qui a quitte
l'etablissement. La premiere erreur d'un enseignant devenait un incident
irreparable.

Archivage plutot que suppression : une suppression reelle detruirait les
progressions, les resultats et l'historique des rapports d'impact — et rendrait
une erreur d'archivage elle-meme definitive. Un horodatage NULL/non-NULL est
reversible, et dit QUAND plutot que seulement SI.

Toutes les colonnes sont nullables : la migration est donc sans effet sur les
donnees existantes (tout reste actif), et reversible sans perte.

Revision ID: 026
Revises: 025
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


# (table, colonne, commentaire)
_COLUMNS = [
    ("classrooms", "archived_at", "Salle archivee — masquee des listes, donnees conservees."),
    ("classroom_courses", "archived_at", "Cours archive — retire des listes et des livraisons eleve."),
    ("classroom_exercises", "archived_at", "Exercice archive — les soumissions deja faites restent intactes."),
    ("classroom_memberships", "removed_at", "Eleve retire de la salle — ses livraisons deja recues lui restent."),
    ("classroom_teachers", "removed_at", "Affectation de l'enseignant a cette salle retiree."),
]


def upgrade() -> None:
    for table, column, comment in _COLUMNS:
        op.add_column(
            table,
            sa.Column(column, sa.DateTime(timezone=True), nullable=True, comment=comment),
        )
    # Les listes filtrent systematiquement sur ces colonnes : un index partiel
    # sur les lignes encore actives garde ces requetes efficaces sans indexer
    # l'historique archive, qui n'est jamais parcouru.
    for table, column, _ in _COLUMNS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{column}_active "
            f"ON {table} ({column}) WHERE {column} IS NULL"
        )


def downgrade() -> None:
    for table, column, _ in _COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{column}_active")
    for table, column, _ in _COLUMNS:
        op.drop_column(table, column)
