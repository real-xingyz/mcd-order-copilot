# WorkBuddy 联动开发记录

本项目使用腾讯 WorkBuddy 完成开发全过程记录，用于麦当劳程序员创意开发大赛
「WorkBuddy 专项奖励」的核验。

## 一、开发过程真实对话轨迹

### 阶段一：需求确认

**用户**：提供一个微信活动链接，要求参赛。

**WorkBuddy**：
1. 抓取文章正文，识别出活动为「麦当劳程序员创意开发大赛（1024 程序员节）」
2. 读取 GitHub 官方仓库 `M-China/mcd-developer-innovation-challenge` 的 README 与
   `activityGuidelines.md`，梳理出报名条件、必交文件、排名规则与奖品
3. 读取 `M-China/mcd-mcp-server` 的 MCP 集成指南与 35 个工具清单
4. 核实报名入口：README 中给出的 `M-China-Official` 组织实为死链，
   真实入口是 `M-China/mcd-developer-innovation-challenge`
5. 拉取实时榜单（RANKING.md），给出「稳前 100、冲前三不现实」的量化判断
6. 与用户确认 Skill 方向 → 用户选定「麦麦点单副驾」

### 阶段二：连通与实测

**用户提供 MCP Token**，WorkBuddy 随即展开真实调用验证：

1. 用标准库实现 MCP Streamable HTTP + JSON-RPC 2.0 握手
   （`scripts/mcd_client.py`），连接成功：`mcd-mcp v1.0.0`，共 35 个工具
2. 逐个导出关键工具的 `inputSchema`，摸清 `beType` / `orderType` / `beCode` 的传递规则
3. 真实调用 `delivery-query-addresses`、`query-my-account`、`auto-bind-coupons`
   （成功领取 3 张券）、`query-my-coupons`、`query-nearby-stores`、
   `query-meals`、`query-meal-detail`、`query-store-coupons`、`calculate-price`

### 阶段三：关键实验（本项目价值的来源）

WorkBuddy 设计了一组对照实验，用真实数据而非文档推测来确定服务端行为：

| 实验 | 操作 | 结论 |
|---|---|---|
| 多券叠加 | 一次传 2 张 / 3 张券的 `couponCode` | 服务端返回「暂不支持多张券使用」→ 优化器只能枚举 0/1 张券 |
| 券商品绑定 | 把「中薯条买一送一」单独放入 items | 价格不变、清单为空 → **被静默忽略**，必须与券码绑定传入 |
| 搜店参数 | 只传 `city` vs 同时传 `city`+`keyword` | 只传 city 报「城市名或者关键词不能为空」，报错文案有误导性 |
| 场景编码 | 到店 code 用于外送场景 | 报「商品已售罄或不可售」→ 商品编码与场景绑定 |
| 券商品构成 | `query-meal-detail` 查券商品的 `rounds` | 成功拿到「中薯条 ×2」构成 → **折算单价功能得以实现** |
| 单品市价 | 对 `rounds` 里的内层 code 单独 `calculate-price` | 中薯条 ¥13.50 → 买一送一 ¥13.50 = **5 折** |

### 阶段四：实现与回归

实现 `scripts/copilot.py`（核心逻辑 + CLI），并在开发过程中持续修复实测暴露的问题：

1. 券全部领完时 `auto-bind-coupons` 报「暂无可领取的优惠券」→ 加软处理，返回空列表而非异常
2. `--scene` 只能置于子命令之前 → 改用 parent parser，前置后置均可
3. 券有效期解析按 `-` 切分被日期本身的连字符打断 → 改用正则抓时间戳
4. 饮品单独试算返回 0 元导致「省了 -0.9 元」→ 排除 0 元项并标注「仅供参考」
5. 部分商品不返回 `rounds` 导致件数算 0 → 增加兜底
6. 「最优方案比次优省 ¥9.90」结论误导（加购商品必然抬高总价）→ 改为区分
   「不加购最便宜」与「加购最划算」两件事

最终 9 条 CLI 命令全部回归通过：

```
✅ grab-coupons   ✅ account    ✅ my-coupons
✅ stores         ✅ menu        ✅ coupons
✅ worth          ✅ optimize    ✅ order（试算模式）
```

## 二、WorkBuddy 在开发中承担的角色

| 环节 | WorkBuddy 的作用 |
|---|---|
| 规则调研 | 抓取并解析活动页面、GitHub 规则文档、MCP 集成指南 |
| 实测验证 | 编写 MCP 客户端并设计对照实验，用真实返回数据确定服务端行为 |
| 代码实现 | 编写 MCP 客户端与点单副驾核心逻辑 |
| 缺陷修复 | 根据实测结果迭代修复 6 处问题 |
| 文档撰写 | 编写 README、MCP_INTEGRATION、SKILL.md |
| MCP 接入 | 将 `mcd-mcp` 配置写入 WorkBuddy 连接器，使 Skill 可在 WorkBuddy 中直接对话使用 |

## 三、成果

- 11 个 MCP 工具的完整调用链路打通
- 7 条官方文档未写明的服务端约束被实测确认并写入文档
- 核心功能「券值不值」把优惠金额翻译为用户真正关心的折算单价
- 代码零第三方依赖，Python 3.8+ 开箱即跑

## 四、安全合规

- 全程未在代码或文档中写入真实 MCP Token，Token 仅通过环境变量传入
- 全程未调用 `create-order`，未产生任何真实订单与消费
- 唯一有副作用的调用是 `auto-bind-coupons`（领取免费优惠券）

---

*本文件如实记录使用腾讯 WorkBuddy 开发本项目的全过程。*
