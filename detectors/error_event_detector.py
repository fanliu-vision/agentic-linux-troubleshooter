from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Iterable


@dataclass(frozen=True)
class ErrorRule:
    event_type: str
    issue_type: str
    severity: str
    summary: str
    patterns: list[str]


@dataclass
class ErrorEvent:
    """
    Stage 6B structured error event.

    event_type:
        更细的监控事件类型，例如 gpu_oom、disk_full、network_port。
    issue_type:
        对齐已有 routers/issue_router.py 的领域类型，例如 gpu、disk、network_port。
    fingerprint:
        用于去重的稳定指纹，不依赖完整日志上下文。
    """

    event_type: str
    issue_type: str
    severity: str
    summary: str
    source: str

    matched_keywords: list[str] = field(default_factory=list)
    raw_excerpt: str = ""
    signature: str = ""
    line_number: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 内部字段，用于 suppress traceback 这类泛化事件
    span_start: int = field(default=0, repr=False)
    span_end: int = field(default=0, repr=False)

    @property
    def fingerprint(self) -> str:
        base = f"{self.event_type}|{self.issue_type}|{self.signature}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    def to_evidence_text(self) -> str:
        return (
            "[ERROR_EVENT]\n"
            f"event_type: {self.event_type}\n"
            f"issue_type: {self.issue_type}\n"
            f"severity: {self.severity}\n"
            f"summary: {self.summary}\n"
            f"source: {self.source}\n"
            f"created_at: {self.created_at}\n"
            f"line_number: {self.line_number}\n"
            f"fingerprint: {self.fingerprint}\n"
            f"signature: {self.signature}\n"
            f"matched_keywords: {self.matched_keywords}\n\n"
            "[RAW_EXCERPT]\n"
            f"{self.raw_excerpt}"
        )


