# AI梦境画画师 2API 网关

把「AI梦境画画师」App 的文生图能力封装为 **OpenAI 兼容** HTTP 接口，底层调用阿里云百炼
`qwen_image3` 模型。内置账号池（自动注册、自动补号、会话保鲜）、API 密钥管理、Web 管理控制台。

- 纯 Python 标准库实现，**零第三方依赖**，Docker 镜像极小、启动快、占用低
- OpenAI 兼容：`POST /v1/images/generations`
- 管理控制台：API 密钥、账号池、全部参数在线配置
- 默认密码 `admin123`，首次登录后请尽快修改

---

## 快速开始

### 方式一：Docker Compose

```bash
docker compose up -d --build
```

### 方式二：Docker

```bash
docker build -t mengjing-2api .
docker run -d --name mengjing-2api -p 8788:8788 -v $PWD/data:/data mengjing-2api
```

### 方式三：直接运行（需 Python 3.10+）

```bash
PORT=8788 DATA_DIR=./data python3 main.py
```

打开 <http://localhost:8788> → 使用默认密码 `admin123` 登录。

---

## 部署到 Coolify

1. 新建 Resource → 选择你的 Git 仓库（或直接指向本目录）。
2. Build Pack 选 **Dockerfile**（项目已内置 `Dockerfile`）。
3. Ports 暴露 `8788`（或设置环境变量 `PORT`）。
4. Volumes 挂载持久化数据目录：容器内 `/data`（存放 `config.json`、`pool.json`）。
5. 部署后访问域名根路径 `/` 即为管理控制台。

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | `8788` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `DATA_DIR` | `./data` | 配置与账号池持久化目录 |

---

## 使用 API

先在控制台「API 密钥」中生成密钥。

```bash
curl -X POST "http://localhost:8788/v1/images/generations" \
  -H "Authorization: Bearer sk-mj-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"一只白色的猫坐在窗台上，阳光洒落","size":"1344x768","style":"二次元"}'
```

响应：

```json
{
  "created": 1730000000,
  "data": [{ "url": "https://....aliyuncs.com/....png", "b64_json": null }],
  "task_id": "659826c5-...",
  "elapsed_s": 70.1,
  "upstream": "qwen_image3 (dashscope)"
}
```

### 参数

| 字段 | 必填 | 说明 |
|------|------|------|
| `prompt` | 是 | 提示词（会先做敏感词检测） |
| `size` | 否 | `1024x1024`（默认）/ `768x1344`（竖）/ `1344x768`（横） |
| `style` | 否 | 画风，默认「二次元」 |

### 接口一览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/v1/images/generations` | API Key | 文生图 |
| GET | `/v1/models` | API Key | 模型列表 |
| GET | `/v1/styles` | API Key | 画风列表 |
| GET | `/v1/pool` | API Key | 账号池状态 |
| GET | `/health` | 无 | 健康检查 |
| GET | `/` | 会话 | 管理控制台 |
| GET | `/docs` | 可选 | 接口文档 |

---

## 工作原理

```
客户端 ──/v1/images/generations──► 本网关 ──► 账号池取号 ──► 上游 App 接口 ──► 阿里云百炼 qwen_image3
                                     │
                                     └── 敏感词检测 → 提交任务 → 轮询结果 → 返回图片 URL
```

上游协议（全部为 POST multipart，会话基于 `JSESSIONID` cookie）：

1. `/yh!tempUser.action` 注册临时账号 → 返回 `id` / `mima`
2. `/yh!dlUser.action` 登录 → 下发 `JSESSIONID`
3. `/ctext!isweigui.action` 敏感词检测
4. `/qwenpic!zhizuo.action` 提交生成任务 → 返回 `taskid`
5. `/qwenpic!jieguo.action` 轮询结果 → 返回图片 OSS 直链

账号池对每个账号维护免费额度（默认 5 张），额度用尽后自动注册新账号补充，并对空闲账号做会话保鲜。

---

## 目录结构

```
.
├── main.py               # 入口
├── app/
│   ├── __init__.py       # 版本号
│   ├── config.py         # 配置加载/持久化
│   ├── security.py       # 密码哈希、会话、API 密钥
│   ├── upstream.py       # 上游协议客户端
│   ├── pool.py           # 账号池
│   ├── server.py         # HTTP 服务、路由、管理 API
│   └── web/              # 控制台与文档页面
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

数据目录（`DATA_DIR`）内含：

- `config.json`：配置、管理员密码哈希、API 密钥
- `pool.json`：账号池持久化

---

## 常见问题

- **429 账号池繁忙**：可用账号暂时不足，稍等片刻或到控制台点「补充账号」。
- **502 上游异常**：上游服务波动，重试即可。
- **400 提示词命中敏感词**：更换措辞。
- **图片打不开**：上游图片链接有效期约 24 小时，请及时转存。

---

## 免责声明

本项目仅供学习与接口研究使用。请遵守相关平台的服务条款与法律法规，不得用于任何商业或非法用途。
