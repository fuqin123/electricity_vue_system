/**
 * 能源 mock 数据生成器
 *
 * ⚠ 本文件只在 `dataConfig.mode = 'mock'`（或后端不可达降级）时生效。
 *   现场已接通真实电表与蒸汽表，正常演示走 `mode = 'api'`。
 *
 * 设计原则（沿用原实现，勿随意改动）：
 *  1. 用「产线 + 日期 + 小时」做种子的**确定性随机** —— 同一时刻反复刷新数值不跳变，
 *     时间往前走数值才增长，符合大屏观感。
 *  2. 电量取整会带来 ±0.5kWh 误差 → 单位电耗跳动，因此小于 20kWh 时保留 1 位小数。
 *
 * ── v1.2.1 的重要变更：产量归零 ────────────────────────────────
 * 产量侧（灌装机计数器 / 装箱机 PLC / MES 班报工）尚未改造，因此：
 *   · `output` 一律为 **null**（无论已过去还是未到时段：0 会被读成「确实没产出」，
 *     与「还没接数据」是两回事，界面统一显示 `--`）；
 *   · `unitKwh` 一律为 **null**（无产量算不出单位电耗，显示为 `--`）；
 *   · `rate`（达成率）为 0，且 `dataConfig.productionEnabled = false` 时界面标注「产量未接入」；
 *   · **不生成任何与产量有关的告警**（低产 / 无产量 / 电表缺失），
 *     否则会因「产量恒为 null」刷出一屏假的停线告警。
 * 等真实产量接口接入后，把 `dataConfig.productionEnabled` 置 true 即可恢复全部产量口径。
 *
 * 电量仍然模拟（真实环境走接口）：按「基础负荷 × 负荷形态」生成，
 * 这样即使产量为 null，用电曲线也有真实感的日周期，便于在离线环境演示图表。
 */

import {
  dataConfig,
  energyModel,
  linesConfig,
  lineColors,
  thresholds,
  videoConfig,
  type LineConfig
} from '../config';
import { isoDate, makeRng, pad2, round2 } from '../format';
import type { AlertItem, EnergySnapshot, HourPoint, LineData, TrendPoint, WorkshopData } from '../types';

/** 24 小时产能形态：夜班偏低，午间与班次交接有凹陷，全天三峰 */
export const PROFILE = [
  0.62, 0.6, 0.59, 0.59, 0.61, 0.66, 0.78, 0.88, 0.98, 1.0, 0.99, 0.96, 0.8, 0.96, 1.0, 1.0,
  0.98, 0.95, 0.72, 0.95, 1.0, 0.98, 0.92, 0.82
];

/**
 * 24 小时用电负荷形态（产量未接入时使用）
 *
 * 与 PROFILE 的区别：这里描述的是**辅机/待机负荷**的日周期 ——
 * 空压机、冰水机、输送线、照明等不会随产量归零，深夜略低但不会掉到 0。
 * 现场实测各线每小时增量在 10—35 kWh 区间（六线约 240 kWh），与此形态量级一致。
 */
const LOAD_PROFILE = [
  0.84, 0.82, 0.8, 0.79, 0.81, 0.85, 0.92, 0.97, 1.0, 1.02, 1.01, 0.97, 0.9, 0.97, 1.02, 1.02,
  0.99, 0.95, 0.88, 0.96, 1.0, 0.98, 0.93, 0.87
];

/** 计划产量 = 额定 × 88%（贴合各线真实效率区间） */
export const PLAN_RATE = 0.88;

/** 产量数据源是否已接入（当前 false，见 config.ts 的 dataConfig） */
const PROD_ENABLED = dataConfig.productionEnabled;

/** 每条线的产能利用率与停机窗口（CIP 清洗 / 换型 / 计划检修） */
const LINE_TRAITS: Record<string, { avail: number; stopHours: number[]; reason: string }> = {
  A1: { avail: 0.9, stopHours: [], reason: '' },
  A2: { avail: 0.93, stopHours: [], reason: '' },
  A3: { avail: 0.86, stopHours: [12], reason: 'CIP 清洗' },
  A4: { avail: 0.74, stopHours: [18, 19], reason: '计划检修' },
  A5: { avail: 0.88, stopHours: [4], reason: 'CIP 清洗' },
  A6: { avail: 0.8, stopHours: [6, 7, 8, 9], reason: '产品换型' }
};

