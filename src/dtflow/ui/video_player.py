"""Inline playback of a saved video in the Images detail view (QtMultimedia from PySide6-Addons).

The player is created on the first Play of an item and fully released (stopped, source cleared, widgets deleted) when
the selection changes, the page is left, the detail is cleared or the file is about to be recycled, so no file handle
stays open. Nothing here touches the disk on the UI thread: the caller resolves and checks the MP4 on a worker and
hands over the path. "Open video in player" stays as the fallback when playback cannot start.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

SLIDER_SCALE = 1000


def clock(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds // 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


class VideoPlayer(QObject):
    def __init__(self, surface: QWidget, on_problem) -> None:
        super().__init__(surface)
        self.surface = surface
        self._on_problem = on_problem
        self._panel: QWidget | None = None
        self._player = None
        self._audio = None
        self._duration = 0
        self._muted = False
        self.play_button = QPushButton("▶  Play video", surface)
        self.play_button.setObjectName("galleryPlayVideoButton")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.hide()
        surface.installEventFilter(self)

    # -- item -----------------------------------------------------------------------------------------------------
    def show_for_video(self, is_video: bool) -> None:
        """The poster of a video item gets the Play button; any other item releases the player."""
        self.release()
        self.play_button.setVisible(is_video)
        self._place()

    @property
    def playing(self) -> bool:
        return self._panel is not None

    def start(self, path: str) -> None:
        """Called on the UI thread with an MP4 path a worker has already found."""
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.release()
        self.play_button.hide()
        panel = QWidget(self.surface)
        panel.setObjectName("galleryVideoPanel")
        panel.setStyleSheet("#galleryVideoPanel { background: black; }")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        video = QVideoWidget(panel)
        column.addWidget(video, 1)
        bar = QWidget(panel)
        row = QHBoxLayout(bar)
        row.setContentsMargins(330, 4, 56, 4)  # clear of the position overlay and the next arrow
        toggle = QPushButton("Pause", bar)
        slider = QSlider(Qt.Orientation.Horizontal, bar)
        slider.setRange(0, SLIDER_SCALE)
        label = QLabel("0:00 / 0:00", bar)
        mute = QPushButton("Mute", bar)
        close = QPushButton("Close", bar)
        for widget in (toggle, slider, label, mute, close):
            row.addWidget(widget, 1 if widget is slider else 0)
        column.addWidget(bar)
        player, audio = QMediaPlayer(panel), QAudioOutput(panel)
        player.setAudioOutput(audio)
        player.setVideoOutput(video)
        audio.setMuted(self._muted)
        mute.setText("Unmute" if self._muted else "Mute")
        self._panel, self._player, self._audio, self._duration = panel, player, audio, 0

        def toggle_play() -> None:
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                player.pause()
            else:
                if player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                    player.setPosition(0)
                player.play()

        def moved(position: int) -> None:
            if not slider.isSliderDown() and self._duration:
                slider.setValue(int(position * SLIDER_SCALE / self._duration))
            label.setText(f"{clock(position)} / {clock(self._duration)}")

        def duration(value: int) -> None:
            self._duration = value
            moved(player.position())

        def state(_value) -> None:
            playing = player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            toggle.setText("Pause" if playing else "Play")

        def seek(value: int) -> None:
            if self._duration:
                player.setPosition(int(value * self._duration / SLIDER_SCALE))

        def toggle_mute() -> None:
            self._muted = not audio.isMuted()
            audio.setMuted(self._muted)
            mute.setText("Unmute" if self._muted else "Mute")

        def failed(_error, text: str) -> None:
            self._on_problem(text or "The video could not be played here. Use Open video in player.")
            self.release(show_play=True)

        toggle.clicked.connect(toggle_play)
        slider.sliderMoved.connect(seek)
        slider.sliderReleased.connect(lambda: seek(slider.value()))
        mute.clicked.connect(toggle_mute)
        close.clicked.connect(lambda: self.release(show_play=True))
        player.positionChanged.connect(moved)
        player.durationChanged.connect(duration)
        player.playbackStateChanged.connect(state)
        player.errorOccurred.connect(failed)
        self._place()
        panel.show()
        panel.raise_()
        player.setSource(QUrl.fromLocalFile(path))
        player.play()

    def release(self, *, show_play: bool = False) -> None:
        """Stop, drop the source (frees the file) and delete the widgets."""
        panel, player = self._panel, self._player
        self._panel = self._player = self._audio = None
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
        if show_play and panel is not None:
            self.play_button.show()
            self._place()

    # -- layout ---------------------------------------------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:
        if watched is self.surface and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self) -> None:
        width, height = self.surface.width(), self.surface.height()
        self.play_button.adjustSize()
        self.play_button.move((width - self.play_button.width()) // 2, (height - self.play_button.height()) // 2)
        self.play_button.raise_()
        if self._panel is not None:
            self._panel.setGeometry(0, 0, width, height)
