# Superflow

[English](README.md)

Superflow 是一个面向 Codex 的端到端软件交付工作流 Skill，由一个产品经理主代理和六个按需路由的专业子代理组成。

它让主代理成为唯一流程协调者，通过项目内可恢复状态、Git worktree 隔离、结构化 Agent 合同和同一候选提交的测试/审查双门禁，把需求、设计、开发、验证和交付串成可审计流程。

只有用户在当前请求中明确调用 Superflow（例如使用 `$superflow`）时才会启用这套规约。任务看起来适合、以前使用过 Superflow、项目已有配置或存在未完成账本，都不会触发隐式调用。

## 为什么使用 Superflow

多代理编码最容易在边界处失控：子代理修改越界文件、测试和审查针对不同提交、代码变化后旧结论仍然有效，或者无法解释一个运行为什么被标记为完成。Superflow 使用确定性脚本和 fail-closed 策略约束这些问题。

主要能力：

- 主代理只负责编写需求，并独占用户沟通、工作流状态、审批和机械 Git 集成权限。
- 无论当前是否有派发等待，主代理都不得执行任何专业角色工作。
- 六类项目级 Agent 按任务需要路由，不强制走固定流水线。
- 三档执行模式控制成本：`lite` 使用一个合并质量 Agent，`standard` 使用独立并行门禁，`strict` 保留完整高风险流程。
- 新子代理只接收最小任务 brief，不继承主对话；记忆召回与 CodeGraph 使用由当前档位限制。
- 每个 Agent 返回符合 JSON Schema 的严格结构化结果。
- 测试和代码审查必须审批同一个真实 Git commit。
- candidate 发生变化后，既有门禁自动失效。
- 返工绑定不可变任务 lineage，同一任务最多自动修复三轮。
- 项目内状态快照和 hash 链事件日志支持跨会话恢复。
- 每个新运行都会生成面向用户的需求基线和自动刷新的处理过程记录，且不进入业务 worktree。
- 日常协调只读取紧凑状态摘要；完整 Agent 结果保留在不可变审计制品中，仅在恢复或证据核查时读取。
- 七个角色都能通过角色绑定的临时凭证主动读取自己的项目历史，并提出重要记忆，但不能读取其他角色记忆。
- 记录 brief 时会校验当前安装的角色记忆脚本；每次首次、重试或修复派发都必须使用新的角色绑定 capability。缺失、复用、过期、已撤销、伪造或跨角色 capability 会直接阻塞，角色历史为空则可以正常继续。
- 被接受的专业子代理结果必须报告成功的记忆召回数量，但不得暴露 capability、查询内容或实际召回记录。
- 远程 Git、破坏性清理和最终集成都需要用户明确授权。

## 七个角色

| 角色 | 模型配置 | 职责 |
|---|---|---|
| 产品经理 | 当前主代理 | 需求、路由、状态、Git、审批和用户决策 |
| 架构师 | gpt-5.6-sol，高 | 架构、模块边界、接口、数据流和风险 |
| UI 设计师 | gpt-5.6-sol，中 | 使用项目初始化时由用户选择的原型平台交付流程、状态、组件和交互规则 |
| 前端开发 | gpt-5.6-sol，中 | 前端实现及相关测试 |
| 后端开发 | gpt-5.6-sol，中 | API、领域逻辑、数据、服务和迁移 |
| 测试 | gpt-5.6-terra，中 | 自动化测试、回归覆盖和真实浏览器验证 |
| 代码审查 | gpt-5.6-sol，高 | 规范符合性、正确性、一致性、安全和可维护性 |

六类专业子代理不是固定串行链路。局部低风险任务通常只使用对应开发者和一个合并质量审查者；纯后端缺陷不需要 UI 设计师，跨模块页面功能则可能需要架构、UI、前端、后端、测试和审查共同参与。

## 执行档位

| 档位 | 最低适用范围 | 质量门禁 | 上下文预算 |
|---|---|---|---|
| `lite` | 局部低风险代码、测试或文档 | 一个代码审查 Agent 同时运行测试和审查，一份结果支持两个 gate | 3 条记忆、2 KiB、精简输出，必要时才使用 CodeGraph |
| `standard` | 用户可见、浏览器、原型、跨模块或公共接口任务 | tester 与 reviewer 独立并行 | 5 条记忆、4 KiB、标准输出 |
| `strict` | 权限、安全、迁移、生产、发布或破坏性操作 | 独立门禁及所有风险触发角色 | 10 条记忆、8 KiB、完整输出 |

