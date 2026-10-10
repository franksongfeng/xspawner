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
        try:
            v = await self.getValue(SPEC_KEY)
        except Exception as e:
            self.eLog(f"_load_spec error: {e}")
            v = None
        self.spec = v if isinstance(v, dict) else {}
        return self.spec

    async def _save_spec(self, spec: dict) -> bool:
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
            # 关键：把 spec 这一行从业务数据里剔除
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

    # ---------------- 类型推断 / 控件构造 / 回值转换 ----------------
    def _infer_type(self, value, spec_value) -> str:
        """
        决定字段控件与数据类型。
        spec 优先（spec 是 schema 意图），否则按现值推断。
        """
        ref = spec_value if spec_value is not None else value
        if isinstance(ref, bool):
            return 'bool'
        if isinstance(ref, int):
            return 'int'
        if isinstance(ref, float):
            return 'float'
        return 'str'

    def _put_field(self, name: str, label: str, value, ftype: str):
        """
        在当前作用域渲染一个 pin 控件。
        name 是安全字段名（f0/f1/... 或 __key__/__value__），label 是展示名。
        """
        if ftype == 'bool':
            put_radio(
                name, label=label,
                options=[('是', True), ('否', False)],
                value=bool(value) if value is not None else False,
            )
        elif ftype == 'int':
            put_input(
                name, label=label, type='number',
                value=value if value is not None else '',
            )
        elif ftype == 'float':
            put_input(
                name, label=label, type='float',
                value=value if value is not None else '',
            )
        else:
            put_input(
                name, label=label,
                value='' if value is None else str(value),
            )

    def _coerce(self, raw, ftype: str):
        """把 pin 的原始值按 ftype 转成目标 Python 值。"""
        if ftype == 'bool':
            return bool(raw)
        if ftype == 'int':
            try:
                return int(raw) if raw not in (None, '') else 0
            except (ValueError, TypeError):
                return 0
        if ftype == 'float':
            try:
                return float(raw) if raw not in (None, '') else 0.0
            except (ValueError, TypeError):
                return 0.0
        return '' if raw is None else str(raw)

    # ---------------- 通用表单弹窗 ----------------
    async def _show_form_popup(self, title: str, fields: list):
        """
        通用表单弹窗。新增与编辑共用。

        参数:
          title:  弹窗标题
          fields: [(safe_name, label, ftype, initial_value), ...]

        返回:
          - dict {safe_name: raw_value}  用户点击「💾 保存」
          - None                         用户点击「↩️ 取消」

        「🔄 重置」在弹窗内部完成，逐字段清空，不关闭弹窗、不返回。
        """
        fut = tornado.gen.Future()

        async def _on_save(_b=None):
            values = {}
            for safe, _label, _ftype, _v in fields:
                try:
                    raw = await pin[safe]
                except Exception:
                    raw = None
                values[safe] = raw
            if not fut.done():
                fut.set_result(values)
            close_popup()

        async def _on_reset(_b=None):
            """
            清空所有字段。弹窗保持打开，不写库、不关闭。
            - bool：重置为 False
            - int/float：重置为空（None）
            - str：重置为空字符串
            """
            for safe, _label, ftype, _v in fields:
                try:
                    if ftype == 'bool':
                        pin_update(safe, False)
                    elif ftype in ('int', 'float'):
                        pin_update(safe, None)
                    else:
                        pin_update(safe, '')
                except Exception as e:
                    self.eLog(f"reset {safe} failed: {e}")
            toast('已重置', color='info')

        async def _on_cancel(_b=None):
            if not fut.done():
                fut.set_result(None)
            close_popup()

        with popup(title, closable=False):
            for safe, label, ftype, sv in fields:
                self._put_field(safe, label, sv, ftype)
            put_html('<div style="height:8px;"></div>')
            put_buttons(
                [
                    {'label': '💾 保存', 'value': 'save', 'color': 'primary'},
                    {'label': '🔄 重置', 'value': 'reset'},
                    {'label': '↩️ 取消', 'value': 'cancel'},
                ],
                onclick=[_on_save, _on_reset, _on_cancel],
            )

        return await fut

    # ---------------- 规范弹窗 ----------------
    async def _do_spec(self):
        """
        规范（spec）编辑弹窗。
        - 打开时读出最新 spec，序列化为 JSON 填入 textarea
        - textarea 使用代码模式（语法高亮）
        - 💾 保存：校验 JSON（失败则不关闭，用户可继续修改）
        - ↩️ 取消：直接关闭，不写库
        """
        # 每次进入都从 EntityModel 重新读取
        await self._load_spec()
        default_text = json.dumps(self.spec, ensure_ascii=False, indent=2)

        fut = tornado.gen.Future()

        async def _on_save(_b=None):
            try:
                text = await pin['__spec_text__']
            except Exception:
                text = ''
            text = text or ''

            # 校验 JSON
            if not text.strip():
                new_spec = {}
            else:
                try:
                    obj = json.loads(text)
                except Exception as e:
                    toast(f'JSON 格式错误: {e}', color='error')
                    return  # 不关闭弹窗，让用户修正
                if not isinstance(obj, dict):
                    toast('规范必须是 JSON 对象', color='error')
                    return  # 不关闭弹窗
                new_spec = obj

            if not fut.done():
                fut.set_result(new_spec)
            close_popup()

        async def _on_cancel(_b=None):
            if not fut.done():
                fut.set_result(None)
            close_popup()

        with popup('规范 (JSON)', closable=False):
            put_textarea(
                '__spec_text__',
                value=default_text,
                rows=14,
                code=True,
            )
            put_html('<div style="height:8px;"></div>')
            put_buttons(
                [
                    {'label': '💾 保存', 'value': 'save', 'color': 'primary'},
                    {'label': '↩️ 取消', 'value': 'cancel'},
                ],
                onclick=[_on_save, _on_cancel],
            )

        new_spec = await fut
        if new_spec is None:
            # 用户取消，不做任何写库
            return

        ok = await self._save_spec(new_spec)
        toast('规范已保存' if ok else '规范保存失败',
              color='success' if ok else 'error')
        if ok:
            await self._render_home()

    # ---------------- 渲染 ----------------
    async def render_body(self):
        # 新增与规范都改为弹窗后，body 只渲染主页
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

    # ---------------- 按钮回调 ----------------
    async def on_nav(self, view):
        if view == 'add':
            # 新增走弹窗
            await self._do_add()
            return
        if view == 'spec':
            # 规范走弹窗
            await self._do_spec()
            return
        # 其余情况回主页
        local.view = 'home'
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

    # ---------------- 新增（弹窗） ----------------
    async def _do_add(self):
        # 需要最新 spec 决定字段类型
        await self._load_spec()

        # fields: [(safe, label, ftype, initial_value)]
        fields = [
            # 键字段：永远是文本，初始为空
            ('__key__', '键', 'str', ''),
        ]

        if not self.spec:
            # 无 spec → 单值模式
            fields.append(('__value__', '值', 'str', ''))
        else:
            for i, (sk, sv) in enumerate(self.spec.items()):
                ftype = self._infer_type(None, sv)
                # 新增时的初始值：按 spec 类型给个合理的空值
                if ftype == 'bool':
                    initial = False
                else:
                    initial = None
                fields.append((f'f{i}', sk, ftype, initial))

        values = await self._show_form_popup('新增数据', fields)
        if values is None:
            # 用户取消
            return

        # 解析键
        key = str(values.get('__key__', '') or '').strip()
        if not key:
            toast('键不能为空', color='error'); return
        if key == SPEC_KEY:
            toast(f'"{SPEC_KEY}" 是保留键，请换一个', color='error'); return

        # 构造 value
        if not self.spec:
            value = self._coerce(values.get('__value__'), 'str')
        else:
            value = {}
            for i, (sk, sv) in enumerate(self.spec.items()):
                ftype = self._infer_type(None, sv)
                raw = values.get(f'f{i}')
                value[sk] = self._coerce(raw, ftype)

        ok = await self.setValue(key, value)
        toast('保存成功' if ok else '保存失败',
              color='success' if ok else 'error')
        if ok:
            await self._render_home()

    # ---------------- 编辑（弹窗，与新增风格一致） ----------------
    async def _do_edit(self, key):
        if key == SPEC_KEY:
            # 不允许从数据页编辑 spec，请从 📋 视图改
            toast(f'"{SPEC_KEY}" 为保留键，请用 📋 视图编辑', color='error')
            return

        old = await self.getValue(key)
        if old is None:
            toast(f'键 "{key}" 不存在', color='error'); return

        # 用最新 spec 决定字段类型
        await self._load_spec()

        # fields: [(safe, label, ftype, initial_value)]
        fields = []
        is_dict = isinstance(old, dict)

        if is_dict:
            # 字段集合 = spec 字段 ∪ old 字段（保序：spec 优先）
            names = list(self.spec.keys()) if self.spec else []
            for k in old.keys():
                if k not in names:
                    names.append(k)
            for i, sk in enumerate(names):
                sv = old.get(sk, self.spec.get(sk))
                spec_v = self.spec.get(sk)
                ftype = self._infer_type(sv, spec_v)
                fields.append((f'f{i}', sk, ftype, sv))
        else:
            ftype = self._infer_type(old, None)
            fields.append(('f0', '值', ftype, old))

        values = await self._show_form_popup(f'编辑 "{key}"', fields)
        if values is None:
            # 取消：不做任何写库，弹窗关闭即回到之前的样子
            return

        # 构造最终 value
        if is_dict:
            value = {}
            for safe, label, ftype, _v in fields:
                raw = values.get(safe)
                value[label] = self._coerce(raw, ftype)
        else:
            safe, _label, ftype, _v = fields[0]
            value = self._coerce(values.get(safe), ftype)

        ok = await self.setValue(key, value)
        toast('更新成功' if ok else '更新失败',
              color='success' if ok else 'error')
        await self._render_home()

    # ---------------- 删除 ----------------
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