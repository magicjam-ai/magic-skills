---
name: llm-wiki
description: "Karpathy 风格 LLM Wiki — 在任意 Markdown 目录里增量编译交叉链接的知识库。触发：'建个 wiki'、'初始化 wiki'、'编入 [源]'、'查 wiki'、'lint wiki'，或用户引用他们的 wiki/knowledge base/notes。基于 https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f，改造自 NousResearch/hermes-agent 的 llm-wiki skill 以在 Claude Code 里原生执行。"
version: 1.0.0
author: magic-skills (adapted from NousResearch/hermes-agent llm-wiki 2.1.0 for Claude Code)
license: MIT
---

# Karpathy's LLM Wiki（Claude Code 版）

在用户指定的目录里维护一个**持续编译、交叉链接**的 Markdown 知识库。

与 RAG 每次查询都从零检索不同，本 wiki 把知识**编译一次**并持续更新。交叉引用已经就位，矛盾已经被标注，综述反映了所有已摄入内容。

**分工**：人类负责选源和指定分析方向；Agent 负责总结、交叉引用、归档、保持一致性。

---

## Claude Code 工具映射（重要）

本 skill 源自 Hermes Agent 的 codex CLI 版本。在 Claude Code 里用以下原生工具实现：

| Hermes 原操作 | Claude Code 工具 |
|---|---|
| `read_file` | `Read` |
| `search_files "<pattern>" path=... file_glob="*.md"` | `Grep pattern="..." path="..." glob="*.md"` |
| `search_files "*.md" target="files"` | `Glob pattern="**/*.md"` |
| `web_extract <url>` | `WebFetch url=... prompt="返回正文 Markdown"` |
| `execute_code`（Python / Bash 扫描） | `Bash`（git-bash 环境，支持 `sha256sum`、`python`、`grep`、`awk`） |
| 写文件 | `Write`（新建） / `Edit`（精修） |
| 计划管理 | `TodoWrite` |

**路径约定**：
- 始终使用相对于项目根目录的路径，或显式绝对路径；Windows Bash 下用 forward-slash
- 若项目存在 `CLAUDE.md`，先读它再下笔——很多仓库/vault 对"能在哪里建目录"有硬约束（Obsidian vault 常禁止在根新建顶级目录；建议把 wiki 放在已有的 `others/<name>/`、`docs/<name>/`、或项目子目录之下）
- 在 Obsidian vault 里回显文件引用时用 wikilink 格式 `[[相对路径.md]]`；纯代码仓库用普通路径即可

---

## 何时触发

- 用户要**创建/初始化** wiki 或 knowledge base
- 用户要**编入**（ingest）某个源（URL / PDF / 粘贴文本 / 路径）到 wiki
- 用户提问，且存在已配置的 wiki
- 用户要**lint / 体检 / 审计** wiki
- 用户在研究上下文里引用他们的 wiki / knowledge base / notes

---

## Wiki 位置

Wiki 路径**必须显式指定**（没有环境变量）。支持三种指定方式，按优先级：

1. 用户在当前对话里明确给出（例：'建 wiki 在 `docs/research-wiki/`'）
2. `<wiki-root>/.wiki-config` 文件内的 `wiki_path:` 字段
3. 若都没有，**询问用户**，并给出默认建议 `wiki/` 或 `docs/wiki/`

⚠️ **绝不**未经用户确认就把 wiki 根建在已有的生产知识库目录、项目关键路径或 vault 根下。若项目里已有同名/相似的知识库目录（例如 Obsidian vault 的 `_wiki/`），先读它的 schema、遵循其约定，不要套用本模板。

---

## 架构：三层

```
<wiki-root>/
├── SCHEMA.md           # 约定、结构规则、领域配置
├── index.md            # 分节内容目录，每行一个摘要
├── log.md              # 时间线操作日志（append-only，满 500 条轮转）
├── raw/                # Layer 1：不可变原始材料
│   ├── articles/       # 网页剪藏、公众号文章
│   ├── papers/         # PDF、arxiv 论文
│   ├── transcripts/    # 会议纪要、访谈
│   └── assets/         # 源引用到的图片、图表
├── entities/           # Layer 2：实体页（人物、组织、产品、模型）
├── concepts/           # Layer 2：概念/主题页
├── comparisons/        # Layer 2：对比分析
└── queries/            # Layer 2：值得保留的查询结果
```

