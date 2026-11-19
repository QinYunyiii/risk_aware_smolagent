# P1-D1 Report

## Objectives

建立 Day 7--14 正式实验、模型、配置、数据和日报的不可变本地基线；核对关键
计数、标识符、字段和来源关系；为后续 Phase 2 审计提供可重复的 SHA-256 清单。
本次只执行 P1-D1。

## Local Environment Work

- 确认工作目录为 `/home/three/projects/risk-aware-smolagents`。
- 完整阅读 `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`、
  `NEXT_STAGE_EXPERIMENT_GUIDE.md` 和最近的 Day 14 日报。
- 检查 `git status --short`：仓库现有内容全部显示为未跟踪，未发现可用的历史
  commit 基线。
- 非激活 shell 中 `python` 命令不可用；项目 `.venv/bin/python` 为 Python 3.12.3，
  后续命令全部显式使用该解释器。
- 只读审计 Day 7--14 的正式 run、Day 10/11 模型、配置、summary 和日报。
- 未重跑任何历史实验，未修改 `runs/`、`models/`、`configs/` 或 Day 1--14 日报。

## Remote Environment Work

无。本任务不需要 SSH、HTTPS API、GPU、vLLM 或远程模型；未检查、启动、停止或
修改任何远程服务。

## Work Completed

1. 定位 8 个正式 run 和 2 个最终模型目录，核实相应配置、summary、关键数据及日报。
2. 对 80 个关键文件记录逐文件 SHA-256。
3. 对 10 个正式 run/model 目录的 1,837 个文件计算确定性全树摘要。
4. 核对 Day 8：24 个唯一逻辑任务、43 个唯一 attempt；19 个任务有两次 attempt，
   5 个任务有一次；状态为 19 success、4 infra_failure、1 task_failure。
5. 核对 Day 9：20 个唯一 Checkpoint、60 个唯一 action/branch record、36 个 applicable；
   CONTINUE/RETRY/REPLAN applicable 数分别为 20/6/10。
6. 核对 Day 10/11：模型 metadata 均引用同一 Day 9 正式 run；两者均使用 26 个互异且
   完全一致的特征。
7. 核对 Day 13：180 个 policy records 与 180 个 branch results 的 branch ID 集合及
   payload 完全一致；180 个 `(policy, checkpoint_id)` 全部唯一。
8. 核对适用记录的 split 和分支执行 controller 字段；记录 Day 8 缺少
   `controller_type`、Day 9 以 `state_hash` 代替独立 `checkpoint_hash` 两项 schema
   观察。
9. 新增确定性、只读的审计脚本和两项专项测试。
10. 复核正式目录树摘要与审计开始时一致，确认历史产物未改变。

## Files Changed

本次仅新增：

- `scripts/audit_phase2_history.py`
- `tests/test_phase2_history_audit.py`
- `docs/phase2/audit_inventory.md`
- `docs/phase2/historical_artifact_manifest.json`
- `docs/phase2/daily_reports/p1_d1_report.md`

没有修改外部 Recovery-Bench 仓库，也没有修改 Day 1--14 历史产物。

## Artifacts Generated

- 审计说明：`docs/phase2/audit_inventory.md`
- 机器可读基线：`docs/phase2/historical_artifact_manifest.json`
- 本日报：`docs/phase2/daily_reports/p1_d1_report.md`
- 审计脚本：`scripts/audit_phase2_history.py`
- 专项测试：`tests/test_phase2_history_audit.py`

Manifest 包含 80 个关键文件哈希、10 个目录树摘要、正式 stage 路径映射、结构审计
结果和已记录 findings。生成时 manifest 文件本身的 SHA-256 为
`4e133da039288b73895caefc2851c1673ee567965ae5e16133231ba0cc1a9283`。

## Commands and Tests

主要只读检查与测试命令：

