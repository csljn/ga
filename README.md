<div align="center">

# GenericAgent Enhanced

**基于 GenericAgent 的增强版自主 Agent 框架**

*原版 ~3K 行种子代码 + 9 个新功能模块*

<p>
  <a href="https://github.com/csljn/ga"><img src="https://img.shields.io/badge/Fork-Original-181717?style=flat-square&logo=github" alt="Original Repo"/></a>
  <a href="https://github.com/lsdefine/GenericAgent"><img src="https://img.shields.io/badge/Upstream-GenericAgent-blue?style=flat-square&logo=github" alt="Upstream"/></a>
</p>

</div>

---

## 项目简介

这是一个基于 [GenericAgent](https://github.com/lsdefine/GenericAgent) 的增强版本，在原版极简、可自我进化的自主 Agent 框架基础上，新增了 **9 个功能模块**，全面提升多 Agent 协作、任务管理、系统监控等能力。

### 核心特性

| 特性 | 说明 |
|:---|:---|
| 🧬 **自我进化** | 每次任务自动沉淀 Skill，能力随使用持续增长 |
| 🪶 **极简架构** | ~3K 行核心代码，Agent Loop 约百行 |
| ⚡ **强执行力** | 注入真实浏览器，9 个原子工具直接接管系统 |
| 🔌 **高兼容性** | 支持 Claude / Gemini / Kimi / MiniMax 等主流模型 |
| 🆕 **功能增强** | 新增 9 个功能模块，覆盖监控、协作、管理等场景 |

---

## 新增功能模块

### 1. 更新系统 (`frontends/update_app.py`)

版本检测、差异对比、一键更新、冲突自动合并。

```bash
python frontends/update_app.py --port 8901
# 访问 http://127.0.0.1:8901
```

**功能**：
- Git 远程版本检测
- 文件变更列表和代码差异展示
- 一键 git pull 更新
- 本地修改自动 stash/pop
- 冲突检测和强制更新

### 2. 回溯系统 (`frontends/timeline_app.py`)

对话时间线可视化，支持分支 Fork 和多时间线切换。

```bash
python frontends/timeline_app.py --port 8910
# 访问 http://127.0.0.1:8910
```

**功能**：
- 解析 model_responses/ 日志构建时间线
- 消息导航（前进/后退/首/尾）
- 从任意节点 Fork 分支
- 多时间线管理

### 3. 技能系统

技能树可视化 + SOP 在线编辑器。

```bash
# 技能树可视化
python frontends/skill_tree_app.py --port 8901
# 访问 http://127.0.0.1:8901

# SOP 编辑器
python frontends/sop_editor.py --port 8902
# 访问 http://127.0.0.1:8902
```

**功能**：
- 递归扫描 memory/ 目录构建技能树
- SOP 文件语法解析和章节展示
- 在线编辑 Markdown 文件
- 实时预览和搜索过滤

### 4. 蜂巢系统 (`frontends/hive_app.py`)

基于 BBS 的多 Agent 协作系统。

```bash
# 启动 BBS 核心
python frontends/agent_bbs.py --port 58800 --db hive_bbs.db

# 启动管理前端
python frontends/hive_app.py --port 58801 --db hive_bbs.db
# 访问 http://127.0.0.1:58801
```

**功能**：
- 目标发布和管理
- Worker 注册和心跳
- 任务领取和结果验收
- 系统统计和日志

### 5. 吸收系统 (`frontends/morphling_app.py`)

Morphling 项目能力分析与吸收。

```bash
python frontends/morphling_app.py --port 58802
# 访问 http://127.0.0.1:58802
```

**功能**：
- 项目目录扫描和文件分析
- Python AST 解析提取能力
- 能力类型自动识别
- 吸收流程管理（集成/替换/扩展/引用）

### 6. 监控增强 (`frontends/resource_monitor.py`)

系统资源监控 + 成本追踪。

**功能**：
- CPU / 内存 / 磁盘使用率监控
- 历史数据采样和图表
- 与 cost_tracker 集成

### 7. 聊天增强 (`frontends/stapp2.py`)

图片粘贴 + 会话历史管理。

**功能**：
- 图片粘贴支持（base64 编码）
- 会话历史管理
- 增强的 Streamlit UI

### 8. 编排增强 (`frontends/conductor.py`)

DAG 依赖调度 + 编排模板。

**功能**：
- 子任务间前置依赖（depends_on 字段）
- DAG 循环检测
- 编排模板功能

### 9. 待办增强 (`frontends/todo_card.py`)

悬浮任务卡片 + 自动意图识别。

**功能**：
- 交互式任务卡片 UI
- 从对话自动识别意图生成待办
- 一键执行任务
- 拖拽排序

---

## 快速开始

### 环境要求

- Python 3.11 或 3.12（推荐）
- Git

### 安装

```bash
git clone https://github.com/csljn/ga.git
cd ga
pip install -e ".[ui]"
cp mykey_template.py mykey.py  # 填入你的 LLM API Key
```

### 启动

```bash
# 主界面
python launch.pyw

# 或者启动各个功能模块
python frontends/update_app.py      # 更新系统
python frontends/timeline_app.py    # 回溯系统
python frontends/skill_tree_app.py  # 技能树
python frontends/hive_app.py        # 蜂巢系统
python frontends/morphling_app.py   # 吸收系统
```

---

## 项目结构

```
ga/
├── agent_loop.py          # Agent 核心循环（~100行）
├── agentmain.py           # Agent 主入口
├── ga.py                  # GenericAgentHandler
├── llmcore.py             # LLM 调用核心
├── memory/                # 记忆系统（SOP 文件）
│   ├── *.md               # 技能文档
│   └── *.py               # 技能脚本
├── frontends/             # 前端模块
│   ├── update_app.py      # 更新系统
│   ├── timeline_app.py    # 回溯系统
│   ├── skill_tree_app.py  # 技能树
│   ├── sop_editor.py      # SOP 编辑器
│   ├── hive_app.py        # 蜂巢系统
│   ├── morphling_app.py   # 吸收系统
│   ├── resource_monitor.py # 资源监控
│   ├── stapp2.py          # 聊天增强
│   ├── conductor.py       # 编排增强
│   ├── todo_card.py       # 待办增强
│   └── ...                # 其他前端
└── docs/                  # 文档
```

---

## 与原版对比

| 特性 | 原版 GenericAgent | 本增强版 |
|:---|:---:|:---:|
| 核心架构 | ✅ | ✅ |
| 自我进化 | ✅ | ✅ |
| 版本更新系统 | ❌ | ✅ |
| 对话回溯 | ❌ | ✅ |
| 技能树可视化 | ❌ | ✅ |
| 多 Agent 协作 | ❌ | ✅ |
| 项目能力吸收 | ❌ | ✅ |
| 系统资源监控 | ❌ | ✅ |
| DAG 依赖调度 | ❌ | ✅ |

---

## 致谢

- [GenericAgent](https://github.com/lsdefine/GenericAgent) — 原版框架
- [Datawhale](https://datawhalechina.github.io/hello-generic-agent/) — 教程支持

---

## 许可

基于 **MIT License** 发布。

---

## 更新日志

### 2026-06-03

- 新增 9 个功能模块
- 更新系统、回溯系统、技能系统、蜂巢系统、吸收系统
- 监控增强、聊天增强、编排增强、待办增强
- 完善 README 文档
