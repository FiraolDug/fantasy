from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import FPLTeam, User
from app.schemas import FPLTeamLookup, FPLTeamOut
from app.services.fpl_client import FPLManagerNotFound, fetch_manager_entry
from app.services.fpl_sync import fpl_rate_limiter

router = APIRouter(prefix="/fpl", tags=["fpl"])


@router.post("/lookup", response_model=FPLTeamOut)
def lookup_manager(payload: FPLTeamLookup):
    """
    Step 4 of registration: resolve a raw FPL Manager ID into team/manager
    name so the user can confirm it's theirs before it's saved.
    """
    fpl_rate_limiter.acquire()
    try:
        info = fetch_manager_entry(payload.manager_id)
    except FPLManagerNotFound:
        raise HTTPException(status_code=404, detail="No FPL manager found with that ID")
    return FPLTeamOut(**info, verified=False)


@router.post("/confirm", response_model=FPLTeamOut)
def confirm_manager(
    payload: FPLTeamLookup,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(FPLTeam).filter(FPLTeam.manager_id == payload.manager_id).first()
    if existing is not None and existing.user_id != current_user.id:
        raise HTTPException(
            status_code=409, detail="This FPL Manager ID is already registered to another account."
        )

    fpl_rate_limiter.acquire()
    try:
        info = fetch_manager_entry(payload.manager_id)
    except FPLManagerNotFound:
        raise HTTPException(status_code=404, detail="No FPL manager found with that ID")

    if existing is not None:
        existing.team_name = info["team_name"]
        existing.manager_name = info["manager_name"]
        existing.verified = True
        team = existing
    else:
        team = FPLTeam(
            user_id=current_user.id,
            manager_id=info["manager_id"],
            team_name=info["team_name"],
            manager_name=info["manager_name"],
            verified=True,
        )
        db.add(team)

    db.commit()
    db.refresh(team)
    return team
