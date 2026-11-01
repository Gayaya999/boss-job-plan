#!/usr/bin/env python3
"""把简历 JSON 渲染成排版好的简历：HTML（预览）+ PDF（投递）+ Markdown（可编辑）。

用法：
    python3 make_resume.py <resume.json> <输出目录> [--open] [--no-pdf]

- PDF 用本机的 Chrome / Edge / Chromium 无头模式导出；找不到浏览器时只生成 HTML，可以在浏览器里“打印 → 存储为 PDF”。
- 自动检查：未填的【占位符】数量（有占位符时文件名加“_草稿”，HTML 顶部显示提醒），以及 PDF 页数（超过 max_pages 时给出警告）。
- 运行结果的最后一行是 JSON 摘要，方便 Agent 读取：{"pdf": ..., "pages": ..., "placeholders": [...], ...}
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "resume-template.html"
PH_RE = re.compile(r"【[^】]*】")
BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


# ---------- 文本处理 ----------
def inline(text):
    """转义 HTML，支持 **加粗**，并高亮【占位符】。"""
    s = html.escape(str(text))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = PH_RE.sub(lambda m: f'<mark class="ph">{m.group(0)}</mark>', s)
    return s


def link_html(url):
    u = str(url)
    if PH_RE.search(u) or not re.match(r"^(https?://|mailto:)", u):
        return inline(u)
    return f'<a href="{html.escape(u)}">{html.escape(re.sub(r"^https?://", "", u))}</a>'


def collect_placeholders(obj, found):
    if isinstance(obj, str):
        found.extend(PH_RE.findall(obj))
    elif isinstance(obj, list):
        for x in obj:
            collect_placeholders(x, found)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in {"accent", "density", "file_name", "max_pages", "photo"}:
                collect_placeholders(v, found)
    return found


# ---------- 渲染 ----------
def render_header(d):
    contacts = []
    for c in d.get("contacts", []):
        if isinstance(c, dict):
            label, value = c.get("label", ""), c.get("value", "")
            contacts.append((inline(label) + "：" if label else "") + link_html(value))
        else:
            contacts.append(link_html(c))
    photo = ""
    if d.get("photo"):
        photo = f'<img class="photo" src="{html.escape(Path(d["photo"]).resolve().as_uri())}" alt="">'
    target = f'<div class="target">{inline(d["target"])}</div>' if d.get("target") else ""
    out = (
        "<header>"
        f'<div class="left"><h1>{inline(d.get("name", "【姓名】"))}</h1>{target}</div>'
        f'<div style="display:flex;align-items:flex-end"><div class="contacts">{"<br>".join(contacts)}</div>{photo}</div>'
        "</header>"
    )
    if d.get("summary"):
        out += f'<div class="summary">{inline(d["summary"])}</div>'
    return out


def render_section(sec):
    kind = sec.get("type", "entries")
    parts = [f'<section><h2>{inline(sec.get("title", ""))}</h2>']
    items = sec.get("items", [])
    if kind == "entries":
        for it in items:
            role = f'<span class="role">{inline(it["role"])}</span>' if it.get("role") else ""
            date = f'<span class="date">{inline(it["date"])}</span>' if it.get("date") else ""
            parts.append(f'<div class="entry"><div class="head"><div><span class="title">{inline(it.get("title", ""))}</span>{role}</div>{date}</div>')
            if it.get("link"):
                parts.append(f'<div class="link">{link_html(it["link"])}</div>')
            bullets = it.get("bullets", [])
            if bullets:
                parts.append("<ul>" + "".join(f"<li>{inline(b)}</li>" for b in bullets) + "</ul>")
            parts.append("</div>")
    elif kind == "list":
        for it in items:
            if isinstance(it, dict):
                date = f'<span class="date">{inline(it["date"])}</span>' if it.get("date") else ""
                parts.append(f'<div class="list-row"><span>{inline(it.get("text", ""))}</span>{date}</div>')
            else:
                parts.append(f'<div class="list-row"><span>{inline(it)}</span></div>')
    elif kind == "skills":
        parts.append('<div class="skills">')
        for it in items:
            parts.append(f'<div class="label">{inline(it.get("label", ""))}</div><div>{inline(it.get("text", ""))}</div>')
        parts.append("</div>")
    elif kind == "text":
        for it in items:
            parts.append(f"<p style='margin:2px 0'>{inline(it)}</p>")
    parts.append("</section>")
    return "".join(parts)


def render_html(d, placeholders):
    body = render_header(d) + "".join(render_section(s) for s in d.get("sections", []))
    draft = ""
    if placeholders:
        uniq = list(dict.fromkeys(placeholders))
        draft = (
            f'<div class="draft"><b>草稿：还有 {len(placeholders)} 处待确认（黄色高亮）</b>，填好之前不要投递。'
            f'<br>{html.escape("、".join(uniq[:12]))}{" 等" if len(uniq) > 12 else ""}</div>'
        )
    tpl = TEMPLATE.read_text(encoding="utf-8")
    return (
        tpl.replace("__TITLE__", html.escape(d.get("file_name") or f'{d.get("name", "简历")}-简历'))
        .replace("__ACCENT__", d.get("accent", "#0f7b6c"))
        .replace("__BODY_CLASS__", "compact" if d.get("density") == "compact" else "")
        .replace("__DRAFT__", draft)
        .replace("__BODY__", body)
    )


def render_markdown(d):
    lines = [f'# {d.get("name", "【姓名】")}']
    if d.get("target"):
        lines.append(f'**{d["target"]}**')
    contacts = [
        (f'{c.get("label")}：{c.get("value")}' if c.get("label") else c.get("value", "")) if isinstance(c, dict) else str(c)
        for c in d.get("contacts", [])
    ]
    if contacts:
        lines.append(" ｜ ".join(contacts))
    if d.get("summary"):
        lines += ["", d["summary"]]
    for sec in d.get("sections", []):
        lines += ["", f'## {sec.get("title", "")}']
        kind = sec.get("type", "entries")
        for it in sec.get("items", []):
            if kind == "entries":
                head = f'**{it.get("title", "")}**'
                if it.get("role"):
                    head += f'｜{it["role"]}'
                if it.get("date"):
                    head += f'｜{it["date"]}'
                lines.append(head)
                if it.get("link"):
                    lines.append(it["link"])
                lines += [f"- {b}" for b in it.get("bullets", [])]
                lines.append("")
            elif kind == "list":
                lines.append(f'- {it.get("text", "")}｜{it.get("date", "")}' if isinstance(it, dict) and it.get("date") else f'- {it.get("text", it) if isinstance(it, dict) else it}')
            elif kind == "skills":
                lines.append(f'- **{it.get("label", "")}**：{it.get("text", "")}')
            else:
                lines.append(str(it))
    return "\n".join(lines).rstrip() + "\n"


# ---------- PDF ----------
def find_browser():
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def to_pdf(browser, html_path, pdf_path):
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--no-pdf-header-footer", "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri(),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
    return pdf_path.exists() and pdf_path.stat().st_size > 0


def pdf_pages(pdf_path):
    data = pdf_path.read_bytes()
    m = re.findall(rb"/Type\s*/Pages\b.*?/Count\s+(\d+)", data, re.S)
    if m:
        return max(int(x) for x in m)
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", data))


def open_target(path):
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("resume_json")
    ap.add_argument("out_dir")
    ap.add_argument("--open", action="store_true", help="生成后打开 PDF（没有 PDF 时打开 HTML）")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    d = json.loads(Path(args.resume_json).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    placeholders = collect_placeholders(d, [])
    base = d.get("file_name") or f'简历_{d.get("name", "未命名")}'
    base = re.sub(r'[\\/:*?"<>|]', "_", PH_RE.sub("", base)).strip("_") or "简历"
    if placeholders:
        base += "_草稿"

    html_path = out_dir / f"{base}.html"
    md_path = out_dir / f"{base}.md"
    html_path.write_text(render_html(d, placeholders), encoding="utf-8")
    md_path.write_text(render_markdown(d), encoding="utf-8")

    result = {"html": str(html_path.resolve()), "markdown": str(md_path.resolve()), "pdf": None, "pages": None,
              "placeholders": placeholders, "warnings": []}
    if not args.no_pdf:
        browser = find_browser()
        pdf_path = out_dir / f"{base}.pdf"
        if browser and to_pdf(browser, html_path, pdf_path):
            result["pdf"] = str(pdf_path.resolve())
            result["pages"] = pdf_pages(pdf_path)
            max_pages = int(d.get("max_pages", 1))
            if result["pages"] and result["pages"] > max_pages:
                result["warnings"].append(
                    f"PDF 共 {result['pages']} 页，超过 {max_pages} 页：先删减或合并弱相关内容，再考虑设置 \"density\": \"compact\""
                )
        else:
            result["warnings"].append("没有找到可用的 Chrome / Edge，未生成 PDF；请在浏览器打开 HTML 后“打印 → 存储为 PDF”")
    if placeholders:
        result["warnings"].append(f"还有 {len(placeholders)} 处【占位符】未填写，文件已标记为草稿")

    print(f"HTML：{result['html']}")
    print(f"Markdown：{result['markdown']}")
    if result["pdf"]:
        print(f"PDF：{result['pdf']}（{result['pages']} 页）")
    for w in result["warnings"]:
        print("⚠️ " + w)
    if args.open:
        open_target(result["pdf"] or result["html"])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
