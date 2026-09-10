# AIGateway — 本地 AI 模型网关与可视化控制台

聚合多家模型厂商,对外提供统一的 **OpenAI 兼容 API**,配套科技风管理控制台、接口文档页与实时数据大屏。

Flask 单机部署,Windows 直接运行,数据库零配置(SQLite),前端零构建(原生 JS + ECharts)。

---

## 功能总览

### 统一 API 与协议转换

| 能力 | 说明 |
|---|---|
| 统一 OpenAI 兼容接口 | `/v1/chat/completions`(流式 SSE + 非流式)、`/v1/completions`、`/v1/embeddings`、`/v1/images/generations`、`/v1/models` |
| 全协议转换 | OpenAI 兼容厂商直通;**Anthropic Claude / Google Gemini 原生协议 ↔ OpenAI 格式完整互转**(消息、system、工具调用、多模态图片、流式事件);Azure OpenAI 部署名映射 |
| auto 智能选型 | `model="auto"` 由网关自动选择实际模型:优先按管理员偏好顺序,未配置时按 Key 白名单 → 全部渠道(优先级降序);候选模型渠道全故障时自动降级到下一候选;实际模型在响应 `model` 字段返回 |

### 高可用与路由

| 能力 | 说明 |
|---|---|
| 负载均衡 | 优先级分层,同层内按权重加权随机分流 |
| 故障转移 | 超时 / 5xx / 429 / 连接错误自动切换下一渠道,客户端无感知 |
| 熔断器 | 连续失败 N 次自动熔断,冷却后进入半开状态放行一次真实请求探活,失败则冷却时间指数退避 |
| 每渠道独立代理 | 支持 http / https / socks5,渠道专属 HTTP Client,完全忽略系统环境代理 —— 无全局代理也能访问外网 API,本地渠道直连 |

### 计费与额度

| 能力 | 说明 |
|---|---|
| API Key 体系 | 多 Key 签发,每 Key 独立令牌额度(-1 无限)、模型白名单、过期时间;请求前预检,超额返回 429 |
| 模型单价 | 全局单价表(元/百万 token),渠道级价格覆盖;一键导入 12 家预设厂商参考价;单价表含上下文窗口 / 最大输出元数据,`only_missing` 填充模式不覆盖已配置价格 |
| 精确计费 | 流式自动注入 `stream_options.include_usage` 获取精确 usage;拿不到时按中英文比例估算(明细中标记 ≈) |
| 缓存 token | 解析并落库各家缓存命中:OpenAI `prompt_tokens_details.cached_tokens`、Anthropic `cache_read/cache_creation_input_tokens`、Gemini `cachedContentTokenCount` |

### 渠道管理与探测

| 能力 | 说明 |
|---|---|
| 预设厂商 | OpenAI、Anthropic、Gemini、Azure、DeepSeek、Kimi、通义千问、智谱、OpenRouter、Groq、硅基流动、Ollama 等 12 家,新增渠道自动填充 base_url / 适配器 / 参考价 |
| 自动获取模型 | 一键从上游模型列表端点拉取模型清单,**无需手动输入模型 ID**;返回表格展示上下文窗口 / 最大输出 / 参考价,元数据两级来源:上游返回(OpenRouter 风格 `context_length`/`pricing`、Gemini `inputTokenLimit`/`outputTokenLimit`)优先,内置知识库兜底(约 40 个主流模型,前缀匹配);勾选后自动写入单价表 |
| 定时健康探测(零额度消耗) | **L1 定时探测**:GET 上游模型列表端点,不消耗任何 token,验证连通性 / 鉴权 / 代理并记录延迟,默认 300 秒一轮(可配置,0=关闭);**L2 深度测试**:`max_tokens=1` 真实补全,仅管理台手动触发,消耗极少 token |
| 多 Key 轮询 | 单渠道可配多个上游 Key(逗号分隔),失败时自动切换 |

### 统计与可视化

| 能力 | 说明 |
|---|---|
| 调用明细 | 每次调用记录时间 / Key / 渠道 / 模型 / 输入输出缓存 tokens / 费用 / 延迟 / 状态码 / 重试次数 / 错误信息 |
| 每日用量 | 按天聚合每日 tokens(输入/输出/缓存)、总调用次数、活跃模型数、费用,组合图展示 |
| 热点分析 | 热点模型排名、调用时段热力图(周 x 24 小时,用量统计页 / 总览 / 大屏三处)、模型 x 时段交叉热力 |
| 数据大屏 | 全屏科技风:核心指标大数字、SSE 实时调用流水、模型调用占比、24h 趋势、渠道健康卡片(含探测延迟)、时段热力图、Key 消耗排行 |
| 接口文档页 | `/docs` 免登录:接口清单、Python / curl / JavaScript 调用示例(自动填入当前主机)、auto 说明、错误码表,可直接发给调用方 |

