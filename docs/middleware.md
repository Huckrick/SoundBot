# 中间件工作台 / Middleware workspace

[简体中文](#简体中文) · [English](#english) · [日本語の概要](../README.ja.md)

## 简体中文

### 连接 Wwise 并选中源

1. 在 Wwise Authoring 打开目标工程，启用本机 Wwise Authoring API（WAAPI）。SoundBot 默认地址为 `ws://127.0.0.1:8080/waapi`。端口不同可在 **设置 → 中间件** 修改。建议先在一个可恢复的工程副本里试完整流程。
2. 打开 **中间件工作台 → Wwise → 连接**，核对显示的工程名称。项目浏览器展示 Sound SFX 的 AudioFileSource 以及上级工作单元、文件夹和容器。使用搜索框查找已读取的对象，展开树核对其位置。节点可显示 Wwise 返回的颜色和对象类型。
3. 在树中点选具体音源，确认右侧详情中的目标和格式。写回目标必须是工程 `Originals` 内现存的、可写的单声道或立体声 **16/24-bit PCM WAV**。源格式决定写回的采样率、声道和位深。

### 试听和叠加素材

下方音效库跟随主库的文件夹、分类与搜索。拖动缩略波形可把整条音效放在时间线，点击素材名称可进入编辑器选区，再将该片段送入工作台。比如给冲击声补低频，可以在新轨道加入短促的低频素材，将起音对齐，调低叠加轨增益，再用静音或独奏比较前后。

- **时间线。** 拖动标尺定位，使用放大、缩小或复位检查起音对齐。拖动片段定位，下半部边缘可裁剪，开启磁吸可对齐。选中片段后可改增益。
- **淡化与声像。** 拖动片段上的淡化手柄调整时长，右键选择线性、等功率、缓起、缓收或 S 曲线。开启自动交叉淡化后，同轨相邻片段部分重叠时可交叉淡化，可单独更改每个交叉区的曲线。立体声目标可在片段波形上添加、调整和移除声像点。
- **效果与预览。** 选中轨道，在右侧 **VST** 调整该轨的效果。播放当前混音前可切换轨道静音、独奏和效果旁通，检查电平和渲染波形。各轨的参数保持独立，写回成功后复位。

### 写回、转换与恢复

1. 试听满意后点 **写回**，在确认窗口核对工程、音源、采样率、声道和位深。同一 WAV 被多个 Wwise 源共享、目标已被外部修改、目标不可写或带循环和定位标记时，SoundBot 会拒绝写回并显示原因。
2. SoundBot 将混音渲染为临时 WAV，重新检查对象、源文件绑定和指纹，备份原件后原子替换原路径，再查询 Wwise 核对原绑定。可验证的写回失败会回滚。成功后 SoundBot 重新读取音源。编辑会话保存在内存里，切换目标、工程或退出时会提示处理未交付的编辑。
3. **「源 WAV 已更新 · Wwise 转换状态待确认」** 表示 `Originals` 中的文件已更新。回到 Wwise 试听，按工程流程转换音频或重新生成 SoundBank。需要撤回时，在 SoundBot 的 **清理中心** 找到 Wwise 恢复备份，并核对目标后恢复。恢复也会检查当前文件是否已被另一次编辑改变。

### FMOD Studio 与 CRI Atom Craft（实验性，未进行真实宿主测试）

已实现的部分是素材目录浏览和 WAV 写出。两条宿主操作路径目前是预期工作流，等待实机反馈。

| 宿主 | SoundBot 中的准备 | 预期的宿主操作 |
| --- | --- | --- |
| FMOD Studio | 选择 `.fspro` 和工程实际使用的 Assets 目录。工作台预期导出一份 48 kHz / 24-bit WAV。 | 在 FMOD Studio 的 Assets 中 Import，再将素材用于事件。 |
| CRI Atom Craft | 选择 `.atmcproject` 和工程或 Work Unit 使用的 Materials 目录。工作台预期导出一份 48 kHz / 24-bit WAV。 | 在 Atom Craft 注册导出的素材，再分配给 Cue 或轨道。 |

两条路径均按目录交付新 WAV。工程对象、事件和 Cue 在各自的宿主中管理。欢迎用工程副本尝试，在反馈中写明操作系统、宿主版本，以及卡在选择目录、预览、导出还是宿主登记。 [Issue](https://github.com/Huckrick/SoundBot/issues/new/choose) · [Discussions](https://github.com/Huckrick/SoundBot/discussions)

## English

### Connect to Wwise and select a source

1. Open the target project in Wwise Authoring and enable local WAAPI. SoundBot defaults to `ws://127.0.0.1:8080/waapi`. If you use another port, enter it under **Settings → Middleware**. A project copy is a good place to try the complete workflow.
2. Choose **Middleware Workspace → Wwise → Connect** and check the project name. The browser reads AudioFileSource entries under Sound SFX, with their work units, folders, and containers. Search the loaded hierarchy or expand the tree to check where a sound belongs. The tree shows object types and Wwise colors where available.
3. Select a specific source in the tree. Check the target and its format in the inspector. Writeback requires an existing writable mono or stereo **16/24-bit PCM WAV** inside the project's `Originals` directory. SoundBot uses the original sample rate, channel count, and bit depth.

### Layer, edit and audition

The library dock follows the main library's folder, category, and search filters. Drag a thumbnail waveform onto a timeline track to add a whole sound. Open the sound in the editor to mark a range and send that clip to the workspace. To add weight to a thin impact, try placing a short low-end sound on another track, line up the attacks, lower the layer's gain, and compare with mute or solo.

- **Timeline.** Drag the ruler to seek and use the zoom controls to check timing. Drag a clip to move it, trim with the lower portions of its edges, and turn on snapping to align clips. Select a clip to edit its gain.
- **Fades and pan.** Drag a clip's fade handles and right-click for linear, equal-power, ease-in, ease-out, or S curves. With automatic crossfades on, adjacent clips that partly overlap on one track crossfade. Pick a different curve for an individual overlap. On stereo targets, add, move, or remove pan points directly on the selected clip's waveform.
- **Effects and preview.** Select a track and use the **VST** panel to adjust that track's effects. Use mute, solo, and bypass to compare changes. The rendered waveform and meters reflect the preview. Each track keeps its own settings until successful writeback.

### Write back, confirm conversion, and restore

1. When the mix is ready, choose **Write back** and confirm the project, source, sample rate, channels, and bit depth in the dialog. A shared, changed, or unwritable source, or a WAV with loop or cue markers, is rejected with a reason.
2. SoundBot renders a temporary WAV, checks the file fingerprint and the Wwise binding again, backs up the original, and replaces it atomically at the same path. It checks that the binding still matches after replacement and rolls back a failure it can verify. On success, SoundBot rereads the source. Uncommitted edits are kept in memory. Switching projects or sources, or quitting, prompts you when edits are pending.
3. **Source WAV updated · Wwise conversion unverified** is the file writeback status. Audition the source in Wwise and run conversion or regenerate SoundBanks as required. To undo a writeback, locate its Wwise recovery backup in **Cleanup Center**, review the target, and restore it. Restoration checks whether a later edit has changed the file.

### FMOD Studio and CRI Atom Craft (experimental, untested in the hosts)

SoundBot implements folder browsing and WAV export for these workflows. The steps inside FMOD Studio and CRI Atom Craft are expected behavior and await testing in the actual hosts.

| Host | Prepare in SoundBot | Expected host step |
| --- | --- | --- |
| FMOD Studio | Choose the `.fspro` file and the project's actual Assets directory. SoundBot targets a new 48 kHz / 24-bit WAV. | Import that file in FMOD Studio's Assets view, then use it in an event. |
| CRI Atom Craft | Choose the `.atmcproject` file and the project's or Work Unit's Materials directory. SoundBot targets a new 48 kHz / 24-bit WAV. | Register the exported file in Atom Craft, then add it to a Cue or track. |

Both paths deliver a new WAV into the selected folder. Manage events, Cues, and other project objects in the host. Feedback from project copies is welcome. Include the OS, host version, and the step that worked or failed: finding the folder, preview, export, or host registration. [Issue](https://github.com/Huckrick/SoundBot/issues/new/choose) · [Discussions](https://github.com/Huckrick/SoundBot/discussions)
