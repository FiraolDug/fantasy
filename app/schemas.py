import re
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import DepositMethod

_TXID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/]{2,63}$")
_DEST_RE = re.compile(r"^[0-9+][0-9 \-]{7,24}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TelegramWebAppAuth(Strict):
    init_data: str = Field(min_length=10, max_length=4096)


class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    available_balance: Decimal
    pending_balance: Decimal


class DepositInstructions(BaseModel):
    telebirr_receiver_name: str
    telebirr_receiver_number: str
    cbe_receiver_name: str
    cbe_account_number: str
    instructions: str


class DepositRequestCreate(Strict):
    method: DepositMethod
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    transaction_id: str = Field(min_length=3, max_length=64)

    @field_validator("method", mode="before")
    @classmethod
    def _method_case(cls, v):
        return v.upper() if isinstance(v, str) else v

    @field_validator("transaction_id")
    @classmethod
    def _txid(cls, v: str) -> str:
        if not _TXID_RE.fullmatch(v):
            raise ValueError("Transaction ID may contain only letters, digits, - _ /")
        return v.upper()


class DepositRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    method: DepositMethod
    amount: Decimal
    transaction_id: str
    status: str
    created_at: datetime


class WithdrawalCreate(Strict):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    destination_account: str = Field(min_length=8, max_length=25)

    @field_validator("destination_account")
    @classmethod
    def _dest(cls, v: str) -> str:
        if not _DEST_RE.fullmatch(v):
            raise ValueError("Enter a valid Telebirr number or bank account number")
        return v


class WithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount: Decimal
    destination_account: str
    status: str
    requested_at: datetime


class ManagerIdIn(Strict):
    manager_id: str = Field(min_length=1, max_length=12)


class TeamNameIn(Strict):
    team_name: str = Field(min_length=1, max_length=100)
