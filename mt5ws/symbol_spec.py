"""品种规格（symbol spec）解析 —— Web 终端 cmd 18 的响应体。

来源
----
真机实测（Exness demo，2026-10-21）：向 **cmd 18（``CMD_SYMBOL_SPEC``）** 发送

    [u32 count][u32 symbol_id] * count

服务器回一条 **cmd 18 响应**，``res=0``、body 长度 **3072 字节**，
开头是 ``[u32 count]`` 后紧跟每个品种的规格块（首字段为 UTF-16 品种名）。

已确认的字段偏移（**用三个品种交叉验证**，全部符合预期）
--------------------------------------------------------

===========  ========  ==============================================
偏移          类型      含义（实测值 XAUUSDm / EURUSDm / BTCUSDm）
===========  ========  ==============================================
``0x574``     u32       ``digits`` 3 / 5 / 2 ✓
``0x578``     f64       ``point`` 0.001 / 1e-5 / 0.01 ✓
``0x5b4``     f64       ``contract_size`` 100 / 100000 / 1 ✓
``0x754``     i64       ``volume_min`` ×1e8（0.01 手 = 1000000）✓
``0x75c``     i64       ``volume_max`` ×1e8（200 手）✓
``0x764``     i64       ``volume_step`` ×1e8（0.01 手）✓
===========  ========  ==============================================

``volume`` 三个字段是**连续**的三个 i64（0x754 / 0x75c / 0x764，间隔 8），
``digits`` 与 ``point`` 也**相邻**（0x574 / 0x578）—— 这种整齐相邻是偏移正确性的旁证。

用法::

    from mt5api.symbol_spec import parse_spec, SPEC_SIZE
    info = parse_spec(raw_3072_bytes)     # → dict
"""

from __future__ import annotations

import struct

__all__ = ["parse_spec", "SPEC_SIZE", "SPEC_OFFSETS", "volume_to_lots",
           "snap_volume", "compute_margin", "margin_from_spec",
           "selftest_margin", "check_sltp", "selftest_sltp",
           "size_by_risk", "selftest_size_by_risk", "quote_currency",
           "size_by_risk_conv", "selftest_conv"]

#: 单品种规格块长度（实测 3072 字节 = 0x18A0）
SPEC_SIZE = 0xC00

#: 已确认的字段偏移
SPEC_OFFSETS = {
    "digits": 0x574,        # u32
    "point": 0x578,         # f64
    "contract_size": 0x5B4,  # f64
    "volume_min_raw": 0x754,  # i64 ×1e8
    "volume_max_raw": 0x75C,  # i64 ×1e8
    "volume_step_raw": 0x764,  # i64 ×1e8
    "symbol_id": 0x588,     # u32（与品种列表里的 id 一致，可作完整性校验）
    "trade_mode": 0x70C,    # u32（0=disabled 1=longonly 2=shortonly 3=closeonly 4=full）
    "spread_constant": 0x572,  # u16（三个品种都 240，疑为点差上限/常量，非实时点差）
}

#: 手数定点（1 手 = 1e8）
VOLUME_SCALE = 100_000_000


def parse_spec(raw: bytes) -> dict:
    """解析一条 cmd 18 响应的**单个品种规格块**。

    :param raw: 规格块字节（实测 3072 字节；不足会抛 ValueError）
    :returns: ``{name, digits, point, contract_size, volume_min, volume_max,
              volume_step, raw_len}``；不可靠的字段直接不放进结果。
    """
    if len(raw) < SPEC_SIZE:
        raise ValueError(f"规格块长度不足：{len(raw)} < {SPEC_SIZE}")
    out: dict = {"raw_len": len(raw)}

    # 品种名：body 开头是 [u32 count]，随后第一块以 UTF-16 名开头（这里从 4 起）
    for start in (0, 4):
        try:
            name = raw[start:start + 64].decode("utf-16-le", "ignore")
            name = name.split("\x00")[0]
        except Exception:                                    # noqa: BLE001
            continue
        if name and name.isprintable() and len(name) >= 3:
            out["name"] = name
            break

    digits = struct.unpack_from("<I", raw, SPEC_OFFSETS["digits"])[0]
    if 0 <= digits <= 12:
        out["digits"] = digits

    point = struct.unpack_from("<d", raw, SPEC_OFFSETS["point"])[0]
    if 0 < point < 1e6:
        out["point"] = point

    contract = struct.unpack_from("<d", raw, SPEC_OFFSETS["contract_size"])[0]
    if 0 < contract < 1e12:
        out["contract_size"] = contract

    for key, tag in (("volume_min", "volume_min_raw"),
                     ("volume_max", "volume_max_raw"),
                     ("volume_step", "volume_step_raw")):
        raw_v = struct.unpack_from("<q", raw, SPEC_OFFSETS[tag])[0]
        lots = raw_v / VOLUME_SCALE
        if 0 <= lots < 1e9:
            out[key] = lots
    sid = struct.unpack_from("<I", raw, SPEC_OFFSETS["symbol_id"])[0]
    if 0 < sid < 10 ** 6:
        out["symbol_id"] = sid
    tmode = struct.unpack_from("<I", raw, SPEC_OFFSETS["trade_mode"])[0]
    if 0 <= tmode <= 4:
        out["trade_mode"] = tmode
    sp = struct.unpack_from("<H", raw, SPEC_OFFSETS["spread_constant"])[0]
    if 0 < sp < 10 ** 5:
        out["spread_constant"] = sp
    return out


