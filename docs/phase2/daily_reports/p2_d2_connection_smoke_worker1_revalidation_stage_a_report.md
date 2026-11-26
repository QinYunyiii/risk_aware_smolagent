# P2-D2 Connection Smoke — Worker 1 Live Ownership Revalidation / Stage A

日期：2026-11-26（Asia/Shanghai）。
最终 Gate：**CONNECTION_SMOKE_WORKER1_REVALIDATION_STAGE_A_PASS**。

## 1. 目标及结论

仅修复正式Smoke CLI缺失的Worker 1实时复核调用。
初始双Worker预检保留；Worker 0完整PASS后、Worker 1隧道创建前，重新调用现有只读预检，
比较Worker 1初始与新取得的身份，复核和审计持久化均通过后才允许第二个隧道与单次GET。

本轮为**离线实现和Fake验收**。真实SSH、Tunnel、TLS、HTTP、Qwen、GPU/vLLM/Docker和远端写入均为0。
没有Commit/Push，没有执行真实Smoke、完整Gate、P2-D3或正式采集。

## 2. 原始BLOCKED根因及实际调用链

已读取原Stage A集成报告、Stage B阻塞报告、正式CLI、部署归属预检、SSH Evidence Transport、
SSHProcess生命周期、HTTPS Client和Manifest实现，并通过源码追踪而非沿用口头结论。

原正式调用链为：初始dual ownership → Worker 0 tunnel → GET/TLS/API/model通过 →
Worker 0 PASS → observe本地已登记Adapter → Worker 1 tunnel → GET → cleanup。
只有循环前的ownership_preflight获取实时远端证据，observe不查询未创建的Worker 1远端PID或GPU。
因此真实Run `p2_d2_connection_smoke_20261126_001`在本地审计阶段BLOCKED，SSH/GET均0。
该历史结果和文件保持原样，未将本次离线PASS回填到历史Run。

## 3. 最小修改及复用边界

| 文件 | 本次改动 |
| --- | --- |
| `scripts/run_p2_d2_connection_smoke.py` | 新增revalidate_worker1及调用门禁；预检可选文件前缀，避免覆盖初始证据；增加复核计数和通过标志 |
| `tests/test_connection_smoke_integration.py` | 现有正式CLI Fake harness支持第二次只读预检，更新执行顺序及计数断言 |
| `tests/test_connection_smoke_worker1_revalidation.py` | 新增45例完整入口、身份漂移、缺失字段、异常及HTTP失败测试 |
| 本报告 | 验收、历史保护和证据索引 |

沿用 `ownership_preflight → deployment.remote('ownership-readonly')` 现有正式SSH只读机制、
同一Metadata Producer和字段结构。原producer返回双Worker当前记录，本次第二次调用也保持原样，
仅对Worker 1做初始/实时身份稳定性比较；没有为只查询Worker 1而重写远端采集程序。
该producer只读取GPU 0/1，不访问GPU 2–7，不调用HTTP，不创建远端目录或文件。

`ssh_evidence_transport.py`及V1协议未修改；没有替换现有SSH机制或重构Metadata Producer。
`manage_remote_vllm.py`、ConnectionLifecycle/SSHProcess整体与本轮基线字节相同。
GET/HTTP Client、strict_context、SSH配置检查、Manifest publisher等8个相关函数AST完全一致。
TLS、CA、hostname、API Key处理、超时和重试参数均未改变。

## 4. 新调用位置和失败门禁

```text
Initial Dual Worker Ownership Preflight
Worker 0 Tunnel Create
Worker 0 Single GET → TLS/API Key/Model Verification → Worker 0 PASS
Observe current owned connection
Worker 1 Live Ownership Revalidation（新的只读SSH执行）
Compare Initial and Current Worker 1 Ownership
Worker 1 Ownership PASS
Observe current owned connection
Worker 1 Tunnel Create
Worker 1 Single GET → TLS/API Key/Model Verification
Resource Cleanup
Manifest Verification
```

Worker 0没有完整PASS时，循环不会进入Worker 1分支；其复核、Tunnel和GET次数均为0。
Worker 0 PASS后，仍先检查当前已拥有连接，再启动远端复核；复核期间若原连接失效，
复核后的observe也会阻止创建第二个Tunnel。
第二次远端结果不复用初始字典或缓存，通过同一个remote入口重新执行现有只读程序。

