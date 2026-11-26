# P2-D2 Connection Smoke — Production Runner Integration Stage A

日期：2026-11-26（Asia/Shanghai）。

## 1. 执行摘要与 Gate

**CONNECTION_SMOKE_STAGE_A_PASS**。

正式 `scripts/run_p2_d2_connection_smoke.py` 已接入双 Worker 当前所有权预检，
按 Worker 0 → Worker 1 串行创建隧道、各执行最多一次 GET。通过正式 `main(argv)`
的完整 Fake E2E，运行实际生命周期、归属判定、HTTP Client、请求审计和 Manifest 发布代码；
只替换系统边界，不另造 Runner 或直接返回成功结果。

本轮为离线工程验收。**没有真实 SSH、Tunnel、TLS、HTTP、Qwen 或 GPU 操作证据**。
Fake PASS 不等于远程 Smoke PASS；Full Five-Task Gate 仍保留原有 BLOCKED 状态。
没有自动进入 Stage B、P2-D3 或正式采集，没有 Commit/Push。

## 2. 原始 BLOCKED 根因与实际调用链

已阅读 Connection Smoke Final、TLS Stage B 004、Connection Stability Hardening 报告，
并检查正式 CLI、ConnectionLifecycle/SSHProcess、现有部署预检及原子 Manifest 实现。

原始调用链：`main → manager.launch(specs) → 同时准备两个 Worker 的隧道 →
observation_window → T+0/T+45 各调用两个 Worker → 共四次 GET → cleanup`。
源代码中 `(0, 45, 90)` 的计划和 `get_count == 4` 的成功条件直接造成预算冲突。
`launch()` 一次遍历两个 Worker，造成 Worker 0 请求之前已经创建 Worker 1。
`observe()` 原来假定两个 Adapter 均存在，无法支持分阶段启动。

原 CLI 没有调用当前双 Worker 归属检查。`tls_stage_b.remote_preflight` 是冻结的单 Worker
TLS 专用预检，依赖经过历史 Manifest 核验的原始 PID/启动身份，不能直接当作当前双 Worker
发现逻辑。现有 `manage_remote_vllm` 已提供部署状态、进程命令和 GPU UUID 的交叉检查，
但其旧 `status` 会创建状态目录、探测 HTTP 和查询所有 GPU；本次没有调用该旧路径。

修复后的正式调用链：

```text
main(argv)
  → API Key 存在性、冻结 CA SHA-256、严格 SSLContext
  → 现有部署 remote(ownership-readonly)：同一次只读检查两个 Worker
  → 本地重新核验两个独立所有权记录
  → launch_worker(worker-0) → 原 HTTP Client 单次 GET → 审计持久化 → Worker 0 PASS
  → 再检查现有连接 → launch_worker(worker-1) → 单次 GET → Worker 1 PASS
  → finally 关闭 Client、关闭并回收本次拥有的子进程
  → 资源与冻结配置复核 → 原子 Manifest 发布及重读校验 → 最终 Gate
```

原 90 秒辅助函数保留供历史回归，正式入口完全不调用，也没有公开选择旧四 GET 计划的模式。
它不代表本轮执行了 90 秒监测。

## 3. 修改范围及复用

| 文件 | 本次修改 |
| --- | --- |
| `scripts/run_p2_d2_connection_smoke.py` | 正式两 GET 调度、双归属门禁、独立 Client、Trace/响应证据、PARTIAL、失败封闭发布 |
| `src/risk_agent/monitoring/connection_lifecycle.py` | 提取原启动逻辑为 `launch_worker`，保留双 Worker `launch` 接口；注册 Adapter 时加锁 |
| `scripts/manage_remote_vllm.py` | 在现有部署工具中增加只读归属分支；沿用 `remote()`，不执行旧状态/启动/停止脚本 |
| `tests/test_connection_smoke.py` | 原四 GET CLI 测试由新版正式入口 E2E 替代；历史 90 秒辅助函数期望不变 |
| `tests/test_connection_smoke_integration.py` | 新增 41 个专项用例，含完整 CLI、故障、归属字段和实际预检生产代码测试 |
| 本报告 | 离线集成交付与证据索引 |

复用原 SSHProcess/Popen 句柄归属和 `close(expected_identity)`；没有第二套 SSH 管理器。
复用 httpx2 Client/HTTPTransport；没有新的 HTTP Client 框架。
只在现有部署审计内增加缺失的当前监听发现和限定 GPU 查询，没有独立 GPU 审计模块。
复用 `tls_stage_b._write/_publish` 的独占写入、fsync、no-clobber 原子发布和 SHA-256 重读验证。
没有修改这些 TLS 公共实现、TLS Classifier、V1 Frame、Checkpoint v2 或预算协议。

## 4. 双 Worker 当前归属门禁

生产只读程序先核验容器 `36f825bcc5da`、root、Ubuntu 22.04，然后只查询
`nvidia-smi --id=0,1 --query-gpu=index,uuid --format=csv,noheader,nounits`。
不查询或操作 GPU 2–7，不分配 GPU，不根据进程表为空判断空闲，也不启动新服务。

