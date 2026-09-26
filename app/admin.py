"""
Admin web panel (sqladmin) — separate from the JSON API.
Session-based login, restricted to FINANCE_ADMIN / OPERATIONS_ADMIN /
SUPER_ADMIN roles. This is the "Admin Dashboard" from the spec:
Users, Deposits, Withdrawals, Gameweeks, Prizes, Disputes, Audit Logs.
"""
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.database import SessionLocal, engine
from app.deps import ADMIN_ROLES
from app.models import (
    AuditLog,
    CompetitionEntry,
    DepositRequest,
    Dispute,
    FPLTeam,
    FraudAlert,
    Gameweek,
    Prize,
    Refund,
    User,
    Wallet,
    WalletTransaction,
    Withdrawal,
)
from app.security import decode_access_token, verify_password


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email, password = form.get("username"), form.get("password")
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.phone_number == email).first()
            if (
                user is None
                or user.role not in ADMIN_ROLES
                or user.password_hash is None
                or not verify_password(password, user.password_hash)
            ):
                return False
            request.session.update({"admin_user_id": str(user.id)})
            return True
        finally:
            db.close()

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return "admin_user_id" in request.session


# --- Read-mostly / sensitive views: no arbitrary delete from the UI ---

class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.full_name, User.phone_number, User.role, User.is_active]
    can_delete = False
    form_excluded_columns = [User.password_hash]


class WalletAdmin(ModelView, model=Wallet):
    column_list = [Wallet.id, Wallet.user_id, Wallet.available_balance, Wallet.pending_balance]
    can_create = False
    can_edit = False
    can_delete = False


class WalletTransactionAdmin(ModelView, model=WalletTransaction):
    column_list = [
        WalletTransaction.id,
        WalletTransaction.wallet_id,
        WalletTransaction.type,
        WalletTransaction.amount,
        WalletTransaction.balance_after,
        WalletTransaction.created_at,
    ]
    can_create = False
    can_edit = False
    can_delete = False  # ledger rows are immutable — reverse with a new txn, never delete


class DepositRequestAdmin(ModelView, model=DepositRequest):
    column_list = [
        DepositRequest.id,
        DepositRequest.user_id,
        DepositRequest.method,
        DepositRequest.amount,
        DepositRequest.transaction_id,
        DepositRequest.status,
        DepositRequest.created_at,
    ]
    can_create = False
    can_delete = False
    # Approve/reject with ledger effects go through POST /deposits/{id}/approve|reject,
    # NOT by editing this row directly — status is shown here for visibility/audit only.
    form_excluded_columns = [DepositRequest.status]


class WithdrawalAdmin(ModelView, model=Withdrawal):
    column_list = [
        Withdrawal.id,
        Withdrawal.user_id,
        Withdrawal.amount,
        Withdrawal.status,
        Withdrawal.requested_at,
    ]
    can_delete = False


class RefundAdmin(ModelView, model=Refund):
    column_list = [Refund.id, Refund.user_id, Refund.amount, Refund.status, Refund.reason]
    can_delete = False


class FPLTeamAdmin(ModelView, model=FPLTeam):
    column_list = [FPLTeam.id, FPLTeam.user_id, FPLTeam.manager_id, FPLTeam.team_name, FPLTeam.verified]
    can_delete = False


class GameweekAdmin(ModelView, model=Gameweek):
    column_list = [
        Gameweek.id,
        Gameweek.gw_number,
        Gameweek.entry_fee,
        Gameweek.registration_deadline,
        Gameweek.status,
    ]


class CompetitionEntryAdmin(ModelView, model=CompetitionEntry):
    column_list = [
        CompetitionEntry.id,
        CompetitionEntry.user_id,
        CompetitionEntry.gameweek_id,
        CompetitionEntry.status,
    ]
    can_create = False
    can_delete = False


class PrizeAdmin(ModelView, model=Prize):
    column_list = [Prize.id, Prize.gameweek_id, Prize.user_id, Prize.position, Prize.amount]
    can_create = False
    can_edit = False
    can_delete = False


class DisputeAdmin(ModelView, model=Dispute):
    column_list = [Dispute.id, Dispute.competition_entry_id, Dispute.status, Dispute.reason]
    can_delete = False


class FraudAlertAdmin(ModelView, model=FraudAlert):
    column_list = [FraudAlert.id, FraudAlert.category, FraudAlert.description, FraudAlert.resolved]
    can_create = False


class AuditLogAdmin(ModelView, model=AuditLog):
    column_list = [
        AuditLog.id,
        AuditLog.actor_id,
        AuditLog.action,
        AuditLog.entity_type,
        AuditLog.entity_id,
        AuditLog.created_at,
    ]
    can_create = False
    can_edit = False
    can_delete = False  # audit logs are append-only, always


def register_admin(app, secret_key: str):
    auth_backend = AdminAuth(secret_key=secret_key)
    admin = Admin(app, engine, authentication_backend=auth_backend, title="FPL Platform Admin")

    admin.add_view(UserAdmin)
    admin.add_view(WalletAdmin)
    admin.add_view(WalletTransactionAdmin)
    admin.add_view(DepositRequestAdmin)
    admin.add_view(WithdrawalAdmin)
    admin.add_view(RefundAdmin)
    admin.add_view(FPLTeamAdmin)
    admin.add_view(GameweekAdmin)
    admin.add_view(CompetitionEntryAdmin)
    admin.add_view(PrizeAdmin)
    admin.add_view(DisputeAdmin)
    admin.add_view(FraudAlertAdmin)
    admin.add_view(AuditLogAdmin)

    return admin
