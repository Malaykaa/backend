"""Service Tendances — agrégations SQL sur scraped_offers pour alimenter le frontend."""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from collections import Counter

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType

# Mapping GoalType → types d'offres correspondants dans scraped_offers
_GOAL_TO_OFFER_TYPES: dict[str, list[str]] = {
    "career":      ["job"],
    "scholarship": ["scholarship"],
    "study_grant": ["scholarship"],
    "funding":     ["grant", "partnership"],
    "tender":      ["call_for_applications"],
    "freelance":   ["opportunity"],
    "exam":        ["scholarship", "grant"],
}

# ── Mots vides — exclus de l'extraction de compétences ───────────────────────
# Couvre le français, l'anglais et le boilerplate typique des offres d'emploi.
_STOP_WORDS: frozenset[str] = frozenset({
    # Français — mots grammaticaux
    "le","la","les","de","du","des","un","une","et","en","a","au","aux",
    "par","pour","sur","avec","dans","que","qui","est","sont","etre","avoir",
    "faire","plus","tres","bien","tout","cette","notre","votre","leur","leurs",
    "vous","nous","ils","elles","son","ses","mon","mes","ton","tes","ou","ni",
    "mais","donc","or","car","si","ne","pas","moins","aussi","comme","meme",
    "entre","sans","sous","vers","chez","dont","ou","selon","lors","des",
    "afin","ainsi","apres","avant","depuis","pendant","autres","autre",
    "tous","toute","toutes","chaque","plusieurs","certains","certaines",
    "ces","cet","peu","assez","trop","alors","enfin","puis","cela","ceci",
    # Anglais — mots grammaticaux
    "the","a","an","of","in","to","for","and","or","at","by","from","with",
    "on","is","are","was","were","be","been","being","have","has","had","do",
    "does","did","will","would","could","should","may","might","must","shall",
    "can","not","no","so","if","this","that","these","those","it","its","we",
    "you","they","he","she","our","your","their","which","who","what","when",
    "where","how","all","any","both","each","few","more","other","some",
    "such","than","too","very","just","about","into","up","down","out","off",
    "only","own","same","then","through","while","as","its","his","her",
    "him","them","whose","whom","new","must","able","well","also","including",
    # Boilerplate offres d'emploi (FR)
    "profil","poste","offre","emploi","stage","cdi","cdd","hf","fh","candidature",
    "recherche","recrute","recrutement","experience","exp","minimum","requis",
    "requises","souhaite","souhaites","connaissance","connaissances","maitrise",
    "travail","entreprise","societe","client","clients","equipe","projet","projets",
    "bac","licence","master","doctorat","diplome","junior","senior","confirme",
    "debutant","temps","plein","partiel","presentiel","teletravail","salaire",
    "remuneration","contrat","duree","service","services","support","aide",
    "assistance","competences","qualites","aptitudes","responsabilites","missions",
    "taches","activites","formations","certificat","certification","langues",
    "langue","international","secteur","domaine","applications","logiciels",
    "systemes","systeme","outils","outil","cadre","lieu","ville","pays","region",
    "zone","bureau","agence","niveau","ans","annee","annees","mois","semaine",
    "avantages","conges","mutuelle","vehicule","permis","disponibilite",
    # Boilerplate offres d'emploi (EN)
    "experience","years","year","skills","skill","required","preferred",
    "knowledge","team","work","working","strong","ability","excellent","good",
    "proficient","familiar","solid","position","role","job","opportunity",
    "company","business","responsibilities","requirements","qualifications",
    "candidate","candidates","apply","application","degree","bachelor",
    "location","remote","office","salary","benefits","contract","environment",
    "culture","plus","great","ideal","key","core","main","primary",
})