通过当前 `/proc/net/tcp` 的 loopback LISTEN inode 发现实际当前 PID，要求唯一所有者；
随后读取其 `/proc/<pid>/fd`、两次 stat、cmdline 和仅在内存使用的 environ。
交叉核验部署现有 `worker-{i}.state` 的 PID/port/GPU/UUID。
不使用历史日报或历史 Run 中的 PID作为候选。

检查 Container、Worker ID、PID/启动 tick、存活状态、vLLM 命令与端口、完整命令 SHA-256、
CUDA_VISIBLE_DEVICES、物理 GPU/UUID、Endpoint/监听归属、项目模型路径及 served-model-name。
命令原文和 environ 不写入审计；输出固定安全身份标签和命令摘要。
两个记录必须分别通过，PID/UUID 不得共享，本地也重新计算门禁，不能仅相信 aggregate PASS。

| 检查 | Worker 0 | Worker 1 |
| --- | --- | --- |
| 本轮预期 GPU / 端口 | 0 / 8000 | 1 / 8001 |
| Fake CLI 全字段归属门禁 | PASS | PASS |
| 独立模型 ID / 严格 TLS 策略 / Bearer 传递 | Fake 验证通过 | Fake 验证通过 |
| 真实远端当前状态 | 未查询 | 未查询 |

另有测试直接运行实际 System 预检，读取模拟文件系统和 Mock GPU 查询，发现新 PID
12341/23452，覆盖实际 `vllm serve <path>` 的位置参数格式，并证明环境凭据未进入结果。
这是本地字节/文件 Fixture，不是实际远端观测。

未来生产入口包含一次部署只读 SSH 和最多两个独立隧道 SSH；本轮这些系统边界全部替换为 Fake。
预检程序由当前函数源码生成，经 SSH stdin 执行 `python3 -B -`，不创建远端文件或目录。

## 5. 串行顺序、请求预算及 TLS/鉴权

最终保留的成功 Fake CLI `execution_sequence.jsonl` 顺序为：

```text
Ownership Preflight
Worker 0 Tunnel
Worker 0 GET
Worker 0 PASS
Worker 1 Tunnel
Worker 1 GET
Worker 1 PASS
Cleanup
```

测试同时统计 Fake Process 工厂和 MockTransport **实际调用**，断言只有两个 Tunnel、
两个 GET，GET 路径仅 `/v1/models`，每个 Worker 恰好一次。
第二个 Tunnel 仅在第一个请求的审计写入与 PASS 事件后启动。
若 Worker 0 PASS 后连接退出，启动 Worker 1 前的再次观测也会拦截。
没有 Completion、额外健康 GET、Redirect Follow、HTTP 自动重试或 Worker Failover。

两个独立 Client/Transport 均使用 `verify=严格 SSLContext`、`retries=0`、`trust_env=False`、
`follow_redirects=False`。CA SHA-256沿用历史可信冻结值，Context 必须为 CERT_REQUIRED
且 check_hostname=true。API Key只从原设置解析，缺失即停，不申请或生成新 Key。
Mock HTTP 边界分别断言两个请求包含预期鉴权，但日志不包含 Key 或 Authorization Header。

HTTP 超时保持 connect=5、read/write/pool 各 10 秒；不是整体 10 秒 deadline。
历史 OpenSSL 5 秒进程预算独立记录，正式 Smoke 不执行 OpenSSL或额外握手探测。
没有更换 SNI、CA 或证书校验策略。

每次请求记录时间、耗时、异常链、状态码、Model ID、Provider Request ID、原始响应 bytes
及 SHA-256。TCP/TLS Trace采用白名单事件字段，不序列化 request、headers或底层 info 字典。
models GET 的 Usage记为不适用，没有伪造 Completion Usage。
Mock Trace 仅证明日志接线，不能当作真实 TLS 成功证据。

## 6. 正式 CLI 负向 E2E 与实际计数

| Fake 故障 | Tunnel 创建调用 | GET 调用 | 结果 |
| --- | ---: | ---: | --- |
| Worker 0 所有权不符 | 0 | 0 | BLOCKED |
| Worker 1 所有权不符 | 0 | 0 | BLOCKED；两者先验证 |
| 所有权 SSH 失败 | 0 | 0 | BLOCKED；不重试 |
| API Key缺失 / CA 不匹配 | 0 | 0 | BLOCKED；不执行远端预检 |
| Worker 0 Tunnel失败 | 1 次尝试 | 0 | BLOCKED；Worker 1 创建次数 0 |
| Worker 0 GET超时 / 模型不符 | 1 | 1 | BLOCKED；Worker 1 创建次数 0 |
| Worker 0 401 / Redirect / 证书错误 | 1 | 1 | BLOCKED；没有第二次请求 |
| 请求审计写入失败 | 1 | 1 | BLOCKED；清理已拥有资源 |
| Worker 0 PASS 后连接退出 | 1 | 1 | PARTIAL；Worker 1 创建次数 0 |
| Worker 1 GET失败 | 2 | 2 | PARTIAL；不再请求 |
| Worker 1 Model ID不符 | 2 | 2 | PARTIAL；不再请求 |
| Manifest发布失败 | 2 | 2 | INCONCLUSIVE；不报告 PASS |
| 资源清理审计失败 | 2 | 2 | INCONCLUSIVE；不报告 PASS |

