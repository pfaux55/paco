from __future__ import annotations

from PySide6.QtCore import QByteArray, QThreadPool, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME, APP_TITLE, DEFAULT_ACTIVITY
from local_matrix_assistant.services.audio import AudioPlayer, AudioRecorder
from local_matrix_assistant.services.agent_history import AgentHistoryStore
from local_matrix_assistant.services.agent_permissions import AgentPermissionStore
from local_matrix_assistant.services.attachments import AttachmentService, LocalAttachment
from local_matrix_assistant.services.conversation_memory import ConversationMemoryService
from local_matrix_assistant.services.desktop_actions import DesktopActionService
from local_matrix_assistant.services.history import HistoryStore
from local_matrix_assistant.services.model_router import ModelRouter, ModelSelection
from local_matrix_assistant.services.ollama import OllamaClient
from local_matrix_assistant.services.project_tasks import ProjectTaskService
from local_matrix_assistant.services.runtime_status import RuntimeStatusService
from local_matrix_assistant.services.stt import SttService
from local_matrix_assistant.services.tts import TtsService
from local_matrix_assistant.services.web_search import WebSearchService
from local_matrix_assistant.services.workspace_actions import WorkspaceActionService
from local_matrix_assistant.services.workspace_analysis import WorkspaceAnalysisService
from local_matrix_assistant.services.word_documents import WordDocumentService
from local_matrix_assistant.ui.agent_panel import AgentPanel
from local_matrix_assistant.ui.chat_panel import ChatPanel
from local_matrix_assistant.ui.main_window_agent import AgentWindowMixin
from local_matrix_assistant.ui.main_window_chat import ChatWindowMixin
from local_matrix_assistant.ui.main_window_settings import SettingsStatusWindowMixin
from local_matrix_assistant.ui.main_window_voice import VoiceWindowMixin
from local_matrix_assistant.ui.settings_panel import SettingsPanel
from local_matrix_assistant.ui.shortcut_help import ShortcutHelpDialog
from local_matrix_assistant.ui.startup_sequence import StartupSequence
from local_matrix_assistant.ui.system_notice import SystemNoticeBar
from local_matrix_assistant.ui.task_runner import TaskRunner
from local_matrix_assistant.ui.voice_panel import VoicePanel
from local_matrix_assistant.ui.widgets import MessageBubble, StatusBadge
from local_matrix_assistant.ui.workers import StreamWorker


