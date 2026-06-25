from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.umo_alias import parse_umo


@register(
    "astrbot_plugin_napcat_offline_notice",
    "qiongqiong",
    "监控 OneBot v11/NapCat 连接状态，在掉线或恢复后主动通知指定会话，并尽量使用该会话当前的模型与人格生成提醒文案",
    "0.1.0",
)
class NapcatOfflineNoticePlugin(Star):
    """NapCat 掉线通知插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_platform_status: dict[str, dict[str, Any]] = {}

    async def initialize(self):
        """启动后台监控任务。"""
        self._stop_event = asyncio.Event()
        await self._seed_platform_status()
        self.monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="napcat_offline_notice_monitor",
        )
        logger.info(
            "[NapcatOfflineNotice] 插件已启动，监控范围: %s",
            self._format_monitored_platform_hint(),
        )

    async def terminate(self):
        """停止后台监控任务。"""
        self._stop_event.set()
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None

    @filter.command_group("napcat_notice")
    def napcat_notice(self):
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("bind")
    async def bind_target(self, event: AstrMessageEvent):
        """将当前会话绑定为通知目标。"""
        alias = self._extract_subcommand_payload(event.message_str, "napcat_notice bind")
        umo = event.unified_msg_origin
        targets = await self._load_targets()
        parsed = parse_umo(umo)

        for item in targets:
            if item.get("umo") == umo:
                if alias:
                    item["alias"] = alias
                await self._save_targets(targets)
                yield event.plain_result(
                    f"这个会话已经在通知列表里了。\n当前标记：{self._format_target_display(item)}"
                )
                return

        target = {
            "umo": umo,
            "alias": alias,
            "platform": parsed.get("platform", "unknown"),
            "message_type": parsed.get("message_type", "unknown"),
            "session_id": parsed.get("session_id", umo),
            "created_at": int(time.time()),
        }
        targets.append(target)
        await self._save_targets(targets)
        yield event.plain_result(
            "已绑定当前会话为 NapCat 掉线通知目标。\n"
            f"会话：{self._format_target_display(target)}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("unbind")
    async def unbind_target(self, event: AstrMessageEvent):
        """解绑当前会话。"""
        umo = event.unified_msg_origin
        targets = await self._load_targets()
        kept_targets = [item for item in targets if item.get("umo") != umo]

        if len(kept_targets) == len(targets):
            yield event.plain_result("当前会话还没有绑定到通知列表。")
            return

        await self._save_targets(kept_targets)
        yield event.plain_result("已解除当前会话的 NapCat 通知绑定。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("list")
    async def list_targets(self, event: AstrMessageEvent):
        """查看已绑定的通知会话。"""
        targets = await self._load_targets()
        if not targets:
            yield event.plain_result(
                "还没有绑定任何通知会话。\n请在目标对话里执行 /napcat_notice bind"
            )
            return

        lines = ["已绑定的通知会话："]
        for index, item in enumerate(targets, start=1):
            lines.append(f"{index}. {self._format_target_display(item)}")
            lines.append(f"   {item.get('umo', '')}")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("status")
    async def show_status(self, event: AstrMessageEvent):
        """查看监控状态。"""
        targets = await self._load_targets()
        configured_ids = sorted(self._configured_platform_ids())
        monitored_platforms = self._get_monitored_platforms()
        current_rows = self._collect_platform_status_rows(monitored_platforms)
        missing = [
            platform_id
            for platform_id in configured_ids
            if platform_id not in {platform.meta().id for platform in monitored_platforms}
        ]

        lines = [
            "NapCat 掉线通知状态：",
            f"- 监控范围: {self._format_monitored_platform_hint()}",
            f"- 轮询间隔: {self._poll_interval_seconds()} 秒",
            f"- 通知冷却: {self._cooldown_seconds()} 秒",
            f"- 恢复通知: {'开启' if self._notify_recovery() else '关闭'}",
            f"- LLM 文案: {'开启' if self._use_llm() else '关闭'}",
            f"- 已绑定会话数: {len(targets)}",
        ]

        if current_rows:
            lines.append("- 当前平台状态：")
            lines.extend(current_rows)
        else:
            lines.append("- 当前没有匹配到可监控的 aiocqhttp 平台实例。")

        if missing:
            lines.append("- 以下配置的平台 ID 当前不存在：")
            lines.extend(f"  - {platform_id}" for platform_id in missing)

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("test")
    async def send_test_notice(self, event: AstrMessageEvent):
        """手动发送测试通知到全部已绑定会话。"""
        targets = await self._load_targets()
        if not targets:
            yield event.plain_result("还没有绑定任何通知会话，无法发送测试通知。")
            return

        monitored_platforms = self._get_monitored_platforms()
        platform_id = monitored_platforms[0].meta().id if monitored_platforms else "aiocqhttp"
        await self._notify_targets(
            status="offline",
            platform_id=platform_id,
            detail="这是管理员手动触发的测试通知。",
        )
        yield event.plain_result(f"已向 {len(targets)} 个已绑定会话发送测试通知。")

    async def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("[NapcatOfflineNotice] 监控循环出错: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds(),
                )
            except asyncio.TimeoutError:
                continue

    async def _seed_platform_status(self):
        self._last_platform_status = {}
        for platform in self._get_monitored_platforms():
            platform_id = platform.meta().id
            connection_count = self._get_connection_count(platform)
            self._last_platform_status[platform_id] = {
                "online": connection_count > 0,
                "connection_count": connection_count,
                "updated_at": int(time.time()),
            }

    async def _poll_once(self):
        for platform in self._get_monitored_platforms():
            platform_id = platform.meta().id
            connection_count = self._get_connection_count(platform)
            online = connection_count > 0
            previous = self._last_platform_status.get(platform_id)

            self._last_platform_status[platform_id] = {
                "online": online,
                "connection_count": connection_count,
                "updated_at": int(time.time()),
            }

            if previous is None:
                continue

            previous_online = bool(previous.get("online"))
            previous_count = int(previous.get("connection_count", 0))
            if previous_online == online:
                continue

            if online:
                if not self._notify_recovery():
                    logger.info(
                        "[NapcatOfflineNotice] %s 已恢复连接，但恢复通知已关闭。",
                        platform_id,
                    )
                    continue
                detail = (
                    f"连接数从 {previous_count} 恢复到 {connection_count}，"
                    "QQ 侧消息现在应该已经恢复。"
                )
                await self._handle_status_change("recovery", platform_id, detail)
            else:
                detail = (
                    f"连接数从 {previous_count} 变为 {connection_count}，"
                    "这通常表示 NapCat 已断开，可能是被踢下线、断网或进程退出。"
                )
                await self._handle_status_change("offline", platform_id, detail)

    async def _handle_status_change(self, status: str, platform_id: str, detail: str):
        if not await self._should_send_notification(status, platform_id):
            logger.info(
                "[NapcatOfflineNotice] %s %s 通知处于冷却时间内，跳过发送。",
                platform_id,
                status,
            )
            return

        logger.info(
            "[NapcatOfflineNotice] 检测到 %s 状态变化: %s",
            platform_id,
            status,
        )
        await self._notify_targets(status=status, platform_id=platform_id, detail=detail)

    async def _notify_targets(self, status: str, platform_id: str, detail: str):
        targets = await self._load_targets()
        if not targets:
            logger.info(
                "[NapcatOfflineNotice] 已检测到 %s 的 %s，但当前没有绑定任何通知会话。",
                platform_id,
                status,
            )
            return

        for target in targets:
            target_umo = target.get("umo", "").strip()
            if not target_umo:
                continue

            text = await self._build_notice_text(
                target_umo=target_umo,
                status=status,
                platform_id=platform_id,
                detail=detail,
            )
            try:
                sent = await self.context.send_message(
                    target_umo,
                    MessageChain([Plain(text)]),
                )
                if not sent:
                    logger.warning(
                        "[NapcatOfflineNotice] 未找到可发送的平台，会话=%s",
                        target_umo,
                    )
            except Exception as exc:
                logger.exception(
                    "[NapcatOfflineNotice] 向会话 %s 发送通知失败: %s",
                    target_umo,
                    exc,
                )

    async def _build_notice_text(
        self,
        *,
        target_umo: str,
        status: str,
        platform_id: str,
        detail: str,
    ) -> str:
        fallback_text = self._build_fallback_text(
            target_umo=target_umo,
            status=status,
            platform_id=platform_id,
            detail=detail,
        )
        if not self._use_llm():
            return fallback_text

        try:
            provider = self.context.get_using_provider(target_umo)
            if not provider:
                return fallback_text

            provider_id = provider.meta().id
            persona_prompt = await self._resolve_persona_prompt(target_umo)
            llm_prompt = self._render_template(
                str(self.config.get("llm_prompt_template", "")),
                target_umo=target_umo,
                platform_id=platform_id,
                status=status,
                status_text=self._status_text(status),
                detail=detail,
            )

            if not llm_prompt.strip():
                return fallback_text

            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=llm_prompt,
                system_prompt=persona_prompt or None,
            )
            text = (getattr(response, "completion_text", "") or "").strip()
            return text or fallback_text
        except Exception as exc:
            logger.warning("[NapcatOfflineNotice] LLM 生成通知失败: %s", exc)
            return fallback_text

    async def _resolve_persona_prompt(self, umo: str) -> str:
        try:
            config = self.context.get_config(umo)
            provider_settings = {}
            if hasattr(config, "get"):
                provider_settings = config.get("provider_settings", {}) or {}

            platform_name = parse_umo(umo).get("platform", "unknown")
            _, persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=None,
                platform_name=platform_name,
                provider_settings=provider_settings,
            )
            if not persona:
                persona = await self.context.persona_manager.get_default_persona_v3(umo)
            return str(self._read_value(persona, "prompt", "") or "")
        except Exception as exc:
            logger.warning("[NapcatOfflineNotice] 解析会话人格失败: %s", exc)
            return ""

    def _build_fallback_text(
        self,
        *,
        target_umo: str,
        status: str,
        platform_id: str,
        detail: str,
    ) -> str:
        template_key = (
            "fallback_recovery_template" if status == "recovery" else "fallback_offline_template"
        )
        template = str(self.config.get(template_key, "") or "")
        return self._render_template(
            template,
            target_umo=target_umo,
            platform_id=platform_id,
            status=status,
            status_text=self._status_text(status),
            detail=detail,
        )

    async def _load_targets(self) -> list[dict[str, Any]]:
        raw = await self.get_kv_data("targets", [])
        if not isinstance(raw, list):
            return []

        targets: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            umo = str(item.get("umo", "")).strip()
            if not umo:
                continue
            targets.append(
                {
                    "umo": umo,
                    "alias": str(item.get("alias", "")).strip(),
                    "platform": str(item.get("platform", "")).strip(),
                    "message_type": str(item.get("message_type", "")).strip(),
                    "session_id": str(item.get("session_id", "")).strip(),
                    "created_at": int(item.get("created_at", 0) or 0),
                }
            )
        return targets

    async def _save_targets(self, targets: list[dict[str, Any]]):
        await self.put_kv_data("targets", targets)

    async def _should_send_notification(self, status: str, platform_id: str) -> bool:
        cooldown = self._cooldown_seconds()
        if cooldown <= 0:
            return True

        key = f"notify_ts:{status}:{platform_id}"
        now = int(time.time())
        last_sent_at = await self.get_kv_data(key, 0)
        try:
            last_sent_at_int = int(last_sent_at or 0)
        except (TypeError, ValueError):
            last_sent_at_int = 0

        if last_sent_at_int and now - last_sent_at_int < cooldown:
            return False

        await self.put_kv_data(key, now)
        return True

    def _get_monitored_platforms(self) -> list[Any]:
        target_ids = self._configured_platform_ids()
        platforms: list[Any] = []
        for platform in self.context.platform_manager.platform_insts:
            meta = platform.meta()
            if meta.name != "aiocqhttp":
                continue
            if target_ids and meta.id not in target_ids:
                continue
            platforms.append(platform)
        return platforms

    def _collect_platform_status_rows(self, platforms: list[Any]) -> list[str]:
        rows: list[str] = []
        for platform in platforms:
            platform_id = platform.meta().id
            connection_count = self._get_connection_count(platform)
            status_text = "在线" if connection_count > 0 else "离线"
            rows.append(f"  - {platform_id}: {status_text} (连接数: {connection_count})")
        return rows

    def _configured_platform_ids(self) -> set[str]:
        raw = str(self.config.get("target_platform_ids", "") or "").strip()
        if not raw or raw == "*":
            return set()

        result: set[str] = set()
        normalized = raw.replace("\r", "\n").replace(",", "\n")
        for item in normalized.split("\n"):
            value = item.strip()
            if value:
                result.add(value)
        return result

    def _get_connection_count(self, platform: Any) -> int:
        bot = getattr(platform, "bot", None)
        if bot is None:
            get_client = getattr(platform, "get_client", None)
            if callable(get_client):
                bot = get_client()

        api_clients = getattr(bot, "_wsr_api_clients", None)
        event_clients = getattr(bot, "_wsr_event_clients", None)

        connection_count = 0
        if isinstance(api_clients, dict):
            connection_count += len(api_clients)
        if isinstance(event_clients, set):
            connection_count += len(event_clients)
        return connection_count

    def _format_monitored_platform_hint(self) -> str:
        configured_ids = sorted(self._configured_platform_ids())
        if not configured_ids:
            return "全部 aiocqhttp / OneBot v11 实例"
        return ", ".join(configured_ids)

    def _format_target_display(self, target: dict[str, Any]) -> str:
        alias = str(target.get("alias", "")).strip()
        if alias:
            return alias

        platform = str(target.get("platform", "unknown")).strip() or "unknown"
        message_type = str(target.get("message_type", "unknown")).strip() or "unknown"
        session_id = str(target.get("session_id", target.get("umo", ""))).strip()
        return f"{platform} / {message_type} / {session_id}"

    def _status_text(self, status: str) -> str:
        if status == "recovery":
            return "NapCat 已恢复连接"
        return "NapCat 已断开连接，可能是被踢下线"

    def _extract_subcommand_payload(self, message: str, command_text: str) -> str:
        normalized = (message or "").strip()
        for prefix in (f"/{command_text}", command_text):
            if normalized.startswith(prefix):
                return normalized[len(prefix) :].strip()
        return ""

    def _render_template(self, template: str, **kwargs: Any) -> str:
        try:
            return template.format(**kwargs).strip()
        except Exception:
            return template.strip()

    def _read_value(self, obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _poll_interval_seconds(self) -> int:
        try:
            return max(2, int(self.config.get("poll_interval_seconds", 5)))
        except (TypeError, ValueError):
            return 5

    def _cooldown_seconds(self) -> int:
        try:
            return max(0, int(self.config.get("offline_cooldown_seconds", 600)))
        except (TypeError, ValueError):
            return 600

    def _notify_recovery(self) -> bool:
        return bool(self.config.get("notify_recovery", True))

    def _use_llm(self) -> bool:
        return bool(self.config.get("use_llm", True))
