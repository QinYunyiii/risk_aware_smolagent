# Recovery-Bench Reuse Audit

审计对象：`/home/three/projects/agent-intervention/recovery-bench`，Git HEAD `c5f83f2b`  
配套运行时：本地虚拟环境中的 Harbor 0.4.0  
审计方式：读取 tracked source、已安装 Harbor source、Docker 元数据和历史产物 schema；没有执行 benchmark、没有修改外部仓库。

## 1. 总体结论

Recovery-Bench 可复用 Harbor 任务编排、Docker trial 环境、确定性 verifier、ATIF 轨迹和最终结果/Token 统计。它的 recovery 是“从失败轨迹抽取命令，在新环境中尽力重放，然后让 recovery agent 继续”，不是完整 Checkpoint/Restore。它没有保存 Agent 内部状态、任意任务文件/数据库/服务状态的统一快照，也没有同一 checkpoint 的隔离多分支协议。

因此推荐：**不修改 Recovery-Bench 原仓库；在本项目中以只读 Adapter 接入任务、轨迹、结果和 verifier，并自行实现 Checkpoint/Restore/Branch Runner。**

## 2. 架构与关键代码

| 文件 | 关键实现 | 证据 |
|---|---|---|
| `recovery_bench/generate_traces.py` | CLI 参数校验并调用 `run_pipeline()` | `main()`，34-125 行 |
| `recovery_bench/pipeline.py` | 构造和执行 `harbor run`；初始轨迹、失败任务筛选、恢复运行 | `_build_harbor_cmd()` 91-138；`generate_initial_traces()` 141-185；`run_pipeline()` 313-430 |
| `recovery_bench/utils.py` | 从 `result.json` 的 verifier reward 识别失败任务；结果目录和 usage 处理 | `get_unsolved_tasks()` 57-94；`save_usage()` 349-380；`aggregate_usage()` 394-430 |
| `recovery_bench/replay.py` | 解析 ATIF，提取 tool calls/命令和消息，执行重放 | `_extract_from_step()` 61-113；`replay_via_exec()` 182-232；`replay_via_tmux()` 235-269 |
| `recovery_bench/agents/recovery_mixin.py` | 定位旧轨迹，保存历史消息并构造 recovery instruction | `RecoveryMixin` 15-53 |
| `recovery_bench/agents/recovery/terminus.py` | setup 时重放命令，run 时注入历史并执行 agent loop | `setup()` 53-64；`run()` 95-134 |
| Harbor `trial/trial.py` | 环境启动、Agent、verifier、artifact、异常和结果生命周期 | 230-355、520-615 行 |
| Harbor `environments/docker/docker.py` | 每 trial Docker Compose 环境、bind mounts、启动/清理 | 114-151、242-248、328-398 行 |
| Harbor trajectory models | `Step.tool_calls`、`observation`、`metrics`、时间戳 | `models/trajectories/step.py` 14-77；`tool_call.py` 8-22；`observation_result.py` 9-31 |

其中 Harbor 文件根目录为：

```text
/home/three/projects/agent-intervention/.venv/lib/python3.12/site-packages/harbor
```

## 3. Agent 执行流程

```text
python -m recovery_bench.generate_traces
  -> generate_traces.main()
  -> pipeline.run_pipeline()
  -> pipeline.generate_initial_traces()
  -> subprocess: harbor run --dataset terminal-bench@2.0 ...
  -> Harbor Job / Trial
  -> environment.start()
  -> agent.setup() / agent.run()
  -> verifier.verify()
  -> result.json + agent/trajectory.json + verifier/* + trial.log
  -> environment.stop()
```

失败恢复路径：

```text
get_unsolved_tasks(): verifier reward <= 0
  -> recovery Harbor trial starts a fresh task environment
  -> locate prior agent/trajectory.json
  -> extract recorded keystrokes/commands
  -> best-effort replay commands
  -> inject prior messages according to full/none/summary mode
  -> run recovery agent and verifier
```

`replay_via_exec()` 对 timeout 和其他异常只记录后继续（`replay.py:203-227`），因此重放不保证恢复出的环境与原历史状态一致。

## 4. 日志与轨迹能力

### 4.1 代码支持

ATIF `Step` 支持：

- `step_id`、ISO 8601 `timestamp`、source 和 message；
- `tool_calls[]`，每项包含 `tool_call_id`、`function_name`、`arguments`；
- `observation.results[]`，可包含 `source_call_id` 和工具输出 `content`；
- `metrics`，包含 prompt/completion/cached tokens、cost 及 extra。

Trial `result.json` 支持：

- task/trial/config/agent 信息；
- `AgentContext` 的输入/输出/cache token 和 cost；
- verifier rewards；
- 类型化 `exception_info`，包含 exception type/message/traceback/time；
- environment setup、agent setup/execution、verifier 各阶段开始和结束时间。

