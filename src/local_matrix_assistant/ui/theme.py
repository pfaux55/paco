from __future__ import annotations

import colorsys
from functools import lru_cache
import re

from local_matrix_assistant.core.config import (
    DEFAULT_CHAT_FONT_FAMILY,
    DEFAULT_CHAT_FONT_SIZE,
    normalize_chat_font_family,
    normalize_chat_font_size,
)


THEME_OPTIONS: tuple[tuple[str, str], ...] = (
    ("matrix", "Matrix Green"),
    ("ocean", "Ocean Blue"),
    ("violet", "Violet"),
    ("cyan", "Electric Cyan"),
    ("teal", "Teal"),
    ("pink", "Neon Pink"),
    ("orange", "Sunset Orange"),
    ("lime", "Acid Lime"),
    ("amber", "Amber"),
    ("red", "Crimson Red"),
)
THEME_PREVIEWS = {
    "matrix": ("#07140d", "#24e081"),
    "ocean": ("#070d14", "#247ee0"),
    "violet": ("#100714", "#8a4de0"),
    "cyan": ("#071214", "#24c8e0"),
    "teal": ("#071412", "#24e0c1"),
    "pink": ("#14070f", "#e0249a"),
    "orange": ("#140b07", "#e06f24"),
    "lime": ("#101407", "#8fe024"),
    "amber": ("#141007", "#e0a224"),
    "red": ("#140707", "#e04444"),
}
DEFAULT_THEME = "matrix"
_THEME_HUES = {
    "matrix": 0.39,
    "ocean": 0.58,
    "violet": 0.76,
    "cyan": 0.52,
    "teal": 0.47,
    "pink": 0.91,
    "orange": 0.06,
    "lime": 0.23,
    "amber": 0.10,
    "red": 0.0,
}


