import os
import json
import random
import dashscope
from dotenv import load_dotenv
from openai import OpenAI
from dashscope.audio.tts_v2 import SpeechSynthesizer
import base64
import queue
import threading
from typing import Iterator

load_dotenv()
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
STORY_PATH = CORE_DIR / "story.json"

STORY_TAGS = (
    "勇气", "诚实", "责任", "规则", "独立", "友谊", "分享", "礼貌", "合作", "亲情",
    "感恩", "同情心", "情绪管理", "自信心", "成长", "生命教育", "表达与倾听",
    "科学启蒙", "解决问题", "数理逻辑", "坚持", "专注", "职业启蒙", "身体健康",
    "生活习惯", "安全自护", "运动锻炼", "艺术感知", "想象力", "创造力", "幽默",
    "冒险", "睡前故事", "角色扮演", "归属感", "科学", "冒险旅程", "奇幻", "伤心",
    "生命", "死亡", "英雄", "自然",
)
TONE_OPTIONS = (
    "温柔", "治愈", "活泼", "明快", "欢快", "勇敢", "阳光", "激昂", "豪放", "自信",
    "坚定", "沉稳", "平静", "严肃",
)
VOICE_MAPPING = {
    "儿童-温柔": "longhuhu_v3",
    "儿童-治愈": "longhuhu_v3",
    "儿童-活泼": "longpaopao_v3",
    "儿童-明快": "longpaopao_v3",
    "儿童-欢快": "longpaopao_v3",
    "儿童-勇敢": "longjielidou_v3",
    "儿童-阳光": "longjielidou_v3",
    "儿童-激昂": "longjielidou_v3",
    "儿童-豪放": "longxian_v3",
    "儿童-自信": "longxian_v3",
    "儿童-坚定": "longxian_v3",
    "儿童-沉稳": "longling_v3",
    "儿童-平静": "longling_v3",
    "儿童-严肃": "longling_v3",
    "成人-温柔": "longanyang",
    "成人-治愈": "longanyang",
    "成人-沉稳": "longanyang",
    "成人-阳光": "longanyang",
    "成人-勇敢": "longanyang",
    "成人-激昂": "longanyang",
    "成人-坚定": "longanyang",
    "成人-平静": "longanyang",
    "成人-严肃": "longanyang",
    "成人-活泼": "longanhuan",
    "成人-明快": "longanhuan",
    "成人-欢快": "longanhuan",
    "成人-豪放": "longanhuan",
    "成人-自信": "longanhuan",
}
DEFAULT_VOICE = "longhuhu_v3"


def encode_audio(audio_path):
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode("utf-8")


def _parse_json_object(raw: str) -> dict:
    """兼容纯 JSON 和 Markdown 代码块，并拒绝非对象结果。"""
    raw = (raw or "").strip()
    if "```" in raw:
        parts = raw.split("```")
        raw = next((part for part in parts if "{" in part), raw)
        raw = raw.removeprefix("json").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end < start:
        raise ValueError("模型未返回有效 JSON 对象")
    result = json.loads(raw[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    return result


def _normalize_speaker_type(raw: str) -> str:
    value = (raw or "").strip().replace("'", "").replace('"', "")
    if "儿童" in value and "成人" not in value:
        return "儿童"
    if "成人" in value and "儿童" not in value:
        return "成人"
    raise ValueError(f"无法判断说话人类型: {raw!r}")


# ==================== 语音识别 ====================
def recognization(audio_file: str):
    """返回 (文本, 情绪, 人群类型)"""
    audio_path = Path(audio_file).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    messages = [
        {"role": "user", "content": [{"audio": str(audio_path)}]},
    ]
    response = dashscope.MultiModalConversation.call(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        model="qwen3-asr-flash",
        messages=messages,
        result_format="message",
        asr_options={"enable_lid": True, "enable_itn": False},
    )
    status_code = getattr(response, "status_code", 200)
    if status_code != 200:
        message = getattr(response, "message", "未知错误")
        raise RuntimeError(f"语音识别服务返回错误({status_code}): {message}")
    try:
        message = response["output"]["choices"][0]["message"]
        asr_text = message["content"][0]["text"].strip()
        annotations = message.get("annotations") or []
        emotion = annotations[0].get("emotion", "neutral") if annotations else "neutral"
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise RuntimeError("语音识别服务返回结构异常") from exc
    if not asr_text:
        raise ValueError("没有识别到有效语音内容")

    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    base64_audio = encode_audio(audio_path)

    completion = client.chat.completions.create(
        model="qwen3-omni-flash",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:;base64,{base64_audio}",
                            "format": "wav",
                        },
                    },
                    {"type": "text", "text": "判断这段音频中的说话人是'成人'还是'儿童'。请仅输出这两个词中的一个。"},
                ],
            },
        ],
        modalities=["text"],
        stream=True,
        stream_options={"include_usage": True},
    )

    speaker_chunks = []
    for chunk in completion:
        if chunk.choices and chunk.choices[0].delta.content:
            speaker_chunks.append(chunk.choices[0].delta.content)
    voice_features = _normalize_speaker_type("".join(speaker_chunks))
    return asr_text, emotion, voice_features


