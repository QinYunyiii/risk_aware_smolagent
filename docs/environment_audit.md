# Day 1 Environment Audit

审计日期：2026-11-17（Asia/Shanghai）  
审计原则：先完成只读审计；随后按用户明确授权，在远程新建隔离 Conda 环境、下载模型，并在两张当时空闲的 GPU 上执行 vLLM/HTTPS smoke test。验收后已停止本任务的服务并释放 GPU；未停止他人进程、修改本地 Docker 或修改 Recovery-Bench。

## 1. 审计结论摘要

| 项目 | 状态 | 证据或限制 |
|---|---|---|
| Codex 工作区 | 已确认 | `/home/three/projects/risk-aware-smolagents` |
| 本地平台 | 已确认 | WSL2 内核 `microsoft-standard-WSL2`，Ubuntu 24.04.5 LTS，主机名 `DESKTOP-A1E0NC0` |
| 默认 Python | 部分可用 | `python` 不在 PATH；`/usr/bin/python3` 为 3.12.3 |
| Conda | 未确认可用 | `conda` 不在 PATH，常见位置未发现可执行文件；未安装 |
| Git | 已确认 | Git 2.43.0；项目原有空 `.git`，本次完成初始化 |
| Docker CLI/Engine | 已确认 | Docker CLI 29.8.2；经只读授权可查询 engine 资源 |
| Recovery-Bench | 已定位 | `/home/three/projects/agent-intervention/recovery-bench`，HEAD `c5f83f2b` |
| Harbor | 本地已安装 | 本地父项目虚拟环境中的 Harbor 0.4.0，不是独立远程服务 |
| 远程 SSH | 已确认 | `root@121.48.161.243:4035`；ED25519 Host Key 已由服务器控制台确认；专用 key 登录成功 |
| 远程运行边界 | 已由用户澄清 | 共享实验室服务器；SSH 进入 Ubuntu 22.04 Docker 容器；宿主 OS 未审计 |
| 远程 GPU/CUDA | 已审计 | 容器可见 8 × RTX 4090；每次动态选择两张空闲卡；禁止抢占他人任务 |
| vLLM/Qwen3-8B | 双实例 smoke 通过 | 新环境 `risk-agent-vllm`：vLLM 0.34.0；模型位于 `/mnt/risk-aware-smolagents/remote_models/Qwen3-8B`；GPU 3/7 独立实例均完成加载和短生成 |
| HTTPS/TLS | SSH 隧道方案验证通过 | vLLM 原生 TLS 只监听远端 loopback；本地 `127.0.0.1:8000/8001` 经 SSH 隧道访问，严格 CA 与 API Key 验证通过；验收后服务已停止 |

## 2. 本地开发环境

执行并核实：

```text
pwd
uname -a
cat /proc/version
cat /etc/os-release
python --version
python3 --version
conda --version
git --version
git status --short
docker --version
df -h .
free -h
```

结果：

- 当前目录为本项目根目录。
- Linux 内核为 WSL2，发行版为 Ubuntu 24.04.5 LTS；因此当前 Codex 位于本地 Windows 的 WSL，而不是远程 GPU 服务器。
- `python` 命令不存在；系统 `python3` 为 3.12.3。Recovery-Bench 的独立虚拟环境也使用 Python 3.12.3。
- `conda` 命令不存在，且 `/home/three/miniconda3`、`/home/three/anaconda3`、`/opt/conda` 中未发现可执行的 Conda。没有执行安装。
- Git 2.43.0 可用。初查时项目不是有效 Git 仓库，`.git` 只是空目录；本次执行 `git init` 后成为空的新仓库，尚无 commit。
- 本地文件系统约 1007 GiB，总使用约 11 GiB，可用约 945 GiB。
- 本地内存约 17 GiB，可用约 15 GiB；swap 8 GiB，审计时未使用。

## 3. 本地 Docker 环境

普通沙箱用户访问 `/var/run/docker.sock` 被拒绝；随后经只读授权运行 `docker ps -a`、`docker image ls`、`docker volume ls`、`docker network ls` 和 `docker inspect`。没有执行任何变更命令。

### 3.1 Images