```bash
pwd
git status --short
sed -n '/## P1-D1/,/## P1-D2/p' NEXT_STAGE_EXPERIMENT_GUIDE.md
.venv/bin/python --version
.venv/bin/python scripts/audit_phase2_history.py --check-only
.venv/bin/python scripts/audit_phase2_history.py
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts/audit_phase2_history.py
.venv/bin/pytest -q tests/test_phase2_history_audit.py
.venv/bin/python -m pytest -q
PYTHONPATH=. .venv/bin/python -m pytest -q
```

另使用只读 Python 检查脚本解析 JSON/JSONL 的 record count、唯一键、字段完整性、
模型 metadata、引用关系和目录树摘要。

## Test Results

- P1-D1 audit `--check-only`：PASS，11/11 gate checks 为 true。
- P1-D1 专项测试：`2 passed in 0.59s`。
- `pip check`：`No broken requirements found.`
- `compileall`：PASS，无输出错误。
- 完整回归第一次直接运行 `.venv/bin/python -m pytest -q`：在收集既有
  `tests/test_day13_policies.py` 时失败，因为其导入 `scripts.run_day13_experiment`，
  但仓库根目录未进入导入路径；未执行测试用例。
- 按该既有导入结构显式设置 `PYTHONPATH=.` 后完整回归：`78 passed in 7.86s`。
- 正式历史树审计前后文件数、字节数和 10 个 tree SHA-256 全部一致。

## Acceptance Checklist

- [x] 已读取四份要求的根目录指南及最近日报。
- [x] 已记录项目根目录和 Git 状态。
- [x] Day 7--14 每个正式 run、配置、summary、关键数据和日报均可定位。
- [x] Day 10/11 最终模型及 metadata 均可定位。
- [x] 已生成可重复核查的 SHA-256 manifest。
- [x] 已检查重复 task/branch ID。
- [x] 已检查 Checkpoint hash、split 和 controller_type，并记录所有 schema 缺口。
- [x] 已核对 Day 8 的 24 logical tasks 与 43 attempts。
- [x] 已核对 Day 9 的 20 Checkpoints、60 action records 与适用性。
- [x] 已核对 Day 10/11 的训练来源和 feature schema。
- [x] 已核对 Day 13 的 180/180 一一对应关系。
- [x] 已通过专项测试、依赖检查、编译检查和规范化完整回归。
- [x] 历史产物审计前后摘要一致。
- [x] 未修改外部 Recovery-Bench。
- [x] 未进入 P1-D2 或任何后续任务。

## Known Issues

1. 仓库当前内容全部未被 Git 跟踪，无法用 commit ID 证明历史来源；本地 manifest
   是从本次审计开始的完整性基线。
2. Day 8 的 43 个 attempt 没有 `controller_type`。这是明确记录的 schema 缺口，
   不做历史数据回填。
3. Day 9 的 20 个 Checkpoint 使用 `state_hash`，没有独立 `checkpoint_hash` 字段。
   所有 20 个 `state_hash` 都存在；未来消费者需要显式 adapter。
4. 既有完整测试依赖仓库根目录位于 `PYTHONPATH`；不设置时会在 Day 13 测试收集
   阶段失败。本次未为此修改旧代码或测试。
5. Day 14 的 3 条 Recovery-Bench trial 均标记为不适合 branch utility；它们只证明
   read-only adapter/demo 产物存在，不能并入 Day 9--13 的分支实验样本。

## Remaining Work

P1-D1 无剩余核心工作。P1-D2 尚未开始；等待用户明确指令后再执行。是否建立 Git
基线、如何适配 `checkpoint_hash` 命名、是否统一测试导入路径均属于后续独立决策。

## Status: PASS

P1-D1 的所有核心 Gate 已通过。该 PASS 仅表示历史基线审计完成，不表示新的研究
假设、风险模型或策略效果得到验证。

## Git Commit

未创建 commit。仓库在本次任务开始前即整体处于未跟踪状态；本次未擅自初始化、
添加或提交文件。
