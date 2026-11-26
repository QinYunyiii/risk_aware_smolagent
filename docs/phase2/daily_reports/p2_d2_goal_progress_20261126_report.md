# P2-D2 Real-Qwen Gate — Active Goal Progress

日期：2026-11-26（Asia/Shanghai）。目标保持active，**尚未完成五个真实Qwen任务验收**。

## 当前结果

四领域新增Train五任务选择和冻结1024-token配置重新通过正式local-preflight，
但新的真实连接流程在SSH阶段受阻；本轮GET=0、Completion=0、真实任务完成=0/5。
不把本地PASS、历史T1结果或单测通过改称五任务真实Gate通过。

本轮用户授权“推进P2-D2，有问题解决”，按原部署边界使用现有服务，不启动/重启/停止vLLM、
不占用新GPU，不修改Checkpoint、模型参数、Catalog、故障或标签语义。

## 已完成的实际工作

1. 核验最新Runner、固定任务、Provider/恢复调用路径及当前未提交工作区。
2. 在现有SSHProcess加入原生-v日志，保存在已有私有stderr，保留30秒就绪与15秒ConnectTimeout。
3. 完成一次带详细日志的新Connection Smoke，取得此前没有的实际SSH阶段证据。
4. 根据原生配置中的lowdelay/throughput，准备显式可选IPQoS=none连接profile，仅改变网络流量类别，
   不改变SSH加密/认证、TLS、推理参数或预算；默认仍为原OpenSSH配置。
5. profile验证在初始SSH连接前置阶段失败，未执行到改变后的Tunnel；不能声称该profile有效或无效。
6. 初始专项116通过；profile专项117通过；最终全仓**777 passed in 65.32s，0 failed，0 skipped**。
7. compileall与git diff --check通过。历史文件、冻结配置和实验核心保持不变。

## 本轮两个独立连接Run

| Run | 实际结果 | 请求/资源 |
| --- | --- | --- |
| `p2_d2_goal_connection_20261126_a20f2120` | 初始双Worker实时归属通过；W0 TCP连接、SSH密钥交换/主机验证通过，未见SERVICE_ACCEPT/Authenticated，最终就绪timeout | GET0，W1复核0/W1隧道0；本次owned child受控回收 |
| `p2_d2_goal_connection_ipqos_20261126_9ee02966` | 初始只读SSH连接即报Connection timed out，退出255；尚未执行到IPQoS=none Tunnel | Tunnel0，GET0，无模型调用 |

原始Run全部保留，各自使用新唯一ID；没有自动重试同一请求、重放工具或修改失败结果。
第二个Run是独立单变量连接profile尝试，但改变后的阶段未到达，因此**没有完成对IPQoS假设的验证**。
当前根因仍不能归结为某个SSH算法、服务器故障或Qwen行为。

本轮SSH Client共3个：两次初始只读SSH尝试（一次成功、一次TCP超时）及一个W0 Tunnel child。
只读远端归属实际成功执行一次。HTTP、Completion、Agent工具执行均0。
已停止追加真实连接；没有用户新的网络检查结果时不盲目重复相同失败流程。

## 五任务准备与尚欠证据

Catalog：80模板/160实例；Train/Validation/Test模板48/16/16。
Canonical Hash：`c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c`。

| 顺序 | 新Train任务 | 领域 | 本轮真实执行 |
| --- | --- | --- | --- |
| T1 | p2_data_region_units_sum_001 | data_analysis | 未启动 |
| T2 | p2_file_report_control_001 | file_retrieval | 未启动 |
| T3 | p2_api_process_control_001 | api_workflow | 未启动 |
| T4 | p2_logic_chain_control_001 | calculation_logic | 未启动 |
| T5 | p2_data_region_units_sum_002 | data_analysis | 未启动 |

每个任务至少3个reference步骤，全部新增Train。正式local-preflight Run：
`runs/phase2_p2_d2_full_gate_1024/p2_d2_goal_local_20261126_91471bc3/`。

P2-D2现有冻结协议是：第二次依赖工具Observation后Checkpoint，独立恢复、六Hash、
精确首次输入、一回受控恢复模型响应及重放防护；execute_restored_response=false。
不会把这次受控请求称为完整Agent运行到终止；完整resume(stream=True)+Verifier属于既有B3流程。
当前这些真实验收项仍未在新五任务Run中执行，详见goal_requirement_audit.json。

后续成功仍需：当前远端模型/版本/权重与GPU映射证据、严格HTTPS身份验证，
然后固定顺序使用正式full-five-task-gate-1024，每任务至多一次Source Attempt，失败立即停止。
不会绕过现有门禁或用历史权重hash冒充新审计。

## 需要用户协助的外部信息

已通过当前会话的异步问题请求在Windows PowerShell检查：

```powershell
ssh -p 4035 -o ClearAllForwardings=yes -o ConnectTimeout=15 root@121.48.161.243 "hostname; id -u"
```

只需成功/失败及错误信息，不发送密码或密钥。此项区分Windows/WSL网络路径差异；
不是要求新OpenAI API Key，也不是要求重启共享服务。
目标不缩小为“单测通过”，不声明完成；等待该外部状态后继续处理真实连接与五任务Gate。

## Git和文件范围

HEAD `bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；本轮实际新增修改仅：

- `src/risk_agent/monitoring/connection_lifecycle.py`：原生详细日志及显式可选IPQoS profile。
- `scripts/run_p2_d2_connection_smoke.py`：该可选profile的CLI入口和manifest记录。
- `tests/test_connection_lifecycle.py`：详细日志和安全profile/无副作用拒绝测试。

既有用户Stage A修改保留；源码/config/history前后逐文件复核。
本轮没有Commit/Push。IPQoS profile为**尚未完成真实验证的可选项**，不能标成已确认根因修复。
审计：`runs/phase2_p2_d2_goal_progress/p2_d2_goal_progress_20261126_91471bc3/`，包含baseline、计划、连接原始输出、测试、要求核验和资源检查。

## 状态

**P2-D2 REAL-QWEN GATE — INCOMPLETE / SSH ACCESS BLOCKED**。
实际推进获得SSH阶段证据、通过本地五任务准备及777项回归，但仍欠完整真实五任务验收。
历史Run和原始结果保持不变；没有进入P2-D3或正式160任务采集。
