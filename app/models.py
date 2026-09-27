import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class UserRole(str, enum.Enum):
    USER = "user"
    FINANCE_ADMIN = "finance_admin"
    OPERATIONS_ADMIN = "operations_admin"
    SUPER_ADMIN = "super_admin"


class DepositMethod(str, enum.Enum):
    TELEBIRR = "telebirr"
    CBE = "cbe"


class DepositStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WalletTxnType(str, enum.Enum):
    DEPOSIT = "deposit"
    ENTRY_FEE = "entry_fee"
    PRIZE = "prize"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class WalletTxnStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REVERSED = "reversed"


class GameweekStatus(str, enum.Enum):
    REGISTRATION_OPEN = "registration_open"
    REGISTRATION_LOCKED = "registration_locked"
    FPL_MATCHES_IN_PROGRESS = "fpl_matches_in_progress"
    SCORES_PROVISIONAL = "scores_provisional"
    SCORES_FINAL = "scores_final"
    DISPUTE_WINDOW = "dispute_window"
    RANKING_LOCKED = "ranking_locked"
    PRIZE_CALCULATED = "prize_calculated"
    PAYOUT_IN_PROGRESS = "payout_in_progress"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class EntryStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    REFUNDED = "refunded"


class DisputeStatus(str, enum.Enum):
    OPEN = "open"
    UPHELD = "upheld"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    telegram_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # only set for admin roles
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fpl_teams: Mapped[list["FPLTeam"]] = relationship(back_populates="user")
    wallet: Mapped["Wallet"] = relationship(back_populates="user", uselist=False)

    def __str__(self) -> str:
        return self.full_name or self.telegram_id or str(self.id)


class FPLTeam(Base):
    __tablename__ = "fpl_teams"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    manager_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255))
    manager_name: Mapped[str] = mapped_column(String(255))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="fpl_teams")

    def __str__(self) -> str:
        return f"{self.team_name} ({self.manager_id})"


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    available_balance: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=0)
    pending_balance: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="wallet")

    def __str__(self) -> str:
        return f"Wallet<{self.user_id}> {self.available_balance} ETB"


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = uuid_pk()
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    type: Mapped[WalletTxnType] = mapped_column(Enum(WalletTxnType), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)  # signed: + credit / - debit
    balance_after: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[WalletTxnStatus] = mapped_column(Enum(WalletTxnStatus), default=WalletTxnStatus.SUCCESS)
    reference_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "deposit_request"
    reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __str__(self) -> str:
        return f"{self.type} {self.amount}"


class DepositRequest(Base):
    __tablename__ = "deposit_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    method: Mapped[DepositMethod] = mapped_column(Enum(DepositMethod), nullable=False)
    receiver_account: Mapped[str] = mapped_column(String(64))  # snapshot of the account paid to
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[DepositStatus] = mapped_column(Enum(DepositStatus), default=DepositStatus.PENDING)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __str__(self) -> str:
        return f"{self.method} {self.amount} [{self.transaction_id}] - {self.status}"


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(128))
    status: Mapped[WithdrawalStatus] = mapped_column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    def __str__(self) -> str:
        return f"{self.amount} -> {self.destination_account} ({self.status})"


class Gameweek(Base):
    __tablename__ = "gameweeks"

    id: Mapped[uuid.UUID] = uuid_pk()
    gw_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    entry_fee: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=200)
    registration_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[GameweekStatus] = mapped_column(Enum(GameweekStatus), default=GameweekStatus.REGISTRATION_OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    config: Mapped["GameweekConfig"] = relationship(back_populates="gameweek", uselist=False)

    def __str__(self) -> str:
        return f"GW{self.gw_number} ({self.status})"


class GameweekConfig(Base):
    __tablename__ = "gameweek_configs"

    id: Mapped[uuid.UUID] = uuid_pk()
    gameweek_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gameweeks.id"), unique=True, nullable=False)
    tie_break_rule_version: Mapped[str] = mapped_column(String(32), default="v1")
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=10)
    dispute_window_hours: Mapped[int] = mapped_column(Integer, default=24)

    gameweek: Mapped["Gameweek"] = relationship(back_populates="config")


class CompetitionEntry(Base):
    __tablename__ = "competition_entries"
    __table_args__ = (UniqueConstraint("user_id", "gameweek_id", name="uq_entry_user_gameweek"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    gameweek_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gameweeks.id"), nullable=False)
    fpl_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fpl_teams.id"), nullable=False)
    wallet_transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("wallet_transactions.id"), nullable=True)
    status: Mapped[EntryStatus] = mapped_column(Enum(EntryStatus), default=EntryStatus.PENDING_PAYMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __str__(self) -> str:
        return f"Entry<{self.user_id}, GW {self.gameweek_id}>"


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    competition_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competition_entries.id"), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TieBreakRecord(Base):
    __tablename__ = "tie_break_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    gameweek_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gameweeks.id"), nullable=False)
    tied_user_ids: Mapped[dict] = mapped_column(JSON)
    rule_applied: Mapped[str] = mapped_column(String(64))
    resolved_rank: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Prize(Base):
    __tablename__ = "prizes"

    id: Mapped[uuid.UUID] = uuid_pk()
    gameweek_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gameweeks.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2))
    calculation_lock_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[uuid.UUID] = uuid_pk()
    prize_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prizes.id"), nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[uuid.UUID] = uuid_pk()
    competition_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competition_entries.id"), nullable=False)
    raised_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[DisputeStatus] = mapped_column(Enum(DisputeStatus), default=DisputeStatus.OPEN)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = uuid_pk()
    deposit_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deposit_requests.id"), nullable=True)
    competition_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competition_entries.id"), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[WithdrawalStatus] = mapped_column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    before_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}:{self.entity_id}"


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id: Mapped[uuid.UUID] = uuid_pk()
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    related_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlatformSettings(Base):
    """
    Single-row table (id is always 1) for platform-wide toggles admins
    control at runtime, as opposed to deployment-time env vars in
    app/config.py. Use app.services.settings.get_platform_settings(db)
    rather than querying this directly, so the singleton row always exists.
    """

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ads_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __str__(self) -> str:
        return f"PlatformSettings(ads_enabled={self.ads_enabled})"
