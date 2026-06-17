import os
import shlex
import subprocess
from pathlib import Path

from smolagents import tool


def _run_readonly_command(command: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        return (
            f"$ {' '.join(shlex.quote(x) for x in command)}\n"
            f"return_code: {result.returncode}\n"
            f"stdout:\n{stdout if stdout else '<empty>'}\n"
            f"stderr:\n{stderr if stderr else '<empty>'}"
        )
    except FileNotFoundError:
        return f"$ {' '.join(command)}\nCOMMAND_NOT_FOUND"
    except subprocess.TimeoutExpired:
        return f"$ {' '.join(command)}\nCOMMAND_TIMEOUT"
    except Exception as exc:
        return f"$ {' '.join(command)}\nCOMMAND_ERROR: {type(exc).__name__}: {exc}"


@tool
def check_disk_usage(target_path: str = "~") -> str:
    """
    Check disk usage for troubleshooting 'No space left on device' or quota issues.

    Args:
        target_path: Directory to inspect, usually '~', current project path, cache path, or dataset path.

    Returns:
        A structured disk diagnosis report, including filesystem usage and common large cache directories.
    """
    path = Path(target_path).expanduser().resolve()
    home = Path.home()

    sections = []

    sections.append("[DISK_CHECK_TARGET]")
    sections.append(f"target_path: {path}")
    sections.append(f"exists: {path.exists()}")
    sections.append(f"is_dir: {path.is_dir()}")

    sections.append("\n[FILESYSTEM_USAGE]")
    sections.append(_run_readonly_command(["df", "-h", str(path if path.exists() else home)]))

    sections.append("\n[INODE_USAGE]")
    sections.append(_run_readonly_command(["df", "-ih", str(path if path.exists() else home)]))

    if path.exists() and path.is_dir():
        sections.append("\n[TARGET_TOP_LEVEL_USAGE]")
        sections.append(_run_readonly_command(["du", "-sh", str(path)]))

        try:
            children = [p for p in path.iterdir()]
            children = children[:30]
            if children:
                sections.append("\n[TARGET_CHILDREN_USAGE]")
                for child in children:
                    sections.append(_run_readonly_command(["du", "-sh", str(child)], timeout=8))
        except PermissionError:
            sections.append("\n[TARGET_CHILDREN_USAGE]\nPermission denied when listing target directory.")
        except Exception as exc:
            sections.append(f"\n[TARGET_CHILDREN_USAGE]\nERROR: {type(exc).__name__}: {exc}")

    common_cache_dirs = [
        home / ".cache",
        home / ".cache" / "pip",
        home / ".cache" / "huggingface",
        home / ".cache" / "torch",
        home / ".conda",
        home / "miniconda3" / "pkgs",
        home / "anaconda3" / "pkgs",
        home / ".local",
    ]

    sections.append("\n[COMMON_CACHE_DIRS_USAGE]")
    for cache_dir in common_cache_dirs:
        if cache_dir.exists():
            sections.append(_run_readonly_command(["du", "-sh", str(cache_dir)], timeout=10))

    sections.append("\n[ENV_INFO]")
    sections.append(f"USER: {os.environ.get('USER') or os.environ.get('USERNAME')}")
    sections.append(f"HOME: {home}")

    sections.append("\n[DIAGNOSIS_HINT]")
    sections.append(
        "如果 df -h 显示 Use% 接近 100%，说明文件系统空间不足；"
        "如果 df -ih 显示 IUse% 接近 100%，说明 inode 用尽，可能是小文件过多；"
        "如果公共文件系统没满但用户仍报错，可能是用户 quota 或个人缓存目录过大。"
    )

    return "\n".join(sections)