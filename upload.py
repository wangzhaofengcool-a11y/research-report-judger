import os, base64, json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TOKEN = input("粘贴你的 GitHub Token: ").strip()
OWNER = "wangzhaofengcool-a11y"
REPO = "research-report-judger"
BASE = "E:/claude pj/Research report judger 2/deploy"

def upload(path, content):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
    body = json.dumps({"message": f"Add {path}", "content": base64.b64encode(content).decode()}).encode()
    req = Request(url, data=body, headers={
        "Authorization": f"token {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
    }, method="PUT")
    try:
        urlopen(req, timeout=30)
        print(f"  OK  {path}")
    except HTTPError as e:
        err = e.read().decode()[:200]
        print(f"  ERR {path}: {err}")

for root, dirs, files in os.walk(BASE):
    for f in files:
        fpath = os.path.join(root, f)
        rel = os.path.relpath(fpath, BASE).replace("\\", "/")
        with open(fpath, "rb") as fh:
            upload(rel, fh.read())

print("\n完成! 访问: https://github.com/wangzhaofengcool-a11y/research-report-judger")
