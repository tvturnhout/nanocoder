VERSION = 40
TAGS = {"edit": "edit", "find": "find", "replace": "replace", "request": "request_files", "drop": "drop_files", "commit": "commit_message", "shell": "shell_command", "create": "create"}
SYSTEM_PROMPT = f'You are an assistant with expert coding capabilities. Answer any questions the user might have. If the user asks you to modify code, use this XML format:\n[{TAGS["edit"]} path="file.py"]\n[{TAGS["find"]}]lines to find[/{TAGS["find"]}]\n[{TAGS["replace"]}]new code[/{TAGS["replace"]}]\n[/{TAGS["edit"]}]\nThe [{TAGS["find"]}] text is replaced literally, so it must match exactly. Keep it short - only enough lines to be unambiguous. Split large changes into multiple small edits.\nTo delete, leave [{TAGS["replace"]}] empty. To create a new file: [{TAGS["create"]} path="new_file.py"]file content[/{TAGS["create"]}].\nTo request files (one path per line):\n[{TAGS["request"]}]\npath/file1.py\npath/file2.py\n[/{TAGS["request"]}]\nTo drop files from context (one path per line):\n[{TAGS["drop"]}]\npath/file.py\n[/{TAGS["drop"]}]\nTo run a shell command: [{TAGS["shell"]}]echo hi[/{TAGS["shell"]}]. The tool will ask the user to approve (y/n). After running, the shell output will be returned truncated (first 10 lines, then a TRUNCATED marker, then the last 40 lines; full output if <= 50 lines).\nWhen making edits provide a [{TAGS["commit"]}]...[/{TAGS["commit"]}].'.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, struct, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil
from pathlib import Path

