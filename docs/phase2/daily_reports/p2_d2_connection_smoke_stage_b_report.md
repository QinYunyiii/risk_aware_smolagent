# P2-D2 Connection Smoke — Stage B Report

日期：2026-11-26（Asia/Shanghai）。
Run ID：`p2_d2_connection_smoke_20261126_001`。
最终 Gate：**CONNECTION_SMOKE_BLOCKED**。

## 1. 执行摘要

本次在本地正式入口审计阶段停止，未执行真实 Connection Smoke CLI。
真实 SSH认证/Remote Command/隧道/TLS/HTTP GET/Qwen/Agent均为0。
本地凭据、CA、专用密钥、known_hosts和端口检查通过；冻结源码与 Stage A验收记录完全匹配。

唯一阻塞是本次任务第8节要求：Worker 0完整PASS后、创建 Worker 1隧道之前，
再次验证 Worker 1的远端PID仍存活、GPU映射和Endpoint未变。
当前冻结CLI只在开始执行一次双 Worker远端预检，之后仅观测本地已创建的Adapter。
没有该阶段的远端二次核验调用。

本次同时明确禁止修改代码，因此没有临时更改入口、添加后台检查器或绕过该要求。
没有重跑历史Run、没有消耗实际GET预算、没有自动换号或重试。
Stage A自身的验收结果及历史TLS 004结果原样保留；本报告仅判断本轮Stage B前置条件。

## 2. 正式调用链与阻塞证据

已阅读本次指定的Stage A集成报告、TLS Stage B 004报告和此前Smoke Final报告，
核对正式CLI、SSHProcess/ConnectionLifecycle、部署归属预检、HTTPS Client和Manifest实现。

Stage A Manifest共531个文件重新核验通过；正式三份实现SHA-256与最终成功Fake CLI
`run_manifest.json`中的验收哈希一致，没有把Git HEAD相同当作未提交代码相同。

源码：`scripts/run_p2_d2_connection_smoke.py`。

| 位置 | 实际行为 |
| --- | --- |
| 255–257行 | 循环前唯一一次远端双Worker归属预检与本地记录验证 |
| 260–266行 | Worker 1只检查Worker 0已PASS，再调用observe和launch_worker |
| observe() | 遍历本地已登记Adapter，检查本地进程及监听；尚未创建Worker 1 Adapter时无法查询其远端PID/GPU |
| 283–284行 | Worker 0请求与审计成功后记录PASS；没有触发新的远端Worker 1复核 |

`tests/test_connection_smoke_integration.py`也显式断言整个正式CLI至多一次ownership调用。
它证明Stage A规定的初始双Worker预检和串行调度，不能证明第8节要求的后续远端复核。

两次GET、严格顺序、零HTTP重试、禁用Redirect/代理和TLS校验等Stage A能力均核对一致；
没有重新制造此前“四GET/提前双隧道”的旧阻塞。
`runner_compatibility_audit.json`保存此具体调用链和单一阻塞码。

## 3. Git与冻结保护

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支：`main`。
本轮没有修改源码、测试、配置、TLS Classifier、V1、Checkpoint v2或历史Fixture。
开始时已有Stage A的四个修改文件及历史未跟踪材料，均保留原字节。
完整前后状态在baseline.json和final_git_status.txt；本轮仅新增本日报及独立本地审计目录。
没有Commit、Push、fetch、reset、clean、rebase或强制历史操作。

## 4. 本地环境与SSH配置

- 当前执行环境已继承RISK_AGENT_API_KEY和RISK_AGENT_CA_BUNDLE；只记录存在性。
- CA文件存在可读，SHA-256匹配冻结值
  `97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`。
- SSLContext初始化成功，CERT_REQUIRED且check_hostname=true；未执行TLS握手。
- 专用密钥可读、权限私有，公钥指纹匹配原有可信值；没有输出私钥。
- known_hosts可读，目标Host Key匹配原有可信指纹。
- 只执行一次本地`ssh -G risk-agent-remote`配置展开，以及本地ssh-keygen公钥元数据检查；
  这些没有建立SSH会话，不能写成远端认证成功。
- 正式SSHProcess源码显式BatchMode、严格Host Key、IdentitiesOnly、ControlMaster=no、
  ControlPath=none、-F /dev/null和单个LocalForward，禁止历史配置转发/密码交互。
- 本地8000/8001通过/proc/net/tcp和tcp6被动检查无LISTEN，没有主动TCP端口探测。
- HTTP Client/Transport均trust_env=false、retries=0，follow_redirects=false。
- HTTP超时仍为connect=5、read/write/pool各10秒；历史OpenSSL的5秒进程预算未变且未执行。

存在凭据和初始化Context不代表鉴权或握手通过。本轮未生成、复制、打印或哈希API Key。
本地审计准备最初使用系统python3时发生缺少risk_agent模块错误；当时目录尚未创建、
远端尝试为0。改用项目既有.venv解释器完成本地审计，错误保存在local_preparation_errors.json。
它不计为SSH或网络重试。

