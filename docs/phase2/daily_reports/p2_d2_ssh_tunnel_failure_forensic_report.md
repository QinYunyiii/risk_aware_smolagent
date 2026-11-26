# P2-D2 SSH Tunnel Failure Forensic Audit — Offline Evidence Only

日期：2026-11-26（Asia/Shanghai）。
最终 Gate：**SSH_TUNNEL_DIAGNOSTIC_INCONCLUSIVE**。

## 1. 结论：为什么之前能监听、这次不能

**现有离线证据不能确定两次结果不同的具体根因。** 能够确定的是：两次Worker 0的SSH argv逐项
完全相同，SSHProcess类和就绪轮询代码也一致；静态展开没有发现LocalForward被清除、地址/端口
错误、配置覆盖或代理/复用变化。本次child创建成功且在清理前仍存活，但本地可连接监听未被
观测到；没有保存SSH认证或bind阶段日志，也没有保存socket返回码。

因此不能在“连接/认证未完成”“LocalForward未完成”和“监听检测假阴性”等候选中选出确定根因。
没有证据证明应修改30秒预算，不建议据此延长预算、替换SSH框架或更改TLS/API Client。
本轮网络操作全部为0，未运行真实Tunnel、TCP探测、远端命令或Qwen；未修改任何生产代码。

## 2. 两个真实Run及证据完整性

| 属性 | 历史监听成功Run | 本次监听失败Run |
| --- | --- | --- |
| Run ID | p2_d2_connection_smoke_20261125_001 | p2_d2_connection_smoke_20261126_002 |
| Git HEAD记录 | 88a465f07f87705b08270c71c72c9b9e01cdf2dc | bfd74dacf523d4677bc662e2e92b3bd5c8c09a10 |
| 本地监听门禁 | Worker 0、1均connection_ready | Worker 0未ready，Worker 1未创建 |
| 整体Smoke历史状态 | BLOCKED：后续Worker 0 TLS握手超时 | BLOCKED：Worker 0 forward readiness timeout |
| GET尝试 | Worker 0一次，Worker 1零次 | 两者零次 |

历史Run只在**隧道/本地监听阶段成功**，不能称为整个Smoke成功，也不混用两个Run的TLS结果。
原始目录均存在，按实际Manifest读取：历史artifact_manifest.json 7项、本次核心9项、
本次artifact_manifest_final_audit.json 42项，全部大小/记录的SHA-256匹配。
本次9项核心清单与42项最终清单有包含关系，不当作互不重复的51份证据。

Manifest SHA-256：

- 历史核心：`af3ae0b5dd8d0f2f9fbec9518ffeb43e85663e2e43950acd8a9bc57a3e9eae1b`。
- 本次核心：`de71f4cedd58ab757509a50b9386b5bd13d3c10329b1ab3695e8acdc26eec11d`。
- 本次最终：`fdbdebda8a1dd2acd8bbb32c88ba99f1157739232029e95efbca2b015c35dde6`。

本次没有独立process_lifecycle.json；实际原始生命周期是connection_audit.jsonl。
ssh_process_audit.jsonl是已封存的派生索引，引用初始只读结果及原始三个连接事件，
不把它重复计为新的SSH观测。execution_sequence.jsonl记录调度；没有真实单调combined timeline。
按实际文件名读取，没有补造不存在的历史日志。

## 3. 本次SSH Process生命周期

以下为connection_audit.jsonl原始行号及北京时间（UTC+08:00）：

| 行 | 事件 | 时间（2026-11-26） | 观测 |
| --- | --- | --- | --- |
| 1 | connection_created | 02:19:12.631811 | PID 1249034，PROCESS_ALIVE，LOCAL_PORT_CLOSED，exit=null |
| 2 | connection_cleanup_started | 02:19:40.692223 | 同一owned child仍ALIVE，端口CLOSED，exit=null |
| 3 | connection_cleanup_finished | 02:19:40.695140 | PROCESS_EXITED，exit=-15，端口CLOSED |

创建时间身份记录为02:19:12.631432；owner为run_connection_manager，Tunnel ID为
`dddb558897d44d5283cfbb128043fa4d`。Popen句柄是实际归属依据，process_start_identity为
句柄代际标识，不把其随机token称为内核start ticks。

