// ====================================================================
// 识语绘声 - 智能故事机  (全自动交互版)
// ====================================================================

// ==================== DOM ====================
const landingPage  = document.getElementById('landingPage');
const robotPage    = document.getElementById('robotPage');
const startBtn     = document.getElementById('startBtn');
const resetBtn     = document.getElementById('resetBtn');
const playBtn      = document.getElementById('playBtn');

const mouthText       = document.getElementById('mouthText');
const waveCanvas      = document.getElementById('waveCanvas');
const listenIndicator = document.getElementById('listenIndicator');
const statusLabel     = document.getElementById('statusLabel');
const storyLabel      = document.getElementById('storyLabel');
const audioPlayer     = document.getElementById('audioPlayer');

const eyeLeft  = document.querySelector('.eye.left');
const eyeRight = document.querySelector('.eye.right');

// ==================== 状态机 ====================
// IDLE → LISTENING → PROCESSING → PLAYING → (LISTENING during play) → INTERRUPTED → LISTENING → ...
let state = 'IDLE';

// ==================== 音频相关 ====================
let micStream       = null;   // 麦克风流
let micAnalyser     = null;   // 麦克风音量分析
let audioContext     = null;   // 全局 AudioContext
let mediaRecorder   = null;
let recordingSession = null;

// 音量检测参数
const SILENCE_THRESHOLD    = 15;   // 音量阈值 (0-255 范围，中心128，振幅越大值离128越远)
const SILENCE_DURATION     = 1800; // 静音持续多久认为说完了 (ms)
const MIN_SPEECH_DURATION  = 500;  // 最短说话时长 (ms)
const PLAYBACK_THRESHOLD   = 25;   // 播放时更高的阈值（过滤扬声器声音）

let isSpeaking      = false;
let silenceStart    = null;
let speechStart     = null;
let volumeCheckId   = null;
let isListening     = false;

// 当前播放相关
let playbackAnalyser = null;
let playbackSource   = null;
let waveAnimId       = null;
let currentStoryName = '';
let restartListeningTimer = null;

// ==================== 入口 ====================
startBtn.addEventListener('click', async () => {
    try {
        // 先申请麦克风权限
        micStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });

        audioContext = new (window.AudioContext || window.webkitAudioContext)();

        // 设置麦克风分析器
        const micSource = audioContext.createMediaStreamSource(micStream);
        micAnalyser = audioContext.createAnalyser();
        micAnalyser.fftSize = 512;
        micSource.connect(micAnalyser);
        // 不连接 destination，避免回声

        // 切换到机器人页面
        landingPage.classList.add('hidden');
        robotPage.classList.remove('hidden');

        // 开场问候
        await typeWriter('你好呀，想听什么故事？');
        setStatus('正在聆听...');

        // 开始监听
        startListening(false);

    } catch (err) {
        alert('需要麦克风权限才能使用哦！\n\n' + err.message);
    }
});

// ==================== 返回首页 ====================
resetBtn.addEventListener('click', () => {
    // 停止一切
    stopListening();
    stopPlayback();
    cancelTypeWriter();
    clearScheduledListening();

    // 清理状态
    if (micStream) {
        micStream.getTracks().forEach(t => t.stop());
        micStream = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }

    state = 'IDLE';
    currentStoryName = '';
    mouthText.innerHTML = '';
    storyLabel.classList.add('hidden');
    playBtn.classList.add('hidden');
    setStatus('');
    hideAllMouth();

    // 切换页面
    robotPage.classList.add('hidden');
    landingPage.classList.remove('hidden');
});

// ==================== 打字机效果 ====================
let typeWriterTimer = null;
let typeWriterCursorTimer = null;
let typeWriterResolve = null;

