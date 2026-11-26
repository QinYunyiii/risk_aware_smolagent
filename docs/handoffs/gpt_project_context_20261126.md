# 给 GPT 的项目交接：目标、进度、当前困难与决策边界

更新时间：2026-11-26，Asia/Shanghai。
项目：**Risk-Aware smolagents / MiniToolBench-R**。
本文件整理当前对话与本地已保存报告，供另一个 GPT 理解上下文。历史结论与最新状态分开记录。

## 1. 先读这段：现在到底卡在哪里

我们要研究工具型 Agent 在执行过程中何时会失败，并最终用风险估计帮助选择干预策略。
Checkpoint、独立 Restore、真实模型 CONTINUE 与确定性 Verifier 已经有工程实现和历史真实 Smoke 证据。
目前正在扩大到多步骤任务，推进 **P2-D2 真实五任务 Checkpoint/Restore Gate** 的前置连接验收。

**当前卡在本地 WSL 到实验室远端现有 Qwen3-8B Worker 的 SSH 转发连接，尚未通过最新双 Worker HTTPS Smoke。**
不是所有工作都没做完，也不是已经证明 Qwen 模型、Checkpoint 或 TLS 证书有缺陷。
最新一次真实执行中：初始双 Worker 远端归属检查成功，但 Worker 0 隧道没有在就绪预算内被检测为监听；
所以两个 HTTPS GET 都没有发出，Worker 1 的后续复核和隧道也没有执行。

最新离线取证发现：**本次与历史能成功监听的 Worker 0 SSH 参数完全相同，SSHProcess 类和就绪轮询逻辑也相同。**
然而本次缺少认证、bind 阶段日志和端口检测错误码，无法确定具体根因，结论必须保持 INCONCLUSIVE。

用户已经经历很多轮修补，希望尽快结束连接排障、回到研究主线。
请集中处理当前单一阻塞，不继续扩张框架，不把每个不确定性都变成新的大工程。

## 2. 研究目标与当前实验主线

长期目标：

1. 从 Agent 截至决策时刻的轨迹提取特征，预测后续失败风险。
2. 从同一个 Checkpoint 独立恢复，保证初始状态一致、分支隔离、预算与模型输入可追踪。
3. 通过真实模型继续执行到终止，再由确定性 Verifier 判定结果。
4. 在后续研究中比较 CONTINUE、RETRY、REPLAN 的效果、成本和适用时机。

目前主要风险定义：

\[
r_t=P(Y_{\mathrm{continue}}=0\mid X_{\leq t})
\]

- `Y_continue=1`：CONTINUE 后满足确定性成功判定。
- `Y_continue=0`：可判定的任务失败。
- `Y_continue=null`：结果不可判定，例如基础设施中断导致没有完整结果。
- 特征只能使用决策时刻已知信息，不能使用未来答案、Verifier 结果或未来成本。
- API/SSH 断连属于基础设施问题，不能直接标成 Agent 任务失败。

关键区别：

- B2 的一次受控恢复请求，用于验证输入和恢复接线，不等于完整 CONTINUE。
- B3 必须实际调用 `resume(stream=True)` 到 Agent 终止，再运行 Verifier。
- 工程 Smoke 的少量标签只能验证链路，不能当作正式风险训练集或正式研究结论。
- 当前不同时扩展三种动作，优先把 CONTINUE 结果链路和真实采集前置验收做好。

## 3. 系统部署与安全边界

| 位置 | 职责 |
| --- | --- |
| 本地 Windows + WSL Ubuntu 24.04 | 项目代码、smolagents、工具执行、Recovery-Bench/Docker、任务控制、Checkpoint/Restore、Verifier、日志与研究分析 |
| 远端共享实验室服务器 | 既有 Qwen3-8B 模型，通过 vLLM 提供 HTTPS OpenAI-compatible API |

远端 SSH 入口实际进入 Ubuntu 22.04 容器；宿主机操作系统未经审计。
远端模型返回 Tool Call，由本地执行工具，远端模型不直接访问本地文件系统或 Docker。

