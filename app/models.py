"""
Database models. Portable across PostgreSQL and MySQL 8+ / MariaDB 10.5+:
  - generic Uuid / JSON / Numeric types, no dialect-specific columns
  - enums stored as VARCHAR (native_enum=False) so adding a value never needs
    an ALTER TYPE
  - CHECK constraints declared here (MySQL enforces them from 8.0.16)
  - no partial indexes; uniqueness relies on plain UNIQUE (NULLs allowed)
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Column, DateTime, Enum, ForeignKey, Index,
    Integer, Numeric, String, Table, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def pk():
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk(target: str, *, nullable=False, ondelete=None):
    return mapped_column(Uuid(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable)


def E(enum_cls):
    return Enum(enum_cls, native_enum=False, length=40, validate_strings=True)


# ---------------------------------------------------------------- enums
class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BANNED = "BANNED"


class AdminStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class VerificationStatus(str, enum.Enum):
    MANAGER_ACCEPTED = "MANAGER_ACCEPTED"        # ID exists and is registered in Ethiopia
    AWAITING_OWNERSHIP = "AWAITING_OWNERSHIP"    # name matched, waiting for the code challenge
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class VerificationStage(str, enum.Enum):
    MANAGER_ID = "MANAGER_ID"
    TEAM_NAME = "TEAM_NAME"
    OWNERSHIP = "OWNERSHIP"


class VerificationOutcome(str, enum.Enum):
    OK = "OK"
    MANAGER_REJECTED = "MANAGER_REJECTED"      # not found or not Ethiopian (deliberately one bucket)
    NAME_MISMATCH = "NAME_MISMATCH"
    ALREADY_LINKED = "ALREADY_LINKED"
    CHALLENGE_FAILED = "CHALLENGE_FAILED"
    CHALLENGE_EXPIRED = "CHALLENGE_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"


class VerificationMethod(str, enum.Enum):
    NAME_MATCH = "NAME_MATCH"
    NAME_MATCH_PLUS_CODE = "NAME_MATCH_PLUS_CODE"


class PostStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class DepositMethod(str, enum.Enum):
    TELEBIRR = "TELEBIRR"
    CBE = "CBE"


class DepositStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class WithdrawalStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WalletTxnType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    ENTRY_FEE = "ENTRY_FEE"
    PRIZE = "PRIZE"
    WITHDRAWAL = "WITHDRAWAL"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    REFERRAL_BONUS = "REFERRAL_BONUS"


class WalletTxnStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class GameweekStatus(str, enum.Enum):
    REGISTRATION_OPEN = "REGISTRATION_OPEN"
    REGISTRATION_LOCKED = "REGISTRATION_LOCKED"
    FPL_MATCHES_IN_PROGRESS = "FPL_MATCHES_IN_PROGRESS"
    SCORES_PROVISIONAL = "SCORES_PROVISIONAL"
    SCORES_FINAL = "SCORES_FINAL"
    DISPUTE_WINDOW = "DISPUTE_WINDOW"
    RANKING_LOCKED = "RANKING_LOCKED"
    PRIZE_CALCULATED = "PRIZE_CALCULATED"
    PAYOUT_IN_PROGRESS = "PAYOUT_IN_PROGRESS"
    ARCHIVED = "ARCHIVED"
    CANCELLED = "CANCELLED"


class EntryStatus(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class DisputeStatus(str, enum.Enum):
    OPEN = "OPEN"
    UPHELD = "UPHELD"
    REJECTED = "REJECTED"


class RefundStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ReferralStatus(str, enum.Enum):
    PENDING = "PENDING"      # referee signed up, reward condition not met yet
    HELD = "HELD"            # condition met but an abuse signal fired; an admin decides
    REWARDED = "REWARDED"
    VOIDED = "VOIDED"


class Severity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------- end users
class User(Base):
    """End user. Authenticates only through Telegram; never has a password."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = pk()
    telegram_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)   # E.164, e.g. +2519...
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    telegram_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    full_name: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str] = mapped_column(String(2), default="ET", nullable=False)
    preferred_language: Mapped[str | None] = mapped_column(String(8))
    referral_code: Mapped[str | None] = mapped_column(String(12), unique=True)
    status: Mapped[UserStatus] = mapped_column(E(UserStatus), default=UserStatus.ACTIVE, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    fpl_team: Mapped["FplTeam | None"] = relationship(back_populates="user", uselist=False)
    wallet: Mapped["Wallet | None"] = relationship(back_populates="user", uselist=False)

    __table_args__ = (Index("ix_users_status", "status"), Index("ix_users_created_at", "created_at"))

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def is_phone_verified(self) -> bool:
        return self.phone_verified_at is not None


class UserSession(Base):
    """Server-side record behind every user access token, so tokens can be revoked."""
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = pk()          # the token's `sid` claim
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_user_sessions_user", "user_id", "revoked_at"),)


# ---------------------------------------------------------------- FPL verification
class FplVerification(Base):
    """
    One row per verification attempt chain (ID -> team name -> optional code check).
    Holds NO data copied from FPL for someone else's account: the FPL response
    is re-fetched at every step and only compared, never stored, until verified.
    """
    __tablename__ = "fpl_verifications"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    manager_id: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[VerificationStatus] = mapped_column(E(VerificationStatus), nullable=False)
    team_name_submitted: Mapped[str | None] = mapped_column(String(64))
    challenge_code: Mapped[str | None] = mapped_column(String(16))
    challenge_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (Index("ix_fplver_user_status", "user_id", "status"),)


class FplVerificationAttempt(Base):
    """Append-only log used for rate limits, lockouts and fraud review."""
    __tablename__ = "fpl_verification_attempts"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    verification_id: Mapped[uuid.UUID | None] = fk("fpl_verifications.id", nullable=True, ondelete="SET NULL")
    stage: Mapped[VerificationStage] = mapped_column(E(VerificationStage), nullable=False)
    outcome: Mapped[VerificationOutcome] = mapped_column(E(VerificationOutcome), nullable=False)
    manager_id: Mapped[str] = mapped_column(String(12), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fplatt_user_time", "user_id", "created_at"),
        Index("ix_fplatt_ip_time", "ip_hash", "created_at"),
        Index("ix_fplatt_manager_time", "manager_id", "created_at"),
    )


class FplTeam(Base):
    """Exists only after successful verification. One per user, one owner per manager ID."""
    __tablename__ = "fpl_teams"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    manager_id: Mapped[str] = mapped_column(String(12), nullable=False)
    team_name: Mapped[str] = mapped_column(String(64), nullable=False)
    team_name_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    verification_method: Mapped[VerificationMethod] = mapped_column(E(VerificationMethod), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="fpl_team")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_fpl_teams_user"),
        UniqueConstraint("manager_id", name="uq_fpl_teams_manager"),
        CheckConstraint("country_code = 'ET'", name="ck_fpl_teams_country_et"),
    )


