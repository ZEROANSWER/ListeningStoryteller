import uuid
import json
import asyncio
import os
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydub import AudioSegment

from core import recognization, llm_deal, choose_story, classify_intent, stream_voice

# ==================== 初始化 ====================
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", 10 * 1024 * 1024))
TASK_TTL_SECONDS = int(os.getenv("TASK_TTL_SECONDS", 30 * 60))
READ_CHUNK_BYTES = 1024 * 1024
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="识语绘声 - 智能故事机")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

tasks: dict = {}
tts_params: dict = {}  # 存储待流式合成的 TTS 参数


def _cleanup_expired_tasks() -> None:
    """清除长时间未消费的内存任务和临时音频。"""
    cutoff = time.time() - TASK_TTL_SECONDS
    expired_tasks = [key for key, value in tasks.items() if value["created_at"] < cutoff]
    for task_id in expired_tasks:
        task = tasks.pop(task_id, None)
        if task:
            Path(task["wav_path"]).unlink(missing_ok=True)

    expired_tts = [key for key, value in tts_params.items() if value["created_at"] < cutoff]
    for task_id in expired_tts:
        tts_params.pop(task_id, None)


async def _save_upload(file: UploadFile, target: Path) -> None:
    """分块保存上传内容，避免一次性读入内存并限制文件大小。"""
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(READ_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    limit_mb = MAX_AUDIO_BYTES / (1024 * 1024)
                    raise HTTPException(status_code=413, detail=f"音频文件不能超过 {limit_mb:g} MB")
                output.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传的音频为空")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def _convert_to_wav(raw_path: Path, wav_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("服务器未安装 ffmpeg，无法转换浏览器录音")
    audio_seg = AudioSegment.from_file(str(raw_path))
    audio_seg.set_channels(1).set_frame_rate(16000).export(str(wav_path), format="wav")


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="服务器未安装 ffmpeg，暂时无法处理浏览器录音")


# ==================== 页面入口 ====================
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    """用于本地检查和部署平台健康探测，不调用外部模型。"""
    ffmpeg_ready = shutil.which("ffmpeg") is not None
    api_key_ready = bool(os.getenv("DASHSCOPE_API_KEY"))
    return {
        "status": "ready" if ffmpeg_ready and api_key_ready else "degraded",
        "ffmpeg": ffmpeg_ready,
        "api_key_configured": api_key_ready,
    }


# ==================== 1. 上传音频 ====================
@app.post("/api/upload")
async def upload_audio(file: UploadFile = File(...)):
    _cleanup_expired_tasks()
    _ensure_ffmpeg()
    task_id = uuid.uuid4().hex
    raw_path = UPLOAD_DIR / f"{task_id}_raw"
    wav_path = UPLOAD_DIR / f"{task_id}.wav"

    try:
        await _save_upload(file, raw_path)
        await asyncio.to_thread(_convert_to_wav, raw_path, wav_path)
    except HTTPException:
        raise
    except Exception as e:
        wav_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"音频转换失败: {e}")
    finally:
        raw_path.unlink(missing_ok=True)

    tasks[task_id] = {
        "status": "uploaded",
        "wav_path": str(wav_path),
        "created_at": time.time(),
    }
    return {"task_id": task_id}


# ==================== 2. SSE 流式处理 ====================
@app.get("/api/process/{task_id}")
async def process_stream(task_id: str):
    _cleanup_expired_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "uploaded":
        raise HTTPException(status_code=409, detail="任务正在处理或已经处理")

    task["status"] = "processing"
    wav_path = task["wav_path"]

    async def event_generator():
        def sse(event: str, data: dict):
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            # Step 1: 语音识别
            yield sse("progress", {"step": 1, "total": 4, "message": "正在听你说了什么..."})
            asr_text, emotion, voice_features = await asyncio.to_thread(
                recognization, wav_path
            )
            yield sse("recognition", {
                "text": asr_text,
                "emotion": emotion,
                "speaker_type": voice_features,
            })

            # Step 2: 标签匹配
            yield sse("progress", {"step": 2, "total": 4, "message": "正在为你挑选故事..."})
            tags, tone, voice, voice_key = await asyncio.to_thread(
                llm_deal, asr_text, emotion, voice_features
            )
            yield sse("matching", {
                "tags": tags,
                "tone": tone,
                "voice": voice,
                "voice_key": voice_key,
            })

            # Step 3: 故事匹配
            yield sse("progress", {"step": 3, "total": 4, "message": "找到了一个好故事..."})
            story_name, story_content = await asyncio.to_thread(choose_story, tags)
            yield sse("story", {
                "name": story_name,
                "content": story_content,
            })

            # Step 4: 准备流式 TTS（不等生成完毕，只存参数）
            yield sse("progress", {"step": 4, "total": 4, "message": "马上开始讲故事..."})

            tts_params[task_id] = {
                "story_name": story_name,
                "story_content": story_content,
                "voice": voice,
                "created_at": time.time(),
            }

            # 返回流式音频 URL
            yield sse("audio", {
                "url": f"/api/stream-audio/{task_id}",
            })

            yield sse("done", {"message": "开始讲故事啦！"})

        except Exception as e:
            yield sse("error", {"message": f"出了点小问题: {str(e)}"})
        finally:
            tasks.pop(task_id, None)
            Path(wav_path).unlink(missing_ok=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 3. 流式音频端点 ====================
@app.get("/api/stream-audio/{task_id}")
async def stream_audio(task_id: str):
    """
    浏览器 <audio> 请求此 URL 时，边合成边返回 MP3 数据，实现即时播放。
    """
    _cleanup_expired_tasks()
    if task_id not in tts_params:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    params = tts_params.pop(task_id)  # 取出后删除，防止重复请求

    def generate():
        for chunk in stream_voice(
            params["story_name"],
            params["story_content"],
            params["voice"],
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
        },
    )


# ==================== 4. 获取生成的音频（保留兼容） ====================
@app.get("/api/audio/{filename}")
async def get_audio(filename: str):
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or path.suffix.lower() != ".mp3" or not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(str(path), media_type="audio/mpeg", filename=filename)


# ==================== 5. 意图识别（播放中打断） ====================
@app.post("/api/intent")
async def detect_intent(file: UploadFile = File(...)):
    """播放过程中检测用户语音意图"""
    _cleanup_expired_tasks()
    _ensure_ffmpeg()
    task_id = uuid.uuid4().hex
    raw_path = UPLOAD_DIR / f"{task_id}_intent_raw"
    wav_path = UPLOAD_DIR / f"{task_id}_intent.wav"

    try:
        await _save_upload(file, raw_path)
        await asyncio.to_thread(_convert_to_wav, raw_path, wav_path)
    except HTTPException:
        raise
    except Exception as e:
        wav_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"音频转换失败: {e}")
    finally:
        raw_path.unlink(missing_ok=True)

    try:
        asr_text, emotion, voice_features = await asyncio.to_thread(
            recognization, str(wav_path)
        )
        intent_result = await asyncio.to_thread(classify_intent, asr_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"意图识别失败: {e}")
    finally:
        wav_path.unlink(missing_ok=True)

    return {
        "text": asr_text,
        "emotion": emotion,
        "voice_features": voice_features,
        "intent": intent_result["intent"],
        "response": intent_result["response"],
    }


# ==================== 启动 ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=True)
