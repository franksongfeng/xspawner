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


# 在 m_entity 表中存储 spec 的保留键。
# EntityModel 的 (spawn, key) 联合唯一约束保证：
#   同一个 spawn 实例下，key="__spec__" 的记录最多只有一条。
# 选择双下划线前后缀是为了降低与用户业务键冲突的概率。
SPEC_KEY = '__spec__'


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
        # spec 的"真理之源"是 m_entity 表中的 (spawn, key=SPEC_KEY) 这一行，
        # self.spec 只是会话内缓存，避免每次渲染都查库。
        self.spec = {}

    # ---------------- spec 存取：走 EntityModel ----------------
    async def _load_spec(self) -> dict:
        """
        从 EntityModel（m_entity 表）读取本 spawn 的 spec。
        约定：value 字段是 dict 时视为合法 spec，否则视为空 spec。
        """
        try:
            v = await self.getValue(SPEC_KEY)
        except Exception as e:
            self.eLog(f"_load_spec error: {e}")
            v = None
        self.spec = v if isinstance(v, dict) else {}
        return self.spec

    async def _save_spec(self, spec: dict) -> bool:
        """
        把 spec 写入 EntityModel（m_entity 表）。
        成功时同步刷新内存缓存。
        """
        try:
            ok = await self.setValue(SPEC_KEY, spec)
        except Exception as e:
            self.eLog(f"_save_spec error: {e}")
            return False
        if ok:
            self.spec = spec
        return ok

    # ---------------- 数据访问 ----------------
    async def _fetch_values(self) -> dict:
        try:
            v = await self.getValues()
            if not isinstance(v, dict):
                return {}
            # 关键：把 spec 这一行从业务数据里剔除，
            # 否则它会以 key="__spec__" 的形式出现在主页列表上。
            return {k: val for k, val in v.items() if k != SPEC_KEY}
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
        # 每次进入 spec 视图都从 EntityModel 重新读取，
        # 避免多会话 / 多端修改时读到旧数据
        await self._load_spec()
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
        # 新增表单需要依据 spec 决定字段类型，务必读最新
        await self._load_spec()
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
            new_spec = {}
        else:
            try:
                obj = json.loads(text)
            except Exception as e:
                toast(f'JSON 格式错误: {e}', color='error'); return
            if not isinstance(obj, dict):
                toast('规范必须是 JSON 对象', color='error'); return
            new_spec = obj

        # ← 真正的持久化：写入 m_entity 表 (spawn=self._config.id, key=SPEC_KEY)
        ok = await self._save_spec(new_spec)
        toast('规范已保存' if ok else '规范保存失败',
              color='success' if ok else 'error')
        if ok:
            local.view = 'home'
            await self.render_body()

    async def on_save_new(self, _b=None):
        key = ((await pin.new_key) or '').strip()
        if not key:
            toast('键不能为空', color='error'); return
        if key == SPEC_KEY:
            # 防止用户业务键把内部 spec 覆盖掉
            toast(f'"{SPEC_KEY}" 是保留键，请换一个', color='error'); return

        # 用最新的 spec 解析字段类型
        await self._load_spec()

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
        if key == SPEC_KEY:
            # 不允许从数据页编辑 spec，请从 📋 视图改
            toast(f'"{SPEC_KEY}" 为保留键，请用 📋 视图编辑', color='error')
            return
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
        if key == SPEC_KEY:
            toast(f'"{SPEC_KEY}" 为保留键，不能删除', color='error')
            return
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