import json
import re
from datetime import datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_chatsummary", "laopanmemz", "一个基于LLM的历史聊天记录总结插件", "1.2.0")
class ChatSummary(Star):

    max_count:int
    prompt:str
    wake_prefix:str

    def __init__(self, context: Context,config:AstrBotConfig):
        super().__init__(context)
        self.max_count = config.get("max_count",50)
        self.prompt = config.get("prompt","")
        self.wake_prefix = config.get("wake_prefix","")
        logger.info(f"max_count:{self.max_count},prompt:{self.prompt[:10]},wake_prefix:{self.wake_prefix}")
    @filter.llm_tool(name="group_summary")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("group_summary",alias={"消息总结"})
    async def group_summary(self,event:AstrMessageEvent,params:str):
        """群聊场景触发消息总结

        Args:
            params(string): 参数,可以为整数或者中文时间表达式.当为整数时,表示对前N条消息进行总结;当为时间表达式时,表示对时间范围内的消息进行总结,例如:今天,昨天,N天(N天内)
        使用:
            /group_summary <消息数量>       对前N条消息进行总结
            /group_summary <时间范围>       对指定时间范围内的消息进行总结,例如: 今天,昨天,N天
        """

        payloads = {
            "group_id": event.get_group_id(),
            "message_seq": 0,  # 从该序号的消息开始往前数
            "count": self.max_count,
            "reverse_order": True # 时间早的在前
        }

        message_text = ""
        # 参数为数值
        if params.isdigit():
            count = int(params)
            payloads["count"] = count
            message_text = await self._fetch_msg_by_count(event,payloads)
        else:
            time_range = self._parse_time_expr(params)
            if time_range is None:
                yield event.plain_result(
                    f"无法解析时间范围 {params} \n"
                    "请使用以下格式: 今天,昨天,N天(例如: 1天,7天)"
                )
                event.stop_event()
                return
            start = time_range[0]
            end = time_range[1]
            days = time_range[2]
            payloads["count"] *= days
            logger.info(f"起始时间戳start:{start},结束时间戳end:{end},查询天数为:{days}")
            message_text = await self._fetch_msg_by_time(event,start,end,payloads)
        logger.info(f"处理后的信息为:\n{message_text}")
        result = await self._do_summary(message_text)
        yield event.plain_result(result)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("群总结")
    async def private_summary(self, event: AstrMessageEvent, count: int = None, group_id: int = None): #type:ignore
        """私聊场景触发群消息总结，命令加空格，后面跟获取聊天记录的数量和群号即可(例如“ /群总结 30 1145141919”)"""
        # 检查是否传入了要总结的聊天记录数量和群号，未传入则返回错误，并终止事件传播,pylance提示不可访问,实测仍能正常运行
        logger.info(f"消息数量:{count},群号:{group_id}")
        if count is None:
            yield event.plain_result("未传入要总结的聊天记录数量\n请按照「 /群总结 [要总结的聊天记录数量] [要总结的群号] 」格式发送\n例如「 /群总结 30 1145141919 」~")
            event.stop_event()
        if group_id is None:
            yield event.plain_result("未传入要总结的群号\n请按照「 /群总结 [要总结的聊天记录数量] [要总结的群号] 」格式发送\n例如「 /群总结 30 1145141919 」~")
            event.stop_event()

        # 构造获取群消息历史的请求参数
        payloads = {
            "group_id": group_id,
            "message_seq": 0,
            "count": count,
            "reverseOrder": True
        }

        message_text = await self._fetch_msg_by_count(event,payloads)
        result = await self._do_summary(message_text)
        yield event.plain_result(result)

    async def _fetch_msg_by_count(self,event:AstrMessageEvent,payloads:dict):
        """
        获取指定数量的消息并格式化
        """
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        assert isinstance(event, AiocqhttpMessageEvent)
        client = event.bot

        # 调用API获取群聊历史消息
        ret = await client.api.call_action("get_group_msg_history", **payloads)
        # 拿到bot本身的登录信息
        bot_id_post = await client.api.call_action("get_login_info")
        bot_id = bot_id_post.get("user_id", "")

        messages = ret.get("messages", [])  # 获取响应内的历史消息体列表
        # 处理消息历史记录，对其格式化
        msg = self._format_msg(messages,bot_id)

        return msg
    async def _fetch_msg_by_time(self,event:AstrMessageEvent,start_ts:float,end_ts:float,payloads:dict):
        """
        获取指定时间段内的消息并格式化
        """
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        assert isinstance(event, AiocqhttpMessageEvent)
        client = event.bot

        # 调用API获取群聊历史消息
        ret = await client.api.call_action("get_group_msg_history", **payloads)
        # 拿到bot本身的登录信息
        bot_id_post = await client.api.call_action("get_login_info")
        bot_id = bot_id_post.get("user_id", "")

        messages = ret.get("messages", [])  # 获取响应内的历史消息体列表
        logger.info(f"原始数据有{len(messages)}条")
        collected: list[dict] = []

        for msg in messages:
            msg_time = msg.get("time", 0)
            if msg["raw_message"] == "":
                continue
            if msg_time >= start_ts and msg_time <= end_ts:
                collected.append(msg)

        logger.info(f"排除空消息之后有{len(collected)}条")
        msg = self._format_msg(collected,bot_id)
        return msg
    def _format_msg(self,messages:list[dict],bot_id:str):
        """
        获取群消息历史记录,格式化为:
        [时间][用户名]: 内容
        """
        chat_lines = []
        for msg in messages:  # 遍历历史消息体列表
            # 解析发送者信息
            sender = msg.get("sender", {})
            if bot_id == sender.get("user_id", ""): # 忽略bot的回复
                continue
            nickname = sender.get("nickname", "未知用户")  # 拿到发送者名字
            msg_time = datetime.fromtimestamp(msg.get("time", 0))  # 拿到发送时间
            # 提取所有文本内容
            message_text = ""
            for part in msg["message"]:
                if part["type"] == "text":  # 如果遍历到的消息是纯文本，则直接拿到消息
                    message_text += part["data"]["text"].strip() + " "
                elif part["type"] == "json":  # 处理JSON格式的分享卡片等特殊消息
                    try:
                        json_content = json.loads(part["data"]["data"])
                        if "desc" in json_content.get("meta", {}).get("news", {}):
                            message_text += f"[分享内容]{json_content['meta']['news']['desc']} "
                    except:
                        pass

                # 表情消息处理
                elif part["type"] == "face":
                    message_text += "[表情] "

            # 检查message_text的开头字符是否为唤醒词，如果是则跳过当前循环（用于跳过用户调用Bot的命令）
            if any(message_text.startswith(wa) for wa in self.wake_prefix):
                continue

            # 生成标准化的消息记录格式
            if message_text:
                chat_lines.append(f"[{msg_time}][{nickname}]: {message_text.strip()}")

        # 生成最终内容
        msg = "\n".join(chat_lines)
        return msg
    def _parse_time_expr(self,expr:str):
        """将中文时间表达式转换为时间戳格式

        返回结果:
            (start_timestamp,end_timestamp,days) or None(转换失败)

        支持格式:
            "今天": 本日的00:00:00到现在
            "昨天": 昨日的00:00:00到今日00:00:00
            "N天":  N天内,例如今日8号,3天就是6,7,8三天
        """

        now = datetime.now()
        today_start = now.replace(hour=0,minute=0,second=0,microsecond=0)
        yesterday_start = today_start - timedelta(days=1)

        if expr == "今天":
            return (today_start.timestamp(), now.timestamp(),1)
        elif expr == "昨天":
            return (yesterday_start.timestamp(), today_start.timestamp(),1)

        match = re.fullmatch(r"(\d+)天", expr)
        if match:
            days = int(match.group(1))
            if days <= 0:
                logger.info(f"days:{days},非预期输入,返回None")
                return None

            start_time = today_start - timedelta(days=days-1)
            return (start_time.timestamp(), now.timestamp(),days)
        return None

    async def _do_summary(self,message_text:str) -> str:
        """调用LLM生成总结内容"""
        llm_response = await self.context.get_using_provider().text_chat( #type:ignore
            prompt=self.prompt,
            contexts=[
                {"role": "user", "content": message_text}
            ],
        )
        return llm_response.completion_text
