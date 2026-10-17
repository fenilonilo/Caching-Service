"""SQLAlchemy schema shared by the repositories and the app's startup."""

from uuid import UUID

from sqlalchemy import String, Text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TransformationRow(Base):
    __tablename__ = "transformations"

    # SHA-256 of the input rather than the input itself: fixed-size keys keep the index small
    # and stay below PostgreSQL's B-tree entry limit (~2.7 kB) for long strings.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[str] = mapped_column(Text)


class PayloadRow(Base):
    __tablename__ = "payloads"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    output: Mapped[str] = mapped_column(Text)


async def create_schema(engine: AsyncEngine) -> None:
    # shortcut: create_all at startup; move to Alembic migrations once the schema changes
    # or several replicas start at the same time (create_all is check-then-create).
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
