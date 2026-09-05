from annotation.domain.artifacts import (
    CalloutNode,
    DocumentSection,
    FormulaNode,
    LearningDocument,
    MarkdownNode,
    QuizNode,
    ReviewIssue,
)


def demo_document() -> LearningDocument:
    return LearningDocument(
        artifact_id="doc-artifact-demo-001",
        document_id="doc-demo-001",
        blueprint_version="bp-fixture-001",
        run_id="run-demo-001",
        version=1,
        status="fixture",
        source_refs=["src-fixture-p1-b1", "src-fixture-p1-b2"],
        created_by="fixture",
        issues=[
            ReviewIssue(
                issue_id="issue-demo-001",
                category="uncertainty",
                severity="warning",
                message="这是占位内容，接入真实教材后需要重新审核。",
                target_id="doc-demo-001",
            )
        ],
        title="极限与连续 · Demo 章节",
        sections=[
            DocumentSection(
                id="sec-intro",
                title="函数极限",
                children=[
                    MarkdownNode(
                        id="md-intro",
                        content="函数极限描述了自变量趋近某一点时，函数值所接近的稳定结果。这里使用 fixture 验证文档渲染链路。",
                        source_refs=["src-fixture-p1-b1"],
                    ),
                    FormulaNode(
                        id="formula-limit",
                        latex=r"\lim_{x \to a} f(x)=L",
                        source_refs=["src-fixture-p1-b2"],
                    ),
                    CalloutNode(
                        id="callout-note",
                        tone="info",
                        title="学习提示",
                        content="先关注‘趋近’与‘取值’的区别，再进入连续性的定义。",
                    ),
                    QuizNode(
                        id="quiz-limit",
                        question="当 x 趋近 a 时，f(x) 趋近 L，这个表达描述的是？",
                        options=["函数极限", "函数定义域", "函数周期"],
                        answer="函数极限",
                        explanation="极限关注趋近过程中的函数值趋势。",
                        source_refs=["src-fixture-p1-b1"],
                    ),
                ],
            )
        ],
    )
