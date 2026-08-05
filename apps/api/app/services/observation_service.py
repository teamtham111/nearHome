"""User observations CRUD for unverified listing notes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.orm import ConfirmedListingORM, ObservationORM


class ObservationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_for_session(self, session_id: UUID) -> list[dict]:
        rows = (
            self.db.query(ObservationORM)
            .join(ConfirmedListingORM)
            .filter(ConfirmedListingORM.session_id == session_id)
            .order_by(ObservationORM.created_at.desc())
            .all()
        )
        return [self._to_dict(r) for r in rows]

    def create(self, listing_id: UUID, category: str, value_text: str) -> dict:
        listing = self.db.query(ConfirmedListingORM).filter(ConfirmedListingORM.id == listing_id).first()
        if not listing:
            raise ValueError("Listing not found")
        obs = ObservationORM(
            listing_id=listing_id,
            category=category,
            value_text=value_text,
            source="USER",
            verification_state="UNVERIFIED",
        )
        self.db.add(obs)
        self.db.commit()
        self.db.refresh(obs)
        return self._to_dict(obs)

    def delete(self, observation_id: UUID) -> bool:
        row = self.db.query(ObservationORM).filter(ObservationORM.id == observation_id).first()
        if not row:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    @staticmethod
    def _to_dict(row: ObservationORM) -> dict:
        return {
            "observation_id": str(row.id),
            "listing_id": str(row.listing_id),
            "category": row.category,
            "value_text": row.value_text,
            "source": row.source,
            "verification_state": row.verification_state,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
