from __future__ import annotations

from typing import Dict, Type

from agents.domain_agents import (
    DiskDiagnosisAgent,
    GPUDiagnosisAgent,
    NetworkDiagnosisAgent,
    PythonEnvDiagnosisAgent,
    SlurmDiagnosisAgent,
)


DOMAIN_AGENT_REGISTRY: Dict[str, Type] = {
    "gpu": GPUDiagnosisAgent,
    "disk": DiskDiagnosisAgent,
    "python_env": PythonEnvDiagnosisAgent,
    "network_port": NetworkDiagnosisAgent,
    "slurm": SlurmDiagnosisAgent,
}


def get_domain_agent_class(issue_type: str):
    return DOMAIN_AGENT_REGISTRY.get(issue_type)


def available_domain_agents() -> list[str]:
    return sorted(DOMAIN_AGENT_REGISTRY.keys())