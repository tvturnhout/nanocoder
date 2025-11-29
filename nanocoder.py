# curl -o ~/nanocoder.py https://raw.githubusercontent.com/koenvaneijk/nanocoder/refs/heads/main/nanocoder.py
VERSION = 24
TAG_EDIT, TAG_FIND, TAG_REPLACE, TAG_REQUEST, TAG_DROP, TAG_COMMIT, TAG_SHELL, TAG_CREATE = "edit", "find", "replace", "request_files", "drop_files", "commit_message", "shell_command", "create"
SYSTEM_PROMPT = f'You are a coding expert. Answer any questions the user might have. If the user asks you to modify code, use this XML format:\n[{TAG_EDIT} path="file.py"]\n[{TAG_FIND}]exact code to replace[/{TAG_FIND}]\n[{TAG_REPLACE}]new code[/{TAG_REPLACE}]\n[/{TAG_EDIT}]\nTo delete, leave [{TAG_REPLACE}] empty. To create a new file: [{TAG_CREATE} path="new_file.py"]file content[/{TAG_CREATE}].\nTo request files content: [{TAG_REQUEST}]path/f.py[/{TAG_REQUEST}].\nTo drop irrelevant files from context to save cognitive capacity: [{TAG_DROP}]path/f.py[/{TAG_DROP}].\nTo run a shell command: [{TAG_SHELL}]echo hi[/{TAG_SHELL}]. The tool will ask the user to approve (y/n). After running, the shell output will be returned truncated (first 10 lines, then a TRUNCATED marker, then the last 40 lines; full output if <= 50 lines).\nWhen making edits provide a [{TAG_COMMIT}]...[/{TAG_COMMIT}].'.replace('[', '<').replace(']', '>')

import ast, difflib, glob, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error, platform, shutil
from pathlib import Path

