"""采集与聚合的单元测试。

重点覆盖**小时差值的每一条异常分支** —— 这几条决定了库里数字可不可信：
缺数绝不补 0、累计值回退判换表、超阈值判尖峰、端点偏离整点判 partial。
跑法：python manage.py test apps.energy
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase

from apps.energy.collectors.base import get_path
from apps.energy.collectors.modbus_rtu import ModbusRtuOverTcpCollector
from apps.energy.management.commands import collect
from apps.energy.models import Gateway, HourlyUsage, Line, Meter, PeriodSummary, Reading
from apps.energy.services import slots
from apps.energy.services import summary as S
from apps.energy.services.aggregate import aggregate_hour, compute_hour, current_hour_usage
from apps.energy.services.snapshot import build_line, build_overview

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


class IfDueGuardTests(TestCase):
    """`collect --if-due` 的幂等守卫 —— 决定「这一轮到底采不采」。

    现场最常见的两个疑问，都在这里锁死：
      · 10 点采到了，10:05 / 10:10 … 还会再采吗？ → 不会，直到 11 点。
      · 11:01 有一路失败，11:06 会再试吗？        → 会，一直试到本小时采全。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A2", name="二线")
        cls.gw = Gateway.objects.create(
            code="GW-DUE", name="守卫测试网关", kind=Gateway.KIND_MODBUS, host="127.0.0.1", port=2
        )
        cls.meters = [
            Meter.objects.create(
                code=f"E-DUE-{i:02d}",
                name=f"守卫测试表 {i}",
                kind=Meter.KIND_ELEC,
                scope=Meter.SCOPE_LINE,
                line=cls.line,
                gateway=cls.gw,
                unit="kWh",
            )
            for i in (1, 2)
        ]

    def _due(self, moment: datetime) -> bool:
        """在指定时刻问一次「该采吗」——把命令内部的 datetime.now() 冻结住。"""
        with mock.patch("apps.energy.management.commands.collect.datetime") as frozen:
            frozen.now.return_value = moment
            return collect.Command()._is_due(None)

    def _read(self, meter: Meter, at: datetime, ok: bool = True) -> Reading:
        return Reading.objects.create(
            meter=meter, at=at, value=Decimal("100"), ok=ok, raw="test"
        )

    def test_采样窗口未到时不采(self):
        """整点后还没到 hourly_offset_seconds —— 这一步就是 offset 配置真正生效的地方。

        没有它，计划任务若在 11:00:00 整触发，就会绕过刻意留出的避让窗口，
        在整点瞬间读表（表内累计可能还没刷新完）。
        """
        b = datetime(2026, 9, 23, 11, 0, 0)
        self.assertFalse(self._due(slots.slot_ready_at(b) - timedelta(seconds=1)))

    def test_本小时采全后跳过(self):
        """10 点那一轮采全了 → 之后每 5 分钟都跳过，直到 11 点。"""
        b = datetime(2026, 9, 23, 11, 0, 0)
        for meter in self.meters:
            self._read(meter, slots.slot_ready_at(b))
        self.assertFalse(self._due(slots.slot_ready_at(b) + timedelta(minutes=4)))

    def test_有点位没采到就重试(self):
        """11:01 只成功一路 → 11:06 必须再跑（这是重试机制本身）。"""
        b = datetime(2026, 9, 23, 11, 0, 0)
        self._read(self.meters[0], slots.slot_ready_at(b))
        self.assertTrue(self._due(slots.slot_ready_at(b) + timedelta(minutes=5)))

    def test_失败读数不算采到(self):
        """`ok=False` 的读数不算完成 —— 否则一次超时会被当成「已采过」，整个小时就废了。"""
        b = datetime(2026, 9, 23, 11, 0, 0)
        for meter in self.meters:
            self._read(meter, slots.slot_ready_at(b), ok=False)
        self.assertTrue(self._due(slots.slot_ready_at(b) + timedelta(minutes=5)))

    def test_上一小时的读数顶不了本小时(self):
        """11 点该采时，10:59 的读数不算数（守卫只看本小时整点之后的读数）。"""
        b = datetime(2026, 9, 23, 11, 0, 0)
        for meter in self.meters:
            self._read(meter, b - timedelta(minutes=1))
        self.assertTrue(self._due(slots.slot_ready_at(b) + timedelta(minutes=4)))


