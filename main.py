import os.path
from datetime import datetime
import json

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_chatsummary", "laopanmemz", "一个基于LLM的历史聊天记录总结插件", "1.0.0")
class ChatSummary(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("消息总结")
    async def summary(self, event: AstrMessageEvent, count: int = None, debug:str=None):
        '''触发消息总结，命令加空格，后面跟获取聊天记录的数量即可''' # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        assert isinstance(event, AiocqhttpMessageEvent)
        client = event.bot
        payloads = {
          "group_id": event.get_group_id(),
          "message_seq": "0",
          "count": count,
          "reverseOrder": True
        }
        ret = await client.api.call_action("get_group_msg_history", **payloads)
        logger.info(f"get_group_msg_history: {ret}")
        # 处理消息历史记录，对其格式化
        messages = ret.get("messages", [])
        chat_lines = []
        for msg in messages:
            # 使用get方法并提供默认值
            sender = msg.get('sender', {})
            nickname = sender.get('nickname', '未知用户')
            msg_time = datetime.fromtimestamp(msg.get('time', 0))  # 防止time字段缺失
            # 提取所有文本内容（兼容多段文本消息）
            message_text = ""
            for part in msg['message']:
                if part['type'] == 'text':
                    message_text += part['data']['text'].strip() + " "
                elif part['type'] == 'json':  # 处理分享卡片等特殊消息
                    try:
                        json_content = json.loads(part['data']['data'])
                        if 'desc' in json_content.get('meta', {}).get('news', {}):
                            message_text += f"[分享内容]{json_content['meta']['news']['desc']} "
                    except:
                        pass

                # 表情消息处理
                elif part['type'] == 'face':
                    message_text += "[表情] "

            if message_text:
                # 使用清晰的分隔格式（换行 + 缩进）
                chat_lines.append(f"[{msg_time}]「{nickname}」: {message_text.strip()}")

        # 生成最终prompt
        msg = "\n".join(chat_lines)

        # 判断是否为管理员
        def _load_admins():
            with open(os.path.join('data', 'cmd_config.json'), 'r', encoding='utf-8-sig') as f:
                config = json.load(f)
                return config.get('admins_id', [])

        def is_admin(user_id):
            return str(user_id) in _load_admins()


        if debug == "debug" or debug == "Debug":
            if not is_admin(str(event.get_sender_id())):
                yield event.plain_result("您无权使用该命令！")
                return
            else:
                yield event.plain_result(str(msg))

        def load_prompt():
            with open(os.path.join('data','config','astrbot_plugin_chatsummary_config.json'), 'r', encoding='utf-8-sig') as a:
                config = json.load(a)
                prompt_str = config.get('prompt',{})
                return str(prompt_str.replace('\\n','\n'))

        logger.info(f"prompt: {load_prompt()}")
        logger.info(f"msg: {msg}")
        llm_response = await self.context.get_using_provider().text_chat(
            prompt=load_prompt(),
            contexts=[
                {"role": "user", "content": str(msg)}
            ],
        )
        logger.info(f"llm_response: {llm_response}")

        yield event.plain_result(llm_response.completion_text)
