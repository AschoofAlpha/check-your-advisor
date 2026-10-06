"""Chinese for argparse's own words: `usage:`, the option headings, `-h`'s help, its errors.

Not a `zh_` catalog, and `catalog_modules()` does not load it: these keys are
argparse's source strings, which this package never writes, so the orphan check
in tests/test_i18n.py — every key must still be a string this package prints —
cannot hold them. `cli._translate_argparse()` reads this table instead, and the
same test file checks its keys against the argparse that is running.

Only what a person at the terminal can meet is here: the help page's scaffolding
and the errors a mistyped command line produces. The errors that only a
programmer building a parser can trigger stay in English. A key that a newer
Python rewords simply stops matching, and argparse's English prints in its place.
"""

ARGPARSE_ZH = {
    # The help page.
    "usage: ": "用法：",
    "options": "选项",
    "positional arguments": "位置参数",
    "%(heading)s:": "%(heading)s：",
    "show this help message and exit": "显示本帮助并退出",
    # A command line argparse rejects. The first wraps every message below it.
    "%(prog)s: error: %(message)s\n": "%(prog)s：错误：%(message)s\n",
    "%(prog)s: warning: %(message)s\n": "%(prog)s：警告：%(message)s\n",
    "argument %(argument_name)s: %(message)s": "参数 %(argument_name)s：%(message)s",
    "unrecognized arguments: %s": "无法识别的参数：%s",
    "the following arguments are required: %s": "缺少必需的参数：%s",
    "invalid choice: %(value)r (choose from %(choices)s)": "无效的取值：%(value)r（可选：%(choices)s）",
    "invalid %(type)s value: %(value)r": "无效的 %(type)s 值：%(value)r",
    "expected one argument": "需要一个值",
    "expected at most one argument": "最多只能给一个值",
    "expected at least one argument": "至少要给一个值",
    "ambiguous option: %(option)s could match %(matches)s": "选项有歧义：%(option)s 可能是 %(matches)s",
    "not allowed with argument %s": "不能与参数 %s 同时使用",
    "one of the arguments %s is required": "参数 %s 中必须给出一个",
    "ignored explicit argument %r": "忽略了显式给出的值 %r",
}
