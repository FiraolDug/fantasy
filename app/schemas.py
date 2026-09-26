import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models import DepositMethod


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminLogin(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    telegram_id: str | None
    phone_number: str | None
    full_name: str | None
    is_active: bool

    class Config:
        from_attributes = True


class WalletOut(BaseModel):
    available_balance: Decimal
    pending_balance: Decimal

    class Config:
        from_attributes = True


class DepositInstructions(BaseModel):
    telebirr_receiver_name: str
    telebirr_receiver_number: str
    cbe_receiver_name: str
    cbe_account_number: str
    instructions: str


class DepositRequestCreate(BaseModel):
    method: DepositMethod
    amount: Decimal = Field(gt=0)
    transaction_id: str = Field(min_length=3, max_length=128)


class DepositRequestOut(BaseModel):
    id: uuid.UUID
    method: DepositMethod
    amount: Decimal
    transaction_id: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class FPLTeamLookup(BaseModel):
    manager_id: str = Field(min_length=1, max_length=32)


class FPLTeamOut(BaseModel):
    manager_id: str
    team_name: str
    manager_name: str
    verified: bool

    class Config:
        from_attributes = True