对应证据：Harbor `models/trial/result.py:13-70`、`models/agent/context.py:8-34`、`models/trajectories/metrics.py:8-42`。

### 4.2 历史产物实证

本地 `runs/` 中找到 90 个 `result.json` 和 89 个 `agent/trajectory.json`。抽样轨迹：

- 42 steps；
- 63 tool calls；
- 41 observations；
- 41 个 step 有 metrics；
- 42 个 step 有 timestamp；
- function 为 `bash_command` / `mark_task_complete`，arguments 含 `keystrokes` / `duration`；
- observation 有 `content`，但该样本的 41 个结果都没有 `source_call_id`。

90 个结果中 17 个包含 trial-level exception：16 个 `AgentTimeoutError`，1 个 `VerifierTimeoutError`。

### 4.3 对 MiniToolBench-R 字段的适配判断

| 所需信息 | 当前能力 | 结论 |
|---|---|---|
| 每步工具名 | ATIF `function_name` | 可复用 |
| 工具参数 | ATIF `arguments` | 可复用 |
| 工具返回 | observation `content` | 可复用，但需补强 call/result 关联 |
| 异常和失败原因 | trial-level `exception_info`；工具级错误多嵌在文本 | 需扩展显式 `error_type` |
| Token | step metrics、final metrics、AgentContext、`usage.json` | 可复用并统一口径 |
| 执行时间 | step timestamp 和阶段 timing | 可计算部分延迟；缺显式逐工具 latency |
| Worker/model/prompt/tool version | model 部分存在，其余不完整 | Adapter 统一补充 |
| checkpoint/environment/branch IDs | 不存在 | 新项目实现 |

## 5. `pipeline.py` 与 `per_task.json`

- `pipeline.py` 存在且是 Recovery-Bench 的 Harbor orchestration wrapper。
- tracked files、外部仓库（排除 run/results 大目录）及父项目中均未找到 `per_task.json`。
- 当前实际的逐任务产物是 `result.json`，以及 `agent/trajectory.json`、`usage.json`、`trial.log`、`verifier/*`、`artifacts/*`。
- 因此不能基于不存在的 `per_task.json` 设计 Adapter；应以当前 Harbor 0.4.0 schema 为准，并为 schema version 做显式校验。

## 6. Docker 环境能力

Harbor `DockerEnvironment`：

- 使用 task Dockerfile 或 docker-compose 定义环境（`docker.py:190-240`）；
- 使用 session ID 生成独立 compose project；
- 把 trial 的 agent/verifier/artifacts 目录 bind mount 到容器 `/logs/*`；
- 同 session 启动前清理 stale container，再启动环境；
- trial 完成后按配置 stop/down/delete。

判断：

- **任务级 fresh reset：支持。** 每个 trial 可从 image 重新创建容器；这属于重新初始化，不是恢复中间状态。
- **不同实验独立环境：基本支持。** 独立 session/project 和 trial log paths 提供隔离；但若 task 自定义 compose、外部服务或额外 mount 指向相同可变状态，仍可能串扰，必须由 Adapter 审计。
- **现有本地状态：** 3 个历史容器均 stopped，0 个命名 volume，挂载仅为各 trial 日志目录。
- **不能推出：** 容器内任意时点快照、SQLite 一致性备份、外部 API 状态、服务内存状态或多分支一致初态。

## 7. Checkpoint / Restore 审计

对 Recovery-Bench tracked Python 和实际 Harbor 0.4.0 Python package 检索后，Docker backend 没有 checkpoint/restore API。出现的 Daytona snapshot 是另一种 backend 的启动模板，不是本地 Docker trial 的中间状态恢复协议。

| 状态部分 | 当前能力 | 结论 |
|---|---|---|
| Agent Memory | 可读取旧 messages，并作为新 prompt context 注入 | 不是内部 memory/state 的精确快照 |
| 工具调用历史 | ATIF 记录并可抽取命令 | 可作为审计数据复用 |
| 工具执行状态 | 通过命令 best-effort replay 重建 | 无状态哈希和一致性保证 |
| 任务文件 | 命令重放可能再次创建 | 未直接快照，副作用可能不可重放 |
| SQLite/数据库 | 无 SQLite Backup API 或事务快照 | 不支持 |
| Mock API/服务状态 | 无统一快照接口 | 不支持 |
| 预算/seed/config | config/result 有部分信息 | 没有统一 checkpoint manifest |
| 指定历史状态 Restore | 无 | 不支持 |
| 同状态多独立分支 | 无 branch protocol/state hash | 不支持 |

结论：MiniToolBench-R 需要在本项目中重新实现任务级 Checkpoint/Restore 协议；可以复用 Recovery-Bench 的轨迹作为“重放输入/诊断材料”，但不能把 replay 标记为 Restore 通过。