def ansi(code): return f"\033[{code}"
def styled(text, style): return f"{ansi(style)}{text}{ansi('0m')}"
def run(shell_cmd):
    try: return subprocess.check_output(shell_cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except: return None
_TMUX_WIN = run("tmux display-message -p '#{window_id}' 2>/dev/null")
def title(t): print(f"\033]0;{t}\007", end="", flush=True); _TMUX_WIN and run(f"tmux rename-window -t {_TMUX_WIN} {t!r} 2>/dev/null")
_CACHED_SYSTEM_INFO = None
def system_summary():
    global _CACHED_SYSTEM_INFO
    if _CACHED_SYSTEM_INFO: return _CACHED_SYSTEM_INFO
    try:
        tools = ["apt","bash","curl","docker","gcc","git","make","node","npm","perl","pip","python3","sh","tar","unzip","wget","zip"]
        versions = {tool: (run(f"{tool} --version") or "").split('\n')[0][:80] for tool in ["git","python3","pip","node","npm","docker","gcc"] if shutil.which(tool)}
        _CACHED_SYSTEM_INFO = {"os": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": sys.version.split()[0], "cwd": os.getcwd(), "shell": os.environ.get("SHELL") or os.environ.get("ComSpec") or "", "path": os.environ.get("PATH", ""), "venv": bool(os.environ.get("VIRTUAL_ENV") or sys.prefix != sys.base_prefix), "tools": [tool for tool in tools if shutil.which(tool)], "versions": {key: val for key, val in versions.items() if val}}
    except: _CACHED_SYSTEM_INFO = {}
    return _CACHED_SYSTEM_INFO

def load_agents_md(root):
    agents_path = Path(root, "AGENTS.md")
    if agents_path.exists():
        try: return agents_path.read_text()
        except: pass
    return None

def get_map(root, max_files=100):
    BINARY_EXT = {'.png','.jpg','.jpeg','.gif','.ico','.webp','.bmp','.mp3','.mp4','.wav','.avi','.mov','.zip','.tar','.gz','.rar','.7z','.pdf','.exe','.dll','.so','.dylib','.pyc','.woff','.woff2','.ttf','.eot'}
    EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', '.tox', 'dist', 'build', '.eggs', '.mypy_cache', '.pytest_cache', '.ruff_cache', 'htmlcov', '.coverage', 'env', '.env'}
    files = (run(f"git -C {root} ls-files") or "").splitlines()
    if not files:
        files = [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file() and not any(ex in p.parts for ex in EXCLUDE_DIRS)]
    files = sorted(files, key=lambda f: (f.count('/'), f))[:max_files]
    output = []
    for f in files:
        p = Path(root, f)
        if not p.exists(): continue
        if p.suffix.lower() in BINARY_EXT: output.append(f"{f} [binary]"); continue
        defs = ""
        if f.endswith('.py'):
            try: defs = ": " + ", ".join(n.name for n in ast.parse(p.read_text()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)))[:80]
            except: pass
        output.append(f"{f}{defs}")
    return "\n".join(output)

TAG_COLORS = {TAGS["shell"]: '46;30m', TAGS["find"]: '41;37m', TAGS["replace"]: '42;30m', TAGS["commit"]: '44;37m', TAGS["request"]: '45;37m', TAGS["drop"]: '45;37m', TAGS["edit"]: '43;30m', TAGS["create"]: '43;30m'}
def get_tag_color(tag): return next((c for t, c in TAG_COLORS.items() if t in tag), None)

def is_bedrock(url): return url and "amazonaws.com" in url

def parse_aws_event_stream(response):
    """Parse AWS binary event stream, yielding text chunks"""
    buffer = b""
    while True:
        chunk = response.read(4096)
        if not chunk and len(buffer) < 8: break
        buffer += chunk
        while len(buffer) >= 8:
            total_len, headers_len = struct.unpack('>II', buffer[:8])
            if len(buffer) < total_len: break
            headers_start, headers_end = 12, 12 + headers_len
            payload_data = buffer[headers_end:total_len - 4]
            headers_data, event_type, pos = buffer[headers_start:headers_end], None, 0
            while pos < len(headers_data):
                try:
                    name_len = headers_data[pos]; pos += 1
                    name = headers_data[pos:pos + name_len].decode('utf-8'); pos += name_len
                    header_type = headers_data[pos]; pos += 1
                    if header_type == 7:
                        value_len = struct.unpack('>H', headers_data[pos:pos + 2])[0]; pos += 2
                        value = headers_data[pos:pos + value_len].decode('utf-8'); pos += value_len
                        if name == ':event-type': event_type = value
                    else: break
                except: break
            if payload_data and event_type == 'contentBlockDelta':
                try:
                    text = json.loads(payload_data.decode('utf-8')).get('delta', {}).get('text', '')
                    if text: yield text
                except: pass
            buffer = buffer[total_len:]

def to_bedrock_messages(messages):
    """Convert OpenAI-style messages to Bedrock format, returning (system_list, messages_list)"""
    system, msgs = [], []
    for m in messages:
        role, content = m.get('role'), m.get('content', '')
        if role == 'system': system.append({"text": content})
        elif role in ('user', 'assistant'): msgs.append({"role": role, "content": [{"text": content}]})
    return system, msgs

def render_md(text):
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    result = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            inner = part[3:-3]
            if inner.startswith('\n'): inner = inner[1:]
            elif '\n' in inner: inner = inner.split('\n', 1)[1]
            inner_lines = inner.split('\n')
            inner = '\n'.join(f"{line}{ansi('K')}" for line in inner_lines) + ansi('K')
            result.append(f"\n{ansi('48;5;236;37m')}{inner}{ansi('0m')}")
        elif part.startswith('`') and part.endswith('`'):
            result.append(f"{ansi('48;5;236m')}{part[1:-1]}{ansi('0m')}")
        else:
            part = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: f"\033]8;;{m.group(2)}\033\\{ansi('4;34m')}{m.group(1)}{ansi('0m')}\033]8;;\033\\", part)
            part = re.sub(r'\*\*(.+?)\*\*', lambda m: f"{ansi('1m')}{m.group(1)}{ansi('22m')}", part)
            part = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', lambda m: f"{ansi('3m')}{m.group(1)}{ansi('23m')}", part)
            part = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)', lambda m: f"{ansi('3m')}{m.group(1)}{ansi('23m')}", part)
            def format_header(m):
                level, text = len(m.group(1)), m.group(2)
                if level == 1: return f"{ansi('1;4;33m')}{text}{ansi('0m')}"
                elif level == 2: return f"{ansi('1;33m')}{text}{ansi('0m')}"
                else: return f"{ansi('33m')}{text}{ansi('0m')}"
            part = re.sub(r'^(#{1,3}) (.+)$', format_header, part, flags=re.MULTILINE)
            result.append(part)
    return ''.join(result)

def truncate(lines, n=50): return lines if len(lines) <= n else lines[:10] + ["[TRUNCATED]"] + lines[-40:]

