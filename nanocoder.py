VERSION = 49
TAGS = dict(edit="edit", find="find", replace="replace", request="request_files", drop="drop_files", commit="commit_message", shell="bash", create="create", detail="detail_map")
SYSTEM_PROMPT = f'''You are an assistant with expert coding capabilities and Bash scripting skills. Answer any questions the user might have.

## File Operations (PREFERRED over shell commands for reading files)
Request files to add to context (one path per line):
[{TAGS["request"]}]
path/file.py
[/{TAGS["request"]}]

Drop files you no longer need from context:
[{TAGS["drop"]}]
path/file.py
[/{TAGS["drop"]}]

Get structure overview of files/directories:
[{TAGS["detail"]}]
path/to/dir
[/{TAGS["detail"]}]

## Code Editing
[{TAGS["edit"]} path="file.py"]
[{TAGS["find"]}]lines to find[/{TAGS["find"]}]
[{TAGS["replace"]}]new code[/{TAGS["replace"]}]
[/{TAGS["edit"]}]
The [{TAGS["find"]}] text is replaced literally - keep it short, just enough to be unambiguous. Split large changes into multiple edits. Leave [{TAGS["replace"]}] empty to delete.

Create new files:
[{TAGS["create"]} path="new_file.py"]content[/{TAGS["create"]}]

## Shell Commands
[{TAGS["shell"]}]command[/{TAGS["shell"]}]
Use for: running tests, installing deps, searching with grep/find. User must approve. Output may be truncated.
IMPORTANT: Do NOT use cat/head/tail to read files - use [{TAGS["request"]}] instead for full, untruncated content.

When making edits provide a [{TAGS["commit"]}]...[/{TAGS["commit"]}].'''.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, struct, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil, traceback
from collections import defaultdict
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
        distro = None
        if platform.system() == "Linux":
            distro = run("lsb_release -ds 2>/dev/null") or run("cat /etc/os-release 2>/dev/null | grep -m1 PRETTY_NAME | cut -d= -f2 | tr -d '\"'")
        _CACHED_SYSTEM_INFO = {"os": platform.system(), "distro": distro, "release": platform.release(), "machine": platform.machine(), "python": sys.version.split()[0], "cwd": os.getcwd(), "shell": os.environ.get("SHELL") or os.environ.get("ComSpec") or "", "path": os.environ.get("PATH", ""), "venv": bool(os.environ.get("VIRTUAL_ENV") or sys.prefix != sys.base_prefix), "tools": [tool for tool in tools if shutil.which(tool)], "versions": {key: val for key, val in versions.items() if val}}
    except: _CACHED_SYSTEM_INFO = {}
    return _CACHED_SYSTEM_INFO

BINARY_EXT = {'.png','.jpg','.jpeg','.gif','.ico','.webp','.bmp','.mp3','.mp4','.wav','.avi','.mov','.zip','.tar','.gz','.rar','.7z','.pdf','.exe','.dll','.so','.dylib','.pyc','.woff','.woff2','.ttf','.eot'}
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', '.tox', 'dist', 'build', '.eggs', '.mypy_cache', '.pytest_cache', '.ruff_cache', 'htmlcov', '.coverage', 'env', '.env'}
SHOW_BUT_SKIP = {'.env', '.venv', 'venv', 'env'}  # Show in tree but don't recurse
MAX_FILE_TOKENS = 50000  # ~200KB - files larger than this get truncated
MAX_LINE_LENGTH = 1000   # Lines longer than this get truncated
MAX_SHELL_OUTPUT_CHARS = 20000  # Total shell output character limit

def _py_defs(path):
    try:
        defs = []
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args][:4]
                defs.append(f"def {node.name}({', '.join(args)}{'...' if len(node.args.args) > 4 else ''})")
            elif isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                defs.append(f"class {node.name}: {', '.join(methods)}" if methods else f"class {node.name}")
        return defs
    except: return None

def get_parent_context(root):
    """Get minimal listing of parent directory (sibling projects/files)."""
    try:
        root_path = Path(root).resolve()
        parent = root_path.parent
        if parent == root_path or str(parent) == '/':
            return None
        siblings = []
        for entry in sorted(parent.iterdir(), key=lambda p: (not p.is_dir(), p.name))[:15]:
            if entry.name.startswith('.') and entry.name not in {'.env', '.venv'}:
                continue
            if entry.resolve() == root_path:
                siblings.append(f"[{entry.name}]")  # Mark current repo
            elif entry.is_dir():
                siblings.append(f"{entry.name}/")
            else:
                siblings.append(entry.name)
        return f"../ ({parent.name}): {', '.join(siblings[:10])}" if siblings else None
    except:
        return None