## 8. Harbor 实际部署与接口

### 8.1 实际位置

Harbor 0.4.0 安装在本地 WSL 的父项目 `.venv` 中。它是 CLI/Python library，并通过本地 Docker daemon 执行 task environment；没有发现独立 Harbor HTTP service。

### 8.2 提交任务

Recovery-Bench `_build_harbor_cmd()` 构造：

```text
harbor run --dataset terminal-bench@<version>
  --agent ... | --agent-import-path ...
  --model ... --n-concurrent ...
  [--job-name ...] [--env docker] [--include-task-name ...]
```

由 `run_command()` 使用 `subprocess.run()` 执行（`pipeline.py:108-138,182-185`；`utils.py:296-306`）。

### 8.3 状态、日志和结果

- Harbor Trial 生命周期把 `config.json` 写入 trial 目录，最终写 `result.json`（Harbor `trial/trial.py:520-534,336-357`）。
- 日志和 verifier 位置由 `TrialPaths` 固定定义（Harbor `models/trial/paths.py:72-165`）。
- CLI 完成后显示结果路径，并建议 `harbor view <job-parent>`（Harbor `cli/jobs.py:1032-1041`）。
- 没有在 Recovery-Bench 中发现远程提交/轮询 Harbor API；状态读取应通过本地 job/trial 文件或本地 CLI Adapter。

### 8.4 失败任务恢复

- Recovery-Bench 可筛选 reward 不大于 0 的任务，并在新 trial 中执行 trajectory replay + recovery agent。
- Harbor `jobs resume` 是 job 级恢复入口，但对指定 error type 会先 `shutil.rmtree(trial_dir)` 再重跑（Harbor `cli/jobs.py:1067-1138`）。它不是中间状态 restore，而且是破坏性操作；本次没有调用，未来也不得在未确认数据策略时直接调用。

## 9. Branch Execution 能力

没有发现：

- checkpoint ID / branch ID；
- 同一规范化状态的 hash 校验；
- 每个候选动作独立复制可变文件/数据库/服务状态；
- Continue/Retry/Replan 三分支统一执行接口；
- 分支间 mutable volume 隔离检查。

多个 Harbor trial 能创建多个 fresh environment，但不能证明它们来自同一中间状态。Branch Runner 必须由新项目实现。

## 10. 模块复用表

| 模块 | 当前能力 | 处理方案 |
|---|---|---|
| Recovery-Bench 代码 | Harbor 命令编排、失败任务筛选、recovery orchestration | 只读 Adapter 复用，不直接修改 |
| Docker Images | 本地已有少量 Harbor 历史 image；Harbor 可按 task image 启动 trial | 本地审计后复用；按任务核实镜像 |
| Harbor | 本地 CLI/library 0.4.0 + Docker backend | Adapter 接入 CLI/schema，不当远程服务 |
| Agent 执行日志 | trial.log、result、phase timing、exceptions | 复用并标准化 |
| 工具调用轨迹 | ATIF tool calls/args/observations/metrics | 扩展 call/result 关联、error type、latency 和版本字段 |
| Checkpoint | 无完整中间状态快照 | 新项目实现 |
| Restore | best-effort command replay，不保证一致 | 新项目实现；replay 仅作辅助 |
| Branch Runner | 无同状态独立分支协议 | 新项目实现 |
| 任务验证器 | Harbor verifier reward 和输出 | 复用或包装；逐任务验证确定性 |

## 11. 最终复用建议

1. 新项目实现 `RecoveryBenchAdapter`，只读解析 Harbor 0.4.0 的 config/result/ATIF/verifier 产物。
2. 复用 Harbor Docker environment 和 verifier 时，为每个 run/branch 设置唯一 session/environment ID，并禁止共享可变 mount。
3. 轨迹转换保留原路径、schema version、task checksum 和 Git commit，新增 MiniToolBench-R 所需字段。
4. Checkpoint 独立保存 Agent Memory、任务目录、SQLite backup、Mock API/tool state、预算、seed/config，并生成规范化 state hash。
5. Restore 与 Branch Runner 在本项目实现；只有恢复后 state hash 一致才允许标记分支可比。
6. 不调用 Harbor 的 destructive `jobs resume` 清理逻辑，不覆盖历史 runs/results。

## 12. 审计限制

- 没有启动新的 Harbor trial，因此 Docker isolation 和 verifier 可重复性属于代码审计结论，尚非本项目的 end-to-end 验收。
- 外部 Recovery-Bench 仓库已有大量用户修改，故没有执行测试、format、checkout 或任何写入。
- 远程 GPU 与 HTTPS 不影响上述本地源码结论，但会阻塞完整 Day 1 Gate。