没有connection_ready、connection_lost_observed或provider_request_started事件。
冻结启动循环反复poll同一子进程，走到就绪timeout分支；逐轮snapshot没有写入原始日志。
直接原始观测证明创建时及清理开始时存活，不能凭这两个事件构造完整SSH协议阶段时间线。

初始双Worker只读SSH先前退出0、提供真实容器及进程Metadata；**它是另一条SSH连接**，
不能据此认定后续-N隧道连接也完成了认证或远端握手。
后续Tunnel的TCP远端连接、SSH key exchange和认证完成均为UNKNOWN。

## 4. stderr、退出和清理

本次worker-0_ssh.stderr真实大小为**0 bytes**，SHA-256为
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。
没有bind失败、认证失败、Host Key失败或SSH握手完成消息；未启用-v/-vv或单独-E client log。
空stderr既不是认证成功证明，也不能把某个未记录错误排除为逻辑上不可能。

Runner异常原文：`TimeoutError: Owned SSH forward readiness timeout`。
超时后的finally调用SSHProcess.close：poll仍为None时执行terminate，随后wait(timeout=10)。
退出码**-15**表示本次受控SIGTERM回收，不能解释成SSH自行报错255或自然成功退出0。
resource_release及已封存的local_post_run_resource_verification确认进程消失、两端口释放。
本轮只读取该历史复核，不重新查询当前进程/端口，更没有再次发信号。

历史两个child同样由close清理，最终退出均为0、两stderr均为空；不同退出码没有对应SSH阶段日志，
不能仅凭0与-15的差异定位认证阶段。未观察到提前退出但被Runner忽略的直接证据。

## 5. 历史真实监听成功证据

| Worker | PID | 创建→READY北京时间（2026-11-25） | wall时间差 | 清理前/退出 |
| --- | ---: | --- | ---: | --- |
| worker-0 | 956316 | 01:39:58.707393 → 01:40:03.591813 | 4.884420秒 | ALIVE且LISTENING，最终0 |
| worker-1 | 956329 | 01:40:03.602435 → 01:40:12.419349 | 8.816914秒 | ALIVE且LISTENING，最终0 |

历史connection_audit记录93项，其中connection_created/ready各2、后台health 38、
smoke_health 44、Provider开始/失败各1及其他诊断/清理事件。历史两次READY均明确为
LOCAL_PORT_LISTENING，后续Worker 0 Provider开始及失败时仍LISTENING。
这里的LISTENING是既有socket连接检测门禁，不是保存了内核bind/listen系统调用或完整PID-inode映射。
这些历史wall时间差不冒充精确SSH连接、认证或单调耗时。

历史后来发生的TLS超时仅作为历史事实区分失败阶段，本轮不产生新的TLS PASS/FAIL判断。

## 6. 成功与失败的明确差异表

| 项目 | 历史Worker 0（另有Worker 1） | 本次Worker 0 | 差异/证据限制 |
| --- | --- | --- | --- |
| 完整argv及参数顺序 | resource_release.ssh_command | 同一数组逐项相等 | 无差异 |
| SSH目标/端口/User | root@121.48.161.243 / 4035 | 相同 | 无差异 |
| Alias | 前置解析risk-agent-remote；Tunnel用显式目标 | 同一构造 | Tunnel并非靠Alias加载转发 |
| -F /dev/null / -N / -T | 均有 | 均有 | 无差异 |
| -i / known_hosts | risk_agent_audit / ~/.ssh/known_hosts | 路径相同 | 未保存两次私钥或known_hosts全文，不能比较文件内容 |
| BatchMode / IdentitiesOnly / StrictHostKeyChecking | yes / yes / yes | 相同 | 无差异 |
| LocalForward绑定/目标 | 127.0.0.1:8000 → 127.0.0.1:8000 | 相同 | 历史Worker 1另为8001，不能套用给本次未执行Worker 1 |
| ExitOnForwardFailure | yes | yes | 无差异 |
| ControlMaster / ControlPath | no / none | 相同 | 无共享控制socket |
| ConnectTimeout / keepalive | 15 / interval15,count3 | 相同 | 无差异 |
| Popen方式 | 前台、stdin/stdout DEVNULL、私有stderr、独立session | 相同 | SSHProcess类AST完全一致 |
| 子进程env参数 | 不传env，继承父环境 | 相同 | 真实父环境快照未保存，不能证明值完全一致 |
| 监听检测 | IPv4 127.0.0.1:8000 connect_ex，0.1秒socket timeout | 相同 | 无地址或端口回归 |
| 就绪轮询 | 单调30秒deadline，sleep0.1 | 相同 | While循环AST完全一致 |
| 启动调度 | 初始没有新只读SSH；W0 READY后启动W1 | 先一次双Worker只读SSH；W0启动阶段停止 | 执行上下文不同，不能推断它造成失败 |
| 结果 | W0/W1各有READY | 无READY | 直接观察差异 |
| 清理 | 两child最终0 | 一个child最终-15 | 都走owned close；未保存阶段日志解释退出差异 |

