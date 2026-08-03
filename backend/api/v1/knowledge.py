"""知识库文档管理接口：查看列表、读取内容、导入、删除。"""

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.auth import AuthenticatedUser, Superuser
from core.errors import ApiError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_DOCUMENT_BYTES = 300 * 1024


class DocumentInfoOut(BaseModel):
    name: str
    chunks: int
    chars: int
    modified_at: str


class DocumentListResponse(BaseModel):
    docs: list[DocumentInfoOut]
    total_chunks: int


class DocumentContentResponse(BaseModel):
    name: str
    content: str


class DocumentImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)

    @field_validator("filename")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("filename must contain non-whitespace characters")
        return normalized

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must contain non-whitespace characters")
        return stripped


class DocumentImportResponse(BaseModel):
    doc: DocumentInfoOut
    message: str


class DeleteResponse(BaseModel):
    deleted: bool


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    doc: str | None = Field(default=None, max_length=120)
    k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must contain non-whitespace characters")
        return normalized


class RetrieveResult(BaseModel):
    source: str
    heading: str
    content: str
    score: float


class RetrieveResponse(BaseModel):
    query: str
    doc: str | None
    results: list[RetrieveResult]


def _kb(request: Request):
    kb = getattr(request.app.state, "rag", None)
    if kb is None:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="RAG_NOT_ENABLED",
            message="RAG knowledge base is not enabled.",
        )
    return kb


@router.get(
    "/docs",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List knowledge base documents",
)
async def list_docs(request: Request, user: AuthenticatedUser) -> DocumentListResponse:
    del user
    kb = getattr(request.app.state, "rag", None)
    if kb is None:
        return DocumentListResponse(docs=[], total_chunks=0)

    docs = [
        DocumentInfoOut(
            name=d.name,
            chunks=d.chunks,
            chars=d.chars,
            modified_at=d.modified_at,
        )
        for d in kb.list_documents()
    ]
    return DocumentListResponse(
        docs=docs,
        total_chunks=kb.chunk_count,
    )


@router.get(
    "/docs/{name}",
    response_model=DocumentContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Read a knowledge base document",
)
async def read_doc(
    request: Request,
    name: str,
    user: AuthenticatedUser,
) -> DocumentContentResponse:
    del user
    kb = _kb(request)
    content = kb.read_document(name)
    if content is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="DOCUMENT_NOT_FOUND",
            message="The knowledge base document does not exist.",
        )
    return DocumentContentResponse(name=name, content=content)


@router.post(
    "/docs",
    response_model=DocumentImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a knowledge base document",
)
async def import_doc(
    request: Request,
    body: DocumentImportRequest,
    superuser: Superuser,
) -> DocumentImportResponse:
    del superuser
    kb = _kb(request)
    if len(body.content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ApiError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code="DOCUMENT_TOO_LARGE",
            message="The document is too large.",
        )
    try:
        info = kb.write_document(body.filename, body.content)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="INVALID_DOCUMENT_NAME",
            message="The document name is invalid.",
        ) from exc

    return DocumentImportResponse(
        doc=DocumentInfoOut(
            name=info.name,
            chunks=info.chunks,
            chars=info.chars,
            modified_at=info.modified_at,
        ),
        message=f"已导入「{info.name}」，生成 {info.chunks} 个检索分块。",
    )


@router.delete(
    "/docs/{name}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a knowledge base document",
)
async def delete_doc(
    request: Request,
    name: str,
    superuser: Superuser,
) -> DeleteResponse:
    del superuser
    kb = _kb(request)
    deleted = kb.delete_document(name)
    return DeleteResponse(deleted=deleted)


@router.post(
    "/retrieve",
    response_model=RetrieveResponse,
    status_code=status.HTTP_200_OK,
    summary="Test retrieval against the knowledge base",
)
async def retrieve_docs(
    request: Request,
    body: RetrieveRequest,
    user: AuthenticatedUser,
) -> RetrieveResponse:
    """检索测试：不调用大模型，直接返回命中的知识库分块，便于调试 RAG。"""

    del user
    kb = _kb(request)
    results = kb.retrieve(body.query, k=body.k, doc=body.doc)
    return RetrieveResponse(
        query=body.query,
        doc=body.doc,
        results=[
            RetrieveResult(
                source=r.source,
                heading=r.heading,
                content=r.content,
                score=round(r.score, 3),
            )
            for r in results
        ],
    )