| Image | Tag | 大小 |
|---|---|---:|
| `alexgshaw/openssl-selfsigned-cert` | `20251031` | 181 MB |
| `python` | `3.12-slim` | 179 MB |
| `hello-world` | `latest` | 25.9 kB |

### 3.2 Containers

共有 3 个容器，均由 `alexgshaw/openssl-selfsigned-cert:20251031` 创建，审计时全部为 exited，未发布端口：

- `openssl-selfsigned-cert__9fu4hut-main-1`：Exited (255)，约 7 天前停止；
- `openssl-selfsigned-cert__7w4js8k-main-1`：Exited (137)，约 5 周前停止；
- `openssl-selfsigned-cert__h3juhnu-main-1`：Exited (137)，约 5 周前停止。

### 3.3 Volumes 与 mounts

- `docker volume ls` 没有命名 volume。
- 每个容器各使用 3 个可写 bind mount，把对应历史 job 的宿主目录挂载到：
  - `/logs/agent`；
  - `/logs/artifacts`；
  - `/logs/verifier`。
- 宿主路径位于 `/home/three/projects/agent-intervention/results/jobs/...`，各 trial 使用各自的 job/task 目录。

Harbor 0.4.0 的 Docker backend 也明确把三个 trial 目录 bind mount 到 `/logs/*`（`docker.py:139-150`；`docker-compose-base.yaml:1-10`）。`start()` 会先针对同一个 session 执行 compose `down --remove-orphans`，再 `up --detach --wait`（`docker.py:328-355`）；`stop()` 根据配置执行 stop 或 down（`docker.py:372-398`）。这支持“每个 trial 从镜像创建环境”和旧同名容器清理，但不等于任意历史状态的 Checkpoint/Restore。

## 4. 本地 Recovery-Bench 与 Harbor 运行时

- Recovery-Bench 路径：`/home/three/projects/agent-intervention/recovery-bench`。
- Git HEAD：`c5f83f2b`。
- 外部仓库已有 13,724 个 tracked-file modifications，主要是历史 `runs/` 产物。本次未修改、还原或清理任何内容。
- 项目依赖声明要求 Python `>=3.12`、`harbor[modal]>=0.4.0`（`pyproject.toml:6-16`）。
- 实际 Harbor 可执行文件：`/home/three/projects/agent-intervention/.venv/bin/harbor`，版本 0.4.0。
- 实际 Harbor Python 包：`/home/three/projects/agent-intervention/.venv/lib/python3.12/site-packages/harbor`。
- Harbor 是本地 CLI/library；Recovery-Bench 通过子进程执行 `harbor run`，不是调用一个已部署的 Harbor HTTP server（`pipeline.py:91-138,171-185`）。

## 5. 远程 GPU 环境

### 5.1 SSH 目标身份与运行边界

- SSH 目标：`root@121.48.161.243:4035`。
- 服务器 ED25519 Host Key 指纹 `SHA256:g8sbLpuIhU3e/BydNugmhs+VMXbJ6fuqUEwqmsTUESM` 已由用户通过服务器控制台核对。
- 本地生成项目专用 ED25519 key，私钥权限为 600，路径在本机 `~/.ssh/`，未写入项目或日志；`BatchMode` + `IdentitiesOnly` + `StrictHostKeyChecking` 登录成功。
- SSH 实际进入 hostname `36f825bcc5da`。`systemd-detect-virt`、`/.dockerenv` 与 `/proc/1/cgroup` 均证明它是 Docker 容器。
- 容器用户空间为 Ubuntu 22.04.3；内核来自 Ubuntu 22.04 宿主系列。该目标不是项目描述的 Ubuntu 24.04 双 GPU 宿主机。

### 5.2 GPU、Driver 与 CUDA

`nvidia-smi` 在 2026-11-17 10:03（远程显示时间）看到 8 张 RTX 4090，每张 24,564 MiB，Driver 580.178.04，NVIDIA runtime 报告 CUDA 13.0。`/usr/local/cuda/bin/nvcc` 为 CUDA toolkit 12.5（V12.5.82）。