- 模型为自行部署的 Qwen3-8B，不使用 OpenAI 云端推理服务。
- `RISK_AGENT_API_KEY` 是现有自部署 API 的鉴权凭据，不要要求申请 OpenAI Key或擅自生成新 Key。
- API Key只从环境/安全配置读取，禁止输出或写入报告；不得打印 Authorization Header。
- TLS 验证、CA 和主机名校验必须开启，不允许 `verify=False` 或 `curl -k`。
- 不启动/重启/停止共享 vLLM，不抢占 GPU，不修改远端 Worker，不杀他人进程。
- 不迁移本地工具环境、不在远端重建 Recovery-Bench、不把模型下载到本地。

连接配置（连接元数据，不含密钥）：

| 项目 | 当前授权目标 |
| --- | --- |
| SSH Alias | `risk-agent-remote` |
| SSH Host / Port / User | `121.48.161.243` / `4035` / `root` |
| 预期容器 | `36f825bcc5da` |
| Worker 0 | 本轮指定 Physical GPU 0，远端 `127.0.0.1:8000` |
| Worker 1 | 本轮指定 Physical GPU 1，远端 `127.0.0.1:8001` |
| 预期 Model ID | `Qwen/Qwen3-8B` |
| 本地请求 | `https://localhost:8000/v1/models`、`https://localhost:8001/v1/models` |

GPU 0/1 是这些获批验证中已确认的现有 Worker映射，不代表以后启动服务永远固定使用这两张卡。
GPU 2–7不在本轮排查范围内。

## 4. 已完成哪些工作

| 阶段 | 已有结果 | 必须保留的限制 |
| --- | --- | --- |
| B1 / Protocol v2 | 协议冻结、真实风险数据语义与审计设计 | 不擅自改研究设计或标签 |
| B2 | Checkpoint v2、生命周期捕获、独立 Restore、分支隔离、预算/模型身份硬化；历史真实 Qwen Gate有通过记录 | 受控单次请求不是完整 Agent继续 |
| B3 | 真实模型 CONTINUE + Verifier历史工程 Gate通过 | 5个Day 5 Smoke Checkpoint不是正式训练集 |
| B4 | 真实Qwen工程 Smoke历史通过 | 工程标签不能当正式研究数据 |
| P2-D1 | 新增40个多步骤模板；结合既有模板形成80模板/160计划实例v2 Catalog，含F0–F6覆盖 | 参考Runner/离线Verifier通过，不等于160个真实Qwen任务已采集 |
| P2-D2 本地 | 多步骤Checkpoint、Provider原始响应/Usage/异常审计、1024-token配置及Fake Gate实现 | 不能用Fake结果替代真实Gate |
| P2-D2真实T1 | 1024-token的T1曾完成两次依赖工具调用、Checkpoint、独立Restore和一次恢复请求 | 是限定诊断/Gate任务，不是正式大规模采集 |
| P2-D2完整五任务Gate | 历史T1通过，T2恢复Provider请求基础设施失败，T3–T5未启动 | 整体仍BLOCKED，历史Run不能改成PASS |
| SSH Evidence Transport | V1帧、stdout/stderr字节精确、退出状态、Manifest，真实Stage B通过 | SSH传输成功不等于HTTPS请求成功 |
| TLS Evidence Capture | Worker 0单次真实远端OpenSSL握手已通过 | 远端本机TLS通过不代表经WSL隧道的HTTPS链路通过 |
| Connection Smoke Runner Stage A | 两Worker初始归属检查、严格串行、最多两GET、资源清理和Manifest接线通过 | Fake通过不等于最新真实连接可用 |
| Worker 1 Live Revalidation Stage A | W0 PASS后、W1隧道前重新获取实时身份并与初始快照比较；45项新增测试通过 | 最新真实Run未走到该步骤 |

截至最近实现回归：**776项全仓测试通过，0失败、0跳过**。
这是工程测试数量，不是成功实验数量、模型任务数量或正式训练样本数量。

## 5. 为什么前面反复修补

前期遇到的几个问题不同，不应统称为一个“TLS错误”：

1. 256-token完成预算下，多步骤响应曾出现截断/解析问题；后来以独立1024-token配置诊断，保持原实验不变。
2. 真实五任务Gate中发生Provider连接中断，需要明确SSH资源归属、生命周期和失败证据。
3. 部分TLS采集/分类阶段缺少足够原始握手证据，补齐了OpenSSL输出、SSH无损证据传输和Metadata接线。
4. 旧Smoke CLI固定4次GET，且提前创建两个隧道，与新授权“最多2次、W0成功后才启动W1”不兼容。
5. 修复调度后发现W1隧道前缺少实时远端复核，随后已在正式入口补齐并通过离线测试。
6. 最新真实执行卡在更早的W0隧道READY阶段，尚未进入HTTPS。