/**
 * 单线单小时的原始采样（不含当日截断）
 *
 * 产量在 `PROD_ENABLED = false` 时恒为 **null**（v1.2.1 口径：占位一律为 null，
 * 0 会被读成「确实没产出」）；此时电量按负荷形态模拟，
 * 不再与产量挂钩（产量未知却按产量推电耗，会算出无意义的数）。
 */
export function hourSample(line: LineConfig, date: Date, hour: number): HourPoint {
  const t = LINE_TRAITS[line.id] ?? { avail: 0.85, stopHours: [], reason: '' };
  const rng = makeRng(`${line.id}|${isoDate(date)}|${hour}`);
  const stopped = t.stopHours.indexOf(hour) >= 0;

  let output: number | null = null;
  if (PROD_ENABLED) {
    if (stopped) {
      output = 0; // 计划停机：有真实数据源时的「确实没产出」
    } else {
      const wd = date.getDay();
      const weekend = wd === 0 || wd === 6 ? 0.93 : 1;
      output = Math.round(line.rated * PROFILE[hour] * t.avail * weekend * (0.94 + rng() * 0.12));
      if (output < line.rated * 0.02) output = 0;
    }
  }

  // 计划停机时负荷降低，但不会到 0（CIP 要用水、要循环泵）
  const loadFactor = stopped ? 0.45 : 1;
  const kwh = Math.round(
    energyModel.baseLoadKwhPerHour * LOAD_PROFILE[hour] * t.avail * loadFactor * (0.9 + rng() * 0.22)
  );

  return {
    hour,
    output,
    kwh,
    unitKwh: PROD_ENABLED && output !== null && output > 0 ? round2((kwh / output) * 1000) : null,
    stopped
  };
}

/**
 * 单线条日缓存 —— 近 30 日 × 近 12 月 × 近 5 年要跑上万次采样，
 * 数据是确定性的（同一线 + 同一天结果恒定），缓存能显著降低刷新耗时。
 */
const dayCache = new Map<string, { hours: HourPoint[]; output: number; kwh: number; unitKwh: number | null; plan: number }>();

/** 单线整日（不含当日截断） */
export function daySample(
  line: LineConfig,
  date: Date
): { hours: HourPoint[]; output: number; kwh: number; unitKwh: number | null; plan: number } {
  const key = `${line.id}|${isoDate(date)}`;
  const hit = dayCache.get(key);
  if (hit) return hit;

  let out = 0;
  let kwh = 0;
  const hours: HourPoint[] = [];
  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, date, h);
    out += s.output ?? 0;
    kwh += s.kwh ?? 0;
    hours.push(s);
  }
  const rec = {
    hours,
    output: out,
    kwh,
    unitKwh: out > 0 ? round2((kwh / out) * 1000) : null,
    plan: Math.round(line.rated * PLAN_RATE * PROFILE.reduce((a, b) => a + b, 0))
  };
  // 上限保护：跨年演示也不至于无限增长
  if (dayCache.size > 4096) dayCache.clear();
  dayCache.set(key, rec);
  return rec;
}

