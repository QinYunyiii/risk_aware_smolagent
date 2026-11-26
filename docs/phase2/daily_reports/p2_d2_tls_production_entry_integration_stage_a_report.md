# P2-D2 TLS Production Entry Integration — Stage A

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。
最终 Gate：**TLS_PRODUCTION_ENTRY_INTEGRATION_STAGE_A_PASS**。

已补齐正式 TLS Stage B 执行入口：从真实 CLI 进入完整预检、Worker 所有权记录、既有
Metadata Producer/V1 Transport、原始两流、退出收据、冻结分类器及 Manifest 路径。
新 CLI 的默认模式不启动进程或网络；旧 CLI 的执行模式已转接到同一生产入口。
真实模式所需的远端 bootstrap 已实现，不再保留一个等待后续补写的空接线位置。

新增 **46 项专项测试**，最终全仓 **692 passed in 46.87s，0 failed，0 skipped**。
可检查样例直接从正式 CLI 启动，并执行生成后的远端控制程序，使用纯内存 Fake SSH、
Fake 预检和匿名管道 Mock 子进程；不以独立 Producer 测试替代入口验收。

本轮真实 SSH/TLS/HTTP/Qwen 均为 0。历史 TLS Stage B 003 的 BLOCKED 结论保持原样。
本次 PASS 是完整入口的离线工程验收，不是真实 TLS 成功或正式训练数据。

## 2. 原始阻塞与代码审计

修改前的实际链：

```text
旧 CLI remote_tls_diagnostic → isolation.run_authorized
  → SSHRunner / bounded_process → remote_program / remote_entry
  → 筛选后的摘要事件

新 Metadata 测试 → 预先构造的记录 → Producer / V1 → Fake 传输
```

两条链没有由正式执行入口接起来。原 Metadata 校验只允许 16 个字段，无法携带
Worker PID、进程 start identity、命令摘要和所有权状态。最近三份 TLS Stage B 报告、
实际脚本、采集器、分类器、Producer/Decoder 及测试均已核对；本轮先记录根因再修改。

## 3. 正式入口与完整调用链

正式脚本：`scripts/run_p2_d2_tls_evidence_capture_stage_b.py`。
唯一编排入口：`risk_agent.collection.tls_stage_b.cli_main / run_stage_b`。

```text
正式 CLI（默认 prepare；execute 需单独授权）
  → 读取经历史 Manifest 验证的 Worker 所有权基准
  → 建立 Run / Session / Worker 期望，校验本地 CA
  → 既有 capture_ssh_transport，先持久化期望并预留 Metadata 文件
  → 唯一 SSH 工厂，Connect=15 / Overall=30，无转发/复用/重试
  → 生成后的 remote_main
      → remote_preflight（真实读取 / 注入 Fake 使用同一个函数）
      → 完整 Worker Ownership Metadata
      → 既有 authorized_launch_after_preflight / produce_session
      → 既有 V1 start 扩展 / stdout、stderr data / exit
  → 既有 Decoder、Metadata Artifact、原始字节、生命周期、核心 Manifest
  → verified_remote_evidence / 原始 Wire 退出收据
  → 原冻结分类器的离线原始输出回放
  → 派生审计文件及追加完整 Manifest
```

没有第二套 SSH Transport、第二个 Metadata Producer 或新的 V1 Stream ID。
生产者、编码器、解码器、退出语义、原始采集器和监督预算继续复用已有实现。

## 4. 旧摘要路径的处理

旧脚本 `run_p2_d2_remote_local_tls_isolation.py` 保留参数形式和历史 preflight 模式，
其已授权执行分支统一转交正式 CLI；不再调用旧 run_authorized 作为 Stage B 证据路径。
旧函数和历史测试接口仍保留在 isolation 模块中，供历史兼容，源码一字未改。
专项测试阻止 run_authorized、bounded_process、旧 remote_program 被新入口调用，
同时验证旧 CLI 的执行转接。新 CLI 可从文件直接启动，默认 prepare 已本地验证。

## 5. 真正的 Remote Preflight 与 Worker 证据

生产远端程序在同一 SSH Remote Command 内，先执行实际只读检查，再启动 TLS 子进程：

