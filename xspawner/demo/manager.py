# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
# manager.py

from xspawner.spawner import Spawner          # NOQA
from xspawner.xspawner import UiHandler       # NOQA

from pywebio import *                          # NOQA
from pywebio.input import *                    # NOQA
from pywebio.output import *                   # NOQA
from pywebio.pin import *                      # NOQA
from pywebio.session import *                  # NOQA

import json
import traceback
import tornado.gen


def _esc(s):
    """HTML 转义"""
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


class Manager(Spawner):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.spec = {}

    # ---------------- 数据访问 ----------------
    async def _fetch_values(self) -> dict:
        try:
            v = await self.getValues()
            return v if isinstance(v, dict) else {}
        except Exception as e:
            self.eLog(f"_fetch_values error: {e}")
            return {}

    def _filter_values(self, values: dict) -> dict:
        text = getattr(local, 'search_text', '') or ''
        if not text:
            return values
        ft = str(text)
        return {
            k: v for k, v in values.items()
            if (any(str(sv).startswith(ft) for sv in v.values())
                if isinstance(v, dict) else str(v).startswith(ft))
        }

    # ---------------- 渲染 ----------------
    async def render_body(self):
        view = getattr(local, 'view', 'home')
        if view == 'spec':
            await self._render_spec()
        elif view == 'add':
            await self._render_add()
        else:
            await self._render_home()

    async def _render_home(self):
        values = self._filter_values(await self._fetch_values())

        with use_scope('body', clear=True):
            if not values:
                put_html('<div style="padding:24px;text-align:center;'
                         'color:#999;">（无数据）</div>')
                return

            for k, v in values.items():
                if isinstance(v, dict):
                    sub = ''.join(
                        f'<div style="padding:2px 0 2px 20px;color:#555;">'
                        f'<b>{_esc(sk)}</b>: {_esc(sv)}</div>'
                        for sk, sv in v.items()
                    )
                    left = (
                        f'<div style="padding:8px 0;">'
                        f'<details>'
                        f'<summary style="cursor:pointer;color:#007bff;'
                        f'font-weight:bold;">{_esc(k)}</summary>'
                        f'{sub}</details></div>'
                    )
                else:
                    left = (
                        f'<div style="padding:8px 0;">'
                        f'<b>{_esc(k)}</b>: {_esc(v)}</div>'
                    )

                put_row(
                    [
                        put_html(left),
                        put_buttons(
                            [
                                {'label': '✏️', 'value': 'edit'},
                                {'label': '🗑️', 'value': 'delete'},
                            ],
                            small=True,
                            onclick=lambda act, k=k: self.on_row_action(act, k)
                        ),
                    ],
                    size='1fr auto',
                )

    async def _render_spec(self):
        with use_scope('body', clear=True):
            put_markdown('### 规范 (JSON)')
            put_textarea(
                'spec_text',
                value=json.dumps(self.spec, ensure_ascii=False, indent=2),
                rows=12
            )
            put_buttons(
                [{'label': '💾', 'value': 'save'}],
                onclick=[self.on_save_spec]
            )

    async def _render_add(self):
        with use_scope('body', clear=True):
            put_markdown('### 新增数据')
            put_input('new_key', label='键')

            if not self.spec:
                put_input('new_value', label='值')
            else:
                for sk, sv in self.spec.items():
                    if isinstance(sv, bool):
                        put_radio(f'new_{sk}', label=sk,
                                  options=[('是', True), ('否', False)],
                                  value=False)
                    elif isinstance(sv, int) and not isinstance(sv, bool):
                        put_input(f'new_{sk}', label=sk, type='number')
                    elif isinstance(sv, float):
                        put_input(f'new_{sk}', label=sk, type='float')
                    else:
                        put_input(f'new_{sk}', label=sk)

            put_buttons(
                [
                    {'label': '💾', 'value': 'save'},
                    {'label': '🧹', 'value': 'clear'},
                ],
                onclick=[self.on_save_new, self.on_clear_new]
            )

    # ---------------- 按钮回调 ----------------
    async def on_nav(self, view):
        if view not in ('home', 'spec', 'add'):
            view = 'home'
        local.view = view
        await self.render_body()

    async def on_search(self, _b=None):
        local.search_text = (await pin.search_input) or ''
        local.view = 'home'
        await self.render_body()

    async def on_row_action(self, act, key):
        if act == 'edit':
            await self._do_edit(key)
        elif act == 'delete':
            await self._do_delete(key)

    async def on_save_spec(self, _b=None):
        text = (await pin.spec_text) or ''
        if not text.strip():
            self.spec = {}
        else:
            try:
                obj = json.loads(text)
            except Exception as e:
                toast(f'JSON 格式错误: {e}', color='error'); return
            if not isinstance(obj, dict):
                toast('规范必须是 JSON 对象', color='error'); return
            self.spec = obj
        toast('规范已保存', color='success')
        local.view = 'home'
        await self.render_body()

    async def on_save_new(self, _b=None):
        key = ((await pin.new_key) or '').strip()
        if not key:
            toast('键不能为空', color='error'); return

        if not self.spec:
            value = await pin.new_value
            if value is None:
                value = ''
            value = str(value)
        else:
            value = {}
            for sk, sv in self.spec.items():
                raw = await pin[f'new_{sk}']
                if isinstance(sv, bool):
                    value[sk] = bool(raw)
                elif isinstance(sv, int) and not isinstance(sv, bool):
                    try:
                        value[sk] = int(raw) if raw not in (None, '') else 0
                    except (ValueError, TypeError):
                        value[sk] = 0
                elif isinstance(sv, float):
                    try:
                        value[sk] = float(raw) if raw not in (None, '') else 0.0
                    except (ValueError, TypeError):
                        value[sk] = 0.0
                else:
                    value[sk] = raw if raw is not None else ''

        ok = await self.setValue(key, value)
        toast('保存成功' if ok else '保存失败',
              color='success' if ok else 'error')
        if ok:
            local.view = 'home'
            await self.render_body()

    async def on_clear_new(self, _b=None):
        await self._render_add()

    # ---------------- 编辑 / 删除 ----------------
    async def _do_edit(self, key):
        old = await self.getValue(key)
        if old is None:
            toast(f'键 "{key}" 不存在', color='error'); return
        default = (json.dumps(old, ensure_ascii=False)
                   if isinstance(old, dict) else str(old))
        text = await input(f'编辑 "{key}" 的值', value=default)
        if text is None:
            return
        try:
            val = json.loads(text)
        except (ValueError, TypeError):
            val = text
        ok = await self.setValue(key, val)
        toast('更新成功' if ok else '更新失败',
              color='success' if ok else 'error')
        await self._render_home()

    async def _do_delete(self, key):
        res = await actions(
            f'确认删除 "{key}" 吗？',
            buttons=[{'label': '确认', 'value': 'yes', 'color': 'danger'},
                     {'label': '取消', 'value': 'no'}]
        )
        if res != 'yes':
            return
        ok = await self.delValue(key)
        toast('删除成功' if ok else '删除失败',
              color='success' if ok else 'error')
        await self._render_home()

    # ---------------- 主路由 ----------------
    @UiHandler.route("/")
    async def _(self):
        try:
            if not hasattr(local, 'view'):
                local.view = 'home'
            if not hasattr(local, 'search_text'):
                local.search_text = ''

            # ---- 全局样式：图标按钮字号略大，数据行按钮高度对齐 ----
            put_html(
                '<style>'
                '#pywebio-scope-body button {'
                '  padding: 8px 14px !important;'
                '  font-size: 18px !important;'
                '  line-height: 1.2 !important;'
                '  min-height: 0 !important;'
                '  height: auto !important;'
                '  vertical-align: middle;'
                '  margin-top: 2px !important;'
                '}'
                '</style>'
            )

            # ---- 头部第一行：标题 ----
            put_html(f'<h2 style="margin:6px 0;">'
                     f'Manager {self._config.id}</h2>')

            # ---- 头部第二行：导航按钮 + 搜索框 + 搜索按钮 ----
            put_row(
                [
                    put_buttons(
                        [
                            {'label': '🏠', 'value': 'home'},
                            {'label': '📋', 'value': 'spec'},
                            {'label': '➕', 'value': 'add'},
                        ],
                        onclick=self.on_nav
                    ),
                    put_html('<div style="width:24px;"></div>'),
                    put_input('search_input', placeholder='输入关键词...'),
                    put_button('🔍', onclick=self.on_search),
                ],
                size='auto auto 1fr auto',
            )
            put_html('<hr style="margin:8px 0;">')

            # ---- 数据区 ----
            put_scope('body')

            # ---- 初始渲染 ----
            await self.render_body()

            # ---- 保持会话存活 ----
            while True:
                await tornado.gen.sleep(60)

        except Exception as e:
            put_error(f"发生未知错误: {e}")
            put_error(traceback.format_exc())