# P2-D2 Connection Smoke Test — Final Two-Worker HTTPS/API Verification

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_connection_smoke_20261125_002`。
最终 Gate：**CONNECTION_SMOKE_BLOCKED**。

本轮在本地 Runner 兼容性审计阶段停止。真实 SSH=0、Tunnel=0、HTTPS GET=0、
GPU 查询=0、Qwen/Agent=0；没有连接失败、HTTP 状态码或新的 TLS 握手结果。
此前真实 TLS Run 004 仍为 **TLS_EVIDENCE_CAPTURE_STAGE_B_PASS**，没有被撤销或改写。

当前正式 Smoke Runner 固定 90 秒、T+0/T+45 各请求两个 Worker，总计 4 次 GET，
并在第一次 GET 前启动两个隧道。本次授权只允许每 Worker 一次、总计 2 次 GET，
且 Worker 0 完整 PASS 后才能启动 Worker 1。当前没有参数能选择该顺序和次数。
Runner 还没有本次要求的双 Worker PID/CUDA/GPU 所有权预检。

按任务“如果现有 Runner 存在未解决的实现缺口，先报告，不要临时重构”的要求，
没有运行旧计划、临时修改调度或另写 SSH/TLS 框架。

## 2. 前置 Gate、Git 与历史证据

已读取最新 TLS 004 报告、先前 Smoke BLOCKED 报告、TLS Forensic、Connection Stability
Hardening、当前 Smoke Runner、ConnectionLifecycle/SSHProcess 及其测试和配置。

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支 main。
原有 Stage A 未提交修改全部保留，完整状态在 baseline.json。
本轮没有修改代码、测试、冻结配置、TLS Classifier、V1 或历史 Fixture。
没有 reset/checkout/clean/stash，没有 Commit/Push/fetch。

基线覆盖 **406 个历史文件、29 份历史 Manifest**。执行前及结束后逐项核对记录中的
SHA-256 和大小，全部匹配。历史 Smoke 001、TLS 002/003/004 及其原始结果均保留。
原 Manifest 没有大小字段的条目按已记录 SHA 校验，不补造历史字段。

## 3. 当前正式 Runner 的实际调用链及冲突

入口：`scripts/run_p2_d2_connection_smoke.py`。

```text
main
  → specs_from_host（只展开本地 SSH 配置）
  → ConnectionLifecycle.launch(specs)
      → 先启动 worker-0，再启动 worker-1，两者均 READY
      → 启动监测线程
  → observation_window
      → T+0: GET worker-0, GET worker-1
      → T+45: GET worker-0, GET worker-1
      → T+90: 结束
  → get_count 必须为 4 才能 PASS
