VERSION = 21
TE, TF, TR, TRQ, TDR, TCM, SC, TC = "edit", "find", "replace", "request_files", "drop_files", "commit_message", "shell_command", "create"
SYSTEM_PROMPT = f'You are a coding expert. Answer any questions the user might have. If the user asks you to modify code, use this XML format:\n[{TE} path="file.py"]\n[{TF}]exact code to replace[/{TF}]\n[{TR}]new code[/{TR}]\n[/{TE}]\nTo delete, leave [{TR}] empty. To create a new file: [{TC} path="new_file.py"]file content[/{TC}].\nTo request files content: [{TRQ}]path/f.py[/{TRQ}].\nTo drop irrelevant files from context to save cognitive capacity: [{TDR}]path/f.py[/{TDR}].\nTo run a shell command: [{SC}]echo hi[/{SC}]. The tool will ask the user to approve (y/n). After running, the shell output will be returned truncated (first 10 lines, then a TRUNCATED marker, then the last 40 lines; full output if <= 50 lines).\nWhen making edits provide a [{TCM}]...[/{TCM}].'.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil
from html.parser import HTMLParser; from pathlib import Path

class H(HTMLParser):
    def __init__(self): super().__init__(); self.err = []
    def handle_endtag(self, t):
        if t not in {'br','img','hr','input','meta','link'}: self.err.append(t)
def c(x): return f"\033[{x}"
def run(cmd):
    try: return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except: return None
def env(k, d=None): return os.getenv(k, d)

