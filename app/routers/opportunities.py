"""Routes opportunités — liste matchée avec scores.

OBSOLETE — remplace par ScrapedOffer / recommendations_router.

Deux systemes d'opportunites coexistent dans l'application :

- Opportunity / UserOpportunity (ce module) : generation historique, scoring
  par regles fixes (domaine + pays + statut, puis bonus d'intention).
- ScrapedOffer (app/models/scraped_offer.py) : le systeme actuel — collecte
  automatique, recherche semantique pgvector, rescoring LLM, retours
  utilisateur. C'est LUI qui alimente le fil "Pour toi" et les alertes.

En cas de doute sur la table faisant autorite pour les opportunites, c'est
ScrapedOffer. Ne pas ajouter de fonctionnalite ici.

Conserve et non supprime : verifie, le frontend n'appelle jamais /opportunities
(seule occurrence du mot : une categorie de scraping sans rapport). Mais des
donnees peuvent subsister en base et un eventuel client externe n'est pas
verifiable depuis le code. La suppression se fera a froid, une fois cette
derniere incertitude levee.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.opportunity import MatchedOpportunityResponse, OpportunityResponse
from app.services.opportunity_service import OpportunityService

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=list[MatchedOpportunityResponse])
def list_matched_opportunities(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = OpportunityService(db)
    return service.match_for_user(
        user_id=current_user.id,
        profile=current_user.profile,
    )


@router.post("/{opportunity_id}/view", response_model=dict)
def mark_opportunity_viewed(
    opportunity_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = OpportunityService(db)
    service.repo.mark_viewed(current_user.id, opportunity_id)
    db.commit()
    return {"detail": "Opportunité marquée comme vue."}
