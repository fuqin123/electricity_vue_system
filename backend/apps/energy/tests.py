"""采集与聚合的单元测试。

重点覆盖**小时差值的每一条异常分支** —— 这几条决定了库里数字可不可信：
缺数绝不补 0、累计值回退判换表、超阈值判尖峰、端点偏离整点判 partial。
跑法：python manage.py test apps.energy
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, RequestFactory, TestCase

from apps.energy.collectors.base import BaseCollector, CollectResult, get_path
from apps.energy.collectors.modbus_rtu import ModbusRtuOverTcpCollector
from apps.energy.management.commands import collect
from apps.energy import api
from apps.energy.models import (
    EnergyGateway,
    EnergyCollectLog,
    EnergyHourly,
    EnergyMeter,
    EnergyDaily,
    EnergyRaw,
    EnergyType,
    PeriodSummary,
    ProductionHourly,
    ProductionLine,
    SystemParam,
)
from apps.energy.services import ranges, slots
from apps.energy.services import summary as S
from apps.energy.services.aggregate import aggregate_hour, compute_hour, current_hour_usage
from apps.energy.services.snapshot import build_line, build_overview
from eep import conf

DAY = datetime(2026, 9, 22).date()


def etype(code: str = EnergyType.ELECTRIC) -> EnergyType:
    """取能源类型实例（v1.3.0 起它是一张表，迁移里已灌好三种）。"""
    return EnergyType.objects.get(type_code=code)


class AggregationTests(TestCase):
    """小时差值的口径测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        cls.gw = EnergyGateway.objects.create(code="GW-T", name="测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1)

    def make_meter(self, code: str = "E-T-01", **kw) -> EnergyMeter:
        return EnergyMeter.objects.create(
            code=code,
            name=f"测试表 {code}",
            energy_type=etype(kw.pop("kind", EnergyType.ELECTRIC)),
            scope=kw.pop("scope", EnergyMeter.SCOPE_LINE),
            line=kw.pop("line", self.line),
            gateway=self.gw,
            unit="kWh",
            **kw,
        )

    def read(self, meter: EnergyMeter, at: datetime, value: str) -> EnergyRaw:
        return EnergyRaw.objects.create(
            meter=meter,
            line=meter.line,
            energy_type=meter.energy_type,
            collect_time=at,
            reading=Decimal(value),
            ok=True,
            raw="test",
        )

    def test_正常整点差值(self):
        """13:59 与 14:59 两条读数 → 14 点用量 = 30。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 50), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 50), "130")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_OK)
        self.assertEqual(res["usage"], Decimal("30.000"))

    def test_端点取最接近整点的那条读数(self):
        """14 点的终点应取 15:00:00 那条，而不是 14:59:00 那条。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 0, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "120")
        self.read(meter, datetime(2026, 9, 22, 15, 0, 0), "125")  # 更贴近整点

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["usage"], Decimal("25.000"))
        self.assertEqual(res["quality"], EnergyHourly.Q_OK)

    def test_缺读数时置空而不是零(self):
        """14:00 前没有任何读数 → 该小时 missing，usage 必须是 None（绝不能是 0）。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 30, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "110")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_MISSING)
        self.assertIsNone(res["usage"])
        self.assertNotEqual(res["usage"], 0)

    def test_累计值回退按换表处理(self):
        """读数从 100 掉到 50（换表/归零）→ 判 reset，差值丢弃。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "50")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_RESET)
        self.assertIsNone(res["usage"])
        self.assertIn("疑似换表", res["note"])

    def test_超阈值判尖峰(self):
        """差值超过 max_hourly → 标 spike，数值保留但不进聚合。"""
        meter = self.make_meter(max_hourly=Decimal("10"))
        self.read(meter, datetime(2026, 9, 22, 13, 59, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 0), "400")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_SPIKE)
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
        self.assertEqual(res["quality"], EnergyHourly.Q_MISSING)
        self.assertIsNone(res["usage"])
        self.assertIn("采样槽", res["note"])

    def test_两条陈旧读数不会被配对成零(self):
        """两条隔了一天的读数不能被配成一对（会得到 0）。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 21, 14, 0, 0), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 0, 0), "100")  # 值相同 → 差值会是 0

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_MISSING)
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
        self.assertEqual(res["quality"], EnergyHourly.Q_OK)
        self.assertEqual(res["usage"], Decimal("30.000"))
        self.assertEqual(res["end_at"], datetime(2026, 9, 22, 10, 1, 3))
        self.assertEqual(res["end_reading"], Decimal("130"))

    def test_整点采样节奏下区间正好一小时(self):
        """整点 + 60 秒采样的两条读数 → 区间严格 1 小时，不累积偏差。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 14, 1, 2), "100")
        self.read(meter, datetime(2026, 9, 22, 15, 1, 2), "142")

        res = compute_hour(meter, DAY, 14)
        self.assertEqual(res["quality"], EnergyHourly.Q_OK)
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
        self.assertEqual(res["quality"], EnergyHourly.Q_PARTIAL)
        self.assertIn("未按期采到", res["note"])

    def test_写入小时表可重复执行(self):
        """聚合是幂等的：跑两遍结果一致，不会产生重复行。"""
        meter = self.make_meter()
        self.read(meter, datetime(2026, 9, 22, 13, 59, 50), "100")
        self.read(meter, datetime(2026, 9, 22, 14, 59, 50), "130")

        aggregate_hour(DAY, 14, meters=[meter])
        aggregate_hour(DAY, 14, meters=[meter])

        rows = EnergyHourly.objects.filter(meter=meter, stat_date=DAY, stat_hour=14)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().consumption, Decimal("30.000"))

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

    def test_车间总表挂哨兵行且不进产线合计(self):
        """水汽总表 scope=workshop —— 挂「车间哨兵行」，且不计入任何产线合计。

        v1.3.0 起改为挂哨兵行而不是留空：日/月/年汇总表以 (line, energy_type, 周期)
        建唯一键，而 MySQL 把多个 NULL 视为互不相同，留空会让唯一约束失效。
        """
        ws = ProductionLine.objects.get(line_code=ProductionLine.WORKSHOP_CODE)
        meter = self.make_meter(code="S-T-01", kind=EnergyType.STEAM,
                                scope=EnergyMeter.SCOPE_WORKSHOP, line=ws)
        self.assertTrue(meter.line.is_workshop)
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
        cls.line = ProductionLine.objects.create(line_code="A2", line_name="二线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-DUE", name="守卫测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=2
        )
        cls.meters = [
            EnergyMeter.objects.create(
                code=f"E-DUE-{i:02d}",
                name=f"守卫测试表 {i}",
                energy_type=etype(),
                scope=EnergyMeter.SCOPE_LINE,
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

    def _read(self, meter: EnergyMeter, at: datetime, ok: bool = True) -> EnergyRaw:
        return EnergyRaw.objects.create(
            meter=meter,
            line=meter.line,
            energy_type=meter.energy_type,
            collect_time=at,
            reading=Decimal("100"),
            ok=ok,
            raw="test",
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
        cls.line = ProductionLine.objects.create(line_code="A9", line_name="九线", rated=30000)
        cls.gw = EnergyGateway.objects.create(
            code="GW-ST", name="状态测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=9
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-A9-01",
            name="九线产线电表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            unit="kWh",
        )

    def _usage(self, hour: int, value: str, quality: str = EnergyHourly.Q_OK) -> None:
        EnergyHourly.objects.create(
            meter=self.meter,
            line=self.meter.line,
            energy_type=self.meter.energy_type,
            stat_date=self.DAY,
            stat_hour=hour,
            consumption=Decimal(value),
            unit="kWh",
            quality=quality,
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
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        # 车间哨兵行已由迁移 0006 建好，这里取用即可
        cls.ws = ProductionLine.objects.get(line_code=ProductionLine.WORKSHOP_CODE)
        # ① 产线电力：正常有值
        EnergyDaily.objects.create(
            line=cls.line,
            energy_type=etype(),
            stat_date=DAY,
            consumption=Decimal("123.456"),
            unit="kWh",
            covered_count=1,
            expected_count=24,
            quality=PeriodSummary.Q_OK,
        )
        # ② 车间水表：该周期一条可信数据都没有 → consumption 为 NULL（不是 0）
        EnergyDaily.objects.create(
            line=cls.ws,
            energy_type=etype(EnergyType.WATER),
            stat_date=DAY,
            consumption=None,
            unit="m³",
            covered_count=0,
            expected_count=24,
            quality=PeriodSummary.Q_MISSING,
        )

    def test_周期总计接口遇空值行仍返回200(self):
        """B-509 回归：汇总行有 NULL 值时，接口必须返回 200 + JSON null，而不是 500。

        同时锁住两条口径：
          · 车间**电力** = 各产线之和（车间没有独立电力总表）；
          · 水/汽取车间哨兵行的总表，该周期没有可信数据时给 null（不是 0）。
        """
        r = Client().get("/api/energy/period-summary", {"dim": "day", "scope": "workshop"})
        self.assertEqual(r.status_code, 200)
        item = r.json()["items"][0]
        self.assertEqual(item["kwh"], 123.456)   # 车间电力 = 产线之和
        self.assertIsNone(item["waterT"])        # 空值 → JSON null（绝不补 0）
        self.assertIsNone(item["steamT"])
        self.assertIsNone(item["output"])        # 产量未接入 → null
        self.assertIsNone(item["unitKwh"])       # 分母缺失 → 算不出，给 null

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
        cls.line = ProductionLine.objects.create(line_code="A8", line_name="八线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-H", name="健康测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=8
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-A8-01",
            name="八线产线电表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            unit="kWh",
        )

    def _texts(self):
        return [a["text"] for a in build_overview(self.NOW)["workshop"]["alerts"]]

    def test_读数按时更新时不告警(self):
        EnergyRaw.objects.create(
            meter=self.meter,
            line=self.meter.line,
            energy_type=self.meter.energy_type,
            collect_time=datetime(2026, 9, 23, 12, 4),
            reading=Decimal("1"),
            ok=True,
        )
        self.assertFalse(any("采集疑似中断" in t for t in self._texts()))

    def test_读数停在两小时前时告警(self):
        EnergyRaw.objects.create(
            meter=self.meter,
            line=self.meter.line,
            energy_type=self.meter.energy_type,
            collect_time=datetime(2026, 9, 23, 10, 4),
            reading=Decimal("1"),
            ok=True,
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
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线", status=ProductionLine.STATUS_ON)

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
        EnergyDaily.objects.create(
            line=self.line,
            energy_type=etype(),
            stat_date=self.NOW.date(),
            consumption=Decimal("23.000"),
            unit="kWh",
            covered_count=3,
            expected_count=24,
            quality=PeriodSummary.Q_OK,
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


class HourRangeTests(TestCase):
    """v1.4.0：起止时间精确到小时（数据中心 / 用量查询 / 仪表读数三处共用）。

    以前只有 `YYYY-MM-DD`，现场反馈「现在的方式无法准确知晓」覆盖到哪一小时；
    端点统一规范到整点，因为本项目最小的时间桶就是 1 小时。
    """

    @staticmethod
    def req(**params):
        return RequestFactory().get("/api/energy/range", params)

    def test_小时端点规范到整点(self):
        s, e = ranges.parse_hours(self.req(**{"from": "2026-09-24 08:37", "to": "2026-09-24 14:12"}))
        self.assertEqual((s.hour, s.minute), (8, 0))
        self.assertEqual((e.hour, e.minute), (14, 0))

    def test_纯日期取当日零点(self):
        s, e = ranges.parse_hours(self.req(**{"from": "2026-09-24", "to": "2026-09-24 05"}))
        self.assertEqual(s, datetime(2026, 9, 24, 0, 0))
        self.assertEqual(e, datetime(2026, 9, 24, 5, 0))

    def test_T_写法与带秒写法都能解析(self):
        s, _ = ranges.parse_hours(self.req(**{"from": "2026-09-24T08:15:30", "to": "2026-09-24 09"}))
        self.assertEqual(s, datetime(2026, 9, 24, 8, 0))

    def test_起止乱序自动交换(self):
        s, e = ranges.parse_hours(self.req(**{"from": "2026-09-24 14", "to": "2026-09-24 08"}))
        self.assertLess(s, e)

    def test_非法格式抛错而不是静默取默认(self):
        """静默回退到「今天」会让用户以为筛选生效了，实际查的是另一段时间。"""
        with self.assertRaises(ValueError):
            ranges.parse_hours(self.req(**{"from": "24/09/2026"}))

    def test_小时桶只取端点闭区间(self):
        """查 08→10 就该是 3 个桶：多一桶少一桶都会让区间的合计对不上。"""
        buckets = ranges._hour_labels(
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24, 20, 0),
            start_at=datetime(2026, 9, 24, 8),
            end_at=datetime(2026, 9, 24, 10),
        )
        self.assertEqual([h for _d, h in buckets], [8, 9, 10])

    def test_不给端点时仍是整日行为(self):
        """大屏与既有调用方没传小时端点，行为必须一字不变。"""
        buckets = ranges._hour_labels(
            datetime(2026, 9, 24).date(), datetime(2026, 9, 24).date(), datetime(2026, 9, 24, 3, 0)
        )
        self.assertEqual([h for _d, h in buckets], [0, 1, 2, 3])

    # ── v1.4.4：进行中的当前小时不入桶 ────────────────────────

    def test_进行中的当前小时不入桶(self):
        """15 点时最大可查时段是 14 点。

        小时用量 = 下个整点读数 − 本整点读数，当前小时要到下个整点才算得出。
        若把它放进桶里，得到的必然是一个恒空时段，用量查询页会把它读成
        「共 16 个时段，其中 1 个时段缺数」—— 现场据此以为采集漏了一小时。
        """
        buckets = ranges._hour_labels(
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24, 15, 20),
            start_at=datetime(2026, 9, 24, 0),
            end_at=datetime(2026, 9, 24, 15),
            closed_only=True,
        )
        hours = [h for _d, h in buckets]
        self.assertEqual(hours[-1], 14)
        self.assertNotIn(15, hours)
        self.assertEqual(len(hours), 15)

    def test_未开closed_only时行为不变(self):
        """默认仍含当前小时 —— 数据中心那条「本小时起」的口径不能被动到。"""
        buckets = ranges._hour_labels(
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24, 15, 20),
            start_at=datetime(2026, 9, 24, 0),
            end_at=datetime(2026, 9, 24, 15),
        )
        self.assertEqual([h for _d, h in buckets][-1], 15)

    def test_零点刚过当天没有已结算小时(self):
        """00:xx 时当天一个已结束的小时都没有，桶必须是空的而不是硬塞一个 0 点。"""
        buckets = ranges._hour_labels(
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24).date(),
            datetime(2026, 9, 24, 0, 30),
            start_at=datetime(2026, 9, 24, 0),
            end_at=datetime(2026, 9, 24, 0),
            closed_only=True,
        )
        self.assertEqual(buckets, [])

    def test_已过完的日子不受closed_only影响(self):
        buckets = ranges._hour_labels(
            datetime(2026, 9, 23).date(),
            datetime(2026, 9, 23).date(),
            datetime(2026, 9, 24, 15, 20),
            closed_only=True,
        )
        self.assertEqual(len(buckets), 24)

    def test_用量区间接口不再带出当前小时(self):
        """端到端：查「今天 00:00 → 现在（15:20）」，最后一个时段必须是 14 点。"""
        day = datetime(2026, 9, 24).date()
        res = ranges.usage_range(
            day,
            day,
            granularity="hour",
            scope="workshop",
            now=datetime(2026, 9, 24, 15, 20),
            start_at=datetime(2026, 9, 24, 0),
            end_at=datetime(2026, 9, 24, 15),
        )
        self.assertEqual(res["bucketCount"], 15)
        self.assertEqual(res["labels"][-1], "09-24 14")
        # 没有可信数据时质量是 partial/missing（不是 ok），但**不能**出现 future/缺一个多出来的空桶
        self.assertEqual(len(res["quality"]), 15)


class DashboardCumulativeTests(TestCase):
    """v1.4.0 数据中心：累计序列与单箱变动费用的缺数口径。

    核心要防的还是那一类错：**缺数被当成 0 参与累计**，或者费用少算一项单价
    却给出一个看起来正常的偏小数。
    """

    DAY = datetime(2026, 9, 24).date()
    NOW = datetime(2026, 9, 24, 10, 30)

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        # v1.3.0 起 line_id 全表非空：车间总表挂「车间哨兵行」，这样唯一键处处有效
        cls.workshop = ProductionLine.objects.create(
            line_code="WS", line_name="车间", line_type=ProductionLine.TYPE_WORKSHOP
        )
        cls.gw = EnergyGateway.objects.create(
            code="GW-T", name="测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )

    def meter(self, code: str, kind: str, scope: str = EnergyMeter.SCOPE_LINE) -> EnergyMeter:
        owner = self.line if scope == EnergyMeter.SCOPE_LINE else self.workshop
        return EnergyMeter.objects.create(
            code=code,
            name=f"测试表 {code}",
            energy_type=etype(kind),
            scope=scope,
            line=owner,
            gateway=self.gw,
            unit="kWh" if kind == EnergyType.ELECTRIC else "t",
        )

    def usage(self, meter: EnergyMeter, hour: int, value: str, quality: str = EnergyHourly.Q_OK) -> None:
        EnergyHourly.objects.create(
            meter=meter,
            line=meter.line,
            energy_type=meter.energy_type,
            stat_date=self.DAY,
            stat_hour=hour,
            consumption=Decimal(value) if value else None,
            unit=meter.unit,
            quality=quality,
        )

    def test_累计跳过缺数格并如实给出覆盖度(self):
        """09 点整块没采到：累计值仍是真实部分和，但 covered 必须停在 1 → 2 而不是 3。"""
        m = self.meter("E-A1-01", EnergyType.ELECTRIC)
        self.usage(m, 8, "10")
        self.usage(m, 10, "40")

        d = ranges.dashboard_range(datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 10), now=self.NOW)
        e = d["energies"]["electric"]
        self.assertEqual(d["bucketCount"], 3)
        self.assertEqual(e["values"], [10.0, None, 40.0])
        self.assertEqual(e["cumulative"], [10.0, 10.0, 50.0])
        self.assertEqual(e["cumulativeCovered"], [1, 1, 2])
        self.assertEqual(d["totals"]["kwh"], 50.0)

    def test_产量未接入时单耗与费用一律为null(self):
        m = self.meter("E-A1-01", EnergyType.ELECTRIC)
        self.usage(m, 8, "10")
        d = ranges.dashboard_range(datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 8), now=self.NOW)
        self.assertFalse(d["productionAvailable"])
        self.assertTrue(all(v is None for v in d["energies"]["electric"]["outputCumulative"]))
        self.assertTrue(all(v is None for v in d["energies"]["electric"]["unitCumulative"]))
        self.assertIsNone(d["totals"]["output"])
        self.assertIn("产量数据源未接入", " ".join(d["cost"]["blockers"]))

    def test_单价未标定不产出偏小费用(self):
        """库里一个参数都没有 → 费用必须是 None，而不是「按 0 价算出来的 0 元」。"""
        m = self.meter("E-A1-01", EnergyType.ELECTRIC)
        self.usage(m, 8, "10")
        d = ranges.dashboard_range(datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 8), now=self.NOW)
        self.assertFalse(d["cost"]["available"])
        self.assertTrue(all(v is None for v in d["cost"]["values"]))
        self.assertIn("成本参数未标定", " ".join(d["cost"]["blockers"]))

    def test_算料齐全时单箱变动费用按财务口径算(self):
        """电 100kWh×0.65 + 水 2t×4.5 + 汽 1t×260 = 334 元；24000 瓶 ÷ 24 = 1000 箱 → 0.334 元/箱"""
        elec = self.meter("E-A1-01", EnergyType.ELECTRIC)
        water = self.meter("E-W-01", EnergyType.WATER, EnergyMeter.SCOPE_WORKSHOP)
        steam = self.meter("E-S-01", EnergyType.STEAM, EnergyMeter.SCOPE_WORKSHOP)
        self.usage(elec, 8, "100")
        self.usage(water, 8, "2")
        self.usage(steam, 8, "1")
        ProductionHourly.objects.create(
            line=self.line,
            stat_date=self.DAY,
            stat_hour=8,
            output=Decimal("24000"),
            quality=ProductionHourly.Q_OK,
            source=ProductionHourly.SOURCE_MES,
        )
        for key, val, unit in (
            ("price.electric", "0.65", "元/kWh"),
            ("price.water", "4.5", "元/t"),
            ("price.steam", "260", "元/t"),
            ("package.bottles_per_case", "24", "瓶/箱"),
        ):
            SystemParam.objects.create(key=key, name=key, value=Decimal(val), unit=unit)

        d = ranges.dashboard_range(datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 8), now=self.NOW)
        self.assertTrue(d["cost"]["available"])
        self.assertEqual(d["cost"]["values"][0], 334.0)
        self.assertEqual(d["cost"]["perCase"][0], 0.33)
        # 标准值未标定 → 不画基准线，且不能是 0
        self.assertIsNone(d["cost"]["standardPerCase"])

    def test_少一项单价时整段费用为null而不是偏小值(self):
        """只标定电价时，按「水汽 0 元」算出来的费用会偏小且看起来完全正常 —— 必须拒绝。"""
        elec = self.meter("E-A1-01", EnergyType.ELECTRIC)
        self.usage(elec, 8, "100")
        SystemParam.objects.create(key="price.electric", name="电价", value=Decimal("0.65"), unit="元/kWh")
        SystemParam.objects.create(key="package.bottles_per_case", name="箱规", value=Decimal("24"), unit="瓶/箱")
        d = ranges.dashboard_range(datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 8), now=self.NOW)
        self.assertFalse(d["cost"]["available"])
        self.assertTrue(all(v is None for v in d["cost"]["values"]))

    def test_按线查询时水汽无数据但不报错(self):
        """水/汽只有车间总表：选单线时它们的序列是 null，界面据此禁用勾选。"""
        elec = self.meter("E-A1-01", EnergyType.ELECTRIC)
        self.usage(elec, 8, "10")
        d = ranges.dashboard_range(
            datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 8), line_code="A1", now=self.NOW
        )
        self.assertEqual(d["line"], "A1")
        self.assertTrue(all(v is None for v in d["energies"]["water"]["values"]))
        self.assertFalse(d["energies"]["steam"]["perLineAvailable"])


