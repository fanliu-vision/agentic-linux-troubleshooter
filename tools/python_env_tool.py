import importlib.util
import os
import re
import shlex
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import List

from smolagents import tool


COMMON_IMPORT_TO_PACKAGE = {
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "torch": "torch",
    "numpy": "numpy",
    "pandas": "pandas",
    "smolagents": "smolagents",
    "openai": "openai",
}


def _run_command(command: List[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 127, "", "COMMAND_NOT_FOUND"
    except subprocess.TimeoutExpired:
        return 124, "", "COMMAND_TIMEOUT"
    except Exception as exc:
        return 1, "", f"COMMAND_ERROR: {type(exc).__name__}: {exc}"


def _format_command(command: List[str], return_code: int, stdout: str, stderr: str, max_chars: int = 8000) -> str:
    if len(stdout) > max_chars:
        stdout = stdout[:max_chars] + "\n[STDOUT_TRUNCATED]"
    if len(stderr) > max_chars:
        stderr = stderr[:max_chars] + "\n[STDERR_TRUNCATED]"

    return (
        f"$ {' '.join(shlex.quote(x) for x in command)}\n"
        f"return_code: {return_code}\n"
        f"stdout:\n{stdout if stdout else '<empty>'}\n"
        f"stderr:\n{stderr if stderr else '<empty>'}"
    )


def _extract_missing_module(text: str) -> str:
    patterns = [
        r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]",
        r"No module named ['\"]([^'\"]+)['\"]",
        r"ImportError:\s*No module named ['\"]([^'\"]+)['\"]",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    return ""


@tool
def check_python_environment() -> str:
    """
    Check current Python interpreter, pip path, virtual environment and important package versions.

    Args:
        None

    Returns:
        A structured Python environment report.
    """
    sections = ["[PYTHON_ENVIRONMENT]"]

    sections.append("\n[BASIC_INFO]")
    sections.append(f"sys.executable: {sys.executable}")
    sections.append(f"sys.version: {sys.version.replace(chr(10), ' ')}")
    sections.append(f"sys.prefix: {sys.prefix}")
    sections.append(f"sys.base_prefix: {sys.base_prefix}")
    sections.append(f"cwd: {Path.cwd()}")

    in_venv = sys.prefix != sys.base_prefix
    conda_prefix = os.environ.get("CONDA_PREFIX")
    virtual_env = os.environ.get("VIRTUAL_ENV")

    sections.append("\n[VIRTUAL_ENV_INFO]")
    sections.append(f"in_venv: {in_venv}")
    sections.append(f"VIRTUAL_ENV: {virtual_env if virtual_env else '<not set>'}")
    sections.append(f"CONDA_PREFIX: {conda_prefix if conda_prefix else '<not set>'}")

    pip_cmd = [sys.executable, "-m", "pip", "--version"]
    return_code, stdout, stderr = _run_command(pip_cmd, timeout=15)
    sections.append("\n[PIP_INFO]")
    sections.append(_format_command(pip_cmd, return_code, stdout, stderr))

    important_packages = [
        "smolagents",
        "openai",
        "python-dotenv",
        "PyYAML",
        "torch",
        "numpy",
        "pandas",
    ]

    sections.append("\n[IMPORTANT_PACKAGES]")
    for pkg in important_packages:
        try:
            version = metadata.version(pkg)
            sections.append(f"{pkg}: {version}")
        except metadata.PackageNotFoundError:
            sections.append(f"{pkg}: <not installed>")

    sections.append("\n[DIAGNOSIS_HINT]")
    sections.append(
        "如果出现 ModuleNotFoundError，优先确认当前 sys.executable 是否是你运行项目时使用的解释器；"
        "安装依赖时建议使用 `python -m pip install 包名`，不要直接使用不确定来源的 pip。"
    )

    return "\n".join(sections)


@tool
def check_python_package(package_or_module: str) -> str:
    """
    Check whether a Python module/package can be imported in current environment.

    Args:
        package_or_module: Python import name or pip package name, such as yaml, dotenv, torch, smolagents.

    Returns:
        Import availability, installed package version if found, and installation hint.
    """
    name = package_or_module.strip()
    if not name:
        return "[PYTHON_PACKAGE_CHECK]\nstatus: empty_name\nmessage: 包名或模块名不能为空。"

    pip_package = COMMON_IMPORT_TO_PACKAGE.get(name, name)

    spec = importlib.util.find_spec(name)
    importable = spec is not None

    sections = ["[PYTHON_PACKAGE_CHECK]"]
    sections.append(f"query_name: {name}")
    sections.append(f"mapped_pip_package: {pip_package}")
    sections.append(f"importable: {importable}")

    if importable and spec:
        sections.append(f"module_origin: {spec.origin}")

    try:
        version = metadata.version(pip_package)
        sections.append(f"installed_version: {version}")
    except metadata.PackageNotFoundError:
        sections.append("installed_version: <not found by importlib.metadata>")

    pip_show_cmd = [sys.executable, "-m", "pip", "show", pip_package]
    return_code, stdout, stderr = _run_command(pip_show_cmd, timeout=15)

    sections.append("\n[PIP_SHOW]")
    sections.append(_format_command(pip_show_cmd, return_code, stdout, stderr))

    sections.append("\n[INSTALL_HINT]")
    if importable:
        sections.append("当前环境可以 import 该模块。如果运行脚本仍报错，可能是脚本使用了另一个 Python 解释器。")
    else:
        sections.append(f"当前环境无法 import `{name}`。建议在当前解释器中执行：")
        sections.append(f"{sys.executable} -m pip install {pip_package}")

    return "\n".join(sections)


@tool
def diagnose_python_error_text(error_text: str) -> str:
    """
    Diagnose pasted Python error text, especially ModuleNotFoundError, ImportError,
    interpreter mismatch and dependency installation issues.

    Args:
        error_text: Raw Python traceback or error text.

    Returns:
        A structured diagnosis and suggested commands.
    """
    text = error_text.strip()
    if not text:
        return "[PYTHON_ERROR_DIAGNOSIS]\nstatus: empty_text\nmessage: 未提供 Python 错误文本。"

    lower = text.lower()
    signals = []

    if "modulenotfounderror" in lower or "no module named" in lower:
        signals.append("module_not_found")
    if "importerror" in lower:
        signals.append("import_error")
    if "version" in lower and ("conflict" in lower or "mismatch" in lower):
        signals.append("version_conflict")
    if "cuda" in lower and "torch" in lower:
        signals.append("pytorch_cuda_related")
    if ".venv" in lower or "site-packages" in lower:
        signals.append("virtualenv_related")

    missing_module = _extract_missing_module(text)
    pip_package = COMMON_IMPORT_TO_PACKAGE.get(missing_module, missing_module) if missing_module else ""

    sections = ["[PYTHON_ERROR_DIAGNOSIS]"]
    sections.append("status: ok")
    sections.append(f"detected_signals: {signals if signals else []}")
    sections.append(f"missing_module: {missing_module if missing_module else '<not detected>'}")
    sections.append(f"recommended_pip_package: {pip_package if pip_package else '<unknown>'}")

    sections.append("\n[DIAGNOSIS]")
    if missing_module:
        sections.append(
            f"检测到缺失模块 `{missing_module}`。这通常不是代码逻辑错误，而是当前 Python 环境没有安装对应依赖，"
            "或者安装到了另一个解释器环境中。"
        )
    elif "importerror" in lower:
        sections.append("检测到 ImportError，可能是包版本不兼容、循环导入、二进制依赖缺失或解释器环境不一致。")
    else:
        sections.append("未识别到明确 Python 依赖错误，需要结合完整 Traceback 和当前解释器信息继续判断。")

    sections.append("\n[SUGGESTED_NEXT_CHECKS]")
    sections.append("- which python")
    sections.append("- python -m pip --version")
    sections.append("- python -c \"import sys; print(sys.executable)\"")
    if missing_module:
        sections.append(f"- python -m pip show {pip_package}")
        sections.append(f"- python -m pip install {pip_package}")

    sections.append("\n[SAFETY_NOTE]")
    sections.append("不要盲目在系统 Python 中 sudo pip install；优先确认并使用项目虚拟环境中的 python -m pip。")

    return "\n".join(sections)