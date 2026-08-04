"""RAG 召回质量测试：标题加权、口语扩展、拒答和精确编号。"""

from pathlib import Path

from infrastructure.knowledge_base import KnowledgeBase


def _write_docs(directory: Path, docs: dict[str, str]) -> KnowledgeBase:
    directory.mkdir()
    for name, content in docs.items():
        (directory / name).write_text(content, encoding="utf-8")
    knowledge_base = KnowledgeBase(directory, top_k=3, min_score=1.0)
    knowledge_base.load()
    return knowledge_base


def test_heading_path_is_indexed_and_weighted(tmp_path: Path) -> None:
    """关键产品名只出现在标题中时，也应优先命中正确价格章节。"""

    knowledge_base = _write_docs(
        tmp_path / "kb",
        {
            "pricing.md": (
                "# 价格与套餐\n\n## 标准版\n每位每月 99 元起，年付享 8 折，适合小团队。"
            ),
            "trial.md": (
                "# 试用说明\n\n## 延期规则\n"
                "标准版试用期为 14 天，到期后可以联系销售申请延期。"
            ),
        },
    )

    results = knowledge_base.retrieve("标准版多少钱")

    assert results
    assert results[0].source == "pricing"
    assert results[0].heading == "价格与套餐 > 标准版"


def test_domain_query_expansion_recalls_colloquial_questions(tmp_path: Path) -> None:
    """正文没有用户原词时，低权重领域同义词仍能召回正确事实。"""

    knowledge_base = _write_docs(
        tmp_path / "kb",
        {
            "pricing.md": "# 商务\n\n## 套餐\n按席位计费，具体报价以套餐页面为准。",
            "security.md": (
                "# 数据安全\n\n## 加密与审计\n"
                "数据传输和存储均加密，并通过权限控制与审计日志追踪访问。"
            ),
        },
    )

    assert knowledge_base.retrieve("你们收费贵不贵")[0].source == "pricing"
    assert knowledge_base.retrieve("客户数据会不会泄露")[0].source == "security"


def test_irrelevant_question_returns_no_results(tmp_path: Path) -> None:
    """只碰巧命中一个常见词时应拒绝召回，避免把无关资料交给模型。"""

    knowledge_base = _write_docs(
        tmp_path / "kb",
        {
            "faq.md": (
                "# 常见问题\n\n## 忘记密码怎么办\n"
                "在登录页面自助重置密码，连续失败会临时锁定账号。"
            )
        },
    )

    assert knowledge_base.retrieve("今天天气怎么样") == []
    assert knowledge_base.retrieve("退款政策是什么") == []


def test_partial_compound_identifier_matches_full_anchor(tmp_path: Path) -> None:
    """省略校验后缀的复合编号仍应命中唯一场景。"""

    knowledge_base = _write_docs(
        tmp_path / "kb",
        {
            "manual.md": (
                "# 部署手册\n\n## 场景一\n"
                "检索锚点 cloudhub-deploy-000008-3c4538a2c775，维护窗口 13 分钟，"
                "故障码 E-DEPLOY-1008。\n\n"
                "## 场景二\n检索锚点 cloudhub-deploy-000009-aaaaaaaaaaaa，"
                "维护窗口 20 分钟，故障码 E-DEPLOY-1009。"
            )
        },
    )

    results = knowledge_base.retrieve("cloudhub-deploy-000008 的维护窗口和故障码")

    assert results
    assert "cloudhub-deploy-000008-3c4538a2c775" in results[0].content


def test_document_filter_uses_prebuilt_source_index(tmp_path: Path) -> None:
    knowledge_base = _write_docs(
        tmp_path / "kb",
        {
            "a.md": "# A 文档\n\n## 配置\n接口每分钟限制 100 次请求。",
            "b.md": "# B 文档\n\n## 配置\n接口每分钟限制 500 次请求。",
        },
    )

    results = knowledge_base.retrieve("接口限制", doc="b.md")

    assert results
    assert {result.source for result in results} == {"b"}