function typeWriter(text, speed = 80) {
    return new Promise((resolve) => {
        cancelTypeWriter();
        typeWriterResolve = resolve;

        showMouth('text');
        mouthText.innerHTML = '';

        let i = 0;
        const cursor = document.createElement('span');
        cursor.className = 'cursor';

        function tick() {
            if (i < text.length) {
                mouthText.textContent = text.slice(0, i + 1);
                mouthText.appendChild(cursor);
                i++;
                typeWriterTimer = setTimeout(tick, speed);
            } else {
                // 打字完成，光标再闪一会儿后消失
                typeWriterTimer = null;
                typeWriterCursorTimer = setTimeout(() => {
                    const c = mouthText.querySelector('.cursor');
                    if (c) c.remove();
                    typeWriterCursorTimer = null;
                    typeWriterResolve = null;
                    resolve();
                }, 1200);
            }
        }
        tick();
    });
}

function cancelTypeWriter() {
    if (typeWriterTimer) {
        clearTimeout(typeWriterTimer);
        typeWriterTimer = null;
    }
    if (typeWriterCursorTimer) {
        clearTimeout(typeWriterCursorTimer);
        typeWriterCursorTimer = null;
    }
    if (typeWriterResolve) {
        typeWriterResolve();
        typeWriterResolve = null;
    }
    const c = mouthText.querySelector('.cursor');
    if (c) c.remove();
}

// ==================== 嘴巴区域切换 ====================
function hideAllMouth() {
    mouthText.classList.remove('hidden');
    waveCanvas.classList.add('hidden');
    listenIndicator.classList.add('hidden');
    listenIndicator.classList.remove('speaking');
    mouthText.innerHTML = '';
}

function showMouth(mode) {
    // mode: 'text' | 'wave' | 'listen'
    mouthText.classList.add('hidden');
    waveCanvas.classList.add('hidden');
    listenIndicator.classList.add('hidden');
    listenIndicator.classList.remove('speaking');

    if (mode === 'text')   mouthText.classList.remove('hidden');
    if (mode === 'wave')   waveCanvas.classList.remove('hidden');
    if (mode === 'listen') listenIndicator.classList.remove('hidden');
}

// ==================== 眼睛状态 ====================
function setEyeState(s) {
    eyeLeft.classList.remove('listening', 'processing');
    eyeRight.classList.remove('listening', 'processing');
    if (s) {
        eyeLeft.classList.add(s);
        eyeRight.classList.add(s);
    }
}

// ==================== 状态标签 ====================
function setStatus(text) {
    statusLabel.textContent = text;
}

function clearScheduledListening() {
    if (restartListeningTimer) {
        clearTimeout(restartListeningTimer);
        restartListeningTimer = null;
    }
}

function scheduleListening(duringPlayback, delay) {
    clearScheduledListening();
    restartListeningTimer = setTimeout(() => {
        restartListeningTimer = null;
        startListening(duringPlayback);
    }, delay);
}

// ==================== 监听逻辑 ====================
function startListening(duringPlayback = false) {
    if (isListening || !micStream || !micAnalyser) return;
    clearScheduledListening();
    isListening = true;
    state = duringPlayback ? 'PLAYING' : 'LISTENING';

    if (!duringPlayback) {
        showMouth('listen');
        setEyeState('listening');
    }

    isSpeaking = false;
    silenceStart = null;
    speechStart = null;

    // 启动 MediaRecorder（准备随时录制）
    if (micStream) {
        const mimeType = getSupportedMimeType();
        const recorder = mimeType
            ? new MediaRecorder(micStream, { mimeType })
            : new MediaRecorder(micStream);
        const session = {
            chunks: [],
            shouldSubmit: false,
            duringPlayback,
        };
        mediaRecorder = recorder;
        recordingSession = session;
        recorder.ondataavailable = (e) => {
            if (e.data.size > 0) session.chunks.push(e.data);
        };
        recorder.onstop = () => {
            const blob = new Blob(session.chunks, { type: recorder.mimeType });
            if (recordingSession === session) recordingSession = null;
            if (session.shouldSubmit && blob.size > 0) {
                onSpeechCaptured(blob, session.duringPlayback);
            }
        };
    }

    // 开始音量检测循环
    startVolumeCheck(duringPlayback);
}

