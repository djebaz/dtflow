"""Inline template library over the independent queue draft; no execution or provider ownership."""

import asyncio
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSignalBlocker, Qt
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QListWidgetItem

from dtflow.core.models import ValidationError
from dtflow.core.queue_authoring import QueueEdit
from dtflow.services import queue_templates as service
from dtflow.ui.inline_feedback import confirm_action, edit_text, feedback


class QueueTemplates(QObject):
    def __init__(self, queue):
        super().__init__(queue.page)
        self.queue, self.page = queue, queue.page
        self.loaded = None
        self.baseline = None
        self.busy = False
        self.applying = False
        self._epoch = 0
        self.page.saveQueueTemplateButton.clicked.connect(self.save_as)
        self.page.loadQueueTemplateButton.clicked.connect(self.browse)
        self.page.queueComposerScroll.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self.navigation_changed()
        return False

    def capture(self):
        q = self.queue
        request, backend, _, _ = q._composer_request(preview=True)
        if q._draft_job is not None:
            raise ValidationError("Templates support text-to-image only; image sources and masks are not portable.")
        recipe = service.Recipe(
            request,
            q._plan_form(),
            self.page.queueCountSpinBox.value(),
            backend,
            self.page.newQueueCapSpinBox.value(),
            q._authoring_reconstructed,
        )
        return service.recipe_from_record(service.recipe_record(recipe))

    def changed(self):
        if self.loaded is None:
            self.page.queueTemplateStatusLabel.setText("No template loaded")
            return
        try:
            dirty = service.recipe_record(self.capture()) != self.baseline
        except ValueError:
            dirty = True
        self.page.queueTemplateStatusLabel.setText(f"Template: {self.loaded.name}" + (" · modified" if dirty else ""))

    def _error(self, exc):
        # Never send arbitrary recipe/path/prompt data to diagnostics or the error projection.
        message = (
            str(exc)
            if isinstance(exc, ValidationError)
            else "The template library could not be accessed; your draft is unchanged."
        )
        feedback(self.page).notice("Template not changed", message, "error")

    def _run(self, work, done):
        if self.busy or self.queue._runner.closed:
            return
        self.busy = True
        for button in (self.page.saveQueueTemplateButton, self.page.loadQueueTemplateButton):
            button.setEnabled(False)
        epoch = self._epoch

        async def task():
            return await asyncio.to_thread(work)

        def complete(result, error):
            self.busy = False
            for button in (self.page.saveQueueTemplateButton, self.page.loadQueueTemplateButton):
                button.setEnabled(True)
            if error is not None:
                if self.queue.composing and self.page.queueComposerScroll.isVisible() and epoch == self._epoch:
                    self._error(error)
                return
            if epoch == self._epoch and self.queue.composing and self.page.queueComposerScroll.isVisible():
                done(result)

        self.queue._runner.submit(task(), complete)

    def navigation_changed(self):
        self._epoch += 1

    def detach(self):
        self.loaded = self.baseline = None
        self.queue._template_cap = False
        self.changed()

    def save_as(self):
        if feedback(self.page).commit(self.page.saveQueueTemplateButton):
            return
        try:
            recipe = self.capture()
        except ValueError as exc:
            self._error(exc)
            return
        edit_text(
            self.page, "Save queue template", "Template name (1–80 characters)", lambda name: self._save(name, recipe)
        )

    def _save(self, name, recipe, previous=None):
        self._run(lambda: service.save_template(self.queue._state, name, recipe, previous=previous), self._saved)

    def _saved(self, template):
        self.loaded = template
        self.queue._template_cap = True
        self.baseline = service.recipe_record(template.recipe)
        self.changed()  # edits made while the write ran remain visibly modified
        feedback(self.page).notice("Template saved", "Recipe saved locally. No queue or generation was created.", "ok")

    def browse(self):
        if feedback(self.page).commit(self.page.loadQueueTemplateButton):
            return
        self._run(lambda: service.list_templates(self.queue._state), self._library)

    def _library(self, templates):
        file = QFile(str(Path(__file__).with_name("forms") / "queue_template_library.ui"))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            self._error(ValidationError("The template library form could not be opened."))
            return
        try:
            editor = QUiLoader().load(file)
        finally:
            file.close()
        panel = feedback(self.page)
        by_id = {template.identity: template for template in templates}

        def selected():
            item = editor.templateList.currentItem()
            return by_id.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

        def selection_changed():
            template = selected()
            text = (
                service.summary(template) if template else "No matching template. Save a new recipe from the composer."
            )
            if template:
                text += "\nSource prompt: " + template.recipe.request.prompt[:160].replace("\n", " ")
                if template.recipe.reconstructed:
                    text += "\nReconstructed from older queue results: original authoring input was missing. Review before reuse."
                text += "\nWildcard files use current local contents, not archived copies. Missing dependencies remain unresolved."
                if template.recipe.backend != self.queue._backend()[0]:
                    text += "\nBackend mismatch: loading requires explicit rebinding to the active connection."
            editor.templateSummaryLabel.setText(text)
            for button in (
                editor.templateUpdateButton,
                editor.templateRenameButton,
                editor.templateDeleteButton,
                panel.form.inlineEditorAcceptButton,
            ):
                button.setEnabled(template is not None)

        def populate(query=""):
            editor.templateList.clear()
            for template in templates:
                if query.casefold() not in template.name.casefold():
                    continue
                item = QListWidgetItem(template.name)
                item.setData(Qt.ItemDataRole.UserRole, template.identity)
                item.setToolTip(template.name)
                editor.templateList.addItem(item)
            if editor.templateList.count():
                editor.templateList.setCurrentRow(0)
            selection_changed()

        def load():
            template = selected()
            if template:
                panel.dismiss()
                self.load(template)

        def update():
            template = selected()
            if template is None:
                return
            try:
                recipe = self.capture()
            except ValueError as exc:
                self._error(exc)
                return
            panel.dismiss()
            captured = self._draft_key()

            def commit_update():
                if not self.queue.composing or self._draft_key() != captured:
                    self._error(ValidationError("Draft changed; review the template update again."))
                    return
                self._save(template.name, recipe, template)

            confirm_action(
                self.page,
                self.page.saveQueueTemplateButton,
                "Update template",
                "Replace the selected saved recipe with the current draft? Queues and outputs are unchanged.",
                commit_update,
            )

        def rename():
            template = selected()
            if template:
                panel.dismiss()
                edit_text(
                    self.page,
                    "Rename template",
                    "Template name",
                    lambda name: self._run(
                        lambda: service.rename_template(self.queue._state, template, name), self._renamed
                    ),
                    template.name,
                )

        def delete():
            template = selected()
            if template:
                panel.dismiss()
                confirm_action(
                    self.page,
                    self.page.loadQueueTemplateButton,
                    "Delete template",
                    "Delete only this saved recipe? Existing queues, attempts and outputs are kept.",
                    lambda: self._run(
                        lambda: service.delete_template(self.queue._state, template), lambda _: self._deleted(template)
                    ),
                    danger=True,
                )

        editor.templateSearchEdit.textChanged.connect(populate)
        editor.templateList.currentItemChanged.connect(lambda *_: selection_changed())
        editor.templateUpdateButton.clicked.connect(update)
        editor.templateRenameButton.clicked.connect(rename)
        editor.templateDeleteButton.clicked.connect(delete)
        panel.editor("Queue templates", editor, "Load template", load)
        panel.form.inlineEditorAcceptButton.setEnabled(True)
        populate()
        editor.templateSearchEdit.setFocus()

    def load(self, template):
        # A library entry is revalidated before any UI state is touched, including all spin/combo ranges.
        try:
            recipe = service.recipe_from_record(service.recipe_record(template.recipe))
            try:
                different = service.recipe_record(self.capture()) != service.recipe_record(recipe)
            except ValueError:
                different = True
        except ValueError as exc:
            self._error(exc)
            return
        active = self.queue._backend()
        mismatch = recipe.backend != active[0]
        captured = self._draft_key()

        def apply():
            if not self.queue.composing or self._draft_key() != captured or self.queue._backend() != active:
                self._error(ValidationError("Draft or connection changed; load again to review the replacement."))
                return
            self._apply(template, replace(recipe, backend=active[0]), active)

        if different or mismatch or recipe.reconstructed:
            text = "Replace the current queue draft? Generate and existing queues are unchanged."
            if mismatch:
                text += f" This recipe requires {recipe.backend}; Confirm explicitly rebinds this draft to {active[0]}. Review its models and LoRAs; no connection is switched or asset uploaded."
            if recipe.reconstructed:
                text += " This is a reconstruction from older resolved items, not complete original authoring input. Confirm only after review."
            confirm_action(self.page, self.page.loadQueueTemplateButton, "Load template", text, apply)
        else:
            apply()

    def _draft_key(self):
        try:
            return service.recipe_record(self.capture())
        except ValueError:
            return (self.queue._draft, self.queue._plan_form(), self.page.composerPromptEdit.toPlainText())

    def _apply(self, template, recipe, active):
        from dtflow.ui.queue_page import PLAN_INPUTS

        self.queue._template_cap = True
        self.applying = True
        blockers = [
            QSignalBlocker(getattr(self.page, name))
            for name in (*PLAN_INPUTS, "newQueueCapSpinBox", "composerPromptEdit")
            if name != "planRerollButton"
        ]
        try:
            edited = QueueEdit(
                asdict(recipe.request),
                recipe.form,
                recipe.count,
                "Reconstructed authoring input; review before reuse." if recipe.reconstructed else "",
            )
            self.queue._edit_loaded((edited, {"name": active[0], "endpoint": active[1]}, (), recipe.cap), None)
            self.page.queueCountSpinBox.setValue(recipe.count)  # retain the dormant Repeat count in Pair/Cross recipes
        finally:
            del blockers
            self.applying = False
        self.loaded = template
        self.baseline = service.recipe_record(template.recipe)
        self.queue._mode_changed()
        self.changed()
        feedback(self.page).notice(
            "Template loaded",
            "Only the queue draft changed. Review unresolved model/LoRA choices and current wildcard files before creating a queue. Nothing was submitted.",
            "warning",
        )

    def validate_use(self, request, backend, endpoint):
        if self.loaded is None:
            return ()
        if (backend, endpoint) != self.queue._backend():
            raise ValidationError(
                "Connection changed after template loading; load again and explicitly review/rebind it."
            )
        receipts = self.queue._choices().get("receipts", {})
        return tuple(receipts[item.identifier] for item in request.loras if item.identifier in receipts)

    def _renamed(self, template):
        if self.loaded and self.loaded.identity == template.identity:
            self.loaded = template
            self.changed()
        feedback(self.page).notice("Template renamed", "The saved recipe was renamed; queues are unchanged.", "ok")

    def _deleted(self, template):
        if self.loaded and self.loaded.identity == template.identity:
            self.loaded = self.baseline = None
            self.changed()
        feedback(self.page).notice(
            "Template deleted", "The recipe was removed; your draft, queues and outputs are kept.", "ok"
        )