---

## 快速开始

### 环境要求

- Python 3.10+(开发验证于 3.12)

### 安装与启动

```bash
# 1. 虚拟环境(已创建则跳过)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 2. 启动(生产模式,waitress,32 线程)
.venv/Scripts/python app.py --port 5100

# 调试模式(flask reloader)
.venv/Scripts/python app.py --port 5100 --dev
```

| 页面 | 地址 | 说明 |
|---|---|---|
| 管理控制台 | http://127.0.0.1:5100 | 默认密码 `admin123`,**登录后请修改** |
| 数据大屏 | http://127.0.0.1:5100/screen | 免登录,本机部署场景 |
| 接口文档 | http://127.0.0.1:5100/docs | 免登录,可直接发给调用方 |

数据与密钥存于 `data/` 目录(`gateway.db`、`.secret_key`),首次启动自动创建;老版本数据库启动时自动迁移新增列,无需删库。

### 配置说明([config.py](config.py))

| 配置 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_ADMIN_PASSWORD` | `admin123` | 首次启动初始化的管理员密码,登录后可在控制台修改 |
| `DEFAULT_TIMEOUT` | 120 | 渠道请求默认超时(秒),可被渠道级 `timeout` 覆盖,控制台可改 |
| `DEFAULT_RETRY` | 3 | 单次请求最大尝试渠道数,控制台可改 |
| `BREAKER_THRESHOLD` / `BREAKER_COOLDOWN` | 5 / 60 | 熔断阈值与冷却秒数,控制台可改 |

---

## 使用流程

### 管理员:配置渠道与 Key

1. 登录控制台 → **渠道管理** → 新增渠道:
   - 选预设厂商(自动填充 base_url / 适配器)→ 填 API Key(多个用英文逗号分隔自动轮询)
   - 点击 **⟳ 自动获取** 从上游拉取模型清单并勾选(不消耗 token,顺带带入元数据与参考价)
   - 需要外网访问时填**专属代理**:`http://127.0.0.1:7890` 或 `socks5://user:pass@host:1080`
   - 设置优先级(大者优先)与权重(同层分流)
   - 保存后点**探测**(免费)验证连通;**深度测试**(真实补全,消耗极少 token)验证推理链路
2. **模型单价**:确认 / 修改参考价,补充上下文窗口、最大输出;未配置的模型按 0 元计费
3. **API Key**:签发对外 Key,设置令牌额度 / 模型白名单 / 过期时间
4. **系统设置**:按需调整超时、重试、熔断、探测间隔、**auto 模型偏好**(如 `deepseek-chat, gpt-4o-mini`)

### 调用方:接入

任意 OpenAI SDK,把 `base_url` 指向网关、`api_key` 填网关签发的 Key 即可,所有已配置模型(含 Claude / Gemini 原生协议模型)统一访问:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:5100/v1", api_key="sk-gw-xxx")

# 显式指定模型
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "你好"}])

# auto:由网关自动选择模型
resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}])

# 流式
stream = client.chat.completions.create(
    model="claude-sonnet-4-5",      # 协议转换自动完成
    messages=[{"role": "user", "content": "写一首诗"}],
    stream=True)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

curl 流式示例:

```bash
curl -N http://127.0.0.1:5100/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"讲个笑话"}],"stream":true}'
```

更多示例(含 JavaScript fetch 流式读取)见 `/docs` 页面。

### 错误码

| 状态码 | 含义 |
|---|---|
| 401 | API Key 缺失 / 无效 / 已过期 |
| 403 | Key 无权访问该模型(白名单限制;auto 模式按实际命中模型校验) |
| 429 | Key 令牌额度已耗尽 |
| 404 | 没有可用渠道支持该模型(auto 无可用候选时同样返回) |
| 502 | 所有候选渠道尝试失败,明细见控制台用量日志 |

---

## 项目结构

