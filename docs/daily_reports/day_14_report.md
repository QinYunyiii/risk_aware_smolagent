# Day 14 Report

## Objectives

- 通过项目内只读 Adapter 选择少量本地 Recovery-Bench/Harbor 任务进行扩展验证。
- 只有在 Recovery-Bench 实际支持一致状态恢复时才进行同状态分支效用实验；否则明确报告限制。
- 验证实际 Harbor 0.4.0 `config.json`、`result.json`、ATIF trajectory 和 verifier reward 产物。
- 保护外部 Recovery-Bench 仓库已有用户修改，不修改、重建、清理或恢复其工作树。
- 完成复现指南、最终工程报告、README 和可执行 Demo。
- 不改变研究设计、动作语义、效用公式、数据划分或本地/远程职责边界。

## Local Environment Work

- 重新核实 Recovery-Bench 路径 `/home/three/projects/agent-intervention/recovery-bench` 和 Git HEAD `c5f83f2b`。
- 外部仓库在本日操作前后均有 13,724 个既有 modified 项；本项目未写入任何外部文件。
- 核实本地 Harbor 0.4.0、Docker client/server 29.8.2。
- Docker 当前只有 3 个历史 stopped container、0 个 named volume；本日未启动、停止、删除或修改任何容器、镜像、Volume 或 Mount。
- 从现有 89 个任务级 Harbor trial 中 checksum-pinned 选择 3 个互补样本：
  - `headless-terminal`：reward 1.0，`success`；
  - `path-tracing-reverse`：reward 0.0，无 trial exception，`verifier_failure`；
  - `caffe-cifar-10`：reward 0.0，`AgentTimeoutError`，`agent_error`。
- Adapter 默认只导出字段、计数和 SHA-256；不复制历史命令参数值、模型消息或 observation 内容。
- 每个选中源 artifact 在解析前后重新计算 SHA-256，全部一致。

## Remote Environment Work

- 无。Day 14 不需要远程 Qwen3-8B、SSH 隧道、HTTPS API、vLLM 或 GPU。
- 未连接远程服务器，未启动服务，未占用或检查共享 GPU。

## Work Completed

- 新增路径约束的 `RecoveryBenchAdapter`：trial 名只能是单一目录组件，解析路径必须位于配置的 trials root 内。
- 严格校验：
  - `result.json`、`config.json`、`agent/trajectory.json` 必须是 JSON object；
  - result 内嵌 config 必须与独立 config 完全相同；
  - ATIF schema 必须在 allowlist 中，本次为 `ATIF-v1.6`；
  - task checksum 必须与配置的选择记录一致；
  - verifier reward 文件必须与 result reward 一致；
  - trial 时间必须合法且非负。
- 标准化 Trial 身份、task/source、agent/model、Docker environment、reward、exception、Token、cost、duration、steps、tool calls 和 observations。
- 状态分类优先保留 exception：`agent_error`、`verifier_error`、`environment_error`、`trial_error`；无异常时再区分 `success`、`verifier_failure`、`incomplete`，避免把执行异常混为普通任务失败。
- 工具结果关联规则：优先 `source_call_id`，唯一等长集合可做 positional 关联，其余保留 `step_level_unlinked`，不伪造 call/result 对应关系。
- 正式 Demo 读取 3 个真实历史 Trial，共标准化：
  - 37 个 ATIF step；
  - 100 个 tool call；
  - 34 个 observation result；
  - 其中 23 个 observation result 无可靠 call-level 关联，显式保留为 step-level/unlinked。
- 正式 Demo 的 16 项 identity/schema/content safety/source immutability/restore boundary Gate 全部为 true，`passed=true`。
- 明确记录 `checkpoint_restore_supported=false`、`branch_utility_experiment_executed=false`：Harbor Docker trial 的 fresh reset 和 Recovery-Bench command replay 不构成规范化中间状态快照、hash-verified Restore 或同 Checkpoint 隔离分支。
- 完成复现指南、最终工程报告、Demo 文档并更新 README 到实际交付状态。

## Files Changed

- `configs/day14_recovery_bench.yaml`
- `src/risk_agent/recovery_bench/__init__.py`
- `src/risk_agent/recovery_bench/schema.py`
- `src/risk_agent/recovery_bench/adapter.py`
- `scripts/run_day14_recovery_bench_demo.py`
- `tests/test_recovery_bench_adapter.py`
- `docs/reproducibility.md`
- `docs/final_report.md`
- `docs/demo.md`
- `docs/daily_reports/day_14_report.md`
- `README.md`

未修改外部 Recovery-Bench、Harbor package、Day 1–13 原始产物、模型、任务 split 或远程部署。

## Artifacts Generated

正式 run：`runs/day14/day14_20261119_090131_2c077c82/`

- `recovery_bench_demo_summary.json`：模式、版本、计数、恢复边界、16 项 Gate 和最终状态。
- `selected_trials.jsonl`：3 条标准化 Trial；原始工具参数/observation 内容为 null。
- `normalized_tool_calls.jsonl`：100 条工具调用元数据、参数 key/digest、关联状态和 step metrics。
- `normalized_observations.jsonl`：34 条 observation 元数据、内容 digest 和关联状态。
- `source_artifact_manifest.json`：12 个源文件解析前后 SHA-256。

本次只读取已存在的外部历史 Trial，没有生成新的 Recovery-Bench/Harbor run。

## Commands and Tests

