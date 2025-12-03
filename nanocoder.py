VERSION = 37
TAGS = {"edit": "edit", "find": "find", "replace": "replace", "request": "request_files", "drop": "drop_files", "commit": "commit_message", "shell": "shell_command", "create": "create"}
SYSTEM_PROMPT = f'You are a coding expert. Answer any questions the user might have. If the user asks you to modify code, use this XML format:\n[{TAGS["edit"]} path="file.py"]\n[{TAGS["find"]}]lines to find[/{TAGS["find"]}]\n[{TAGS["replace"]}]new code[/{TAGS["replace"]}]\n[/{TAGS["edit"]}]\nThe [{TAGS["find"]}] text is replaced literally, so it must match exactly. Keep it short - only enough lines to be unambiguous. Split large changes into multiple small edits.\nTo delete, leave [{TAGS["replace"]}] empty. To create a new file: [{TAGS["create"]} path="new_file.py"]file content[/{TAGS["create"]}].\nTo request files (one path per line):\n[{TAGS["request"]}]\npath/file1.py\npath/file2.py\n[/{TAGS["request"]}]\nTo drop files from context (one path per line):\n[{TAGS["drop"]}]\npath/file.py\n[/{TAGS["drop"]}]\nTo run a shell command: [{TAGS["shell"]}]echo hi[/{TAGS["shell"]}]. The tool will ask the user to approve (y/n). After running, the shell output will be returned truncated (first 10 lines, then a TRUNCATED marker, then the last 40 lines; full output if <= 50 lines).\nWhen making edits provide a [{TAGS["commit"]}]...[/{TAGS["commit"]}].'.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil
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