```

| 项目 | 当前实现 | 本次授权 | 结论 |
| --- | --- | --- | --- |
| GET 数量 | 固定 4 次，无单轮模式 | 总计最多 2 次 | 不能直接执行 |
| Worker 1 隧道 | 首次 Worker 0 GET 前已创建 | Worker 0 PASS 后才允许创建 | 顺序不兼容 |
| 单 Worker 观察 | observe() 始终要求两个 adapter | 第一阶段只有 Worker 0 | 不能简单少传一个 spec |
| 所有权预检 | SSH 配置/密钥检查，无远端 GPU/Worker 审计 | PID、命令、CUDA_VISIBLE_DEVICES、GPU 映射 | 未接入 |
| 原始响应和阶段审计 | 保存模型 ID/状态汇总，无独立原始响应及完整 TCP/TLS trace | 两个独立请求/响应和 TLS 审计 | 证据路径不足 |

ConnectionLifecycle.launch 明确要求两个 Worker spec，并统一启动；observe() 直接遍历
两者。因此不能只传 Worker 0、删掉 T+45 或改一个调用参数来满足新计划。
本轮没有猴子补丁生产路径、放松监测、伪造 Worker 1 adapter 或用旧 4 次 GET 代替授权。

runner_compatibility_audit.json 保存当前源码事实及无进程 Fake Clock 复现：得到
T+0/0/45/45 的四次 probe，Fake 时间推进至 90。它不是真实 90 秒连接监测或 HTTP。

## 4. 本地环境检查

| 检查 | 本地结果 |
| --- | --- |
| RISK_AGENT_API_KEY | 已设置；没有用于请求，没有保存值或其哈希 |
| RISK_AGENT_CA_BUNDLE | 已设置 |
| CA 文件 | /home/three/.config/risk-aware-smolagents/pki/ca.crt，存在且可读 |
| CA SHA-256 | 97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef，匹配冻结值 |
| SSL Context | 初始化成功；check_hostname=true，CERT_REQUIRED |
| 专用 SSH 私钥 | 文件可读，权限私有；未输出内容 |
| known_hosts | 文件存在且可读；本轮未发起认证 |
| 本地 8000 / 8001 | /proc/net/tcp、tcp6 被动检查无 LISTEN，端口空闲 |
| 当前 HTTP Client 源码 | Client/Transport trust_env=false、retries=0、follow_redirects=false |

环境存在凭据不等于本轮 API 鉴权通过，Context 初始化不等于完成真实 TLS。
模型为自部署 Qwen/vLLM，不需要 OpenAI 云端 Key，也没有生成新 Key。
端口检查使用被动本地表，不新增 TCP 探测连接。因为 Runner 计划冲突，未继续远端检查。

## 5. 超时与 SSH 隧道实现审计

当前 Smoke 的 HTTP 参数仍为 connect=5 秒、read/write/pool 各 10 秒；没有单一整体
10 秒 deadline。本轮没有把远程模型配置中的 connect=30 混作 Smoke 参数，也没有
延长预算。TLS 004 的 5 秒是 OpenSSL 总进程预算，与 HTTPX 的 connect 阶段预算不同。

现有 SSHProcess 使用 -F /dev/null 隔离配置，指定专用 key/known_hosts，-N/-T，
严格 Host Key、BatchMode、IdentitiesOnly、ExitOnForwardFailure、ControlMaster=no、
ControlPath=none，单个显式 -L；不会同时使用 ClearAllForwardings=yes 清除转发。
本轮没有更换认证方式、创建 Tunnel 或关闭任何历史连接。

生命周期仍是已验收的 Popen 句柄归属和 close(expected_identity)；并未被替换。
其 snapshot 会主动 connect_ex 检查端口，既有 Forensic 已记录该行为。本轮只审计
源码，没有实际调用 SSHProcess、launch、snapshot 或正式 main 的授权分支。

## 6. 两个 Worker 的当前结果

用户授权的预期映射是 worker-0→物理 GPU 0、worker-1→物理 GPU 1。
本轮没有把这项期望或历史 PID 写成新的远端观测，也没有操作 GPU 2–7。

| 检查项 | Worker 0 | Worker 1 |
| --- | --- | --- |
| 预期物理 GPU / Port | 0 / 8000 | 1 / 8001 |
| 当前 PID/Command/CUDA/GPU 归属 | 未检查 | 未检查 |
| SSH Tunnel | 未创建 | 未创建 |
| TCP Connect | 未执行 | 未执行 |
| TLS Handshake | 未执行 | 未执行 |
| Certificate Chain | null | null |
| Hostname Verification | null | null |
| API Authentication | null | null |
| HTTP Status | null | null |
| 预期 Model ID | Qwen/Qwen3-8B | Qwen/Qwen3-8B |
| 实际 Served Model ID | 未收到 | 未收到 |
| Request Latency / Provider Request ID | null / null | null / null |
| Response Usage | 未收到；models GET 本身不产生 Completion Usage | 同左 |

没有把 Worker 0 的历史 TLS 成功复制给 Worker 1，也没有把未执行记为模型/Agent 失败。
本次没有 401/403/5xx、证书错误、TLS 超时或模型 ID 不匹配事件可供诊断。

## 7. 资源释放与原始证据语义

本次 owned SSH/Tunnel 创建数均为 0，没有需要 terminate/wait 的连接资源；
resource_release.json 明确记录不适用及零残留。没有向任何已有 PID 发信号，没有
关闭 ControlMaster，没有远端文件、Worker、vLLM、GPU 或 Docker 操作。

未产生 ssh_client_stderr.raw 或真实 HTTP 响应 bytes，不用空 raw 文件冒充已经采集。
两个 Worker 的 request/response JSON、remote_preflight、worker_ownership 和 TLS 审计
都是明确标记的本地 NOT_ATTEMPTED/NOT_RECEIVED/NOT_EXECUTED 状态，非远端实测记录。
ssh_process_audit.jsonl 只有本地阻塞事件；没有伪造 SSH PID、退出码或清理时间。

## 8. 测试与工程检查

相关离线测试：**151 passed in 6.38s，0 failed，0 skipped**。
覆盖 Connection Smoke、ConnectionLifecycle/SSHProcess、TLS Evidence/Manifest、
TLS Classifier、API Client 接线及 Provider 安全审计。均使用 Mock/Fake；防护入口
拒绝真实网络和 SSH/OpenSSL 工具，未发送 Qwen 请求。

compileall、git diff --check、敏感材料扫描及历史 Manifest 检查通过。
防护入口阻止的 urllib3 导入期本地 IPv6 bind 能力检查有记录，不是建立了连接。
没有新增或修改代码/测试，因此未重复整个 692 项全仓测试；此前 692 结果仍是 TLS
004 任务保存的历史结果，不冒充本轮新执行数量。

```text
.venv/bin/python runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261125_002/offline_test_guard.py -q tests/test_connection_smoke.py tests/test_connection_lifecycle.py tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py tests/agent/test_day5_real_wiring.py tests/agent/test_provider_response_audit.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## 9. 产物与 Manifest

