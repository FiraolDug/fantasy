from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ManagerIdIn, TeamNameIn
from app.services import verification as svc
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/verification", tags=["verification"],
                   dependencies=[Depends(rate_limit("verification", 30, 60))])


@router.get("/status")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Verification state of the signed-in user only."""
    return svc.status_for(db, user)


@router.post("/fpl/manager-id")
def manager_id(payload: ManagerIdIn, request: Request, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    return svc.submit_manager_id(db, request, user, payload.manager_id)


@router.post("/fpl/team-name")
def team_name(payload: TeamNameIn, request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    return svc.submit_team_name(db, request, user, payload.team_name)


@router.post("/fpl/ownership")
def ownership(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.check_ownership(db, request, user)
