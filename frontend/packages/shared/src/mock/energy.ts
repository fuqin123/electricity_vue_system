/**
 * 能源 mock 数据生成器
 *
 * 设计原则（沿用原实现，勿随意改动）：
 *  1. 用「产线 + 日期 + 小时」做种子的确定性随机 —— 同一时刻反复刷新数值不跳变，
 *     时间往前走数值才增长，符合大屏观感。
 *  2. 电耗 = 基础负荷（待机/辅机） + 产量 × 变动电耗 —— 低产时段单位电耗自然升高。
 *  3. 水 / 汽 只有车间总表，按车间合计产量折算，不做线别拆分。
 *
 * ⚠ 关键正确性约束（原实现踩过的坑，已固化）：
 *  a) 当前小时必须用「同一比例」同时缩放产量与电量。若产量按已过分钟缩放、
 *     电量按固定比例缩放，整点刚过时 电量/产量 会虚高成 20 kWh/千瓶，导致全线误报。
 *  b) 电量取整会带来 ±0.5kWh 误差 → 单位电耗跳动，因此小于 20kWh 时保留 1 位小数。
 *  c) 状态判定用 projected（整点预期值），避免整点时刻被误判为低产。
 */

import {
  energyModel,
  linesConfig,
  lineColors,
  thresholds,
  videoConfig,
  type LineConfig
} from '../config';
import { isoDate, makeRng, pad2, round2 } from '../format';
import type { AlertItem, EnergySnapshot, HourPoint, LineData, WorkshopData } from '../types';

/** 24 小时产能形态：夜班偏低，午间与班次交接有凹陷，全天三峰 */
export const PROFILE = [
  0.62, 0.6, 0.59, 0.59, 0.61, 0.66, 0.78, 0.88, 0.98, 1.0, 0.99, 0.96, 0.8, 0.96, 1.0, 1.0,
  0.98, 0.95, 0.72, 0.95, 1.0, 0.98, 0.92, 0.82
];

/** 计划产量 = 额定 × 88%（贴合各线真实效率区间） */
export const PLAN_RATE = 0.88;

/** 每条线的产能利用率与停机窗口（CIP 清洗 / 换型 / 计划检修） */
const LINE_TRAITS: Record<string, { avail: number; stopHours: number[]; reason: string }> = {
  A1: { avail: 0.9, stopHours: [], reason: '' },
  A2: { avail: 0.93, stopHours: [], reason: '' },
  A3: { avail: 0.86, stopHours: [12], reason: 'CIP 清洗' },
  A4: { avail: 0.74, stopHours: [18, 19], reason: '计划检修' },
  A5: { avail: 0.88, stopHours: [4], reason: 'CIP 清洗' },
  A6: { avail: 0.8, stopHours: [6, 7, 8, 9], reason: '产品换型' }
};

/** 单线单小时的原始采样（不含当日截断） */
export function hourSample(line: LineConfig, date: Date, hour: number): HourPoint {
  const t = LINE_TRAITS[line.id] ?? { avail: 0.85, stopHours: [], reason: '' };
  const rng = makeRng(`${line.id}|${isoDate(date)}|${hour}`);
  const stopped = t.stopHours.indexOf(hour) >= 0;
  const wd = date.getDay();
  const weekend = wd === 0 || wd === 6 ? 0.93 : 1;

  let output = 0;
  if (!stopped) {
    output = Math.round(line.rated * PROFILE[hour] * t.avail * weekend * (0.94 + rng() * 0.12));
    if (output < line.rated * 0.02) output = 0;
  }

  const kwh =
    output > 0
      ? Math.round(
          energyModel.baseLoadKwhPerHour +
            output * energyModel.variableKwhPerBottle * (0.96 + rng() * 0.09)
        )
      : // 停线仍有辅机待机负荷
        Math.round(energyModel.baseLoadKwhPerHour * (0.55 + rng() * 0.2));

  return {
    hour,
    output,
    kwh,
    unitKwh: output > 0 ? round2((kwh / output) * 1000) : null,
    stopped
  };
}

