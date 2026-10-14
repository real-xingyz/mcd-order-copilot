#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copilot.py — 麦麦点单副驾核心逻辑

把「领券 → 选店 → 挑券 → 凑单比价 → 下单」串成一条可复用的链路，
核心价值在 `optimize()`：枚举「购物车 + 单张券」的所有组合，
用官方 calculate-price 逐个试算，找出实付最低方案。

实测得到的两条硬约束（官方文档未写明，来自 2026-10 实测）：
  1. 一笔订单只能用 1 张券，服务端对多券返回「暂不支持多张券使用」。
  2. 券专属商品必须与 couponCode/couponId 一起传入，
     单独加入该商品会被服务端静默忽略（价格不变）。

用法：
  python copilot.py --help
  python copilot.py grab-coupons
  python copilot.py stores --scene pickup --city 北京 --keyword 麦当劳
  python copilot.py menu --store 1950970 --scene pickup
  python copilot.py optimize --store 1950970 --scene pickup --items 9900004064:1
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcd_client import McdMcpClient, McdMcpError  # noqa: E402

# ---------------------------------------------------------------- 场景定义
# beType/orderType/beCode 的传递规则是本项目踩坑最多的地方，集中定义避免散落
SCENES = {
    "pickup": {
        "beType": 1, "orderType": 1, "needBeCode": False,
        "label": "到店自取", "storeTool": "query-nearby-stores",
    },
    "drive": {
        "beType": 5, "orderType": 1, "needBeCode": True,
        "label": "得来速车道取餐", "storeTool": "query-nearby-stores",
    },
    "delivery": {
        "beType": 2, "orderType": 2, "needBeCode": True,
        "label": "麦乐送到家", "storeTool": "delivery-query-stores",
    },
    "group": {
        "beType": 6, "orderType": 2, "needBeCode": True,
        "label": "企业团餐", "storeTool": "delivery-query-stores",
    },
}


def yuan(fen):
    """分 → 元"""
    return "¥%.2f" % (float(fen or 0) / 100)


def parse_items(text):
    """'9900004064:1,1440:2' → [{'productCode':'9900004064','quantity':1}, ...]"""
    items = []
    for seg in (text or "").split(","):
        seg = seg.strip()
        if not seg:
            continue
        if ":" in seg:
            code, qty = seg.rsplit(":", 1)
            items.append({"productCode": code.strip(), "quantity": int(qty)})
        else:
            items.append({"productCode": seg, "quantity": 1})
    return items