确定性选择器会采用满足风险边界的最轻档位。用户可以主动提高档位，但用户请求和主代理都不能把档位降低到已识别风险以下。没有档位字段的旧运行继续按 `strict` 处理。

`workflow_state.py init` 接收 `--profile auto`、JSON 格式的 `--risk-signals` 和 `--document-language en|zh-CN`，在冻结运行前重新执行选择器，并初始化面向用户的过程记录。没有风险信号时，新 CLI 运行会落到 `lite`。

## 环境要求

- 支持项目级 Agent 的 Codex
- Python 3.10 或更高版本
- Git
- 干净的目标 Git worktree

条件工具：

- 冻结 brief 设置 `codeGraphRequired` 时，重要代码探索使用 CodeGraph；局部 `lite` 工作可改用精确的本地检查。
- 首次初始化由用户选择 UI 原型工具：Penpot MCP、Codex Figma 插件或自定义工具。
- 首次初始化由用户选择浏览器：Codex Browser 插件、Chrome MCP 或自定义工具。
- 项目选择写入 Git 共享配置，同一仓库的所有 worktree 和后续运行持续使用；只有用户明确同意才能改选。
- 产品管理、架构设计、UI/UX 设计、前端工程、后端工程、测试策略和代码审查标准已经内置，不要求安装同名外部 Skill。
- 角色增强 Skill 属于可选能力，不是核心运行依赖。

## 安装

将 superflow 目录复制到 ~/.codex/skills/superflow；如果配置了 CODEX_HOME，则复制到对应的 skills/superflow：

~~~bash
mkdir -p ~/.codex/skills
cp -R ./superflow ~/.codex/skills/superflow
~~~

如果当前 Codex 会话无法发现新 Skill，请重启 Codex。支持按路径加载 Skill 的环境也可以直接使用本目录的绝对路径。

GitHub Release 发布包包含可直接安装的顶层 `superflow/` 目录：

~~~bash
unzip superflow-v0.2.3.zip -d ~/.codex/skills
~~~

## 快速开始

在 Git 项目中要求 Codex 使用 Superflow：

~~~text
使用 $superflow 从需求开始实现这个功能，并完成测试、审查和可验证交付：……
~~~

首次使用时，主代理先询问浏览器与 UI 原型工具，再初始化项目内托管配置：

~~~bash
python3 <superflow>/scripts/init_project.py --project "$PWD" \
  --browser-provider chrome-mcp \
  --ui-provider penpot-mcp
~~~

示例中的 provider 不能作为默认值静默使用，必须替换成用户实际选择。脚本会把项目选择写入 Git 共享配置 `info/superflow.json`，在 `.codex/agents/` 下安装或升级六份 Agent 配置，并通过 Git 本地的 `info/exclude` 排除整个 `.codex/` 目录。如果已有任何 `.codex` 文件被 Git 跟踪，初始化会直接拒绝继续。与用户自定义内容冲突的文件会保留。如果 Codex 需要重启才能发现新 Agent 或所选插件，Superflow 会暂停并要求用户重启或新开会话。

项目已有配置时会直接复用。只有用户明确要求时才能用 `--reconfigure` 改选。每次运行会冻结启动时的选择；改选后旧运行只能阻塞或取消，不能混用新旧 provider 的证据继续。

## 工作流程

~~~text
初始化
  → 预检
  → 项目探索
  → 需求就绪
  → 架构设计？/ UI 设计？
  → 实施计划
  → 开发实现
  → 测试 + 代码审查
  → 返工（最多三轮）
  → 就绪或用户明确接受风险
  → 完成
~~~

主代理负责以下核心步骤：

1. 初始化或升级六份托管 Agent 模板。
2. 确认目标项目是干净的 Git worktree。
3. 澄清需求、冻结面向用户的 `requirements.md`，并定义可观察验收标准。
4. 选择并冻结最轻安全档位，只调度当前任务需要的专业角色。
5. 创建 integration worktree，并按需创建互不冲突的任务 worktree。
6. 记录每次派发，把不可变派发 ID 交给专业子代理，然后等待且不重叠执行已分配工作。
7. 将返回结果绑定到对应派发后，只机械校验 Schema、授权路径、已记录工具证据和 Git snapshot。
8. 只提交明确授权的路径。
9. 将 integration worktree 的真实 HEAD 冻结为 candidate。
10. 记录同一 candidate SHA 的门禁：`lite` 使用一份综合质量结果，`standard` 和 `strict` 使用独立并行的 tester 与 reviewer 结果。
11. 所有计划任务完成且双门通过后才结束；失败门禁只能由用户逐项明确接受风险。