前几项工程修复已完成；当前不应从头重做TLS、SSH Transport或Checkpoint。

## 6. 三个关键真实Run：不要混淆

### A. 历史能监听的Smoke Run

Run：`p2_d2_connection_smoke_20261125_001`。

- W0 PID 956316、W1 PID 956329，两个独立隧道均有`connection_ready`记录。
- 本地监听为127.0.0.1:8000/8001。
- 历史wall时间从创建到READY约4.884秒、8.817秒，不是精确单调SSH阶段耗时。
- 后续W0首次GET在约5.137秒发生TLS握手超时，未取得HTTP状态码或Model ID。
- 整体Smoke仍BLOCKED，不能说“历史整个Smoke成功”。
- 两个隧道后来按owned生命周期清理，退出记录均为0。

### B. 真实远端本机TLS成功Run

Run：`p2_d2_tls_evidence_capture_stage_b_20261125_004`。

- 真实只读身份/CA/Worker门禁通过，一次SSH、一次远端OpenSSL TLS握手。
- TLS 1.3，CA、证书链、hostname验证通过，有双方Finished和完整握手摘要。
- OpenSSL进程约0.013995秒退出0；该时间不是WSL到远端HTTP请求延迟。
- 没有Tunnel、HTTP、Completion或Worker 1验证。
- Gate：`TLS_EVIDENCE_CAPTURE_STAGE_B_PASS`。

### C. 最新真实双WorkerSmoke失败Run

Run：`p2_d2_connection_smoke_20261126_002`。

- 唯一正式CLI执行，代码与最新Stage A验收哈希一致，没有Fake或临时改代码。
- 初始只读SSH退出0，重新发现并核验两个现有Worker的实时身份。
- 随后W0隧道child成功创建，但冻结30秒就绪deadline内没有观测到本地监听。
- W0 GET=0、W1实时复核=0、W1隧道=0、W1 GET=0。
- 真实SSH Client共2个：1次初始只读预检，1个未就绪的W0隧道child。
- 自动重试=0、Failover=0、Qwen Completion=0。
- child PID 1249034在清理前仍存活，stderr为0 bytes；受控terminate/wait后退出-15。
- PID已消失，本地端口已释放；原始结果与Manifest保持不变。
- Gate：`CONNECTION_SMOKE_BLOCKED`。

最新初始归属记录（只代表当时观测，后续必须重新核验，不能硬编码为当前可信）：

| 字段 | Worker 0 | Worker 1 |
| --- | --- | --- |
| PID | 382420 | 382422 |
| Process start ticks | 519951147 | 519951148 |
| CUDA_VISIBLE_DEVICES | 0 | 1 |
| GPU UUID | GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160 | GPU-aec607a6-84e2-0b80-eddd-996bf48c344d |
| alive / listener ownership | true / true | true / true |

这些进程参数声明Qwen/Qwen3-8B，但本次没有实际API响应，不能替代GET的Model ID验证。

## 7. 最新离线取证：确定了什么，没确定什么

最新Gate：**`SSH_TUNNEL_DIAGNOSTIC_INCONCLUSIVE`**。

已经确定：

1. 历史和最新W0 SSH argv逐项完全相同，参数顺序也相同。
2. `SSHProcess`类与历史SHA校验过的源码AST一致，就绪While循环也一致。
3. 本地静态`ssh -G`确认显式LocalForward保留，`ClearAllForwardings=no`，没有ProxyCommand/ProxyJump或历史RemoteForward。
4. `-F /dev/null`隔离用户及系统配置；没有错误套用会清除-L的`ClearAllForwardings=yes`。
5. 检测目标与-L绑定相同，都是IPv4 `127.0.0.1:8000`，不是错测8001或localhost解析问题。
6. 本次child创建成功，创建及清理开始时ALIVE，受控清理后EXITED/-15。
7. 初始只读SSH成功是另一条连接，不能证明后续隧道child也已认证成功。
8. GET未执行，所以不能把失败归因于Qwen API、API Key或远端HTTP服务。

尚不能确定：

