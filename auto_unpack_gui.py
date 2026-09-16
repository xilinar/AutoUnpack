#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_unpack_gui.py —— auto_unpack.py 的图形界面（tkinter + tkinterdnd2）

把压缩包拖进窗口 -> 选编码 -> 点“开始解压”，进度实时显示在下方文本框里。
核心解压逻辑完全复用 auto_unpack.py，本文件只负责界面与调用。

依赖安装
--------
    pip install tkinterdnd2

    # tkinter 一般随 Python 自带；个别 Linux 发行版若缺失：
    #   sudo apt install python3-tk

用法
----
    python auto_unpack_gui.py

    日文压缩包一般用 shift_jis，中文压缩包一般用 gbk。
    解压目标默认是“压缩包所在目录下的同名文件夹”。

PyInstaller 打包
----------------
tkinterdnd2 自带 tkdnd 二进制文件（.dll/.so/.dylib），必须一起打包，
否则运行时会报 “Unable to load tkdnd library”。

    # 1) 先查到 tkdnd 目录位置
    python -c "import tkinterdnd2,os;print(os.path.dirname(tkinterdnd2.__file__))"

    # 2) Windows（--add-data 的源与目标之间用分号 ; 分隔）
    pyinstaller -F -w --name AutoUnpack ^
        --add-data "C:\\Python\\Lib\\site-packages\\tkinterdnd2\\tkdnd;tkinterdnd2\\tkdnd" ^
        auto_unpack_gui.py

    # 3) Linux / macOS（--add-data 用冒号 : 分隔）
    pyinstaller -F -w --name AutoUnpack \
        --add-data "/usr/lib/python3/dist-packages/tkinterdnd2/tkdnd:tkinterdnd2/tkdnd" \
        auto_unpack_gui.py

    # 4) 更省事的写法：自动收集 tkinterdnd2 及其 tkdnd 二进制
    pyinstaller -F -w --name AutoUnpack --collect-all tkinterdnd2 auto_unpack_gui.py

    说明：-F 打单文件、-w 不弹控制台窗口。
    auto_unpack.py 必须与 auto_unpack_gui.py 放在同一目录（本文件直接 import 它）。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

import auto_unpack as au

# 下拉框可选编码（文件名编码与文本编码共用）
ENCODINGS = ("utf-8", "gbk", "gb18030", "shift_jis",
             "cp932", "euc-jp", "euc-kr", "big5")


class TextRedirector:
    """把 print 的输出安全地送进 Text 文本框。

    子线程绝不能直接操作 tkinter 控件，所以 write() 只往队列里塞字符串；
    真正的插入动作由主线程的 _poll()（root.after 定时器）完成。
    """

    def __init__(self, root, widget, on_done=None):
        self.root = root
        self.widget = widget
        self.queue = queue.Queue()
        self.on_done = on_done      # 子线程结束时通过哨兵 None 触发
        self._poll()

    def write(self, text):
        if text:
            self.queue.put(text)    # queue.Queue 是线程安全的

    def flush(self):
        pass

    def _poll(self):
        chunks = []
        finished = False
        try:
            while True:
                item = self.queue.get_nowait()
                if item is None:    # 哨兵：子线程已结束
                    finished = True
                else:
                    chunks.append(item)
        except queue.Empty:
            pass

        if chunks:
            self.widget.configure(state=tk.NORMAL)
            self.widget.insert(tk.END, "".join(chunks))
            self.widget.configure(state=tk.DISABLED)
            self.widget.see(tk.END)

        if finished and self.on_done:
            self.on_done()

        self.root.after(100, self._poll)