def ansi(code): return f"\033[{code}"
def title(t): print(f"\033]0;{t}\007", end="", flush=True)
def run(shell_cmd):
    try: return subprocess.check_output(shell_cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except: return None
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

def get_tag_color(tag):
    for tag_name, color_code in [(TAG_SHELL, '46;30m'), (TAG_FIND, '41;37m'), (TAG_REPLACE, '42;30m'), (TAG_COMMIT, '44;37m'), (TAG_REQUEST, '45;37m'), (TAG_DROP, '45;37m'), (TAG_EDIT, '43;30m'), (TAG_CREATE, '43;30m')]:
        if tag_name in tag: return color_code

def truncate(lines, max_lines=50): return lines[:10] + ["[TRUNCATED]"] + lines[-40:] if len(lines) > max_lines else lines

def stream_chat(messages, model):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: return print(f"{ansi('31m')}Err: Missing OPENAI_API_KEY{ansi('0m')}")
    stop_event, full_response, buffer = threading.Event(), "", ""
    def spin():
        spinner_idx = 0
        while not stop_event.is_set(): print(f"\r{ansi('47;30m')} AI {ansi('0m')} {'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[spinner_idx % 10]} ", end="", flush=True); time.sleep(0.1); spinner_idx += 1
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
                    known_tags = [TAG_EDIT, TAG_FIND, TAG_REPLACE, TAG_REQUEST, TAG_DROP, TAG_COMMIT, TAG_SHELL, TAG_CREATE]
                    tag_pattern = re.compile(r'<(/?(?:' + '|'.join(known_tags) + r'))(?:\s[^>]*)?>') 
                    while True:
                        match = tag_pattern.search(buffer)
                        if match:
                            print(buffer[:match.start()], end="", flush=True); tag = match.group(0); color = get_tag_color(tag)
                            print(f"{ansi(color)}{tag}{ansi('0m')}" if color else tag, end="", flush=True); buffer = buffer[match.end():]
                        else:
                            # Check if buffer might contain an incomplete tag - hold back from '<' onwards
                            lt_pos = buffer.rfind('<')
                            if lt_pos != -1:
                                print(buffer[:lt_pos], end="", flush=True); buffer = buffer[lt_pos:]
                            else:
                                print(buffer, end="", flush=True); buffer = ""
                            break
                except: pass
            if buffer: print(buffer, end="", flush=True)
    except urllib.error.HTTPError as err: stop_event.set(); spinner_thread.join(); print(f"\n{ansi('31m')}HTTP {err.code}: {err.reason}{ansi('0m')}")
    except Exception as err: stop_event.set(); spinner_thread.join(); print(f"\n{ansi('31m')}Err: {err}{ansi('0m')}")
    print("\n"); return full_response

def apply_edits(text, root):
    changes = 0
    # Handle file creation
    for path, content in re.findall(rf'<{TAG_CREATE} path="(.*?)">(.*?)</{TAG_CREATE}>', text, re.DOTALL):
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
    # Handle file edits
    for path, find_text, replace_text in re.findall(rf'<{TAG_EDIT} path="(.*?)">\s*<{TAG_FIND}>(.*?)</{TAG_FIND}>\s*<{TAG_REPLACE}>(.*?)</{TAG_REPLACE}>\s*</{TAG_EDIT}>', text, re.DOTALL):
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
    commit_match = re.search(rf'<{TAG_COMMIT}>(.*?)</{TAG_COMMIT}>', text, re.DOTALL)
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
        except KeyboardInterrupt: break
        user_input = "\n".join(input_lines).strip()
        if not user_input: continue
        if user_input.startswith("/"):
            command, _, arg = user_input.partition(" ")
            if command == "/add": found = [filepath for filepath in glob.glob(arg, root_dir=repo_root, recursive=True) if Path(repo_root, filepath).is_file()]; context_files.update(found); print(f"Added {len(found)} files")
            elif command == "/drop": context_files.discard(arg)
            elif command == "/clear": history = []; print("History cleared.")
            elif command == "/undo": run("git reset --soft HEAD~1")
            elif command == "/exit": print("Bye!"); break
            elif command == "/help": print("/add <glob> - Add files to context\n/drop <file> - Remove file from context\n/clear - Clear conversation history\n/undo - Undo last commit\n/update - Update nanocoder\n/exit - Exit\n!<cmd> - Run shell command")
            elif command == "/update":
                try: current_content, remote_content = Path(__file__).read_text(), urllib.request.urlopen("https://raw.githubusercontent.com/koenvaneijk/nanocoder/refs/heads/main/nanocoder.py").read().decode(); current_content != remote_content and (Path(__file__).write_text(remote_content), print("Updated! Restarting..."), os.execv(sys.executable, [sys.executable] + sys.argv))
                except: print("Update failed")
            continue

        if user_input.startswith("!"):
            shell_cmd = user_input[1:].strip()
            if shell_cmd:
                output_lines, process = [], subprocess.Popen(shell_cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                try:
                    for line in process.stdout: print(line, end="", flush=True); output_lines.append(line.rstrip('\n'))
                    process.wait()
                except KeyboardInterrupt: process.terminate(); process.wait(timeout=2); output_lines.append("[INTERRUPTED]"); print("\n[INTERRUPTED]")
                print(f"\n{ansi('90m')}exit={process.returncode}{ansi('0m')}")
                title("❓ nanocoder")
                try: answer = input("\aAdd to context? [t]runcated/[f]ull/[n]o: ").strip().lower()
                except EOFError: answer = "n"
                if answer in ("t", "f"):
                    history.append({"role": "user", "content": f"$ {shell_cmd}\nexit={process.returncode}\n" + "\n".join(truncate(output_lines) if answer == "t" else output_lines)})
                    print(f"{ansi('93m')}Added to context{ansi('0m')}")
            continue

        request = user_input
        while True:
            context = f"### Repo Map\n{get_map(repo_root)}\n### Files\n" + "\n".join([f"File: {filepath}\n```\n{Path(repo_root,filepath).read_text()}\n```" for filepath in context_files if Path(repo_root,filepath).exists()])
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": f"System summary: {json.dumps(system_summary(), separators=(',',':'))}"}] + history + [{"role": "user", "content": f"{context}\nRequest: {request}"}]
            title("⏳ nanocoder"); full_response = stream_chat(messages, model); history.extend([{"role": "user", "content": request}, {"role": "assistant", "content": full_response}]); apply_edits(full_response, repo_root)
            file_requests = re.findall(rf'<({TAG_REQUEST}|{TAG_DROP})>(.*?)</\1>', full_response, re.DOTALL)
            added_files = []
            for tag, content in file_requests:
                for filepath in content.strip().split('\n'):
                    filepath = filepath.strip()
                    if not filepath: continue
                    if tag == TAG_REQUEST and filepath not in context_files and Path(repo_root, filepath).exists():
                        added_files.append(filepath)
                    elif tag == TAG_DROP:
                        context_files.discard(filepath)
            context_files.update(added_files)
            if added_files: print(f"{ansi('93m')}+{len(added_files)} file(s){ansi('0m')}"); request = f"Added files: {', '.join(added_files)}. Please continue."; continue
            shell_commands = re.findall(rf'<{TAG_SHELL}>(.*?)</{TAG_SHELL}>', full_response, re.DOTALL)
            if shell_commands:
                results = []
                for cmd in [shell_cmd.strip() for shell_cmd in shell_commands]:
                    print(f"{ansi('1m')}{cmd}{ansi('0m')}\n"); answer = ""
                    title("❓ nanocoder")
                    try: answer = input("\aRun? (y/n): ").strip().lower()
                    except EOFError: answer = "n"
                    if answer == "y":
                        try:
                            output_lines, process = [], subprocess.Popen(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                            try:
                                for line in process.stdout: print(line, end="", flush=True); output_lines.append(line.rstrip('\n'))
                                process.wait()
                            except KeyboardInterrupt: process.terminate(); process.wait(timeout=2); output_lines.append("[INTERRUPTED by Ctrl+C]"); print("\n[INTERRUPTED by Ctrl+C]")
                            results.append(f"$ {cmd}\nexit={process.returncode}\n" + "\n".join(truncate(output_lines)))
                        except Exception as err: results.append(f"$ {cmd}\nerror: {err}")
                    else: results.append(f"$ {cmd}\nDENIED by user.")
                request = "Shell results:\n" + "\n\n".join(results) + "\nPlease continue."; continue
            break

if __name__ == "__main__": main()