- hostname、uid、Ubuntu 22.04 容器身份。
- 已知 OpenSSL 可执行文件、实际版本和 s_client 参数支持。
- 原 CA 路径及实际 SHA-256。
- 仅检查已批准 Worker PID 的 /proc 项：PID、start ticks、运行状态、命令身份。
- 读取完整 cmdline 只在内存做 vLLM/port 身份检查和 SHA-256，不保存参数原文。
- 被动核对 /proc/net/tcp 的 127.0.0.1:8000 LISTEN 与该 PID 持有的 socket。
- 前后采样 start ticks，拒绝进程消失、PID 复用、所有权/命令/端口不匹配。

可信期望来自原真实审计中唯一通过的 Worker owner，读取前校验其原 Manifest 哈希。
默认基准为项目已保存的历史审计；不是从 PID namespace 进程表为空推断资源空闲。
没有 GPU 查询、模型请求或 Worker 管理。当前基准是否仍匹配，留待独立授权的真实运行核验。

真实预检的 version/help 是只读工具元数据子命令，不建立 TLS；正式握手只由一个受
5 秒监督的 s_client 子进程执行。本轮这两个工具边界也均为 Fake，没有执行 OpenSSL。

## 6. Worker Ownership 应用 Schema 扩展

原 16 个字段保持可读；新版是该记录加一个完整所有权字段组，共 24 个字段。
不改变 V1 必需字段或帧种类；仍位于既有 start 的应用 Metadata 扩展内。

| 字段 | 约束与来源 |
| --- | --- |
| worker_pid | 已观测的正整数 PID；PASS 不允许缺失 |
| worker_process_identity | /proc start ticks，数字字符串，核对原实例 |
| worker_command_identity | 固定安全身份标签 vllm-worker-0，无完整参数 |
| worker_command_fingerprint | 完整命令字节的 SHA-256，与历史可信摘要核对 |
| worker_endpoint | 127.0.0.1:8000，和 base endpoint 一致 |
| ownership_verified | true / false / null，分别表示验证通过、失败、尚未验证 |
| ownership_evidence_timestamp | UTC ISO 时间，实际预检生成 |
| preflight_error | 固定安全错误码或 null，不记录异常中的参数/凭据 |

既有 worker_id、容器、hostname、user、Run/Session 继续共同绑定。PASS 要求完整字段和
ownership_verified=true；FAIL 可以保留未知字段为 null。字段组不完整、未知额外字段、
凭据形式或不安全类型均拒绝。未添加命令行原文、API Key、私钥或证书私密材料。

本地期望仍明确标为 LOCAL_EXPECTATION_NOT_REMOTE_OBSERVATION。远端时间与期望时间
不要求相等，但实际时间字段纳入 Metadata Hash 和文件 Manifest，不绕过完整性检查。
新 Decoder/读取器已实际读取历史 16 字段样例，原文件与 Manifest 未修改。

## 7. 正式 CLI Fake E2E

测试从 `scripts.run_p2_d2_tls_evidence_capture_stage_b.main` 调用，不直接以 Producer
替代入口。Fake SSH 接收正式入口生成的源程序，实际编译并执行其中的 remote_main、
remote_preflight、authorized_launch_after_preflight、produce_session 和权威 Encoder。
只在系统边界注入 Fake 预检方法及匿名管道 Mock Popen；没有替换生产控制流或帧协议。
Decoder、Metadata 文件、原始流、收据、分类器和两级 Manifest 使用正式代码。

另有默认真实采集器的离线 /proc、CA、版本/help fixture 测试，检查其实际读取和
Worker socket 归属算法；没有停留在一个预填 PASS 字段。命令 fixture 含合成敏感参数，
断言它没有出现在输出记录中。

可检查样例：本 Run 的 `formal_entry_fake_sample/production_entry_fake/`。
样例逻辑 Fake SSH 工厂 1 次、逻辑 Fake TLS 子进程工厂 1 次；**实际 OS 子进程、
真实 SSH、Remote Command、OpenSSL、TLS 均为 0**。样例 manifest/run_manifest 明确标记
INJECTED_OFFLINE_FAKE / formal_experiment=false，不能当作真实 TLS 产物。

| 样例文件 | bytes | SHA-256 |
| --- | --- | --- |
| remote_preflight.json | 991 | af75a9cf150185b71341928e833f08be152299db355dc0bfdceaa931c0abae4e |
| openssl_stdout.raw | 98 | 9ffdb1a7be128fe42b20e4b7e9a47fcb5b89169ad218c2777d88b7231016b5a6 |
| openssl_stderr.raw | 102 | 51679b3642313093f0fdd43f4089f7722704f339f31062da3c950a1a2d873864 |
| ssh_client.log.raw | 30 | 6b59d654fcacb92c3788e0c3228c80f19c965cd02419cf043029b3e8dbca0e6f |
| ssh_wire_stdout.raw | 3561 | 0bab3228be2a43e4d344edc8f55fbe35dceea00095d0ccb8f53c4531b1bf689d |
| remote_exit_receipt.raw | 1212 | 57d4502c18810d1e1aa1556a7ae31ad663324d4c445c381510648867388a16c4 |

