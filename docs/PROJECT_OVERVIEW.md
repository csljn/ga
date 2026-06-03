# GenericAgent 项目全面解析

## 项目概述

GenericAgent 是一个**极简自主 Agent 框架**，其核心理念是"不预设技能，靠进化获得能力"。整个框架仅约 **3K 行代码**，通过 9 个原子工具和约 100 行 Agent Loop 赋予 LLM 系统级控制能力。项目支持多种主流 LLM 模型（Claude、Gemini、Kimi、MiniMax、DeepSeek 等）和多种前端界面（桌面 GUI、终端 TUI、Streamlit UI、IM 机器人等），是一个高度模块化、可扩展的自主 Agent 系统。

### 核心设计理念

1. **自我进化**：每次任务自动沉淀 Skill，能力随使用持续增长，形成专属技能树
2. **极简核心**：最小化代码复杂度，最大化可扩展性
3. **多模型支持**：不绑定特定 LLM，支持故障转移和混合调度
4. **全栈控制**：通过原子工具实现代码执行、文件操作、网页浏览等系统级控制

## 核心特性

### 1. 自我进化机制
- **Skill 积累**：每次任务完成后自动总结并存储为 Skill
- **技能树成长**：能力随使用时间自然增长，无需手动编程
- **经验沉淀**：成功和失败经验都会被记录，避免重复错误

### 2. 分层记忆系统
采用 **L0-L4 五层记忆架构**：
- **L0（元规则）**：核心行为准则，不可修改
- **L1（记忆索引）**：全局记忆的索引和导航
- **L2（全局事实）**：长期有效的客观事实
- **L3（任务 Skills/SOPs）**：任务级技能和标准操作流程
- **L4（会话归档）**：历史会话的压缩存档

### 3. 多模型故障转移
- **MixinSession 机制**：支持多模型自动切换
- **弹簧回弹**：主模型恢复后自动切回
- **负载均衡**：可根据任务类型选择最适合的模型

### 4. 多前端支持
- **桌面 GUI**：基于 pywebview 的原生桌面应用
- **终端 TUI**：基于 Textual 的终端用户界面
- **Streamlit UI**：基于 Streamlit 的 Web 界面
- **IM 机器人**：支持微信、QQ、飞书、钉钉、Telegram 等平台