def get_map(root, max_files=200, max_depth=6):
    files, root_path, noted_dirs = [], Path(root), []
    def walk(dir_path, depth):
        if depth > max_depth or len(files) >= max_files: return
        try: entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_file(), p.name))
        except (PermissionError, OSError): return
        for entry in entries:
            if entry.name in EXCLUDE_DIRS:
                if entry.name in SHOW_BUT_SKIP and entry.is_dir():
                    noted_dirs.append(str(entry.relative_to(root_path)))
                continue
            if entry.is_file():
                files.append(str(entry.relative_to(root_path)))
                if len(files) >= max_files: return
            elif entry.is_dir(): walk(entry, depth + 1)
    walk(root_path, 0)
    files = sorted(files, key=lambda f: (f.count('/'), f))[:max_files]
    dir_exts, ext_counts = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for f in files:
        ext = Path(f).suffix.lower() or '[no ext]'
        ext_counts[ext] += 1
        dir_exts[str(Path(f).parent)][ext] += 1
    parent_ctx = get_parent_context(root)
    summary = (f"{parent_ctx}\n" if parent_ctx else "") + f"{len(files)} files: " + ", ".join(f"{ext}({n})" for ext, n in sorted(ext_counts.items(), key=lambda x: -x[1])[:5])
    if noted_dirs:
        summary += f" | env dirs: {', '.join(sorted(noted_dirs))}"
    tree_lines = []
    for d in sorted(dir_exts.keys(), key=lambda x: (x.count('/'), x)):
        if d == '.' or d.count('/') >= 3: continue
        ext_str = ", ".join(f"{ext}({n})" for ext, n in sorted(dir_exts[d].items(), key=lambda x: -x[1])[:3])
        tree_lines.append(f"{'  ' * d.count('/')}{Path(d).name}/ ({ext_str})")
    file_lines = []
    for f in files:
        p = Path(root, f)
        if not p.exists(): continue
        if p.suffix.lower() in BINARY_EXT: file_lines.append(f"{f} [binary]"); continue
        defs = _py_defs(p) if f.endswith('.py') else None
        info = ": " + ", ".join(d.split('(')[0].split()[-1] for d in defs)[:60] if defs else ""
        file_lines.append(f"{f}{info}")
    return summary + "\n" + "\n".join(tree_lines) + ("\n---\n" if tree_lines else "\n") + "\n".join(file_lines)

def get_detail_map(root, paths):
    output = []
    for path in paths:
        p = Path(root, path.strip())
        if not p.exists(): output.append(f"{path}: [not found]"); continue
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not any(ex in f.parts for ex in EXCLUDE_DIRS):
                    output.append(_get_file_detail(root, str(f.relative_to(root))))
        else: output.append(_get_file_detail(root, path))
    return "\n".join(output)

def truncate_line(line, max_len=MAX_LINE_LENGTH):
    """Truncate a single line if too long."""
    if len(line) <= max_len:
        return line
    return line[:max_len] + f" [...+{len(line)-max_len} chars]"

def read_file_safely(path, max_tokens=MAX_FILE_TOKENS):
    """Read file with truncation for very large files and long lines."""
    try:
        content = Path(path).read_text()
    except:
        return "[binary/unreadable]", []
    
    if not content:
        return "[empty]", []
    
    # Truncate long lines first
    lines = content.splitlines()
    has_long_lines = any(len(l) > MAX_LINE_LENGTH for l in lines)
    lines = [truncate_line(l) for l in lines]
    content = '\n'.join(lines)
    
    # Check total size and truncate if needed
    est_tokens = len(content) // 4
    warnings = []
    
    if has_long_lines:
        warnings.append("[warning: file has very long lines, may be minified/binary]")
    
    if est_tokens > max_tokens:
        head_chars = max_tokens * 2  # ~50% for head
        tail_chars = max_tokens      # ~25% for tail
        omitted_tokens = (len(content) - head_chars - tail_chars) // 4
        content = (content[:head_chars] + 
                   f"\n\n[... TRUNCATED: ~{omitted_tokens // 1000}k tokens omitted ...]\n\n" +
                   content[-tail_chars:])
        warnings.append(f"[warning: file truncated from ~{est_tokens // 1000}k tokens]")
    
    return content, warnings

