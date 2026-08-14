# Importe tous les modèles ici pour qu'Alembic les détecte lors des migrations.
from app.models.user import User, Profile, UserRole, PrimaryRole, Gender  # noqa: F401
from app.models.goal import Goal, GoalType, GoalStatus  # noqa: F401
from app.models.plan import Plan, PlanStep, StepStatus  # noqa: F401
from app.models.document import Document, DocumentType  # noqa: F401
from app.models.chat import ChatThread, ChatMessage, ThreadStatus, MessageRole  # noqa: F401
from app.models.opportunity import Opportunity, UserOpportunity, OpportunityType, UserOpportunityStatus  # noqa: F401
from app.models.attachment import Attachment  # noqa: F401
from app.models.scraped_offer import ScrapedOffer, ScrapedOfferType  # noqa: F401
from app.models.user_intent import UserIntent  # noqa: F401
from app.models.user_offer_feedback import UserOfferFeedback, FeedbackAction  # noqa: F401
from app.models.scraping_source import ScrapingSource  # noqa: F401
from app.models.scraping_actor import ScrapingActor   # noqa: F401
from app.models.notification import UserNotification  # noqa: F401
from app.models.service import (  # noqa: F401
    ServiceProvider, ServiceRequest, ServiceRequestMatch,
    ProviderStatus, RequestType, RequestStatus, MatchDecision, MatchSource,
)
from app.models.structure import (  # noqa: F401
    Structure, StructureMember, StructureStatus, StructureMemberRole, Classroom,
    ClassroomTeacher, StructureInvitation, StructureInvitationClassroom, InvitationStatus,
    ClassroomRosterEntry, ClassroomMembership, MembershipStatus,
    ClassroomStepStatus, ClassroomCourse, ClassroomCourseStep,
    ClassroomCourseRecipient, ClassroomCourseStepProgress, ClassroomCourseKind,
)
