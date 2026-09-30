import django.db.models.deletion
from django.db import migrations, models


def copy_tool_code_to_gate(apps, schema_editor):
    """把旧 tool_code 迁入新 FK 列，并为每把出现过的刀补建闸门行。"""
    OffsetSubmission = apps.get_model("desk", "OffsetSubmission")
    ToolPause = apps.get_model("desk", "ToolPause")
    seen = set()
    for code in (
        OffsetSubmission.objects.exclude(tool_code__isnull=True)
        .values_list("tool_code", flat=True)
        .distinct()
    ):
        seen.add(code)
    for code in seen:
        ToolPause.objects.create(tool_code=code, is_paused=False)
    # 迁移完成后同值回填新外键列。
    for code in seen:
        OffsetSubmission.objects.filter(tool_code=code).update(tool_id=code)


class Migration(migrations.Migration):
    dependencies = [
        ("desk", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ToolPause",
            fields=[
                ("tool_code", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("is_paused", models.BooleanField(db_index=True, default=False)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tool_pause_changes",
                        to="desk.user",
                    ),
                ),
                ("changed_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        # 1) 先加可空 FK 列 tool_id（与旧列 tool_code 暂时并存）。
        migrations.AddField(
            model_name="offsetsubmission",
            name="tool",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="desk.toolpause",
            ),
        ),
        # 2) 为每把刀建闸门行，并把旧刀号回填到 FK 列。
        migrations.RunPython(copy_tool_code_to_gate, migrations.RunPython.noop),
        # 3) 删除旧的字符串列。
        migrations.RemoveField(model_name="offsetsubmission", name="tool_code"),
        # 4) FK 收紧为非空（数据已回填）。
        migrations.AlterField(
            model_name="offsetsubmission",
            name="tool",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="desk.toolpause",
            ),
        ),
        migrations.CreateModel(
            name="ToolPauseEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("action", models.CharField(choices=[("pause", "暂停"), ("resume", "恢复")], max_length=8)),
                ("actor_name", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tool_pause_events",
                        to="desk.user",
                    ),
                ),
                (
                    "tool",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="events",
                        to="desk.toolpause",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
