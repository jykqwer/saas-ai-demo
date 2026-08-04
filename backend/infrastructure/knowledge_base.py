"""轻量 RAG：对 Markdown 做章节感知分块，并用字段加权 BM25 检索。

设计取舍：DeepSeek 等常用服务不提供 Embedding 接口，因此本项目采用
「中文二元组 + 英文单词」的词汇级 BM25 检索，并对文档标题、章节标题、
正文设置不同权重。索引在文档加载时一次构建，查询只遍历命中词的倒排列表，
适合资源受限的 k3s 部署。

若日后接入 Embedding 服务，可在此模块基础上增加向量检索实现。
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.logging import get_logger

MAX_CHUNK_CHARS = 700
_MIN_CHUNK_TOKENS = 8
_K1 = 1.5
_B = 0.75
_DOCUMENT_WEIGHT = 1.5
_HEADING_WEIGHT = 2.5
_EXPANSION_WEIGHT = 0.65
_MIN_ORIGINAL_QUERY_COVERAGE = 0.25
_MIN_EXPANSION_MATCHES = 2
# 只允许安全的 Markdown 文件名：字母/数字/中文/下划线/连字符 + .md，禁止路径穿越。
_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*\.md$")

# 产品领域的高频口语归一化。扩展词只用于召回且权重低于原查询，避免覆盖用户原意。
_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("多少钱", "收费", "费用", "贵不贵"), "价格 计费 报价 套餐"),
    (
        ("自己服务器", "自己的集群", "本地部署", "自建", "本地化"),
        "私有化部署 Kubernetes Docker",
    ),
    (("单点登录", "统一认证"), "SSO SAML OIDC 身份认证"),
    (
        ("数据泄露", "会不会泄露", "数据安全吗", "隐私安全"),
        "数据安全 加密 权限 审计 脱敏",
    ),
    (("找回密码", "忘了密码", "不能登录", "登录不上"), "忘记密码 重置密码 账号锁定"),
    (("对接", "接入现有系统", "系统集成"), "API Webhook 集成"),
    (("容灾", "灾难恢复"), "备份恢复 RPO RTO"),
)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """知识库中的一个分块。"""

    source: str
    heading: str
    content: str


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
        normalized = word.lower()
        tokens.append(normalized)
        # 精确编号保留完整 token，同时拆出组成部分，兼容用户省略校验后缀。
        for part in re.split(r"[_\.\-]+", normalized):
            if len(part) > 1 and part != normalized:
                tokens.append(part)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
    return tokens


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题分段，并保留完整的 # > ## > ### 章节路径。"""

    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    headings: dict[int, str] = {}
    body: list[str] = []

    def current_heading() -> str:
        path = [headings[level] for level in sorted(headings)]
        return " > ".join(path) if path else "未分类"

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append((current_heading(), content))
        body.clear()

    for line in lines:
        match = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if not match:
            body.append(line)
            continue
        flush()
        level = len(match.group(1))
        headings[level] = match.group(2).strip()
        for child_level in tuple(headings):
            if child_level > level:
                del headings[child_level]

    flush()
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
        self._index: dict[str, list[tuple[int, float]]] = {}
        self._doc_len: list[float] = []
        self._source_indices: dict[str, set[int]] = {}
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
        self._index = defaultdict(list)
        self._doc_len = []
        self._source_indices = defaultdict(set)
        self._avgdl = 0.0
        if not self.docs_dir.is_dir():
            self._logger.warning("rag_no_dir", extra={"dir": str(self.docs_dir)})
            return 0

        for path in sorted(self.docs_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for heading, body in _split_sections(text):
                for sub_heading, sub_body in _chunk_section(heading, body):
                    body_tokens = tokenize(sub_body)
                    if len(body_tokens) < _MIN_CHUNK_TOKENS:
                        continue
                    chunk = KnowledgeChunk(path.stem, sub_heading, sub_body)
                    chunk_index = len(self.chunks)
                    self.chunks.append(chunk)
                    self._source_indices[path.stem].add(chunk_index)

                    weighted_tf: Counter[str] = Counter(body_tokens)
                    for term, count in Counter(tokenize(path.stem)).items():
                        weighted_tf[term] += count * _DOCUMENT_WEIGHT
                    for term, count in Counter(tokenize(sub_heading)).items():
                        weighted_tf[term] += count * _HEADING_WEIGHT
                    document_length = sum(weighted_tf.values())
                    self._doc_len.append(document_length)
                    for term, count in weighted_tf.items():
                        self._index[term].append((chunk_index, float(count)))

        self._avgdl = sum(self._doc_len) / len(self._doc_len) if self._doc_len else 0.0
        self._logger.info(
            "rag_loaded",
            extra={"docs": self.document_count, "chunks": len(self.chunks)},
        )
        return len(self.chunks)

    @staticmethod
    def _query_terms(query: str) -> dict[str, float]:
        """生成原查询与低权重领域同义词，保留原查询的主导地位。"""

        weighted: dict[str, float] = defaultdict(float)
        for term, count in Counter(tokenize(query)).items():
            weighted[term] += float(count)

        normalized_query = re.sub(r"\s+", "", query).lower()
        for triggers, expansion in _QUERY_EXPANSIONS:
            if any(trigger.lower() in normalized_query for trigger in triggers):
                for term in set(tokenize(expansion)):
                    weighted[term] += _EXPANSION_WEIGHT
        return weighted

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
        query_terms = self._query_terms(query)
        if not query_terms:
            return []

        # 按文档过滤：source 是去扩展名的 stem，兼容调用方传带/不带 .md 的名字。
        if doc:
            doc_stem = doc.removesuffix(".md")
            allowed_indices = self._source_indices.get(doc_stem, set())
        else:
            allowed_indices = None
        if doc and not allowed_indices:
            return []

        # 直接复用加载阶段构建的全局倒排索引；查询复杂度取决于命中 posting，
        # 不再随知识库总分块数线性重建索引。
        n = len(self.chunks)
        scores: dict[int, float] = defaultdict(float)
        matched_query_terms: dict[int, set[str]] = defaultdict(set)
        original_terms = set(tokenize(query))
        for term, query_weight in query_terms.items():
            postings = self._index.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for doc_idx, tf in postings:
                if allowed_indices is not None and doc_idx not in allowed_indices:
                    continue
                dl = self._doc_len[doc_idx]
                denom = (
                    tf + _K1 * (1.0 - _B + _B * dl / self._avgdl)
                    if self._avgdl
                    else tf + _K1
                )
                scores[doc_idx] += query_weight * idf * (tf * (_K1 + 1.0)) / denom
                matched_query_terms[doc_idx].add(term)

        if not scores:
            return []

        # 查询词覆盖率作为温和校准项：避免只碰巧命中一个高频词的长段落排到前面。
        original_term_count = max(len(original_terms), 1)
        for doc_idx in scores:
            matched_original = matched_query_terms[doc_idx] & original_terms
            coverage = len(matched_original) / original_term_count
            scores[doc_idx] *= 0.75 + 0.25 * coverage

        ranked = sorted(scores, key=scores.__getitem__, reverse=True)
        results: list[RetrievedChunk] = []
        for idx in ranked:
            # 全局检索时，低于最低相关度阈值视为噪音直接截断（分数降序）。
            # 显式按单篇文档过滤时视为用户明确意图，不套全局阈值；但由于候选分块
            # 来自命中词的 posting，仍不会返回完全不相关的内容。
            if doc is None and scores[idx] <= self.min_score:
                break
            if doc is None:
                matched_original = matched_query_terms[idx] & original_terms
                original_coverage = len(matched_original) / original_term_count
                expansion_matches = len(matched_query_terms[idx] - original_terms)
                if (
                    original_coverage < _MIN_ORIGINAL_QUERY_COVERAGE
                    and expansion_matches < _MIN_EXPANSION_MATCHES
                ):
                    continue
            chunk = self.chunks[idx]
            results.append(
                RetrievedChunk(chunk.source, chunk.heading, chunk.content, scores[idx])
            )
            if len(results) >= limit:
                break
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
