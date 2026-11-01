"""主窗口的模拟后端，用于前端独立开发与预览。

方法名、参数、返回结构与 docs/ui-api.md 完全一致，最终会被 umless/webapi.py 的 Api 替换。
数据由固定种子生成，保证截图稳定；耗时操作用后台线程模拟 job（带 progress）。
"""

from __future__ import annotations

import copy
import math
import random
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------- 示例语料

# 每条：应用、场景、强度、原文分段标注、润色稿、录音时长
_UTTERANCES: list[dict] = [
    {
        "app": "Typora.exe", "scene": "document", "intensity": "moderate", "dur": 9.6,
        "segs": [["嗯", "filler"], ["我现在想", "keep"], ["就是", "filler"], ["测试一下这个", "keep"],
                 ["这个", "repeat"], ["新的润色功能，", "keep"], ["然后", "filler"],
                 ["看看它能不能把口癖都去掉", "keep"]],
        "out": "我现在想测试一下这个新的润色功能，看看它能不能把口癖都去掉。",
    },
    {
        "app": "WeChat.exe", "scene": "chat", "intensity": "moderate", "dur": 5.2,
        "segs": [["那个", "filler"], ["我晚上可能", "keep"], ["可能", "repeat"],
                 ["会晚一点到，你们先吃，不用等我了", "keep"], ["啊", "filler"]],
        "out": "我晚上可能会晚一点到，你们先吃，不用等我了。",
    },
    {
        "app": "Code.exe", "scene": "code", "intensity": "faithful", "dur": 8.1,
        "segs": [["帮我看一下", "keep"], ["呃", "filler"], ["这个函数为什么会返回 null，", "keep"],
                 ["就是", "filler"], ["在", "repeat"], ["在用户没有登录的时候", "keep"]],
        "out": "帮我看一下这个函数为什么会返回 null，在用户没有登录的时候。",
    },
    {
        "app": "Cursor.exe", "scene": "code", "intensity": "faithful", "dur": 7.4,
        "segs": [["把这个", "repeat"], ["嗯", "filler"], ["把这个组件改成用 useMemo 缓存，", "keep"],
                 ["然后", "filler"], ["依赖数组里加上 userId", "keep"]],
        "out": "把这个组件改成用 useMemo 缓存，依赖数组里加上 userId。",
    },
    {
        "app": "OUTLOOK.EXE", "scene": "email", "intensity": "restructure", "dur": 14.8,
        "segs": [["王老师您好，", "keep"], ["嗯", "filler"], ["就是", "filler"], ["我想跟您约一下", "keep"],
                 ["下周三，不对，", "correction"], ["应该是下周四下午，讨论一下论文的实验部分，", "keep"],
                 ["然后", "filler"], ["看您有没有时间", "cut"]],
        "out": "王老师您好！想约您下周四下午讨论一下论文的实验部分，不知您是否方便？",
    },
    {
        "app": "Feishu.exe", "scene": "chat", "intensity": "moderate", "dur": 9.0,
        "segs": [["这个需求", "keep"], ["其实", "filler"], ["我觉得", "repeat"],
                 ["我觉得可以先做一个最小版本，", "keep"], ["对吧，", "filler"], ["先跑通再说", "keep"]],
        "out": "这个需求我觉得可以先做一个最小版本，先跑通再说。",
    },
    {
        "app": "Obsidian.exe", "scene": "document", "intensity": "restructure", "dur": 21.3,
        "segs": [["今天的实验结果", "keep"], ["嗯", "filler"], ["基线模型在验证集上是百分之七十二，", "keep"],
                 ["然后", "filler"], ["加了数据增强之后到了百分之七十六，", "keep"], ["但是", "keep"],
                 ["那个", "filler"], ["训练时间多了一倍，", "keep"], ["所以说", "filler"],
                 ["下一步要看看能不能用更小的增强策略", "keep"]],
        "out": "今天的实验结果：\n- 基线模型：验证集 72%\n- 加入数据增强：76%，但训练时间增加一倍\n\n下一步：尝试更轻量的增强策略。",
    },
    {
        "app": "WindowsTerminal.exe", "scene": "code", "intensity": "faithful", "dur": 4.6,
        "segs": [["git 提交一下，", "keep"], ["嗯", "filler"], ["提交信息写修复历史页分页的问题", "keep"]],
        "out": "git 提交一下，提交信息写：修复历史页分页的问题。",
    },
    {
        "app": "Notion.exe", "scene": "document", "intensity": "restructure", "dur": 18.2,
        "segs": [["会议纪要，", "keep"], ["呃", "filler"], ["第一个是", "keep"], ["是", "repeat"],
                 ["下个月上线新版本，", "keep"], ["然后", "filler"], ["第二个是测试那边需要再加两个人，", "keep"],
                 ["我是说", "correction"], ["加两个外包", "keep"]],
        "out": "会议纪要：\n1. 下个月上线新版本；\n2. 测试团队需要再增加两名外包人员。",
    },
    {
        "app": "QQ.exe", "scene": "chat", "intensity": "moderate", "dur": 6.3,
        "segs": [["哈哈好的，", "keep"], ["那个", "filler"], ["周末一起去爬山吧，", "keep"], ["就是", "filler"],
                 ["天气预报说周六是晴天", "keep"]],
        "out": "哈哈好的，周末一起去爬山吧，天气预报说周六是晴天。",
    },
    {
        "app": "DingTalk.exe", "scene": "chat", "intensity": "moderate", "dur": 4.1,
        "segs": [["收到，", "keep"], ["我", "repeat"], ["我今天下班前把周报发出来", "keep"]],
        "out": "收到，我今天下班前把周报发出来。",
    },
    {
        "app": "WINWORD.EXE", "scene": "document", "intensity": "restructure", "dur": 12.7,
        "segs": [["本文提出了一种", "keep"], ["呃", "filler"], ["基于", "repeat"], ["基于对比学习的方法，", "keep"],
                 ["然后", "filler"], ["在三个公开数据集上验证了它的有效性", "keep"]],
        "out": "本文提出了一种基于对比学习的方法，并在三个公开数据集上验证了其有效性。",
    },
    {
        "app": "chrome.exe", "scene": "default", "intensity": "moderate", "dur": 3.2,
        "segs": [["搜索一下", "keep"], ["那个", "filler"], ["明天的天气", "keep"]],
        "out": "搜索一下明天的天气",
    },
    {
        "app": "slack.exe", "scene": "chat", "intensity": "moderate", "dur": 6.9,
        "segs": [["I'll push the fix", "keep"], ["嗯", "filler"], ["今晚，", "cut"], ["不对，", "correction"],
                 ["明天早上发 PR 给你 review", "keep"]],
        "out": "I'll push the fix，明天早上发 PR 给你 review。",
    },
    {
        "app": "Typora.exe", "scene": "document", "intensity": "moderate", "dur": 16.4,
        "segs": [["这周主要做了三件事，", "keep"], ["嗯", "filler"], ["第一是把识别模型换成了 SenseVoice，", "keep"],
                 ["然后", "filler"], ["第二是", "keep"], ["就是", "filler"], ["加了场景识别，", "keep"],
                 ["第三是", "keep"], ["其实", "filler"], ["还在写报告页面", "keep"]],
        "out": "这周主要做了三件事：一是把识别模型换成了 SenseVoice；二是加了场景识别；三是还在写报告页面。",
    },
]

_APPS_SCENE = {u["app"]: u["scene"] for u in _UTTERANCES}

# Android 模拟数据：把桌面进程名换成对应 App 的包名
_ANDROID_APPS = {
    "Typora.exe": "md.obsidian", "WeChat.exe": "com.tencent.mm", "Code.exe": "com.github.android",
    "Cursor.exe": "com.termux", "OUTLOOK.EXE": "com.microsoft.office.outlook", "Feishu.exe": "com.ss.android.lark",
    "Obsidian.exe": "md.obsidian", "WindowsTerminal.exe": "com.termux", "Notion.exe": "notion.id",
    "QQ.exe": "com.tencent.mobileqq", "DingTalk.exe": "com.alibaba.android.rimet", "WINWORD.EXE": "cn.wps.moffice_eng",
    "chrome.exe": "com.android.chrome", "slack.exe": "com.Slack",
}