class ClosedDaySnapshotTests(TestCase):
    """v1.4.1（B-522）：查**已经过完的一天**时，大屏不能留一个「进行中」的小时。

    起因：`_now_for(历史日)` 把 as-of 造成那天的 23:59:59，于是 `day == now.date()` 恒真，
    第 23 小时被当成还没结束，改走实时窗口；而实时窗口当时只写了下界，
    就把「那天 23:04 之后直到此刻」的全部读数减成一个数。
    实测 09-27 一线 23 点显示 85 kWh（真值 8.1）、三线 3285 kWh（真值 343.1），
    日累计跟着一起虚高 —— 大屏与后台于是既「对不上」又「23 点特别离谱」。
    """

    DAY = datetime(2026, 9, 27).date()
    AS_OF = datetime(2026, 9, 27, 23, 59, 59)  # 大屏查历史日时接口构造的「现在」
    REAL_NOW = datetime(2026, 9, 28, 9, 17)  # 真实的此刻（已是次日）
    STEP = Decimal("10")

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        cls.workshop = ProductionLine.objects.create(
            line_code="WS", line_name="车间", line_type=ProductionLine.TYPE_WORKSHOP
        )
        cls.gw = EnergyGateway.objects.create(
            code="GW-T", name="测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )

    def meter(self, code: str = "E-A1-01", kind: str = EnergyType.ELECTRIC) -> EnergyMeter:
        return EnergyMeter.objects.create(
            code=code,
            name=f"测试表 {code}",
            energy_type=etype(kind),
            scope=EnergyMeter.SCOPE_LINE,
            line=self.line,
            gateway=self.gw,
            unit="kWh",
        )

    def seed_hourly_readings(self, meter: EnergyMeter, first: datetime, count: int) -> None:
        """按现场节奏（每小时 :04 一条）灌读数，每小时恰好用量 = STEP。"""
        for i in range(count):
            EnergyRaw.objects.create(
                meter=meter,
                line=meter.line,
                energy_type=meter.energy_type,
                collect_time=first + timedelta(hours=i),
                reading=Decimal("1000") + self.STEP * i,
                ok=True,
                raw="test",
            )

    def aggregate_day_27(self, meter: EnergyMeter) -> None:
        for h in range(24):
            aggregate_hour(self.DAY, h, meters=[meter])

    def test_实时窗口不跨过小时边界(self):
        """进行中的参考值只能在本小时内取数，不许一路读到明天。"""
        m = self.meter()
        # 09-28 09:04 / 09:34 两条 → 本小时已用 5；10:04 那条必须被排除
        for i, (minute, value) in enumerate([(4, "1000"), (34, "1005"), (64, "1010")]):
            EnergyRaw.objects.create(
                meter=m,
                line=m.line,
                energy_type=m.energy_type,
                collect_time=datetime(2026, 9, 28, 9, 0) + timedelta(minutes=minute),
                reading=Decimal(value),
                ok=True,
                raw="test",
            )
        res = current_hour_usage(m, now=datetime(2026, 9, 28, 9, 40))
        self.assertIsNotNone(res)
        self.assertEqual(res["usage"], Decimal("5.000"))

    def test_历史日不足两条读数时不给参考值(self):
        """as-of=09-27 23:59:59 时本小时只有 23:04 一条 → None（旧实现会减到今天）。"""
        m = self.meter()
        self.seed_hourly_readings(m, datetime(2026, 9, 26, 22, 4), 36)
        self.assertIsNone(current_hour_usage(m, now=self.AS_OF))

    def test_历史日23点走聚合表(self):
        m = self.meter()
        self.seed_hourly_readings(m, datetime(2026, 9, 26, 23, 4), 26)
        self.aggregate_day_27(m)

        data = build_line(m.line, self.AS_OF, closed=True)
        h23 = data["hours"][23]
        self.assertEqual(h23["kwh"], 10.0)
        self.assertEqual(h23["quality"], EnergyHourly.Q_OK)
        self.assertFalse(h23.get("partial"))
        # 24 小时全部计入，日累计 = 24 × 10
        self.assertEqual(data["today"]["kwh"], 240.0)
        self.assertEqual(data["today"]["kwhCoveredHours"], 24)

    def test_快照显式声明没有进行中的小时(self):
        m = self.meter()
        snap = build_overview(self.AS_OF, closed=True)
        self.assertIsNone(snap["workshop"]["ongoingHour"])
        self.assertTrue(snap["workshop"]["dayClosed"])
        # 锚点仍留在 23：界面按它排序、写「已完成 24 点」
        self.assertEqual(snap["workshop"]["currentHour"], 23)

    def test_实时快照的进行中小时就是当前小时(self):
        m = self.meter()
        snap = build_overview(self.REAL_NOW)
        self.assertEqual(snap["workshop"]["ongoingHour"], self.REAL_NOW.hour)
        self.assertIsNone(snap["workshop"].get("dayClosed"))

    def test_大屏与后台逐格相等(self):
        """同源同口径的硬保证：大屏每小时数字 == /api/energy/range 的线别序列。"""
        m = self.meter()
        self.seed_hourly_readings(m, datetime(2026, 9, 26, 23, 4), 26)
        self.aggregate_day_27(m)

        big = [h["kwh"] for h in build_line(m.line, self.AS_OF, closed=True)["hours"]]
        rng = ranges.usage_range(
            self.DAY,
            self.DAY,
            granularity="hour",
            scope="all",
            now=self.REAL_NOW,
            start_at=datetime(2026, 9, 27, 0),
            end_at=datetime(2026, 9, 27, 23),
        )
        admin = next(s["values"] for s in rng["series"] if s["key"] == "A1")
        self.assertEqual(len(admin), 24)
        self.assertEqual(big, admin)
        self.assertEqual(rng["totals"]["kwh"], 240.0)


class YesterdayRollupTests(TestCase):
    """v1.4.1（B-523）：日总计必须包含第 23 小时。

    昨天 23 点的差值要等**今天 00:04** 采到跨日边界读数才算得出来，
    那一刻 `now.date()` 已经是今天了 —— 只刷今天的汇总，
    每一天就永远少最后一个小时（现场实测覆盖数恒为 23，日合计 377.4 对不上小时表的 385.5），
    而月 / 年是按日行累加的，三个尺度一起偏小。
    """

    YESTERDAY = datetime(2026, 9, 27).date()
    NOW = datetime(2026, 9, 28, 0, 34)

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-T", name="测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )

    def test_刷新滚动窗口会把昨天的第23小时算进日总计(self):
        meter = EnergyMeter.objects.create(
            code="E-A1-01",
            name="一线电表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=self.line,
            gateway=self.gw,
            unit="kWh",
        )
        # 09-27 00:04 … 09-28 00:04 共 25 条，每小时恰好 10 kWh
        for i in range(25):
            EnergyRaw.objects.create(
                meter=meter,
                line=meter.line,
                energy_type=meter.energy_type,
                collect_time=datetime(2026, 9, 27, 0, 4) + timedelta(hours=i),
                reading=Decimal("1000") + Decimal("10") * i,
                ok=True,
                raw="test",
            )
        for h in range(24):
            aggregate_hour(self.YESTERDAY, h, meters=[meter])

        S.refresh_rollups(self.NOW)

        row = EnergyDaily.objects.get(stat_date=self.YESTERDAY, line=self.line, energy_type=etype())
        self.assertEqual(row.consumption, Decimal("240.000"))
        self.assertEqual(row.covered_count, 24)
        self.assertEqual(row.quality, PeriodSummary.Q_OK)