def volume_to_lots(raw_volume: int) -> float:
    """把线上的 ``volume``（×1e8 定点整数）转成手数。"""
    return raw_volume / VOLUME_SCALE


def snap_volume(volume: float, spec: dict) -> float:
    """把任意手数**规整**到该品种的合法档位（在 [min, max] 内、按 step 取整）。

    没有规格信息时原样返回（不猜）。
    """
    vmin = spec.get("volume_min")
    vmax = spec.get("volume_max")
    step = spec.get("volume_step")
    if not (vmin and vmax and step):
        return volume
    v = min(max(volume, vmin), vmax)
    n = round((v - vmin) / step)
    v = vmin + n * step
    # 浮点修正：按 step 的十进制位数收敛
    dec = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1])
              if "." in f"{step:.10f}" else 0)
    return round(min(max(v, vmin), vmax), dec)


# ---------------------------------------------------------------- 保证金估算


def compute_margin(volume: float, contract_size: float, price: float,
                   leverage: int) -> float:
    """按 MT5 标准公式估算**所需保证金**。

    ``margin = 手数 × 合约大小 × 价格 / 杠杆``

    * 手数（lots）、合约大小（如 XAUUSDm=100 盎司、EURUSDm=100000）
    * 杠杆取账户值（Exness demo 实测 1:2000）
    * 说明：MT5 允许按品种/账户设置**保证金比率**与**汇率折算**，本函数只做
      标准线性估算，不含品种级覆盖与保证金货币换算，属**估算**而非权威值。
    """
    if leverage <= 0:
        raise ValueError("杠杆必须为正")
    return volume * contract_size * price / leverage


def margin_from_spec(volume: float, spec: dict, price: float,
                     leverage: int) -> float:
    """:func:`compute_margin` 的便捷版（直接从 :func:`parse_spec` 结果取合约大小）。"""
    contract = spec.get("contract_size")
    if not contract:
        raise ValueError("规格里缺少 contract_size")
    return compute_margin(volume, contract, price, leverage)


def selftest_margin() -> list[tuple[str, bool, str]]:
    """保证金公式自检（纯算术，不联网）。"""
    out: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, ok, detail))

    # XAUUSDm 0.72 手 @4131.81 / 1:2000  → 0.72×100×4131.81/2000 = 148.745...
    m = compute_margin(0.72, 100.0, 4131.81, 2000)
    case("XAU 0.72手 @4131.81 1:2000 ≈ 148.75", abs(m - 148.745) < 0.01, f"{m:.3f}")

    # EURUSDm 0.01 手 @1.12186 / 1:2000 → 0.01×100000×1.12186/2000 = 0.5609...
    m2 = compute_margin(0.01, 100000.0, 1.12186, 2000)
    case("EUR 0.01手 @1.12186 1:2000 ≈ 0.561", abs(m2 - 0.56093) < 0.001, f"{m2:.5f}")

    # BTCUSDm 0.01 手 @84533.19 / 1:2000 → 0.01×1×84533.19/2000 = 0.4227
    m3 = compute_margin(0.01, 1.0, 84533.19, 2000)
    case("BTC 0.01手 @84533.19 1:2000 ≈ 0.423", abs(m3 - 0.422666) < 0.001, f"{m3:.5f}")

    # 杠杆翻倍 → 保证金减半
    case("杠杆翻倍则保证金减半",
         abs(compute_margin(1, 100, 4000, 4000)
             - compute_margin(1, 100, 4000, 2000) / 2) < 1e-9)

    # 用规格里的 contract_size
    spec = {"contract_size": 100000.0}
    case("margin_from_spec 用规格合约大小",
         abs(margin_from_spec(0.01, spec, 1.12186, 2000) - m2) < 1e-12)

    # 非法杠杆
    try:
        compute_margin(1, 100, 4000, 0)
        case("杠杆为 0 应报错", False, "未报错")
    except ValueError:
        case("杠杆为 0 应报错", True)

    return out