- 隧道child是否完成TCP连接、SSH key exchange、用户认证。
- 是否实际尝试bind，以及bind返回什么。
- 是否存在过短暂监听。
- 每次connect_ex究竟返回连接拒绝、权限问题还是其他错误。
- 当时SSH服务端状态、完整继承环境及实际二进制版本是否与历史完全一致。

监听检测的限制：当前`snapshot()`把connect_ex返回0记为LISTENING，其他返回都记为CLOSED；
未保存具体errno、监听inode归属或启动阶段每轮采样。
因此“没有观测到可连接监听”不等于已经用内核证据证明“从未bind/listen”。

没有已确认的隧道启动/预算代码缺陷。不能制造“已找到根因”的结论，也不能据此直接延长30秒预算。

另有已确认但非致因的标签问题：`readiness`含`read`，异常分类器把就绪超时归成Provider read timeout；
它发生于异常诊断阶段，不会导致隧道不监听。原记录未改，报告已解释，不能把它当作连接根因修复。

## 8. 当前正式执行顺序与冻结预算

```text
初始双Worker实时所有权预检
→ W0 Tunnel
→ W0唯一GET /v1/models
→ W0 TLS / API Key / Model ID全部PASS
→ W1实时远端所有权复核
→ 与初始W1快照逐项比较PASS
→ W1 Tunnel
→ W1唯一GET /v1/models
→ 资源释放与Manifest核验
```

- 每Worker最多一次GET，总HTTP最多2。
- W0失败：W1复核/Tunnel/GET必须全0。
- W1复核缺字段、不匹配或审计失败：禁止W1 Tunnel和GET。
- 自动重试0，Failover0，无Completion、Agent任务或额外HTTP健康检查。
- HTTP超时：connect=5秒，read/write/pool各10秒；不是总10秒deadline。
- 隧道就绪预算：child创建及登记后设置单调30秒deadline；Popen之前工作不计入。
- SSH ConnectTimeout=15秒，不等于整个认证/转发生命周期总deadline。
- 历史OpenSSL总进程预算5秒，与上述预算分开。

## 9. 最小下一步建议与停止边界

当前没有证据支持“直接改某个参数就能修复”。不建议重构SSH Transport、新建诊断框架、重写Metadata、
修改API Client/TLS Classifier，或不断追加与当前阻塞无关的检查。

若决定继续，最小的证据补充方向是：在**现有SSHProcess同一次获批连接**中启用OpenSSH原生详细日志，
保留连接、认证、LocalForward setup/bind的实际阶段，帮助区分目前未知的故障类型。
这只是下一步候选，不是已执行行动或已确认修复；是否允许改日志参数及进行真实连接，需要用户单独授权。

尚未具备进入P2-D2 Full Five-Task Gate的条件。
即使以后双Worker HTTPS Smoke通过，也只能取得连接前置条件，不等于五任务Checkpoint Gate通过，
更不等于正式数据采集或风险模型研究完成。

最新网络授权已经执行并按失败停止，不能沿用旧授权自动重跑；新Run也不得覆盖或复用历史目录。
用户最近只要求离线取证和本交接文件，**当前没有新真实连接授权**。

## 10. 代码、仓库与工作区

- 本地项目：`/home/three/projects/risk-aware-smolagents`。
- GitHub：`https://github.com/QinYunyiii/risk_aware_smolagent.git`。
- 当前HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支main。
- 工作区有此前Stage A未提交修改和多个未跟踪实现/报告；不能只用HEAD判断实际执行版本，必须比源码SHA。
- 已修改但未提交的既有文件包括：
  - `scripts/manage_remote_vllm.py`
  - `scripts/run_p2_d2_connection_smoke.py`
  - `src/risk_agent/monitoring/connection_lifecycle.py`
  - `tests/test_connection_smoke.py`
- 新增但未跟踪的专项测试包括`tests/test_connection_smoke_integration.py`和`tests/test_connection_smoke_worker1_revalidation.py`，另有TLS/SSH相关文件。
- 保护全部现有用户修改，不reset/clean/stash或覆盖历史产物。
- 近期任务明确不Commit/Push，均未自动提交或推送；本交接也不执行Git交付。

主要实现：

