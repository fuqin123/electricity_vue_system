"""采集与聚合的单元测试。

重点覆盖**小时差值的每一条异常分支** —— 这几条决定了库里数字可不可信：
缺数绝不补 0、累计值回退判换表、超阈值判尖峰、端点偏离整点判 partial。
跑法：python manage.py test apps.energy
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.energy.collectors.base import get_path
from apps.energy.collectors.modbus_rtu import ModbusRtuOverTcpCollector
from apps.energy.models import Gateway, HourlyUsage, Line, Meter, Reading
from apps.energy.services.aggregate import aggregate_hour, compute_hour, current_hour_usage

DAY = datetime(2026, 9, 22).date()


class AggregationTests(TestCase):
    """小时差值的口径测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A1", name="一线")
        cls.gw = Gateway.objects.create(code="GW-T", name="测试网关", kind=Gateway.KIND_MODBUS, host="127.0.0.1", port=1)

    def make_meter(self, code: str = "E-T-01", **kw) -> Meter:
        return Meter.objects.create(
            code=code,
            name=f"测试表 {code}",
            kind=kw.pop("kind", Meter.KIND_ELEC),
            scope=kw.pop("scope", Meter.SCOPE_LINE),
            line=kw.pop("line", self.line),
            gateway=self.gw,
            unit="kWh",
            **kw,
        )

    def read(self, meter: Meter, at: datetime, value: str) -> Reading:
        return Reading.objects.create(meter=meter, at=at, value=Decimal(value), ok=True, raw="test")

    def test_正常整点差值(self):
        """13:59 与 14:59 两条读数 → 14 点用量 = 30。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 50), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 50), "130")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_OK)
        self.assertEqual(res["usage"], Decimal("30.000"))

    def test_端点取最接近整点的那条读数(self):
        """14 点的终点应取 15:00:00 那条，而不是 14:59:00 那条。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 0, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "120")
        self.read(meter, datetime(2026, 9, 22, 15, 0, 0), "125")  # 更贴近整点

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["usage"], Decimal("25.000"))
        self.assertEqual(res["quality"], HourlyUsage.Q_OK)

    def test_缺读数时置空而不是零(self):
        """14:00 前没有任何读数 → 该小时 missing，usage 必须是 None（绝不能是 0）。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 30, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "110")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_MISSING)
        self.assertIsNone(res["usage"])
        self.assertNotEqual(res["usage"], 0)

    def test_累计值回退按换表处理(self):
        """读数从 100 掉到 50（换表/归零）→ 判 reset，差值丢弃。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "50")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_RESET)
        self.assertIsNone(res["usage"])
        self.assertIn("疑似换表", res["note"])

    def test_超阈值判尖峰(self):
        """差值超过 max_hourly → 标 spike，数值保留但不进聚合。"""
        meter = self.make_meter(max_hourly=Decimal("10"))
        self.read(meter, datetime(2026, 9, 22, 13, 59, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "400")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_SPIKE)
        self.assertEqual(res["usage"], Decimal("300.000"))

    def test_容差外不配对_判缺数而不是凑一个数(self):
        """★ B-503 回归测试 —— 这是最危险的一类错，必须锁死。

        场景：本小时**根本没采到样**，场上只有一小时前（13:00）和本小时末（14:59:50）两条读数。

        老实现的端点取法是「at <= 整点 的最后一条」，只设上界不设下界，
        于是它会把 13:00 和 14:59:50 配成一对，算出一个覆盖近 2 小时的「14 点用量」，
        而且只标 partial、数值照给。

        更糟的是另一种常见形态：两条陈旧读数相减正好得 **0**。
        界面上一看就是「这一小时一点电都没用」—— 量级正常、没有报错、没有告警，
        现场根本无从察觉。所以现在超出容差直接判缺数。
        """
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 0, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 50), "130")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_MISSING)
        self.assertIsNone(res["usage"])
        self.assertIn("采样槽", res["note"])

    def test_两条陈旧读数不会被配对成零(self):
        """两条隔了一天的读数不能被配成一对（会得到 0）。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 21, 14, 0, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 0, 0), "100")  # 值相同 → 差值会是 0

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_MISSING)
        self.assertIsNone(res["usage"])
        self.assertNotEqual(res["usage"], 0)

    def test_槽内补采不拉长差值区间(self):
        """同一采样槽内的补采读数不能把区间拉长。

        场景：10:01 正常采到，10:30 因失败重试又采到一条。
        若端点取「整点前最后一条」，区间会变成 09:01→10:30（89 分钟），
        09 点被多算、10 点被少算，而且两条都会因为偏离 1 小时被判 partial 而不进合计
        —— 现场只会看到「这几小时没数据」，完全想不到是补采造成的。
        取「距整点最近」时，10:01 永远胜出。
        """
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 9, 1, 5), "100")
        self.read(meter, datetime(2026, 9, 22, 10, 1, 3), "130")  # 正常采样（离 10:00 最近）
        self.read(meter, datetime(2026, 9, 22, 10, 30, 20), "160")  # 补采，必须被忽略

        res = compute_hour(meter, DAY, 9)
        self.assertEqual(res["quality"], HourlyUsage.Q_OK)
        self.assertEqual(res["usage"], Decimal("30.000"))
        self.assertEqual(res["end_at"], datetime(2026, 9, 22, 10, 1, 3))
        self.assertEqual(res["end_value"], Decimal("130"))

    def test_整点采样节奏下区间正好一小时(self):
        """整点 + 60 秒采样的两条读数 → 区间严格 1 小时，不累积偏差。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 1, 2), "100")
        self.read(meter, datetime(2026, 9, 22, 15, 1, 2), "142")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], HourlyUsage.Q_OK)
        self.assertEqual(res["usage"], Decimal("42.000"))
        self.assertEqual((res["end_at"] - res["start_at"]).total_seconds(), 3600)

    def test_端点未按期采到判partial(self):
        """★ 相位判定：两个端点都偏离采样时刻时，即便区间约等于 1 小时，也不能算「干净」。

        真实形态（09-22 的历史数据）：读数落在 14:49:37 与 15:50:23，
        区间 60.8 分钟，看着完全正常，但两个点各自偏了约 11 分钟，
        这一小时的窗口整体平移了 10 分钟。数值不算错，但不该标成 ok。
        """
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 49, 37), "100")
        self.read(meter, datetime(2026, 9, 22, 15, 50, 23), "130")

        res = compute_hour(meter, DAY, 15)
        self.assertEqual(res["quality"], HourlyUsage.Q_PARTIAL)
        self.assertIn("未按期采到", res["note"])

    def test_写入小时表可重复执行(self):
        """聚合是幂等的：跑两遍结果一致，不会产生重复行。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 50), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 50), "130")

        aggregate_hour(DAY, 14, meters=[meter])
        aggregate_hour(DAY, 14, meters=[meter])

        rows = HourlyUsage.objects.filter(meter=meter, date=DAY, hour=14)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().usage, Decimal("30.000"))

    def test_当前小时给参考值(self):
        """进行中的小时：用「本小时内首末两条读数」给参考值，并带上覆盖占比。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 0, 30), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 30, 0), "112")

        res = current_hour_usage(meter, now=datetime(2026, 9, 22, 14, 30, 0))
        self.assertIsNotNone(res)
        self.assertEqual(res["usage"], Decimal("12.000"))
        self.assertAlmostEqual(res["progress"], 0.4917, places=2)

    def test_当前小时只有一条读数时不给参考值(self):
        """★ 每小时采样节奏下，本小时只有一条读数（就是整点边界读数）时不能给参考值。

        那条读数已经代表**上一个小时**用完了。若照旧把它和「上小时末读数」相减，
        得到的是上一小时的完整用量，当成「本小时已用量」显示会整整错一个小时 ——
        而且量级完全正常，看不出任何异常。
        """
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 1, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 1, 0), "145")  # 本小时唯一一条

        res = current_hour_usage(meter, now=datetime(2026, 9, 22, 14, 30, 0))
        self.assertIsNone(res)

    def test_当前小时无读数时返回空(self):
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 1, 0), "100")
        self.assertIsNone(current_hour_usage(meter, now=datetime(2026, 9, 22, 14, 30, 0)))

    def test_车间总表不挂产线(self):
        """水汽总表 scope=workshop，line 为空，不参与任何产线合计。"""
        meter = self.make_meter(code="S-T-01", kind=Meter.KIND_STEAM, scope=Meter.SCOPE_WORKSHOP, line=None)
        self.assertIsNone(meter.line)
        self.assertEqual(self.line.meters.count(), 0)