def check_sltp(side: str, price: float | None,
               sl: float | None = None, tp: float | None = None) -> list[str]:
    """检查止损/止盈**方向**是否合理，返回问题清单（空 = 没问题）。

    多单：``SL < 价 < TP``；空单：``TP < 价 < SL``。
    """
    problems: list[str] = []
    if not price:
        return problems
    if sl is not None:
        if side == "buy" and not sl < price:
            problems.append(f"多单 SL({sl}) 应低于价格({price})")
        if side == "sell" and not sl > price:
            problems.append(f"空单 SL({sl}) 应高于价格({price})")
    if tp is not None:
        if side == "buy" and not tp > price:
            problems.append(f"多单 TP({tp}) 应高于价格({price})")
        if side == "sell" and not tp < price:
            problems.append(f"空单 TP({tp}) 应低于价格({price})")
    return problems


def selftest_sltp() -> list[tuple[str, bool, str]]:
    """SL/TP 方向检查的自检（纯逻辑）。"""
    out: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, ok, detail))

    case("多单 SL<价<TP 合法", not check_sltp("buy", 100.0, 95.0, 110.0))
    case("多单 SL 高于价 → 报错", len(check_sltp("buy", 100.0, 105.0)) == 1,
         str(check_sltp("buy", 100.0, 105.0)))
    case("多单 TP 低于价 → 报错", len(check_sltp("buy", 100.0, None, 90.0)) == 1)
    case("空单 TP<价<SL 合法", not check_sltp("sell", 100.0, 110.0, 90.0))
    case("空单 SL 低于价 → 报错", len(check_sltp("sell", 100.0, 90.0)) == 1)
    case("无 SL/TP 时不报错", not check_sltp("buy", 100.0))
    case("价格为 0 时跳过检查", not check_sltp("buy", 0.0, 105.0))
    return out


# ---------------------------------------------------------------- 按风险算手数


def size_by_risk(balance: float, risk_pct: float, entry: float, sl: float,
                 spec: dict) -> dict:
    """**按风险百分比**算手数。

    逻辑（线性品种，不含点差/佣金/汇率折算）::

        风险金额      = 余额 × 风险百分比
        每手风险金额  = |入场价 − 止损价| × 合约大小
        理论手数      = 风险金额 / 每手风险金额
        实际手数      = 按 volume_min/step/max 规整后的值

    返回 ``{volume, volume_raw, risk_amount, risk_per_lot, stop_distance,
    snapped}``；``volume`` 为 0 表示**风险太小或太大，任何合法手数都会超风险**。
    """
    contract = spec.get("contract_size")
    if not contract:
        raise ValueError("规格里缺少 contract_size")
    dist = abs(entry - sl)
    if dist <= 0:
        raise ValueError("入场价与止损价相同，无法计算风险")
    risk_amount = balance * risk_pct / 100.0
    risk_per_lot = dist * contract
    raw = risk_amount / risk_per_lot
    snapped = snap_volume(raw, spec)
    return {
        "volume": snapped,
        "volume_raw": raw,
        "risk_amount": risk_amount,
        "risk_per_lot": risk_per_lot,
        "stop_distance": dist,
        "snapped": abs(snapped - raw) > 1e-12,
        "risk_at_volume": snapped * risk_per_lot,
        # 最小手数可能把风险顶上去（例如余额小、止损近、或最小手数 0.01）
        "overshoot": (snapped * risk_per_lot) > risk_amount * 1.5 + 1e-9,
    }


def selftest_size_by_risk() -> list[tuple[str, bool, str]]:
    """按风险算手数的自检（纯算术）。"""
    out: list[tuple[str, bool, str]] = []
    spec = {"contract_size": 100.0, "volume_min": 0.01,
            "volume_step": 0.01, "volume_max": 200.0}

    def case(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, ok, detail))

    # 余额 10000，风险 1% = 100 美元；止损距离 10 美元；合约 100
    # 每手风险 = 10×100 = 1000 美元 → 理论 0.1 手
    r = size_by_risk(10000, 1.0, 4100.0, 4090.0, spec)
    case("余额1万/风险1%/止损10刀 → 0.1 手", abs(r["volume"] - 0.1) < 1e-9,
         str(r["volume"]))
    case("风险金额 = 100", abs(r["risk_amount"] - 100) < 1e-9)
    case("每手风险 = 1000", abs(r["risk_per_lot"] - 1000) < 1e-9)
    case("实际风险 ≈ 100", abs(r["risk_at_volume"] - 100) < 1e-6,
         str(r["risk_at_volume"]))

    # 止损距离减半 → 手数翻倍
    r2 = size_by_risk(10000, 1.0, 4100.0, 4095.0, spec)
    case("止损距离减半 → 手数翻倍", abs(r2["volume"] - 0.2) < 1e-9, str(r2["volume"]))

    # 风险太小手数被下限夹住
    r3 = size_by_risk(100, 0.1, 4100.0, 4099.0, spec)   # 风险 0.1 美元
    case("极小风险 → 夹到最小手数并标记",
         abs(r3["volume"] - 0.01) < 1e-12 and r3["snapped"], str(r3["volume"]))
    case("最小手数导致风险超目标 → overshoot=True",
         size_by_risk(100, 0.1, 4100.0, 4099.0, spec)["overshoot"])
    case("正常情形 overshoot=False",
         not size_by_risk(10000, 1.0, 4100.0, 4090.0, spec)["overshoot"])

    # 止损距离为 0 应报错
    try:
        size_by_risk(10000, 1.0, 4100.0, 4100.0, spec)
        case("止损距离为 0 应报错", False, "未报错")
    except ValueError:
        case("止损距离为 0 应报错", True)
    return out


