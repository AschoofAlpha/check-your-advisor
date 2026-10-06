"""Chinese translations: the HTML-to-PDF converter's messages (`html_to_pdf.py`)."""

MESSAGES = {
    "No HTML-to-PDF converter was found on this machine. The HTML report is complete and "
    "unchanged — a PDF is a second copy of it, not a different document.":
        "这台机器上没有找到 HTML 转 PDF 的转换器。HTML 报告是完整的，一个字节都没变——PDF 只是它的"
        "第二份副本，不是另一份文档。",
    "Install any one of these, then re-run with --pdf:":
        "装下面任意一个，然后加 --pdf 重跑：",
    "Nothing is downloaded for you. This package runs a program that is already installed "
    "and never fetches an installer.":
        "不会替你下载任何东西。本包只调用已经装好的程序，从不去取安装包。",
    "{source} does not exist, so there was nothing to convert.":
        "{source} 不存在，没有可转换的东西。",
    "no HTML-to-PDF converter is installed on this machine.":
        "这台机器上没有装 HTML 转 PDF 的转换器。",
    "{name} exited {returncode} and wrote no file to {target}.{said}":
        "{name} 以 {returncode} 退出，没有在 {target} 写出文件。{said}",
    " It said: {stderr}": "它的输出：{stderr}",
    "{name} exited {returncode} but did write {target}; check the page before circulating it.":
        "{name} 以 {returncode} 退出，但写出了 {target}；转发之前先看一眼这份 PDF。",
    "- {name} — {notes}": "- {name} —— {notes}",
    "{name} did not finish within {timeout}s and was abandoned. The HTML report is unaffected.":
        "{name} 在 {timeout} 秒内没有完成，已放弃。HTML 报告不受影响。",
    "{name} could not be started: {exc}": "{name} 无法启动：{exc}",
    "honours the report's @page margins and @media print rules":
        "遵守报告的 @page 页边距和 @media print 规则",
    "already present on most machines; collapsed <details> print open because the page reopens "
    "them on beforeprint":
        "大多数机器上已经有了；折叠的 <details> 打印时是展开的，因为页面在 beforeprint 时会把它们重新打开",
    "runs no JavaScript, so the roster filter controls are absent from the PDF; every row is "
    "still there, because Python rendered them":
        "不执行 JavaScript，所以 PDF 里没有花名册的筛选控件；每一行都还在，因为它们是 Python 渲染出来的",
    "the weakest renderer here — inline SVG and CSS grid come out approximate; use it only when "
    "nothing else is installed":
        "这里最弱的渲染器——内联 SVG 和 CSS grid 只能近似还原；别的都没装时再用它",
}