function stopListening(discardRecording = true) {
    isListening = false;
    if (volumeCheckId) {
        cancelAnimationFrame(volumeCheckId);
        volumeCheckId = null;
    }
    if (recordingSession && discardRecording) {
        recordingSession.shouldSubmit = false;
    }
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    isSpeaking = false;
    silenceStart = null;
    speechStart = null;
}

function startVolumeCheck(duringPlayback) {
    const threshold = duringPlayback ? PLAYBACK_THRESHOLD : SILENCE_THRESHOLD;
    const dataArray = new Uint8Array(micAnalyser.frequencyBinCount);

    function check() {
        if (!isListening) return;

        micAnalyser.getByteTimeDomainData(dataArray);

        // 计算音量（偏离128的幅度）
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            sum += Math.abs(dataArray[i] - 128);
        }
        const volume = sum / dataArray.length;

        if (volume > threshold) {
            // 有声音
            if (!isSpeaking) {
                // 刚开始说话
                isSpeaking = true;
                speechStart = Date.now();
                silenceStart = null;

                // 开始录音
                if (mediaRecorder && mediaRecorder.state === 'inactive') {
                    mediaRecorder.start(100);
                }

                if (!duringPlayback) {
                    listenIndicator.classList.add('speaking');
                    setStatus('正在听你说...');
                }
            }
            silenceStart = null;
        } else {
            // 安静
            if (isSpeaking) {
                if (!silenceStart) {
                    silenceStart = Date.now();
                } else if (Date.now() - silenceStart > SILENCE_DURATION) {
                    // 安静够久了，认为说完了
                    const speechDuration = Date.now() - speechStart;
                    if (speechDuration > MIN_SPEECH_DURATION) {
                        // 有效语音
                        isSpeaking = false;
                        if (mediaRecorder && mediaRecorder.state === 'recording') {
                            if (recordingSession) recordingSession.shouldSubmit = true;
                            stopListening(false); // 触发 onstop → onSpeechCaptured
                        }
                        if (!duringPlayback) {
                            listenIndicator.classList.remove('speaking');
                        }
                        return; // 等 onstop 回调处理
                    } else {
                        // 太短了，忽略
                        isSpeaking = false;
                        silenceStart = null;
                        if (mediaRecorder && mediaRecorder.state === 'recording') {
                            stopListening(true);
                            scheduleListening(duringPlayback, 100);
                        }
                    }
                }
            }
        }

        volumeCheckId = requestAnimationFrame(check);
    }

    volumeCheckId = requestAnimationFrame(check);
}

