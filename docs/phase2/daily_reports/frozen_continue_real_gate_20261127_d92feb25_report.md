# Frozen CONTINUE Real-Qwen Gate — Stage B

日期：2026-11-27。执行基线：`8d5655c4c6de92d51ee9020a321232725692af58`。
Run ID：`p2_continue_real_20261127_d92feb25`。

## Objectives

按用户“好的，继续”的授权，使用已通过 Stage A 的冻结统一入口执行五任务真实
Qwen CONTINUE 验收。固定 T1–T5、Worker 0/1/0/1/0、每任务一次 Source Attempt、
零自动重试、零替换、零 Failover；失败即停，不进入正式采集。

## Local Environment Work

初始工作区干净。读取仓库规范、最新 Stage A 报告、入口说明、冻结配置及历史
成功连接/身份核验实现。父清单及入口清单校验通过；本轮未修改任何生产代码、
参数、Prompt、Catalog、Split、Verifier 或历史 Run。

私有准备与审计目录：
`runs/phase2_continue_real_preflight/p2_continue_stage_b_20261127_d92feb25/`。

API Key 和 CA Bundle 环境变量均已继承；只记录存在性，不记录凭据值。
CA 存在、可读、未过期，与可信基准 Hash 一致；专用公钥和 Host Key 指纹匹配。

| 冻结输入 | SHA-256 |
| --- | --- |
| 父清单 | `c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7` |
| 统一入口清单 | `bd86d505193ee511d4c362273f62a62a8a913769e7196ba7476ab9493c7585d7` |
| 可信 CA | `97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef` |

## Remote Environment Work

唯一一次 SSH Remote Command 完成只读身份、Worker、模型权重和 GPU 预检。
复用此前已成功运行的所有权/模型核验程序，仅增加远端 CA Hash、当前 Run 绑定、
检查时间及本次 Python 进程的超时保护。远端以 python3 -B - 执行，
未发出远端文件管理或实验目录创建命令。

SSH 使用原专用密钥、严格 known_hosts、BatchMode、禁用密码/键盘交互和连接复用，
预检设置 ClearAllForwardings=yes。使用已验证的 Windows TCP Proxy，认证和加密仍
由 WSL OpenSSH 完成，没有使用 Windows 密码登录或新的身份。

预检 SSH rc=0，进程已回收；单调总耗时 92.7658 秒。GPU 两次采样间隔至少 65 秒，
只查询当前项目使用的 GPU 0/1；没有查询或分配其他 GPU，也没有据进程表为空判空闲。

| Worker | 当前 PID | 已核验物理 GPU | Endpoint | 所有权 |
| --- | --- | --- | --- | --- |
| worker-0 | 382420 | 0 | 127.0.0.1:8000 | true |
| worker-1 | 382422 | 1 | 127.0.0.1:8001 | true |

身份为既定 Ubuntu 22.04 容器、root；监听 socket、当前进程 start identity、
命令摘要、部署 state、GPU UUID/选择器及模型路径均匹配，前后进程记录一致。
这些 GPU 编号是本次现有 Worker 的观测映射，不是新的永久资源分配。
模型配置、索引和五个权重分片均现场 SHA-256 校验通过；服务版本与冻结要求一致。

真实 CLI 启动前再次验证新鲜证据的时间、Worker、容器及唯一 Run ID 绑定。
两个 HTTPS Endpoint 的严格 TLS、鉴权及 /v1/models 身份预检均通过，Model ID 为
Qwen/Qwen3-8B。没有关闭 TLS 验证、重启 vLLM、修改参数或占用新卡。

## Work Completed / Final Gate

**最终 Gate：CONTINUE_ACCEPTANCE_BLOCKED。**

T1 已完整执行，Source 在第一个合格 Observation 后捕获。独立 Restore、六类
Hash、首次实际模型输入、Provider/Parsing 审计及真正 resume 到 final_answer 均通过。
原确定性 Verifier 返回 false；按照当前入口“Verifier=false 也停止 Gate”的条件，
本次停在 T1，未执行 T2–T5。没有重跑或替换任务。