# ==================== LLM 标签 / 音色匹配 ====================
def llm_deal(asr_text: str, emotion: str, voice_features: str):
    """
    调用LLM分析用户输入，返回 (tags, tone, voice, voice_key)
    """
    voice_features = _normalize_speaker_type(voice_features)
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    tags_list = ",".join(STORY_TAGS)
    tone_list = ",".join(TONE_OPTIONS)
    user_prompt = f"""
    用户语音内容：{asr_text}
    用户情绪：{emotion}
    说话人类型：{voice_features}
    请严格按以下要求返回JSON格式数据（仅返回JSON，无其他内容）：
    1. tags：从[{tags_list}]中选2-3个最匹配的标签（列表格式）；
    2. tone：从[{tone_list}]中选1个最匹配的语气；
    3. voice_key：格式为「{voice_features}-{{你选的tone}}」。
    4.每次挑选不同种子，增加随机性。
    示例：
    {{"tags":["勇气","冒险"], "tone":"勇敢", "voice_key":"{voice_features}-勇敢"}}
    """

    comp = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {"role": "system",
             "content": "你是儿童故事推荐助手，根据用户语音和情绪匹配故事标签与讲述语气。仅返回指定JSON格式。"},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = comp.choices[0].message.content
    result = _parse_json_object(raw)
    raw_tags = result.get("tags")
    tags = [tag for tag in raw_tags if tag in STORY_TAGS] if isinstance(raw_tags, list) else []
    tags = list(dict.fromkeys(tags))[:3]
    if not tags:
        tags = ["成长", "想象力"]
    tone = result.get("tone")
    if tone not in TONE_OPTIONS:
        tone = "温柔"
    voice_key = f"{voice_features}-{tone}"
    voice = VOICE_MAPPING.get(voice_key, DEFAULT_VOICE)
    return tags, tone, voice, voice_key


# ==================== 故事匹配 ====================
def load_stories(path=STORY_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["stories"]


def find_best_story(stories, input_tags):
    if not stories:
        return None
    input_tags = set(input_tags or [])
    best_score = -1
    candidates = []
    for s in stories:
        overlap = len(set(s.get("tags", [])) & input_tags)
        if overlap > best_score:
            best_score = overlap
            candidates = [s]
        elif overlap == best_score:
            candidates.append(s)
    if best_score <= 0:
        candidates = [
            story for story in stories
            if {"成长", "想象力"} & set(story.get("tags", []))
        ]
    return random.choice(candidates) if candidates else None


def choose_story(detected_tags, stories_path=STORY_PATH):
    stories = load_stories(stories_path)
    story = find_best_story(stories, detected_tags)
    if story is None:
        raise ValueError("故事库为空或没有可用故事")
    return story["title"], story["summary"]


# ==================== 语音合成（非流式，保留兼容） ====================
def generate_voice(story_name: str, story_content: str, voice: str, output_path: str):
    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
    synthesizer = SpeechSynthesizer(model="cosyvoice-v3-flash", voice=voice)

    if story_name and story_content:
        text = (
            f"我已为您选择合适的故事，这个故事名为《{story_name}》。"
            f"接下来我会为您讲述：{story_content}。"
            f"这个故事到这里就结束啦。"
        )
    else:
        text = "抱歉，未找到合适的故事哦。"

    audio = synthesizer.call(text)
    with open(output_path, "wb") as f:
        f.write(audio)


# ==================== 语音合成（流式） ====================
def stream_voice(story_name: str, story_content: str, voice: str) -> Iterator[bytes]:
    """
    流式 TTS：子线程合成，回调逐块放入 queue，主线程逐块 yield。
    用法: for chunk in stream_voice(name, content, voice): ...
    """
    from dashscope.audio.tts_v2 import SpeechSynthesizer as StreamSynthesizer
    from dashscope.audio.tts_v2 import ResultCallback

    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

    # 拼接朗读文本
    if story_name and story_content:
        audio_text = (
            f"我已为您选择合适的故事，这个故事名为《{story_name}》。"
            f"接下来我会为您讲述：{story_content}。"
            f"这个故事到这里就结束啦。"
        )
    else:
        audio_text = "抱歉，未找到合适的故事哦。"

    q: queue.Queue = queue.Queue()
    errors: list = []
    stopped = threading.Event()

    class _Callback(ResultCallback):
        def on_data(self, data: bytes) -> None:
            if not stopped.is_set():
                q.put(data)

        def on_error(self, message) -> None:
            errors.append(str(message))

    def _synthesize():
        try:
            synthesizer = StreamSynthesizer(
                model="cosyvoice-v3-flash",
                voice=voice,
                callback=_Callback(),
            )
            synthesizer.streaming_call(audio_text)
            synthesizer.streaming_complete()
        except Exception as e:
            errors.append(str(e))
        finally:
            q.put(None)  # 结束信号

    threading.Thread(target=_synthesize, daemon=True).start()

    try:
        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

        if errors:
            raise RuntimeError(errors[0])
    finally:
        stopped.set()


# ==================== 意图分类（打断判断） ====================
def classify_intent(text: str) -> dict:
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    prompt = f"""
用户正在听故事，中途说了这句话："{text}"

请判断用户意图，仅返回JSON，不要有其他内容：

1. 用户想换一个故事（如"换一个""不喜欢这个""讲别的"）
   → {{"intent":"change_story","response":"好的，你想听一个什么故事呢？"}}

2. 用户想停止/不听了（如"停""不要了""关掉"）
   → {{"intent":"stop","response":"好的，想听故事的时候随时叫我哦！"}}

3. 用户在说新的需求（如"我想听恐龙的故事""讲个勇敢的"）
   → {{"intent":"new_request","response":"好的，让我为你准备一下！"}}

4. 无关内容或听不清
   → {{"intent":"ignore","response":""}}
"""

    comp = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {"role": "system", "content": "你是意图分类助手。仅返回JSON，无其他内容。"},
            {"role": "user", "content": prompt},
        ],
    )
    try:
        result = _parse_json_object(comp.choices[0].message.content)
        if result.get("intent") not in {"change_story", "stop", "new_request", "ignore"}:
            raise ValueError("未知意图")
        response = result.get("response", "")
        result["response"] = response if isinstance(response, str) else ""
        return result
    except (ValueError, json.JSONDecodeError):
        return {"intent": "ignore", "response": ""}