样例中的 TLS 成功日志是合成输入；只证明同一正式入口能够生成和验证完整证据链。

## 8. Fail-closed 验收与时点

预先提供的 Metadata 缺失、PID/身份/所有权缺失或不匹配、Run/Session/Worker 不符、
序列化失败、Hash 不符，以及本地 Metadata 写入/fsync/审计失败，均从正式 CLI 验证：
SSH 工厂=0，TLS 工厂=0，失败原因文件保留。具体安全错误码与阶段独立记录。

另补充会话内首次发现的容器/CA/Worker 不匹配：如实记录 Fake SSH 工厂已经调用 1 次，
TLS 工厂为 0，远端 FAIL Metadata 保留；不能把已经建立的会话倒写为零启动。
会话中断、Wire Hash 损坏、exit 缺失均禁止 PASS、保留部分原始流并回收本次句柄，
不重试、不切换 Worker、不推断远端退出状态。

真实远端状态必须经 SSH 才能首次观察，因此“所有远端未知故障都保证 SSH 0”不具有
物理可行性。本轮验证的是本地已知/已提供的全部门禁故障零启动，以及会话内故障
不再执行 TLS/不伪造成功；不新增 ACK 协议或改变冻结 V1 来掩盖时序。

## 9. 原始流、收据、分类器与生命周期

OpenSSL 原始两流仍由原采集器逐字节持久化，公共文件名是原始证据的无损副本；
不把 Metadata 写入任一应用流或 SSH Client Log，不覆盖 exit 帧。测试包含 NUL、
非 UTF-8、CRLF、非零退出和分流。原始 Wire 中 exit 行按原 bytes 保存。
元数据不进入 Agent Memory 或 Checkpoint，没有调用这些模块。

分类前先验证核心 Manifest、Metadata 与 Wire、完整采集和可信 exit。随后把已验证
原始输出的行送入原 remote_entry 的注入 Runner，使用真实收据中的退出/超时结果；
这只是离线回放，不发起第二个请求。Classifier 文件没有改动。Verification: OK、
只有部分握手、fatal alert 或证书失败不会被包装层变成 PASS。
回放事件的时间是本地解析时间，不能当成远端精确 TLS 阶段时间；原始采集时钟域保留。

本地 SSH 生命周期仍由原 capture_process 管理，源程序通过受控 binary stdin 线程
投递，只有本次句柄可回收；异常路径不向 Worker/其他 PID 发信号。TLS 子进程仍由
原 produce_session 管理其 5 秒预算。stdin 字节投递另用匿名管道验证，不启动进程。

## 10. Manifest 与冻结项

原核心 Manifest 原子发布及验证机制保持不变。公共别名、分类结果、运行清单和释放
审计另以追加完整 Manifest 发布，使用相同 fsync + pending + no-clobber 硬链接模式；
重读每个文件核对 SHA-256/大小。原核心 Manifest 不事后改写，失败证据不删除。

修改前后 AST 对比确认以下函数/类原样：encode_frame、data_frame、exit_frame、
FrameEncoder、FrameDecoder、produce_session、ssh_argv、exit_semantics、
remote_wrapper_source、_remote_wrapper_source。只扩展应用记录校验及匹配的时间字段。
V1 常量/Stream ID、Exit Receipt、采集模块整体、TLS Classifier 整体、原 OpenSSL 参数、
5 秒预算、CA、Checkpoint v2、B2/B3/B4 和冻结模型配置未修改。

## 11. 测试结果

- 新增正式入口专项：**46 项**。
- 相关组合：333 passed in 7.85s；覆盖新入口、Metadata、SSH、采集、分类和旧 Isolation。
- 最终全仓：**692 passed in 46.87s，0 failed，0 skipped**。
- compileall（src/scripts/tests）、git diff --check、未跟踪文件额外空白检查：通过。
- 默认正式 CLI 直接运行 prepare：退出 0，未启动连接/工具进程。
- 敏感材料扫描、历史文件和 Manifest 验证：通过。

