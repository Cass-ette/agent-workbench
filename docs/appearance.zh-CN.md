# 外观与项目标签

三端提供「跟随系统」「浅色 · Latte」「深色 · Mocha」，只保存到当前设备。macOS 的入口在侧栏「外观」与菜单栏「外观」；Windows 在右上角；Android 在顶部「外观」。窗口和终端随选择更新，不重启会话、不清空草稿、不改变 Key、权限或历史。

Mac「共享终端外观」中的「终端跟随应用主题」可以关闭。原有自定义前景、背景、光标和字体保留；旧的内置默认颜色会迁移为跟随应用主题，自定义颜色继续使用。重新打开跟随后，保存的自定义颜色仍在，下次关闭可继续使用。CLI 自己输出的真彩色仍可能覆盖终端调色板。

Mac 项目会话标签可直接从标题拖动，插入线表示释放位置；溢出的标签可以横向滚动，拖近边缘可滚动。也可以右键标签「向左移动／向右移动」。重排只改变显示顺序，当前选中的会话、进程和附件目标保持不变。Windows 使用相同的插入位置语义；Android 保持长按标题拖动。

配色来源：[Catppuccin palette](https://github.com/catppuccin/palette)，MIT 许可见 `shared/CATPPUCCIN-LICENSE`。三端共享 `shared/catppuccin.json`，Swift / Kotlin 常量由 `scripts/generate-themes.py` 生成。Latte 的小字使用 subtext1，交互强调蓝适当加深以保证对比度；ANSI 颜色保留官方色值。

本次界面整理参考 [unslop](https://heydamir.design/unslop)，作者 Damir Sokolovsky（[@heydamir](https://x.com/heydamir)），CC BY 4.0：保留功能布局，统一文字层级、标签尺寸、焦点和选中状态，不添加装饰动画或无关视觉。