class LineStatusTests(TestCase):
    """产线状态判定 —— 用电负荷必须取「最近一个已知的小时」（B-510）。

    现场现象：整点后的整整一个小时里，六条产线在大屏上全是「停线」，
    但现场明明在用电。根因是状态取了**当前小时**的用电量，
    而每小时采样节奏下当前小时的值要等下一个整点采样做差才算得出来 —— 恒为 0。
    """

    NOW = datetime(2026, 9, 23, 13, 30)  # 13:30：当前小时（13 点）必然还没有值
    DAY = NOW.date()

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A9", name="九线", rated=30000)
        cls.gw = Gateway.objects.create(
            code="GW-ST", name="状态测试网关", kind=Gateway.KIND_MODBUS, host="127.0.0.1", port=9
        )
        cls.meter = Meter.objects.create(
            code="E-A9-01",
            name="九线产线电表",
            kind=Meter.KIND_ELEC,
            scope=Meter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            unit="kWh",
        )

    def _usage(self, hour: int, value: str, quality: str = HourlyUsage.Q_OK) -> None:
        HourlyUsage.objects.create(
            meter=self.meter, date=self.DAY, hour=hour, usage=Decimal(value), unit="kWh", quality=quality
        )

    def _status(self) -> str:
        return build_line(self.line, self.NOW)["status"]

    def test_当前小时没值时按最近已知小时判定(self):
        """12 点用了 30 kWh/h ≥ 基础负荷 60%（12）→ 运行中，不能因为 13 点还没值就报停线。"""
        self._usage(12, "30")
        self.assertEqual(self._status(), "running")

    def test_低于基础负荷六成判低负荷(self):
        self._usage(12, "5")
        self.assertEqual(self._status(), "slow")

    def test_用电为零判停线(self):
        self._usage(12, "0")
        self.assertEqual(self._status(), "stopped")

    def test_一条数据都没有判停线(self):
        self.assertEqual(self._status(), "stopped")