MATRIX_STYLESHEET = """
QWidget {
    background: #050c09;
    color: #e4eee7;
    selection-background-color: #1f8f52;
    selection-color: #f5fff8;
    font-family: "Consolas", "JetBrains Mono", "Courier New", monospace;
}
QMainWindow, QWidget#appRoot {
    background: #030806;
}
QLabel {
    background: transparent;
}
QWidget#startupOverlay {
    background: transparent;
}
QFrame#topBar {
    background: transparent;
    border: none;
    border-radius: 0;
}
QWidget#compactAssistant {
    background: transparent;
}
QScrollArea#compactTranscript,
QWidget#compactTranscriptHost {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    border: none;
    width: 8px;
    margin: 3px 0;
}
QScrollBar:horizontal {
    background: transparent;
    border: none;
    height: 8px;
    margin: 0 3px;
}
QScrollBar::handle:vertical {
    background: #35684a;
    border: none;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:horizontal {
    background: #35684a;
    border: none;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {
    background: #24e081;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    background: transparent;
    border: none;
    height: 0;
}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    background: transparent;
    border: none;
    width: 0;
}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: transparent;
}
QFrame#compactInputBar {
    background: #07100b;
    border: 1px solid #24583c;
    border-radius: 24px;
}
QFrame#compactMessageAssistant {
    background: #09130e;
    border: 1px solid #24583c;
    border-radius: 14px;
}
QLabel#compactMessageRole {
    color: #6f927b;
    font-size: 9px;
    font-weight: 800;
}
QLabel#compactMessageBody {
    color: #e4eee7;
}
QLabel#compactStatusIndicator {
    color: #718f7c;
    font-size: 14px;
}
QLabel#compactStatusIndicator[assistantState="ready"] {
    color: #24e081;
}
QLabel#compactStatusIndicator[assistantState="loading"],
QLabel#compactStatusIndicator[assistantState="streaming"] {
    color: #e0a224;
}
QLabel#compactStatusIndicator[assistantState="error"] {
    color: #e04444;
}
QLabel#compactStatusLabel,
QLabel#compactCaptureNote {
    background: #09130e;
    border: 1px solid #24583c;
    border-radius: 10px;
    color: #9eb8a7;
    font-size: 10px;
    padding: 6px 9px;
}
QPushButton#compactCaptureButton,
QPushButton#compactMainButton,
QPushButton#compactCloseButton {
    background: transparent;
    border: none;
    color: #9eb8a7;
    padding: 0;
}
QPushButton#compactCaptureButton {
    border-radius: 15px;
    font-size: 14px;
}
QPushButton#compactCaptureButton:hover,
QPushButton#compactMainButton:hover,
QPushButton#compactCloseButton:hover {
    background: #102b1c;
    color: #e4eee7;
}
QLineEdit#compactPromptInput {
    background: transparent;
    border: none;
    min-height: 32px;
    padding: 0 4px;
}
QFrame#systemNoticeWarning,
QFrame#systemNoticeError,
QFrame#systemNoticeInfo {
    border-radius: 13px;
}
QFrame#systemNoticeWarning {
    background: #261e0c;
    border: 1px solid #8f6e24;
}
QFrame#systemNoticeError {
    background: #2b1313;
    border: 1px solid #9d4646;
}
QFrame#systemNoticeInfo {
    background: #0b2018;
    border: 1px solid #287656;
}
QLabel#systemNoticeMessage {
    color: #edf3ee;
    font-weight: 650;
}
QPushButton#systemNoticeAction {
    background: #102b1c;
    border: 1px solid #36b971;
    border-radius: 9px;
    color: #effff4;
    padding: 7px 11px;
    font-weight: 700;
}
QPushButton#systemNoticeAction:hover {
    background: #16442a;
}
QPushButton#systemNoticeDismiss {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: #b8c7bd;
    min-width: 30px;
    max-width: 30px;
    padding: 6px 0;
}
QPushButton#systemNoticeDismiss:hover {
    background: rgba(255, 255, 255, 0.06);
    border-color: #52655a;
    color: #ffffff;
}
QPushButton#headerActionButton {
    background: transparent;
    border: 1px solid #1d412e;
    border-radius: 11px;
    color: #9eb8a7;
    padding: 8px 12px;
}
QPushButton#headerActionButton:hover {
    background: #0c1d14;
    border-color: #2d6a48;
    color: #eff8f1;
}
QPushButton#sidebarToggleButton {
    background: #0a1710;
    border: 1px solid #214332;
    border-radius: 11px;
    color: #c7d8cc;
    padding: 8px 12px;
    font-weight: 700;
}
QPushButton#sidebarToggleButton:hover,
QPushButton#sidebarToggleButton:checked {
    background: #10271a;
    border-color: #2b6848;
    color: #f1fff5;
}
QFrame#shortcutCard {
    background: #07140d;
    border: 1px solid #1e432f;
    border-radius: 14px;
}
QLabel#shortcutKey {
    background: #0c2116;
    border: 1px solid #2d6245;
    border-radius: 7px;
    color: #bdf5ce;
    padding: 4px 8px;
    font-weight: 700;
}
QLabel#appLogo {
    background: transparent;
    border: none;
    border-radius: 17px;
}
QLabel#title {
    color: #f4f8f5;
    font-size: 30px;
    font-weight: 800;
}
QWidget#sidebar {
    background: transparent;
}
QFrame#historyPanel {
    background: #07100c;
    border: 1px solid #183428;
    border-radius: 24px;
}
QPushButton#navButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 13px 12px;
    color: #bac8bf;
    font-weight: 700;
}
QPushButton#navButton:hover {
    background: #0d1b14;
    color: #eef7f0;
}
QPushButton#navButton:checked {
    background: #0f2b1a;
    border-bottom: 2px solid #20df7c;
    color: #f4fff7;
}
QLabel#sidebarLabel {
    color: #72977f;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#sidebarActionButton {
    background: #0e1c15;
    border: 1px solid #1c392b;
    border-radius: 14px;
    padding: 16px 16px;
    color: #e4eee7;
    font-weight: 700;
}
QPushButton#sidebarActionButton:hover {
    background: #11251a;
    border-color: #28533c;
}
QPushButton#sidebarSecondaryButton {
    background: #0a1710;
    border: 1px solid #193527;
    border-radius: 11px;
    padding: 9px 10px;
    color: #b8cabd;
    font-weight: 700;
}
QPushButton#sidebarSecondaryButton:hover {
    background: #102219;
    border-color: #2b6043;
    color: #f0f7f2;
}
QPushButton#sidebarDangerButton {
    background: #321717;
    border: 1px solid #a34a4a;
    border-radius: 11px;
    padding: 9px 10px;
    color: #f3d0d0;
    font-weight: 700;
}
QPushButton#sidebarDangerButton:hover {
    background: #421d1d;
    border-color: #c45c5c;
}
QFrame#renameChatPanel {
    background: #081a11;
    border: 1px solid #28553d;
    border-radius: 13px;
}
QLabel#historyEmptyLabel {
    color: #718f7c;
    padding: 14px 8px;
}
QLineEdit#historySearch {
    border-radius: 12px;
    padding: 10px 12px;
}
QListWidget#historyList {
    background: transparent;
    border: none;
    padding: 0;
    color: #e4eee7;
}
QListWidget#historyList::item {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 14px;
    margin-bottom: 8px;
    padding: 14px;
    color: #dce8df;
}
QListWidget#historyList::item:hover {
    background: #0d1b14;
    border-color: #1c392b;
}
QListWidget#historyList::item:selected {
    background: #123a23;
    border-left: 3px solid #23e081;
    border-color: #24583c;
    color: #f5fff8;
}
QFrame#idleCard {
    background: #0b1711;
    border: 1px solid #1b3528;
    border-radius: 18px;
}
QLabel#idleAvatar {
    background: transparent;
    border: none;
}
QLabel#idleTitle {
    color: #f0f7f2;
    font-weight: 800;
}
QFrame#chatCanvas,
QFrame#agentCanvas,
QFrame#panel,
QFrame#voiceOnlyPanel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b1a12, stop:1 #060d0a);
    border: 1px solid #1a3a2b;
    border-radius: 26px;
}
QFrame#modelInstallCard {
    background: #07130d;
    border: 1px solid #24513a;
    border-radius: 16px;
}
QScrollArea#settingsScroll,
QWidget#settingsSurface,
QWidget#settingsField,
QWidget#settingsControlGroup {
    background: transparent;
}
QFrame#settingsCard {
    background: #09150f;
    border: 1px solid #1b392a;
    border-radius: 19px;
}
QFrame#settingsCard:hover {
    border-color: #28583e;
}
QLabel#settingsSectionTitle {
    color: #edf5ef;
    font-size: 15px;
    font-weight: 800;
}
QLabel#settingsSectionDescription,
QLabel#settingsFieldHelp {
    color: #839d8b;
    font-size: 10px;
}
QLabel#settingsFieldLabel {
    color: #cfe0d4;
    font-size: 11px;
    font-weight: 700;
}
QComboBox#settingsControl,
QLineEdit#settingsControl {
    background: #07120c;
    border: 1px solid #224431;
    border-radius: 11px;
    color: #edf5ef;
    min-height: 24px;
    padding: 8px 11px;
}
QComboBox#settingsControl:hover,
QLineEdit#settingsControl:hover {
    background: #0a1911;
    border-color: #326348;
}
QComboBox#settingsControl:focus,
QLineEdit#settingsControl:focus {
    border-color: #31b96d;
}
QLabel#settingsInsetTitle {
    color: #dcebe1;
    font-size: 12px;
    font-weight: 800;
}
QLabel#settingsLocalBadge {
    background: #10301e;
    border: 1px solid #286644;
    border-radius: 8px;
    color: #68d993;
    font-size: 9px;
    font-weight: 800;
    padding: 3px 7px;
}
QLabel#settingsModelDetails {
    background: #0b1d13;
    border: 1px solid #1e432f;
    border-radius: 10px;
    color: #b8d1bf;
    padding: 9px 11px;
}
QLabel#settingsInstallStatus {
    color: #91ad99;
    font-size: 10px;
}
QPushButton#modelInstallButton {
    background: #176d40;
    border-color: #28c975;
    color: #f4fff7;
    min-width: 88px;
}
QPushButton#modelInstallButton:hover {
    background: #1b8050;
    border-color: #3bea91;
}
QFrame#settingsActionBar {
    background: #08130d;
    border: 1px solid #1d3d2c;
    border-radius: 17px;
}
QWidget#settingsStatus,
QWidget#settingsStatus QLabel#statusStrip {
    background: transparent;
}
QWidget#settingsStatus QLabel#statusStrip {
    color: #9eb8a7;
    padding: 0 4px;
}
QProgressBar#modelInstallProgress {
    min-height: 6px;
    max-height: 6px;
    background: #10251a;
    border: none;
    border-radius: 3px;
}
QProgressBar#modelInstallProgress::chunk {
    background: #24df80;
    border-radius: 3px;
}
QWidget#chatScreen,
QWidget#conversationSurface,
QWidget#conversationViewport,
QStackedWidget#chatContentStack {
    background: transparent;
}
QScrollArea#conversationScroll,
QScrollArea {
    border: none;
    background: transparent;
}
QFrame#composerDock {
    background: #09150f;
    border: 1px solid #1b382a;
    border-radius: 22px;
}
QFrame#composerDock[dragActive="true"] {
    background: #0d2418;
    border: 2px solid #2bce78;
}
QLabel#agentScopeLabel {
    color: #6f927b;
    font-size: 9px;
    font-weight: 800;
}
QComboBox#agentPermissionMode {
    background: #07120c;
    border: 1px solid #31563f;
    border-radius: 9px;
    color: #c9ded0;
    min-height: 27px;
    padding: 2px 9px;
}
QComboBox#agentPermissionMode[accessMode="read_only"] {
    background: #191406;
    border-color: #80682f;
    color: #ead895;
}
QComboBox#agentTimelineFilter {
    background: #07120c;
    border: 1px solid #284735;
    border-radius: 8px;
    color: #aac5b3;
    min-height: 25px;
    padding: 2px 8px;
}
QWidget#agentExecutionDetailsPage {
    background: transparent;
}
QComboBox#agentTaskDetailSelector {
    background: #07120c;
    border: 1px solid #2b503a;
    border-radius: 8px;
    color: #c3d8ca;
    min-height: 27px;
    padding: 2px 9px;
}
QFrame#attachmentTray {
    background: #07120c;
    border: 1px solid #1c3d2c;
    border-radius: 13px;
}
QFrame#editMessageBanner {
    background: #132317;
    border: 1px solid #3b704d;
    border-radius: 12px;
}
QLabel#editMessageLabel {
    color: #d8eadc;
    font-size: 11px;
}
QPushButton#messageEditCancelButton {
    background: transparent;
    border: 1px solid #315b40;
    border-radius: 9px;
    padding: 6px 10px;
    color: #b8d1bf;
    font-size: 11px;
}
QScrollArea#attachmentScroll,
QWidget#attachmentItemsHost {
    background: transparent;
    border: none;
}
QLabel#attachmentHeading {
    color: #78a88a;
    font-size: 10px;
    font-weight: 800;
}
QFrame#attachmentChip {
    background: #0d2116;
    border: 1px solid #27553c;
    border-radius: 10px;
}
QLabel#attachmentName,
QLabel#messageAttachmentSummary {
    color: #b8dac3;
    font-size: 11px;
}
QFrame#messageAttachmentPreviews {
    background: transparent;
    border: none;
}
QLabel#messageAttachmentThumbnail {
    background: #061009;
    border: 1px solid #2a5d41;
    border-radius: 9px;
}
QLabel#attachmentThumbnail {
    background: #061009;
    border: 1px solid #2a5d41;
    border-radius: 7px;
}
QLabel#messageAttachmentSummary {
    background: #0b1d13;
    border: 1px solid #214a34;
    border-radius: 10px;
    padding: 8px 10px;
}
QPushButton#attachmentButton {
    min-height: 46px;
    max-height: 46px;
    border-radius: 13px;
    padding: 0 10px;
    color: #b9d8c2;
}
QPushButton#attachmentRemoveButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 0;
    color: #8fb5a0;
    font-size: 16px;
}
QPushButton#attachmentRemoveButton:hover {
    background: #173323;
    border-color: #326348;
    color: #f0f7f2;
}
QPlainTextEdit[dragActive="true"] {
    background: #0d2418;
    border: 2px solid #2bce78;
}
QFrame#messageUser,
QFrame#messageAssistant {
    background: #09130e;
    border: 1px solid #173325;
    border-radius: 16px;
}
QFrame#messageUser {
    border-color: #24583c;
}
QPushButton#loadEarlierMessages {
    background: #0a1710;
    border: 1px solid #245139;
    border-radius: 11px;
    color: #9bc8a9;
    padding: 8px 14px;
    font-size: 11px;
}
QPushButton#loadEarlierMessages:hover {
    background: #10281a;
    border-color: #3a8158;
    color: #effff3;
}
QPushButton#jumpToLatestButton {
    background: #10281a;
    border: 1px solid #3a8158;
    border-radius: 11px;
    color: #d8f0df;
    padding: 7px 12px;
    font-size: 11px;
    font-weight: 700;
}
QPushButton#jumpToLatestButton:hover {
    background: #173923;
    border-color: #47a36f;
    color: #f3fff6;
}
QFrame#messageAssistant[messageState="error"] {
    background: #140b0b;
    border-color: #763636;
}
QFrame#messageAssistant[messageState="interrupted"] {
    background: #151207;
    border-color: #76612f;
}
QFrame#messageAssistant[messageState="save_error"] {
    background: #171006;
    border-color: #8b5d2f;
}
QFrame#messageThinkingPanel {
    background: #0b1210;
    border: 1px solid #3a4540;
    border-radius: 10px;
}
QLabel#messageThinkingHeading {
    color: #aeb8b2;
    font-size: 9px;
    font-weight: 800;
}
QLabel#messageThinkingText {
    color: #c8cfcb;
    font-size: 12px;
}
QLabel#messageRole {
    color: #30e487;
    font-weight: 750;
}
QLabel#statusLabel[progressState="waiting"] {
    color: #9bb8a6;
}
QLabel#statusLabel[progressState="streaming"] {
    color: #62d894;
}
QLabel#statusLabel[progressState="stalled"] {
    color: #e4c36f;
    font-weight: 700;
}
QLabel#messageBody {
    color: #edf4ef;
    font-size: 14px;
}
QWidget#messageContent,
QWidget#messageSegments {
    background: transparent;
}
QFrame#codeBlock {
    background: #030806;
    border: 1px solid #1d4030;
    border-radius: 12px;
}
QFrame#codeHeader {
    background: #0c1b13;
    border: none;
    border-bottom: 1px solid #1d4030;
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
}
QLabel#codeLanguage {
    color: #8fb5a0;
    font-size: 11px;
    font-weight: 700;
}
QPlainTextEdit#codeEditor {
    background: #030806;
    border: none;
    border-bottom-left-radius: 11px;
    border-bottom-right-radius: 11px;
    padding: 12px 14px;
    color: #eaf5ed;
    selection-background-color: #21643f;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 13px;
}
QPushButton#copyCodeButton,
QPushButton#messageCopyButton,
QPushButton#messageActionButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 5px 9px;
    color: #8fb5a0;
    font-size: 11px;
    font-weight: 700;
}
QPushButton#copyCodeButton:hover,
QPushButton#messageCopyButton:hover,
QPushButton#messageActionButton:hover {
    background: #14271d;
    border-color: #28523d;
    color: #e7f4eb;
}
QFrame#messageErrorPanel {
    background: #231010;
    border: 1px solid #6f3030;
    border-radius: 10px;
}
QFrame#messageErrorPanel[recoveryState="interrupted"] {
    background: #26200d;
    border-color: #7c652f;
}
QFrame#messageErrorPanel[recoveryState="save_error"] {
    background: #2b1d0c;
    border-color: #946334;
}
QLabel#messageErrorTitle {
    color: #ee9d9d;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}
QFrame#messageErrorPanel[recoveryState="interrupted"] QLabel#messageErrorTitle {
    color: #e8cf80;
}
QFrame#messageErrorPanel[recoveryState="save_error"] QLabel#messageErrorTitle {
    color: #f0bc7a;
}
QLabel#messageErrorDetail {
    color: #d9baba;
    font-size: 11px;
}
QFrame#messageErrorPanel[recoveryState="interrupted"] QLabel#messageErrorDetail {
    color: #d8cba5;
}
QFrame#messageErrorPanel[recoveryState="save_error"] QLabel#messageErrorDetail {
    color: #dfc3a2;
}
QLabel#messageLinkNotice {
    background: #211a0b;
    border: 1px solid #715b26;
    border-radius: 8px;
    color: #e5ca7c;
    padding: 7px 9px;
    font-size: 11px;
}
QPushButton#messageRetryButton {
    background: #351717;
    border: 1px solid #8d4545;
    border-radius: 9px;
    color: #f5d6d6;
    padding: 7px 12px;
}
QPushButton#messageRetryButton:hover {
    background: #492020;
    border-color: #b95a5a;
}
QFrame#messageErrorPanel[recoveryState="interrupted"] QPushButton#messageRetryButton {
    background: #32280d;
    border-color: #8b7132;
    color: #f0dfaa;
}
QFrame#messageErrorPanel[recoveryState="interrupted"] QPushButton#messageRetryButton:hover {
    background: #463814;
    border-color: #b08c3c;
}
QFrame#messageErrorPanel[recoveryState="save_error"] QPushButton#messageRetryButton {
    background: #3b250f;
    border-color: #a76b34;
    color: #ffe0b5;
}
QFrame#messageErrorPanel[recoveryState="save_error"] QPushButton#messageRetryButton:hover {
    background: #503318;
    border-color: #d08a46;
}
QLabel#subtitle,
QLabel#statusLabel {
    color: #87a592;
    font-size: 11px;
}
QLabel#agentLogHeading {
    color: #7fab8b;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}
QPushButton#agentLogToggle {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    color: #8fb5a0;
    font-size: 11px;
    padding: 5px 8px;
}
QPushButton#agentLogToggle:hover {
    background: #102219;
    border-color: #28523d;
    color: #e7f4eb;
}
QPushButton#agentLogClear {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: #779685;
    padding: 6px 9px;
}
QPushButton#agentLogClear:hover {
    background: #1c1212;
    border-color: #673333;
    color: #f0aaa5;
}
QStackedWidget#agentLogStack,
QScrollArea#agentTimeline,
QWidget#agentTimelineCanvas {
    background: transparent;
    border: none;
}
QLabel#agentTimelineEmpty {
    background: #07120c;
    border: 1px dashed #284635;
    border-radius: 14px;
    color: #71907a;
    font-size: 12px;
    padding: 26px;
}
QFrame#agentEventCard {
    background: #091610;
    border: 1px solid #1b3d2b;
    border-radius: 13px;
}
QFrame#agentEventCard[eventKind="command"] {
    background: #0a1410;
    border-color: #315441;
}
QFrame#agentEventCard[eventKind="error"] {
    background: #1a0d0d;
    border-color: #713434;
}
QLabel#agentEventDot {
    color: #38df83;
    font-size: 9px;
}
QLabel#agentEventDot[eventKind="command"] {
    color: #91bda0;
}
QLabel#agentEventDot[eventKind="error"] {
    color: #ef6d6d;
}
QLabel#agentEventRole {
    color: #afd3b9;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}
QLabel#agentEventTime {
    color: #557361;
    font-size: 10px;
}
QLabel#agentEventBody {
    color: #d9e7dd;
    font-size: 12px;
}
QLabel#agentEventScope {
    color: #759482;
    font-size: 10px;
}
QLabel#agentTaskState {
    background: #10251a;
    border: 1px solid #315c42;
    border-radius: 7px;
    color: #b8d7c1;
    font-size: 9px;
    font-weight: 800;
    padding: 3px 6px;
}
QLabel#agentTaskState[taskState="running"],
QLabel#agentTaskState[taskState="success"] {
    background: #123821;
    border-color: #2f8a56;
    color: #bdf6cf;
}
QLabel#agentTaskState[taskState="waiting_review"],
QLabel#agentTaskState[taskState="waiting_approval"] {
    background: #30270f;
    border-color: #7c652f;
    color: #ead899;
}
QLabel#agentTaskState[taskState="error"],
QLabel#agentTaskState[taskState="blocked"] {
    background: #351616;
    border-color: #854040;
    color: #f1b9b9;
}
QLabel#agentTaskState[taskState="canceled"],
QLabel#agentTaskState[taskState="discarded"],
QLabel#agentTaskState[taskState="interrupted"] {
    background: #26221a;
    border-color: #655b47;
    color: #d9cba9;
}
QFrame#agentEventCard[eventKind="command"] QLabel#agentEventBody {
    color: #b8d3c0;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 11px;
}
QFrame#agentEventCard[eventKind="error"] QLabel#agentEventBody {
    color: #f3b1b1;
}
QWidget#agentArtifactActions {
    background: transparent;
}
QLabel#agentArtifactName {
    color: #9fc6ac;
    font-weight: 700;
}
QPushButton#agentArtifactButton {
    background: #0c2116;
    border: 1px solid #28573d;
    border-radius: 8px;
    color: #c7ecd2;
    padding: 6px 9px;
    font-weight: 700;
}
QPushButton#agentArtifactButton:hover {
    background: #12331f;
    border-color: #35a765;
    color: #f0fff4;
}
QPushButton#agentRecallButton {
    background: transparent;
    border: 1px solid #315c42;
    border-radius: 8px;
    color: #b8d7c1;
    font-size: 10px;
    padding: 4px 8px;
}
QPushButton#agentRecallButton:hover {
    background: #10291a;
    border-color: #3f9362;
    color: #effff4;
}
QPushButton#agentRecallButton:disabled {
    border-color: #26372d;
    color: #52645a;
}
QPushButton#agentDetailsButton,
QPushButton#agentOutputAction {
    background: transparent;
    border: 1px solid #315c42;
    border-radius: 8px;
    color: #b8d7c1;
    font-size: 10px;
    padding: 4px 8px;
}
QPushButton#agentDetailsButton:hover,
QPushButton#agentOutputAction:hover {
    background: #10291a;
    border-color: #3f9362;
    color: #effff4;
}
QPushButton#agentDetailsButton:disabled,
QPushButton#agentOutputAction:disabled {
    border-color: #26372d;
    color: #52645a;
}
QPushButton#agentLogClear[confirmClear="true"] {
    background: #421b1b;
    border-color: #a54b4b;
    color: #ffd0d0;
}
QFrame#editPreviewPanel {
    background: #07140d;
    border: 1px solid #2b7a4b;
    border-radius: 14px;
}
QFrame#followUpPanel {
    background: #151006;
    border: 1px solid #765b22;
    border-radius: 14px;
}
QFrame#scriptApprovalPanel {
    background: #10150a;
    border: 1px solid #6f6530;
    border-radius: 14px;
}
QFrame#scriptApprovalPanel[riskLevel="high"] {
    background: #1a0d0d;
    border-color: #934848;
}
QLabel#scriptRiskLabel {
    background: #28210b;
    border: 1px solid #7c6723;
    border-radius: 8px;
    color: #e5c769;
    padding: 5px 8px;
    font-size: 10px;
    font-weight: 800;
}
QFrame#scriptApprovalPanel[riskLevel="high"] QLabel#scriptRiskLabel {
    background: #351313;
    border-color: #9c4747;
    color: #f0a4a4;
}
QLabel#scriptApprovalName {
    color: #edf6f0;
    font-size: 13px;
    font-weight: 750;
}
QPlainTextEdit#scriptApprovalCommand {
    background: #030806;
    border: 1px solid #334b29;
    border-radius: 9px;
    color: #d8e9dc;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 11px;
    padding: 9px;
}
QLabel#scriptApprovalWarning {
    color: #d7c68d;
    font-size: 11px;
}
QFrame#scriptApprovalPanel[riskLevel="high"] QLabel#scriptApprovalWarning {
    color: #e7b2b2;
}
QPushButton#scriptRunButton {
    background: #224f32;
    border: 1px solid #3b965d;
    border-radius: 9px;
    color: #f2fff5;
    padding: 8px 14px;
    font-weight: 750;
}
QFrame#scriptApprovalPanel[riskLevel="high"] QPushButton#scriptRunButton {
    background: #5a2424;
    border-color: #aa5050;
}
QLabel#followUpTitle {
    color: #e7bd5b;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QPlainTextEdit#agentDiffPreview {
    background: #030806;
    border: 1px solid #173d29;
    border-radius: 10px;
    color: #b8d8c1;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 11px;
    padding: 10px;
}
QFrame#diffReviewPanel,
QStackedWidget#diffContentStack {
    background: transparent;
    border: none;
}
QLabel#diffFileLabel {
    color: #d7ede0;
    font-weight: 700;
}
QLabel#diffSummary {
    color: #80a98d;
    font-size: 11px;
}
QComboBox#diffFileSelector {
    min-width: 210px;
    max-width: 430px;
    padding: 6px 9px;
}
QPushButton#diffCopyButton {
    background: transparent;
    border: 1px solid #245139;
    border-radius: 8px;
    color: #a8cdb3;
    padding: 6px 9px;
}
QPushButton#diffCopyButton:hover {
    background: #10281a;
    border-color: #3a8158;
    color: #effff3;
}
QLabel#diffEmptyState {
    background: #030806;
    border: 1px dashed #214532;
    border-radius: 10px;
    color: #789482;
    padding: 28px;
}
QFrame#agentProgressCard {
    background: #081a11;
    border: 1px solid #2b6544;
    border-radius: 13px;
}
QFrame#agentProgressCard[taskState="success"] {
    background: #0a1d13;
    border-color: #2f8a56;
}
QFrame#agentProgressCard[taskState="error"] {
    background: #1b0d0d;
    border-color: #8a3c3c;
}
QFrame#agentProgressCard[taskState="canceled"] {
    background: #171308;
    border-color: #80652d;
}
QLabel#agentProgressTitle {
    color: #eef8f1;
    font-weight: 800;
}
QLabel#agentProgressState {
    background: #123821;
    border: 1px solid #2a7950;
    border-radius: 7px;
    color: #aef0c4;
    font-size: 10px;
    font-weight: 800;
    padding: 3px 7px;
}
QLabel#agentProgressState[taskState="success"] {
    background: #123821;
    color: #bdf6cf;
}
QLabel#agentProgressState[taskState="error"] {
    background: #351616;
    border-color: #854040;
    color: #f1b9b9;
}
QLabel#agentProgressState[taskState="canceled"] {
    background: #30270f;
    border-color: #7c652f;
    color: #ead899;
}
QLabel#agentProgressElapsed {
    color: #8faa98;
    font-size: 11px;
}
QLabel#agentProgressPhase {
    color: #d6e7db;
}
QPushButton#agentProgressCancel {
    background: transparent;
    border: 1px solid #397152;
    border-radius: 9px;
    color: #cbe5d3;
    padding: 7px 11px;
}
QPushButton#agentProgressCancel:hover {
    background: #10291a;
    border-color: #47a36f;
}
QProgressBar#agentProgressBar {
    background: #10251a;
    border: none;
    border-radius: 2px;
}
QProgressBar#agentProgressBar::chunk {
    background: #24df80;
    border-radius: 2px;
}
QProgressBar#agentProgressBar[taskState="error"]::chunk {
    background: #db6767;
}
QProgressBar#agentProgressBar[taskState="canceled"]::chunk {
    background: #c8a94f;
}
QLabel#startupEyebrow {
    color: #92c9a2;
    font-size: 11px;
    font-weight: 700;
}
QLabel#startupStatus {
    color: #dce7de;
    font-size: 12px;
}
QLabel#statusStrip {
    background: transparent;
    border: none;
    color: #91ad99;
    font-size: 11px;
}
QPlainTextEdit,
QLineEdit,
QComboBox {
    background: #0a1710;
    border: 1px solid #193527;
    border-radius: 14px;
    padding: 12px;
    color: #edf4ef;
}
QPlainTextEdit:focus,
QLineEdit:focus,
QComboBox:focus {
    border: 1px solid #289f5f;
}
QLineEdit#historySearch[searchState="busy"] {
    background: #0d2116;
    border-color: #38a967;
}
QLineEdit#historySearch[searchState="error"] {
    background: #1b0d0d;
    border-color: #8a3c3c;
    color: #efcccc;
}
QComboBox#modelProfileCombo {
    background: #0b1711;
    border: 1px solid #1d3b2c;
    border-radius: 12px;
    padding: 9px 12px;
    color: #cfe0d4;
    font-weight: 700;
}
QComboBox#modelProfileCombo:hover {
    background: #102219;
    border-color: #2a5a40;
}
QPushButton {
    background: #0b1711;
    border: 1px solid #1d3b2c;
    border-radius: 14px;
    padding: 10px 16px;
    color: #dce7de;
    font-weight: 700;
}
QPushButton:hover {
    background: #102219;
    border-color: #2a5a40;
}
QPushButton#primaryButton {
    background: #176d40;
    border-color: #28d67b;
    color: #f4fff7;
}
QPushButton#primaryButton:hover {
    background: #1b8050;
    border-color: #3bea91;
}
QPushButton#applyAndTestButton {
    background: #102c1d;
    border-color: #278553;
    color: #dff8e7;
}
QPushButton#applyAndTestButton:hover {
    background: #153b27;
    border-color: #32b86d;
}
QPushButton:pressed {
    background: #08120d;
}
QPushButton:disabled {
    color: #66776b;
    border-color: #17271f;
    background: #09130e;
}
QPushButton#togglePillOff,
QPushButton#togglePillOn,
QPushButton#voiceOnlyButton,
QPushButton#thinkButton {
    border-radius: 12px;
    padding: 10px 16px;
}
QPushButton#thinkButton {
    padding: 8px 6px;
    font-size: 10px;
}
QPushButton#togglePillOn,
QPushButton#voiceOnlyButton,
QPushButton#thinkButton:checked,
QPushButton#sendCircleButton,
QPushButton#micCircleButtonActive {
    background: #123821;
    border: 1px solid #23c976;
    color: #effff4;
}
QPushButton#togglePillOn:hover,
QPushButton#voiceOnlyButton:hover,
QPushButton#thinkButton:hover,
QPushButton#sendCircleButton:hover,
QPushButton#micCircleButtonActive:hover {
    background: #164a2b;
}
QPushButton#sendCircleButton,
QPushButton#micCircleButton,
QPushButton#micCircleButtonActive,
QPushButton#micCircleButtonMuted {
    min-width: 50px;
    max-width: 50px;
    min-height: 50px;
    max-height: 50px;
    border-radius: 25px;
    padding: 0;
    font-size: 18px;
    font-weight: 800;
}
QPushButton#sendCircleButton {
    font-size: 22px;
}
QPushButton#micCircleButton {
    background: #0b1711;
    border: 1px solid #1d3b2c;
    color: #dce7de;
}
QPushButton#micCircleButtonMuted {
    background: #291313;
    border: 1px solid #8a3e3e;
    color: #f2caca;
}
QPushButton#micCircleButtonMuted:hover {
    background: #371919;
    border-color: #b25757;
}
QPushButton#voiceMuteButtonOn {
    background: #321717;
    border: 1px solid #a34a4a;
    color: #f3d0d0;
}
QPushButton#voiceMuteButtonOn:hover {
    background: #421d1d;
    border-color: #c45c5c;
}
QPushButton#voiceContinuousButtonOn {
    background: #123821;
    border: 1px solid #23c976;
    color: #effff4;
}
QPushButton#voiceContinuousButtonOn:hover {
    background: #164a2b;
}
QLabel#privacyNote {
    background: #0a1b12;
    border: 1px solid #1c4932;
    border-radius: 12px;
    padding: 11px 13px;
    color: #9dcbb0;
    font-size: 11px;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background: #0b1711;
    border: 1px solid #1c392b;
    padding: 10px 17px;
    margin-right: 6px;
    border-radius: 13px;
    color: #9eb3a5;
    font-weight: 650;
}
QTabBar::tab:selected {
    background: #10291a;
    color: #edf3ee;
    border-color: #24583c;
}
QCheckBox {
    color: #cdd9d0;
}
QSlider::groove:horizontal {
    border: 1px solid #1c3327;
    height: 8px;
    background: #0b1510;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: #32df86;
    border: 1px solid #123821;
    width: 18px;
    margin: -6px 0;
    border-radius: 9px;
}
QLabel#voiceOnlyState {
    color: #edf3ee;
    font-size: 15px;
    font-weight: 700;
}
QLabel#statusLabel[voiceRecovery="error"] {
    background: #2b1d0c;
    border: 1px solid #946334;
    border-radius: 10px;
    color: #f0c684;
    padding: 9px 12px;
    font-weight: 700;
}
QLabel#statusLabel[voiceRecovery="progress"] {
    background: #0a1b12;
    border: 1px solid #286744;
    border-radius: 10px;
    color: #a8dbba;
    padding: 8px 12px;
}
"""