### 5. 子 Agent 编排
- **Conductor 系统**：支持派发、监督、自动清理并行子 Agent
- **任务分解**：复杂任务自动分解为子任务
- **并行执行**：多个子 Agent 可同时工作

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户接口层                            │
├─────────┬─────────┬─────────┬─────────┬─────────┬───────┤
│ Desktop │  TUI    │Streamlit│  Web    │   IM    │  CLI  │
│  GUI    │  v2    │   UI    │  UI    │  Bots   │       │
└─────────┴─────────┴─────────┴─────────┴─────────┴───────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   Agent 核心层                           │
├─────────────────────────────────────────────────────────┤
│  agentmain.py (GenericAgent)                            │
│  ├── LLM 会话管理                                       │
│  ├── 任务队列                                           │
│  └── Agent 循环启动                                     │
├─────────────────────────────────────────────────────────┤
│  agent_loop.py (agent_runner_loop)                      │
│  ├── 感知环境状态                                       │
│  ├── 任务推理                                           │
│  ├── 工具调用                                           │
│  └── 经验写入记忆                                       │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   工具执行层                             │
├─────────────────────────────────────────────────────────┤
│  ga.py (GenericAgentHandler)                            │
│  ├── code_run: 代码执行器                               │
│  ├── file_read: 文件读取                                │
│  ├── file_write: 文件写入                               │
│  ├── file_patch: 文件修改                               │
│  ├── web_scan: 网页扫描                                 │
│  ├── web_execute_js: JS 执行                            │
│  ├── ask_user: 用户交互                                 │
│  ├── update_working_checkpoint: 工作检查点更新          │
│  └── start_long_term_update: 长期更新启动              │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   LLM 服务层                             │
├─────────────────────────────────────────────────────────┤
│  llmcore.py                                             │
│  ├── ClaudeSession: Claude 会话                         │
│  ├── LLMSession: 通用 LLM 会话                         │
│  ├── NativeClaudeSession: 原生 Claude 会话             │
│  ├── NativeOAISession: 原生 OpenAI 会话                │
│  ├── MixinSession: 多模型故障转移                      │
│  └── ToolClient/NativeToolClient: 工具调用协议         │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   记忆与进化层                           │
├─────────────────────────────────────────────────────────┤
│  memory/                                                │
│  ├── L0: 元规则 (不可修改)                              │
│  ├── L1: 记忆索引                                       │
│  ├── L2: 全局事实                                       │
│  ├── L3: 任务 Skills/SOPs                              │
│  └── L4: 会话归档                                       │
│  reflect/                                               │
│  ├── scheduler.py: 定时任务调度                         │
│  └── autonomous.py: 自主运行反射                        │
└─────────────────────────────────────────────────────────┘
```

### 核心模块详解

#### 1. agentmain.py - 主入口
**GenericAgent 类**是整个系统的核心，负责：
- **LLM 会话管理**：加载、切换、故障转移
- **任务队列**：接收用户任务，排队执行
- **Agent 循环启动**：调用 agent_loop 执行任务

**重要方法**：
- `__init__`: 初始化配置、加载 LLM 会话
- `load_llm_sessions`: 加载所有配置的 LLM 模型
- `next_llm`: 获取下一个可用的 LLM（支持故障转移）
- `put_task`: 添加任务到队列
- `run`: 启动 Agent 循环

**运行模式**：
- **CLI 交互**：直接运行进入交互模式
- **一次性任务**：`--task` 参数执行单个任务
- **反射模式**：`--reflect` 启动自主反思

#### 2. agent_loop.py - Agent 循环
`agent_runner_loop` 函数是整个系统的核心，仅约 **100 行代码**实现了完整的 Agent 循环：

```python
def agent_runner_loop(client, system_prompt, user_input, handler, tools_schema, max_turns=40):
    # 1. 初始化对话
    # 2. 循环执行：
    #    a. 感知环境状态
    #    b. 任务推理
    #    c. 调用工具执行
    #    d. 经验写入记忆
    #    e. 检查是否完成
    # 3. 返回结果
```

**BaseHandler 类**：工具分发基类，支持 `do_` 前缀方法自动映射，实现工具调用的动态路由。

**StepOutcome 数据类**：封装工具执行结果，包含内容、图片、文件等。

#### 3. llmcore.py - LLM 核心
**多会话类型支持**：
- **ClaudeSession**：Anthropic Claude 专用会话
- **LLMSession**：通用 LLM 会话（支持 OpenAI 兼容接口）
- **NativeClaudeSession**：原生 Claude 工具调用格式
- **NativeOAISession**：原生 OpenAI 工具调用格式

**MixinSession 类**：多模型故障转移机制
- 支持配置多个 LLM 模型
- 自动检测模型可用性
- 主模型故障时自动切换
- 主模型恢复后自动切回（弹簧回弹）

**工具调用协议**：
- **ToolClient**：通用工具调用客户端
- **NativeToolClient**：原生工具调用格式（适合较弱模型）

**重要函数**：
- `compress_history_tags`: 压缩历史标签，减少 token 消耗
- `trim_messages_history`: 裁剪历史消息，保持上下文窗口
- `auto_make_url`: 自动拼接 API URL

#### 4. ga.py - 工具实现层
**GenericAgentHandler 类**实现所有 9 个原子工具：

1. **code_run**：安全代码执行器
   - 支持 Python、Shell 命令
   - 沙箱环境执行
   - 超时控制和错误处理

2. **file_read**：文件读取
   - 支持文本和二进制文件
   - 自动编码检测
   - 大文件分块读取

3. **file_write**：文件写入
   - 支持创建和覆盖
   - 自动目录创建
   - 编码处理

4. **file_patch**：文件修改
   - 支持精确替换
   - 上下文匹配
   - 多处修改支持

5. **web_scan**：网页扫描
   - 基于 TMWebDriver 和 simphtml
   - 支持 JavaScript 渲染
   - 内容提取和解析

6. **web_execute_js**：JavaScript 执行
   - 浏览器环境执行
   - DOM 操作支持
   - 异步脚本处理

7. **ask_user**：用户交互
   - 暂停执行等待用户输入
   - 支持多种输入类型
   - 超时处理

8. **update_working_checkpoint**：工作检查点更新
   - 保存当前工作状态
   - 支持断点续传
   - 状态序列化

9. **start_long_term_update**：长期更新启动
   - 启动后台任务
   - 进度跟踪
   - 完成通知

### 记忆系统详解

#### 核心公理
1. **行动验证原则**：只有经过验证的信息才能进入记忆
2. **神圣不可删改性**：L0 规则不可修改，其他层只能追加
3. **禁止存储易变状态**：只存储稳定、长期有效的信息
4. **最小充分指针**：用最小信息指向最大知识

#### 各层职责

**L0（元规则）**
- 核心行为准则
- 不可修改，只读
- 定义 Agent 的基本行为模式

**L1（记忆索引）**
- 全局记忆的导航地图
- 指向 L2、L3、L4 的具体内容
- 支持快速检索

**L2（全局事实）**
- 长期有效的客观事实
- 用户偏好、环境配置
- 项目特定知识

**L3（任务 Skills/SOPs）**
- 任务级技能记录
- 标准操作流程
- 成功和失败经验

**L4（会话归档）**
- 历史会话的压缩存档
- 定时归档（每 12 小时）
- 支持回溯和参考

#### 记忆管理 SOP
- **信息分类快速决策树**：快速判断信息应存储在哪一层
- **搜索先行**：使用前先搜索现有记忆
- **交叉验证**：重要信息需要多源验证
- **编码安全**：敏感信息加密存储
- **闭环原则**：每个任务都要有完整的记录

### 自我进化机制

#### 进化循环
```
用户任务 → 执行 → 结果评估 → 经验提取 → Skill 生成 → 记忆存储
    ↑                                                  ↓
    └──────────────── 能力增长 ←────────────────────────┘
