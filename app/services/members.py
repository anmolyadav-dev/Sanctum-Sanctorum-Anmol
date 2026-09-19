"""Member operations and tier helpers."""
from datetime import datetime
from typing import List

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Loan, Member, MemberTier, Order, OrderStatus
from app.schemas import MemberCreate, MemberPage, MemberStats

# Tiers from lowest to highest; a member's rank is their index in this list.
TIER_ORDER: List[str] = [
    MemberTier.APPRENTICE.value,
    MemberTier.ADEPT.value,
    MemberTier.MASTER.value,
    MemberTier.SUPREME.value,
]

# Minimum tier allowed to buy or borrow restricted books.
RESTRICTED_MIN_TIER = MemberTier.MASTER.value


def tier_at_least(tier: str, minimum: str) -> bool:
    """True if ``tier`` ranks at or above ``minimum``."""
    return TIER_ORDER.index(tier) >= TIER_ORDER.index(minimum)


def ensure_can_access_restricted(member: Member) -> None:
    """Raise 403 unless the member's tier may access restricted books."""
    if not tier_at_least(member.tier, RESTRICTED_MIN_TIER):
        raise HTTPException(
            status_code=403, detail=f"Restricted books require tier '{RESTRICTED_MIN_TIER}' or higher"
        )


def create_member(db: Session, data: MemberCreate, now: datetime) -> Member:
    """Register a member.

    Rules: email (already stripped + lowercased) must be unique -> 409; created_at = now.
    """
    existing = db.scalar(select(Member.id).where(func.lower(Member.email) == data.email.lower()))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Member with email '{data.email}' already exists")
    member = Member(name=data.name, email=data.email, tier=data.tier.value, created_at=now)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def get_member(db: Session, member_id: int) -> Member:
    """Return a member by id, or raise 404."""
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def list_members(db: Session, limit: int = 20, offset: int = 0) -> MemberPage:
    """List members with pagination (optional extra endpoint)."""
    query = select(Member).order_by(Member.id.asc())
    total = db.scalar(select(func.count(Member.id))) or 0
    members = list(db.scalars(query.limit(limit).offset(offset)).all())
    return MemberPage(items=members, total=total, limit=limit, offset=offset)


def list_member_orders(db: Session, member_id: int) -> List[Order]:
    """All orders of a member ordered by id ascending; 404 if the member is missing."""
    get_member(db, member_id)
    return list(db.scalars(select(Order).where(Order.member_id == member_id).order_by(Order.id)))


def get_member_stats(db: Session, member_id: int, now: datetime) -> MemberStats:
    """Summarize a member's activity.

    Rules:
    - 404 if the member is missing.
    - orders_paid / total_spent_cents consider only ``paid`` orders.
    - active_loans counts every unreturned loan (overdue ones included).
    - overdue_loans counts unreturned loans with now > due_at.
    - late_fees_cents sums late fees of returned loans.
    """
    get_member(db, member_id)

    orders_stmt = (
        select(
            func.count(Order.id).label("orders_paid"),
            func.coalesce(func.sum(Order.total_cents), 0).label("total_spent_cents"),
        )
        .where(Order.member_id == member_id)
        .where(Order.status == OrderStatus.PAID.value)
    )
    order_row = db.execute(orders_stmt).one()
    orders_paid = order_row.orders_paid
    total_spent_cents = order_row.total_spent_cents

    active_loans = (
        db.scalar(
            select(func.count(Loan.id))
            .where(Loan.member_id == member_id)
            .where(Loan.returned_at.is_(None))
        )
        or 0
    )

    overdue_loans = (
        db.scalar(
            select(func.count(Loan.id))
            .where(Loan.member_id == member_id)
            .where(Loan.returned_at.is_(None))
            .where(Loan.due_at < now)
        )
        or 0
    )

    late_fees_cents = (
        db.scalar(
            select(func.coalesce(func.sum(Loan.late_fee_cents), 0))
            .where(Loan.member_id == member_id)
            .where(Loan.returned_at.is_not(None))
        )
        or 0
    )

    return MemberStats(
        member_id=member_id,
        orders_paid=orders_paid,
        total_spent_cents=total_spent_cents,
        active_loans=active_loans,
        overdue_loans=overdue_loans,
        late_fees_cents=late_fees_cents,
    )
