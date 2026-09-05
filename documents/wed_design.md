你是一名资深 Web Designer、Frontend Engineer 和 Digital Editorial Designer。

现在请为我设计并实现一个网站。你的目标是提炼并重新实现一种：

Editorial Publication × Digital Archive × Independent Media × Digital Humanities

的视觉风格。

一、核心设计理念

网站应该像一个由独立研究者、编辑或文化机构维护的数字档案馆。

整个 UI 必须以内容、排版、信息结构和阅读体验为核心。

二、视觉关键词

Editorial
Archive
Research
Documentation
Independent Publication
Digital Humanities
Chinese Typography
Print-inspired Web Design
Quiet
Serious
Minimal
Text-oriented

三、颜色

使用低饱和、接近纸张和墨水的颜色。

推荐 Design Tokens：

--color-paper: #F5F3EE;
--color-paper-light: #FAF9F6;
--color-ink: #171717;
--color-ink-secondary: #55514B;
--color-ink-muted: #88837B;
--color-line: #C9C5BD;
--color-line-light: #DEDAD2;
--color-accent: #8B2E24;

页面整体以纸张色和黑灰色为主。

Accent color 少量使用。

不要使用渐变。

不要使用鲜艳蓝色、紫色或 SaaS 风格的高饱和颜色。

四、字体

标题优先使用中文衬线字体：

"Noto Serif SC",
"Source Han Serif SC",
"Songti SC",
serif

正文使用：

"Noto Sans SC",
"PingFang SC",
"Microsoft YaHei",
sans-serif

标题应该具有杂志、出版物和书籍标题的感觉。

十七、禁止风格

绝对不要出现：

* SaaS Dashboard 风格
* AI Startup Landing Page 风格
* Material Design 风格
* Bootstrap 风格
* 大量圆角 Card
* 大量 box-shadow
* gradient
* glassmorphism
* 紫蓝色 AI 配色
* 巨型 CTA
* emoji UI
* 大量 icon
* 3D 装饰
* floating blobs
* KPI dashboard
* 彩色卡片网格
* 过度动画

建立自己的 Design Tokens。所有组件都应该遵循统一 Design System。

十九、实现原则

不要为了填充页面而增加不必要的组件。

在开始编码之前，先分析页面的信息架构，并建立完整的 Design System。

然后再实现页面。

如果需要在设计决策之间选择，始终优先：

内容 > 装饰

排版 > 图形

留白 > 卡片

细线 > 阴影

克制 > 炫技

编辑感 > SaaS 感

最后，完成后检查整个页面，确保所有组件共享同一套 typography、spacing、border、color 和 interaction rules，而不是每个区域各自设计。