/** 组装一条线的完整数据 */
function buildLine(line: LineConfig, now: Date): LineData {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const curH = now.getHours();
  const curFrac = now.getMinutes() / 60;

  const hours: HourPoint[] = [];
  let out = 0;
  let kwh = 0;
  let planElapsed = 0;
  let planFull = 0;
  // 刚过整点时用 1/60 兜底，避免 progress=0
  const frac = Math.max(curFrac, 1 / 60);

  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, today, h);
    if (h === curH) {
      s.projected = PROD_ENABLED ? (s.output ?? 0) : null;
      s.progress = frac;
      s.partial = true;
      s.output = PROD_ENABLED ? Math.round((s.output ?? 0) * frac) : null;
      // 刚过整点时电量很小，取整会带来误差 → 单位电耗跳动，故小额保留 1 位小数
      const scaled = (s.kwh ?? 0) * frac;
      s.kwh = scaled < 20 ? Math.max(0.1, Math.round(scaled * 10) / 10) : Math.round(scaled);
      s.unitKwh = PROD_ENABLED && (s.output ?? 0) > 0 ? round2(((s.kwh ?? 0) / (s.output ?? 1)) * 1000) : null;
    } else if (h > curH) {
      s.future = true;
      s.output = null;
      s.kwh = null;
      s.unitKwh = null;
    }
    const planHour = Math.round(line.rated * PLAN_RATE * PROFILE[h]);
    planFull += planHour;
    if (h < curH) planElapsed += planHour;
    else if (h === curH) planElapsed += Math.round(planHour * frac);

    if (!s.future) {
      out += s.output ?? 0;
      kwh += s.kwh ?? 0;
    }
    hours.push(s);
  }

  /* 近 30 日 */
  const daily30: TrendPoint[] = [];
  for (let d = 29; d >= 0; d--) {
    const dt = new Date(today.getTime() - d * 86400000);
    let rec: { output: number; kwh: number; unitKwh: number | null };
    if (d === 0) {
      rec = { output: out, kwh, unitKwh: PROD_ENABLED && out > 0 ? round2((kwh / out) * 1000) : null };
    } else {
      const ds = daySample(line, dt);
      rec = { output: ds.output, kwh: ds.kwh, unitKwh: ds.unitKwh };
    }
    daily30.push({
      label: `${dt.getMonth() + 1}/${dt.getDate()}`,
      // 产量未接入：null（显示 --），不是 0
      output: PROD_ENABLED ? rec.output : null,
      kwh: rec.kwh,
      unitKwh: rec.unitKwh
    });
  }

  /* 近 12 个月（与总览同口径，供线别页「月」维度切换） */
  const monthly12: TrendPoint[] = [];
  for (let mo = 11; mo >= 0; mo--) {
    const base = new Date(today.getFullYear(), today.getMonth() - mo, 1);
    const dim = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate();
    let moOut = 0;
    let moKwh = 0;
    for (let day = 1; day <= dim; day++) {
      const dtx = new Date(base.getFullYear(), base.getMonth(), day);
      if (dtx > today) break;
      if (isoDate(dtx) === isoDate(today)) {
        moOut += out;
        moKwh += kwh;
        break;
      }
      const ds = daySample(line, dtx);
      moOut += ds.output;
      moKwh += ds.kwh;
    }
    monthly12.push({
      label: `${base.getFullYear()}-${pad2(base.getMonth() + 1)}`,
      output: PROD_ENABLED ? moOut : null,
      kwh: moKwh,
      unitKwh: moOut > 0 ? round2((moKwh / moOut) * 1000) : null
    });
  }

  /* 近 7 日 × 24 小时（热力图，单位：百瓶）
     产量未接入时整片留 null —— 热力图会显示为「未上报」，而不是一片 0（0 会被读成「产量为零」） */
  const heat: (number | null)[][] = [];
  const heatDays: string[] = [];
  for (let k = 6; k >= 0; k--) {
    const hd = new Date(today.getTime() - k * 86400000);
    heatDays.push(`${hd.getMonth() + 1}/${hd.getDate()}${k === 0 ? ' 今日' : ''}`);
    const row: (number | null)[] = [];
    for (let hh = 0; hh < 24; hh++) {
      if (!PROD_ENABLED || (k === 0 && hh > curH)) {
        row.push(null);
        continue;
      }
      const hs = hourSample(line, hd, hh);
      row.push((hs.output ?? 0) > 0 ? Math.round((hs.output ?? 0) / 100) : null);
    }
    heat.push(row);
  }

  /* 月 / 年累计 */
  let monthOut = 0;
  let monthKwh = 0;
  for (let md = 1; md <= today.getDate(); md++) {
    if (md === today.getDate()) {
      monthOut += out;
      monthKwh += kwh;
    } else {
      const ms = daySample(line, new Date(today.getFullYear(), today.getMonth(), md));
      monthOut += ms.output;
      monthKwh += ms.kwh;
    }
  }
  const dayOfMonth = today.getDate();
  const dayOfYear = Math.floor((today.getTime() - new Date(today.getFullYear(), 0, 1).getTime()) / 86400000) + 1;
  const dailyAvgOut = monthOut / dayOfMonth;
  const dailyAvgKwh = monthKwh / dayOfMonth;

  /* 近 5 年（当年按已过天数折算；往年按整年推算值递减） */
  const yearly5: TrendPoint[] = [];
  const fullYearOut = dailyAvgOut * 365;
  const fullYearKwh = dailyAvgKwh * 365;
  for (let y = 4; y >= 0; y--) {
    const yr = today.getFullYear() - y;
    const rngY = makeRng(`Y${line.id}|${yr}`);
    if (y === 0) {
      yearly5.push({
        label: `${yr}年`,
        output: PROD_ENABLED ? Math.round(dailyAvgOut * dayOfYear) : null,
        kwh: Math.round(dailyAvgKwh * dayOfYear),
        unitKwh: PROD_ENABLED && monthOut > 0 ? round2((monthKwh / monthOut) * 1000) : null
      });
    } else {
      const f = Math.pow(0.955, y) * (0.97 + rngY() * 0.08);
      const yo = Math.round(fullYearOut * f);
      const yk = Math.round(fullYearKwh * f * (1 + (rngY() - 0.5) * 0.05));
      yearly5.push({
        label: `${yr}年`,
        output: PROD_ENABLED ? yo : null,
        kwh: yk,
        unitKwh: yo > 0 ? round2((yk / yo) * 1000) : null
      });
    }
  }

  /* 状态：产量未接入选不了「产能利用率」，改用**用电负荷**判定（与后端口径一致） */
  const cur = hours[curH];
  const baseRef = energyModel.baseLoadKwhPerHour;
  const curKwh = cur?.kwh ?? 0;
  let status: LineData['status'] = 'running';
  if (curKwh <= 0) status = 'stopped';
  else if (curKwh < baseRef * 0.6) status = 'slow';

  const trait = LINE_TRAITS[line.id];

  /* 昨日同期（截到当前小时，按已过分钟折算） */
  const yest = new Date(today.getTime() - 86400000);
  let cut = 0;
  let cutKwh = 0;
  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, yest, h);
    if (h < curH) {
      cut += s.output ?? 0;
      cutKwh += s.kwh ?? 0;
    } else if (h === curH) {
      cut += PROD_ENABLED ? Math.round((s.output ?? 0) * curFrac) : 0;
      cutKwh += Math.round((s.kwh ?? 0) * curFrac);
    }
  }
  const yestFull = daySample(line, yest);

  return {
    id: line.id,
    name: line.name,
    rated: line.rated,
    product: line.product,
    note: trait?.reason ? `${trait.reason}停机` : '',
    status,
    hours,
    today: {
      output: PROD_ENABLED ? out : null,
      kwh,
      unitKwh: PROD_ENABLED && out > 0 ? round2((kwh / out) * 1000) : null,
      plan: planElapsed,
      planFull,
      rate: PROD_ENABLED && planElapsed > 0 ? round2((out / planElapsed) * 100) : 0,
      // mock 数据每个整点都有，覆盖度必然是满的
      kwhCoveredHours: curH + 1,
      kwhExpectedHours: curH + 1
    },
    yesterday: {
      output: PROD_ENABLED ? cut : null,
      kwh: cutKwh,
      unitKwh: PROD_ENABLED && cut > 0 ? round2((cutKwh / cut) * 1000) : null,
      fullOutput: PROD_ENABLED ? yestFull.output : null,
      fullKwh: yestFull.kwh
    },
    daily30,
    monthly12,
    yearly5,
    heat,
    heatDays,
    month: { output: PROD_ENABLED ? monthOut : null, kwh: monthKwh },
    year: {
      output: PROD_ENABLED ? Math.round(dailyAvgOut * dayOfYear) : null,
      kwh: Math.round(dailyAvgKwh * dayOfYear),
      full: PROD_ENABLED ? Math.round(fullYearOut) : null,
      fullKwh: Math.round(fullYearKwh)
    },
    dayOfYear
  };
}