class OpenPeriodRollupTests(TestCase):
    """v1.4.2：周期汇总改成每天结算一次之后的取数规则。

    一句话：**已结束的周期读汇总表，进行中的周期（今天 / 本月 / 本年）从小时表现算**。
    两头都要锁 —— 少第一条，00:30 的结算就白做；少第二条，大屏与后台的
    「今日 / 本月」会整天不动（汇总表在当天早上就定格了），值班人员以为系统卡死。
    """

    PER = Decimal("10")

    @classmethod
    def setUpTestData(cls):
        cls.now = datetime.now().replace(minute=0, second=0, microsecond=0)
        cls.today = cls.now.date()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.line = ProductionLine.objects.create(line_code="A1", line_name="一线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-T", name="测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-A1-01",
            name="一线电表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            unit="kWh",
        )

    def hourly(self, day: object, hours: range) -> None:
        for h in hours:
            EnergyHourly.objects.create(
                meter=self.meter,
                line=self.line,
                energy_type=etype(),
                stat_date=day,
                stat_hour=h,
                consumption=self.PER,
                unit="kWh",
                quality=EnergyHourly.Q_OK,
            )

    def daily_row(self, day: object, value: str, covered: int = 24) -> None:
        EnergyDaily.objects.create(
            line=self.line,
            energy_type=etype(),
            stat_date=day,
            consumption=Decimal(value),
            unit="kWh",
            covered_count=covered,
            expected_count=24,
            quality=PeriodSummary.Q_OK,
        )

    def test_当期不读汇总表_汇总行再旧也不影响(self):
        """今天的日行故意写成 999（模拟 00:30 结算后一整天没再刷）→ 当期必须给小时表的值。"""
        done = range(self.now.hour)  # 已完成的小时
        self.hourly(self.today, done)
        self.daily_row(self.today, "999")

        key = S.make_key(S.DIM_DAY, self.today)
        row = S.period_rows(S.DIM_DAY, S.SCOPE_LINE, self.line, [key], self.now)[key]
        self.assertEqual(row.kwh, self.PER * len(done))
        self.assertNotEqual(row.kwh, Decimal("999"))
        # 进行中的那一小时还没有小时行 → 覆盖度如实小于应有
        self.assertLess(row.covered_hours, row.expected_hours)

    def test_已结束的周期读汇总表(self):
        """昨天的行是给定的结算值 —— 现算会得到不同结果，用它证明「读的是表」。"""
        self.hourly(self.yesterday, range(24))  # 240
        self.daily_row(self.yesterday, "777")

        key = S.make_key(S.DIM_DAY, self.yesterday)
        row = S.period_rows(S.DIM_DAY, S.SCOPE_LINE, self.line, [key], self.now)[key]
        self.assertEqual(row.kwh, Decimal("777"))

    def test_昨天的行没结算完整时退回现算(self):
        """00:00—00:30 的窗口：昨天的日行还是前天写的残缺值 → 必须现算，不能空一晚。

        `EEP-Rollup` 没注册或跑失败时也是靠这条兜住：数字仍然正确，只是多扫一次小时表。
        """
        before_midnight = datetime.now().replace(hour=0, minute=12, second=0, microsecond=0)
        y = before_midnight.date() - timedelta(days=1)
        self.hourly(y, range(24))  # 240
        self.daily_row(y, "100", covered=10)  # 前天 00:30 那次结算的残缺值

        key = S.make_key(S.DIM_DAY, y)
        row = S.period_rows(S.DIM_DAY, S.SCOPE_LINE, self.line, [key], before_midnight)[key]
        self.assertEqual(row.kwh, self.PER * 24)
        self.assertNotEqual(row.kwh, Decimal("100"))

    def test_更早的周期即使残缺也不回退(self):
        """回退只对今天 / 昨天开放 —— 更早的行按结算值交付，避免长序列反复扫小时表。"""
        long_ago = self.now.date() - timedelta(days=3)
        self.hourly(long_ago, range(24))  # 240
        self.daily_row(long_ago, "777", covered=10)

        key = S.make_key(S.DIM_DAY, long_ago)
        row = S.period_rows(S.DIM_DAY, S.SCOPE_LINE, self.line, [key], self.now)[key]
        self.assertEqual(row.kwh, Decimal("777"))

    def test_大屏与后台在当期同样相等(self):
        """当期现算之后，大屏的「今天那一根」必须仍等于后台日粒度序列的同一格。"""
        self.hourly(self.today, range(self.now.hour))
        self.daily_row(self.today, "999")  # 谁读汇总表谁就错

        big = build_line(self.line, self.now)["daily30"][-1]
        rng = ranges.usage_range(
            self.today, self.today, granularity="day", scope="all", now=self.now
        )
        admin = next(s["values"] for s in rng["series"] if s["key"] == "A1")[-1]
        self.assertEqual(big["label"], f"{self.today:%m-%d}")
        self.assertEqual(big["kwh"], admin)

    def test_采集命令默认不再刷汇总(self):
        """每天 00:30 的 `summarize --rollup` 才是结算入口；采集命令只出小时数。"""
        opts = collect.Command().create_parser("x", "y").parse_args([])
        self.assertFalse(opts.summarize)
        self.assertFalse(hasattr(opts, "no_summary"))

    def test_每日结算入口存在(self):
        from apps.energy.management.commands import summarize as summarize_cmd

        opts = summarize_cmd.Command().create_parser("x", "y").parse_args(["--rollup"])
        self.assertTrue(opts.rollup)


