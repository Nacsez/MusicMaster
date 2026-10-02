"""Semantic, accessibility, and security contracts for packaged portal assets."""

from __future__ import annotations

import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from importlib import resources
from typing import ClassVar

ASSET_NAMES = ("index.html", "app.css", "app.js")
PRIMARY_WORKSPACES = {"master-job", "catalog", "runs"}
UTILITY_PANELS = {"dashboard", "reference-sets", "events", "diagnostics"}
EXPECTED_API_FAMILIES = {
    "/api/bootstrap",
    "/api/browser-session",
    "/api/job-defaults",
    "/api/preferences",
    "/api/capabilities",
    "/api/diagnostics",
    "/api/logs",
    "/api/tracks",
    "/api/reference-sets",
    "/api/runs",
    "/api/tasks",
    "/api/dialog",
    "/api/open-folder",
    "/api/catalog",
    "/api/master-library",
    "/api/selections",
    "/api/jobs",
    "/api/recovery",
    "/api/shutdown",
    "/media/tracks/",
}


class _MarkupInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.ids: list[str] = []
        self.classes: Counter[str] = Counter()
        self.asset_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag, attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag, attrs)

    def _record(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        element_id = attributes.get("id")
        if element_id is not None:
            self.ids.append(element_id)
        for class_name in (attributes.get("class") or "").split():
            self.classes[class_name] += 1
        for attribute in ("src", "href"):
            value = attributes.get(attribute)
            if value is not None:
                self.asset_urls.append(value)

    def matching(
        self,
        *,
        tag: str | None = None,
        attribute: str | None = None,
        value: str | None = None,
    ) -> list[dict[str, str | None]]:
        matches: list[dict[str, str | None]] = []
        for element_tag, attributes in self.elements:
            if tag is not None and element_tag != tag:
                continue
            if attribute is not None and attributes.get(attribute) != value:
                continue
            matches.append(attributes)
        return matches

    def by_id(self, element_id: str) -> tuple[str, dict[str, str | None]]:
        matches = [item for item in self.elements if item[1].get("id") == element_id]
        if len(matches) != 1:
            raise AssertionError(
                f"expected exactly one element with id={element_id!r}, found {len(matches)}"
            )
        return matches[0]


def _javascript_function_body(script: str, name: str) -> str:
    """Return one named function body without depending on the next function's order."""

    declaration = re.search(rf"\b(?:async\s+)?function\s+{re.escape(name)}\b", script)
    if declaration is None:
        raise AssertionError(f"JavaScript function is missing: {name}")
    parameters = script.find("(", declaration.end())
    if parameters < 0:
        raise AssertionError(f"JavaScript function has no parameter list: {name}")
    parameter_depth = 0
    parameter_end = -1
    for index in range(parameters, len(script)):
        character = script[index]
        if character == "(":
            parameter_depth += 1
        elif character == ")":
            parameter_depth -= 1
            if parameter_depth == 0:
                parameter_end = index
                break
    if parameter_end < 0:
        raise AssertionError(f"JavaScript function parameters are not balanced: {name}")
    opening = script.find("{", parameter_end)
    if opening < 0:
        raise AssertionError(f"JavaScript function has no body: {name}")
    depth = 0
    for index in range(opening, len(script)):
        character = script[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return script[opening + 1 : index]
    raise AssertionError(f"JavaScript function body is not balanced: {name}")


class PortalAssetContractTests(unittest.TestCase):
    assets: ClassVar[dict[str, str]]
    markup: ClassVar[_MarkupInventory]

    @classmethod
    def setUpClass(cls) -> None:
        root = resources.files("music_mastering_tools").joinpath("web_assets")
        cls.assets = {}
        for name in ASSET_NAMES:
            asset = root.joinpath(name)
            if not asset.is_file():
                raise AssertionError(f"packaged portal asset is missing: {name}")
            cls.assets[name] = asset.read_text(encoding="utf-8")
        cls.markup = _MarkupInventory()
        cls.markup.feed(cls.assets["index.html"])
        cls.markup.close()

    def test_navigation_has_three_primary_workspaces_and_separate_utilities(self) -> None:
        tabs = {
            attributes["data-tab"]: attributes
            for _, attributes in self.markup.elements
            if attributes.get("data-tab") is not None
            and "primary-tab" in (attributes.get("class") or "").split()
        }
        panels = {
            attributes["data-panel"]: attributes
            for attributes in self.markup.matching(attribute="role", value="region")
            if attributes.get("data-panel") is not None
            and "tab-panel" in (attributes.get("class") or "").split()
            and "utility-panel" not in (attributes.get("class") or "").split()
        }
        utilities = {
            attributes["data-panel"]: attributes
            for attributes in self.markup.matching(attribute="role", value="region")
            if "utility-panel" in (attributes.get("class") or "").split()
        }

        self.assertEqual(set(tabs), PRIMARY_WORKSPACES)
        self.assertEqual(set(panels), PRIMARY_WORKSPACES)
        self.assertEqual(set(utilities), UTILITY_PANELS)
        self.assertEqual(
            [name for name, item in tabs.items() if item.get("aria-current") == "page"],
            ["master-job"],
        )
        for name in sorted(PRIMARY_WORKSPACES):
            with self.subTest(workspace=name):
                self.assertEqual(tabs[name].get("id"), f"tab-{name}")
                self.assertEqual(tabs[name].get("aria-controls"), f"panel-{name}")
                self.assertEqual(panels[name].get("id"), f"panel-{name}")
                self.assertEqual(panels[name].get("aria-labelledby"), f"tab-{name}")
                self.assertEqual("hidden" in panels[name], name != "master-job")
                self.assertNotEqual(tabs[name].get("role"), "tab")
        for name, panel in utilities.items():
            with self.subTest(utility=name):
                self.assertEqual(panel.get("id"), f"panel-{name}")
                self.assertIn("hidden", panel)
                self.assertTrue(panel.get("aria-label"))

        utility_destinations = {
            attributes["data-go-tab"]
            for _, attributes in self.markup.elements
            if attributes.get("data-go-tab") in UTILITY_PANELS
        }
        self.assertTrue({"reference-sets", "events", "diagnostics"}.issubset(utility_destinations))

        run_tabs = {
            attributes["data-run-subtab"]: attributes
            for _, attributes in self.markup.elements
            if attributes.get("data-run-subtab") is not None
        }
        run_panels = {
            attributes["data-run-panel"]: attributes
            for _, attributes in self.markup.elements
            if attributes.get("data-run-panel") is not None
        }
        self.assertEqual(set(run_tabs), {"artifacts", "overview", "raw"})
        self.assertEqual(set(run_panels), set(run_tabs))
        for name, tab in run_tabs.items():
            with self.subTest(run_detail_tab=name):
                panel = run_panels[name]
                self.assertEqual(tab.get("role"), "tab")
                self.assertEqual(tab.get("id"), f"run-tab-{name}")
                self.assertEqual(tab.get("aria-controls"), f"run-panel-{name}")
                self.assertEqual(panel.get("role"), "tabpanel")
                self.assertEqual(panel.get("id"), f"run-panel-{name}")
                self.assertEqual(panel.get("aria-labelledby"), f"run-tab-{name}")

    def test_master_workspace_keeps_core_flow_visible_and_advanced_options_collapsed(
        self,
    ) -> None:
        panel_tag, panel = self.markup.by_id("panel-master-job")
        self.assertEqual(panel_tag, "section")
        self.assertNotIn("hidden", panel)
        self.assertEqual(
            len(self.markup.matching(tag="form", attribute="id", value="job-form")),
            1,
        )
        workflow = self.markup.matching(tag="ol", attribute="class", value="workflow-progress")
        self.assertEqual(len(workflow), 1)
        self.assertTrue(workflow[0].get("aria-label"))

        for title_id in ("job-sources-title", "job-outputs-title"):
            sections = self.markup.matching(
                tag="section",
                attribute="aria-labelledby",
                value=title_id,
            )
            with self.subTest(visible_section=title_id):
                self.assertEqual(len(sections), 1)
                self.assertNotIn("hidden", sections[0])

        advanced = [
            attributes
            for attributes in self.markup.matching(tag="details")
            if "advanced-settings" in (attributes.get("class") or "").split()
        ]
        self.assertEqual(len(advanced), 1)
        self.assertNotIn("open", advanced[0], "advanced settings must be collapsed initially")
        self.assertNotIn("hidden", advanced[0])

        html = self.assets["index.html"]
        advanced_offset = html.index('class="advanced-settings')
        self.assertLess(html.index('id="job-sources-title"'), advanced_offset)
        self.assertLess(html.index('id="job-outputs-title"'), advanced_offset)
        self.assertGreater(html.index('id="job-processing-title"'), advanced_offset)
        self.assertGreater(html.index('id="job-safety-title"'), advanced_offset)
        self.assertNotIn("data-job-section=", html)
        self.assertNotIn("data-job-panel=", html)

    def test_batch_picker_readiness_and_job_payload_contracts_are_complete(self) -> None:
        required_ids = {
            "job-target",
            "target-add-button",
            "target-count",
            "job-targets-body",
            "reference-picker",
            "reference-add-button",
            "reference-count",
            "job-references-body",
            "job-output-directory",
            "output-folder-choose-button",
            "output-folder-default-button",
            "output-folder-save-default-button",
            "readiness-inputs",
            "readiness-references",
            "readiness-destination",
            "readiness-output",
            "task-target-progress",
            "job-validate-button",
            "dry-run-button",
            "render-button",
            "review-masters-button",
        }
        self.assertEqual(sorted(required_ids - set(self.markup.ids)), [])
        for button_id in ("target-add-button", "reference-add-button"):
            tag, attributes = self.markup.by_id(button_id)
            with self.subTest(multi_select_trigger=button_id):
                self.assertEqual(tag, "button")
                self.assertNotIn("hidden", attributes)

        script = self.assets["app.js"]
        picker_body = _javascript_function_body(script, "chooseCatalogTracks")
        for contract in (
            'type: "checkbox"',
            "new Set()",
            "capacity",
            "maxTargets()",
            "maxReferences()",
            "openDialog",
            "selected.size",
        ):
            with self.subTest(multi_select_contract=contract):
                self.assertIn(contract, picker_body)
        self.assertIn('chooseCatalogTracks("target")', script)
        self.assertIn('chooseCatalogTracks("reference")', script)

        readiness_body = _javascript_function_body(script, "jobReadiness")
        for contract in (
            "state.jobTargets.length > 0",
            "state.jobReferences.length > 0",
            'queuedTrackIsUsable(target, "target")',
            'queuedTrackIsUsable(reference, "reference")',
            "referenceWeightsAreUsable()",
            '$("job-output-directory").value.trim()',
            '"output-limited"',
            '"output-normalized"',
            '"output-raw"',
        ):
            with self.subTest(readiness_contract=contract):
                self.assertIn(contract, readiness_body)

        payload_body = _javascript_function_body(script, "buildJobRequest")
        for contract in (
            "target_id: targetIds[0]",
            "target_ids: targetIds",
            "output_directory: outputDirectory",
            "references: state.jobReferences.map",
            "level_weight: Number(reference.level_weight)",
            "frequency_weight: Number(reference.frequency_weight)",
        ):
            with self.subTest(job_payload_contract=contract):
                self.assertIn(contract, payload_body)
        self.assertNotIn('$("job-target").value', payload_body)

    def test_a_b_dock_has_direct_pickers_swap_and_one_persistent_player(self) -> None:
        required_ids = {
            "audition-dock",
            "audition-slot-a",
            "audition-slot-b",
            "audition-label-a",
            "audition-label-b",
            "audition-picker-a",
            "audition-picker-b",
            "audition-swap-button",
            "audition-player",
            "audition-clear-button",
        }
        self.assertEqual(sorted(required_ids - set(self.markup.ids)), [])

        label_targets = {
            attributes.get("for")
            for attributes in self.markup.matching(tag="label")
            if attributes.get("for") is not None
        }
        for picker_id in ("audition-picker-a", "audition-picker-b"):
            tag, attributes = self.markup.by_id(picker_id)
            with self.subTest(direct_picker=picker_id):
                self.assertEqual(tag, "select")
                self.assertIn(picker_id, label_targets)
                self.assertTrue(attributes.get("aria-label"))

        swap_tag, swap = self.markup.by_id("audition-swap-button")
        self.assertEqual(swap_tag, "button")
        self.assertTrue(swap.get("aria-label"))
        self.assertTrue(swap.get("title"))

        players = self.markup.matching(tag="audio")
        self.assertEqual(len(players), 1, "the A/B dock must reuse one media element")
        self.assertEqual(players[0].get("id"), "audition-player")
        self.assertIn("controls", players[0])
        self.assertEqual(players[0].get("preload"), "metadata")

        script = self.assets["app.js"]
        for contract in (
            "function auditionChoices()",
            "function renderAuditionPicker(slot, choices)",
            "function chooseAuditionCandidate(slot, key)",
            "function swapAuditionSlots()",
            "[state.audition.a, state.audition.b] = [state.audition.b, state.audition.a]",
            '$("audition-picker-a").addEventListener("change"',
            '$("audition-picker-b").addEventListener("change"',
            '$("audition-swap-button").addEventListener("click", swapAuditionSlots)',
            "/media/tracks/${encodeURIComponent(selected.track_id)}",
            "player.currentTime",
        ):
            with self.subTest(audition_contract=contract):
                self.assertIn(contract, script)
        self.assertNotIn('element("audio"', script)

        quick_play = _javascript_function_body(script, "playTrack")
        self.assertIn("state.audition.preview = auditionDescriptor", quick_play)
        self.assertIn('activateAuditionSlot("preview"', quick_play)
        self.assertNotIn(
            "assignAudition(",
            quick_play,
            "Quick Play must not silently replace a prepared A/B slot",
        )

        automatic = _javascript_function_body(script, "prepareAutomaticComparison")
        self.assertIn("comparisonHasAudio", automatic)
        self.assertIn("!state.automaticComparisonSourceId", automatic)
        self.assertIn("selectedMasterVersion(entry) || versions[0]", automatic)

        picker_change = _javascript_function_body(script, "chooseAuditionCandidate")
        self.assertIn("if (!key)", picker_change)
        self.assertIn("state.audition[slot] = null", picker_change)

        activation = _javascript_function_body(script, "activateAuditionSlot")
        self.assertIn("++state.audition.loadSequence", activation)
        self.assertIn("loadSequence !== state.audition.loadSequence", activation)
        self.assertIn("player.dataset.trackId !== selected.track_id", activation)

        table_actions = _javascript_function_body(script, "auditionActionCell")
        self.assertIn("`Load ${accessibleLabel} into comparison A`", table_actions)
        self.assertIn("`Load ${accessibleLabel} into comparison B`", table_actions)

        catalog_render = _javascript_function_body(script, "renderCatalog")
        self.assertIn(
            "renderAuditionDock();",
            catalog_render,
            "catalog refreshes must also refresh the direct A/B inventory",
        )

    def test_library_management_keeps_source_version_and_lifecycle_contracts(self) -> None:
        required_ids = {
            "catalog-search",
            "catalog-role-filter",
            "catalog-archived-filter",
            "catalog-discarded-filter",
            "master-library-select-all",
            "master-library-selection-summary",
            "master-library-body",
            "master-detail",
            "master-export-suffix",
            "master-export-clear-button",
            "master-export-button",
            "master-version-select-all",
            "master-version-summary",
            "master-versions-body",
            "track-compare-button",
            "track-rename-button",
        }
        self.assertEqual(sorted(required_ids - set(self.markup.ids)), [])
        for body_id in ("master-library-body", "master-versions-body"):
            self.assertEqual(
                len(self.markup.matching(tag="tbody", attribute="id", value=body_id)),
                1,
            )
        self.assertGreaterEqual(self.markup.classes["library-source-table"], 1)
        self.assertGreaterEqual(self.markup.classes["master-version-table"], 1)

        script = self.assets["app.js"]
        for data_contract in (
            "entry.source",
            "entry.active_version_count",
            "version.deliverables",
            "artifact_ordinal",
            "selectedMasterExports",
        ):
            with self.subTest(library_data_contract=data_contract):
                self.assertIn(data_contract, script)
        for route in (
            'api("/api/tracks/label"',
            'api("/api/master-library/export"',
            'api("/api/master-library/discard"',
            'api("/api/master-library/restore"',
        ):
            with self.subTest(library_route=route):
                self.assertIn(route, script)
        for function_name in (
            "renderCatalog",
            "renderMasterVersions",
            "exportSelectedMasters",
            "discardMasterVersion",
            "restoreMasterVersion",
            "compareOriginalWithMaster",
        ):
            with self.subTest(library_handler=function_name):
                _javascript_function_body(script, function_name)

        self.assertGreaterEqual(
            script.count("event.target !== row"),
            2,
            "nested library controls must not trigger selectable-row keyboard handlers",
        )

    def test_replacing_sources_requires_confirmation_and_invalidates_validation(self) -> None:
        script = self.assets["app.js"]
        import_body = _javascript_function_body(script, "importCatalog")
        self.assertIn("hasCurrentSources", import_body)
        self.assertIn("await confirmAction", import_body)
        self.assertIn("loadImportedSelection", import_body)

        imported_body = _javascript_function_body(script, "loadImportedSelection")
        self.assertIn("state.jobTargets =", imported_body)
        self.assertIn("state.jobReferences =", imported_body)
        self.assertRegex(
            imported_body,
            r"renderJobTargets\(\);\s*renderJobReferences\(\);\s*invalidateValidation\(\);",
        )

        set_body = _javascript_function_body(script, "loadReferenceSet")
        self.assertIn("await confirmAction", set_body)
        self.assertRegex(
            set_body,
            r"renderJobReferences\(\);\s*invalidateValidation\(\);",
        )

        validation_body = _javascript_function_body(script, "validateJob")
        for contract in (
            "jobRequestIsPending()",
            "const requestRevision = state.jobRevision",
            "state.validating = true",
            "requestRevision !== state.jobRevision",
            '$("validation-badge").textContent = "Outdated"',
            "state.validating = false",
        ):
            with self.subTest(validation_race_contract=contract):
                self.assertIn(contract, validation_body)

        submission_body = _javascript_function_body(script, "startJob")
        for contract in (
            "jobRequestIsPending()",
            "const submittedRevision = state.jobRevision",
            "const submittedTargets = state.jobTargets.map",
            "state.submitting = true",
            "state.jobTaskTargets = submittedTargets",
            "state.jobDirty = state.jobRevision !== submittedRevision",
            "state.submitting = false",
            'const statusHeading = $("validation-title")',
        ):
            with self.subTest(submission_race_contract=contract):
                self.assertIn(contract, submission_body)
        self.assertLess(
            submission_body.index("const submittedTargets"),
            submission_body.index("await confirmAction"),
        )

    def test_dialog_menu_and_initialized_state_hooks_are_present(self) -> None:
        app_menu_tag, app_menu = self.markup.by_id("app-menu")
        self.assertEqual(app_menu_tag, "details")
        self.assertIn("command-menu", (app_menu.get("class") or "").split())
        self.assertGreaterEqual(self.markup.classes["command-menu-popover"], 1)

        dialogs = self.markup.matching(tag="dialog")
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].get("id"), "app-dialog")
        self.assertEqual(dialogs[0].get("aria-labelledby"), "dialog-title")
        self.assertEqual(dialogs[0].get("aria-describedby"), "dialog-description")
        self.assertTrue({"dialog-body", "dialog-actions", "toast-region"}.issubset(self.markup.ids))

        _, body = next(item for item in self.markup.elements if item[0] == "body")
        self.assertEqual(body.get("data-app-state"), "loading")
        script = self.assets["app.js"]
        self.assertIn('document.body.dataset.appState = "ready"', script)
        self.assertIn('document.body.dataset.appState = "error"', script)
        refresh_body = _javascript_function_body(script, "refreshAll")
        self.assertIn("return true", refresh_body)
        self.assertIn("return false", refresh_body)
        initialize_body = _javascript_function_body(script, "initialize")
        self.assertIn("const workspaceReady = await refreshAll", initialize_body)
        self.assertIn("if (!workspaceReady)", initialize_body)
        self.assertLess(
            initialize_body.index("if (!workspaceReady)"),
            initialize_body.index('document.body.dataset.appState = "ready"'),
        )
        dialog_body = _javascript_function_body(script, "openDialog")
        for busy_contract in (
            "let busy = false",
            'dialog.setAttribute("aria-busy", "true")',
            "closeButton.disabled = true",
            "if (!busy)",
            'type: "submit"',
            'form.addEventListener("submit", submitHandler)',
            'form.removeEventListener("submit", submitHandler)',
            "if (busy || settled)",
            "(hideCancel ? closeButton : cancel).focus()",
        ):
            with self.subTest(dialog_busy_contract=busy_contract):
                self.assertIn(busy_contract, dialog_body)

        navigation_body = _javascript_function_body(script, "activateTab")
        self.assertIn('priorFocus.closest(".command-menu")', navigation_body)
        self.assertIn('tab.setAttribute("aria-controls", requestedPanel.id)', navigation_body)
        self.assertIn('tab.removeAttribute("aria-controls")', navigation_body)
        self.assertRegex(
            navigation_body,
            r"if \(focus\)\s*\{\s*requested\.focus\(\);",
        )
        self.assertIn("focusWouldBeHidden", navigation_body)
        close_menu_body = _javascript_function_body(script, "closeMenus")
        self.assertIn("menu.contains(document.activeElement)", close_menu_body)
        self.assertIn("summary.focus()", close_menu_body)

        for focus_contract in (
            "function restoreLibraryRowFocus",
            "function restoreVersionFocus",
            "function restoreSelectedRowFocus",
            "function restoreQueueControlFocus",
            '"data-library-control": "export"',
            '"data-version-control": "output"',
            '"data-queue-control": "remove"',
        ):
            with self.subTest(rerender_focus_contract=focus_contract):
                self.assertIn(focus_contract, script)

        task_body = _javascript_function_body(script, "renderTask")
        for terminal_contract in (
            'badge.textContent = "Failed"',
            'badge.textContent = "Partial"',
            'badge.textContent = "Complete"',
            "state.jobTaskId === task.operation_id",
            "!state.jobDirty",
            "renderTaskTargetProgress(task)",
            '$("review-masters-button").hidden = !reviewable',
        ):
            with self.subTest(task_terminal_contract=terminal_contract):
                self.assertIn(terminal_contract, task_body)

        recover_targets = _javascript_function_body(script, "recoverTaskTargets")
        for reload_contract in (
            "result.targets",
            "result.prepared.target_id",
            "context.target_ids",
            "context.target_id",
            "jobTargetFromTrack(track)",
        ):
            with self.subTest(task_reload_contract=reload_contract):
                self.assertIn(reload_contract, recover_targets)
        self.assertIn("recoveredTaskTargets = recoverTaskTargets(task)", task_body)
        self.assertIn('["render", "dry-run"].includes(task.kind)', task_body)

    def test_assets_are_local_only_and_load_packaged_css_and_javascript(self) -> None:
        for name, document in self.assets.items():
            with self.subTest(asset=name):
                self.assertNotRegex(document, r"(?i)\bhttps?://")
                self.assertNotRegex(document, r"(?i)(?:src|href)\s*=\s*[\"']//")
        self.assertIn("/assets/app.css", self.markup.asset_urls)
        self.assertIn("/assets/app.js", self.markup.asset_urls)
        external = [
            value
            for value in self.markup.asset_urls
            if value.casefold().startswith(("http://", "https://", "//"))
        ]
        self.assertEqual(external, [])

    def test_javascript_uses_safe_dom_construction_and_authenticated_json(self) -> None:
        script = self.assets["app.js"]
        forbidden_patterns = {
            "innerHTML assignment": r"\.innerHTML\b",
            "HTML insertion": r"\binsertAdjacentHTML\s*\(",
            "dynamic evaluation": r"(?<![\w$])eval\s*\(",
        }
        for description, pattern in forbidden_patterns.items():
            with self.subTest(description=description):
                self.assertIsNone(re.search(pattern, script))
        self.assertRegex(script, r"\bcreateElement\s*\(")
        self.assertIn("window.__MMT_CONFIG__", script)
        self.assertIn("X-MMT-Token", script)
        self.assertIn("Content-Type", script)
        self.assertIn("application/json", script)
        self.assertRegex(script, r"\bfetch\s*\(")

    def test_javascript_references_every_supported_api_route_family(self) -> None:
        script = self.assets["app.js"]
        missing = sorted(route for route in EXPECTED_API_FAMILIES if route not in script)
        self.assertEqual(missing, [], f"app.js does not reference API families: {missing}")

    def test_every_declared_action_command_has_a_javascript_handler(self) -> None:
        script = self.assets["app.js"]
        commands = {
            attributes["data-command"]
            for _, attributes in self.markup.elements
            if attributes.get("data-command") is not None
        }
        self.assertGreaterEqual(len(commands), 10)
        for command in sorted(commands):
            with self.subTest(command=command):
                if re.fullmatch(r"[A-Za-z_$][\w$]*", command):
                    pattern = rf"\b{re.escape(command)}\s*:"
                else:
                    pattern = rf"[\"']{re.escape(command)}[\"']\s*:"
                self.assertIsNotNone(
                    re.search(pattern, script),
                    f"data-command={command!r} has no entry in the handler table",
                )

    def test_accessible_names_focus_order_and_keyboard_hooks_are_present(self) -> None:
        html_tag, html_attributes = next(item for item in self.markup.elements if item[0] == "html")
        self.assertEqual(html_tag, "html")
        self.assertTrue(html_attributes.get("lang"))
        skip_links = [
            attributes
            for tag, attributes in self.markup.elements
            if tag == "a" and "skip-link" in (attributes.get("class") or "").split()
        ]
        self.assertEqual(len(skip_links), 1)
        self.assertEqual(skip_links[0].get("href"), "#workspace-main")

        label_targets = {
            attributes.get("for")
            for attributes in self.markup.matching(tag="label")
            if attributes.get("for") is not None
        }
        for control_id in (
            "job-target",
            "reference-picker",
            "reference-set-picker",
            "job-output-directory",
            "catalog-search",
            "catalog-role-filter",
            "audition-picker-a",
            "audition-picker-b",
        ):
            with self.subTest(labelled_control=control_id):
                self.assertIn(control_id, label_targets)

        for tag, attributes in self.markup.elements:
            classes = (attributes.get("class") or "").split()
            if tag == "button" and "icon-button" in classes:
                with self.subTest(icon_button=attributes.get("id")):
                    self.assertTrue(attributes.get("aria-label"))
            tabindex = attributes.get("tabindex")
            if tabindex is not None:
                with self.subTest(tabindex=attributes.get("id") or tag):
                    self.assertLessEqual(int(tabindex), 0)

        script = self.assets["app.js"]
        self.assertIsNone(re.search(r"\btabIndex\s*=\s*[1-9]\d*\b", script))
        self.assertIsNone(re.search(r"\btabindex\s*:\s*[\"'][1-9]\d*[\"']", script))
        for keyboard_contract in (
            "function bindLinearTabKeys",
            'event.key === "ArrowRight"',
            'event.key === "ArrowLeft"',
            'event.key === "Home"',
            'event.key === "End"',
            "requested.focus()",
        ):
            with self.subTest(keyboard_contract=keyboard_contract):
                self.assertIn(keyboard_contract, script)

    def test_css_supports_focus_motion_contrast_and_responsive_workflows(self) -> None:
        css = self.assets["app.css"]
        self.assertRegex(css, r":focus-visible\s*\{[^}]*outline\s*:")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("animation-duration: 0.01ms !important", css)
        self.assertIn("transition-duration: 0.01ms !important", css)
        self.assertIn("@media (forced-colors: active)", css)
        self.assertIn("background: Canvas", css)
        self.assertIn("background: Highlight", css)

        responsive_breakpoints = re.findall(r"@media\s*\(max-width:\s*\d+px\)", css)
        self.assertGreaterEqual(len(responsive_breakpoints), 2)
        for responsive_surface in (
            ".studio-layout",
            ".source-board",
            ".master-library-layout",
            ".run-list-table",
            ".audition-dock",
            ".picker-dialog",
        ):
            with self.subTest(responsive_surface=responsive_surface):
                self.assertIn(responsive_surface, css)

        self.assertRegex(
            css,
            r"\.toast-region\s*\{[^}]*bottom:\s*calc\(var\(--status-height\) \+ 14px\)",
        )
        self.assertIn(
            "body:has(.audition-dock:not([hidden])) .toast-region",
            css,
        )
        self.assertIn("bottom: calc(var(--status-height) + 254px)", css)
        self.assertIn("bottom: calc(var(--status-height) + 314px)", css)
        self.assertIn(
            "max-height: calc(100vh - var(--header-height) - var(--status-height) - 268px)",
            css,
        )
        self.assertIn(
            "max-height: calc(100vh - var(--header-height) - var(--status-height) - 328px)",
            css,
        )

        for run_history_contract in (
            ".run-list-table > table > thead",
            "#runs-body > tr:not(:has(.empty-cell))",
            "grid-area: run",
            "grid-area: selection",
            "grid-area: manifest",
        ):
            with self.subTest(run_history_contract=run_history_contract):
                self.assertIn(run_history_contract, css)

        member_editor = _javascript_function_body(self.assets["app.js"], "memberEditor")
        for member_accessibility_contract in (
            'id: "reference-member-picker"',
            'for: "reference-member-picker"',
            'text: "Reference to add"',
            '"aria-label": `Move ${member.label} earlier`',
            '"aria-label": `Move ${member.label} later`',
            '"aria-label": `Remove ${member.label}`',
            "const restoreMemberControlFocus",
            '"data-member-control": "remove"',
            'restoreMemberControlFocus(item.track_id, "up", destination)',
            'restoreMemberControlFocus(item.track_id, "down", destination)',
            'restoreMemberControlFocus(null, "remove", index)',
        ):
            with self.subTest(member_accessibility_contract=member_accessibility_contract):
                self.assertIn(member_accessibility_contract, member_editor)

    def test_cader_theme_preserves_semantic_roles_and_measured_contrast(self) -> None:
        css = self.assets["app.css"]
        root = re.search(r":root\s*\{([^}]+)\}", css)
        self.assertIsNotNone(root)
        assert root is not None
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6});", root.group(1)))
        # These values come from the owned CADER palette. Keep the independent
        # design contract here so later visual edits cannot silently drift.
        expected = {
            "canvas": "#070709",
            "surface": "#101014",
            "surface-raised": "#1b1b23",
            "surface-input": "#09090d",
            "border-strong": "#727684",
            "text": "#f5f7fa",
            "text-muted": "#b6bbc6",
            "accent": "#76ff53",
            "accent-ink": "#070709",
            "reference": "#c9a0ff",
            "focus": "#ff78d4",
        }
        for name, value in expected.items():
            with self.subTest(cader_role=name):
                self.assertEqual(tokens[name], value)

        def luminance(color: str) -> float:
            channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return sum(
                weight * value
                for weight, value in zip((0.2126, 0.7152, 0.0722), linear, strict=True)
            )

        def contrast(first: str, second: str) -> float:
            low, high = sorted((luminance(tokens[first]), luminance(tokens[second])))
            return (high + 0.05) / (low + 0.05)

        surfaces = (
            "canvas",
            "canvas-raised",
            "surface",
            "surface-raised",
            "surface-strong",
            "surface-input",
        )
        for foreground in ("text", "text-muted", "text-subtle", "reference", "warning", "danger"):
            for surface in surfaces:
                with self.subTest(text=foreground, surface=surface):
                    self.assertGreaterEqual(contrast(foreground, surface), 4.5)
        for surface in ("canvas", "surface", "surface-raised", "surface-input"):
            with self.subTest(control_border=surface):
                self.assertGreaterEqual(contrast("border-strong", surface), 3)
        self.assertGreaterEqual(contrast("accent-ink", "accent"), 4.5)
        self.assertGreaterEqual(contrast("focus", "surface-strong"), 3)
        self.assertNotEqual(tokens["focus"], tokens["accent"])
        self.assertNotEqual(tokens["reference"], tokens["warning"])
        self.assertNotRegex(css, r"(?:radial|linear)-gradient\(")
        self.assertNotIn("backdrop-filter", css)
        self.assertRegex(root.group(1), r"--radius:\s*2px;")

        reference_badge = re.search(r"\.queue-number\.reference\s*\{([^}]+)\}", css)
        self.assertIsNotNone(reference_badge)
        assert reference_badge is not None
        self.assertIn("var(--reference)", reference_badge.group(1))
        self.assertIn("--reference: CanvasText;", css)
        theme_meta = self.markup.matching(tag="meta", attribute="name", value="theme-color")
        self.assertEqual(len(theme_meta), 1)
        self.assertEqual(theme_meta[0].get("content"), tokens["canvas"])

    def test_literal_javascript_id_selectors_resolve_to_unique_elements(self) -> None:
        script = self.assets["app.js"]
        html_ids = set(self.markup.ids)
        self.assertEqual(
            len(self.markup.ids),
            len(html_ids),
            "index.html contains duplicate element IDs",
        )
        referenced_ids: set[str] = set()
        referenced_ids.update(
            re.findall(
                r"\bgetElementById\(\s*[\"']([A-Za-z][\w:.-]*)[\"']\s*\)",
                script,
            )
        )
        referenced_ids.update(
            re.findall(
                r"\bgetElementById\(\s*`([A-Za-z][\w:.-]*)`\s*\)",
                script,
            )
        )
        referenced_ids.update(
            re.findall(
                r"\bquerySelector(?:All)?\(\s*[\"']#([A-Za-z][\w:.-]*)",
                script,
            )
        )
        referenced_ids.update(
            re.findall(
                r"\$\(\s*[\"']([A-Za-z][\w:.-]*)[\"']\s*\)",
                script,
            )
        )

        dynamically_declared = set(re.findall(r"\.id\s*=\s*[\"']([A-Za-z][\w:.-]*)[\"']", script))
        dynamically_declared.update(
            re.findall(
                r"setAttribute\(\s*[\"']id[\"']\s*,\s*[\"']([A-Za-z][\w:.-]*)[\"']",
                script,
            )
        )
        dynamically_declared.update(re.findall(r"\bid\s*:\s*[\"']([A-Za-z][\w:.-]*)[\"']", script))
        unresolved = sorted(referenced_ids - html_ids - dynamically_declared)
        self.assertEqual(
            unresolved,
            [],
            f"literal JavaScript ID selectors have no declaration: {unresolved}",
        )


if __name__ == "__main__":
    unittest.main()
