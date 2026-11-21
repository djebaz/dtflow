"""In-app playback of a saved video in Images Detail.

Only the existing resolved MP4 enters QtMultimedia. Changing output, leaving
Images, and closing the player stop playback and release the file handle.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSize, Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

CONTROLS_FORM = Path(__file__).parent / "forms" / "gallery_video_controls.ui"
ICONS = Path(__file__).parents[1] / "resources" / "images" / "gallery"
SLIDER_SCALE = 1000


def clock(milliseconds: int) -> str:
    """Format media time without losing hours in long videos."""
    seconds = max(0, int(milliseconds // 1000))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class VideoPlayer(QObject):
    def __init__(self, surface: QWidget, on_problem, on_playback_changed=None) -> None:
        super().__init__(surface)
        self.surface = surface
        self._on_problem = on_problem
        self._on_playback_changed = on_playback_changed
        self._panel: QWidget | None = None
        self._player = None
        self._audio = None
        self._duration = 0
        self._muted = False
        self.play_button = QPushButton(surface)
        self.play_button.setObjectName("galleryPlayVideoButton")
        self.play_button.setIcon(QIcon(str(ICONS / "video-play.svg")))
        self.play_button.setIconSize(QSize(34, 34))
        self.play_button.setFixedSize(76, 76)
        self.play_button.setToolTip("Play video")
        self.play_button.setAccessibleName("Play video")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.hide()
        surface.installEventFilter(self)

    def show_for_video(self, is_video: bool) -> None:
        """Show the poster Play affordance only when a companion MP4 is available."""
        self.release()
        self.play_button.setVisible(is_video)
        self._place()

    @property
    def playing(self) -> bool:
        """True while loading, playing, paused or finished in the inline player."""
        return self._panel is not None

    def start(self, path: str) -> None:
        """Start a local MP4 already resolved on the gallery worker."""
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.release()
        panel = QWidget(self.surface)
        panel.setObjectName("galleryVideoPanel")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        video = QVideoWidget(panel)
        video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        column.addWidget(video, 1)

        file = QFile(str(CONTROLS_FORM))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            panel.deleteLater()
            raise OSError("Video playback controls form could not be opened.")
        try:
            bar = QUiLoader().load(file, panel)
        finally:
            file.close()
        if bar is None:
            panel.deleteLater()
            raise RuntimeError("Video playback controls form could not be loaded.")
        toggle = bar.findChild(QPushButton, "videoPlaybackToggle")
        slider = bar.findChild(QSlider, "videoPlaybackSeek")
        time = bar.findChild(QLabel, "videoPlaybackTime")
        mute = bar.findChild(QPushButton, "videoPlaybackMute")
        restart = bar.findChild(QPushButton, "videoPlaybackRestart")
        close = bar.findChild(QPushButton, "videoPlaybackClose")
        slider.setRange(0, SLIDER_SCALE)
        slider.setToolTip("Seek video. W/X: 1 second, Numpad 4/6: 10 seconds.")
        for button, name in (
            (toggle, "video-pause.svg"),
            (mute, "video-muted.svg" if self._muted else "video-volume.svg"),
            (restart, "refresh.svg"),
            (close, "circle-x.svg"),
        ):
            button.setIcon(QIcon(str(ICONS / name)))
            button.setIconSize(QSize(18, 18))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        column.addWidget(bar)

        player, audio = QMediaPlayer(panel), QAudioOutput(panel)
        player.setAudioOutput(audio)
        player.setVideoOutput(video)
        audio.setMuted(self._muted)
        self._panel, self._player, self._audio, self._duration = panel, player, audio, 0
        self.play_button.hide()

        def update_time(position: int) -> None:
            if not slider.isSliderDown() and self._duration:
                slider.setValue(int(position * SLIDER_SCALE / self._duration))
            time.setText(f"{clock(position)} / {clock(self._duration)}")

        def duration_changed(value: int) -> None:
            if self._player is player:
                self._duration = max(0, value)
                update_time(player.position())

        def state_changed(_value=None) -> None:
            if self._player is not player:
                return
            active = player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ended = player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
            toggle.setIcon(QIcon(str(ICONS / ("video-pause.svg" if active else "video-play.svg"))))
            description = "Pause video (Space/K)" if active else "Replay video" if ended else "Play video (Space/K)"
            toggle.setToolTip(description)
            toggle.setAccessibleName(description)

        def media_status(status) -> None:
            if self._player is not player:
                return
            if status in (QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.BufferingMedia):
                time.setText("Loading video…")
            else:
                update_time(player.position())
            state_changed()

        def seek(value: int) -> None:
            if self._duration:
                player.setPosition(int(value * self._duration / SLIDER_SCALE))

        def failure(_error, message: str) -> None:
            if self._player is player:
                self.release(show_play=True)
                self._on_problem(message or "Video playback failed. Use Open video in player.")

        toggle.clicked.connect(self.toggle_play)
        slider.sliderMoved.connect(seek)
        slider.sliderReleased.connect(lambda: seek(slider.value()))
        mute.clicked.connect(self.toggle_mute)
        restart.clicked.connect(self.restart)
        close.clicked.connect(lambda: self.release(show_play=True))
        player.positionChanged.connect(update_time)
        player.durationChanged.connect(duration_changed)
        player.playbackStateChanged.connect(state_changed)
        player.mediaStatusChanged.connect(media_status)
        player.errorOccurred.connect(failure)
        state_changed()
        if self._muted:
            mute.setToolTip("Unmute video (M)")
            mute.setAccessibleName("Unmute video")
        self._place()
        panel.show()
        panel.raise_()
        if self._on_playback_changed is not None:
            self._on_playback_changed(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        player.setSource(QUrl.fromLocalFile(path))
        player.play()

    def toggle_play(self) -> None:
        """Pause, resume or replay a finished video."""
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self._player.setPosition(0)
            self._player.play()

    def seek_delta(self, milliseconds: int) -> None:
        """Seek within the recorded player duration."""
        if self._player is not None and self._duration:
            self._player.setPosition(max(0, min(self._duration, self._player.position() + milliseconds)))

    def restart(self) -> None:
        """Return to 0:00 without changing paused/playing state."""
        if self._player is not None:
            self._player.setPosition(0)

    def toggle_mute(self) -> None:
        """Keep muted status across clips, with no separate volume shortcuts."""
        if self._audio is not None:
            self._muted = not self._audio.isMuted()
            self._audio.setMuted(self._muted)
            mute = self._panel.findChild(QPushButton, "videoPlaybackMute")
            mute.setIcon(QIcon(str(ICONS / ("video-muted.svg" if self._muted else "video-volume.svg"))))
            description = "Unmute video (M)" if self._muted else "Mute video (M)"
            mute.setToolTip(description)
            mute.setAccessibleName(description)

    def release(self, *, show_play: bool = False) -> None:
        """Stop, detach video/audio, clear the source and free the media handle."""
        panel, player = self._panel, self._player
        self._panel = self._player = self._audio = None
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if player is not None:
            try:
                player.stop()
                player.setVideoOutput(None)
                player.setAudioOutput(None)
                player.setSource(QUrl())
            except RuntimeError:
                pass
        if panel is not None:
            panel.hide()
            panel.deleteLater()
            if self._on_playback_changed is not None:
                self._on_playback_changed(False)
        if show_play and panel is not None:
            self.play_button.show()
            self._place()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.surface and event.type() == QEvent.Type.Resize:
            self._place()
            return False
        if self._panel is None or event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activeWindow() is not self.surface.window():
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)):
            return False
        if isinstance(focus, QComboBox) and focus.isEditable():
            return False
        modifiers = event.modifiers()
        if modifiers & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        key = event.key()
        keypad = bool(modifiers & Qt.KeyboardModifier.KeypadModifier)
        action = None
        if keypad and key in (Qt.Key.Key_4, Qt.Key.Key_Left):
            action = lambda: self.seek_delta(-10000)
        elif keypad and key in (Qt.Key.Key_6, Qt.Key.Key_Right):
            action = lambda: self.seek_delta(10000)
        elif keypad and key in (Qt.Key.Key_0, Qt.Key.Key_Insert):
            action = self.restart
        elif not keypad and key in (Qt.Key.Key_Space, Qt.Key.Key_K):
            action = self.toggle_play
        elif not keypad and key == Qt.Key.Key_W:
            action = lambda: self.seek_delta(-1000)
        elif not keypad and key == Qt.Key.Key_X:
            action = lambda: self.seek_delta(1000)
        elif not keypad and key == Qt.Key.Key_M:
            action = self.toggle_mute
        if action is None:
            return False
        if event.type() == QEvent.Type.KeyPress and (
            not event.isAutoRepeat()
            or key in (Qt.Key.Key_4, Qt.Key.Key_6, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_W, Qt.Key.Key_X)
        ):
            action()
        event.accept()
        return True

    def _place(self) -> None:
        width, height = self.surface.width(), self.surface.height()
        self.play_button.move((width - self.play_button.width()) // 2, (height - self.play_button.height()) // 2)
        self.play_button.raise_()
        if self._panel is not None:
            self._panel.setGeometry(0, 0, width, height)
