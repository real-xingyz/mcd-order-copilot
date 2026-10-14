# 麦麦点单副驾 · mcd-order-copilot

> 「这一单到底怎么点最划算？」

一个基于**麦当劳中国 MCP Server** 的点单助手。它把领券、选店、挑券、比价、下单串成一条链路，并且认真回答一个多数人从没算清楚的问题：

**「中薯条买一送一」到底是省了 13.5 块，还是等于薯条半价？**

答案往往是后者，而这就是本项目存在的理由。

```
【中薯条买一送一】中薯条买一送一
   券后价   : ¥13.50（2 件）
     · 中薯条 ¥13.50 ×2
   市场等价 : ¥27.00  → 折算单价 ¥6.75（5.0 折）
   等于省了 : ¥13.50
   有效期   : 2026-10-12 10:30:00 至 2026-10-16 23:59:59
```

---

## 它解决什么问题

麦当劳 MCP 已经提供了菜单查询、优惠券、算价、下单等 35 个工具。但直接调用工具，人还是要自己比来比去。本项目在工具之上补了三件事：

| 能力 | 说明 |
|---|---|
| **券值不值** | 拆开券商品的 `rounds`，算出折算单价与实际折扣，而不是只告诉你「省了多少钱」 |
| **凑单比价** | 枚举「购物车 + 单张券」的所有组合，逐个真实试算，按实付升序给出方案 |
| **参数不出错** | 四种场景（到店/得来速/外送/团餐）的 `beType`/`orderType`/`beCode` 传递规则极易踩坑，本项目把它封装成 `--scene` |

---

## 安装

**依赖：Python 3.8+，仅标准库，不需要 `pip install` 任何东西。**

```bash
git clone https://github.com/<你的用户名>/mcd-order-copilot.git
cd mcd-order-copilot
```

### 配置 MCP Token

1. 打开 <https://open.mcd.cn/mcp>，用手机号登录
2. 点击右上角「控制台」→「激活」，复制 MCP Token
3. 提供给程序，二选一：

**方式 A（推荐，零配置）** — 如果你已经在 WorkBuddy / CodeBuddy 里配置过 `mcd-mcp`
自定义连接器，脚本会自动从 `~/.workbuddy/mcp.json` 读取 Token，无需任何额外设置。

**方式 B** — 设置环境变量：

```bash
# Windows PowerShell
$env:MCD_MCP_TOKEN = "你的Token"

# macOS / Linux
export MCD_MCP_TOKEN="你的Token"
```

Token 读取优先级：显式传入 > 环境变量 `MCD_MCP_TOKEN` > 本机 `~/.workbuddy/mcp.json`

> 也可以把 `mcp-config.example.json` 里的 `${MCD_MCP_TOKEN}` 替换成实际 Token，
> 导入 Cherry Studio / Cursor / WorkBuddy 等 MCP Client。
> **请勿把真实 Token 提交到仓库**（`.gitignore` 已排除 `.env` 与 `mcp-config.json`）。

---

## 使用示例

### 1. 一键领券

```bash
python scripts/copilot.py grab-coupons
```
```
✅ 新领到 3 张券：
   - 中薯条买一送一 | code: MCD6S94J1B00H071JH001
   - 王牌小食拼   | code: MCD69H610J00006HS51JC
   - 9.9元中杯冰美式 | code: MCD608H96140JJS0J001H
```

### 2. 找附近门店

```bash
python scripts/copilot.py stores --scene pickup --city 北京 --keyword 麦当劳
```
```
共 5 家门店：
   storeCode=1950970 beCode=- | 麦当劳北京中粮广场餐厅 | 建国门内大街8号 | 营业 07:00-22:00
   storeCode=1951029 beCode=- | 麦当劳北京北京站北广场餐厅 | 北京火车站北广场 | 营业 07:00-21:00
   ...
```

### 3. 算券值不值 ⭐

```bash
python scripts/copilot.py worth --store 1950970 --scene pickup
```

### 4. 凑单比价

```bash
python scripts/copilot.py optimize --store 1950970 --scene pickup --items 9900004064:1,1440:1
```
```
共试算 4 个方案，按实付升序：

1. 不用券                                实付 ¥57.00   原价 ¥57.00   省 ¥0.00
2. 加「麦咖啡™冰美式中杯」用券「9.9元中杯冰美式」   实付 ¥66.90   原价 ¥79.90   省 ¥13.00
3. 加「中薯条买一送一」用券「中薯条买一送一」       实付 ¥70.50   原价 ¥84.00   省 ¥13.50
4. 加「王牌小食拼」用券「王牌小食拼」           实付 ¥72.90   原价 ¥79.00   省 ¥6.10

💡 最低实付：不用券（¥57.00）
   若想顺便用券，中薯条买一送一 最划算：+¥13.50 得 中薯条买一送一
   （提示：加购商品会抬高总价，是否划算请配合 worth 命令看折算单价）
```

