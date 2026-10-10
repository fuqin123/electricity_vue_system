import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EnergyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.energy"
    verbose_name = "能源采集"

    def ready(self):
        """启动时对一遍「前端提交字段 → 模型字段」的映射是否完整。

        ⚠ 这类漂移（如前端写 `ip`、模型叫 `host` 而别名表漏登记）**不会报错**，
          只会把字段静默丢掉 —— 现场看到的现象和「设备没填对」一模一样，
          排查成本极高（B-535）。宁可在启动日志里吵一句。
        """
        from .api import assert_alias_covers_frontend

        missing = assert_alias_covers_frontend()
        if missing:
            logger.warning(
                "前后端字段契约不完整，以下字段会被 _apply() 静默丢弃：%s"
                "（请在 api.py 的 GATEWAY_ALIAS / METER_ALIAS 里补映射）",
                "、".join(missing),
            )