状态脚本还会根据审计事件自动维护 `process-log.md`。两份面向用户的 Markdown 文档位于共享运行目录，因此所有 worktree 都可读取，又不会改变业务候选提交。日常协调使用紧凑 `summary` 命令，完整 `show` 仅用于恢复或审计。

## Candidate 与双门审批

Candidate 必须是当前干净 integration worktree 的真实 HEAD：

~~~bash
python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  set-candidate <run-id> <candidate-sha>
~~~

在专业 Agent 执行前后采集 Git snapshot：

~~~bash
python3 <superflow>/scripts/git_workspace.py snapshot \
  --project <integration-worktree> > before.json
~~~

门禁 PASS/FAIL 由脚本根据完整 Agent 结果推导，不能由主代理手工填写：

~~~bash
python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  record-gate <run-id> test <candidate-sha> <task-id> \
  --agent-result test-result.json \
  --before before.json \
  --after after.json \
  --allowed-path 'tests/**' \
  --browser

python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  record-gate <run-id> review <candidate-sha> <task-id> \
  --agent-result review-result.json \
  --before before.json \
  --after after.json
~~~

状态脚本会检查 Agent 角色、任务 ID、candidate SHA、Schema、授权路径、Git 权限、项目工具配置快照、工具 evidence provider、验证检查、测试命令、findings 和当前仓库事实。

浏览器权限也会被冻结到任务 brief 中。`chrome-mcp` 和可由专业角色直连的自定义浏览器，可由前端角色用于复现、调试和实现自检，再由测试角色独立完成最终验收。`codex-browser` 仅属于主代理，不能用于委派后的浏览器工作；需要真实页面操作时，流程会暂停并要求用户为新运行改选专业角色可直连的 provider。主代理不会代替专业角色操作浏览器。

实现结果被接受后，主代理只校验结果合同、执行必要的 Git 集成、冻结候选 SHA，然后立即派发单质量门禁或并行的审查与测试双门禁。主代理不会复跑测试、启动容器、准备候选运行环境或提前检查页面；所有候选级验证由门禁角色完成。

主代理只负责编写需求和控制流程机械运转。即使当前没有子代理在运行，它也不参与架构、UI、实现、调试、测试、浏览器操作或代码审查。每份专业指南和专业任务只由对应角色处理。

专业角色可以使用明确的只读 Git 白名单（包括 `git rev-parse`）确认冻结候选并收集证据；Git 写操作、远程操作和间接包装命令仍被禁止。合法的只读命令不会让合规结果失效，但 tester 的每次成功重试仍必须重新执行该候选要求的验证。

## 可恢复状态

每次运行都保存在 worktree 之外的 Git common directory：`superflow/workflows/<run-id>/`。因此同一仓库的所有 linked worktree 始终读取同一份账本：

~~~text
state.json
events.jsonl
plan.json
routing.json
worktrees.json
requirements.json
requirements.md
process-log.md
briefs/
artifacts/
attempts/
gates/
~~~

每个计划任务都会冻结角色、依赖、授权路径、验收标准、精确验证命令和可观察结果。任务 brief、调度记录、worktree 注册信息、每次被接受或拒绝的尝试以及绑定 candidate 的 gate 都会作为审计产物保留。等待中的派发会绑定任务、角色、worktree、brief、执行前快照和真实子代理会话；结果被记录前，状态推进和 Git 写入持续锁定。state.json 同时接受 JSON Schema 和跨字段不变量校验。events.jsonl 是带状态 hash 和事件间 hash 的追加式 revision 链。写入使用进程锁和 revision compare-and-swap，陈旧并发更新会被拒绝。

## 角色隔离记忆

七个角色分别拥有项目级记忆，实际数据位于 Git common directory 的 `superflow/memory/`。同一仓库的 linked worktree 可以共享，但记忆不会进入 commit、push 或普通 clone。

主代理派发任务时签发绑定 `role + runId + taskId` 的临时 capability。角色使用该 capability 主动查询自己的历史；`recall` 命令不能自行指定角色，召回过程不会写文件或创建锁。完整 Agent 结果通过 Schema 和策略检查后，主代理才会写入最多三条结构化 `memoryWriteRequests`，随后撤销 capability。

