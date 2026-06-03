"""
ga_cli/repl.py - GenericAgent REPL 交互模式

使用 prompt_toolkit + Rich 构建的增强型命令行界面
支持流式输出、子命令、历史记录等功能
"""
import os, sys, threading, queue, time, json
from typing import Optional, Dict, Any

# Windows GBK 终端兼容
if sys.platform == "win32" and sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312"):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors="replace")

# 添加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.patch_stdout import patch_stdout
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.table import Table
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    MISSING_DEP = str(e)

from agentmain import GenericAgent


class REPLSession:
    """GenericAgent REPL 交互会话"""
    
    def __init__(self, agent: GenericAgent, llm_no: int = 0, verbose: bool = False):
        self.agent = agent
        self.console = Console()
        self.running = False
        self.current_task_queue = None
        self.output_thread = None
        
        # 子命令注册
        self.commands: Dict[str, Dict[str, Any]] = {}
        self._register_default_commands()
        
        # 历史记录文件
        history_dir = os.path.join(PROJECT_DIR, "temp")
        os.makedirs(history_dir, exist_ok=True)
        self.history_file = os.path.join(history_dir, "repl_history")
        
        # 命令补全器
        self.completer = WordCompleter(
            list(self.commands.keys()) + ["/help", "/exit", "/quit", "/clear", "/history"],
            ignore_case=True
        )
        
        # 初始化 agent
        self.agent.next_llm(llm_no)
        self.agent.verbose = verbose
        self.agent.inc_out = True
        
        # 启动 agent 后台线程
        self.agent_thread = threading.Thread(target=self.agent.run, daemon=True)
        self.agent_thread.start()
    
    def _register_default_commands(self):
        """注册默认子命令"""
        from .repl_commands import register_commands
        register_commands(self)
    
    def register_command(self, name: str, handler, help_text: str, aliases: list = None):
        """注册子命令"""
        self.commands[name] = {
            "handler": handler,
            "help": help_text,
            "aliases": aliases or []
        }
        # 注册别名
        for alias in (aliases or []):
            self.commands[alias] = self.commands[name]
    
    def _show_welcome(self):
        """显示欢迎信息"""
        model_name = "unknown"
        try:
            model_name = self.agent.get_llm_name(model=True)
        except:
            pass
        
        welcome_text = f"""[bold green]GenericAgent REPL[/bold green]
[dim]Version 0.1.0 | Model: {model_name}[/dim]

Type your message to chat with GenericAgent.
Commands start with / (type /help for list)
Press Ctrl+C to interrupt, Ctrl+D or /exit to quit.
"""
        self.console.print(Panel(welcome_text, title="Welcome", border_style="blue"))
    
    def _show_help(self):
        """显示帮助信息"""
        table = Table(title="REPL Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="green")
        table.add_column("Description")
        table.add_column("Aliases", style="dim")
        
        # 收集唯一命令
        seen = set()
        for name, info in sorted(self.commands.items()):
            if name not in seen:
                aliases = [a for a in info["aliases"] if a != name]
                table.add_row(
                    name,
                    info["help"],
                    ", ".join(aliases) if aliases else ""
                )
                seen.add(name)
                seen.update(info["aliases"])
        
        self.console.print(table)
        self.console.print("\n[dim]Type /exit or press Ctrl+D to quit[/dim]")
    
    def _process_command(self, line: str) -> bool:
        """处理子命令，返回 True 表示已处理"""
        if not line.startswith("/"):
            return False
        
        parts = line.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd_name in ("/exit", "/quit"):
            self.running = False
            return True
        
        if cmd_name == "/help":
            self._show_help()
            return True
        
        if cmd_name == "/clear":
            os.system('cls' if os.name == 'nt' else 'clear')
            return True
        
        if cmd_name in self.commands:
            try:
                self.commands[cmd_name]["handler"](args)
            except Exception as e:
                self.console.print(f"[red]Error executing command: {e}[/red]")
            return True
        
        self.console.print(f"[red]Unknown command: {cmd_name}[/red]")
        self.console.print("[dim]Type /help for available commands[/dim]")
        return True
    
    def _stream_output(self, display_queue: queue.Queue):
        """流式输出处理线程"""
        try:
            while True:
                try:
                    item = display_queue.get(timeout=0.1)
                except queue.Empty:
                    if not self.running:
                        break
                    continue
                
                if "next" in item:
                    # 增量输出
                    text = item["next"]
                    try:
                        self.console.print(text, end="")
                    except:
                        print(text, end="", flush=True)
                
                if "done" in item:
                    # 任务完成
                    self.console.print()  # 换行
                    break
                    
        except Exception as e:
            self.console.print(f"\n[red]Output error: {e}[/red]")
    
    def _execute_task(self, query: str):
        """执行 agent 任务"""
        try:
            # 提交任务
            display_queue = self.agent.put_task(query, source="user")
            self.current_task_queue = display_queue
            
            # 启动输出线程
            self.output_thread = threading.Thread(
                target=self._stream_output,
                args=(display_queue,),
                daemon=True
            )
            self.output_thread.start()
            
            # 等待任务完成
            while self.output_thread.is_alive():
                time.sleep(0.1)
                if not self.running:
                    break
            
            self.current_task_queue = None
            
        except KeyboardInterrupt:
            self.agent.abort()
            self.console.print("\n[yellow]Task interrupted[/yellow]")
        except Exception as e:
            self.console.print(f"\n[red]Task error: {e}[/red]")
    
    def run(self):
        """运行 REPL 主循环"""
        if not HAS_DEPS:
            self.console.print(f"[red]Missing dependencies: {MISSING_DEP}[/red]")
            self.console.print("[yellow]Install with: pip install prompt_toolkit rich[/yellow]")
            return
        
        self._show_welcome()
        self.running = True
        
        # 创建 prompt session
        try:
            session = PromptSession(
                history=FileHistory(self.history_file),
                auto_suggest=AutoSuggestFromHistory(),
                completer=self.completer,
            )
        except Exception as e:
            self.console.print(f"[red]Failed to initialize prompt: {e}[/red]")
            # 回退到简单 input()
            self._run_simple()
            return
        
        with patch_stdout():
            while self.running:
                try:
                    # 获取模型名称用于提示符
                    model_name = "?"
                    try:
                        model_name = self.agent.get_llm_name(model=True)[:10]
                    except:
                        pass
                    
                    line = session.prompt(f"\x1b[92m✦\x1b[0m [{model_name}]> ")
                    
                    if not line.strip():
                        continue
                    
                    # 处理子命令
                    if self._process_command(line.strip()):
                        continue
                    
                    # 执行 agent 任务
                    self._execute_task(line.strip())
                    
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    self.running = False
                    break
                except Exception as e:
                    self.console.print(f"\n[red]Error: {e}[/red]")
        
        self.console.print("\n[dim]Goodbye![/dim]")
    
    def _run_simple(self):
        """简单模式回退（无 prompt_toolkit）"""
        self.console.print("[yellow]Running in simple mode (prompt_toolkit not available)[/yellow]")
        
        model_name = "?"
        try:
            model_name = self.agent.get_llm_name(model=True)[:10]
        except:
            pass
        
        while self.running:
            try:
                line = input(f"\x1b[92m✦\x1b[0m [{model_name}]> ").strip()
                
                if not line:
                    continue
                
                if self._process_command(line):
                    continue
                
                self._execute_task(line)
                
            except KeyboardInterrupt:
                continue
            except EOFError:
                self.running = False
            except Exception as e:
                self.console.print(f"\n[red]Error: {e}[/red]")
        
        print("\nGoodbye!")


def start_repl(llm_no: int = 0, verbose: bool = False):
    """启动 REPL 会话"""
    agent = GenericAgent()
    repl = REPLSession(agent, llm_no=llm_no, verbose=verbose)
    repl.run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GenericAgent REPL")
    parser.add_argument("--llm", type=int, default=0, help="LLM model number")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    start_repl(llm_no=args.llm, verbose=args.verbose)
