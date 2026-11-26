# P2-D2 TLS Evidence Capture — Stage B / Final Verification 003

## 1. 执行摘要与最终 Gate

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_tls_evidence_capture_stage_b_20261125_003`。
最终 Gate：**TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**。

停在连接前的真实入口核验。真实 SSH=0、Remote Command=0、OpenSSL=0、TLS=0、
HTTP=0、Qwen=0；没有重试、Failover、Tunnel 或远端资源操作。
本次没有新的真实 TLS 结果，握手完成、证书链与 hostname 验证均为 null。

核心原因来自当前代码：已验收元数据传输链支持预先生成的记录，但真实 TLS CLI
仍调用旧的摘要采集入口。新模板没有完整的真实预检 bootstrap，且现有应用记录
拒绝 Worker PID、命令身份和所有权的附加字段。当前任务明确要求入口已经接通，
仍有缺口时停止、不允许临时补实现，因此没有在消耗真实连接额度前编写新的接线。

上一轮 Stage A 的 PASS 证明元数据传输和 Fake 流程通过，不证明真实预检入口完整。
此前“接线缺口已解决”的交付说明没有充分强调这一区别，需要纠正；不是本轮又
发现了新的 TLS 或远端服务故障。不修改历史 PASS、BLOCKED 报告或 Run。

## 2. 当前实现的直接证据

已读取任务指定的四份报告、三个实现模块及实际 CLI/测试。
本地审计与无进程探针结果保存在 `entrypoint_audit.json`。

| 当前位置 | 已核实行为 | 本次要求的缺口 |
| --- | --- | --- |
| scripts/run_p2_d2_remote_local_tls_isolation.py | 调用 run_authorized(root,args.run_id) | 未调用最新 capture_ssh_transport |
| remote_local_tls_isolation.run_authorized | SSHRunner/bounded_process 执行旧 remote_program | 仍是摘要事件链，无法提供要求的原始 OpenSSL 独立两流 |
| ssh_evidence_transport.remote_wrapper_source | 定义 authorized_launch_after_preflight(preflight_metadata) | 明确要求调用方另外提供经过审核的身份/CA/Worker bootstrap |
| tests/test_tls_preflight_metadata.py 的 Fake 端到端用例 | 给模板传入预先构造的合成记录，监督本地 Python 字节程序 | 不执行真实远端预检，也不是可直接替代真实 TLS 的生产入口 |
| preflight_metadata_bytes | 精确允许既有 16 个应用字段，拒绝未知字段 | worker_pid、worker_command_sha256、worker_ownership_verified 均被当前校验拒绝 |

本轮探针直接调用当前校验器：原合成记录可通过；分别增加上述 Worker 字段均被拒绝。
没有据此声称底层 V1 协议不能表示元数据。V1 start 扩展与传输能力本身仍然可用；
缺少的是完整生产预检入口及 Worker 详细证据的既有记录映射。
不能用一个预先填写的 PASS 字段替代实际 Worker 检查或补造具体 PID/所有权证据。

本次没有重新实现帧协议、替换生产者、修改应用 Schema，也没有改写远端模板来临时
增加门禁。旧摘要 CLI 也没有执行，避免请求已经发出而证据仍不满足要求。

## 3. Git 与冻结保护

- HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支 main。
- 原有未提交 Stage A 修改全部保留，没有 reset/checkout/clean/stash。
- 当前 ssh_evidence_transport.py 的 SHA-256 与 Stage A 保存的验收后副本一致。
- baseline.json 记录全部源码、脚本、测试、配置 SHA-256，以及原有未跟踪文件和 Git 状态。
- 全部配置哈希记录在 baseline.json 的 source_sha256 中；结束时匹配。
- 本轮仅新增本 Run 及本日报；没有修改已验收实现、TLS Classifier、V1、CA 或冻结参数。
- 未 Commit、Push、fetch 或访问 GitHub。

任务指定的同名日报属于历史 001，原样保留。本次新增带日期和 003 的独立报告，
不覆盖原报告和已冻结历史哈希。

## 4. 本地前置检查

固定 003 目录创建前不存在，使用独占创建；没有覆盖 002 或自动更换 Run ID。
审计文件独占创建，目录/文件私有权限，写入 fsync；最终清单原子发布后逐项复核。

CA：`/home/three/.config/risk-aware-smolagents/pki/ca.crt`，存在且可读。
SHA-256：`97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`，
与冻结值一致。没有读取输出或复制证书内容，没有替换 CA。

专用密钥 `/home/three/.ssh/risk_agent_audit` 存在、可读、无 group/other 权限；
known_hosts `/home/three/.ssh/known_hosts` 存在且可读。没有输出密钥或 API Key。
本轮未调用 SSH，包括未进行只读远端认证；不能把这些本地文件检查写成认证成功。

最新入口这一前置条件未成立，故没有创建或启动真实连接工厂；没有真实 SSH
Manifest、Remote Metadata 的启动准备或认证结果可供声称通过。

## 5. TLS 参数、预算与未执行状态

从原 remote_entry 的 AST 提取并记录冻结命令，没有执行：

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost
  -verify_hostname localhost -verify_return_error
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

TLS 进程预算 5 秒，SSH Connect=15 秒、Overall=30 秒、Attempts=1 的授权上限均未改变。
没有通过额外 SSH、OpenSSL 版本命令、TCP 健康探测或任何 HTTP 请求绕过本地阻塞。

| 项目 | 本轮状态 |
| --- | --- |
| SSH 连接/认证 | NOT_ATTEMPTED；退出码 null |
| 容器、用户、Worker PID/命令/所有权/监听 | 未重新观测 |
| 远端 CA、OpenSSL 路径及版本 | 未重新观测 |
| 真实 V1 Metadata 发送/接收/Hash | 未发生，结果 null |
| tls_handshake_started | false |
| tls_handshake_completed | null |
| certificate_chain_verified | null |
| hostname_verified | null |
| ClientHello / ServerHello / Certificate / Finished / Alert | 未观测 |
| OpenSSL Exit Status / TLS Classifier Result | null / null |
| remote_termination_status | UNKNOWN；没有远端进程被启动 |

本轮没有真实输入，分类器不运行；不把历史 Verification: OK、Fake 结果或退出码
补成当前握手完成，也没有新的证书拒绝/超时异常可以诊断。

## 6. 原始证据及产物语义

没有真实 OpenSSL stdout/stderr、SSH Client Log/Wire/stderr、Remote Exit Receipt 或
真实采集观察时间线。它们的字节数和 SHA-256 为 null，不是已观察到的空输出。
未创建空 raw 文件冒充实际采集。缺失原因列在 transport_verification.json。

remote_preflight.json 和 tls_preflight_metadata.json 是明确标记的本地
NOT_EXECUTED/NOT_RECEIVED 状态记录，不冒充经 V1 收到的真实远端 Metadata。
tls_classification.json、process_lifecycle.json、exit_status_audit.json、resource_release.json
均保留未执行/null/不适用语义，不伪造 PID、wait 收据或实际认证。

Run：`runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_003/`。
另有 baseline、run_manifest、entrypoint_audit、离线防护入口/测试记录、gate_summary、
完整性检查、本报告副本及 artifact_manifest.json。
清单证明本次本地审计文件完整，不证明已经拥有 TLS 原始证据。

## 7. 测试与历史 Manifest

本轮相关离线回归：**287 passed in 5.71s，0 failed，0 skipped**。
覆盖 TLS Evidence Capture、TLS Preflight Metadata、SSH Transport/Compatibility、
TLS Classifier、Remote-Local TLS Isolation。未修改代码，不重复运行整个 646 项测试。
此前 646 passed 是上轮已保存的结果，不混为本轮新测试数。

compileall、git diff --check、敏感材料扫描通过。
防护入口禁止网络及真实 SSH/OpenSSL 工具；urllib3 导入期 IPv6 bind 能力探测在
系统调用前被阻止并记录，没有外部连接或真实 TLS。

基线覆盖 **284 个历史文件、21 份历史 Manifest**；结束时全部匹配。
此前 TLS Run 002 的 21 项审计、SSH Stage B 两级 Manifest、Metadata Stage A 的样例
及清单均不变。源码、测试、配置和原有未跟踪文件全部保持其本轮前的哈希。
新清单对每项记录路径、大小、SHA-256、Run ID、来源及时间观察值，fsync 后以独占
pending/no-clobber 硬链接原子发布，重读并核对文件集合；不包含清单自身。

```text
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_003/offline_test_guard.py -q tests/test_tls_evidence_capture.py tests/test_tls_preflight_metadata.py tests/test_ssh_evidence_transport.py tests/test_ssh_evidence_compatibility.py tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## 8. 资源释放、后续条件与停止

没有创建真实 SSH/OpenSSL 进程、Tunnel 或远端资源，因此无需 terminate/wait；
清理记录明确“不适用”，不对既有 PID 发送信号。没有 GPU、vLLM、Docker、Worker
或远端文件操作，不能由此声称已重新测量远端服务存活。

当前 **不具备进入 Connection Smoke Test 的条件**：真实 TLS、证书链、hostname、
完整原始流及退出收据均未完成本轮验收。

剩余工作需要集中补齐一个真实预检执行入口，并一次性离线验收“真实检查接口 →
完整 Worker 证据 → 元数据 → 原始流 → 退出收据 → 冻结分类器”的全链路。
这超出本轮禁止实现变更的授权，因此没有自动开展，也不以继续堆叠摘要诊断代替。

最终 **TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**，停止等待审核；未 Commit/Push，
没有自动进入 Smoke、HTTP/Qwen、五任务 Gate、P2-D3 或正式采集。
