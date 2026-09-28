r"""每日周期结算入口 —— 计划任务（建议 00:30）静默跑 `summarize --rollup`。

## 为什么需要这一个任务（v1.4.2 起是**必须**的）

日 / 月 / 年汇总表以前挂在每小时采集后面刷，现在改成**每天结算一次**：

    EEP-Collect    每 5 分钟   collect --aggregate --if-due    只出原始读数与小时差值
    EEP-Rollup     每天 00:30  summarize --rollup              结算昨天 + 今天 + 近 2 月 + 今年

界面（大屏与后台）的**当期**数字一律从小时表现算（`summary.is_open_period`），
所以当天不依赖汇总表；而**昨天及更早**的周期完全靠这个任务写出来。
⚠ 停掉它，大屏的「近 30 日 / 近 12 月」会停在最后一次结算的那天 ——
今天不动、明天也不动，而且不报任何错。

## 为什么 00:30

昨天 23 点的差值要等今天 00:04 采到跨日边界读数才算得出来（见 B-523），
00:30 留了足够余量，又赶在白班 08:00 之前。

## 为什么单独一个脚本而不是复用 collect_task.py

`collect_task.py` 的默认参数与日志文件名都是采集语义；
本脚本只换 `args` / 日志 / 标题，无控制台环境里接管 stdout 的那段实现直接复用
（那个坑只需要踩一次）。

## 用法

Windows 计划任务（不弹窗）：

    schtasks /Create /TN EEP-Rollup /SC DAILY /ST 00:30 /F ^
      /TR "\"<venv>\\Scripts\\pythonw.exe\" \"<仓库>\\backend\\scripts\\daily-rollup-task.py\""

手工验证（等价于计划任务那一轮）：

    python scripts/daily-rollup-task.py
    python scripts/daily-rollup-task.py summarize --dim day --days 30   # 传参时按传入参数执行

返回码：0 = 正常；1 = 异常（详见 backend/logs/rollup-task.log）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_task import LOG_DIR, run_task  # noqa: E402  —— 必须先补 sys.path

#: 结算日志单独一份：巡检「昨天到底结算了没有」时只看这一个文件
ROLLUP_LOG = LOG_DIR / "rollup-task.log"

DEFAULT_ARGS = ["summarize", "--rollup"]

if __name__ == "__main__":
    raise SystemExit(run_task(sys.argv[1:] or DEFAULT_ARGS, log_file=ROLLUP_LOG, title="周期结算任务"))