def truncate_shell_output(output_lines, max_chars=MAX_SHELL_OUTPUT_CHARS):
    """Truncate shell output: per-line and total character limit."""
    truncated_lines = [truncate_line(line) for line in output_lines]
    
    # Apply line count limit (keep first 10 + last 40 if over 50)
    if len(truncated_lines) > 50:
        truncated_lines = truncated_lines[:10] + ["[...TRUNCATED LINES...]"] + truncated_lines[-40:]
    
    # Apply total character limit
    result = []
    total_chars = 0
    for i, line in enumerate(truncated_lines):
        if total_chars + len(line) > max_chars:
            remaining = len(truncated_lines) - i
            result.append(f"[...TRUNCATED: {remaining} more lines, ~{(sum(len(l) for l in truncated_lines[i:]))//1000}k chars...]")
            break
        result.append(line)
        total_chars += len(line) + 1  # +1 for newline
    
    return result

def _get_file_detail(root, filepath):
    p = Path(root, filepath)
    if not p.exists(): return f"{filepath}: [not found]"
    if p.suffix.lower() in BINARY_EXT: return f"{filepath}: [binary]"
    if p.suffix == '.py':
        defs = _py_defs(p)
        if defs: return f"{filepath}:\n  " + "\n  ".join(defs)
        return f"{filepath}: [empty]" if defs == [] else f"{filepath}: [parse error]"
    try: return f"{filepath}: {len(p.read_text().splitlines())} lines"
    except: return f"{filepath}: [unreadable]"

TAG_COLORS = {"bash": '46;30m', "find": '41;37m', "replace": '42;30m', "commit": '44;37m', "request": '45;37m', "drop": '45;37m', "edit": '43;30m', "create": '43;30m', "detail": '45;37m'}
def get_tag_color(tag): return next((c for t, c in TAG_COLORS.items() if t in tag), None)

def is_bedrock(url): return url and "amazonaws.com" in url

def parse_aws_event_stream(response):
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
            part = re.sub(r'^(#{1,3}) (.+)$', lambda m: f"{ansi(['1;4;33m','1;33m','33m'][min(len(m.group(1)),3)-1])}{m.group(2)}{ansi('0m')}", part, flags=re.MULTILINE)
            result.append(part)
    return ''.join(result)

DANGEROUS_PATTERNS = [r'\bsudo\b', r'\brm\b', r'\brmdir\b', r'\b(mkfs|dd|chmod|chown|chroot|mount|umount)\b', r'\b(shutdown|reboot|poweroff|halt|init)\b', r'\b(kill|pkill|killall)\b', r'\b(iptables|ufw|systemctl|service|launchctl)\b', r'\b(apt|apt-get|dpkg|yum|dnf|pacman)\s+.*(install|remove|purge|-[iRS])', r'\b(pip3?|npm)\s+(uninstall|rm|prune)', r'\bgit\s+(push\s+-f|reset\s+--hard|clean\s+-[fd]|checkout\s+--\s+\.|branch\s+-[dD])', r'>\s*/(dev|etc|sys|proc)/', r'\b(format|fdisk|parted|mkswap)\b', r'\b(curl|wget)\b.*\|\s*(ba)?sh']

def is_dangerous_command(cmd):
    cmd_lower = cmd.lower()
    return any(re.search(pattern, cmd_lower) for pattern in DANGEROUS_PATTERNS)

