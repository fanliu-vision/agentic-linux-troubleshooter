from __future__ import annotations

import argparse

from sessions import TroubleshootingSession


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy interactive Stage-4 Linux Troubleshooting CLI "
            "(not the monitor/daemon entry)."
        )
    )
    parser.add_argument(
        "--agent-depth",
        choices=["minimal", "balanced", "full"],
        default="balanced",
        help="V3 agent execution depth.",
    )
    parser.add_argument(
        "--report-mode",
        choices=["rule", "llm", "auto"],
        default="auto",
        help="Report generation mode.",
    )
    parser.add_argument(
        "--project-dir",
        default="",
        help="Project directory used by /rerun.",
    )
    parser.add_argument(
        "--run-command",
        default="",
        help="Command used by /rerun.",
    )
    parser.add_argument(
        "--rerun-timeout",
        type=int,
        default=120,
        help="Timeout seconds for /rerun.",
    )
    return parser.parse_args()


HELP_TEXT = """
可用命令：

/help
  查看帮助。

/log <日志路径>
  从日志文件开始排障。
  示例：/log examples/logs/regression/08_complex_mixed_failure.log

/paste
  粘贴一段错误日志、Traceback 或终端报错。输入 END 结束。

/add
  添加一段新的证据，例如某个命令的输出。输入 END 结束。

/next
  根据当前证据生成下一步只读检查命令。

/run <只读命令>
  执行一条 allowlist 内的只读命令，并自动加入证据。
  示例：/run df -h /tmp
  示例：/run python -m pip --version

/evidence
  查看当前已收集证据。

/report
  基于当前证据生成最终报告。
  
/project <项目目录>
  设置项目目录，用于 /rerun。

/command <运行命令>
  设置项目运行命令，用于 /rerun。
  示例：/command python train.py --config configs/sr_x4.yaml

/project-status
  查看当前项目目录和运行命令。
  
/context
  只读扫描当前 project_dir，识别项目结构、配置文件、依赖文件、日志文件和可修复字段。
  扫描结果会加入证据，后续 /fix 会基于项目上下文生成更准确的修复计划。

/fix
  基于当前诊断结果生成修复计划。
  
/apply <fix_id>
  对 apply_supported=True 的修复项执行可控配置修改。
  会自动备份配置文件、写入修改并生成 diff。
  示例：/apply fix-network-1

/diff
  查看最近一次 /apply 或 /rollback 生成的配置差异。

/rollback
  回滚最近一次 /apply 的配置修改。

/rerun
  重新运行项目命令。
  如果成功，Agent 判断当前复现命令已不再报错。
  如果失败，Agent 会自动把新错误加入证据并继续排查。
  
/remote-set <user>@<host> [port]
  设置远程 SSH Profile。
  示例：/remote-set lf@10.16.1.9 22
  示例：/remote-set lf@server.example.com

/remote-status
  查看当前远程 SSH Profile。

/remote-run <只读命令>
  在远程服务器执行 allowlist 内的只读命令。
  示例：/remote-run df -h
  示例：/remote-run squeue -u lf
  示例：/remote-run ss -lntp | grep 9100

/remote-log <远程日志路径> [行数]
  读取远程日志尾部，并加入当前证据。
  示例：/remote-log /home/lf/project/train.log 400

/remote-context <远程项目目录>
  只读扫描远程项目目录中的配置、依赖和日志候选文件。
  示例：/remote-context /home/lf/projects/order-service

/remote-rerun <远程项目目录>
  在远程服务器指定目录下执行当前 run_command。
  示例：/remote-rerun /home/lf/projects/order-service
  注意：该命令会运行项目脚本，可能占用远程资源，因此需要用户确认。
  
/remote-apply <fix_id> <远程项目目录>
  在远程服务器上执行受控配置修改。
  示例：/remote-apply fix-network-1 /home/lf/projects/order-service

/remote-diff
  查看最近一次远程 apply 生成的 diff。

/remote-rollback
  回滚最近一次远程 apply。

/remote-recover <fix_id> <远程项目目录>
  自动执行远程修复闭环：
  remote-rerun → 如果失败则 remote-apply → 再 remote-rerun。
  
/exit、exit、quit、q
  退出交互式排障模式。
"""


def read_multiline_until_end() -> str:
    print("请输入内容，输入单独一行 END 结束：")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)

def parse_remote_set_args(text: str) -> tuple[str, str, int] | None:
    """
    Parse:
      /remote-set user@host
      /remote-set user@host 22
    """
    parts = text.strip().split()

    if not parts:
        return None

    target = parts[0]
    port = 22

    if len(parts) >= 2:
        try:
            port = int(parts[1])
        except ValueError:
            return None

    if "@" not in target:
        return None

    user, host = target.split("@", 1)

    if not user or not host:
        return None

    return user, host, port