# ---------------------------------------------------------------- admin accounts, roles, permissions
role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Uuid(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = pk()
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)   # e.g. "post.publish"
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin")


class AdminAccount(Base):
    """Staff login. Completely separate from end users."""
    __tablename__ = "admin_accounts"

    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[uuid.UUID] = fk("roles.id")
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[AdminStatus] = mapped_column(E(AdminStatus), default=AdminStatus.ACTIVE, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    role: Mapped[Role] = relationship(lazy="joined")

    @property
    def permission_keys(self) -> set[str]:
        return {p.key for p in self.role.permissions}


class AdminSession(Base):
    """Opaque cookie session. Only the SHA-256 of the cookie value is stored."""
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = pk()
    admin_id: Mapped[uuid.UUID] = fk("admin_accounts.id", ondelete="CASCADE")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_admin_sessions_admin", "admin_id", "revoked_at"),)


# ---------------------------------------------------------------- content
class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = pk()
    author_id: Mapped[uuid.UUID] = fk("admin_accounts.id")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)     # plain text; clients must never render it as HTML
    status: Mapped[PostStatus] = mapped_column(E(PostStatus), default=PostStatus.DRAFT, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_posts_status_published", "status", "published_at"),)


# ---------------------------------------------------------------- money
class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    available_balance: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    pending_balance: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="wallet")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_wallets_user"),
        CheckConstraint("available_balance >= 0", name="ck_wallet_available_nonneg"),
        CheckConstraint("pending_balance >= 0", name="ck_wallet_pending_nonneg"),
    )


