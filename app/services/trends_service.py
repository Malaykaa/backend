"""Service Tendances — agrégations SQL sur scraped_offers pour alimenter le frontend."""

from __future__ import annotations

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

# Liens de formation de qualité par compétence — fallback si aucune offre type=formation scrapée
_SKILL_FORMATIONS: dict[str, dict] = {
    "Python": {
        "url": "https://www.coursera.org/learn/python",
        "title": "Python for Everybody — University of Michigan",
        "platform": "Coursera",
    },
    "Data Analysis": {
        "url": "https://www.coursera.org/professional-certificates/google-data-analytics",
        "title": "Google Data Analytics Professional Certificate",
        "platform": "Coursera",
    },
    "Excel / Reporting": {
        "url": "https://www.youtube.com/watch?v=Vl0H-qTclOg",
        "title": "Excel complet pour débutants — YouTube",
        "platform": "YouTube",
    },
    "Gestion de projet": {
        "url": "https://www.coursera.org/professional-certificates/google-project-management",
        "title": "Google Project Management Certificate",
        "platform": "Coursera",
    },
    "Communication": {
        "url": "https://www.coursera.org/learn/wharton-communication-skills",
        "title": "Communication Skills for Professionals — Wharton",
        "platform": "Coursera",
    },
    "Anglais": {
        "url": "https://www.duolingo.com",
        "title": "Anglais professionnel — Duolingo",
        "platform": "Duolingo",
    },
    "Marketing Digital": {
        "url": "https://learndigital.withgoogle.com/ateliersnumeriques/",
        "title": "Marketing Digital gratuit — Google Ateliers Numériques",
        "platform": "Google",
    },
    "Comptabilité": {
        "url": "https://www.coursera.org/specializations/accounting-fundamentals",
        "title": "Accounting Fundamentals — University of Virginia",
        "platform": "Coursera",
    },
    "Développement web": {
        "url": "https://www.freecodecamp.org/learn/responsive-web-design/",
        "title": "Responsive Web Design Certification — freeCodeCamp",
        "platform": "freeCodeCamp",
    },
    "Machine Learning": {
        "url": "https://www.coursera.org/specializations/machine-learning-introduction",
        "title": "Machine Learning Specialization — Andrew Ng",
        "platform": "Coursera",
    },
    "SQL": {
        "url": "https://www.youtube.com/watch?v=HXV3zeQKqGY",
        "title": "SQL Full Course — freeCodeCamp (YouTube)",
        "platform": "YouTube",
    },
    "Rédaction technique": {
        "url": "https://developers.google.com/tech-writing",
        "title": "Technical Writing — Google Developers (gratuit)",
        "platform": "Google",
    },
    "Finance": {
        "url": "https://www.coursera.org/learn/understanding-financial-statements",
        "title": "Understanding Financial Statements — Coursera",
        "platform": "Coursera",
    },
    "Agile / Scrum": {
        "url": "https://www.coursera.org/learn/agile-meets-design-thinking",
        "title": "Agile Meets Design Thinking — University of Virginia",
        "platform": "Coursera",
    },
    "Java": {
        "url": "https://www.mooc.fi/en/",
        "title": "Java Programming MOOC — University of Helsinki (gratuit)",
        "platform": "MOOC.fi",
    },
    "React / Frontend": {
        "url": "https://react.dev/learn",
        "title": "Apprendre React — Documentation officielle",
        "platform": "react.dev",
    },
    "Leadership": {
        "url": "https://www.coursera.org/specializations/leadership-development",
        "title": "Leadership Development for Professionals — Coursera",
        "platform": "Coursera",
    },
    "Vente / Commercial": {
        "url": "https://www.coursera.org/professional-certificates/salesforce-sales-development-representative",
        "title": "Salesforce Sales Development Representative Certificate",
        "platform": "Coursera",
    },
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

# Pays africains reconnus pour la géolocalisation des offres
AFRICAN_COUNTRIES = [
    "nigeria", "côte d'ivoire", "cote d'ivoire", "ghana", "kenya", "sénégal",
    "senegal", "cameroun", "cameroon", "maroc", "morocco", "tanzanie", "tanzania",
    "ethiopie", "ethiopia", "ouganda", "uganda", "rwanda", "mali", "burkina faso",
    "guinée", "guinea", "bénin", "benin", "togo", "niger", "madagascar",
    "mozambique", "zambie", "zambia", "zimbabwe", "angola", "afrique du sud",
    "south africa", "egypte", "egypt", "tunisie", "tunisia", "algérie", "algeria",
]

# Skills à détecter dans les titres/descriptions
SKILL_KEYWORDS: list[tuple[str, str]] = [
    ("Python", "python"),
    ("Data Analysis", "data analy"),
    ("Excel / Reporting", "excel"),
    ("Gestion de projet", "gestion de projet"),
    ("Communication", "communication"),
    ("Anglais", "anglais"),
    ("Marketing Digital", "marketing digital"),
    ("Comptabilité", "comptabilit"),
    ("Développement web", "développement web"),
    ("Machine Learning", "machine learning"),
    ("SQL", " sql"),
    ("Rédaction technique", "rédaction"),
    ("Finance", "financ"),
    ("Agile / Scrum", "agile"),
    ("Java", " java"),
    ("React / Frontend", "react"),
    ("Leadership", "leadership"),
    ("Vente / Commercial", "commercial"),
]


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

        # Normaliser : garder uniquement pays africains reconnus
        country_counts: Counter = Counter()
        for loc, cnt in rows:
            if not loc:
                continue
            loc_lower = loc.lower()
            for country in AFRICAN_COUNTRIES:
                if country in loc_lower:
                    # Capitaliser proprement
                    display = country.title().replace("D'", "d'")
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
        """Détecte les compétences les plus demandées dans les offres récentes."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        cutoff_prev = datetime.now(timezone.utc) - timedelta(days=60)

        # Récupérer titres + descriptions récents
        recent_rows = self.db.execute(
            select(ScrapedOffer.title, ScrapedOffer.description)
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff,
                ScrapedOffer.offer_type.in_([
                    ScrapedOfferType.job, ScrapedOfferType.opportunity, ScrapedOfferType.formation
                ]),
            )
            .limit(500)
        ).all()

        prev_rows = self.db.execute(
            select(ScrapedOffer.title, ScrapedOffer.description)
            .where(
                ScrapedOffer.is_active.is_(True),
                ScrapedOffer.scraped_at >= cutoff_prev,
                ScrapedOffer.scraped_at < cutoff,
                ScrapedOffer.offer_type.in_([
                    ScrapedOfferType.job, ScrapedOfferType.opportunity, ScrapedOfferType.formation
                ]),
            )
            .limit(500)
        ).all()

        def count_skills(rows) -> Counter:
            counts: Counter = Counter()
            for title, desc in rows:
                text = ((title or "") + " " + (desc or "")).lower()
                for skill_label, keyword in SKILL_KEYWORDS:
                    if keyword in text:
                        counts[skill_label] += 1
            return counts

        counts_recent = count_skills(recent_rows)
        counts_prev = count_skills(prev_rows)

        total_recent = len(recent_rows) or 1
        total_prev = len(prev_rows) or 1

        results = []
        for skill_label, _ in SKILL_KEYWORDS:
            cnt = counts_recent.get(skill_label, 0)
            if cnt == 0:
                continue
            prev_cnt = counts_prev.get(skill_label, 0)
            pct_recent = round((cnt / total_recent) * 100)
            pct_prev = round((prev_cnt / total_prev) * 100) if prev_cnt else 0
            variation = pct_recent - pct_prev

            results.append({
                "competence": skill_label,
                "count": cnt,
                "pct_offres": pct_recent,
                "variation_pts": variation,
            })

        results.sort(key=lambda x: x["count"], reverse=True)
        return results[:6]

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
                loc_lower = loc.lower()
                if country_lower in loc_lower:
                    continue
                for ac in AFRICAN_COUNTRIES:
                    if ac in loc_lower:
                        display = ac.title().replace("D'", "d'")
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
                loc_lower = loc.lower()
                for ac in AFRICAN_COUNTRIES:
                    if ac in loc_lower:
                        display = ac.title().replace("D'", "d'")
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
        user_skills_lower = {s.lower() for s in user_skills if s}
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        skill_kw_map = {sl: kw for sl, kw in SKILL_KEYWORDS}

        for comp in competences:
            skill_label = comp["competence"]
            skill_lower = skill_label.lower()
            comp["user_has"] = any(
                skill_lower in us or us in skill_lower
                for us in user_skills_lower
            ) if user_skills_lower else False

            keyword = skill_kw_map.get(skill_label)
            if keyword:
                kw = keyword.strip()
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
                comp["skill_keyword"] = kw
                comp["unlock_max_age"] = 30

                # Chercher une formation scrapée en premier — plus pertinente qu'un lien hardcodé
                scraped_formation = self.db.execute(
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

                if scraped_formation and scraped_formation[0]:
                    comp["formation_url"]   = scraped_formation[0]
                    comp["formation_title"] = scraped_formation[1] or skill_label
                    comp["formation_platform"] = "Malayka"
                else:
                    # Fallback sur le lien curé
                    fallback = _SKILL_FORMATIONS.get(skill_label)
                    if fallback:
                        comp["formation_url"]      = fallback["url"]
                        comp["formation_title"]    = fallback["title"]
                        comp["formation_platform"] = fallback["platform"]
            else:
                comp["offres_debloquees"] = 0

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