## 5. 双Worker、GPU、隧道与请求结果

| 验收项 | Worker 0 | Worker 1 |
| --- | --- | --- |
| 预期Physical GPU | 0 | 1 |
| 预期Remote Endpoint | 127.0.0.1:8000 | 127.0.0.1:8001 |
| 预期Model ID | Qwen/Qwen3-8B | Qwen/Qwen3-8B |
| 当前PID/Process Identity/Command Fingerprint | 未远端查询 | 未远端查询 |
| 当前CUDA_VISIBLE_DEVICES / GPU UUID / Listener归属 | 未远端查询 | 未远端查询 |
| 真实所有权门禁 | 未执行 | 未执行 |
| Tunnel创建次数 | 0 | 0 |
| GET次数 | 0 | 0 |
| TCP / TLS / Certificate Chain / Hostname | 未执行 | 未执行 |
| HTTP Status / Response Body / Actual Model ID | 无响应 | 无响应 |
| API Key鉴权 | 未执行 | 未执行 |
| Request Latency | null | null |

没有把用户确认的预期GPU映射或历史PID当作本次实时观测，没有复制Worker 0历史TLS成功。
无真实TLS/HTTP异常链，没有401/403/5xx或模型不匹配事件；均属于未执行状态。
未创建空raw文件冒充响应或SSH日志，raw_evidence_absence.json明确说明缺失原因。

## 6. 实际操作预算和资源释放

真实SSH连接=0，Remote Command=0，Tunnel=0，TLS握手=0。
Worker 0 GET=0，Worker 1 GET=0，总HTTP=0，Qwen Completion=0，Agent/Tool/Checkpoint/Restore=0。
自动重试=0，Failover=0，GPU计算及GPU查询=0，vLLM/Docker/远端写入=0。
本地ssh -G属于配置元数据查询，单独记录，不混入真实连接计数。

没有本次owned PID或隧道需要terminate/wait/reap，资源释放记录为“不适用、残留0”。
没有关闭外部SSH、历史ControlMaster或远端Worker，没有操作GPU 2–7。

## 7. 回归与工程检查

本轮相关离线回归：**190 passed in 10.05s，0 failed，0 skipped**。
覆盖Connection Smoke、实际Worker Ownership、SSHProcess生命周期、HTTPS Client接线、
TLS Evidence/Classifier、Manifest及Provider安全审计。

```text
.venv/bin/python runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261126_001/offline_test_guard.py -q tests/test_connection_smoke.py tests/test_connection_smoke_integration.py tests/test_connection_lifecycle.py tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py tests/agent/test_day5_real_wiring.py tests/agent/test_provider_response_audit.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

compileall、diff检查和本轮敏感材料扫描通过。
离线防护拒绝真实网络和SSH/OpenSSL/curl/wget；依赖导入时一次本地IPv6 bind能力探测被阻止，
原堆栈保存在test_results.json，没有形成连接或监听。
本轮无代码修改，未重复全仓731项；731是Stage A已保存结果，不冒充本轮新执行数量。

## 8. 历史证据、Manifest和交付目录

目录：`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261126_001/`。

历史 **7055个文件**SHA-256保持不变；**119份历史Artifact Manifest**、
**3980个条目**均按文件和大小/哈希复核通过。
包括历史Smoke 001/002、TLS 004及Stage A审计原始结果，不改写旧分类。

本次包含run_manifest、remote_preflight、worker_ownership、ssh_process_audit、
两个独立request/response状态记录、tls_audit、resource_release、gate_summary及完整artifact_manifest。
它们明确标记LOCAL_PREFLIGHT_BLOCKED/NOT_EXECUTED，不是远端实验证据。

所有新增证据fsync后，以独占pending/no-clobber硬链接原子发布Manifest，排除自身，
随后重新读取并校验文件集合、大小和SHA-256。未静默截断原始数据，没有待清理连接。
Manifest校验只证明本地阻塞审计完整。

## 9. 最终判断及停止

**CONNECTION_SMOKE_BLOCKED**。尚不具备进入P2-D2 Full Five-Task Gate的条件；
本轮两个真实GET、TLS、鉴权和当前模型身份均未执行，不能替代真实验收。

需要先由用户决定本次第8节的处理边界：保留“Worker 1启动前再次远端复核”时，
冻结入口需允许补充这一现有预检调用；若继续保持代码完全冻结，则须明确调整该复核要求。
本轮不实施任何一种变更，不添加诊断框架，不请求更多探测，不重试。
固定Run 001现已作为本地BLOCKED记录封存，后续不得覆盖或复用，Run ID需由用户另行指定。

本次已停止，未自动执行Full Five-Task Gate、Qwen、P2-D3或正式采集，未Commit/Push。