class WalletTransaction(Base):
    """Append-only ledger. Never update or delete; reverse with a new row."""
    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = pk()
    wallet_id: Mapped[uuid.UUID] = fk("wallets.id")
    type: Mapped[WalletTxnType] = mapped_column(E(WalletTxnType), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)   # signed
    balance_after: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[WalletTxnStatus] = mapped_column(E(WalletTxnStatus), default=WalletTxnStatus.SUCCESS, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(64))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_wtx_wallet_time", "wallet_id", "created_at"),
        Index("ix_wtx_reference", "reference_type", "reference_id"),
        CheckConstraint("amount <> 0", name="ck_wtx_amount_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_wtx_balance_nonneg"),
    )


class DepositRequest(Base):
    __tablename__ = "deposit_requests"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id")
    method: Mapped[DepositMethod] = mapped_column(E(DepositMethod), nullable=False)
    receiver_account: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[DepositStatus] = mapped_column(E(DepositStatus), default=DepositStatus.PENDING, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("method", "transaction_id", name="uq_deposit_method_txid"),
        CheckConstraint("amount > 0", name="ck_deposit_amount_pos"),
        Index("ix_deposit_user_status", "user_id", "status"),
        Index("ix_deposit_status_time", "status", "created_at"),
    )


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id")
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    fee: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    destination_account: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(E(WithdrawalStatus), default=WithdrawalStatus.PENDING, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    processed_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_withdrawal_user_idem"),
        CheckConstraint("amount > 0", name="ck_withdrawal_amount_pos"),
        CheckConstraint("fee >= 0", name="ck_withdrawal_fee_nonneg"),
        Index("ix_withdrawal_user_status", "user_id", "status"),
        Index("ix_withdrawal_status_time", "status", "requested_at"),
    )


class Gameweek(Base):
    __tablename__ = "gameweeks"

    id: Mapped[uuid.UUID] = pk()
    gw_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    entry_fee: Mapped[Numeric] = mapped_column(Numeric(14, 2), default=200, nullable=False)
    registration_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[GameweekStatus] = mapped_column(E(GameweekStatus), default=GameweekStatus.REGISTRATION_OPEN, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    config: Mapped["GameweekConfig | None"] = relationship(back_populates="gameweek", uselist=False)

    __table_args__ = (
        CheckConstraint("entry_fee >= 0", name="ck_gw_fee_nonneg"),
        Index("ix_gw_status", "status"),
    )


class GameweekConfig(Base):
    __tablename__ = "gameweek_configs"

    id: Mapped[uuid.UUID] = pk()
    gameweek_id: Mapped[uuid.UUID] = fk("gameweeks.id", ondelete="CASCADE")
    tie_break_rule_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    dispute_window_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    gameweek: Mapped[Gameweek] = relationship(back_populates="config")
    __table_args__ = (UniqueConstraint("gameweek_id", name="uq_gwconfig_gw"),)


class CompetitionEntry(Base):
    __tablename__ = "competition_entries"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id")
    gameweek_id: Mapped[uuid.UUID] = fk("gameweeks.id")
    fpl_team_id: Mapped[uuid.UUID] = fk("fpl_teams.id")
    wallet_transaction_id: Mapped[uuid.UUID | None] = fk("wallet_transactions.id", nullable=True)
    status: Mapped[EntryStatus] = mapped_column(E(EntryStatus), default=EntryStatus.PENDING_PAYMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "gameweek_id", name="uq_entry_user_gw"),
        UniqueConstraint("fpl_team_id", "gameweek_id", name="uq_entry_team_gw"),
        Index("ix_entry_gw_status", "gameweek_id", "status"),
    )


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id: Mapped[uuid.UUID] = pk()
    competition_entry_id: Mapped[uuid.UUID] = fk("competition_entries.id", ondelete="CASCADE")
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_score_entry_time", "competition_entry_id", "captured_at"),)