# ── Carte de normalisation — variantes → label canonique ─────────────────────
# Règle : le plus spécifique (bigramme) est testé avant le plus court (unigrame).
_CANONICAL: dict[str, str] = {
    # === Langages de programmation ===
    "python": "Python", "java": "Java", "javascript": "JavaScript",
    "js": "JavaScript", "typescript": "TypeScript", "ts": "TypeScript",
    "php": "PHP", "ruby": "Ruby", "swift": "Swift", "kotlin": "Kotlin",
    "dart": "Dart", "scala": "Scala", "rust": "Rust", "golang": "Go",
    "go": "Go", "c++": "C/C++", "c#": "C#", "matlab": "Matlab",
    "bash": "Bash/Linux", "shell": "Bash/Linux",
    # === Frontend ===
    "react js": "React", "reactjs": "React", "react": "React",
    "react native": "React Native", "angular": "Angular",
    "vue js": "Vue.js", "vuejs": "Vue.js", "vue": "Vue.js",
    "html/css": "HTML/CSS", "html css": "HTML/CSS",
    "html": "HTML/CSS", "css": "HTML/CSS",
    "tailwind": "Tailwind CSS", "bootstrap": "Bootstrap", "flutter": "Flutter",
    # === Backend ===
    "node js": "Node.js", "nodejs": "Node.js", "node": "Node.js",
    "spring boot": "Spring Boot", "spring": "Spring Boot",
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "laravel": "Laravel", "symfony": "Symfony",
    "dotnet": ".NET", ".net": ".NET",
    "express js": "Express.js", "express": "Express.js",
    # === Bases de données ===
    "sql": "SQL", "mysql": "MySQL", "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL", "mongodb": "MongoDB", "nosql": "NoSQL",
    "redis": "Redis", "oracle": "Oracle", "firebase": "Firebase",
    "elasticsearch": "Elasticsearch",
    # === Cloud & DevOps ===
    "aws": "AWS", "azure": "Azure",
    "google cloud": "Google Cloud", "gcp": "Google Cloud",
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "ci/cd": "CI/CD", "devops": "DevOps",
    "terraform": "Terraform", "ansible": "Ansible",
    "jenkins": "CI/CD", "github actions": "CI/CD",
    "linux": "Linux", "unix": "Linux", "git": "Git",
    # === Data & IA ===
    "machine learning": "Machine Learning", "deep learning": "Deep Learning",
    "intelligence artificielle": "IA / Intelligence Artificielle",
    "artificial intelligence": "IA / Intelligence Artificielle",
    "data science": "Data Science",
    "data analysis": "Analyse de données", "data analyst": "Analyse de données",
    "analyse de donnees": "Analyse de données",
    "big data": "Big Data", "power bi": "Power BI",
    "tableau": "Tableau", "nlp": "NLP / Traitement du langage",
    "computer vision": "Computer Vision",
    "tensorflow": "Machine Learning", "pytorch": "Machine Learning",
    "pandas": "Python", "numpy": "Python",
    # === Business / Gestion ===
    "excel": "Excel / Reporting", "vba": "Excel / Reporting",
    "pack office": "Pack Office", "microsoft office": "Pack Office",
    "powerpoint": "Pack Office",
    "gestion de projet": "Gestion de projet",
    "project management": "Gestion de projet",
    "chef de projet": "Gestion de projet",
    "marketing digital": "Marketing Digital",
    "digital marketing": "Marketing Digital",
    "seo": "SEO / SEM", "sem": "SEO / SEM",
    "community management": "Community Management",
    "reseaux sociaux": "Community Management",
    "social media": "Community Management",
    "crm": "CRM", "salesforce": "CRM",
    "erp": "ERP / SAP", "sap": "ERP / SAP", "odoo": "ERP / SAP",
    "comptabilite": "Comptabilité", "accounting": "Comptabilité",
    "finance": "Finance",
    "audit": "Audit / Contrôle de gestion",
    "controle de gestion": "Audit / Contrôle de gestion",
    "fiscalite": "Fiscalité",
    "ressources humaines": "Ressources Humaines",
    "rh": "Ressources Humaines", "human resources": "Ressources Humaines",
    "hr": "Ressources Humaines", "paie": "Ressources Humaines",
    "communication": "Communication",
    "redaction": "Rédaction / Contenu",
    "copywriting": "Rédaction / Contenu",
    "leadership": "Leadership",
    "agile": "Agile / Scrum", "scrum": "Agile / Scrum",
    "kanban": "Agile / Scrum", "jira": "Agile / Scrum",
    "vente": "Vente / Commercial", "commercial": "Vente / Commercial",
    "sales": "Vente / Commercial",
    "negociation": "Négociation",
    "supply chain": "Logistique / Supply Chain",
    "logistique": "Logistique / Supply Chain",
    "achats": "Achats / Procurement", "procurement": "Achats / Procurement",
    # === Design ===
    "ux/ui": "UX/UI Design", "ux": "UX/UI Design", "ui": "UX/UI Design",
    "figma": "UX/UI Design",
    "photoshop": "Adobe Creative Suite", "illustrator": "Adobe Creative Suite",
    "adobe": "Adobe Creative Suite",
    "design graphique": "Design graphique",
    # === Cybersécurité ===
    "cybersecurite": "Cybersécurité", "cybersecurity": "Cybersécurité",
    "securite informatique": "Cybersécurité",
    "pentest": "Cybersécurité", "penetration testing": "Cybersécurité",
    # === Réseaux / Télécoms ===
    "reseaux": "Réseaux / Télécoms", "reseau": "Réseaux / Télécoms",
    "cisco": "Réseaux / Télécoms", "telecoms": "Réseaux / Télécoms",
    "telecommunications": "Réseaux / Télécoms",
}

