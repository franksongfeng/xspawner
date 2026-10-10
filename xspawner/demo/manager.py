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
        ref = spec_value if spec_value is not None else value
        if isinstance(ref, bool):
            return 'bool'
        if isinstance(ref, int):
            return 'int'
        if isinstance(ref, float):
            return 'float'
        return 'str'

    def _put_field(self, name: str, label: str, value, ftype: str):
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
        await self._load_spec()
        default_text = json.dumps(self.spec, ensure_ascii=False, indent=2)

        fut = tornado.gen.Future()

        async def _on_save(_b=None):
            try:
                text = await pin['__spec_text__']
            except Exception:
                text = ''
            text = text or ''

            if not text.strip():
                new_spec = {}
            else:
                try:
                    obj = json.loads(text)
                except Exception as e:
                    toast(f'JSON 格式错误: {e}', color='error')
                    return
                if not isinstance(obj, dict):
                    toast('规范必须是 JSON 对象', color='error')
                    return
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
            return

        ok = await self._save_spec(new_spec)
        toast('规范已保存' if ok else '规范保存失败',
              color='success' if ok else 'error')
        if ok:
            await self._render_home()

    # ---------------- 渲染 ----------------
    async def render_body(self):
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
                        f'<div>'
                        f'<details>'
                        f'<summary style="cursor:pointer;color:#007bff;'
                        f'font-weight:bold;">{_esc(k)}</summary>'
                        f'{sub}</details></div>'
                    )
                else:
                    left = (
                        f'<div>'
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
            await self._do_add()
            return
        if view == 'spec':
            await self._do_spec()
            return
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
        await self._load_spec()

        fields = [
            ('__key__', '键', 'str', ''),
        ]

        if not self.spec:
            fields.append(('__value__', '值', 'str', ''))
        else:
            for i, (sk, sv) in enumerate(self.spec.items()):
                ftype = self._infer_type(None, sv)
                initial = False if ftype == 'bool' else None
                fields.append((f'f{i}', sk, ftype, initial))

        values = await self._show_form_popup('新增数据', fields)
        if values is None:
            return

        key = str(values.get('__key__', '') or '').strip()
        if not key:
            toast('键不能为空', color='error'); return
        if key == SPEC_KEY:
            toast(f'"{SPEC_KEY}" 是保留键，请换一个', color='error'); return

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

    # ---------------- 编辑（弹窗） ----------------
    async def _do_edit(self, key):
        if key == SPEC_KEY:
            toast(f'"{SPEC_KEY}" 为保留键，请用 📋 视图编辑', color='error')
            return

        old = await self.getValue(key)
        if old is None:
            toast(f'键 "{key}" 不存在', color='error'); return

        await self._load_spec()

        fields = []
        is_dict = isinstance(old, dict)

        if is_dict:
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
            return

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

            # ---- 全局样式 ----
            put_html(
                '<style>'

                # 1) 数据区外框：整个数据列表被一道线包住
                '#pywebio-scope-body {'
                '  border: 1px solid #e2e2e2;'              # 外框颜色
                '  border-radius: 10px;'                    # 外框圆角大小
                '  overflow: hidden;'
                '  background: #ffffff;'
                '  margin-top: 4px;'
                '}'

                # 2) 数据行：很浅的底板 + 行间分隔线
                #    同时保证左列展开/收起时按钮位置不被带动
                '#pywebio-scope-body .pywebio-scope-row {'
                '  background: #fafafa;'                    # 行底板
                '  padding: 10px 16px;'                     # 行的"厚度": 上下 10px、左右 16px
                '  border-bottom: 1px solid #eeeeee;'       # 行分隔线，位置由行高决定
                '  align-items: flex-start !important;'
                '  transition: background .15s ease;'
                '}'

                # 3) 最后一行去掉底部线，避免和外框重叠
                '#pywebio-scope-body .pywebio-scope-row:last-child {'
                '  border-bottom: none;'
                '}'

                # 4) 悬停时整行轻微加深，提示"这一行可以操作"
                '#pywebio-scope-body .pywebio-scope-row:hover {'
                '  background: #f0f4f8;'                    # 悬停时颜色
                '}'

                # 5) 按钮样式
                '#pywebio-scope-body button {'
                '  padding: 8px 14px !important;'
                '  font-size: 18px !important;'
                '  line-height: 1.2 !important;'
                '  min-height: 0 !important;'
                '  height: auto !important;'
                '  vertical-align: middle;'
                '  margin-top: 2px !important;'
                '}'

                # 6) 表单控件字号
                '#pywebio-scope-body input {'
                '  font-size: 15px !important;'
                '}'

                '</style>'
            )

            put_html(f'<h2 style="margin:6px 0;">'
                     f'Manager {self._config.id}</h2>')

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

            put_scope('body')

            await self.render_body()

            while True:
                await tornado.gen.sleep(60)

        except Exception as e:
            put_error(f"发生未知错误: {e}")
            put_error(traceback.format_exc())