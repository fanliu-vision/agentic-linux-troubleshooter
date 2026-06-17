import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from smolagents import CodeAgent, OpenAIModel

from tools import (
    run_shell_command,
    read_log_file,
    analyze_log_text,
    diagnose_log_file,
    diagnose_mixed_log_file,
    check_disk_usage,
    check_gpu_status,
    diagnose_slurm_text,
    diagnose_slurm_file,
    check_slurm_queue,
    check_slurm_job,
    check_slurm_nodes,
    check_python_environment,
    check_python_package,
    diagnose_python_error_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_settings() -> dict[str, Any]:
    config_path = PROJECT_ROOT / "configs" / "settings.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_system_prompt() -> str:
    prompt_path = PROJECT_ROOT / "prompts" / "system_prompt.txt"
    if not prompt_path.exists():
        return "你是一个 Linux / GPU / Slurm / 训练任务排障助手。请基于工具结果进行诊断。"

    return prompt_path.read_text(encoding="utf-8")


def build_model(settings: dict[str, Any]) -> OpenAIModel:
    load_dotenv()

    model_cfg = settings.get("model", {})
    model_id = model_cfg.get("model_id", "deepseek-chat")
    api_base = model_cfg.get("api_base", "https://api.deepseek.com")
    temperature = model_cfg.get("temperature", 0.2)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "未检测到 DEEPSEEK_API_KEY。请复制 .env.example 为 .env，并填写 DEEPSEEK_API_KEY。"
        )

    model = OpenAIModel(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
        temperature=temperature,
    )
    return model


def build_troubleshooting_agent() -> CodeAgent:
    settings = load_settings()
    system_prompt = load_system_prompt()
    model = build_model(settings)

    agent_cfg = settings.get("agent", {})
    max_steps = agent_cfg.get("max_steps", 8)
    verbosity_level = agent_cfg.get("verbosity_level", 2)

    tools = [
        # 复杂日志优先使用
        diagnose_mixed_log_file,

        # 路由后常用高层工具
        diagnose_log_file,

        # 日志相关底层工具
        read_log_file,
        analyze_log_text,

        # 第一阶段工具
        check_disk_usage,
        check_gpu_status,
        run_shell_command,

        # 第二阶段：Slurm 调度工具
        diagnose_slurm_file,
        diagnose_slurm_text,
        check_slurm_queue,
        check_slurm_job,
        check_slurm_nodes,

        # 第二阶段：Python 环境工具
        check_python_environment,
        check_python_package,
        diagnose_python_error_text,
    ]

    agent = CodeAgent(
        tools=tools,
        model=model,
        max_steps=max_steps,
        verbosity_level=verbosity_level,
        additional_authorized_imports=[
            "os",
            "re",
            "json",
            "math",
            "statistics",
            "pathlib",
        ],
    )

    # 不同 smolagents 版本对 system_prompt 的注入方式可能略有差异。
    # 为了兼容，这里不强改内部属性，而是在 main.py 中把 system_prompt 拼接进用户任务。
    agent.system_prompt_for_project = system_prompt

    return agent