def run_shell_interactive(cmd):
    output_lines, process = [], subprocess.Popen(cmd, shell=True, executable='/bin/bash', text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
    in_xml, in_code, interrupted = [False], [False], [False]
    tag_re = re.compile(r'<(/?(?:' + '|'.join(TAGS.values()) + r'))(?:\s[^>]*)?>')
    def spin():
        i = 0; chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"; print()
        while not stop_event.is_set(): print(f"\r{styled(' AI ', '47;30m')} {chars[i % len(chars)]} ", end="", flush=True); time.sleep(0.1); i += 1
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
                        fence = re.match(r'^(```[^\n]*\n?)', buffer) if not in_xml[0] else None
                        if not fence and not in_xml[0] and '\n```' in buffer: fence = re.match(r'^(\n```[^\n]*\n?)', buffer[buffer.find('\n```'):])
                        if fence and not in_xml[0]:
                            pos = buffer.find(fence.group(0))
                            if in_code[0]: out(buffer[:pos] + ansi('0m'), True)
                            else: md_buffer += buffer[:pos]; out(md_buffer); md_buffer = ""
                            in_code[0] = not in_code[0]
                            if in_code[0]: print(f"{ansi('48;5;236;37m')}", end="", flush=True)
                            buffer = buffer[pos + len(fence.group(0)):]; continue
                        match = tag_re.search(buffer)
                        if match and not in_code[0]:
                            before = buffer[:match.start()]
                            if in_xml[0]: print(before, end="", flush=True)
                            else: md_buffer += before; out(md_buffer); md_buffer = ""
                            tag, color = match.group(0), get_tag_color(match.group(0))
                            print(f"{ansi(color)}{tag}{ansi('0m')}" if color else tag, end="", flush=True)
                            in_xml[0] = not tag.startswith('</'); buffer = buffer[match.end():]
                        else:
                            lt = buffer.rfind('<') if not in_code[0] else -1
                            if lt != -1 and not in_xml[0]: md_buffer += buffer[:lt]; try_flush(); buffer = buffer[lt:]
                            elif in_xml[0]:
                                if lt != -1: print(buffer[:lt], end="", flush=True); buffer = buffer[lt:]
                                else: print(buffer, end="", flush=True); buffer = ""
                            elif in_code[0]: out(buffer, True); buffer = ""
                            else: md_buffer += buffer; try_flush(); buffer = ""
                            break
            if buffer: (print(buffer, end="", flush=True) if in_xml[0] else out(buffer, in_code[0]) if in_code[0] else (md_buffer := md_buffer + buffer, out(md_buffer)))
            elif md_buffer: out(md_buffer)
            if in_code[0]: print(ansi('0m'), end="", flush=True)
    except KeyboardInterrupt: stop_event.set(); spinner.join(); interrupted[0] = True; md_buffer and out(md_buffer); in_code[0] and print(ansi('0m'), end="", flush=True); print(f"\n{styled('[user interrupted]', '93m')}")
    except urllib.error.HTTPError as e:
        stop_event.set(); spinner.join()
        error_body = ""
        try: error_body = e.read().decode('utf-8', errors='replace')[:500]
        except: pass
        print(f"\n{styled(f'HTTP {e.code}: {e.reason}', '31m')}")
        if error_body: print(styled(f"Response: {error_body}", '31m'))
    except Exception as e: stop_event.set(); spinner.join(); print(f"\n{styled(f'Err: {e}', '31m')}"); traceback.print_exc()
    print("\n"); return full_response, interrupted[0]

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
    auto_approve, auto_approve_all, last_interrupt = False, False, 0.0
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"{styled(' nanocoder v' + str(VERSION) + ' ', '47;30m')} {styled(' ' + model + ' ', '47;30m')} {styled(' ctrl+d to send ', '47;30m')}")
    while True:
        title("❓ nanocoder"); print(f"\a{styled('❯ ', '1;34m')}", end="", flush=True); input_lines = []
        try:
            while True: input_lines.append(input())
        except EOFError: pass
        except KeyboardInterrupt:
            now = time.time()
            if now - last_interrupt < 2.0:
                print(f"\n{styled('Bye!', '93m')}"); title(""); return
            last_interrupt = now
            print(f"\n{styled('Press Ctrl+C again within 2s to exit', '93m')}"); continue
        if not (user_input := "\n".join(input_lines).strip()): continue
        if user_input.startswith("/"):
            command, _, arg = user_input.partition(" ")
            def cmd_add(): found = [f for f in glob.glob(arg, root_dir=repo_root, recursive=True) if Path(repo_root, f).is_file()]; context_files.update(found); print(f"Added {len(found)} files")
            commands = {"/add": cmd_add, "/drop": lambda: context_files.discard(arg), "/clear": lambda: (history.clear(), print("Cleared.")), "/undo": lambda: run("git reset --soft HEAD~1"), "/exit": lambda: (print("Bye!"), title(""), sys.exit(0)), "/help": lambda: print("/add /drop /clear /undo /exit")}
            if command in commands: commands[command]()
            continue

        request = user_input
        def read(p):
            content, warnings = read_file_safely(Path(repo_root, p))
            if warnings:
                return f"{chr(10).join(warnings)}\n{content}"
            return content
        while True:
            # Build context hints based on state
            context_hints = []
            if len(context_files) > 5:
                context_hints.append(f"Note: {len(context_files)} files in context. Consider using <drop_files> to remove files you no longer need.")
            hints_str = "\n".join(context_hints)
            
            context = f"### Repo Map\n{get_map(repo_root)}\n### Files in Context\n" + "\n".join(f"File: {f}\n```\n{read(f)}\n```" for f in context_files if Path(repo_root,f).exists())
            if hints_str:
                context = f"{hints_str}\n\n{context}"
            system_prompt = SYSTEM_PROMPT
            sys_info = system_summary()
            os_desc = sys_info.get("distro") or sys_info.get("os", "unknown")
            messages = [{"role": "system", "content": system_prompt}, {"role": "system", "content": f"You are on a {os_desc} machine. System details: {json.dumps(sys_info, separators=(',',':'))}"}] + history + [{"role": "user", "content": f"{context}\nRequest: {request}"}]
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
            detail_requests = re.findall(rf'<{TAGS["detail"]}>(.*?)</{TAGS["detail"]}>', full_response, re.DOTALL)
            if detail_requests:
                paths = [p.strip() for req in detail_requests for p in req.strip().split('\n') if p.strip()]
                detail_info = get_detail_map(repo_root, paths)
                print(styled(f"Detail map for: {', '.join(paths)}", "93m"))
                request = f"Detail map:\n{detail_info}\nPlease continue."
                continue
            tok_hist = sum(len(m.get("content","")) for m in history)//4
            tok_files = sum(len(read(f)) for f in context_files)//4
            tok_total = tok_hist + tok_files
            tok_bg = '47;30m' if tok_total < 80000 else '43;30m' if tok_total < 120000 else '41;37m'
            print(styled(f" ~{tok_hist//1000}k hist, ~{tok_files//1000}k files ", tok_bg))
            if len(context_files) > 0:
                print(styled(f" context: {', '.join(sorted(context_files)[:5])}{'...' if len(context_files) > 5 else ''} ", '90m'))
            if added_files: print(styled(f"+{len(added_files)} file(s)", "93m")); request = f"Added files: {', '.join(added_files)}. Please continue."; continue
            shell_commands = re.findall(rf'<{TAGS["shell"]}>(.*?)</{TAGS["shell"]}>', full_response, re.DOTALL)
            if shell_commands:
                results, denied = [], False
                for cmd in [s.strip() for s in shell_commands]:
                    # Detect file-reading commands and suggest request_files instead
                    file_read_match = re.match(r'^(cat|head|tail|less|more|bat)\s+([^\s|>]+)', cmd)
                    if file_read_match:
                        suggested_file = file_read_match.group(2)
                        if Path(repo_root, suggested_file).exists() and Path(repo_root, suggested_file).is_file():
                            print(styled(f"Hint: Use <request_files> for '{suggested_file}' instead of {file_read_match.group(1)}", "93m"))
                    print(f"{styled(cmd, '1m')}")
                    dangerous = is_dangerous_command(cmd)
                    if dangerous: print(styled("⚠ dangerous command", "93m"))
                    
                    if auto_approve_all or (auto_approve and not dangerous):
                        answer = "y"
                        print(styled("[auto-approved]", "90m"))
                    else:
                        try: answer = input(f"\aRun? (y/n/a=auto/A=all): ").strip()
                        except EOFError: answer = "n"
                        if answer == "A": auto_approve_all = True; print(styled("⚠ Full auto-approve enabled", "91m"))
                        elif answer == "a": auto_approve = True; print(styled("Auto-safe enabled", "93m"))
                        if answer in "aA": answer = "y"

                    if answer == "y":
                        try: output_lines, exit_code = run_shell_interactive(cmd); truncated = truncate_shell_output(output_lines); results.append(f"$ {cmd}\nexit={exit_code}\n" + "\n".join(truncated))
                        except Exception as err: results.append(f"$ {cmd}\nerror: {err}")
                    else: denied = True; break
                if denied: break
                request = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break
if __name__ == "__main__": main()
