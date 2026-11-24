# v1.7.5：视频监控网关与 RTSP 通道落库，管理员可在后台直接维护

from django.db import migrations, models


#: 与迁移前前端 shared/config.ts 的默认值保持一致，避免升级后大屏突然没画面。
DEFAULT_CHANNELS = [
    ("W103", "一线", "CH01", "my_camera", "rtsp://admin:***@10.113.1.118:554/Streaming/Channels/102", "1920×1080", 25, "H.264", True),
    ("W101", "二线", "CH02", "my_camera", "", "1920×1080", 25, "H.264", False),
    ("W102", "三线", "CH03", "my_camera", "", "1920×1080", 25, "H.264", False),
    ("W301", "四线", "CH04", "my_camera", "", "1920×1080", 25, "H.264", False),
    ("W104", "五线", "CH05", "my_camera", "", "1280×720", 25, "H.264", False),
    ("W105", "六线", "CH06", "my_camera", "", "1280×720", 25, "H.264", False),
]


def seed_video_config(apps, schema_editor):
    Gateway = apps.get_model("energy", "VideoGatewayConfig")
    Channel = apps.get_model("energy", "VideoChannel")

    Gateway.objects.update_or_create(
        pk=1,
        defaults={"host": "10.113.1.61", "api_port": 1984, "webrtc_port": 8555, "enabled": True},
    )
    for sort, (line_code, line_name, channel, stream, rtsp, resolution, fps, codec, enabled) in enumerate(
        DEFAULT_CHANNELS
    ):
        Channel.objects.update_or_create(
            line_code=line_code,
            defaults={
                "line_name": line_name,
                "channel": channel,
                "stream": stream,
                "rtsp": rtsp,
                "resolution": resolution,
                "fps": fps,
                "codec": codec,
                "enabled": enabled,
                "sort": sort,
            },
        )


def unseed_video_config(apps, schema_editor):
    apps.get_model("energy", "VideoChannel").objects.all().delete()
    apps.get_model("energy", "VideoGatewayConfig").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("energy", "0021_binding_tree"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoGatewayConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("host", models.CharField(blank=True, default="", max_length=120, verbose_name="网关 IP / 主机名")),
                ("api_port", models.PositiveIntegerField(default=1984, verbose_name="API / 播放页端口")),
                ("webrtc_port", models.PositiveIntegerField(default=8555, verbose_name="WebRTC 端口")),
                ("enabled", models.BooleanField(default=True, verbose_name="启用视频模块")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("updated_by", models.CharField(blank=True, default="", max_length=32, verbose_name="更新人")),
            ],
            options={
                "verbose_name": "视频网关配置",
                "verbose_name_plural": "视频网关配置",
                "db_table": "video_gateway_config",
            },
        ),
        migrations.CreateModel(
            name="VideoChannel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_code", models.CharField(max_length=16, unique=True, verbose_name="产线编码")),
                ("line_name", models.CharField(blank=True, default="", max_length=32, verbose_name="产线名称")),
                ("channel", models.CharField(blank=True, default="", max_length=16, verbose_name="通道号")),
                ("stream", models.CharField(blank=True, default="", max_length=64, verbose_name="网关流名称")),
                ("rtsp", models.CharField(blank=True, default="", max_length=255, verbose_name="RTSP 源地址")),
                ("resolution", models.CharField(blank=True, default="", max_length=32, verbose_name="分辨率")),
                ("fps", models.PositiveIntegerField(blank=True, null=True, verbose_name="帧率")),
                ("codec", models.CharField(blank=True, default="", max_length=16, verbose_name="编码")),
                ("enabled", models.BooleanField(default=False, verbose_name="启用")),
                ("sort", models.PositiveSmallIntegerField(default=0, verbose_name="排序")),
            ],
            options={
                "verbose_name": "视频通道",
                "verbose_name_plural": "视频通道",
                "db_table": "video_channel",
                "ordering": ["sort", "line_code"],
            },
        ),
        migrations.RunPython(seed_video_config, unseed_video_config),
    ]
