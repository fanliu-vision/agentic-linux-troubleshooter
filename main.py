import sys
from pathlib import Path

from agents.troubleshooting_agent import build_troubleshooting_agent
from routers import classify_issue_dict, format_route_context


def build_task(user_question: str, system_prompt: str, route_context: str) -> str:
    return f"""
{system_prompt}

下面是系统在进入 Agent 前完成的内容感知路由结果。你必须优先参考该路由结果选择工具。

{route_context}

路由使用要求：
1. 你必须优先调用 [PRIMARY_TOOLS] 中的工具。
2. [OPTIONAL_TOOLS] 不是默认必须调用的工具，只有以下情况才允许调用：
   - PRIMARY_TOOLS 返回的信息不足；
   - 用户明确要求检查当前运行环境；
   - 路由结果与工具证据冲突；
   - 需要验证某个当前系统状态。
3. 如果 all_detected_issue_types 中包含多个问题，说明这是混合故障，默认只调用 diagnose_mixed_log_file。
4. 对复杂日志，不要默认同时调用 diagnose_log_file、read_log_file、check_gpu_status、check_disk_usage、check_python_environment。
5. 如果 diagnose_mixed_log_file 已经返回 primary_issue_type、secondary_issue_types、TIMELINE 和 RECOMMENDED_NEXT_CHECKS，通常可以直接生成最终报告。
6. 如果日志环境明显是远程集群，而当前环境只是 WSL/本机环境，不要默认检查当前环境；如检查，必须说明“仅作参考，不能代表故障节点”。
7. 如果 primary_issue_type=gpu，重点分析 CUDA/HIP/DCU OOM、显存占用、batch size、混合精度、梯度检查点。
8. 如果 secondary_issue_types 中包含 disk，需要说明磁盘问题是主故障还是次要影响，并优先给出只读检查命令。
9. 如果 secondary_issue_types 中包含 python_env，需要检查解释器路径、pip 路径、依赖缺失和虚拟环境激活。
10. 如果 secondary_issue_types 中包含 network_port，需要检查端口占用和服务监听状态。
11. 如果 secondary_issue_types 中包含 slurm，需要区分 Slurm 调度等待、Slurm OOM kill、节点/分区问题。
12. 如果路由结果与工具证据冲突，以工具证据为准，并在最终报告中说明。
13. 不要调用明显无关工具，避免 token 浪费。
14. 最终回答必须明确区分：
    - 主故障
    - 次要问题
    - 问题发生时间线
    - 证据来源
    - 低风险修复建议
    - 需要人工确认的操作

现在用户的问题是：

{user_question}

请你按以下结构输出：

1. 问题类型
2. 路由判断结果
3. 你调用了哪些工具
4. 主故障判断
5. 次要问题列表
6. 关键时间线
7. 关键证据
8. 原因分析
9. 建议继续执行的检查命令
10. 修复建议
11. 风险提醒

安全要求：
- 不要执行删除文件、杀进程、取消作业、修改系统配置等危险操作。
- 不要在可复制代码块中输出 `rm -rf`、`kill -9`、`scancel`、`sudo rm`、`chmod -R`、`chown -R`、`mkfs`、`dd` 等危险命令。
- 如必须提到危险操作，只能放在“需要人工确认的操作”小节中，用自然语言描述，不要放在 bash 代码块中。
- 优先给出只读检查命令，例如 df、du、ss、lsof、squeue、scontrol show、which python、python -m pip。
- 修复建议必须拆成：
  1. 只读检查命令；
  2. 低风险调整；
  3. 需要人工确认的操作。
- 如果信息不足，要明确说明还缺什么信息。
- 最终回答必须包含“证据来源”：来自日志、来自当前系统命令、来自规则匹配或来自路由判断。

非常重要：你正在 CodeAgent 中运行，所有动作都必须写在 <code> 和 </code> 之间。
如果你已经获得足够证据并准备给出最终答案，必须使用下面格式，不要直接输出 Markdown：

Thought: 我已经获得足够证据，现在给出最终答案。
<code>
final_answer(\"\"\"
这里写最终排障报告，使用 Markdown 格式。
\"\"\")
</code>

不要在 <code> 标签之外直接输出最终报告。
不要把中文自然语言直接写进 <code> 中，除非它位于 final_answer 的三引号字符串内部。
"""


def main() -> None:
    agent = build_troubleshooting_agent()

    if len(sys.argv) > 1:
        user_question = " ".join(sys.argv[1:])
    else:
        print("Agentic Linux Troubleshooting Assistant")
        print("请输入你的排障问题，例如：")
        print("- 我的训练任务报 HIP out of memory，帮我分析原因")
        print("- ssh 登录时报 No space left on device，帮我定位")
        print("- 端口 9100 不通，帮我排查")
        print("- 帮我分析 examples/logs/oom_example.log")
        print()
        user_question = input("Question> ").strip()

    if not user_question:
        print("问题不能为空。")
        return

    system_prompt = getattr(agent, "system_prompt_for_project", "")

    route = classify_issue_dict(user_question)
    route_context = format_route_context(route)

    print("\n" + "=" * 80)
    print("路由判断结果")
    print("=" * 80)
    print(route_context)

    task = build_task(user_question, system_prompt, route_context)

    try:
        result = agent.run(task)
    except Exception as exc:
        result = (
            "## Agent 运行失败\n\n"
            f"错误类型：`{type(exc).__name__}`\n\n"
            f"错误信息：`{exc}`\n\n"
            "建议检查：\n"
            "1. 是否遵守 CodeAgent 输出格式；\n"
            "2. 工具调用是否正常；\n"
            "3. max_steps 是否过小或模型是否陷入格式循环；\n"
            "4. 可以先运行 `python tests/test_tools.py` 验证工具层。"
        )

    print("\n" + "=" * 80)
    print("排障结果")
    print("=" * 80)
    print(result)

    output_dir = Path("outputs") / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "last_report.md"
    report_path.write_text(str(result), encoding="utf-8")

    print("\n报告已保存到：", report_path)


if __name__ == "__main__":
    main()