class AutoUnpackGUI:
    def __init__(self, root):
        self.root = root
        self.archive_path = None
        self.running = False

        root.title("auto_unpack 图形界面 —— 压缩包解压")
        root.geometry("780x580")

        # ---- 1. 拖拽区域 ----
        self.drop_label = tk.Label(
            root, text="把压缩包拖到这里", relief="groove", borderwidth=2,
            height=4, justify="center", wraplength=720,
        )
        self.drop_label.pack(fill=tk.X, padx=10, pady=(10, 6))
        self.drop_label.drop_target_register(DND_FILES)
        self.drop_label.dnd_bind("<<Drop>>", self.on_drop)

        # ---- 2. 两个编码下拉框 ----
        enc_frame = tk.LabelFrame(root, text="编码设置")
        enc_frame.pack(fill=tk.X, padx=10, pady=6)

        self.name_var = tk.StringVar(value="shift_jis")
        self.text_var = tk.StringVar(value="shift_jis")

        tk.Label(enc_frame, text="文件名编码：").grid(
            row=0, column=0, padx=(8, 2), pady=6, sticky="e")
        tk.OptionMenu(enc_frame, self.name_var, *ENCODINGS).grid(
            row=0, column=1, sticky="w")
        tk.Label(enc_frame, text="文本编码：").grid(
            row=0, column=2, padx=(20, 2), pady=6, sticky="e")
        tk.OptionMenu(enc_frame, self.text_var, *ENCODINGS).grid(
            row=0, column=3, sticky="w")

        # ---- 3. 覆盖复选框 ----
        self.overwrite_var = tk.BooleanVar(value=False)
        tk.Checkbutton(enc_frame, text="覆盖已有文件",
                       variable=self.overwrite_var).grid(
            row=1, column=0, columnspan=4, padx=8, pady=(0, 8), sticky="w")

        # ---- 4. 开始按钮 ----
        self.run_button = tk.Button(root, text="开始解压", height=2,
                                    command=self.on_start)
        self.run_button.pack(fill=tk.X, padx=10, pady=4)

        # ---- 5. 输出文本框 ----
        text_frame = tk.Frame(root)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))

        self.text = tk.Text(text_frame, wrap="word", state=tk.DISABLED)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(text_frame, command=self.text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=scrollbar.set)

        # 重定向 stdout：之后所有 print 都实时显示在文本框里
        self.redirector = TextRedirector(root, self.text, self.on_finished)
        sys.stdout = self.redirector

    # ------------------------------------------------------------------
    # 拖拽
    # ------------------------------------------------------------------
    def on_drop(self, event):
        # 必须用 splitlist 解析：文件名带空格时 event.data 会用 {} 包裹
        paths = self.root.tk.splitlist(event.data)
        if not paths:
            return
        self.archive_path = os.path.abspath(paths[0])
        self.drop_label.configure(text=self.archive_path)

    # ------------------------------------------------------------------
    # 开始 / 结束
    # ------------------------------------------------------------------
    def on_start(self):
        if self.running:
            return
        if not self.archive_path:
            print("【错误】请先把压缩包拖到上面的区域。")
            return
        if not os.path.isfile(self.archive_path):
            print(f"【错误】找不到文件：{self.archive_path}")
            return

        self.running = True
        self.run_button.configure(state=tk.DISABLED, text="正在解压…")
        threading.Thread(target=self.worker, daemon=True).start()

    def on_finished(self):
        """由主线程的 _poll 调用，安全地恢复按钮。"""
        self.running = False
        self.run_button.configure(state=tk.NORMAL, text="开始解压")

    # ------------------------------------------------------------------
    # 解压线程
    # ------------------------------------------------------------------
    def worker(self):
        archive = Path(self.archive_path)
        # output=None 表示解压到压缩包同目录下的同名文件夹
        options = au.Options(
            output=None,
            name_encoding=self.name_var.get(),
            text_encoding=self.text_var.get(),
            overwrite=self.overwrite_var.get(),
            dry_run=False,
            verbose=True,
            quiet=False,
            password=None,
            unsafe_paths=False,
        )
        try:
            fmt = au.detect_format(archive)
            with au.make_extractor(fmt, archive, options, print) as extractor:
                dest = au.default_output_dir(archive)
                rc = au.run_extract(extractor, options, dest)
            if rc == au.EXIT_OK:
                print("\n解压完成。")
            else:
                print("\n解压结束，但有项目未成功，详见上面的【警告】/【错误】。")
        except Exception as exc:
            print(f"\n【错误】{exc}")
        finally:
            self.redirector.queue.put(None)   # 通知主线程恢复按钮


def main():
    root = TkinterDnD.Tk()   # 必须用 TkinterDnD.Tk()，否则拖拽不可用
    AutoUnpackGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