class GatewayHeartbeatTests(TestCase):
    """v1.4.4：网关「离线」判定必须跟着采集节奏走。

    现场事故：采集节奏是每小时一次（整点后 offset 秒采），心跳间隔天然就是 60 分钟，
    而判定写死 5 分钟 —— 于是网关列表在约 92% 的时间里显示「离线」，
    同一时刻计量点位却全部「在线」（点位判的是 `last_ok`，与心跳无关）。
    更糟的是数据中心那张卡用另一套口径（有过任意一条采集日志即在线），
    同一个「在线网关」两处给出不同答案。
    """

    def setUp(self):
        self.gw = EnergyGateway.objects.create(
            code="GW-HB",
            name="心跳测试网关",
            kind=EnergyGateway.KIND_MODBUS,
            host="10.0.0.1",
            port=8891,
            enabled=True,
            interval_seconds=60,
        )

    def test_阈值按hourly节奏自适应(self):
        """未配置时：hourly → 90 分钟（一个采集周期 + 30 分钟宽限）。"""
        with mock.patch.object(conf, "get", return_value=None):
            self.assertEqual(api.gateway_offline_after(), timedelta(minutes=90))

    def test_阈值可被配置覆盖(self):
        def fake_get(key, default=None):
            return 45 if key == "collect.gateway_offline_minutes" else default

        with mock.patch.object(conf, "get", side_effect=fake_get):
            self.assertEqual(api.gateway_offline_after(), timedelta(minutes=45))

    def test_心跳在一个采集周期内算在线(self):
        """最后心跳 15:04、现在 15:20（差 16 分钟）→ 在线。

        这正是现场那次误判：5 分钟阈值把它判成离线，而链路其实是好的。
        """
        with mock.patch.object(api, "datetime") as dt:
            dt.now.return_value = datetime(2026, 10, 9, 15, 20)
            obj = api.gateway_json(self.gw, last_heartbeat=datetime(2026, 10, 9, 15, 4))
        self.assertEqual(obj["status"], "online")
        self.assertEqual(obj["lastHeartbeat"], "2026-10-09 15:04:00")

    def test_心跳超过阈值算离线(self):
        with mock.patch.object(api, "datetime") as dt:
            dt.now.return_value = datetime(2026, 10, 9, 17, 30)
            obj = api.gateway_json(self.gw, last_heartbeat=datetime(2026, 10, 9, 15, 4))
        self.assertEqual(obj["status"], "offline")

    def test_从没有过心跳算离线(self):
        obj = api.gateway_json(self.gw, last_heartbeat=None)
        self.assertEqual(obj["status"], "offline")
        self.assertEqual(obj["lastHeartbeat"], "")

    def test_数据中心与网关列表用同一个在线集合(self):
        """同一指标只能有一个来源：避免「卡片说 5/5、列表全离线」这类自相矛盾。"""
        EnergyCollectLog.objects.create(
            gateway=self.gw,
            started_at=datetime.now() - timedelta(minutes=16),
            total=1,
            ok_count=1,
            fail_count=0,
        )
        self.assertIn(self.gw.id, api._online_gateway_ids())

        EnergyCollectLog.objects.all().delete()
        EnergyCollectLog.objects.create(
            gateway=self.gw,
            started_at=datetime.now() - timedelta(hours=5),
            total=1,
            ok_count=1,
            fail_count=0,
        )
        self.assertNotIn(self.gw.id, api._online_gateway_ids())