_ORIENTATION_LABELS: dict[str, str] = {
    "job":                   "Emploi salarié",
    "scholarship":           "Bourse d'études",
    "grant":                 "Financement / Grant",
    "call_for_applications": "Appel à candidature",
    "opportunity":           "Mission freelance",
    "formation":             "Formation",
    "partnership":           "Partenariat",
    "resource":              "Ressource",
}

# ── Géolocalisation africaine — 54 pays reconnus ─────────────────────────────
#
# Structure : liste de (clé_normalisée, label_affiché).
# - La clé est sans accents, minuscules → compare avec _normalize(location).
# - Triée par longueur décroissante : les entrées les plus longues sont testées
#   en premier pour éviter les faux positifs ("niger" matchant "nigeria",
#   "guinea" matchant "guinea-bissau", etc.).
# - Plusieurs clés par pays (FR + EN + variantes) → même label affiché.

_AFRICAN_GEO: list[tuple[str, str]] = sorted([
    # Afrique du Nord
    ("algerie",              "Algérie"),
    ("algeria",              "Algérie"),
    ("maroc",                "Maroc"),
    ("morocco",              "Maroc"),
    ("tunisie",              "Tunisie"),
    ("tunisia",              "Tunisie"),
    ("libye",                "Libye"),
    ("libya",                "Libye"),
    ("egypte",               "Égypte"),
    ("egypt",                "Égypte"),
    ("mauritanie",           "Mauritanie"),
    ("mauritania",           "Mauritanie"),
    ("soudan",               "Soudan"),
    ("sudan",                "Soudan"),

    # Afrique de l'Ouest
    ("nigeria",              "Nigeria"),
    ("cote d'ivoire",        "Côte d'Ivoire"),
    ("cote d ivoire",        "Côte d'Ivoire"),   # variante sans apostrophe
    ("ivory coast",          "Côte d'Ivoire"),
    ("ghana",                "Ghana"),
    ("senegal",              "Sénégal"),
    ("mali",                 "Mali"),
    ("burkina faso",         "Burkina Faso"),
    ("guinea-bissau",        "Guinée-Bissau"),
    ("guinee-bissau",        "Guinée-Bissau"),
    ("guinee equatoriale",   "Guinée Équatoriale"),
    ("equatorial guinea",    "Guinée Équatoriale"),
    ("guinee",               "Guinée"),
    ("guinea",               "Guinée"),
    ("benin",                "Bénin"),
    ("togo",                 "Togo"),
    ("niger",                "Niger"),
    ("sierra leone",         "Sierra Leone"),
    ("liberia",              "Libéria"),
    ("gambie",               "Gambie"),
    ("gambia",               "Gambie"),
    ("cap-vert",             "Cap-Vert"),
    ("cape verde",           "Cap-Vert"),
    ("sao tome",             "São Tomé-et-Príncipe"),

    # Afrique Centrale
    ("cameroun",             "Cameroun"),
    ("cameroon",             "Cameroun"),
    ("republique democratique du congo", "RDC"),
    ("democratic republic of the congo", "RDC"),
    ("congo-kinshasa",       "RDC"),
    ("rdc",                  "RDC"),
    ("drc",                  "RDC"),   # abréviation anglaise
    ("republique du congo",  "Congo-Brazzaville"),
    ("republic of the congo","Congo-Brazzaville"),
    ("congo-brazzaville",    "Congo-Brazzaville"),
    ("gabon",                "Gabon"),
    ("tchad",                "Tchad"),
    ("chad",                 "Tchad"),
    ("republique centrafricaine", "Centrafrique"),
    ("central african republic",  "Centrafrique"),
    ("rca",                  "Centrafrique"),
    ("angola",               "Angola"),
    ("guinee equatoriale",   "Guinée Équatoriale"),
    ("equatorial guinea",    "Guinée Équatoriale"),
    ("sao tome-et-principe", "São Tomé-et-Príncipe"),

    # Afrique de l'Est
    ("ethiopie",             "Éthiopie"),
    ("ethiopia",             "Éthiopie"),
    ("kenya",                "Kenya"),
    ("tanzanie",             "Tanzanie"),
    ("tanzania",             "Tanzanie"),
    ("ouganda",              "Ouganda"),
    ("uganda",               "Ouganda"),
    ("rwanda",               "Rwanda"),
    ("burundi",              "Burundi"),
    ("somalie",              "Somalie"),
    ("somalia",              "Somalie"),
    ("djibouti",             "Djibouti"),
    ("erythree",             "Érythrée"),
    ("eritrea",              "Érythrée"),
    ("soudan du sud",        "Soudan du Sud"),
    ("south sudan",          "Soudan du Sud"),
    ("mozambique",           "Mozambique"),
    ("madagascar",           "Madagascar"),
    ("comores",              "Comores"),
    ("comoros",              "Comores"),
    ("seychelles",           "Seychelles"),
    ("maurice",              "Maurice"),
    ("mauritius",            "Maurice"),

    # Afrique Australe
    ("afrique du sud",       "Afrique du Sud"),
    ("south africa",         "Afrique du Sud"),
    ("zimbabwe",             "Zimbabwe"),
    ("zambie",               "Zambie"),
    ("zambia",               "Zambie"),
    ("namibie",              "Namibie"),
    ("namibia",              "Namibie"),
    ("botswana",             "Botswana"),
    ("malawi",               "Malawi"),
    ("lesotho",              "Lesotho"),
    ("eswatini",             "Eswatini"),
    ("swaziland",            "Eswatini"),
], key=lambda x: -len(x[0]))  # plus long d'abord → évite les faux positifs