class Copilot:
    def __init__(self, client=None, verbose=False):
        self.c = client or McdMcpClient()
        self.c.initialize()
        self.verbose = verbose

    # -------------------------------------------------- 内部：场景参数拼装
    def _scene_args(self, scene_key, store_code, be_code=None, reservation_date=None):
        sc = SCENES[scene_key]
        args = {
            "storeCode": str(store_code),
            "orderType": sc["orderType"],
            "beType": sc["beType"],
        }
        # beCode 只在 得来速/外送/团餐 场景需要
        if sc["needBeCode"]:
            if not be_code:
                raise McdMcpError(
                    "%s 场景必须提供 beCode（来自门店查询结果）" % sc["label"]
                )
            args["beCode"] = be_code
        if reservation_date:
            args["reservationDate"] = reservation_date
        return args

    # 业务上视为「空结果」而非失败的提示语（例：券已全部领完）
    SOFT_EMPTY = ("暂无可领取的优惠券", "收藏餐厅列表为空", "暂无可用优惠券")

    def _data(self, tool, args, soft_empty=False):
        r = self.c.call_tool(tool, args)
        if not isinstance(r, dict) or not r.get("success", True):
            msg = (r or {}).get("message", "未知错误") if isinstance(r, dict) else str(r)
            if soft_empty and any(s in msg for s in self.SOFT_EMPTY):
                return {}
            raise McdMcpError("%s 失败：%s" % (tool, msg))
        return r.get("data")

    # -------------------------------------------------- 1. 领券
    def grab_coupons(self):
        """一键领券：把当前可领的麦麦省券全部收进账户。已领完时返回空列表。"""
        data = self._data("auto-bind-coupons", {}, soft_empty=True)
        got = data.get("successCoupons", []) if isinstance(data, dict) else []
        return got

    def my_coupons(self, page=1, page_size=20):
        return self._data("query-my-coupons", {"page": page, "pageSize": page_size})

    def account(self):
        return self._data("query-my-account", {})

    def server_now(self):
        d = self._data("now-time-info", {})
        return d.get("datetime") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------- 2. 选店
    def addresses(self):
        d = self._data("delivery-query-addresses", {})
        return (d or {}).get("addresses", [])

    def find_stores(self, scene_key, city=None, keyword="麦当劳", address_id=None):
        """
        到店/得来速：query-nearby-stores（searchType=2 时 city 与 keyword 都必填，
        只传 city 会被服务端判为「城市名或者关键词不能为空」）
        外送/团餐：delivery-query-stores（依赖 addressId）
        """
        sc = SCENES[scene_key]
        if sc["storeTool"] == "query-nearby-stores":
            if not (city and keyword):
                raise McdMcpError("按位置搜店需要同时提供 city 与 keyword")
            args = {"beType": sc["beType"], "searchType": 2, "city": city, "keyword": keyword}
        else:
            if not address_id:
                addrs = self.addresses()
                if not addrs:
                    raise McdMcpError("外送场景需要配送地址，请先 delivery-create-address")
                address_id = addrs[0].get("addressId")
            args = {"beType": sc["beType"], "addressId": address_id}

        data = self._data(sc["storeTool"], args)
        return data if isinstance(data, list) else (data or {}).get("stores", [])

    # -------------------------------------------------- 3. 菜单
    def menu(self, store_code, scene_key, be_code=None):
        data = self._data(
            "query-meals", self._scene_args(scene_key, store_code, be_code)
        )
        cats = []
        for cat in (data or {}).get("categories", []):
            for meal in cat.get("meals", []):
                cats.append((cat.get("name", "").replace("\n", ""), meal.get("code"), meal.get("tags", [])))
        return cats

    def meal_detail(self, store_code, scene_key, code, be_code=None):
        return self._data(
            "query-meal-detail",
            self._scene_args(scene_key, store_code, be_code, )
            | {"code": code},
        )

    # -------------------------------------------------- 4. 券（门店维度）
    def store_coupons(self, store_code, scene_key, be_code=None, only_valid=True):
        data = self._data(
            "query-store-coupons", self._scene_args(scene_key, store_code, be_code)
        )
        coupons = data if isinstance(data, list) else (data or {}).get("coupons", [])
        if not only_valid:
            return coupons
        now = self.server_now()
        valid = []
        for cp in coupons:
            window = cp.get("tradeDateTime") or ""
            if " - " in window or "至" in window:
                lo, hi = re_split_window(window)
                if lo <= now <= hi:
                    valid.append(cp)
            else:
                valid.append(cp)
        return valid

    # -------------------------------------------------- 5. 算价
    def price(self, store_code, scene_key, items, be_code=None, need_tableware=None):
        args = self._scene_args(scene_key, store_code, be_code)
        args["items"] = items
        if need_tableware is not None:
            args["needTableware"] = need_tableware
        return self._data("calculate-price", args)

    # -------------------------------------------------- 6. 核心：凑单最优解
    def optimize(self, store_code, scene_key, base_items, be_code=None):
        """
        枚举「基础购物车」与「基础购物车 + 单张券」，逐个 calculate-price 试算，
        返回按实付升序排列的方案列表。

        服务端只支持单券，因此枚举的是 0/1 张券的组合（2^n 会全部被拒）。
        """
        coupons = self.store_coupons(store_code, scene_key, be_code)
        scenarios = [{"tag": "不用券", "items": list(base_items), "coupon": None}]

        for cp in coupons:
            products = cp.get("products") or []
            if not products:
                continue  # 无指定商品的券（如全场券）暂不参与自动凑单
            for p in products:
                pcode = p.get("productCode")
                if not pcode:
                    continue
                # 券商品必须与券码绑定传入，否则被服务端忽略
                items = list(base_items) + [{
                    "productCode": pcode,
                    "quantity": 1,
                    "couponCode": cp.get("couponCode"),
                    "couponId": cp.get("couponId"),
                }]
                scenarios.append({
                    "tag": "加「%s」用券「%s」" % (p.get("productName"), cp.get("title")),
                    "items": items,
                    "coupon": cp,
                })

        results = []
        for sc in scenarios:
            try:
                data = self.price(store_code, scene_key, sc["items"], be_code)
            except McdMcpError as e:
                results.append({**sc, "ok": False, "error": str(e)})
                continue
            results.append({
                "tag": sc["tag"],
                "ok": True,
                "items": sc["items"],
                "coupon": sc["coupon"],
                "price": int(data.get("price") or 0),
                "originalPrice": int(data.get("originalPrice") or 0),
                "discount": int(data.get("discount") or 0),
                "takeWayList": data.get("takeWayList", []),
                "productList": data.get("productList", []),
            })

        ok = [r for r in results if r.get("ok")]
        ok.sort(key=lambda r: r["price"])
        return ok, results

    # -------------------------------------------------- 6.5 值不值：券商品拆解
    # 券专属商品（如「中薯条买一送一」）只有带券码才会进入订单。
    # 但 query-meal-detail 会把它的 rounds 摊开告诉我们里面装了什么、
    # 各几份，于是可以精确算出「折算单价」并与单点价对比。
    def worth(self, store_code, scene_key, be_code=None):
        """列出该门店当前有效券商品，拆解内容并算出折算折扣。"""
        coupons = self.store_coupons(store_code, scene_key, be_code)
        rows = []
        for cp in coupons:
            products = cp.get("products") or []
            if not products:
                continue
            p = products[0]
            pcode, pname = p.get("productCode"), p.get("productName", "")
            if not pcode:
                continue

            # 1) 券价
            d = self.price(store_code, scene_key, [{
                "productCode": pcode, "quantity": 1,
                "couponCode": cp.get("couponCode"), "couponId": cp.get("couponId"),
            }], be_code)
            coupon_price = int(d.get("price") or 0)

            # 2) 拆解内容：rounds 里 quantity>0 的 choice
            try:
                detail = self._data("query-meal-detail",
                                    self._scene_args(scene_key, store_code, be_code) | {"code": pcode})
            except McdMcpError:
                detail = {}
            contents = []
            for rd in detail.get("rounds") or []:
                for ch in rd.get("choices") or []:
                    q = int(ch.get("quantity") or 0)
                    if q > 0 and ch.get("code"):
                        contents.append({"code": ch["code"], "name": ch.get("name", ""), "qty": q})

            # 3) 逐项查单点价，算出「按市场单价买齐」的总价
            #    注意：饮料等商品单独试算时可能返回 0 元（属正常），
            #    这类项不计入市场等价，并在结果里标注为「参考价不完整」。
            market_total = 0
            pieces = 0
            zero_priced = 0
            detail_rows = []
            for it in contents:
                try:
                    one = self.price(store_code, scene_key,
                                     [{"productCode": it["code"], "quantity": 1}], be_code)
                    unit = int(one.get("price") or 0)
                except McdMcpError:
                    unit = 0
                it = dict(it, unitPrice=unit)
                if unit > 0:
                    market_total += unit * it["qty"]
                else:
                    zero_priced += 1
                pieces += it["qty"]
                detail_rows.append(it)
            if not pieces:
                pieces = 1  # 少数商品不返回 rounds，按 1 件计

            per_piece_coupon = float(coupon_price) / pieces
            per_piece_market = float(market_total) / pieces
            rate = (per_piece_coupon / per_piece_market) if per_piece_market else None

            window = cp.get("tradeDateTime") or ""
            lo, hi = re_split_window(window)
            rows.append({
                "couponTitle": cp.get("title"),
                "productName": pname,
                "couponPrice": coupon_price,
                "pieces": pieces,
                "contents": detail_rows,
                "marketTotal": market_total,
                "partial": zero_priced > 0,
                "perPieceCoupon": per_piece_coupon,
                "perPieceMarket": per_piece_market,
                "rate": rate,
                "save": market_total - coupon_price,
                "validFrom": lo,
                "validUntil": hi,
            })
        return rows

    # -------------------------------------------------- 7. 下单（需显式确认）
    def create_order(self, store_code, scene_key, items, be_code=None,
                     take_way_code=None, address_id=None, remark=None,
                     need_tableware=False, reservation_date=None):
        args = self._scene_args(scene_key, store_code, be_code, reservation_date)
        args["items"] = items
        args["needTableware"] = need_tableware
        sc = SCENES[scene_key]
        if sc["orderType"] == 1:
            if not take_way_code:
                raise McdMcpError("到店/得来速下单必须提供 takeWayCode（堂食 eat-in / 外带 take-in-store）")
            args["takeWayCode"] = take_way_code
        else:
            if address_id:
                args["addressId"] = address_id
            if remark:
                args["remark"] = remark
        return self._data("create-order", args)

    def order_list(self):
        return self._data("order-list", {})


