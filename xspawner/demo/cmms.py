# -*- coding: utf-8 -*-
from bokeh.embed import components
from pywebio import config
from pywebio.input import *
from pywebio.output import *
from pywebio.session import *
from pywebio.pin import *
from xspawner.xspawner import *  # NOQA
from xspawner.apps.spawner import *  # NOQA
from xspawner.utilities.log import *  # NOQA
from xspawner.utilities.misc import *  # NOQA
from xspawner import *  # NOQA
from bokeh.plotting import figure, output_notebook
import traceback
import json
import asyncio
import inspect
from collections import defaultdict
from functools import partial


class CMMS(Spawner):  # NOQA
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_nav = "home"
        self.selected_klass = None
        self.selected_subklass = None
        self.selected_alias = None
        self.data = getContent()                     # 必须包含完整数据
        self.expanded_klasses = {item["Klass"] for item in self.data}
        self.expanded_subklasses = set()
        self._menu_queue: asyncio.Queue = None       # 菜单事件队列

    # ─────────────────────────────────────────────────────────────────────
    # 路由入口
    # ─────────────────────────────────────────────────────────────────────
    @UiHandler.route("/")
    @config(theme="yeti")
    async def _(self):
        try:
            set_env(title="CMMS", output_animation=False)

            # 初始化队列并启动后台任务
            self._menu_queue = asyncio.Queue()
            run_async(self._poll_loop())          # 轮询 JS 事件

            put_html(
                """
                <style>
                    html, body { height: 100%; margin: 0; padding: 0; }
                    body { display: flex; flex-direction: column; box-sizing: border-box; }
                    * { box-sizing: border-box; }

                    #pywebio-scope-header,
                    #pywebio-scope-nav,
                    #pywebio-scope-main,
                    #pywebio-scope-statement { border: 1px solid #ccc; }

                    #pywebio-scope-header    { flex-shrink: 0; height: 60px; }
                    #pywebio-scope-nav       { flex-shrink: 0; height: 60px; }
                    #pywebio-scope-main      { flex: 1; min-height: 0; }
                    #pywebio-scope-statement { flex-shrink: 0; height: 40px; }

                    #pywebio-scope-main > div > .row {
                        display: grid !important;
                        grid-template-columns: 250px 1fr !important;
                        height: 100%; margin: 0 !important; padding: 0 !important; gap: 0 !important;
                    }
                    #pywebio-scope-main > div > .row > .column {
                        width: auto !important; max-width: none !important;
                        min-width: 0 !important; height: 100%; overflow: hidden;
                    }
                    #pywebio-scope-main > div > .row > .column:first-child {
                        border-right: 1px solid #ccc; width: 300px !important;
                        flex-shrink: 0 !important; flex-grow: 0 !important;
                        overflow-y: auto; overflow-x: hidden;
                    }
                    #pywebio-scope-main > div > .row > .column:last-child { flex: 1; overflow: auto; }

                    #pywebio-scope-left_panel {
                        width: 100% !important; max-width: 300px !important; overflow: hidden;
                    }
                    #pywebio-scope-left_panel .btn {
                        width: auto !important; min-width: 60px; margin: 2px 8px;
                        padding: 2px 8px; font-size: 12px; white-space: nowrap;
                        overflow: hidden; text-overflow: ellipsis; max-width: 100%;
                    }

                    /* 导航栏样式 */
                    .ncms-nav-bar { display: flex; align-items: center; gap: 8px; padding: 5px 10px; flex-wrap: wrap; }
                    .ncms-nav-bar .btn { margin: 0; }

                    /* 三点按钮样式 */
                    .province-menu-btn {
                        background: none !important;
                        border: none !important;
                        color: #666;
                        font-size: 18px;
                        cursor: pointer;
                        padding: 0 6px;
                        line-height: 1;
                        border-radius: 3px;
                        position: absolute;
                        right: 0;
                        top: 50%;
                        transform: translateY(-50%);
                    }
                    .province-menu-btn:hover {
                        background: #ddd !important;
                        color: #000;
                    }
                    /* 让 summary 成为定位容器 */
                    #pywebio-scope-left_panel details > summary {
                        position: relative;
                        padding-right: 30px;
                    }
                </style>
                """
            )

            # 标题栏
            with use_scope("header", clear=True):
                put_html('<h1 style="text-align:left;color:#2c3e50;padding:10px 20px;margin:0;">CMMS</h1>')

            # 导航栏
            self.render_nav()

            # 主区域（左树 + 右内容）
            with use_scope("main", clear=True):
                with put_row(size="250px 1fr").style("height:100%;margin:0;padding:0;"):
                    with put_column().style(
                            "height:100%;display:flex;flex-direction:column;"
                            "min-width:0;border-right:2px solid #ccc;"
                    ):
                        put_scope("left_panel")
                    with put_column().style("height:100%;min-width:0;"):
                        put_scope("content")

            # 声明栏
            with use_scope("statement", clear=True):
                put_html('<div style="padding:10px 20px;text-align:center;color:#666;">'
                         '© 2026 · CMMS 系统</div>')

            # 构建左侧树
            self.render_tree()

            # 默认显示首页
            self._switch_nav("home")

            # 进入模态循环（阻塞，直到会话结束）
            await self._modal_loop()

            return True   # 确保路由返回

        except Exception:
            put_text("应用内部错误，详情如下：")
            put_text(traceback.format_exc())
            return True

    # ─────────────────────────────────────────────────────────────────────
    # 导航栏
    # ─────────────────────────────────────────────────────────────────────
    def render_nav(self):
        with use_scope("nav", clear=True):
            def _go(nav):
                if nav in ("home", "help"):
                    self._switch_nav(nav)

            btns = [
                dict(
                    label="首页",
                    value="home",
                    color="primary" if self.current_nav == "home" else "dark",
                    outline=(self.current_nav != "home"),
                ),
                dict(
                    label="帮助",
                    value="help",
                    color="primary" if self.current_nav == "help" else "dark",
                    outline=(self.current_nav != "help"),
                ),
            ]
            put_buttons(btns, onclick=_go).style("margin:5px 10px;display:inline-flex;gap:0;")

    # ─────────────────────────────────────────────────────────────────────
    # 左侧树（带三点菜单）
    # ─────────────────────────────────────────────────────────────────────
    def render_tree(self):
        with use_scope("left_panel", clear=True):
            put_html('<h3 style="margin-top:0;padding:10px;flex-shrink:0;border-bottom:1px solid #eee;">元器件类型</h3>')
            if not self.data:
                put_warning("暂无元器件数据。")
                return

            # 按 Klass 分组
            klass_dict = {}
            for item in self.data:
                k = item["Klass"]
                if k not in klass_dict:
                    klass_dict[k] = defaultdict(set)
                klass_dict[k][item["Subklass"]].add(item["Alias"])

            with put_scrollable().style("flex-grow:1;overflow-y:auto;min-height:0;padding:0 10px;"):
                for klass, sub_dict in sorted(klass_dict.items()):
                    with put_collapse(klass, open=(klass in self.expanded_klasses)):
                        for subklass, aliases in sorted(sub_dict.items()):
                            with put_collapse(subklass,
                                              open=((klass, subklass) in self.expanded_subklasses)).style("margin-left:10px;"):
                                for alias in sorted(aliases):
                                    is_selected = (self.selected_klass == klass and
                                                   self.selected_subklass == subklass and
                                                   self.selected_alias == alias)
                                    put_button(
                                        alias,
                                        onclick=partial(self.select_alias, klass, subklass, alias),
                                        outline=not is_selected,
                                        small=True,
                                        color="primary" if is_selected else "dark",
                                    ).style("margin-left:10px;")

        # 注入 JS：三点悬浮菜单
        run_js("""
        (function() {
            if (!window.__cmms_event_queue)   window.__cmms_event_queue   = [];
            if (window.__cmms_polling_paused === undefined) window.__cmms_polling_paused = false;

            function pushEvent(type, klass, subklass, action) {
                if (window.__cmms_polling_paused) return;
                var ev = {type: type, klass: klass, subklass: subklass};
                if (action) ev.action = action;
                window.__cmms_event_queue.push(ev);
            }

            function extractNodeText(summary) {
                var text = summary.innerText || summary.textContent;
                return text.replace('⋮', '').trim();
            }

            function bindEvents() {
                var panel = document.getElementById('pywebio-scope-left_panel');
                if (!panel) return;

                // 悬浮菜单（只创建一次）
                if (!window.__cmms_floating_menu) {
                    var menu = document.createElement('div');
                    menu.id = '__cmms_floating_menu';
                    menu.style.position = 'fixed';
                    menu.style.zIndex = '99999';
                    menu.style.minWidth = '120px';
                    menu.style.background = '#fff';
                    menu.style.border = '1px solid #ddd';
                    menu.style.borderRadius = '6px';
                    menu.style.boxShadow = '0 8px 24px rgba(0,0,0,0.12)';
                    menu.style.padding = '6px';
                    menu.style.display = 'none';
                    menu.style.fontSize = '13px';
                    menu.innerHTML = [
                        '<div data-action="create" style="padding:6px 8px; cursor:pointer; border-radius:4px;">创建内容</div>',
                        '<div data-action="delete" style="padding:6px 8px; cursor:pointer; border-radius:4px; color:#c0392b;">删除内容</div>'
                    ].join('');
                    menu.addEventListener('mouseover', function(e){
                        var item = e.target && e.target.getAttribute && e.target.getAttribute('data-action');
                        if (!item) return;
                        e.target.style.background = '#f4f6f8';
                    });
                    menu.addEventListener('mouseout', function(e){
                        var item = e.target && e.target.getAttribute && e.target.getAttribute('data-action');
                        if (!item) return;
                        e.target.style.background = '';
                    });
                    menu.addEventListener('mousedown', function(e){ e.stopPropagation(); });
                    menu.addEventListener('click', function(e){
                        e.stopPropagation();
                        var act = e.target && e.target.getAttribute && e.target.getAttribute('data-action');
                        if (!act) return;
                        var klass = menu.getAttribute('data-klass');
                        var subklass = menu.getAttribute('data-subklass');
                        menu.style.display = 'none';
                        if (!klass || !subklass) return;
                        pushEvent('menu_action', klass, subklass, act);
                    });
                    document.body.appendChild(menu);
                    document.addEventListener('mousedown', function(e){
                        if (menu.contains(e.target)) return;
                        menu.style.display = 'none';
                    }, false);
                    window.__cmms_floating_menu = menu;
                    window.__cmms_menu_hide_timer = null;
                }

                // 为每个 Subklass 的 summary 绑定菜单
                panel.querySelectorAll('details > summary').forEach(function(summary) {
                    if (summary.getAttribute('data-cmms-bound')) return;
                    summary.setAttribute('data-cmms-bound', 'true');

                    var detail = summary.parentElement;
                    var isTopLevel = detail.parentElement &&
                                     detail.parentElement.closest('details') === null;

                    // 只为二级节点（Subklass）添加三点按钮
                    if (!isTopLevel) {
                        var subklass = extractNodeText(summary);
                        if (subklass) summary.setAttribute('data-subklass', subklass);

                        var parentDetail = detail.parentElement.closest('details');
                        var klass = '';
                        if (parentDetail) {
                            var parentSummary = parentDetail.querySelector('summary');
                            if (parentSummary) {
                                klass = extractNodeText(parentSummary);
                                summary.setAttribute('data-klass', klass);
                            }
                        }

                        if (!summary.querySelector('.province-menu-btn')) {
                            var btn = document.createElement('button');
                            btn.className = 'province-menu-btn';
                            btn.innerHTML = '⋮';
                            btn.title = '操作菜单';
                            btn.addEventListener('mousedown', function(e){ e.stopPropagation(); });

                            function showMenuAt(x, y) {
                                var klass = summary.getAttribute('data-klass');
                                var subklass = summary.getAttribute('data-subklass');
                                if (!klass || !subklass) return;
                                var menu = window.__cmms_floating_menu;
                                menu.setAttribute('data-klass', klass);
                                menu.setAttribute('data-subklass', subklass);
                                var vw = window.innerWidth, vh = window.innerHeight;
                                menu.style.display = 'block';
                                var rect = menu.getBoundingClientRect();
                                if (x + rect.width > vw - 8) x = vw - rect.width - 8;
                                if (y + rect.height > vh - 8) y = vh - rect.height - 8;
                                menu.style.left = x + 'px';
                                menu.style.top = y + 'px';
                            }
                            function cancelHide() {
                                if (window.__cmms_menu_hide_timer) {
                                    clearTimeout(window.__cmms_menu_hide_timer);
                                    window.__cmms_menu_hide_timer = null;
                                }
                            }
                            function scheduleHide() {
                                cancelHide();
                                window.__cmms_menu_hide_timer = setTimeout(function(){
                                    window.__cmms_floating_menu.style.display = 'none';
                                }, 180);
                            }
                            btn.addEventListener('mouseenter', function(e) {
                                e.stopPropagation();
                                e.preventDefault();
                                cancelHide();
                                showMenuAt(e.clientX, e.clientY);
                            });
                            btn.addEventListener('mouseleave', function(e){
                                scheduleHide();
                            });
                            window.__cmms_floating_menu.addEventListener('mouseenter', cancelHide);
                            window.__cmms_floating_menu.addEventListener('mouseleave', scheduleHide);
                            btn.addEventListener('click', function(e){
                                e.stopPropagation();
                                e.preventDefault();
                                cancelHide();
                                showMenuAt(e.clientX, e.clientY);
                            });
                            summary.appendChild(btn);
                        }
                    }
                });
            }

            setTimeout(function() {
                bindEvents();
                var panel = document.getElementById('pywebio-scope-left_panel');
                if (panel) {
                    new MutationObserver(bindEvents)
                        .observe(panel, {childList: true, subtree: true});
                }
            }, 400);
        })();
        """)

    # ─────────────────────────────────────────────────────────────────────
    # 事件轮询与分发
    # ─────────────────────────────────────────────────────────────────────
    async def _eval_js_compat(self, expression: str):
        res = eval_js(expression)
        if inspect.isawaitable(res):
            return await res
        else:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(res)
            return await future

    async def _poll_loop(self):
        while True:
            await asyncio.sleep(0.2)
            try:
                raw = await self._eval_js_compat(r"""
                (function(){
                  try{
                    if(!window.__cmms_event_queue) window.__cmms_event_queue = [];
                    var q = window.__cmms_event_queue;
                    window.__cmms_event_queue = [];
                    return JSON.stringify({ok: true, q: q});
                  }catch(e){
                    return JSON.stringify({ok: false, err: String(e && (e.stack||e.message||e))});
                  }
                })()
                """)

                if not raw:
                    continue

                payload = json.loads(raw)
                if not payload.get('ok', False):
                    await asyncio.sleep(1)
                    continue

                events = payload.get('q') or []

                for event in events:
                    self._dispatch_sync(event)
            except Exception as e:
                toast(f"轮询异常: {str(e)}", color='error', duration=1)
                await asyncio.sleep(1)

    def _dispatch_sync(self, event):
        try:
            etype = event.get('type', '')
            if etype == 'nav_click':
                return
            if etype == 'menu_action':
                klass = event.get('klass', '')
                subklass = event.get('subklass', '')
                action = event.get('action', '')
                if not klass or not subklass or not action:
                    return
                try:
                    self._menu_queue.put_nowait((klass, subklass, action))
                except asyncio.QueueFull:
                    pass
        except Exception as e:
            toast(f"分发事件异常: {str(e)}", color='error')

    async def _modal_loop(self):
        while True:
            try:
                klass, subklass, action = await self._menu_queue.get()
                run_js("window.__cmms_polling_paused = true;")
                try:
                    if action == 'create':
                        self.show_create_popup(klass, subklass)
                    elif action == 'delete':
                        self.show_delete_popup(klass, subklass)
                except Exception as e:
                    toast(f"菜单处理出错: {e}", color='error')
                    traceback.print_exc()
                finally:
                    run_js(
                        "window.__cmms_polling_paused = false;"
                        "window.__cmms_event_queue = [];"
                    )
                    self._menu_queue.task_done()
            except Exception as e:
                toast(f"菜单循环异常: {e}", color='error')
                await asyncio.sleep(1)

    # ─────────────────────────────────────────────────────────────────────
    # 创建 / 删除弹窗
    # ─────────────────────────────────────────────────────────────────────
    def _get_aliases(self, klass, subklass):
        return sorted({d['Alias'] for d in self.data
                       if d['Klass'] == klass and d['Subklass'] == subklass})

    def show_create_popup(self, klass: str, subklass: str):
        popup(f"创建内容 - {klass} / {subklass}", [
            put_row([
                put_text("Alias").style("width:90px;"),
                put_input(label="", name="cmms_c_alias", type=TEXT, value="").style("width:260px;"),
            ]),
            put_row([
                put_text("C").style("width:90px;"),
                put_input(label="", name="cmms_c_c", type=TEXT, value="").style("width:260px;"),
            ]),
            put_row([
                put_text("L").style("width:90px;"),
                put_input(label="", name="cmms_c_l", type=TEXT, value="").style("width:260px;"),
            ]),
            put_row([
                put_text("R1").style("width:90px;"),
                put_input(label="", name="cmms_c_r1", type=TEXT, value="").style("width:260px;"),
            ]),
            put_row([
                put_text("R2").style("width:90px;"),
                put_input(label="", name="cmms_c_r2", type=TEXT, value="").style("width:260px;"),
            ]),
            put_row([
                put_buttons(
                    [
                        {"label": "确定", "value": "ok", "color": "primary"},
                        {"label": "取消", "value": "cancel", "color": "secondary", "outline": True},
                    ],
                    onclick=lambda v: self._handle_create_popup_submit(v, klass, subklass),
                )
            ]).style("justify-content:center; padding-top:10px;"),
        ])

    def _handle_create_popup_submit(self, v: str, klass: str, subklass: str):
        if v != "ok":
            close_popup()
            return

        alias = (pin.cmms_c_alias or "").strip()
        c = (pin.cmms_c_c or "").strip()
        l = (pin.cmms_c_l or "").strip()
        r1 = (pin.cmms_c_r1 or "").strip()
        r2 = (pin.cmms_c_r2 or "").strip()

        if not alias or not c or not l or not r1 or not r2:
            toast("请填写所有字段", color="error")
            return

        self.data.append({
            "Klass": klass,
            "Subklass": subklass,
            "Alias": alias,
            "C": c,
            "L": l,
            "R1": r1,
            "R2": r2,
        })
        self.expanded_klasses.add(klass)
        self.expanded_subklasses.add((klass, subklass))
        self.render_tree()
        with use_scope('content', clear=True):
            put_markdown(f"已添加 **{klass} / {subklass} / {alias}**")
        close_popup()
        toast("创建成功", color="success")

    def show_delete_popup(self, klass: str, subklass: str):
        aliases = self._get_aliases(klass, subklass)
        if not aliases:
            toast("该分类下没有可删除的 Alias", color="warning")
            return

        popup(f"删除内容 - {klass} / {subklass}", [
            put_markdown(f"选择要删除的 Alias："),
            put_row([
                put_text("Alias").style("width:90px;"),
                put_select(
                    label="",
                    options=aliases,
                    name="cmms_d_alias",
                    value=aliases[0]
                ).style("width:260px;"),
            ]),
            put_row([
                put_buttons(
                    [
                        {"label": "确定", "value": "ok", "color": "primary"},
                        {"label": "取消", "value": "cancel", "color": "secondary", "outline": True},
                    ],
                    onclick=lambda v: self._handle_delete_popup_submit(v, klass, subklass),
                )
            ]).style("justify-content:center; padding-top:10px;"),
        ])

    def _handle_delete_popup_submit(self, v: str, klass: str, subklass: str):
        if v != "ok":
            close_popup()
            return

        alias = pin.cmms_d_alias
        if not alias:
            toast("请选择 Alias", color="error")
            return

        before = len(self.data)
        self.data = [d for d in self.data
                     if not (d["Klass"] == klass and d["Subklass"] == subklass and d["Alias"] == alias)]
        after = len(self.data)
        if after == before:
            toast("未找到对应记录", color="warning")
            return

        if (self.selected_klass == klass and self.selected_subklass == subklass and
                self.selected_alias == alias):
            self.selected_klass = None
            self.selected_subklass = None
            self.selected_alias = None

        self.expanded_klasses.add(klass)
        self.expanded_subklasses.add((klass, subklass))
        self.render_tree()
        with use_scope('content', clear=True):
            put_markdown(f"已删除 **{klass} / {subklass} / {alias}**")
        close_popup()
        toast("删除成功", color="success")

    # ─────────────────────────────────────────────────────────────────────
    # 原有功能：选中 Alias 显示详情
    # ─────────────────────────────────────────────────────────────────────
    def select_alias(self, klass, subklass, alias):
        self.selected_klass = klass
        self.selected_subklass = subklass
        self.selected_alias = alias
        self.expanded_subklasses.add((klass, subklass))

        detail = None
        for item in self.data:
            if item["Klass"] == klass and item["Subklass"] == subklass and item["Alias"] == alias:
                detail = item
                break

        with use_scope("content", clear=True):
            if not detail:
                put_warning("未找到对应的元器件记录。")
            else:
                rows = [
                    ["Klass", detail["Klass"]],
                    ["Subklass", detail["Subklass"]],
                    ["Alias", detail["Alias"]],
                    ["C", detail["C"]],
                    ["L", detail["L"]],
                    ["R1", detail["R1"]],
                    ["R2", detail["R2"]],
                ]
                put_html(f"<h3>{klass} / {subklass} / {alias}</h3>")
                put_table(rows)

    # ─────────────────────────────────────────────────────────────────────
    # 内容区：主页 / 帮助
    # ─────────────────────────────────────────────────────────────────────
    def _show_home(self):
        with use_scope("content", clear=True):
            headers = ["Klass", "Subklass", "Alias", "C", "L", "R1", "R2"]
            rows = [[item[h] for h in headers] for item in self.data]
            put_markdown("### 全部元器件数据")
            put_table([headers] + rows)

    def _show_help(self):
        with use_scope("content", clear=True):
            put_markdown(
                "### 帮助\n\n"
                "1. 在左侧树中展开 `Klass`（元器件类型）、`Subklass`，然后点击具体的 `Alias` 节点。\n"
                "2. 右侧会以“字段-值”列表形式显示该元器件的参数：`C`、`L`、`R1`、`R2` 等。\n"
                "3. 将鼠标悬停在 **Subklass** 右侧的 `⋮` 按钮上，可弹出菜单：\n"
                "   - **创建内容**：在当前 Subklass 下添加新的 Alias\n"
                "   - **删除内容**：从当前 Subklass 中删除指定的 Alias\n"
            )

    def _switch_nav(self, target):
        self.current_nav = target
        self.render_nav()
        if target == "home":
            self._show_home()
        elif target == "help":
            self._show_help()


