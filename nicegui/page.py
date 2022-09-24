from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from functools import wraps
from typing import Callable, Optional

import justpy as jp
from addict import Dict
from pygments.formatters import HtmlFormatter
from starlette.requests import Request

from . import globals
from .helpers import is_coroutine
from .page_builder import PageBuilder


class Page(jp.QuasarPage):

    def __init__(self,
                 title: Optional[str] = None,
                 *,
                 favicon: Optional[str] = None,
                 dark: Optional[bool] = ...,
                 classes: str = 'q-ma-md column items-start',
                 css: str = HtmlFormatter().get_style_defs('.codehilite'),
                 on_connect: Optional[Callable] = None,
                 on_page_ready: Optional[Callable] = None,
                 on_disconnect: Optional[Callable] = None,
                 shared: bool = False,
                 ):
        super().__init__()

        if globals.config:
            self.title = title or globals.config.title
            self.favicon = favicon or globals.config.favicon
            self.dark = dark if dark is not ... else globals.config.dark
        else:
            self.title = title
            self.favicon = favicon
            self.dark = dark if dark is not ... else None
        self.tailwind = True  # use Tailwind classes instead of Quasars
        self.css = css
        self.connect_handler = on_connect
        self.page_ready_handler = on_page_ready
        self.disconnect_handler = on_disconnect
        self.delete_flag = not shared

        self.waiting_javascript_commands: dict[str, str] = {}
        self.on('result_ready', self.handle_javascript_result)
        self.on('page_ready', self.handle_page_ready)

        self.view = jp.Div(a=self, classes=classes, style='row-gap: 1em', temp=False)
        self.view.add_page(self)

    async def _route_function(self, request: Request):
        for handler in globals.connect_handlers + ([self.connect_handler] if self.connect_handler else []):
            arg_count = len(inspect.signature(handler).parameters)
            is_coro = is_coroutine(handler)
            if arg_count == 1:
                await handler(request) if is_coro else handler(request)
            elif arg_count == 0:
                await handler() if is_coro else handler()
            else:
                raise ValueError(f'invalid number of arguments (0 or 1 allowed, got {arg_count})')
        return self

    async def handle_page_ready(self, msg: Dict) -> bool:
        if self.page_ready_handler:
            arg_count = len(inspect.signature(self.page_ready_handler).parameters)
            is_coro = is_coroutine(self.page_ready_handler)
            if arg_count == 1:
                await self.page_ready_handler(msg.websocket) if is_coro else self.page_ready_handler(msg.websocket)
            elif arg_count == 0:
                await self.page_ready_handler() if is_coro else self.page_ready_handler()
            else:
                raise ValueError(f'invalid number of arguments (0 or 1 allowed, got {arg_count})')
        return False

    async def on_disconnect(self, websocket=None) -> None:
        for handler in globals.disconnect_handlers + ([self.disconnect_handler] if self.disconnect_handler else[]):
            arg_count = len(inspect.signature(handler).parameters)
            is_coro = is_coroutine(handler)
            if arg_count == 1:
                await handler(websocket) if is_coro else handler(websocket)
            elif arg_count == 0:
                await handler() if is_coro else handler()
            else:
                raise ValueError(f'invalid number of arguments (0 or 1 allowed, got {arg_count})')
        await super().on_disconnect(websocket)

    async def await_javascript(self, code: str, *, check_interval: float = 0.01, timeout: float = 1.0) -> str:
        start_time = time.time()
        request_id = str(uuid.uuid4())
        await self.run_javascript(code, request_id=request_id)
        while request_id not in self.waiting_javascript_commands:
            if time.time() > start_time + timeout:
                raise TimeoutError('JavaScript did not respond in time')
            await asyncio.sleep(check_interval)
        return self.waiting_javascript_commands.pop(request_id)

    def handle_javascript_result(self, msg) -> bool:
        self.waiting_javascript_commands[msg.request_id] = msg.result
        return False