def re_split_window(window):
    """
    解析券有效期区间，返回 (起, 止)。

    服务端返回的格式形如：
        '2026-10-12 10:30:00-2026-10-16 23:59:59'
    注意日期本身含连字符，不能直接按 '-' 切分（早期版本就在这里踩过坑），
    因此改为用正则抓时间戳。
    """
    stamps = re.findall(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", window or "")
    if len(stamps) >= 2:
        return stamps[0], stamps[1]
    days = re.findall(r"\d{4}-\d{2}-\d{2}", window or "")
    if len(days) >= 2:
        return days[0] + " 00:00:00", days[1] + " 23:59:59"
    return "", ""


# ================================================================ CLI
def main():
    ap = argparse.ArgumentParser(
        prog="copilot",
        description="麦麦点单副驾 — 基于麦当劳 MCP 的点单助手",
    )
    ap.add_argument("--scene", default="pickup", choices=list(SCENES), help="点单场景")
    # 同一选项也挂在子命令上，`--scene` 放前放后都能用
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scene", dest="scene_sub", choices=list(SCENES),
                        default=None, help="点单场景（子命令级）")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("grab-coupons", parents=[common], help="一键领取当前可领的麦麦省券")
    sub.add_parser("my-coupons", parents=[common], help="查看账户已有优惠券")
    sub.add_parser("account", parents=[common], help="查看麦享会积分账户")

    p = sub.add_parser("stores", parents=[common], help="查询可点餐门店")
    p.add_argument("--city")
    p.add_argument("--keyword", default="麦当劳")
    p.add_argument("--address-id")

    p = sub.add_parser("menu", parents=[common], help="查看门店菜单分类")
    p.add_argument("--store", required=True)
    p.add_argument("--be-code")

    p = sub.add_parser("coupons", parents=[common], help="查看该门店当前可用券")
    p.add_argument("--store", required=True)
    p.add_argument("--be-code")

    p = sub.add_parser("optimize", parents=[common], help="核心：枚举凑单组合并比价")
    p.add_argument("--store", required=True)
    p.add_argument("--be-code")
    p.add_argument("--items", required=True, help="格式 code:qty,code:qty")

    p = sub.add_parser("worth", parents=[common], help="值不值：拆解券商品折算单价")
    p.add_argument("--store", required=True)
    p.add_argument("--be-code")

    p = sub.add_parser("order", parents=[common], help="下单（危险操作，需 --confirm）")
    p.add_argument("--store", required=True)
    p.add_argument("--be-code")
    p.add_argument("--items", required=True)
    p.add_argument("--take-way", help="堂食 eat-in / 外带 take-in-store")
    p.add_argument("--address-id")
    p.add_argument("--remark")
    p.add_argument("--confirm", action="store_true", help="真正下单必须显式加此参数")

    args = ap.parse_args()
    args.scene = getattr(args, "scene_sub", None) or args.scene
    cop = Copilot()

    if args.cmd == "grab-coupons":
        got = cop.grab_coupons()
        print("✅ 新领到 %d 张券：" % len(got))
        for g in got:
            print("   -", g.get("couponName"), "| code:", g.get("couponCode"))

    elif args.cmd == "my-coupons":
        d = cop.my_coupons() or {}
        for c in d.get("coupons", []):
            print("-", c.get("title"), "| code:", c.get("code"))

    elif args.cmd == "account":
        d = cop.account() or {}
        print("可用积分:", d.get("availablePoint"), "| 累计:", d.get("accumulativePoint"))

    elif args.cmd == "stores":
        stores = cop.find_stores(args.scene, args.city, args.keyword, args.address_id)
        print("共 %d 家门店：" % len(stores))
        for s in stores:
            print("   storeCode=%s beCode=%s | %s | %s | 营业 %s-%s" % (
                s.get("storeCode"), s.get("beCode", "-"), s.get("storeName"),
                s.get("address", "")[:30], s.get("businessStartTime"), s.get("businessEndTime")))

    elif args.cmd == "menu":
        for cat, code, tags in cop.menu(args.store, args.scene, args.be_code):
            print("[%s] %s  %s" % (cat, code, "/".join(tags or [])))

    elif args.cmd == "coupons":
        for cp in cop.store_coupons(args.store, args.scene, args.be_code):
            prods = ",".join(p.get("productName", "") for p in cp.get("products", []))
            print("- %s | %s | 适用: %s | 有效期 %s" % (
                cp.get("title"), cp.get("couponCode"), prods, cp.get("tradeDateTime")))

    elif args.cmd == "optimize":
        base = parse_items(args.items)
        best, allr = cop.optimize(args.store, args.scene, base, args.be_code)
        print("共试算 %d 个方案，按实付升序：\n" % len(allr))
        for i, r in enumerate(best, 1):
            print("%d. %-46s 实付 %-8s 原价 %-8s 省 %s" % (
                i, r["tag"][:46], yuan(r["price"]), yuan(r["originalPrice"]), yuan(r["discount"])))
            for p in r.get("productList", []):
                print("      · %s x%s 小计 %s" % (p.get("productName"), p.get("quantity"), yuan(p.get("subtotal"))))
        # 结论：区分「不加购最便宜」与「加购最划算」两件事，避免误导
        if best:
            cheapest = best[0]
            print("\n💡 最低实付：%s（%s）" % (cheapest["tag"], yuan(cheapest["price"])))
            extras = [r for r in best if r.get("coupon")]
            if extras:
                best_extra = min(extras, key=lambda r: r["price"])
                add = best_extra["price"] - cheapest["price"]
                if add > 0:
                    print("   若想顺便用券，%s 最划算：+%s 得 %s" % (
                        best_extra["coupon"].get("title"), yuan(add),
                        "、".join(p.get("productName", "") for p in best_extra.get("productList", [])[1:])))
                else:
                    print("   用券反而更便宜：%s 实付 %s" % (
                        best_extra["coupon"].get("title"), yuan(best_extra["price"])))
            print("   （提示：加购商品会抬高总价，是否划算请配合 worth 命令看折算单价）")

    elif args.cmd == "worth":
        rows = cop.worth(args.store, args.scene, args.be_code)
        if not rows:
            print("该门店当前没有可用的券商品")
        for r in rows:
            print("【%s】%s" % (r["couponTitle"], r["productName"]))
            print("   券后价   : %s（%d 件）" % (yuan(r["couponPrice"]), r["pieces"]))
            for it in r["contents"]:
                price_txt = yuan(it["unitPrice"]) if it["unitPrice"] else "单点价 0（饮品常见）"
                print("     · %s %s ×%d" % (it["name"], price_txt, it["qty"]))
            if r["rate"]:
                tail = "（含 0 元项，仅供参考）" if r["partial"] else ""
                print("   市场等价 : %s%s  → 折算单价 %s（%.1f 折）" % (
                    yuan(r["marketTotal"]), tail, yuan(r["perPieceCoupon"]), r["rate"] * 10))
                if r["save"] > 0:
                    print("   等于省了 : %s" % yuan(r["save"]))
            print("   有效期   : %s 至 %s" % (r["validFrom"], r["validUntil"]))
            print()

    elif args.cmd == "order":
        items = parse_items(args.items)
        if not args.confirm:
            data = cop.price(args.store, args.scene, items, args.be_code)
            print("🧾 试算（未下单）：原价 %s → 实付 %s" % (
                yuan(data.get("originalPrice")), yuan(data.get("price"))))
            for p in data.get("productList", []):
                print("   · %s x%s %s" % (p.get("productName"), p.get("quantity"), yuan(p.get("subtotal"))))
            print("\n确认无误后追加 --confirm 才会真正下单。")
            return
        r = cop.create_order(args.store, args.scene, items, args.be_code,
                             args.take_way, args.address_id, args.remark)
        print("✅ 下单成功：", json.dumps(r, ensure_ascii=False)[:500])


if __name__ == "__main__":
    try:
        main()
    except McdMcpError as e:
        print("❌ %s" % e, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(130)
