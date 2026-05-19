"""Configurations détaillées des livrables — sections, prompts, et paramètres.

Chaque type de document est découpé en sections avec des instructions précises
pour garantir un contenu riche, structuré et professionnel.

Porté depuis l'ancien backend (NestJS/deliverable-configs.ts).
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Structures ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeliverableSection:
    """Une section d'un document généré."""

    id: str
    label: str
    instruction: str
    max_tokens: int = 800


@dataclass(frozen=True, slots=True)
class DeliverableConfig:
    """Configuration complète d'un type de livrable."""

    doc_type: str
    description: str
    system_prompt: str
    sections: tuple[DeliverableSection, ...]


# ── Règles communes ──────────────────────────────────────────────────────────

_COMMON_RULES = """
RÈGLES DE SORTIE :
• Réponds en markdown structuré avec titres (##, ###) et listes à puces.
• Langue : français (sauf si l'utilisateur écrit en anglais).
• Sois précis, concret et actionnable. Évite le remplissage.
• N'ajoute pas de préambule ni de conclusion méta ("dans cette section, nous allons…").
• Utilise les informations du profil utilisateur pour personnaliser le contenu.""".strip()

# ── Business Plan ─────────────────────────────────────────────────────────────

_BUSINESS_PLAN_SYSTEM = (
    "Tu es un consultant senior en stratégie d'entreprise et expert en création "
    "de business plans pour des startups et PME africaines et internationales. "
    "Tu produis des business plans professionnels, complets et orientés résultats, "
    "prêts à présenter à des investisseurs ou partenaires.\n\n"
    f"{_COMMON_RULES}"
)