class ScriptedCollector(BaseCollector):
    """脚本化采集器：按预设序列返回成功 / 失败，用来验证重试**真的发生了**。

    故意继承 `BaseCollector` 而不是 mock 掉整条读流程，是为了让
    `_read_with_retry` 那段真实逻辑参与测试 —— 之前 `_try_read` 直接调 `read_one`，
    把 `retry_times` 静默吞掉（参数传了、重试没发生），mock 掉反而看不出来。
    """

    def __init__(self, gateway, script, **kwargs):
        super().__init__(gateway, **kwargs)
        self.script = list(script) or [True]
        self.calls = 0

    def prepare(self) -> str:
        return ""

    def read_one(self, meter) -> CollectResult:
        ok = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if ok:
            return CollectResult(ok=True, value=Decimal("123.4"), raw="slave=8 addr=1000 regs=04d2")
        return CollectResult(ok=False, error="脚本指定失败")


def scripted_factory(script, captured: dict):
    """造一个替换 `api.build_collector` 的工厂，顺手记下传进来的超时/重试参数。"""

    def factory(gateway, *, timeout_ms=3000, retry_times=2, retry_backoff=0.5):
        captured.update(timeout_ms=timeout_ms, retry_times=retry_times, retry_backoff=retry_backoff)
        return ScriptedCollector(
            gateway, script, timeout_ms=timeout_ms, retry_times=retry_times, retry_backoff=retry_backoff
        )

    return factory