_PROVIDERS = [
    {"id": "ollama", "name": "Ollama（本地）", "base_url": "http://localhost:11434/v1",
     "models": ["qwen3.5:2b", "qwen3.5:4b", "gemma3:4b"]},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-v4-flash", "deepseek-v4"]},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "models": ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3.2"]},
    {"id": "dashscope", "name": "阿里百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-flash", "qwen-plus"]},
    {"id": "zhipu", "name": "智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4.5-flash", "glm-4.5-air"]},
]

_BUILTIN_FILLERS = [
    "嗯", "呃", "额", "啊", "哦", "噢", "诶", "欸", "唉",
    "然后", "就是", "就是说", "那个", "这个", "其实", "反正", "对吧", "的话",
    "怎么说呢", "所以说", "然后呢", "那么", "基本上", "相当于说",
]

_TOPICS_A = [
    {"id": "t1", "title": "用 1 分钟介绍你正在做的 Umless 项目", "category": "项目介绍",
     "prompt": "假设你在组会上向导师介绍这个项目：它解决什么问题、目前做到哪一步、接下来打算做什么。",
     "hint": "背景 → 做了什么 → 下一步", "duration": 60, "personal": True},
    {"id": "t2", "title": "向非技术朋友解释什么是对比学习", "category": "讲解概念",
     "prompt": "你最近的论文用到了对比学习。用一个生活中的比喻，让没有技术背景的朋友听懂它的核心思想。",
     "hint": "比喻 → 核心思想 → 一个例子", "duration": 90, "personal": True},
    {"id": "t3", "title": "汇报本周进展与遇到的阻碍", "category": "工作汇报",
     "prompt": "向团队简要同步：本周完成了什么、卡在哪里、需要谁的帮助。",
     "hint": "结论 → 进展 → 阻碍 → 求助", "duration": 60, "personal": False},
    {"id": "t4", "title": "讲一次你解决棘手问题的经历", "category": "面试",
     "prompt": "面试官问：说一个你遇到过的最难的技术问题，你是怎么解决的？",
     "hint": "情境 → 任务 → 行动 → 结果", "duration": 90, "personal": False},
    {"id": "t5", "title": "远程办公利大于弊吗？", "category": "观点表达",
     "prompt": "选择一个立场，给出两到三个理由，并回应一个可能的反对意见。",
     "hint": "立场 → 理由 → 回应反方 → 总结", "duration": 60, "personal": False},
]

_TOPICS_B = [
    {"id": "t6", "title": "说说 SenseVoice 和 Qwen3-ASR 该怎么选", "category": "技术讲解",
     "prompt": "你对比过两个识别模型。向同事解释它们的差异，并给出你的推荐。",
     "hint": "结论 → 关键差异 → 适用场景", "duration": 60, "personal": True},
    {"id": "t7", "title": "向导师申请延期一周提交论文初稿", "category": "沟通协商",
     "prompt": "说明原因、目前进度，以及延期后能交付的内容，语气礼貌但不卑微。",
     "hint": "请求 → 原因 → 进度 → 承诺", "duration": 60, "personal": True},
    {"id": "t8", "title": "介绍一本最近读过的书", "category": "分享",
     "prompt": "用一分钟讲清楚这本书讲了什么、哪一点打动你、推荐给谁。",
     "hint": "一句话概括 → 打动你的点 → 推荐理由", "duration": 60, "personal": False},
    {"id": "t9", "title": "你为什么想加入这个团队？", "category": "面试",
     "prompt": "面试的最后，对方问你为什么选择他们。给出真诚且具体的回答。",
     "hint": "看重什么 → 为什么是这里 → 你能带来什么", "duration": 60, "personal": False},
    {"id": "t10", "title": "解释为什么要写单元测试", "category": "讲解概念",
     "prompt": "新同事觉得写测试浪费时间。用具体的例子说服他。",
     "hint": "观点 → 例子 → 代价对比", "duration": 90, "personal": False},
]

_REPORT_TEXT = {
    "summary": "整体表达清楚，约时间、提需求时大多能一句话讲清目的。主要问题是「就是」「然后」作为填充词出现得太频繁"
               "（每百字约 2 次），以及描述技术细节时容易边想边说、出现改口。建议停顿时用沉默代替填充词，"
               "内容超过三点时先报数量再展开。",
    "strengths": [
        "能先说结论再展开，约时间、提需求时目的明确",
        "技术名词使用准确，中英混说自然流畅",
        "聊天场景句子简短，语气友好不生硬",
    ],
    "issues": [
        {"title": "「就是」作为填充词过多", "type": "filler",
         "evidence": ["我现在有个问题就是就是这个接口一直超时", "就是我觉得我们可以先做一个最小版本"],
         "advice": "「就是」常出现在想下一句的时候。试着在这里停顿半秒、不出声，比填充词更显从容。"},
        {"title": "关键信息边想边说，出现改口", "type": "correction",
         "evidence": ["我想跟您约一下下周三，不对，应该是下周四下午"],
         "advice": "涉及时间、数字、人名的信息，开口前先在心里确认一遍。"},
        {"title": "长句缺少结构，连用「然后」", "type": "structure",
         "evidence": ["我们先跑了基线然后发现效果不太好然后又换了数据增强然后好了一点"],
         "advice": "超过三件事时先说「有三点」，再逐条展开；用「首先 / 接着 / 最后」代替连续的「然后」。"},
    ],
    "examples": [
        {"original": "嗯就是我觉得这个方案可能也许还是有一点问题的吧",
         "better": "我觉得这个方案有一个问题：",
         "why": "去掉填充词和层层叠加的模糊限定（可能、也许、有一点、吧），观点更有力量。"},
        {"original": "我们先跑了基线然后发现效果不太好然后又换了数据增强然后效果好了一点",
         "better": "我们先跑了基线，效果一般；加入数据增强后，准确率提升了 4 个点。",
         "why": "用分号切开两个阶段，因果关系一目了然；给出具体数字比「好了一点」更可信。"},
        {"original": "王老师您好就是我想跟您约一下下周三不对下周四下午",
         "better": "王老师您好，想约您下周四下午聊聊论文的实验部分，您看方便吗？",
         "why": "先讲目的和时间，再询问对方是否方便；关键信息一次说对，避免改口。"},
    ],
    "personal_fillers": [{"word": "大概就是", "added": True}, {"word": "说白了", "added": False},
                         {"word": "我觉得吧", "added": True}],
    "terms": [{"wrong": "CLOUD COD", "right": "Claude Code", "added": False},
              {"wrong": "派 torch", "right": "PyTorch", "added": False},
              {"wrong": "get hub", "right": "GitHub", "added": True}],
    "filler_stats": [{"word": "就是", "count": 12, "per100": 2.1}, {"word": "然后", "count": 9, "per100": 1.6},
                     {"word": "那个", "count": 6, "per100": 1.0}, {"word": "嗯", "count": 5, "per100": 0.9},
                     {"word": "其实", "count": 3, "per100": 0.5}, {"word": "对吧", "count": 2, "per100": 0.3}],
}