```
AIGateway/
├── app.py                  入口:create_app、SQLite 迁移、探测调度器启动、waitress/--dev
├── config.py               全局配置(密钥、默认参数)
├── requirements.txt
├── gateway/                核心逻辑
│   ├── db.py               SQLAlchemy 实例
│   ├── models.py           ORM:Channel / ApiKey / ModelPrice / UsageLog / Setting / Admin
│   ├── presets.py          预设厂商库(base_url + 适配器 + 参考价)
│   ├── adapters/           协议转换
│   │   ├── base.py         适配器基类(模型列表端点、元数据解析、缓存 token)
│   │   ├── openai_compat.py  OpenAI 兼容直通(含 usage 抓取)
│   │   ├── anthropic.py    Claude 原生协议 ↔ OpenAI
│   │   ├── gemini.py       Gemini 原生协议 ↔ OpenAI
│   │   ├── azure.py        Azure OpenAI 部署名映射
│   │   └── registry.py     适配器注册表
│   ├── balancer.py         优先级分层加权选路 + 熔断器(关闭/打开/半开)
│   ├── relay.py            转发核心:故障转移、auto 路由、流式透传、计费落库
│   ├── probe.py            渠道健康探测(L1 免费 + 定时调度)
│   ├── model_meta.py       模型元数据知识库(上下文/最大输出/参考价)
│   ├── proxy.py            渠道专属 httpx Client(每渠道独立代理)
│   ├── pricing.py          单价缓存与计费
│   ├── quota.py            额度预检/扣减、用量落库
│   ├── stats.py            统计聚合(总览/热点/热力/每日)
│   └── events.py           实时事件总线(SSE)
├── routes/
│   ├── v1_api.py           对外 /v1/*(统一 OpenAI 兼容)
│   ├── admin_api.py        管理 /admin/api/*(渠道/Key/单价/探测/统计/设置)
│   └── screen_api.py       大屏 SSE 事件流
├── web/
│   ├── templates/          login / console / screen / docs
│   └── static/             css(theme/console/screen/docs) + js + lib/echarts
└── tests/
    ├── mock_upstream.py    本地 mock 上游(联调用)
    └── test_adapters.py    协议转换/缓存token/auto路由单元测试(42 项)
```

## 管理 API 一览(`/admin/api/*`,登录 session 鉴权)

| 分组 | 端点 |
|---|---|
| 登录 | `POST /login`、`POST /logout`、`GET /me`、`POST /password` |
| 渠道 | `GET/POST /channels`、`PUT/DELETE /channels/<id>`、`POST /channels/<id>/probe`、`POST /channels/<id>/test`、`POST /channels/<id>/reset_breaker`、`POST /channels/fetch_models`(自动获取模型+元数据) |
| 探测 | `POST /probe/all`(全渠道 L1 免费探测) |
| API Key | `GET/POST /keys`、`PUT/DELETE /keys/<id>` |
| 单价 | `GET/POST /prices`(POST 支持 `only_missing` 批量填充)、`DELETE /prices/<model>`、`POST /prices/import_presets` |
| 统计 | `GET /stats/overview`、`/stats/trend`、`/stats/by_model|by_channel|by_key`、`/stats/logs`、`/stats/hot_models`、`/stats/hourly_heatmap`、`/stats/model_hour_heatmap`、`/stats/daily` |
| 设置 | `GET/POST /settings`(含 `auto_models` 偏好、`probe_interval`) |
| 大屏 | `GET /events`(SSE 实时事件:usage / probe) |

---

## 验证记录(开发过程实测)

- 非流式 / 流式转发、SSE 实时事件推送(httpx 订阅验证 usage 事件)✓
- 故障转移:高优先级渠道故障后自动切换低优先级渠道,明细记录重试次数 ✓
- 每渠道代理:配置代理后流量确认走代理(错误代理导致连接失败)✓
- 额度耗尽返回 429、模型白名单返回 403、计费金额与单价精确一致 ✓
- 自动获取模型:临时 / 已保存渠道均可拉取模型清单与元数据;错误地址正确报错 ✓
- 元数据合并:上游返回(OpenRouter 价格换算 美元/token→美元/百万token)与知识库兜底(前缀匹配、大小写不敏感)均正确;`only_missing` 不覆盖已有价格 ✓
- 定时探测:渠道代理损坏后,调度器下一轮自动标记离线并记录时间戳 ✓
- 热力图:周x24 时段热力矩阵数据正确,三处 ECharts heatmap 渲染 ✓
- auto 路由:`/v1/models` 含 auto;偏好命中、白名单回退、渠道全故障自动降级 ✓
- 缓存 token:三家协议字段解析正确;流式与非流式均准确落库并展示 ✓
- 每日统计:按天聚合 tokens(入/出/缓存)、总调用、模型数、费用;按天x模型分布正确 ✓
- 单元测试 42/42 通过(`tests/test_adapters.py`)✓

## 说明与注意事项

- 预设单价与元数据知识库仅为参考(官网价格折算),请自行核对,控制台均可修改
- 大屏与 `/docs` 默认免登录(本机部署场景);对外暴露端口时建议加反向代理鉴权
- 单管理员模式;管理员密码在系统设置中修改
- 探测设计为"零额度消耗":定时任务只调模型列表端点;若某厂商连该端点都计费(极罕见),可对该渠道关闭定时探测,依赖转发时的熔断机制兜底

## 技术栈

Flask 3 · Flask-SQLAlchemy(SQLite WAL)· httpx[socks] · waitress · 原生 JavaScript · ECharts 5(本地化)