class MeterCurrentTests(TestCase):
    """v1.4.4 新增：读取当前值 `POST /api/admin/meters/{id}/current`。

    这个接口存在的唯一理由是**不落库**。`last_value / last_at / last_ok` 是采集链路的
    健康信号，「仪表读数查询」页排障完全依赖它；随手看一眼当前读数不能把它刷成
    「刚刚成功」—— 否则断讯的表在列表里显示在线，之后谁也说不清是链路恢复了
    还是有人点过按钮。所以下面的断言里，**「什么都没写」和「读数正确」一样重要**。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A7", line_name="七线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-CUR",
            name="读取测试网关",
            kind=EnergyGateway.KIND_MODBUS,
            host="127.0.0.1",
            port=7,
            timeout_ms=1200,
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-CUR-01",
            name="读取测试表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            slave=8,
            register=1000,
            registers=2,
            unit="kWh",
        )

    def setUp(self):
        self.captured: dict = {}

    def post(self, payload=None, pk=None):
        body = json.dumps({} if payload is None else payload)
        return Client().post(
            f"/api/admin/meters/{self.meter.id if pk is None else pk}/current",
            data=body,
            content_type="application/json",
        )

    # ── 读数本身 ───────────────────────────────────────────
    def test_返回当前读数与原始报文(self):
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
            res = self.post()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["value"], 123.4)
        self.assertEqual(body["raw"], "slave=8 addr=1000 regs=04d2")
        self.assertEqual(body["unit"], "kWh")
        self.assertEqual(body["meter"], "E-CUR-01")
        self.assertEqual(body["gatewayCode"], "GW-CUR")
        self.assertIn("从站 8", body["address"])
        self.assertGreaterEqual(body["durationMs"], 0)

    def test_失败时如实给错误而不是500(self):
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([False], self.captured)):
            res = self.post()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertIsNone(body["value"])
        self.assertEqual(body["error"], "脚本指定失败")

    def test_采集器抛异常也返回200并写清原因(self):
        boom = mock.Mock(side_effect=RuntimeError("设备爆炸了"))
        with mock.patch.object(api, "build_collector", boom):
            res = self.post()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertIn("RuntimeError", body["error"])
        self.assertIn("设备爆炸了", body["error"])

    # ── 不落库（本接口的核心承诺）──────────────────────────
    def test_读取当前值不写任何表字段(self):
        self.meter.last_value = Decimal("100.0")
        self.meter.last_at = datetime(2026, 10, 9, 15, 4, 0)
        self.meter.last_ok = True
        self.meter.last_error = ""
        self.meter.save(update_fields=["last_value", "last_at", "last_ok", "last_error"])
        before = EnergyMeter.objects.get(pk=self.meter.pk)
        snapshot = (before.last_value, before.last_at, before.last_ok, before.last_error, before.updated_at)
        raw_before = EnergyRaw.objects.count()
        logs_before = EnergyCollectLog.objects.count()

        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
            res = self.post()

        self.assertTrue(res.json()["ok"])
        self.assertFalse(res.json()["persisted"])

        after = EnergyMeter.objects.get(pk=self.meter.pk)
        self.assertEqual(
            (after.last_value, after.last_at, after.last_ok, after.last_error, after.updated_at),
            snapshot,
            "读取当前值不得更新点位的最近读数 / 最近状态",
        )
        self.assertEqual(EnergyRaw.objects.count(), raw_before, "读取当前值不得写入 energy_raw")
        self.assertEqual(EnergyCollectLog.objects.count(), logs_before, "读取当前值不得写采集日志")

    def test_读取失败同样不落库(self):
        """失败也不能把 last_ok 改成 False / last_error 写上 —— 那同样是留痕。"""
        self.meter.last_ok = True
        self.meter.last_error = ""
        self.meter.save(update_fields=["last_ok", "last_error"])

        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([False], self.captured)):
            self.post()

        after = EnergyMeter.objects.get(pk=self.meter.pk)
        self.assertTrue(after.last_ok)
        self.assertEqual(after.last_error, "")

    # ── 超时与重试 ─────────────────────────────────────────
    def test_失败会按retryTimes重试并最终成功(self):
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([False, True], self.captured)):
            res = self.post({"retryTimes": 1})
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["attempts"], 2, "第一次失败后应重试一次再成功")

    def test_重试用尽才报失败(self):
        factory_calls: list[ScriptedCollector] = []

        def factory(gateway, *, timeout_ms=3000, retry_times=2, retry_backoff=0.5):
            inst = ScriptedCollector(
                gateway, [False], timeout_ms=timeout_ms, retry_times=retry_times, retry_backoff=retry_backoff
            )
            factory_calls.append(inst)
            return inst

        with mock.patch.object(api, "build_collector", side_effect=factory):
            res = self.post({"retryTimes": 2})

        body = res.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["attempts"], 3, "retryTimes=2 → 总共尝试 3 次")
        self.assertEqual(factory_calls[0].calls, 3)

    def test_未指定时用网关超时与配置重试次数(self):
        with mock.patch.object(conf, "get", side_effect=lambda k, d=None: 3 if k == "collect.retry_times" else d):
            with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
                res = self.post()
        # 网关自己配了 1200ms，优先于全局默认
        self.assertEqual(self.captured["timeout_ms"], 1200)
        self.assertEqual(self.captured["retry_times"], 3)
        self.assertEqual(res.json()["timeoutMs"], 1200)
        self.assertEqual(res.json()["retryTimes"], 3)

    def test_请求给的值会被裁剪到安全区间(self):
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
            res = self.post({"timeoutMs": 99999, "retryTimes": 99})
        body = res.json()
        # 最坏耗时 = 超时 ×(1+重试) 必须留在一次 HTTP 请求能承受的范围内
        self.assertEqual(body["timeoutMs"], 10000)
        self.assertEqual(body["retryTimes"], 5)
        self.assertEqual(self.captured["timeout_ms"], 10000)
        self.assertEqual(self.captured["retry_times"], 5)

    def test_非法数值退回默认而不是500(self):
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
            res = self.post({"timeoutMs": "abc", "retryTimes": None})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.captured["timeout_ms"], 1200)

    # ── 其它 ───────────────────────────────────────────────
    def test_停用的点位也允许读取(self):
        """停用表往往正是要排障的那块，读一眼不该被拦住（不落库所以没有副作用）。"""
        meter = EnergyMeter.objects.create(
            code="E-CUR-OFF",
            name="停用测试表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=self.line,
            gateway=self.gw,
            slave=9,
            register=1000,
            unit="kWh",
            enabled=False,
        )
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([True], self.captured)):
            res = self.post(pk=meter.id)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["enabled"])
        self.assertTrue(res.json()["ok"])

    def test_计量点不存在返回404(self):
        res = self.post(pk=999999)
        self.assertEqual(res.status_code, 404)

    def test_试采路径同样走了重试(self):
        """`_try_read` 曾经直接调 `read_one`，把重试静默吞掉（v1.4.4 修）。

        这里锁住「测试连接 / 试采」共用的那条路径：传了 retry_times 就必须真的重试。
        """
        with mock.patch.object(api, "build_collector", side_effect=scripted_factory([False, True], self.captured)):
            result = api._try_read(self.gw, [self.meter], timeout_ms=900, retry_times=1)
        self.assertTrue(result["items"][0]["ok"])
        self.assertEqual(result["items"][0]["attempts"], 2)
        self.assertEqual(self.captured["timeout_ms"], 900)
