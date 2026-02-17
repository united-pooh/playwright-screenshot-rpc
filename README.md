# Playwright 截图服务器

一个基于 **Playwright** 的 JSON-RPC 2.0 后端服务，通过在无头浏览器中渲染任意 HTML 并返回 Base64 编码的截图。

---

## 项目结构

```
playwright-screenshot-server/
├── config.py                  # 全局设置（支持环境变量覆盖）
├── requirements.txt
├── README.md
├── server/
│   ├── __init__.py
│   ├── main.py                # aiohttp HTTP 服务器和入口点
│   ├── rpc_handler.py         # JSON-RPC 2.0 分发器
│   ├── screenshot_service.py  # Playwright 浏览器管理与截图捕获
│   └── models.py              # Pydantic 请求/响应模型
└── tests/
    ├── test_rpc.py            # 单元测试（无需浏览器）
    └── test_screenshot.py     # 集成测试（需要 Playwright）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 启动服务器

```bash
python -m server.main
# 或者
python server/main.py
```

服务器默认监听 `http://0.0.0.0:8080`。

### 3. 环境变量

| 变量名             | 默认值          | 描述                                     |
|--------------------|----------------|------------------------------------------|
| `HOST`             | `0.0.0.0`      | 绑定地址                                 |
| `PORT`             | `8080`         | 监听端口                                 |
| `BROWSER_TYPE`     | `chromium`     | 浏览器类型：`chromium`, `firefox` 或 `webkit` |
| `HEADLESS`         | `true`         | 是否以无头模式运行浏览器                   |
| `VIEWPORT_WIDTH`   | `1280`         | 默认视口宽度                             |
| `VIEWPORT_HEIGHT`  | `720`          | 默认视口高度                             |
| `LOG_LEVEL`        | `INFO`         | 日志级别：`DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## JSON-RPC API

所有请求均发送至 `POST /rpc`，且 `Content-Type: application/json`。

### 方法: `screenshot`

渲染 HTML 并捕获截图。

**请求示例**

```json
{
  "jsonrpc": "2.0",
  "method": "screenshot",
  "params": {
    "html": "<h1>Hello World</h1>",
    "selector": "#my-element",
    "viewport": { "width": 1280, "height": 720 },
    "image_type": "png",
    "wait_until": "networkidle"
  },
  "id": 1
}
```

**完整参数说明**

| 字段                  | 类型              | 默认值           | 描述                                                     |
|-----------------------|------------------|------------------|----------------------------------------------------------|
| `html` *(必填)*       | `string`         | —                | 要渲染的原始 HTML                                         |
| `selector`            | `string`         | `null`           | CSS 选择器 – 仅截取匹配的元素                             |
| `clip`                | `object`         | `null`           | 像素区域 `{x, y, width, height}`; 优先级高于 selector     |
| `full_page`           | `boolean`        | `false`          | 是否截取整个可滚动页面                                   |
| `viewport`            | `object`         | 1280×720         | 像素单位的 `{width, height}`                             |
| `wait_until`          | `string`         | `"networkidle"`  | 渲染完成判定：`load` / `domcontentloaded` / `networkidle` |
| `wait_for_selector`   | `string`         | `null`           | 截图前等待的额外 CSS 选择器                               |
| `timeout_ms`          | `integer`        | `30000`          | 最大页面加载等待时间（毫秒，0–120,000）                   |
| `extra_http_headers`  | `object`         | `{}`             | 转发给页面的 HTTP 请求头                                  |
| `style_overrides`     | `string`         | `null`           | 注入到 `<head>` 的原始 CSS                               |
| `scripts`             | `string[]`       | `[]`             | 截图前执行的 JS 脚本片段                                  |
| `image_type`          | `"png"\|"jpeg"` | `"png"`          | 输出格式                                                 |
| `quality`             | `integer`        | `90`             | JPEG 质量 1–100（PNG 格式下忽略）                         |
| `scale`               | `float`          | `1.0`            | 设备像素比 (0.1–4.0)                                     |
| `omit_background`     | `boolean`        | `false`          | 隐藏默认背景（仅限 PNG，支持透明度）                       |

**响应示例**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "image": "<base64 编码的图像数据>",
    "image_type": "png",
    "width": 1280,
    "height": 720,
    "size_bytes": 54321
  }
}
```

---

### 方法: `ping`

健康检查。

```json
{ "jsonrpc": "2.0", "method": "ping", "id": 1 }
```
→ `{ "result": { "pong": true } }`

---

### 方法: `get_methods`

获取可用的 RPC 方法列表。

```json
{ "jsonrpc": "2.0", "method": "get_methods", "id": 1 }
```
→ `{ "result": { "methods": ["get_methods", "ping", "screenshot"] } }`

---

## 错误代码

| 代码     | 名称                 | 含义                                   |
|----------|----------------------|----------------------------------------|
| `-32700` | 解析错误 (Parse Error) | 无效的 JSON                            |
| `-32600` | 无效请求 (Invalid Request) | 不是有效的 JSON-RPC 对象               |
| `-32601` | 方法未找到 (Method Not Found) | 未知的请求方法                         |
| `-32602` | 参数无效 (Invalid Params) | 参数校验失败                           |
| `-32603` | 内部错误 (Internal Error) | 服务器非预期错误                       |
| `-32001` | 截图失败 (Screenshot Failed) | 通用的截图操作失败                     |
| `-32002` | 浏览器错误 (Browser Error) | 浏览器未启动或已崩溃                   |
| `-32003` | 选择器未找到 (Selector Not Found) | CSS 选择器未匹配到任何元素             |
| `-32004` | 超时 (Timeout)        | 页面或元素加载超时                     |

---

## 使用示例

### Python 客户端

```python
import base64
import json
import httpx

def screenshot(html: str, selector: str | None = None) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "method": "screenshot",
        "params": {"html": html, "selector": selector},
        "id": 1,
    }
    resp = httpx.post("http://localhost:8080/rpc", json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data and data["error"]:
        raise RuntimeError(data["error"])
    return base64.b64decode(data["result"]["image"])


# 截取特定元素
image_bytes = screenshot(
    html='<div id="card" style="padding:20px;background:#fff">Hello!</div>',
    selector="#card",
)
with open("card.png", "wb") as f:
    f.write(image_bytes)
```

### curl

```bash
curl -s -X POST http://localhost:8080/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "screenshot",
    "params": {
      "html": "<h1 style=\"font-size:72px\">你好</h1>",
      "selector": "h1",
      "image_type": "png"
    },
    "id": 1
  }' | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
open('out.png','wb').write(base64.b64decode(d['result']['image']))
print('已保存 out.png')
"
```

---

## 运行测试

```bash
# 仅执行单元测试（无需浏览器）
pytest tests/test_rpc.py -v

# 执行集成测试（需要执行 `playwright install chromium`）
pytest tests/test_screenshot.py -v

# 运行所有测试并生成覆盖率报告
pytest --cov=server --cov-report=term-missing
```
