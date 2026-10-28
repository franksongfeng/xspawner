# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import config
from pywebio.output import put_table, put_text, put_markdown
from pywebio.session import set_env

from xspawner.spawner import Spawner        # NOQA
from xspawner.xspawner import UiHandler      # NOQA
from xspawner.orm import *                   # NOQA

# 一个ORM操作和数据渲染的演示例子，具体ORM用法详见tortoise-orm

class Tab123(models.Model):
    class Meta:
        table = "m_tab_123"
    id = fields.IntField(pk=True, generated=True)
    x  = fields.CharField(max_length=255)
    y  = fields.IntField()
    z  = fields.FloatField()
    w  = fields.BooleanField(null=True)


class TestORM(Spawner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @UiHandler.route("/")
    @config(theme="yeti")
    async def _(self):

        # 1) 写入表格
        try:
            # 清空
            await Tab123.all().delete()

            # 单独写入
            await Tab123.create(x="b13", y=22, z=23.021, w=True)

            # 批量写入（原子性更好）
            await Tab123.bulk_create([
                Tab123(x="a35", y=44, z=17.715, w=False),
                Tab123(x="c50", y=19, z=18.249, w=True),
                Tab123(x="b56", y=35, z=59.0, w=True)
            ])

            self.iLog("cleared m_table and inserted 2 rows")
        except Exception as e:
            self.eLog(f"seed failed: {e}")

        # 2) 查询表格
        rows = await Tab123.all()
        w_true_rows = await Tab123.filter(w=True).order_by("z")

        # 3) 渲染表格
        table = [["id", "x", "y", "z", "w"]]
        for r in rows:
            table.append([
                str(r.id),
                r.x,
                str(r.y),
                "-" if r.z is None else f"{r.z:.2f}",
                "✓" if r.w else "✗",
            ])
        put_markdown("### Tab123 全部数据")
        put_table(table)
        put_text(f"共 {len(rows)} 行")

        table_w = [["id"]]
        for r in w_true_rows:
            table_w.append([
                str(r.id)
            ])
        put_markdown("### w = True 的数据")
        put_table(table_w)
        put_text(f"共 {len(w_true_rows)} 行")