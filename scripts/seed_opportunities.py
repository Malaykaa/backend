"""Seed script — insère ~50 opportunités de test dans la base de données.

Usage : python -m scripts.seed_opportunities
"""

from datetime import date

from app.core.database import SessionLocal
from app.models.opportunity import Opportunity, OpportunityType

OPPORTUNITIES = [
    # ── Jobs ─────────────────────────────────────────────
    {"type": "job", "title": "Développeur Python Backend", "domain": "Informatique", "country": "Sénégal", "description": "Poste de développeur Python/FastAPI dans une startup fintech à Dakar.", "deadline": date(2026, 6, 30)},
    {"type": "job", "title": "Data Analyst Junior", "domain": "Data Science", "country": "Côte d'Ivoire", "description": "Analyse de données pour une entreprise de télécommunications.", "deadline": date(2026, 5, 15)},
    {"type": "job", "title": "Chef de projet digital", "domain": "Marketing", "country": "Sénégal", "description": "Gestion de projets de transformation digitale.", "deadline": date(2026, 7, 1)},
    {"type": "job", "title": "Ingénieur DevOps", "domain": "Informatique", "country": "France", "description": "Mise en place d'infrastructures cloud AWS/GCP.", "deadline": date(2026, 6, 15)},
    {"type": "job", "title": "Comptable Senior", "domain": "Finance", "country": "Cameroun", "description": "Gestion comptable et financière d'une PME.", "deadline": date(2026, 5, 30)},
    {"type": "job", "title": "Responsable RH", "domain": "Ressources Humaines", "country": "Sénégal", "description": "Gestion des talents et recrutement.", "deadline": date(2026, 6, 20)},
    {"type": "job", "title": "Développeur Frontend React", "domain": "Informatique", "country": "International", "description": "Développement d'interfaces web modernes en React/TypeScript.", "deadline": date(2026, 7, 15)},
    {"type": "job", "title": "Consultant SAP", "domain": "Informatique", "country": "Maroc", "description": "Implémentation de modules SAP pour des clients grands comptes.", "deadline": date(2026, 6, 1)},

    # ── Scholarships ─────────────────────────────────────
    {"type": "scholarship", "title": "Bourse Mastercard Foundation", "domain": "Sciences", "country": "International", "description": "Bourse complète pour étudiants africains en master ou doctorat.", "deadline": date(2026, 9, 1)},
    {"type": "scholarship", "title": "Bourse DAAD Allemagne", "domain": "Ingénierie", "country": "Allemagne", "description": "Programme de bourses pour études de master en Allemagne.", "deadline": date(2026, 8, 15)},
    {"type": "scholarship", "title": "Bourse Chevening UK", "domain": "Sciences Politiques", "country": "Royaume-Uni", "description": "Bourse du gouvernement britannique pour futurs leaders.", "deadline": date(2026, 11, 1)},
    {"type": "scholarship", "title": "Bourse Campus France", "domain": "Sciences", "country": "France", "description": "Programme de bourses pour étudiants africains francophones.", "deadline": date(2026, 7, 30)},
    {"type": "scholarship", "title": "Bourse de l'Union Africaine", "domain": "Droit", "country": "International", "description": "Bourse pour études juridiques dans une université africaine.", "deadline": date(2026, 8, 1)},
    {"type": "scholarship", "title": "Bourse Erasmus Mundus", "domain": "Informatique", "country": "Europe", "description": "Programme de master conjoint en informatique dans 3 universités européennes.", "deadline": date(2026, 10, 15)},
    {"type": "scholarship", "title": "Bourse ARES Belgique", "domain": "Agronomie", "country": "Belgique", "description": "Bourses pour étudiants des pays en développement.", "deadline": date(2026, 9, 30)},
    {"type": "scholarship", "title": "Bourse Fulbright USA", "domain": "Sciences", "country": "États-Unis", "description": "Programme d'échange académique international.", "deadline": date(2026, 10, 1)},

    # ── Internships ──────────────────────────────────────
    {"type": "internship", "title": "Stage Data Science — Orange", "domain": "Data Science", "country": "Sénégal", "description": "Stage de 6 mois en analyse de données chez Orange Sonatel.", "deadline": date(2026, 5, 30)},
    {"type": "internship", "title": "Stage Marketing Digital — Jumia", "domain": "Marketing", "country": "Côte d'Ivoire", "description": "Stage en stratégie digitale et SEO.", "deadline": date(2026, 6, 15)},
    {"type": "internship", "title": "Stage Développement Web — Wave", "domain": "Informatique", "country": "Sénégal", "description": "Stage fullstack dans une fintech mobile.", "deadline": date(2026, 6, 1)},
    {"type": "internship", "title": "Stage Audit — Deloitte Afrique", "domain": "Finance", "country": "Sénégal", "description": "Stage en audit et conseil financier.", "deadline": date(2026, 5, 15)},
    {"type": "internship", "title": "Stage Recherche IA — Google", "domain": "Informatique", "country": "International", "description": "Programme de recherche en intelligence artificielle.", "deadline": date(2026, 7, 1)},
    {"type": "internship", "title": "Stage Communication — UNESCO", "domain": "Communication", "country": "France", "description": "Stage au siège de l'UNESCO à Paris.", "deadline": date(2026, 6, 30)},

    # ── Grants ───────────────────────────────────────────
    {"type": "grant", "title": "Subvention Tony Elumelu Foundation", "domain": "Entrepreneuriat", "country": "International", "description": "5000 USD pour entrepreneurs africains avec mentorat.", "deadline": date(2026, 6, 1)},
    {"type": "grant", "title": "Grant Google for Startups Africa", "domain": "Informatique", "country": "International", "description": "Programme d'accélération et financement pour startups tech.", "deadline": date(2026, 8, 15)},
    {"type": "grant", "title": "Fonds Afrique Créative", "domain": "Arts", "country": "International", "description": "Financement pour entreprises culturelles et créatives africaines.", "deadline": date(2026, 7, 15)},
    {"type": "grant", "title": "Subvention BAD Youth", "domain": "Agriculture", "country": "International", "description": "Financement de projets agricoles pour jeunes africains.", "deadline": date(2026, 9, 1)},
    {"type": "grant", "title": "Innovation Fund USAID", "domain": "Santé", "country": "International", "description": "Financement de projets innovants en santé publique.", "deadline": date(2026, 8, 1)},
    {"type": "grant", "title": "Subvention AFD Numérique", "domain": "Informatique", "country": "Sénégal", "description": "Appui aux startups numériques en Afrique francophone.", "deadline": date(2026, 7, 30)},

    # ── Funding ──────────────────────────────────────────
    {"type": "funding", "title": "Programme Seed Partech Africa", "domain": "Informatique", "country": "International", "description": "Investissement seed pour startups tech africaines (50K-500K USD).", "deadline": None},
    {"type": "funding", "title": "Investissement Orange Ventures", "domain": "Informatique", "country": "Sénégal", "description": "Fonds d'investissement pour startups innovantes.", "deadline": None},
    {"type": "funding", "title": "Crédit PME BCEAO", "domain": "Finance", "country": "Sénégal", "description": "Ligne de crédit pour PME de la zone UEMOA.", "deadline": date(2026, 12, 31)},
    {"type": "funding", "title": "Fonds VC TLcom Capital", "domain": "Informatique", "country": "International", "description": "Venture capital pour startups tech en Afrique.", "deadline": None},
    {"type": "funding", "title": "Financement ADEPME", "domain": "Entrepreneuriat", "country": "Sénégal", "description": "Accompagnement et financement pour PME sénégalaises.", "deadline": date(2026, 12, 31)},
    {"type": "funding", "title": "Programme Invest in Africa", "domain": "Commerce", "country": "International", "description": "Financement et réseau pour PME africaines.", "deadline": None},

    # ── Tenders ──────────────────────────────────────────
    {"type": "tender", "title": "Appel d'offres — Digitalisation SENELEC", "domain": "Informatique", "country": "Sénégal", "description": "Développement d'un portail client digital pour la société d'électricité.", "deadline": date(2026, 5, 30)},
    {"type": "tender", "title": "Marché public — Système RH État du Sénégal", "domain": "Informatique", "country": "Sénégal", "description": "Mise en place d'un SIRH pour la fonction publique.", "deadline": date(2026, 6, 15)},
    {"type": "tender", "title": "Appel d'offres — Site web Banque Mondiale", "domain": "Informatique", "country": "International", "description": "Refonte du portail web régional Afrique de l'Ouest.", "deadline": date(2026, 7, 1)},
    {"type": "tender", "title": "Consultation — Étude d'impact ONG PLAN", "domain": "Sciences Sociales", "country": "Sénégal", "description": "Évaluation d'un programme éducatif dans 5 régions.", "deadline": date(2026, 6, 30)},
    {"type": "tender", "title": "Marché — Formation digitale UNICEF", "domain": "Éducation", "country": "International", "description": "Conception d'une plateforme e-learning pour l'Afrique de l'Ouest.", "deadline": date(2026, 8, 15)},
    {"type": "tender", "title": "Appel d'offres — Application mobile Santé", "domain": "Informatique", "country": "Cameroun", "description": "Développement d'une app de suivi médical communautaire.", "deadline": date(2026, 7, 15)},
    {"type": "tender", "title": "Consultation — Audit énergétique", "domain": "Énergie", "country": "Côte d'Ivoire", "description": "Audit énergétique de 10 bâtiments publics.", "deadline": date(2026, 6, 1)},
    {"type": "tender", "title": "Marché — Infrastructure réseau", "domain": "Télécommunications", "country": "Sénégal", "description": "Déploiement fibre optique dans 3 nouvelles zones.", "deadline": date(2026, 9, 1)},
]


def seed():
    """Insère les opportunités de test si la table est vide."""
    db = SessionLocal()
    try:
        count = db.query(Opportunity).count()
        if count > 0:
            print(f"Table opportunities contient déjà {count} entrées. Seed ignoré.")
            return

        for data in OPPORTUNITIES:
            opp = Opportunity(
                type=OpportunityType(data["type"]),
                title=data["title"],
                description=data.get("description"),
                domain=data.get("domain"),
                country=data.get("country"),
                deadline=data.get("deadline"),
            )
            db.add(opp)

        db.commit()
        print(f"{len(OPPORTUNITIES)} opportunités insérées avec succès.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
