---
name: mcd-order-copilot
description: 麦当劳点单副驾——基于麦当劳中国 MCP 的点单助手。当用户想点麦当劳、找附近门店、查可用优惠券、比较不同点法哪个更划算、或者问「买一送一到底等于几折」时使用。支持到店自取、得来速车道取餐、麦乐送到家、企业团餐四种场景，覆盖领券→选店→挑券→凑单比价→下单全流程。关键词：麦当劳、麦乐送、得来速、麦麦省、优惠券、点单、巨无霸、凑单、买一送一划算吗。
---

# 麦麦点单副驾

把「领券 → 选店 → 挑券 → 凑单比价 → 下单」串成一条链路，核心是回答一个多数人没算清的问题：

> **这一单到底怎么点最划算？**

## 何时使用

- 「附近有麦当劳吗」「我要点麦当劳」
- 「我有几张券」「有什么优惠能领」
- 「买一送一划算吗」（券商品折算单价）
- 「我这些东西怎么点最便宜」（凑单比价）
- 「帮我下单」（需用户明确确认后才执行）

## 前置条件

1. 已申请麦当劳 MCP Token（<https://open.mcd.cn/mcp> 控制台激活）
2. Token 读取优先级：显式传入 > 环境变量 `MCD_MCP_TOKEN` > 本机 `~/.workbuddy/mcp.json`
   （已在 WorkBuddy 配置过 `mcd-mcp` 自定义连接器的话，脚本会自动读取，无需设置）
3. Python 3.8+（仅用标准库，无需 pip install）

## 工作流程

### 第 0 步：确认场景

先问清楚是哪种场景，四种场景的参数传递规则完全不同（见下方铁律）：

| 场景参数 | 场景含义 | beType | orderType | beCode | 必填 |
|---|---|---|---|---|---|
| `pickup` | 到店自取 | 1 | 1 | 不传 | — |
| `drive` | 得来速车道取餐 | 5 | 1 | **必传** | `takeWayCode` |
| `delivery` | 麦乐送到家 | 2 | 2 | **必传** | `addressId`（外送时） |
| `group` | 企业团餐 | 6 | 2 | **必传** | `gmServiceCode` |

### 第 1 步：一键领券

```bash
python scripts/copilot.py grab-coupons
```

调用 `auto-bind-coupons` 把当前可领的券全部收进账户。已领完时返回「暂无可领取的优惠券」，属正常情况，脚本已做软处理。

### 第 2 步：定位门店

```bash
# 到店 / 得来速：city 与 keyword 都必须传
python scripts/copilot.py stores --scene pickup --city 北京 --keyword 麦当劳

# 麦乐送：自动取账户第一个地址
python scripts/copilot.py stores --scene delivery
```

记下返回的 `storeCode` 与 `beCode`（到店自取场景无 `beCode`，这是正常的）。

### 第 3 步：看券值不值（推荐先做这步）

```bash
python scripts/copilot.py worth --store 1950970 --scene pickup
```

输出形如：

```
【中薯条买一送一】中薯条买一送一
   券后价   : ¥13.50（2 件）
     · 中薯条 ¥13.50 ×2
   市场等价 : ¥27.00  → 折算单价 ¥6.75（5.0 折）
   等于省了 : ¥13.50
   有效期   : 2026-10-12 10:30:00 至 2026-10-16 23:59:59
```

这是本 Skill 最有价值的输出：**买一送一不是「省 13.5」，而是「单份半价」**。

### 第 4 步：凑单比价

```bash
python scripts/copilot.py optimize --store 1950970 --scene pickup --items 9900004064:1,1440:1
```

枚举「不用券」与「加一张券商品」的所有组合，逐个调 `calculate-price` 试算，按实付升序排列。

### 第 5 步：下单

```bash
# 先试算（不产生订单）
python scripts/copilot.py order --scene pickup --store 1950970 --items 9900004064:1 --take-way eat-in

# 用户明确确认后才真正下单
python scripts/copilot.py order --scene pickup --store 1950970 --items 9900004064:1 --take-way eat-in --confirm
```

**铁律：不加 `--confirm` 绝不调用 `create-order`。** 必须把试算结果念给用户听，得到明确同意后再加 `--confirm`。

## 踩坑铁律（实测得出，官方文档未写明）

1. **一笔订单只能用 1 张券。** 传 2 张以上服务端直接返回「暂不支持多张券使用」。所以枚举的是 0/1 张券的组合，不要尝试 2^n 组合。

2. **券专属商品必须与券码绑定传入。** 「中薯条买一送一」这类商品单独加入购物车会被服务端**静默忽略**（价格不变、清单为空），必须写成：
   ```json
   {"productCode": "9900012387", "quantity": 1,
    "couponCode": "...", "couponId": "..."}
   ```

3. **`query-nearby-stores` 的 `searchType=2` 必须同时传 `city` 和 `keyword`。** 只传 `city` 会报「城市名或者关键词不能为空」——这个报错信息有误导性，容易让人以为是参数名写错了。

4. **商品编码是场景相关的。** 同一个汉堡，到店菜单和麦乐送菜单的 `code` 不一样。跨场景直接复用 code 会报「商品已售罄或不可售」。

5. **金额单位是分。** `calculate-price` 返回的 `price`/`originalPrice`/`discount` 都是分，输出给用户前要除以 100。

6. **到店/得来速下单必须传 `takeWayCode`**，取值只能是 `calculate-price` 返回的 `takeWayList[].code`（`eat-in` 堂食 / `take-in-store` 外带）。

7. **饮品单独试算常返回 0 元**，属正常。做市场等价对比时要标注「仅供参考」，不要因此得出错误折扣结论。

## 目标用户

- 想吃麦当劳但懒得翻菜单、比券、比价的人
- 经常点麦乐送、熟悉门店优惠但懒得算账的上班族
- 想了解 MCP 能力边界与真实调用链路的开发者

## 免责声明

本项目为个人参赛作品，非麦当劳官方产品。餐品信息、价格与供应状态以麦当劳官方渠道实时结果为准。