/** 单线整日（不含当日截断） */
export function daySample(
  line: LineConfig,
  date: Date
): { hours: HourPoint[]; output: number; kwh: number; unitKwh: number | null; plan: number } {
  let out = 0;
  let kwh = 0;
  const hours: HourPoint[] = [];
  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, date, h);
    out += s.output ?? 0;
    kwh += s.kwh ?? 0;
    hours.push(s);
  }
  return {
    hours,
    output: out,
    kwh,
    unitKwh: out > 0 ? round2((kwh / out) * 1000) : null,
    plan: Math.round(line.rated * PLAN_RATE * PROFILE.reduce((a, b) => a + b, 0))
  };
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
  // 刚过整点时用 1/60 兜底，避免 progress=0 导致单位电耗为 null
  const frac = Math.max(curFrac, 1 / 60);

  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, today, h);
    if (h === curH) {
      s.projected = s.output ?? 0; // 整小时预期产量，用于状态判定
      s.progress = frac;
      s.partial = true;
      s.output = Math.round((s.output ?? 0) * frac);
      // 刚过整点时电量很小，取整会带来误差 → 单位电耗跳动，故小额保留 1 位小数
      const scaled = (s.kwh ?? 0) * frac;
      s.kwh = scaled < 20 ? Math.max(0.1, Math.round(scaled * 10) / 10) : Math.round(scaled);
      s.unitKwh = (s.output ?? 0) > 0 ? round2(((s.kwh ?? 0) / (s.output ?? 1)) * 1000) : null;
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
  const daily30 = [];
  for (let d = 29; d >= 0; d--) {
    const dt = new Date(today.getTime() - d * 86400000);
    let rec: { output: number; kwh: number; unitKwh: number | null };
    if (d === 0) {
      rec = { output: out, kwh, unitKwh: out > 0 ? round2((kwh / out) * 1000) : null };
    } else {
      const ds = daySample(line, dt);
      rec = { output: ds.output, kwh: ds.kwh, unitKwh: ds.unitKwh };
    }
    daily30.push({
      label: `${dt.getMonth() + 1}/${dt.getDate()}`,
      output: rec.output,
      kwh: rec.kwh,
      unitKwh: rec.unitKwh
    });
  }

  /* 近 7 日 × 24 小时（热力图，单位：百瓶） */
  const heat: (number | null)[][] = [];
  const heatDays: string[] = [];
  for (let k = 6; k >= 0; k--) {
    const hd = new Date(today.getTime() - k * 86400000);
    heatDays.push(`${hd.getMonth() + 1}/${hd.getDate()}${k === 0 ? ' 今日' : ''}`);
    const row: (number | null)[] = [];
    for (let hh = 0; hh < 24; hh++) {
      if (k === 0 && hh > curH) {
        row.push(null);
        continue;
      }
      const hs = hourSample(line, hd, hh);
      row.push((hs.output ?? 0) > 0 ? Math.round((hs.output ?? 0) / 100) : 0);
    }
    heat.push(row);
  }

  /* 月 / 年累计
     达成率的分母必须与统计窗口一致：日达成率用「截至当前小时」的计划，
     年累计用「日均 × 年内已过天数」推算，避免与月度柱状图口径打架。 */
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

  /* 状态：整点时用 projected 判定，避免刚过整点就误报低产 */
  const cur = hours[curH];
  const refOut = cur?.partial ? cur.projected ?? 0 : cur?.output ?? 0;
  let status: LineData['status'] = 'running';
  if (!cur || refOut === 0) status = 'stopped';
  else if (refOut < line.rated * thresholds.outputLowRate) status = 'slow';

  const trait = LINE_TRAITS[line.id];

  /* 昨日同期（截到当前小时，按已过分钟折算）—— 在同一条线内一次算完，
     避免 buildSnapshot 里出现「先构造再回填」的两段式赋值。 */
  const yest = new Date(today.getTime() - 86400000);
  let cut = 0;
  let cutKwh = 0;
  for (let h = 0; h < 24; h++) {
    const s = hourSample(line, yest, h);
    if (h < curH) {
      cut += s.output ?? 0;
      cutKwh += s.kwh ?? 0;
    } else if (h === curH) {
      cut += Math.round((s.output ?? 0) * curFrac);
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
      output: out,
      kwh,
      unitKwh: out > 0 ? round2((kwh / out) * 1000) : null,
      plan: planElapsed,
      planFull,
      rate: planElapsed > 0 ? round2((out / planElapsed) * 100) : 0
    },
    yesterday: {
      output: cut,
      kwh: cutKwh,
      unitKwh: cut > 0 ? round2((cutKwh / cut) * 1000) : null,
      fullOutput: yestFull.output,
      fullKwh: yestFull.kwh
    },
    daily30,
    heat,
    heatDays,
    month: { output: monthOut, kwh: monthKwh },
    year: {
      output: Math.round(dailyAvgOut * dayOfYear),
      kwh: Math.round(dailyAvgKwh * dayOfYear),
      full: Math.round(dailyAvgOut * 365),
      fullKwh: Math.round(dailyAvgKwh * 365)
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
    sumOut += l.today.output;
    sumKwh += l.today.kwh;
    sumYOut += l.yesterday.output;
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
      output: o,
      kwh: e,
      unitKwh: o > 0 ? round2((e / o) * 1000) : null,
      future: false,
      partial: part
    });
  }

  /* 车间 近 30 日 */
  const wsDaily30 = [];
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
      output: o2,
      kwh: e2,
      unitKwh: o2 > 0 ? round2((e2 / o2) * 1000) : null
    });
  }

  /* 近 12 个月 */
  const monthly12 = [];
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
      label: `${base.getMonth() + 1}月`,
      output: moOut,
      kwh: moKwh,
      unitKwh: moOut > 0 ? round2((moKwh / moOut) * 1000) : null
    });
  }

  /* 近 5 年：以「整年推算值」为基准，当年按已过天数折算 */
  const fullYearOut = lines.reduce((a, l) => a + l.year.full, 0);
  const fullYearKwh = lines.reduce((a, l) => a + l.year.fullKwh, 0);
  const dayOfYear = lines[0].dayOfYear;
  const yearly5 = [];
  for (let y = 4; y >= 0; y--) {
    const yr = today.getFullYear() - y;
    const rngY = makeRng(`Y${yr}`);
    if (y === 0) {
      yearly5.push({
        label: `${yr}年`,
        output: Math.round((fullYearOut * dayOfYear) / 365),
        kwh: Math.round((fullYearKwh * dayOfYear) / 365)
      });
    } else {
      const f = Math.pow(0.955, y) * (0.97 + rngY() * 0.08);
      yearly5.push({
        label: `${yr}年`,
        output: Math.round(fullYearOut * f),
        kwh: Math.round(fullYearKwh * f * (1 + (rngY() - 0.5) * 0.05))
      });
    }
  }

  /* 水 / 汽：只有车间总表 */
  const elapsedHours = Math.max(curH + curFrac, 0.2);
  const waterTodayT = (sumOut * m.waterPerBottleL) / 1000;
  const steamTodayT = (sumOut * m.steamPerBottleKg) / 1000;
  const yWaterT = ((sumYOut / elapsedHours) * m.waterPerBottleL * elapsedHours) / 1000;
  const ySteamT = ((sumYOut / elapsedHours) * m.steamPerBottleKg * elapsedHours) / 1000;
  const curTotalOut = lines.reduce((a, l) => a + (l.hours[curH]?.output ?? 0), 0);
  const utilTrend = wsHours.map((x) => ({
    hour: x.hour,
    water: x.future ? null : round2(((x.output ?? 0) * m.waterPerBottleL) / 1000),
    steam: x.future ? null : round2(((x.output ?? 0) * m.steamPerBottleKg) / 1000)
  }));

  /* 告警：全部由真实判定条件生成，同类合并、按严重度排序 */
  const alerts: AlertItem[] = [];
  lines.forEach((l) => {
    /* ① 连续停线合并为一条 */
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

    /* ② 单位电耗超阈值：只报当日最差的一个已完成整点（跳过进行中的小时） */
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

  const unitAll = sumOut > 0 ? round2((sumKwh / sumOut) * 1000) : 0;
  const yUnitAll = sumYOut > 0 ? round2((sumYKwh / sumYOut) * 1000) : 0;

  const workshop: WorkshopData = {
    lines,
    today: {
      output: sumOut,
      kwh: sumKwh,
      unitKwh: unitAll,
      plan: lines.reduce((a, l) => a + l.today.plan, 0),
      waterT: round2(waterTodayT),
      steamT: round2(steamTodayT)
    },
    yesterday: {
      output: sumYOut,
      kwh: sumYKwh,
      unitKwh: yUnitAll,
      waterT: round2(yWaterT),
      steamT: round2(ySteamT)
    },
    month: {
      output: lines.reduce((a, l) => a + l.month.output, 0),
      kwh: lines.reduce((a, l) => a + l.month.kwh, 0)
    },
    year: {
      output: lines.reduce((a, l) => a + l.year.output, 0),
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
        rateNow: round2((curTotalOut * m.waterPerBottleL) / 1000),
        unit: 't',
        trend: utilTrend.map((x) => x.water)
      },
      steam: {
        today: round2(steamTodayT),
        yesterday: round2(ySteamT),
        rateNow: round2((curTotalOut * m.steamPerBottleKg) / 1000),
        unit: 't',
        trend: utilTrend.map((x) => x.steam)
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