function getSupportedMimeType() {
    const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    for (const t of types) {
        if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
}

// ==================== 语音捕获后处理 ====================
async function onSpeechCaptured(blob, duringPlayback) {
    stopListening();

    if (duringPlayback) {
        // 播放中被打断 → 检测意图
        state = 'INTERRUPTED';
        setStatus('正在理解你说的...');

        try {
            const formData = new FormData();
            formData.append('file', blob, 'interrupt.webm');

            const res = await fetch('/api/intent', { method: 'POST', body: formData });
            if (!res.ok) throw new Error('意图识别请求失败');

            const data = await res.json();
            console.log('意图识别结果:', data);

            if (data.intent === 'ignore') {
                // 无关内容，继续播放
                startListening(true);
                return;
            }

            // 有效意图 → 停止播放
            stopPlayback();

            if (data.intent === 'change_story' || data.intent === 'stop') {
                await typeWriter(data.response);
                if (data.intent === 'change_story') {
                    setStatus('正在聆听...');
                    // 等一下再开始监听，让用户看完文字
                    scheduleListening(false, 1500);
                } else {
                    setStatus('待机中，随时叫我哦');
                    scheduleListening(false, 2000);
                }
            } else if (data.intent === 'new_request') {
                // 用户直接说了新需求，用这段语音走完整流程
                await typeWriter(data.response);
                // 上传这段音频走 process 流程
                await uploadAndProcess(blob);
            }

        } catch (err) {
            console.error('意图识别失败:', err);
            // 识别失败，继续播放
            startListening(true);
        }

    } else {
        // 非播放中 → 正常流程
        state = 'PROCESSING';
        setEyeState('processing');
        setStatus('');
        await typeWriter('让我想想...');
        await uploadAndProcess(blob);
    }
}

// ==================== 上传 & 处理 ====================
async function uploadAndProcess(blob) {
    state = 'PROCESSING';
    setEyeState('processing');

    try {
        // 上传
        const formData = new FormData();
        formData.append('file', blob, 'recording.webm');

        const uploadRes = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!uploadRes.ok) throw new Error('上传失败');
        const { task_id } = await uploadRes.json();

        // SSE 处理
        await processSSE(task_id);

    } catch (err) {
        console.error('处理失败:', err);
        await typeWriter('出了点小问题，再试一次吧');
        setStatus('正在聆听...');
        scheduleListening(false, 2000);
    }
}

// ==================== SSE 事件流 ====================
function processSSE(taskId) {
    return new Promise((resolve, reject) => {
        const source = new EventSource(`/api/process/${taskId}`);

        source.addEventListener('progress', async (e) => {
            const data = JSON.parse(e.data);
            cancelTypeWriter();
            await typeWriter(data.message);
        });

        source.addEventListener('recognition', (e) => {
            const data = JSON.parse(e.data);
            console.log('识别结果:', data);
        });

        source.addEventListener('matching', (e) => {
            const data = JSON.parse(e.data);
            console.log('匹配结果:', data);
        });

        source.addEventListener('story', async (e) => {
            const data = JSON.parse(e.data);
            if (data.name) {
                currentStoryName = data.name;
                storyLabel.textContent = data.name;
                storyLabel.classList.remove('hidden');
            }
        });

        source.addEventListener('audio', (e) => {
            const data = JSON.parse(e.data);
            startPlayback(data.url);
        });

        source.addEventListener('done', (e) => {
            source.close();
            resolve();
        });

        source.addEventListener('error', (e) => {
            source.close();
            if (e.data) {
                const data = JSON.parse(e.data);
                reject(new Error(data.message));
            } else {
                reject(new Error('连接中断'));
            }
        });
    });
}

// ==================== 音频播放 + 波形可视化 ====================
function startPlayback(url) {
    state = 'PLAYING';
    setEyeState('');
    setStatus('正在讲故事...');

    audioPlayer.src = url;
    audioPlayer.crossOrigin = 'anonymous';
    playBtn.classList.add('hidden');

    // 设置 Web Audio 分析器
    if (!playbackSource) {
        playbackSource = audioContext.createMediaElementSource(audioPlayer);
    }
    if (playbackAnalyser) {
        playbackAnalyser.disconnect();
    }
    playbackAnalyser = audioContext.createAnalyser();
    playbackAnalyser.fftSize = 256;

    playbackSource.disconnect();
    playbackSource.connect(playbackAnalyser);
    playbackAnalyser.connect(audioContext.destination);

    // 显示波浪
    showMouth('wave');
    resizeCanvas();
    startWaveAnimation();

    audioPlayer.play().catch(err => {
        console.error('播放失败:', err);
        state = 'PAUSED';
        stopWaveAnimation();
        setStatus('浏览器阻止了自动播放');
        playBtn.classList.remove('hidden');
    });

    // 播放结束
    audioPlayer.onended = () => {
        stopListening(true);
        clearScheduledListening();
        stopWaveAnimation();
        state = 'IDLE';
        setStatus('');
        typeWriter('故事讲完啦，还想听什么？').then(() => {
            setStatus('正在聆听...');
            scheduleListening(false, 0);
        });
    };

    // 播放期间同时监听
    beginPlaybackListening();
}