| 项目 | 结果 |
| --- | --- |
| T1 Source Attempt / capture step | 1 / 1 |
| Source / CONTINUE 真实模型请求 | 1 / 3 |
| 六类 Hash / 首次输入匹配 | 全部 true / true |
| Source 不变 / Checkpoint 不变 | true / true |
| 真实任务工具执行 | read_csv、filter_records、aggregate_records，均成功 |
| Agent termination | final_answer |
| component.complete_chain | true |
| Verifier / accepted_y_continue | false / 0 |
| Canonical failure category | verification_failure |
| T2–T5 Source Attempts / Completions | 0 / 0 |

本次确实观察到一条真实完整 CONTINUE → Verifier 闭环，而不是一次受控恢复请求。
闭环成立与任务判定成功是两个分别记录的结果；不据此将五任务 Gate 改成 PASS。
T3 没有运行，因此没有声称订单更新后重读的真实模型路径已通过。

## T1 Offline Evidence Review

任务要求读取 CSV，筛选 category=segment_1，再按 region 对 units 求和。
实际三个工具步骤全部成功，聚合结果与冻结 expected 完全相等：

```json
{"north": 11.0, "south": 8.0}
```

模型最后调用 final_answer 时，answer 是自然语言字符串：

> The sum of units grouped by region for category 'segment_1' is: north - 11.0, south - 8.0.

冻结 Verifier 直接比较 result 与 expected.result。前者为字符串，后者为字典，
因此 checks.result=false；Verifier 本身没有异常。该 0 反映当前严格输出判定契约，
不能解释成工具没有算出正确数值。

任务 Prompt 没有显式要求 final_answer.answer 保持字典类型；这是后续需要审查的
输出契约问题。本轮没有补提示、转换自然语言、放宽 Verifier 或事后重标。

另在新的本地审计工作区使用同一 Verifier 做两个离线对照：原回答仍为 false，
已观察到的聚合字典为 true。对照保存在 t1_offline_output_contract_review.json，
不产生新的模型调用或训练标签，也不替代本次真实原回答和标签 0。

## Provider Audit and Budgets

四次 Provider 响应及四次 Parsing Outcome 完整关联；SDK 可见原始响应 Hash 均复核
匹配，Usage 均完整，Parsing 均 success，Finish Reason 均 tool_calls，没有 length 截断。

| 用量 | 数值 |
| --- | --- |
| Prompt tokens | 11,172 |
| Completion tokens | 496 |
| Total tokens | 11,668 |
| 四次 Provider 单调延迟之和 | 26.9665 秒 |
| T1 Source + Restore + CONTINUE wall | 27.1756 秒 |
| 整个真实 CLI 单调耗时 | 31.4609 秒 |

未触及 1024 Completion、8192 Context、50,000 总 Tokens、10 Model Calls、12 Tool Calls
或 900 秒任务预算；Source 已耗预算被继承。最大单次 Completion 为 292 Tokens。
完整真实模型数据仅来自 Worker 0；Worker 1 完成服务/模型身份预检，没有 Completion。

## Resource Release

- 任务的两个 SDK 客户端均已关闭，关闭错误为 0；API 预检客户端由原 finally 关闭。
- 两条本次拥有的 SSH 隧道均退出 0，已回收；结束记录为 PROCESS_EXITED / LOCAL_PORT_CLOSED。
- 只读检查本地 /proc/net/tcp 与 tcp6，8000/8001 均无监听。
- 本地 Windows 进程检查确认本次预检和两条隧道的 3 个 Proxy helper 均已不存在，
  没有额外终止任何 Windows 进程。
- 没有关闭历史隧道、终止他人进程或操作远端 Worker。

失败后没有再向模型服务器发 SSH、TLS、HTTP 或 Qwen 请求。没有取得 Worker 的运行后远端快照，
不以预检记录冒充运行后观测。远端无服务启停/配置/GPU 管理操作由执行命令和调用记录证明。

