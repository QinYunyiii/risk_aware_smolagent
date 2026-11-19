# Day 03 Report

## Objectives

- 实现 Data Analysis、File Retrieval、API Workflow、Calculation & Logic 四类本地任务环境。
- 实现 YAML Task Loader、隔离环境 reset 和确定性验证器。
- 每领域至少提供 2 个任务，完成至少 8 个本地端到端 smoke task。
- 验证 reset 有效且写操作具备事务、状态确认和幂等保护。
- 不提前实现 Day 4 的 F0--F6 故障注入。

## Local Environment Work

- 使用 Day 2 已验证的项目级 Python 3.12 `.venv`。
- 所有 Day 3 工具、SQLite Mock API、任务环境和验证器均在本地 WSL 执行。
- 任务工作区位于唯一 `run_id` 目录下；任务文件和 SQLite 状态按 task 隔离。
- YAML 任务显式标记为 `dataset_role: day3_smoke`，不冒充正式训练、验证或测试数据。

## Remote Environment Work

- Day 3 不需要远程模型推理、SSH、GPU 或 vLLM。
- 未启动远程服务，未占用实验室 GPU，未修改 Recovery-Bench 或本地 Docker 环境。

## Work Completed

- 实现不可变 Task Schema：`task_id`、`template_id`、`instance_id`、domain、seed、
  fixtures、reference steps 和 deterministic expected state。
- 实现 `yaml.safe_load` Task Loader，并验证 schema version、唯一 task ID、安全标识符、
  相对路径及 smoke 数据角色。
- 实现隔离 `TaskEnvironment`：
  - 原子 materialize/reset；
  - 路径逃逸和 symlink 替换保护；
  - CSV / 文档 fixture；
  - SQLite inventory、orders、idempotency 状态；
  - 文件哈希和规范化数据库行组成的状态清单/状态哈希。
- 实现四领域本地工具：
  - Data Analysis：`read_csv`、`filter_data`、`aggregate_data`；
  - File Retrieval：`list_files`、`search_files`、`read_file`；
  - API Workflow：`query_inventory`、`get_order`、`update_order`；
  - Calculation & Logic：`calculator`、`unit_converter`、`validate_result`。
- 实现显式 domain-aware Tool Registry，阻止跨领域工具调用。
- API 写操作使用 `BEGIN IMMEDIATE` 事务、期望旧状态、合法状态迁移、版本递增和
  idempotency key；同键异请求被拒绝，同键同请求不重复提交。
- 实现统一确定性验证器：精确结果、文件内容、inventory 行和 order 行验证，不使用 LLM Judge。
- 实现 reference runner，对每个任务执行 reset → tools → verifier → reset → state hash comparison。
- 实现唯一 `experiment_id` / `run_id` 结果目录；已有 run 目录拒绝覆盖。

## Files Changed

- `configs/tasks.yaml`
- `src/risk_agent/benchmark/__init__.py`
- `src/risk_agent/benchmark/schema.py`
- `src/risk_agent/benchmark/task_loader.py`
- `src/risk_agent/benchmark/environment.py`
- `src/risk_agent/benchmark/verifier.py`
- `src/risk_agent/benchmark/runner.py`
- `src/risk_agent/tools/data_analysis_tools.py`
- `src/risk_agent/tools/file_retrieval_tools.py`
- `src/risk_agent/tools/api_workflow_tools.py`
- `src/risk_agent/tools/logic_tools.py`
- `src/risk_agent/tools/task_tool_registry.py`
- `scripts/run_day3_tasks.py`
- `tests/test_tasks.py`
- `docs/daily_reports/day_03_report.md`

## Artifacts Generated

- 验收 run：`day3_20261117_185053_d4e19f1d`（UTC 时间戳）。
- 结果：`runs/day03/day3_20261117_185053_d4e19f1d/task_results.json`。
- 结果包括每个 task 的模板、实例、domain、seed、工具调用数、验证 checks、初始状态哈希和
  reset 后状态哈希。
- `runs/` 由 `.gitignore` 排除；历史 smoke 结果未删除或覆盖。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_day3_tasks.py
.venv/bin/python -m pip check
.venv/bin/python -m py_compile $(find src scripts tests -name '*.py' -type f)
```

## Test Results

- Unit / integration：24 passed。
- YAML Loader：8 个任务加载成功，四领域各 2 个，全部为 `day3_smoke`。
- End-to-end：8 passed，0 failed。
- Deterministic verifier：8/8 通过；API 任务同时验证 SQLite 状态。
- Reset：8/8 初始状态哈希等于恢复状态哈希。
- 文件 reset：主动修改 fixture 后可恢复原内容和原状态哈希。
- SQLite reset：订单版本、状态和 idempotency table 均恢复初始值。
- Write safety：状态冲突、幂等键异请求冲突和非法状态迁移均被拒绝。
- Workspace safety：`../` fixture 和运行时路径逃逸均被拒绝。
- Domain safety：跨领域工具调用被拒绝。

## Acceptance Checklist

- [x] 实现四领域本地工具。
- [x] 实现 YAML Task Loader。
- [x] 实现任务级环境 reset。
- [x] 所有任务使用确定性验证器。
- [x] Data Analysis 至少 2 个任务。
- [x] File Retrieval 至少 2 个任务。
- [x] API Workflow 至少 2 个任务。
- [x] Calculation & Logic 至少 2 个任务。
- [x] 至少 8 个端到端任务通过（8/8）。
- [x] reset 有效（8/8 状态哈希一致）。
- [x] 写操作具有事务、状态确认、合法迁移和幂等保护。
- [x] 未实现或提前运行 Day 4 故障注入。

## Known Issues

- 当前 8 个任务是 Day 3 工程 smoke tasks，不是 Day 7 的正式模板/实例数据集。
- Reference runner 使用预定义工具步骤验证环境与 verifier，并非远程 LLM Agent 成功率实验。
- Day 4 的 Timeout、503、Invalid Argument、Stale Observation、No Progress 和 Partial Failure
  尚未实现，符合本日边界。

## Remaining Work

- Day 3 无核心遗留项。
- 等待用户确认后进入 Day 4；不得把本日 smoke 结果当作正式实验结果。

## Status: PASS

四领域、8 个端到端任务、确定性验证、reset 和写安全核心验收全部通过。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留。