- **Layer 1 — raw/**：只读。Agent 读但永不修改。
- **Layer 2 — wiki 页面**：Agent 创建、更新、交叉引用。
- **Layer 3 — SCHEMA.md**：定义结构、约定、标签分类法。

**raw 目录可外挂**：`.wiki-config` 可以指定 `raw_source:` 指向 wiki 之外的目录（例如 Obsidian vault 的 inbox、团队共享的文档目录、或某个爬虫的输出目录）。Agent 只读、不拷贝、不修改该外部目录。

**外部 raw 源的特殊规则**：
- `sources:` frontmatter 使用**项目相对路径**（如 `inbox/articles/article.md`），不用 `raw/...` 前缀
- **跳过 sha256 drift 检查**——外部源文件通常已有自己的 frontmatter 或元数据（id、URL、导出时间），不要给它们注入 sha256
- 想追溯内容变化时，靠外部源自身的 id/URL + 修改时间即可
- `^[...]` provenance markers 同样用项目相对路径

**可移动外部源（movable raw source）**：若外部 raw 源是 inbox/缓冲目录（如 Obsidian 的 `00_Inbox/`，内容消费后无需保留原位置），ingest 完成后应将源文件**移入 `raw/articles/`** 并更新所有受影响的 wiki 页的 `sources:` frontmatter。这使 wiki 逐步从"外部引用"过渡到"自包含"模式——raw/ 最终持有所有已编译源，inbox 保持清爽。操作规则：

- **只移动被 wiki `sources:` 实际引用的文件**，不要批量移动 inbox 全部内容
- 移动后更新所有 wiki 页面的 frontmatter（`00_Inbox/...` → `raw/articles/...`）
- `.wiki-config` 同步更新：移除已清空的 `raw_source:` 条目，或添加注释说明 raw/ 已自包含
- `log.md` 记录迁移清单
- 活跃项目文件（如 `30_项目/` 下的方案设计、产品文档）**不移入 raw/**——它们不是消费缓冲，需要在原位置继续编辑

---

## 恢复已有 wiki（CRITICAL — 每个会话开始都做）

当用户已有 wiki 时，**动手之前必先定位**：

① **Read SCHEMA.md** — 理解领域、约定、标签分类
② **Read index.md** — 了解已有页面及其摘要
③ **Scan log.md 末尾 20–30 行** — 理解最近活动

```
Read file_path="<wiki-root>/SCHEMA.md"
Read file_path="<wiki-root>/index.md"
Read file_path="<wiki-root>/log.md"   # 读尾部；必要时用 offset
```

只有在定位完成后才能 ingest / query / lint。跳过定位会导致：
- 为已存在的实体重复建页
- 错过已有内容的交叉引用
- 违反 schema 约定
- 重做日志里已记录的工作

对于 100+ 页的 wiki，在新建任何页前用 `Grep` 搜一下当前主题。

---

## 初始化新 wiki

当用户要创建 wiki 时：

1. 确定 wiki 路径（对话 / `.wiki-config` / 询问用户）
2. 用 Bash 创建目录结构：
   ```bash
   mkdir -p "<wiki-root>"/{raw/{articles,papers,transcripts,assets},entities,concepts,comparisons,queries}
   ```
3. 询问用户 wiki 覆盖的**领域**——要具体
4. 若用户指定了外部 raw 源（项目里某个只读目录），写 `.wiki-config`：
   ```yaml
   wiki_path: docs/research-wiki
   raw_source: inbox         # 可选，外部只读源（相对项目根）
   ```
5. Write `SCHEMA.md`（基于下方模板，按领域定制）
6. Write 初始 `index.md`
7. Write 初始 `log.md`，含创建条目
8. 向用户确认 wiki 就绪，并建议首批要摄入的源

### SCHEMA.md 模板

```markdown
# Wiki Schema

## Domain
[本 wiki 覆盖什么——如 "AI/ML 研究"、"个人健康"、"创业情报"]

## Conventions
- 文件名：小写、连字符、无空格（如 `transformer-architecture.md`）
- 每个 wiki 页面必须以 YAML frontmatter 开头（见下）
- 使用 `[[wikilinks]]` 在页面间链接（每页**至少 2 条**出链）
- 更新页面时必须 bump `updated` 日期
- 每个新页面必须加入 `index.md` 对应 section
- 每次操作必须追加 `log.md`
- **Provenance markers**：综合 3+ 源的页面，在段落末尾加 `^[raw/articles/source-file.md]`
  以便读者追溯单条断言。单源页面靠 frontmatter 的 `sources:` 即可，可选。

## Frontmatter
  ```yaml
  ---
  title: Page Title
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  type: entity | concept | comparison | query | summary
  tags: [from taxonomy below]
  sources: [raw/articles/source-name.md]
  # Optional quality signals:
  confidence: high | medium | low
  contested: true
  contradictions: [other-page-slug]
  ---
  ```

### raw/ Frontmatter

Raw sources 也要有小型 frontmatter，便于 re-ingest 侦测漂移：

```yaml
---
source_url: https://example.com/article
ingested: YYYY-MM-DD
sha256: <body 部分的 hex digest>
---
```

`sha256` 只覆盖 frontmatter 之后的正文（不含 frontmatter 本身）。

## Tag Taxonomy
[定义 10–20 个领域顶级标签。新增标签必须先在此登记再使用。]

示例（AI/ML）：
- Models: model, architecture, benchmark, training
- People/Orgs: person, company, lab, open-source
- Techniques: optimization, fine-tuning, inference, alignment, data
- Meta: comparison, timeline, controversy, prediction

规则：页面上每个标签都必须出现在本分类表里。这防止标签膨胀。

## Page Thresholds
- **新建页**：实体/概念在 2+ 源中出现，或在单源中居于中心
- **追加到已有页**：源提到已覆盖过的内容
- **不建页**：一笔带过、次要细节、领域外的内容
- **拆分页**：超过 ~200 行时，按子主题拆分并交叉链接
- **归档页**：内容被完全取代时，移到 `_archive/`，从 index 移除

## Entity / Concept / Comparison Pages
- **Entity**：overview、关键事实/日期、与其他实体的关系（wikilinks）、源引用
- **Concept**：定义、当前知识状态、开放问题/争议、相关概念、源
- **Comparison**：对比对象与动机、对比维度（优先表格）、判定/综合、源

## Update Policy
新信息与旧内容冲突时：
1. 比日期——新源通常覆盖旧源
2. 若真有矛盾，标注双方立场、日期、源
3. frontmatter 里标 `contradictions: [page-name]`
4. lint 报告里 flag 以供用户 review
```

### index.md 模板

```markdown
# Wiki Index

> 内容目录。每个 wiki 页面按类型列出并给一句话摘要。
> 查询前先读这个文件。
> Last updated: YYYY-MM-DD | Total pages: N

## Entities
<!-- 节内按字母序 -->

## Concepts

## Comparisons

## Queries
```

**扩展规则**：单个 section 超过 50 项时按首字母/子域拆分；index 总量超过 200 时另建 `_meta/topic-map.md`。

### log.md 模板

```markdown
# Wiki Log

> 所有 wiki 操作的时间线记录。Append-only。
> 格式：`## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> 满 500 条时轮转：重命名为 log-YYYY.md，开新文件。

## [YYYY-MM-DD] create | Wiki initialized
- Domain: [领域]
- Structure created with SCHEMA.md, index.md, log.md
```

---

## 核心操作

### 1. Ingest（编入）

当用户给出一个源（URL / 文件 / 粘贴内容 / 外部 raw 目录里的某篇）时：

#### ① 捕获原始源

- **URL** → `WebFetch` 抓取正文 Markdown → Write 到 `<wiki-root>/raw/articles/<slug>.md`
- **PDF** → 若文件已在项目里，用 `Read file_path="..."` 读取原文；产出摘要级 raw 文件放 `raw/papers/`
- **粘贴文本** → Write 到对应 `raw/` 子目录
- **外部 raw 源**（`.wiki-config` 里配置的 `raw_source:`） → 直接引用其路径作为 `sources:`，不拷贝、不修改
- 文件命名描述性：`raw/articles/karpathy-llm-wiki-2026.md`
- **Raw frontmatter** 必填 `source_url`、`ingested`、`sha256`。计算 sha256（body 部分）：
  ```bash
  # 仅对 body（--- 之后的内容）计算
  awk '/^---$/{c++; next} c>=2' <file> | sha256sum | awk '{print $1}'
  ```
  Re-ingest 同 URL 时：重算 sha256，相同则跳过；不同则 flag drift 并更新。

#### ② 讨论要点

与用户讨论：有意思的点、对领域的意义。自动化/批处理场景下跳过本步。

#### ③ 检查已有内容

搜 index.md 和全 wiki，找源中提到的实体/概念的已有页：
```
Read file_path="<wiki-root>/index.md"
Grep pattern="<EntityName>" path="<wiki-root>" glob="*.md"
```

这一步是"增长的 wiki"与"重复页大杂烩"的分水岭。

#### ④ 写/更新 wiki 页

- **新实体/概念**：只在符合 SCHEMA Page Thresholds 时建页（2+ 源提及，或单源中心）
- **已有页**：追加新信息、更新事实、bump `updated`。冲突时走 Update Policy。
- **交叉引用**：每个新/更新页至少 2 条 `[[wikilinks]]` 出链。检查已有页是否需要回链。
- **标签**：仅用 SCHEMA 分类表内的标签。
- **Provenance**：综合 3+ 源的页在段尾加 `^[raw/articles/source.md]`。
- **Confidence**：观点性/快速变化/单源断言设 `medium` 或 `low`。多源佐证才可 `high`。

#### ⑤ 更新导航

- `index.md` 对应 section 按字母序插入新页
- 更新 index header 的 "Total pages" 和 "Last updated"
- `log.md` append：`## [YYYY-MM-DD] ingest | Source Title`，列出所有新建/更新的文件
- 用 `Edit`（不是 Write）做 index/log 的增量更新，避免全文重写丢数据

#### ⑥ 报告变更

列出每个新建/修改的文件给用户，用 wikilink 格式。

> 单个源触发 5–15 个 wiki 页的更新是**正常且期望**的——这就是复利效应。

#### ⑦ 移入 raw（可移动外部源场景）

若源来自可移动 inbox 目录（见上文"可移动外部源"），ingest 完成后执行：

- 将源文件从原路径**移入** `<wiki-root>/raw/articles/<filename>.md`
- 用 `Edit`（或批量 Python 脚本）更新所有受影响的 wiki 页面的 `sources:` frontmatter（原路径 → `raw/articles/...`）
- 若原外部目录被清空，更新 `.wiki-config` 移除或注释对应的 `raw_source:` 条目
- `log.md` 追加迁移记录：`## [YYYY-MM-DD] migrate | N 个源从 <原路径> 移入 raw/articles/`

### 2. Query（查询）

当用户提问 wiki 领域内的问题：

① Read `index.md` 识别相关页
② 100+ 页时还要 `Grep` 搜关键词——index 单靠 summary 可能漏
③ Read 相关页面
④ 基于编译过的知识合成回答，引用来源："基于 [[page-a]] 和 [[page-b]]……"
⑤ **值得归档的答案** → 建页到 `queries/` 或 `comparisons/`。琐碎查询不存。
  - **归档 query 时必须加一条反向链接**：从 2–3 个被综合的源概念/实体页里选一个最相关的，在其 "相关" 节追加 `[[<new-query-slug>]]`。否则 query 页是孤立叶子，未来读者从 concept 追不过去。
  - Lint `[3b]` 专门检查 query 页的 back-link 情况。
⑥ 更新 `log.md`：`## [YYYY-MM-DD] query | <问题>`（若归档则注明）

### 3. Lint（体检）

用户要 lint / 审计 wiki 时，**按下列检查清单**跑；优先用 Bash 脚本跑扫描，Agent 消化结果：

① **孤立页**：无任何入链的页面
② **死链**：`[[wikilinks]]` 指向不存在的页
③ **Index 完整性**：文件系统中的每个 wiki 页都应出现在 index.md
④ **Frontmatter 校验**：必填字段齐全（title/created/updated/type/tags/sources），标签在 taxonomy 里
⑤ **陈旧内容**：`updated` 比最近相关源旧 >90 天
⑥ **矛盾**：同主题不同事实；列出所有 `contested: true` 或有 `contradictions:` 的页
⑦ **质量信号**：列出 `confidence: low` 和单源但无 confidence 字段的页
⑧ **Source drift**：重算 raw/ 中每个文件的 sha256，比对 frontmatter，不同则 flag
⑨ **页面大小**：>200 行的候选拆分
⑩ **标签审计**：列所有在用标签，flag 不在 taxonomy 的
⑪ **日志轮转**：`log.md` 超过 500 条则轮转为 `log-YYYY.md`
⑫ **报告**：按严重度分组（死链 > 孤立 > drift > 矛盾 > 陈旧 > 样式）
⑬ `log.md` append：`## [YYYY-MM-DD] lint | N issues found`

参考脚本见 `scripts/lint.py`（相对本 skill 目录）。调用方式：

```bash
python3 ~/.claude/skills/llm-wiki/scripts/lint.py <wiki-root>
# 例：python3 ~/.claude/skills/llm-wiki/scripts/lint.py docs/research-wiki
```

该脚本**只读扫描**，输出人类可读的分类报告；Agent 应读取输出、按严重度（死链 > 孤立 > drift > 矛盾 > 陈旧 > 样式）重新组织后汇报给用户，并 append log.md 条目。

---

## 常用查找

```bash
# 按内容查页
Grep pattern="transformer" path="<wiki-root>" glob="*.md"

# 按文件名查
Glob pattern="<wiki-root>/**/*.md"