历史生命周期源文件由本地Git对象恢复，SHA-256严格匹配历史Manifest的
`869fdd1b66be1a86ee8767763036603ba7129d2a6081a0e5fc0e21a4af4894ab`。
历史Runner最初在记录的旧Commit中尚未入库（本地git show返回128）；随后从HEAD Git对象及原Stage A before副本
独立恢复，两者SHA-256均精确匹配历史Manifest。恢复来源与这次本地取证错误单独记录，
没有因旧Commit缺文件而猜测历史Runner行为或改变任何历史Run。
提取比较SSHProcess类和启动While循环AST均完全一致；当前launch_worker是旧逻辑的提取，
add加锁及新调度没有改变所审计的监听轮询分支。

## 7. SSH配置静态审计

本轮仅执行3次隔离的本地`ssh -G -F /dev/null ...`（历史W0、本次W0、历史W1）以及一次`ssh -V`。
它们只展开配置/版本并退出，没有建立SSH连接。防护钩子拒绝真实socket及非-G/-V SSH调用。
两份W0 argv在**当前客户端**展开的结果完全相同，不将其冒充当时客户端配置dump。

关键生效值：LocalForward各1；W0为`[127.0.0.1]:8000 [127.0.0.1]:8000`；
ClearAllForwardings=no，RemoteForward/DynamicForward均无；ControlMaster=false，ControlPath无，
ControlPersist=no；ProxyCommand/ProxyJump均无；BatchMode=yes，严格Host Key=true；
ConnectTimeout=15，ConnectionAttempts=1，SessionType=none，RequestTTY=false。

当前用户Alias块有ClearAllForwardings=yes，用于受控只读SSH；**真实Tunnel的-F /dev/null隔离了该块**。
本地ssh(1)手册也确认指定-F时忽略系统ssh_config。因此Tunnel不会加载用户历史转发、系统Include
或Proxy配置，也没有在-L后另传ClearAllForwardings=yes。前置Alias解析选取目标、密钥和known_hosts，
实际Tunnel依赖显式参数而非旧配置的转发规则。

当前客户端路径为`/usr/bin/ssh`，版本`OpenSSH_9.6p1 Ubuntu-3ubuntu13.20, OpenSSL 3.0.13 30 Jan 2024`。
实际历史PATH、所执行二进制版本/摘要和继承环境没有保存在两个Tunnel Run里；本轮只记录当前
二进制与配置摘要和环境变量存在性，不输出代理URL、用户名/密码/Token或API凭据。
不能仅凭相同命令断言两次运行环境、网络路径或服务端状态也完全相同。

## 8. 就绪预算、检测及生命周期竞争

具体实现：`src/risk_agent/monitoring/connection_lifecycle.py`。

- 246–250行：先本地端口connect_ex占用检查，再调用factory/Popen。此占用检查未设置独立timeout，
  位于30秒预算之前；本次它已返回并成功创建child，不能用它解释后续就绪timeout。
- 251–256行：登记Adapter、写入created事件后，才设置`deadline=time.monotonic()+30`。
  Popen/登记/首次日志的时间不计入该deadline；SSH child此时异步继续连接/认证，后续阶段会消耗预算。