Run：`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261125_002/`。

包含 baseline、run_manifest、environment_preflight、runner_compatibility_audit、
remote_preflight、worker_ownership、两个 Worker 独立 request/response 状态、
ssh_process_audit、tls_audit、resource_release、gate_summary、原始证据缺失说明、
离线测试记录、完整性核验、本日报副本和 artifact_manifest.json。

全部文件 fsync，记录 Run ID、类型、路径、大小、SHA-256 及 ctime 观察值。
Manifest 独占 pending/no-clobber 硬链接原子发布，重读检查每项和文件集合；排除自身。
它证明本地阻塞审计文件完整，不证明已经产生真实网络证据。历史 Manifest 原样保留。

## 10. 遗留工作与 Full Five-Task Gate 条件

**目前不具备进入 P2-D2 Full Five-Task Gate 的条件。** 本次两个 GET、鉴权、模型 ID
和 GPU/Worker 当前映射均未验收；TLS 004 PASS 只解决已经完成的真实 TLS 前置验证。

剩余范围已明确，应集中到当前 Smoke Runner 的请求调度和证据接线：支持单次顺序
两 Worker GET，Worker 0 PASS 后再创建 Worker 1，复用现有 SSHProcess/Lifecycle，
接入双 Worker 只读所有权审计及独立请求/响应/阶段证据。保持已批准 HTTP 超时、严格
TLS、零重试/Failover，不需要新的 SSH Transport 或 TLS 诊断框架。
当前任务不授权临时实现这些缺口，本轮未修改。
002 保留为本地 BLOCKED 证据；后续真实执行应由用户另行授权新的唯一 Run，不复用本目录。

## 11. 最终状态与停止

**CONNECTION_SMOKE_BLOCKED**。本轮实际 GET=0、SSH=0、Tunnel=0、GPU=0、Qwen=0、
Agent=0、重试=0、Failover=0。没有新的 TLS 服务失败，旧 Runner 计划与新授权不兼容。
未 Commit/Push；已停止，不自动进入 Full Five-Task Gate、P2-D3 或正式采集。