def get_map(root, max_defs=50):
    files = (run(f"git -C {root} ls-files") or "").splitlines()
    # Sort by depth (shallow first), then alphabetically
    files = sorted(files, key=lambda f: (f.count('/'), f))
    py_files = [f for f in files if f.endswith('.py')]
    output, count = [], 0
    for filepath in py_files:
        if count >= max_defs: break
        if not Path(root, filepath).exists(): continue
        try:
            definitions = [node.name for node in ast.parse(Path(root, filepath).read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
            if definitions: output.append(f"{filepath}: " + ", ".join(definitions)); count += 1
        except: pass
    # Directory summary
    dirs = {}
    for f in files:
        d = f.rsplit('/', 1)[0] if '/' in f else '.'
        dirs[d] = dirs.get(d, 0) + 1
    top_dirs = sorted(dirs.items(), key=lambda x: -x[1])[:10]
    summary = f"({len(files)} files, {len(py_files)} py) " + ", ".join(f"{d}({n})" for d, n in top_dirs)
    return summary + "\n" + "\n".join(output)

TAG_COLORS = {TAGS["shell"]: '46;30m', TAGS["find"]: '41;37m', TAGS["replace"]: '42;30m', TAGS["commit"]: '44;37m', TAGS["request"]: '45;37m', TAGS["drop"]: '45;37m', TAGS["edit"]: '43;30m', TAGS["create"]: '43;30m'}
def get_tag_color(tag): return next((c for t, c in TAG_COLORS.items() if t in tag), None)

def render_md(text):
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    result = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            inner = part[3:-3]
            if inner.startswith('\n'): inner = inner[1:]
            elif '\n' in inner: inner = inner.split('\n', 1)[1]
            # Add erase-to-end-of-line after each line to extend background color
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
                if level == 1: return f"{ansi('1;4;33m')}{text}{ansi('0m')}"  # Bold + underline + yellow
                elif level == 2: return f"{ansi('1;33m')}{text}{ansi('0m')}"  # Bold + yellow
                else: return f"{ansi('33m')}{text}{ansi('0m')}"  # Just yellow
            part = re.sub(r'^(#{1,3}) (.+)$', format_header, part, flags=re.MULTILINE)
            result.append(part)
    return ''.join(result)
def truncate(lines, n=50): return lines if len(lines) <= n else lines[:10] + ["[TRUNCATED]"] + lines[-40:]
DANGEROUS_PATTERNS = [re.compile(p, re.I) for p in [r'\brm\s+(-[a-z]*[rf]|/)', r'\b(sudo|su)\b', r'\bmkfs\b', r'\bdd\b', r'\b(shutdown|reboot|halt|poweroff)\b', r'>\s*/dev/', r'\bchmod\s+777', r'(curl|wget).*\|\s*(ba)?sh', r'git\s+push\s+(-f|--force)', r'git\s+reset\s+--hard', r'git\s+clean\s+-[a-z]*f', r'\bkill\s+-9', r'\b(killall|pkill)\b', r':()\s*{\s*:|:\s*&\s*};']]
def is_dangerous(cmd): return any(p.search(cmd) for p in DANGEROUS_PATTERNS)
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
    stop_event, full_response, buffer, md_buffer = threading.Event(), "", "", ""
    in_xml_tag, in_code_fence, interrupted = False, False, False
    def spin():
        spinner_idx = 0
        print()  # Move to new line before spinner
        while not stop_event.is_set(): print(f"\r{styled(' AI ', '47;30m')} {'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[spinner_idx % 10]} ", end="", flush=True); time.sleep(0.1); spinner_idx += 1
    def flush_md():
        nonlocal md_buffer
        if md_buffer: print(render_md(md_buffer), end="", flush=True); md_buffer = ""
    def flush_buffer():
        nonlocal buffer, md_buffer
        if not buffer: return
        if in_xml_tag: print(buffer, end="", flush=True)
        elif in_code_fence: print(f"{ansi('48;5;236;37m')}" + '\n'.join(f"{ln}{ansi('K')}" for ln in buffer.split('\n')), end="", flush=True)
        else: md_buffer += buffer
        buffer = ""
    spinner_thread = threading.Thread(target=spin, daemon=True); spinner_thread.start()
    request = urllib.request.Request(f"{os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, data=json.dumps({"model": model, "messages": messages, "stream": True}).encode())
    try:
        with urllib.request.urlopen(request) as response:
            started = False
            for line in response:
                if not line.startswith(b"data: "): continue
                if not started: stop_event.set(); spinner_thread.join(); print(f"\r{styled(' AI ', '47;30m')}   \n", end="", flush=True); started = True
                try:
                    chunk = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                    full_response += chunk; buffer += chunk
                    tag_pattern = re.compile(r'<(/?(?:' + '|'.join(TAGS.values()) + r'))(?:\s[^>]*)?>')
                    while buffer:
                        fence_match = re.match(r'^(```[^\n]*\n?)', buffer) if not in_xml_tag else None
                        if not fence_match and not in_xml_tag:
                            fence_pos = buffer.find('\n```')
                            if fence_pos != -1: fence_match = re.match(r'^(\n```[^\n]*\n?)', buffer[fence_pos:])
                        if fence_match and not in_xml_tag:
                            fence_pos = buffer.find(fence_match.group(0))
                            if in_code_fence:
                                # Print remaining code content WITH code styling before closing
                                print(f"{ansi('48;5;236;37m')}{buffer[:fence_pos]}{ansi('0m')}", end="", flush=True)
                            else:
                                md_buffer += buffer[:fence_pos]
                                flush_md()
                            in_code_fence = not in_code_fence
                            fence_text = fence_match.group(0)
                            if in_code_fence:
                                print(f"{ansi('48;5;236;37m')}", end="", flush=True)
                            buffer = buffer[fence_pos + len(fence_text):]
                            continue
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
                            lt_pos = buffer.rfind('<') if not in_code_fence else -1
                            if lt_pos != -1 and not in_xml_tag:
                                md_buffer += buffer[:lt_pos]
                                if '\n\n' in md_buffer:
                                    parts = md_buffer.rsplit('\n\n', 1)
                                    md_buffer = parts[0] + '\n\n'
                                    flush_md()
                                    md_buffer = parts[1] if len(parts) > 1 else ""
                                buffer = buffer[lt_pos:]
                            elif in_xml_tag:
                                if lt_pos != -1:
                                    print(buffer[:lt_pos], end="", flush=True)
                                    buffer = buffer[lt_pos:]
                                else:
                                    print(buffer, end="", flush=True); buffer = ""
                            elif in_code_fence:
                                print(f"{ansi('48;5;236;37m')}" + '\n'.join(f"{ln}{ansi('K')}" for ln in buffer.split('\n')), end="", flush=True); buffer = ""
                            else:
                                md_buffer += buffer
                                if '\n\n' in md_buffer:
                                    parts = md_buffer.rsplit('\n\n', 1)
                                    md_buffer = parts[0] + '\n\n'
                                    flush_md()
                                    md_buffer = parts[1] if len(parts) > 1 else ""
                                buffer = ""
                            break
                except: pass
            flush_buffer(); flush_md()
            if in_code_fence: print(ansi('0m'), end="", flush=True)
    except KeyboardInterrupt:
        stop_event.set(); spinner_thread.join(); interrupted = True
        flush_buffer(); flush_md()
        if in_code_fence: print(ansi('0m'), end="", flush=True)
        print(f"\n{styled('[user interrupted]', '93m')}")
    except urllib.error.HTTPError as err: stop_event.set(); spinner_thread.join(); print(f"\n{styled(f'HTTP {err.code}: {err.reason}', '31m')}")
    except Exception as err: stop_event.set(); spinner_thread.join(); print(f"\n{styled(f'Err: {err}', '31m')}")
    print("\n"); return full_response, interrupted

def apply_edits(text, root):
    changes = 0
    for path, content in re.findall(rf'<{TAGS["create"]} path="(.*?)">(.*?)</{TAGS["create"]}>', text, re.DOTALL):
        filepath = Path(root, path)
        if filepath.exists(): print(styled(f"Skip create {path} (already exists)", "31m")); continue
        content = content.strip()
        if path.endswith(".py"):
            try: ast.parse(content)
            except SyntaxError as err: print(styled(f"Lint Fail {path}: {err}", "31m")); continue
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        for line in content.splitlines(): print(styled(f"+{line}", "32m"))
        print(styled(f"Created {path}", "32m")); changes += 1
    for path, find_text, replace_text in re.findall(rf'<{TAGS["edit"]} path="(.*?)">\s*<{TAGS["find"]}>(.*?)</{TAGS["find"]}>\s*<{TAGS["replace"]}>(.*?)</{TAGS["replace"]}>\s*</{TAGS["edit"]}>', text, re.DOTALL):
        filepath = Path(root, path)
        if not filepath.exists(): print(styled(f"Skip {path} (not found)", "31m")); continue
        content = filepath.read_text()
        if find_text.strip() not in content: print(styled(f"Match failed in {path}", "31m")); continue
        new_content = content.replace(find_text.strip(), replace_text.strip(), 1)
        if path.endswith(".py"):
            try: ast.parse(new_content)
            except SyntaxError as err: print(styled(f"Lint Fail {path}: {err}", "31m")); continue
        if content != new_content:
            for diff_line in difflib.unified_diff(content.splitlines(), new_content.splitlines(), lineterm=""):
                if not diff_line.startswith(('---','+++')): print(styled(diff_line, '32m' if diff_line.startswith('+') else '31m' if diff_line.startswith('-') else '0m'))
            filepath.write_text(new_content); print(styled(f"Applied {path}", "32m")); changes += 1
    commit_match = re.search(rf'<{TAGS["commit"]}>(.*?)</{TAGS["commit"]}>', text, re.DOTALL)
    if changes: run(f"git add -A && git commit -m '{commit_match.group(1).strip() if commit_match else 'Update'}'")

def main():
    repo_root, context_files, history = run("git rev-parse --show-toplevel") or os.getcwd(), set(), []
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"{styled(' nanocoder v' + str(VERSION) + ' ', '47;30m')} {styled(' ' + model + ' ', '47;30m')} {styled(' ctrl+d to send ', '47;30m')}")
    while True:
        title("❓ nanocoder"); print(f"\a{styled('> ', '1;34m')}", end="", flush=True); input_lines = []
        try:
            while True: input_lines.append(input())
        except EOFError: pass
        except KeyboardInterrupt: print(); continue
        if not (user_input := "\n".join(input_lines).strip()): continue
        if user_input.startswith("/"):
            command, _, arg = user_input.partition(" ")
            if command == "/add": found = [filepath for filepath in glob.glob(arg, root_dir=repo_root, recursive=True) if Path(repo_root, filepath).is_file()]; context_files.update(found); print(f"Added {len(found)} files")
            elif command == "/drop": context_files.discard(arg)
            elif command == "/clear": history = []; print("History cleared.")
            elif command == "/undo": run("git reset --soft HEAD~1")
            elif command == "/exit": print("Bye!"); title(""); break
            elif command == "/help": print("/add <glob> - Add files to context\n/drop <file> - Remove file from context\n/clear - Clear conversation history\n/undo - Undo last commit\n/exit - Exit\n!<cmd> - Run shell command")
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
            def safe_read(filepath):
                try: return Path(repo_root, filepath).read_text()
                except (UnicodeDecodeError, OSError): return "[binary or unreadable file]"
            context = f"### Repo Map\n{get_map(repo_root)}\n### Files\n" + "\n".join([f"File: {filepath}\n```\n{safe_read(filepath)}\n```" for filepath in context_files if Path(repo_root,filepath).exists()])
            system_prompt = SYSTEM_PROMPT
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
            tok_hist, tok_files = sum(len(m.get("content","")) for m in history)//4, len(context)//4
            if added_files: print(styled(f"+{len(added_files)} file(s) (~{tok_hist//1000}k hist, ~{tok_files//1000}k files)", "93m")); request = f"Added files: {', '.join(added_files)}. Please continue."; continue
            print(styled(f"~{tok_hist//1000}k hist, ~{tok_files//1000}k files", "90m"))
            shell_commands = re.findall(rf'<{TAGS["shell"]}>(.*?)</{TAGS["shell"]}>', full_response, re.DOTALL)
            if shell_commands:
                results = []
                for cmd in [s.strip() for s in shell_commands]:
                    print(f"{styled(cmd, '1m')}\n"); title("❓ nanocoder")
                    if is_dangerous(cmd):
                        print(styled("⚠ Dangerous command detected", "1;31m"))
                        try: answer = input("\aRun? (y/n): ").strip().lower()
                        except EOFError: answer = "n"
                    else: answer = "y"
                    if answer == "y":
                        try: output_lines, exit_code = run_shell_interactive(cmd); results.append(f"$ {cmd}\nexit={exit_code}\n" + "\n".join(truncate(output_lines)))
                        except Exception as err: results.append(f"$ {cmd}\nerror: {err}")
                    else: results.append(f"$ {cmd}\nDENIED by user.")
                request = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break

if __name__ == "__main__": main()