## Artifacts Generated

原始 Run：`runs/phase2_continue_acceptance/p2_continue_real_20261127_d92feb25/`。
gate_summary_final_audit.json、原 Artifact Manifest 和全部 **71 个**已发布文件
的集合、大小和 SHA-256 均复核匹配，原样保留。

独立准备/审计目录保存本地预检、一次只读 SSH 的原始两流及退出链、当前模型/GPU/
Worker 证据、Gate argv 与进程记录、原 CLI stdout/stderr、只读输出契约审阅、端口/
Proxy 释放核验、回归结果和 real_gate_final_integrity.json。
全部原始材料保持私有，不进入 Git。

本次标记 formal_experiment=false、research_dataset_eligible=false；只有一个真实
Smoke 标签 0，不属于正式训练数据。T2–T5 没有被填标签或自动记为失败。

## Commands and Tests

真实验收唯一入口：

```text
.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode real-five-task-gate --authorize-remote --run-id p2_continue_real_20261127_d92feb25 --remote-evidence <本次私有证据.json> --connection-spec <本次私有连接配置.json>
```

本地离线回归：

```bash
.venv/bin/python runs/phase2_continue_real_preflight/p2_continue_stage_b_20261127_d92feb25/offline_test_guard.py -q tests/test_continue_acceptance_entry.py tests/test_continue_readonly_refresh.py tests/test_protocol_freeze.py tests/agent/test_b3_continue_gate.py tests/agent/test_continue_outcome.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_continue_real_preflight/p2_continue_stage_b_20261127_d92feb25/offline_test_guard.py -q tests/test_continue_acceptance_entry.py tests/agent/test_continue_outcome.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Test Results

- 预检期间相关离线回归：69 passed，19.22 秒。
- 实验后入口/结果分类回归：35 passed，12.32 秒。
- compileall、diff 检查、敏感信息扫描及完整性复核通过。
- 本轮没有生产代码修改，未重复全仓；基线全仓结果为 843 passed。
- 9,043 个历史文件 SHA-256、父 99 个冻结输入及入口清单均保持不变。
- 回归 Guard 拦截依赖的 IPv6 支持探测 bind，没有新增真实请求。

## Acceptance Checklist

- [x] 当前 SSH/容器/Worker/GPU/模型/CA 预检通过。
- [x] 双 HTTPS Endpoint 严格 TLS 与模型身份验证通过。
- [x] T1 完成真实 Source、首个点捕获、独立恢复、完整 CONTINUE 和 Verifier。
- [x] 六类 Hash、精确输入、持续预算、完整审计和资源释放成立。
- [x] 按冻结条件在 T1 Verifier=false 后停止，未重跑或进入正式采集。
- [ ] 五任务真实 Gate 通过：未满足；T2–T5 未运行。

## Known Issues / Remaining Work

应先离线审查 Task Prompt、final_answer Schema 与 Verifier 的输出类型契约。
如需明确结构化输出，必须事先统一规定并经过用户确认，以新版本/新 Run 验证，
不能只对本次失败回答做归一化或追求把 Smoke 标签都改成 1。

还应明确工程 Gate 检查的是“有效结果链路”还是“任务必须成功”：当前入口把
Verifier=false 纳入停止条件；风险研究本身需要合法的 0，不能把有效失败标签与
连接、恢复或审计异常混为一类。本轮没有自行修改该停止条件。

完成后停止，等待用户审核；不继续 T2–T5，不进入正式采集，不扩展 RETRY/REPLAN。

## Status: BLOCKED

**CONTINUE_ACCEPTANCE_BLOCKED**。T1 真实闭环有效，但最终输出未满足冻结 Verifier，
五任务验收未通过。历史 Gate、真实原回答和标签 0 均保持原样。

## Git Commit

仅按仓库规范交付本脱敏报告；原始 Run、私有凭据/连接配置、证书和模型不上传。
最终提交 SHA 保存在交付回复和本地 Git 记录，不嵌入本文件造成自引用。