## 5. Worker 1初始/实时比较

必须先通过现有当前归属校验，再逐字段要求“存在、类型相同、值相同”；两项均为真才PASS。
比较20个字段：Container、User、Ubuntu、Worker ID、PID、alive、Process Identity、Command Identity、
Command Fingerprint、CUDA_VISIBLE_DEVICES、Physical GPU、GPU UUID、部署状态UUID/PID/GPU、
Endpoint、Listener Ownership、Model ID、Model Path和Ownership Verified。
任何缺失、不匹配或无法验证都会失败封闭；不以PID存活代替所有权。

成功Fake场景的身份对照如下，全部为**模拟值，不是当前远端观测**：

| 字段 | 初始 | 新只读预检 | 判定 |
| --- | --- | --- | --- |
| Container / Worker ID | 36f825bcc5da / worker-1 | 同值 | PASS |
| PID / alive | 901 / true | 901 / true | PASS |
| Process Identity | current-start-1 | 同值 | PASS |
| Command Fingerprint | 64位十六进制模拟摘要 | 同值 | PASS |
| CUDA / Physical GPU | 1 / 1 | 1 / 1 | PASS |
| GPU UUID | 固定模拟Worker 1 UUID | 同值 | PASS |
| Endpoint / Listener Ownership | 127.0.0.1:8001 / true | 同值 | PASS |
| Model ID / Ownership Verified | Qwen/Qwen3-8B / true | 同值 | PASS |

PID变化测试同时更新当前部署状态PID，使新快照本身仍合法，但与初始PID不同，最终比较失败。
Process Identity、Command Fingerprint、GPU UUID也覆盖“新快照独立合法、与初始不一致”的场景，
验证不能只调用当前归属谓词而漏掉初始身份比较。
Physical GPU变化、Endpoint失去归属、模型变化、容器变化、alive=false均阻止第二个Tunnel。

## 6. 证据文件及Manifest

初始文件名和两参数预检接口保留，新增可选内部前缀用于分开保存本次复核：

- `worker1_revalidation_ownership_ssh_command.json`
- `worker1_revalidation_ownership_ssh_result.json`
- `worker1_revalidation_ownership.json`
- `worker1_revalidation_comparison.json`

比较记录保留初始/当前Worker 1快照、逐字段结果、当前归属校验结果和总判定。
复核开始、身份比较、Ownership PASS事件写入既有execution_sequence.jsonl。
写入失败不继续；失败时由既有finally清理Worker 0，最终状态不能PASS。
复核SSH失败/超时不会重试，也不会伪造当前快照或PASS记录。

所有文件由原fsync writer保存，原publisher将新增平面文件纳入Manifest，未增加Manifest协议。
12个持久化Fake CLI场景的Manifest文件集合、大小和SHA-256均重新核对通过。

## 7. 正式CLI Fake E2E顺序与实际次数

测试调用正式 `main(argv)`，运行实际预检调用、比较函数、生命周期、HTTP请求和Manifest代码，
只在SSH/进程/本地socket/HTTP传输边界注入Fake；没有替代Runner或直接伪造返回PASS。

| 场景 | 现有只读预检调用总数 | Worker 1实时复核 | W1 Tunnel / GET | 结果 |
| --- | ---: | ---: | --- | --- |
| A 完整成功 | 2 | 1 | 1 / 1 | PASS |
| B Worker 0 GET/模型/401/TLS/Tunnel失败 | 1 | 0 | 0 / 0 | 非PASS |
| C Worker 1 PID变化 | 2 | 1 | 0 / 0 | PARTIAL |
| D Physical GPU或GPU UUID变化 | 2 | 1 | 0 / 0 | PARTIAL |
| E Endpoint或监听归属变化 | 2 | 1 | 0 / 0 | PARTIAL |
| F Command Fingerprint/Identity变化 | 2 | 1 | 0 / 0 | PARTIAL |
| G Worker 1复核通过后GET失败 | 2 | 1 | 1 / 1 | PARTIAL |
| 任一20个比较字段缺失 | 2 | 1 | 0 / 0 | PARTIAL |
| 复核SSH失败、超时或比较审计写入失败 | 2 | 1 | 0 / 0 | PARTIAL |