class ErrorEventDetector:
    """
    Stage 6B: 错误事件检测与去重。

    目标：
    1. 从日志增量文本中识别错误事件；
    2. 优先识别具体错误，例如 disk_full、gpu_oom、network_port；
    3. 如果同一段日志已经识别出具体错误，则抑制泛化 traceback 事件；
    4. 生成稳定 fingerprint，供 MonitorLoop 避免重复报警。
    """

    RULES: ClassVar[list[ErrorRule]] = [
        ErrorRule(
            event_type="gpu_oom",
            issue_type="gpu",
            severity="high",
            summary="GPU/DCU 显存不足或 OOM 错误",
            patterns=[
                r"cuda\s+out\s+of\s+memory",
                r"hip\s+out\s+of\s+memory",
                r"torch\.outofmemoryerror",
                r"\boutofmemoryerror\b",
                r"\boom-kill\b",
                r"\boom kill\b",
                r"accelerator memory constraints",
            ],
        ),
        ErrorRule(
            event_type="disk_full",
            issue_type="disk",
            severity="high",
            summary="磁盘空间不足、inode 不足或缓存写入失败",
            patterns=[
                r"no space left on device",
                r"\berrno\s*28\b",
                r"disk quota exceeded",
                r"no usable temporary directory",
                r"no space left",
                r"inode.*(?:full|exhausted|no space)",
            ],
        ),
        ErrorRule(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="端口占用或服务绑定失败",
            patterns=[
                r"address already in use",
                r"\berrno\s*98\b",
                r"bind(?:ing)? failed",
                r"port\s+\d+\s+already\s+in\s+use",
                r"port already in use",
                r"cannot assign requested address",
            ],
        ),
        ErrorRule(
            event_type="python_env",
            issue_type="python_env",
            severity="medium",
            summary="Python 依赖缺失、解释器不一致或环境异常",
            patterns=[
                r"\bmodulenotfounderror\b",
                r"no module named",
                r"\bimporterror\b",
                r"python interpreter and pip path do not belong",
                r"pip path.*python interpreter",
                r"pkg_resources\.distributionnotfound",
            ],
        ),
        ErrorRule(
            event_type="slurm",
            issue_type="slurm",
            severity="high",
            summary="Slurm 作业异常、节点异常或调度资源问题",
            patterns=[
                r"\bslurmstepd\b",
                r"\bjobstate=pending\b",
                r"\breason=resources\b",
                r"node.*(?:down|drain|drained|not responding)",
                r"cancelled at",
                r"exceeded memory",
                r"batch job.*failed",
            ],
        ),
        ErrorRule(
            event_type="process_crash",
            issue_type="process",
            severity="high",
            summary="进程崩溃、core dump、段错误或非零退出",
            patterns=[
                r"\bsystemd\b.*\bfailed\b",
                r"\bcore dumped\b",
                r"\bsegmentation fault\b",
                r"\bexited with code\b",
                r"\bmain process exited\b.*\bstatus=11\b",
                r"\bsignal\s*11\b",
                r"\bsigsegv\b",
            ],
        ),
        ErrorRule(
            event_type="container_k8s",
            issue_type="container_k8s",
            severity="high",
            summary="容器或 Kubernetes Pod 异常、镜像拉取失败或调度失败",
            patterns=[
                r"\bcrashloopbackoff\b",
                r"\bimagepullbackoff\b",
                r"\berrimagepull\b",
                r"\boomkilled\b",
                r"\bcreatecontainerconfigerror\b",
                r"\bback-off restarting failed container\b",
                r"\bpod failed scheduling\b",
            ],
        ),
        ErrorRule(
            event_type="host_resource",
            issue_type="host_resource",
            severity="high",
            summary="主机资源不足、内存分配失败、文件句柄耗尽或负载过高",
            patterns=[
                r"\bout of memory:\s+(?:kill|killed) process\b",
                r"\bcannot allocate memory\b",
                r"\btoo many open files\b",
                r"\bload average\b.*\btoo high\b",
                r"\bsystem load\b.*\btoo high\b",
            ],
        ),
        ErrorRule(
            event_type="network_connectivity",
            issue_type="network_connectivity",
            severity="medium",
            summary="DNS、连接超时、连接拒绝或 TLS 握手超时",
            patterns=[
                r"\btemporary failure in name resolution\b",
                r"\bname or service not known\b",
                r"\bdns (?:resolution )?failed\b",
                r"\bconnection timed out\b",
                r"\bconnection refused\b",
                r"\btls handshake timeout\b",
            ],
        ),
        ErrorRule(
            event_type="dependency_service",
            issue_type="dependency_service",
            severity="high",
            summary="数据库、缓存、消息队列或外部依赖服务异常",
            patterns=[
                r"\b(?:mysql|postgresql|postgres)\b.*\bconnection failed\b",
                r"\bredis\b.*\bconnection refused\b",
                r"\bkafka\b.*\bbroker unavailable\b",
                r"\brabbitmq\b.*\bconnection timeout\b",
                r"\bmq\b.*\bconnection timeout\b",
                r"\bdatabase connection pool exhausted\b",
            ],
        ),
        ErrorRule(
            event_type="process_kill",
            issue_type="process",
            severity="high",
            summary="进程被系统、调度器或外部信号终止",
            patterns=[
                r"\bsigkill\b",
                r"\bsignal\s*=\s*sigkill\b",
                r"\bexit[_ -]?status\s*=\s*137\b",
                r"\bexit(?:ed)?\s+with\s+(?:code|status)\s+137\b",
                r"\bkilled\s+process\b",
                r"\bprocess.*(?:killed|terminated)\b",
            ],
        ),
        ErrorRule(
            event_type="config_error",
            issue_type="config",
            severity="high",
            summary="配置文件缺失、格式错误或配置值无效",
            patterns=[
                r"\bmissing required config key\b",
                r"\binvalid (?:yaml|json|toml)\b",
                r"\binvalid config value\b",
                r"\binvalid path\b",
                r"\binvalid port\b",
                r"\bconfig file not found\b",
            ],
        ),
        ErrorRule(
            event_type="auth_cert",
            issue_type="auth_cert",
            severity="high",
            summary="认证授权失败、token 异常或证书/TLS 校验失败",
            patterns=[
                r"\bhttp\s+(?:401|403)\b",
                r"\btoken expired\b",
                r"\binvalid token\b",
                r"\bcertificate expired\b",
                r"\bcertificate verify failed\b",
                r"\btls handshake certificate error\b",
            ],
        ),
        ErrorRule(
            event_type="permission_denied",
            issue_type="permission",
            severity="high",
            summary="权限不足、访问被拒绝或受保护资源不可写",
            patterns=[
                r"\bpermission denied\b",
                r"\beacces\b",
                r"\boperation not permitted\b",
                r"\baccess denied\b",
            ],
        ),
        # 泛化规则必须放最后，避免盖过具体错误。
        ErrorRule(
            event_type="traceback",
            issue_type="log",
            severity="medium",
            summary="通用运行时 Traceback、Exception、Fatal 或 Error",
            patterns=[
                r"traceback \(most recent call last\)",
                r"\bexception\b",
                r"\bfatal\b",
                r"\berror:\b",
                r"\bfailed\b",
            ],
        ),
    ]

    SEVERITY_RANK: ClassVar[dict[str, int]] = {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    def detect(self, text: str, source: str) -> list[ErrorEvent]:
        """
        从日志文本中识别错误事件。

        注意：
        - 这里不保存跨轮状态；
        - 跨 poll 的去重仍由 MonitorLoop.seen_fingerprints 完成；
        - 本函数只负责“同一段文本内部”的事件降噪与去重。
        """
        if not text or not text.strip():
            return []

        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        candidates: list[ErrorEvent] = []

        for rule in self.RULES:
            for pattern in rule.patterns:
                for match in re.finditer(pattern, normalized_text, flags=re.IGNORECASE | re.MULTILINE):
                    excerpt, span_start, span_end = self._extract_excerpt(normalized_text, match.start())
                    signature = self._build_signature(excerpt, match.group(0))
                    line_number = normalized_text[: match.start()].count("\n") + 1

                    candidates.append(
                        ErrorEvent(
                            event_type=rule.event_type,
                            issue_type=rule.issue_type,
                            severity=rule.severity,
                            summary=rule.summary,
                            source=source,
                            matched_keywords=[match.group(0)],
                            raw_excerpt=excerpt,
                            signature=signature,
                            line_number=line_number,
                            span_start=span_start,
                            span_end=span_end,
                        )
                    )

        return self._dedupe_and_suppress(candidates)

    def detect_all(self, text: str, source: str) -> list[ErrorEvent]:
        """
        Return one scoped candidate event per event_type for a log window.

        This is the R10 multi-event detector API. It is detection-only: it does
        not run recovery, send notifications, write reports, or mutate state.
        The existing detect() API remains the compatibility path for current
        callers.
        """
        if not text or not text.strip():
            return []

        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized_text.splitlines()
        events: list[ErrorEvent] = []

        for rule in self.RULES:
            matches = self._collect_rule_line_matches(rule, lines)
            if not matches:
                continue

            matched_line_numbers = [line_number for line_number, _ in matches]
            raw_excerpt = "\n".join(
                lines[line_number - 1]
                for line_number in matched_line_numbers
            ).strip()
            matched_keywords = self._unique_keywords(
                keyword for _, keyword in matches
            )
            first_keyword = matched_keywords[0] if matched_keywords else rule.event_type
            signature = self._build_signature(raw_excerpt, first_keyword)

            events.append(
                ErrorEvent(
                    event_type=rule.event_type,
                    issue_type=rule.issue_type,
                    severity=rule.severity,
                    summary=rule.summary,
                    source=source,
                    matched_keywords=matched_keywords,
                    raw_excerpt=raw_excerpt,
                    signature=signature,
                    line_number=matched_line_numbers[0],
                    span_start=0,
                    span_end=0,
                )
            )

        if any(event.issue_type != "log" for event in events):
            events = [event for event in events if event.issue_type != "log"]

        return events

    def _collect_rule_line_matches(self, rule: ErrorRule, lines: list[str]) -> list[tuple[int, str]]:
        matches: list[tuple[int, str]] = []
        seen_line_numbers: set[int] = set()

        for line_number, line in enumerate(lines, start=1):
            line_keywords: list[str] = []
            for pattern in rule.patterns:
                match = re.search(pattern, line, flags=re.IGNORECASE)
                if match:
                    line_keywords.append(match.group(0))

            if line_keywords and line_number not in seen_line_numbers:
                matches.extend((line_number, keyword) for keyword in line_keywords)
                seen_line_numbers.add(line_number)

        return matches

    def _unique_keywords(self, keywords: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for keyword in keywords:
            key = str(keyword).lower()
            if key in seen:
                continue
            result.append(str(keyword))
            seen.add(key)

        return result

    def _extract_excerpt(self, text: str, match_start: int, window: int = 1400) -> tuple[str, int, int]:
        """
        提取错误上下文。

        如果匹配点附近存在 Traceback，则尽量返回完整 Traceback 尾部；
        否则返回匹配点前后窗口。
        """
        traceback_start = text.rfind("Traceback (most recent call last):", 0, match_start + 1)

        if traceback_start >= 0 and match_start - traceback_start <= window:
            start = traceback_start
        else:
            start = max(0, match_start - window // 2)

        end = min(len(text), match_start + window // 2)

        # Traceback 通常到下一个空行、summary、或日志段落结束。
        next_markers = [
            text.find("\n\n", match_start),
            text.find("\n[summary]", match_start),
            text.find("\n[service]", match_start + 1),
            text.find("\nINFO", match_start + 1),
            text.find("\nWARNING", match_start + 1),
        ]
        next_markers = [idx for idx in next_markers if idx > 0]
        if next_markers:
            end = min(max(end, min(next_markers)), len(text))

        excerpt = text[start:end].strip()
        return excerpt, start, end

    def _build_signature(self, excerpt: str, matched_keyword: str) -> str:
        """
        构造稳定签名。

        优先选择最能代表失败原因的行，而不是整段 traceback。
        这样相同错误在不同时间重复出现时，会得到相同 fingerprint。
        """
        lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
        lower_keyword = matched_keyword.lower()

        # 优先返回包含匹配关键字的具体错误行。
        for line in lines:
            if lower_keyword in line.lower():
                return self._normalize_signature_line(line)

        # 其次返回常见异常行。
        for line in reversed(lines):
            if re.search(r"(error|exception|traceback|failed|fatal|oom|errno)", line, flags=re.IGNORECASE):
                return self._normalize_signature_line(line)

        if lines:
            return self._normalize_signature_line(lines[-1])

        return self._normalize_signature_line(matched_keyword)

    def _normalize_signature_line(self, line: str) -> str:
        text = line.lower().strip()

        # 去掉常见时间戳和日志级别。
        text = re.sub(r"\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}(?:,\d+)?", "<time>", text)
        text = re.sub(r"^\[[^\]]*(info|warning|error|debug|critical)[^\]]*\]\s*", "", text)

        # 归一化动态路径、行号、PID、十六进制地址。
        text = re.sub(r'file ".*?", line \d+', 'file "<path>", line <n>', text)
        text = re.sub(r"\bline\s+\d+\b", "line <n>", text)
        text = re.sub(r"\bpid\s*=?\s*\d+\b", "pid=<n>", text)
        text = re.sub(r"0x[0-9a-f]+", "<hex>", text)

        # 归一化明显的资源数值，但不强行删除端口号，方便区分 9100/19100。
        text = re.sub(r"\b\d+(?:\.\d+)?\s*(mib|gib|mb|gb)\b", "<mem>", text)
        text = re.sub(r"\s+", " ", text)

        return text[:300]

    def _dedupe_and_suppress(self, events: list[ErrorEvent]) -> list[ErrorEvent]:
        """
        事件降噪逻辑：

        1. 同 fingerprint 只保留一个；
        2. 同一段日志里如果已经有具体错误，抑制 traceback/log 泛化事件；
        3. 按严重程度排序，保证高优先级事件先进入 MonitorLoop。
        """
        if not events:
            return []

        specific_events = [event for event in events if event.issue_type != "log"]
        result: list[ErrorEvent] = []
        seen_fingerprints: set[str] = set()

        sorted_events = sorted(
            events,
            key=lambda event: (
                self.SEVERITY_RANK.get(event.severity, 0),
                0 if event.issue_type != "log" else -1,
            ),
            reverse=True,
        )

        for event in sorted_events:
            if event.fingerprint in seen_fingerprints:
                continue

            if event.issue_type == "log" and self._overlaps_specific_event(event, specific_events):
                continue

            result.append(event)
            seen_fingerprints.add(event.fingerprint)

        return result

    def _overlaps_specific_event(self, event: ErrorEvent, specific_events: list[ErrorEvent]) -> bool:
        for other in specific_events:
            if other.fingerprint == event.fingerprint:
                return True

            overlap_start = max(event.span_start, other.span_start)
            overlap_end = min(event.span_end, other.span_end)
            if overlap_start < overlap_end:
                return True

        return False
