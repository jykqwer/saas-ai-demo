"""轻量 RAG：对知识库 Markdown 文档分块并建立 BM25 倒排索引，检索相关资料。

设计取舍：DeepSeek 等常用服务不提供 Embedding 接口，因此本项目采用
「中文二元组 + 英文单词」的词汇级 BM25 检索。对 FAQ / 产品文档类知识库
足够有效，且零外部依赖、可离线运行、适合 k3s 部署。

若日后接入 Embedding 服务，可在此模块基础上增加向量检索实现。
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.logging import get_logger

MAX_CHUNK_CHARS = 420
_MIN_CHUNK_TOKENS = 8
_K1 = 1.5
_B = 0.75
# 只允许安全的 Markdown 文件名：字母/数字/中文/下划线/连字符 + .md，禁止路径穿越。
_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*\.md$")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """知识库中的一个分块。"""

    source: str
    heading: str
    content: str
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """一次检索命中的分块。"""

    source: str
    heading: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """知识库文档的元信息，用于管理界面展示。"""

    name: str
    chunks: int
    chars: int
    modified_at: str


def tokenize(text: str) -> list[str]:
    """把文本切分为可检索的 term：英文/数字按单词，中文按二元组。"""

    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\.\-]*", text):
        tokens.append(word.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
        tokens.append(run[-1])
    return tokens


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题（# / ## / ###）把文档切成 (heading, body)。"""

    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    heading = "未分类"
    body: list[str] = []

    for line in lines:
        match = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if match and body:
            sections.append((heading, "\n".join(body).strip()))
            body = []
            heading = match.group(2).strip()
        elif match:
            heading = match.group(2).strip()
        else:
            body.append(line)

    if body and "".join(body).strip():
        sections.append((heading, "\n".join(body).strip()))
    return sections


def _chunk_section(heading: str, body: str) -> list[tuple[str, str]]:
    """把超长段落按空行再拆分，保持每个分块有独立语义。"""

    if len(body) <= MAX_CHUNK_CHARS:
        return [(heading, body)]
    chunks: list[tuple[str, str]] = []
    current = ""
    for paragraph in body.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > MAX_CHUNK_CHARS and current:
            chunks.append((heading, current.strip()))
            current = paragraph
        else:
            current = candidate
    if current.strip():
        chunks.append((heading, current.strip()))
    return chunks


