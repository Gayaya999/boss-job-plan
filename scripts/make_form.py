#!/usr/bin/env python3
"""把问卷 JSON 填进 assets/form-template.html，生成卡片引导式问卷页面。

用法：
    python3 make_form.py <questions.json> <输出.html> [--open | --serve [--port N] [--timeout 秒]]

--open   生成后用默认浏览器以本地文件方式打开（语音输入可能因浏览器安全限制不可用）。
--serve  推荐。在 127.0.0.1 启动本地服务并打开页面：
         - 语音输入可正常使用（localhost 属于安全上下文，浏览器允许使用麦克风）；
         - 页面出现「提交给 Agent」按钮，回答直接保存为输出目录下的 <file_name>.md；
         - 进程会一直运行，直到超时（默认 3 小时）或被结束，请在后台运行。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "form-template.html"
VALID_TYPES = {"single", "multi", "text", "textarea"}


def validate(data):
    errors = []
    if not data.get("title"):
        errors.append("缺少 title")
    ids = set()
    for si, sec in enumerate(data.get("sections", [])):
        for qi, q in enumerate(sec.get("questions", [])):
            where = f"sections[{si}].questions[{qi}]"
            if not q.get("id"):
                errors.append(f"{where} 缺少 id")
            elif q["id"] in ids:
                errors.append(f"{where} id 重复：{q['id']}")
            ids.add(q.get("id"))
            if q.get("type") not in VALID_TYPES:
                errors.append(f"{where} type 必须是 {sorted(VALID_TYPES)}")
            if q.get("type") in {"single", "multi"} and not q.get("options"):
                errors.append(f"{where} 选择题缺少 options")
            if not q.get("label"):
                errors.append(f"{where} 缺少 label")
    if not ids:
        errors.append("没有任何问题")
    return errors


def open_target(target):
    if sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    elif os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", target], check=False)


def free_port(preferred):
    for port in [preferred] + list(range(preferred + 1, preferred + 50)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("找不到可用端口")


class FormHandler(SimpleHTTPRequestHandler):
    answer_path: Path = None  # type: ignore[assignment]

    def do_POST(self):
        if self.path.rstrip("/").split("/")[-1] != "submit":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 2_000_000:
            self.send_error(400)
            return
        try:
            text = json.loads(self.rfile.read(length).decode("utf-8"))["text"]
        except Exception:
            self.send_error(400)
            return
        self.answer_path.write_text(text, encoding="utf-8")
        print(f"已收到回答：{self.answer_path}", flush=True)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(out, data, port, timeout, auto_open=True):
    directory = out.parent.resolve()
    answer_path = directory / ((data.get("file_name") or f"求职问卷回答_{data.get('id', 'job')}") + ".md")
    handler = type("Handler", (FormHandler,), {"answer_path": answer_path})
    httpd = ThreadingHTTPServer(("127.0.0.1", free_port(port)), partial(handler, directory=str(directory)))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/{quote(out.name)}"
    print(f"问卷地址：{url}", flush=True)
    print(f"提交后的回答会保存到：{answer_path}", flush=True)
    threading.Timer(timeout, httpd.shutdown).start()
    if auto_open:
        open_target(url)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions")
    ap.add_argument("output")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    ap.add_argument("--no-open", action="store_true", help="配合 --serve：只启动服务，不自动打开浏览器")
    args = ap.parse_args()

    src, out = Path(args.questions), Path(args.output)
    data = json.loads(src.read_text(encoding="utf-8"))
    errors = validate(data)
    if errors:
        print("问卷 JSON 有问题：\n- " + "\n- ".join(errors))
        sys.exit(2)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__FORM_DATA__", payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已生成问卷：{out.resolve()}", flush=True)

    if args.serve:
        serve(out, data, args.port, args.timeout, auto_open=not args.no_open)
    elif args.open:
        open_target(str(out.resolve()))


if __name__ == "__main__":
    main()