class TieBreakRecord(Base):
    __tablename__ = "tie_break_records"

    id: Mapped[uuid.UUID] = pk()
    gameweek_id: Mapped[uuid.UUID] = fk("gameweeks.id", ondelete="CASCADE")
    tied_user_ids: Mapped[dict] = mapped_column(JSON, nullable=False)
    rule_applied: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_rank: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Prize(Base):
    __tablename__ = "prizes"

    id: Mapped[uuid.UUID] = pk()
    gameweek_id: Mapped[uuid.UUID] = fk("gameweeks.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    calculation_lock_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("gameweek_id", "position", name="uq_prize_gw_position"),
        UniqueConstraint("gameweek_id", "user_id", name="uq_prize_gw_user"),
        CheckConstraint("amount > 0", name="ck_prize_amount_pos"),
    )


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[uuid.UUID] = pk()
    prize_id: Mapped[uuid.UUID] = fk("prizes.id", ondelete="CASCADE")
    status: Mapped[WithdrawalStatus] = mapped_column(E(WithdrawalStatus), default=WithdrawalStatus.PENDING, nullable=False)
    method: Mapped[str | None] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(128))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("prize_id", name="uq_payout_prize"), Index("ix_payout_status", "status"))


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[uuid.UUID] = pk()
    competition_entry_id: Mapped[uuid.UUID] = fk("competition_entries.id")
    raised_by: Mapped[uuid.UUID] = fk("users.id")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[DisputeStatus] = mapped_column(E(DisputeStatus), default=DisputeStatus.OPEN, nullable=False)
    resolved_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (Index("ix_dispute_status_time", "status", "created_at"),)


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = pk()
    deposit_request_id: Mapped[uuid.UUID | None] = fk("deposit_requests.id", nullable=True)
    competition_entry_id: Mapped[uuid.UUID | None] = fk("competition_entries.id", nullable=True)
    user_id: Mapped[uuid.UUID] = fk("users.id")
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RefundStatus] = mapped_column(E(RefundStatus), default=RefundStatus.PENDING, nullable=False)
    approved_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_refund_amount_pos"),
        Index("ix_refund_status_time", "status", "created_at"),
    )


class Referral(Base):
    """One row per referred user. UNIQUE(referee_id): a person can only ever be referred once."""
    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = pk()
    referrer_id: Mapped[uuid.UUID] = fk("users.id")
    referee_id: Mapped[uuid.UUID] = fk("users.id")
    status: Mapped[ReferralStatus] = mapped_column(E(ReferralStatus), default=ReferralStatus.PENDING, nullable=False)
    reward_amount: Mapped[Numeric | None] = mapped_column(Numeric(14, 2))
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("referee_id", name="uq_referral_referee"),
        CheckConstraint("referrer_id <> referee_id", name="ck_referral_not_self"),
        Index("ix_referral_referrer_status", "referrer_id", "status"),
    )


# ---------------------------------------------------------------- audit & security
class AuditLog(Base):
    """Append-only record of administrative actions. Grant the app DB user INSERT/SELECT only."""
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = pk()
    actor_admin_id: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_actor_time", "actor_admin_id", "created_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_time", "created_at"),
    )


class SecurityEvent(Base):
    """Append-only log of authentication, verification and abuse signals (users and admins)."""
    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = pk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(E(Severity), default=Severity.LOW, nullable=False)
    user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    admin_id: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True, ondelete="SET NULL")
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_secevt_type_time", "event_type", "created_at"),
        Index("ix_secevt_user_time", "user_id", "created_at"),
        Index("ix_secevt_ip_time", "ip_hash", "created_at"),
    )


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id: Mapped[uuid.UUID] = pk()
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(E(Severity), default=Severity.MEDIUM, nullable=False)
    related_user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[uuid.UUID | None] = fk("admin_accounts.id", nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fraud_open", "resolved", "severity"),
        Index("ix_fraud_user", "related_user_id"),
    )


class PlatformSettings(Base):
    """Single row, id always 1."""
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ads_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (CheckConstraint("id = 1", name="ck_platform_settings_singleton"),)
