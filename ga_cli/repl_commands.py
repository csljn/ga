"""
ga_cli/repl_commands.py - REPL 子命令系统

提供各种实用子命令，增强 REPL 交互体验
"""
import os, sys, json, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .repl import REPLSession


def register_commands(repl: "REPLSession"):
    """注册所有子命令到 REPL 会话"""
    
    # /help - 显示帮助
    repl.register_command(
        "/help",
        lambda args: repl._show_help(),
        "显示帮助信息",
        aliases=["/?", "/h"]
    )
    
    # /exit, /quit - 退出
    repl.register_command(
        "/exit",
        lambda args: setattr(repl, 'running', False),
        "退出 REPL",
        aliases=["/quit", "/q"]
    )
    
    # /clear - 清屏
    repl.register_command(
        "/clear",
        lambda args: os.system('cls' if os.name == 'nt' else 'clear'),
        "清屏",
        aliases=["/cls"]
    )
    
    # /history - 显示历史记录
    def show_history(args):
        try:
            history_file = repl.history_file
            if os.path.exists(history_file):
                with open(history_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                count = int(args) if args.isdigit() else 20
                recent = lines[-count:]
                
                repl.console.print(f"\n[bold]Recent History (last {len(recent)}):[/bold]")
                for i, line in enumerate(recent, 1):
                    line = line.strip()
                    if line:
                        # 截断过长的行
                        display = line[:100] + "..." if len(line) > 100 else line
                        repl.console.print(f"  {i:3d}: {display}")
                repl.console.print()
            else:
                repl.console.print("[dim]No history yet[/dim]")
        except Exception as e:
            repl.console.print(f"[red]Error reading history: {e}[/red]")
    
    repl.register_command(
        "/history",
        show_history,
        "显示输入历史 (可选参数: 数量)",
        aliases=["/hist"]
    )
    
    # /model - 切换模型
    def switch_model(args):
        try:
            if not args:
                # 显示当前模型列表
                models = repl.agent.list_llms()
                repl.console.print("\n[bold]Available Models:[/bold]")
                for i, name, is_current in models:
                    marker = "●" if is_current else "○"
                    repl.console.print(f"  {marker} [{i}] {name}")
                repl.console.print(f"\n[dim]Use /model <number> to switch[/dim]")
            else:
                model_no = int(args)
                repl.agent.next_llm(model_no)
                new_model = repl.agent.get_llm_name(model=True)
                repl.console.print(f"[green]Switched to model: {new_model}[/green]")
        except ValueError:
            repl.console.print("[red]Invalid model number[/red]")
        except Exception as e:
            repl.console.print(f"[red]Error: {e}[/red]")
    
    repl.register_command(
        "/model",
        switch_model,
        "显示/切换 LLM 模型",
        aliases=["/m"]
    )
    
    # /abort - 中止当前任务
    def abort_task(args):
        if repl.agent.is_running:
            repl.agent.abort()
            repl.console.print("[yellow]Task aborted[/yellow]")
        else:
            repl.console.print("[dim]No task running[/dim]")
    
    repl.register_command(
        "/abort",
        abort_task,
        "中止当前运行的任务",
        aliases=["/stop", "/cancel"]
    )
    
    # /status - 显示状态
    def show_status(args):
        from rich.table import Table
        
        table = Table(title="Agent Status", show_header=False)
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        
        # 模型信息
        try:
            model_name = repl.agent.get_llm_name(model=True)
            model_backend = repl.agent.get_llm_name()
        except:
            model_name = "unknown"
            model_backend = "unknown"
        
        table.add_row("Model", model_name)
        table.add_row("Backend", model_backend)
        table.add_row("Running", "Yes" if repl.agent.is_running else "No")
        table.add_row("Verbose", "Yes" if repl.agent.verbose else "No")
        table.add_row("History", f"{len(repl.agent.history)} entries")
        
        repl.console.print(table)
    
    repl.register_command(
        "/status",
        show_status,
        "显示 agent 状态信息",
        aliases=["/info", "/st"]
    )
    
    # /reset - 重置对话历史
    def reset_history(args):
        repl.agent.history = []
        repl.console.print("[green]History cleared[/green]")
    
    repl.register_command(
        "/reset",
        reset_history,
        "清空对话历史",
        aliases=["/new"]
    )
    
    # /save - 保存对话到文件
    def save_conversation(args):
        try:
            filename = args or f"conversation_{int(time.time())}.json"
            if not filename.endswith('.json'):
                filename += '.json'
            
            save_path = os.path.join(repl.agent.log_path.rsplit('/', 1)[0] if '/' in repl.agent.log_path else '.', filename)
            
            data = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": repl.agent.get_llm_name(model=True),
                "history": repl.agent.history
            }
            
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            repl.console.print(f"[green]Saved to: {save_path}[/green]")
        except Exception as e:
            repl.console.print(f"[red]Error saving: {e}[/red]")
    
    repl.register_command(
        "/save",
        save_conversation,
        "保存对话到 JSON 文件 (可选参数: 文件名)"
    )
    
    # /load - 加载对话历史
    def load_conversation(args):
        try:
            if not args:
                # 列出可用的对话文件
                temp_dir = os.path.join(os.path.dirname(repl.agent.log_path), '.')
                if os.path.exists(temp_dir):
                    files = [f for f in os.listdir(temp_dir) if f.startswith('conversation_') and f.endswith('.json')]
                    if files:
                        repl.console.print("\n[bold]Available conversations:[/bold]")
                        for f in sorted(files, reverse=True)[:10]:
                            repl.console.print(f"  - {f}")
                        repl.console.print(f"\n[dim]Use /load <filename> to load[/dim]")
                    else:
                        repl.console.print("[dim]No saved conversations found[/dim]")
                return
            
            load_path = args
            if not os.path.isabs(load_path):
                load_path = os.path.join('.', load_path)
            
            if not os.path.exists(load_path):
                repl.console.print(f"[red]File not found: {load_path}[/red]")
                return
            
            with open(load_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'history' in data:
                repl.agent.history = data['history']
                repl.console.print(f"[green]Loaded {len(data['history'])} history entries from {load_path}[/green]")
            else:
                repl.console.print("[red]Invalid conversation file format[/red]")
        except Exception as e:
            repl.console.print(f"[red]Error loading: {e}[/red]")
    
    repl.register_command(
        "/load",
        load_conversation,
        "加载对话历史 (无参数列出可用文件)"
    )
    
    # /verbose - 切换详细模式
    def toggle_verbose(args):
        repl.agent.verbose = not repl.agent.verbose
        status = "enabled" if repl.agent.verbose else "disabled"
        repl.console.print(f"[green]Verbose mode {status}[/green]")
    
    repl.register_command(
        "/verbose",
        toggle_verbose,
        "切换详细输出模式",
        aliases=["/v"]
    )
    
    # /memory - 显示/管理记忆
    def show_memory(args):
        memory_dir = os.path.join(os.path.dirname(os.path.dirname(repl.agent.log_path)), 'memory')
        if not os.path.exists(memory_dir):
            repl.console.print("[dim]Memory directory not found[/dim]")
            return
        
        if not args or args == "list":
            # 列出记忆文件
            files = os.listdir(memory_dir)
            repl.console.print("\n[bold]Memory Files:[/bold]")
            for f in sorted(files):
                if f.endswith('.md') or f.endswith('.txt'):
                    size = os.path.getsize(os.path.join(memory_dir, f))
                    repl.console.print(f"  - {f} ({size} bytes)")
        elif args.startswith("show "):
            # 显示特定记忆文件
            filename = args[5:].strip()
            filepath = os.path.join(memory_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                repl.console.print(f"\n[bold]{filename}:[/bold]")
                repl.console.print(content[:2000] + ("..." if len(content) > 2000 else ""))
            else:
                repl.console.print(f"[red]File not found: {filename}[/red]")
        else:
            repl.console.print("[dim]Usage: /memory [list|show <filename>][/dim]")
    
    repl.register_command(
        "/memory",
        show_memory,
        "显示/管理记忆文件",
        aliases=["/mem"]
    )
    
    # /tools - 显示可用工具
    def show_tools(args):
        try:
            tools_path = os.path.join(os.path.dirname(os.path.dirname(repl.agent.log_path)), 'assets', 'tools_schema.json')
            if os.path.exists(tools_path):
                with open(tools_path, 'r', encoding='utf-8') as f:
                    tools = json.load(f)
                
                repl.console.print("\n[bold]Available Tools:[/bold]")
                for tool in tools.get('tools', []):
                    func = tool.get('function', {})
                    name = func.get('name', 'unknown')
                    desc = func.get('description', '')[:60]
                    repl.console.print(f"  - {name}: {desc}")
            else:
                repl.console.print("[dim]Tools schema not found[/dim]")
        except Exception as e:
            repl.console.print(f"[red]Error: {e}[/red]")
    
    repl.register_command(
        "/tools",
        show_tools,
        "显示可用工具列表"
    )
    
    # /config - 配置管理
    def show_config(args):
        config_path = os.path.join(os.path.dirname(os.path.dirname(repl.agent.log_path)), 'mykey.py')
        if os.path.exists(config_path):
            repl.console.print(f"\n[bold]Config file:[/bold] {config_path}")
            repl.console.print("[dim]Edit this file to configure API keys and settings[/dim]")
        else:
            repl.console.print("[dim]Config file not found. Run 'ga configure' first.[/dim]")
    
    repl.register_command(
        "/config",
        show_config,
        "显示配置文件信息"
    )
    
    # /time - 显示当前时间
    def show_time(args):
        repl.console.print(f"[cyan]{time.strftime('%Y-%m-%d %H:%M:%S')}[/cyan]")
    
    repl.register_command(
        "/time",
        show_time,
        "显示当前时间",
        aliases=["/now"]
    )
    
    # /about - 关于信息
    def show_about(args):
        about_text = """[bold]GenericAgent REPL[/bold]
Version: 0.1.0
License: MIT

A minimalist self-evolving autonomous agent framework.
Type /help for available commands.
"""
        repl.console.print(about_text)
    
    repl.register_command(
        "/about",
        show_about,
        "显示关于信息"
    )