class KnowledgeBase:
    """加载知识库文档并做 BM25 检索。"""

    def __init__(
        self, docs_dir: str | Path, top_k: int = 3, min_score: float = 0.0
    ) -> None:
        self.docs_dir = Path(docs_dir)
        self.top_k = top_k
        self.min_score = min_score
        self.chunks: list[KnowledgeChunk] = []
        self._index: dict[str, list[tuple[int, int]]] = {}
        self._doc_len: list[int] = []
        self._avgdl = 0.0
        self._logger = get_logger()

    @property
    def document_count(self) -> int:
        return len({c.source for c in self.chunks})

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def load(self) -> int:
        """加载目录下所有 *.md 文档并建立索引；返回分块数。"""

        self.chunks = []
        if not self.docs_dir.is_dir():
            self._logger.warning("rag_no_dir", extra={"dir": str(self.docs_dir)})
            return 0

        for path in sorted(self.docs_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for heading, body in _split_sections(text):
                for sub_heading, sub_body in _chunk_section(heading, body):
                    tokens = tokenize(sub_body)
                    if len(tokens) < _MIN_CHUNK_TOKENS:
                        continue
                    self.chunks.append(
                        KnowledgeChunk(path.stem, sub_heading, sub_body, tuple(tokens))
                    )

        self._build_index()
        self._logger.info(
            "rag_loaded",
            extra={"docs": self.document_count, "chunks": len(self.chunks)},
        )
        return len(self.chunks)

    def _build_index(self) -> None:
        self._index = defaultdict(list)
        self._doc_len = []
        for idx, chunk in enumerate(self.chunks):
            tf = Counter(chunk.tokens)
            self._doc_len.append(sum(tf.values()))
            for term, count in tf.items():
                self._index[term].append((idx, count))
        n = len(self.chunks)
        self._avgdl = sum(self._doc_len) / n if n else 0.0

    def retrieve(
        self,
        query: str,
        k: int | None = None,
        doc: str | None = None,
    ) -> list[RetrievedChunk]:
        """BM25 检索 top-k；可按文档名过滤，返回按相关性降序的分块。"""

        if not self.chunks:
            return []
        limit = k or self.top_k
        query_terms = set(tokenize(query))
        if not query_terms:
            return []

        # 按文档过滤：source 是去扩展名的 stem，兼容调用方传带/不带 .md 的名字。
        if doc:
            doc_stem = doc.removesuffix(".md")
            candidates = [c for c in self.chunks if c.source == doc_stem]
        else:
            candidates = list(self.chunks)
        if not candidates:
            return []

        # 对候选分块建立局部倒排索引（可按单篇文档检索）。
        local_index: dict[str, list[tuple[int, int]]] = defaultdict(list)
        doc_len: list[int] = []
        for idx, chunk in enumerate(candidates):
            tf = Counter(chunk.tokens)
            doc_len.append(sum(tf.values()))
            for term, count in tf.items():
                local_index[term].append((idx, count))
        n = len(candidates)
        avgdl = sum(doc_len) / n if n else 0.0

        scores = [0.0] * n
        for term in query_terms:
            postings = local_index.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for doc_idx, tf in postings:
                dl = doc_len[doc_idx]
                denom = tf + _K1 * (1.0 - _B + _B * dl / avgdl) if avgdl else tf + _K1
                scores[doc_idx] += idf * (tf * (_K1 + 1.0)) / denom

        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        results: list[RetrievedChunk] = []
        for idx in ranked[:limit]:
            # 全局检索时，低于最低相关度阈值视为噪音直接截断（分数降序）。
            # 显式按单篇文档过滤时视为用户明确意图，不套全局阈值，
            # 因为局部索引（n 很小）会让 idf 偏低，同一查询分数会下降。
            if doc is None and scores[idx] <= self.min_score:
                break
            chunk = candidates[idx]
            results.append(
                RetrievedChunk(chunk.source, chunk.heading, chunk.content, scores[idx])
            )
        return results

    # ============ 文档管理（查看 / 导入 / 删除） ============

    def _resolve_path(self, name: str) -> Path:
        """校验文档名并把名字解析为目录内路径，杜绝路径穿越。"""

        if not _SAFE_NAME_RE.fullmatch(name):
            raise ValueError("unsafe document name")
        return self.docs_dir / name

    def _info_for(self, path: Path) -> DocumentInfo:
        try:
            mtime = path.stat().st_mtime
            chars = path.stat().st_size
        except OSError:
            mtime = 0.0
            chars = 0
        chunk_count = sum(1 for c in self.chunks if c.source == path.stem)
        return DocumentInfo(
            name=path.name,
            chunks=chunk_count,
            chars=chars,
            modified_at=datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        )

    def list_documents(self) -> list[DocumentInfo]:
        """返回知识库目录下所有 Markdown 文档的元信息。"""

        if not self.docs_dir.is_dir():
            return []
        return [self._info_for(p) for p in sorted(self.docs_dir.glob("*.md"))]

    def read_document(self, name: str) -> str | None:
        """读取单个文档原文；不存在或名字非法返回 None。"""

        try:
            path = self._resolve_path(name)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def write_document(self, name: str, content: str) -> DocumentInfo:
        """写入（新增或覆盖）文档并重建检索索引。"""

        path = self._resolve_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.load()
        return self._info_for(path)

    def delete_document(self, name: str) -> bool:
        """删除文档并重建检索索引；不存在返回 False。"""

        try:
            path = self._resolve_path(name)
        except ValueError:
            return False
        if not path.is_file():
            return False
        path.unlink()
        self.load()
        return True