成功调用顺序断言为：ownership → tunnel0 → get0 → ownership → tunnel1 → get1 → cleanup。
事件时间线进一步断言：Worker 0 PASS → Live Revalidation → Ownership Comparison →
Ownership PASS → Worker 1 Tunnel。Fake边界次数与事件序号同时保存，不能只靠最终状态推断顺序。
CLI保留既有status=PASS/PARTIAL语义；证据摘要以`CONNECTION_SMOKE_`加status标注对应Gate。
A对应CONNECTION_SMOKE_PASS，G对应CONNECTION_SMOKE_PARTIAL，不改变既有状态机。

## 8. 预算、鉴权、TLS及资源

Worker 0最多1 GET，Worker 1最多1 GET，总HTTP最多2；无HTTP健康检查、无Redirect Follow、
无自动重试、无Worker Failover、无Completion。每个Client仍独立、trust_env=false、retries=0，
冻结CA及CERT_REQUIRED/check_hostname验证保持不变；connect=5、read/write/pool各10秒。
API Key处理保持原样，不打印Authorization，不引入OpenAI云端Key。

未来获独立授权的成功真实流程包含2次只读预检SSH加最多2个独立Tunnel SSH；
新增复核不是失败重试，不会增加HTTP请求数。本轮所有这些实际网络操作均为0。
Fake场景均断言已创建Adapter调用归属清理，复核失败时只存在Worker 0 Adapter。
没有关闭任何真实SSH或远端进程，没有vLLM/GPU/Docker/远端文件操作。

## 9. 测试结果与真实命令

- 新增专项：45例。
- Connection Smoke及生命周期专项：**116 passed in 20.36s，0 failed，0 skipped**。
- 全仓：**776 passed in 64.82s，0 failed，0 skipped**（731 + 新增45）。
- compileall、git diff --check、敏感信息扫描、历史Manifest和冻结实现检查通过。
- 已有历史TLS Stage B、SSH Transport、HTTPS Client、TLS验证、Manifest等回归均包含在全仓内。

```text
.venv/bin/python runs/phase2_p2_d2_worker1_revalidation_stage_a/p2_d2_worker1_revalidation_stage_a_20261126_f3f68613/offline_test_guard.py -q tests/test_connection_smoke_worker1_revalidation.py tests/test_connection_smoke_integration.py tests/test_connection_smoke.py tests/test_connection_lifecycle.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_worker1_revalidation_stage_a/p2_d2_worker1_revalidation_stage_a_20261126_f3f68613/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_worker1_revalidation_stage_a/p2_d2_worker1_revalidation_stage_a_20261126_f3f68613/save_fake_cli_evidence.py
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

测试防护拒绝真实socket connect/bind/getaddrinfo/sendto及SSH/OpenSSL/curl/wget；
保存Fake证据时还拒绝nvidia-smi和Docker。依赖导入的一次本地IPv6 bind能力探测被阻止，
原堆栈保留，不构成监听或外部网络请求。原始测试输出没有截断，保存于独立审计目录。

## 10. 历史证据与Git状态

历史 **7080个文件**逐文件SHA-256不变；**120份Artifact Manifest**、
**4004个条目**均复核匹配。历史BLOCKED Run 001、之前Smoke/TLS/SSH结果均未改写。
旧Fixture和其期望结果未修改。除正式Runner及现有Fake harness两份文件外，
所有本轮开始前的源码、配置、测试及文档字节不变。

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支main。原有Stage A修改和历史未跟踪文件全部保留。
本轮新增上述45例测试、本报告和本地审计目录；没有暂存、Commit、Push或其他历史操作。
`task_changes.patch`按本轮before副本生成，区分当前最小修复与此前未提交修改。

## 11. 交付目录及停止

`runs/phase2_p2_d2_worker1_revalidation_stage_a/p2_d2_worker1_revalidation_stage_a_20261126_f3f68613/`

包含baseline、before副本、真实测试输出、12个完整Fake CLI Run、Fake调用审计和时间线、
逐项身份对照、历史完整性结果、敏感扫描、源码差异、最终Git状态、gate_summary及统一Manifest。
所有Fake材料标记OFFLINE_FAKE_ONLY、research_dataset_eligible=false，不是训练数据或真实远端验收。

**CONNECTION_SMOKE_WORKER1_REVALIDATION_STAGE_A_PASS**。
原阻塞调用已补齐并完成离线验收；下一步仅等待用户对新的唯一真实Run独立授权。
本轮立即停止，不自动执行真实Smoke、Qwen或后续Gate。