### 5. 下单

```bash
# 先试算，不产生订单
python scripts/copilot.py order --scene pickup --store 1950970 \
        --items 9900004064:1 --take-way eat-in

# 确认无误后再真正下单
python scripts/copilot.py order --scene pickup --store 1950970 \
        --items 9900004064:1 --take-way eat-in --confirm
```

> ⚠️ 不加 `--confirm` 绝不会调用 `create-order`。请务必先把试算结果给用户确认。

---

## 在 WorkBuddy 中使用

本项目也是一个标准 Agent Skill，把 `SKILL.md` 与 `scripts/` 放进你的技能目录即可：

```
~/.workbuddy/skills/mcd-order-copilot/
├── SKILL.md
└── scripts/
    ├── copilot.py
    └── mcd_client.py
```

之后直接对 WorkBuddy 说「附近有什么麦当劳优惠」「买一送一划算吗」即可。

---

## 实测踩坑记录

以下 7 条都是实际调用麦当劳 MCP 得到的结论，官方文档里没有写清楚：

| # | 结论 |
|---|---|
| 1 | **一笔订单只能用 1 张券**，传 2 张服务端直接拒：「暂不支持多张券使用」 |
| 2 | **券专属商品必须与券码绑定传入**，单独加入会被静默忽略（价格不变、清单为空） |
| 3 | `query-nearby-stores` 的 `searchType=2` **必须同时传 `city` 和 `keyword`**，只传 city 会报「城市名或者关键词不能为空」（报错信息有误导性） |
| 4 | **商品编码是场景相关的**，同一商品到店与外送的 code 不同，跨场景复用会报「商品已售罄或不可售」 |
| 5 | **金额单位是分**，返回的 `price`/`discount` 需要除以 100 |
| 6 | 到店/得来速下单**必须传 `takeWayCode`**，取值只能是 `eat-in` 或 `take-in-store` |
| 7 | **饮品单独试算常返回 0 元**，做折扣对比时需标注「仅供参考」 |

---

## 项目结构

```
mcd-order-copilot/
├── SKILL.md                    # Agent Skill 定义
├── scripts/
│   ├── mcd_client.py           # MCP Streamable HTTP 客户端（仅标准库）
│   └── copilot.py              # 核心逻辑 + CLI
├── mcp-config.example.json     # 脱敏 MCP 配置
├── MCP_INTEGRATION.md          # 用了哪些 Tool、调用流程、业务价值
├── CONTEST_DECLARATION.md      # 参赛声明
└── README.md
```

## 常见问题

### Q: 在 WorkBuddy 里配好 MCP 后，连接器列表里找不到 `mcd-mcp`？

**A: 两个可能原因。**

1. **WorkBuddy 只在启动时读取 `mcp.json`。** 如果你是在 WorkBuddy 运行期间修改的
   该文件，列表不会自动刷新，需要**重启 WorkBuddy**。
   （可观察 `~/.workbuddy/mcp-tool-list.json` 的修改时间，若早于你改配置的時間，说明尚未重新加载。）

2. **`type` 字段大小写不匹配。** 麦当劳官方文档示例写的是 `"streamablehttp"`（全小写），
   但部分 WorkBuddy / CodeBuddy 版本只认驼峰写法 `"streamableHttp"`，
   写成小写会导致该条目**不出现或加载失败**。

   稳妥做法：与同一份 `mcp.json` 里**已在正常工作**的条目保持一致。
   例如若你的配置里已有可用的 streamable HTTP 类型服务，直接照抄它的 `type` 值。

   ```jsonc
   // 两种写法在不同版本上表现不一致，请以本机实测为准
   "type": "streamableHttp"   // WorkBuddy 2026-10 版本实测可用
   "type": "streamablehttp"   // 麦当劳官方文档示例写法
   ```

### Q: 脚本报 401 / 429？

- **401**：Token 无效或过期，回 <https://open.mcd.cn/mcp> 控制台重新「激活」。
- **429**：触发限流（每个 Token 每分钟 600 次），降低调用频率再试。

---

## 目标用户

- 懒得翻菜单比券比价的普通用户
- 经常点麦乐送、知道有优惠但懒得算账的上班族
- 想搞清 MCP 真实调用链路的开发者

## 免责声明

本项目为个人参赛作品，非麦当劳官方产品，不构成任何商业合作。餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。

## 许可证

MIT