首轮 9 failed / 122 passed，原因是纯内存 Fake subprocess 对象遗漏 DEVNULL/PIPE 常量，
生成的生产程序在该 Fake 边界无法创建管道。补齐 Fake 常量后入口 40 项通过；
再增加默认采集器、历史基准、stdin、错误码及验证标志等 6 项，最终为上述 46 项。
开发失败记录保留，没有真实网络重试，也没有修改历史期望值使测试通过。

```text
.venv/bin/python runs/phase2_p2_d2_tls_production_entry_stage_a/p2_d2_tls_production_entry_stage_a_20261125_080905/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_tls_production_entry_stage_a/p2_d2_tls_production_entry_stage_a_20261125_080905/formal_entry_fake_example.py
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/run_p2_d2_tls_evidence_capture_stage_b.py
git diff --check
```

测试防护禁止网络及真实 SSH/OpenSSL 等工具。urllib3 导入期的本地 IPv6 bind 能力
探测在系统调用前被阻止并留痕，不是一次连接。全仓已有本地 Python Fake 生命周期
测试不属于远端操作；新增正式入口样例本身连 OS 子进程也没有启动。

## 12. 修改文件与 Git

修改两个既有文件：

- src/risk_agent/collection/ssh_evidence_transport.py：向后兼容的应用记录字段组及时间匹配。
- scripts/run_p2_d2_remote_local_tls_isolation.py：执行分支转接生产入口。

新增三个源码/测试文件：

- src/risk_agent/collection/tls_stage_b.py：单 Session 编排、真实预检和证据收尾。
- scripts/run_p2_d2_tls_evidence_capture_stage_b.py：正式 CLI。
- tests/test_tls_stage_b_entry.py：正式入口与系统边界离线验收。

另新增本日报及独立 Run。没有非必要诊断模块、第二套 Transport 或复制的 Producer。
HEAD 保持 `bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支 main。
原有未跟踪工作保留；未 reset/checkout/clean/stash，未 Commit/Push/fetch。

## 13. 历史完整性与审计目录

本轮目录：
`runs/phase2_p2_d2_tls_production_entry_stage_a/p2_d2_tls_production_entry_stage_a_20261125_080905/`。

保存修改前基线、Metadata 变更 patch、测试结果、默认 CLI/正式入口 Fake 样例、
兼容验证、最终清单和本报告副本。目录私有，原始产物不自动发布。
基线的 **300 个历史文件、22 份历史 Manifest** 全部匹配；未修改历史 Run。
历史 16 字段 Metadata 样例已通过新读取器验证；历史 Worker 基准的真实审计读取也通过。
除授权的两个既有文件，所有原有源码、测试、配置和未跟踪文件的哈希不变。

## 14. 资源与请求统计

真实 SSH=0、Tunnel=0、Remote Command=0、OpenSSL=0、TLS=0、HTTP=0、Qwen=0、
GPU/vLLM/Docker/远端文件操作=0。未修改远端 Worker、CA 或服务。
Fake 样例逻辑进程已完成 wait/close；没有本次真实连接资源需要清理。

## 15. 下一次真实 Stage B 的前置条件

生产执行路径已经存在：只需独立授权、新唯一 Run，以及继续可用的专用 SSH 配置、
RISK_AGENT_CA_BUNDLE 和经验证的历史 Worker 基准。正式入口会重新检查本地 CA，
再在唯一远端 Session 中核验实际身份、Worker 所有权和 CA，匹配后才握手一次。
不需要再补写 bootstrap、增加 Stream ID 或改写 Classifier。

真实执行仍必须明确使用 execute + authorize-stage-b；本轮未执行下面的命令：

```text
.venv/bin/python scripts/run_p2_d2_tls_evidence_capture_stage_b.py --mode execute --authorize-stage-b --run-id <新的唯一RunID>
```

若当前 Worker 与历史基准不符，入口将拒绝执行；本轮没有测量其当前状态，不据本地
Fake PASS 宣称真实 TLS、证书链、hostname 或 Connection Smoke 已通过。

## 16. 最终 Gate 与停止

**TLS_PRODUCTION_ENTRY_INTEGRATION_STAGE_A_PASS**。
完整正式入口 Fake E2E、应用 Schema 兼容、fail-closed、全仓回归及历史证据保护通过。
本轮结束，停止等待独立审核和授权；不自动真实 SSH/TLS、Smoke、HTTP/Qwen、
五任务 Gate、P2-D3 或正式采集；未 Commit/Push。