_PRACTICE_SAMPLES = [
    {
        "topic": "用 1 分钟介绍你正在做的 Umless 项目", "category": "项目介绍", "duration_s": 56.8,
        "segs": [["嗯", "filler"], ["我最近在做一个叫 Umless 的项目，", "keep"], ["就是", "filler"],
                 ["一个语音输入工具。", "keep"], ["然后", "filler"], ["它的特点是", "keep"], ["是", "repeat"],
                 ["会帮你分析说话时的口癖，", "keep"], ["嗯", "filler"],
                 ["目前已经做完了识别和润色，每天都在用。", "keep"], ["然后", "filler"], ["那个", "filler"],
                 ["下一步打算做", "keep"], ["做", "repeat"], ["每日的诊断报告和口语练习", "keep"]],
        "scores": {"structure": 70, "logic": 76, "fluency": 62, "concision": 74, "wording": 71},
        "strengths": ["开头直接点明项目名称和定位，听众很快知道你在说什么",
                      "按「现状 → 下一步」的顺序展开，脉络清楚"],
        "improvements": ["「然后」出现了 2 次、「嗯」2 次，可以换成「目前」「接下来」这样更明确的过渡词",
                         "缺少一句「为什么要做」：讲清痛点能让听众更有兴趣",
                         "结尾可以补一句需要的帮助或时间预期"],
        "outline": ["背景：用语音打字很快，但口癖会原样留在文字里", "做了什么：本地识别 + LLM 润色，已经能日常使用",
                    "下一步：每日诊断报告和口语练习，帮用户真正改掉口癖"],
        "better": "我在做一个叫 Umless 的语音输入工具。它要解决的问题是：用语音打字很快，但口语里的「嗯」「然后」会原样留下来。"
                  "目前识别和润色已经完成，我自己每天都在用；接下来会加上每日诊断报告和口语练习，帮用户从根上改掉口癖。",
        "cpm": 212,
    },
    {
        "topic": "汇报本周进展与遇到的阻碍", "category": "工作汇报", "duration_s": 61.0,
        "segs": [["这周", "keep"], ["呃", "filler"], ["主要完成了报告页面的前端，", "keep"], ["然后", "filler"],
                 ["遇到的问题是", "keep"], ["就是", "filler"], ["诊断接口有时候会超时，", "keep"],
                 ["需要", "keep"], ["需要", "repeat"], ["后端同学帮忙看一下重试的逻辑", "keep"]],
        "scores": {"structure": 78, "logic": 80, "fluency": 70, "concision": 77, "wording": 74},
        "strengths": ["进展、阻碍、求助三部分齐全", "求助对象和事项具体"],
        "improvements": ["开头可以先给一句总体结论，例如「整体按计划推进」", "超时问题补充一下发生频率和影响范围"],
        "outline": ["结论：整体按计划推进", "进展：报告页前端完成", "阻碍：诊断接口偶发超时", "求助：后端协助排查重试逻辑"],
        "better": "本周整体按计划推进：报告页面的前端已经完成。目前有一个阻碍——诊断接口偶尔超时，大约每 20 次出现一次，"
                  "会导致报告生成失败。需要后端同学帮忙看一下重试逻辑。",
        "cpm": 196,
    },
    {
        "topic": "远程办公利大于弊吗？", "category": "观点表达", "duration_s": 48.3,
        "segs": [["我觉得", "keep"], ["嗯", "filler"], ["利大于弊，", "keep"], ["因为", "keep"],
                 ["其实", "filler"], ["省下了通勤时间，", "keep"], ["然后", "filler"], ["也更容易专注，", "keep"],
                 ["但是", "keep"], ["那个", "filler"], ["沟通成本会高一些", "keep"]],
        "scores": {"structure": 64, "logic": 68, "fluency": 66, "concision": 82, "wording": 69},
        "strengths": ["开门见山表明立场", "提到了反方观点"],
        "improvements": ["理由可以更具体，比如通勤节省了多少时间", "对反方观点只是提及，没有回应", "缺少总结句"],
        "outline": ["立场：利大于弊", "理由：省通勤、更专注", "回应：沟通成本可以靠固定同步会弥补", "总结"],
        "better": "我认为远程办公利大于弊。第一，每天能省下约一小时通勤；第二，在家更容易进入专注状态。"
                  "有人担心沟通成本高，但固定的每日同步加上文档化，基本能弥补。所以整体来看，利大于弊。",
        "cpm": 224,
    },
]

_FALLBACK_SAMPLE = _PRACTICE_SAMPLES[0]

_SOUND_EVENTS = ["start", "stop", "done", "error"]
_SOUND_PRESETS = ["rise", "fall", "pop", "drop", "chime", "tick", "soft", "classic", "low"]

# mock_dictate 不传参数时的示例：原文 → 输出，标出被删掉的口癖
_DICTATE_SAMPLES = {
    "zh": {"text": "我明天想约你聊一下那个方案",
           "highlights": [["嗯，", "filler"], ["我明天", "keep"], ["就是", "filler"], ["想约你聊一下那个方案", "keep"]]},
    "en": {"text": "I'd like to move the meeting to Thursday.",
           "highlights": [["Um, ", "filler"], ["I'd like to ", "keep"], ["uh, ", "filler"],
                          ["move the meeting to Thursday", "keep"]]},
}

