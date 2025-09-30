import os
from datetime import datetime
import json
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("astrbot_plugin_chatsummary", "laopanmemz", "一个基于LLM的历史聊天记录总结插件", "1.1.0")
# 聊天记录总结插件主类，继承自Star基类
class ChatSummary(Star):
    # 初始化插件实例
    def __init__(self, context: Context):
        super().__init__(context)
        self.wake_prefix = self.context.get_config()["wake_prefix"]
        with open(os.path.join('data', 'config', 'astrbot_plugin_chatsummary_config.json'), 'r',
                  encoding='utf-8-sig') as a:
            config = json.load(a)
            self.prompt = str(config.get('prompt', {}).replace('\\n', '\n'))

    async def message_get_data(self, event: AstrMessageEvent, payloads: dict):
        """获取消息历史记录"""
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        assert isinstance(event, AiocqhttpMessageEvent)
        client = event.bot
        # 调用API获取群聊历史消息
        ret = await client.api.call_action("get_group_msg_history", **payloads)
        # 拿到bot本身的登录信息
        myid_post = await client.api.call_action("get_login_info", **payloads)
        myid = myid_post.get("user_id", {})

        # 处理消息历史记录，对其格式化
        messages = ret.get("messages", [])  # 获取响应内的历史消息体列表
        chat_lines = []
        for msg in messages:  # 遍历历史消息体列表
            # 解析发送者信息
            sender = msg.get('sender', {})
            if myid == sender.get('user_id', ""):
                continue
            nickname = sender.get('nickname', '未知用户')  # 拿到发送者名字
            msg_time = datetime.fromtimestamp(msg.get('time', 0))  # 拿到发送时间
            # 提取所有文本内容
            message_text = ""
            for part in msg['message']:
                if part['type'] == 'text':  # 如果遍历到的消息是纯文本，则直接拿到消息
                    message_text += part['data']['text'].strip() + " "
                elif part['type'] == 'json':  # 处理JSON格式的分享卡片等特殊消息
                    try:
                        json_content = json.loads(part['data']['data'])
                        if 'desc' in json_content.get('meta', {}).get('news', {}):
                            message_text += f"[分享内容]{json_content['meta']['news']['desc']} "
                    except:
                        pass

                # 表情消息处理
                elif part['type'] == 'face':
                    message_text += "[表情] "

            # 检查message_text的开头字符是否为唤醒词，如果是则跳过当前循环（用于跳过用户调用Bot的命令）
            if any(message_text.startswith(wa) for wa in self.wake_prefix):
                continue

            # 生成标准化的消息记录格式
            if message_text:
                chat_lines.append(f"[{msg_time}]「{nickname}」: {message_text.strip()}")

        # 生成最终内容
        msg = "\n".join(chat_lines)
        return msg

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("消息总结")
    async def summary(self, event: AstrMessageEvent, count: int = None):
        """群聊场景触发消息总结，命令加空格，后面跟获取聊天记录的数量即可（例如“ /消息总结 20 ”）"""
        # 检查是否传入了要总结的聊天记录数量，未传入则返回错误，并终止事件传播
        if count is None:
            yield event.plain_result("未传入要总结的聊天记录数量\n请按照「 /消息总结 [要总结的聊天记录数量] 」格式发送\n例如「 /消息总结 114 」~")
            event.stop_event()
            return

        # 构造获取群消息历史的请求参数
        payloads = {
          "group_id": event.get_group_id(),
          "message_seq": "0",
          "count": count,
          "reverseOrder": True
        }

        # 调用LLM生成总结内容
        llm_response = await self.context.get_using_provider().text_chat(
            prompt=self.prompt,
            contexts=[
                {"role": "user", "content": str(await self.message_get_data(event, payloads))}
            ],
        )

        # 输出LLM最终总结内容，发送总结消息
        yield event.plain_result(llm_response.completion_text)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("群总结")
    async def private_summary(self, event: AstrMessageEvent, count: int = None, group_id: int = None):
        """私聊场景触发群消息总结，命令加空格，后面跟获取聊天记录的数量和群号即可（例如“ /群总结 30 1145141919”）"""
        # 检查是否传入了要总结的聊天记录数量和群号，未传入则返回错误，并终止事件传播
        if count is None:
            yield event.plain_result("未传入要总结的聊天记录数量\n请按照「 /群总结 [要总结的聊天记录数量] [要总结的群号] 」格式发送\n例如「 /群总结 30 1145141919 」~")
            event.stop_event()
            return
        if group_id is None:
            yield event.plain_result("未传入要总结的群号\n请按照「 /群总结 [要总结的聊天记录数量] [要总结的群号] 」格式发送\n例如「 /群总结 30 1145141919 」~")
            event.stop_event()
            return

        # 构造获取群消息历史的请求参数
        payloads = {
          "group_id": group_id,
          "message_seq": "0",
          "count": count,
          "reverseOrder": True
        }

        # 调用LLM生成总结内容
        llm_response = await self.context.get_using_provider().text_chat(
            prompt=self.prompt,
            contexts=[
                {"role": "user", "content": str(await self.message_get_data(event, payloads))}
            ],
        )

        # 输出LLM最终总结内容，发送总结消息
        yield event.plain_result(llm_response.completion_text)