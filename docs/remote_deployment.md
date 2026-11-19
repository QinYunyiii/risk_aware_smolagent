# Remote Qwen3-8B Deployment Status

Last verified: 2026-11-19 17:38:28 Asia/Shanghai (09:38:28 UTC).

## Current Deployment

The user confirmed a time-bounded laboratory reservation for physical GPU 0 and GPU 1. This deployment is an explicit lease override; the project's default policy remains dynamic selection of authorized idle GPUs.

| Worker | Physical GPU | GPU UUID | Remote PID | Remote endpoint | Access |
|---|---:|---|---:|---|---|
| Worker 0 | 0 | `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160` | 382420 | `127.0.0.1:8000` | local `https://localhost:8000/v1` through SSH tunnel |
| Worker 1 | 1 | `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d` | 382422 | `127.0.0.1:8001` | local `https://localhost:8001/v1` through SSH tunnel |

Both workers are independent Qwen3-8B/vLLM instances with `tensor_parallel_size=1`, bfloat16, Hermes tool parser, TLS and API-key authentication. They use approximately 21,567 MiB per GPU. No process on GPU 2–7 was changed.

The current local forwarding uses two independent SSH ControlMasters so one stalled channel can be rebuilt without sharing state with the other:

| Worker | Local ControlMaster PID | Local port |
|---|---:|---:|
| Worker 0 | 582763 | 8000 |
| Worker 1 | 582789 | 8001 |

## Verified Checks

- GPU 0 and GPU 1 passed two idle samples and the compute-process check before launch.
- Ports 8000 and 8001 were unoccupied before launch.
- Worker 0 and Worker 1 each returned the configured model from `/v1/models`.
- Both workers completed a real short generation and returned `READY.`.
- Strict CA verification passed for both endpoints.
- Requests without an API key returned HTTP 401.
- Authenticated `/v1/models` requests returned HTTP 200 on both endpoints.
- Repeating `start --gpu-ids 0,1` reported both workers already healthy and created no duplicate processes.
- After splitting the forwards into independent ControlMasters, both local endpoints returned authenticated HTTP 200 in the final parallel check.
- Full local regression after the management-script change: 76 passed; `pip check` and compile checks passed.

## Operations

Set the private SSH environment without writing secrets into the repository:

```bash
export RISK_AGENT_SSH_HOST=<configured-host>
export RISK_AGENT_SSH_PORT=4035
export RISK_AGENT_SSH_USER=root
export RISK_AGENT_SSH_KEY="$HOME/.ssh/risk_agent_audit"
```

Status and idempotent lease-pinned start:

```bash
.venv/bin/python scripts/manage_remote_vllm.py status
.venv/bin/python scripts/manage_remote_vllm.py start --gpu-ids 0,1
.venv/bin/python scripts/manage_remote_vllm.py wait-health
```

If only the local tunnel drops, restart the tunnel; this does not reload Qwen or change GPU allocation:

```bash
.venv/bin/python scripts/manage_remote_vllm.py tunnel-start
```

If a ControlMaster still exists but HTTPS forwarding stalls, rebuild only the local forwarding layer:

```bash
.venv/bin/python scripts/manage_remote_vllm.py tunnel-stop
.venv/bin/python scripts/manage_remote_vllm.py tunnel-start
```

At lease expiration, release resources in this order:

```bash
.venv/bin/python scripts/manage_remote_vllm.py tunnel-stop
.venv/bin/python scripts/manage_remote_vllm.py stop
.venv/bin/python scripts/manage_remote_vllm.py status
```

`stop` validates the recorded PID and command line before sending TERM. It does not terminate unrecognized processes.

## Persistence Boundary

- The two vLLM workers use `nohup` and survive the SSH session or local WSL tunnel closing.
- The SSH entry showed intermittent handshake and forwarding stalls during deployment. The two independent local tunnels may need reconnection when SSH is unstable or WSL restarts; reconnecting them does not reload the model.
- No remote systemd/supervisor auto-start service was installed. A remote container/host reboot or process crash will require the idempotent `start --gpu-ids 0,1` and `wait-health` sequence.
- The reservation end time was not provided. The user must stop the workers when the approved usage window ends.
- API keys and TLS private keys remain in private configuration and are not recorded here or in ordinary logs.