class MainWindow(ChatWindowMixin, AgentWindowMixin, VoiceWindowMixin, SettingsStatusWindowMixin, QMainWindow):
    compact_layout_width = 1060

    def __init__(self, paths: AppPaths, config: AppConfig) -> None:
        super().__init__()
        self.paths = paths
        self.config = config
        self.history_store = HistoryStore(paths.chats_dir, paths.history_file)
        self.agent_history_store = AgentHistoryStore(paths.data_dir / "agent_history.json")
        initial_conversation = self.history_store.load_preferred_or_latest(
            config.last_conversation_id
        )
        self.active_conversation_id = initial_conversation.summary.conversation_id
        self.active_conversation_created_at = initial_conversation.summary.created_at
        self.messages = initial_conversation.messages
        self.conversation_memory = initial_conversation.memory

        self.ollama_client = OllamaClient(config.ollama_base_url)
        self.stt_service = SttService(config.stt_model_dir)
        self.tts_service = TtsService(config.tts_model_path, config.tts_config_path)
        self.recorder = AudioRecorder(config.preferred_input_name)
        self.player = AudioPlayer(config.playback_output_name)
        self.startup_player = AudioPlayer(config.playback_output_name)
        self.web_search_service = WebSearchService()
        self.model_router = ModelRouter()
        self.conversation_memory_service = ConversationMemoryService()
        self.attachment_service = AttachmentService()
        self._pending_chat_attachments: list[LocalAttachment] = []
        self._chat_file_dialog = None
        self._message_bubbles: list[tuple[MessageBubble, object]] = []
        self._rendered_message_start = 0
        self._history_search_generation = 0
        self._history_search_inflight = False
        self._editing_message_index: int | None = None
        self._composer_before_edit: tuple[str, list[LocalAttachment]] | None = None
        self.available_ollama_models: list[str] = []
        self.desktop_action_service = DesktopActionService(
            working_folders=config.working_folders,
            active_working_folder=config.active_working_folder,
        )
        self.agent_permission_store = AgentPermissionStore(
            paths.data_dir / "agent_permissions.json"
        )
        initial_agent_folder = (
            self.desktop_action_service.active_working_folder
            or self.desktop_action_service.default_files_dir
        )
        self._agent_permission_mode = self.agent_permission_store.mode_for(initial_agent_folder)
        self.workspace_action_service = WorkspaceActionService(self.desktop_action_service)
        self.workspace_analysis_service = WorkspaceAnalysisService(self.workspace_action_service)
        self.project_task_service = ProjectTaskService(self.desktop_action_service)
        self.word_document_service = WordDocumentService(self.desktop_action_service)
        self.status_service = RuntimeStatusService(
            self.ollama_client,
            self.stt_service,
            self.tts_service,
            self.recorder,
            self.player,
        )

        self.thread_pool = QThreadPool(self)
        self.task_runner = TaskRunner(self.thread_pool)
        self._awaiting_response = False
        self._status_poll_inflight = False
        self._last_status_snapshot = None
        self._settings_save_pending = False
        self._dismissed_system_notice_key = ""
        self._cancel_requested = False
        self._voice_capture_pending = False
        self._voice_input_request_id = 0
        self._voice_capture_started_at: float | None = None
        self._voice_capture_last_audio_at: float | None = None
        self._voice_capture_health_timer = QTimer(self)
        self._voice_capture_health_timer.setInterval(1000)
        self._voice_capture_health_timer.timeout.connect(self._check_voice_capture_health)
        self._voice_stage = ""
        self._voice_stage_request_id = 0
        self._voice_stage_item_index = 0
        self._voice_stage_started_at: float | None = None
        self._voice_stage_timer = QTimer(self)
        self._voice_stage_timer.setInterval(1000)
        self._voice_stage_timer.timeout.connect(self._refresh_voice_stage_progress)
        self._voice_output_recovery_message = ""
        self._tts_request_id = 0
        self._tts_text_chunks: list[str] = []
        self._tts_audio_chunks: dict[int, bytes] = {}
        self._tts_next_synthesis_index = 0
        self._tts_next_play_index = 0
        self._tts_synthesis_active = False
        self._continuous_voice_armed = False
        self._continuous_voice_timer = QTimer(self)
        self._continuous_voice_timer.setSingleShot(True)
        self._continuous_voice_timer.setInterval(350)
        self._continuous_voice_timer.timeout.connect(self._resume_continuous_voice_capture)
        self._voice_tuning_save_timer = QTimer(self)
        self._voice_tuning_save_timer.setSingleShot(True)
        self._voice_tuning_save_timer.setInterval(250)
        self._voice_tuning_save_timer.timeout.connect(self._save_voice_tuning)
        self._chat_draft_save_timer = QTimer(self)
        self._chat_draft_save_timer.setSingleShot(True)
        self._chat_draft_save_timer.setInterval(600)
        self._chat_draft_save_timer.timeout.connect(self._save_current_chat_draft)
        self._suspend_chat_draft_save = False
        self._active_stream_worker: StreamWorker | None = None
        self._active_reply_stage = ""
        self._reply_progress_started_at: float | None = None
        self._reply_stage_started_at: float | None = None
        self._reply_first_chunk_at: float | None = None
        self._reply_last_chunk_at: float | None = None
        self._reply_progress_label = ""
        self._reply_progress_state = ""
        self._reply_progress_timer = QTimer(self)
        self._reply_progress_timer.setInterval(1000)
        self._reply_progress_timer.timeout.connect(self._refresh_reply_progress)
        self._active_model_pull_worker: StreamWorker | None = None
        self._active_model_pull_name = ""
        self._pending_assistant_bubble: MessageBubble | None = None
        self._pending_assistant_record: ChatMessage | None = None
        self._failed_assistant_bubble: MessageBubble | None = None
        self._unsaved_reply_message: ChatMessage | None = None
        self._pending_assistant_text = ""
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.setInterval(40)
        self._stream_render_timer.timeout.connect(self._flush_pending_stream_render)
        self._history_scroll_timer = QTimer(self)
        self._history_scroll_timer.setSingleShot(True)
        self._history_scroll_timer.setInterval(50)
        self._history_scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._active_reply_metadata: dict = {}
        self._active_model_selection: ModelSelection | None = None
        self._pending_workspace_edit = None
        self._pending_project_script_plan = None
        self._run_tests_after_workspace_apply = False
        self._pending_applied_fix_issue = ""
        self._active_agent_action_worker: StreamWorker | None = None
        self._active_agent_task_worker: StreamWorker | None = None
        self._active_project_task_plan = None
        self._active_project_fix_issue = ""
        self._last_project_test_plan = None
        self._last_project_test_result = None
        self._follow_up_fix_issue = ""
        self._compact_layout = False
        self._compact_sidebar_open = False
        self._sidebar_preference_collapsed = config.sidebar_collapsed
        self._delete_confirmation_conversation_id = ""
        self._delete_confirmation_timer = QTimer(self)
        self._delete_confirmation_timer.setSingleShot(True)
        self._delete_confirmation_timer.setInterval(5000)
        self._delete_confirmation_timer.timeout.connect(self._reset_delete_confirmation)

        self.setWindowTitle(APP_NAME)
        self.resize(1420, 900)
        self.setMinimumSize(820, 640)
        self._build_ui()
        self._apply_responsive_layout(self.width())
        self._wire_events()
        self._setup_agent_history()
        self._register_shortcuts()
        self._apply_initial_ui_state()
        self._populate_voice_options()
        self._refresh_input_device_options()
        self._refresh_output_device_options()
        self._refresh_conversation_list()
        self._render_history()
        self._restore_chat_session()
        self._update_model_hint()
        self._apply_audio_state("Idle")
        self._set_activity(DEFAULT_ACTIVITY)
        self.refresh_status()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(7000)
        self._restore_window_session()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        self.root_layout = QVBoxLayout(root)
        self.root_layout.setContentsMargins(20, 20, 20, 20)
        self.root_layout.setSpacing(18)

        self.content_root = QWidget(root)
        self.root_layout.addWidget(self.content_root)

        self.outer_layout = QVBoxLayout(self.content_root)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_layout.setSpacing(18)
        self.outer_layout.addWidget(self._build_header())
        self.system_notice = SystemNoticeBar()
        self.outer_layout.addWidget(self.system_notice)

        body = QWidget()
        body.setObjectName("appBody")
        self.body_layout = QHBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(18)

        self.chat_panel = ChatPanel()
        self.sidebar = self._build_sidebar()
        self.body_layout.addWidget(self.sidebar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        self.agent_panel = AgentPanel(
            str(self.desktop_action_service.active_working_folder or ""),
            str(
                self.desktop_action_service.active_working_folder
                or self.desktop_action_service.default_files_dir
            ),
        )
        self.agent_panel.set_permission_mode(self._agent_permission_mode)
        self.voice_panel = VoicePanel(self.config)
        self.settings_panel = SettingsPanel(self.config)
        self.page_stack.addWidget(self.chat_panel)
        self.page_stack.addWidget(self.agent_panel)
        self.page_stack.addWidget(self.voice_panel)
        self.page_stack.addWidget(self.settings_panel)
        self.body_layout.addWidget(self.page_stack, stretch=1)
        self.outer_layout.addWidget(body, stretch=1)
        self._set_nav_state(self.chat_nav_button)

        self.startup_sequence = StartupSequence(
            app_name=APP_NAME,
            root=root,
            content_root=self.content_root,
            startup_player=self.startup_player,
        )

    def _build_header(self) -> QWidget:
        header_panel = QFrame()
        header_panel.setObjectName("topBar")
        header_layout = QHBoxLayout(header_panel)
        header_layout.setContentsMargins(8, 0, 8, 0)
        header_layout.setSpacing(14)

        self.sidebar_toggle_button = QPushButton("Menu")
        self.sidebar_toggle_button.setObjectName("sidebarToggleButton")
        self.sidebar_toggle_button.setCheckable(True)
        self.sidebar_toggle_button.setAccessibleName("Toggle navigation and chat history")
        header_layout.addWidget(self.sidebar_toggle_button)

        logo = QLabel("")
        logo.setObjectName("appLogo")
        logo.setFixedSize(34, 34)
        header_layout.addWidget(logo)

        self.header_title = QLabel(APP_TITLE)
        self.header_title.setObjectName("title")
        header_layout.addWidget(self.header_title)
        header_layout.addStretch(1)

        self.shortcuts_button = QPushButton("Shortcuts")
        self.shortcuts_button.setObjectName("headerActionButton")
        self.shortcuts_button.setToolTip("Show keyboard shortcuts (Ctrl+/)")
        header_layout.addWidget(self.shortcuts_button)

        badges = QHBoxLayout()
        badges.setSpacing(10)
        self.ollama_badge = StatusBadge("Ollama")
        self.model_badge = StatusBadge("Model")
        self.mic_badge = StatusBadge("Mic")
        self.tts_badge = StatusBadge("Voice")
        for badge in (self.ollama_badge, self.model_badge, self.mic_badge, self.tts_badge):
            badges.addWidget(badge)
        header_layout.addLayout(badges)
        return header_panel

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(300)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QWidget()
        nav.setObjectName("sidebarNav")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 10)
        nav_layout.setSpacing(8)
        self.chat_nav_button = QPushButton("Chat")
        self.agent_nav_button = QPushButton("Agent")
        self.voice_nav_button = QPushButton("Voice")
        self.settings_nav_button = QPushButton("Settings")
        self.chat_nav_button.setToolTip("Chat (Alt+1)")
        self.agent_nav_button.setToolTip("Agent (Alt+2)")
        self.voice_nav_button.setToolTip("Voice (Alt+3)")
        self.settings_nav_button.setToolTip("Settings (Alt+4 or Ctrl+,)")
        for button in (self.chat_nav_button, self.agent_nav_button, self.voice_nav_button, self.settings_nav_button):
            button.setObjectName("navButton")
            button.setCheckable(True)
            nav_layout.addWidget(button)
        layout.addWidget(nav)

        history_panel = QFrame()
        history_panel.setObjectName("historyPanel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(18, 20, 18, 18)
        history_layout.setSpacing(14)

        history_label = QLabel("CONVERSATION HISTORY")
        history_label.setObjectName("sidebarLabel")
        history_layout.addWidget(history_label)

        self.new_chat_button = QPushButton("+  New Chat")
        self.new_chat_button.setToolTip("Start a new chat (Ctrl+N)")
        self.delete_chat_button = QPushButton("Delete Chat")
        self.rename_chat_button = QPushButton("Rename")
        self.new_chat_button.setObjectName("sidebarActionButton")
        self.delete_chat_button.setObjectName("sidebarSecondaryButton")
        self.rename_chat_button.setObjectName("sidebarSecondaryButton")
        history_layout.addWidget(self.new_chat_button)

        self.history_search_input = QLineEdit()
        self.history_search_input.setObjectName("historySearch")
        self.history_search_input.setPlaceholderText("Search chats  Ctrl+K")
        self.history_search_input.setClearButtonEnabled(True)
        self.history_search_input.setToolTip("Search conversation titles and messages (Ctrl+K)")
        history_layout.addWidget(self.history_search_input)

        self.history_empty_label = QLabel("No chats yet.")
        self.history_empty_label.setObjectName("historyEmptyLabel")
        self.history_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_empty_label.setWordWrap(True)
        self.history_empty_label.setVisible(False)
        history_layout.addWidget(self.history_empty_label)

        self.history_list = self.chat_panel.history_list
        self.history_list.setParent(history_panel)
        history_layout.addWidget(self.history_list, stretch=1)

        history_actions = QHBoxLayout()
        history_actions.setSpacing(8)
        history_actions.addWidget(self.rename_chat_button)
        history_actions.addWidget(self.delete_chat_button)
        history_layout.addLayout(history_actions)

        self.rename_chat_panel = QFrame()
        self.rename_chat_panel.setObjectName("renameChatPanel")
        rename_layout = QVBoxLayout(self.rename_chat_panel)
        rename_layout.setContentsMargins(12, 12, 12, 12)
        rename_layout.setSpacing(8)
        rename_label = QLabel("RENAME CHAT")
        rename_label.setObjectName("sidebarLabel")
        rename_layout.addWidget(rename_label)
        self.rename_chat_input = QLineEdit()
        self.rename_chat_input.setMaxLength(80)
        self.rename_chat_input.setPlaceholderText("Conversation title")
        rename_layout.addWidget(self.rename_chat_input)
        rename_actions = QHBoxLayout()
        self.cancel_rename_button = QPushButton("Cancel")
        self.save_rename_button = QPushButton("Save")
        self.save_rename_button.setObjectName("primaryButton")
        rename_actions.addWidget(self.cancel_rename_button)
        rename_actions.addWidget(self.save_rename_button)
        rename_layout.addLayout(rename_actions)
        self.rename_chat_panel.setVisible(False)
        history_layout.addWidget(self.rename_chat_panel)

        self.sidebar_activity_card = QFrame()
        self.sidebar_activity_card.setObjectName("idleCard")
        idle_layout = QHBoxLayout(self.sidebar_activity_card)
        idle_layout.setContentsMargins(14, 12, 14, 12)
        idle_layout.setSpacing(10)
        idle_dot = QLabel("J")
        idle_dot.setObjectName("idleAvatar")
        idle_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_dot.setFixedSize(34, 34)
        idle_layout.addWidget(idle_dot)
        idle_text_col = QVBoxLayout()
        idle_text_col.setSpacing(2)
        idle_title = QLabel("Idle")
        idle_title.setObjectName("idleTitle")
        self.sidebar_activity_label = QLabel("Ready when you are.")
        self.sidebar_activity_label.setObjectName("statusLabel")
        self.sidebar_activity_label.setWordWrap(True)
        idle_text_col.addWidget(idle_title)
        idle_text_col.addWidget(self.sidebar_activity_label)
        idle_layout.addLayout(idle_text_col, stretch=1)
        history_layout.addWidget(self.sidebar_activity_card)
        layout.addWidget(history_panel, stretch=1)

        self.chat_panel.new_chat_button = self.new_chat_button
        self.chat_panel.delete_chat_button = self.delete_chat_button
        return sidebar

    def _show_page(self, index: int, active_button: QPushButton) -> None:
        if index != 0 and self.chat_panel.voice_only_mode_active():
            self._hide_voice_only_screen()
        self.page_stack.setCurrentIndex(index)
        self._set_nav_state(active_button)
        if index != self.config.active_page:
            self._update_config(active_page=index)
        if self._compact_layout and self._compact_sidebar_open:
            self._compact_sidebar_open = False
            self._apply_responsive_layout()

    def _toggle_sidebar(self) -> None:
        if self._compact_layout:
            self._compact_sidebar_open = not self._compact_sidebar_open
        else:
            self._sidebar_preference_collapsed = self.sidebar.isVisible()
            self._update_config(sidebar_collapsed=self._sidebar_preference_collapsed)
        self._apply_responsive_layout()

    def _show_sidebar_for_navigation(self) -> None:
        if self.sidebar.isVisible():
            return
        if self._compact_layout:
            self._compact_sidebar_open = True
        else:
            self._sidebar_preference_collapsed = False
            self._update_config(sidebar_collapsed=False)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self, width: int | None = None) -> None:
        available_width = self.width() if width is None else width
        compact = available_width < self.compact_layout_width
        if compact and not self._compact_layout:
            self._compact_sidebar_open = False
        self._compact_layout = compact

        sidebar_visible = self._compact_sidebar_open if compact else not self._sidebar_preference_collapsed
        if compact and sidebar_visible:
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(16_777_215)
            self.body_layout.setStretchFactor(self.sidebar, 1)
            self.page_stack.setVisible(False)
        else:
            self.sidebar.setFixedWidth(300)
            self.body_layout.setStretchFactor(self.sidebar, 0)
            self.page_stack.setVisible(True)
        self.sidebar.setVisible(sidebar_visible)

        margin = 12 if compact else 20
        spacing = 12 if compact else 18
        self.root_layout.setContentsMargins(margin, margin, margin, margin)
        self.root_layout.setSpacing(spacing)
        self.outer_layout.setSpacing(spacing)
        self.body_layout.setSpacing(spacing)
        self.chat_panel.set_compact_mode(compact)

        self.shortcuts_button.setVisible(available_width >= 940)
        self.model_badge.setVisible(available_width >= 900)
        self.mic_badge.setVisible(not compact)
        self.tts_badge.setVisible(not compact)
        self.sidebar_toggle_button.setChecked(sidebar_visible)
        self.sidebar_toggle_button.setText("Hide Menu" if sidebar_visible else "Menu")
        action = "Hide" if sidebar_visible else "Show"
        self.sidebar_toggle_button.setToolTip(f"{action} navigation and chat history (Ctrl+B)")
        self.sidebar_toggle_button.setAccessibleDescription(
            f"{action} navigation and chat history."
        )

    def _set_nav_state(self, active_button: QPushButton) -> None:
        for button in (self.chat_nav_button, self.agent_nav_button, self.voice_nav_button, self.settings_nav_button):
            button.setChecked(button is active_button)

    def _restore_window_session(self) -> None:
        page_index = min(3, max(0, int(self.config.active_page)))
        page_buttons = (
            self.chat_nav_button,
            self.agent_nav_button,
            self.voice_nav_button,
            self.settings_nav_button,
        )
        self.page_stack.setCurrentIndex(page_index)
        self._set_nav_state(page_buttons[page_index])

        encoded_geometry = self.config.window_geometry.strip()
        restored = False
        if encoded_geometry:
            geometry = QByteArray.fromBase64(QByteArray(encoded_geometry.encode("ascii")))
            restored = bool(geometry) and self.restoreGeometry(geometry)
            restored = restored and self._window_intersects_available_screen()
        if restored:
            state = self.windowState()
            if state & Qt.WindowState.WindowMinimized:
                self.setWindowState(state & ~Qt.WindowState.WindowMinimized)
            self._apply_responsive_layout(self.width())
            return
        self.resize(1420, 900)
        self.showMaximized()

    def _window_intersects_available_screen(self) -> bool:
        geometry = self.frameGeometry()
        return any(
            geometry.intersects(screen.availableGeometry())
            for screen in QApplication.screens()
        )

    def _setup_agent_history(self) -> None:
        self._agent_history_save_failed = False
        self._agent_history_save_timer = QTimer(self)
        self._agent_history_save_timer.setSingleShot(True)
        self._agent_history_save_timer.setInterval(300)
        self._agent_history_save_timer.timeout.connect(self._save_agent_history)
        self.agent_panel.history_changed.connect(self._schedule_agent_history_save)
        self.agent_panel.load_history(self.agent_history_store.load())

    def _schedule_agent_history_save(self) -> None:
        if not self._agent_history_save_timer.isActive():
            self._agent_history_save_timer.start()

    def _save_agent_history(self, report_errors: bool = True) -> None:
        folder = str(self.desktop_action_service.active_working_folder or "")
        try:
            self.agent_history_store.save(self.agent_panel.history_record(folder))
        except OSError as exc:
            if report_errors and not self._agent_history_save_failed:
                self._set_activity(f"Could not save Agent history: {exc}")
            self._agent_history_save_failed = True
            return
        self._agent_history_save_failed = False

    def _wire_events(self) -> None:
        self.chat_panel.send_button.clicked.connect(self._send_from_input)
        self.chat_panel.input_box.submit_requested.connect(self._send_from_input)
        self.chat_panel.input_box.textChanged.connect(self._update_send_enabled_state)
        self.chat_panel.input_box.textChanged.connect(self._on_chat_draft_changed)
        self.chat_panel.attach_button.clicked.connect(self._choose_chat_attachments)
        self.chat_panel.input_box.file_paths_dropped.connect(self._add_chat_attachment_paths)
        self.chat_panel.input_box.clipboard_image_pasted.connect(self._add_chat_clipboard_image)
        self.chat_panel.attachment_remove_requested.connect(self._remove_chat_attachment)
        self.chat_panel.edit_cancel_requested.connect(self._cancel_message_edit)
        self.chat_panel.load_earlier_button.clicked.connect(self._load_earlier_messages)
        self.chat_panel.voice_button.clicked.connect(self._toggle_voice_mode)
        self.chat_panel.voice_only_button.clicked.connect(self._show_voice_only_screen)
        self.chat_panel.voice_only_panel.toggle_requested.connect(self._toggle_voice_mode)
        self.chat_panel.voice_only_panel.close_requested.connect(self._hide_voice_only_screen)
        self.chat_panel.voice_only_panel.mute_requested.connect(self._on_microphone_muted_toggled)
        self.chat_panel.voice_only_panel.continuous_requested.connect(self._on_continuous_voice_toggled)
        self.chat_panel.cancel_button.clicked.connect(self._cancel_active_reply)
        self.chat_panel.stop_audio_button.clicked.connect(self._stop_voice_output)
        self.chat_panel.new_chat_button.clicked.connect(self._start_new_chat)
        self.chat_panel.delete_chat_button.clicked.connect(self._delete_current_chat)
        self.chat_panel.history_list.currentItemChanged.connect(self._on_conversation_selected)
        self.rename_chat_button.clicked.connect(self._begin_conversation_rename)
        self.save_rename_button.clicked.connect(self._commit_conversation_rename)
        self.rename_chat_input.returnPressed.connect(self._commit_conversation_rename)
        self.cancel_rename_button.clicked.connect(self._cancel_conversation_rename)
        self._history_filter_timer = QTimer(self)
        self._history_filter_timer.setSingleShot(True)
        self._history_filter_timer.setInterval(180)
        self._history_filter_timer.timeout.connect(self._apply_history_filter)
        self.history_search_input.textChanged.connect(self._on_history_search_changed)
        self.history_search_input.returnPressed.connect(self._apply_history_filter)
        self.chat_panel.web_search_button.toggled.connect(self._on_web_search_toggled)
        self.chat_panel.model_profile_combo.currentIndexChanged.connect(self._on_model_profile_changed)

        self.agent_panel.run_button.clicked.connect(self._run_agent_command)
        self.agent_panel.command_input.submit_requested.connect(self._run_agent_command)
        self.agent_panel.choose_folder_button.clicked.connect(self._choose_agent_folder)
        self.agent_panel.apply_edit_requested.connect(self._apply_pending_workspace_edit)
        self.agent_panel.apply_and_test_requested.connect(self._apply_pending_workspace_edit_and_test)
        self.agent_panel.discard_edit_requested.connect(self._discard_pending_workspace_edit)
        self.agent_panel.cancel_task_requested.connect(self._cancel_active_agent_task)
        self.agent_panel.follow_up_fix_requested.connect(self._draft_follow_up_fix)
        self.agent_panel.dismiss_follow_up_requested.connect(self._dismiss_follow_up_fix)
        self.agent_panel.open_artifact_file_requested.connect(self._open_agent_artifact_file)
        self.agent_panel.open_artifact_folder_requested.connect(self._open_agent_artifact_folder)
        self.agent_panel.approve_script_requested.connect(self._approve_project_script)
        self.agent_panel.reject_script_requested.connect(self._reject_project_script)
        self.agent_panel.command_recalled.connect(
            lambda _command: self._set_activity("Command restored for review; nothing has run yet.")
        )
        self.agent_panel.permission_mode_changed.connect(self._on_agent_permission_changed)
        self.agent_panel.command_recall_blocked.connect(
            lambda _command, origin, current: self._set_activity(
                f"Command belongs to {origin}. Current workspace is {current}; switch folders before reusing it."
            )
        )
        self.agent_panel.history_clear_confirmation_changed.connect(
            lambda armed: self._set_activity(
                "Select Confirm Clear All within five seconds to delete every saved Agent task."
                if armed
                else "Agent history deletion canceled."
            )
        )
        self.agent_panel.history_cleared.connect(
            lambda: self._set_activity("Agent task history and execution details cleared.")
        )
        self.agent_panel.save_task_output_requested.connect(self._choose_agent_output_export)

        self.settings_panel.refresh_button.clicked.connect(self.refresh_status)
        self.settings_panel.save_button.clicked.connect(self._save_settings)
        self.settings_panel.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.settings_panel.model_install_button.clicked.connect(self._start_model_install)
        self.settings_panel.model_cancel_button.clicked.connect(self._cancel_model_install)

        self.voice_panel.voice_enabled_checkbox.toggled.connect(self._on_voice_enabled_toggled)
        self.voice_panel.auto_speak_checkbox.toggled.connect(self._on_auto_speak_toggled)
        self.voice_panel.continuous_voice_checkbox.toggled.connect(self._on_continuous_voice_toggled)
        self.voice_panel.microphone_muted_checkbox.toggled.connect(self._on_microphone_muted_toggled)
        self.voice_panel.voice_combo.currentIndexChanged.connect(self._on_voice_selection_changed)
        self.voice_panel.input_device_combo.currentIndexChanged.connect(self._on_input_device_changed)
        self.voice_panel.output_device_combo.currentIndexChanged.connect(self._on_output_device_changed)
        self.voice_panel.rate_slider.valueChanged.connect(self._on_voice_tuning_changed)
        self.voice_panel.volume_slider.valueChanged.connect(self._on_voice_tuning_changed)
        self.voice_panel.preview_button.clicked.connect(self._preview_voice)
        self.voice_panel.stop_preview_button.clicked.connect(self._stop_voice_output)

        self.recorder.recording_changed.connect(self._on_recording_changed)
        self.recorder.audio_level_changed.connect(self._on_microphone_level)
        self.recorder.speech_ended.connect(self._on_voice_endpoint)
        self.player.playback_changed.connect(self._on_playback_changed)

        self.chat_nav_button.clicked.connect(lambda: self._show_page(0, self.chat_nav_button))
        self.agent_nav_button.clicked.connect(lambda: self._show_page(1, self.agent_nav_button))
        self.voice_nav_button.clicked.connect(lambda: self._show_page(2, self.voice_nav_button))
        self.settings_nav_button.clicked.connect(lambda: self._show_page(3, self.settings_nav_button))
        self.shortcuts_button.clicked.connect(self._show_shortcut_help)
        self.sidebar_toggle_button.clicked.connect(self._toggle_sidebar)
        self.system_notice.action_requested.connect(self._on_system_notice_action)
        self.system_notice.dismiss_requested.connect(self._dismiss_system_notice)

    def _register_shortcuts(self) -> None:
        bindings = {
            "Ctrl+N": self._start_new_chat,
            "Ctrl+Shift+R": self._regenerate_latest_response,
            "Ctrl+Shift+Space": self._toggle_voice_shortcut,
            "Ctrl+Shift+M": self._toggle_microphone_mute_shortcut,
            "Ctrl+Shift+X": self._stop_voice_output_shortcut,
            "Ctrl+K": self._focus_history_search,
            "Ctrl+L": self._focus_chat_composer,
            "Ctrl+O": self._choose_chat_attachments,
            "Ctrl+B": self._toggle_sidebar,
            "F2": self._begin_conversation_rename,
            "Alt+1": lambda: self._show_page(0, self.chat_nav_button),
            "Alt+2": lambda: self._show_page(1, self.agent_nav_button),
            "Alt+3": lambda: self._show_page(2, self.voice_nav_button),
            "Alt+4": lambda: self._show_page(3, self.settings_nav_button),
            "Ctrl+,": lambda: self._show_page(3, self.settings_nav_button),
            "Ctrl+/": self._show_shortcut_help,
            "Escape": self._handle_escape_shortcut,
        }
        self._shortcuts: dict[str, QShortcut] = {}
        for sequence, callback in bindings.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts[sequence] = shortcut

    def _focus_history_search(self) -> None:
        self._show_sidebar_for_navigation()
        self.history_search_input.setFocus()
        self.history_search_input.selectAll()

    def _focus_chat_composer(self) -> None:
        self._show_page(0, self.chat_nav_button)
        if self.chat_panel.voice_only_mode_active():
            self._hide_voice_only_screen()
        self.chat_panel.input_box.setFocus()

    def _show_shortcut_help(self) -> None:
        dialog = getattr(self, "_shortcut_help_dialog", None)
        if dialog is None:
            dialog = ShortcutHelpDialog(self)
            self._shortcut_help_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _handle_escape_shortcut(self) -> None:
        dialog = getattr(self, "_shortcut_help_dialog", None)
        if dialog is not None and not dialog.isHidden():
            dialog.close()
            return
        if getattr(self, "rename_chat_panel", None) is not None and not self.rename_chat_panel.isHidden():
            self._cancel_conversation_rename()
            return
        if getattr(self, "_editing_message_index", None) is not None:
            self._cancel_message_edit()
            return
        if getattr(self, "_delete_confirmation_conversation_id", ""):
            self._reset_delete_confirmation(announce=True)
            return
        if self.agent_panel.cancel_clear_history_confirmation(announce=True):
            return
        if getattr(self, "_pending_project_script_plan", None) is not None:
            self._reject_project_script()
            return
        if getattr(self, "history_search_input", None) is not None and self.history_search_input.text():
            self.history_search_input.clear()
            self._apply_history_filter()
            return
        if self.chat_panel.voice_only_mode_active():
            self._hide_voice_only_screen()


























































    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._chat_draft_save_timer.isActive():
            self._chat_draft_save_timer.stop()
        self._save_current_chat_draft()
        encoded_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self._update_config(
            window_geometry=encoded_geometry,
            active_page=self.page_stack.currentIndex(),
        )
        if hasattr(self, "status_timer"):
            self.status_timer.stop()
        self._reply_progress_timer.stop()
        self._voice_capture_health_timer.stop()
        self._voice_stage_timer.stop()
        if self._voice_tuning_save_timer.isActive():
            self._voice_tuning_save_timer.stop()
            self._save_voice_tuning()
        self._interrupt_pending_assistant_reply_for_shutdown()
        if self._active_stream_worker:
            self._active_stream_worker.cancel()
        if self._active_model_pull_worker:
            self._active_model_pull_worker.cancel()
        if self._active_agent_action_worker:
            self._active_agent_action_worker.cancel()
        if self._active_agent_task_worker:
            self._active_agent_task_worker.cancel()
        self.task_runner.close()
        self.startup_sequence.stop()
        self.recorder.cancel()
        self.player.stop()
        self.task_runner.wait_for_done(5000)
        if hasattr(self, "_agent_history_save_timer"):
            self._agent_history_save_timer.stop()
            self._save_agent_history(report_errors=False)
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.startup_sequence.sync_geometry()
        self.startup_sequence.begin()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self._apply_responsive_layout(event.size().width())
        if hasattr(self, "startup_sequence"):
            self.startup_sequence.sync_geometry()