def run_shell_interactive(cmd):
    output_lines, process = [], subprocess.Popen(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for line in process.stdout: print(line, end="", flush=True); output_lines.append(line.rstrip('\n'))
        process.wait()
    except KeyboardInterrupt: process.terminate(); process.wait(timeout=2); output_lines.append("[INTERRUPTED]"); print("\n[INTERRUPTED]")
    return output_lines, process.returncode

def stream_chat(messages, model):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: print(styled("Err: Missing OPENAI_API_KEY", "31m")); return None, False
    base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    stop_event, full_response, buffer, md_buffer = threading.Event(), "", "", ""
    state = {"xml": False, "code": False, "int": False}
    tag_re = re.compile(r'<(/?(?:' + '|'.join(TAGS.values()) + r'))(?:\s[^>]*)?>')
    def spin():
        i = 0; print()
        while not stop_event.is_set(): print(f"\r{styled(' AI ', '47;30m')} {'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[i % 10]} ", end="", flush=True); time.sleep(0.1); i += 1
    def out(txt, code=False): print((f"{ansi('48;5;236;37m')}" + '\n'.join(f"{ln}{ansi('K')}" for ln in txt.split('\n'))) if code else render_md(txt), end="", flush=True)
    def try_flush():
        nonlocal md_buffer
        if '\n\n' in md_buffer:
            parts = md_buffer.rsplit('\n\n', 1)
            to_flush = parts[0]
            last_line = to_flush.split('\n')[-1] if to_flush else ""
            incomplete = (last_line.count('**') % 2 == 1 or 
                         (last_line.count('`') - last_line.count('```') * 3) % 2 == 1 or
                         re.search(r'(?<!\*)\*[^*\n]+$', last_line) or
                         re.search(r'(?<!\w)_[^_\n]+$', last_line))
            if not incomplete:
                out(parts[0] + '\n\n'); md_buffer = parts[1] if len(parts) > 1 else ""
    def chunk_iter(resp):
        if is_bedrock(base_url):
            for text in parse_aws_event_stream(resp): yield text
        else:
            for line in resp:
                if not line.startswith(b"data: "): continue
                try: yield json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                except: pass
    spinner = threading.Thread(target=spin, daemon=True); spinner.start()
    if is_bedrock(base_url):
        system, bedrock_msgs = to_bedrock_messages(messages)
        url = f"{base_url.rstrip('/')}/model/{model}/converse-stream"
        payload = {"messages": bedrock_msgs, "inferenceConfig": {"maxTokens": 16384}}
        if system: payload["system"] = system
    else:
        url = f"{base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "stream": True}
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": f"nanocoder/{VERSION}"}, data=json.dumps(payload).encode())
    try:
        with urllib.request.urlopen(req) as resp:
            started = False
            for chunk in chunk_iter(resp):
                if not started: stop_event.set(); spinner.join(); print(f"\r{styled(' AI ', '47;30m')}   \n", end="", flush=True); started = True
                if chunk:
                    full_response += chunk; buffer += chunk
                    while buffer:
                        fence = re.match(r'^(```[^\n]*\n?)', buffer) if not state["xml"] else None
                        if not fence and not state["xml"] and '\n```' in buffer: fence = re.match(r'^(\n```[^\n]*\n?)', buffer[buffer.find('\n```'):])
                        if fence and not state["xml"]:
                            pos = buffer.find(fence.group(0))
                            if state["code"]: out(buffer[:pos] + ansi('0m'), True)
                            else: md_buffer += buffer[:pos]; out(md_buffer); md_buffer = ""
                            state["code"] = not state["code"]
                            if state["code"]: print(f"{ansi('48;5;236;37m')}", end="", flush=True)
                            buffer = buffer[pos + len(fence.group(0)):]; continue
                        match = tag_re.search(buffer)
                        if match and not state["code"]:
                            before = buffer[:match.start()]
                            if state["xml"]: print(before, end="", flush=True)
                            else: md_buffer += before; out(md_buffer); md_buffer = ""
                            tag, color = match.group(0), get_tag_color(match.group(0))
                            print(f"{ansi(color)}{tag}{ansi('0m')}" if color else tag, end="", flush=True)
                            state["xml"] = not tag.startswith('</'); buffer = buffer[match.end():]
                        else:
                            lt = buffer.rfind('<') if not state["code"] else -1
                            if lt != -1 and not state["xml"]: md_buffer += buffer[:lt]; try_flush(); buffer = buffer[lt:]
                            elif state["xml"]:
                                if lt != -1: print(buffer[:lt], end="", flush=True); buffer = buffer[lt:]
                                else: print(buffer, end="", flush=True); buffer = ""
                            elif state["code"]: out(buffer, True); buffer = ""
                            else: md_buffer += buffer; try_flush(); buffer = ""
                            break
            if buffer: (print(buffer, end="", flush=True) if state["xml"] else out(buffer, state["code"]) if state["code"] else (md_buffer := md_buffer + buffer, out(md_buffer)))
            elif md_buffer: out(md_buffer)
            if state["code"]: print(ansi('0m'), end="", flush=True)
    except KeyboardInterrupt: stop_event.set(); spinner.join(); state["int"] = True; md_buffer and out(md_buffer); state["code"] and print(ansi('0m'), end="", flush=True); print(f"\n{styled('[user interrupted]', '93m')}")
    except urllib.error.HTTPError as e:
        stop_event.set(); spinner.join()
        error_body = ""
        try: error_body = e.read().decode('utf-8', errors='replace')[:500]
        except: pass
        print(f"\n{styled(f'HTTP {e.code}: {e.reason}', '31m')}")
        if error_body: print(styled(f"Response: {error_body}", '31m'))
    except Exception as e: stop_event.set(); spinner.join(); print(f"\n{styled(f'Err: {e}', '31m')}")
    print("\n"); return full_response, state["int"]

def apply_edits(text, root):
    changes, p = 0, lambda path: Path(root, path)
    def lint_py(path, content):
        if not path.endswith(".py"): return True
        try: ast.parse(content); return True
        except SyntaxError as e: print(styled(f"Lint Fail {path}: {e}", "31m")); return False
    for path, content in re.findall(rf'<{TAGS["create"]} path="(.*?)">(.*?)</{TAGS["create"]}>', text, re.DOTALL):
        if p(path).exists(): print(styled(f"Skip create {path} (exists)", "31m")); continue
        content = content.strip()
        if not lint_py(path, content): continue
        try: p(path).parent.mkdir(parents=True, exist_ok=True); p(path).write_text(content); [print(styled(f"+{ln}", "32m")) for ln in content.splitlines()]; print(styled(f"Created {path}", "32m")); changes += 1
        except (PermissionError, OSError) as e: print(styled(f"Failed {path}: {e}", "31m"))
    for path, find_text, replace_text in re.findall(rf'<{TAGS["edit"]} path="(.*?)">\s*<{TAGS["find"]}>(.*?)</{TAGS["find"]}>\s*<{TAGS["replace"]}>(.*?)</{TAGS["replace"]}>\s*</{TAGS["edit"]}>', text, re.DOTALL):
        if not p(path).exists(): print(styled(f"Skip {path} (not found)", "31m")); continue
        content = p(path).read_text()
        if find_text.strip() not in content: print(styled(f"Match failed in {path}", "31m")); continue
        new_content = content.replace(find_text.strip(), replace_text.strip(), 1)
        if not lint_py(path, new_content) or content == new_content: continue
        [print(styled(d, '32m' if d.startswith('+') else '31m' if d.startswith('-') else '0m')) for d in difflib.unified_diff(content.splitlines(), new_content.splitlines(), lineterm="") if not d.startswith(('---','+++'))]
        try: p(path).write_text(new_content); print(styled(f"Applied {path}", "32m")); changes += 1
        except (PermissionError, OSError) as e: print(styled(f"Failed {path}: {e}", "31m"))
    commit_msg = (m.group(1).strip() if (m := re.search(rf'<{TAGS["commit"]}>(.*?)</{TAGS["commit"]}>', text, re.DOTALL)) else 'Update')
    if changes: run(f"git add -A && git commit -m {commit_msg!r}")

def main():
    repo_root, context_files, history = run("git rev-parse --show-toplevel") or os.getcwd(), set(), []
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"{styled(' nanocoder v' + str(VERSION) + ' ', '47;30m')} {styled(' ' + model + ' ', '47;30m')} {styled(' ctrl+d to send ', '47;30m')}")
    while True:
        title("❓ nanocoder"); print(f"\a{styled('❯ ', '1;34m')}", end="", flush=True); input_lines = []
        try:
            while True: input_lines.append(input())
        except EOFError: pass
        except KeyboardInterrupt: print(); continue
        if not (user_input := "\n".join(input_lines).strip()): continue
        if user_input.startswith("/"):
            command, _, arg = user_input.partition(" ")
            def cmd_add(): found = [f for f in glob.glob(arg, root_dir=repo_root, recursive=True) if Path(repo_root, f).is_file()]; context_files.update(found); print(f"Added {len(found)} files")
            commands = {"/add": cmd_add, "/drop": lambda: context_files.discard(arg), "/clear": lambda: (history.clear(), print("History cleared.")), "/undo": lambda: run("git reset --soft HEAD~1"), "/help": lambda: print("/add <glob> - Add files\n/drop <file> - Remove file\n/clear - Clear history\n/undo - Undo commit\n/exit - Exit\n!<cmd> - Shell")}
            if command == "/exit": print("Bye!"); title(""); break
            if command in commands: commands[command]()
            continue

        if user_input.startswith("!"):
            shell_cmd = user_input[1:].strip()
            if shell_cmd:
                output_lines, exit_code = run_shell_interactive(shell_cmd)
                print(f"\n{styled(f'exit={exit_code}', '90m')}"); title("❓ nanocoder")
                try: answer = input("\aAdd to context? [t]runcated/[f]ull/[n]o: ").strip().lower()
                except EOFError: answer = "n"
                if answer in ("t", "f"):
                    history.append({"role": "user", "content": f"$ {shell_cmd}\nexit={exit_code}\n" + "\n".join(truncate(output_lines) if answer == "t" else output_lines)})
                    print(styled("Added to context", "93m"))
            continue

        request = user_input
        while True:
            def read(p):
                f = Path(repo_root, p)
                if not f.exists(): return ""
                try: return f.read_text() or "[empty]"
                except: return "[binary/unreadable]"
            context = f"### Repo Map\n{get_map(repo_root)}\n### Files\n" + "\n".join(f"File: {f}\n```\n{read(f)}\n```" for f in context_files if Path(repo_root,f).exists())
            agents_md = load_agents_md(repo_root)
            system_prompt = SYSTEM_PROMPT + (f"\n\n### Project Instructions (AGENTS.md)\n{agents_md}" if agents_md else "")
            messages = [{"role": "system", "content": system_prompt}, {"role": "system", "content": f"System summary: {json.dumps(system_summary(), separators=(',',':'))}"}] + history + [{"role": "user", "content": f"{context}\nRequest: {request}"}]
            title("⏳ nanocoder"); full_response, interrupted = stream_chat(messages, model)
            if full_response is None: break
            response_content = full_response + ("\n\n[user interrupted]" if interrupted else "")
            history.extend([{"role": "user", "content": request}, {"role": "assistant", "content": response_content}])
            if interrupted: break
            apply_edits(full_response, repo_root)
            file_requests = re.findall(rf'<({TAGS["request"]}|{TAGS["drop"]})>(.*?)</\1>', full_response, re.DOTALL)
            added_files = []
            for tag, content in file_requests:
                for filepath in content.strip().split('\n'):
                    filepath = filepath.strip()
                    if not filepath: continue
                    if tag == TAGS["request"] and filepath not in context_files and Path(repo_root, filepath).exists():
                        added_files.append(filepath)
                    elif tag == TAGS["drop"]:
                        context_files.discard(filepath)
            context_files.update(added_files)
            def safe_read(fp):
                try: return Path(repo_root, fp).read_text()
                except: return ""
            tok_hist = sum(len(m.get("content","")) for m in history)//4
            tok_files = sum(len(safe_read(f)) for f in context_files if Path(repo_root,f).exists())//4
            tok_total = tok_hist + tok_files
            tok_bg = '47;30m' if tok_total < 80000 else '43;30m' if tok_total < 120000 else '41;37m'
            print(styled(f" ~{tok_hist//1000}k hist, ~{tok_files//1000}k files ", tok_bg))
            if added_files: print(styled(f"+{len(added_files)} file(s)", "93m")); request = f"Added files: {', '.join(added_files)}. Please continue."; continue
            shell_commands = re.findall(rf'<{TAGS["shell"]}>(.*?)</{TAGS["shell"]}>', full_response, re.DOTALL)
            if shell_commands:
                results, denied = [], False
                for cmd in [s.strip() for s in shell_commands]:
                    print(f"{styled(cmd, '1m')}\n"); title("❓ nanocoder")
                    try: answer = input("\aRun? (y/n): ").strip().lower()
                    except EOFError: answer = "n"
                    if answer == "y":
                        try: output_lines, exit_code = run_shell_interactive(cmd); results.append(f"$ {cmd}\nexit={exit_code}\n" + "\n".join(truncate(output_lines)))
                        except Exception as err: results.append(f"$ {cmd}\nerror: {err}")
                    else: denied = True; break
                if denied: break
                request = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break

if __name__ == "__main__": main()
