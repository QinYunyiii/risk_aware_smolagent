# Validation Preflight Diagnostic — 2026-11-28

## Result

**`REMOTE_PREFLIGHT_DIAGNOSTIC_PASS`：一次带阶段记录的真实只读预检完整通过。**
SSH 总耗时 **71.559 秒**，远端核验 **65.714 秒**，退出码 **0**。
上次中断未复现，具体根因仍为 `NOT_REPRODUCED_UNCONFIRMED`；不宣称已经永久修复
间歇性中断。旧 Stage B 的 BLOCKED、原始文件和原标签保持原样。

本次解决了预检只有最终输出、失败时无法定位阶段的证据缺口。保存了可复用的带阶段
记录程序、48 条阶段事件，以及成功、原检查拒绝错误身份、闹钟失败路径的离线验证。
没有发现可证实的生产代码缺陷，因此不改生产连接逻辑、冻结配置或实验协议。

正式 Validation 仍未执行；本报告不代表 HTTPS/API 或 Validation 采集 PASS。

## Authorization and Scope

用户在上一预检失败报告后要求“你来帮我解决”。本次执行一次新的、独立 Run 的只读
诊断，定位预检阶段并验证当前条件。不重试旧 Run，不重启服务，不操作 GPU 分配，
不创建隧道，不发送 HTTP/Completion，不自动恢复正式采集。

执行代码基线：`7320d27`；起始工作区干净。
新诊断 ID：`validation_preflight_diagnostic_20261128_8c49becb`。
原失败 ID：`formal_validation_20261128_ebd801fd`。
读取前验证原失败审计 Manifest 的 **23 个文件**，SHA-256 和长度全部匹配。

## Minimal Repair

仅在新目录复制上一只读预检程序并替换诊断 Run ID，增加 stderr 阶段事件：

- CA 检查、初始所有权、监听进程扫描、两次 GPU 采样。
- 每个模型文件 Hash 的起止、已读字节和约每 5 秒的进度。
- 软件版本检查、采样间隔、最终所有权复核和完成事件。
- SIGALRM 的名称、当前阶段、已读字节和不含局部变量的调用栈。

远端原 **150 秒 alarm**、本地 **180 秒监督预算**、原所有权谓词、七个权重 Hash、
版本要求和采样间隔不变。不减少检查、不延长预算、不复用历史证明或修改系统时间。
诊断版本若捕获 SIGALRM，会先记录信号再以 **124** 失败退出，不发布成功证明；
不把这一诊断退出码视为原程序曾经收到 SIGALRM 的证据。

AST 回归验证：移除新增 telemetry 节点后，程序与原程序（仅替换新 Run ID）的 AST
完全一致。原始 stdout 的最终证据格式不变，阶段事件只进入 stderr。
程序和实际 SSH argv 保存在本次私有审计目录，不覆盖旧程序。后续若采用该模板，
必须在新的审计目录绑定新的实际 Run ID 并重新核验，不能直接复用本次证明。

## Actual Stage Evidence

以下时间为远端单调时间，自诊断程序启动计算，不能与 SSH 总耗时混用。

| 阶段 | 实际结果 | 累计时间 |
| --- | --- | --- |
| CA 文件可读、SHA-256 一致 | PASS | 0.014 秒 |
| 容器/root/Ubuntu、双 Worker 进程及监听归属 | PASS | 0.198 秒 |
| 首次 GPU 采样 | 完成 | 0.320 秒 |
| 七个模型文件 SHA-256 | 全部匹配 | 50.917 秒 |
| 软件版本核验数据 | 与冻结版本一致 | 51.984 秒 |
| 原采样等待、第二次采样及最终所有权 | PASS | 65.714 秒内 |

模型文件实际读取 **16,381,550,382 bytes**，Hash 阶段耗时 **50.596 秒**。
本次 150 秒 alarm 未触发。大文件读取是本次主要工作阶段，但不能据此推定上次
一定在这个阶段超时；上次缺少阶段记录和信号名称，这一历史限制仍然存在。

两个现有 Worker 的实际映射仍为 Worker 0 → GPU 0、Worker 1 → GPU 1，实时进程与
原部署收据、CUDA 选择、GPU UUID、模型路径和监听归属均一致。没有将此映射作为未来
运行的固定配置，也没有申请新 GPU。容器为 `36f825bcc5da`，用户 root，Ubuntu 22.04。
模型七个 Hash 与冻结值一致；vLLM 0.34.0、torch 2.14.0+cu132、transformers 5.22.2。

两次 GPU 样本原始 UTC 为 2026-11-28T05:58:32.953389+00:00 和
2026-11-28T05:59:38.179859+00:00，间隔大于 60 秒。本地核验时通过原新鲜度门禁。
这份证明只绑定诊断 Run，不重新绑定到正式 Validation Run；之后仍会自然过期。

## SSH and Resource Release

真实 SSH 尝试 **1**、Remote Command **1**、自动重试 **0**。
复用上一已验收 argv：专用密钥、严格 known_hosts、BatchMode、禁止密码回退、
ClearAllForwardings、ConnectionAttempts=1、隔离配置与原 Windows TCP relay。
没有 LocalForward、RemoteForward 或动态转发。

SSH 已自然退出并回收，非本地超时。只读检查本次 relay 的 PID 和启动身份，原实例
已不存在；本地 8000/8001 无监听。未清理其他进程或远端 Worker。
本次 HTTP、Completion、隧道、Agent Task 均为 **0**，没有创建正式采集目录或 claim。
只读元数据核验不代表新增 TLS 握手或 API 验收。

## Tests and Integrity

本地离线专项 **5 passed，0 failed，0 skipped**，0.52 秒：

1. 实际保存的最终证据通过原模型核验。
2. 篡改模型权重身份仍被原门禁拒绝。
3. 篡改软件版本仍被原门禁拒绝。
4. 模拟 SIGALRM：记录实际信号、阶段上下文及字节计数，以 124 失败退出。
5. 去除 telemetry 后的 AST 与原核验程序完全相同。

测试通过禁止真实网络/远程工具的审计钩子运行，没有发送网络请求。compileall、
diff check 和最终 Manifest 核验通过。没有生产代码修改，不重复全仓 1134 项测试，
也不把前置测试数量算作本轮新增通过数量。

保护基线中的 **36931 个历史文件、406 个已有跟踪文件**逐项 Hash 未变。
Train 的有效样本、Validation Stage A 清单和收据、旧失败 Run、Test 均未修改。
未输出、记录或复制 API Key/私钥明文；原始日志不上传。

本地证据：
`runs/phase2_validation_preflight_diagnostic/validation_preflight_diagnostic_20261128_8c49becb/`。
包含实际程序、argv、原始 stdout/stderr、进程审计、48 条阶段记录、诊断摘要、五项测试、
资源释放、完整性记录和最终 Artifact Manifest。

## Next Step

当前只读预检已恢复通过，可以据此准备下一次正式 Validation 的重新预检和启动。
正式任务仍须新 Run ID、新鲜证据和原入口所有门禁；不沿用已经过期或绑定诊断 Run 的
证明，不改变 32 个任务、7 个预选重复或预算。本轮不执行正式采集。

## Git and Status

仅新增本脱敏报告，按仓库规范提交并推送；实际 Commit 见 Git 历史。

**Status: CONDITIONAL PASS** — 本次只读诊断 PASS，阶段与信号记录验证通过；
旧中断根因未复现、未确认，正式 Validation 尚未执行。