def normalize_theme(theme: str) -> str:
    """Return a supported theme identifier."""
    return theme if theme in _THEME_HUES else DEFAULT_THEME


@lru_cache(maxsize=len(_THEME_HUES))
def _stylesheet_for_normalized_theme(theme: str) -> str:
    if theme == DEFAULT_THEME:
        return MATRIX_STYLESHEET

    target_hue = _THEME_HUES[theme]

    def recolor(match: re.Match[str]) -> str:
        color = match.group(1)
        red, green, blue = (
            int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)
        )
        hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
        # Matrix surfaces are green-toned. Preserve neutral text and warning/error colours.
        if saturation < 0.12 or not 0.18 <= hue <= 0.52:
            return match.group(0)
        recolored = colorsys.hls_to_rgb(target_hue, lightness, saturation)
        return "#" + "".join(f"{round(channel * 255):02x}" for channel in recolored)

    return re.sub(r"#([0-9a-fA-F]{6})", recolor, MATRIX_STYLESHEET)


@lru_cache(maxsize=256)
def _stylesheet_for_preferences(theme: str, font_family: str, font_size: int) -> str:
    base = _stylesheet_for_normalized_theme(theme)
    font_rules = f"""

/* User-selected chat and text-entry typography. */
QLabel#messageBody,
QPlainTextEdit#chatInput,
QLineEdit,
QPlainTextEdit,
QTextEdit,
QWidget#compactAssistant QLabel,
QWidget#compactAssistant QLineEdit,
QWidget#compactAssistant QPushButton {{
    font-family: \"{font_family}\";
    font-size: {font_size}pt;
}}
"""
    return base + font_rules


def stylesheet_for_theme(
    theme: str,
    font_family: str = DEFAULT_CHAT_FONT_FAMILY,
    font_size: int = DEFAULT_CHAT_FONT_SIZE,
) -> str:
    """Return a cached stylesheet for the selected colour and chat typography."""
    return _stylesheet_for_preferences(
        normalize_theme(theme),
        normalize_chat_font_family(font_family),
        normalize_chat_font_size(font_size),
    )
