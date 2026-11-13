---
name: space-video
description: 视频创作总控/导演。把一条视频从「参考素材 → 选题 → 脚本 → 剪辑 → B-roll → 字幕 → 封面」串成一条流水线，判断当前该走哪一步、调用哪个 space-video-* 子技能，并在成片后做复盘。当用户说「做个视频」「我要拍条口播」「这个视频从头帮我做」「视频工作流」「video pipeline」，或不确定该用哪个视频子技能时触发。
triggers: ["用户说「视频创作总控」时", "用户说「参考素材」时", "space video"]
---

# space-video · 视频创作导演

一条视频从想法到成片，中间有多个环节。这个技能是**总调度**：判断你现在在哪一步，把活派给对应的子技能，最后收口做复盘。

## 全流程地图

```text
参考/灵感 ──► ① 选题        space-video-topic     选题·标题·正文·下载参考·逐字稿
            ► ② 脚本        space-video-script    去 AI 味口播稿·精确到秒
            ► ③ 配音        space-video-audio     TTS 旁白·找 BGM·压音混音
            ► ④ B-roll      space-video-broll     动效导演·HTML→30s+ MP4
            ► ⑤ 剪辑        space-video-edit      剪口播·删前保后·成片
            ► ⑥ 字幕        space-video-subtitle  转写·断句·AI 校对·烧录
            ► ⑦ 封面        space-video-cover     封面家族气质
成片 ──────► ⑧ 复盘（本技能收口）
```

## 状态机

```text
intake
  │
  ├─→ topic_planning      （选题完成）
  │
  ├─→ script_locked       （脚本锁定）
  │
  ├─→ audio_locked        （配音锁定）
  │
  ├─→ visual_assets_ready （B-roll/动效就绪）
  │
  ├─→ composition_done    （剪辑完成）
  │
  ├─→ captioned          （字幕完成）
  │
  ├─→ cover_ready        （封面完成）
  │
  └→ verified             （验收完成）
      │
      └─→ delivered       （已交付）
```

### 状态定义

| 状态 | 说明 | 产出 |
|------|------|------|
| `intake` | 需求输入 | 理解主题、确认类型 |
| `topic_planning` | 选题完成 | 标题候选、正文、逐字稿 |
| `script_locked` | 脚本锁定 | 精确到秒的分镜脚本 |
| `audio_locked` | 配音锁定 | 定稿音频文件 |
| `visual_assets_ready` | 视觉素材就绪 | B-roll、动效、图表 |
| `composition_done` | 剪辑完成 | 成片视频 |
| `captioned` | 字幕完成 | 含字幕成片 |
| `cover_ready` | 封面完成 | 封面图 |
| `verified` | 验收完成 | 通过质量门禁 |
| `delivered` | 已交付 | 发布链接 |

## 路由规则

先判断用户手里有什么、想要什么，再决定入口：

| 用户的话 / 手里的东西 | 派给 | 目标状态 |
|---|---|---|
| 「不知道拍什么」「帮我想选题」「这条爆款拆一下」「起标题」 | `space-video-topic` | `topic_planning` |
| 「稿子写一下」「口播脚本」「这段太 AI 了」「精确到秒的分镜」 | `space-video-script` | `script_locked` |
| 「配音」「配乐」「加 BGM」「找音乐」「TTS 旁白」「混音」 | `space-video-audio` | `audio_locked` |
| 「加点动画」「B-roll」「做个 motion 开场」「HTML 转视频」 | `space-video-broll`（代码渲染，确定性、免费） | `visual_assets_ready` |
| 「文章转图解视频」「手绘白板动图」 | `space-video-broll-sketch`（调视频模型生成） | `visual_assets_ready` |
| 「数字人」「AI主播」「虚拟人口播」 | `digital-human-video`（数字人专精） | 数字人状态机 |
| 「剪一下这条录屏」「口误删掉」「做成片」「时间轴」 | `space-video-edit` | `composition_done` |
| 「上字幕」「转写」「srt」「烧录字幕」 | `space-video-subtitle` | `captioned` |
| 「做封面」「缩略图」「首图」 | `space-video-cover` | `cover_ready` |
| 「从头帮我做一条」 | 从①开始，逐环节走 | - |

**不确定就先问一句**：这条视频是口播/录屏/混剪？发哪个平台（竖屏 3:4 / 横屏 16:9）？有没有参考？

## 质量门禁

### 技术验收

```yaml
技术检查:
  视频:
    - 预期分辨率 ✓/✗
    - 预期帧率 ✓/✗
    - 预期时长 ✓/✗
    - 可解码流 ✓/✗
    - 无黑帧 ✓/✗
    - 无长期冻结 ✓/✗

  音频:
    - ASR 校验通过 ✓/✗
    - 响度达标 (-16 LUFS) ✓/✗
    - 无削波 ✓/✗
    - 无回声 ✓/✗
    - 无意外静音 ✓/✗
```

### 人工验收

```yaml
内容检查:
  - 开头有钩子 ✓/✗
  - 内容有价值 ✓/✗
  - 节奏合适 ✓/✗
  - 结尾有收尾 ✓/✗

  视觉检查:
  - B-roll 与内容相关 ✓/✗
  - 字幕准确 ✓/✗
  - 封面吸引 ✓/✗
```

## 一条龙时的交接约定

各环节用文件交接，不要把上一步的结果只留在对话里：

```text
项目目录/
├── job.json           # 任务配置与状态
├── 00_参考/          下载的参考视频、逐字稿
├── 01_选题/          选题.md、标题候选.md、正文.md
├── 02_脚本/          script.md（精确到秒）
├── 03_配音/          audio_final.wav
├── 04_broll/         *.mp4 B-roll 片段
├── 05_剪辑/          source_cut.mp4、cutlist.json
├── 06_字幕/          subtitles.srt
├── 07_封面/          cover.png
├── qa/               验收报告
└── outputs/           最终成片
```

每步产出落盘后，更新 `job.json` 中的状态，然后进入下一步。子技能之间不直接互相调用，统一由本技能编排。

## ⑧ 复盘（成片后收口）

成片发布后，帮用户沉淀：

1. **数据**：完播率、点赞/评论比、涨粉，哪个环节可能是瓶颈
2. **钩子回看**：前 3 秒留没留住人
3. **可复用资产**：这条里哪个 B-roll / 封面模板 / 选题公式下次能复用
4. 追加一条到用户的视频复盘记录（若有）

## 数字人视频特殊处理

当用户请求数字人视频时，委托给 `digital-human-video` Skill：

```text
digital-human-video（独立状态机）
    │
    ├─→ intake → content_locked → audio_locked
    │
    └─→ ...（见 digital-human-video SKILL.md）
```

数字人视频完成后，返回本技能进行封面和复盘。

## 核心原则

- **单一职责**：本技能只做调度和复盘，不亲自剪、不亲自写；具体活交子技能
- **落盘交接**：每步产出写成文件，可追溯、可回退
- **状态追踪**：使用 job.json 记录当前状态，支持断点续传
- **质量门禁**：每个环节设置检查点，不合格不进入下一环节
- **平台先行**：动手前先定比例和平台，避免竖屏横屏返工