# ─────────────────────────────────────────────────────────────────────────
# 完整数据
# ─────────────────────────────────────────────────────────────────────────
def getContent():
    return [
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102", "C": "1.91408n", "L": "21.5334n", "R1": "154.333m", "R2": "639.911"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102-2", "C": "1.66931n", "L": "20.8815n", "R1": "137.873m", "R2": "112.808"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102-3", "C": "1.63232n", "L": "20.0566n", "R1": "145.178m", "R2": "111.691"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102-4", "C": "1.65614n", "L": "23.0144n", "R1": "146.862m", "R2": "113.669"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102-5", "C": "1.573n", "L": "20.9977n", "R1": "151.131m", "R2": "115.979"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "102-6", "C": "1.63075n", "L": "22.0932n", "R1": "153.949m", "R2": "114.967"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103", "C": "13.5062n", "L": "25.4068n", "R1": "82.9408m", "R2": "587.209"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103-2", "C": "14.1008n", "L": "26.473n", "R1": "103.826m", "R2": "107.175"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103-3", "C": "13.9728n", "L": "24.066n", "R1": "97.4698m", "R2": "113.502"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103-4", "C": "14.2953n", "L": "24.3418n", "R1": "77.3342m", "R2": "108.932"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103-5", "C": "14.3908n", "L": "26.7297n", "R1": "103.863m", "R2": "111.251"},
        {"Klass": "Capacitor", "Subklass": "CBB", "Alias": "103-6", "C": "14.4653n", "L": "25.6528n", "R1": "94.219m", "R2": "110.668"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474", "C": "383.664n", "L": "48.0737n", "R1": "47.0632m", "R2": "26.0269"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474-2", "C": "461.276n", "L": "41.7251n", "R1": "56.9177m", "R2": "21.3232"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474-3", "C": "457.3n", "L": "42.088n", "R1": "54.3047m", "R2": "21.6074"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474-4", "C": "453.6n", "L": "42.4312n", "R1": "59.4836m", "R2": "21.8969"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474-5", "C": "459.93n", "L": "41.8473n", "R1": "60.9386m", "R2": "21.2618"},
        {"Klass": "Capacitor", "Subklass": "安规X电容", "Alias": "WDDZ-474-6", "C": "479.769n", "L": "52.7969n", "R1": "70.0091m", "R2": "22.0113"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102", "C": "972.903p", "L": "970.143p", "R1": "787.451m", "R2": "236.139"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102-2", "C": "970.143p", "L": "19.7749n", "R1": "787.451m", "R2": "236.139"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102-3", "C": "957.861p", "L": "19.3292n", "R1": "799.277m", "R2": "271.669"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102-4", "C": "945.771p", "L": "20.7554n", "R1": "772.487m", "R2": "287.744"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102-5", "C": "935.566p", "L": "20.5728n", "R1": "787.694m", "R2": "302.547"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "102-6", "C": "940.349p", "L": "20.8064n", "R1": "797.769m", "R2": "321.436"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103", "C": "9.80949n", "L": "34.9814n", "R1": "191.791m", "R2": "998.052"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103-2", "C": "12.1152n", "L": "39.9796n", "R1": "236.035m", "R2": "229.506"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103-3", "C": "11.9664n", "L": "38.6205n", "R1": "228.121m", "R2": "253.259"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103-4", "C": "11.7514n", "L": "38.0554n", "R1": "230.725m", "R2": "229.831"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103-5", "C": "11.98n", "L": "40.1576n", "R1": "234.286m", "R2": "222.898"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "103-6", "C": "11.8041n", "L": "39.4123n", "R1": "233.647m", "R2": "261.98"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332", "C": "3.06005n", "L": "40.5469n", "R1": "281.149m", "R2": "2952.11"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332-2", "C": "3.92515n", "L": "38.7858n", "R1": "326.201m", "R2": "279.491"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332-3", "C": "3.91281n", "L": "39.0565n", "R1": "323.575m", "R2": "259.745"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332-4", "C": "4.01282n", "L": "39.7314n", "R1": "325.657m", "R2": "292.162"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332-5", "C": "4.0811n", "L": "39.9954n", "R1": "331.649m", "R2": "249.819"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "332-6", "C": "3.92702n", "L": "39.6675n", "R1": "327.142m", "R2": "310.143"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472", "C": "5.27457n", "L": "28.3218n", "R1": "287.017m", "R2": "261.817"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472-2", "C": "5.22861n", "L": "30.4928n", "R1": "296.981m", "R2": "275.78"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472-3", "C": "5.58388n", "L": "33.0178n", "R1": "332.815m", "R2": "275.605"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472-4", "C": "5.44809n", "L": "32.2027n", "R1": "311.845m", "R2": "270.622"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472-5", "C": "5.5999n", "L": "33.7661n", "R1": "329.247m", "R2": "247.246"},
        {"Klass": "Capacitor", "Subklass": "安规Y电容", "Alias": "472-6", "C": "5.49343n", "L": "31.1686n", "R1": "307.582m", "R2": "279.678"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N-2", "C": "5.10935n", "L": "51.0276n", "R1": "869.745m", "R2": "183.914"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N-3", "C": "5.0853n", "L": "51.5251n", "R1": "611.7m", "R2": "186.838"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N-4", "C": "5.93368n", "L": "42.865n", "R1": "405.317m", "R2": "184.539"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N-5", "C": "5.11542n", "L": "50.967n", "R1": "446.136m", "R2": "190.212"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N-6", "C": "3.58863n", "L": "73.0142n", "R1": "474.355m", "R2": "1174.82"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "3.3N", "C": "2.66088n", "L": "97.2158n", "R1": "641.079m", "R2": "4433.88"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "33P-2", "C": "95.8478p", "L": "32.9605n", "R1": "605.463m", "R2": "799.119"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "33P-3", "C": "104.015p", "L": "30.9663n", "R1": "593.079m", "R2": "893.024"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "33P-4", "C": "108.767p", "L": "29.139n", "R1": "686.369m", "R2": "863.906"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "33P-5", "C": "108.267p", "L": "29.7502n", "R1": "580.502m", "R2": "857.169"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N-2", "C": "7.48376n", "L": "55.3422n", "R1": "275.441m", "R2": "179.882"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N-3", "C": "7.4095n", "L": "58.0621n", "R1": "344.636m", "R2": "178.602"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N-4", "C": "6.13448n", "L": "54.8162n", "R1": "398.835m", "R2": "181.504"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N-5", "C": "7.48448n", "L": "57.4804n", "R1": "330.741m", "R2": "184.671"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N-6", "C": "4.53916n", "L": "74.0818n", "R1": "382.726m", "R2": "1011.63"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7N", "C": "3.5909n", "L": "98.2998n", "R1": "430.149m", "R2": "6340.13"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7U-2", "C": "2.68236u", "L": "58.6224n", "R1": "196.028m", "R2": "258.164m"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7U-4", "C": "2.07046u", "L": "52.4892n", "R1": "176.018m", "R2": "323.853m"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7U-5", "C": "2.95745u", "L": "109.562n", "R1": "367.56m", "R2": "146.712m"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7U-6", "C": "3.17268u", "L": "67.9573n", "R1": "77.0053m", "R2": "390.791m"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "4.7U", "C": "2.91067u", "L": "76.0882n", "R1": "103.477m", "R2": "407.207m"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N-2", "C": "516.919n", "L": "78.0204n", "R1": "79.9174m", "R2": "3.89366"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N-3", "C": "530.184n", "L": "75.1994n", "R1": "77.2717m", "R2": "3.7618"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N-4", "C": "525.888n", "L": "80.1928n", "R1": "229.173m", "R2": "3.50756"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N-5", "C": "519.603n", "L": "79.4376n", "R1": "106.332m", "R2": "3.80441"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N-6", "C": "514.574n", "L": "76.6005n", "R1": "78.2162m", "R2": "3.87533"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470N", "C": "489.12n", "L": "72.2652n", "R1": "143.088m", "R2": "3.6623"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P-2", "C": "646.104p", "L": "59.5576n", "R1": "1.04895", "R2": "1735.37"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P-3", "C": "643.382p", "L": "59.9242n", "R1": "1.0581", "R2": "1765.84"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P-4", "C": "581.783p", "L": "64.65n", "R1": "1.08912", "R2": "1740.73"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P-5", "C": "563.964p", "L": "67.5842n", "R1": "1.06462", "R2": "3386.61"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P-6", "C": "564.818p", "L": "66.5918n", "R1": "1.09592", "R2": "2116.16"},
        {"Klass": "Capacitor", "Subklass": "贴片电容0805", "Alias": "470P", "C": "775.679p", "L": "48.8173n", "R1": "1.07897", "R2": "4928.34"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N", "C": "1.21708n", "L": "67.6804n", "R1": "762.397m", "R2": "1247.77"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N-2", "C": "1.27279n", "L": "65.8184n", "R1": "752.407m", "R2": "1588.44"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N-3", "C": "1.29166n", "L": "67.1102n", "R1": "772.267m", "R2": "1587.93"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N-4", "C": "1.23421n", "L": "66.7414n", "R1": "787.984m", "R2": "1625.43"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N-5", "C": "1.24919n", "L": "67.0621n", "R1": "778.47m", "R2": "1652.89"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "1N-6", "C": "1.15677n", "L": "71.2093n", "R1": "765.433m", "R2": "2635.81"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N", "C": "2.6977n", "L": "68.3425n", "R1": "441.523m", "R2": "1023.89"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N-2", "C": "2.71726n", "L": "67.8506n", "R1": "430.431m", "R2": "2048.51"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N-3", "C": "2.67179n", "L": "67.3041n", "R1": "441.766m", "R2": "2094.94"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N-4", "C": "2.66695n", "L": "67.4262n", "R1": "446.188m", "R2": "2072.27"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N-5", "C": "2.66695n", "L": "67.4262n", "R1": "446.188m", "R2": "2072.27"},
        {"Klass": "Capacitor", "Subklass": "贴片电容1206", "Alias": "2.2N-6", "C": "2.72856n", "L": "67.5694n", "R1": "438.553m", "R2": "2059.5"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "CBB21-B\\104K630V", "C": "93.46n", "L": "20.2094n", "R1": "152.854m", "R2": "116.655"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "CBB21-B\\224K630V", "C": "184.127n", "L": "18.3828n", "R1": "66.4781m", "R2": "50.9864"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "CBB21-B\\474K630V", "C": "405.41n", "L": "19.4894n", "R1": "69.1145m", "R2": "22.6535"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "CBB21-B\\105K630V", "C": "863.468n", "L": "16.1024n", "R1": "37.5294m", "R2": "10.2552"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "MPX/DAIN.40/110/21/C 0.15uFK 275V~X2", "C": "129.5n", "L": "17.5974n", "R1": "71.5726m", "R2": "64.5207"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "MPX/DAIN.40/110/21/C 0.22uFK 275V~X2", "C": "197.501n", "L": "19.1888n", "R1": "57.8067m", "R2": "43.8975"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "MPX/DAIN.40/110/21/C 0.33uFK 275V~X2", "C": "290.375n", "L": "17.3133n", "R1": "50.6102m", "R2": "30.4849"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "MPX/DAIN.40/110/21/C 0.47uFK 275V~X2", "C": "405.41n", "L": "19.4894n", "R1": "69.1145m", "R2": "22.6535"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "MPX/DAIN.40/110/21/C 0.68uFK 275V~X2", "C": "587.704n", "L": "21.1295n", "R1": "37.424m", "R2": "14.919"},
        {"Klass": "Capacitor", "Subklass": "MY电容样例", "Alias": "IEC60384-14 40/110/58/B 2.2uF MPX K 275V~X2", "C": "1.99717u", "L": "32.9375n", "R1": "40.562m", "R2": "4.39679"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223", "C": "20.5732n", "L": "41.1994n", "R1": "158.042m", "R2": "682.288"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223-2", "C": "26.9045n", "L": "28.141n", "R1": "108.714m", "R2": "99.8884"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223-3", "C": "27.3091n", "L": "27.724n", "R1": "123.237m", "R2": "98.1673"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223-4", "C": "247.226n", "L": "40.588n", "R1": "49.597m", "R2": "35.3706"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223-5", "C": "241.835n", "L": "41.4929n", "R1": "52.6585m", "R2": "35.4887"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-223-6", "C": "27.4281n", "L": "23.8266n", "R1": "96.7202m", "R2": "98.3845"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224", "C": "234.836n", "L": "34.6099n", "R1": "51.9495m", "R2": "43.2864"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224-2", "C": "243.736n", "L": "41.1693n", "R1": "50.2936m", "R2": "36.0282"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224-3", "C": "242.988n", "L": "42.6015n", "R1": "57.5856m", "R2": "34.9102"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224-4", "C": "247.226n", "L": "40.588n", "R1": "49.597m", "R2": "35.3706"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224-5", "C": "241.835n", "L": "41.4929n", "R1": "52.6585m", "R2": "35.4887"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-224-6", "C": "238.754n", "L": "42.0283n", "R1": "51.4444m", "R2": "36.8233"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333", "C": "29.4358n", "L": "33.1648n", "R1": "104.031m", "R2": "423.51"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333-2", "C": "40.7347n", "L": "29.3045n", "R1": "96.6498m", "R2": "92.015"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333-3", "C": "39.9853n", "L": "24.8589n", "R1": "87.2727m", "R2": "90.8443"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333-4", "C": "40.0735n", "L": "26.3166n", "R1": "89.3562m", "R2": "94.6542"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333-5", "C": "39.7888n", "L": "24.7406n", "R1": "89.3262m", "R2": "94.3687"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-333-6", "C": "39.762n", "L": "26.5228n", "R1": "95.6563m", "R2": "87.7158"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473", "C": "41.8358n", "L": "35.6524n", "R1": "82.8729m", "R2": "267.299"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473-2", "C": "55.192n", "L": "24.404n", "R1": "63.4462m", "R2": "84.9389"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473-3", "C": "55.942n", "L": "25.7985n", "R1": "67.0762m", "R2": "82.9306"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473-4", "C": "55.5738n", "L": "29.6687n", "R1": "76.7798m", "R2": "86.4274"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473-5", "C": "55.4197n", "L": "27.6364n", "R1": "74.2297m", "R2": "83.1029"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "WDDZ-473-6", "C": "54.5331n", "L": "28.4277n", "R1": "70.1993m", "R2": "83.4835"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "宝仁弘1918L-802P", "C": "3.21901p", "L": "9.80181m", "R1": "28871.4", "R2": "10.2109m"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "宝仁弘1515L-602P", "C": "2.75735p", "L": "5.18701m", "R1": "10202.4", "R2": "3.84479m"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "宝仁弘1212L-253P", "C": "1.70546p", "L": "15.1804m", "R1": "34390.9", "R2": "41.3021m"},
        {"Klass": "Inductor", "Subklass": "MY电感样例", "Alias": "宝仁弘10.5-1OmH ", "C": "16.4691p", "L": "9.59122m", "R1": "21045.1", "R2": "19.3399m"}
    ]