| 文件 | 职责 |
| --- | --- |
| `scripts/run_p2_d2_connection_smoke.py` | 正式Smoke CLI、单次GET、严格顺序、Worker 1复核与审计 |
| `scripts/manage_remote_vllm.py` | 现有部署工具中的只读ownership分支，不调用其启动/停止操作 |
| `src/risk_agent/monitoring/connection_lifecycle.py` | owned SSHProcess、端口检测、30秒READY等待、受控清理 |
| `src/risk_agent/collection/ssh_evidence_transport.py` | 已验收SSH证据传输/V1，不重构 |
| `src/risk_agent/collection/tls_stage_b.py` | 已验收TLS生产接线及原子证据写入/Manifest |

## 11. 最重要的报告与原始证据

以下路径均相对于项目根目录。另一个GPT若无法访问本机，应请用户提供对应文件，而不是假装读过。

优先读这三份：

1. `docs/phase2/daily_reports/p2_d2_ssh_tunnel_failure_forensic_report.md`
   - 最新离线根因取证、两Run差异表、预算与未知事项。
2. `docs/phase2/daily_reports/p2_d2_connection_smoke_stage_b_final_report.md`
   - 最新真实执行、归属检查、Tunnel超时、清理、实际计数。
3. `docs/phase2/daily_reports/p2_d2_connection_smoke_worker1_revalidation_stage_a_report.md`
   - 最近最小修复、45项新增测试和776项全仓结果。

其他关键报告：

- `docs/phase2/daily_reports/p2_d2_connection_smoke_stage_a_integration_report.md`
- `docs/phase2/daily_reports/p2_d2_tls_evidence_capture_stage_b_20261125_004_report.md`
- `docs/phase2/daily_reports/p2_d2_full_gate_1024_stage_b_report.md`
- `docs/phase2/daily_reports/p2_d1_report.md`
- `docs/phase2/daily_reports/b3_continue_gate_report.md`
- `docs/phase2/daily_reports/b4_smoke_gate_report.md`

最新真实Run：

`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261126_002/`

重点文件：

- `gate_summary.json`：原Runner结果，不修改。
- `gate_summary_final_audit.json`：规范Gate及完整实际计数。
- `worker_ownership.json`：原初始实时归属Metadata。
- `connection_audit.jsonl`：原始3条生命周期事件。
- `resource_release.json`：实际argv、owned PID、退出-15及清理结果。
- `worker-0_ssh.stderr`：原始空stderr，0 bytes不是文件缺失。
- `artifact_manifest.json`：原核心9项清单。
- `artifact_manifest_final_audit.json`：追加审计42项清单。

历史能监听的Run：

`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261125_001/`

最新离线取证：

`runs/phase2_p2_d2_ssh_tunnel_forensic/p2_d2_ssh_tunnel_forensic_20261126_c64a3b3e/`

包括原始证据出处、参数比较、静态ssh -G、旧源码校验、Fake时钟测试和独立Manifest。

## 12. 最近测试与证据保护结果

| 工作 | 真实保存结果 |
| --- | --- |
| Worker 1复核实现后全仓 | 776 passed，0 failed，0 skipped |
| 最新真实Smoke后的专项 | 116 passed in 20.20s |
| 最新真实Smoke后的全仓 | 776 passed in 63.95s |
| 最新离线取证既有单测 | 30 passed in 3.11s |
| 最新离线取证Fake专项 | 9 passed，0 failed |
| 最新离线取证历史保护 | 7,393个历史文件不变；135份Manifest、4,489个条目匹配 |

测试均与真实实验分开；离线测试有阻止socket及外部连接工具的防护。
没有为了通过测试改写历史Fixture或历史Run。

## 13. 希望另一个GPT帮忙判断什么

请在以上事实基础上，帮助判断下一步最小、可验证且不会扩大工程范围的行动。

重点回答：

1. 同一argv与就绪代码，历史能监听而本次不能，现有证据究竟排除了什么，仍缺哪项决定性证据？
2. 是否只需在现有SSHProcess增加原生详细日志，即可区分连接/认证未完成、LocalForward建立失败和检测假阴性？
3. 如何限定下一次授权范围，避免重复Smoke、自动重试或新增诊断框架？
4. 什么明确条件满足后可以结束连接排障，回到真实五任务Gate和研究主线？

请不要把猜测写成根因，不要把进程存活等同于认证完成，不要把初始只读SSH成功套到另一条Tunnel连接。
请不要要求申请OpenAI API Key，不要默认重启vLLM或占用其他GPU，也不要把Fake工程通过当作真实实验成功。