Manifest失败时保留未发布候选和失败材料，正式 gate_summary明确为失败，退出码非零。
没有用改写历史 Run 或重试发布来制造 PASS。
专项测试还逐字段破坏归属数据，验证17个关键字段独立门禁；另验证直接脚本导入/帮助入口、
拒绝不安全 SSLContext、串行 launch_worker 的兼容性、重复启动拒绝及受控清理。

## 7. 资源清理、Manifest 与历史保护

所有 Fake CLI 场景都实际到达 finally 清理，断言每个创建的 Fake Adapter 都已调用归属清理。
清理故障场景保留失败收据，即使模拟进程已结束也不报告完整释放成功。
实际本轮创建的 SSH/隧道数为0，未终止任何外部进程，未操作远端 Worker。

成功及可封存失败场景的 Manifest覆盖 gate、归属、连接审计、顺序、两个独立原始响应/Trace、
SSH stderr与资源释放记录；逐项比对文件集合、大小、SHA-256。
Manifest写入故障场景保留为明确未封存，不把未发布候选当作有效成功证据。

执行前保存 HEAD、完整 Git状态、代码/配置/文档摘要和历史 Run文件摘要；保存四个修改文件的原始副本。
历史 **85份 Artifact Manifest、3,115个条目全部匹配**；执行前的 **6,523个历史 Run文件**
逐文件复核不变。冻结配置与 TLS Classifier/V1/TLS Capture源码均与基线一致。
没有修改历史 Fixture 的期望结果或历史实验分类。

## 8. 测试与离线防护

- 最终全仓：**731 passed in 48.75s，0 failed，0 skipped**。
- Connection Smoke / 生命周期专项：**71 passed in 6.46s，0 failed，0 skipped**。
- 新增专项文件共41例，替代旧CLI的2例后，总数从692变为731；历史辅助函数期望未修改。
- 全仓覆盖原有HTTPS Client、TLS验证、Manifest、历史TLS Stage B、SSH Transport等回归。
- compileall、git diff --check、敏感信息扫描和历史完整性复核全部通过。

命令和原始输出位于本轮独立审计目录；主要命令：

```text
.venv/bin/python <本轮审计目录>/offline_test_guard.py -q tests/test_connection_smoke_integration.py tests/test_connection_smoke.py tests/test_connection_lifecycle.py --disable-warnings --tb=short
.venv/bin/python <本轮审计目录>/save_final_fake_cli_evidence.py
.venv/bin/python <本轮审计目录>/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

防护钩子拒绝真实 socket connect/getaddrinfo/sendto/bind，以及 SSH/OpenSSL/curl/wget。
独立保存 Fake CLI 时还拒绝 nvidia-smi和Docker程序。依赖导入期间一次本地 IPv6 bind
能力探测被钩子阻止，保留原始堆栈；它没有建立监听或外部连接。
所有实际网络、SSH、TLS、HTTP、Qwen、GPU、vLLM、Docker及远端写入操作均为0。

## 9. 产物索引

`runs/phase2_p2_d2_connection_smoke_stage_a/p2_d2_connection_smoke_stage_a_20261126_173104`

- `baseline.json`、`before/`：修改前状态和源码副本。
- `fake_cli_acceptance_final.json`：最终18个正式 CLI Fake 场景的计数和摘要。
- `fake_cli_cases_final/`：逐场景完整正式入口输出、顺序、归属、请求、资源与 Manifest。
- `verified_full_pytest.txt`、`final_focused_pytest.txt`、`test_results*.json`：原始测试和阻止事件。
- `historical_manifest_check.json`、`final_integrity.json`：历史 Manifest、SHA及范围检查。
- `engineering_checks.json`、`final_git_status.txt`、`task_changes.patch`：工程/敏感信息检查与 Git状态。
- `artifact_manifest.json`：本轮本地离线交付清单，排除自身，不是网络实验证据。

早期轮次的 Fake和测试证据保留，最终以带 `_final` 的 Fake产物和 `verified_full_pytest.txt`
为准。未覆盖早期审计文件，也未修改任何历史 Run。

## 10. Git状态与剩余前置条件

Baseline/最终 HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支 main。
原有未提交/未跟踪修改全部保留；本次仅修改上列四个既有文件，新增专项测试和本报告。
本任务明确禁止 Git交付，**未执行 Commit或Push**。

离线生产入口集成已完成，不新增诊断框架。下一阶段仅在用户单独审核并授权后，
使用新的唯一 Run ID和当前实现，重新核验两 Worker现有服务、凭据和CA，执行限定预算的真实
Connection Smoke。真实当前 PID/GPU/服务/鉴权/Model ID仍需当次预检及两个 GET给出证据。
本轮没有执行该阶段，也未改变五任务 Gate的准入状态。