_SYS = None
def system_summary(refresh=False):
    global _SYS
    if _SYS and not refresh: return _SYS
    try:
        tools = ["bash","zsh","pwsh","powershell","cmd","sh","fish","git","curl","wget","tar","zip","unzip","make","cmake","gcc","clang","docker","kubectl","python","python3","pip","node","npm","yarn","pnpm","go","rustc","cargo","java","javac","mvn","gradle","ruby","gem","php","composer","perl","R","julia","conda","mamba","poetry","uv","brew","apt","dnf","yum","pacman","zypper","apk","choco","scoop","winget"]
        vers = {t: (run(f"{t} --version") or run(f"{t} -version") or "").split('\n')[0][:200] for t in ["git","python","python3","pip","node","npm","docker","go","rustc","cargo","java","javac","gcc","clang","cmake","uv","poetry"] if shutil.which(t)}
        _SYS = {"os": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": sys.version.split()[0], "cwd": os.getcwd(), "shell": os.environ.get("SHELL") or os.environ.get("ComSpec") or "", "path": os.environ.get("PATH", ""), "venv": bool(os.environ.get("VIRTUAL_ENV") or (hasattr(sys, "base_prefix") and sys.prefix != sys.base_prefix)), "tools": sorted([t for t in tools if shutil.which(t)]), "versions": {k: v for k, v in vers.items() if v}}
    except: _SYS = {}
    return _SYS

def get_map(root):
    out = []
    for f in (run(f"git -C {root} ls-files") or "").splitlines():
        if not Path(root, f).exists(): continue
        try:
            defs = [n.name for n in ast.parse(Path(root, f).read_text()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
            if defs: out.append(f"{f}: " + ", ".join(defs))
        except: pass
    return "\n".join(out)

def get_tag_color(tag):
    if SC in tag: return '46;30m'  # cyan bg - shell commands
    if TF in tag: return '41;37m'  # red bg - find (being replaced)
    if TR in tag: return '42;30m'  # green bg - replace (new code)
    if TCM in tag: return '44;37m'  # blue bg - commit message
    if TRQ in tag or TDR in tag: return '45;37m'  # magenta bg - file operations
    if TE in tag or TC in tag: return '43;30m'  # yellow bg - edit/create
    return None

def colorize_tags(text):
    result = []
    i = 0
    while i < len(text):
        if text[i] == '<':
            end = text.find('>', i)
            if end != -1:
                tag = text[i:end+1]
                color = get_tag_color(tag)
                if color:
                    result.append(f"{c(color)}{tag}{c('0m')}")
                else:
                    result.append(tag)
                i = end + 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)

def stream_chat(msgs, model):
    api_key = env("OPENAI_API_KEY")
    if not api_key: return print(f"{c('31m')}Err: Missing OPENAI_API_KEY{c('0m')}")
    stop, full, buf = threading.Event(), "", ""
    def spin():
        i = 0
        while not stop.is_set(): print(f"\r{c('47;30m')} AI {c('0m')} {'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[i % 10]} ", end="", flush=True); time.sleep(0.1); i += 1
    t = threading.Thread(target=spin, daemon=True); t.start()
    req = urllib.request.Request(f"{env('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": f"nanocoder v{VERSION}"}, data=json.dumps({"model": model, "messages": msgs, "stream": True}).encode())
    def flush_buf():
        nonlocal buf
        if buf:
            print(colorize_tags(buf), end="", flush=True)
            buf = ""
    try:
        with urllib.request.urlopen(req) as r:
            started = False
            for line in r:
                if not line.startswith(b"data: "): continue
                if not started: stop.set(); t.join(); print(f"\r{c('47;30m')} AI {c('0m')} ", end="", flush=True); started = True
                try:
                    chunk = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                    full += chunk
                    buf += chunk
                    # Process complete parts, keep incomplete tag in buffer
                    while True:
                        lt = buf.find('<')
                        if lt == -1:
                            print(buf, end="", flush=True); buf = ""; break
                        gt = buf.find('>', lt)
                        if gt == -1:
                            # Incomplete tag - print text before '<', keep rest in buffer
                            if lt > 0: print(buf[:lt], end="", flush=True); buf = buf[lt:]
                            break
                        # Complete tag found - print up to and including tag
                        print(buf[:lt], end="", flush=True)
                        tag = buf[lt:gt+1]
                        color = get_tag_color(tag)
                        if color:
                            print(f"{c(color)}{tag}{c('0m')}", end="", flush=True)
                        else:
                            print(tag, end="", flush=True)
                        buf = buf[gt+1:]
                except: pass
            flush_buf()
    except urllib.error.HTTPError as e: stop.set(); t.join(); print(f"\n{c('31m')}HTTP {e.code}: {e.reason}\nURL: {req.full_url}\nResponse: {e.read().decode() if e.fp else ''}{c('0m')}")
    except Exception as e: stop.set(); t.join(); import traceback; print(f"\n{c('31m')}Err: {e}\n{traceback.format_exc()}{c('0m')}")
    print("\n"); return full

def apply_edits(text, root):
    changes = 0
    # Handle file creation
    for path, content in re.findall(rf'<{TC} path="(.*?)">(.*?)</{TC}>', text, re.DOTALL):
        p = Path(root, path)
        if p.exists(): print(f"{c('31m')}Skip create {path} (already exists){c('0m')}"); continue
        content = content.strip()
        err = None
        if path.endswith(".py"):
            try: ast.parse(content)
            except SyntaxError as e: err = f"{e}"
        elif path.endswith(".html"): h = H(); h.feed(content); err = str(h.err) if h.err else None
        if err: print(f"{c('31m')}Lint Fail {path}: {err}{c('0m')}"); continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        for l in content.splitlines(): print(f"{c('32m')}+{l}{c('0m')}")
        print(f"{c('32m')}Created {path}{c('0m')}"); changes += 1
    # Handle file edits
    for path, find, repl in re.findall(rf'<{TE} path="(.*?)">\s*<{TF}>(.*?)</{TF}>\s*<{TR}>(.*?)</{TR}>\s*</{TE}>', text, re.DOTALL):
        p = Path(root, path)
        if not p.exists(): print(f"{c('31m')}Skip {path} (not found){c('0m')}"); continue
        content = p.read_text()
        if find.strip() not in content: print(f"{c('31m')}Match failed in {path}{c('0m')}"); continue
        new = content.replace(find.strip(), repl.strip(), 1)
        err = None
        if path.endswith(".py"):
            try: ast.parse(new)
            except SyntaxError as e: err = f"{e}"
        elif path.endswith(".html"): h = H(); h.feed(new); err = str(h.err) if h.err else None
        if err: print(f"{c('31m')}Lint Fail {path}: {err}{c('0m')}"); continue
        if content != new:
            for l in difflib.unified_diff(content.splitlines(), new.splitlines(), lineterm=""):
                if not l.startswith(('---','+++')): print(f"{c('32m' if l.startswith('+') else '31m' if l.startswith('-') else '0m')}{l}{c('0m')}")
            p.write_text(new); print(f"{c('32m')}Applied {path}{c('0m')}"); changes += 1
    cm = re.search(rf'<{TCM}>(.*?)</{TCM}>', text, re.DOTALL)
    if changes: run(f"git add -A && git commit -m '{cm.group(1).strip() if cm else 'Update'}'")

def main():
    root, ctx, hist = run("git rev-parse --show-toplevel") or os.getcwd(), set(), []
    model = env("OPENAI_MODEL", "gpt-4o")
    print(f"{c('47;30m')} nanocoder v{VERSION} {c('0m')} {c('47;30m')} {model} {c('0m')} {c('47;30m')} ctrl+d to send {c('0m')}")
    while True:
        print(f"{c('1;34m')}> {c('0m')}", end="", flush=True); lines = []
        try:
            while True: lines.append(input())
        except EOFError: pass
        except KeyboardInterrupt: break
        txt = "\n".join(lines).strip()
        if not txt: continue
        if txt.startswith("/"):
            cmd, _, arg = txt.partition(" ")
            if cmd == "/add": found = [f for f in glob.glob(arg, root_dir=root, recursive=True) if Path(root, f).is_file()]; ctx.update(found); print(f"Added {len(found)} files")
            elif cmd == "/drop": ctx.discard(arg)
            elif cmd == "/clear": hist = []; print("History cleared.")
            elif cmd == "/undo": run("git reset --soft HEAD~1")
            elif cmd == "/exit": print("Bye!"); break
            elif cmd == "/help": print("/add <glob> - Add files to context\n/drop <file> - Remove file from context\n/clear - Clear conversation history\n/undo - Undo last commit\n/update - Update nanocoder\n/exit - Exit\n!<cmd> - Run shell command")
            elif cmd == "/update":
                try: curr, remote = Path(__file__).read_text(), urllib.request.urlopen("https://raw.githubusercontent.com/koenvaneijk/nanocoder/refs/heads/main/nanocoder.py").read().decode(); curr != remote and (Path(__file__).write_text(remote), print("Updated! Restarting..."), os.execv(sys.executable, [sys.executable] + sys.argv))
                except: print("Update failed")
            continue

        if txt.startswith("!"):
            shell_cmd = txt[1:].strip()
            if shell_cmd:
                out_lines, pr = [], subprocess.Popen(shell_cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                try:
                    for line in pr.stdout: print(line, end="", flush=True); out_lines.append(line.rstrip('\n'))
                    pr.wait()
                except KeyboardInterrupt: pr.terminate(); pr.wait(timeout=2); out_lines.append("[INTERRUPTED]"); print("\n[INTERRUPTED]")
                print(f"\n{c('90m')}exit={pr.returncode}{c('0m')}")
                ans = ""
                while ans not in ("t", "f", "n"):
                    try: ans = input("Add to context? [t]runcated/[f]ull/[n]o: ").strip().lower()
                    except EOFError: ans = "n"
                if ans in ("t", "f"):
                    output = "\n".join(out_lines[:10] + ["[TRUNCATED]"] + out_lines[-40:]) if ans == "t" and len(out_lines) > 50 else "\n".join(out_lines)
                    hist.append({"role": "user", "content": f"Shell command output:\n$ {shell_cmd}\nexit={pr.returncode}\n{output}"})
                    print(f"{c('93m')}Added to context{c('0m')}")
            continue

        req = txt
        while True:
            context = f"### Repo Map\n{get_map(root)}\n### Files\n" + "\n".join([f"File: {f}\n```\n{Path(root,f).read_text()}\n```" for f in ctx if Path(root,f).exists()])
            msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": f"System summary: {json.dumps(system_summary(), separators=(',',':'))}"}] + hist + [{"role": "user", "content": f"{context}\nRequest: {req}"}]
            full = stream_chat(msgs, model); hist.extend([{"role": "user", "content": req}, {"role": "assistant", "content": full}]); apply_edits(full, root)
            reqs = re.findall(rf'<({TRQ}|{TDR})>(.*?)</\1>', full)
            added = [f.strip() for tag, f in reqs if tag == TRQ and f.strip() not in ctx and Path(root, f.strip()).exists()]
            ctx.update(added)
            for tag, f in reqs:
                if tag == TDR: ctx.discard(f.strip())
            if added: print(f"{c('93m')}+{len(added)} file(s){c('0m')}"); req = f"Added files: {', '.join(added)}. Please continue."; continue
            cmds = re.findall(rf'<{SC}>(.*?)</{SC}>', full, re.DOTALL)
            if cmds:
                results = []
                for cmd in [c.strip() for c in cmds]:
                    print(f"{c('1m')}{cmd}{c('0m')}\n"); ans = ""
                    while ans not in ("y", "n"):
                        try: ans = input("Run? (y/n): ").strip().lower()
                        except EOFError: ans = "n"
                    if ans == "y":
                        try:
                            out_lines, pr = [], subprocess.Popen(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                            try:
                                for line in pr.stdout: print(line, end="", flush=True); out_lines.append(line.rstrip('\n'))
                                pr.wait()
                            except KeyboardInterrupt: pr.terminate(); (pr.wait(timeout=2) if True else pr.kill()); out_lines.append("[INTERRUPTED by Ctrl+C]"); print("\n[INTERRUPTED by Ctrl+C]")
                            results.append(f"$ {cmd}\nexit={pr.returncode}\n" + ("\n".join(out_lines[:10] + ["[TRUNCATED]"] + out_lines[-40:]) if len(out_lines) > 50 else "\n".join(out_lines)))
                        except Exception as e: results.append(f"$ {cmd}\nerror: {e}")
                    else: results.append(f"$ {cmd}\nDENIED by user.")
                req = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break

if __name__ == "__main__": main()
