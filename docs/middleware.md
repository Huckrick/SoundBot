# 中间件工作台

在顶栏打开 **中间件工作台**，从左上角选择 Wwise、FMOD Studio 或 CRI Atom Craft。左侧浏览工程音频，中央编辑和试听混音，右侧查看交付方式及 VST，下方继续使用停靠音效库。切换宿主、工程或源文件前，未交付的编辑需要确认。关闭应用时也会提醒。

| 宿主 | 打开工程 | 编辑完成后 |
| --- | --- | --- |
| Wwise | 在 Wwise 打开项目并启用本机 WAAPI。SoundBot 从当前项目读取 Sound SFX 的 AudioFileSource。 | 原路径写回支持的 16/24-bit PCM WAV，保留对象绑定和恢复备份。是否完成 Wwise 转换另行确认。 |
| FMOD Studio | 选择 `.fspro`，然后选择 Studio 实际配置的 Assets 目录。浏览目录中的 WAV、AIFF、MP3、OGG、WMA 或 FLAC。 | SoundBot 把 48 kHz / 24-bit WAV 渲染为 Assets 中的新文件。Studio 会把通过文件系统加入的文件标为 **#unimported**；在 Assets 中右键 **Import**，或拖到事件轨道时导入。 |
| CRI Atom Craft | 选择 `.atmcproject`，然后选择工程或目标 Work Unit 实际使用的 Materials 目录。浏览 WAV、AIFF。 | SoundBot 把 48 kHz / 24-bit WAV 渲染为 Materials 中的新文件。回到 Atom Craft，在对应素材目录使用 **Register unregistered files**，再按正常流程添加到 Cue 或轨道。 |

FMOD 和 CRI 当前是**素材目录交付**。可编辑和试听可读取的单声道或立体声素材，并混入 SoundBot 库内选区。素材树反映磁盘文件，不推断这些文件是否已注册、是否被多个事件或 Cue 引用。SoundBot 不修改这两个宿主的工程文件、原始素材、事件、Cue 或已构建音频。交付后工作台显示下一步和「打开文件位置」。同一目录重复渲染相同内容会复用成品，成品不纳入暂存清理。

如果工程或素材目录迁移，使用 **设置 → 中间件 → 重新选择工程**。在本次运行内「刷新工程」会重扫原选目录。扫描跳过隐藏项、符号链接和目录联接；大目录最多枚举 100,000 个条目、收集 20,000 个音频源，达到上限时可选择较小的素材目录。树一次渲染至多 600 条匹配结果，可通过搜索缩小范围。
