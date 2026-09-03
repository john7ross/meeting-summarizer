"""Speaker management dialog.

Shown automatically after WhisperX transcription when diarisation markers
([SPEAKER_NN]) are detected, and also available manually from the Results bar.

Design
------
One card per unique speaker. Inside each card:
  • a name input (default = the raw label, e.g. SPEAKER_00)
  • that speaker's utterances listed in chronological order, each with its
    [HH:MM:SS] timestamp and an editable text field

Because the underlying data is time-ordered, each speaker's lines appear in the
real dialogue sequence — the user reads them top-to-bottom to recognise who was
talking, types the real name once, and optionally fixes recognition errors in
any individual line. On Save we rename the label and apply per-line edits while
keeping every timestamp and the global chronological order intact.

Signals
-------
  accepted_data(str, list)  — (new_transcript_text, participants_list)
  cancelled()
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from ..backend.speakers import (
    Utterance, apply_edited_utterances, extract_speakers,
    parse_utterances, speaker_stats, utterances_for_speaker,
)

# ── i18n ─────────────────────────────────────────────────────────────────────
_L = {
    "ru": {
        "title":          "Управление спикерами",
        "subtitle":       "Прослушайте реплики по порядку, укажите имена участников и при необходимости поправьте текст",
        "name_ph":        "Имя участника…",
        "stats_title":    "Статистика",
        "total_speakers": "Спикеров",
        "total_words":    "Слов всего",
        "total_segments": "Реплик",
        "speaking_time":  "Доля речи",
        "save":           "Сохранить и продолжить",
        "cancel":         "Пропустить (оставить как есть)",
        "no_speakers":    "Разделение по спикерам не найдено в транскрипте.",
        "words":          "сл.",
    },
    "en": {
        "title":          "Manage Speakers",
        "subtitle":       "Read the lines in order, assign participant names and optionally fix the text",
        "name_ph":        "Participant name…",
        "stats_title":    "Statistics",
        "total_speakers": "Speakers",
        "total_words":    "Total words",
        "total_segments": "Utterances",
        "speaking_time":  "Speaking share",
        "save":           "Save & continue",
        "cancel":         "Skip (keep as-is)",
        "no_speakers":    "No speaker separation found in transcript.",
        "words":          "w",
    },
}


def _t(key: str, lang: str) -> str:
    return _L.get(lang, _L["en"]).get(key, key)


# ── one editable utterance row ────────────────────────────────────────────────

class _UtteranceRow(QWidget):
    """A single line: [timestamp] + editable one-line text field.

    Holds the global utterance index so edits map back to the right line.
    """

    def __init__(self, global_index: int, utt: Utterance,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.global_index = global_index
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(8)

        ts = QLabel(utt.timestamp or "—")
        ts.setObjectName("utteranceTs")
        ts.setFixedWidth(72)
        ts.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        row.addWidget(ts)

        self.edit = QPlainTextEdit()
        self.edit.setObjectName("utteranceText")
        self.edit.setPlainText(utt.text)
        self.edit.setTabChangesFocus(True)
        # Auto-size: small, grows with content up to a cap
        self.edit.setMinimumHeight(28)
        self.edit.setMaximumHeight(96)
        self.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        row.addWidget(self.edit, 1)

    def text(self) -> str:
        return self.edit.toPlainText().strip()


# ── speaker card ──────────────────────────────────────────────────────────────

class _SpeakerCard(QFrame):
    """Name input + chronological list of this speaker's editable utterances."""

    def __init__(self, speaker_id: str, all_utterances: list[Utterance],
                 lang: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.speaker_id = speaker_id
        self.setObjectName("speakerCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # Header: raw label + name field
        header = QHBoxLayout()
        id_lbl = QLabel(f"<b>{speaker_id}</b>")
        id_lbl.setMinimumWidth(110)
        header.addWidget(id_lbl)
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("speakerNameEdit")
        self.name_edit.setPlaceholderText(_t("name_ph", lang))
        self.name_edit.setText(speaker_id)
        header.addWidget(self.name_edit, 1)
        layout.addLayout(header)

        # Chronological utterance rows for this speaker
        self._rows: list[_UtteranceRow] = []
        for idx, utt in enumerate(all_utterances):
            if utt.speaker != speaker_id:
                continue
            row = _UtteranceRow(idx, utt)
            self._rows.append(row)
            layout.addWidget(row)

    def speaker_name(self) -> str:
        name = self.name_edit.text().strip()
        return name if name else self.speaker_id

    def edited_map(self) -> dict[int, str]:
        """global_index -> edited text, for every row in this card."""
        return {r.global_index: r.text() for r in self._rows}


# ── stats sidebar ─────────────────────────────────────────────────────────────

class _StatsSidebar(QWidget):
    def __init__(self, utterances: list[Utterance], speakers: list[str],
                 lang: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        title = QLabel(f"<b>{_t('stats_title', lang)}</b>")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        stats = speaker_stats(utterances)
        total_words = sum(v["words"] for v in stats.values())
        total_segs = sum(v["segments"] for v in stats.values())

        def _kv(label: str, value: str) -> None:
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.addWidget(QLabel(label))
            rh.addStretch(1)
            rh.addWidget(QLabel(f"<b>{value}</b>"))
            layout.addWidget(row)

        _kv(_t("total_speakers", lang), str(len(speakers)))
        _kv(_t("total_words", lang), str(total_words))
        _kv(_t("total_segments", lang), str(total_segs))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("analysisSep")
        layout.addWidget(sep)

        layout.addWidget(QLabel(f"<b>{_t('speaking_time', lang)}</b>"))
        for spk in speakers:
            s = stats.get(spk, {"words": 0})
            pct = int(s["words"] / total_words * 100) if total_words else 0
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 2, 0, 2)
            bv.setSpacing(2)
            head = QHBoxLayout()
            head.addWidget(QLabel(spk))
            head.addStretch(1)
            head.addWidget(QLabel(f"{pct}%  ({s['words']} {_t('words', lang)})"))
            bv.addLayout(head)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setObjectName("analysisBar")
            bar.setStyleSheet(
                "QProgressBar::chunk{background:#007acc;border-radius:3px;}"
                "QProgressBar{background:rgba(255,255,255,.1);border-radius:3px;border:none;}"
            )
            bv.addWidget(bar)
            layout.addWidget(box)

        layout.addStretch(1)


# ── dialog ────────────────────────────────────────────────────────────────────

class SpeakersDialog(QDialog):
    accepted_data = Signal(str, list)   # transcript, participants
    cancelled     = Signal()

    def __init__(self, transcript: str, language: str = "ru",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lang = language
        self._transcript = transcript
        self._utterances = parse_utterances(transcript)
        self._speakers = extract_speakers(transcript)

        self.setWindowTitle(_t("title", language))
        self.setMinimumSize(880, 600)
        self.resize(1120, 700)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        sub = QLabel(_t("subtitle", self._lang))
        sub.setObjectName("hint")
        sub.setWordWrap(True)
        root.addWidget(sub)

        if not self._speakers:
            root.addWidget(QLabel(_t("no_speakers", self._lang)))
        else:
            splitter = QSplitter(Qt.Horizontal)

            left = QWidget()
            ll = QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(10)
            self._cards: list[_SpeakerCard] = []
            for spk in self._speakers:
                card = _SpeakerCard(spk, self._utterances, self._lang)
                self._cards.append(card)
                ll.addWidget(card)
            ll.addStretch(1)

            left_scroll = QScrollArea()
            left_scroll.setWidgetResizable(True)
            left_scroll.setFrameShape(QFrame.Shape.NoFrame)
            left_scroll.setWidget(left)
            splitter.addWidget(left_scroll)

            right_scroll = QScrollArea()
            right_scroll.setWidgetResizable(True)
            right_scroll.setFrameShape(QFrame.Shape.NoFrame)
            right_scroll.setWidget(
                _StatsSidebar(self._utterances, self._speakers, self._lang))
            right_scroll.setMinimumWidth(220)
            right_scroll.setMaximumWidth(300)
            splitter.addWidget(right_scroll)

            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 1)
            root.addWidget(splitter, 1)

        btn_box = QDialogButtonBox()
        self._btn_save = btn_box.addButton(
            _t("save", self._lang), QDialogButtonBox.AcceptRole)
        self._btn_save.setProperty("variant", "primary")
        btn_box.addButton(_t("cancel", self._lang), QDialogButtonBox.RejectRole)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self._on_cancel)
        root.addWidget(btn_box)

    # -- slots ---------------------------------------------------------
    def _on_save(self) -> None:
        if not self._speakers:
            self.cancelled.emit()
            self.accept()
            return

        name_map: dict[str, str] = {}
        edited: dict[int, str] = {}
        for card in self._cards:
            name_map[card.speaker_id] = card.speaker_name()
            edited.update(card.edited_map())

        new_transcript = apply_edited_utterances(
            self._utterances, name_map, edited)
        participants = [
            name_map.get(spk, spk) for spk in self._speakers
            if name_map.get(spk, spk).strip()
        ]
        self.accepted_data.emit(new_transcript, participants)
        self.accept()

    def _on_cancel(self) -> None:
        self.cancelled.emit()
        self.reject()