def add_head_html(self, html: str) -> None:
    for page in find_parent_view().pages.values():
        page.head_html += html


def add_body_html(self, html: str) -> None:
    for page in find_parent_view().pages.values():
        page.body_html += html


async def run_javascript(self, code: str) -> None:
    for page in find_parent_view().pages.values():
        await page.run_javascript(code)


async def await_javascript(self, code: str, *, check_interval: float = 0.01, timeout: float = 1.0) -> None:
    for page in find_parent_view().pages.values():
        return await page.await_javascript(code)


def page(self,
         route: str,
         title: Optional[str] = None,
         *,
         favicon: Optional[str] = None,
         dark: Optional[bool] = ...,
         classes: str = 'q-ma-md column items-start',
         css: str = HtmlFormatter().get_style_defs('.codehilite'),
         on_connect: Optional[Callable] = None,
         on_page_ready: Optional[Callable] = None,
         on_disconnect: Optional[Callable] = None,
         shared: bool = False,
         ):
    """Page

    Creates a new page at the given route.

    :param route: route of the new page (path must start with '/')
    :param title: optional page title
    :param favicon: optional favicon
    :param dark: whether to use Quasar's dark mode (defaults to `dark` argument of `run` command)
    :param classes: tailwind classes for the container div (default: `'q-ma-md column items-start'`)
    :param css: CSS definitions
    :param on_connect: optional function or coroutine which is called for each new client connection
    :param on_page_ready: optional function or coroutine which is called when the websocket is connected
    :param on_disconnect: optional function or coroutine which is called when a client disconnects
    :param shared: whether the page instance is shared between multiple clients (default: `False`)
    """
    def decorator(func):
        @wraps(func)
        async def decorated():
            page = Page(
                title=title,
                favicon=favicon,
                dark=dark,
                classes=classes,
                css=css,
                on_connect=on_connect,
                on_page_ready=on_page_ready,
                on_disconnect=on_disconnect,
                shared=shared,
            )
            with globals.within_view(page.view):
                await func() if is_coroutine(func) else func()
            return page
        builder = PageBuilder(decorated, shared)
        if globals.server:
            builder.create_route(route)
        globals.page_builders[route] = builder
        return decorated
    return decorator


def find_parent_view() -> jp.HTMLBaseComponent:
    view_stack = globals.get_view_stack()
    if not view_stack:
        if globals.loop and globals.loop.is_running():
            raise RuntimeError('cannot find parent view, view stack is empty')
        page = Page(shared=True)
        view_stack.append(page.view)
        jp.Route('/', page._route_function)
    return view_stack[-1]


def error404() -> jp.QuasarPage:
    title = globals.config.title if globals.config else '404'
    favicon = globals.config.favicon if globals.config else None
    dark = globals.config.dark if globals.config else False
    wp = jp.QuasarPage(title=title, favicon=favicon, dark=dark, tailwind=True)
    div = jp.Div(a=wp, classes='py-20 text-center')
    jp.Div(a=div, classes='text-8xl py-5', text='☹',
           style='font-family: "Arial Unicode MS", "Times New Roman", Times, serif;')
    jp.Div(a=div, classes='text-6xl py-5', text='404')
    jp.Div(a=div, classes='text-xl py-5', text='This page doesn\'t exist.')
    return wp


def init_auto_index_page() -> None:
    view_stack = globals.view_stacks.get(0)
    if not view_stack:
        return  # there is no auto-index page on the view stack
    page: Page = view_stack.pop().pages[0]
    page.title = globals.config.title
    page.favicon = globals.config.favicon
    page.dark = globals.config.dark
    page.view.classes = globals.config.main_page_classes
    assert len(view_stack) == 0


def create_page_routes() -> None:
    jp.Route("/{path:path}", error404, last=True)
    for route, page_builder in globals.page_builders.items():
        page_builder.create_route(route)