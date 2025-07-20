# lark_send_patcher/main.py
# Description: 一个用于修正Lark平台回复行为的补丁插件，将所有回复操作转为发送新消息。
# Final Version 2.1: 修正了错误的过滤器名称，确保插件能正确加载。

import json
import uuid
import asyncio
from astrbot.api.star import Context, register, Star
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.platform import MessageType

# 全局变量，用于保存原始的 send 方法
original_lark_send = None

# ---------------- 我们自己定义的新 `send` 逻辑 ----------------
async def _new_lark_send(self: AstrMessageEvent, message: MessageChain):
    """
    这是我们新的send方法实现。
    它将调用 'acreate' (发送) 而不是 'areply' (回复)。
    """
    try:
        # 动态导入Lark API组件
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        bot = self.bot
        session_id = self.message_obj.session_id
        message_type = self.message_obj.type

        if id_type := {
            MessageType.GROUP_MESSAGE: "chat_id",
            MessageType.FRIEND_MESSAGE: "open_id",
        }.get(message_type):
            if "%" in session_id:
                session_id = session_id.split("%")[1]
        else:
            logger.error(f"[Lark Patcher] 未知的消息类型: {message_type}，无法确定ID类型。")
            return

        # 复用LarkMessageEvent原有的消息转换逻辑
        res = await self.__class__._convert_to_lark(message, bot)
        wrapped = {"zh_cn": {"title": "", "content": res}}

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(session_id)
                .content(json.dumps(wrapped))
                .msg_type("post")
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )

        response = await bot.im.v1.message.acreate(request)

        if not response.success():
            logger.error(f"[Lark Patcher] 发送飞书消息失败({response.code}): {response.msg}")

        await super(self.__class__, self).send(message)

    except Exception as e:
        logger.error(f"[Lark Patcher] 在新的send方法中发生错误: {e}", exc_info=True)


# ---------------- 补丁插件本体 ----------------
@register(
    "lark_send_patcher",
    "Gemini",
    "修正Lark平台的回复行为，将所有回复转为发送",
    "2.1.0",
)
class LarkSendModePatcher(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.is_patched = False
        self.patch_lock = asyncio.Lock()
        logger.info("[Lark Patcher] 插件已加载，正等待第一个Lark事件以应用补丁...")

    # 修正点: 使用两个正确的过滤器，分别监听私聊和群聊
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        await self.patch_on_first_lark_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        await self.patch_on_first_lark_event(event)

    async def patch_on_first_lark_event(self, event: AstrMessageEvent):
        """在第一个Lark事件到达时执行补丁操作的通用函数"""
        if self.is_patched or event.get_platform_name() != 'lark':
            return

        async with self.patch_lock:
            if self.is_patched:
                return

            logger.info("[Lark Patcher] 检测到第一个Lark事件，开始应用发送模式补丁...")
            
            global original_lark_send
            
            LarkMessageEventClass = event.__class__
            
            if not hasattr(LarkMessageEventClass, '_convert_to_lark'):
                logger.error(f"[Lark Patcher] 目标事件类 '{LarkMessageEventClass.__name__}' 不符合预期，补丁应用失败。")
                self.is_patched = True
                return

            original_lark_send = LarkMessageEventClass.send
            LarkMessageEventClass.send = _new_lark_send
            self.is_patched = True
            
            logger.info("[Lark Patcher] 成功！Lark平台的所有回复操作现已修正为发送新消息模式。")

    async def terminate(self):
        """插件被卸载/停用时调用，用于恢复原始行为"""
        global original_lark_send
        if self.is_patched and original_lark_send is not None:
            try:
                from astrbot.core.platform.lark.lark_event import LarkMessageEvent
                LarkMessageEvent.send = original_lark_send
                logger.info("[Lark Patcher] 插件已停用，Lark平台的发送行为已尝试恢复为原始状态。")
            except ImportError:
                 logger.warning("[Lark Patcher] 插件停用时无法找到Lark组件来恢复原始方法。")