- 257–267行：每轮snapshot先poll child，IPv4 connect_ex使用0.1秒socket timeout；
  exited立即报错，listening立即ready，否则检查单调deadline，未到则sleep0.1。
  因此“30秒就绪预算”不等于独立的绑定超时或完整CLI总deadline。
- 192–205行后台monitor只有READY后才启动，本次未启动；缺少health事件符合控制流，
  不能把3条日志少当作监控线程崩溃。也没有后台monitor与启动循环争抢状态的本次证据。
- 269–289及137–146行：finally停止monitor并对owned child terminate/wait，最终退出被保存。

本次created→cleanup_start wall差28.060412秒；没有保存deadline起点/每轮monotonic值。
wall与单调差异不能证明预算少算；本次不改写原时间、不延长预算、不推断时钟调整原因。
SSH ConnectTimeout=15按本地ssh_config手册覆盖建立连接和初始协议握手/密钥交换，
不能当作整个认证与转发生命周期总上限，故child在30秒时仍存活本身不能证明Timer错误。

监听检测是**TCP可连接性**：snapshot把connect_ex返回0映射为LISTENING，所有其他返回值
都映射为CLOSED，没有记录errno、绑定syscall、监听socket owner或逐轮采样。
因此CLOSED记录不能单独证明“内核从未存在LISTEN”或“未尝试bind”，也无法排除检测假阴性。
但目标地址/端口确与-L一致，不存在可证的IPv6 localhost解析错误或错测8001。

## 9. 故障类型A–I及原始证据

| 类型 | 直接证据 / 反对证据 | 判断 |
| --- | --- | --- |
| A Client创建失败 | connection_created有owned PID 1249034且ALIVE | 与本次直接证据不符 |
| B 进程已启动但连接/认证未完成 | ALIVE、未ready、空stderr；无Authenticated/SSH阶段日志 | 不能确认或排除；另一条预检成功不证明此child成功 |
| C SSH连接成功但LocalForward未建立 | -L传入、无READY；缺少此child认证和bind日志 | 不能确认已连接前提，故不能定案 |
| D 本地bind端口失败 | 初始端口空闲、ExitOnForwardFailure=yes、无bind错误且child仍ALIVE | 缺乏支持，典型已完成bind失败与观测不符；无syscall证据不能绝对排除竞态 |
| E child退出但Runner漏捕获 | 清理前ALIVE、循环有poll退出分支，退出-15在受控清理后记录 | 无提前退出被漏记的证据 |
| F child存活但监听检测失败 | ALIVE和CLOSED直接存在，errno未留存 | 观测组合成立，不能确定是否为检测假阴性 |
| G 已监听但测错地址/端口 | -L与snapshot均127.0.0.1:8000，代码与历史一致 | 未发现此类代码错误；没有bind日志证明实际监听的其他地址 |
| H 生命周期竞争使检测超时 | 初始/清理端点记录，启动期无逐轮日志；monitor尚未启动 | 不支持后台monitor竞争；瞬态监听/调度问题证据不足 |
| I 证据不足以区分 | 无认证、bind、连接阶段与errno/单调采样 | **最终采用：INCONCLUSIVE** |

LocalForward本地监听与远端Endpoint是否接受HTTP是不同阶段。ssh_config手册明确
ExitOnForwardFailure不覆盖通过转发连接最终目标失败；本次GET为0，不能把就绪失败归因于
Qwen、API Key、远端HTTP服务或TLS证书，也不能把旧Run的TLS超时当作本次隧道失败根因。

## 10. 已确认的诊断标签问题及最小建议

`connection_lifecycle.py:65`按TimeoutError消息包含read分类；readiness也被命中，因此原结果
误标provider_read_timeout。真实异常原文仍是Owned SSH forward readiness timeout。
这是发生失败**之后**的标签问题，不会导致LocalForward不绑定，不能当作本次根因。
本轮未修标签、未修改TLS Classifier，也不将修标签列为重新执行的充分条件。

**没有已确认的隧道启动或预算计算缺陷，故当前没有可证有效的根因代码修复。**
不建议移除-F、加ClearAllForwardings=yes、改端口、放松认证/TLS或延长30秒预算；
相同argv历史已到READY，静态展开也正确。