/** 主入口：生成一份完整快照 */
export function buildSnapshot(now: Date = new Date()): EnergySnapshot {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const curH = now.getHours();
  const curFrac = now.getMinutes() / 60;
  const m = energyModel;
  const th = thresholds;

  const lines = linesConfig.map((l) => buildLine(l, now));

  /* 车间合计 */
  let sumOut = 0;
  let sumKwh = 0;
  let sumYOut = 0;
  let sumYKwh = 0;
  lines.forEach((l) => {
    sumOut += l.today.output ?? 0;
    sumKwh += l.today.kwh;
    sumYOut += l.yesterday.output ?? 0;
    sumYKwh += l.yesterday.kwh;
  });

  /* 车间 24 小时合计 */
  const wsHours: HourPoint[] = [];
  for (let h = 0; h < 24; h++) {
    if (h > curH) {
      wsHours.push({ hour: h, output: null, kwh: null, unitKwh: null, future: true, partial: false });
      continue;
    }
    let o = 0;
    let e = 0;
    let part = false;
    lines.forEach((l) => {
      const s = l.hours[h];
      o += s.output ?? 0;
      e += s.kwh ?? 0;
      if (s.partial) part = true;
    });
    wsHours.push({
      hour: h,
      output: PROD_ENABLED ? o : null,
      kwh: e,
      unitKwh: PROD_ENABLED && o > 0 ? round2((e / o) * 1000) : null,
      future: false,
      partial: part
    });
  }

  /* 车间 近 30 日 */
  const wsDaily30: TrendPoint[] = [];
  for (let d = 29; d >= 0; d--) {
    const dt = new Date(today.getTime() - d * 86400000);
    let o2 = 0;
    let e2 = 0;
    if (d === 0) {
      o2 = sumOut;
      e2 = sumKwh;
    } else {
      linesConfig.forEach((l) => {
        const s = daySample(l, dt);
        o2 += s.output;
        e2 += s.kwh;
      });
    }
    wsDaily30.push({
      label: `${dt.getMonth() + 1}/${dt.getDate()}`,
      output: PROD_ENABLED ? o2 : null,
      kwh: e2,
      unitKwh: PROD_ENABLED && o2 > 0 ? round2((e2 / o2) * 1000) : null
    });
  }

  /* 近 12 个月 */
  const monthly12: TrendPoint[] = [];
  for (let mo = 11; mo >= 0; mo--) {
    const base = new Date(today.getFullYear(), today.getMonth() - mo, 1);
    const dim = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate();
    let moOut = 0;
    let moKwh = 0;
    for (let day = 1; day <= dim; day++) {
      const dtx = new Date(base.getFullYear(), base.getMonth(), day);
      if (dtx > today) break;
      if (isoDate(dtx) === isoDate(today)) {
        moOut += sumOut;
        moKwh += sumKwh;
        break;
      }
      linesConfig.forEach((l) => {
        const s = daySample(l, dtx);
        moOut += s.output;
        moKwh += s.kwh;
      });
    }
    monthly12.push({
      label: `${base.getFullYear()}-${pad2(base.getMonth() + 1)}`,
      output: PROD_ENABLED ? moOut : null,
      kwh: moKwh,
      unitKwh: moOut > 0 ? round2((moKwh / moOut) * 1000) : null
    });
  }

  /* 近 5 年：以各线整年推算值合计为基准，当年按已过天数折算 */
  const fullYearOut = lines.reduce((a, l) => a + (l.year.full ?? 0), 0);
  const fullYearKwh = lines.reduce((a, l) => a + l.year.fullKwh, 0);
  const dayOfYear = lines[0].dayOfYear;
  const yearly5: TrendPoint[] = [];
  for (let y = 4; y >= 0; y--) {
    const yr = today.getFullYear() - y;
    const rngY = makeRng(`Y${yr}`);
    if (y === 0) {
      const yo = Math.round((fullYearOut * dayOfYear) / 365);
      const yk = Math.round((fullYearKwh * dayOfYear) / 365);
      yearly5.push({
        label: `${yr}年`,
        output: PROD_ENABLED ? yo : null,
        kwh: yk,
        unitKwh: yo > 0 ? round2((yk / yo) * 1000) : null
      });
    } else {
      const f = Math.pow(0.955, y) * (0.97 + rngY() * 0.08);
      const yo = Math.round(fullYearOut * f);
      const yk = Math.round(fullYearKwh * f * (1 + (rngY() - 0.5) * 0.05));
      yearly5.push({
        label: `${yr}年`,
        output: PROD_ENABLED ? yo : null,
        kwh: yk,
        unitKwh: yo > 0 ? round2((yk / yo) * 1000) : null
      });
    }
  }

  /* 水 / 汽：只有车间总表，不可拆分到线别。
     产量未接入 → 不能按「强度 × 产量」折算，改用电量比例给出一个同量级估算，
     并在界面标注「估算」。 */
  const elapsedHours = Math.max(curH + curFrac, 0.2);
  const waterTodayT = (sumKwh * m.waterPerKwhL) / 1000;
  const steamTodayT = (sumKwh * m.steamPerKwhKg) / 1000;
  const yWaterT = ((sumYKwh / elapsedHours) * m.waterPerKwhL * elapsedHours) / 1000;
  const ySteamT = ((sumYKwh / elapsedHours) * m.steamPerKwhKg * elapsedHours) / 1000;
  const curTotalKwh = lines.reduce((a, l) => a + (l.hours[curH]?.kwh ?? 0), 0);
  const utilTrend = wsHours.map((x) => ({
    hour: x.hour,
    water: x.future ? null : round2(((x.kwh ?? 0) * m.waterPerKwhL) / 1000),
    steam: x.future ? null : round2(((x.kwh ?? 0) * m.steamPerKwhKg) / 1000)
  }));

  /* 告警：全部由真实判定条件生成，同类合并、按严重度排序 */
  const alerts: AlertItem[] = [];

  if (!PROD_ENABLED) {
    /* 产量未接入：只给一条说明，不产生任何产量类告警 */
    alerts.push({ level: 'info', text: dataConfig.productionNote, time: '--:--:--' });
  }

  lines.forEach((l) => {
    if (PROD_ENABLED) {
      /* ① 连续无产量合并为一条 */
      const runs: [number, number][] = [];
      let start = -1;
      for (let i = 0; i <= curH; i++) {
        const s = l.hours[i];
        if (s && !s.future && s.output === 0 && s.kwh !== null) {
          if (start < 0) start = i;
        } else if (start >= 0) {
          runs.push([start, i - 1]);
          start = -1;
        }
      }
      if (start >= 0) runs.push([start, curH]);
      runs.forEach((r) => {
        const span = r[1] - r[0] + 1;
        alerts.push({
          level: 'warn',
          text: `${l.id} 线 ${
            span > 1 ? `${pad2(r[0])}—${pad2(r[1])} 时连续 ${span} 小时无产量` : `${pad2(r[0])} 时无产量`
          }（待确认是否计划停机）`,
          time: `${pad2(r[1])}:00:00`
        });
      });

      /* ② 单位电耗超阈值：只报当日最差的一个已完成整点 */
      let worst: HourPoint | null = null;
      for (let j = 0; j < curH; j++) {
        const sj = l.hours[j];
        if (sj && !sj.future && !sj.partial && sj.unitKwh !== null && sj.unitKwh > th.unitKwhHigh) {
          if (!worst || sj.unitKwh > (worst.unitKwh ?? 0)) worst = sj;
        }
      }
      if (worst) {
        alerts.push({
          level: 'err',
          text: `${l.id} 线 ${pad2(worst.hour)} 时单位电耗 ${(worst.unitKwh ?? 0).toFixed(2)}，超阈值 ${th.unitKwhHigh.toFixed(2)}`,
          time: `${pad2(worst.hour)}:00:00`
        });
      }

      /* ③ 电表缺失（有产量无电量） */
      for (let k = 0; k < curH; k++) {
        const sk = l.hours[k];
        if (sk && !sk.future && (sk.output ?? 0) > 0 && (sk.kwh === null || sk.kwh === 0)) {
          alerts.push({
            level: 'err',
            text: `${l.id} 线 ${pad2(k)} 时电表无数据，产量正常`,
            time: `${pad2(k)}:00:00`
          });
          break;
        }
      }

      /* ④ 本小时低产：用整点预期值判定，且至少已过 1/4 小时才判 */
      const cur2 = l.hours[curH];
      if (
        cur2 &&
        !cur2.future &&
        cur2.partial &&
        (cur2.progress ?? 0) >= 0.25 &&
        (cur2.projected ?? 0) > 0 &&
        (cur2.projected ?? 0) < l.rated * th.outputLowRate
      ) {
        alerts.push({
          level: 'warn',
          text: `${l.id} 线本小时预计产量 ${((cur2.projected ?? 0) / 1000).toFixed(1)} 千瓶，低于额定 ${Math.round(
            th.outputLowRate * 100
          )}%`,
          time: `${pad2(curH)}:00:00`
        });
      }
    }
  });

  if (ySteamT > 0) {
    const sd = ((steamTodayT - ySteamT) / ySteamT) * 100;
    if (Math.abs(sd) > 10)
      alerts.push({
        level: 'warn',
        text: `车间汽耗较昨日同期 ${sd > 0 ? '+' : ''}${sd.toFixed(1)}%`,
        time: `${pad2(curH)}:00:00`
      });
  }
  if (yWaterT > 0) {
    const wd = ((waterTodayT - yWaterT) / yWaterT) * 100;
    if (Math.abs(wd) > 12)
      alerts.push({
        level: 'info',
        text: `车间水耗较昨日同期 ${wd > 0 ? '+' : ''}${wd.toFixed(1)}%`,
        time: `${pad2(curH)}:00:00`
      });
  }

  /* ⑤ 视频通道：合并为一条，不逐线刷屏 */
  if (videoConfig.enabled) {
    let note = '';
    if (videoConfig.provider === 'off') {
      note = '视频模块已关闭';
    } else if (videoConfig.provider === 'go2rtc') {
      if (!videoConfig.go2rtc.base) note = '视频网关地址未配置';
      else {
        const offline = Object.keys(videoConfig.channels).filter(
          (id) => videoConfig.channels[id].enabled === false
        );
        if (offline.length) {
          note = `视频通道未接入摄像头（${offline.length} 路：${offline.join('、')}）`;
        }
      }
    } else {
      const unset = Object.keys(videoConfig.customEndpoints).filter((id) => {
        const ep = videoConfig.customEndpoints[id];
        return !ep.hls && !ep.flv && !ep.webrtc;
      });
      if (unset.length) note = `视频通道未配置转流网关（${unset.length} 路：${unset.join('、')}）`;
    }
    if (note) alerts.push({ level: 'info', text: note, time: '--:--:--' });
  }

  const order: Record<AlertItem['level'], number> = { err: 0, warn: 1, info: 2 };
  alerts.sort((a, b) => order[a.level] - order[b.level]);

  const unitAll = PROD_ENABLED && sumOut > 0 ? round2((sumKwh / sumOut) * 1000) : 0;
  const yUnitAll = PROD_ENABLED && sumYOut > 0 ? round2((sumYKwh / sumYOut) * 1000) : 0;

  const workshop: WorkshopData = {
    lines,
    today: {
      output: PROD_ENABLED ? sumOut : null,
      kwh: sumKwh,
      unitKwh: PROD_ENABLED && sumOut > 0 ? unitAll : null,
      plan: lines.reduce((a, l) => a + l.today.plan, 0),
      waterT: round2(waterTodayT),
      steamT: round2(steamTodayT),
      kwhCoveredHours: curH + 1,
      kwhExpectedHours: curH + 1
    },
    yesterday: {
      output: PROD_ENABLED ? sumYOut : null,
      kwh: sumYKwh,
      unitKwh: PROD_ENABLED && sumYOut > 0 ? yUnitAll : null,
      waterT: round2(yWaterT),
      steamT: round2(ySteamT)
    },
    month: {
      output: PROD_ENABLED ? lines.reduce((a, l) => a + (l.month.output ?? 0), 0) : null,
      kwh: lines.reduce((a, l) => a + l.month.kwh, 0)
    },
    year: {
      output: PROD_ENABLED ? lines.reduce((a, l) => a + (l.year.output ?? 0), 0) : null,
      kwh: lines.reduce((a, l) => a + l.year.kwh, 0)
    },
    hours: wsHours,
    daily30: wsDaily30,
    monthly12,
    yearly5,
    utility: {
      water: {
        today: round2(waterTodayT),
        yesterday: round2(yWaterT),
        rateNow: round2((curTotalKwh * m.waterPerKwhL) / 1000),
        unit: 't',
        trend: utilTrend.map((x) => x.water),
        coveredHours: curH + 1,
        expectedHours: curH + 1
      },
      steam: {
        today: round2(steamTodayT),
        yesterday: round2(ySteamT),
        rateNow: round2((curTotalKwh * m.steamPerKwhKg) / 1000),
        unit: 't',
        trend: utilTrend.map((x) => x.steam),
        coveredHours: curH + 1,
        expectedHours: curH + 1
      }
    },
    targetUnitKwh: m.targetUnitKwh,
    alerts,
    currentHour: curH
  };

  return { now, workshop };
}

/** 供图表使用的产线色 */
export { lineColors };
