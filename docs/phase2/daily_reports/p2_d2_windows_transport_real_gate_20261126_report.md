# P2-D2 Real-Qwen Gate — Windows TCP Transport / Partial Real Gate

日期：2026-11-26（Asia/Shanghai）。
**连接Smoke：PASS；真实五任务Gate：BLOCKED（2/5完成）。总目标尚未完成。**

## 1. 本轮取得的实际进展

用户在Windows PowerShell使用密码成功连接36f825bcc5da、UID0后，本轮继续原P2-D2目标。
WSL原生TCP路径再次在SSH banner交换前超时；使用Windows原生TCP字节转接后，
同一WSL OpenSSH、同一专用密钥和严格known_hosts在约1.84秒完成publickey认证。
这证明可用替代传输路径，不足以断言WSL底层网络故障的具体原因。

真实双Worker HTTPS Smoke通过，之后现场复核模型/权重/版本及GPU采样，再执行原冻结五任务Gate。
T1/T2完整通过；T3真实模型错误缩写订单ID，本地get_order失败，按stop-on-first-failure停止。
T4/T5未启动。没有自动纠正模型参数、替换任务、修改Prompt或重复同一Source Attempt。

## 2. 传输实现及部署边界

修改现有connection_lifecycle.py，加入显式可选windows_tcp_proxy配置。
Windows PowerShell仅以.Net TcpClient按二进制复制stdin/stdout到原SSH Endpoint：
**不读取私钥、不负责SSH认证/加密、不创建本地监听、不执行远端命令**。
WSL OpenSSH继续持有Popen句柄，负责原专用密钥、严格Host Key、-L及受控清理。
原stdin关闭或远端关闭后，helper在finally Dispose TcpClient并退出。

本次不创建第二套SSH管理器，不迁移Agent或本地工具，没有修改Windows/WSL系统配置、
安装依赖、重启服务、创建远端目录、修改Worker或操作GPU 2–7。
原生直连仍为默认；仅本次connection spec与CLI显式启用Windows TCP profile。
没有继续使用未验证的IPQoS profile，也不声称它解决了问题。

修改文件：connection_lifecycle.py、run_p2_d2_connection_smoke.py、test_connection_lifecycle.py。
冻结配置、P2-D2 Runner、Checkpoint v2、V1 Frame、TLS策略及API Key处理均未变。

## 3. 真实双Worker连接验收

Run：`p2_d2_windows_transport_smoke_20261126_3d23818c`。
正式CLI使用--windows-tcp-proxy，严格按初始双归属→W0 Tunnel/GET/PASS→W1实时复核→W1 Tunnel/GET。

| Worker | HTTP | TLS | Model ID | GET延迟 |
| --- | --- | --- | --- | --- |
| worker-0 | 200 | 严格CA/hostname验证通过 | Qwen/Qwen3-8B | 1.021284秒 |
| worker-1 | 200 | 严格CA/hostname验证通过 | Qwen/Qwen3-8B | 0.776776秒 |

GET各1次，总2次，无额外健康GET、自动重试或Failover；Worker 1实时20字段复核通过。
本次Smoke的4个Windows TCP helper均用本地Get-Process确认已退出，无额外SSH探测。
没有把Windows用户的密码交给Codex，所有自动化仍使用原专用密钥。

## 4. 当前模型和资源身份

模型权重根目录与现场Worker进程/部署记录一致，7个权重/config/index文件实际读取并SHA-256核验，
与冻结EXPECTED_WEIGHTS逐项匹配，不是复制历史hash充当新观测。
现场版本：vLLM 0.34.0，torch 2.14.0+cu132，transformers 5.22.2。
GPU0/1两次只读采样时间：2026-11-26T07:32:24.029872+00:00、2026-11-26T07:33:58.718075+00:00，间隔约94.69秒。
前后Worker PID、命令摘要、CUDA选择、GPU UUID、监听归属不变。
当前使用已存在的本项目Worker，不启动或重启任何服务，不从共享资源抢占新GPU。

## 5. 唯一真实五任务Run

Run：`p2_d2_full_gate_1024_windows_20261126_8fb15d86`。
入口为现有run_p2_d2_full_gate_1024.py --mode full-five-task-gate-1024。
实验配置仍p2-d2-full-gate-1024-v1，seed=20261118、temperature=0、completion max=1024。
Catalog、T1–T5任务、worker schedule、Train分组均保持原冻结值。