召回组合高重要性、关键词相关和最近记忆。派发预算与档位绑定：`lite` 最多 3 条/2 KiB，`standard` 最多 5 条/4 KiB，`strict` 最多 10 条/8 KiB。每个角色最多保留 500 条有效记忆；新记录可以通过 `supersedes` 修订旧记录，旧记录和超限记录进入该角色自己的归档。

记忆绝不跨角色共享。跨角色合同和项目事实必须通过 brief、正式产物或项目文档传递。只有用户明确要求时，主代理才能列出、查看、删除、清空、导出或导入指定角色记忆。详见[角色隔离记忆](references/role-memory.md)。

## 安全边界

- 专业子代理不能联系用户、更新工作流状态或执行 Git 写操作。
- 项目本地的整个 `.codex/` 目录通过 Git `info/exclude` 排除；发现任何已跟踪的 `.codex` 文件都会阻塞初始化。
- 只有主代理可以暂存、提交和集成代码。
- push、PR、最终 merge、分支删除和 worktree 删除永不自动执行。
- Git 脚本只提供本地预检、worktree 创建、snapshot、状态、提交和 cherry-pick。
- 存在任意可执行 Git hook 时，自动 Git 写操作会阻塞；Superflow 不绕过 hook。
- 提交使用 literal pathspec，并拒绝宽范围路径和 .git 元数据路径。
- 脏工作树、陈旧 revision、符号链接状态路径、损坏状态、断裂事件链和不匹配 candidate 都会 fail closed。
- 产品经理不能覆盖失败门禁；只有用户可以明确接受当前失败 gate 的风险。

## 项目结构

~~~text
superflow/
├── SKILL.md
├── README.md
├── README_CN.md
├── VERSION
├── LICENSE
├── agents/openai.yaml
├── assets/
│   ├── agent-templates/
│   └── schemas/
├── licenses/
├── references/
├── scripts/
└── tests/
~~~

详细合同：

- [工作流状态机](references/workflow-state-machine.md)
- [角色合同](references/role-contracts.md)
- [工具与增强 Skill 策略](references/tool-and-skill-policy.md)
- [角色隔离记忆](references/role-memory.md)
- [融合的 Superpowers 规则](references/superpowers-derived-rules.md)
- [产品管理规则](references/product-management-rules.md)
- [架构设计规则](references/architecture-design-rules.md)
- [UI/UX 设计规则](references/ui-ux-design-rules.md)
- [前端工程规则](references/frontend-engineering-rules.md)
- [后端工程规则](references/backend-engineering-rules.md)
- [测试策略](references/testing-strategy.md)
- [代码审查标准](references/code-review-criteria.md)

## 版本与发布

Superflow 使用语义化版本，当前版本记录在 [VERSION](VERSION)。

生成确定性 ZIP 发布包和 SHA-256 校验文件：

~~~bash
python3 scripts/build_release.py
~~~

GitHub CI 会在 push 和 pull request 时运行完整测试并验证发布包。推送与 `v$(cat VERSION)` 完全一致的 tag 后，Release 工作流会发布 ZIP 和校验文件。

## 验证

运行完整测试：

~~~bash
python3 -m unittest discover -s tests -v
~~~

当前测试覆盖显式调用门、派发绑定等待与结果身份校验、角色记忆 brief 强校验、用户文档生成、紧凑状态摘要、项目级工具选择与漂移拦截、共享工作流账本、完整任务合同与依赖、不可变尝试审计、外部证据来源、角色隔离记忆、英文发布边界、发布包可复现性、作者元数据、双门完整性、candidate 失效、返工上限、状态恢复、事件篡改、Git 路径安全、hook 阻塞和策略检查等对抗场景。

使用 Codex Skill Creator 校验 Skill 元数据：

~~~bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
~~~

## 开源协议

Superflow 使用 [MIT License](LICENSE) 开源。第三方归属与随附的上游许可证保存在 [licenses/](licenses/) 目录。

## 作者与联系

- 作者：beautiful boy
- 邮箱：[xbyzzz0917@163.com](mailto:xbyzzz0917@163.com)

## 来源与归属

Superflow 吸收了 [obra/superpowers](https://github.com/obra/superpowers) 的需求先行、TDD、隔离 worktree、证据化验证和代码审查思想，并重新实现为面向 Codex 七角色的自动审批工作流。

上游 Superpowers MIT 许可证声明保存在 [licenses/superpowers-MIT.txt](licenses/superpowers-MIT.txt)。
