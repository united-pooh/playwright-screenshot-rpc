# systemd 部署说明

这两个单元文件用于把 API 和 Worker 分开托管：

- `rpc-server-api.service`
- `rpc-server-worker.service`

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
BROWSER_RESTART_INTERVAL=200
```

查看状态和日志：

```bash
sudo systemctl status rpc-server-api.service
sudo systemctl status rpc-server-worker.service
sudo journalctl -u rpc-server-api.service -f
sudo journalctl -u rpc-server-worker.service -f
```