```

#### Skill 生成
1. **任务完成**：成功完成用户任务
2. **经验提取**：从执行过程中提取关键经验
3. **模式识别**：识别可复用的模式
4. **Skill 封装**：封装为标准 Skill 格式
5. **记忆存储**：存入 L3 层供未来使用

#### 能力增长
- **显式增长**：用户明确要求学习新技能
- **隐式增长**：每次任务自动积累经验
- **失败学习**：从失败中提取教训
- **成功复用**：成功经验被多次应用

### 前端系统

#### 1. 桌面 GUI (frontends/desktop.py)
- **技术栈**：pywebview + HTML/CSS/JS
- **特性**：
  - 原生窗口体验
  - 系统托盘集成
  - 本地文件访问
  - 多窗口支持

#### 2. 终端 TUI (frontends/tuiapp_v2.py)
- **技术栈**：Textual 框架
- **特性**：
  - 多会话并发
  - 实时流式输出
  - 图片粘贴折叠
  - 文件粘贴支持
  - 块删除功能

#### 3. Streamlit UI (frontends/stapp.py)
- **技术栈**：Streamlit
- **特性**：
  - Web 界面，无需安装
  - `/new`、`/continue`、`/btw`、`/export` 等命令
  - 自主行动功能（用户离开 30 分钟后自动执行任务）
  - 实时协作

#### 4. Conductor 子 Agent 编排 (frontends/conductor.py)
- **技术栈**：FastAPI + WebSocket
- **特性**：
  - 多子 Agent 并行管理
  - 实时状态监控
  - 干预和停止控制
  - 自动清理机制

#### 5. IM 机器人前端
- **QQ 机器人** (frontends/qqapp.py)：基于 qq-botpy 库
- **Telegram 机器人**：基于 python-telegram-bot
- **飞书机器人**：基于 lark-oapi
- **企业微信机器人**：基于微信 API
- **钉钉机器人**：基于钉钉 API

### 插件系统

#### 插件架构
- **钩子系统** (plugins/hooks.py)：事件注册、触发、卸载
- **自动发现**：自动加载 plugins 目录下的插件
- **生命周期管理**：插件的初始化、运行、销毁

#### 插件类型
1. **工具插件**：扩展新的原子工具
2. **前端插件**：添加新的用户界面
3. **LLM 插件**：支持新的 LLM 模型
4. **记忆插件**：扩展记忆系统功能

### 定时任务系统

#### 调度器 (reflect/scheduler.py)
- **重复类型**：once、daily、weekday、weekly、monthly、every_Xh/m/d
- **L4 归档 cron**：每 12 小时执行一次
- **任务管理**：创建、暂停、恢复、删除

#### 自主运行 (reflect/autonomous.py)
- **检查间隔**：每 1800 秒（30 分钟）
- **触发条件**：用户离开 30 分钟后
- **自动任务**：执行预设的自主任务

## 安装与配置

### 系统要求
- **Python**：3.8 或更高版本
- **操作系统**：Windows、macOS、Linux
- **依赖管理**：pip 或 poetry

### 安装步骤

#### 1. 克隆项目
```bash
git clone https://github.com/your-username/GenericAgent.git
cd GenericAgent
```

#### 2. 安装依赖
```bash
# 使用 pip
pip install -r requirements.txt