_BUSINESS_PLAN_SECTIONS = (
    DeliverableSection(
        id="executive_summary",
        label="Résumé exécutif",
        instruction=(
            "Rédige le **résumé exécutif** du business plan.\n"
            "Inclus : vision du projet, problème résolu, solution proposée, marché cible, "
            "modèle économique en une ligne, chiffres clés projetés (CA année 1-3), "
            "besoin en financement et utilisation des fonds.\n"
            "Format : ## Résumé exécutif suivi du contenu (max 400 mots)."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="project_vision",
        label="Présentation du projet & vision",
        instruction=(
            "Rédige la section **Présentation du projet & Vision stratégique**.\n"
            "Inclus : description détaillée du projet, histoire/genèse, mission et vision, "
            "valeurs fondatrices, stade de développement actuel (idée / MVP / en croissance), "
            "et impact visé (social, économique, environnemental).\n"
            "Format : ## Présentation du projet & Vision."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="market_opportunity",
        label="Analyse de marché & opportunité",
        instruction=(
            "Rédige la section **Analyse de marché & Opportunité**.\n"
            "Inclus : taille du marché global (TAM), marché adressable (SAM), "
            "marché cible réaliste (SOM), tendances du secteur, problèmes non résolus "
            "par les solutions actuelles, pourquoi maintenant (timing), "
            "et données chiffrées si pertinentes.\n"
            "Format : ## Analyse de marché & Opportunité."
        ),
        max_tokens=1100,
    ),
    DeliverableSection(
        id="competitive_analysis",
        label="Analyse concurrentielle & avantages compétitifs",
        instruction=(
            "Rédige la section **Analyse concurrentielle & Avantages compétitifs**.\n"
            "Inclus : cartographie des concurrents directs et indirects (tableau comparatif), "
            "positionnement différencié, barrières à l'entrée construites, avantages "
            "concurrentiels durables (technologie, réseau, coût, marque, réglementation), "
            "et proposition de valeur unique (UVP).\n"
            "Format : ## Analyse concurrentielle & Avantages compétitifs."
        ),
        max_tokens=1100,
    ),
    DeliverableSection(
        id="product_service",
        label="Offre produit/service & proposition de valeur",
        instruction=(
            "Rédige la section **Offre produit/service & Proposition de valeur**.\n"
            "Inclus : description précise de l'offre (fonctionnalités/services clés), "
            "comment elle résout le problème identifié, roadmap produit "
            "(3-6 mois, 6-12 mois, 12-24 mois), propriété intellectuelle ou innovations, "
            "et preuves de concept / retours clients si disponibles.\n"
            "Format : ## Offre produit/service & Proposition de valeur."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="go_to_market",
        label="Stratégie Go-to-Market & plan commercial",
        instruction=(
            "Rédige la section **Stratégie Go-to-Market & Plan commercial**.\n"
            "Inclus : segments clients prioritaires et personas, canaux d'acquisition "
            "(digitaux et physiques), stratégie de prix et justification, cycle de vente, "
            "objectifs commerciaux année 1 (clients, revenus, parts de marché), "
            "partenariats stratégiques envisagés, et plan d'actions des 90 premiers jours.\n"
            "Format : ## Stratégie Go-to-Market & Plan commercial."
        ),
        max_tokens=1200,
    ),
    DeliverableSection(
        id="team",
        label="Équipe fondatrice & organisation",
        instruction=(
            "Rédige la section **Équipe fondatrice & Organisation**.\n"
            "Inclus : présentation de l'équipe clé (fondateurs, managers), compétences "
            "et expériences pertinentes, complémentarité de l'équipe, organigramme "
            "prévisionnel, besoins en recrutement à 12 mois, et conseillers/mentors "
            "stratégiques si applicable.\n"
            "Si les infos sur l'équipe sont limitées, indique les profils à recruter "
            "pour couvrir les fonctions critiques (Tech, Commercial, Ops, Finance).\n"
            "Format : ## Équipe fondatrice & Organisation."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="operations",
        label="Plan opérationnel & roadmap",
        instruction=(
            "Rédige la section **Plan opérationnel & Roadmap**.\n"
            "Inclus : modèle opérationnel (comment le service/produit est délivré), "
            "ressources clés nécessaires (humaines, technologiques, physiques), "
            "fournisseurs et partenaires opérationnels, KPIs opérationnels, "
            "et roadmap sur 18 mois avec jalons clés (product, commercial, organisationnel).\n"
            "Format : ## Plan opérationnel & Roadmap."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="financials",
        label="Projections financières",
        instruction=(
            "Rédige la section **Projections financières**.\n"
            "Inclus : hypothèses clés (prix moyen, nombre de clients, coûts variables "
            "et fixes), tableau de revenus prévisionnels sur 3 ans (Year 1 / Year 2 / Year 3), "
            "structure de coûts, point mort (break-even), besoin en fonds de roulement, "
            "et indicateurs clés (marges, CAC, LTV si applicable).\n"
            "Format : ## Projections financières. Utilise des tableaux markdown si pertinent."
        ),
        max_tokens=1200,
    ),
    DeliverableSection(
        id="funding",
        label="Besoins en financement & utilisation des fonds",
        instruction=(
            "Rédige la section **Besoins en financement & Utilisation des fonds**.\n"
            "Inclus : montant total recherché, type de financement visé (fonds propres, "
            "dette, subvention, crowdfunding), répartition détaillée de l'utilisation "
            "des fonds (R&D, équipe, marketing, opérations, BFR), retour sur investissement "
            "attendu pour les investisseurs, et exit possible si applicable.\n"
            "Format : ## Besoins en financement & Utilisation des fonds."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="risks",
        label="Risques & plan de mitigation",
        instruction=(
            "Rédige la section **Risques & Plan de mitigation**.\n"
            "Identifie les principaux risques (marché, technologique, réglementaire, "
            "concurrentiel, financier, opérationnel) avec pour chacun : niveau de probabilité "
            "(faible/moyen/élevé), niveau d'impact, et mesures de mitigation concrètes.\n"
            "Format : ## Risques & Plan de mitigation. Utilise un tableau markdown."
        ),
        max_tokens=900,
    ),
)

# ── Plan Marketing ────────────────────────────────────────────────────────────

_PLAN_MARKETING_SYSTEM = (
    "Tu es un directeur marketing expérimenté spécialisé dans la stratégie digitale "
    "et le growth marketing pour les marchés africains et émergents. Tu produis des "
    "plans marketing complets, actionnables et mesurables.\n\n"
    f"{_COMMON_RULES}"
)

_PLAN_MARKETING_SECTIONS = (
    DeliverableSection(
        id="situation_analysis",
        label="Analyse de la situation",
        instruction=(
            "Rédige la section **Analyse de la situation**.\n"
            "Inclus : résumé du contexte business, analyse SWOT (Forces, Faiblesses, "
            "Opportunités, Menaces), positionnement actuel sur le marché, et bilan "
            "des actions marketing passées si mentionnées.\n"
            "Format : ## Analyse de la situation."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="target_personas",
        label="Cibles & Personas",
        instruction=(
            "Rédige la section **Cibles & Personas**.\n"
            "Définis 2 à 3 personas détaillés : profil démographique, comportements d'achat, "
            "motivations, frustrations, canaux utilisés, et message clé à leur adresser. "
            "Priorise les segments.\n"
            "Format : ## Cibles & Personas."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="positioning",
        label="Positionnement & proposition de valeur",
        instruction=(
            "Rédige la section **Positionnement & Proposition de valeur**.\n"
            "Inclus : positionnement différencié vs concurrents, déclaration de positionnement "
            '(format : "Pour [cible], [marque] est le/la [catégorie] qui [bénéfice unique] '
            'parce que [preuve]"), territoire de marque, et messaging par persona.\n'
            "Format : ## Positionnement & Proposition de valeur."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="marketing_mix",
        label="Mix marketing (Produit, Prix, Distribution, Communication)",
        instruction=(
            "Rédige la section **Mix marketing**.\n"
            "Couvre les 4P : Produit (offre, packaging, services associés), Prix (stratégie "
            "tarifaire, positionnement prix), Place/Distribution (canaux de vente et distribution), "
            "Promotion (stratégie de communication globale).\n"
            "Format : ## Mix marketing."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="digital_channels",
        label="Canaux digitaux & tactiques",
        instruction=(
            "Rédige la section **Canaux digitaux & Tactiques**.\n"
            "Détaille pour chaque canal pertinent (SEO, réseaux sociaux, email, paid ads, "
            "WhatsApp/SMS, influenceurs, etc.) : objectif, audience visée, type de contenu, "
            "fréquence, budget indicatif et KPI attendu.\n"
            "Format : ## Canaux digitaux & Tactiques."
        ),
        max_tokens=1100,
    ),
    DeliverableSection(
        id="content_plan",
        label="Plan de contenu & calendrier éditorial",
        instruction=(
            "Rédige la section **Plan de contenu & Calendrier éditorial**.\n"
            "Inclus : piliers de contenu (thèmes clés alignés sur les personas), exemples "
            "de sujets par pilier, fréquence de publication par canal, et un exemple de "
            "calendrier éditorial sur 4 semaines (tableau markdown).\n"
            "Format : ## Plan de contenu & Calendrier éditorial."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="budget_roi",
        label="Budget marketing & ROI estimé",
        instruction=(
            "Rédige la section **Budget marketing & ROI estimé**.\n"
            "Inclus : répartition budgétaire recommandée par poste et par canal (tableau), "
            "coût d'acquisition client (CAC) cible, revenus estimés générés par le plan, "
            "ROI prévisionnel, et règle d'allocation (ex: 70% exploitation, 20% test, "
            "10% innovation).\n"
            "Format : ## Budget marketing & ROI estimé."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="kpis",
        label="Indicateurs de performance (KPIs) & suivi",
        instruction=(
            "Rédige la section **Indicateurs de performance & Suivi**.\n"
            "Définis les KPIs par objectif (notoriété, acquisition, conversion, fidélisation), "
            "le tableau de bord de suivi mensuel, la fréquence de revue, et les seuils d'alerte "
            "déclenchant un ajustement de stratégie.\n"
            "Format : ## Indicateurs de performance & Suivi."
        ),
        max_tokens=800,
    ),
)

# ── Contrat commercial ────────────────────────────────────────────────────────

_CONTRAT_SYSTEM = (
    "Tu es un juriste spécialisé en droit des affaires, droit commercial et droit OHADA "
    "(pour l'Afrique francophone). Tu rédiges des contrats professionnels, équilibrés "
    "et juridiquement solides. Tu signales quand une validation par un avocat est recommandée.\n\n"
    f"{_COMMON_RULES}"
)

_CONTRAT_SECTIONS = (
    DeliverableSection(
        id="parties",
        label="Identification des parties & préambule",
        instruction=(
            "Rédige le **préambule et l'identification des parties** du contrat.\n"
            "Inclus : titre du contrat, date, identification complète des deux parties "
            "(dénomination, forme juridique, siège, représentant légal), et les considérants "
            "(contexte et objectif du contrat).\n"
            "Format : ## Préambule & Identification des parties. Style juridique formel."
        ),
        max_tokens=700,
    ),
    DeliverableSection(
        id="object_scope",
        label="Objet du contrat & périmètre",
        instruction=(
            "Rédige la section **Objet du contrat & Périmètre**.\n"
            "Inclus : définition précise de l'objet (ce qui est fourni/vendu/presté), "
            "périmètre inclus et exclusions explicites, livrables attendus, et glossaire "
            "des termes clés si nécessaire.\n"
            "Format : ## Article 1 — Objet du contrat & Périmètre."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="obligations_provider",
        label="Obligations du prestataire/vendeur",
        instruction=(
            "Rédige la section **Obligations du prestataire/vendeur**.\n"
            "Détaille toutes les obligations : prestations à réaliser, délais, standards "
            "de qualité, ressources affectées, obligation de moyens ou de résultat, "
            "et reporting.\n"
            "Format : ## Article 2 — Obligations du prestataire."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="obligations_client",
        label="Obligations du client",
        instruction=(
            "Rédige la section **Obligations du client**.\n"
            "Détaille : informations et accès à fournir, délais de validation et de paiement, "
            "coopération requise, et ce qui conditionne la bonne exécution par le prestataire.\n"
            "Format : ## Article 3 — Obligations du client."
        ),
        max_tokens=700,
    ),
    DeliverableSection(
        id="financial_terms",
        label="Conditions financières & modalités de paiement",
        instruction=(
            "Rédige la section **Conditions financières & Modalités de paiement**.\n"
            "Inclus : prix total (HT/TTC), décomposition si applicable (acompte, mensualités, "
            "solde), délais de paiement, pénalités de retard, révision des prix, "
            "et mode de facturation.\n"
            "Format : ## Article 4 — Conditions financières."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="duration_termination",
        label="Durée, renouvellement & résiliation",
        instruction=(
            "Rédige la section **Durée, Renouvellement & Résiliation**.\n"
            "Inclus : date d'entrée en vigueur, durée du contrat, conditions de renouvellement "
            "(tacite ou express), délai de préavis, causes de résiliation unilatérale "
            "(faute grave, force majeure, motif légitime), et effets de la résiliation.\n"
            "Format : ## Article 5 — Durée & Résiliation."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="ip_confidentiality",
        label="Propriété intellectuelle & confidentialité",
        instruction=(
            "Rédige la section **Propriété intellectuelle & Confidentialité**.\n"
            "Inclus : propriété des livrables (transfert ou licence), droits d'auteur et brevets, "
            "clause de confidentialité (périmètre, durée, exceptions), et non-divulgation "
            "des informations sensibles.\n"
            "Format : ## Article 6 — Propriété intellectuelle & Confidentialité."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="liability_warranties",
        label="Responsabilités, garanties & assurances",
        instruction=(
            "Rédige la section **Responsabilités, Garanties & Assurances**.\n"
            "Inclus : plafond de responsabilité, exclusions de responsabilité, garanties "
            "sur les livrables (durée, périmètre), obligations d'assurance, et force majeure.\n"
            "Format : ## Article 7 — Responsabilités & Garanties."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="misc_clauses",
        label="Clauses diverses, litiges & droit applicable",
        instruction=(
            "Rédige les **clauses finales**.\n"
            "Inclus : clause de totalité de l'accord, intégralité et modifications (avenant écrit), "
            "clause de non-sollicitation, droit applicable (préciser si droit OHADA ou droit local), "
            "juridiction compétente en cas de litige, médiation préalable, et signatures.\n"
            "Format : ## Article 8 — Dispositions finales & Signatures."
        ),
        max_tokens=700,
    ),
)

# ── CV + Lettre de motivation ─────────────────────────────────────────────────

_CV_LETTRE_SYSTEM = (
    "Tu es un expert RH senior et coach en recherche d'emploi avec 15 ans d'expérience "
    "en recrutement international. Tu rédiges des CV et lettres de motivation optimisés ATS, "
    "percutants et personnalisés selon le profil et le secteur cible.\n\n"
    f"{_COMMON_RULES}"
)

_CV_SECTIONS = (
    DeliverableSection(
        id="cv",
        label="CV professionnel",
        instruction=(
            "Rédige le **CV professionnel complet** optimisé ATS.\n\n"
            "FORMAT OBLIGATOIRE :\n"
            "# [Prénom Nom]\n"
            "**[Titre professionnel ciblé]** | [Ville, Pays]\n\n"
            "## Résumé professionnel\n"
            "[3-4 phrases percutantes : qui, valeur ajoutée, objectif]\n\n"
            "## Expériences professionnelles\n"
            "### [Poste] — [Entreprise] *(Période)*\n"
            "- [Réalisation quantifiée]\n\n"
            "## Formation\n"
            "### [Diplôme] — [École] *(Année)*\n\n"
            "## Compétences clés\n"
            "**[Domaine]** : [compétences séparées par des virgules]\n\n"
            "## Langues\n"
            "## Certifications & Formations complémentaires (si applicable)\n\n"
            "RÈGLES :\n"
            "- Utilise les informations du profil utilisateur et de la conversation.\n"
            "- Si l'utilisateur demande explicitement d'inventer ou de créer un exemple, "
            "génère un CV complet avec des données réalistes et cohérentes.\n"
            "- Sinon, mets [À compléter] pour les données manquantes."
        ),
        max_tokens=1400,
    ),
)

_COVER_LETTER_SECTIONS = (
    DeliverableSection(
        id="cover_letter",
        label="Lettre de motivation",
        instruction=(
            "Rédige une **lettre de motivation percutante et personnalisée**.\n\n"
            "FORMAT :\n"
            "[Ville, Date]\n"
            "**Objet : Candidature — [Poste ciblé]**\n\n"
            "Madame, Monsieur,\n\n"
            "§1 — Accroche : ce qui motive CETTE candidature (spécifique à l'entreprise/secteur)\n"
            "§2 — Valeur ajoutée : 2-3 compétences clés directement liées au poste avec preuves\n"
            "§3 — Adéquation : pourquoi cette opportunité + vision de la contribution\n"
            "§4 — Call to action : demande d'entretien\n\n"
            "[Formule de politesse]\n"
            "[Prénom Nom]\n\n"
            'TON : professionnel, direct, authentique. Pas de clichés ("je suis passionné…").'
        ),
        max_tokens=900,
    ),
)

# ── Mémoire / Rapport académique ─────────────────────────────────────────────

_MEMOIRE_SYSTEM = (
    "Tu es un directeur de mémoire et expert en méthodologie de recherche académique. "
    "Tu produis des mémoires et rapports structurés selon les standards universitaires, "
    "avec un raisonnement rigoureux et des sources bien référencées.\n\n"
    f"{_COMMON_RULES}"
)

_MEMOIRE_SECTIONS = (
    DeliverableSection(
        id="introduction",
        label="Introduction & problématique",
        instruction=(
            "Rédige l'**Introduction & Problématique**.\n"
            "Inclus : contexte général du sujet, accroche, délimitation du sujet, "
            "énoncé de la problématique centrale (sous forme de question), hypothèses "
            "de travail, et plan annoncé du mémoire.\n"
            "Format : ## Introduction."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="literature_review",
        label="Revue de littérature & cadre théorique",
        instruction=(
            "Rédige la **Revue de littérature & Cadre théorique**.\n"
            "Présente les principaux auteurs et théories sur le sujet, les débats existants, "
            "les lacunes identifiées dans la littérature, et le cadre conceptuel retenu. "
            "Cite les auteurs pertinents (format : Nom, Année).\n"
            "Format : ## Revue de littérature & Cadre théorique."
        ),
        max_tokens=1200,
    ),
    DeliverableSection(
        id="methodology",
        label="Méthodologie",
        instruction=(
            "Rédige la section **Méthodologie**.\n"
            "Explique : approche de recherche choisie (qualitative/quantitative/mixte), "
            "méthodes de collecte des données, échantillonnage, outils d'analyse, "
            "limites méthodologiques, et justification des choix.\n"
            "Format : ## Méthodologie."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="development_part1",
        label="Développement — Partie I",
        instruction=(
            "Rédige la **Première partie du développement**.\n"
            "Présente la première grande partie de l'analyse : définitions clés, "
            "premier axe d'étude, données et analyse approfondies. "
            "Alterne entre théorie, données et analyse critique.\n"
            "Format : ## Partie I — [Titre pertinent selon le sujet]."
        ),
        max_tokens=1300,
    ),
    DeliverableSection(
        id="development_part2",
        label="Développement — Partie II",
        instruction=(
            "Rédige la **Deuxième partie du développement**.\n"
            "Présente le deuxième axe : approfondissement, comparaisons, études de cas "
            "si pertinent, et articulation avec la première partie. "
            "Réponds progressivement à la problématique.\n"
            "Format : ## Partie II — [Titre pertinent selon le sujet]."
        ),
        max_tokens=1300,
    ),
    DeliverableSection(
        id="analysis_discussion",
        label="Analyse & Discussion des résultats",
        instruction=(
            "Rédige la section **Analyse & Discussion des résultats**.\n"
            "Synthétise les principaux résultats, confronte-les aux hypothèses initiales, "
            "met en perspective avec la littérature, et discute des implications "
            "pratiques et théoriques.\n"
            "Format : ## Analyse & Discussion."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="conclusion",
        label="Conclusion & Recommandations",
        instruction=(
            "Rédige la **Conclusion & Recommandations**.\n"
            "Récapitule les apports du travail, répond à la problématique, formule des "
            "recommandations concrètes, mentionne les limites du travail et ouvre "
            "sur des pistes de recherche futures.\n"
            "Format : ## Conclusion & Recommandations."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="bibliography",
        label="Bibliographie",
        instruction=(
            "Rédige une **Bibliographie indicative**.\n"
            "Liste 10 à 15 références pertinentes (livres, articles académiques, rapports). "
            "Utilise le format APA. Si les sources exactes ne sont pas connues, "
            "propose des auteurs et ouvrages de référence reconnus.\n"
            "Format : ## Bibliographie."
        ),
        max_tokens=700,
    ),
)

# ── Étude de marché ───────────────────────────────────────────────────────────

_ETUDE_MARCHE_SYSTEM = (
    "Tu es un analyste stratégique et consultant en market research avec une expertise "
    "sur les marchés africains et émergents. Tu produis des études de marché rigoureuses, "
    "chiffrées et actionnables.\n\n"
    f"{_COMMON_RULES}"
)

_ETUDE_MARCHE_SECTIONS = (
    DeliverableSection(
        id="context_objectives",
        label="Contexte & objectifs de l'étude",
        instruction=(
            "Rédige la section **Contexte & Objectifs de l'étude**.\n"
            "Présente : contexte de la démarche, objectifs précis de l'étude, "
            "périmètre géographique et sectoriel, méthodologie utilisée, "
            "et questions centrales auxquelles l'étude doit répondre.\n"
            "Format : ## Contexte & Objectifs."
        ),
        max_tokens=700,
    ),
    DeliverableSection(
        id="macro_environment",
        label="Analyse macro-environnement (PESTEL)",
        instruction=(
            "Rédige l'**Analyse macro-environnement (PESTEL)**.\n"
            "Pour chaque dimension (Politique, Économique, Social, Technologique, "
            "Environnemental, Légal) : identifie les facteurs clés, leur impact "
            "(positif/négatif/neutre) et leur horizon temporel.\n"
            "Format : ## Analyse PESTEL."
        ),
        max_tokens=1100,
    ),
    DeliverableSection(
        id="market_sizing",
        label="Taille & segmentation du marché",
        instruction=(
            "Rédige la section **Taille & Segmentation du marché**.\n"
            "Inclus : estimation TAM/SAM/SOM avec méthodologie de calcul, segmentation "
            "par critères pertinents (géographique, démographique, comportemental), "
            "et taux de croissance annuel (CAGR).\n"
            "Format : ## Taille & Segmentation du marché."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="demand_analysis",
        label="Analyse de la demande & comportements clients",
        instruction=(
            "Rédige la section **Analyse de la demande & Comportements clients**.\n"
            "Analyse : profils des acheteurs, besoins et motivations d'achat, "
            "freins et objections, comportements de consommation, sensibilité au prix, "
            "fidélité, et canaux préférés.\n"
            "Format : ## Analyse de la demande."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="supply_analysis",
        label="Analyse de l'offre & acteurs clés",
        instruction=(
            "Rédige la section **Analyse de l'offre & Acteurs clés**.\n"
            "Cartographie les acteurs (tableau : nom, positionnement, parts de marché "
            "estimées, forces/faiblesses), les acteurs émergents, et les substituts.\n"
            "Format : ## Analyse de l'offre & Acteurs clés."
        ),
        max_tokens=1100,
    ),
    DeliverableSection(
        id="porter_five_forces",
        label="Analyse concurrentielle (5 forces de Porter)",
        instruction=(
            "Rédige l'**Analyse des 5 forces de Porter**.\n"
            "Pour chaque force : évalue l'intensité (faible/modérée/forte) "
            "et les facteurs explicatifs.\n"
            "Format : ## 5 Forces de Porter."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="opportunities_threats",
        label="Opportunités, menaces & facteurs clés de succès",
        instruction=(
            "Rédige la section **Opportunités, Menaces & FCS**.\n"
            "Synthétise les principales opportunités à saisir, les menaces à surveiller, "
            "les Facteurs Clés de Succès, et les niches sous-exploitées.\n"
            "Format : ## Opportunités, Menaces & FCS."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="strategic_recommendations",
        label="Recommandations stratégiques",
        instruction=(
            "Rédige la section **Recommandations stratégiques**.\n"
            "Formule des recommandations concrètes et priorisées : stratégie d'entrée, "
            "positionnement conseillé, segments à cibler, partenariats recommandés, "
            "et indicateurs à surveiller.\n"
            "Format : ## Recommandations stratégiques."
        ),
        max_tokens=900,
    ),
)

# ── Proposition commerciale ───────────────────────────────────────────────────

_PROPOSITION_COMMERCIALE_SYSTEM = (
    "Tu es un expert en développement commercial et rédaction de propositions "
    "commerciales B2B gagnantes. Tu produis des propositions structurées, persuasives "
    "et personnalisées qui répondent précisément aux enjeux du client.\n\n"
    f"{_COMMON_RULES}"
)

_PROPOSITION_COMMERCIALE_SECTIONS = (
    DeliverableSection(
        id="executive_summary",
        label="Page de garde & résumé exécutif",
        instruction=(
            "Rédige la **Page de garde & Résumé exécutif** de la proposition.\n"
            "Inclus : titre, date, destinataire, résumé en 5-6 lignes "
            "(problème → solution → valeur → investissement → prochaine étape).\n"
            "Format : ## Page de garde & Résumé exécutif."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="client_needs",
        label="Compréhension du besoin client",
        instruction=(
            "Rédige la section **Compréhension du besoin client**.\n"
            "Reformule avec précision le contexte du client, le problème identifié, "
            "les enjeux business sous-jacents, et le coût de l'inaction.\n"
            "Format : ## Compréhension du besoin."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="approach",
        label="Notre approche & méthodologie",
        instruction=(
            "Rédige la section **Notre approche & Méthodologie**.\n"
            "Explique : philosophie d'intervention, étapes clés, ce qui différencie "
            "des autres prestataires, et pourquoi l'approche est adaptée.\n"
            "Format : ## Notre approche & Méthodologie."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="solution_deliverables",
        label="Solution proposée & livrables",
        instruction=(
            "Rédige la section **Solution proposée & Livrables**.\n"
            "Détaille précisément : ce qui est inclus, livrables concrets, "
            "formats de rendu, et résultats attendus.\n"
            "Format : ## Solution proposée & Livrables."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="timeline",
        label="Planning & jalons",
        instruction=(
            "Rédige la section **Planning & Jalons**.\n"
            "Présente un planning détaillé : phases, jalons clés, livrables par phase, "
            "et ressources mobilisées. Inclus un tableau markdown.\n"
            "Format : ## Planning & Jalons."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="pricing",
        label="Conditions financières & tarification",
        instruction=(
            "Rédige la section **Conditions financières & Tarification**.\n"
            "Présente : investissement total, décomposition par poste/phase, "
            "options si applicable, conditions de paiement, et ROI estimé.\n"
            "Format : ## Conditions financières."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="next_steps",
        label="Prochaines étapes & engagement",
        instruction=(
            "Rédige la section **Prochaines étapes & Engagement**.\n"
            "Propose un appel à l'action clair : prochaine étape recommandée, "
            "délai de validité de l'offre, et conditions générales simplifiées.\n"
            "Format : ## Prochaines étapes."
        ),
        max_tokens=600,
    ),
)

# ── Pitch Deck ────────────────────────────────────────────────────────────────

_PITCH_DECK_SYSTEM = (
    "Tu es un expert en pitch investor et storytelling pour startups. "
    "Tu as aidé des dizaines de startups africaines et internationales à lever des fonds. "
    "Tu produis des scripts de pitch deck percutants, structurés selon les standards "
    "des meilleurs VC.\n\n"
    f"{_COMMON_RULES}"
)

_PITCH_DECK_SECTIONS = (
    DeliverableSection(
        id="slide_hook",
        label="Slide 1 — Accroche & vision",
        instruction=(
            "Rédige le contenu de la **Slide 1 : Accroche & Vision**.\n"
            "Inclus : nom de la startup, tagline percutant (1 phrase), "
            "et la déclaration de vision. Doit captiver en 10 secondes.\n"
            "Format : ## Slide 1 — Accroche & Vision."
        ),
        max_tokens=400,
    ),
    DeliverableSection(
        id="slide_problem",
        label="Slide 2 — Le problème",
        instruction=(
            "Rédige le contenu de la **Slide 2 : Le Problème**.\n"
            "Formule le problème de façon viscérale : qui souffre, comment, "
            "pourquoi c'est urgent, et le coût estimé. Données chiffrées si possible.\n"
            "Format : ## Slide 2 — Le Problème."
        ),
        max_tokens=500,
    ),
    DeliverableSection(
        id="slide_solution",
        label="Slide 3 — La solution",
        instruction=(
            "Rédige le contenu de la **Slide 3 : La Solution**.\n"
            "Explique la solution en termes simples, ce qui la rend unique, "
            "et comment elle résout directement le problème. Maximum 3 messages clés.\n"
            "Format : ## Slide 3 — La Solution."
        ),
        max_tokens=500,
    ),
    DeliverableSection(
        id="slide_product",
        label="Slide 4 — Produit & démonstration",
        instruction=(
            "Rédige le contenu de la **Slide 4 : Produit & Démonstration**.\n"
            "Décris les fonctionnalités clés, le parcours utilisateur principal, "
            "et les éléments différenciants. Suggère des visuels.\n"
            "Format : ## Slide 4 — Produit & Démonstration."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_market",
        label="Slide 5 — Marché & opportunité",
        instruction=(
            "Rédige le contenu de la **Slide 5 : Marché & Opportunité**.\n"
            "Présente TAM / SAM / SOM avec chiffres, taux de croissance, "
            "et pourquoi c'est le bon moment.\n"
            "Format : ## Slide 5 — Marché & Opportunité."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_business_model",
        label="Slide 6 — Modèle économique",
        instruction=(
            "Rédige le contenu de la **Slide 6 : Modèle économique**.\n"
            "Explique comment tu gagnes de l'argent : sources de revenus, pricing, "
            "unité économique (CAC, LTV, marges), et voie vers la rentabilité.\n"
            "Format : ## Slide 6 — Modèle économique."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_traction",
        label="Slide 7 — Traction & validation",
        instruction=(
            "Rédige le contenu de la **Slide 7 : Traction & Validation**.\n"
            "Présente les preuves de marché : clients, revenus, croissance MoM, "
            "partenariats. Si early stage, validations (interviews, pilotes, liste d'attente).\n"
            "Format : ## Slide 7 — Traction & Validation."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_team",
        label="Slide 8 — Équipe",
        instruction=(
            "Rédige le contenu de la **Slide 8 : Équipe**.\n"
            "Présente les fondateurs avec leur background pertinent, pourquoi CETTE équipe "
            "est la mieux placée, et les conseillers clés.\n"
            "Format : ## Slide 8 — Équipe."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_financials",
        label="Slide 9 — Plan financier & besoins",
        instruction=(
            "Rédige le contenu de la **Slide 9 : Plan financier & Besoins**.\n"
            "Présente les projections revenus sur 3 ans, le montant levé, "
            "l'utilisation des fonds (en %), et le point mort prévisionnel.\n"
            "Format : ## Slide 9 — Plan financier & Besoins."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="slide_cta",
        label="Slide 10 — Call to action",
        instruction=(
            "Rédige le contenu de la **Slide 10 : Call to action**.\n"
            "Formule le closing du pitch : demande claire, ce que tu offres en échange, "
            "prochaine étape, et coordonnées de contact.\n"
            "Format : ## Slide 10 — Call to action."
        ),
        max_tokens=400,
    ),
)

# ── Politique RH ──────────────────────────────────────────────────────────────

_POLITIQUE_RH_SYSTEM = (
    "Tu es un DRH expérimenté et consultant en management des ressources humaines. "
    "Tu produis des politiques RH conformes aux obligations légales et aux bonnes pratiques, "
    "adaptées au contexte de PME et startups africaines et internationales.\n\n"
    f"{_COMMON_RULES}"
)

_POLITIQUE_RH_SECTIONS = (
    DeliverableSection(
        id="introduction_rh",
        label="Introduction & principes directeurs",
        instruction=(
            "Rédige la section **Introduction & Principes directeurs** de la politique RH.\n"
            "Inclus : objet du document, valeurs RH, principes d'équité et non-discrimination, "
            "champ d'application, et processus de révision.\n"
            "Format : ## Introduction & Principes directeurs."
        ),
        max_tokens=700,
    ),
    DeliverableSection(
        id="recruitment",
        label="Recrutement & onboarding",
        instruction=(
            "Rédige la section **Recrutement & Onboarding**.\n"
            "Couvre : processus de recrutement, critères de sélection, politique de diversité, "
            "programme d'intégration (onboarding 30-60-90 jours), et période d'essai.\n"
            "Format : ## Recrutement & Onboarding."
        ),
        max_tokens=1000,
    ),
    DeliverableSection(
        id="compensation",
        label="Rémunération & avantages",
        instruction=(
            "Rédige la section **Rémunération & Avantages**.\n"
            "Inclus : politique salariale, révisions annuelles, primes et bonus, "
            "avantages en nature, et politique de remboursement des frais.\n"
            "Format : ## Rémunération & Avantages."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="performance",
        label="Évaluation des performances",
        instruction=(
            "Rédige la section **Évaluation des performances**.\n"
            "Couvre : processus d'évaluation, fixation des objectifs (OKR/SMART), "
            "critères d'évaluation, gestion de la sous-performance, et lien avec "
            "la rémunération.\n"
            "Format : ## Évaluation des performances."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="training",
        label="Formation & développement des compétences",
        instruction=(
            "Rédige la section **Formation & Développement des compétences**.\n"
            "Inclus : droit à la formation, processus de demande, types de formations éligibles, "
            "budget indicatif, et plan de développement individuel (PDI).\n"
            "Format : ## Formation & Développement."
        ),
        max_tokens=800,
    ),
    DeliverableSection(
        id="conduct_discipline",
        label="Code de conduite & discipline",
        instruction=(
            "Rédige la section **Code de conduite & Discipline**.\n"
            "Couvre : comportements attendus, politique anti-harcèlement, conflits d'intérêts, "
            "procédure disciplinaire, et protection des lanceurs d'alerte.\n"
            "Format : ## Code de conduite & Discipline."
        ),
        max_tokens=900,
    ),
    DeliverableSection(
        id="work_conditions",
        label="Conditions de travail & départs",
        instruction=(
            "Rédige la section **Conditions de travail & Départs**.\n"
            "Inclus : horaires, télétravail, congés, sécurité et santé, "
            "procédures de départ, entretien de sortie, et confidentialité post-emploi.\n"
            "Format : ## Conditions de travail & Départs."
        ),
        max_tokens=900,
    ),
)

# ── Email professionnel ───────────────────────────────────────────────────────

_EMAIL_PRO_SYSTEM = (
    "Tu es un expert en communication professionnelle. Tu rédiges des emails "
    "clairs, concis et adaptés au registre demandé (formel, semi-formel).\n\n"
    f"{_COMMON_RULES}"
)

_EMAIL_PRO_SECTIONS = (
    DeliverableSection(
        id="email",
        label="Email professionnel",
        instruction=(
            "Rédige un **email professionnel complet**.\n"
            "Inclus : Objet précis, Corps structuré (contexte, demande/information, "
            "conclusion), Formule de politesse appropriée.\n"
            "Adapte le registre au contexte. Sois concis et direct.\n"
            "Format : commencer par **Objet :** puis le corps de l'email."
        ),
        max_tokens=800,
    ),
)

# ── Document général (fallback) ───────────────────────────────────────────────

_DOCUMENT_GENERAL_SYSTEM = (
    "Tu es un rédacteur professionnel expert en documentation business. "
    "Tu produis des documents clairs, structurés et adaptés à leur contexte "
    "et audience.\n\n"
    f"{_COMMON_RULES}"
)

_DOCUMENT_GENERAL_SECTIONS = (
    DeliverableSection(
        id="context_purpose",
        label="Contexte & objectif du document",
        instruction=(
            "Rédige la section **Contexte & Objectif**.\n"
            "Présente : le contexte de rédaction, l'objectif précis du document, "
            "l'audience ciblée, et la portée du document.\n"
            "Format : ## Contexte & Objectif."
        ),
        max_tokens=600,
    ),
    DeliverableSection(
        id="main_content_1",
        label="Contenu principal — Partie 1",
        instruction=(
            "Rédige la **première partie du contenu principal**.\n"
            "Développe le premier axe thématique de façon structurée.\n"
            "Format : ## [Titre pertinent au sujet]."
        ),
        max_tokens=1200,
    ),
    DeliverableSection(
        id="main_content_2",
        label="Contenu principal — Partie 2",
        instruction=(
            "Rédige la **deuxième partie du contenu principal**.\n"
            "Développe le deuxième axe en assurant la cohérence avec la partie précédente.\n"
            "Format : ## [Titre pertinent au sujet]."
        ),
        max_tokens=1200,
    ),
    DeliverableSection(
        id="conclusion_next_steps",
        label="Conclusion & prochaines étapes",
        instruction=(
            "Rédige la **Conclusion & Prochaines étapes**.\n"
            "Résume les points clés, formule des recommandations concrètes, "
            "et propose les étapes suivantes.\n"
            "Format : ## Conclusion & Prochaines étapes."
        ),
        max_tokens=600,
    ),
)


# ── Registre des configurations ───────────────────────────────────────────────

DELIVERABLE_CONFIGS: dict[str, DeliverableConfig] = {
    "business_plan": DeliverableConfig(
        doc_type="business_plan",
        description="business plan",
        system_prompt=_BUSINESS_PLAN_SYSTEM,
        sections=_BUSINESS_PLAN_SECTIONS,
    ),
    "plan_marketing": DeliverableConfig(
        doc_type="plan_marketing",
        description="plan marketing",
        system_prompt=_PLAN_MARKETING_SYSTEM,
        sections=_PLAN_MARKETING_SECTIONS,
    ),
    "contract": DeliverableConfig(
        doc_type="contract",
        description="contrat commercial",
        system_prompt=_CONTRAT_SYSTEM,
        sections=_CONTRAT_SECTIONS,
    ),
    "contrat": DeliverableConfig(  # alias FR
        doc_type="contract",
        description="contrat commercial",
        system_prompt=_CONTRAT_SYSTEM,
        sections=_CONTRAT_SECTIONS,
    ),
    "cv": DeliverableConfig(
        doc_type="cv",
        description="CV professionnel",
        system_prompt=_CV_LETTRE_SYSTEM,
        sections=_CV_SECTIONS,
    ),
    "cover_letter": DeliverableConfig(
        doc_type="cover_letter",
        description="lettre de motivation",
        system_prompt=_CV_LETTRE_SYSTEM,
        sections=_COVER_LETTER_SECTIONS,
    ),
    "report": DeliverableConfig(
        doc_type="report",
        description="mémoire / rapport",
        system_prompt=_MEMOIRE_SYSTEM,
        sections=_MEMOIRE_SECTIONS,
    ),
    "memoire_rapport": DeliverableConfig(  # alias
        doc_type="report",
        description="mémoire / rapport",
        system_prompt=_MEMOIRE_SYSTEM,
        sections=_MEMOIRE_SECTIONS,
    ),
    "study_sheet": DeliverableConfig(
        doc_type="study_sheet",
        description="fiche de révision",
        system_prompt=_DOCUMENT_GENERAL_SYSTEM,
        sections=_DOCUMENT_GENERAL_SECTIONS,
    ),
    "etude_marche": DeliverableConfig(
        doc_type="etude_marche",
        description="étude de marché",
        system_prompt=_ETUDE_MARCHE_SYSTEM,
        sections=_ETUDE_MARCHE_SECTIONS,
    ),
    "proposition_commerciale": DeliverableConfig(
        doc_type="proposition_commerciale",
        description="proposition commerciale",
        system_prompt=_PROPOSITION_COMMERCIALE_SYSTEM,
        sections=_PROPOSITION_COMMERCIALE_SECTIONS,
    ),
    "pitch_deck": DeliverableConfig(
        doc_type="pitch_deck",
        description="pitch deck",
        system_prompt=_PITCH_DECK_SYSTEM,
        sections=_PITCH_DECK_SECTIONS,
    ),
    "politique_rh": DeliverableConfig(
        doc_type="politique_rh",
        description="politique RH",
        system_prompt=_POLITIQUE_RH_SYSTEM,
        sections=_POLITIQUE_RH_SECTIONS,
    ),
    "email_pro": DeliverableConfig(
        doc_type="email_pro",
        description="email professionnel",
        system_prompt=_EMAIL_PRO_SYSTEM,
        sections=_EMAIL_PRO_SECTIONS,
    ),
}

# Fallback pour les types non reconnus
DEFAULT_CONFIG = DeliverableConfig(
    doc_type="document_general",
    description="document",
    system_prompt=_DOCUMENT_GENERAL_SYSTEM,
    sections=_DOCUMENT_GENERAL_SECTIONS,
)


# ── Détection du type de document depuis le message utilisateur ───────────────

_DOC_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "business_plan": ("business plan", "plan d'affaires", "business-plan", "plan d affaires"),
    "plan_marketing": ("plan marketing", "stratégie marketing", "plan de marketing"),
    "contract": ("contrat", "accord commercial", "convention"),
    "cv": ("cv", "curriculum", "curriculum vitae"),
    "cover_letter": ("lettre de motivation", "lettre de candidature"),
    "report": ("mémoire", "rapport", "thèse", "mémoire de fin"),
    "etude_marche": ("étude de marché", "etude de marche", "analyse de marché", "market research"),
    "proposition_commerciale": ("proposition commerciale", "offre commerciale", "devis"),
    "pitch_deck": ("pitch deck", "pitch", "présentation investisseur", "deck"),
    "politique_rh": ("politique rh", "ressources humaines", "politique des ressources"),
    "email_pro": ("email professionnel", "mail professionnel", "email pro", "courriel"),
    "study_sheet": ("fiche de révision", "fiche revision", "fiche de cours"),
}


def detect_doc_type(message: str) -> str | None:
    """Détecte le type de document depuis le message utilisateur.

    Retourne la clé du DELIVERABLE_CONFIGS ou None si non détecté.
    """
    lower = message.lower()
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return doc_type
    return None