# 按标签查
Grep pattern="^tags:.*alignment" path="<wiki-root>" glob="*.md"

# 最近活动
Read file_path="<wiki-root>/log.md"    # 必要时用 offset
```

## 批量 ingest

一次给多个源时，批处理更新：
1. 先全部读入
2. 跨源识别所有实体/概念
3. **一次性** Grep 现有页（避免 N 次扫描）
4. 一次性创建/更新页（避免重复写）
5. 末尾**一次**更新 index.md
6. **单条** log 条目覆盖整批

## 归档

内容被完全取代时：
1. 需要时建 `_archive/`
2. 移动页到 `_archive/<原子路径>`
3. 从 index.md 移除
4. 更新指向它的页：wikilink 换成纯文本 + "(archived)"
5. log 记录

## Obsidian 集成（可选）

若 wiki 位于 Obsidian vault 里：
- `[[wikilinks]]` 原生渲染
- Graph View 可视化
- YAML frontmatter 驱动 Dataview
- 图片 `![[image.png]]` 放 `raw/assets/`

**不要碰** `.obsidian/`；若 vault 已有生产知识库目录（如 `_wiki/`），除非用户明确指定，否则别往那写——那通常有独立 schema。

---

## Pitfalls（坑）

- **永不修改 raw/**——源是不可变的。修正在 wiki 页里做。
- **永远先定位**——新会话里先读 SCHEMA + index + 最近 log。跳过必然导致重复与漏引。
- **永远更新 index.md 和 log.md**——跳过就是让 wiki 退化。这是导航主干。
- **不为一笔带过建页**——遵守 Page Thresholds。脚注里出现一次的名字不值得一个实体页。
- **不建无交叉引用的页**——孤立页不可见。每页至少 2 条出链。
- **Frontmatter 必填**——启用搜索、过滤、陈旧检测。
- **标签必须来自 taxonomy**——自由标签会退化为噪音。先加到 SCHEMA.md 再用。
- **保持可扫**——每页应 30 秒读完。>200 行拆分，细节挪深挖专页。
- **大规模改动前确认**——ingest 会触达 10+ 已有页时先问用户。
- **轮转日志**——log 超 500 条时重命名为 log-YYYY.md 开新。lint 时检查。
- **显式处理矛盾**——别默默覆盖。记录双方立场/日期、frontmatter 标记、lint 报告 flag。

---

## 首次试用建议

没有既有 wiki 的用户，建议这样起步：

1. 选一个明确的领域（例："AI Agent 工程与工具生态"、"家装材料研究"、"竞品情报"），领域越窄越好起步
2. 挑一个不碍事的试用目录：`docs/wiki-test/`、`others/llm-wiki/`、或在 Obsidian vault 里用 `others/llm-wiki/`
3. 先备 3–5 篇有**主题重叠**的源（单篇源难触发 "2+ 源才建页" 阈值，重叠才能把交叉引用跑起来）
4. 跑一次完整循环：初始化 → 编入 3–5 篇 → `lint wiki <path>` → 看是否产生了合理的交叉链接
5. 满意后再做成日常习惯

若项目里已存在生产知识库目录，本 skill **默认不触碰**——除非用户显式说"在 `<path>` 里 ingest"，并已确认与该目录的 schema 兼容。

---

## 附：快速清单

**New session, existing wiki：**
```
1. Read <wiki>/SCHEMA.md
2. Read <wiki>/index.md
3. Read tail of <wiki>/log.md
```

**Ingest one URL：**
```
1. WebFetch → raw/articles/<slug>.md（含 source_url/ingested/sha256 frontmatter）
2. Grep wiki for 源中提到的实体/概念
3. 建/更 2–15 个 wiki 页，注意交叉链接
4. Edit index.md 加新页、bump header
5. Edit log.md append 条目
6. 用 wikilink 报告变更
7. 若源来自可移动 inbox → 移入 raw/articles/ 并更新 sources: frontmatter
```

**Lint：**
```
1. Glob 全部 *.md
2. 扫孤立/死链/frontmatter/标签/大小
3. 分严重度报告
4. Edit log.md append 条目
```
