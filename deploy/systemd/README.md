# systemd 部署说明

这两个单元文件用于把 API 和 Worker 分开托管：

- `rpc-server-api.service`
- `rpc-server-worker.service`

2C2G 机器建议只启用 1 个 Worker 实例。任务数量不做上限，统一由 Redis 排队；单机通过 `MAX_CONCURRENT_SCREENSHOTS=1`、浏览器轮换和 Worker 自重启控制内存。

如果你想偷懒，也可以把 `AUTO_START_WORKER=true` 写进环境变量，让 `server.main` 自动拉起一个托管 Worker 子进程。
但这种模式下不要再同时启用 `rpc-server-worker.service`，否则会变成两个 Worker 并行消费同一个 Redis 队列。

建议目录布局：

```text
/opt/rpc-server
├── .venv/
├── server/
├── config.py
├── .env
└── deploy/systemd/
```

建议把运行环境变量单独放到：

```bash
/etc/rpc-server/rpc-server.env
```

最小安装步骤：

```bash
sudo mkdir -p /etc/rpc-server
sudo cp /opt/rpc-server/.env /etc/rpc-server/rpc-server.env
sudo cp /opt/rpc-server/deploy/systemd/rpc-server-api.service /etc/systemd/system/
sudo cp /opt/rpc-server/deploy/systemd/rpc-server-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpc-server-api.service
sudo systemctl enable --now rpc-server-worker.service
```

推荐在 `/etc/rpc-server/rpc-server.env` 中至少设置：

```bash
HOST=0.0.0.0
PORT=8080
MAX_CONCURRENT_SCREENSHOTS=1
BROWSER_RESTART_INTERVAL=50
WORKER_MAX_TASKS=200
WORKER_MAX_AGE_SECONDS=14400
WORKER_MAX_RSS_MB=750
DEFAULT_TIMEOUT_MS=15000
DEFAULT_WAIT_FOR_SELECTOR_TIMEOUT=5000
```

查看状态和日志：

```bash
sudo systemctl status rpc-server-api.service
sudo systemctl status rpc-server-worker.service
sudo journalctl -u rpc-server-api.service -f
sudo journalctl -u rpc-server-worker.service -f
```