# Rétro-compatibilité : garder AFRICAN_COUNTRIES pour le code qui l'utilise directement
AFRICAN_COUNTRIES = [key for key, _ in _AFRICAN_GEO]



def _normalize(text: str) -> str:
    """Minuscules + suppression des accents pour la comparaison."""
    nfkd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _extract_canonical_skills(rows: list) -> Counter:
    """Extrait les compétences canoniques de chaque offre (titre + description).

    Stratégie :
    - On teste d'abord les bigrammes (plus spécifiques), puis les unigrammes.
    - Chaque compétence est comptée une seule fois par offre, même si elle
      apparaît plusieurs fois dans le texte.
    - La normalisation (sans accents, minuscules) rend la détection robuste
      quelle que soit la casse ou l'accentuation dans les offres.
    """
    counter: Counter = Counter()
    token_re = re.compile(r"[a-z0-9#+./\-]{2,}")

    for title, desc in rows:
        raw = _normalize(f"{title or ''} {desc or ''}")
        tokens = token_re.findall(raw)

        found: set[str] = set()

        # 1. Bigrammes — testés en priorité (ex: "machine learning" > "machine")
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]} {tokens[i + 1]}"
            canonical = _CANONICAL.get(bigram)
            if canonical:
                found.add(canonical)

        # 2. Unigrammes — uniquement si pas déjà couvert par un bigramme
        for token in tokens:
            if token in _STOP_WORDS:
                continue
            canonical = _CANONICAL.get(token)
            if canonical and canonical not in found:
                found.add(canonical)

        counter.update(found)

    return counter


class TrendsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _base_active_query(self, cutoff: datetime):
        return (
            select(ScrapedOffer)
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
                or_(
                    ScrapedOffer.expires_at.is_(None),
                    ScrapedOffer.expires_at > datetime.now(timezone.utc),
                ),
            )
        )

    def _count_by_type(self, cutoff: datetime) -> dict[str, int]:
        rows = self.db.execute(
            select(ScrapedOffer.offer_type, func.count(ScrapedOffer.id))
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
            )
            .group_by(ScrapedOffer.offer_type)
        ).all()
        return {str(r[0].value): int(r[1]) for r in rows}

    def _top_locations(self, cutoff: datetime, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            select(ScrapedOffer.location, func.count(ScrapedOffer.id).label("cnt"))
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
                ScrapedOffer.location.isnot(None),
                ScrapedOffer.location != "",
            )
            .group_by(ScrapedOffer.location)
            .order_by(func.count(ScrapedOffer.id).desc())
            .limit(limit * 3)
        ).all()

        # Normaliser : garder uniquement pays africains reconnus.
        # _normalize() supprime les accents → matching robuste quelle que soit
        # la casse ou l'accentuation dans le champ location des offres.
        # _AFRICAN_GEO est trié du plus long au plus court → pas de faux positif
        # (ex: "niger" ne matche pas avant "nigeria").
        country_counts: Counter = Counter()
        for loc, cnt in rows:
            if not loc:
                continue
            loc_norm = _normalize(loc)
            for key, display in _AFRICAN_GEO:
                if key in loc_norm:
                    country_counts[display] += cnt
                    break
            else:
                # Garder tel quel si non reconnu mais court (probablement un pays)
                if len(loc) < 50:
                    country_counts[loc.strip()] += cnt

        return [
            {"pays": pays, "count": count}
            for pays, count in country_counts.most_common(limit)
        ]

    # ── Bloc 1 : Cette semaine en Afrique ────────────────────────────────────

    def get_week_africa(self) -> dict:
        """Agrégations des 7 derniers jours pour le bloc 'Cette semaine en Afrique'."""
        now = datetime.now(timezone.utc)
        cutoff_week = now - timedelta(days=7)
        cutoff_prev = now - timedelta(days=14)

        counts_this = self._count_by_type(cutoff_week)
        counts_prev = self._count_by_type(cutoff_prev)
        total_this = sum(counts_this.values())
        # counts_prev couvre 14 jours — on isole la fenêtre [14j..7j] par soustraction.
        counts_prev_only: dict[str, int] = {
            k: max(0, v - counts_this.get(k, 0))
            for k, v in counts_prev.items()
        }

        # Top locations cette semaine
        top_pays = self._top_locations(cutoff_week, limit=5)

        # Construire 3 "tendances" basées sur les types d'offres les plus actifs
        tendances = []
        type_labels = {
            "job": "Offres d'emploi",
            "scholarship": "Bourses d'études",
            "grant": "Financements & Grants",
            "call_for_applications": "Appels à candidature",
            "opportunity": "Opportunités",
            "formation": "Formations",
            "partnership": "Partenariats",
            "resource": "Ressources",
        }
        sorted_types = sorted(counts_this.items(), key=lambda x: x[1], reverse=True)
        for offer_type, count in sorted_types[:3]:
            prev_count = counts_prev_only.get(offer_type, 0)
            variation = 0
            if prev_count > 0:
                variation = round(((count - prev_count) / prev_count) * 100)
            tendances.append({
                "type": offer_type,
                "label": type_labels.get(offer_type, offer_type),
                "count": count,
                "variation_pct": variation,
            })

        return {
            "total_offres": total_this,
            "top_pays": top_pays,
            "tendances": tendances,
            "periode": "7 derniers jours",
        }

    # ── Bloc 2 : Ton pays ce mois-ci ─────────────────────────────────────────

    def get_mon_pays(self, country: str) -> dict:
        """Agrégations du mois pour le pays de l'utilisateur."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        country_lower = country.lower()

        def count_for_type(offer_types: list[str]) -> int:
            enums = []
            for t in offer_types:
                try:
                    enums.append(ScrapedOfferType(t))
                except ValueError:
                    pass
            if not enums:
                return 0
            return int(self.db.execute(
                select(func.count(ScrapedOffer.id))
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    ScrapedOffer.offer_type.in_(enums),
                    or_(
                        func.lower(ScrapedOffer.location).contains(country_lower),
                        func.lower(ScrapedOffer.location).in_(
                            ["africa", "afrique", "international", "global", "remote"]
                        ),
                    ),
                )
            ).scalar_one())

        # Chaque type d'offre est compté une seule fois — pas de double comptage.
        # job      → offres d'emploi salarié
        # opportunity → missions/consulting freelance
        # formation  → formations courtes (pas comptées dans missions pour éviter confusion)
        # grant + partnership → financements
        # call_for_applications → appels à candidature
        # scholarship → bourses
        emplois = count_for_type(["job"])
        financements = count_for_type(["grant", "partnership"])
        missions = count_for_type(["opportunity"])
        appels_offre = count_for_type(["call_for_applications"])
        bourses = count_for_type(["scholarship"])

        has_data = (emplois + financements + missions + appels_offre + bourses) > 0

        return {
            "pays": country,
            "has_data": has_data,
            "emplois": emplois,
            "financements": financements,
            "missions": missions,
            "appels_offre": appels_offre,
            "bourses": bourses,
            "periode": "30 derniers jours",
        }

    # ── Bloc 3 : Compétences montantes ───────────────────────────────────────

    def get_competences(self) -> list[dict]:
        """Détecte les compétences les plus demandées — extraction dynamique sur toutes les offres.

        - Aucune liste hardcodée : les compétences émergent directement des offres.
        - Aucune limite d'offres : toutes les offres actives de la période sont analysées.
        - Seuil minimum : une compétence doit apparaître dans au moins 2 % des offres.
        - Comparaison sur la période précédente (J-30 à J-60) pour calculer la variation.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=30)
        cutoff_prev = now - timedelta(days=60)

        _ELIGIBLE_TYPES = [
            ScrapedOfferType.job,
            ScrapedOfferType.opportunity,
            ScrapedOfferType.formation,
            ScrapedOfferType.scholarship,
            ScrapedOfferType.grant,
            ScrapedOfferType.call_for_applications,
        ]

        def _fetch(date_from: datetime, date_to: datetime | None = None):
            stmt = (
                select(ScrapedOffer.title, ScrapedOffer.description)
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= date_from,
                    ScrapedOffer.offer_type.in_(_ELIGIBLE_TYPES),
                )
            )
            if date_to is not None:
                stmt = stmt.where(ScrapedOffer.scraped_at < date_to)
            return self.db.execute(stmt).all()

        recent_rows = _fetch(cutoff)
        prev_rows   = _fetch(cutoff_prev, cutoff)

        total_recent = len(recent_rows) or 1
        total_prev   = len(prev_rows)   or 1

        counts_recent = _extract_canonical_skills(recent_rows)
        counts_prev   = _extract_canonical_skills(prev_rows)

        results = []
        for skill, count in counts_recent.most_common(20):
            pct_recent = round(count / total_recent * 100)
            if pct_recent < 2:   # compétence trop marginale
                break
            pct_prev = round(counts_prev.get(skill, 0) / total_prev * 100)
            results.append({
                "competence":    skill,
                "count":         count,
                "pct_offres":    pct_recent,
                "variation_pts": pct_recent - pct_prev,
            })

        return results[:10]

    # ── Bloc 4 : Vue Globale ──────────────────────────────────────────────────

    def get_vue_globale(self) -> dict:
        """Synthèse globale : top pays, secteurs, répartition par type."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        # Répartition par type d'opportunité
        by_type = self._count_by_type(cutoff)

        # Top pays
        top_pays = self._top_locations(cutoff, limit=8)

        # Total
        total = sum(by_type.values())

        # Signal faible : type d'offre avec la plus forte croissance relative
        cutoff_prev = datetime.now(timezone.utc) - timedelta(days=14)
        counts_prev = self._count_by_type(cutoff_prev)
        signal: dict | None = None
        best_growth = 0
        type_labels = {
            "job": "Offres d'emploi",
            "scholarship": "Bourses",
            "grant": "Financements",
            "call_for_applications": "Appels à candidature",
            "opportunity": "Opportunités",
            "formation": "Formations",
            "partnership": "Partenariats",
        }
        for t, cnt in by_type.items():
            prev = max(1, counts_prev.get(t, 1) - cnt)  # [14d..7d] approx
            growth = ((cnt - prev) / prev) * 100 if prev > 0 else 0
            if growth > best_growth and cnt >= 3:
                best_growth = growth
                signal = {
                    "type": t,
                    "label": type_labels.get(t, t),
                    "count": cnt,
                    "croissance_pct": round(growth),
                }

        return {
            "total_offres": total,
            "top_pays": top_pays,
            "par_type": by_type,
            "signal_semaine": signal,
            "periode": "7 derniers jours",
        }

    # ── Agrégation complète ───────────────────────────────────────────────────

    def get_full_summary(self, user_country: str | None = None) -> dict:
        """Retourne toutes les données tendances en un seul appel."""
        country = user_country or "international"
        return {
            "week_africa": self.get_week_africa(),
            "mon_pays": self.get_mon_pays(country),
            "competences": self.get_competences(),
            "vue_globale": self.get_vue_globale(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Enrichissements personnalisés (Phase 2) ───────────────────────────────

    def _offer_types_for_goals(self, goal_types: list[str]) -> list[ScrapedOfferType]:
        type_strs: set[str] = set()
        for gt in goal_types:
            for t in _GOAL_TO_OFFER_TYPES.get(gt, []):
                type_strs.add(t)
        result = []
        for t in type_strs:
            try:
                result.append(ScrapedOfferType(t))
            except ValueError:
                pass
        return result

    def _enrich_week_africa(
        self, week: dict, offer_types: list[ScrapedOfferType]
    ) -> None:
        if not offer_types:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        count = int(self.db.execute(
            select(func.count(ScrapedOffer.id))
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
                ScrapedOffer.offer_type.in_(offer_types),
            )
        ).scalar_one())

        total = week.get("total_offres", 1) or 1
        week["offres_pour_toi"] = count
        week["pct_match"] = round((count / total) * 100)

        rows = self.db.execute(
            select(
                ScrapedOffer.id,
                ScrapedOffer.title,
                ScrapedOffer.url,
                ScrapedOffer.location,
                ScrapedOffer.offer_type,
            )
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
                ScrapedOffer.offer_type.in_(offer_types),
                ScrapedOffer.url.isnot(None),
            )
            .order_by(
                func.coalesce(ScrapedOffer.quality_score, 0).desc(),
                ScrapedOffer.scraped_at.desc(),
            )
            .limit(3)
        ).all()

        week["top_matched"] = [
            {
                "id":       str(r[0]),
                "title":    r[1] or "",
                "url":      r[2] or "",
                "location": r[3] or "",
                "type":     str(r[4].value) if r[4] else "",
            }
            for r in rows
        ]
        week["pour_toi_types"] = [t.value for t in offer_types]
        week["pour_toi_max_age"] = 7

    def _enrich_mon_pays(
        self,
        mon_pays: dict,
        country: str,
        offer_types: list[ScrapedOfferType],
    ) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        country_lower = country.lower()

        if offer_types:
            match_local = int(self.db.execute(
                select(func.count(ScrapedOffer.id))
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    ScrapedOffer.offer_type.in_(offer_types),
                    or_(
                        func.lower(ScrapedOffer.location).contains(country_lower),
                        func.lower(ScrapedOffer.location).in_(
                            ["africa", "afrique", "international", "global", "remote"]
                        ),
                    ),
                )
            ).scalar_one())

            mon_pays["match_local"] = match_local
            mon_pays["pour_toi_types"] = [t.value for t in offer_types]
            mon_pays["match_local_max_age"] = 30

            if match_local == 0:
                mon_pays["verdict"] = "Aucune offre adaptée localement"
            elif match_local < 3:
                s = "s" if match_local > 1 else ""
                mon_pays["verdict"] = f"{match_local} offre{s} adaptée{s} — peu d'options"
            elif match_local < 10:
                mon_pays["verdict"] = f"{match_local} offres adaptées — marché actif"
            else:
                mon_pays["verdict"] = f"{match_local} offres adaptées — bonne dynamique !"

            # Top 3 alternative countries with the most matching offers
            rows = self.db.execute(
                select(ScrapedOffer.location, func.count(ScrapedOffer.id).label("cnt"))
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    ScrapedOffer.offer_type.in_(offer_types),
                    ScrapedOffer.location.isnot(None),
                    ScrapedOffer.location != "",
                    ~func.lower(ScrapedOffer.location).in_(
                        ["africa", "afrique", "international", "global", "remote"]
                    ),
                )
                .group_by(ScrapedOffer.location)
                .order_by(func.count(ScrapedOffer.id).desc())
                .limit(40)
            ).all()

            alt_counts: Counter = Counter()
            for loc, cnt in rows:
                if not loc:
                    continue
                loc_norm = _normalize(loc)
                if country_lower in loc_norm:
                    continue
                for ac, display in _AFRICAN_GEO:
                    if ac in loc_norm:
                        alt_counts[display] += cnt
                        break
                else:
                    if len(loc) < 50:
                        alt_counts[loc.strip()] += cnt

            mon_pays["top_alternative_countries"] = [
                {"pays": pays, "count": count}
                for pays, count in alt_counts.most_common(3)
            ]

        # Comparaison top 5 pays africains pour les goals de l'utilisateur
        # Inclut le pays de l'utilisateur pour situer sa position relative
        if offer_types:
            comp_rows = self.db.execute(
                select(ScrapedOffer.location, func.count(ScrapedOffer.id).label("cnt"))
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    ScrapedOffer.offer_type.in_(offer_types),
                    ScrapedOffer.location.isnot(None),
                    ScrapedOffer.location != "",
                    ~func.lower(ScrapedOffer.location).in_(
                        ["africa", "afrique", "international", "global", "remote"]
                    ),
                )
                .group_by(ScrapedOffer.location)
                .order_by(func.count(ScrapedOffer.id).desc())
                .limit(60)
            ).all()

            global_counts: Counter = Counter()
            for loc, cnt in comp_rows:
                if not loc:
                    continue
                loc_norm = _normalize(loc)
                for ac, display in _AFRICAN_GEO:
                    if ac in loc_norm:
                        global_counts[display] += cnt
                        break
                else:
                    if len(loc) < 50:
                        global_counts[loc.strip()] += cnt

            # S'assurer que le pays de l'utilisateur apparaît dans la liste
            user_pays_display = country.strip().title()
            top5 = dict(global_counts.most_common(5))
            if user_pays_display not in top5 and country_lower != "international":
                # Ajouter avec 0 si absent du top 5
                user_count = int(self.db.execute(
                    select(func.count(ScrapedOffer.id))
                    .where(
                        ScrapedOffer.is_active.is_(True),
                        ScrapedOffer.scraped_at >= cutoff,
                        ScrapedOffer.offer_type.in_(offer_types),
                        func.lower(ScrapedOffer.location).contains(country_lower),
                    )
                ).scalar_one())
                top5[user_pays_display] = user_count

            mon_pays["country_comparison"] = [
                {
                    "pays": pays,
                    "count": count,
                    "is_user_country": pays.lower() == country_lower
                        or country_lower in pays.lower(),
                }
                for pays, count in sorted(top5.items(), key=lambda x: -x[1])
            ]

    def _enrich_competences(
        self, competences: list[dict], user_skills: list[str]
    ) -> None:
        """Enrichit chaque compétence avec des données personnalisées et des liens de formation.

        Pour chaque compétence :
        1. user_has      — comparaison avec le profil utilisateur
        2. offres_debloquees — nombre d'offres actives liées à cette compétence
        3. formation_url — cherche d'abord une formation scrapée, sinon Coursera search
        """
        user_skills_lower = {_normalize(s) for s in user_skills if s}
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        for comp in competences:
            skill_label = comp["competence"]
            skill_norm  = _normalize(skill_label)

            # ── 1. L'utilisateur possède-t-il cette compétence ? ──────────────
            comp["user_has"] = any(
                skill_norm in us or us in skill_norm
                for us in user_skills_lower
            ) if user_skills_lower else False

            # ── 2. Mot-clé de recherche : premier segment avant "/" ou espace ─
            # Ex: "Audit / Contrôle de gestion" → "audit"
            kw = skill_norm.split("/")[0].strip().split()[0]
            comp["skill_keyword"]  = kw
            comp["unlock_max_age"] = 30

            # ── 3. Nombre d'offres actives liées ─────────────────────────────
            unlock_count = int(self.db.execute(
                select(func.count(ScrapedOffer.id))
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    or_(
                        func.lower(ScrapedOffer.title).contains(kw),
                        func.lower(ScrapedOffer.description).contains(kw),
                    ),
                )
            ).scalar_one())
            comp["offres_debloquees"] = unlock_count

            # ── 4. Lien de formation ──────────────────────────────────────────
            # Priorité 1 : formation scrapée en base (la plus pertinente et à jour)
            scraped = self.db.execute(
                select(ScrapedOffer.url, ScrapedOffer.title)
                .where(
                    ScrapedOffer.is_active.is_(True),
                    ScrapedOffer.scraped_at >= cutoff,
                    ScrapedOffer.offer_type == ScrapedOfferType.formation,
                    or_(
                        func.lower(ScrapedOffer.title).contains(kw),
                        func.lower(ScrapedOffer.description).contains(kw),
                    ),
                    ScrapedOffer.url.isnot(None),
                )
                .order_by(func.coalesce(ScrapedOffer.quality_score, 0).desc())
                .limit(1)
            ).first()

            if scraped and scraped[0]:
                comp["formation_url"]      = scraped[0]
                comp["formation_title"]    = scraped[1] or skill_label
                comp["formation_platform"] = "Malayka"
            else:
                # Priorité 2 : recherche Coursera (universel, pas de lien mort)
                comp["formation_url"]      = (
                    f"https://www.coursera.org/search?query={urllib.parse.quote(skill_label)}"
                )
                comp["formation_title"]    = f"Se former : {skill_label}"
                comp["formation_platform"] = "Coursera"

    def _enrich_vue_globale(
        self, vue_globale: dict, goal_types: list[str]
    ) -> None:
        user_target_types: set[str] = set()
        for gt in goal_types:
            for t in _GOAL_TO_OFFER_TYPES.get(gt, []):
                user_target_types.add(t)

        par_type = vue_globale.get("par_type", {})
        orientations = [
            {
                "type":      offer_type_str,
                "label":     _ORIENTATION_LABELS.get(offer_type_str, offer_type_str),
                "count":     count,
                "relevance": 2 if offer_type_str in user_target_types else 1,
            }
            for offer_type_str, count in par_type.items()
        ]
        orientations.sort(key=lambda x: (-x["relevance"], -x["count"]))
        vue_globale["career_orientations"] = orientations

    def get_personalized_summary(
        self,
        user_country: str | None = None,
        user_goal_types: list[str] | None = None,
        user_skills: list[str] | None = None,
        user_domain: str | None = None,
    ) -> dict:
        """Like get_full_summary() but enriched with per-user SQL computations."""
        country = user_country or "international"
        goal_types = user_goal_types or []
        skills = user_skills or []

        week       = self.get_week_africa()
        mon_pays   = self.get_mon_pays(country)
        competences = self.get_competences()
        vue_globale = self.get_vue_globale()

        offer_types = self._offer_types_for_goals(goal_types)
        self._enrich_week_africa(week, offer_types)
        self._enrich_mon_pays(mon_pays, country, offer_types)
        self._enrich_competences(competences, skills)
        self._enrich_vue_globale(vue_globale, goal_types)

        return {
            "week_africa":  week,
            "mon_pays":     mon_pays,
            "competences":  competences,
            "vue_globale":  vue_globale,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