| 任务 | 本次结果 | 证据 |
| --- | --- | --- |
| T1 p2_data_region_units_sum_001 | PASS | 2次真实Source依赖工具；Checkpoint；独立恢复；六Hash/精确输入/真实恢复请求/Replay Guard通过 |
| T2 p2_file_report_control_001 | PASS | 同上，Worker 1真实调用及独立恢复通过 |
| T3 p2_api_process_control_001 | 失败 | 第一次get_order使用错误的order_id="83001" |
| T4 p2_logic_chain_control_001 | 未执行 | T3失败即停 |
| T5 p2_data_region_units_sum_002 | 未执行 | T3失败即停 |

P2-D2恢复请求仍是一次真实request_next_model_response，返回工具未执行。
本轮不将这称为完整resume到终止，不生成正式Y_continue训练标签。
两个已生成Checkpoint另用verify_v2离线复核通过，原文件和Manifest未改变，
SQLite独立、精确首次输入及重放阻断结果均复核通过。

## 6. T3原始证据与直接原因

任务原Prompt：

> Read order-83001, use the observed current status to move it to processing with idempotency key p2_api_process_control-001, then query it again and return the verified final state.

任务初始SQLite与reference均使用精确订单ID `order-83001`。
模型真实原始Provider响应为get_order，arguments=`{"order_id": "83001"}`，finish_reason=tool_calls。
Source工具错误为APIWorkflowError；不是响应截断、解析失败、SSH/API中断或工具自动移除前缀。
在独立本地Fixture中，83001失败、order-83001正常查询，且环境状态不变，印证精确ID行为。

本轮不将“规范化错误参数”当作修复；保留原失败，未在同一Run重试。
原Runner分类task_execution_failure，具体可观察原因是模型工具参数不符合任务标识符。

## 7. Provider审计及费用

真实Qwen Completion请求总数：**7**（T1=3，T2=3，T3=1）。
7个Provider响应Usage均完整，委托Completion上限均1024，原始SDK响应、finish_reason、解析记录均保留。
总prompt tokens=14758，completion tokens=613，
total tokens=15371，累计Provider latency约22.278234秒。
另有Smoke2次models GET、正式Gate API预检2次models GET，不能与7次Completion混计。

自动模型重试0、Failover0、任务替换0；每个已尝试任务1次Source Attempt。
无P2-D3或正式160任务采集，没有生成正式风险模型训练标签。

## 8. 清理、测试与保护

正式Gate的两个WSL SSH child均按现有close(expected_identity)回收，local监听关闭。
远端只读审计与Gate的3个Windows TCP helper均确认已退出，Smoke4个helper也已退出。
无本次连接残留，没有终止任何远端Worker或其他用户进程。

专项：118 passed in 20.22s；全仓：**778 passed in 64.95s，0 failed，0 skipped**。
compileall、git diff --check通过；全部测试离线Mock，不发出真实模型请求。
历史文件SHA-256及冻结源码/配置均核验未变化，失败Run不覆盖为PASS。
HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，已有未提交修改保护，未Commit/Push。

核心Manifest：Smoke 19项，五任务Run 91项，全部大小/SHA匹配。
后续派生审核存本独立审计目录，不修改核心Run文件。
本地离线审核脚本最初读取成功记录不存在的attempt_index字段报KeyError；
改用已保存checkpoint_id完成verify_v2，未重跑模型、未更改任何实验产物。

## 9. 等待的精确变更授权

用户此前要求Prompt冻结、失败即停。因此执行下个版本前需要明确允许T3提示表述修订。
已生成完整提案：`runs/phase2_p2_d2_access_after_windows/p2_d2_access_after_windows_20261126_1e41034b/t3_prompt_revision_proposal.md`及对应JSON。
建议只把订单ID明确为精确JSON字符串并要求保留前缀，其他4任务/模型/Seed/预算/协议不变；
用独立新版配置与新Run完整记录提示变化，不覆盖旧Catalog或失败结果。
审批前不执行任何新Completion，不以修订Prompt后的结果冒充原冻结Prompt的通过。

当前五任务目标仍为**2/5完成，BLOCKED于T3模型参数行为**，不是目标已完成。
已发出异步确认问题，等待用户决定是否批准独立新版Prompt；本轮不自行扩大实验范围。

## 10. 交付索引

- Smoke：`runs/phase2_p2_d2_connection_smoke/p2_d2_windows_transport_smoke_20261126_3d23818c/`
- 五任务原始Gate：`runs/phase2_p2_d2_full_gate_1024/p2_d2_full_gate_1024_windows_20261126_8fb15d86/`
- 传输验证、现场模型证据、独立Checkpoint复核、Proposal及本报告：`runs/phase2_p2_d2_access_after_windows/p2_d2_access_after_windows_20261126_1e41034b/`

本目录统一Manifest为追加审核证据，不将2/5宣称成五任务Gate PASS。
