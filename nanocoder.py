# curl -o ~/nanocoder.py https://raw.githubusercontent.com/koenvaneijk/nanocoder/refs/heads/main/nanocoder.py
VERSION = 27
TAGS = {"edit": "edit", "find": "find", "replace": "replace", "request": "request_files", "drop": "drop_files", "commit": "commit_message", "shell": "shell_command", "create": "create"}
SYSTEM_PROMPT = f'You are a coding expert. Answer any questions the user might have. If the user asks you to modify code, use this XML format:\n[{TAGS["edit"]} path="file.py"]\n[{TAGS["find"]}]exact code to replace[/{TAGS["find"]}]\n[{TAGS["replace"]}]new code[/{TAGS["replace"]}]\n[/{TAGS["edit"]}]\nTo delete, leave [{TAGS["replace"]}] empty. To create a new file: [{TAGS["create"]} path="new_file.py"]file content[/{TAGS["create"]}].\nTo request files content: [{TAGS["request"]}]path/f.py[/{TAGS["request"]}].\nTo drop irrelevant files from context to save cognitive capacity: [{TAGS["drop"]}]path/f.py[/{TAGS["drop"]}].\nTo run a shell command: [{TAGS["shell"]}]echo hi[/{TAGS["shell"]}]. The tool will ask the user to approve (y/n). After running, the shell output will be returned truncated (first 10 lines, then a TRUNCATED marker, then the last 40 lines; full output if <= 50 lines).\nWhen making edits provide a [{TAGS["commit"]}]...[/{TAGS["commit"]}].'.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil
from pathlib import Path

def ansi(code): return f"\033[{code}"
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

