"""采集与聚合的单元测试。

重点覆盖**小时差值的每一条异常分支** —— 这几条决定了库里数字可不可信：
缺数绝不补 0、累计值回退判换表、超阈值判尖峰、端点偏离整点判 partial。
跑法：python manage.py test apps.energy
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase

from apps.energy.collectors.base import BaseCollector, CollectResult, get_path
from apps.energy.collectors.modbus_rtu import ModbusRtuOverTcpCollector, decode_registers
from apps.energy.management.commands import collect
from apps.energy.management.commands.ingest_production import write_production
from apps.energy import api
from apps.energy.models import (
    EnergyAlarm,
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
from apps.energy.services import alerts
from apps.energy.services import ranges, registry, slots
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
        v1.5.2：哨兵行从 1 条变 N 条（每车间一条），编码由 WORKSHOP 改为 WS-CARB，
        所以这里改用 `default_workshop()` 取当前唯一在跑的车间节点。
        """
        ws = ProductionLine.default_workshop()
        self.assertIsNotNone(ws, "测试库缺少车间节点 —— 检查迁移 0009 是否执行")
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
        # 车间节点由迁移 0009 建好（编码 WS-CARB），这里取用即可
        cls.ws = ProductionLine.default_workshop()
        assert cls.ws is not None, "测试库缺少车间节点 —— 检查迁移 0009"
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
        # v1.5.2：多车间树 —— node_type 才是分类字段，line_type 只是兼容镜像。
        # 只写 line_type 会让 is_workshop / workshops_qs() 落空（node_type 默认 line）。
        cls.workshop = ProductionLine.objects.create(
            line_code="WS", line_name="车间",
            node_type=ProductionLine.NODE_WORKSHOP, line_type=ProductionLine.TYPE_WORKSHOP,
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
        # v1.5.2：多车间树 —— node_type 才是分类字段，line_type 只是兼容镜像。
        # 只写 line_type 会让 is_workshop / workshops_qs() 落空（node_type 默认 line）。
        cls.workshop = ProductionLine.objects.create(
            line_code="WS", line_name="车间",
            node_type=ProductionLine.NODE_WORKSHOP, line_type=ProductionLine.TYPE_WORKSHOP,
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
        """未配置时：hourly → (整点偏移 + 60 分钟) × 1.5，保底 90 分钟。

        v1.5.1 起真实一跳按 offset 算：平均 60 分钟、最坏 61 分钟（3660s），
        阈值必须**大于**最坏一跳，否则正经跑着的网关会周期性闪「离线」。
        """
        def fake_get(key, default=None):
            return 60 if key == "collect.hourly_offset_seconds" else default

        with mock.patch.object(conf, "get", side_effect=fake_get):
            limit = api.gateway_offline_after()
        # (60s 偏移 + 3600s) / 60 = 61 分钟 → ×1.5 = 91.5 分钟（int 截断后 91 分钟）
        self.assertEqual(limit, timedelta(minutes=91))
        self.assertGreater(limit.total_seconds(), 3600 + 60, "阈值必须大于最坏一跳")

    def test_偏移变大时阈值跟着放宽(self):
        """offset 调大 → 一跳变长 → 阈值也要跟着放宽，不然又会误判离线。"""
        def fake_get(key, default=None):
            return 600 if key == "collect.hourly_offset_seconds" else default

        with mock.patch.object(conf, "get", side_effect=fake_get):
            limit = api.gateway_offline_after()
        self.assertGreater(limit.total_seconds(), 3600 + 600)

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


class NewGatewayOnlineTests(TestCase):
    """v1.5.1（B-537）：刚配好的网关「测试连接成功」却一直显示离线。

    现场现象：新增「一期弱电房274-D」（`GW-LORA-1`），点「测试连接」逐点读取成功，
    但网关列表的状态仍是「离线」、最后心跳空白，最长要等约一小时才变过来 ——
    现场自然会反复怀疑设备 / 配置。

    根因：网关在线只看**采集日志**（`EnergyCollectLog`），而那是**整轮**采集才写的，
    节奏是 hourly。新网关在下一轮整点采集之前没有任何日志 → 判离线。
    可这一时刻点位试采分明是成功的（报文都收回来了），网关**确实**是通的。

    修复：把「点位读取成功」也当成网关级心跳，两个信号取并集。

    ⚠ 但**不能**拿 `last_at` 当心跳 —— 试采**失败**也会写它
      （`admin_meter_test` 的失败分支），那等于「一次失败反而证明网关在线」。
      所以只认 `last_ok=True` 的 `last_at`。下面 `test_试采失败不证明在线` 锁住这条。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A9", line_name="九线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-NEW-ONLINE",
            name="新装网关",
            kind=EnergyGateway.KIND_MODBUS,
            host="10.0.0.9",
            port=8888,
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-NEW-01",
            name="新装表",
            energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE,
            line=cls.line,
            gateway=cls.gw,
            slave=11,
            register=0,
            enabled=True,
        )

    def _status(self):
        hb = api._gateway_heartbeats().get(self.gw.id)
        return api.gateway_json(self.gw, point_count=1, last_heartbeat=hb)["status"]

    def _mark_ok(self, at=None):
        self.meter.last_at = at or datetime.now()
        self.meter.last_ok = True
        self.meter.last_error = ""
        self.meter.save(update_fields=["last_at", "last_ok", "last_error"])

    # ── 核心：新网关的在线判定 ─────────────────────────────
    def test_新网关无任何信号时算离线(self):
        """刚建好、还没试采过 → 没有证据说它通，只能显示离线。"""
        self.assertEqual(self._status(), "offline")

    def test_试采成功后立即可见在线而不必等整轮采集(self):
        """这条就是现场反馈的场景：没有任何采集日志，但点位试采成功。"""
        self.assertEqual(EnergyCollectLog.objects.filter(gateway=self.gw).count(), 0)
        self._mark_ok()
        self.assertEqual(self._status(), "online")

    def test_在线集合也包含这类新网关(self):
        """数据中心那张「在线网关」卡片必须与列表页一致（不能一个在线一个离线）。"""
        self._mark_ok()
        self.assertIn(self.gw.id, api._online_gateway_ids())

    def test_试采失败不证明在线(self):
        """试采失败也会写 `last_at`，不能因此判在线 —— 那是把「读不到」当成「通」。

        正是这条区分了「点位级心跳必须带 `last_ok` 条件」，而不是直接取 `Max(last_at)`。
        """
        self.meter.last_at = datetime.now()
        self.meter.last_ok = False
        self.meter.last_error = "无响应"
        self.meter.save(update_fields=["last_at", "last_ok", "last_error"])
        self.assertEqual(self._status(), "offline")
        self.assertNotIn(self.gw.id, api._online_gateway_ids())

    def test_停用点位不算数(self):
        """停用点位不参与采集，它的历史成功不该长期把网关「撑」成在线。"""
        self._mark_ok()
        self.assertEqual(self._status(), "online")
        self.meter.enabled = False
        self.meter.save(update_fields=["enabled"])
        self.assertEqual(self._status(), "offline")

    def test_上次成功超过阈值仍算离线(self):
        """并集只解决「谁更新」，不放松时限：太久没成功照样离线。"""
        self._mark_ok(at=datetime.now() - timedelta(hours=3))
        self.assertEqual(self._status(), "offline")

    def test_采集日志比点位更新时取采集日志(self):
        """两个信号取较新者，任一方新鲜都算在线。"""
        self._mark_ok(at=datetime.now() - timedelta(hours=3))  # 点位陈旧
        EnergyCollectLog.objects.create(
            gateway=self.gw,
            started_at=datetime.now() - timedelta(minutes=5),  # 采集日志新鲜
            total=1,
            ok_count=1,
            fail_count=0,
        )
        self.assertEqual(self._status(), "online")

    def test_网关列表接口直接反映在线(self):
        """走真实接口：这是现场点开页面看到的那条数据。"""
        self._mark_ok()
        res = Client().get("/api/admin/gateways")
        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.json() if r["code"] == "GW-NEW-ONLINE")
        self.assertEqual(row["status"], "online")
        self.assertTrue(row["lastHeartbeat"], "心跳不该是空白")



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


class GatewayFieldContractTests(TestCase):
    """v1.5.1（B-535）：网关字段名前后端不一致，IP 被**静默丢弃**。

    现场现象：后台新增串口服务器网关，**端口写进去了、IP 那一格永远是空的**。
    根因是前端提交 `ip`（那一列就叫「IP 地址」），而模型字段叫 `host`，
    别名表 `GATEWAY_ALIAS` 漏了 `ip → host`；`_apply()` 对认不出的字段
    **不报错、不告警，直接跳过** —— 接口照样返回 201，所以现场只会以为
    「是不是我 IP 填错了」，而实际上值在接口层就被扔了。

    这组测试锁住两件事：
      ① `ip` 必须能落进 `host`（写入路径）；
      ② 前端能提交的每个字段都必须有去处（防止下一次字段名漂移再静默丢）。
    """

    def setUp(self):
        self.client = Client()

    def post(self, payload):
        return self.client.post(
            "/api/admin/gateways", data=json.dumps(payload), content_type="application/json"
        )

    def base_payload(self, **over):
        payload = {
            "code": "GW-CONTRACT",
            "name": "契约测试网关",
            "kind": EnergyGateway.KIND_MODBUS,
            "ip": "10.9.9.9",
            "port": 8899,
            "baseUrl": "",
            "timeoutMs": 1500,
            "intervalSeconds": 60,
            "enabled": True,
            "remark": "",
        }
        payload.update(over)
        return payload

    # ── 写入路径 ───────────────────────────────────────────
    def test_新增网关的ip能落库(self):
        """这条断言直接对应现场现象：曾经 ip 进库后是空串。"""
        res = self.post(self.base_payload())
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["ip"], "10.9.9.9")
        self.assertEqual(EnergyGateway.objects.get(code="GW-CONTRACT").host, "10.9.9.9")

    def test_新增网关的endpoint由ip与端口拼出(self):
        """端点是日志与「测试连接」用的地址，IP 丢了它就成了「:8899」。"""
        res = self.post(self.base_payload())
        self.assertEqual(res.json()["endpoint"], "10.9.9.9:8899")

    def test_修改网关时ip同样生效(self):
        gw = EnergyGateway.objects.create(
            code="GW-CONTRACT", name="契约测试网关", kind=EnergyGateway.KIND_MODBUS,
            host="1.1.1.1", port=1,
        )
        res = self.client.put(
            f"/api/admin/gateways/{gw.id}",
            data=json.dumps(self.base_payload(ip="2.2.2.2", port=2222)),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        gw.refresh_from_db()
        self.assertEqual(gw.host, "2.2.2.2")
        self.assertEqual(gw.port, 2222)

    def test_HTTP类型网关的接口根地址不受影响(self):
        """顺带确认 baseUrl 这条别名没被 ip 的修复带坏。"""
        res = self.post(
            self.base_payload(
                kind=EnergyGateway.KIND_HTTP, ip="", baseUrl="http://47.111.74.100:8980"
            )
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["baseUrl"], "http://47.111.74.100:8980")

    # ── 契约自检 ───────────────────────────────────────────
    def test_前端字段都有去处(self):
        """前端能提交的字段，必须每一个都能落到模型字段上（空列表 = 契约完好）。"""
        self.assertEqual(api.assert_alias_covers_frontend(), [])

    def test_自检能抓出漏登记的别名(self):
        """自检本身要有效：故意摘掉 ip 的映射，必须能报出来。

        没有这条，「自检永远返回空列表」和「契约真的完好」看起来一模一样。
        """
        broken = {k: v for k, v in api.GATEWAY_ALIAS.items() if k != "ip"}
        with mock.patch.object(api, "GATEWAY_ALIAS", broken):
            missing = api.assert_alias_covers_frontend()
        self.assertIn("gateway.ip → ip", missing)


class MeterSaveValidationTests(TestCase):
    """v1.5.1（B-536）：新增计量点带空 `installDate` 直接 HTTP 500。

    现场现象：新增「车间总表」「辅机」报 500。看起来像是这两类点位的问题，
    其实是**所有**点位 —— 前端表单的 `installDate` 默认就是空串 `""`，
    而 `_save_meter()` 把它原样塞给 `DateField`，Django 对空串抛 ValidationError
    （它只认 `None` 或不带这个键），未被捕获 → 500。

    与 scope / role 完全无关：只要表单带着 `installDate` 来就必炸，
    所以现场会觉得「加车间总表就报错」，而加电表时可能没细看表单。
    """

    def setUp(self):
        self.client = Client()
        self.line = ProductionLine.objects.create(line_code="A8", line_name="八线")
        self.gw = EnergyGateway.objects.create(
            code="GW-VAL", name="校验测试网关", kind=EnergyGateway.KIND_MODBUS,
            host="127.0.0.1", port=1,
        )

    def post(self, payload):
        return self.client.post(
            "/api/admin/meters", data=json.dumps(payload), content_type="application/json"
        )

    def base_payload(self, **over):
        payload = {
            "code": "M-VAL-01",
            "name": "校验测试点位",
            "kind": EnergyType.ELECTRIC.lower() if False else "elec",
            "scope": "line",
            "role": "line",
            "lineCode": "A8",
            "gatewayId": self.gw.id,
            "slave": 1,
            "register": 1000,
            "registers": 2,
            "factor": 0.1,
            "unit": "kWh",
            "installDate": "",
        }
        payload.update(over)
        return payload

    # ── 现场两条报错路径 ────────────────────────────────────
    def test_车间总表带空安装日期能新增(self):
        """现场报错场景①：车间总表（scope=workshop, role=total）。"""
        res = self.post(
            self.base_payload(code="W-VAL-01", name="校验车间总表", kind="water",
                              scope="workshop", role="total", lineCode="", unit="t")
        )
        self.assertEqual(res.status_code, 201, res.content.decode("utf-8", "replace")[:300])
        meter = EnergyMeter.objects.get(code="W-VAL-01")
        self.assertIsNone(meter.install_date)
        self.assertEqual(meter.scope, EnergyMeter.SCOPE_WORKSHOP)
        # 车间总表挂车间节点（v1.5.2 起每车间一条，编码 WS-CARB），不是留空
        self.assertIsNotNone(meter.line)
        self.assertTrue(meter.line.is_workshop)

    def test_辅机带空安装日期能新增(self):
        """现场报错场景②：辅机（role=aux）。"""
        res = self.post(self.base_payload(code="E-VAL-AUX", name="校验辅机", role="aux"))
        self.assertEqual(res.status_code, 201, res.content.decode("utf-8", "replace")[:300])
        self.assertIsNone(EnergyMeter.objects.get(code="E-VAL-AUX").install_date)

    def test_空串与不带该键等价(self):
        """「没填」的两种表达（空串 / 不传）必须落成同一个结果：NULL。"""
        no_key = self.base_payload(code="M-VAL-A", name="不传日期")
        no_key.pop("installDate")
        self.assertEqual(self.post(no_key).status_code, 201)

        empty = self.base_payload(code="M-VAL-B", name="空串日期", installDate="")
        self.assertEqual(self.post(empty).status_code, 201)

        self.assertEqual(
            EnergyMeter.objects.get(code="M-VAL-A").install_date,
            EnergyMeter.objects.get(code="M-VAL-B").install_date,
        )

    def test_合法安装日期正常落库(self):
        res = self.post(self.base_payload(code="M-VAL-C", installDate="2026-03-15"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(str(EnergyMeter.objects.get(code="M-VAL-C").install_date), "2026-03-15")

    # ── 错误必须是 400 带原因，不能是 500 ──────────────────
    def test_非法安装日期返回400而不是500(self):
        res = self.post(self.base_payload(code="M-VAL-D", installDate="2026/13/45"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("安装日期", res.json()["detail"])

    def test_更新时也接受空安装日期(self):
        meter = EnergyMeter.objects.create(
            code="M-VAL-E", name="更新测试", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=self.gw,
            slave=2, register=1000, install_date=datetime(2026, 1, 1).date(),
        )
        res = self.client.put(
            f"/api/admin/meters/{meter.id}",
            data=json.dumps(self.base_payload(code="M-VAL-E", name="更新测试", installDate="")),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200, res.content.decode("utf-8", "replace")[:300])
        meter.refresh_from_db()
        self.assertIsNone(meter.install_date)

    def test_模型校验失败转成400而不是500(self):
        """`_save_meter` 里 Django 的 ValidationError 必须被接住转成 400。

        触发方式：`_save_meter` 只归一无日期的**字符串**，若调用方直接塞一个
        非法类型的日期对象（这里用 `datetime` 冒充），`DateField.to_python()`
        就会抛 ValidationError。这条走的是与「安装日期格式不对」不同的分支 ——
        它在 `m.save()` 里抛出，正是原先漏成 500 的那条路。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        meter = EnergyMeter.objects.create(
            code="M-VAL-F", name="模型校验", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=self.gw,
            slave=5, register=1000,
        )
        err = DjangoValidationError({"install_date": ["日期不合法"]})
        with mock.patch.object(EnergyMeter, "save", side_effect=err):
            with self.assertRaises(ValueError) as ctx:
                api._save_meter(meter, {"name": "模型校验"})
        self.assertIn("字段校验未通过", str(ctx.exception))
        self.assertIn("install_date", str(ctx.exception))

    def test_倍率写入正确而不是被默认值顶掉(self):
        """v1.5.1（B-535 同源）：前端叫 `factor`、模型叫 `scale`，别名漏登记时
        倍率会被**静默改成默认 1** —— 界面照样保存成功，读数却差一个量级。"""
        res = self.post(self.base_payload(code="M-VAL-G", factor=0.5))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["factor"], 0.5)
        self.assertEqual(EnergyMeter.objects.get(code="M-VAL-G").scale, Decimal("0.5"))


class WorkshopHierarchyTests(TestCase):
    """v1.5.2：车间→用能类型→设备 三级筛选 + 多车间树。

    这一族修的坑都属同一类：**两份数字各用一条规则，于是对不上**。
    现场核对时第一个结论必然是「系统算错了」，真正的病因反被淹掉。
    """

    @classmethod
    def setUpTestData(cls):
        cls.ws = ProductionLine.default_workshop()
        assert cls.ws is not None, "测试库缺少车间节点 —— 检查迁移 0009"
        cls.line = ProductionLine.objects.create(
            line_code="A1", line_name="一线", node_type=ProductionLine.NODE_LINE,
            line_type=ProductionLine.TYPE_LINE, parent=cls.ws,
        )
        cls.aux = ProductionLine.objects.create(
            line_code="AUX-AIR", line_name="辅机单元", node_type=ProductionLine.NODE_AUX,
            line_type=ProductionLine.TYPE_LINE, parent=cls.ws,
        )

    def test_多车间树_节点分类与过滤(self):
        """workshop / line / aux 三类必须各归其位：靠 node_type 而不是 line_type。"""
        self.assertTrue(self.ws.is_workshop)
        self.assertTrue(self.line.is_line)
        self.assertTrue(self.aux.is_aux)
        # 产线查询不能把车间行或辅机行混进来
        self.assertIn(self.line, list(ProductionLine.lines_qs()))
        self.assertNotIn(self.ws, list(ProductionLine.lines_qs()))
        self.assertNotIn(self.aux, list(ProductionLine.lines_qs()))

    def test_车间电力口径含产线与辅机(self):
        """车间电合计 = 本车间产线电表 + 本车间辅机类电表（v1.5.2 口径变更）。"""
        gw = EnergyGateway.objects.create(
            code="GW-H", name="层级测试", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        EnergyMeter.objects.create(
            code="E-H-LINE", name="一线表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电", device_name="生产线",
        )
        EnergyMeter.objects.create(
            code="E-H-AUX", name="辅机表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.aux, gateway=gw,
            slave=2, register=1000, usage_type="辅机用电", device_name="高压机",
        )
        codes = [m.code for m in ranges._workshop_meters()]
        # 辅机节点下的表也要进来（旧实现只认产线表，会把辅机漏掉）
        self.assertIn("E-H-LINE", codes)
        self.assertIn("E-H-AUX", codes)

    def test_三级筛选按维度取点(self):
        """workshop / usage_type / node_code 逐级收窄，任一级不传即不限。"""
        gw = EnergyGateway.objects.create(
            code="GW-H2", name="维度测试", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        EnergyMeter.objects.create(
            code="E-H2-LINE", name="一线表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电", device_name="生产线",
        )
        EnergyMeter.objects.create(
            code="E-H2-AUX", name="辅机表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.aux, gateway=gw,
            slave=2, register=1000, usage_type="辅机用电", device_name="高压机",
        )
        # 车间级：两块都取到
        self.assertEqual(len(ranges.meters_of(workshop="WS-CARB")), 2)
        # 按用能类型收窄
        self.assertEqual(
            [m.code for m in ranges.meters_of(usage_type="辅机用电")], ["E-H2-AUX"]
        )
        # 按节点收窄
        self.assertEqual([m.code for m in ranges.meters_of(node_code="A1")], ["E-H2-LINE"])

    def test_已接入的表缺采时合计为空而不是偏小值(self):
        """★ 核心不变量（加了未接入豁免后**依然成立**）：接入过的表缺采 → 合计必须为空。

        修这个函数前，totals 用 `Sum(consumption)`（有几块算几块），而序列用
        `_hour_matrix`（全表可信才算）—— 于是出现「图表全空、合计 16000 kWh」，
        用户只能得出「系统算错了」。

        ⚠ m2 在前一天出过可信值 = 已接入，本区间却缺采 → 这是真故障，必须继续
          否决整格。「豁免未接入的表」绝不能顺手把缺采也放过去，否则又回到偏小
          的部分和 —— 这条测试存在的意义就是卡住这个边界。
        """
        gw = EnergyGateway.objects.create(
            code="GW-I", name="一致性", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        m1 = EnergyMeter.objects.create(
            code="E-I-1", name="表1", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电",
        )
        m2 = EnergyMeter.objects.create(
            code="E-I-2", name="表2", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=2, register=1000, usage_type="生产线用电",
        )
        day = datetime(2026, 9, 20).date()
        # m2 前一天正常出过数 —— 它是「已接入」的表，因此有否决权
        EnergyHourly.objects.create(
            meter=m2, line=self.line, energy_type=m2.energy_type,
            stat_date=datetime(2026, 9, 19).date(), stat_hour=8,
            consumption=Decimal("7.0"), quality=EnergyHourly.Q_OK,
        )
        # 只给 m1 数据、m2 整天空缺 → 严格口径下合计必须为空
        # ⚠ energy_hourly 的 line / energy_type 是必填外键（不是从 meter 反推的），
        #   漏传会直接 IntegrityError: Column 'line_id' cannot be null
        for h in range(0, 5):
            EnergyHourly.objects.create(
                meter=m1, line=self.line, energy_type=m1.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("10.0"), quality=EnergyHourly.Q_OK,
            )
        data = ranges.usage_range(
            day, day, granularity="hour", scope="workshop", kind="elec",
            workshop="WS-CARB", node_code="A1",
        )
        vals = [v for v in data["series"][0]["values"] if v is not None]
        self.assertIsNone(
            data["totals"]["kwh"], "已接入的表缺采时合计必须是 None，不能是偏小的部分和"
        )
        self.assertEqual(vals, [], "缺采时序列也应全空（口径一致）")
        cov = data["coverage"]
        self.assertEqual(cov["missing"], ["E-I-2"], "缺采的表要报进 missing")
        self.assertEqual(cov["unconnected"], [], "它已接入，不该被归成未接入")

    def test_从未接入的表不否决同组其它表(self):
        """★ 新增不变量：登记了但从未出过可信值的表没有否决权。

        现场真实形态：车间进水总表登记了却还没接线 / 还没给 token，
        aggregate 每小时给它写一行 missing。旧口径下这块表让所在组永久全 None ——
        水没有数，一旦与电混进同一组（三级筛选未带 kind）电和汽也一起没有。

        新口径把它排除出参采集合，如实记进 coverage.unconnected，
        由告警引擎单独报「未接入」，而不是让它污染数字。
        """
        gw = EnergyGateway.objects.create(
            code="GW-K", name="未接入", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        m1 = EnergyMeter.objects.create(
            code="E-K-1", name="在跑的表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电",
        )
        EnergyMeter.objects.create(
            code="E-K-2", name="还没接线的表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=2, register=1000, usage_type="生产线用电",
        )
        day = datetime(2026, 9, 20).date()
        # 喂满整天 24 格：hour 粒度会生成 24 个桶，只喂几小时的话剩下的格子
        # 按严格口径就是「已接入却缺采」→ 合计必须 None（那是正确行为，
        # 会让本测试在验证一件根本没发生的事）
        for h in range(24):
            EnergyHourly.objects.create(
                meter=m1, line=self.line, energy_type=m1.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("10.0"), quality=EnergyHourly.Q_OK,
            )
        data = ranges.usage_range(
            day, day, granularity="hour", scope="workshop", kind="elec",
            workshop="WS-CARB", node_code="A1",
        )
        vals = data["series"][0]["values"]
        self.assertEqual(
            [v for v in vals if v is not None], [10.0] * 24,
            "未接入的表不该否决在跑的表",
        )
        self.assertEqual(data["totals"]["kwh"], 240.0, "合计 = 已接入表的和")
        cov = data["coverage"]
        self.assertEqual(cov["unconnected"], ["E-K-2"], "未接入要能单独报出来")
        self.assertEqual(cov["missing"], [])
        self.assertEqual(cov["connected"], 1)

    def test_中途接入的表不倒退否决历史(self):
        """只从「首次出可信值」那一格起参与合计。

        否则新装一块表会让上周的图表整体倒退成空值 —— 现场一定会当成
        「系统把历史数据弄丢了」。
        """
        gw = EnergyGateway.objects.create(
            code="GW-N", name="中途接入", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        m1 = EnergyMeter.objects.create(
            code="E-N-1", name="老表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电",
        )
        m2 = EnergyMeter.objects.create(
            code="E-N-2", name="新装表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=2, register=1000, usage_type="生产线用电",
        )
        day = datetime(2026, 9, 20).date()
        for h in range(24):
            EnergyHourly.objects.create(
                meter=m1, line=self.line, energy_type=m1.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("10.0"), quality=EnergyHourly.Q_OK,
            )
        # m2 从第 3 小时起才接入（其余格子没有它的行）
        for h in range(3, 24):
            EnergyHourly.objects.create(
                meter=m2, line=self.line, energy_type=m2.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("5.0"), quality=EnergyHourly.Q_OK,
            )
        data = ranges.usage_range(
            day, day, granularity="hour", scope="workshop", kind="elec",
            workshop="WS-CARB", node_code="A1",
        )
        vals = data["series"][0]["values"]
        self.assertEqual(vals[:3], [10.0] * 3, "m2 接入前的格子不该被它否决")
        self.assertEqual(vals[3:], [15.0] * 21, "接入后两块表一起算")
        self.assertEqual(data["totals"]["kwh"], 30.0 + 15.0 * 21, "合计含两段")

    def test_水未接入不连坐电与汽(self):
        """★ 跨能源类型必须互不否决：三级筛选未带 kind 时最易爆发。

        `meters_of` 不传 kind 时是「不限类型」的，旧实现把电表 + 水表 + 汽表
        丢进同一个「全员可信才算数」的矩阵 —— 一块没接的水表能让电也整段变 None，
        而且序列单位还跟着 `focus_meters[0].unit` 串成 m³。
        """
        gw = EnergyGateway.objects.create(
            code="GW-M", name="混组", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        em = EnergyMeter.objects.create(
            code="E-M-1", name="电表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电", device_name="生产线",
        )
        EnergyMeter.objects.create(
            code="W-M-1", name="水表", energy_type=etype(EnergyType.WATER),
            scope=EnergyMeter.SCOPE_WORKSHOP, line=self.ws, gateway=gw,
            slave=2, register=1000, unit="m\u00b3", usage_type="车间进水", device_name="生产线",
        )
        day = datetime(2026, 9, 20).date()
        for h in range(24):
            EnergyHourly.objects.create(
                meter=em, line=self.line, energy_type=em.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("10.0"), quality=EnergyHourly.Q_OK,
            )
        # 只按设备筛、不带 kind → 电与水会同时进焦点集合
        data = ranges.usage_range(
            day, day, granularity="hour", scope="all", device="生产线"
        )
        by_unit = {s["unit"]: s for s in data["series"]}
        self.assertIn("kWh", by_unit, "电表应单独成一条序列")
        self.assertEqual(
            [v for v in by_unit["kWh"]["values"] if v is not None], [10.0] * 24,
            "水未接入不该把电抹空",
        )
        self.assertEqual(data["totals"]["kwh"], 240.0)
        self.assertIn("m\u00b3", by_unit, "水也要有自己的序列，而不是混进电的组")
        self.assertTrue(
            all(v is None for v in by_unit["m\u00b3"]["values"]), "水仍未接入 → 一律 None"
        )
        self.assertEqual(data["totals"]["waterT"], None)

    def test_电量一位小数_与大屏同源(self):
        """B-524 同族：本模块曾用 3 位小数，大屏用 1 位，同一格 24.812 / 24.8。"""
        self.assertEqual(ranges.round_kwh(Decimal("24.8123")), 24.8)
        self.assertEqual(ranges.round_kwh(Decimal("1234.5678")), 1234.6)
        self.assertIsNone(ranges.round_kwh(None))

    def test_水汽不给kwh字段_避免量纲错误(self):
        """水/汽焦点通道不能把 m³ / t 塞进 kwh 字段（量纲会错）。"""
        gw = EnergyGateway.objects.create(
            code="GW-J", name="水汽", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        wtype = etype(EnergyType.WATER)
        EnergyMeter.objects.create(
            code="W-J-1", name="水表", energy_type=wtype,
            scope=EnergyMeter.SCOPE_WORKSHOP, line=self.ws, gateway=gw,
            slave=1, register=68, unit="m\u00b3",
            usage_type="车间进水", device_name="进水总表",
        )
        day = datetime(2026, 9, 20).date()
        data = ranges.usage_range(
            day, day, granularity="hour", scope="all", kind="water", workshop="WS-CARB"
        )
        self.assertIsNone(data["totals"]["kwh"], "水表没有 kWh 概念，不能填进 kwh")
        self.assertEqual(data["series"][0]["unit"], "m\u00b3")

    def test_合计与序列逐格对齐(self):
        """`_range_total_kwh` 必须用与图表同一批桶，否则会多带一格。"""
        gw = EnergyGateway.objects.create(
            code="GW-K", name="桶对齐", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        m = EnergyMeter.objects.create(
            code="E-K-1", name="表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电",
        )
        day = datetime(2026, 9, 20).date()
        for h in range(24):
            EnergyHourly.objects.create(
                meter=m, line=self.line, energy_type=m.energy_type,
                stat_date=day, stat_hour=h,
                consumption=Decimal("100.0"), quality=EnergyHourly.Q_OK,
            )
        data = ranges.usage_range(
            day, day, granularity="hour", scope="workshop", kind="elec",
            workshop="WS-CARB", node_code="A1",
        )
        vals = [v for v in data["series"][0]["values"] if v is not None]
        self.assertEqual(len(vals), data["bucketCount"], "序列与桶数必须一致")
        self.assertAlmostEqual(sum(vals), data["totals"]["kwh"], places=1)
    def test_漏写单位的点位按能源类型推导(self):
        """★ B-538：`unit` 漏写时**按能源类型推**，绝不静默兜成 kWh。

        这是 B-535 同族的坑：单位不参与任何计算，采集 / 聚合 / 汇总一路跑通、
        不报一个错，只有界面把 380 吨水标成 380 kWh —— 现场核对时的表现是
        「数字对不上」，而不是「配置写错了」，排查方向一开始就是歪的。
        """
        self.assertEqual(registry._unit_for(etype(EnergyType.WATER), None), "m\u00b3")
        self.assertEqual(registry._unit_for(etype(EnergyType.STEAM), None), "t")
        self.assertEqual(registry._unit_for(etype(EnergyType.ELECTRIC), None), "kWh")
        # 配置里明确写了就以配置为准（现场可能用 m3 / 吨 等写法）
        self.assertEqual(registry._unit_for(etype(EnergyType.WATER), "m3"), "m3")
        self.assertEqual(registry._unit_for(etype(EnergyType.WATER), "  "), "m\u00b3")

    def test_后台新增水表不填单位也不给kwh(self):
        """同一坑的第二条路径：后台表单没填单位 → 走 `_save_meter` 的兜底。"""
        gw = EnergyGateway.objects.create(
            code="GW-U", name="单位兜底", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        m = api._save_meter(None, {
            "code": "W-U-1", "name": "水表", "kind": "water",
            "gatewayId": gw.id, "slave": 1, "register": 1000,
            "lineCode": self.ws.line_code,
        })
        self.assertEqual(m.unit, "m\u00b3", "水表没填单位时必须推成 m³，不能是默认的 kWh")
    def test_三级筛选参数驼峰下划线都认(self):
        """★ B-539：`usageType` 与 `usage_type` 必须等价，认不出的参数不许静默忽略。

        前端发驼峰、脚本/手敲习惯下划线。旧代码只读下划线，于是手敲的那份
        被**静默丢掉**、整条链路退化成「车间全量」——界面照样出数，
        只是口径从 13 块表变成 31 块表，比报错更难发现。
        """
        from django.test import Client
        gw = EnergyGateway.objects.create(
            code="GW-P", name="参数", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=1
        )
        EnergyMeter.objects.create(
            code="E-P-1", name="一线表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=self.line, gateway=gw,
            slave=1, register=1000, usage_type="生产线用电", device_name="生产线",
        )
        c = Client()
        day = "2026-09-20"
        camel = c.get("/api/energy/range", {
            "kind": "elec", "workshop": "WS-CARB", "usageType": "生产线用电",
            "granularity": "hour", "date": day,
        }).json()
        snake = c.get("/api/energy/range", {
            "kind": "elec", "workshop": "WS-CARB", "usage_type": "生产线用电",
            "granularity": "hour", "date": day,
        }).json()
        self.assertEqual(camel["meterCount"], 1, "usageType 被忽略时会退化成车间全量")
        self.assertEqual(snake["meterCount"], 1)
        self.assertEqual(camel["series"][0]["name"], snake["series"][0]["name"])

    def test_非法日期返回400而不是500(self):
        """★ B-540：`parse_date` 对**越界**日期抛 ValueError，不能漏成 500。

        Django 的 parse_date 只在格式错（"abc"）时返回 None；`2026-13-99`
        这种「格式对、数值越界」直接抛 ValueError。旧代码只判 None，
        越界日期未捕获 → HTTP 500，界面只看到「服务器错误」，
        拿不到「到底哪一格填错了」。
        """
        from django.test import Client
        c = Client()
        res = c.get("/api/energy/range", {"kind": "elec", "date": "2026-13-99"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("date", res.json()["detail"])

class MeterNodeOptionTests(TestCase):
    """v1.5.2（B-541）：计量点「所属节点」下拉必须覆盖产线与辅助计量单元。

    现场现象：新增辅机 / 调配点位时，「所属产线」下拉**只有 A1—A6 六条产线**，
    选不到归属 —— 因为那三类电表挂在 aux 节点（辅机 / 调配 / 瓶胚）上，不挂产线。

    根因：前端把选项**写死**成 ['A1'...'A6']，而不是读组织节点。
    修复后前端改为读 `admin/lines`，因此该接口必须如实返回：
      · `nodeType`   —— 前端靠它把节点分成「产线」与「辅助计量单元」两组；
      · `parentCode` —— 节点归属哪个车间。
    另外 `workshop` 原本写死 "灌装车间"，多车间下是错的标注，一并锁住。
    """

    @classmethod
    def setUpTestData(cls):
        cls.ws = ProductionLine.default_workshop()
        cls.line = ProductionLine.objects.create(
            line_code="A9", line_name="九线",
            node_type=ProductionLine.NODE_LINE, line_type=ProductionLine.TYPE_LINE,
            parent=cls.ws,
        )
        cls.aux = ProductionLine.objects.create(
            line_code="AUX-A9", line_name="九线辅机",
            node_type=ProductionLine.NODE_AUX, line_type=ProductionLine.TYPE_LINE,
            parent=cls.ws,
        )

    def _rows(self):
        res = Client().get("/api/admin/lines", {"pageSize": 200})
        self.assertEqual(res.status_code, 200)
        return {r["code"]: r for r in res.json()["list"]}

    def test_接口返回节点类型与父车间(self):
        rows = self._rows()
        self.assertEqual(rows["A9"]["nodeType"], ProductionLine.NODE_LINE)
        self.assertEqual(rows["A9"]["parentCode"], self.ws.line_code)
        self.assertEqual(rows["AUX-A9"]["nodeType"], ProductionLine.NODE_AUX)
        self.assertEqual(rows["AUX-A9"]["parentCode"], self.ws.line_code)
        # 车间节点自身：nodeType=workshop 且没有父节点
        self.assertEqual(rows[self.ws.line_code]["nodeType"], ProductionLine.NODE_WORKSHOP)
        self.assertIsNone(rows[self.ws.line_code]["parentCode"])

    def test_辅机节点必须出现在列表里(self):
        """接口若把 aux 过滤掉，前端下拉里就永远选不到辅机归属。"""
        codes = self._rows().keys()
        self.assertIn("AUX-A9", codes)
        self.assertIn("A9", codes)

    def test_所属车间按父节点实测而不是写死灌装车间(self):
        """多车间下把 workshop 写死成「灌装车间」是**错误标注**（旧代码如此）。"""
        rows = self._rows()
        self.assertEqual(rows["A9"]["workshop"], self.ws.line_name)
        self.assertNotEqual(rows["A9"]["workshop"], "灌装车间")


class GatewayTestEvidenceTests(TestCase):
    """v1.5.2（B-542）：网关「测试连接」的结果必须落成证据，失败要判离线。

    现场现象：点「测试连接」失败后，列表里的状态**仍是「在线」**，
    与眼前看到的结果正好相反，最长拖到旧心跳过期（约 91 分钟）。

    根因两层：
      ① `admin_gateway_test` 是**纯只读**，测试结果不落库，状态只由心跳推导；
         而心跳只能证明「这一刻通」，无法证明「不通」。
      ② `_gateway_last_heartbeat` 取「任意一条日志」的时间 ——
         整轮**全失败**的采集同样刷新心跳，彻底连不上的网关永远显示在线。

    修复：成功写 `last_ok_at`、失败写 `last_fail_at`；判定改为**最新证据优先**，
    并且只有 `ok_count > 0` 的采集日志才算心跳。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A8", line_name="八线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-EV", name="证据测试网关",
            kind=EnergyGateway.KIND_MODBUS, host="10.0.0.8", port=8888,
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-EV-01", name="八线表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=cls.line, gateway=cls.gw,
            slave=1, register=1000, enabled=True,
        )

    # ── 工具 ───────────────────────────────────────────────
    def _fresh_heartbeat(self, minutes_ago=1):
        """造一个「刚成功过」的点位信号（网关此刻确实通）。"""
        self.meter.last_at = datetime.now() - timedelta(minutes=minutes_ago)
        self.meter.last_ok = True
        self.meter.last_error = ""
        self.meter.save(update_fields=["last_at", "last_ok", "last_error"])

    def _status(self):
        self.gw.refresh_from_db()
        hb = api._gateway_heartbeats().get(self.gw.id)
        fails = api._gateway_failures().get(self.gw.id)
        return api.gateway_json(self.gw, last_heartbeat=hb, failure_at=fails)["status"]

    def _test(self, ok: bool):
        """走真实接口并替掉底层试读，只关心结果怎么落库。"""
        items = [
            {
                "meter": self.meter.code, "name": self.meter.name, "ok": ok,
                "value": 100.0 if ok else None, "raw": "0103" if ok else "",
                "error": "" if ok else "Modbus 无响应",
            }
        ]
        with mock.patch.object(api, "_try_read", return_value={"durationMs": 7, "items": items}):
            res = Client().post(f"/api/admin/gateways/{self.gw.id}/test")
        self.assertEqual(res.status_code, 200)
        return res.json()

    # ── ① 失败要判离线 ─────────────────────────────────────
    def test_测试失败写入失败证据并判离线(self):
        """★ 现场反馈的场景：心跳还新鲜（1 分钟前刚读到过），但此刻测试失败。"""
        self._fresh_heartbeat()
        self.assertEqual(self._status(), "online", "前提：测试前确实是在线")

        body = self._test(ok=False)
        self.assertFalse(body["reachable"])

        self.gw.refresh_from_db()
        self.assertIsNotNone(self.gw.last_fail_at, "失败必须留下证据")
        self.assertEqual(self._status(), "offline", "更新的失败证据必须推翻旧的成功")

    def test_失败后网关列表也显示离线(self):
        self._fresh_heartbeat()
        self._test(ok=False)
        row = next(
            r for r in Client().get("/api/admin/gateways").json() if r["code"] == "GW-EV"
        )
        self.assertEqual(row["status"], "offline")
        self.assertTrue(row["lastFailure"], "界面据此解释为什么有心跳却判离线")

    # ── ② 成功要判在线（不能只修失败那一半）───────────────
    def test_测试成功写入成功证据无需等待采集(self):
        """对称的一条：修了「失败→离线」却漏了「成功→在线」，现场照样会反复怀疑。"""
        self.assertEqual(EnergyCollectLog.objects.filter(gateway=self.gw).count(), 0)
        body = self._test(ok=True)
        self.assertTrue(body["reachable"])
        self.gw.refresh_from_db()
        self.assertIsNotNone(self.gw.last_ok_at)
        self.assertIsNone(self.gw.last_fail_at, "成功要清掉旧的失败证据")
        self.assertEqual(self._status(), "online")

    # ── ③ 采集日志的 ok_count 语义 ────────────────────────
    def test_整轮采集全失败不算心跳(self):
        """旧实现取「任意一条日志」，于是彻底连不上的网关永远显示在线。"""
        EnergyCollectLog.objects.create(
            gateway=self.gw, started_at=datetime.now() - timedelta(minutes=5),
            total=3, ok_count=0, fail_count=3,
        )
        self.assertNotIn(self.gw.id, api._online_gateway_ids())
        self.assertEqual(self._status(), "offline")

    def test_整轮采集全失败构成失败证据(self):
        """有心跳（刚成功过）+ 整轮采集全失败 → 失败的证据更新 → 离线。"""
        self._fresh_heartbeat(minutes_ago=5)
        self.assertEqual(self._status(), "online")
        EnergyCollectLog.objects.create(
            gateway=self.gw, started_at=datetime.now() - timedelta(minutes=1),
            total=3, ok_count=0, fail_count=3,
        )
        self.assertEqual(self._status(), "offline")

    def test_整轮采集成功仍算心跳(self):
        """越严格越好，但不能把正常采集判掉 —— 这条防止 'ok_count > 0' 过滤写反。"""
        EnergyCollectLog.objects.create(
            gateway=self.gw, started_at=datetime.now() - timedelta(minutes=5),
            total=3, ok_count=3, fail_count=0,
        )
        self.assertEqual(self._status(), "online")
        self.assertIn(self.gw.id, api._online_gateway_ids())

    def test_没有点位的网关不写证据(self):
        """无从判断 ≠ 不通：一个点位都没有时不能判离线、也不该留失败证据。"""
        self.meter.enabled = False
        self.meter.save(update_fields=["enabled"])
        body = self._test(ok=False)
        self.assertFalse(body["reachable"])
        self.gw.refresh_from_db()
        self.assertIsNone(self.gw.last_fail_at, "「没得测」不能当成「测出不通」")

    def test_更新的失败推翻更早的成功_反之不成立(self):
        """最新证据优先：只有**更新**的失败才推翻旧成功，旧的失败不推翻新的成功。"""

        def decide():
            """每次都从库里重读两条证据。

            ⚠ 不能先把心跳取出来复用：`datetime.now() - timedelta(...)` 在两次
              调用中会得到不同的绝对时刻，复用旧心跳会让「失败」凭空变新，
              测出的是测试写法的问题而不是规则的问题（这条踩过）。
            """
            hb = api._gateway_test_heartbeat().get(self.gw.id)
            fails = api._gateway_failures().get(self.gw.id)
            return api.gateway_is_online(self.gw, hb, fails)

        # 场景 A：成功在前（-10 分）、失败在后（-2 分）→ 失败的证据更新 → 离线
        self.gw.last_ok_at = datetime.now() - timedelta(minutes=10)
        self.gw.last_fail_at = datetime.now() - timedelta(minutes=2)
        self.gw.save(update_fields=["last_ok_at", "last_fail_at"])
        self.assertFalse(decide(), "更新的失败必须推翻更早的成功")

        # 场景 B：失败在前（-10 分）、成功在后（-1 分）→ 成功的证据更新 → 在线
        self.gw.last_fail_at = datetime.now() - timedelta(minutes=10)
        self.gw.last_ok_at = datetime.now() - timedelta(minutes=1)
        self.gw.save(update_fields=["last_ok_at", "last_fail_at"])
        self.assertTrue(decide(), "更早的失败不该推翻更新的成功")


class AlarmEngineTests(TestCase):
    """告警引擎：去重、自愈、确认，以及「读路径兜底」。

    这一族测试盯的是同一类风险 —— 告警系统很容易变成两种没用的东西：
      · 每 5 分钟为同一件事新增一条 → 变成噪音源，没人再看；
      · 只在采集正常时才工作 → 采集停了就静默，变成新的盲区。
    """

    NOW = datetime(2026, 9, 23, 13, 30)

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(
            line_code="A9", line_name="九线", node_type=ProductionLine.NODE_LINE
        )
        cls.gw = EnergyGateway.objects.create(
            code="GW-ALM", name="告警测试网关", kind=EnergyGateway.KIND_MODBUS, host="127.0.0.1", port=9
        )
        cls.meter = EnergyMeter.objects.create(
            code="E-A9-01", name="九线电表", energy_type=etype(),
            scope=EnergyMeter.SCOPE_LINE, line=cls.line, gateway=cls.gw, unit="kWh",
        )

    def setUp(self):
        self.client = Client()

    def test_未接入的表生成未接入告警(self):
        """表被排除出合计口径的同时必须被报出来，否则就成了隐式行为。"""
        alerts.evaluate()
        row = EnergyAlarm.objects.filter(category=EnergyAlarm.CAT_METER_UNCONNECTED).first()
        self.assertIsNotNone(row, "登记了却从未出数的表已被排除出合计，不报出来就是隐式行为")
        self.assertEqual(row.meter_id, self.meter.id)
        self.assertIn("E-A9-01", row.content)

    def test_重复评估只累加次数不新增行(self):
        """同一件事反复检测到只能更新一条 —— 否则告警页会被刷爆。"""
        alerts.evaluate()
        alerts.evaluate()
        code = f"METER-UNCONNECTED:{self.meter.code}"
        self.assertEqual(EnergyAlarm.objects.filter(code=code).count(), 1, "同一件事不该新增第二行")
        self.assertEqual(EnergyAlarm.objects.get(code=code).repeat, 2, "应当以 repeat 累加")

    def test_表开始出数后未接入告警自动恢复(self):
        alerts.evaluate()
        code = f"METER-UNCONNECTED:{self.meter.code}"
        EnergyHourly.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            stat_date=self.NOW.date(), stat_hour=8,
            consumption=Decimal("10.0"), quality=EnergyHourly.Q_OK,
        )
        alerts.evaluate()
        row = EnergyAlarm.objects.get(code=code)
        self.assertEqual(row.status, EnergyAlarm.STATUS_RESOLVED, "接入后该条必须自动关闭")
        self.assertIsNotNone(row.resolved_at)

    def test_确认后同一问题不重新打开(self):
        """否则「确认」等于只静音一轮，值班人员会开始忽略整个告警页。"""
        alerts.evaluate()
        row = EnergyAlarm.objects.get(code=f"METER-UNCONNECTED:{self.meter.code}")
        row.status = EnergyAlarm.STATUS_ACKED
        row.acked_by = "张三"
        row.save(update_fields=["status", "acked_by"])
        alerts.evaluate()
        row.refresh_from_db()
        self.assertEqual(row.status, EnergyAlarm.STATUS_ACKED, "确认后不该被下一轮评估改回未处理")
        self.assertEqual(row.acked_by, "张三")

    def test_刚重启还没有完整小时不该被误判为未接入(self):
        """判据必须是「设备读得到数」，不是「算得出完整小时差值」。

        停机几天再拉起时，本小时还没有上一秒的端点读数，小时行一律是 missing。
        若按「有过 ok 的小时差值」判接入，这一刻会把所有正常在采的表全部误判成
        未接入 —— 既踢出合计，又刷出几十条假告警，告警系统本身变成噪音源。
        """
        EnergyRaw.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            collect_time=self.NOW, reading=Decimal("1234.5"), ok=True,
        )
        EnergyHourly.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            stat_date=self.NOW.date(), stat_hour=13,
            consumption=None, quality=EnergyHourly.Q_MISSING,
        )
        alerts.evaluate()
        self.assertFalse(
            EnergyAlarm.objects.filter(category=EnergyAlarm.CAT_METER_UNCONNECTED).exists(),
            "读得到数就是已接入，跟能不能算出整点差值无关",
        )

    def test_读路径兜底会生成停摆告警(self):
        """采集进程死了就没人评估告警 —— 而「采集停了」恰是最该看见的一条。

        所以大屏读路径必须能自己把这一条兜出来（规则实现仍只有 alerts 里那一份）。
        """
        EnergyRaw.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            collect_time=datetime(2026, 9, 23, 10, 4), reading=Decimal("1"), ok=True,
        )
        self.assertEqual(EnergyAlarm.objects.count(), 0, "前置：还没评估过")
        build_overview(self.NOW)  # 13:30 看大屏，最新读数停在 10:04 → 超 2 小时阈值
        self.assertTrue(
            EnergyAlarm.objects.filter(category=EnergyAlarm.CAT_COLLECT_STALE).exists(),
            "只打开大屏就要能报出停摆，不能依赖采集进程还活着",
        )

    def test_读路径兜底不污染重复次数(self):
        alerts.evaluate()
        code = "COLLECT-STALE:ALL"
        EnergyRaw.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            collect_time=datetime(2026, 9, 23, 10, 4), reading=Decimal("1"), ok=True,
        )
        before = EnergyAlarm.objects.get(code=code).repeat
        build_overview(self.NOW)
        build_overview(self.NOW)
        self.assertEqual(
            EnergyAlarm.objects.get(code=code).repeat, before,
            "大屏每 30 秒刷新一次，不该把 repeat 刷成「被查询次数」",
        )

    def test_告警接口按状态与级别筛选(self):
        alerts.evaluate()
        body = json.loads(self.client.get("/api/admin/alarms", {"status": "active"}).content)
        self.assertTrue(body["list"], "未处理列表应有内容")
        self.assertTrue(all(r["status"] == "active" for r in body["list"]))
        body = json.loads(self.client.get("/api/admin/alarms", {"level": "major"}).content)
        self.assertTrue(all(r["level"] == "major" for r in body["list"]))

    def test_确认接口写入确认人与时间(self):
        alerts.evaluate()
        alarm = EnergyAlarm.objects.get(code=f"METER-UNCONNECTED:{self.meter.code}")
        res = self.client.post(
            f"/api/admin/alarms/{alarm.id}/ack",
            data=json.dumps({"by": "李四"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        alarm.refresh_from_db()
        self.assertEqual(alarm.status, EnergyAlarm.STATUS_ACKED)
        self.assertEqual(alarm.acked_by, "李四")
        self.assertIsNotNone(alarm.acked_at)

    def test_确认不存在的告警给404(self):
        self.assertEqual(self.client.post("/api/admin/alarms/999999/ack").status_code, 404)

    def test_大屏告警与接口同源(self):
        """大屏的告警条与 /api/admin/alarms 必须来自同一张表。

        否则「大屏说没事、告警页挂着 3 条」这种分歧又要现场自己去对。
        """
        EnergyRaw.objects.create(
            meter=self.meter, line=self.line, energy_type=self.meter.energy_type,
            collect_time=datetime(2026, 9, 23, 10, 4), reading=Decimal("1"), ok=True,
        )
        texts = [a["text"] for a in build_overview(self.NOW)["workshop"]["alerts"]]
        rows = EnergyAlarm.objects.filter(
            status__in=(EnergyAlarm.STATUS_ACTIVE, EnergyAlarm.STATUS_ACKED)
        )
        for t in texts:
            if "产量数据源未接入" in t:
                continue  # 数据源状态说明，按设计不落告警表
            self.assertTrue(
                any(t == r.content for r in rows),
                f"大屏出现了一条告警表里没有的文案：{t}",
            )


class CoverageAlarmTests(TestCase):
    """覆盖率告警只能基于「日终结算后重算过」的日汇总行（B-553）。

    日结算是每天 00:30 写的，而那只是「到此刻」的快照；凌晨 00:01 的采集
    评估如果直接读它，就会把所有计量数据误报成「昨日仅结算 0/24」。
    另一面：真实缺数的告警不能因为换了一天就自动变成「已恢复」。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(
            line_code="A9", line_name="九线", node_type=ProductionLine.NODE_LINE
        )
        cls.etype = etype()

    def _daily(self, day, covered, computed_at):
        """造一条日汇总行，并把 auto_now 的 computed_at 改成指定时刻。"""
        row = EnergyDaily.objects.create(
            line=self.line,
            energy_type=self.etype,
            stat_date=day,
            consumption=None,
            unit="kWh",
            covered_count=covered,
            expected_count=24,
            quality=(
                EnergyDaily.Q_OK
                if covered >= 24
                else (EnergyDaily.Q_MISSING if covered == 0 else EnergyDaily.Q_PARTIAL)
            ),
        )
        EnergyDaily.objects.filter(pk=row.pk).update(computed_at=computed_at)
        return row

    def _evaluate(self, now):
        return alerts.evaluate(now=now, categories={EnergyAlarm.CAT_COVERAGE})

    def _alarm(self):
        return EnergyAlarm.objects.get(
            code=f"COVERAGE:2026-09-22:{self.line.id}:{self.etype.id}"
        )

    def test_结算前读到当天快照不产生覆盖率告警(self):
        """09-22 00:30 写的当天快照（0/24）在次日凌晨还不是结论。"""
        self._daily(datetime(2026, 9, 22).date(), 0, datetime(2026, 9, 22, 0, 30))
        self._evaluate(datetime(2026, 9, 23, 0, 1, 44))
        self.assertFalse(
            EnergyAlarm.objects.filter(category=EnergyAlarm.CAT_COVERAGE).exists(),
            "日终后还没重算过的日行只是当天快照，不能据此报「昨日少算了 24 小时」",
        )

    def test_零点后结算时刻前重算的快照也不报(self):
        """rollup 容器在 00:10 重启会立刻补跑一次，但昨天的边界读数可能还没到。

        此时日行已经是「今天写的」，只按日期会误以为它是对完整一天的结论；
        门槛必须落在 00:30 的固定结算时刻。
        """
        self._daily(datetime(2026, 9, 22).date(), 23, datetime(2026, 9, 23, 0, 10))
        self._evaluate(datetime(2026, 9, 23, 0, 11))
        self.assertFalse(
            EnergyAlarm.objects.filter(category=EnergyAlarm.CAT_COVERAGE).exists(),
            "00:30 前重启触发的结算仍可能缺最后一小时，不能当成日终结论",
        )

    def test_日终后重算仍缺数才产生覆盖率告警(self):
        self._daily(datetime(2026, 9, 22).date(), 18, datetime(2026, 9, 23, 0, 30))
        self._evaluate(datetime(2026, 9, 23, 0, 31))
        alarm = self._alarm()
        self.assertEqual(alarm.value, "18/24")
        self.assertEqual(alarm.threshold, "24/24")
        self.assertIn("少 6 个小时", alarm.content)

    def test_真实缺数告警不会被换日误恢复(self):
        """数据没补回来，告警不能因为规则改看新的一天就自愈。"""
        self._daily(datetime(2026, 9, 22).date(), 18, datetime(2026, 9, 23, 0, 30))
        self._evaluate(datetime(2026, 9, 23, 0, 31))
        self._evaluate(datetime(2026, 9, 24, 0, 1))
        self.assertEqual(self._alarm().status, EnergyAlarm.STATUS_ACTIVE)

    def test_缺数补齐后才会自动恢复(self):
        row = self._daily(datetime(2026, 9, 22).date(), 18, datetime(2026, 9, 23, 0, 30))
        self._evaluate(datetime(2026, 9, 23, 0, 31))
        EnergyDaily.objects.filter(pk=row.pk).update(
            covered_count=24, quality=EnergyDaily.Q_OK, computed_at=datetime(2026, 9, 23, 12, 0)
        )
        self._evaluate(datetime(2026, 9, 24, 0, 1))
        self.assertEqual(self._alarm().status, EnergyAlarm.STATUS_RESOLVED)


class ProductionIngestTests(TestCase):
    """产量入库命令的字段名回归测试。

    `ingest_production` 此前把 `stat_hour` 写成 `hour`，而模型上没有 `hour` 字段 ——
    也就是说正式写入与 --demo-null 两条路径**从来没跑通过**，产量表实际是空的。
    没有任何测试覆盖它，所以这个 bug 一直活着。这里补上，防止接口「看起来有、
    实际不能用」地被带到产量改造那天。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(
            line_code="A1", line_name="一线", node_type=ProductionLine.NODE_LINE
        )

    def test_写入某线某小时产量(self):
        call_command(
            "ingest_production",
            line="A1", date="2026-09-22", hour=10, output="26800",
            stdout=StringIO(),
        )
        row = ProductionHourly.objects.get(line=self.line, stat_date=DAY, stat_hour=10)
        self.assertEqual(row.output, Decimal("26800"))
        self.assertEqual(row.quality, ProductionHourly.Q_OK)

    def test_演示占位写null而不是0(self):
        """0 会被读成「确实没产出」，null 才是「还没接数据」。"""
        call_command("ingest_production", demo_null=True, days=1, stdout=StringIO())
        rows = ProductionHourly.objects.all()
        self.assertTrue(rows.exists(), "--demo-null 必须能跑通")
        self.assertTrue(all(r.output is None for r in rows))
        self.assertTrue(all(r.source == ProductionHourly.SOURCE_DEMO for r in rows))
        self.assertTrue(all(r.quality == ProductionHourly.Q_NODATA for r in rows))

    def test_重复写入是更新而不是新增(self):
        write_production("A1", DAY, 3, Decimal("100"))
        write_production("A1", DAY, 3, Decimal("180"))
        self.assertEqual(ProductionHourly.objects.filter(stat_date=DAY, stat_hour=3).count(), 1)
        self.assertEqual(
            ProductionHourly.objects.get(line=self.line, stat_date=DAY, stat_hour=3).output,
            Decimal("180"),
        )

class _FakeRegisters:
    """替身：只实现 read_one 用到的那两个接口。"""

    def __init__(self, regs, error=False):
        self.registers = list(regs)
        self._error = error

    def isError(self):
        return self._error


class _FakeModbusClient:
    def __init__(self, regs, error=False):
        self._all = list(regs)
        self._error = error
        self.calls: list[dict] = []

    def read_holding_registers(self, address=None, count=None, **kw):
        self.calls.append({"address": address, "count": count, **kw})
        return _FakeRegisters(self._all[:count], self._error)

    def close(self):
        pass


def _swap16(x: int) -> int:
    """16 位字节对调。"""
    return int.from_bytes(x.to_bytes(2, "big"), "little")


class MeterDataTypeTests(TestCase):
    """v1.5.3：计量点的寄存器**数据类型**（现场水表是 double，不是 32 位整数）。

    背景：碳水市政水表 `W-CARB-CITY-1`（`GW-WATER-CTY` 10.114.1.71:10123、
    从站 5、起始地址 68）是 **4 个寄存器、IEEE754 双精度**。此前所有点位都按
    「32 位无符号整数」拼，于是水表读出来是一个完全无关的大整数 ——
    采集不报错、小时差值照算，只有数字荒谬，现场表现为「水表读数不对」。

    现场用试读脚本确认：**ABCD（字序高字在前 + 寄存器内大端）** 解析的才是准确值。
    注意 ABCD 与 13 块电表的字节/字序**相同**，区别只在数据类型，
    所以本组测试重点锁「类型驱动的解码」与「寄存器个数由类型推导」。
    """

    @classmethod
    def setUpTestData(cls):
        cls.line = ProductionLine.objects.create(line_code="A7", line_name="七线")
        cls.gw = EnergyGateway.objects.create(
            code="GW-DT", name="类型测试网关",
            kind=EnergyGateway.KIND_MODBUS, host="10.0.0.7", port=8888,
        )

    # ── ① 解码：四种排布都要能解回原值 ──────────────────────
    def test_double四种排布各自解回原值(self):
        """按某种排布发出来的寄存器，必须能被对应配置解回原值。

        四种排布＝字节序（寄存器内）× 字序（寄存器之间）：
        ABCD / CDAB / BADC / DCBA。
        """
        value = 1234.5678
        words = list(struct.unpack(">4H", struct.pack(">d", value)))
        cases = {
            "ABCD": (words, "big", "big"),
            "CDAB": (list(reversed(words)), "big", "little"),
            "BADC": ([_swap16(w) for w in words], "little", "big"),
            "DCBA": ([_swap16(w) for w in reversed(words)], "little", "little"),
        }
        for name, (regs, byte_order, word_order) in cases.items():
            with self.subTest(layout=name):
                got = decode_registers(
                    regs, data_type=EnergyMeter.DT_FLOAT64,
                    byte_order=byte_order, word_order=word_order,
                )
                self.assertAlmostEqual(got, value, places=9)

    def test_float32两种排布(self):
        value = 234.5
        words = list(struct.unpack(">2H", struct.pack(">f", value)))
        self.assertAlmostEqual(
            decode_registers(words, data_type=EnergyMeter.DT_FLOAT32), value, places=4
        )
        self.assertAlmostEqual(
            decode_registers(
                [_swap16(w) for w in words],
                data_type=EnergyMeter.DT_FLOAT32, byte_order="little",
            ),
            value, places=4,
        )

    def test_现场脚本组合1与本实现逐位一致(self):
        """现场试读脚本的组合 1（byteorder=big + wordorder=big）＝ ABCD。

        这条是本次改动的**验收锚点**：脚本里的组合 1 打印出来的是准确值，
        本实现必须给出完全相同的数。

        ⚠ 脚本的组合 3/4 不要拿来对齐：它们在 byteorder='little' 时既对调寄存器内
          字节、又用 `'<d'` 整体按小端解释，等于把字节序用了两次，
          打印出来的并不是标准 BADC/DCBA（脚本里那两行只是探索用）。
        """
        def parse_double_combo1(regs):
            data = b"".join(r.to_bytes(2, "big") for r in regs)
            return struct.unpack(">d", data)[0]

        for value in (0.0, 1.0, 1234.5678, 987654.321, -5.25):
            regs = list(struct.unpack(">4H", struct.pack(">d", value)))
            with self.subTest(value=value):
                self.assertEqual(
                    decode_registers(
                        regs, data_type=EnergyMeter.DT_FLOAT64,
                        byte_order="big", word_order="big",
                    ),
                    parse_double_combo1(regs),
                )

    def test_整数类型与旧口径逐位一致(self):
        """回归保护：13 块电表（32 位无符号）的解码结果必须一位都不变。"""
        for regs in ([0x03C3, 0x3B79], [0x0000, 0x0001], [0xFFFF, 0xFFFF]):
            for word_order in ("big", "little"):
                with self.subTest(regs=regs, word_order=word_order):
                    self.assertEqual(
                        decode_registers(
                            regs, data_type=EnergyMeter.DT_UINT32,
                            byte_order="big", word_order=word_order,
                        ),
                        ModbusRtuOverTcpCollector._combine(regs, word_order),
                    )

    def test_有符号与16位类型(self):
        self.assertEqual(decode_registers([0xFFFF, 0xFFFF], data_type=EnergyMeter.DT_INT32), -1)
        self.assertEqual(decode_registers([0xFFFF, 0xFFFF], data_type=EnergyMeter.DT_UINT32), 0xFFFFFFFF)
        self.assertEqual(decode_registers([0x1234], data_type=EnergyMeter.DT_UINT16), 0x1234)
        self.assertEqual(decode_registers([0xFFFE], data_type=EnergyMeter.DT_INT16), -2)

    def test_寄存器不够时解码报错而不是猜(self):
        """给少了必须报错：猜出来的数不会报错，只会是一个数值离谱的读数。"""
        with self.assertRaises(ValueError):
            decode_registers([0x4093, 0x4A45], data_type=EnergyMeter.DT_FLOAT64)
        with self.assertRaises(ValueError):
            decode_registers([0x4093], data_type=EnergyMeter.DT_UINT32)
        with self.assertRaises(ValueError):
            decode_registers([1, 2], data_type="uint8")

    # ── ② 类型驱动读取长度 ────────────────────────────────
    def test_寄存器个数由数据类型推导(self):
        m = EnergyMeter(
            code="X-1", name="x", energy_type=etype(), gateway=self.gw, line=self.line,
            data_type=EnergyMeter.DT_FLOAT64, registers=2,
        )
        self.assertEqual(m.expected_registers, 4)
        self.assertTrue(m.sync_registers(), "推导值与镜像列不一致时应被纠正")
        self.assertEqual(m.registers, 4)

    def test_采集按数据类型决定读几个寄存器(self):
        """float64 必须真的去读 4 个寄存器 —— 读 2 个只会得到半个 double。"""
        m = EnergyMeter.objects.create(
            code="W-DT-1", name="水表", energy_type=etype(EnergyType.WATER),
            scope=EnergyMeter.SCOPE_WORKSHOP, line=ProductionLine.default_workshop(),
            gateway=self.gw, slave=5, register=68,
            data_type=EnergyMeter.DT_FLOAT64, byte_order="big", word_order="big",
            scale=Decimal("1"), unit="m\u00b3",
        )
        m.sync_registers()
        m.save(update_fields=["registers"])
        regs = list(struct.unpack(">4H", struct.pack(">d", 1234.5678)))

        collector = ModbusRtuOverTcpCollector(self.gw, timeout_ms=1000, retry_times=0)
        collector.client = _FakeModbusClient(regs)
        res = collector.read_one(m)

        self.assertTrue(res.ok, res.error)
        self.assertEqual(collector.client.calls[0]["count"], 4, "必须按类型读 4 个寄存器")
        self.assertEqual(res.value, Decimal("1234.5678"))
        self.assertIn("float64", res.raw, "报文留档要写清按什么类型读的")

    def test_float64解码不引入二进制尾巴(self):
        """Decimal(str(float)) 而非 Decimal(float)：否则 1234.5678 会变成
        1234.5678000000000346... 一路带进小时差值。"""
        regs = list(struct.unpack(">4H", struct.pack(">d", 1234.5678)))
        m = EnergyMeter.objects.create(
            code="W-DT-2", name="水表2", energy_type=etype(EnergyType.WATER),
            scope=EnergyMeter.SCOPE_WORKSHOP, line=ProductionLine.default_workshop(),
            gateway=self.gw, slave=5, register=68,
            data_type=EnergyMeter.DT_FLOAT64, scale=Decimal("1"), unit="m\u00b3",
        )
        m.sync_registers()
        m.save(update_fields=["registers"])
        collector = ModbusRtuOverTcpCollector(self.gw, timeout_ms=1000, retry_times=0)
        collector.client = _FakeModbusClient(regs)
        self.assertEqual(collector.read_one(m).value, Decimal("1234.5678"))

    def test_浮点NaN判为采集失败而不是入库(self):
        """表故障或字节序配错时常读到 NaN/Inf。
        Decimal('nan') 能构造出来但会在比较与入库时到处炸，且差值毫无意义 ——
        必须判失败并说清原因，不把一个「数字形状的错误」放进库里。"""
        nan_regs = list(struct.unpack(">4H", struct.pack(">d", float("nan"))))
        m = EnergyMeter.objects.create(
            code="W-DT-3", name="水表3", energy_type=etype(EnergyType.WATER),
            scope=EnergyMeter.SCOPE_WORKSHOP, line=ProductionLine.default_workshop(),
            gateway=self.gw, slave=5, register=68,
            data_type=EnergyMeter.DT_FLOAT64, scale=Decimal("1"), unit="m\u00b3",
        )
        m.sync_registers()
        m.save(update_fields=["registers"])
        collector = ModbusRtuOverTcpCollector(self.gw, timeout_ms=1000, retry_times=0)
        collector.client = _FakeModbusClient(nan_regs)
        res = collector.read_one(m)
        self.assertFalse(res.ok)
        self.assertIn("浮点读数非法", res.error)

    # ── ③ 接口与契约 ──────────────────────────────────────
    def test_接口返回数据类型与字节序(self):
        EnergyMeter.objects.create(
            code="W-DT-4", name="水表4", energy_type=etype(EnergyType.WATER),
            scope=EnergyMeter.SCOPE_WORKSHOP, line=ProductionLine.default_workshop(),
            gateway=self.gw, slave=5, register=68,
            data_type=EnergyMeter.DT_FLOAT64, byte_order="big", unit="m\u00b3",
        )
        res = Client().get("/api/admin/meters", {"pageSize": 200})
        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.json()["list"] if r["code"] == "W-DT-4")
        self.assertEqual(row["dataType"], "float64")
        self.assertEqual(row["byteOrder"], "big")
        self.assertEqual(row["registers"], 4, "镜像列应等于按类型推导的个数")

    def test_新增点位时寄存器个数被推导而不是照抄前端(self):
        """前端只提交 dataType；registers 由后端推导 —— 否则「配的 double、读的 2 个寄存器」。"""
        res = Client().post(
            "/api/admin/meters",
            data=json.dumps({
                "code": "W-DT-5", "name": "水表5", "kind": "water",
                "scope": "workshop", "gatewayId": self.gw.id,
                "slave": 5, "register": 68,
                "dataType": "float64", "byteOrder": "big",
                "unit": "m\u00b3", "factor": 1,
                "registers": 2,          # 前端若误传，后端必须无视
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        m = EnergyMeter.objects.get(code="W-DT-5")
        self.assertEqual(m.data_type, "float64")
        self.assertEqual(m.registers, 4)

    def test_未知数据类型返回400而不是静默存下(self):
        res = Client().post(
            "/api/admin/meters",
            data=json.dumps({
                "code": "W-DT-6", "name": "水表6", "kind": "water",
                "scope": "workshop", "gatewayId": self.gw.id,
                "slave": 5, "register": 68, "dataType": "uint8",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("数据类型", res.json()["detail"])
        self.assertFalse(EnergyMeter.objects.filter(code="W-DT-6").exists())

    def test_前端契约自检覆盖数据类型字段(self):
        """漏登记别名 = 字段被静默丢弃（B-535 同族）：
        现场选了 float64 保存后仍是 uint32，水表继续给出荒谬的大整数。"""
        self.assertIn("dataType", api.FRONTEND_METER_KEYS)
        self.assertIn("byteOrder", api.FRONTEND_METER_KEYS)
        self.assertEqual(api.assert_alias_covers_frontend(), [])

    def test_登记表同步按数据类型推导寄存器个数(self):
        """seed_registry 也要走同一条规则，否则配置写 float64、库里仍读 2 个寄存器。"""
        self.assertEqual(registry._data_type_for(None), EnergyMeter.DATA_TYPE_DEFAULT)
        self.assertEqual(registry._data_type_for("float64"), "float64")
        with self.assertRaises(ValueError):
            registry._data_type_for("float128")