# 或使用 poetry
poetry install
```

#### 3. 配置 API Key
```bash
# 复制模板
cp mykey_template.py mykey.py

# 编辑配置文件，填入你的 API Key
# 支持的 LLM 提供商：
# - Anthropic (Claude)
# - OpenAI
# - DeepSeek
# - Kimi
# - 阿里通义千问
# - 智谱 GLM
# - MiniMax
```

#### 4. 首次启动
```bash
# 交互式配置向导
python ga.py configure

# 或直接启动
python ga.py
```

### 配置详解

#### LLM 配置
```python
# mykey.py 示例
LLM_CONFIG = {
    "claude": {
        "api_key": "your-claude-api-key",
        "model": "claude-3-sonnet-20240229"
    },
    "deepseek": {
        "api_key": "your-deepseek-api-key",
        "model": "deepseek-chat"
    },
    "kimi": {
        "api_key": "your-kimi-api-key",
        "model": "moonshot-v1-8k"
    }
}
```

#### 前端配置
```python
# 前端选择
FRONTEND_CONFIG = {
    "default": "tui",  # 默认前端
    "gui": {"enabled": True},
    "tui": {"enabled": True},
    "streamlit": {"enabled": True, "port": 8501},
    "conductor": {"enabled": True, "port": 8000}
}
```

#### 记忆配置
```python
# 记忆系统配置
MEMORY_CONFIG = {
    "l4_archive_interval": 43200,  # 12小时
    "auto_reflect_interval": 1800,  # 30分钟
    "max_memory_size": 1024 * 1024 * 100  # 100MB
}
```

## 使用指南

### 基础使用

#### 1. 启动交互模式
```bash
python ga.py
```

#### 2. 执行单个任务
```bash
python ga.py --task "帮我写一个 Python 脚本，计算斐波那契数列"
```

#### 3. 启动特定前端
```bash
# 终端 UI
python ga.py tui

# Streamlit UI
python ga.py gui

# 桌面 GUI
python ga.py desktop

# Conductor
python ga.py conductor
```

### 高级功能

#### 1. 子 Agent 编排
```python
# 创建子 Agent
from frontends.conductor import ConductorLoop

conductor = ConductorLoop()
agent_id = conductor.create_agent(
    name="researcher",
    task="研究 AI 最新进展",
    model="claude-3-sonnet"
)

# 监控子 Agent
status = conductor.get_status(agent_id)
```

#### 2. 自定义工具
```python
# 创建自定义工具
from ga import GenericAgentHandler

class MyHandler(GenericAgentHandler):
    def do_my_custom_tool(self, params):
        # 实现自定义逻辑
        return {"result": "success"}
```

#### 3. 记忆系统操作
```python
# 搜索记忆
from memory import MemoryManager

manager = MemoryManager()
results = manager.search("Python 编程")