playBtn.addEventListener('click', async () => {
    try {
        await audioPlayer.play();
        state = 'PLAYING';
        playBtn.classList.add('hidden');
        setStatus('正在讲故事...');
        startWaveAnimation();
        beginPlaybackListening();
    } catch (err) {
        console.error('手动播放仍然失败:', err);
        setStatus('播放失败，请检查网络后重试');
    }
});

audioPlayer.addEventListener('error', () => {
    stopListening(true);
    stopWaveAnimation();
    state = 'IDLE';
    setStatus('音频加载失败，请重新说一次');
    playBtn.classList.add('hidden');
    scheduleListening(false, 1500);
});

function beginPlaybackListening() {
    clearScheduledListening();
    restartListeningTimer = setTimeout(() => {
        restartListeningTimer = null;
        if (state === 'PLAYING') {
            startListening(true);
        }
    }, 1500);
}

function stopPlayback() {
    clearScheduledListening();
    audioPlayer.pause();
    audioPlayer.currentTime = 0;
    audioPlayer.removeAttribute('src');
    audioPlayer.load();
    audioPlayer.onended = null;
    playBtn.classList.add('hidden');
    stopWaveAnimation();
}

// ==================== 正弦波动画 ====================
function resizeCanvas() {
    const rect = waveCanvas.parentElement.getBoundingClientRect();
    waveCanvas.width = rect.width * window.devicePixelRatio;
    waveCanvas.height = rect.height * window.devicePixelRatio;
    waveCanvas.style.width = rect.width + 'px';
    waveCanvas.style.height = rect.height + 'px';
}

function startWaveAnimation() {
    const ctx = waveCanvas.getContext('2d');
    const bufferLength = playbackAnalyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
        waveAnimId = requestAnimationFrame(draw);

        playbackAnalyser.getByteTimeDomainData(dataArray);

        const W = waveCanvas.width;
        const H = waveCanvas.height;
        const dpr = window.devicePixelRatio || 1;

        ctx.clearRect(0, 0, W, H);

        // 画多条正弦波
        const colors = [
            'rgba(92, 207, 255, 0.9)',
            'rgba(92, 207, 255, 0.4)',
            'rgba(191, 239, 255, 0.3)',
        ];
        const lineWidths = [3 * dpr, 2 * dpr, 1.5 * dpr];

        colors.forEach((color, ci) => {
            ctx.beginPath();
            ctx.strokeStyle = color;
            ctx.lineWidth = lineWidths[ci];
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';

            const sliceWidth = W / bufferLength;
            let x = 0;

            for (let i = 0; i < bufferLength; i++) {
                // 将音频数据与正弦波混合
                const v = dataArray[i] / 128.0; // 0~2，静音时约1
                const offset = (v - 1) * H * 0.4; // 振幅缩放
                // 叠加一个缓慢移动的基础波
                const wave = Math.sin((i / bufferLength) * Math.PI * (3 + ci) + Date.now() * 0.002 * (1 + ci * 0.3)) * H * 0.05 * (ci + 1);
                const y = H / 2 + offset + wave;

                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);

                x += sliceWidth;
            }

            ctx.stroke();
        });

        // 中心发光效果
        const gradient = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W * 0.4);
        gradient.addColorStop(0, 'rgba(92, 207, 255, 0.06)');
        gradient.addColorStop(1, 'rgba(92, 207, 255, 0)');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, W, H);
    }

    draw();
}

function stopWaveAnimation() {
    if (waveAnimId) {
        cancelAnimationFrame(waveAnimId);
        waveAnimId = null;
    }
}

// ==================== 窗口 resize ====================
window.addEventListener('resize', () => {
    if (!waveCanvas.classList.contains('hidden')) {
        resizeCanvas();
    }
});