| GPU | Used | Free | 审计时状态 |
|---:|---:|---:|---|
| 0 | 4,179 MiB | 19,903 MiB | 89% utilization，P2 |
| 1 | 22,033 MiB | 2,049 MiB | P8 |
| 2 | 15 MiB | 24,067 MiB | P8 |
| 3 | 15 MiB | 24,067 MiB | P8 |
| 4 | 21,747 MiB | 2,335 MiB | 21% utilization，P2 |
| 5 | 22,155 MiB | 1,927 MiB | 28% utilization，P2 |
| 6 | 21,505 MiB | 2,577 MiB | 27% utilization，P2 |
| 7 | 15 MiB | 24,067 MiB | P8 |

`nvidia-smi` 的 process 表为空，但显存明显被占用，说明容器 PID namespace 看不到宿主/其他容器进程。不得把“process 表为空”解释为 GPU 可占用。用户已确认本项目可以从 8 张共享卡中动态选择两张真正空闲的卡；审计时 GPU 2/3/7 仅是候选，部署前必须重新检查，不能视为已预留。

### 5.3 Conda、Python、vLLM 与权重

- 非交互 SSH 未加载 Conda；interactive login shell 中 Conda 25.1.1 位于 `/root/miniconda3`。
- base Python 为 3.12.9；容器系统 Python 为 3.10.12。
- 可见环境包括 base、`agent` 以及多个已有研究环境。
- 初始审计确认 `agent` 环境为 Python 3.11.16、torch 2.11.0、transformers 5.12.1，且没有 vLLM、Hugging Face Hub、smolagents 或 uv。为避免改变该共享环境的 torch/transformers 依赖，保留 `agent` 不动。
- 按用户明确授权新建隔离环境 `/root/miniconda3/envs/risk-agent-vllm`：Python 3.12.16、vLLM 0.34.0、torch 2.14.0+cu132、transformers 5.22.2、uv 0.13.8、ModelScope Hub 0.7.1；`pip check` 报告 `No broken requirements found`。
- Hugging Face 连接超时后改用可严格验证 TLS 的 ModelScope Hub 下载官方 `Qwen/Qwen3-8B` snapshot，目标为 `/mnt/risk-aware-smolagents/remote_models/Qwen3-8B`。
- 下载结果为 5 个 safetensors 分片，合计 16,381,516,776 bytes；无 `.incomplete` 文件。`model.safetensors.index.json` 引用的分片集合与实际文件完全一致，399 个权重映射项均可解析；`config.json` 为 `model_type=qwen3`、`Qwen3ForCausalLM`、36 层。
- 在当时空闲的 GPU 7（15 MiB、0% utilization、无 compute process）完成最小 CUDA smoke test：环境仅可见该卡，识别 RTX 4090，64×64 CUDA 矩阵运算结果正确，进程退出后 GPU 7 恢复为 15 MiB、0%。未加载模型。
- 执行 `vllm download-kernels` 后安装 `flashinfer-cubin 0.7.3`；`flashinfer-jit-cache 0.7.3+cu130` 因远端无法连接 GitHub 未安装。首次模型加载暴露 CUDA toolkit 12.5 不支持 FlashInfer JIT 参数 `--compress-mode=size`；使用 vLLM 自带并有源码说明的 `VLLM_USE_FLASHINFER_SAMPLER=0`，只禁用 FlashInfer top-k/top-p sampler。实际 attention backend 为 FlashAttention v2，模型加载和生成成功。
- 单实例与双实例 smoke 均使用 BF16、`max_model_len=8192`、`max_num_seqs=1`、eager 模式、Hermes tool parser 和 0.90 GPU memory utilization。Worker 0/GPU 3 与 Worker 1/GPU 7 均返回模型 ID `Qwen/Qwen3-8B` 并严格生成预期短文本。

### 5.4 磁盘与内存

- 容器 overlay：29 TiB，总使用 27 TiB；模型下载与安装后约 683 GiB 可用，使用率仍为 98%。可用容量不小，但高水位会增加缓存、日志和双实例运行风险。
- 内存：251 GiB total，约 192 GiB available。
- swap：2 GiB，审计时已全部使用。

### 5.5 两实例部署判断

