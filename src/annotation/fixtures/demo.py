from annotation.domain.artifacts import (
    CalloutNode,
    DocumentSection,
    ExampleNode,
    FormulaNode,
    LearningDocument,
    MarkdownNode,
    QuizNode,
    ReviewIssue,
)


def demo_document() -> LearningDocument:
    """Deterministic textbook-shaped offline regression document."""
    return LearningDocument(
        artifact_id="doc-artifact-demo-001",
        document_id="doc-demo-001",
        blueprint_version="bp-fixture-001:v1",
        run_id="run-demo-001",
        version=1,
        status="fixture",
        source_refs=["src-fixture-p1-b1", "src-fixture-p1-b2", "src-fixture-p4-b1"],
        created_by="fixture",
        issues=[ReviewIssue(
            issue_id="issue-demo-001",
            category="uncertainty",
            severity="warning",
            message="这是基于 POC 教材章节结构制作的离线回归样例；发布前仍需对教材原文和公式做人工抽样复核。",
            target_id="doc-demo-001",
        )],
        title="实数系与复数系 · 离线回归样例",
        sections=[
            DocumentSection(
                id="sec-real",
                title="1.1–1.5 实数系的结构",
                children=[
                    MarkdownNode(
                        id="md-real",
                        content="本章从实数系开始。教材把实数视为满足域公理、序公理和完全公理的对象，并用这些性质建立后续数学分析的基础。学习时要区分“如何构造实数”和“实数满足哪些性质”：本章主要关注后者。",
                        source_refs=["src-fixture-p1-b1"],
                    ),
                    FormulaNode(id="formula-order", latex=r"x<y \Rightarrow x+z<y+z", source_refs=["src-fixture-p1-b2"]),
                    CalloutNode(
                        id="callout-note", tone="info", title="学习提示",
                        content="先掌握集合、子集、区间和有序关系，再进入上界、上确界（记作 $\\sup S$）与完备性。它们是后面极限理论的语言基础。",
                    ),
                    ExampleNode(
                        id="example-interval", title="例：区间的端点",
                        problem="说明开区间 $(a,b)$ 与闭区间 $[a,b]$ 的区别。",
                        solution="$(a,b)$ 只包含满足 $a<x<b$ 的点，不包含端点；$[a,b]$ 包含满足 $a≤x≤b$ 的全部点。半开区间只包含其中一个端点。",
                        source_refs=["src-fixture-p1-b2"],
                    ),
                ],
            ),
            DocumentSection(
                id="sec-completeness", title="1.8–1.17 有理数、无理数与完全性",
                children=[
                    MarkdownNode(
                        id="md-completeness",
                        content="有理数可以写成整数之商；非有理数的实数称为无理数。教材通过上界、最大元和最小上界引出完全公理：非空且有上界的实数集具有最小上界。这个性质解释了为什么实数轴没有‘空隙’，也是许多极限存在性结论的基础。",
                        source_refs=["src-fixture-p3-b1", "src-fixture-p4-b1"],
                    ),
                    FormulaNode(
                        id="formula-supremum",
                        latex=r"\alpha=\sup S \iff (\forall x\in S,\ x\le\alpha)\land(\forall\varepsilon>0,\ \exists x\in S,\ \alpha-\varepsilon<x)",
                        source_refs=["src-fixture-p4-b1"],
                    ),
                    ExampleNode(
                        id="example-supremum", title="例：最大元不等于上确界",
                        problem="比较 $S=[0,1]$ 与 $S=[0,1)$ 的最大元和上确界。",
                        solution="$[0,1]$ 的最大元和上确界都是 $1$；$[0,1)$ 没有最大元，但上确界仍为 $1$。上确界是所有上界中最小的，不要求它属于集合。",
                        source_refs=["src-fixture-p4-b1"],
                    ),
                ],
            ),
            DocumentSection(
                id="sec-complex", title="1.21–1.29 复数与复平面",
                children=[
                    MarkdownNode(
                        id="md-complex",
                        content="复数可写成 $z=x+iy$，其中 $x$、$y$ 为实数，$i$ 为满足 $i^2=-1$ 的虚数单位。复数可以在复平面上表示为点或向量，因此加法对应分量相加，绝对值对应从原点到该点的距离。教材特别指出，复数不能按实数那样建立同时满足序公理的全序。",
                        source_refs=["src-fixture-p13-b1", "src-fixture-p14-b1"],
                    ),
                    FormulaNode(
                        id="formula-complex-modulus",
                        latex=r"z=x+iy,\qquad |z|=\sqrt{x^2+y^2},\qquad z\overline z=|z|^2",
                        source_refs=["src-fixture-p15-b1"],
                    ),
                    ExampleNode(
                        id="example-complex", title="例：复数的几何意义",
                        problem="写出 $z=3+4i$ 的实部、虚部和绝对值。",
                        solution="实部为 $3$，虚部为 $4$；在复平面上对应点 $(3,4)$，所以 $|z|=\\sqrt{3^2+4^2}=5$。",
                        source_refs=["src-fixture-p14-b1", "src-fixture-p15-b1"],
                    ),
                    QuizNode(
                        id="quiz-complex",
                        question="关于集合 $S=[0,1)$，下列说法正确的是？",
                        options=["$1$ 是最大元", "$S$ 没有上界", "$\\sup S=1$ 但 $S$ 没有最大元"],
                        answer="$\\sup S=1$ 但 $S$ 没有最大元",
                        explanation="$1$ 是上界但不属于 $S$，因此不是最大元；所有元素都不超过 $1$，且可以任意逼近 $1$。",
                        source_refs=["src-fixture-p4-b1"],
                    ),
                ],
            ),
        ],
    )
