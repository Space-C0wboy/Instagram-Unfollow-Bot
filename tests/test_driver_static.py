import ast
import inspect
import textwrap

import playwright.async_api as pw

from igcleanup.driver import playwright_driver


def test_page_get_by_role_name_is_keyword_only():
    sig = inspect.signature(pw.Page.get_by_role)
    assert sig.parameters["name"].kind == inspect.Parameter.KEYWORD_ONLY


def test_no_get_by_role_call_has_more_than_one_positional_argument():
    source = textwrap.dedent(inspect.getsource(playwright_driver))
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_by_role"
        and len(node.args) > 1
    ]
    assert offenders == []
