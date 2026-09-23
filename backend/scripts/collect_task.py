"""计划任务静默入口 —— 给 `pythonw.exe` 用（不弹控制台窗口）。

## 为什么不直接用 `pythonw.exe manage.py collect`

`pythonw.exe` 没有控制台，此时 `sys.stdout` / `sys.stderr` 是 **None**；
而 Django 的 `BaseCommand.__init__` 会把 `sys.stdout` 包进 `OutputWrapper`，
之后每一条 `self.stdout.write(...)` 都会走到 `None.write(...)` → `AttributeError`。

也就是说 `pythonw.exe manage.py collect` 会**不留痕迹地失败**：
没有窗口、没有任何输出、一次都没采到，而计划任务里只看到一个非零退出码 ——
现场会以为「任务在跑但设备没数据」，实际是任务压根没干活。
所以这里先接管 stdout/stderr（接到日志文件），再交给 Django。

## 用法

Windows 计划任务（不弹窗）：

    schtasks /Create /TN EEP-Collect /SC MINUTE /MO 5 /F ^
      /TR "\"<venv>\\Scripts\\pythonw.exe\" \"<仓库>\\backend\\scripts\\collect_task.py\""

> ⚠ 一定用**装了依赖的那个解释器的 pythonw.exe**（venv 里的），
>   不要用系统 `D:\\python3.13.10\\pythonw.exe` —— 那个环境里没有 Django / PyMySQL / pymodbus。

手工验证（可见输出，等价于计划任务那一轮）：

    python scripts/collect_task.py
    python scripts/collect_task.py collect --dry-run      # 传参时按传入参数执行

返回码：0 = 正常（含「本小时已采过，跳过」）；2 = 本轮有采集失败；1 = 异常。
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BACKEND_DIR / "logs"
LOG_FILE = LOG_DIR / "collect-task.log"
#: 单个日志文件上限，超过就轮转一份 .log.1（保留一代，够现场回看一次事故）
MAX_LOG_BYTES = 2 * 1024 * 1024

#: 不传参时的默认动作 —— 与计划任务登记的命令保持一致
DEFAULT_ARGS = ["collect", "--aggregate", "--if-due"]


def _open_log():
    """打开任务日志；超过上限先轮转。日志写不进去也不能让采集停摆，退回黑洞。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            backup = LOG_FILE.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            LOG_FILE.rename(backup)
        return open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError:
        return open(os.devnull, "w", encoding="utf-8")


def main() -> int:
    # 计划任务的工作目录是 C:\Windows\System32，必须先切到工程目录
    os.chdir(BACKEND_DIR)
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eep.settings")

    log = _open_log()
    sys.stdout = log
    sys.stderr = log

    args = sys.argv[1:] or DEFAULT_ARGS
    started = datetime.now()
    log.write(f"\n──── {started:%Y-%m-%d %H:%M:%S} 采集任务开始（{' '.join(args)}）────\n")

    try:
        from django.core.management import execute_from_command_line

        execute_from_command_line(["manage.py", *args])
    except SystemExit as exc:
        # 采集命令用退出码 2 表达「本轮有点位失败」，这是预期分支，不算异常
        code = int(exc.code or 0)
        log.write(
            f"{datetime.now():%Y-%m-%d %H:%M:%S} 结束：退出码 {code}"
            f"（2 = 本轮有采集失败，5 分钟后自动重试），"
            f"耗时 {(datetime.now() - started).total_seconds():.1f}s\n"
        )
        return code
    except Exception:  # noqa: BLE001 —— 兜住一切，必须留下书面痕迹
        log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 异常终止：\n{traceback.format_exc()}\n")
        return 1

    log.write(
        f"{datetime.now():%Y-%m-%d %H:%M:%S} 结束：正常，"
        f"耗时 {(datetime.now() - started).total_seconds():.1f}s\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