# 应用更新（docs/ui-api.md「更新」）：模拟的新版本。notes 按界面语言给出
_UPDATE_RELEASES = {
    "windows": {"current": "0.3.0", "latest": "0.4.0", "size": 58_720_256, "name": "Umless-0.4.0-setup.exe", "notes": {
        "zh": ["新增：应用内更新，发现新版本时在主界面提示，一键下载安装", "新增：报告页可以导出为图片",
               "改进：SenseVoice 识别速度提升约 20%", "修复：部分应用里粘贴后光标位置不对的问题"],
        "en": ["New: in-app updates — get notified of new versions and install them in one click",
               "New: export a report as an image", "Improved: SenseVoice recognition is about 20% faster",
               "Fixed: caret ended up in the wrong place after pasting in some apps"]}},
    "android": {"current": "0.1.1", "latest": "0.2.0", "size": 67_196_611, "name": "Umless-0.2.0-arm64.apk", "notes": {
        "zh": ["新增：应用内更新", "新增：语音面板显示实时音量", "改进：长按空格的响应更快", "修复：部分机型上键盘高度异常"],
        "en": ["New: in-app updates", "New: live input level in the voice panel",
               "Improved: faster long-press on the space bar", "Fixed: wrong keyboard height on some devices"]}},
}
_UPDATE_TEXT = {
    "zh": {"downloading": "正在下载 {name}… {done} / {total} MB", "verifying": "正在校验文件…",
           "check_failed": "无法连接到 GitHub：连接超时", "download_failed": "文件校验不一致，已删除，请重新下载",
           "source_hint": "从源码运行：用 git pull 和 uv sync 更新", "no_update": "没有可下载的更新",
           "not_downloaded": "请先下载更新", "cannot_install": "从源码运行时不能自动安装"},
    "en": {"downloading": "Downloading {name}… {done} / {total} MB", "verifying": "Verifying the file…",
           "check_failed": "Couldn't reach GitHub: connection timed out",
           "download_failed": "Checksum mismatch; the file was deleted. Please download again",
           "source_hint": "Running from source: update with git pull and uv sync", "no_update": "No update to download",
           "not_downloaded": "Download the update first", "cannot_install": "Can't install automatically when running from source"},
}


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _content_len(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def _deep_merge(base: dict, patch: dict) -> None:
    """深度合并：字典递归合并，列表与标量整体替换。"""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


class MockApi:
    """与 docs/ui-api.md 一致的模拟实现。下划线开头的属性不会暴露给前端。"""

    def __init__(self, *, onboarded: bool = True, core_running: bool = True, theme: str = "system",
                 empty: bool = False, seed: int = 7, platform: str = "windows", update: str = "available") -> None:
        """platform="android" 模拟 Android 主 App：配置去掉桌面专属项、应用名为包名，
        并提供键盘状态、麦克风权限等 Android 专属方法（见 docs/ui-api.md「Android」）。
        update 为模拟的更新状态，取值见 mock_update()。"""
        if platform not in ("windows", "android"):
            raise ValueError(f"未知平台：{platform}")
        self._platform = platform
        self._ime = {"enabled": onboarded, "selected": onboarded}
        self._mic_granted = onboarded
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self._now = datetime.now()
        self._today = self._now.date()
        self._core_running = core_running
        self._nav: list[str] = []
        self._jobs: dict[str, dict] = {}
        self._rec: dict | None = None  # 当前录音：{"start": t, "kind": "practice"|"mic", "topic": {...}}
        self._autostart = False
        self._config = self._default_config(onboarded, theme)
        self._last_output: dict | None = None
        self._models = {
            "sensevoice": {"downloaded": True, "size_mb": 166, "name": "SenseVoice-Small"},
            "qwen3_asr": {"downloaded": False, "size_mb": 879, "name": "Qwen3-ASR-0.6B"},
        }
        if platform == "android":
            self._androidize()
        if not onboarded:
            self._models["sensevoice"]["downloaded"] = False
        self._lexicon = [{"word": w, "source": "builtin", "enabled": w not in ("这个", "那么", "的话")}
                         for w in _BUILTIN_FILLERS]
        self._lexicon += [{"word": "我觉得吧", "source": "llm", "enabled": True},
                          {"word": "大概就是", "source": "llm", "enabled": True},
                          {"word": "怎么讲", "source": "llm", "enabled": False},
                          {"word": "你知道吗", "source": "user", "enabled": True}]
        self._topics: dict | None = None
        self._topic_round = 0
        self._history: list[dict] = []
        self._diagnosed: dict[str, dict] = {}
        self._habits: list[dict] = []
        self._practice: list[dict] = []
        # 应用更新：后台自动检查发生在 2 小时前；下载任务同一时间只有一个（真实实现的任务键 "update:download"）
        self._update = {"state": "available", "fail_download": False, "downloaded": False, "job": None,
                        "perm_granted": False, "checked_at": self._now - timedelta(hours=2)}
        self.mock_update(update)
        if not empty:
            self._seed_data()

    # ------------------------------------------------------------ 数据生成

    @staticmethod
    def _default_config(onboarded: bool, theme: str) -> dict:
        return {
            "app": {"onboarded": onboarded, "tour_done": onboarded, "theme": theme, "language": "auto"},
            "hotkey": {"key": "caps_lock", "min_hold_ms": 300},
            "audio": {"beep": True, "volume": 60,
                      "sounds": {"start": "rise", "stop": "fall", "done": "", "error": "low"},
                      "mic_always_on": False, "keep_audio": False},
            "asr": {"engine": "sensevoice", "num_threads": 4,
                    "openai": {"base_url": "https://api.siliconflow.cn/v1", "api_key": "",
                               "model": "FunAudioLLM/SenseVoiceSmall"}},
            "llm": {"enabled": True, "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-mock-3f9a2c7e51b04d8a" if onboarded else "",
                    "model": "deepseek-v4-flash", "temperature": 0.2, "timeout_s": 20},
            "polish": {"intensity": "moderate"},
            "scenes": {"enabled": True,
                       "intensity": {"chat": "", "document": "restructure", "code": "faithful",
                                     "email": "restructure", "default": ""},
                       "rules": [{"app": "WeChat.exe", "scene": "chat"}, {"app": "Feishu.exe", "scene": "chat"},
                                 {"app": "Typora.exe", "scene": "document"}, {"app": "Obsidian.exe", "scene": "document"},
                                 {"app": "Code.exe", "scene": "code"}, {"app": "WindowsTerminal.exe", "scene": "code"},
                                 {"app": "OUTLOOK.EXE", "scene": "email"}]},
            "output": {"mode": "paste", "restore_clipboard": False},
            "ui": {"overlay": True},
            "coach": {"enabled": True, "auto_daily": True, "min_utterances": 5, "model": ""},
            "privacy": {"record_history": True, "exclude_apps": ["KeePass.exe", "1Password.exe"],
                        "retention_days": 0},
            "glossary": {"terms": ["Claude Code", "Codex", "GitHub", "PyTorch", "SenseVoice", "useMemo"]},
            "update": {"auto_check": True, "skipped_version": "", "manifest_url": ""},
        }

    def _androidize(self) -> None:
        """Android 版配置：没有热键、输出方式、提示条、常开麦克风、保留录音；新增 voice 与 privacy.skip_incognito。"""
        c = self._config
        for section in ("hotkey", "output", "ui"):
            c.pop(section)
        c["audio"].pop("mic_always_on")
        c["audio"].pop("keep_audio")
        c["voice"] = {"space_long_press": True, "toolbar_button": True, "haptic": True}
        c["privacy"]["skip_incognito"] = True
        c["privacy"]["exclude_apps"] = ["com.x8bit.bitwarden", "com.kunzisoft.keepass.free"]
        c["scenes"]["rules"] = [{"app": "com.tencent.mm", "scene": "chat"}, {"app": "com.ss.android.lark", "scene": "chat"},
                                {"app": "md.obsidian", "scene": "document"}, {"app": "cn.wps.moffice_eng", "scene": "document"},
                                {"app": "com.termux", "scene": "code"},
                                {"app": "com.microsoft.office.outlook", "scene": "email"}]
        del self._models["qwen3_asr"]  # 手机上只提供 SenseVoice

    def _app(self, exe: str) -> str:
        return _ANDROID_APPS.get(exe, exe) if self._platform == "android" else exe

    def _desktop_only(self, name: str) -> None:
        if self._platform == "android":
            raise ValueError(f"Android 端没有这个方法：{name}")

    def _android_only(self, name: str) -> None:
        if self._platform != "android":
            raise ValueError(f"桌面端没有这个方法：{name}")

    def _make_item(self, uid: int, when: datetime, tpl: dict, progress: float) -> dict:
        segs = [list(s) for s in tpl["segs"]]
        # 越往后进步越大：随机去掉一部分口癖，让趋势有起伏
        if progress > 0.5 and self._rng.random() < progress - 0.4:
            fillers = [i for i, s in enumerate(segs) if s[1] == "filler"]
            if len(fillers) > 1:
                segs.pop(self._rng.choice(fillers))
        raw = "".join(s[0] for s in segs)
        chars = _content_len(raw)
        fc = sum(1 for s in segs if s[1] == "filler")
        rc = sum(1 for s in segs if s[1] == "repeat")
        cc = sum(1 for s in segs if s[1] == "correction")
        per100 = fc * 100 / max(chars, 1)
        fluency = round(_clamp(100 - per100 * 3.2))
        concision = round(_clamp(100 - (rc + cc) * 100 / max(chars, 1) * 4))
        dur = round(tpl["dur"] * self._rng.uniform(0.9, 1.15), 1)
        return {
            "id": uid, "created_at": _iso(when), "duration_s": dur, "app": self._app(tpl["app"]), "scene": tpl["scene"],
            "intensity": tpl["intensity"], "output_text": tpl["out"], "raw_text": raw, "highlights": segs,
            "filler_count": fc, "repeat_count": rc, "correction_count": cc, "fluency": fluency,
            "rewrite_ratio": round(self._rng.uniform(0.12, 0.42) + (0.12 if tpl["intensity"] == "restructure" else 0), 2),
            "asr_ms": int(self._rng.uniform(380, 1300)), "llm_ms": int(self._rng.uniform(900, 3600)),
            "_chars": chars,
        }

    def _seed_data(self) -> None:
        rng = self._rng
        items: list[tuple[datetime, dict, float]] = []
        for d in range(29, -1, -1):
            day = self._today - timedelta(days=d)
            if d in (6, 13, 14, 20, 26):  # 有几天没用
                continue
            progress = (29 - d) / 29
            if d == 0:
                n = 18
                t = self._now - timedelta(minutes=4)
                times = []
                for _ in range(n):
                    times.append(t)
                    t -= timedelta(minutes=rng.randint(4, 26))
                times = [x for x in times if x.date() == day] or [self._now - timedelta(minutes=3)]
            else:
                n = rng.randint(6, 14) if day.weekday() < 5 else rng.randint(3, 7)
                times = sorted(datetime.combine(day, datetime.min.time())
                               + timedelta(minutes=rng.randint(9 * 60, 23 * 60)) for _ in range(n))
            for t in times:
                items.append((t, rng.choice(_UTTERANCES), progress))
        items.sort(key=lambda x: x[0])
        self._history = [self._make_item(i + 1, t, tpl, p) for i, (t, tpl, p) in enumerate(items)]
        self._history.reverse()  # 倒序

        # 已诊断的日期
        for d in (1, 2, 4, 5, 7, 9, 11, 15, 18, 22, 25, 28):
            day = self._today - timedelta(days=d)
            if self._day_items(day.isoformat()):
                self._diagnosed[day.isoformat()] = self._build_report(day.isoformat(), diagnosed=True)

        now = self._now
        self._habits = [
            self._habit(1, "word:就是", "减少「就是」", "word", "就是", "想停顿时直接停顿，不用「就是」填补",
                        ["我现在有个问题就是就是这个接口一直超时"], 3.1, 1.5, 2.2, "active", now - timedelta(days=9)),
            self._habit(2, "repeat", "说话不重复", "repeat", None, "开口前先想好主语，避免「我我」「在在」的重复",
                        ["在在用户没有登录的时候"], 1.8, 0.9, 1.2, "active", now - timedelta(days=6)),
            self._habit(3, "logic", "先说结论", "logic", None, "每段话第一句先给结论，再补充原因和细节",
                        ["我们先跑了基线然后发现效果不太好然后…"], 62, 82, 70, "active", now - timedelta(days=4)),
            self._habit(4, "word:然后", "少用「然后」串句子", "word", "然后", "用「接着」「之后」或干脆断句",
                        ["然后发现效果不太好然后又换了"], 2.6, 1.3, 1.1, "achieved", now - timedelta(days=24),
                        achieved=now - timedelta(days=3)),
            self._habit(5, "correction", "关键信息一次说对", "correction", None, "说时间和数字前先在心里确认",
                        ["下周三，不对，下周四"], 0.8, 0.4, 0.3, "achieved", now - timedelta(days=28),
                        achieved=now - timedelta(days=12)),
            self._habit(6, "word:那个", "减少「那个」", "word", "那个", "「那个」用作停顿时，换成短暂的沉默",
                        ["那个周末一起去爬山吧"], 1.4, 0.7, 1.2, "archived", now - timedelta(days=20)),
        ]

        self._topics = {"date": self._today.isoformat(), "topics": copy.deepcopy(_TOPICS_A)}
        for i, sample in enumerate(_PRACTICE_SAMPLES):
            when = now - timedelta(days=1 + i * 3, hours=i * 2 + 1)
            self._practice.append(self._practice_result(len(_PRACTICE_SAMPLES) - i, when, sample))

    @staticmethod
    def _habit(hid, key, title, kind, word, advice, evidence, baseline, target, current, status, created,
               achieved=None) -> dict:
        lower = kind not in ("logic", "clarity")
        span = (baseline - target) if lower else (target - baseline)
        done = (baseline - current) if lower else (current - baseline)
        progress = _clamp(done / span if span else 0, 0, 1)
        if status == "achieved":
            progress = 1.0
        return {"id": hid, "key": key, "title": title, "kind": kind, "word": word, "advice": advice,
                "evidence": evidence, "baseline": baseline, "target": target, "current": current,
                "unit": "次/百字" if lower else "分", "lower_is_better": lower, "progress": round(progress, 2),
                "status": status, "created_at": _iso(created), "achieved_at": _iso(achieved) if achieved else None}

    def _practice_result(self, pid: int, when: datetime, sample: dict) -> dict:
        segs = [list(s) for s in sample["segs"]]
        scores = dict(sample["scores"])
        overall = round(scores["structure"] * 0.25 + scores["logic"] * 0.25 + scores["fluency"] * 0.2
                        + scores["concision"] * 0.15 + scores["wording"] * 0.15)
        return {"id": pid, "created_at": _iso(when), "topic": sample["topic"], "category": sample["category"],
                "duration_s": sample["duration_s"], "raw_text": "".join(s[0] for s in segs), "overall": overall,
                "scores": scores, "strengths": list(sample["strengths"]), "improvements": list(sample["improvements"]),
                "outline": list(sample["outline"]), "better_version": sample["better"], "highlights": segs,
                "filler_count": sum(1 for s in segs if s[1] == "filler"), "cpm": sample["cpm"]}

    def _day_items(self, day: str) -> list[dict]:
        return [h for h in self._history if h["created_at"].startswith(day)]

    def _day_rule_scores(self, day: str) -> tuple[int | None, int | None]:
        # 模拟数据：按日期给出平滑上升的曲线，带少量起伏（真实实现由逐条指标聚合）
        if not self._day_items(day):
            return None, None
        d = (self._today - date.fromisoformat(day)).days
        flu = 80 - d * 0.42 + math.sin(d * 0.8) * 2.2
        con = 86 - d * 0.3 + math.cos(d * 1.1) * 1.8
        return round(_clamp(flu)), round(_clamp(con))

    def _llm_scores(self, day: str) -> tuple[int, int]:
        d = (self._today - date.fromisoformat(day)).days
        base = 79 - d * 0.5
        return round(_clamp(base + math.sin(d * 1.3) * 2 - 2)), round(_clamp(base + math.cos(d * 0.9) * 2 + 1))

    @staticmethod
    def _overall(flu, con, logic, clarity) -> int | None:
        parts = [(flu, 0.30), (con, 0.20), (logic, 0.25), (clarity, 0.25)]
        parts = [(v, w) for v, w in parts if v is not None]
        if not parts:
            return None
        total = sum(w for _, w in parts)
        return round(sum(v * w for v, w in parts) / total)

    def _filler_stats(self, items: list[dict]) -> list[dict]:
        counts: dict[str, int] = {}
        for h in items:
            for text, kind in h["highlights"]:
                if kind == "filler":
                    w = text.strip("，。,")
                    counts[w] = counts.get(w, 0) + 1
        chars = sum(h["_chars"] for h in items) or 1
        top = sorted(counts.items(), key=lambda x: -x[1])[:6]
        return [{"word": w, "count": c, "per100": round(c * 100 / chars, 1)} for w, c in top]

    def _build_report(self, day: str, diagnosed: bool) -> dict | None:
        items = self._day_items(day)
        if not items:
            return None
        flu, con = self._day_rule_scores(day)
        ratio = round(sum(h["rewrite_ratio"] for h in items) / len(items), 2)
        base = {"date": day, "n": len(items), "diagnosed": diagnosed, "filler_stats": self._filler_stats(items),
                "rewrite_ratio": ratio}
        if not diagnosed:
            base.update({"created_at": None, "model": None,
                         "scores": {"overall": None, "fluency": flu, "concision": con, "logic": None, "clarity": None},
                         "summary": "", "strengths": [], "issues": [], "examples": [], "personal_fillers": [],
                         "terms": []})
            return base
        logic, clarity = self._llm_scores(day)
        created = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time()) + timedelta(
            hours=8, minutes=1, seconds=12)
        if created > self._now:
            created = self._now
        t = copy.deepcopy(_REPORT_TEXT)
        base.update({"created_at": _iso(created), "model": self._config["llm"]["model"],
                     "scores": {"overall": self._overall(flu, con, logic, clarity), "fluency": flu, "concision": con,
                                "logic": logic, "clarity": clarity},
                     "summary": t["summary"], "strengths": t["strengths"], "issues": t["issues"],
                     "examples": t["examples"], "personal_fillers": t["personal_fillers"], "terms": t["terms"]})
        return base

    def _trend(self, days: int) -> list[dict]:
        out = []
        for d in range(days - 1, -1, -1):
            day = (self._today - timedelta(days=d)).isoformat()
            items = self._day_items(day)
            if not items:
                continue
            flu, con = self._day_rule_scores(day)
            logic = clarity = None
            if day in self._diagnosed:
                s = self._diagnosed[day]["scores"]
                logic, clarity = s["logic"], s["clarity"]
            out.append({"date": day, "overall": self._overall(flu, con, logic, clarity), "fluency": flu,
                        "concision": con, "logic": logic, "clarity": clarity, "count": len(items)})
        return out

    @staticmethod
    def _public(item: dict) -> dict:
        return {k: v for k, v in item.items() if not k.startswith("_")}

    # ------------------------------------------------------------ 任务

    def _new_job(self, worker, *args) -> dict:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"state": "running", "progress": None, "message": "", "result": None, "error": None}

        def update(**kw):
            with self._lock:
                self._jobs[job_id].update(kw)

        def run():
            try:
                result = worker(update, *args)
                update(state="done", progress=1.0, result=result)
            except Exception as e:  # noqa: BLE001
                update(state="error", error=str(e))

        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job_id}

    def job_status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在或已过期")
            return copy.deepcopy(job)

    # ------------------------------------------------------------ 1. 通用

    def _language(self) -> str:
        from ..i18n import resolve

        return resolve(self._config["app"].get("language", "auto"))

    def _t(self, key, **values):
        from ..i18n import STRINGS

        return STRINGS[self._language()][key].format(**values)

    def get_app_info(self):
        app = self._config["app"]
        android = self._platform == "android"
        return {"version": _UPDATE_RELEASES[self._platform]["current"], "onboarded": app["onboarded"], "tour_done": app.get("tour_done", False),
                "theme": app["theme"], "language": self._language(),
                "language_setting": app.get("language", "auto"),
                "hotkey": "" if android else self._config["hotkey"]["key"],
                "data_dir": "/data/user/0/org.umless.ime/files" if android else "C:\\Users\\you\\AppData\\Roaming\\Umless",
                "core_running": True if android else self._core_running, "platform": self._platform}

    def core_status(self):
        self._desktop_only("core_status")
        return {"running": self._core_running, "ready": self._core_running,
                "hotkey": self._config["hotkey"]["key"], "mode": self._config["output"]["mode"]}

    def get_last_output(self):
        return copy.deepcopy(self._last_output)

    def list_sounds(self):
        return {"events": list(_SOUND_EVENTS), "presets": list(_SOUND_PRESETS)}

    def preview_sound(self, sound, volume):
        if not (sound == "" or sound in _SOUND_PRESETS or str(sound).lower().endswith(".wav")):
            raise ValueError(f"提示音需要是预设名称或 .wav 文件的绝对路径：{sound}")
        return None

    def pick_sound_file(self):
        time.sleep(0.3)
        if self._platform == "android":  # Android：系统文件选择器选中的文件复制到应用目录
            return "/data/user/0/org.umless.ime/files/sounds/ding.wav"
        return "C:\\Users\\you\\Music\\ding.wav"

    def capture_hotkey(self):
        self._desktop_only("capture_hotkey")
        time.sleep(1.2)  # 模拟用户按下了组合键
        return "ctrl+space"

    def cancel_hotkey_capture(self):
        self._desktop_only("cancel_hotkey_capture")
        return None

    def start_core(self):
        self._desktop_only("start_core")
        time.sleep(0.6)
        self._core_running = True
        return None

    def get_nav(self):
        with self._lock:
            return self._nav.pop(0) if self._nav else None

    def open_path(self, kind):
        self._desktop_only("open_path")
        if kind not in ("data_dir", "config", "log"):
            raise ValueError(f"未知的路径类型：{kind}")
        return None

    def copy_text(self, text):
        return None

    # ------------------------------------------------------------ 2. 概览

    def get_overview(self):
        today = self._today.isoformat()
        items = self._day_items(today)
        chars = sum(h["_chars"] for h in items)
        spoken = sum(h["duration_s"] for h in items) / 60
        flu, con = self._day_rule_scores(today)
        diag_days = sorted(self._diagnosed)
        score = {"overall": None, "fluency": flu, "concision": con, "logic": None, "clarity": None, "delta": None,
                 "date": None}
        latest = None
        if diag_days:
            last = self._diagnosed[diag_days[-1]]
            s = last["scores"]
            prev = self._diagnosed[diag_days[-2]]["scores"]["overall"] if len(diag_days) > 1 else None
            score = {**s, "delta": (s["overall"] - prev) if prev is not None else None, "date": last["date"]}
            latest = {"date": last["date"], "summary": last["summary"]}
        week = [h for h in self._history
                if h["created_at"][:10] >= (self._today - timedelta(days=6)).isoformat()]
        return {
            "today": {"count": len(items), "chars": chars, "minutes_spoken": round(spoken, 1),
                      "minutes_saved": round(max(chars / 40 - spoken, 0), 1), "fluency": flu, "concision": con},
            "score": score,
            "trend": self._trend(30),
            "habits": [h for h in copy.deepcopy(self._habits) if h["status"] == "active"],
            "latest_diagnosis": latest,
            "top_fillers": self._filler_stats(week)[:5],
            "recent": [self._public(h) for h in self._history[:5]],
        }

    # ------------------------------------------------------------ 3. 报告

    def list_reports(self):
        today = self._today.isoformat()
        items = [{"date": d, "overall": r["scores"]["overall"], "n": r["n"]}
                 for d, r in sorted(self._diagnosed.items(), reverse=True) if d != today]
        return {"today": {"date": today, "n": len(self._day_items(today)), "diagnosed": today in self._diagnosed},
                "items": items}

    def get_report(self, date):  # noqa: A002  参数名与约定一致
        if date in self._diagnosed:
            return copy.deepcopy(self._diagnosed[date])
        return self._build_report(date, diagnosed=False)

    def run_diagnosis(self, date):  # noqa: A002
        def work(update, day):
            items = self._day_items(day)
            update(progress=0.05, message=f"正在整理 {len(items)} 条记录…")
            time.sleep(0.9)
            if len(items) < 3:
                raise ValueError("样本不足：至少需要 3 条可评分的记录才能诊断")
            for i in range(12):
                update(progress=0.15 + i * 0.06, message="正在请模型诊断表达问题…")
                time.sleep(0.22)
            update(progress=0.92, message="正在更新习惯卡…")
            time.sleep(0.6)
            report = self._build_report(day, diagnosed=True)
            with self._lock:
                self._diagnosed[day] = report
            return copy.deepcopy(report)

        return self._new_job(work, date)

    def get_trend(self, days):
        return self._trend(int(days))

    def get_habits(self, status):
        return [copy.deepcopy(h) for h in self._habits if status is None or h["status"] == status]

    def _set_habit(self, hid, status):
        for h in self._habits:
            if h["id"] == hid:
                h["status"] = status
                return None
        raise ValueError("习惯卡不存在")

    def archive_habit(self, id):  # noqa: A002
        return self._set_habit(id, "archived")

    def restore_habit(self, id):  # noqa: A002
        h = next((x for x in self._habits if x["id"] == id), None)
        if h is None:
            raise ValueError("习惯卡不存在")
        h["status"] = "achieved" if h.get("achieved_at") else "active"
        return None

    def _mark_added(self, field, key, value):
        for r in self._diagnosed.values():
            for x in r.get(field, []):
                if x[key] == value:
                    x["added"] = True

    def add_term(self, right):
        terms = self._config["glossary"]["terms"]
        if right not in terms:
            terms.append(right)
        self._mark_added("terms", "right", right)
        return None

    def add_filler(self, word):
        word = (word or "").strip()
        if not word:
            raise ValueError("口癖词不能为空")
        if not any(x["word"] == word for x in self._lexicon):
            self._lexicon.append({"word": word, "source": "user", "enabled": True})
        self._mark_added("personal_fillers", "word", word)
        return None

    # ------------------------------------------------------------ 4. 练习

    def get_practice_topics(self):
        return copy.deepcopy(self._topics)

    def generate_topics(self, refresh):
        def work(update, refresh):
            update(progress=None, message="正在根据你最近说过的内容出题…")
            time.sleep(1.6)
            update(message="正在混入通用场景题…")
            time.sleep(0.8)
            if refresh or self._topics is None:
                self._topic_round += 1 if refresh else 0
            pool = _TOPICS_B if self._topic_round % 2 else _TOPICS_A
            self._topics = {"date": self._today.isoformat(), "topics": copy.deepcopy(pool)}
            return copy.deepcopy(self._topics)

        return self._new_job(work, bool(refresh))

    def practice_start(self, topic):
        if not isinstance(topic, dict) or not topic.get("title"):
            raise ValueError("题目不能为空")
        t = dict(topic)
        t.setdefault("duration", 60)
        t.setdefault("category", "自定义")
        self._rec = {"start": time.monotonic(), "kind": "practice", "topic": t}
        return None

    def mic_level(self):
        rec = self._rec
        if not rec:
            return {"recording": False, "elapsed": 0.0, "level": 0.0}
        el = time.monotonic() - rec["start"]
        # 模拟说话：音节起伏 + 句间停顿
        syll = abs(math.sin(el * 7.3)) * (0.55 + 0.45 * math.sin(el * 1.9))
        pause = 0.15 if (el % 4.2) > 3.5 else 1.0
        level = _clamp((0.08 + 0.75 * syll) * pause + self._rng.uniform(-0.05, 0.05), 0.02, 1)
        return {"recording": True, "elapsed": round(el, 2), "level": round(level, 3)}

    def practice_stop(self):
        rec = self._rec
        if not rec or rec["kind"] != "practice":
            raise ValueError("当前没有在录音")
        self._rec = None
        elapsed = time.monotonic() - rec["start"]

        def work(update, topic, elapsed):
            update(progress=0.1, message="正在识别…")
            time.sleep(0.9)
            update(progress=0.45, message="正在评测结构与逻辑…")
            time.sleep(1.0)
            update(progress=0.8, message="正在生成示范表达…")
            time.sleep(0.7)
            sample = next((s for s in _PRACTICE_SAMPLES if s["topic"] == topic["title"]), _FALLBACK_SAMPLE)
            sample = {**sample, "topic": topic["title"], "category": topic.get("category") or "自定义",
                      "duration_s": round(max(elapsed, 3.0), 1)}
            with self._lock:
                pid = max((p["id"] for p in self._practice), default=0) + 1
                result = self._practice_result(pid, datetime.now(), sample)
                self._practice.insert(0, result)
            return copy.deepcopy(result)

        return self._new_job(work, rec["topic"], elapsed)

    def practice_cancel(self):
        self._rec = None
        return None

    def list_practice(self, limit):
        return [{"id": p["id"], "created_at": p["created_at"], "topic": p["topic"], "category": p["category"],
                 "duration_s": p["duration_s"], "overall": p["overall"]} for p in self._practice[: int(limit or 20)]]

    def get_practice(self, id):  # noqa: A002
        p = next((x for x in self._practice if x["id"] == id), None)
        if p is None:
            raise ValueError("练习记录不存在")
        return copy.deepcopy(p)

    # ------------------------------------------------------------ 5. 历史

    def list_history(self, query, offset, limit):
        q = (query or "").strip().lower()
        items = [h for h in self._history
                 if not q or q in h["output_text"].lower() or q in h["raw_text"].lower() or q in h["app"].lower()]
        offset, limit = int(offset or 0), int(limit or 20)
        return {"total": len(items), "items": [self._public(h) for h in items[offset: offset + limit]]}

    def delete_history(self, id):  # noqa: A002
        with self._lock:
            self._history = [h for h in self._history if h["id"] != id]
        return None

    # ------------------------------------------------------------ 6. 设置

    def get_settings(self):
        return {"config": copy.deepcopy(self._config), "models": copy.deepcopy(self._models),
                "providers": [{**copy.deepcopy(p), "name": self._t(f"api.provider.{p['id']}")}
                              for p in _PROVIDERS], "autostart": self._autostart}

    def save_settings(self, patch):
        if not isinstance(patch, dict):
            raise ValueError(self._t("cfg.bad_format"))
        for section in ("app", "audio"):
            if section in patch and not isinstance(patch[section], dict):
                raise ValueError(self._t("cfg.bad_format"))
        if "key" in patch.get("hotkey", {}):
            from .. import hotkey

            try:
                patch["hotkey"]["key"] = hotkey.normalize(patch["hotkey"]["key"])
            except hotkey.HotkeyError as e:
                raise ValueError(self._t(f"cfg.hotkey_{e.code}", name=e.spec)) from None
        lang = patch.get("app", {}).get("language")
        if lang is not None and lang not in ("auto", "zh", "en"):
            raise ValueError(self._t("cfg.bad_language", value=lang))
        vol = patch.get("audio", {}).get("volume")
        if vol is not None and (isinstance(vol, bool) or not isinstance(vol, (int, float))
                                or not 0 <= vol <= 100 or vol != int(vol)):
            raise ValueError(self._t("cfg.bad_volume"))
        if vol is not None:
            patch["audio"]["volume"] = int(vol)
        for event in (patch.get("audio", {}).get("sounds") or {}):
            if event not in _SOUND_EVENTS:
                raise ValueError(self._t("cfg.bad_sound_event", event=event))
        with self._lock:
            _deep_merge(self._config, patch)
        return self.get_settings()

    def test_llm(self, llm):
        base_url = (llm or {}).get("base_url", "")
        key = (llm or {}).get("api_key", "")
        model = (llm or {}).get("model", "")
        time.sleep(0.9)
        sample_in = self._t("api.test_sentence")
        en = self._language() == "en"
        if not base_url.startswith(("http://", "https://")):
            return {"ok": False, "latency_ms": None, "input": sample_in, "output": "",
                    "error": self._t("api.url_scheme")}
        if not key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            return {"ok": False, "latency_ms": None, "input": sample_in, "output": "",
                    "error": "401 Unauthorized: check your API key" if en else "401 未授权：请检查 API Key 是否正确"}
        if not model:
            return {"ok": False, "latency_ms": None, "input": sample_in, "output": "",
                    "error": "Enter a model name first" if en else "请先填写模型名"}
        return {"ok": True, "latency_ms": 820 + self._rng.randint(-120, 260), "input": sample_in,
                "output": "Could we move the meeting to Thursday?" if en else "我明天想请个假，去医院复查一下。",
                "error": None}

    def list_llm_models(self, base_url, api_key):
        time.sleep(0.6)
        for p in _PROVIDERS:
            if p["base_url"].rstrip("/") == (base_url or "").rstrip("/"):
                return list(p["models"])
        if not (base_url or "").startswith("http"):
            raise ValueError("无法获取模型列表：接口地址无效")
        return ["gpt-4.1-mini", "gpt-4.1-nano"]

    def download_model(self, name):
        if name not in self._models:
            raise ValueError(f"未知的模型：{name}")

        def work(update, name):
            m = self._models[name]
            total = m["size_mb"]
            steps = 30
            for i in range(steps + 1):
                p = i / steps
                update(progress=round(p * 0.96, 3), message=f"正在下载 {m['name']}… {round(total * p)} / {total} MB")
                time.sleep(0.16)
            update(progress=0.98, message="正在校验文件…")
            time.sleep(0.5)
            m["downloaded"] = True
            return {"name": name}

        return self._new_job(work, name)

    def set_autostart(self, enabled):
        self._desktop_only("set_autostart")
        self._autostart = bool(enabled)
        return None

    def get_lexicon(self):
        return copy.deepcopy(self._lexicon)

    def remove_filler(self, word):
        self._lexicon = [x for x in self._lexicon if x["word"] != word]
        return None

    def toggle_filler(self, word, enabled):
        for x in self._lexicon:
            if x["word"] == word:
                x["enabled"] = bool(enabled)
        return None

    def mic_test(self):
        def work(update):
            self._rec = {"start": time.monotonic(), "kind": "mic", "topic": None}
            peak = 0.0
            while (el := time.monotonic() - self._rec["start"]) < 4.0:
                peak = max(peak, self.mic_level()["level"])
                # 约定：录音阶段 0–0.8，识别阶段 ≥ 0.85
                update(progress=round(el / 4.0 * 0.8, 3), message=self._t("api.mic_listening"))
                time.sleep(0.1)
            self._rec = None
            update(progress=0.9, message=self._t("api.recognizing"))
            time.sleep(0.7)
            text = ("Hello, I'm testing my microphone to check transcription accuracy."
                    if self._language() == "en" else "你好，我正在测试麦克风，看看识别得准不准。")
            result = {"text": text, "peak": round(peak, 2)}
            if self._platform == "android":
                result["model_ready"] = True
            return result

        return self._new_job(work)

    def export_data(self):
        time.sleep(0.5)
        name = f"umless-export-{self._today.strftime('%Y%m%d')}.json"
        if self._platform == "android":
            return {"path": f"/storage/emulated/0/Download/{name}"}
        return {"path": f"C:\\Users\\you\\Downloads\\{name}"}

    def clear_data(self):
        with self._lock:
            self._last_output = None
            self._history.clear()
            self._diagnosed.clear()
            self._habits.clear()
            self._practice.clear()
            self._topics = None
            self._lexicon = [x for x in self._lexicon if x["source"] == "builtin"]
        return None

    def finish_onboarding(self):
        self._config["app"]["onboarded"] = True
        return None

    # ------------------------------------------------------------ 更新

    def _ut(self, key, **values):
        return _UPDATE_TEXT[self._language()][key].format(**values)

    def check_update(self, force):
        """force=False 返回缓存的检查结果；force=True 模拟一次联网检查（约 0.8 秒）。网络错误不抛异常，写进 error。"""
        u = self._update
        rel = _UPDATE_RELEASES[self._platform]
        if force:
            time.sleep(0.8)
            if u["state"] == "unchecked":
                u["state"] = "latest"
            if u["state"] != "error":
                u["checked_at"] = datetime.now()
        current = rel["current"]
        result = {"current": current, "latest": None, "available": False, "skipped": False, "notes": "",
                  "published_at": None, "size": None,
                  "checked_at": _iso(u["checked_at"]) if u["checked_at"] else None, "error": None,
                  "can_install": u["state"] != "source", "install_hint": None, "downloaded": False}
        if u["state"] == "error":
            result["error"] = self._ut("check_failed")
            return result
        if u["state"] == "unchecked":  # 从未检查过（如关闭了自动检查）：除当前版本外都为空
            return result
        published = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        if u["state"] == "latest":
            result.update(latest=current, published_at=published.strftime("%Y-%m-%dT%H:%M:%SZ"))
            return result
        result.update(latest=rel["latest"], available=True,
                      skipped=self._config["update"].get("skipped_version") == rel["latest"],
                      notes="\n".join(rel["notes"][self._language()]),
                      published_at=published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      size=rel["size"], downloaded=u["downloaded"])
        if u["state"] == "source":
            result["install_hint"] = self._ut("source_hint")
        return result

    def download_update(self):
        u = self._update
        if u["state"] in ("latest", "error", "unchecked"):
            raise ValueError(self._ut("no_update"))
        if u["state"] == "source":
            raise ValueError(self._ut("cannot_install"))
        with self._lock:  # 同一时间只有一个下载任务：正在下载时返回同一个 job
            job = u["job"]
            if job and self._jobs.get(job, {}).get("state") == "running":
                return {"job_id": job}
        rel = _UPDATE_RELEASES[self._platform]

        def work(update):
            total = rel["size"] / 1024 / 1024
            steps = 20
            for i in range(steps + 1):
                p = i / steps
                update(progress=round(p * 0.95, 3),
                       message=self._ut("downloading", name=rel["name"], done=f"{total * p:.1f}", total=f"{total:.1f}"))
                time.sleep(0.12)
                if u["fail_download"] and i == steps // 2:
                    raise ValueError(self._ut("download_failed"))
            update(progress=0.97, message=self._ut("verifying"))
            time.sleep(0.4)
            u["downloaded"] = True
            return {"downloaded": True}

        result = self._new_job(work)
        u["job"] = result["job_id"]
        return result

    def install_update(self):
        """桌面：installing（真实实现随后退出并在安装结束后重启；模拟不退出）。
        Android：第一次返回 needs_permission（模拟打开「安装未知应用」设置页），之后 installing。"""
        u = self._update
        if u["state"] == "source":
            return {"status": "unsupported", "install_hint": self._ut("source_hint")}
        if not u["downloaded"]:
            raise ValueError(self._ut("not_downloaded"))
        if self._platform == "android" and not u["perm_granted"]:
            u["perm_granted"] = True  # 用户在系统设置里允许了，下次再点「安装」即可
            return {"status": "needs_permission"}
        return {"status": "installing"}

    def skip_update(self, version):
        with self._lock:
            self._config["update"]["skipped_version"] = str(version or "")
        return None

    # ------------------------------------------------------------ 7. Android 专属

    def get_ime_status(self):
        self._android_only("get_ime_status")
        return dict(self._ime)

    def open_ime_settings(self):
        """真实实现跳转到系统输入法设置；模拟为用户在设置里启用了 Umless。"""
        self._android_only("open_ime_settings")
        self._ime["enabled"] = True
        return None

    def show_ime_picker(self):
        """真实实现弹出系统输入法选择框；模拟为用户选择了 Umless（未启用时选不到）。"""
        self._android_only("show_ime_picker")
        if self._ime["enabled"]:
            self._ime["selected"] = True
        return None

    def request_mic_permission(self):
        self._android_only("request_mic_permission")
        time.sleep(0.3)  # 模拟系统权限对话框
        self._mic_granted = True
        return {"granted": self._mic_granted}

    def export_logs(self):
        self._android_only("export_logs")
        time.sleep(0.4)
        return {"path": f"/storage/emulated/0/Download/umless-logs-{self._today.strftime('%Y%m%d')}.zip"}

    # ------------------------------------------------------------ 预览辅助（不属于接口约定）

    def mock_dictate(self, text=None, raw=None):
        """调试辅助：模拟常驻进程输出了一段文字，之后 get_last_output 返回它（引导页"试一试"用）。

        不传参数时用内置示例（随界面语言）；只传 text 时原文与输出相同；同时传 raw 时按口癖词表标注原文。
        """
        if text is None:
            sample = _DICTATE_SAMPLES[self._language()]
            text, highlights = sample["text"], copy.deepcopy(sample["highlights"])
            raw = "".join(h[0] for h in highlights)
        elif raw:
            from .. import metrics

            words = [x["word"] for x in self._lexicon if x["enabled"]]
            highlights = metrics.analyze(raw, text, None, words)["highlights"]
        else:
            raw, highlights = text, [[text, "keep"]]
        self._last_output = {"text": text, "raw": raw, "highlights": highlights,
                             "filler_count": sum(1 for h in highlights if h[1] == "filler"),
                             "at": _iso(datetime.now())}
        return copy.deepcopy(self._last_output)

    def mock_ime(self, enabled=False, selected=False):
        """调试辅助（Android）：设置键盘状态，便于预览引导页「启用键盘」步骤。"""
        self._android_only("mock_ime")
        self._ime = {"enabled": bool(enabled), "selected": bool(enabled and selected)}
        return dict(self._ime)

    def mock_update(self, state="available", fail_download=False):
        """调试辅助：设置模拟的更新状态。

        state：available 有新版本（默认）· latest 已是最新 · error 检查失败 · source 桌面从源码运行（不能安装）·
        unchecked 从未检查过（字段为空；下次 check_update(True) 后变为 latest）。
        fail_download=True 时下载到一半失败（测试「重新下载」）。
        每次调用都会清掉已下载的安装包，并收回 Android「安装未知应用」的授权（下次安装先返回 needs_permission）。
        """
        if state not in ("available", "latest", "error", "source", "unchecked"):
            raise ValueError(f"未知的更新状态：{state}")
        self._update.update(state=state, fail_download=bool(fail_download), downloaded=False, perm_granted=False)
        if state == "unchecked":
            self._update["checked_at"] = None
        return None

    def _push_nav(self, page: str) -> None:
        with self._lock:
            self._nav.append(page)
