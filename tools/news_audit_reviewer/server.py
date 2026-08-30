from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .core import ALLOWED_GROUP_FIELDS, DEFAULT_GROUP_BY, ClickHouseReviewBackend


STATIC_ROOT = Path(__file__).with_name("static")


class GroupQuery(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=lambda: list(DEFAULT_GROUP_BY), max_length=3)


class ArticleQuery(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=250)


class ArticleUpdate(BaseModel):
    operator_label: str = ""
    comment: str = Field(default="", max_length=20_000)
    reviewer: str = Field(default="personal_operator", max_length=200)


class GroupUpdate(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    disposition: str = ""
    completed: bool = False
    note: str = Field(default="", max_length=20_000)
    reviewer: str = Field(default="personal_operator", max_length=200)
    apply_label: str = ""


class NoteUpdate(BaseModel):
    scope_type: str
    scope_key: str
    note: str = Field(default="", max_length=30_000)
    reviewer: str = Field(default="personal_operator", max_length=200)


def create_app(backend: ClickHouseReviewBackend) -> FastAPI:
    app = FastAPI(title="News Synthesis ClickHouse Reviewer", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.backend = backend

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/app.js")
    def javascript() -> FileResponse:
        return FileResponse(STATIC_ROOT / "app.js", media_type="application/javascript")

    @app.get("/styles.css")
    def stylesheet() -> FileResponse:
        return FileResponse(STATIC_ROOT / "styles.css", media_type="text/css")

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        try:
            return {
                "audit": backend.validate_source(),
                "summary": backend.summary(),
                "notes": backend.notes(),
                "group_fields": sorted(ALLOWED_GROUP_FIELDS),
                "default_group_by": list(DEFAULT_GROUP_BY),
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        return backend.summary()

    @app.post("/api/groups")
    def groups(query: GroupQuery) -> dict[str, Any]:
        try:
            return backend.groups(query.filters, query.group_by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/articles")
    def articles(query: ArticleQuery) -> dict[str, Any]:
        try:
            return backend.articles(
                query.filters, query.selection, page=query.page, page_size=query.page_size
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/articles/{source_id}")
    def article_detail(source_id: str) -> dict[str, Any]:
        try:
            return backend.article_detail(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/articles/{source_id}/label")
    def put_article_label(source_id: str, update: ArticleUpdate) -> dict[str, Any]:
        try:
            row = backend.set_article_label(
                source_id, update.operator_label, update.comment, update.reviewer
            )
            return {"decision": row, "summary": backend.summary()}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/group")
    def put_group(update: GroupUpdate) -> dict[str, Any]:
        try:
            return backend.save_group(
                filters=update.filters,
                selection=update.selection,
                disposition=update.disposition,
                completed=update.completed,
                note=update.note,
                reviewer=update.reviewer,
                apply_label=update.apply_label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/notes")
    def put_note(update: NoteUpdate) -> dict[str, Any]:
        try:
            return backend.set_note(
                update.scope_type, update.scope_key, update.note, update.reviewer
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