如后续独立授权补充证据，最小范围是在现有SSHProcess的既有stderr文件启用OpenSSH原生详细日志，
使同一次连接可区分认证、转发setup和bind阶段；无需另建SSH/TLS框架或Metadata Schema。
本轮不实施、不连接，也不把这项证据建议宣称为根因修复。
若未来发现确定缺陷，最少Fake E2E应保持：W0未ready时W1复核/Tunnel/GET均0，
child提前退出立即停止、就绪timeout仍清理owned child、非零connect_ex不被当成明确bind错误、
固定参数和HTTP预算/TLS/V1不变。现有对应单测与本轮补充Mock检查已离线通过。

## 11. 未知事项与重新执行条件

未知：本次Tunnel是否完成TCP连接/key exchange/认证，是否调用bind及返回值，
每轮connect_ex errno、瞬态监听、单调时间线、历史完整env/SSH二进制，以及运行当时服务端日志。
当前证据不能证明SSH服务端故障，也不能证明运行环境已恢复或下一次一定成功。

因此**本轮不能给出“已修复、具备重新执行保证”的结论**；仅确认静态配置可表达正确转发。
没有自动重跑授权，不执行新的SSH、Tunnel、GET或五任务Gate，不增加额外探测。
后续是否重试及证据补充方式须另行明确决定，本轮不推进。

## 12. 离线验证、资源和Git状态

- 既有生命周期/Smoke单测：**30 passed in 3.11s，0 failed，0 skipped**。
- 本地Fake专项：**9 passed，0 failed**。覆盖真实snapshot的0/111/13/110返回码映射，
  Fake延迟ready、从不ready、factory延迟不计入deadline、启动期退出、立即ready与owned清理。
- Fake时钟下30秒timeout正确触发，后台monitor尚未启动，没有复现提前计时或生命周期竞争。
  这验证代码路径，不冒充真实历史SSH阶段重建。
- 本轮未修改代码，未重复全仓776项；776是原真实Run报告中的回归结果，不冒充本轮执行数量。
- git diff --check、敏感材料扫描和历史SHA复核通过；所有现有源码/测试/配置/文档字节不变。

命令：

```text
.venv/bin/python runs/phase2_p2_d2_ssh_tunnel_forensic/p2_d2_ssh_tunnel_forensic_20261126_c64a3b3e/static_evidence_audit.py
.venv/bin/python runs/phase2_p2_d2_ssh_tunnel_forensic/p2_d2_ssh_tunnel_forensic_20261126_c64a3b3e/offline_readiness_checks.py
.venv/bin/python runs/phase2_p2_d2_ssh_tunnel_forensic/p2_d2_ssh_tunnel_forensic_20261126_c64a3b3e/offline_test_guard.py -q tests/test_connection_lifecycle.py tests/test_connection_smoke.py --disable-warnings --tb=short
git diff --check
```

本轮真实SSH连接、Tunnel、远端命令、TCP探测、TLS、HTTP、Qwen、GPU/vLLM/Docker均为0。
仅3次ssh -G和1次ssh -V为本地静态查询，未发起认证。
依赖导入的本地IPv6 bind能力探测若被离线保护阻止，详见原test_results，不算监听或网络连接。
所有本地Mock无真实child、socket或信号；旧PID未被重新操作。

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支`main`。原有未提交/未跟踪修改全部保护。
本轮仅新增本日报及独立离线审计目录；未Commit/Push。
历史 **7393个文件**SHA-256不变，**135份Artifact Manifest、4489个条目**复核通过。
审计目录：`runs/phase2_p2_d2_ssh_tunnel_forensic/p2_d2_ssh_tunnel_forensic_20261126_c64a3b3e/`，保存两Run分开的证据摘要、原始出处、源码AST比较、静态配置、Fake结果、
基线/最终Git状态、完整性结果、报告副本和本轮独立Manifest，不修改历史Run。

## 13. 最终Gate与停止

**SSH_TUNNEL_DIAGNOSTIC_INCONCLUSIVE**。
已排查到“同一SSH参数和检测逻辑产生不同就绪结果”，无法用现有证据定位两次差异的具体根因。
不报告新的Connection Smoke/TLS PASS或FAIL。报告完成后停止，不修改代码、不执行连接、不Commit/Push。