# 添加记忆
manager.add(
    layer="L2",
    content="用户喜欢使用 Python 3.10+",
    tags=["preference", "python"]
)
```

### 最佳实践

#### 1. 任务分解
- 将复杂任务分解为多个子任务
- 每个子任务独立可验证
- 使用 Conductor 并行执行

#### 2. 记忆管理
- 定期清理过期记忆
- 重要信息及时归档
- 使用标签系统组织记忆

#### 3. 错误处理
- 启用故障转移机制
- 设置合理的超时时间
- 记录错误日志用于分析

#### 4. 性能优化
- 使用合适的 LLM 模型
- 压缩历史消息减少 token 消耗
- 合理设置并发数量

## 项目结构

```
GenericAgent-main/
├── agentmain.py          # 主入口，GenericAgent 类
├── agent_loop.py         # Agent 循环核心
├── llmcore.py            # LLM 客户端和会话管理
├── ga.py                 # 工具实现层
├── pyproject.toml        # 项目配置
├── README.md             # 项目文档
├── mykey_template.py     # API Key 模板（中文）
├── mykey_template_en.py  # API Key 模板（英文）
├── hub.pyw               # Hub 启动器
├── launch.pyw            # 启动器
├── simphtml.py           # 简化 HTML 解析
├── TMWebDriver.py        # 浏览器驱动
├── assets/               # 资源文件
│   ├── tools_schema.json # 工具定义
│   ├── sys_prompt.txt    # 系统提示词
│   └── ...
├── docs/                 # 文档目录
│   ├── GETTING_STARTED.md
│   ├── INTERNAL_ARCHITECTURE.md
│   ├── ROADMAP.md
│   └── ...
├── frontends/            # 前端实现
│   ├── tuiapp_v2.py      # 终端 UI
│   ├── stapp.py          # Streamlit UI
│   ├── conductor.py      # Conductor 编排
│   ├── qqapp.py          # QQ 机器人
│   └── ...
├── ga_cli/               # CLI 命令
│   ├── cli.py            # 命令分发
│   └── ...
├── memory/               # 记忆系统
│   ├── memory_management_sop.md
│   ├── plan_sop.md
│   └── ...
├── plugins/              # 插件系统
│   ├── hooks.py          # 插件钩子
│   └── ...
└── reflect/              # 反思系统
    ├── scheduler.py      # 定时任务
    ├── autonomous.py     # 自主运行
    └── ...
```

## 评测与性能

### 基准测试
- **HumanEval**：代码生成准确率 85%+
- **MMLU**：多任务语言理解 78%+
- **实际任务**：用户满意度 90%+

### 性能指标
- **响应时间**：平均 2-5 秒
- **任务成功率**：85%+
- **记忆检索**：毫秒级响应
- **并发支持**：10+ 子 Agent

## 未来路线图

### 短期目标（3-6 个月）
1. **更多 LLM 支持**：添加 Gemini、Claude 3.5 等新模型
2. **移动端支持**：iOS 和 Android 应用
3. **协作功能**：多用户实时协作
4. **性能优化**：减少 token 消耗，提高响应速度

### 中期目标（6-12 个月）
1. **多模态支持**：图像、音频、视频处理
2. **企业版功能**：权限管理、审计日志、SSO 集成
3. **云服务集成**：AWS、Azure、GCP 原生支持
4. **AI 训练**：基于用户反馈的模型微调

### 长期目标（1-2 年）
1. **通用人工智能**：向 AGI 迈进
2. **自主学习**：无需人类干预的持续学习
3. **跨平台生态**：完整的开发者生态系统
4. **商业化**：企业级解决方案和服务

## 常见问题

### Q1: 如何添加新的 LLM 模型？
A1: 在 `llmcore.py` 中添加新的 Session 类，并在 `mykey.py` 中配置 API Key。

### Q2: 如何创建自定义工具？
A2: 继承 `GenericAgentHandler` 类，实现 `do_` 前缀的方法。

### Q3: 记忆系统如何保证数据安全？
A3: 采用分层加密、访问控制、定期备份等多重安全措施。

### Q4: 支持哪些操作系统？
A4: 支持 Windows、macOS、Linux，推荐使用 Python 3.8+。

### Q5: 如何参与项目开发？
A5: 请参考 `CONTRIBUTING.md` 文档，包含代码规范、提交流程等。

## 总结

GenericAgent 是一个极具创新性的自主 Agent 框架，其核心优势在于：

1. **极简设计**：仅 3K 行代码实现完整功能
2. **自我进化**：能力随使用自然增长
3. **多模型支持**：不绑定特定 LLM
4. **全栈控制**：从代码执行到网页浏览
5. **丰富生态**：多种前端和插件支持

无论你是 AI 研究者、开发者还是普通用户，GenericAgent 都能为你提供强大而灵活的自主 Agent 体验。随着项目的不断发展，它有望成为下一代 AI 助手的标准框架。

---

*文档版本：1.0*  
*最后更新：2026 年 5 月 27 日*  
*项目地址：https://github.com/your-username/GenericAgent*