def main() -> None:
    args = parse_args()

    session = TroubleshootingSession(
        agent_depth=args.agent_depth,
        report_mode=args.report_mode,
        project_dir=args.project_dir,
        run_command=args.run_command,
        rerun_timeout=args.rerun_timeout,
    )

    print("=" * 100)
    print("Legacy interactive entry: Stage-4 Interactive Linux Troubleshooting Agent")
    print("Historical CLI only; use main_monitor.py for monitor/daemon workflows.")
    print("=" * 100)
    print(f"session_id: {session.session_id}")
    print(f"agent_depth: {args.agent_depth}")
    print(f"report_mode: {args.report_mode}")
    print("")
    print("建议从 `/log <日志路径>` 或 `/paste` 开始。输入 `/help` 查看命令。")

    while True:
        try:
            user_input = input("\n[interactive-agent] > ").strip()
        except KeyboardInterrupt:
            print("\n已退出。")
            break

        if not user_input:
            continue

        if user_input in {"/exit", "exit", "/quit", "quit", "q"}:
            print("已退出。")
            break

        if user_input == "/help":
            print(HELP_TEXT)
            continue

        if user_input.startswith("/log "):
            log_path = user_input[len("/log "):].strip()
            print(session.start_from_log_file(log_path))
            continue

        if user_input == "/paste":
            pasted = read_multiline_until_end()

            if session.evidence_items:
                print(
                    session.add_evidence(
                        pasted,
                        source="user_paste",
                        title="Additional pasted runtime evidence",
                    )
                )
                print("已作为补充证据加入当前会话。输入 `/next` 可继续生成下一步检查计划。")
            else:
                print(session.start_from_paste(pasted))

            continue

        if user_input == "/add":
            content = read_multiline_until_end()
            print(session.add_evidence(content, source="user_paste", title="Additional pasted evidence"))
            continue

        if user_input == "/next":
            plan = session.suggest_next_actions()
            print(plan.to_markdown())
            print("")
            print("说明：你可以手动执行这些命令后用 `/add` 粘贴结果；")
            print("也可以使用 `/run <命令>` 让 Agent 执行 allowlist 内的只读命令。")
            continue

        if user_input.startswith("/run "):
            command = user_input[len("/run "):].strip()
            print("即将进行只读命令安全检查。")
            allowed, reason = session.executor.is_allowed(command)
            print(f"allowed: {allowed}")
            print(f"reason: {reason}")

            if not allowed:
                print("命令未通过安全检查，已拒绝执行。")
                continue

            confirm = input("是否确认执行该只读命令？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消执行。")
                continue

            result_text = session.run_readonly_command(command)
            print(result_text)
            continue

        if user_input == "/evidence":
            print(session.evidence_summary())
            continue

        if user_input == "/report":
            report, save_path, source = session.generate_report()
            print("=" * 100)
            print(f"最终报告生成方式：{source}")
            print(f"报告已保存到：{save_path}")
            print("=" * 100)
            print(report)
            continue

        if user_input.startswith("/project "):
            project_dir = user_input[len("/project "):].strip()
            print(session.set_project_dir(project_dir))
            continue

        if user_input.startswith("/command "):
            run_command = user_input[len("/command "):].strip()
            print(session.set_run_command(run_command))
            continue

        if user_input == "/project-status":
            print(session.project_status())
            continue

        if user_input.startswith("/remote-set "):
            payload = user_input[len("/remote-set "):].strip()
            parsed = parse_remote_set_args(payload)

            if not parsed:
                print("格式错误。示例：/remote-set lf@10.16.1.9 22")
                continue

            user, host, port = parsed
            print(session.set_remote_profile(user=user, host=host, port=port))
            continue

        if user_input == "/remote-status":
            print(session.remote_status())
            continue

        if user_input.startswith("/remote-run "):
            command = user_input[len("/remote-run "):].strip()

            if not command:
                print("远程命令不能为空。")
                continue


            print("即将进行远程只读命令安全检查。")
            allowed, reason = session.remote_executor.is_allowed(command)
            print(f"allowed: {allowed}")
            print(f"reason: {reason}")

            if not allowed:
                print("命令未通过远程只读安全检查，已拒绝执行。")
                continue

            confirm = input("是否确认在远程服务器执行该只读命令？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消远程命令执行。")
                continue

            print(session.run_remote_readonly_command(command))
            continue

        if user_input.startswith("/remote-log "):
            payload = user_input[len("/remote-log "):].strip()
            parts = payload.split()

            if not parts:
                print("格式错误。示例：/remote-log /home/lf/project/train.log 400")
                continue

            remote_path = parts[0]
            lines = 400

            if len(parts) >= 2:
                try:
                    lines = int(parts[1])
                except ValueError:
                    print("行数必须是整数。")
                    continue

            confirm = input(f"是否读取远程日志尾部 {remote_path}？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消远程日志读取。")
                continue

            print(session.read_remote_log(remote_path, lines=lines))
            continue

        if user_input.startswith("/remote-context "):
            remote_project_dir = user_input[len("/remote-context "):].strip()

            if not remote_project_dir:
                print("远程项目目录不能为空。")
                continue

            confirm = input(f"是否扫描远程项目目录 {remote_project_dir}？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消远程项目上下文扫描。")
                continue

            print(session.collect_remote_context(remote_project_dir))
            continue

        if user_input.startswith("/remote-rerun"):
            payload = user_input[len("/remote-rerun"):].strip()

            if not payload:
                print("远程项目目录不能为空。示例：/remote-rerun /home/lf/projects/order-service")
                continue

            print("即将在远程服务器执行项目 rerun。")
            print(session.remote_status())
            print(session.project_status())
            print(f"- remote_project_dir: `{payload}`")
            print("")
            print("说明：该操作会在远程服务器上运行项目命令，可能占用 CPU/GPU/磁盘资源。")
            print("当前版本不会执行远程 rm、kill、sudo、scancel，也不会修改远程配置。")

            confirm = input("是否确认执行远程 /remote-rerun？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消远程 rerun。")
                continue

            print(session.rerun_remote_project(payload))
            continue

        if user_input.startswith("/remote-apply "):
            payload = user_input[len("/remote-apply "):].strip()
            parts = payload.split(maxsplit=1)

            if len(parts) != 2:
                print("格式错误。示例：/remote-apply fix-network-1 /home/lf/projects/order-service")
                continue

            fix_id, remote_project_dir = parts

            print("即将在远程服务器执行受控配置修改。")
            print(session.remote_status())
            print(f"- fix_id: `{fix_id}`")
            print(f"- remote_project_dir: `{remote_project_dir}`")
            print("")
            print("该操作会在远程项目目录内修改已注册支持的 JSON 配置字段。")
            print("修改前会自动生成远程备份和 diff。")
            print("不会执行 sudo、rm、kill、scancel、chmod、chown。")

            confirm = input("是否确认执行远程 /remote-apply？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消远程 apply。")
                continue

            print(session.remote_apply_fix(fix_id, remote_project_dir))
            continue

        if user_input == "/remote-diff":
            print(session.show_latest_remote_diff())
            continue

        if user_input == "/remote-rollback":
            print("即将回滚最近一次远程 /remote-apply。")
            confirm = input("是否确认执行远程 /remote-rollback？输入 yes 确认：").strip().lower()

            if confirm != "yes":
                print("已取消远程 rollback。")
                continue

            print(session.remote_rollback_latest_apply())
            continue

        if user_input.startswith("/remote-recover "):
            payload = user_input[len("/remote-recover "):].strip()
            parts = payload.split(maxsplit=1)

            if len(parts) != 2:
                print("格式错误。示例：/remote-recover fix-network-1 /home/lf/projects/order-service")
                continue

            fix_id, remote_project_dir = parts

            print("即将执行远程自动修复闭环。")
            print("流程：remote-rerun → 如果失败则 remote-apply → 再 remote-rerun。")
            print(session.remote_status())
            print(f"- fix_id: `{fix_id}`")
            print(f"- remote_project_dir: `{remote_project_dir}`")
            print("")
            print("该操作会运行远程项目命令，并可能修改远程项目配置文件。")
            print("修改前会自动备份并生成 diff。")

            confirm = input("是否确认执行远程 /remote-recover？输入 yes 确认：").strip().lower()

            if confirm != "yes":
                print("已取消远程 recover。")
                continue

            print(session.remote_recover_with_fix(fix_id, remote_project_dir))
            continue

        if user_input == "/context":
            print(session.collect_project_context())
            continue

        if user_input == "/fix":
            print(session.generate_fix_plan())
            continue

        if user_input.startswith("/apply "):
            fix_id = user_input[len("/apply "):].strip()

            if not fix_id:
                print("fix_id 不能为空。例如：/apply fix-network-1")
                continue

            print("即将执行受控配置修改。")
            print("该操作会：")
            print("1. 备份配置文件；")
            print("2. 修改 allowlist 中支持的配置字段；")
            print("3. 生成 diff；")
            print("4. 记录 apply 历史，支持 /rollback。")
            print("")
            confirm = input(f"是否确认执行 /apply {fix_id}？输入 yes 确认：").strip().lower()

            if confirm != "yes":
                print("已取消 apply。")
                continue

            print(session.apply_fix(fix_id))
            continue

        if user_input == "/diff":
            print(session.show_latest_diff())
            continue

        if user_input == "/rollback":
            print("即将回滚最近一次 /apply。")
            confirm = input("是否确认执行 /rollback？输入 yes 确认：").strip().lower()

            if confirm != "yes":
                print("已取消 rollback。")
                continue

            print(session.rollback_latest_apply())
            continue

        if user_input == "/rerun":
            print("即将重新运行项目命令。")
            print(session.project_status())
            confirm = input("是否确认执行 /rerun？输入 yes 确认：").strip().lower()
            if confirm != "yes":
                print("已取消重新运行。")
                continue

            print(session.rerun_project())
            continue

        print("未知命令。输入 `/help` 查看可用命令。")


if __name__ == "__main__":
    main()