class ApiSerializationTests(TestCase):
    """接口层序列化 —— 可空字段必须是 JSON null，不能让整个接口 500（B-509）。

    `eep_period_summary` 的数值字段是可空的（该周期无可信数据时写 NULL，
    绝不补 0）。序列化时若直接 `float(r.kwh)`，遇到 NULL 会抛 TypeError，
    接口 500 —— 而「没数据」恰恰是最常见的正常情况。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A1", name="一线")
        # ① 「一条可信数据都没有」的车间行：数值字段全 NULL
        PeriodSummary.objects.create(
            scope=PeriodSummary.SCOPE_WORKSHOP,
            dim=PeriodSummary.DIM_DAY,
            period_key=DAY.isoformat(),
            start_date=DAY,
            end_date=DAY,
            quality=PeriodSummary.Q_MISSING,
            covered_hours=0,
        )
        # ② 正常有值的产线行
        PeriodSummary.objects.create(
            scope=PeriodSummary.SCOPE_LINE,
            dim=PeriodSummary.DIM_DAY,
            period_key=DAY.isoformat(),
            start_date=DAY,
            end_date=DAY,
            line=cls.line,
            kwh=Decimal("123.456"),
            quality=PeriodSummary.Q_OK,
            covered_hours=1,
        )

    def test_周期总计接口遇空值行仍返回200(self):
        r = Client().get("/api/energy/period-summary", {"dim": "day", "scope": "workshop"})
        self.assertEqual(r.status_code, 200)
        item = r.json()["items"][0]
        self.assertIsNone(item["output"])
        self.assertIsNone(item["kwh"])
        self.assertIsNone(item["waterT"])
        self.assertIsNone(item["steamT"])
        self.assertIsNone(item["unitKwh"])

    def test_周期总计接口有值行返回数字(self):
        r = Client().get(
            "/api/energy/period-summary", {"dim": "day", "scope": "line", "line": "A1"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"][0]["kwh"], 123.456)

    def test_总览接口可用(self):
        self.assertEqual(Client().get("/api/energy/overview").status_code, 200)

    def test_小时明细接口可用(self):
        r = Client().get("/api/energy/hourly", {"date": DAY.isoformat()})
        self.assertEqual(r.status_code, 200)


class CollectHealthAlertTests(TestCase):
    """采集停摆必须在界面上看得见（B-511）。

    没有这条告警时，采集整体停掉的现象是「界面一切正常，只是数字不动了」——
    因为每个点位的 `last_ok` 还停在上次成功时的 True。
    """

    NOW = datetime(2026, 9, 23, 13, 30)

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A8", name="八线")
        cls.gw = Gateway.objects.create(
            code="GW-H", name="健康测试网关", kind=Gateway.KIND_MODBUS, host="127.0.0.1", port=8
        )
        cls.meter = Meter.objects.create(
            code="E-A8-01",
            name="八线产线电表",
            kind=Meter.KIND_ELEC,
            scope=Meter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            unit="kWh",
        )

    def _texts(self):
        return [a["text"] for a in build_overview(self.NOW)["workshop"]["alerts"]]

    def test_读数按时更新时不告警(self):
        Reading.objects.create(
            meter=self.meter, at=datetime(2026, 9, 23, 12, 4), value=Decimal("1"), ok=True
        )
        self.assertFalse(any("采集疑似中断" in t for t in self._texts()))

    def test_读数停在两小时前时告警(self):
        Reading.objects.create(
            meter=self.meter, at=datetime(2026, 9, 23, 10, 4), value=Decimal("1"), ok=True
        )
        self.assertTrue(any("采集疑似中断" in t for t in self._texts()))

    def test_从未采到数据时告警(self):
        self.assertTrue(any("尚无任何采集数据" in t for t in self._texts()))


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


class TrendSeriesOrderTests(TestCase):
    """趋势序列的时间方向与空值口径（B-512 / B-513）。

    X 轴是「从左向右读时间」，所以 daily30 / monthly12 / yearly5 必须**正序**
    （末项 = 本期）。原来是倒序：今天落在最左边，看的人要把图反过来读；
    前端还按「末项 = 本期」取去年值（总览的 yearly5[-2]），倒序时取到的是三年前。

    同时锁住空值口径：某个周期没有任何可信小时差值时 `kwh` 必须是 null。
    接口给 0，图上就是一根「这个周期没用电」的零高度柱（铁律禁止的补 0）；
    前端类型声明成非空后，`x.kwh.toFixed()` 还会直接抛异常把整张趋势图带走。
    """

    #: 刻意选在跨月 + 跨年的位置，顺序写反时断言一定会失败
    NOW = datetime(2026, 3, 5, 14, 30)

    @classmethod
    def setUpTestData(cls):
        cls.line = Line.objects.create(code="A1", name="一线", enabled=True)

    def test_近N日正序且末项是今天(self):
        days = S.recent_days(30, self.NOW)
        self.assertEqual(len(days), 30)
        self.assertEqual(days[0], self.NOW.date() - timedelta(days=29))
        self.assertEqual(days[-1], self.NOW.date())
        self.assertEqual(days, sorted(days))

    def test_近N月正序且跨年不回绕(self):
        months = S.recent_months(12, self.NOW)
        self.assertEqual([d.strftime("%Y-%m") for d in months][:2], ["2025-04", "2025-05"])
        self.assertEqual(months[-1].strftime("%Y-%m"), "2026-03")
        self.assertEqual(months, sorted(months))

    def test_近N年正序(self):
        self.assertEqual([d.year for d in S.recent_years(5, self.NOW)], [2022, 2023, 2024, 2025, 2026])

    def test_日序列正序且缺数是None不是0(self):
        PeriodSummary.objects.create(
            scope=PeriodSummary.SCOPE_LINE,
            dim=PeriodSummary.DIM_DAY,
            line=self.line,
            period_key=self.NOW.strftime("%Y-%m-%d"),
            start_date=self.NOW.date(),
            end_date=self.NOW.date(),
            kwh=Decimal("23.000"),
            quality=PeriodSummary.Q_OK,
            covered_hours=3,
        )
        series = build_line(self.line, now=self.NOW)["daily30"]
        self.assertEqual([p["label"] for p in series][0], "02-04")
        self.assertEqual([p["label"] for p in series][-1], "03-05")
        self.assertEqual(series[-1]["kwh"], 23.0)
        self.assertTrue(all(p["kwh"] is None for p in series[:-1]), "没有可信数据的周期必须是 null")

    def test_月序列正序末项是当月(self):
        months = build_line(self.line, now=self.NOW)["monthly12"]
        self.assertEqual([p["label"] for p in months][0], "2025-04")
        self.assertEqual([p["label"] for p in months][-1], "2026-03")

    def test_车间序列同样正序(self):
        ws = build_overview(self.NOW)["workshop"]
        self.assertEqual(ws["yearly5"][0]["label"], "2022年")
        self.assertEqual(ws["yearly5"][-1]["label"], "2026年")
        self.assertEqual(ws["monthly12"][-1]["label"], "2026-03")

    def test_接口返回的日序列与日期升序一致(self):
        """端到端锁住契约：接口出来的顺序就是前端 X 轴的顺序。"""
        today = datetime.now().date()
        want = [(today - timedelta(days=29 - i)).strftime("%m-%d") for i in range(30)]
        got = [p["label"] for p in Client().get("/api/energy/line/A1").json()["daily30"]]
        self.assertEqual(got, want)
