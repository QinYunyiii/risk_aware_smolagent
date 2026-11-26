# P2-D2 Remote OpenSSL Metadata Preflight

## 结论

元数据预检：**PASS**。
Remote-Local TLS Isolation Stage A的唯一版本/参数证据阻塞已解除，当前审计结论更新为
`REMOTE_TLS_ISOLATION_STAGE_A_PASS`。

实际远端为OpenSSL **3.0.2**，原实现需要的8个s_client参数全部存在，且原Stage A
版本/参数门禁接受本次真实记录。不需要为兼容性修改本地握手实现。
这只满足进入Stage B前的工具准备条件，不代表TLS握手成功。尚未取得Stage B授权，
本轮不执行Stage B。

## 执行身份与范围

- 日期：2026-11-25（Asia/Shanghai）。
- HEAD：bfd74dacf523d4677bc662e2e92b3bd5c8c09a10，main。
- 开始时已有8个已知、未提交的前期诊断/Stage A文件，均保留；本轮没有修改源码。
- 仅使用risk-agent-remote建立一次SSH连接，专用密钥指纹匹配原授权，严格known_hosts。
- 实际生效：端口4035、BatchMode、IdentitiesOnly、StrictHostKeyChecking、
  ClearAllForwardings=yes、ForwardAgent=no、PasswordAuthentication=no。
- 无-L/-R/-D，ControlMaster=no/ControlPath=none，不复用或清理其他连接。
- 一次SSH退出码0，无超时、无重试；PID 970258已由communicate收回。

仅执行授权的date、hostname、id、command -v openssl、openssl version -a、
openssl s_client -help，以及历史CA路径的test/stat元数据检查。
没有向8000/8001发起连接，没有TLS握手、HTTP、Qwen、vLLM或GPU操作，也没有远端写入。

## 远端实际元数据

| 字段 | 实际结果 |
| --- | --- |
| date -Ins | 2026-11-24T18:45:35,892870338+00:00 |
| hostname | 36f825bcc5da |
| id | uid=0(root) gid=0(root) groups=0(root) |
| command -v openssl | /usr/bin/openssl |
| version / Library | OpenSSL 3.0.2 15 Mar 2022 / 同版本 |
| built on | Mon Jan 26 12:32:08 2026 UTC |
| platform | debian-amd64 |
| OPENSSLDIR | /usr/lib/ssl |
| ENGINESDIR | /usr/lib/x86_64-linux-gnu/engines-3 |
| MODULESDIR | /usr/lib/x86_64-linux-gnu/ossl-modules |
| s_client help退出码 | 0 |

完整version -a（包括编译选项与CPU信息）保存在独立审计目录stdout.txt；完整help及
SSH诊断保存在stderr.txt，没有用本地OpenSSL版本替代远端结果。

## 参数支持矩阵

下列均从实际远端help的独立参数行提取，而非命令字符串或模拟输出：

| 参数 | 远端支持 | 原实现用途 |
| --- | --- | --- |
| -connect | 是 | 固定远端loopback目标 |
| -servername | 是 | SNI localhost |
| -verify_hostname | 是 | 强制localhost主机名匹配 |
| -verify_return_error | 是 | 验证错误必须失败 |
| -CAfile | 是 | 使用原可信CA |
| -brief | 是 | 连接/验证概要 |
| -state | 是 | TLS状态观测 |
| -msg | 是 | 握手消息概要观测 |

可选-trace也存在，但原实现不依赖它。-timeout的help明确为DTLS收发超时，不能把它
当作本次TCP/TLS握手的5秒超时。因此保留Stage A已经测试的外部5秒总进程预算，
没有延长、降低或替换超时策略。

## CA文件元数据

路径：`/root/.config/risk-agent/tls/ca.crt`。

- 存在：是；类型：regular file；当前SSH用户可读：是。
- mode=644；uid=1000；gid=1000；size=631 bytes。
- mtime=2026-11-17 13:58:22.428654400 +0000。
- owner/group名称显示UNKNOWN，表示容器内名称映射不可得；并不表示文件不存在或不可读。
- 本次**没有读取CA内容、计算远端CA内容哈希或验证证书链**；更没有读取私钥。
  元数据不能证明证书内容未变。

Stage B仍须按冻结门禁读取这个公开CA，核对既有预期SHA-256，匹配后才允许握手。
如不匹配、不可读或身份改变，必须停止，不改CA、不自动启用备用传递方案。

## 原Stage A实现的兼容性判断

- 实际版本3.0.2属于现有门禁覆盖的OpenSSL 3.x。
- 8项必需参数全部有真实远端证据。
- 已在本地将本次记录的版本/help输入**原remote_entry的门禁**，采用合成公开CA和
  立即抛出“OFFLINE_GATE_REACHED_NO_NETWORK”的假probe；原门禁通过，假probe只被
  调用一次，没有实际握手。此测试只验证门禁接受记录，不冒充远端TLS成功。
- 现有22项离线定向测试再次通过；本轮不需要更改握手源码或重跑全仓测试。

需要区分：原preflight默认不加载历史审计目录，所以单独重新运行默认离线preflight
仍会保守输出remote compatibility UNKNOWN/Stage A BLOCKED。这是静态离线入口的
证据输入限制，不是本次OpenSSL不兼容。**本报告没有声称该CLI已自动更新为PASS。**
如以后需要CLI显式接收历史证据，可另行做小型便利改进，本轮不修改代码。

## SSH退出、stderr与耗时

- 本地UTC开始：2026-11-24T18:45:33.743209+00:00。
- 本地UTC结束：2026-11-24T18:45:39.924981+00:00。
- monotonic_ns开始/结束：315041485334294 / 315047665329516。
- 实际单调时钟耗时：6.179995222秒，含SSH连接及全部元数据命令。
- SSH退出码：0；attempt_count=1；timed_out=false。
- stderr原始接收字节数：17,391；包含完整s_client help及SSH-v诊断，不是空错误日志。
- SSH认证/命令没有失败，没有需要解释的异常链；没有因成功而发送第二条SSH命令。

## 本地独立证据目录及完整性

`runs/phase2_p2_d2_remote_openssl_metadata/p2_d2_remote_openssl_metadata_20261125_001/`

- stdout.txt：完整远端标准输出。
- stderr.txt：完整脱敏SSH诊断与s_client help。
- execution.json：SSH参数、PID、UTC/单调时间、退出码、次数和执行范围。
- artifact_manifest.json：原始3项文件的SHA-256及大小。
- compatibility_analysis.json：版本、参数矩阵、CA元数据和离线门禁结果。
- artifact_manifest_post_analysis.json：追加分析后的5项完整性清单，包含原Manifest，
  不覆盖它，不包含自身。

原始及追加清单均独立校验。未修改旧Smoke、Worker 0诊断或Isolation历史Run。
此目录受Git忽略管理；不暂存、不提交原始连接日志或本机配置。

## Stage B前置条件与停止

进入Stage B的工具兼容性证据已具备；原Stage A的唯一阻塞项已补齐。
仍需用户单独授权，并在未来同一远端命令中重新执行已设计的身份、工具和CA门禁。
该门禁属于安全前置核验，不得省略，更不能由本次文件元数据替代CA哈希检查。

当前未验证：8000监听、远端TLS握手、当前证书链、模型身份、Worker PID/GPU映射。
这些不应由本次只读元数据检查推断。远端TLS成功与否必须由未来独立Run实际观测。

本轮没有修改冻结配置、诊断实现、API Key、CA或SSH配置；未Commit、未Push。
到此停止，不自动进入Stage B、Smoke、五任务Gate或P2-D3。
