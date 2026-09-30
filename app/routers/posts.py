from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Post, PostStatus, User

router = APIRouter(prefix="/posts", tags=["posts"])


class PostOut(BaseModel):
    slug: str
    title: str
    summary: str | None
    body: str          # plain text; render with textContent, never innerHTML
    pinned: bool
    published_at: str | None


@router.get("", response_model=list[PostOut])
def list_posts(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Post).filter(Post.status == PostStatus.PUBLISHED, Post.deleted_at.is_(None)) \
        .order_by(Post.pinned.desc(), Post.published_at.desc()).limit(30).all()
    return [PostOut(slug=p.slug, title=p.title, summary=p.summary, body=p.body, pinned=p.pinned,
                    published_at=p.published_at.isoformat() + "Z" if p.published_at else None) for p in rows]