# ---------------------------------------------------------------- 货币折算


def quote_currency(symbol: str, *, known_quotes: tuple[str, ...] = (
        "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")) -> str | None:
    """从品种名里猜出**报价货币**（MT5 常用 ``XXXYYY<后缀>``）。

    例：``XAUUSDm`` → ``USD``；``EURUSDm`` → ``USD``；``USDJPYm`` → ``JPY``；
    ``GBPJPYm`` → ``JPY``。找不到就返回 ``None``（调用方应视为"未知，不折算"）。
    """
    s = symbol.upper()
    # 去掉末尾的账户后缀（如 "m"/"M"）：若去掉后正好以已知货币结尾，就剥掉
    if len(s) > 6 and s[-1].isalpha() and any(s[:-1].endswith(q) for q in known_quotes):
        s = s[:-1]
    for i in range(2, min(len(s), 8)):
        head, tail = s[:i], s[i:]
        # 前半段可以是金属/指数等（如 XAU/XAG/BTC），只要求**后半段是已知货币**
        if 2 <= len(head) <= 6 and head.isalpha() and tail in known_quotes:
            return tail
    return None


def size_by_risk_conv(balance: float, risk_pct: float, entry: float, sl: float,
                      spec: dict, *, quote_to_account: float = 1.0) -> dict:
    """:func:`size_by_risk` + **报价货币 → 账户货币** 折算。

    ``quote_to_account`` = 1 单位报价货币值多少账户货币
    （如 USDJPYm 的报价是 JPY ⇒ ``1/USDJPY价`` ≈ 0.0063）。
    """
    base = size_by_risk(balance, risk_pct, entry, sl, spec)
    conv = max(1e-12, float(quote_to_account))
    base["quote_to_account"] = conv
    base["risk_in_account"] = base["risk_at_volume"] * conv
    base["target_risk"] = base["risk_amount"]
    base["overshoot_account"] = (
        base["risk_in_account"] > base["risk_amount"] * 1.5 + 1e-9)
    return base


def selftest_conv() -> list[tuple[str, bool, str]]:
    """货币折算与品种报价货币识别的自检（纯逻辑）。"""
    out: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, ok, detail))

    for sym, exp in (("XAUUSDm", "USD"), ("EURUSDm", "USD"), ("GBPJPYm", "JPY"),
                     ("USDJPYm", "JPY"), ("AUDCADm", "CAD"), ("USDCHFm", "CHF")):
        case(f"{sym} 报价货币 = {exp}", quote_currency(sym) == exp,
             str(quote_currency(sym)))
    case("无法识别时返回 None", quote_currency("WEIRD") is None,
         str(quote_currency("WEIRD")))

    spec = {"contract_size": 100000.0, "volume_min": 0.01,
            "volume_step": 0.01, "volume_max": 200.0}
    # USDJPYm：止损 0.30（JPY）、0.01 手 → 报价货币风险 300 JPY
    r = size_by_risk_conv(10020, 0.5, 158.158, 157.858, spec,
                          quote_to_account=1 / 158.0)
    case("USDJPY 风险折算到 USD ≈ 1.90", abs(r["risk_in_account"] - 300 / 158) < 0.02,
         f"{r['risk_in_account']:.3f}")
    case("折算后不再 overshoot", not r["overshoot_account"],
         str(r["overshoot_account"]))

    # 同品种若不折算（quote_to_account=1）→ 会误判为 overshoot
    r2 = size_by_risk_conv(10020, 0.5, 158.158, 157.858, spec)
    case("不折算时被误判 overshoot（说明折算必要）", r2["overshoot_account"],
         str(r2["risk_in_account"]))
    case("USD 品种折算系数 = 1 时不改变结果",
         abs(size_by_risk_conv(10000, 1.0, 4100, 4090,
                               {"contract_size": 100.0, "volume_min": 0.01,
                                "volume_step": 0.01, "volume_max": 200.0}
                               )["risk_in_account"] - 100.0) < 1e-9)
    return out
