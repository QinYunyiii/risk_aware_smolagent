# Formal Validation Stage B — 2026-11-28

## Objectives

用户在 Validation Stage A 交付后批准继续。计划复用两个现有 Qwen Worker，执行冻结
Validation 的 32 次 Source attempts 和 7 个预选同 Checkpoint 重复。任务、Split、Prompt、
Verifier、模型参数及预算不变，不包含 Test、训练、额外任务或服务重启。

## Result

**BLOCKED：唯一一次远端只读预检没有返回最终证明，正式采集未启动。**

- 保留的预检 Run ID：`formal_validation_20261128_ebd801fd`。
- SSH 连接尝试 1，Remote Command 1，自动重试 0。
- 公钥认证成功；主机密钥严格匹配；日志记录已发送 `python3 -B -`。
- SSH 返回 **255**，本地监督耗时 **160.948 秒**，本地监督未超时，进程已回收。
- stdout **0 bytes**；stderr **4385 bytes**，完整原始文件保存在本地。
- 日志记录 `client_input_channel_req: channel 0 rtype exit-signal reply 0`，随后
  `Exit status -1`。具体信号名称没有保存，不能把 255 当作远端应用退出码 255。
- Validation Source attempts **0**，重复 **0**，HTTP/Completion 请求 **0**，隧道 **0**。
- 未创建正式 Run 目录或 Validation cohort launch claim，未消耗任何任务尝试。

本轮没有 Validation 标签或新 TLS/API 验收结果。不得把本地 Stage A PASS 或旧 Train
结果作为本次远端预检通过的证明。

## Local Environment Work

执行 Commit：`8ab953e63772c5a2c3af1eb5a3a62005555abfd9`，启动时工作区干净。
Validation 清单 SHA-256：
`91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。

本地冻结链、95 行 Train 资格引用及其 Manifest、完整离线 Gate 收据绑定通过。
原 API Key 与 CA 环境变量均存在；未输出或复制凭据明文。CA 可读、有效、Hash 与可信
基准一致；SSL Context 要求证书和 hostname 校验。专用密钥权限、公钥指纹、known_hosts
指纹、SSH Alias 静态配置通过。8000/8001 未监听，可用磁盘约 **938.89 GiB**。

所有本次文件写入新的预检目录，旧 Run 不覆盖。已准备的正式入口监督脚本未执行。

## Remote Environment Work

只执行一次既有只读预检，复用上一 Train child 的程序，仅替换新 Run ID。
WSL OpenSSH 通过已验收的 Windows TCP relay 传输，认证和加密仍由 OpenSSH 执行。
实际参数包括隔离配置 `-F /dev/null`、专用 `-i`、端口 4035、显式 known_hosts、
BatchMode、StrictHostKeyChecking、IdentitiesOnly、禁止密码及交互认证、
ClearAllForwardings、ConnectionAttempts=1、ConnectTimeout=15、ControlMaster=no、
ControlPath=none。没有 LocalForward、RemoteForward、动态转发或连接复用。

该程序依次检查远端 CA、容器/root/Ubuntu、现有进程和监听归属、GPU 映射、七个模型
文件 SHA-256、软件版本及间隔采样。它在最后一次性输出 JSON，途中不发布阶段收据。
因此本次不能证明执行到了哪一阶段，也不能声称所有权、权重或采样已经通过。

程序有原有 `signal.alarm(150)`，本地监督有 180 秒预算；本次未调整任何超时。
远端退出信号与该闹钟存在可能关联，但现有日志未保存信号名称及阶段进度，
**不能确认是 SIGALRM，也不能确认是权重 Hash 慢、软件检查慢、服务负载或网络故障**。
没有证据将失败归因于 CA、Qwen Completion 或 Agent。

原始 UTC 起止：2026-11-28T05:51:26.973024+00:00 至
2026-11-28T05:53:58.495540+00:00（北京时间约 13:51–13:53）。
UTC 差值不同于单调耗时；保留原值，预算与耗时采用单调时钟，不修改系统时间。

## Evidence and Integrity

本地独立审计目录：
`runs/phase2_formal_validation_preflight/validation_preflight_20261128_ebd801fd/`。

包含 baseline、local_preflight、实际 SSH argv、远端程序、原始 stdout/stderr、
remote_preflight_process、gate_summary、资源释放、本地测试、完整性及统一 Manifest。
完整原始审计产物不提交 GitHub。

**36907 个历史文件及 405 个既有跟踪文件**逐项 SHA-256 未变。
Train 的 96 次 Source attempts / 95 个有效主标签 / 19 个有效重复保持原样；
Validation Stage A 和其清单、收据不变；Test 仍封存。

## Resource Release

本次唯一 SSH 进程已自然退出并由监督进程回收，无本地超时或强制终止。
按本次 stderr 记录的 PID 和启动身份只读核验 Windows relay：原实例已不存在。
只读 `/proc/net/tcp`、`tcp6` 确认 8000/8001 均无监听。未终止其他进程。
收尾新增远端连接和模型请求均为 0。未操作远端 Worker、GPU、vLLM 或 Docker。
没有追加远端检查，因此不宣称退出后 Worker 健康状态已经重新验证。

## Commands and Tests

主要实际本地命令：

```bash
.venv/bin/python -c 'from pathlib import Path; import runpy; runpy.run_path(str(Path(Path("/tmp/formal_validation_preflight_path").read_text())/"run_remote_preflight.py"),run_name="__main__")'
.venv/bin/python -c 'from pathlib import Path; import runpy; runpy.run_path(str(Path(Path("/tmp/formal_validation_preflight_path").read_text())/"verify_local_release.py"),run_name="__main__")'
.venv/bin/python runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/offline_test_guard.py tests/test_formal_validation.py -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

Validation 专项 **20 passed，0 failed，0 skipped**，25.03 秒，使用禁止真实网络的
审计钩子。本轮新测试收据归档到本次预检目录，旧测试收据不改写。
compileall、diff check、历史完整性通过。生产代码无修改，未重复全仓；前置 Stage A
的 1134 passed 属于上一轮验证，不冒充本轮新增测试结果。

## Root Cause and Remaining Work

可确认阻塞层为**远端只读预检命令未完成最终证据发布**，不是 LocalForward 或模型
请求阶段。底层根因与具体中断阶段 **UNKNOWN**，未通过增加预算或跳过检查绕过门禁。

下一步建议是单独授权一次带阶段时间记录的最小只读预检，区分 CA/身份/权重 Hash/
软件版本/采样中哪个阶段中断，并保存远端实际退出信号。先定位，再决定是否需要修复；
本轮不实施该诊断，不重试预检，不自动执行 Validation、Test 或模型训练。

## Git

本轮生产代码、冻结配置和历史产物不变，仅新增本脱敏报告；按仓库自动交付规则提交
和推送报告。实际 Commit 见 Git 历史，私有证据留在本地。

## Status: BLOCKED

`VALIDATION_REMOTE_PREFLIGHT_BLOCKED`。本地 Stage A 仍 PASS；正式 Validation 未启动。
