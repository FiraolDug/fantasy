from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services import referrals as svc
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=20)


@router.get("/me")
def my_referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Counts and earnings for the caller only. Other people's names are never returned."""
    return svc.summary(db, user)


@router.post("/apply", status_code=204, dependencies=[Depends(rate_limit("referral-apply", 10, 3600))])
def apply(payload: ApplyIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.apply_code(db, request, user, payload.code)