Qwen3-8B 每张空闲 24 GiB 4090 一个独立实例已通过实测。启动前复查只有 GPU 3/7 处于 15 MiB、0% 基线，其他 6 张均在使用；随后 Worker 0/GPU 3 与 Worker 1/GPU 7 同时运行，观测显存约 20.3 GiB 和 21.6 GiB。Worker 0 日志给出 8,192-token 请求最大理论并发约 3.52，两个实例都完成 HTTPS 短生成。验收结束后仅终止本任务的两个 PID，GPU 3/7 均恢复为 15 MiB、0%。这证明保守 smoke 配置可行，但最终吞吐、40,960 原生上下文和更高并发仍需单独实验，不能从本次结果外推。

## 6. 本地到远程 SSH/HTTPS/TLS

| 检查项 | 结果 |
|---|---|
| SSH client | 可用 |
| SSH Host Key | 已由服务器控制台确认 |
| SSH 认证 | 专用 ED25519 key，严格校验，登录成功 |
| SSH 实际目标 | Ubuntu 22.04 Docker 容器，不是预期 Ubuntu 24.04 宿主机 |
| HTTPS 8000 | Worker 0 远端只监听 `127.0.0.1:8000`；经 SSH 隧道映射到本地同端口，模型列表和短生成均返回 200 |
| HTTPS 8001 | Worker 1 远端只监听 `127.0.0.1:8001`；经 SSH 隧道映射到本地同端口，模型列表和短生成均返回 200 |
| TLS CA bundle | 项目私有 CA 位于本机仓库外受限目录；CA 私钥未上传远端，服务证书含 `localhost`/`127.0.0.1` SAN |
| TLS 校验 | 默认 trust store 正确拒绝私有 CA；显式 CA 验证通过；未使用 `-k` 或 `verify=False` |
| 代理影响 | 首次请求命中本地 proxy，已用 `--noproxy '*'` 重测并以直连结果为准 |
| VPN/SSH tunnel | 已验证 `127.0.0.1:8000/8001` 双端口 SSH local forwarding；无需开放公网 vLLM 端口 |
| API 鉴权 | 两端无 Key 均返回 401；正确 Key 的 `/v1/models` 和 chat completion 返回 200；Key 不在仓库或日志中 |
| 防火墙/NAT | 不再依赖公网 8000/8001 的未知 NAT；模型服务只绑定远端 loopback |

公共地址的 8000/8001 归属仍未知，但 SSH 隧道方案绕开了该不确定性。验收时本地分别访问 `https://localhost:8000/v1` 和 `https://localhost:8001/v1`，两个 Endpoint 均完成严格 TLS、API Key、模型列表和生成测试。验收后按共享资源规则停止隧道和两个 vLLM 进程；证书、模型、环境与日志保留供 Day 2 重启。

## 7. 当前问题与待解决事项

1. Day 2 每次启动前重新检查共享 GPU 并记录 Worker/GPU 映射；不得假设 GPU 3/7 永久可用。
2. 正式服务启动配置必须保留 `VLLM_USE_FLASHINFER_SAMPLER=0`，除非以后成功安装匹配的 JIT cache 并重新验证；当前 FlashAttention v2 + PyTorch sampler 已通过功能 smoke。
3. 当前仅验证 8K/单并发/eager 配置；扩大上下文、并发或启用 CUDA Graph 前需要独立性能与显存测试。
4. SSH 4035 偶发连接超时；长期运行应增加受控重连/健康检查，但不得重复启动模型进程。
5. 远程磁盘使用率 98%、swap 100%，高资源操作前需继续检查容量和并发影响。
6. 决定 Day 2 本地使用系统 Python venv 还是新增 Conda；当前本地 Conda 不可用。
7. 本地 Docker 日常访问仍需要额外权限；本次不改系统配置。

## 8. 审计状态

本地环境、Docker、Recovery-Bench、本地 Harbor、远程 SSH/GPU/CUDA/Conda/磁盘和端口均已完成审计；隔离 vLLM 环境、Qwen3-8B 权重、单实例、双实例及本地经 SSH 隧道的严格 HTTPS 均有真实测试证据。核心 Day 1 remote connection Gate 已通过。由于服务按 smoke-test 范围在验收后主动停止、FlashInfer JIT cache 未安装且本地 Day 2 Python/Docker 权限仍待处理，状态记为 **CONDITIONAL PASS**。