```bash
# 外部仓库、本地 Harbor 和 Docker 只读核实
git -C /home/three/projects/agent-intervention/recovery-bench status --short
git -C /home/three/projects/agent-intervention/recovery-bench rev-parse --short HEAD
/home/three/projects/agent-intervention/.venv/bin/harbor --version
docker version
docker ps -a
docker image ls
docker volume ls

# Adapter 专项测试与正式只读 Demo
.venv/bin/python -m pytest -q tests/test_recovery_bench_adapter.py
.venv/bin/python scripts/run_day14_recovery_bench_demo.py

# 最终项目验收
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另使用只读审计核对正式 run 的行数、Trial 身份、状态、reward、步骤/工具/observation 计数、Token、duration、raw content redaction、source hash 和外部 Git 状态计数。

## Test Results

- Recovery-Bench Adapter 专项测试：5 passed。
- 全量回归：76 passed。
- 依赖检查：`No broken requirements found.`。
- Python 编译：通过。
- 正式 Demo：3/3 配置身份、task checksum 和预期状态匹配。
- ATIF：3/3 为 `ATIF-v1.6`。
- result/config：3/3 完全一致。
- verifier reward：3/3 与 `verifier/reward.txt` 一致。
- Source artifact：12/12 解析前后 SHA-256 一致。
- 状态覆盖：success 1、verifier failure 1、agent error 1。
- 总量：37 steps、100 tool calls、34 observations。
- 关联限制：23/34 observations 无可靠 call-level 关联并显式保留；没有错误配对。
- Token：input 750,098、cache 627,580、output 81,622；均来自历史 result，不是本日模型调用。
- 历史 cost：USD 0.621248；不是本日新增费用。
- Raw content：0 条参数值或 observation 内容复制到正式 Day 14 产物。
- 外部 Recovery-Bench status：操作前后均为 13,724 个既有 modified 项。
- Docker/Harbor execution：未运行；容器、Volume、外部 trial 均未改变。
- Checkpoint/Restore：未声称支持；Branch utility：未执行。

## Acceptance Checklist

- [x] 通过 Adapter 选择少量真实本地 Recovery-Bench/Harbor Trial。
- [x] Adapter 对实际 Harbor 0.4.0 和 ATIF-v1.6 schema 完成验证。
- [x] 成功、verifier failure 和 Agent error 被明确区分。
- [x] 工具名、参数摘要、observation 摘要、Token、时间和 verifier 结果可追溯。
- [x] 缺少 call/result ID 时不伪造精确关联。
- [x] Task checksum、config/result 和 verifier reward 一致性通过。
- [x] 外部源文件解析前后 hash 一致。
- [x] 未修改 Recovery-Bench 原仓库。
- [x] 未启动或破坏本地 Docker/Harbor 环境。
- [x] 已核实 Recovery-Bench 不支持所需的一致 Checkpoint/Restore。
- [x] 因 Restore Gate 不成立，未执行 Recovery-Bench 分支效用实验。
- [x] 完成复现文档。
- [x] 完成最终报告。
- [x] 更新 README。
- [x] 完成可执行 Demo。
- [x] 全量测试、依赖和编译检查通过。

## Known Issues

- 本日使用已有历史 Trial 做只读集成，没有重新运行原 Agent 或 verifier；结果证明 Adapter 可解析和校验产物，不证明原任务在当前 Docker 环境中可重复执行。
- 3 个样本是有目的的状态覆盖样本，不是 Recovery-Bench 的代表性统计抽样，不能用 1/3 success 估计 benchmark 性能。
- ATIF 中大量 observation 缺少 `source_call_id`；当一个 step 的 call/result 数量不相等时只能保留 step-level 关联。
- Formal Demo 为保护历史内容默认不复制原始参数和输出。授权调用者可以在内存中使用 `include_content=True`，但必须另行处理许可、隐私和日志保护。
- Recovery-Bench command replay 是 best-effort 重放，不保存 Agent 内部状态、任意任务文件/SQLite/服务状态、预算和规范化 state hash；不能用于同状态分支因果/效用数据。
- 当前机器路径、Recovery-Bench HEAD 和选中 task checksum 固定在 Day 14 配置中；迁移环境时必须显式更新并重新审计，不能静默跳过校验。
- 外部仓库有大量既有 modified 文件，不能安全地由本项目自动测试、format、checkout、clean 或 commit。
- Day 10–13 的研究限制不因 Day 14 交付而消失：20 个同源 Train Checkpoint、Retry/Replan 无负类、Token 成本为 0、消融无差异、无官方 Test。

## Remaining Work

- 14 日计划内的工程工作已完成，没有下一 Day 自动推进项。
- 后续若开展正式研究，应先采集更大规模的真实模型 Train/Validation/Test Checkpoint 和失败多样的 Retry/Replan 分支。
- 若要在 Recovery-Bench 上进行效用实验，需要先设计并验证其 Docker 任务状态的完整 snapshot/restore protocol；在 state hash Gate 通过前不得生成同状态分支结论。
- 公开或迁移项目之前需确认第三方 Recovery-Bench/Terminal-Bench 数据许可，并单独归档 `.gitignore` 排除的 runs 和模型二进制。

## Status: PASS

Day 14 的核心要求已完成：真实 Recovery-Bench/Harbor 历史任务经只读 Adapter 校验，外部源保持不变；由于一致 Restore 不受支持，按指南没有执行分支效用实验并明确记录限制；复现指南、最终报告、README 和 Demo 均已交付。PASS 表示 Day 14 工程交付通过，不表示 Recovery-Bench 已具备同状态反事实实验能力，也不提升 Day 10–13 结果为正式泛化证据。

## Git Commit

未提交；用户未要求创建 commit。主项目当前文件仍未跟踪；外部 Recovery-Bench 仓库的 13,724 个既有 modified 项均未修改、清理、覆盖或提交。
