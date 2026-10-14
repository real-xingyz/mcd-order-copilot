# MCP 集成说明

本项目实际使用的麦当劳 MCP Server、工具清单、调用流程与业务价值。

## Server

| 项 | 值 |
|---|---|
| 名称 | `mcd-mcp` v1.0.0 |
| 接入地址 | `https://mcp.mcd.cn` |
| 协议 | Streamable HTTP |
| 认证 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | 每个 Token 每分钟 600 次请求，超出返回 429 |
| Token 申请 | <https://open.mcd.cn/mcp> 控制台「激活」 |

> 服务端当前共提供 35 个工具，本项目实际用到其中 11 个。

## 使用的工具

| 工具 | 在本项目中的用途 |
|---|---|
| `auto-bind-coupons` | 一键领取当前可领的麦麦省优惠券 |
| `query-my-coupons` | 查看账户已有优惠券 |
| `query-my-account` | 查询麦享会积分账户 |
| `now-time-info` | 获取服务端当前时间，用于过滤已过期优惠券 |
| `delivery-query-addresses` | 查询配送地址（外送场景前置） |
| `query-nearby-stores` | 按城市+关键词搜索到店 / 得来速门店 |
| `delivery-query-stores` | 按地址查询可配送门店（麦乐送场景） |
| `query-meals` | 拉取门店菜单分类与商品编码 |
| `query-meal-detail` | 查询商品详情，**券商品的 `rounds` 字段用于拆解内容构成** |
| `query-store-coupons` | 查询指定门店 + 订单类型下可用的优惠券 |
| `calculate-price` | 逐个方案试算，返回实付、原价、优惠与 `takeWayList` |
| `create-order` | 真正下单（仅在用户显式确认后调用） |

未使用但值得开发者关注的工具：`list-nutrition-foods`（营养成分）、`campaign-calendar`（活动日历）、`query-lottery-info` / `draw-lottery`（积分抽奖）、`mall-*`（积分商城）、`query-party-*`（派对/品鉴会预约）。

---

## 调用流程

### 主流程：从领券到比价

```
用户："附近有什么麦当劳优惠，买一送一划算吗？"
   │
   ├─ ① auto-bind-coupons                 一键领券
   │      └→ 拿到 couponId / couponCode
   │
   ├─ ② query-nearby-stores               定位门店
   │      searchType=2, city=<城市>, keyword=<关键词>   ⚠️ 两个都必填
   │      └→ storeCode（beType=1 时无 beCode）
   │
   ├─ ③ query-store-coupons               该门店当前可用券
   │      └→ 每张券含 products[].productCode 与 tradeDateTime
   │
   ├─ ④ now-time-info                     取服务端时间，过滤过期券
   │
   ├─ ⑤ ┌── worth 路径（算划不划算）─────────────────────┐
   │     │  query-meal-detail(券商品)                    │
   │     │    └→ rounds[].choices[] 拆出「中薯条 ×2」    │
   │     │  calculate-price(券商品 + 券码) → 券后价       │
   │     │  calculate-price(单品 ×1)     → 市场单价       │
   │     │  └→ 折算单价、实际折扣、省了多少               │
   │     └───────────────────────────────────────────────┘
   │
   └─ ⑥ ┌── optimize 路径（怎么点最省）──────────────────┐
         │  for each 方案 in [不用券] + [购物车 + 单张券]: │
         │      calculate-price(方案) → 实付               │
         │  └→ 按实付升序输出全部方案                      │
         └───────────────────────────────────────────────┘

用户确认后：
   └─ ⑦ create-order                      仅在显式确认后调用
```

---

## 场景参数传递规则

这是本项目封装得最重的一块，四种场景的规则各不相同：

| 场景 | beType | orderType | beCode | takeWayCode | addressId |
|---|---|---|---|---|---|
| 到店自取 | 1 | 1 | 不传 | 必传 | — |
| 得来速车道取餐 | 5 | 1 | **必传** | 必传 | — |
| 麦乐送到家 | 2 | 2 | **必传** | 不传 | 外送必传 |
| 企业团餐 | 6 | 2 | **必传** | 不传 | 视情况 |

---

## 实测确认的服务端约束

以下结论均由真实调用验证，不是猜测：

1. **单笔订单仅支持 1 张券。**
   同时传 2 张券的 `couponCode` 会返回 `暂不支持多张券使用`。
   因此 `optimize()` 枚举的是「0 张券」与「加 1 张券」的组合，而非 2^n 全组合。

2. **券专属商品必须与券码绑定。**
   形如「中薯条买一送一」「王牌小食拼」的券商品，单独放进 `items` 会被服务端
   静默丢弃——`calculate-price` 返回 `price: 0` 且 `productList` 为空。
   正确写法：

   ```json
   {
     "productCode": "9900012387",
     "quantity": 1,
     "couponCode": "MCD6S94J1B00H071JH001",
     "couponId": "8692944B045928451618BFF22F535D2A"
   }
   ```

3. **`query-nearby-stores` 搜店需 city 与 keyword 同时提供。**
   仅传 `city` 时报 `城市名或者关键词不能为空`，该报错文案会让人误判为参数名错误。

4. **商品编码与场景绑定。**
   同一商品在到店菜单与麦乐送菜单中的 `code` 不同；跨场景复用会报
   `商品已售罄或不可售，换一个商品或检查orderType/beType`。

5. **金额单位为分。** `price` / `originalPrice` / `discount` / `subtotal` 均为分。

6. **饮品单独试算返回 0 元。** 属于正常现象（需搭配主食才有价），做折扣对比时需标注。

7. **领券幂等。** 券全部领完时 `auto-bind-coupons` 返回
   `暂无可领取的优惠券`，本项目将其作为「空结果」软处理而非错误。

---

## 业务价值

| 环节 | 人工要做的事 | 本项目做的事 |
|---|---|---|
| 领券 | 打开小程序翻活动页 | 一条命令领完全部可领券 |
| 选店 | 搜地图比距离 | 按城市+关键词列出可点餐门店及营业时间 |
| 判断券值不值 | 只看到「省 13.5 元」，不知道单份多少钱 | 拆开 `rounds` 算出折算单价与真实折扣 |
| 比价 | 一单一单手动试 | 枚举全部单券组合并按实付排序 |
| 下单 | 手填一堆字段 | 按场景自动拼装参数，但**必须用户确认后才执行** |

其中「判断券值不值」是本项目认为最有价值的一环：官方工具返回的是优惠金额，
而用户真正想知道的是「买一送一等于几折」。本项目通过 `query-meal-detail` 的
`rounds` 字段拿到券商品的真实构成，再逐项查市场单价，最终给出折算后的每份价格。

---

## 错误处理

| 情况 | 处理方式 |
|---|---|
| HTTP 401 | Token 无效或过期，提示重新到控制台激活 |
| HTTP 429 | 触发限流（600 次/分钟），提示降低频率 |
| `暂无可领取的优惠券` | 视为空结果，返回 0 张 |
| `收藏餐厅列表为空` | 视为空结果，提示改用 city+keyword 搜索 |
| `暂不支持多张券使用` | 优化器在枚举阶段即规避，不会触发 |
| 缺 `beCode` / `takeWayCode` | 本地校验直接报错，不发起请求 |