def get_map(root):
    output = []
    for filepath in (run(f"git -C {root} ls-files") or "").splitlines():
        if not Path(root, filepath).exists(): continue
        try:
            definitions = [node.name for node in ast.parse(Path(root, filepath).read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
            if definitions: output.append(f"{filepath}: " + ", ".join(definitions))
        except: pass
    return "\n".join(output)

TAG_COLORS = {TAGS["shell"]: '46;30m', TAGS["find"]: '41;37m', TAGS["replace"]: '42;30m', TAGS["commit"]: '44;37m', TAGS["request"]: '45;37m', TAGS["drop"]: '45;37m', TAGS["edit"]: '43;30m', TAGS["create"]: '43;30m'}
def get_tag_color(tag): return next((c for t, c in TAG_COLORS.items() if t in tag), None)

def render_md(text):
    """Render markdown: bold, inline code, headers, links. Preserves code blocks with grey background."""
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    result = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            # Fenced code block: grey background, white text
            inner = part[3:-3]
            if inner.startswith('\n'): inner = inner[1:]
            elif '\n' in inner: inner = inner.split('\n', 1)[1]  # remove language hint line
            result.append(f"\n{ansi('48;5;236;37m')}{inner}{ansi('0m')}")
        elif part.startswith('`') and part.endswith('`'):
            # Inline code: grey background
            result.append(f"{ansi('48;5;236m')}{part[1:-1]}{ansi('0m')}")
        else:
            # Links [text](url) -> OSC 8 clickable links (underlined blue)
            part = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: f"\033]8;;{m.group(2)}\033\\{ansi('4;34m')}{m.group(1)}{ansi('0m')}\033]8;;\033\\", part)
            # Bold
            part = re.sub(r'\*\*(.+?)\*\*', lambda m: f"{ansi('1m')}{m.group(1)}{ansi('22m')}", part)
            # Headers (only at line start)
            part = re.sub(r'^(#{1,3}) (.+)$', lambda m: f"{ansi('1;33m')}{m.group(2)}{ansi('0m')}", part, flags=re.MULTILINE)
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
    if not api_key: return print(f"{ansi('31m')}Err: Missing OPENAI_API_KEY{ansi('0m')}")
    stop_event, full_response, buffer, md_buffer = threading.Event(), "", "", ""
    in_xml_tag, in_code_fence = False, False
    def spin():
        spinner_idx = 0
        while not stop_event.is_set(): print(f"\r{ansi('47;30m')} AI {ansi('0m')} {'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[spinner_idx % 10]} ", end="", flush=True); time.sleep(0.1); spinner_idx += 1
    def flush_md():
        nonlocal md_buffer
        if md_buffer: print(render_md(md_buffer), end="", flush=True); md_buffer = ""
    spinner_thread = threading.Thread(target=spin, daemon=True); spinner_thread.start()
    request = urllib.request.Request(f"{os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, data=json.dumps({"model": model, "messages": messages, "stream": True}).encode())
    try:
        with urllib.request.urlopen(request) as response:
            started = False
            for line in response:
                if not line.startswith(b"data: "): continue
                if not started: stop_event.set(); spinner_thread.join(); print(f"\r{ansi('47;30m')} AI {ansi('0m')} ", end="", flush=True); started = True
                try:
                    chunk = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                    full_response += chunk; buffer += chunk
                    tag_pattern = re.compile(r'<(/?(?:' + '|'.join(TAGS.values()) + r'))(?:\s[^>]*)?>')
                    while buffer:
                        # Check for code fence toggle (``` at line start or after newline)
                        fence_match = re.match(r'^(```[^\n]*\n?)', buffer) if not in_xml_tag else None
                        if not fence_match and not in_xml_tag:
                            fence_pos = buffer.find('\n```')
                            if fence_pos != -1: fence_match = re.match(r'^(\n```[^\n]*\n?)', buffer[fence_pos:])
                        if fence_match and not in_xml_tag:
                            fence_pos = buffer.find(fence_match.group(0))
                            # Text before fence goes to md_buffer
                            md_buffer += buffer[:fence_pos]
                            if not in_code_fence: flush_md()  # Flush markdown before entering code block
                            in_code_fence = not in_code_fence
                            fence_text = fence_match.group(0)
                            if in_code_fence:
                                print(f"{ansi('48;5;236;37m')}", end="", flush=True)  # Start grey background
                            else:
                                print(f"{ansi('0m')}", end="", flush=True)  # End grey background
                            buffer = buffer[fence_pos + len(fence_text):]
                            continue
                        # Check for XML tag
                        match = tag_pattern.search(buffer)
                        if match and not in_code_fence:
                            before_tag = buffer[:match.start()]
                            if in_xml_tag:
                                print(before_tag, end="", flush=True)  # Raw output inside XML tags
                            else:
                                md_buffer += before_tag
                                flush_md()  # Flush markdown before tag
                            tag = match.group(0); color = get_tag_color(tag)
                            print(f"{ansi(color)}{tag}{ansi('0m')}" if color else tag, end="", flush=True)
                            # Track if we're entering or leaving an XML tag
                            if tag.startswith('</'): in_xml_tag = False
                            else: in_xml_tag = True
                            buffer = buffer[match.end():]
                        else:
                            # No complete tag found yet
                            lt_pos = buffer.rfind('<') if not in_code_fence else -1
                            if lt_pos != -1 and not in_xml_tag:
                                # Potential incomplete tag, buffer it
                                md_buffer += buffer[:lt_pos]
                                # Flush on paragraph boundary if not in code fence
                                if '\n\n' in md_buffer:
                                    parts = md_buffer.rsplit('\n\n', 1)
                                    md_buffer = parts[0] + '\n\n'
                                    flush_md()
                                    md_buffer = parts[1] if len(parts) > 1 else ""
                                buffer = buffer[lt_pos:]
                            elif in_xml_tag:
                                # Inside XML tag: print raw, but buffer potential closing tags
                                if lt_pos != -1:
                                    print(buffer[:lt_pos], end="", flush=True)
                                    buffer = buffer[lt_pos:]
                                else:
                                    print(buffer, end="", flush=True); buffer = ""
                            elif in_code_fence:
                                # Inside code fence: print with grey background
                                print(f"{ansi('48;5;236;37m')}{buffer}", end="", flush=True); buffer = ""
                            else:
                                # Outside tag: accumulate for markdown
                                md_buffer += buffer
                                # Flush on paragraph boundary
                                if '\n\n' in md_buffer:
                                    parts = md_buffer.rsplit('\n\n', 1)
                                    md_buffer = parts[0] + '\n\n'
                                    flush_md()
                                    md_buffer = parts[1] if len(parts) > 1 else ""
                                buffer = ""
                            break
                except: pass
            if buffer: 
                if in_xml_tag: print(buffer, end="", flush=True)
                elif in_code_fence: print(f"{ansi('48;5;236;37m')}{buffer}", end="", flush=True)
                else: md_buffer += buffer
            flush_md()
            if in_code_fence: print(f"{ansi('0m')}", end="", flush=True)  # Reset if stream ended in code block
    except urllib.error.HTTPError as err: stop_event.set(); spinner_thread.join(); print(f"\n{ansi('31m')}HTTP {err.code}: {err.reason}{ansi('0m')}")
    except Exception as err: stop_event.set(); spinner_thread.join(); print(f"\n{ansi('31m')}Err: {err}{ansi('0m')}")
    print("\n"); return full_response

def apply_edits(text, root):
    changes = 0
    for path, content in re.findall(rf'<{TAGS["create"]} path="(.*?)">(.*?)</{TAGS["create"]}>', text, re.DOTALL):
        filepath = Path(root, path)
        if filepath.exists(): print(f"{ansi('31m')}Skip create {path} (already exists){ansi('0m')}"); continue
        content = content.strip()
        if path.endswith(".py"):
            try: ast.parse(content)
            except SyntaxError as err: print(f"{ansi('31m')}Lint Fail {path}: {err}{ansi('0m')}"); continue
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        for line in content.splitlines(): print(f"{ansi('32m')}+{line}{ansi('0m')}")
        print(f"{ansi('32m')}Created {path}{ansi('0m')}"); changes += 1
    for path, find_text, replace_text in re.findall(rf'<{TAGS["edit"]} path="(.*?)">\s*<{TAGS["find"]}>(.*?)</{TAGS["find"]}>\s*<{TAGS["replace"]}>(.*?)</{TAGS["replace"]}>\s*</{TAGS["edit"]}>', text, re.DOTALL):
        filepath = Path(root, path)
        if not filepath.exists(): print(f"{ansi('31m')}Skip {path} (not found){ansi('0m')}"); continue
        content = filepath.read_text()
        if find_text.strip() not in content: print(f"{ansi('31m')}Match failed in {path}{ansi('0m')}"); continue
        new_content = content.replace(find_text.strip(), replace_text.strip(), 1)
        if path.endswith(".py"):
            try: ast.parse(new_content)
            except SyntaxError as err: print(f"{ansi('31m')}Lint Fail {path}: {err}{ansi('0m')}"); continue
        if content != new_content:
            for diff_line in difflib.unified_diff(content.splitlines(), new_content.splitlines(), lineterm=""):
                if not diff_line.startswith(('---','+++')): print(f"{ansi('32m' if diff_line.startswith('+') else '31m' if diff_line.startswith('-') else '0m')}{diff_line}{ansi('0m')}")
            filepath.write_text(new_content); print(f"{ansi('32m')}Applied {path}{ansi('0m')}"); changes += 1
    commit_match = re.search(rf'<{TAGS["commit"]}>(.*?)</{TAGS["commit"]}>', text, re.DOTALL)
    if changes: run(f"git add -A && git commit -m '{commit_match.group(1).strip() if commit_match else 'Update'}'")

def main():
    repo_root, context_files, history = run("git rev-parse --show-toplevel") or os.getcwd(), set(), []
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"{ansi('47;30m')} nanocoder v{VERSION} {ansi('0m')} {ansi('47;30m')} {model} {ansi('0m')} {ansi('47;30m')} ctrl+d to send {ansi('0m')}")
    while True:
        title("❓ nanocoder"); print(f"\a{ansi('1;34m')}> {ansi('0m')}", end="", flush=True); input_lines = []
        try:
            while True: input_lines.append(input())
        except EOFError: pass
        except KeyboardInterrupt: title(""); break
        user_input = "\n".join(input_lines).strip()
        if not user_input: continue
        if user_input.startswith("/"):
            command, _, arg = user_input.partition(" ")
            if command == "/add": found = [filepath for filepath in glob.glob(arg, root_dir=repo_root, recursive=True) if Path(repo_root, filepath).is_file()]; context_files.update(found); print(f"Added {len(found)} files")
            elif command == "/drop": context_files.discard(arg)
            elif command == "/clear": history = []; print("History cleared.")
            elif command == "/undo": run("git reset --soft HEAD~1")
            elif command == "/exit": print("Bye!"); title(""); break
            elif command == "/help": print("/add <glob> - Add files to context\n/drop <file> - Remove file from context\n/clear - Clear conversation history\n/undo - Undo last commit\n/update - Update nanocoder\n/exit - Exit\n!<cmd> - Run shell command")
            elif command == "/update":
                try: current_content, remote_content = Path(__file__).read_text(), urllib.request.urlopen("https://raw.githubusercontent.com/koenvaneijk/nanocoder/refs/heads/main/nanocoder.py").read().decode(); current_content != remote_content and (Path(__file__).write_text(remote_content), print("Updated! Restarting..."), os.execv(sys.executable, [sys.executable] + sys.argv))
                except: print("Update failed")
            continue

        if user_input.startswith("!"):
            shell_cmd = user_input[1:].strip()
            if shell_cmd:
                output_lines, exit_code = run_shell_interactive(shell_cmd)
                print(f"\n{ansi('90m')}exit={exit_code}{ansi('0m')}"); title("❓ nanocoder")
                try: answer = input("\aAdd to context? [t]runcated/[f]ull/[n]o: ").strip().lower()
                except EOFError: answer = "n"
                if answer in ("t", "f"):
                    history.append({"role": "user", "content": f"$ {shell_cmd}\nexit={exit_code}\n" + "\n".join(truncate(output_lines) if answer == "t" else output_lines)})
                    print(f"{ansi('93m')}Added to context{ansi('0m')}")
            continue

        request = user_input
        while True:
            context = f"### Repo Map\n{get_map(repo_root)}\n### Files\n" + "\n".join([f"File: {filepath}\n```\n{Path(repo_root,filepath).read_text()}\n```" for filepath in context_files if Path(repo_root,filepath).exists()])
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": f"System summary: {json.dumps(system_summary(), separators=(',',':'))}"}] + history + [{"role": "user", "content": f"{context}\nRequest: {request}"}]
            title("⏳ nanocoder"); full_response = stream_chat(messages, model); history.extend([{"role": "user", "content": request}, {"role": "assistant", "content": full_response}]); apply_edits(full_response, repo_root)
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
            if added_files: print(f"{ansi('93m')}+{len(added_files)} file(s){ansi('0m')}"); request = f"Added files: {', '.join(added_files)}. Please continue."; continue
            shell_commands = re.findall(rf'<{TAGS["shell"]}>(.*?)</{TAGS["shell"]}>', full_response, re.DOTALL)
            if shell_commands:
                results = []
                for cmd in [s.strip() for s in shell_commands]:
                    print(f"{ansi('1m')}{cmd}{ansi('0m')}\n"); title("❓ nanocoder")
                    try: answer = input("\aRun? (y/n): ").strip().lower()
                    except EOFError: answer = "n"
                    if answer == "y":
                        try: output_lines, exit_code = run_shell_interactive(cmd); results.append(f"$ {cmd}\nexit={exit_code}\n" + "\n".join(truncate(output_lines)))
                        except Exception as err: results.append(f"$ {cmd}\nerror: {err}")
                    else: results.append(f"$ {cmd}\nDENIED by user.")
                request = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break

if __name__ == "__main__": main()
