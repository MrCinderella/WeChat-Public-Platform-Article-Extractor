"""
公众号合集文章批量提取器 —— 图形界面程序。

用法：
    python album_gui.py
"""

import os
import sys
import time
from datetime import datetime

from PySide6.QtCore import QThread, Signal, QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit,
    QProgressBar, QGroupBox, QFileDialog,
    QMessageBox, QSpinBox, QStatusBar,
)

import album_core
from album_core import AlbumExtractor


APP_TITLE = "公众号合集文章提取器"
VERSION = "v1.0"

DEFAULT_URL = ("https://mp.weixin.qq.com/mp/appmsgalbum"
               "?__biz=MzYzMjM4MTYxMg==&action=getalbum"
               "&album_id=4357293669826117647")


class ExtractWorker(QThread):
    """后台执行提取任务，避免界面卡死。"""

    log_signal = Signal(str)
    progress_signal = Signal(int, int, str)
    finished_signal = Signal(object, object)
    error_signal = Signal(str)

    def __init__(self, url, output_dir, formats, skip_existing=False, parent=None):
        super().__init__(parent)
        self.url = url
        self.output_dir = output_dir
        self.formats = formats
        self.skip_existing = skip_existing
        self._stop = False
        self._plan = []
        self.retry_times = 4
        self.skipped_count = 0

    def stop(self):
        self._stop = True

    def stopped(self):
        return self._stop

    def log(self, msg):
        self.log_signal.emit(msg)

    def progress(self, cur, total, title):
        self.progress_signal.emit(cur, total, title)

    def run(self):
        try:
            extractor = AlbumExtractor(
                output_dir=self.output_dir,
                formats=self.formats,
                log=self.log,
                stop_flag=self.stopped,
                progress=self.progress,
                skip_existing=self.skip_existing,
            )

            # 先抓列表，让界面尽早显示总数
            articles, err = extractor.fetch_list(self.url)
            if not articles:
                self.error_signal.emit(err or '未获取到文章')
                return

            plan = album_core.build_plan(articles)
            self._plan = plan
            self.log(f"共{len(plan)} 篇文章，开始下载\n")

            results, failed = extractor.run(
                plan=plan, max_retries=self.retry_times)

            problems = extractor.verify(self._plan)
            results = results, failed
            self.skipped_count = extractor.skipped
            self.finished_signal.emit(results[0], failed)

        except Exception as e:
            self.error_signal.emit(f"{type(e).__name__}: {e}")


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.worker = None
        self.settings = QSettings("AlbumExtractor", "WeChatAlbum")
        self.start_time = None

        self.setWindowTitle(f"{APP_TITLE}  {VERSION}")
        self.resize(960, 720)
        self.setMinimumSize(820, 600)

        self._init_ui()
        self._apply_style()
        self._load_settings()

    # ==================== 界面 ====================
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 12)

        root.addWidget(self._build_source_group())
        root.addWidget(self._build_option_group())
        root.addWidget(self._build_action_group(), 0)
        root.addWidget(self._build_progress_group())
        root.addWidget(self._build_log_group(), 1)

        self.status = QStatusBar()
        self.status.showMessage("就绪")
        self.setStatusBar(self.status)

    def _build_source_group(self):
        box = QGroupBox("合集链接")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "粘贴公众号合集链接，例：https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&album_id=...")
        self.url_edit.setMinimumHeight(32)
        self.url_edit.textChanged.connect(self._on_url_changed)
        row.addWidget(self.url_edit, 1)

        paste_btn = QPushButton("粘贴")
        paste_btn.setFixedWidth(64)
        paste_btn.clicked.connect(self._on_paste)
        row.addWidget(paste_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(64)
        clear_btn.clicked.connect(self.url_edit.clear)
        row.addWidget(clear_btn)

        layout.addLayout(row)

        self.link_hint = QLabel("支持 mp.weixin.qq.com 的合集 / 专辑链接")
        self.link_hint.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self.link_hint)

        return box

    def _build_option_group(self):
        box = QGroupBox("导出选项")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        # 输出目录
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("输出目录"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setMinimumHeight(30)
        row1.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.setFixedWidth(88)
        browse_btn.clicked.connect(self._on_browse)
        row1.addWidget(browse_btn)
        layout.addLayout(row1)

        # 格式 + 重试
        row2 = QHBoxLayout()

        fmt_box = QHBoxLayout()
        fmt_box.addWidget(QLabel("格式"))
        self.chk_md = QCheckBox("Markdown")
        self.chk_md.setChecked(True)
        self.chk_html = QCheckBox("HTML")
        self.chk_excel = QCheckBox("元数据表格")
        fmt_box.addWidget(self.chk_md)
        fmt_box.addWidget(self.chk_html)
        fmt_box.addWidget(self.chk_excel)
        fmt_box.addStretch()
        row2.addLayout(fmt_box, 1)

        retry_box = QHBoxLayout()
        retry_box.addWidget(QLabel("失败重试次数"))
        self.spin_retry = QSpinBox()
        self.spin_retry.setRange(1, 8)
        self.spin_retry.setValue(4)
        self.spin_retry.setFixedWidth(64)
        retry_box.addWidget(self.spin_retry)

        self.chk_skip = QCheckBox("跳过已存在（断点续传）")
        self.chk_skip.setToolTip("勾选后不重复下载已抓取成功的文章，\n"
                                 "适合上次未完成时继续，避免触发微信限流。")
        retry_box.addSpacing(12)
        retry_box.addWidget(self.chk_skip)
        row2.addLayout(retry_box)

        layout.addLayout(row2)
        return box

    def _build_action_group(self):
        box = QGroupBox("操作")
        layout = QHBoxLayout(box)

        self.btn_start = QPushButton("开始提取")
        self.btn_start.setFixedHeight(36)
        self.btn_start.setMinimumWidth(130)
        self.btn_start.clicked.connect(self._on_start)
        layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setMinimumWidth(100)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        layout.addWidget(self.btn_stop)

        self.btn_open = QPushButton("打开输出目录")
        self.btn_open.setFixedHeight(36)
        self.btn_open.setMinimumWidth(130)
        self.btn_open.clicked.connect(self._on_open_dir)
        layout.addWidget(self.btn_open)

        layout.addStretch()
        return box

    def _build_progress_group(self):
        box = QGroupBox("进度")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.lbl_current = QLabel("尚未开始")
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(22)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.lbl_stats = QLabel("成功 0    失败 0")
        self.lbl_stats.setStyleSheet("color: #555555;")
        layout.addWidget(self.lbl_stats)

        return box

    def _build_log_group(self):
        box = QGroupBox("运行日志")
        layout = QVBoxLayout(box)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save_log = QPushButton("保存日志")
        self.btn_save_log.clicked.connect(self._on_save_log)
        btn_row.addWidget(self.btn_save_log)

        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.log_edit.clear)
        btn_row.addWidget(self.btn_clear_log)

        layout.addLayout(btn_row)
        return box

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #f5f6f8; }
            QGroupBox {
                font-weight: 600; border: 1px solid #d8dce3;
                border-radius: 6px; margin-top: 10px; background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 4px;
                color: #2c3e50;
            }
            QLineEdit, QTextEdit, QSpinBox, QComboBox {
                border: 1px solid #d0d5dd; border-radius: 5px;
                padding: 4px 8px; background: #ffffff;
            }
            QLineEdit:focus { border-color: #3b7ddd; }
            QPushButton {
                background: #ffffff; border: 1px solid #c8cdd6;
                border-radius: 5px; padding: 6px 14px;
            }
            QPushButton:hover { background: #eef3fb; border-color: #3b7ddd; }
            QPushButton:disabled { background: #f0f1f3; color: #aab0ba; }
            QPushButton#primary {
                background: #3b7ddd; color: #ffffff;
                border: none; font-weight: 600;
            }
            QPushButton#primary:hover { background: #2f6bc4; }
            QPushButton#primary:disabled { background: #a8c3e8; }
            QProgressBar {
                border: 1px solid #d0d5dd; border-radius: 11px;
                background: #ffffff; text-align: center;
            }
            QProgressBar::chunk { background: #3b7ddd; border-radius: 10px; }
        """)
        self.btn_start.setObjectName("primary")

    # ==================== 交互 ====================
    def _on_url_changed(self, text):
        ok = bool(text.strip()) and 'album_id=' in text
        self.link_hint.setText("链接格式正确，可以开始提取" if ok
                               else "支持 mp.weixin.qq.com 的合集 / 专辑链接")
        self.link_hint.setStyleSheet(
            "color: #1a9e5f; font-size: 11px;" if ok
            else "color: #888888; font-size: 11px;")

    def _on_paste(self):
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _on_open_dir(self):
        d = self.dir_edit.text().strip()
        if not d or not os.path.isdir(d):
            QMessageBox.warning(self, "提示", "输出目录不存在，请先浏览选择。")
            return
        os.startfile(os.path.abspath(d))

    def _on_save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", f"提取日志_{datetime.now():%Y%m%d_%H%M%S}.txt",
            "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.log_edit.toPlainText())
            QMessageBox.information(self, "完成", f"日志已保存到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    def _on_start(self):
        if self.worker and self.worker.isRunning():
            return

        url = self.url_edit.text().strip()
        info = album_core.parse_album_url(url)
        if info['error']:
            QMessageBox.warning(self, "链接有误", info['error'])
            return

        out_dir = self.dir_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "提示", "请选择输出目录。")
            return

        formats = set()
        if self.chk_md.isChecked():
            formats.add('md')
        if self.chk_html.isChecked():
            formats.add('html')
        if self.chk_excel.isChecked():
            formats.add('excel')
        if not formats:
            QMessageBox.warning(self, "提示", "请至少选择一种导出格式。")
            return

        self._append_log("=" * 52)
        self._append_log(f"任务开始  {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._append_log(f"输出目录：{out_dir}")
        self._append_log("")

        self._set_running(True)
        self.progress.setValue(0)
        self.lbl_current.setText("正在获取文章列表...")
        self.lbl_stats.setText("成功 0    失败 0")

        self.start_time = time.time()
        self.worker = ExtractWorker(url, out_dir, formats,
                                    skip_existing=self.chk_skip.isChecked())
        self.worker.retry_times = self.spin_retry.value()
        self.worker.log_signal.connect(self._append_log)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error_signal.connect(self._on_error)
        self.worker.start()

    def _on_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self._append_log("正在停止，请等待当前文章处理完毕...")
            self.btn_stop.setEnabled(False)

    def _on_progress(self, cur, total, title):
        self.progress.setMaximum(total)
        self.progress.setValue(cur)
        self.lbl_current.setText(f"[{cur}/{total}] {title}")

    def _on_finished(self, results, failed):
        elapsed = int(time.time() - self.start_time) if self.start_time else 0
        skipped = getattr(self.worker, 'skipped_count', 0) if self.worker else 0
        self._set_running(False)
        self.progress.setValue(self.progress.maximum())

        stats = f"成功 {len(results)}    失败 {len(failed)}"
        if skipped:
            stats += f"    跳过 {skipped}"
        self.lbl_stats.setText(stats)

        self._append_log("")
        self._append_log("=" * 52)
        self._append_log(f"提取完成：成功 {len(results)} 篇，失败 {len(failed)} 篇"
                         + (f"，跳过 {skipped} 篇" if skipped else ""))
        self._append_log(f"总耗时：{elapsed // 60} 分 {elapsed % 60} 秒")
        self._append_log(f"输出目录：{self.dir_edit.text().strip()}")
        self._append_log("=" * 52)

        self.status.showMessage(f"完成：成功 {len(results)}，失败 {len(failed)}")

        if failed:
            detail = "\n".join(f"· [{x.get('no', 0)}] {x.get('title', '')[:34]}"
                               f"\n    {x.get('error', '')[:60]}"
                               for x in failed[:8])
            more = f"\n...另有 {len(failed) - 8} 篇失败" if len(failed) > 8 else ""
            tip = ("\n\n提示：稍作等待后勾选「跳过已存在」重跑，"
                   "可只补齐失败的文章，避免重复触发微信限流。"
                   if failed else "")
            QMessageBox.warning(
                self, "提取完成（有失败）",
                f"成功 {len(results)} 篇，失败 {len(failed)} 篇。\n\n"
                f"{detail}{more}\n\n失败原因多为微信访问频控，稍后重试即可。{tip}")
        else:
            done = len(results) + skipped
            QMessageBox.information(
                self, "提取完成",
                f"{done} 篇文章已就绪。\n耗时 {elapsed // 60} 分 {elapsed % 60} 秒。")

    def _on_error(self, msg):
        self._set_running(False)
        self._append_log(f"任务失败：{msg}")
        self.lbl_current.setText("任务失败")
        self.status.showMessage("任务失败")
        QMessageBox.critical(self, "任务失败", msg)

    def _append_log(self, msg):
        self.log_edit.append(msg)
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum())

    def _set_running(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.url_edit.setEnabled(not running)
        self.dir_edit.setEnabled(not running)
        self.status.showMessage("正在提取..." if running else "就绪")

    # ==================== 配置持久化 ====================
    def _load_settings(self):
        s = self.settings
        self.url_edit.setText(s.value("url", ""))
        self.dir_edit.setText(s.value("dir", os.path.join(os.getcwd(), "output")))
        self.chk_md.setChecked(s.value("md", True, type=bool))
        self.chk_html.setChecked(s.value("html", False, type=bool))
        self.chk_excel.setChecked(s.value("excel", False, type=bool))
        self.spin_retry.setValue(s.value("retry", 4, type=int))
        self.chk_skip.setChecked(s.value("skip", False, type=bool))

    def _save_settings(self):
        s = self.settings
        s.setValue("url", self.url_edit.text())
        s.setValue("dir", self.dir_edit.text())
        s.setValue("md", self.chk_md.isChecked())
        s.setValue("html", self.chk_html.isChecked())
        s.setValue("excel", self.chk_excel.isChecked())
        s.setValue("retry", self.spin_retry.value())
        s.setValue("skip", self.chk_skip.isChecked())

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            r = QMessageBox.question(
                self, "任务进行中", "任务仍在运行，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(3000)
        self._save_settings()
        event.accept()


def main():
    # Qt6 中高DPI缩放默认开启，这两个属性已废弃，无需显式设置
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("AlbumExtractor")

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()