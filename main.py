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
    "瑞贝特",
    "监控 OneBot v11/NapCat 连接状态，保留管理员的多平台会话并优先向非 QQ 平台推送。",
    "0.3.0",
)
class NapcatOfflineNoticePlugin(Star):
    """NapCat 掉线通知插件（跨平台投递版）。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_platform_status: dict[str, dict[str, Any]] = {}
        self._forced_offline_platforms: set[str] = set()  # 用于本地测试：假装这些平台离线

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
        """把当前平台会话显式加入通知目标。"""

        saved = await self._save_admin_session_if_needed(event, force=True)
        if saved:
            yield event.plain_result(
                f"已绑定当前平台通知会话：{event.unified_msg_origin}\n"
                "其他平台的已绑定会话会继续保留，不会被覆盖。"
            )
        else:
            yield event.plain_result("当前会话无法绑定，请检查 unified_msg_origin。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("unbind")
    async def unbind_target(self, event: AstrMessageEvent):
        """只移除当前平台的通知目标。"""

        removed = await self._remove_admin_session_for_event(event)
        if removed:
            yield event.plain_result(f"已解绑当前平台通知会话：{removed}")
        else:
            yield event.plain_result("当前平台没有已绑定的通知会话。")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("list")
    async def list_targets(self, event: AstrMessageEvent):
        """查看当前配置的管理员和插件记住的推送会话。"""
        admins = self._get_admins()
        known_sessions = await self._load_admin_sessions()
        lines = ["当前 AstrBot 配置的管理员（admins_id，仅作参考）："]
        for idx, admin in enumerate(admins, 1):
            lines.append(f"{idx}. {admin}")
        if not admins:
            lines.append("  （未配置；插件仍会按 event.is_admin() 记录管理员会话）")

        lines.append("")
        lines.append("插件记住的管理员跨平台会话（用于推送）：")
        if known_sessions:
            row = 0
            for admin_id, sessions in known_sessions.items():
                for platform_id, umo in sessions.items():
                    row += 1
                    lines.append(f"{row}. {admin_id} [{platform_id}] -> {umo}")
        else:
            lines.append("  （暂未记住任何会话，请管理员先在其他平台和 AstrBot 说句话）")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("status")
    async def show_status(self, event: AstrMessageEvent):
        """查看监控状态。"""
        configured_ids = sorted(self._configured_platform_ids())
        monitored_platforms = self._get_monitored_platforms()
        current_rows = self._collect_platform_status_rows(monitored_platforms)
        missing = [
            platform_id
            for platform_id in configured_ids
            if platform_id not in {platform.meta().id for platform in monitored_platforms}
        ]
        forced_offline = sorted(self._forced_offline_platforms)

        lines = [
            "NapCat 掉线通知状态（v0.3.0 跨平台投递版）：",
            f"- 监控范围: {self._format_monitored_platform_hint()}",
            f"- 轮询间隔: {self._poll_interval_seconds()} 秒",
            f"- 通知冷却: {self._cooldown_seconds()} 秒",
            f"- 恢复通知: {'开启' if self._notify_recovery() else '关闭'}",
            f"- LLM 文案: {'开启' if self._use_llm() else '关闭'}",
        ]

        if current_rows:
            lines.append("- 当前平台状态：")
            lines.extend(current_rows)
        else:
            lines.append("- 当前没有匹配到可监控的 aiocqhttp 平台实例。")

        if forced_offline:
            lines.append("- ⚠️ 以下平台正在被'假装离线'（测试用）：")
            lines.extend(f"  - {pid}" for pid in forced_offline)

        if missing:
            lines.append("- 以下配置的平台 ID 当前不存在：")
            lines.extend(f"  - {platform_id}" for platform_id in missing)

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("test")
    async def send_test_notice(self, event: AstrMessageEvent):
        """手动发送测试通知给所有管理员。"""
        # 管理员命令本身已通过权限过滤，因此可作为显式绑定兜底。
        await self._save_admin_session_if_needed(event, force=True)

        current_platform_id = parse_umo(event.unified_msg_origin).get("platform", "")
        monitored_platforms = self._get_monitored_platforms()
        if any(platform.meta().id == current_platform_id for platform in monitored_platforms):
            yield event.plain_result(
                "当前命令来自正在监控的 QQ/NapCat 平台。请到微信、WebChat、"
                "企业微信等其他平台执行 /napcat_notice bind，再回到任意平台测试。"
            )
            return

        platform_id = monitored_platforms[0].meta().id if monitored_platforms else "aiocqhttp"

        yield event.plain_result(
            "正在向所有已知管理员会话发送测试通知..."
        )

        success_count = await self._notify_admins(
            status="offline",
            platform_id=platform_id,
            detail="这是管理员手动触发的测试通知。",
            force_send=True,
        )

        yield event.plain_result(
            f"已向 {success_count} 个管理员会话发送测试通知。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("fake_offline")
    async def fake_platform_offline(self, event: AstrMessageEvent):
        """假装某个（或所有） NapCat 平台离线，用于本地测试。

        用法：
        - /napcat_notice fake_offline          假装所有监控的平台离线
        - /napcat_notice fake_offline <平台ID>  假装指定平台离线
        """
        payload = self._extract_subcommand_payload(event.message_str, "napcat_notice fake_offline")
        if payload:
            target_ids = {pid.strip() for pid in payload.replace(",", " ").split() if pid.strip()}
        else:
            target_ids = {p.meta().id for p in self._get_monitored_platforms()}

        if not target_ids:
            yield event.plain_result(
                "没有找到可监控的 aiocqhttp 平台实例。"
            )
            return

        self._forced_offline_platforms.update(target_ids)
        lines = ["已将以下平台标记为'假装离线'（会在下次轮询时触发通知）："]
        lines.extend(f"  - {pid}" for pid in sorted(target_ids))
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @napcat_notice.command("fake_online")
    async def fake_platform_online(self, event: AstrMessageEvent):
        """取消假装离线，恢复真实连接状态。

        用法：
        - /napcat_notice fake_online          取消所有假装
        - /napcat_notice fake_online <平台ID>  取消指定平台的假装
        """
        payload = self._extract_subcommand_payload(event.message_str, "napcat_notice fake_online")
        if payload:
            target_ids = {pid.strip() for pid in payload.replace(",", " ").split() if pid.strip()}
        else:
            target_ids = set(self._forced_offline_platforms)

        if not target_ids:
            yield event.plain_result(
                "当前没有平台被标记为'假装离线'。"
            )
            return

        removed = target_ids & self._forced_offline_platforms
        for pid in removed:
            self._forced_offline_platforms.discard(pid)

        lines = ["已取消以下平台的'假装离线'标记："]
        lines.extend(f"  - {pid}" for pid in sorted(removed))
        yield event.plain_result("\n".join(lines))

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
            is_online = self._is_platform_online(platform_id, connection_count)
            self._last_platform_status[platform_id] = {
                "online": is_online,
                "connection_count": connection_count,
                "updated_at": int(time.time()),
            }

    async def _poll_once(self):
        for platform in self._get_monitored_platforms():
            platform_id = platform.meta().id
            connection_count = self._get_connection_count(platform)
            is_online = self._is_platform_online(platform_id, connection_count)
            previous = self._last_platform_status.get(platform_id)

            self._last_platform_status[platform_id] = {
                "online": is_online,
                "connection_count": connection_count,
                "updated_at": int(time.time()),
            }

            if previous is None:
                continue

            previous_online = bool(previous.get("online"))
            previous_count = int(previous.get("connection_count", 0))
            if previous_online == is_online:
                continue

            if is_online:
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
        success_count = await self._notify_admins(
            status=status,
            platform_id=platform_id,
            detail=detail,
        )
        if success_count > 0:
            await self._mark_notification_sent(status, platform_id)

    async def _notify_admins(
        self,
        status: str,
        platform_id: str,
        detail: str,
        force_send: bool = False,
    ) -> int:
        """向已记录的管理员跨平台会话发送通知。"""

        successful_umos: set[str] = set()
        attempted_umos: set[str] = set()
        start_time = time.time()
        max_wait = 0 if force_send else self._retry_window_seconds()
        retry_interval = self._retry_interval_seconds()

        while True:
            admin_sessions = await self._load_admin_sessions()
            targets = self._build_delivery_targets(
                admin_sessions,
                excluded_platform_id=platform_id,
            )
            pending_targets = [
                (admin_id, umo)
                for admin_id, umo in targets
                if umo not in successful_umos
            ]

            if not targets:
                logger.warning(
                    "[NapcatOfflineNotice] 没有可用的跨平台管理员会话。"
                    "请管理员先在微信、WebChat、企业微信等其他平台和 AstrBot 说句话。"
                )
            for admin_id, umo in pending_targets:
                attempted_umos.add(umo)
                try:
                    text = await self._build_notice_text(
                        target_umo=umo,
                        status=status,
                        platform_id=platform_id,
                        detail=detail,
                    )
                    sent = await self.context.send_message(
                        umo,
                        MessageChain([Plain(text)]),
                    )
                    if sent:
                        successful_umos.add(umo)
                        logger.info(
                            "[NapcatOfflineNotice] 已向管理员 %s 的跨平台会话发送通知: %s",
                            admin_id,
                            umo,
                        )
                    else:
                        logger.warning(
                            "[NapcatOfflineNotice] 未找到会话对应的平台，发送失败: %s",
                            umo,
                        )
                except Exception as exc:
                    logger.exception(
                        "[NapcatOfflineNotice] 向管理员 %s（%s）发送通知失败: %s",
                        admin_id,
                        umo,
                        exc,
                    )

            if force_send or successful_umos:
                break
            if time.time() - start_time >= max_wait:
                break
            await asyncio.sleep(retry_interval)

        if not successful_umos:
            logger.error(
                "[NapcatOfflineNotice] 跨平台通知未送达。已尝试 %d 个会话；"
                "请运行 /napcat_notice list 检查已记录平台。",
                len(attempted_umos),
            )
        return len(successful_umos)

    def _build_delivery_targets(
        self,
        admin_sessions: dict[str, dict[str, str]],
        *,
        excluded_platform_id: str,
    ) -> list[tuple[str, str]]:
        """按平台可用性排序，并排除正在掉线的 NapCat 平台。"""

        available_platforms = {
            platform.meta().id: platform
            for platform in self.context.platform_manager.platform_insts
        }
        ranked: list[tuple[int, str, str]] = []
        seen: set[str] = set()
        for admin_id, sessions in admin_sessions.items():
            for stored_platform_id, umo in sessions.items():
                if not umo or umo in seen:
                    continue
                parsed_platform_id = parse_umo(umo).get("platform", "")
                target_platform_id = parsed_platform_id or stored_platform_id
                if target_platform_id == excluded_platform_id:
                    continue
                platform = available_platforms.get(target_platform_id)
                if platform is None:
                    continue
                if platform.meta().name == "aiocqhttp":
                    continue
                stats = platform.get_stats()
                status = str(stats.get("status", "")) if isinstance(stats, dict) else ""
                rank = 0 if status == "running" else 1
                ranked.append((rank, admin_id, umo))
                seen.add(umo)
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(admin_id, umo) for _, admin_id, umo in ranked]
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
        if status == "recovery":
            template_key = "fallback_recovery_template"
            default_template = (
                "好消息，{platform_id} 对应的 NapCat 已经重新连上了，"
                "QQ 侧消息恢复正常啦。{detail}"
            )
        else:
            template_key = "fallback_offline_template"
            default_template = (
                "提醒一下，{platform_id} 对应的 NapCat 现在断开了，"
                "QQ 侧消息暂时收不到了。{detail}"
            )
        template = str(self.config.get(template_key, "") or default_template)
        return self._render_template(
            template,
            target_umo=target_umo,
            platform_id=platform_id,
            status=status,
            status_text=self._status_text(status),
            detail=detail,
        )

    async def _load_admin_sessions(self) -> dict[str, dict[str, str]]:
        """加载管理员的多平台会话，并兼容 0.2.x 单会话格式。"""

        raw = await self.get_kv_data("admin_sessions", {})
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        migrated = False
        for raw_admin_id, raw_sessions in raw.items():
            admin_id = str(raw_admin_id).strip()
            if not admin_id:
                continue
            sessions: dict[str, str] = {}
            if isinstance(raw_sessions, str):
                umo = raw_sessions.strip()
                if umo:
                    sessions[parse_umo(umo).get("platform", "unknown")] = umo
                    migrated = True
            elif isinstance(raw_sessions, dict):
                for raw_platform_id, raw_umo in raw_sessions.items():
                    if not isinstance(raw_umo, str) or not raw_umo.strip():
                        continue
                    umo = raw_umo.strip()
                    platform_id = parse_umo(umo).get("platform", "") or str(raw_platform_id)
                    sessions[platform_id] = umo
            if sessions:
                result[admin_id] = sessions
        if migrated:
            await self._save_admin_sessions(result)
        return result

    async def _save_admin_sessions(
        self,
        admin_sessions: dict[str, dict[str, str]],
    ) -> None:
        await self.put_kv_data("admin_sessions", admin_sessions)

    async def _save_admin_session_if_needed(
        self,
        event: AstrMessageEvent,
        *,
        force: bool = False,
    ) -> bool:
        """记录管理员在每个平台的最近会话，不让 QQ 覆盖其他平台。"""

        if not force and not event.is_admin():
            return False
        try:
            sender_id = str(event.get_sender_id()).strip()
        except Exception:
            sender_id = ""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo:
            return False
        platform_id = parse_umo(umo).get("platform", "").strip()
        if not platform_id:
            return False
        admin_id = sender_id or f"admin@{platform_id}"

        admin_sessions = await self._load_admin_sessions()
        sessions = admin_sessions.setdefault(admin_id, {})
        if sessions.get(platform_id) == umo:
            return True
        sessions[platform_id] = umo
        await self._save_admin_sessions(admin_sessions)
        logger.info(
            "[NapcatOfflineNotice] 已记录管理员 %s 的 %s 会话: %s",
            admin_id,
            platform_id,
            umo,
        )
        return True

    async def _remove_admin_session_for_event(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        platform_id = parse_umo(umo).get("platform", "").strip()
        if not platform_id:
            return None
        try:
            sender_id = str(event.get_sender_id()).strip()
        except Exception:
            sender_id = ""
        admin_sessions = await self._load_admin_sessions()
        candidate_admin_ids = [sender_id] if sender_id else []
        candidate_admin_ids.extend(
            admin_id
            for admin_id, sessions in admin_sessions.items()
            if sessions.get(platform_id) == umo and admin_id not in candidate_admin_ids
        )
        for admin_id in candidate_admin_ids:
            sessions = admin_sessions.get(admin_id)
            if not sessions or platform_id not in sessions:
                continue
            removed = sessions.pop(platform_id)
            if not sessions:
                admin_sessions.pop(admin_id, None)
            await self._save_admin_sessions(admin_sessions)
            return removed
        return None
    def _get_admins(self) -> list[str]:
        """从 AstrBot 全局配置读取 admins_id。"""
        try:
            config = self.context.get_config()
            if hasattr(config, "get"):
                admins = config.get("admins_id", [])
            else:
                admins = getattr(config, "admins_id", [])
            if isinstance(admins, list):
                return [str(a).strip() for a in admins if a and str(a).strip()]
        except Exception as exc:
            logger.warning(
                "[NapcatOfflineNotice] 读取 AstrBot admins_id 配置失败: %s",
                exc,
            )
        return []

    async def _should_send_notification(self, status: str, platform_id: str) -> bool:
        cooldown = self._cooldown_seconds()
        if cooldown <= 0:
            return True
        key = f"delivered_ts:v3:{status}:{platform_id}"
        last_sent_at = await self.get_kv_data(key, 0)
        try:
            last_sent_at_int = int(last_sent_at or 0)
        except (TypeError, ValueError):
            last_sent_at_int = 0
        return not last_sent_at_int or int(time.time()) - last_sent_at_int >= cooldown

    async def _mark_notification_sent(self, status: str, platform_id: str) -> None:
        key = f"delivered_ts:v3:{status}:{platform_id}"
        await self.put_kv_data(key, int(time.time()))

    def _is_platform_online(self, platform_id: str, real_connection_count: int) -> bool:
        """
        判断平台是否在线。
        优先看是否被标记为 '假装离线'，否则看真实连接数。
        """
        if platform_id in self._forced_offline_platforms:
            return False
        return real_connection_count > 0

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
            real_count = self._get_connection_count(platform)
            is_online = self._is_platform_online(platform_id, real_count)
            status_text = "在线" if is_online else "离线"

            if platform_id in self._forced_offline_platforms:
                rows.append(f"  - {platform_id}: {status_text} (假装离线，真实连接数: {real_count})")
            else:
                rows.append(f"  - {platform_id}: {status_text} (连接数: {real_count})")
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

    def _retry_window_seconds(self) -> int:
        try:
            return max(0, int(self.config.get("retry_window_seconds", 300)))
        except (TypeError, ValueError):
            return 300

    def _retry_interval_seconds(self) -> int:
        try:
            return max(1, int(self.config.get("retry_interval_seconds", 10)))
        except (TypeError, ValueError):
            return 10

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """
        钩子：任何消息进来，先看看是不是管理员。
        如果是，保存该会话，用于以后推送通知。
        """
        try:
            await self._save_admin_session_if_needed(event)
        except Exception:
            pass
        # 不处理消息，不阻塞后续处理
        return None