class SlotTests(TestCase):
    """采样时刻（slots）—— 差值区间由它决定，错了会整体偏一个小时。"""

    def test_整点采样槽的采样时刻(self):
        from apps.energy.services import slots

        b = datetime(2026, 9, 22, 15, 0, 0)
        self.assertEqual(slots.slot_ready_at(b), b + timedelta(seconds=slots.hourly_offset_seconds()))

    def test_due_slot_在采样窗口前为None(self):
        from apps.energy.services import slots

        # 偏移 60 秒：15:00:30 还没到采样时刻
        self.assertIsNone(slots.due_slot(datetime(2026, 9, 22, 15, 0, 30)))
        # 15:01:30 已到 → 本小时（15 点）该采了
        self.assertEqual(slots.due_slot(datetime(2026, 9, 22, 15, 1, 30)), datetime(2026, 9, 22, 15, 0, 0))

    def test_下一次运行时刻(self):
        from apps.energy.services import slots

        nxt = slots.next_run_at(datetime(2026, 9, 22, 15, 30, 0))
        self.assertEqual(nxt, datetime(2026, 9, 22, 16, 0, 0) + timedelta(seconds=slots.hourly_offset_seconds()))


class CollectorUtilTests(TestCase):
    """采集器里几处纯函数（最容易写错、也最值得锁住的）。"""

    def test_寄存器字序(self):
        regs = [0x03C3, 0x3B79]  # 现场一线路电表的报文：raw = 0x03C33B79
        self.assertEqual(ModbusRtuOverTcpCollector._combine(regs, "big"), 0x03C33B79)
        self.assertEqual(ModbusRtuOverTcpCollector._combine(regs, "little"), 0x3B7903C3)
        self.assertEqual(ModbusRtuOverTcpCollector._combine([0x1234], "big"), 0x1234)

    def test_现场报文换算(self):
        """现场实测：08 03 04 03 c3 3b 79 41 99 → 63126393 × 0.1 = 6,312,639.3 kWh"""
        raw = ModbusRtuOverTcpCollector._combine([0x03C3, 0x3B79], "big")
        self.assertEqual(raw, 63126393)
        self.assertEqual(Decimal(raw) * Decimal("0.1"), Decimal("6312639.3"))

    def test_json路径取值(self):
        payload = {"code": 1, "data": [{"device_data": {"collect_time": "2026-09-22 14:25:00", "var1": 60915.18}}]}
        self.assertEqual(get_path(payload, "data.0.device_data.var1"), 60915.18)
        self.assertEqual(get_path(payload, "data.0.device_data.collect_time"), "2026-09-22 14:25:00")
        self.assertIsNone(get_path(payload, "data.5.device_data.var1"))
        self.assertIsNone(get_path(payload, "data.0.nope.var1"))
        self.assertIsNone(get_path(payload, ""))
