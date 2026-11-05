"""Offline Workflows mockup only; not imported by normal application launch.

Designer owns the static layouts. The render fixture injects the accepted POC
canvas; this module imports no graph library, tools, executor or backend.
"""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QGroupBox, QPushButton, QSizePolicy, QWidget


class WorkflowsPreview:
    def __init__(self, owner, canvas: QWidget, fit: Callable, add_node: Callable, thumbnail: QPixmap):
        from dtflow.ui.main_window import FORMS_DIR, load_ui
        from pathlib import Path

        self.owner = owner
        self.thumbnail = thumbnail
        self.context = load_ui(FORMS_DIR / "workflows_context.ui", owner.shell.leftStack)
        self.workspace = load_ui(FORMS_DIR / "workflows_workspace.ui", owner.shell.centerStack)
        self.inspector = load_ui(FORMS_DIR / "workflows_inspector.ui", owner.shell.rightStack)
        for stack, page in (
            (owner.shell.leftStack, self.context),
            (owner.shell.centerStack, self.workspace),
            (owner.shell.rightStack, self.inspector),
        ):
            stack.addWidget(page)
            stack.setCurrentWidget(page)
        self.workspace.workflowCanvasHost.layout().addWidget(canvas)
        self.workspace.layout().setStretch(2, 1)
        self.workspace.workflowToolbar.layout().setStretch(0, 1)
        for page in (self.context, self.inspector, self.workspace):
            for card in page.findChildren(QGroupBox):
                card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            for header in page.findChildren(QFrame):
                if header.property("role") == "sectionHeader":
                    header.setMaximumHeight(32)
        for button in self.workspace.workflowToolbar.findChildren(QPushButton):
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.inspector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy = owner.shell.leftStack.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        owner.shell.leftStack.setSizePolicy(policy)

        self.nav = QPushButton(owner.shell.navigationFrame)
        self.nav.setIcon(QIcon(str(Path(__file__).parents[1] / "resources/images/gallery/waypoints.svg")))
        self.nav.setFixedWidth(30)
        self.nav.setToolTip("Workflows · offline UI preview")
        self.nav.setObjectName("workflowsPreviewNavigation")
        self.nav.setProperty("role", "navigation")
        self.nav.setCheckable(True)
        self.nav.setAccessibleName("Workflows workspace · offline preview")
        owner.shell.navigationFrame.layout().insertWidget(2, self.nav)
        for button in owner.shell.navigationFrame.findChildren(QPushButton):
            if button is not self.nav:
                button.setAutoExclusive(False)
                button.setChecked(False)
        self.nav.setChecked(True)
        self.nav.clicked.connect(lambda: self.nav.setChecked(True))
        self.workspace.fitWorkflowButton.clicked.connect(fit)
        for button in (
            self.context.newWorkflowButton,
            self.context.openWorkflowButton,
            self.workspace.saveWorkflowButton,
            self.workspace.runWorkflowButton,
            self.inspector.continueBranchButton,
            self.inspector.stopBranchButton,
        ):
            button.setToolTip("Offline UI preview only · no files or work are submitted")
            button.clicked.connect(self.preview_notice)
        self.context.nodePalette.itemDoubleClicked.connect(lambda item: add_node(item.text()))
        self.context.nodeSearch.textChanged.connect(self._filter_nodes)
        self.context.workflowSearch.textChanged.connect(self._filter_library)
        self.context.workflowList.setCurrentRow(0)

    def preview_notice(self):
        self.owner.progress_strip.status(
            "workflow-preview-note", "UI preview only · no workflow execution or file writes.", lambda: None
        )

    def _filter_nodes(self, text):
        self._filter(self.context.nodePalette, text)

    def _filter_library(self, text):
        self._filter(self.context.workflowList, text)

    @staticmethod
    def _filter(widget, text):
        for index in range(widget.count()):
            item = widget.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def show_node(self, title, meta, params, *, mask=False):
        self.inspector.workflowNodeTitle.setText(title)
        self.inspector.workflowNodeMeta.setText(meta)
        self.inspector.workflowMaskControls.setVisible(mask)
        self.inspector.workflowModelControls.setVisible(not mask)
        self.workspace.workflowSelectionCount.setText(f"{title} selected")
        if not mask:
            self.inspector.workflowCheckpoint.clear()
            self.inspector.workflowCheckpoint.addItem(str(params.get("checkpoint", "")))
            self.inspector.workflowCheckpoint.setToolTip(str(params.get("checkpoint", "")))
            self.inspector.workflowSteps.setValue(params.get("steps", 20))
            self.inspector.workflowGuidance.setValue(params.get("guidance", 4.5))
            self.inspector.workflowPrompt.setText(str(params.get("prompt", "")))
            self.inspector.workflowStrength.clear()
            self.inspector.workflowStrength.addItem(f"{params.get('strength', 0.6):.2f}")
            loras = params.get("loras", [])
            self.inspector.workflowLora.clear()
            self.inspector.workflowLora.addItems([f"{name} · {weight:g}" for name, weight in loras] or ["None"])

    def present(self, state):
        running = state in {"running", "paused"}
        paused = state == "paused"
        self.workspace.runWorkflowButton.setEnabled(not running)
        self.workspace.workflowOutputs.setMaximumHeight(120 if running else 76)
        self.inspector.workflowBranchActions.setVisible(paused)
        self.inspector.workflowBranchStatus.setText(
            "Paused · no face detected" if paused else "Running · Inpaint" if running else "Ready"
        )
        self.inspector.workflowBranchStatus.setProperty("workflowState", "warning" if paused else "normal")
        self.inspector.workflowBranchStatus.style().unpolish(self.inspector.workflowBranchStatus)
        self.inspector.workflowBranchStatus.style().polish(self.inspector.workflowBranchStatus)
        self.inspector.workflowBranchDetail.setText(
            "No face boxes for image 01. Continue / Stop affect this image branch only; siblings keep running."
            if paused
            else "Saved source-sized image → mask → Inpaint → refine."
        )
        self.inspector.workflowSiblingStatus.setText(
            "Branch 02 · Running · Img2img refine" if running else "Branch 02 · Ready"
        )
        self.inspector.workflowBranch.setVisible(running)
        self.inspector.workflowPreflight.setVisible(not running)
        self.workspace.workflowSummary.setText(
            "5 nodes · 2 image branches · Branch 01 paused; Branch 02 continues"
            if paused
            else "5 nodes · 2 image branches · Running independently"
            if running
            else "5 nodes · 2 image branches · DTFlow workflow v1"
        )
        for widget in (self.workspace.workflowThumb1, self.workspace.workflowThumb2):
            widget.setVisible(running)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setPixmap(
                self.thumbnail.scaled(
                    76, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
        self.workspace.workflowOutputText.setText(
            "2 saved input images · final composites retained in Images.\nLater steps use the saved application final, not the provider raw."
            if running
            else "No run started. Saved results will remain available in Images."
        )
        self.owner.progress_strip.finish("workflow-preview")
        if running:
            self.owner.progress_strip.start(
                "workflow-preview",
                "Workflow · Portrait finishing",
                stop=lambda: self.preview_notice(),
                detail="Branch 01 paused · Branch 02 refining" if paused else "2 branches running · Inpaint / refine",
                stop_text="Preview only · stop is not wired to execution",
            )
